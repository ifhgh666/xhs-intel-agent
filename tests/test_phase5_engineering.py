"""阶段 5 工程化与增强离线测试套件（全程模拟 MCP + 模拟 LLM，零外部网络请求）。"""

from __future__ import annotations

import json
import logging
from io import StringIO
from pathlib import Path

import openpyxl
import pytest
from rich.console import Console

from tests.test_phase2_xhs import FixtureMCPClient
from tests.test_phase3_harness import ScriptedLLM, _assert_valid_tool_pairing
from xhs_agent.config import AppConfig, BudgetConfig, PricingConfig
from xhs_agent.harness import (
    AgentRuntime,
    BudgetTracker,
    ExecutionEngine,
    LLMResponse,
    TokenUsage,
    ToolCallRequest,
    ToolRegistry,
    ToolSpec,
    estimate_cost,
    is_retryable_tool_error,
)
from xhs_agent.jobs import JobStore
from xhs_agent.logging_setup import configure_logging
from xhs_agent.main import build_arg_parser, render_usage_summary
from xhs_agent.xhs import (
    MCPConnectionError,
    MCPError,
    MCPTimeoutError,
    RateLimiter,
    RateLimitExceededError,
    UnknownNoteError,
    XhsAdapter,
)


# ---------------------------------------------------------------------------
# 1. Token 与费用估算
# ---------------------------------------------------------------------------


def test_estimate_cost_calculates_cache_hit_miss_output_and_savings():
    pricing = PricingConfig(
        currency="CNY",
        input_cache_hit_per_1m=0.2,
        input_cache_miss_per_1m=2.0,
        output_per_1m=3.0,
    )
    usage = TokenUsage(
        prompt_tokens=1_000_000,
        completion_tokens=500_000,
        total_tokens=1_500_000,
        cache_hit_tokens=600_000,
        cache_miss_tokens=400_000,
    )
    cost = estimate_cost(usage, pricing)
    assert cost.currency == "CNY"
    assert cost.cache_hit_cost == pytest.approx(0.12)
    assert cost.cache_miss_cost == pytest.approx(0.80)
    assert cost.output_cost == pytest.approx(1.50)
    assert cost.total_cost == pytest.approx(2.42)
    # 若 600k 未命中缓存需 ¥1.20，命中只需 ¥0.12，省 ¥1.08
    assert cost.cache_savings == pytest.approx(1.08)


# ---------------------------------------------------------------------------
# 2. 错误重试策略分类（可重试 vs 不可重试）
# ---------------------------------------------------------------------------


def test_retryable_error_classification():
    assert is_retryable_tool_error(MCPConnectionError("reset")) is True
    assert is_retryable_tool_error(MCPTimeoutError("timeout")) is True
    assert is_retryable_tool_error(ConnectionResetError("drop")) is True
    assert is_retryable_tool_error(TimeoutError("os timeout")) is True

    assert is_retryable_tool_error(MCPError("business error", retryable=False)) is False
    assert is_retryable_tool_error(UnknownNoteError("n999")) is False
    assert is_retryable_tool_error(RateLimitExceededError("search", 50, 50)) is False
    assert is_retryable_tool_error(ValueError("bad input")) is False


async def test_executor_does_not_retry_non_retryable_business_errors():
    calls = 0

    async def bad_tool() -> dict:
        nonlocal calls
        calls += 1
        raise ValueError("invalid business param")

    reg = ToolRegistry()
    reg.register(
        ToolSpec(
            name="bad_tool",
            description="bad",
            parameters={"type": "object", "properties": {}},
            handler=bad_tool,
        )
    )
    engine = ExecutionEngine(reg, BudgetTracker(), max_retries_on_retryable=2, retry_backoff_sec=0.01)
    res = await engine.execute(ToolCallRequest("c1", "bad_tool", "{}"))
    assert not res.ok
    assert res.retryable is False
    assert calls == 1  # 不可重试错误绝不盲目重试


# ---------------------------------------------------------------------------
# 3. 完整端到端模拟（Mock MCP + Mock 主 LLM + Mock 抽取 LLM）：
#    搜索 → 抽取入库 → 联合费用统计 → 多轮追问 → mark_job → export_jobs
# ---------------------------------------------------------------------------


