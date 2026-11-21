"""Web 阶段 W2 离线集成测试：异步多轮会话、SSE 实时推流、409 单任务互斥与任务取消。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from tests.test_phase3_harness import ScriptedLLM
from tests.test_web_w1_api import WebMockMCPClient
from xhs_agent.api.app import create_app
from xhs_agent.harness import LLMResponse, TokenUsage, ToolCallRequest
from xhs_agent.xhs import RateLimiter, XhsAdapter


def _parse_sse_text(raw: str) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    for block in raw.strip().split("\n\n"):
        if not block.strip():
            continue
        ev_name = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                ev_name = line.split(":", 1)[1].strip()
            elif line.startswith("data:"):
                data_lines.append(line.split(":", 1)[1].strip())
        if data_lines:
            events.append((ev_name, json.loads("\n".join(data_lines))))
    return events


class SlowWaitLLM:
    """可控慢速 Mock LLM，用于精确测试 409 并发互斥与取消信号。"""

    def __init__(self, started_event: asyncio.Event, release_event: asyncio.Event) -> None:
        self.started_event = started_event
        self.release_event = release_event
        self.call_idx = 0

    async def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        model: str | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> LLMResponse:
        self.call_idx += 1
        if self.call_idx == 1:
            self.started_event.set()
            await self.release_event.wait()
            return LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("c_slow", "search_notes", '{"keyword": "广州 AI实习"}')],
                usage=TokenUsage(100, 20, 120),
            )
        return LLMResponse(content="慢任务完成。", tool_calls=[], usage=TokenUsage(120, 20, 140))


def test_w2_session_sse_stream_newly_saved_jobs_replay_and_multiturn(tmp_path: Path):
    mock_mcp = WebMockMCPClient()
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

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
                                "evidence": "2028届及以后毕业的本科或硕士在读生",
                                "confidence": 0.95,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                usage=TokenUsage(500, 100, 600, cache_hit_tokens=200, cache_miss_tokens=300),
            )
        ]
    )

    main_llm = ScriptedLLM(
        [
            # Turn 1
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("c1", "search_notes", '{"keyword": "广州 AI实习"}')],
                usage=TokenUsage(300, 30, 330),
            ),
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("c2", "extract_and_save_jobs", '{"note": "n1"}')],
                usage=TokenUsage(600, 30, 630, cache_hit_tokens=300, cache_miss_tokens=300),
            ),
            LLMResponse(
                content="已为你找到并入库腾讯 AI Agent 产品实习生（ID=1）。",
                tool_calls=[],
                usage=TokenUsage(700, 50, 750, cache_hit_tokens=500, cache_miss_tokens=200),
            ),
            # Turn 2（多轮追问改状态）
            LLMResponse(
                content="",
                tool_calls=[ToolCallRequest("c3", "mark_job", '{"job_id": 1, "status": "interested"}')],
                usage=TokenUsage(800, 30, 830, cache_hit_tokens=700, cache_miss_tokens=100),
            ),
            LLMResponse(
                content="已将 1 号腾讯岗位标记为感兴趣。",
                tool_calls=[],
                usage=TokenUsage(900, 30, 930, cache_hit_tokens=800, cache_miss_tokens=100),
            ),
        ]
    )

    app = create_app(
        db_path=tmp_path / "jobs.db",
        traces_dir=tmp_path / "traces",
        export_dir=tmp_path / "exports",
        adapter=adapter,
        llm=main_llm,
        extractor_llm=extractor_llm,
        auto_connect_mcp=True,
    )

    with TestClient(app) as client:
        # 1. 创建会话
        r_sess = client.post("/api/sessions", json={"max_search": 3, "max_detail": 5})
        assert r_sess.status_code == 201
        sid = r_sess.json()["session_id"]

        # 2. 发送 Turn 1 消息并订阅 SSE 流
        r_msg = client.post(f"/api/sessions/{sid}/messages", json={"content": "找广州 AI Agent 实习"})
        assert r_msg.status_code == 202
        run1_id = r_msg.json()["run_id"]

        r_sse = client.get(f"/api/sessions/{sid}/events?run_id={run1_id}")
        assert r_sse.status_code == 200
        assert "text/event-stream" in r_sse.headers["content-type"]

        parsed = _parse_sse_text(r_sse.text)
        ev_names = [name for name, _ in parsed]
        assert ev_names[0] == "turn_start"
        assert "tool_call" in ev_names
        assert "tool_result" in ev_names
        assert ev_names[-1] == "turn_complete"

        complete_data = parsed[-1][1]
        assert "腾讯" in complete_data["answer"]
        assert complete_data["total_llm_calls"] == 4  # 主 LLM 3 + 抽取 LLM 1
        assert len(complete_data["newly_saved_jobs"]) == 1
        assert complete_data["newly_saved_jobs"][0]["company"] == "腾讯"
        assert complete_data["cost"]["total_cost"] > 0

        # 3. 断线重放验证：结束后再次请求 /events 仍可完整回放事件
        r_replay = client.get(f"/api/sessions/{sid}/events?run_id={run1_id}")
        assert len(_parse_sse_text(r_replay.text)) == len(parsed)

        # 4. Turn 2 多轮追问：标记为感兴趣
        r_msg2 = client.post(f"/api/sessions/{sid}/messages", json={"content": "把 1 号岗位标为感兴趣"})
        assert r_msg2.status_code == 202
        run2_id = r_msg2.json()["run_id"]
        r_sse2 = client.get(f"/api/sessions/{sid}/events?run_id={run2_id}")
        parsed2 = _parse_sse_text(r_sse2.text)
        assert parsed2[-1][0] == "turn_complete"
        assert "感兴趣" in parsed2[-1][1]["answer"]

        # 5. 检查会话详情与数据库状态
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["turns"] == 2
        assert len(detail["messages"]) == 4  # 2 user + 2 assistant

        job_after = client.get("/api/jobs/1").json()
        assert job_after["status"] == "interested"


async def test_w2_single_active_task_conflict_and_graceful_cancel(tmp_path: Path):
    import httpx

    mock_mcp = WebMockMCPClient()
    adapter = XhsAdapter(mock_mcp, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))
    started = asyncio.Event()
    release = asyncio.Event()
    slow_llm = SlowWaitLLM(started, release)

    app = create_app(
        db_path=tmp_path / "jobs.db",
        traces_dir=tmp_path / "traces",
        adapter=adapter,
        llm=slow_llm,
        auto_connect_mcp=True,
    )

    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as ac:
            s1 = (await ac.post("/api/sessions")).json()["session_id"]
            s2 = (await ac.post("/api/sessions")).json()["session_id"]

            r1 = await ac.post(f"/api/sessions/{s1}/messages", json={"content": "长任务"})
            assert r1.status_code == 202
            run1_id = r1.json()["run_id"]

            # 等待任务进入运行中状态
            await asyncio.wait_for(started.wait(), timeout=2.0)

            # 此时并发向另一会话发送消息，必须返回 409 Conflict
            r_conflict = await ac.post(f"/api/sessions/{s2}/messages", json={"content": "插队任务"})
            assert r_conflict.status_code == 409

            # 发出取消信号，再放行模拟 LLM 返回，验证在执行工具前优雅取消
            r_cancel = await ac.post(f"/api/sessions/{s1}/cancel")
            assert r_cancel.status_code == 200
            assert r_cancel.json()["cancelled"] is True
            release.set()

            r_sse = await ac.get(f"/api/sessions/{s1}/events?run_id={run1_id}")
            parsed = _parse_sse_text(r_sse.text)
            ev_names = [name for name, _ in parsed]
            assert "cancelled" in ev_names
            assert parsed[-1][0] == "turn_complete"
            assert parsed[-1][1]["stop_reason"] == "cancelled"

            # 验证未执行被取消后的 search_feeds MCP 调用，且锁已释放
            assert not any(name == "search_feeds" for name, _ in mock_mcp.calls)
            st = (await ac.get("/api/system/status")).json()
            assert st["active_session_id"] is None


def test_w4_spa_static_hosting_and_api_404(tmp_path: Path):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html><title>XHS SPA</title></html>", encoding="utf-8")
    (dist / "assets" / "app.js").write_text("console.log('ok');", encoding="utf-8")

    app = create_app(
        db_path=tmp_path / "jobs.db",
        traces_dir=tmp_path / "traces",
        mcp_client=WebMockMCPClient(),
        auto_connect_mcp=False,
        web_dist_dir=dist,
    )
    with TestClient(app) as client:
        r_root = client.get("/")
        assert r_root.status_code == 200 and "XHS SPA" in r_root.text
        r_route = client.get("/jobs")
        assert r_route.status_code == 200 and "XHS SPA" in r_route.text
        r_asset = client.get("/assets/app.js")
        assert r_asset.status_code == 200 and "console.log" in r_asset.text
        r_api_404 = client.get("/api/does-not-exist")
        assert r_api_404.status_code == 404

