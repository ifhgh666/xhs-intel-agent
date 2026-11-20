"""阶段 3 Harness 核心离线单元测试与故障注入测试（使用录制 fixtures + MockLLM）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from tests.test_phase2_xhs import FixtureMCPClient
from xhs_agent.config import AppConfig, BudgetConfig
from xhs_agent.harness import (
    BUDGET_WRAP_UP_PROMPT,
    AgentLoop,
    AgentRuntime,
    BudgetTracker,
    ContextManager,
    ExecutionEngine,
    LLMResponse,
    StartupPrecheckError,
    TokenUsage,
    ToolCallRequest,
    ToolRegistry,
    ToolSpec,
    TraceLogger,
)
from xhs_agent.xhs import MCPConnectionError, RateLimiter, XhsAdapter


class ScriptedLLM:
    """按预设剧本响应的 Mock LLM，记录每次收到的 messages 与 tools。"""

    def __init__(self, responses: list[LLMResponse]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.calls.append({"messages": list(messages), "tools": tools})
        if not self._responses:
            return LLMResponse(content="默认总结：无更多脚本响应。", usage=TokenUsage(10, 10, 20))
        return self._responses.pop(0)


def _assert_valid_tool_pairing(messages: list[dict[str, Any]]) -> None:
    """验证消息历史严格满足 OpenAI/DeepSeek 的 assistant(tool_calls) 与 tool 消息成对协议。"""
    i = 0
    while i < len(messages):
        m = messages[i]
        assert m["role"] != "tool", f"出现孤立的 tool 消息（索引 {i}）"
        if m["role"] == "assistant" and m.get("tool_calls"):
            expected_ids = [tc["id"] for tc in m["tool_calls"]]
            actual_ids = []
            i += 1
            while i < len(messages) and messages[i]["role"] == "tool":
                actual_ids.append(messages[i]["tool_call_id"])
                i += 1
            assert actual_ids == expected_ids, f"tool_call_id 不匹配: {actual_ids} != {expected_ids}"
        else:
            i += 1


# ---------------------------------------------------------------------------
# 1. ToolRegistry & TraceLogger
# ---------------------------------------------------------------------------


def test_registry_register_and_openai_schemas():
    async def dummy(**kwargs):
        return kwargs

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="search_notes",
            description="搜索笔记",
            parameters={"type": "object", "properties": {"keyword": {"type": "string"}}, "required": ["keyword"]},
            handler=dummy,
        )
    )
    assert reg.has("search_notes")
    assert len(reg) == 1
    schemas = reg.schemas()
    assert schemas[0]["type"] == "function"
    assert schemas[0]["function"]["name"] == "search_notes"

    with pytest.raises(ValueError, match="已注册"):
        reg.register(ToolSpec(name="search_notes", description="dup", parameters={}, handler=dummy))


def test_trace_logger_writes_valid_jsonl(tmp_path: Path):
    trace = TraceLogger("s_test01", traces_dir=tmp_path)
    trace.log("session_start", task="测试")
    trace.log("tool_call", step=1, tool="search_notes", args={"keyword": "广州"})
    events = trace.read_events()
    assert len(events) == 2
    assert events[0]["session"] == "s_test01"
    assert events[1]["step"] == 1


# ---------------------------------------------------------------------------
# 2. ContextManager 成对裁剪
# ---------------------------------------------------------------------------


def test_context_manager_paired_trimming_preserves_protocol():
    ctx = ContextManager("system prompt", session_id="s_ctx", max_chars=900, keep_recent_turns=2)
    ctx.add_user("找广州 AI 实习")

    for i in range(6):
        tc_id = f"call_{i}"
        ctx.add_assistant(
            "",
            [
                {
                    "id": tc_id,
                    "type": "function",
                    "function": {"name": "search_notes", "arguments": json.dumps({"keyword": f"kw_{i}"})},
                }
            ],
        )
        # 写入较大 payload 触发裁剪
        ctx.add_tool_result(
            tc_id,
            {
                "ok": True,
                "data": {
                    "keyword": f"kw_{i}",
                    "notes": [{"note": f"n{j}", "title": "很长的标题" * 10} for j in range(5)],
                },
            },
        )

    trimmed = ctx.messages()
    assert trimmed[0]["role"] == "system"
    assert trimmed[1]["role"] == "user"
    _assert_valid_tool_pairing(trimmed)
    assert len(json.dumps(trimmed, ensure_ascii=False)) <= 900 or len(trimmed) < len(ctx.raw_messages())


# ---------------------------------------------------------------------------
# 3. ExecutionEngine 校验 / 超时 / 限流 / 重试 / 异常隔离
# ---------------------------------------------------------------------------


async def test_executor_handles_unknown_tool_bad_json_schema_limit_timeout_and_retry():
    reg = ToolRegistry()
    flaky_attempts = 0
    hook_events: list[str] = []

    async def ok_tool(keyword: str) -> dict[str, Any]:
        return {"keyword": keyword}

    async def slow_tool() -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"done": True}

    async def flaky_tool() -> dict[str, Any]:
        nonlocal flaky_attempts
        flaky_attempts += 1
        if flaky_attempts == 1:
            raise MCPConnectionError("temporary drop")
        return {"recovered": True}

    reg.register(
        ToolSpec(
            name="search_notes",
            description="search",
            parameters={
                "type": "object",
                "properties": {"keyword": {"type": "string", "minLength": 1}},
                "required": ["keyword"],
                "additionalProperties": False,
            },
            handler=ok_tool,
            max_calls_per_task=1,
            budget_group="search",
        )
    )
    reg.register(
        ToolSpec(
            name="slow_tool",
            description="slow",
            parameters={"type": "object", "properties": {}},
            handler=slow_tool,
            timeout=0.05,
        )
    )
    reg.register(
        ToolSpec(
            name="flaky_tool",
            description="flaky",
            parameters={"type": "object", "properties": {}},
            handler=flaky_tool,
        )
    )

    async def before_h(spec, args):
        hook_events.append(f"before:{spec.name}")

    async def after_h(spec, args, res):
        hook_events.append(f"after:{spec.name}:{res.ok}")

    budget = BudgetTracker(max_steps=10, max_search_calls=1, max_consecutive_errors=5)
    engine = ExecutionEngine(
        reg,
        budget,
        max_retries_on_retryable=1,
        retry_backoff_sec=0.01,
        before_hooks=[before_h],
        after_hooks=[after_h],
    )

    # 1) 不在白名单的工具
    r_unknown = await engine.execute(ToolCallRequest("c1", "publish_content", "{}"))
    assert not r_unknown.ok and "不在白名单" in (r_unknown.error or "")

    # 2) 非法 JSON
    r_bad_json = await engine.execute(ToolCallRequest("c2", "search_notes", "{bad json"))
    assert not r_bad_json.ok and "JSON 解析失败" in (r_bad_json.error or "")

    # 3) Schema 校验失败（缺必填参数）
    r_schema = await engine.execute(ToolCallRequest("c3", "search_notes", "{}"))
    assert not r_schema.ok and "参数校验失败" in (r_schema.error or "")

    # 4) 正常调用 + 触发单工具上限
    r_ok = await engine.execute(ToolCallRequest("c4", "search_notes", '{"keyword": "广州"}'))
    assert r_ok.ok and r_ok.data == {"keyword": "广州"}
    assert budget.consecutive_errors == 0

    r_limit = await engine.execute(ToolCallRequest("c5", "search_notes", '{"keyword": "深圳"}'))
    assert not r_limit.ok and "上限" in (r_limit.error or "")

    # 5) 超时捕获（不崩溃，retryable=True）
    r_timeout = await engine.execute(ToolCallRequest("c6", "slow_tool", "{}"))
    assert not r_timeout.ok and r_timeout.retryable and "超时" in (r_timeout.error or "")

    # 6) 可重试网络错误自动重试成功
    r_retry = await engine.execute(ToolCallRequest("c7", "flaky_tool", "{}"))
    assert r_retry.ok and r_retry.data == {"recovered": True}
    assert flaky_attempts == 2
    assert "before:flaky_tool" in hook_events and "after:flaky_tool:True" in hook_events


# ---------------------------------------------------------------------------
# 4. AgentLoop + AgentRuntime 端到端（正常流 + 故障恢复 + 预算收尾 + 预检失败）
# ---------------------------------------------------------------------------


async def test_runtime_end_to_end_multi_search_preview_and_fault_recovery(tmp_path: Path):
    mock_mcp = FixtureMCPClient()
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    # 剧本：
    # Step 1: 拆 2 个关键词搜索（含故意一次错误参数 + 一次正常搜索）
    # Step 2: 修正搜索 + 故意查不存在的 n999 + 查真实 n1
    # Step 3: 输出最终总结
    llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("tc_1", "search_notes", '{"keyword": "广州 AI实习"}'),
                    ToolCallRequest("tc_2", "search_notes", '{"wrong_key": "RAG 实习"}'),
                ],
                usage=TokenUsage(200, 30, 230, cache_hit_tokens=0, cache_miss_tokens=200),
            ),
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("tc_3", "search_notes", '{"keyword": "RAG 实习"}'),
                    ToolCallRequest("tc_4", "get_note_detail", '{"note": "n999"}'),
                    ToolCallRequest("tc_5", "get_note_detail", '{"note": "n1"}'),
                ],
                usage=TokenUsage(500, 40, 540, cache_hit_tokens=200, cache_miss_tokens=300),
            ),
            LLMResponse(
                content="已为你汇总广州 AI Agent 实习岗位：腾讯微信小微 AI Agent 产品实习生（2028届，广州，n1）。",
                tool_calls=[],
                usage=TokenUsage(800, 60, 860, cache_hit_tokens=500, cache_miss_tokens=300),
            ),
        ]
    )

    cfg = AppConfig(budget=BudgetConfig(max_steps=10, max_search_calls=4, max_detail_calls=10, max_consecutive_errors=3))
    async with AgentRuntime(cfg, adapter=adapter, llm=llm, traces_dir=tmp_path) as rt:
        res = await rt.run_task("帮我找广州 AI Agent 实习，2028 届")

    assert res.stop_reason == "completed"
    assert res.steps == 3
    assert "腾讯" in res.answer
    assert res.tool_calls.get("search_notes") == 2
    assert res.tool_calls.get("get_note_detail") == 1
    assert res.usage.cache_hit_tokens == 700
    assert res.trace_path.exists()

    # 验证 LLM 确实看到了错误反馈并恢复
    second_call_msgs = llm.calls[1]["messages"]
    _assert_valid_tool_pairing(second_call_msgs)
    err_tool_msgs = [m for m in second_call_msgs if m["role"] == "tool" and '"ok": false' in m["content"]]
    assert len(err_tool_msgs) == 1

    third_call_msgs = llm.calls[2]["messages"]
    _assert_valid_tool_pairing(third_call_msgs)
    assert any("未知笔记" in m["content"] for m in third_call_msgs if m["role"] == "tool")

    # 验证 trace 完整记录全流程
    events = [json.loads(line) for line in res.trace_path.read_text(encoding="utf-8").splitlines()]
    event_types = [e["type"] for e in events]
    assert event_types[0] == "session_start"
    assert event_types[-1] == "session_end"
    assert event_types.count("tool_call") == 5
    assert event_types.count("tool_result") == 5


async def test_agent_loop_graceful_wrap_up_on_max_steps_and_consecutive_errors(tmp_path: Path):
    mock_mcp = FixtureMCPClient()
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    # 1) 达到 max_steps=1 后注入“预算已用完，请总结”并不再传 tools
    llm_steps = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("tc_1", "search_notes", '{"keyword": "广州 AI实习"}')],
                usage=TokenUsage(100, 20, 120),
            ),
            LLMResponse(
                content="预算到达上限，基于已搜索到的 20 条笔记为你先汇总标题列表。",
                tool_calls=[],
                usage=TokenUsage(200, 40, 240),
            ),
        ]
    )
    cfg = AppConfig(budget=BudgetConfig(max_steps=1, max_search_calls=6, max_detail_calls=10))
    async with AgentRuntime(cfg, adapter=adapter, llm=llm_steps, traces_dir=tmp_path) as rt:
        res = await rt.run_task("找实习")

    assert "最大循环步数" in res.stop_reason
    assert "预算到达上限" in res.answer
    assert llm_steps.calls[-1]["tools"] is None
    assert llm_steps.calls[-1]["messages"][-1]["content"] == BUDGET_WRAP_UP_PROMPT

    # 2) 连续 3 次工具报错触发提前收尾
    llm_errs = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("e1", "get_note_detail", '{"note": "n991"}'),
                    ToolCallRequest("e2", "get_note_detail", '{"note": "n992"}'),
                    ToolCallRequest("e3", "get_note_detail", '{"note": "n993"}'),
                ],
                usage=TokenUsage(100, 20, 120),
            ),
            LLMResponse(
                content="连续工具调用失败，已安全收尾。",
                tool_calls=[],
                usage=TokenUsage(150, 20, 170),
            ),
        ]
    )
    cfg2 = AppConfig(budget=BudgetConfig(max_steps=10, max_consecutive_errors=3))
    async with AgentRuntime(cfg2, adapter=adapter, llm=llm_errs, traces_dir=tmp_path) as rt:
        res2 = await rt.run_task("触发连续错误")

    assert "连续工具报错" in res2.stop_reason
    assert "安全收尾" in res2.answer


async def test_runtime_precheck_blocks_when_not_logged_in(tmp_path: Path):
    mock_mcp = FixtureMCPClient()
    mock_mcp._login_text = "❌ 未登录"
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))
    llm = ScriptedLLM([])

    with pytest.raises(StartupPrecheckError, match="尚未登录"):
        async with AgentRuntime(AppConfig(), adapter=adapter, llm=llm, traces_dir=tmp_path):
            pass
