"""Agent Loop：主推理循环、工具执行闭环与预算优雅收尾。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xhs_agent.harness.context import ContextManager
from xhs_agent.harness.executor import BudgetTracker, ExecutionEngine, ToolExecutionResult
from xhs_agent.harness.llm import LLMClientProtocol, LLMResponse, TokenUsage
from xhs_agent.harness.registry import ToolRegistry
from xhs_agent.harness.trace import TraceLogger

BUDGET_WRAP_UP_PROMPT = "预算已用完，请基于已收集的结果直接给出总结，不要再调用工具。"


@dataclass
class AgentRunResult:
    session_id: str
    task: str
    answer: str
    steps: int
    stop_reason: str
    trace_path: Path
    usage: TokenUsage = field(default_factory=TokenUsage)
    llm_calls: int = 0
    tool_calls: dict[str, int] = field(default_factory=dict)
    budget_groups: dict[str, int] = field(default_factory=dict)


EventCallback = Callable[[str, dict[str, Any]], None]


class AgentLoop:
    """核心 Agent Loop（LLM 决策 ↔ Execution Engine 执行 ↔ Context 回填 ↔ Trace 记录）。"""

    def __init__(
        self,
        *,
        llm: LLMClientProtocol,
        registry: ToolRegistry,
        executor: ExecutionEngine,
        budget: BudgetTracker,
        system_prompt: str,
        traces_dir: Path | str = Path("traces"),
        max_context_chars: int = 48_000,
        keep_recent_turns: int = 6,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self.budget = budget
        self.system_prompt = system_prompt
        self.traces_dir = Path(traces_dir)
        self.max_context_chars = max_context_chars
        self.keep_recent_turns = keep_recent_turns

    def _emit(self, callback: EventCallback | None, event: str, payload: dict[str, Any]) -> None:
        if callback is not None:
            callback(event, payload)

    async def run(
        self,
        task: str,
        *,
        session_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> AgentRunResult:
        ctx = ContextManager(
            self.system_prompt,
            session_id=session_id,
            max_chars=self.max_context_chars,
            keep_recent_turns=self.keep_recent_turns,
        )
        ctx.add_user(task)
        trace = TraceLogger(ctx.session_id, traces_dir=self.traces_dir)
        trace.log(
            "session_start",
            task=task,
            tools=self.registry.names(),
            budget=self.budget.snapshot(),
        )

        total_usage = TokenUsage()
        llm_calls = 0
        tool_schemas = self.registry.schemas()
        stop_reason = "completed"
        final_answer = ""

        for step in range(1, self.budget.max_steps + 1):
            self.budget.steps_used = step
            messages = ctx.messages()
            trace.log("llm_request", step=step, message_count=len(messages))
            self._emit(on_event, "llm_request", {"step": step, "message_count": len(messages)})

            resp: LLMResponse = await self.llm.chat(messages, tools=tool_schemas)
            llm_calls += 1
            total_usage.add(resp.usage)

            trace.log(
                "llm_response",
                step=step,
                model=resp.model,
                finish_reason=resp.finish_reason,
                duration_ms=resp.duration_ms,
                usage=resp.usage.to_dict(),
                content_preview=resp.content[:200],
                tool_calls=[{"id": tc.id, "name": tc.name, "arguments": tc.arguments_raw} for tc in resp.tool_calls],
            )
            self._emit(
                on_event,
                "llm_response",
                {
                    "step": step,
                    "tool_call_count": len(resp.tool_calls),
                    "usage": resp.usage.to_dict(),
                    "duration_ms": resp.duration_ms,
                },
            )

            ctx.add_assistant(
                resp.content,
                [tc.to_openai_dict() for tc in resp.tool_calls] if resp.tool_calls else None,
            )

            if not resp.tool_calls:
                final_answer = resp.content
                stop_reason = "completed"
                break

            for tc in resp.tool_calls:
                trace.log("tool_call", step=step, tool_call_id=tc.id, tool=tc.name, args_raw=tc.arguments_raw)
                self._emit(
                    on_event,
                    "tool_call",
                    {"step": step, "tool": tc.name, "args_raw": tc.arguments_raw},
                )

                exec_res: ToolExecutionResult = await self.executor.execute(tc)
                payload = exec_res.to_tool_payload()
                ctx.add_tool_result(tc.id, payload)

                trace.log(
                    "tool_result",
                    step=step,
                    tool_call_id=tc.id,
                    tool=tc.name,
                    ok=exec_res.ok,
                    retryable=exec_res.retryable,
                    duration_ms=exec_res.duration_ms,
                    result_summary=exec_res.summary(),
                    error=exec_res.error,
                )
                self._emit(
                    on_event,
                    "tool_result",
                    {
                        "step": step,
                        "tool": tc.name,
                        "ok": exec_res.ok,
                        "summary": exec_res.summary(),
                        "duration_ms": exec_res.duration_ms,
                        "error": exec_res.error,
                    },
                )

                exhausted, reason = self.budget.exhausted()
                if exhausted and self.budget.consecutive_errors >= self.budget.max_consecutive_errors:
                    stop_reason = reason or "consecutive_errors"
                    break

            exhausted, reason = self.budget.exhausted()
            if exhausted:
                stop_reason = reason or "budget_exhausted"
                trace.log("budget_exhausted", step=step, reason=stop_reason, budget=self.budget.snapshot())
                self._emit(on_event, "budget_exhausted", {"step": step, "reason": stop_reason})
                break
        else:
            stop_reason = f"已达最大循环步数上限（{self.budget.max_steps}/{self.budget.max_steps}）"

        # 触发预算/上限退出时，不直接中断，而是注入系统提示让 LLM 做最终总结
        if not final_answer:
            ctx.add_system(BUDGET_WRAP_UP_PROMPT)
            wrap_step = self.budget.steps_used + 1
            messages = ctx.messages()
            trace.log("llm_request", step=wrap_step, message_count=len(messages), wrap_up=True)
            self._emit(on_event, "wrap_up", {"step": wrap_step, "reason": stop_reason})

            resp = await self.llm.chat(messages, tools=None)
            llm_calls += 1
            total_usage.add(resp.usage)
            final_answer = resp.content
            ctx.add_assistant(resp.content, None)

            trace.log(
                "llm_response",
                step=wrap_step,
                model=resp.model,
                finish_reason=resp.finish_reason,
                duration_ms=resp.duration_ms,
                usage=resp.usage.to_dict(),
                wrap_up=True,
                content_preview=resp.content[:200],
            )

        trace.log(
            "session_end",
            stop_reason=stop_reason,
            steps=self.budget.steps_used,
            llm_calls=llm_calls,
            usage=total_usage.to_dict(),
            budget=self.budget.snapshot(),
        )

        return AgentRunResult(
            session_id=ctx.session_id,
            task=task,
            answer=final_answer,
            steps=self.budget.steps_used,
            stop_reason=stop_reason,
            trace_path=trace.path,
            usage=total_usage,
            llm_calls=llm_calls,
            tool_calls=dict(self.budget.tool_calls_count),
            budget_groups=dict(self.budget.group_calls_count),
        )
