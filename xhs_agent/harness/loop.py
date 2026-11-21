"""Agent Loop：主推理循环、多轮会话支持、全链路用量/费用统计与预算优雅收尾。"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from xhs_agent.config import PricingConfig
from xhs_agent.harness.context import ContextManager
from xhs_agent.harness.executor import BudgetTracker, ExecutionEngine, ToolExecutionResult
from xhs_agent.harness.llm import LLMClientProtocol, LLMResponse, TokenUsage
from xhs_agent.harness.pricing import CostEstimate, estimate_cost
from xhs_agent.harness.registry import ToolRegistry
from xhs_agent.harness.trace import TraceLogger

logger = logging.getLogger("xhs_agent.loop")

BUDGET_WRAP_UP_PROMPT = "预算已用完，请基于已收集的结果直接给出总结，不要再调用工具。"


def _usage_delta(after: TokenUsage, before: TokenUsage) -> TokenUsage:
    return TokenUsage(
        prompt_tokens=max(0, after.prompt_tokens - before.prompt_tokens),
        completion_tokens=max(0, after.completion_tokens - before.completion_tokens),
        total_tokens=max(0, after.total_tokens - before.total_tokens),
        cache_hit_tokens=max(0, after.cache_hit_tokens - before.cache_hit_tokens),
        cache_miss_tokens=max(0, after.cache_miss_tokens - before.cache_miss_tokens),
    )


@dataclass
class AgentRunResult:
    session_id: str
    task: str
    answer: str
    steps: int
    stop_reason: str
    trace_path: Path
    usage: TokenUsage = field(default_factory=TokenUsage)  # 主 Agent LLM 用量
    llm_calls: int = 0  # 主 Agent LLM 调用次数
    extract_usage: TokenUsage = field(default_factory=TokenUsage)  # 独立抽取 LLM 用量
    extract_llm_calls: int = 0  # 独立抽取 LLM 调用次数
    total_usage: TokenUsage = field(default_factory=TokenUsage)  # 主 + 抽取合计用量
    total_llm_calls: int = 0  # 主 + 抽取合计调用次数
    cost: CostEstimate = field(
        default_factory=lambda: CostEstimate("CNY", 0.0, 0.0, 0.0, 0.0, 0.0)
    )
    tool_calls: dict[str, int] = field(default_factory=dict)
    budget_groups: dict[str, int] = field(default_factory=dict)


EventCallback = Callable[[str, dict[str, Any]], None]
ExtractStatsProvider = Callable[[], tuple[int, TokenUsage]]


class AgentLoop:
    """核心 Agent Loop（支持单次任务 run 与多轮会话 run_turn）。"""

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
        pricing: PricingConfig | None = None,
        extract_stats_fn: ExtractStatsProvider | None = None,
    ) -> None:
        self.llm = llm
        self.registry = registry
        self.executor = executor
        self.budget = budget
        self.system_prompt = system_prompt
        self.traces_dir = Path(traces_dir)
        self.max_context_chars = max_context_chars
        self.keep_recent_turns = keep_recent_turns
        self.pricing = pricing or PricingConfig()
        self._extract_stats_fn = extract_stats_fn

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
        """创建独立会话并执行单轮完整任务。"""
        ctx = ContextManager(
            self.system_prompt,
            session_id=session_id,
            max_chars=self.max_context_chars,
            keep_recent_turns=self.keep_recent_turns,
        )
        trace = TraceLogger(ctx.session_id, traces_dir=self.traces_dir)
        trace.log(
            "session_start",
            task=task,
            tools=self.registry.names(),
            budget=self.budget.snapshot(),
        )
        result = await self.run_turn(task, ctx=ctx, trace=trace, on_event=on_event, turn_index=1)
        trace.log(
            "session_end",
            stop_reason=result.stop_reason,
            steps=result.steps,
            llm_calls=result.llm_calls,
            extract_llm_calls=result.extract_llm_calls,
            total_llm_calls=result.total_llm_calls,
            usage=result.usage.to_dict(),
            extract_usage=result.extract_usage.to_dict(),
            total_usage=result.total_usage.to_dict(),
            cost=result.cost.to_dict(),
            budget=self.budget.snapshot(),
        )
        return result

    async def run_turn(
        self,
        user_input: str,
        *,
        ctx: ContextManager,
        trace: TraceLogger,
        on_event: EventCallback | None = None,
        turn_index: int = 1,
        cancel_event: asyncio.Event | None = None,
    ) -> AgentRunResult:
        """在既有 ContextManager 与 TraceLogger 上执行一轮完整推理（支持多轮交互对话与优雅取消）。"""
        ctx.add_user(user_input)
        trace.log("turn_start", turn=turn_index, user_input=user_input)

        ext_calls_before = 0
        ext_usage_before = TokenUsage()
        if self._extract_stats_fn is not None:
            ext_calls_before, ext_usage_before = self._extract_stats_fn()

        agent_usage = TokenUsage()
        llm_calls = 0
        tool_schemas = self.registry.schemas()
        stop_reason = "completed"
        final_answer = ""
        was_cancelled = False

        for step in range(1, self.budget.max_steps + 1):
            if cancel_event is not None and cancel_event.is_set():
                was_cancelled = True
                stop_reason = "cancelled"
                trace.log("cancelled", step=step, turn=turn_index)
                self._emit(on_event, "cancelled", {"step": step, "turn": turn_index})
                break

            self.budget.steps_used = step
            messages = ctx.messages()
            trace.log("llm_request", step=step, turn=turn_index, message_count=len(messages))
            self._emit(on_event, "llm_request", {"step": step, "turn": turn_index, "message_count": len(messages)})

            resp: LLMResponse = await self.llm.chat(messages, tools=tool_schemas)
            llm_calls += 1
            agent_usage.add(resp.usage)

            logger.debug(
                "LLM step=%d duration=%dms tokens=%d (cache_hit=%d) tool_calls=%d",
                step,
                resp.duration_ms,
                resp.usage.total_tokens,
                resp.usage.cache_hit_tokens,
                len(resp.tool_calls),
            )
            trace.log(
                "llm_response",
                step=step,
                turn=turn_index,
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
                    "turn": turn_index,
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
                if cancel_event is not None and cancel_event.is_set():
                    was_cancelled = True
                    stop_reason = "cancelled"
                    ctx.add_tool_result(tc.id, {"ok": False, "error": "任务已被用户取消", "retryable": False})
                    trace.log("cancelled", step=step, turn=turn_index, tool=tc.name)
                    self._emit(on_event, "cancelled", {"step": step, "turn": turn_index, "tool": tc.name})
                    continue

                trace.log(
                    "tool_call",
                    step=step,
                    turn=turn_index,
                    tool_call_id=tc.id,
                    tool=tc.name,
                    args_raw=tc.arguments_raw,
                )
                self._emit(
                    on_event,
                    "tool_call",
                    {"step": step, "turn": turn_index, "tool": tc.name, "args_raw": tc.arguments_raw},
                )

                exec_res: ToolExecutionResult = await self.executor.execute(tc)
                payload = exec_res.to_tool_payload()
                ctx.add_tool_result(tc.id, payload)

                trace.log(
                    "tool_result",
                    step=step,
                    turn=turn_index,
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
                        "turn": turn_index,
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

            if was_cancelled:
                break

            exhausted, reason = self.budget.exhausted()
            if exhausted:
                stop_reason = reason or "budget_exhausted"
                trace.log("budget_exhausted", step=step, turn=turn_index, reason=stop_reason, budget=self.budget.snapshot())
                self._emit(on_event, "budget_exhausted", {"step": step, "turn": turn_index, "reason": stop_reason})
                break
        else:
            stop_reason = f"已达最大循环步数上限（{self.budget.max_steps}/{self.budget.max_steps}）"

        if was_cancelled and not final_answer:
            final_answer = "任务已手动中止。此前已完成抽取的岗位数据均已安全保存至本地数据库。"
            ctx.add_assistant(final_answer, None)
        elif not final_answer:
            # 触发预算/上限退出时，不直接中断，而是注入系统提示让 LLM 做最终总结
            ctx.add_system(BUDGET_WRAP_UP_PROMPT)
            wrap_step = self.budget.steps_used + 1
            messages = ctx.messages()
            trace.log("llm_request", step=wrap_step, turn=turn_index, message_count=len(messages), wrap_up=True)
            self._emit(on_event, "wrap_up", {"step": wrap_step, "turn": turn_index, "reason": stop_reason})

            resp = await self.llm.chat(messages, tools=None)
            llm_calls += 1
            agent_usage.add(resp.usage)
            final_answer = resp.content
            ctx.add_assistant(resp.content, None)

            trace.log(
                "llm_response",
                step=wrap_step,
                turn=turn_index,
                model=resp.model,
                finish_reason=resp.finish_reason,
                duration_ms=resp.duration_ms,
                usage=resp.usage.to_dict(),
                wrap_up=True,
                content_preview=resp.content[:200],
            )

        ext_calls_delta = 0
        ext_usage_delta = TokenUsage()
        if self._extract_stats_fn is not None:
            ext_calls_after, ext_usage_after = self._extract_stats_fn()
            ext_calls_delta = max(0, ext_calls_after - ext_calls_before)
            ext_usage_delta = _usage_delta(ext_usage_after, ext_usage_before)

        combined_usage = TokenUsage()
        combined_usage.add(agent_usage)
        combined_usage.add(ext_usage_delta)
        combined_calls = llm_calls + ext_calls_delta
        cost = estimate_cost(combined_usage, self.pricing)

        trace.log(
            "turn_end",
            turn=turn_index,
            stop_reason=stop_reason,
            steps=self.budget.steps_used,
            llm_calls=llm_calls,
            extract_llm_calls=ext_calls_delta,
            total_llm_calls=combined_calls,
            usage=agent_usage.to_dict(),
            extract_usage=ext_usage_delta.to_dict(),
            total_usage=combined_usage.to_dict(),
            cost=cost.to_dict(),
            tool_calls=dict(self.budget.tool_calls_count),
        )

        return AgentRunResult(
            session_id=ctx.session_id,
            task=user_input,
            answer=final_answer,
            steps=self.budget.steps_used,
            stop_reason=stop_reason,
            trace_path=trace.path,
            usage=agent_usage,
            llm_calls=llm_calls,
            extract_usage=ext_usage_delta,
            extract_llm_calls=ext_calls_delta,
            total_usage=combined_usage,
            total_llm_calls=combined_calls,
            cost=cost,
            tool_calls=dict(self.budget.tool_calls_count),
            budget_groups=dict(self.budget.group_calls_count),
        )
