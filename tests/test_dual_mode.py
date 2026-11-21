"""双模式（岗位情报 + 经验知识）D1 & D2 离线单元与 API 集成测试。"""

from __future__ import annotations

import json
from io import BytesIO
from pathlib import Path

import openpyxl
from fastapi.testclient import TestClient

from tests.test_phase3_harness import ScriptedLLM
from tests.test_web_w1_api import WebMockMCPClient
from tests.test_web_w2_sse import _parse_sse_text
from xhs_agent.api.app import create_app
from xhs_agent.config import LLMConfig
from xhs_agent.harness import LLMResponse, TokenUsage, ToolCallRequest
from xhs_agent.jobs import JobStore
from xhs_agent.knowledge import (
    ExperienceExtractor,
    KnowledgeTools,
    export_experiences_to_excel,
    export_experiences_to_markdown,
)
from xhs_agent.xhs import RateLimiter, XhsAdapter


async def test_d1_experience_extractor_verifies_evidence_and_filters_non_experience():
    llm = ScriptedLLM(
        [
            # 1) 真实面经：证据合法 -> 通过
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "item": {
                            "category": "interview",
                            "topic": "Coding Agent",
                            "title": "网易互娱 28 届 Coding Agent 实习一二面真题",
                            "target_audience": "2028届本科生",
                            "summary": "涵盖 MCP、Skill、限流并发控制与 AI 自动化修复 MR 闭环架构题。",
                            "key_points": [
                                "开共享屏幕画出 coding agent 架构图",
                                "有了 MCP 为什么还需要 Skill？",
                            ],
                            "tech_stack": ["Coding Agent", "MCP", "Skill"],
                            "resources": [],
                            "pitfalls": ["AI 审查回复没问题不代表可直接上线"],
                            "evidence": "开共享屏幕画出coding agent架构图，并阐述一个请求进来流程怎么走？",
                            "confidence": 0.95,
                        },
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(600, 150, 750),
            ),
            # 2) 编造证据 -> 被 verify_evidence 拦截丢弃
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "item": {
                            "category": "roadmap",
                            "topic": "Agent",
                            "title": "编造的路线",
                            "summary": "编造内容",
                            "key_points": ["假要点"],
                            "evidence": "这段话原文里根本不存在不要相信",
                            "confidence": 0.99,
                        },
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(200, 80, 280),
            ),
            # 3) 纯招聘帖 -> 返回 item=null
            LLMResponse(
                content=json.dumps({"needs_image": False, "item": None}),
                usage=TokenUsage(150, 20, 170),
            ),
        ]
    )
    extractor = ExperienceExtractor(llm, LLMConfig())

    r1 = await extractor.extract(
        title="网易游戏(互娱) base 广州 28届日常实习",
        desc="一面（约40分钟）\n开共享屏幕画出coding agent架构图，并阐述一个请求进来流程怎么走？\n有了MCP为什么还需要skill？",
        image_count=1,
    )
    assert r1.item is not None and r1.item.category == "interview"
    assert r1.dropped_unverified is False

    r2 = await extractor.extract(title="普通帖子", desc="今天学了 Python 基础语法。", image_count=0)
    assert r2.item is None and r2.dropped_unverified is True

    r3 = await extractor.extract(title="急招 AI 实习生", desc="投递邮箱 hr@example.com", image_count=1)
    assert r3.item is None and r3.dropped_unverified is False


