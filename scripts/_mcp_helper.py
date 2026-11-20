"""阶段 1 探测脚本公共辅助模块。

提供：
- config.toml 配置读取
- MCP Streamable HTTP 会话连接（兼容 mcp 2.x 与 1.x）
- 工具返回解析（自动解析 TextContent 中的 JSON）
- 礼貌限流等待（最小间隔 + 随机抖动）
- Fixture 读写与搜索结果字段提取
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import tomllib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from mcp import ClientSession

try:
    from mcp.client.streamable_http import streamable_http_client as _streamable_client
except ImportError:
    from mcp.client.streamable_http import streamablehttp_client as _streamable_client  # type: ignore[attr-defined]

ROOT_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT_DIR / "config.toml"
FIXTURES_DIR = ROOT_DIR / "tests" / "fixtures"
EVALS_DIR = ROOT_DIR / "evals"


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("rb") as f:
        return tomllib.load(f)


def get_mcp_url(override_url: str | None = None) -> str:
    if override_url:
        return override_url
    return str(load_config()["mcp"]["url"])


def get_rate_limit_params() -> tuple[float, float]:
    cfg = load_config().get("rate_limit", {})
    return float(cfg.get("min_interval_sec", 3)), float(cfg.get("jitter_sec", 5))


@asynccontextmanager
async def open_mcp_session(url: str | None = None) -> AsyncIterator[ClientSession]:
    """建立并初始化 MCP Streamable HTTP ClientSession。"""
    target_url = get_mcp_url(url)
    async with _streamable_client(target_url) as transport_streams:
        # mcp 2.x 返回 (read_stream, write_stream)；mcp 1.x 返回 3 元组
        read_stream, write_stream = transport_streams[0], transport_streams[1]
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()
            yield session


def get_tool_input_schema(tool: Any) -> dict[str, Any]:
    """兼容 mcp 2.x (input_schema) 与 1.x (inputSchema) 获取工具输入 Schema。"""
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else {}


def extract_tool_result(result: Any) -> dict[str, Any]:
    """将 MCP CallToolResult 规整为易于序列化和检查的字典。"""
    is_error = bool(getattr(result, "is_error", getattr(result, "isError", False)))
    content_items = getattr(result, "content", []) or []
    structured_content = getattr(result, "structured_content", getattr(result, "structuredContent", None))

    raw_content: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for item in content_items:
        item_type = getattr(item, "type", type(item).__name__)
        if hasattr(item, "model_dump"):
            dumped = item.model_dump(mode="json")
        else:
            dumped = {"type": item_type, "repr": repr(item)}
        raw_content.append(dumped)

        text_val = getattr(item, "text", None)
        if isinstance(text_val, str):
            text_parts.append(text_val)

    combined_text = "\n".join(text_parts).strip()
    parsed: Any = structured_content
    if parsed is None and combined_text:
        try:
            parsed = json.loads(combined_text)
        except json.JSONDecodeError:
            parsed = None

    return {
        "is_error": is_error,
        "text": combined_text,
        "parsed": parsed,
        "raw_content": raw_content,
    }


def save_json(path: Path, data: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slugify_keyword(keyword: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", "_", keyword.strip())
    return cleaned.strip("_") or "query"


async def polite_sleep(
    min_sec: float | None = None,
    jitter_sec: float | None = None,
    reason: str = "防风控间隔",
) -> float:
    default_min, default_jitter = get_rate_limit_params()
    lo = default_min if min_sec is None else min_sec
    jitter = default_jitter if jitter_sec is None else jitter_sec
    delay = lo + random.uniform(0.0, max(0.0, jitter))
    if delay > 0:
        print(f"  [限流] {reason}：等待 {delay:.1f}s ...")
        await asyncio.sleep(delay)
    return delay


def extract_feed_items(payload: Any) -> list[dict[str, Any]]:
    """从 search_feeds 解析后的 JSON 中尽量稳健地提取笔记列表。"""
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []

    for key in ("feeds", "items", "notes", "list", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            return [item for item in val if isinstance(item, dict)]

    data = payload.get("data")
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        for key in ("feeds", "items", "notes", "list", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
    return []


def extract_feed_summary(item: dict[str, Any]) -> dict[str, Any]:
    """从单条搜索结果中提取核心字段（兼容顶层字段与 noteCard 嵌套结构）。"""
    note_card = item.get("noteCard") or item.get("note_card") or {}
    if not isinstance(note_card, dict):
        note_card = {}

    user = note_card.get("user") or item.get("user") or {}
    if not isinstance(user, dict):
        user = {}

    interact = note_card.get("interactInfo") or note_card.get("interact_info") or {}
    if not isinstance(interact, dict):
        interact = {}

    feed_id = (
        item.get("id")
        or item.get("feed_id")
        or item.get("note_id")
        or item.get("noteId")
        or note_card.get("noteId")
        or note_card.get("id")
    )
    xsec_token = (
        item.get("xsecToken")
        or item.get("xsec_token")
        or note_card.get("xsecToken")
        or note_card.get("xsec_token")
    )
    title = (
        item.get("title")
        or note_card.get("displayTitle")
        or note_card.get("title")
        or ""
    )
    author = (
        user.get("nickname")
        or user.get("nickName")
        or user.get("name")
        or item.get("author")
        or ""
    )
    published_at = (
        item.get("time")
        or item.get("publish_time")
        or item.get("published_at")
        or note_card.get("time")
        or note_card.get("lastUpdateTime")
        or ""
    )
    liked_count = interact.get("likedCount") or interact.get("liked_count") or item.get("liked_count")
    note_type = note_card.get("type") or item.get("modelType") or item.get("type")

    return {
        "feed_id": str(feed_id) if feed_id is not None else None,
        "xsec_token": str(xsec_token) if xsec_token is not None else None,
        "title": str(title).strip(),
        "author": str(author).strip(),
        "published_at": published_at,
        "liked_count": liked_count,
        "type": note_type,
    }


def extract_detail_summary(payload: Any) -> dict[str, Any]:
    """从 get_feed_detail 解析后的 JSON 中尽量稳健地提取标题、正文、作者、时间、图片数。"""
    if not isinstance(payload, dict):
        return {
            "feed_id": None,
            "title": "",
            "desc": str(payload) if payload is not None else "",
            "author": "",
            "published_at": None,
            "image_count": 0,
            "tags": [],
        }

    # 兼容 {"data": {"note": {...}}} / {"note": {...}} / 顶层直接展开
    node = payload
    if isinstance(node.get("data"), dict):
        node = node["data"]
    if isinstance(node.get("note"), dict):
        node = node["note"]
    elif isinstance(node.get("feed"), dict):
        node = node["feed"]

    user = node.get("user") or {}
    if not isinstance(user, dict):
        user = {}

    images = node.get("imageList") or node.get("image_list") or node.get("images") or []
    if not isinstance(images, list):
        images = []

    raw_tags = node.get("tagList") or node.get("tag_list") or node.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for t in raw_tags:
            if isinstance(t, dict) and t.get("name"):
                tags.append(str(t["name"]))
            elif isinstance(t, str):
                tags.append(t)

    feed_id = node.get("noteId") or node.get("id") or node.get("feed_id") or payload.get("feed_id")
    title = node.get("title") or node.get("displayTitle") or ""
    desc = node.get("desc") or node.get("content") or node.get("description") or ""
    author = user.get("nickname") or user.get("nickName") or user.get("name") or node.get("author") or ""
    published_at = (
        node.get("time")
        or node.get("publishTime")
        or node.get("published_at")
        or node.get("lastUpdateTime")
    )

    return {
        "feed_id": str(feed_id) if feed_id is not None else None,
        "title": str(title).strip(),
        "desc": str(desc).strip(),
        "author": str(author).strip(),
        "published_at": published_at,
        "image_count": len(images),
        "tags": tags,
    }


def format_timestamp(ts: Any) -> str:
    """把常见毫秒/秒级时间戳格式化为可读日期，非数字则原样返回。"""
    if ts is None or ts == "":
        return "-"
    if isinstance(ts, (int, float)):
        val = float(ts)
        if val > 1e12:
            val /= 1000.0
        if val > 1e8:
            try:
                return datetime.fromtimestamp(val).strftime("%Y-%m-%d %H:%M")
            except (OSError, OverflowError, ValueError):
                return str(ts)
    return str(ts)
