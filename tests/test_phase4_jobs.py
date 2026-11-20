"""阶段 4 岗位抽取、去重、SQLite 存储与 Excel 导出离线单元测试。"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from tests.test_phase2_xhs import FixtureMCPClient
from tests.test_phase3_harness import ScriptedLLM
from xhs_agent.config import AppConfig, BudgetConfig, LLMConfig
from xhs_agent.harness import AgentRuntime, LLMResponse, TokenUsage, ToolCallRequest
from xhs_agent.jobs import (
    Internship,
    JobExtractor,
    JobRecord,
    JobStore,
    JobTools,
    is_deadline_expired,
    is_note_stale,
    make_dedup_key,
    verify_evidence,
)
from xhs_agent.xhs import RateLimiter, XhsAdapter


# ---------------------------------------------------------------------------
# 1. 模型、证据校验、去重键与时效规则
# ---------------------------------------------------------------------------


def test_internship_normalizes_empty_strings_and_skills():
    item = Internship(
        company=" 腾讯 ",
        position="AI Agent 实习生",
        city="  ",
        salary="null",
        skills="Python, RAG、LLM",
        evidence="参与微信小微中ai调用一方能力",
        confidence=0.9,
    )
    assert item.company == "腾讯"
    assert item.city is None
    assert item.salary is None
    assert item.skills == ["Python", "RAG", "LLM"]


def test_verify_evidence_ignores_whitespace_and_punct_and_rejects_hallucination():
    src = "【任职要求】\n1. 2028届及以后毕业的本科或硕士在读生，每周至少到岗 4 天。"
    assert verify_evidence("2028届及以后毕业的本科或硕士在读生", src) is True
    assert verify_evidence("2028届及以后毕业的本科或硕士在读生 每周至少到岗4天", src) is True
    assert verify_evidence("月薪30000元包住宿", src) is False
    assert verify_evidence("", src) is False


def test_make_dedup_key_merges_variants_and_skips_missing_company_or_position():
    k1 = make_dedup_key("小米", "AI产品经理实习生", "广州")
    k2 = make_dedup_key("小米集团", "ai产品经理", "广州市天河区")
    assert k1 is not None and k1 == k2

    assert make_dedup_key(None, "AI技术实习生", "广州") is None
    assert make_dedup_key("腾讯", None, "广州") is None


def test_freshness_staleness_and_deadline_expiry():
    now = datetime(2026, 11, 20, tzinfo=timezone.utc)
    assert is_note_stale(now - timedelta(days=79), now=now, stale_days=60) is True
    assert is_note_stale(now - timedelta(days=10), now=now, stale_days=60) is False

    today = date(2026, 11, 20)
    assert is_deadline_expired("2026年10月15日截止", today=today) is True
    assert is_deadline_expired("2026-12-31", today=today) is False
    assert is_deadline_expired("招满即止", today=today) is False


# ---------------------------------------------------------------------------
# 2. SQLite JobStore（notes / jobs / daily_usage）
# ---------------------------------------------------------------------------


def test_job_store_persists_notes_jobs_dedup_merge_and_daily_usage(tmp_path: Path):
    db_path = tmp_path / "test.db"
    with JobStore(db_path) as store:
        # daily_usage 持久化
        assert store.get_count("2026-11-20", "search") == 0
        assert store.increment("2026-11-20", "search") == 1
        assert store.increment("2026-11-20", "search", 2) == 3

        # notes 表持久化与已处理标记
        store.upsert_note(feed_id="f1", xsec_token="tok1", title="测试帖")
        assert store.is_note_processed("f1") is False
        store.mark_note_processed("f1")
        assert store.is_note_processed("f1") is True

        # jobs 表跨账号同岗位合并：保留最早来源，追加 extra_sources，补全缺失字段
        now = datetime.now(timezone.utc)
        j1 = JobRecord(
            company="小米",
            position="AI产品经理实习生",
            city="广州",
            graduation_year="26-28届",
            salary=None,
            evidence="小米实习捞继任",
            confidence=0.9,
            note_id="n_early",
            source_url="https://www.xiaohongshu.com/explore/n_early",
            crawled_at=now,
            dedup_key=make_dedup_key("小米", "AI产品经理实习生", "广州"),
        )
        saved1, created1 = store.upsert_job(j1)
        assert created1 is True and saved1.id is not None

        j2 = JobRecord(
            company="小米",
            position="ai产品经理",
            city="广州市",
            salary="本科300/天",
            evidence="小米捞实习继任",
            confidence=0.9,
            note_id="n_later",
            source_url="https://www.xiaohongshu.com/explore/n_later",
            crawled_at=now,
            dedup_key=make_dedup_key("小米", "ai产品经理", "广州市"),
        )
        saved2, created2 = store.upsert_job(j2)
        assert created2 is False
        assert saved2.id == saved1.id
        assert saved2.source_url == "https://www.xiaohongshu.com/explore/n_early"
        assert saved2.extra_sources == ["https://www.xiaohongshu.com/explore/n_later"]
        assert saved2.salary == "本科300/天"
        assert store.count_jobs() == 1

        # query_jobs 条件筛选
        queried = store.query_jobs(city="广州", keyword="小米", graduation_year="28")
        assert len(queried) == 1
        assert store.update_job_status(saved1.id, "interested") is True
        assert store.query_jobs(status="interested")[0].status == "interested"


# ---------------------------------------------------------------------------
# 3. JobExtractor（JSON 重试 + 编造证据丢弃 + needs_image）
# ---------------------------------------------------------------------------


async def test_job_extractor_retries_on_bad_json_and_drops_unverified_evidence():
    llm = ScriptedLLM(
        [
            # 第 1 次返回非法 JSON，触发自动重试
            LLMResponse(content="not valid json {", usage=TokenUsage(50, 10, 60)),
            # 第 2 次返回 2 条岗位：1 条证据真实、1 条证据编造
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "jobs": [
                            {
                                "company": "科大讯飞",
                                "position": "AI医疗算法大模型实习生",
                                "city": "广州南沙区",
                                "evidence": "岗位名称：AI医疗算法大模型实习生",
                                "confidence": 0.95,
                            },
                            {
                                "company": "编造公司",
                                "position": "首席科学家实习",
                                "city": "火星",
                                "evidence": "完全不存在于原文的假证据片段",
                                "confidence": 0.99,
                            },
                        ],
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(80, 50, 130),
            ),
        ]
    )
    extractor = JobExtractor(llm, LLMConfig())
    res = await extractor.extract(
        title="急招",
        desc="岗位名称：AI医疗算法大模型实习生\n工作地点：广州南沙区\n工作要求如图",
        image_count=2,
    )

    assert res.retries_used == 1
    assert len(res.jobs) == 1
    assert res.jobs[0].company == "科大讯飞"
    assert res.dropped_unverified == 1
    assert res.needs_image is True  # 启发式识别“工作要求如图”


# ---------------------------------------------------------------------------
# 4. JobTools + AgentRuntime 端到端（去重 + Excel 导出 + 主上下文不暴露正文）
# ---------------------------------------------------------------------------


async def test_extract_and_save_jobs_dedup_export_and_context_isolation(tmp_path: Path):
    mock_mcp = FixtureMCPClient()
    db_path = tmp_path / "jobs.db"
    export_dir = tmp_path / "exports"

    with JobStore(db_path) as store:
        limiter = RateLimiter(min_interval_sec=0, jitter_sec=0, counter=store)
        adapter = XhsAdapter(mock_mcp, rate_limiter=limiter)

        # 独立抽取 LLM：针对 n1 (腾讯) 和 n11/n17 (小米重复帖) 返回真实抽取 JSON
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
                                    "deadline": "2026-01-01",  # 故意测试过期 deadline -> status=expired
                                    "apply_method": "susiesun124@gmail.com",
                                    "evidence": "2028届及以后毕业的本科或硕士在读生",
                                    "confidence": 0.95,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
                LLMResponse(
                    content=json.dumps(
                        {
                            "needs_image": False,
                            "jobs": [
                                {
                                    "company": "小米",
                                    "position": "AI产品经理实习生",
                                    "city": "广州",
                                    "graduation_year": "26-28届",
                                    "evidence": "小米实习捞继任 可约寒假 300/d",
                                    "confidence": 0.9,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
                LLMResponse(
                    content=json.dumps(
                        {
                            "needs_image": False,
                            "jobs": [
                                {
                                    "company": "小米",
                                    "position": "AI产品经理",
                                    "city": "广州",
                                    "salary": "本科生300/天",
                                    "evidence": "热招岗位 策划、人力资源、数据分析、市场运营、财务、广告投放、AI产品经理",
                                    "confidence": 0.9,
                                }
                            ],
                        },
                        ensure_ascii=False,
                    )
                ),
            ]
        )

        # 主 LLM 剧本：搜索 → 抽取 n1、n11、n17 → 重复抽取 n1（验证跳过）→ query_jobs → export_jobs → 总结
        main_llm = ScriptedLLM(
            [
                LLMResponse(
                    content="",
                    tool_calls=[ToolCallRequest("c1", "search_notes", '{"keyword": "广州 AI实习"}')],
                ),
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest("c2", "extract_and_save_jobs", '{"note": "n1"}'),
                        ToolCallRequest("c3", "extract_and_save_jobs", '{"note": "n11"}'),
                        ToolCallRequest("c4", "extract_and_save_jobs", '{"note": "n17"}'),
                    ],
                ),
                LLMResponse(
                    content="",
                    tool_calls=[
                        ToolCallRequest("c5", "extract_and_save_jobs", '{"note": "n1"}'),
                        ToolCallRequest("c6", "query_jobs", '{"city": "广州"}'),
                        ToolCallRequest("c7", "export_jobs", '{"city": "广州", "path": "gz_jobs.xlsx"}'),
                    ],
                ),
                LLMResponse(
                    content="已收集并导出 2 个去重后的广州岗位（腾讯 AI Agent产品实习生、小米 AI产品经理实习生）。",
                    tool_calls=[],
                ),
            ]
        )

        cfg = AppConfig(budget=BudgetConfig(max_steps=10, max_search_calls=5, max_detail_calls=15))
        async with AgentRuntime(
            cfg,
            adapter=adapter,
            llm=main_llm,
            extractor_llm=extractor_llm,
            store=store,
            export_dir=export_dir,
            traces_dir=tmp_path / "traces",
        ) as rt:
            res = await rt.run_task("帮我找广州 AI 实习并导出 Excel")

        assert res.stop_reason == "completed"
        assert store.count_jobs() == 2  # n11 和 n17 的小米同岗位合并为 1 条！
        # 验证 MCP 只拉了 3 次详情（重复调用 n1 直接走 processed_at 跳过）
        detail_calls = [args for name, args in mock_mcp.calls if name == "get_feed_detail"]
        assert len(detail_calls) == 3

        # 验证过期 deadline 自动设为 expired
        tencent_jobs = store.query_jobs(keyword="腾讯")
        assert len(tencent_jobs) == 1
        assert tencent_jobs[0].status == "expired"

        # 验证 Excel 文件真实生成、可由 openpyxl 打开且含来源链接列
        xlsx_path = export_dir / "gz_jobs.xlsx"
        assert xlsx_path.exists()
        wb = openpyxl.load_workbook(xlsx_path)
        ws = wb.active
        headers = [ws.cell(1, col).value for col in range(1, ws.max_column + 1)]
        assert "来源链接" in headers
        url_col = headers.index("来源链接") + 1
        assert ws.max_row == 3  # 表头 + 2 条去重岗位
        assert str(ws.cell(2, url_col).value).startswith("https://www.xiaohongshu.com/explore/")

        # 验证核心设计原则：主 LLM 的所有消息上下文中完全看不到笔记正文全文！
        all_main_llm_text = json.dumps([c["messages"] for c in main_llm.calls], ensure_ascii=False)
        assert "参与微信小微中ai调用一方能力" not in all_main_llm_text
        assert "导师主动跟进答疑" not in all_main_llm_text
