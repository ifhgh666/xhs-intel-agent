"""Web 阶段 W1 离线 API 单元测试（使用 FastAPI TestClient + FixtureMCPClient + 临时 SQLite）。"""

from __future__ import annotations

import base64
import json
from datetime import date, datetime, timedelta, timezone
from io import BytesIO
from pathlib import Path
from typing import Any

import openpyxl
from fastapi.testclient import TestClient

from tests.test_phase2_xhs import FixtureMCPClient
from xhs_agent.api.app import create_app
from xhs_agent.jobs import JobRecord, JobStore, make_dedup_key
from xhs_agent.xhs import MCPCallResult, MCPConnectionError


class WebMockMCPClient(FixtureMCPClient):
    """在 FixtureMCPClient 基础上增加 get_login_qrcode 与可选断线模拟。"""

    def __init__(self, *, fail_connect: bool = False) -> None:
        super().__init__()
        self.fail_connect = fail_connect

    async def connect(self) -> None:
        if self.fail_connect:
            raise MCPConnectionError("mock mcp offline")
        await super().connect()

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> MCPCallResult:
        if name == "get_login_qrcode":
            fake_png = base64.b64encode(b"\x89PNG\r\n\x1a\nfake").decode("ascii")
            return MCPCallResult(
                tool_name=name,
                is_error=False,
                text="请用小红书 App 扫码登录",
                parsed=None,
                raw_content=[
                    {"type": "text", "text": "请用小红书 App 扫码登录"},
                    {"type": "image", "data": fake_png, "mimeType": "image/png"},
                ],
            )
        return await super().call_tool(name, arguments, timeout=timeout)


def _seed_sample_jobs(db_path: Path) -> None:
    now = datetime.now(timezone.utc)
    today_str = date.today().isoformat()
    with JobStore(db_path) as store:
        store.increment(today_str, "search", 2)
        store.increment(today_str, "detail", 5)

        store.upsert_job(
            JobRecord(
                company="腾讯",
                position="AI Agent产品实习生",
                city="广州",
                education="本科或硕士",
                graduation_year="2028届及以后",
                skills=["Agent Loop", "Prompt"],
                attendance_days="每周4天",
                apply_method="susiesun124@gmail.com",
                evidence="2028届及以后毕业的本科或硕士在读生",
                confidence=0.95,
                note_id="note_1",
                source_url="https://www.xiaohongshu.com/explore/note_1",
                note_title="招Ai agent产品实习生 | 广州",
                note_published_at=now - timedelta(days=2),
                crawled_at=now,
                needs_image=False,
                maybe_expired=False,
                dedup_key=make_dedup_key("腾讯", "AI Agent产品实习生", "广州"),
                status="new",
            )
        )
        store.upsert_job(
            JobRecord(
                company="科大讯飞",
                position="AI医疗算法大模型实习生",
                city="广州南沙区",
                education="本科及以上",
                graduation_year="2027届",
                skills=["大模型"],
                evidence="岗位名称：AI医疗算法大模型实习生",
                confidence=0.9,
                note_id="note_2",
                source_url="https://www.xiaohongshu.com/explore/note_2",
                note_title="急招",
                note_published_at=now - timedelta(days=75),
                crawled_at=now,
                needs_image=True,
                maybe_expired=True,
                dedup_key=make_dedup_key("科大讯飞", "AI医疗算法大模型实习生", "广州南沙区"),
                status="interested",
            )
        )
        store.upsert_job(
            JobRecord(
                company="字节跳动",
                position="RAG 算法实习生",
                city="深圳",
                graduation_year="2028届",
                skills=["RAG", "Python"],
                evidence="招 RAG 算法实习生",
                confidence=0.92,
                note_id="note_3",
                source_url="https://www.xiaohongshu.com/explore/note_3",
                note_title="深圳 RAG 算法实习",
                note_published_at=now - timedelta(days=5),
                crawled_at=now,
                needs_image=False,
                maybe_expired=False,
                dedup_key=make_dedup_key("字节跳动", "RAG 算法实习生", "深圳"),
                status="applied",
            )
        )


def _seed_sample_trace(traces_dir: Path) -> None:
    traces_dir.mkdir(parents=True, exist_ok=True)
    events = [
        {"ts": "2026-11-20T10:00:00+00:00", "session": "s_demo01", "type": "session_start", "task": "找广州 AI 实习"},
        {"ts": "2026-11-20T10:00:05+00:00", "session": "s_demo01", "type": "tool_call", "step": 1, "tool": "search_notes"},
        {
            "ts": "2026-11-20T10:00:30+00:00",
            "session": "s_demo01",
            "type": "session_end",
            "stop_reason": "completed",
            "steps": 3,
            "total_llm_calls": 4,
            "total_usage": {
                "prompt_tokens": 10000,
                "completion_tokens": 1000,
                "total_tokens": 11000,
                "cache_hit_tokens": 6000,
                "cache_miss_tokens": 4000,
            },
            "cost": {"currency": "CNY", "total_cost": 0.0122, "cache_savings": 0.0108},
        },
    ]
    (traces_dir / "s_demo01.jsonl").write_text(
        "\n".join(json.dumps(e, ensure_ascii=False) for e in events) + "\n",
        encoding="utf-8",
    )


