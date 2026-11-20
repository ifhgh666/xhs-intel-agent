"""阶段 2 小红书适配层离线单元测试（全部基于阶段 1 录制的真实 fixtures，不访问真实小红书）。"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from xhs_agent.config import load_config
from xhs_agent.xhs import (
    ALLOWED_MCP_TOOLS,
    FORBIDDEN_MCP_TOOLS,
    MCPCallResult,
    MCPClient,
    MCPConnectionError,
    MCPTimeoutError,
    MCPToolInfo,
    MCPToolNotAllowedError,
    MCPToolSchemaError,
    NoteCache,
    RateLimiter,
    RateLimitExceededError,
    UnknownNoteError,
    XhsAdapter,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"


class FixtureMCPClient:
    """基于 tests/fixtures 录制样本的离线 MCP Client Mock。"""

    def __init__(self, fixtures_dir: Path = FIXTURES_DIR) -> None:
        self.fixtures_dir = fixtures_dir
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.connected = False

        tools_payload = json.loads((fixtures_dir / "tools.json").read_text(encoding="utf-8"))
        self._tools = [
            MCPToolInfo(
                name=t["name"],
                description=t.get("description", ""),
                input_schema=t.get("inputSchema", {}),
            )
            for t in tools_payload["tools"]
        ]

        self._searches: dict[str, dict[str, Any]] = {}
        for p in sorted(fixtures_dir.glob("search_*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            kw = data["arguments"]["keyword"]
            self._searches[kw] = data

        self._details: dict[str, dict[str, Any]] = {}
        for p in sorted(fixtures_dir.glob("detail_*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            fid = data["arguments"]["feed_id"]
            self._details[fid] = data

        login_file = fixtures_dir / "login_status.json"
        self._login_text = (
            json.loads(login_file.read_text(encoding="utf-8"))["result"]["text"]
            if login_file.exists()
            else "✅ 已登录\n用户名: test"
        )

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False

    async def list_tools(self) -> list[MCPToolInfo]:
        return list(self._tools)

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> MCPCallResult:
        args = dict(arguments or {})
        self.calls.append((name, args))

        if name == "check_login_status":
            return MCPCallResult(name, False, self._login_text, None)

        if name == "search_feeds":
            kw = args["keyword"]
            fixture = self._searches.get(kw) or next(iter(self._searches.values()))
            return MCPCallResult(name, False, fixture["raw_text"], fixture["parsed"])

        if name == "get_feed_detail":
            fid = args["feed_id"]
            assert args.get("xsec_token"), "get_feed_detail 必须自动注入非空 xsec_token"
            fixture = self._details[fid]
            return MCPCallResult(name, False, fixture["raw_text"], fixture["parsed"])

        raise AssertionError(f"Mock 不应被调用到白名单外工具: {name}")


class FakeClock:
    """可控虚拟时钟，用于精确且快速地测试限流等待逻辑。"""

    def __init__(self, start: float = 100.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


# ---------------------------------------------------------------------------
# 1. Config & Whitelist / Pre-check
# ---------------------------------------------------------------------------


def test_load_config_reads_project_toml():
    cfg = load_config()
    assert cfg.mcp.url == "http://localhost:18060/mcp"
    assert cfg.rate_limit.min_interval_sec == 3.0
    assert cfg.rate_limit.jitter_sec == 5.0
    assert cfg.budget.max_detail_calls == 30


async def test_validate_mcp_tools_passes_on_real_tools_fixture():
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))
    validated = await adapter.validate_mcp_tools()
    assert set(validated.keys()) == ALLOWED_MCP_TOOLS


async def test_validate_mcp_tools_fails_on_missing_or_schema_drift():
    mock = FixtureMCPClient()
    mock._tools = [t for t in mock._tools if t.name != "get_feed_detail"]
    adapter = XhsAdapter(mock)
    with pytest.raises(MCPToolSchemaError, match="get_feed_detail"):
        await adapter.validate_mcp_tools()

    # 模拟上游新增未知必填参数
    mock2 = FixtureMCPClient()
    for i, t in enumerate(mock2._tools):
        if t.name == "search_feeds":
            new_schema = dict(t.input_schema)
            new_schema["required"] = ["keyword", "new_mandatory_param"]
            mock2._tools[i] = MCPToolInfo(t.name, t.description, new_schema)
    adapter2 = XhsAdapter(mock2)
    with pytest.raises(MCPToolSchemaError, match="new_mandatory_param"):
        await adapter2.validate_mcp_tools()


@pytest.mark.parametrize("forbidden_tool", sorted(FORBIDDEN_MCP_TOOLS))
async def test_forbidden_write_tools_are_always_blocked(forbidden_tool: str):
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))
    with pytest.raises(MCPToolNotAllowedError):
        await adapter.call_allowed_tool(forbidden_tool, {})
    assert mock.calls == []


# ---------------------------------------------------------------------------
# 2. NoteCache & Adapter search_notes / get_note_detail
# ---------------------------------------------------------------------------


async def test_search_then_detail_without_manual_xsec_token_and_cache_hit():
    mock = FixtureMCPClient()
    limiter = RateLimiter(min_interval_sec=0, jitter_sec=0)
    adapter = XhsAdapter(mock, rate_limiter=limiter)

    login = await adapter.check_login_status()
    assert login["logged_in"] is True

    search_res = await adapter.search_notes("广州 AI实习")
    assert search_res["count"] == 20
    first = search_res["notes"][0]
    assert first["note"] == "n1"
    assert first["published_at"] is not None  # 由 feed_id 自动解码发布时间
    assert "xsecToken" not in json.dumps(search_res, ensure_ascii=False)
    assert "xsec_token" not in json.dumps(search_res, ensure_ascii=False)

    # 第一次拉详情：自动注入 feed_id + xsec_token，实际请求 1 次 MCP
    d1 = await adapter.get_note_detail("n1")
    assert d1["note"] == "n1"
    assert d1["from_cache"] is False
    assert d1["content_length"] > 300
    assert d1["image_count"] == 1
    assert d1["source_url"].startswith("https://www.xiaohongshu.com/explore/")
    assert "xsecToken" not in json.dumps(d1, ensure_ascii=False)

    # 第二次拉同一篇详情：直接命中 NoteCache，不请求小红书，也不消耗每日配额
    detail_calls_before = sum(1 for name, _ in mock.calls if name == "get_feed_detail")
    d2 = await adapter.get_note_detail("n1")
    detail_calls_after = sum(1 for name, _ in mock.calls if name == "get_feed_detail")
    assert d2["from_cache"] is True
    assert detail_calls_before == 1
    assert detail_calls_after == 1
    assert limiter.get_daily_usage("detail") == 1


async def test_cross_search_deduplication_preserves_short_id_and_detail():
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    res1 = await adapter.search_notes("广州 AI实习")
    n1_feed_id = adapter.cache.get("n1").feed_id
    await adapter.get_note_detail("n1")

    # search_03 中包含同一篇笔记 6afdc5df000000002c00cff2，应复用 n1 且不丢失已缓存详情
    res3 = await adapter.search_notes("Agent 实习 2028届")
    matched = [item for item in res3["notes"] if item["note"] == "n1"]
    assert len(matched) == 1
    assert adapter.cache.get("n1").feed_id == n1_feed_id
    assert adapter.cache.get("n1").detail is not None
    assert res1["count"] == 20


async def test_unknown_note_short_id_raises_clear_error():
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))
    with pytest.raises(UnknownNoteError, match="请先调用 search_notes"):
        await adapter.get_note_detail("n999")


async def test_search_filter_validation_and_forwarding():
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    with pytest.raises(ValueError, match="keyword 不能为空"):
        await adapter.search_notes("   ")

    with pytest.raises(ValueError, match="不合法"):
        await adapter.search_notes("广州 AI实习", sort_by="随便排序")

    await adapter.search_notes("广州 AI实习", sort_by="最新", publish_time="一周内")
    name, args = mock.calls[-1]
    assert name == "search_feeds"
    assert args == {"keyword": "广州 AI实习", "filters": {"sort_by": "最新", "publish_time": "一周内"}}


async def test_trimmed_search_payload_is_dramatically_smaller_than_raw():
    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    for kw, fixture in mock._searches.items():
        raw_bytes = len(fixture["raw_text"].encode("utf-8"))
        trimmed = await adapter.search_notes(kw)
        trimmed_bytes = len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8"))
        reduction = 1.0 - (trimmed_bytes / raw_bytes)
        assert reduction >= 0.75, f"预期裁剪率 >= 75%，实际 {reduction:.1%} ({trimmed_bytes}/{raw_bytes})"


# ---------------------------------------------------------------------------
# 3. RateLimiter: 串行锁 + 间隔抖动 + 每日上限
# ---------------------------------------------------------------------------


async def test_rate_limiter_enforces_min_interval_and_jitter():
    clock = FakeClock()
    limiter = RateLimiter(
        min_interval_sec=3.0,
        jitter_sec=5.0,
        clock=clock,
        sleep_fn=clock.sleep,
        rand_fn=lambda lo, hi: 2.5,  # 固定抖动 2.5s -> 目标间隔 5.5s
    )

    async with limiter.acquire("search") as w1:
        assert w1 == 0.0
    async with limiter.acquire("search") as w2:
        assert w2 == pytest.approx(5.5)
    assert clock.sleeps == [pytest.approx(5.5)]
    assert limiter.get_daily_usage("search") == 2


async def test_rate_limiter_enforces_strict_serial_execution():
    limiter = RateLimiter(min_interval_sec=0.0, jitter_sec=0.0)
    active = 0
    max_active = 0
    order: list[str] = []

    async def worker(tag: str) -> None:
        nonlocal active, max_active
        async with limiter.acquire("detail"):
            active += 1
            max_active = max(max_active, active)
            order.append(f"start:{tag}")
            await asyncio.sleep(0.02)
            order.append(f"end:{tag}")
            active -= 1

    await asyncio.gather(worker("A"), worker("B"), worker("C"))
    assert max_active == 1
    assert order[0].startswith("start:") and order[1] == f"end:{order[0].split(':')[1]}"


async def test_rate_limiter_blocks_when_daily_limit_reached():
    limiter = RateLimiter(
        min_interval_sec=0.0,
        jitter_sec=0.0,
        daily_limits={"search": 2, "detail": 1},
    )
    async with limiter.acquire("search"):
        pass
    async with limiter.acquire("search"):
        pass
    assert limiter.get_remaining("search") == 0

    with pytest.raises(RateLimitExceededError, match="search"):
        async with limiter.acquire("search"):
            pass


# ---------------------------------------------------------------------------
# 4. MCPClient: 断线自动重连一次 & 超时处理
# ---------------------------------------------------------------------------


async def test_mcp_client_auto_reconnects_once_on_disconnect():
    class FlakySession:
        def __init__(self, should_fail: bool) -> None:
            self.should_fail = should_fail

        async def call_tool(self, name: str, args: dict[str, Any]):
            if self.should_fail:
                raise ConnectionResetError("peer closed connection")
            return type(
                "Res",
                (),
                {
                    "is_error": False,
                    "content": [type("Item", (), {"type": "text", "text": '{"ok": true}'})()],
                    "structured_content": None,
                },
            )()

    client = MCPClient("http://localhost:18060/mcp")
    client._session = FlakySession(should_fail=True)  # type: ignore[assignment]
    reconnect_count = 0

    async def fake_reconnect() -> None:
        nonlocal reconnect_count
        reconnect_count += 1
        client._session = FlakySession(should_fail=False)  # type: ignore[assignment]

    client.reconnect = fake_reconnect  # type: ignore[method-assign]
    res = await client.call_tool("search_feeds", {"keyword": "test"})
    assert reconnect_count == 1
    assert res.parsed == {"ok": True}


async def test_mcp_client_raises_timeout_and_connection_error():
    class SlowSession:
        async def call_tool(self, name: str, args: dict[str, Any]):
            await asyncio.sleep(1.0)

    client = MCPClient("http://localhost:18060/mcp")
    client._session = SlowSession()  # type: ignore[assignment]
    with pytest.raises(MCPTimeoutError):
        await client.call_tool("search_feeds", {"keyword": "test"}, timeout=0.05)

    class AlwaysBrokenSession:
        async def call_tool(self, name: str, args: dict[str, Any]):
            raise ConnectionError("broken pipe")

    client2 = MCPClient("http://localhost:18060/mcp")
    client2._session = AlwaysBrokenSession()  # type: ignore[assignment]

    async def fake_reconnect_fail() -> None:
        client2._session = AlwaysBrokenSession()  # type: ignore[assignment]

    client2.reconnect = fake_reconnect_fail  # type: ignore[method-assign]
    with pytest.raises(MCPConnectionError):
        await client2.call_tool("search_feeds", {"keyword": "test"})


def test_note_cache_requires_feed_id():
    cache = NoteCache()
    with pytest.raises(ValueError, match="feed_id"):
        cache.put({"title": "missing id"})
