"""Execution Engine 与任务预算追踪器。

处理顺序（见 docs/design.md 第 12 节与 docs/development_plan.md 阶段 3）：
1. 工具白名单存在性检查（ToolRegistry）
2. 参数 JSON 解析与 jsonschema 校验
3. 单工具 / 预算组调用次数上限检查
4. asyncio.wait_for 超时控制 + 可重试网络错误退避重试
5. 异常统一捕获并封装为结构化结果 {"ok": bool, "data"/"error", "retryable"}，绝不导致主循环崩溃
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from jsonschema import ValidationError, validate

from xhs_agent.config import BudgetConfig
from xhs_agent.harness.llm import ToolCallRequest
from xhs_agent.harness.registry import ToolRegistry, ToolSpec
from xhs_agent.xhs.mcp_client import MCPError
from xhs_agent.xhs.rate_limiter import RateLimitExceededError


@dataclass
class ToolExecutionResult:
    tool_call_id: str
    tool_name: str
    ok: bool
    data: Any = None
    error: str | None = None
    retryable: bool = False
    duration_ms: int = 0

    def to_tool_payload(self) -> dict[str, Any]:
        if self.ok:
            return {"ok": True, "data": self.data}
        return {
            "ok": False,
            "error": self.error or "未知错误",
            "retryable": self.retryable,
        }

    def summary(self) -> str:
        if not self.ok:
            return f"error={str(self.error)[:120]}"
        if isinstance(self.data, dict):
            if "notes" in self.data and isinstance(self.data["notes"], list):
                return f"keyword={self.data.get('keyword')!r} count={len(self.data['notes'])}"
            if "note" in self.data and "created" in self.data:
                return (
                    f"note={self.data.get('note')} status={self.data.get('status')} "
                    f"created={self.data.get('created')} dup={self.data.get('duplicates', 0)}"
                )
            if "note" in self.data and "title" in self.data:
                return (
                    f"note={self.data.get('note')} len={self.data.get('content_length', 0)} "
                    f"from_cache={self.data.get('from_cache', False)}"
                )
        return "ok"


@dataclass
class BudgetTracker:
    """单次任务预算与状态统计。"""

    max_steps: int = 25
    max_search_calls: int = 6
    max_detail_calls: int = 30
    max_consecutive_errors: int = 3

    steps_used: int = 0
    consecutive_errors: int = 0
    tool_calls_count: dict[str, int] = field(default_factory=dict)
    group_calls_count: dict[str, int] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: BudgetConfig) -> BudgetTracker:
        return cls(
            max_steps=cfg.max_steps,
            max_search_calls=cfg.max_search_calls,
            max_detail_calls=cfg.max_detail_calls,
            max_consecutive_errors=cfg.max_consecutive_errors,
        )

    def group_limit(self, group: str | None) -> int | None:
        if group == "search":
            return self.max_search_calls
        if group == "detail":
            return self.max_detail_calls
        return None

    def can_call(self, spec: ToolSpec) -> tuple[bool, str | None]:
        if spec.max_calls_per_task is not None:
            used = self.tool_calls_count.get(spec.name, 0)
            if used >= spec.max_calls_per_task:
                return (
                    False,
                    f"工具 '{spec.name}' 已达本次任务调用上限（{used}/{spec.max_calls_per_task}），请基于已有结果总结。",
                )
        if spec.budget_group is not None:
            limit = self.group_limit(spec.budget_group)
            if limit is not None:
                used = self.group_calls_count.get(spec.budget_group, 0)
                if used >= limit:
                    return (
                        False,
                        f"预算组 '{spec.budget_group}' 已达本次任务上限（{used}/{limit}），请基于已有结果总结。",
                    )
        return True, None

    def record_call(self, spec: ToolSpec, *, charge_group: bool = True) -> None:
        self.tool_calls_count[spec.name] = self.tool_calls_count.get(spec.name, 0) + 1
        if charge_group and spec.budget_group is not None:
            self.group_calls_count[spec.budget_group] = (
                self.group_calls_count.get(spec.budget_group, 0) + 1
            )

    def record_outcome(self, ok: bool) -> None:
        if ok:
            self.consecutive_errors = 0
        else:
            self.consecutive_errors += 1

    def exhausted(self) -> tuple[bool, str | None]:
        if self.consecutive_errors >= self.max_consecutive_errors:
            return (
                True,
                f"连续工具报错达到上限（{self.consecutive_errors}/{self.max_consecutive_errors}）",
            )
        if self.steps_used >= self.max_steps:
            return True, f"已达最大循环步数上限（{self.steps_used}/{self.max_steps}）"
        return False, None

    def snapshot(self) -> dict[str, Any]:
        return {
            "steps_used": self.steps_used,
            "max_steps": self.max_steps,
            "consecutive_errors": self.consecutive_errors,
            "tool_calls": dict(self.tool_calls_count),
            "budget_groups": dict(self.group_calls_count),
        }


BeforeToolHook = Callable[[ToolSpec, dict[str, Any]], Awaitable[None]]
AfterToolHook = Callable[[ToolSpec, dict[str, Any], ToolExecutionResult], Awaitable[None]]


class ExecutionEngine:
    """受控工具执行引擎。"""

    def __init__(
        self,
        registry: ToolRegistry,
        budget: BudgetTracker,
        *,
        max_retries_on_retryable: int = 1,
        retry_backoff_sec: float = 1.0,
        sleep_fn: Callable[[float], Awaitable[None]] = asyncio.sleep,
        before_hooks: list[BeforeToolHook] | None = None,
        after_hooks: list[AfterToolHook] | None = None,
    ) -> None:
        self.registry = registry
        self.budget = budget
        self.max_retries_on_retryable = int(max_retries_on_retryable)
        self.retry_backoff_sec = float(retry_backoff_sec)
        self._sleep = sleep_fn
        self.before_hooks: list[BeforeToolHook] = list(before_hooks or [])
        self.after_hooks: list[AfterToolHook] = list(after_hooks or [])

    async def execute(self, call: ToolCallRequest) -> ToolExecutionResult:
        t0 = time.monotonic()

        # 1. 白名单检查
        spec = self.registry.get(call.name)
        if spec is None:
            res = ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=f"工具 '{call.name}' 不存在或不在白名单内，允许的工具：{self.registry.names()}",
                retryable=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            self.budget.record_outcome(False)
            return res

        # 2. JSON 参数解析与 Schema 校验
        try:
            args = json.loads(call.arguments_raw) if call.arguments_raw.strip() else {}
        except json.JSONDecodeError as exc:
            res = ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=f"参数 JSON 解析失败: {exc.msg}。请按工具 Schema 重新传入合法 JSON 对象。",
                retryable=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            self.budget.record_outcome(False)
            return res

        if not isinstance(args, dict):
            res = ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                error="工具参数必须为 JSON 对象（object）。",
                retryable=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            self.budget.record_outcome(False)
            return res

        try:
            validate(instance=args, schema=spec.parameters)
        except ValidationError as exc:
            path = ".".join(str(p) for p in exc.path) or "(root)"
            res = ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=f"参数校验失败（字段 {path}）: {exc.message}。请修正参数后重试。",
                retryable=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            self.budget.record_outcome(False)
            return res

        # 3. 预算与调用上限检查
        allowed, limit_reason = self.budget.can_call(spec)
        if not allowed:
            res = ToolExecutionResult(
                tool_call_id=call.id,
                tool_name=call.name,
                ok=False,
                error=limit_reason,
                retryable=False,
                duration_ms=int((time.monotonic() - t0) * 1000),
            )
            # 达到调用上限属于软边界引导总结，不视为工具异常故障
            return res

        # 4. 执行工具（带超时、前后钩子与可重试错误退避重试）
        for hook in self.before_hooks:
            await hook(spec, args)

        attempts = self.max_retries_on_retryable + 1
        result: ToolExecutionResult | None = None

        for attempt in range(attempts):
            try:
                data = await asyncio.wait_for(spec.handler(**args), timeout=spec.timeout)
                from_cache = isinstance(data, dict) and bool(data.get("from_cache"))
                # 缓存命中的详情预览不消耗外部拉取配额
                self.budget.record_call(spec, charge_group=not from_cache)
                self.budget.record_outcome(True)
                result = ToolExecutionResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=True,
                    data=data,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                break
            except asyncio.TimeoutError:
                if attempt + 1 < attempts:
                    await self._sleep(self.retry_backoff_sec * (2**attempt))
                    continue
                self.budget.record_call(spec, charge_group=True)
                self.budget.record_outcome(False)
                result = ToolExecutionResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=False,
                    error=f"工具 '{call.name}' 执行超时（>{spec.timeout}s）。",
                    retryable=True,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            except MCPError as exc:
                if exc.retryable and attempt + 1 < attempts:
                    await self._sleep(self.retry_backoff_sec * (2**attempt))
                    continue
                self.budget.record_call(spec, charge_group=True)
                self.budget.record_outcome(False)
                result = ToolExecutionResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=False,
                    error=str(exc),
                    retryable=exc.retryable,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
            except RateLimitExceededError as exc:
                self.budget.record_outcome(False)
                result = ToolExecutionResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=False,
                    error=str(exc),
                    retryable=False,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                break
            except Exception as exc:
                self.budget.record_outcome(False)
                result = ToolExecutionResult(
                    tool_call_id=call.id,
                    tool_name=call.name,
                    ok=False,
                    error=str(exc),
                    retryable=False,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                )
                break

        assert result is not None
        for hook in self.after_hooks:
            await hook(spec, args, result)
        return result