async def test_full_multiturn_end_to_end_with_cost_accounting_and_mark_job(tmp_path: Path):
    mock_mcp = FixtureMCPClient()
    db_path = tmp_path / "e2e.db"
    export_dir = tmp_path / "exports"
    traces_dir = tmp_path / "traces"

    # 独立抽取 LLM：为 n1 和 n4 返回真实结构化岗位与 token 用量
    extractor_llm = ScriptedLLM(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "jobs": [
                            {
                                "company": "腾讯",
                                "position": "AI Agent产品实习生",
                                "city": "广州",
                                "graduation_year": "2028届及以后",
                                "attendance_days": "每周至少到岗4天",
                                "apply_method": "susiesun124@gmail.com",
                                "evidence": "2028届及以后毕业的本科或硕士在读生",
                                "confidence": 0.96,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(1000, 200, 1200, cache_hit_tokens=400, cache_miss_tokens=600),
            ),
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "jobs": [
                            {
                                "company": "趣丸千音",
                                "position": "AI Agent 产品实习生",
                                "city": "广州天河",
                                "attendance_days": "每周可实习5天",
                                "evidence": "招 AI Agent 产品实习生，广州天河",
                                "confidence": 0.92,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(1100, 180, 1280, cache_hit_tokens=500, cache_miss_tokens=600),
            ),
        ]
    )

    # 主 LLM 剧本（跨两轮交互对话）：
    # Turn 1: search_notes -> extract_and_save_jobs(n1, n4) -> query_jobs -> 回复
    # Turn 2: 追问“把 1 号标为感兴趣并导出感兴趣岗位，另外尝试标不存在的 999 号” -> mark_job(1, interested) + mark_job(999, applied) -> export_jobs(status=interested) -> 回复
    main_llm = ScriptedLLM(
        [
            # Turn 1 Step 1
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("t1_c1", "search_notes", '{"keyword": "广州 AI实习"}')],
                usage=TokenUsage(800, 50, 850, cache_hit_tokens=0, cache_miss_tokens=800),
            ),
            # Turn 1 Step 2
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("t1_c2", "extract_and_save_jobs", '{"note": "n1"}'),
                    ToolCallRequest("t1_c3", "extract_and_save_jobs", '{"note": "n4"}'),
                ],
                usage=TokenUsage(1200, 60, 1260, cache_hit_tokens=800, cache_miss_tokens=400),
            ),
            # Turn 1 Step 3
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("t1_c4", "query_jobs", '{"city": "广州"}')],
                usage=TokenUsage(1400, 40, 1440, cache_hit_tokens=1100, cache_miss_tokens=300),
            ),
            # Turn 1 Step 4 (Final answer)
            LLMResponse(
                content="已为你找到 2 个广州 AI Agent 实习岗位：ID=1 腾讯（每周4天）、ID=2 趣丸千音（每周5天）。",
                tool_calls=[],
                usage=TokenUsage(1600, 120, 1720, cache_hit_tokens=1300, cache_miss_tokens=300),
            ),
            # Turn 2 Step 1 (多轮追问：无需重新搜索，直接标记状态)
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("t2_c1", "mark_job", '{"job_id": 1, "status": "interested"}'),
                    ToolCallRequest("t2_c2", "mark_job", '{"job_id": 999, "status": "applied"}'),
                ],
                usage=TokenUsage(1800, 70, 1870, cache_hit_tokens=1600, cache_miss_tokens=200),
            ),
            # Turn 2 Step 2 (导出感兴趣岗位)
            LLMResponse(
                content="",
                tool_calls=[
                    ToolCallRequest("t2_c3", "export_jobs", '{"status": "interested", "path": "interested.xlsx"}')
                ],
                usage=TokenUsage(2000, 50, 2050, cache_hit_tokens=1800, cache_miss_tokens=200),
            ),
            # Turn 2 Step 3 (Final answer)
            LLMResponse(
                content="已将 1 号岗位（腾讯 AI Agent产品实习生）标记为感兴趣并导出至 interested.xlsx；999 号岗位不存在。",
                tool_calls=[],
                usage=TokenUsage(2100, 90, 2190, cache_hit_tokens=1900, cache_miss_tokens=200),
            ),
        ]
    )

    with JobStore(db_path) as store:
        adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0, counter=store))
        cfg = AppConfig(budget=BudgetConfig(max_steps=10, max_search_calls=5, max_detail_calls=10))

        async with AgentRuntime(
            cfg,
            adapter=adapter,
            llm=main_llm,
            extractor_llm=extractor_llm,
            store=store,
            export_dir=export_dir,
            traces_dir=traces_dir,
        ) as rt:
            assert "mark_job" in rt.registry.names()
            session = rt.new_session(session_id="s_multiturn_e2e")

            # Turn 1
            r1 = await session.ask("帮我找广州 AI Agent 实习")
            assert r1.stop_reason == "completed"
            assert r1.llm_calls == 4
            assert r1.extract_llm_calls == 2
            assert r1.total_llm_calls == 6
            assert r1.total_usage.total_tokens == (850 + 1260 + 1440 + 1720) + (1200 + 1280)
            assert r1.cost.total_cost > 0
            assert r1.cost.cache_savings > 0

            # Turn 2（多轮追问）
            r2 = await session.ask("把 1 号岗位标为感兴趣，尝试标 999 号，并只导出感兴趣的岗位")
            assert r2.stop_reason == "completed"
            assert r2.llm_calls == 3
            assert r2.extract_llm_calls == 0
            assert r2.tool_calls == {"mark_job": 1, "export_jobs": 1}

            # 验证多轮上下文保持完整且工具配对协议合法
            _assert_valid_tool_pairing(session.ctx.messages())
            session.finish()

            # 验证会话累计用量与费用
            assert session.turns == 2
            assert session.total_llm_calls == 9
            assert session.total_usage.total_tokens == r1.total_usage.total_tokens + r2.total_usage.total_tokens
            assert session.total_cost.total_cost == pytest.approx(r1.cost.total_cost + r2.cost.total_cost)

        # 验证数据库状态已更新且导出的 Excel 仅包含 status=interested 的 1 条记录
        interested = store.query_jobs(status="interested")
        assert len(interested) == 1 and interested[0].company == "腾讯"

        wb = openpyxl.load_workbook(export_dir / "interested.xlsx")
        ws = wb.active
        assert ws.max_row == 2  # 表头 + 1 条感兴趣岗位

        # 验证单会话 Trace 完整记录了 2 轮对话及费用统计
        events = [json.loads(line) for line in (traces_dir / "s_multiturn_e2e.jsonl").read_text(encoding="utf-8").splitlines()]
        types = [e["type"] for e in events]
        assert types.count("turn_start") == 2
        assert types.count("turn_end") == 2
        assert events[-1]["type"] == "session_end"
        assert events[-1]["cost"]["total_cost"] > 0

        # 验证用法汇总表格可正常渲染输出
        buf = StringIO()
        render_usage_summary(Console(file=buf, force_terminal=False), r1, db_total_jobs=2)
        rendered = buf.getvalue()
        assert "预估费用" in rendered and "主 Agent 4 + 抽取 2" in rendered and "CNY" in rendered


# ---------------------------------------------------------------------------
# 4. CLI 参数解析与日志分级
# ---------------------------------------------------------------------------


def test_cli_arg_parser_and_logging_levels():
    parser = build_arg_parser()
    args_single = parser.parse_args(["找广州实习", "--verbose", "--max-search", "3"])
    assert args_single.task == "找广州实习"
    assert args_single.verbose is True
    assert args_single.interactive is False
    assert args_single.max_search == 3

    args_inter = parser.parse_args(["-i", "-v"])
    assert args_inter.interactive is True
    assert args_inter.verbose is True

    logger_quiet = configure_logging(verbose=False)
    assert logger_quiet.level == logging.WARNING
    logger_verbose = configure_logging(verbose=True)
    assert logger_verbose.level == logging.DEBUG
