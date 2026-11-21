"""异步会话与单活动任务管理器（支持单浏览器互斥锁、事件缓冲断线重放、SSE 推流与任务取消）。"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from xhs_agent.api.state import WebAppState
from xhs_agent.harness.llm import DeepSeekClient, LLMClientProtocol
from xhs_agent.harness.runtime import AgentRuntime, AgentSession, build_v1_registry
from xhs_agent.jobs.extract import JobExtractor
from xhs_agent.jobs.tools import JobTools
from xhs_agent.knowledge.extract import ExperienceExtractor
from xhs_agent.knowledge.models import CATEGORY_LABELS
from xhs_agent.knowledge.tools import KnowledgeTools

logger = logging.getLogger("xhs_agent.api.task_manager")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ActiveTaskConflictError(RuntimeError):
    """已有正在运行中的 Agent 任务时抛出（对应 HTTP 409）。"""

    def __init__(self, active_session_id: str, active_run_id: str) -> None:
        self.active_session_id = active_session_id
        self.active_run_id = active_run_id
        super().__init__(
            f"当前已有运行中的任务（session={active_session_id}, run={active_run_id}）。"
            "受小红书单浏览器限制，请等待当前任务结束或先取消后再发起新任务。"
        )


@dataclass
class ChatMessageRecord:
    role: str  # "user" | "assistant"
    content: str
    turn: int
    created_at: str = field(default_factory=_utc_now_iso)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunRecord:
    run_id: str
    session_id: str
    turn: int
    user_input: str
    status: str = "running"  # "running" | "completed" | "cancelled" | "error"
    started_at: str = field(default_factory=_utc_now_iso)
    finished_at: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None
    subscribers: list[asyncio.Queue[dict[str, Any] | None]] = field(default_factory=list)

    def publish(self, event_name: str, data: dict[str, Any]) -> None:
        packet = {"event": event_name, "data": data, "ts": _utc_now_iso()}
        self.events.append(packet)
        for q in list(self.subscribers):
            q.put_nowait(packet)

    def close_subscribers(self) -> None:
        for q in list(self.subscribers):
            q.put_nowait(None)


@dataclass
class ManagedSession:
    session_id: str
    agent_session: AgentSession
    created_at: str = field(default_factory=_utc_now_iso)
    messages: list[ChatMessageRecord] = field(default_factory=list)
    runs: dict[str, RunRecord] = field(default_factory=dict)
    latest_run_id: str | None = None
    default_max_steps: int | None = None
    default_max_search: int | None = None
    default_max_detail: int | None = None
    default_mode: str = "auto"

    @property
    def status(self) -> str:
        if self.latest_run_id and self.latest_run_id in self.runs:
            return self.runs[self.latest_run_id].status
        return "idle"

    def to_summary_dict(self) -> dict[str, Any]:
        last_msg = self.messages[-1].content[:120] if self.messages else ""
        return {
            "session_id": self.session_id,
            "created_at": self.created_at,
            "status": self.status,
            "turns": self.agent_session.turns,
            "total_llm_calls": self.agent_session.total_llm_calls,
            "total_tokens": self.agent_session.total_usage.total_tokens,
            "total_cost_cny": round(self.agent_session.total_cost.total_cost, 6),
            "last_message": last_msg,
            "latest_run_id": self.latest_run_id,
            "trace_path": str(self.agent_session.trace_path),
        }

    def to_detail_dict(self) -> dict[str, Any]:
        latest_events: list[dict[str, Any]] = []
        if self.latest_run_id and self.latest_run_id in self.runs:
            latest_events = list(self.runs[self.latest_run_id].events)
        return {
            **self.to_summary_dict(),
            "total_usage": self.agent_session.total_usage.to_dict(),
            "cost": self.agent_session.total_cost.to_dict(),
            "tool_calls": dict(self.agent_session.total_tool_calls),
            "messages": [
                {
                    "role": m.role,
                    "content": m.content,
                    "turn": m.turn,
                    "created_at": m.created_at,
                    "meta": m.meta,
                }
                for m in self.messages
            ],
            "latest_events": latest_events,
        }


def _format_sse(event_name: str, data: dict[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, default=str)
    return f"event: {event_name}\ndata: {payload}\n\n"


class TaskManager:
    """管理所有 Web 多轮会话、单活动任务互斥锁与 SSE 推流。"""

    def __init__(self, state: WebAppState) -> None:
        self.state = state
        self.sessions: dict[str, ManagedSession] = {}
        self._active_run: RunRecord | None = None
        self._llm_client: LLMClientProtocol | None = None
        self._extractor_llm: LLMClientProtocol | None = None
        self._owned_llm: DeepSeekClient | None = None

    def _ensure_llms(self) -> tuple[LLMClientProtocol, LLMClientProtocol]:
        if self._llm_client is None:
            if self.state._injected_llm is not None:
                self._llm_client = self.state._injected_llm
            else:
                self._owned_llm = DeepSeekClient(self.state.config.llm)
                self._llm_client = self._owned_llm

        if self._extractor_llm is None:
            self._extractor_llm = self.state._injected_extractor_llm or self._llm_client

        return self._llm_client, self._extractor_llm

    def create_session(
        self,
        *,
        max_steps: int | None = None,
        max_search: int | None = None,
        max_detail: int | None = None,
        mode: str = "auto",
    ) -> ManagedSession:
        if self.state.store is None or self.state.adapter is None:
            raise RuntimeError("WebAppState 尚未完成初始化")

        main_llm, ext_llm = self._ensure_llms()

        session_cfg = self.state.config.model_copy(deep=True)
        if max_steps is not None:
            session_cfg.budget.max_steps = max_steps
        if max_search is not None:
            session_cfg.budget.max_search_calls = max_search
        if max_detail is not None:
            session_cfg.budget.max_detail_calls = max_detail

        rt = AgentRuntime(
            session_cfg,
            adapter=self.state.adapter,
            llm=main_llm,
            extractor_llm=ext_llm,
            store=self.state.store,
            export_dir=self.state.export_dir,
            traces_dir=self.state.traces_dir,
        )
        rt.store = self.state.store
        rt.adapter = self.state.adapter
        rt.llm = main_llm
        extractor = JobExtractor(ext_llm, session_cfg.llm)
        rt.job_tools = JobTools(
            adapter=self.state.adapter,
            store=self.state.store,
            extractor=extractor,
            export_dir=self.state.export_dir,
        )
        exp_extractor = ExperienceExtractor(ext_llm, session_cfg.llm)
        rt.knowledge_tools = KnowledgeTools(
            adapter=self.state.adapter,
            store=self.state.store,
            extractor=exp_extractor,
            export_dir=self.state.export_dir,
        )
        rt.registry = build_v1_registry(
            self.state.adapter,
            session_cfg,
            rt.job_tools,
            rt.knowledge_tools,
        )

        agent_session = rt.new_session()
        managed = ManagedSession(
            session_id=agent_session.session_id,
            agent_session=agent_session,
            default_max_steps=max_steps,
            default_max_search=max_search,
            default_max_detail=max_detail,
            default_mode=mode,
        )
        self.sessions[managed.session_id] = managed
        return managed

    def get_session(self, session_id: str) -> ManagedSession | None:
        return self.sessions.get(session_id)

    def list_sessions(self) -> list[dict[str, Any]]:
        return [
            s.to_summary_dict()
            for s in sorted(self.sessions.values(), key=lambda x: x.created_at, reverse=True)
        ]

    def _snapshot_job_ids(self) -> set[int]:
        if self.state.store is None:
            return set()
        jobs = self.state.store.query_jobs(limit=500)
        return {j.id for j in jobs if j.id is not None}

    def _snapshot_exp_ids(self) -> set[int]:
        if self.state.store is None:
            return set()
        exps = self.state.store.query_experiences(limit=500)
        return {e.id for e in exps if e.id is not None}

    async def start_turn(
        self,
        session_id: str,
        content: str,
        *,
        max_steps: int | None = None,
        max_search: int | None = None,
        max_detail: int | None = None,
        mode: str | None = None,
    ) -> RunRecord:
        managed = self.sessions.get(session_id)
        if managed is None:
            raise KeyError(f"未找到会话: {session_id}")

        cleaned = content.strip()
        if not cleaned:
            raise ValueError("消息内容不能为空")

        if self._active_run is not None and self._active_run.status == "running":
            raise ActiveTaskConflictError(self._active_run.session_id, self._active_run.run_id)

        next_turn = managed.agent_session.turns + 1
        run_id = f"r_{uuid.uuid4().hex[:10]}"
        run = RunRecord(
            run_id=run_id,
            session_id=session_id,
            turn=next_turn,
            user_input=cleaned,
        )
        managed.runs[run_id] = run
        managed.latest_run_id = run_id
        managed.messages.append(ChatMessageRecord(role="user", content=cleaned, turn=next_turn))

        self._active_run = run
        self.state.active_session_id = session_id

        eff_steps = max_steps if max_steps is not None else managed.default_max_steps
        eff_search = max_search if max_search is not None else managed.default_max_search
        eff_detail = max_detail if max_detail is not None else managed.default_max_detail
        eff_mode = mode if mode is not None else managed.default_mode

        before_job_ids = self._snapshot_job_ids()
        before_exp_ids = self._snapshot_exp_ids()
        run.publish(
            "turn_start",
            {"run_id": run_id, "session_id": session_id, "turn": next_turn, "mode": eff_mode, "user_input": cleaned},
        )

        async def _runner() -> None:
            def on_event(event_type: str, payload: dict[str, Any]) -> None:
                data = {"run_id": run_id, **payload}
                if event_type == "tool_call" and "args_raw" in data:
                    try:
                        data["args"] = json.loads(data["args_raw"])
                    except Exception:
                        data["args"] = data["args_raw"]
                run.publish(event_type, data)

            try:
                result = await managed.agent_session.ask(
                    cleaned,
                    on_event=on_event,
                    cancel_event=run.cancel_event,
                    max_steps=eff_steps,
                    max_search_calls=eff_search,
                    max_detail_calls=eff_detail,
                    mode=eff_mode,
                )
                after_jobs = self.state.store.query_jobs(limit=500) if self.state.store is not None else []
                newly_saved = [
                    j.model_dump(mode="json")
                    for j in after_jobs
                    if j.id is not None and j.id not in before_job_ids
                ]
                after_exps = self.state.store.query_experiences(limit=500) if self.state.store is not None else []
                newly_saved_exps = [
                    {
                        **e.model_dump(mode="json"),
                        "category_label": CATEGORY_LABELS.get(e.category, e.category),
                    }
                    for e in after_exps
                    if e.id is not None and e.id not in before_exp_ids
                ]

                run.status = "cancelled" if result.stop_reason == "cancelled" else "completed"
                run.finished_at = _utc_now_iso()

                meta = {
                    "run_id": run_id,
                    "mode": eff_mode,
                    "stop_reason": result.stop_reason,
                    "steps": result.steps,
                    "llm_calls": result.llm_calls,
                    "extract_llm_calls": result.extract_llm_calls,
                    "total_llm_calls": result.total_llm_calls,
                    "total_usage": result.total_usage.to_dict(),
                    "cost": result.cost.to_dict(),
                    "tool_calls": result.tool_calls,
                    "newly_saved_jobs": newly_saved,
                    "newly_saved_experiences": newly_saved_exps,
                }
                managed.messages.append(
                    ChatMessageRecord(
                        role="assistant",
                        content=result.answer,
                        turn=next_turn,
                        meta=meta,
                    )
                )
                run.publish(
                    "turn_complete",
                    {
                        "run_id": run_id,
                        "session_id": session_id,
                        "turn": next_turn,
                        "answer": result.answer,
                        **meta,
                    },
                )
            except Exception as exc:
                logger.exception("会话 %s 轮次 %s 执行异常", session_id, run_id)
                run.status = "error"
                run.finished_at = _utc_now_iso()
                managed.messages.append(
                    ChatMessageRecord(
                        role="assistant",
                        content=f"执行过程中发生错误：{exc}",
                        turn=next_turn,
                        meta={"run_id": run_id, "error": str(exc)},
                    )
                )
                run.publish("error", {"run_id": run_id, "session_id": session_id, "message": str(exc)})
            finally:
                if self._active_run is run:
                    self._active_run = None
                    self.state.active_session_id = None
                run.close_subscribers()

        run.task = asyncio.create_task(_runner(), name=f"xhs-run-{run_id}")
        return run

    def cancel_session(self, session_id: str) -> tuple[bool, str | None]:
        managed = self.sessions.get(session_id)
        if managed is None:
            raise KeyError(f"未找到会话: {session_id}")
        if not managed.latest_run_id:
            return False, None
        run = managed.runs.get(managed.latest_run_id)
        if run is None or run.status != "running":
            return False, managed.latest_run_id
        run.cancel_event.set()
        return True, run.run_id

    async def subscribe_events(
        self,
        session_id: str,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[str]:
        managed = self.sessions.get(session_id)
        if managed is None:
            raise KeyError(f"未找到会话: {session_id}")

        target_run_id = run_id or managed.latest_run_id
        if not target_run_id or target_run_id not in managed.runs:
            yield _format_sse("idle", {"session_id": session_id})
            return

        run = managed.runs[target_run_id]
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        is_running = run.status == "running"
        if is_running:
            run.subscribers.append(queue)

        # 先回放已缓冲的历史事件，保证断线重连或稍晚订阅不丢事件
        snapshot_events = list(run.events)
        seen_count = len(snapshot_events)
        for pkt in snapshot_events:
            yield _format_sse(pkt["event"], pkt["data"])

        if not is_running:
            return

        try:
            # 防止订阅与 snapshot 之间竞态产生重复或遗漏
            while True:
                pkt = await queue.get()
                if pkt is None:
                    break
                if seen_count > 0:
                    # 跳过已经包含在 snapshot 里的重复对象引用
                    if pkt in snapshot_events:
                        seen_count -= 1
                        continue
                yield _format_sse(pkt["event"], pkt["data"])
        finally:
            if queue in run.subscribers:
                run.subscribers.remove(queue)

    async def shutdown(self) -> None:
        if self._active_run is not None and self._active_run.status == "running":
            self._active_run.cancel_event.set()
            if self._active_run.task is not None:
                try:
                    await asyncio.wait_for(self._active_run.task, timeout=3.0)
                except Exception:
                    self._active_run.task.cancel()
        for managed in self.sessions.values():
            try:
                managed.agent_session.finish()
            except Exception:
                pass
        if self._owned_llm is not None:
            await self._owned_llm.close()
            self._owned_llm = None