def test_w1_system_status_qrcode_and_degraded_mode(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    _seed_sample_jobs(db_path)

    # 1) 正常连接态
    mock_mcp = WebMockMCPClient(fail_connect=False)
    app = create_app(db_path=db_path, traces_dir=tmp_path / "traces", mcp_client=mock_mcp)
    with TestClient(app) as client:
        res = client.get("/api/system/status")
        assert res.status_code == 200
        body = res.json()
        assert body["mcp_connected"] is True
        assert body["logged_in"] is True
        assert body["db_jobs_count"] == 3
        assert body["daily_usage"]["search"]["used"] == 2
        assert body["daily_usage"]["detail"]["used"] == 5

        qr_res = client.post("/api/system/login/qrcode")
        assert qr_res.status_code == 200
        qr_body = qr_res.json()
        assert qr_body["image_base64"].startswith("data:image/png;base64,")
        assert "扫码" in qr_body["hint_text"]

    # 2) 降级模式：MCP 离线时后端照常启动，且仍可查询岗位库
    offline_mcp = WebMockMCPClient(fail_connect=True)
    app_offline = create_app(db_path=db_path, traces_dir=tmp_path / "traces", mcp_client=offline_mcp)
    with TestClient(app_offline) as client:
        st = client.get("/api/system/status").json()
        assert st["mcp_connected"] is False
        assert st["logged_in"] is False
        assert st["db_jobs_count"] == 3

        jobs_res = client.get("/api/jobs")
        assert jobs_res.status_code == 200
        assert jobs_res.json()["total"] == 3

        # 离线态恢复后调用 reconnect 可重新连通
        offline_mcp.fail_connect = False
        reconn = client.post("/api/system/mcp/reconnect")
        assert reconn.status_code == 200
        assert reconn.json()["mcp_connected"] is True
        assert reconn.json()["logged_in"] is True


def test_w1_jobs_filtering_status_update_delete_and_excel_export(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    _seed_sample_jobs(db_path)

    app = create_app(db_path=db_path, traces_dir=tmp_path / "traces", mcp_client=WebMockMCPClient(), auto_connect_mcp=False)
    with TestClient(app) as client:
        # 全量列表 + facets
        r_all = client.get("/api/jobs?page=1&page_size=2")
        assert r_all.status_code == 200
        data_all = r_all.json()
        assert data_all["total"] == 3
        assert len(data_all["items"]) == 2
        assert data_all["facets"]["status_counts"]["new"] == 1
        assert data_all["facets"]["status_counts"]["interested"] == 1
        assert data_all["facets"]["status_counts"]["applied"] == 1

        # 组合筛选：广州 + 仅看 60 天内未过期
        r_gz_fresh = client.get("/api/jobs?city=广州&only_fresh=true")
        assert r_gz_fresh.status_code == 200
        gz_items = r_gz_fresh.json()["items"]
        assert len(gz_items) == 1
        assert gz_items[0]["company"] == "腾讯"
        tencent_id = gz_items[0]["id"]

        # 仅看需看图
        r_img = client.get("/api/jobs?needs_image=true")
        assert len(r_img.json()["items"]) == 1
        assert r_img.json()["items"][0]["company"] == "科大讯飞"

        # 单条详情 & PATCH 改状态
        r_one = client.get(f"/api/jobs/{tencent_id}")
        assert r_one.status_code == 200
        assert r_one.json()["evidence"]

        r_patch = client.patch(f"/api/jobs/{tencent_id}", json={"status": "interested"})
        assert r_patch.status_code == 200
        assert r_patch.json()["status"] == "interested"

        assert client.patch("/api/jobs/9999", json={"status": "applied"}).status_code == 404
        assert client.patch(f"/api/jobs/{tencent_id}", json={"status": "invalid_status"}).status_code == 422

        # 导出筛选后的 Excel（status=interested 应包含腾讯 + 科大讯飞 2 条）
        r_xlsx = client.get("/api/jobs/export?status=interested")
        assert r_xlsx.status_code == 200
        assert "spreadsheetml.sheet" in r_xlsx.headers["content-type"]
        wb = openpyxl.load_workbook(BytesIO(r_xlsx.content))
        ws = wb.active
        assert ws.max_row == 3  # 表头 + 2 行数据
        headers = [ws.cell(1, c).value for c in range(1, ws.max_column + 1)]
        url_col = headers.index("来源链接") + 1
        assert str(ws.cell(2, url_col).value).startswith("https://www.xiaohongshu.com/explore/")

        # 删除单条岗位
        r_del = client.delete(f"/api/jobs/{tencent_id}")
        assert r_del.status_code == 200 and r_del.json()["deleted"] is True
        assert client.get(f"/api/jobs/{tencent_id}").status_code == 404


def test_w1_traces_and_overview_stats_with_path_traversal_guard(tmp_path: Path):
    db_path = tmp_path / "jobs.db"
    traces_dir = tmp_path / "traces"
    _seed_sample_jobs(db_path)
    _seed_sample_trace(traces_dir)

    app = create_app(db_path=db_path, traces_dir=traces_dir, mcp_client=WebMockMCPClient(), auto_connect_mcp=False)
    with TestClient(app) as client:
        r_list = client.get("/api/traces")
        assert r_list.status_code == 200
        items = r_list.json()
        assert len(items) == 1
        assert items[0]["session_id"] == "s_demo01"
        assert items[0]["cache_hit_rate"] == 0.6
        assert items[0]["total_cost_cny"] == 0.0122

        r_detail = client.get("/api/traces/s_demo01")
        assert r_detail.status_code == 200
        assert r_detail.json()["event_count"] == 3

        # 路径穿越防护
        assert client.get("/api/traces/..%2Fconfig").status_code in {400, 404}
        assert client.get("/api/traces/bad$id").status_code == 400
        assert client.get("/api/traces/not_found_id").status_code == 404

        # 总览统计接口
        r_stats = client.get("/api/stats/overview")
        assert r_stats.status_code == 200
        stats = r_stats.json()
        assert stats["total_jobs"] == 3
        assert stats["total_sessions"] == 1
        assert stats["total_tokens"] == 11000
        assert stats["total_cost_cny"] == 0.0122
        assert stats["avg_cache_hit_rate"] == 0.6