async def test_d1_cross_mode_cache_sharing_and_md_excel_export(tmp_path: Path):
    """同一篇笔记（n15 网易面经）先被岗位模式拉过详情后，经验模式直接命中本地 notes.detail_json，0 次 MCP 请求。"""
    mock_mcp = WebMockMCPClient()
    db_path = tmp_path / "dual.db"

    with JobStore(db_path) as store:
        adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0, counter=store))
        await adapter.search_notes("广州 AI实习")
        # 模拟岗位模式已经抓过 n15 (6ad215a2000000001800473f) 的详情并落盘 notes 表，且标记为岗位已处理（无岗位）
        cached, raw_detail, _ = await adapter.fetch_note_detail_raw("n15")
        store.upsert_note(
            feed_id=cached.feed_id,
            xsec_token=cached.xsec_token,
            title=cached.title,
            author=cached.author,
            published_at=cached.published_at,
            raw_feed=cached.raw_feed,
            detail=raw_detail,
        )
        store.mark_note_processed(cached.feed_id)
        detail_calls_before = sum(1 for n, _ in mock_mcp.calls if n == "get_feed_detail")

        exp_llm = ScriptedLLM(
            [
                LLMResponse(
                    content=json.dumps(
                        {
                            "needs_image": False,
                            "item": {
                                "category": "interview",
                                "topic": "Coding Agent",
                                "title": "网易互娱 Coding Agent 一二面高频真题",
                                "target_audience": "2028届",
                                "summary": "分享了一面 MCP/Skill/缓存命中率 与二面 AI 自动化修复 MR 闭环设计题。",
                                "key_points": [
                                    "有了MCP为什么还需要skill？",
                                    "不同coding agent缓存命中率不同的原因",
                                ],
                                "tech_stack": ["MCP", "Coding Agent"],
                                "resources": ["CLAUDE.md"],
                                "pitfalls": ["AI 审查回复没问题不等于没问题"],
                                "evidence": "有了MCP为什么还需要skill？",
                                "confidence": 0.96,
                            },
                        },
                        ensure_ascii=False,
                    )
                )
            ]
        )
        # 清空内存 NoteCache detail，强制验证跨会话从 SQLite notes.detail_json 命中缓存
        cached.detail = None

        ktools = KnowledgeTools(
            adapter=adapter,
            store=store,
            extractor=ExperienceExtractor(exp_llm, LLMConfig()),
            export_dir=tmp_path / "exports",
        )
        res = await ktools.extract_and_save_experience("n15")
        detail_calls_after = sum(1 for n, _ in mock_mcp.calls if n == "get_feed_detail")

        assert res["status"] == "ok"
        assert res["from_cache"] is True
        assert detail_calls_after == detail_calls_before  # 0 次额外小红书请求！
        assert store.count_experiences() == 1
        assert store.count_jobs() == 0  # 两库严格隔离！

        # 二次调用走 exp_processed_at 直接跳过
        res2 = await ktools.extract_and_save_experience("n15")
        assert res2["status"] == "already_processed"

        # 验证 Markdown 与 Excel 导出
        md_res = await ktools.export_experiences(format="markdown", path="agent_study.md")
        md_text = Path(md_res["path"]).read_text(encoding="utf-8")
        assert "网易互娱 Coding Agent 一二面高频真题" in md_text
        assert "有了MCP为什么还需要skill？" in md_text
        assert "https://www.xiaohongshu.com/explore/6ad215a2000000001800473f" in md_text

        xlsx_res = await ktools.export_experiences(format="excel", path="agent_study.xlsx")
        wb = openpyxl.load_workbook(xlsx_res["path"])
        assert wb.active.max_row == 2


def test_d2_dual_mode_web_api_sse_and_experiences_endpoints(tmp_path: Path):
    mock_mcp = WebMockMCPClient()
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    extractor_llm = ScriptedLLM(
        [
            LLMResponse(
                content=json.dumps(
                    {
                        "needs_image": False,
                        "item": {
                            "category": "interview",
                            "topic": "Coding Agent",
                            "title": "网易互娱 Coding Agent 实习面经",
                            "summary": "两轮高频架构与工程场景题总结。",
                            "key_points": ["有了MCP为什么还需要skill？"],
                            "tech_stack": ["MCP", "Coding Agent"],
                            "evidence": "有了MCP为什么还需要skill？",
                            "confidence": 0.94,
                        },
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(500, 120, 620),
            )
        ]
    )

    main_llm = ScriptedLLM(
        [
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("e1", "search_notes", '{"keyword": "广州 AI实习", "sort_by": "最多收藏"}')],
                usage=TokenUsage(400, 30, 430),
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("e2", "extract_and_save_experience", '{"note": "n15"}')],
                usage=TokenUsage(600, 30, 630),
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("e3", "query_experiences", '{"category": "interview"}')],
                usage=TokenUsage(700, 30, 730),
            ),
            LLMResponse(
                content="已为你搜集并沉淀 1 篇高赞 Coding Agent 面经（网易互娱），重点考察 MCP 与 Skill 区别。",
                tool_calls=[],
                usage=TokenUsage(800, 60, 860),
            ),
        ]
    )

    app = create_app(
        db_path=tmp_path / "dual_api.db",
        traces_dir=tmp_path / "traces",
        export_dir=tmp_path / "exports",
        adapter=adapter,
        llm=main_llm,
        extractor_llm=extractor_llm,
        auto_connect_mcp=True,
    )

    with TestClient(app) as client:
        # 1. 创建 experience 模式会话并跑 SSE
        sid = client.post("/api/sessions", json={"mode": "experience"}).json()["session_id"]
        run_id = client.post(
            f"/api/sessions/{sid}/messages",
            json={"content": "搜集 AI Agent 面试经验", "mode": "experience"},
        ).json()["run_id"]

        sse_events = _parse_sse_text(client.get(f"/api/sessions/{sid}/events?run_id={run_id}").text)
        assert sse_events[-1][0] == "turn_complete"
        complete = sse_events[-1][1]
        assert len(complete["newly_saved_experiences"]) == 1
        assert complete["newly_saved_experiences"][0]["category"] == "interview"
        assert complete["newly_saved_jobs"] == []
        assert complete["extract_llm_calls"] == 1

        # 验证 mode 前缀确实传给了主 LLM
        first_user_msg = main_llm.calls[0]["messages"][1]["content"]
        assert "仅经验知识模式" in first_user_msg

        # 2. 验证 /api/experiences 列表、改状态、导出 Markdown & Excel、删除
        r_list = client.get("/api/experiences?category=interview")
        assert r_list.status_code == 200
        body = r_list.json()
        assert body["total"] == 1
        exp_item = body["items"][0]
        exp_id = exp_item["id"]
        assert body["facets"]["category_counts"]["interview"] == 1
        assert "xsec_token=" in exp_item["open_url"] and "xsec_source=pc_search" in exp_item["open_url"]

        # 验证本地原帖快照接口（正文 + 配图 + 带 token 外链）
        r_note = client.get(f"/api/notes/{exp_item['note_id']}")
        assert r_note.status_code == 200
        snap = r_note.json()
        assert snap["has_detail"] is True
        assert "coding agent" in snap["desc"].lower()
        assert "xsec_token=" in snap["open_url"]
        assert all(img["url"].startswith("https://") for img in snap["images"])
        assert client.get("/api/notes/nonexistent_feed_id").status_code == 404

        r_patch = client.patch(f"/api/experiences/{exp_id}", json={"status": "starred"})
        assert r_patch.status_code == 200 and r_patch.json()["status"] == "starred"

        r_md = client.get("/api/experiences/export?format=md&status=starred")
        assert r_md.status_code == 200 and "网易互娱 Coding Agent 实习面经" in r_md.text

        r_xlsx = client.get("/api/experiences/export?format=xlsx")
        assert r_xlsx.status_code == 200
        wb = openpyxl.load_workbook(BytesIO(r_xlsx.content))
        assert wb.active.max_row == 2

        st = client.get("/api/system/status").json()
        assert st["db_experiences_count"] == 1 and st["db_jobs_count"] == 0

        assert client.delete(f"/api/experiences/{exp_id}").status_code == 200
        assert client.get(f"/api/experiences/{exp_id}").status_code == 404
