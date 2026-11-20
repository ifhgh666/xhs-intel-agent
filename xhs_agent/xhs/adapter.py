"""小红书适配层（XhsAdapter）。

职责（见 docs/design.md 第 5、6、7 节与 docs/development_plan.md 阶段 2）：
- 严格维护 MCP 工具白名单 ALLOWED_MCP_TOOLS，禁止任何写操作或未授权工具
- 启动预检：校验白名单工具存在且必填参数 Schema 符合预期
- 封装 search_notes：调用 search_feeds → 写入 NoteCache → 按字段裁剪返回精简列表（含由 feed_id 解码的发布时间）
- 封装 get_note_detail：查 NoteCache → 命中直接返回，未命中则注入 feed_id + xsec_token 调 get_feed_detail → 回填缓存 → 返回精简详情
- 所有真实 MCP 调用统一经过 RateLimiter（串行锁 + 随机抖动间隔 + 每日上限）
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from xhs_agent.xhs.cache import CachedNote, NoteCache, parse_timestamp
from xhs_agent.xhs.mcp_client import (
    MCPCallResult,
    MCPClientProtocol,
    MCPError,
    MCPToolExecutionError,
    MCPToolInfo,
    MCPToolNotAllowedError,
)
from xhs_agent.xhs.rate_limiter import RateLimiter

# 仅允许适配层调用的只读 MCP 工具白名单（写操作永不放行）
ALLOWED_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "check_login_status",
        "search_feeds",
        "get_feed_detail",
    }
)

# 实测 xiaohongshu-mcp v2.7.4 的写/破坏性工具清单（文档与回归测试参考）
FORBIDDEN_MCP_TOOLS: frozenset[str] = frozenset(
    {
        "delete_cookies",
        "publish_content",
        "publish_with_video",
        "post_comment_to_feed",
        "reply_comment_in_feed",
        "reply_notification",
        "like_feed",
        "like_notification",
        "favorite_feed",
    }
)

# 启动预检期望的必填参数集合（对照 tests/fixtures/tools.json）
EXPECTED_MCP_TOOL_REQUIRED_PARAMS: dict[str, set[str]] = {
    "check_login_status": set(),
    "search_feeds": {"keyword"},
    "get_feed_detail": {"feed_id", "xsec_token"},
}

# search_feeds.filters 允许的合法枚举值（对照 tests/fixtures/tools.json）
VALID_SEARCH_FILTERS: dict[str, set[str]] = {
    "sort_by": {"综合", "最新", "最多点赞", "最多评论", "最多收藏"},
    "note_type": {"不限", "视频", "图文"},
    "publish_time": {"不限", "一天内", "一周内", "半年内"},
    "search_scope": {"不限", "已看过", "未看过", "已关注"},
    "location": {"不限", "同城", "附近"},
}

_IMAGE_HINT_KEYWORDS = ("见图", "详见图", "看图", "图里", "图片里", "如图", "长图")


class MCPToolSchemaError(MCPError):
    """启动预检发现白名单工具缺失或参数 Schema 与预期不一致时抛出。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=False)


class UnknownNoteError(ValueError):
    """传入的短编号不在 NoteCache 中时抛出。"""

    def __init__(self, short_id: str) -> None:
        self.short_id = short_id
        super().__init__(f"未知笔记 '{short_id}'，请先调用 search_notes 获取笔记列表。")


def _format_dt_local(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _extract_feed_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("feeds", "items", "notes", "list", "results"):
        val = payload.get(key)
        if isinstance(val, list):
            return [item for item in val if isinstance(item, dict)]
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("feeds", "items", "notes", "list", "results"):
            val = data.get(key)
            if isinstance(val, list):
                return [item for item in val if isinstance(item, dict)]
    return []


def _extract_detail_fields(detail: Any, cached: CachedNote) -> dict[str, Any]:
    """从 get_feed_detail 原始 JSON 提取结构化核心字段（不含评论刷屏与长签名 URL）。"""
    node: dict[str, Any] = {}
    if isinstance(detail, dict):
        candidate = detail
        if isinstance(candidate.get("data"), dict):
            candidate = candidate["data"]
        if isinstance(candidate.get("note"), dict):
            candidate = candidate["note"]
        elif isinstance(candidate.get("feed"), dict):
            candidate = candidate["feed"]
        if isinstance(candidate, dict):
            node = candidate

    user = node.get("user") if isinstance(node.get("user"), dict) else {}
    images = node.get("imageList") or node.get("image_list") or node.get("images") or []
    if not isinstance(images, list):
        images = []

    raw_tags = node.get("tagList") or node.get("tag_list") or node.get("tags") or []
    tags: list[str] = []
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, dict) and tag.get("name"):
                tags.append(str(tag["name"]))
            elif isinstance(tag, str):
                tags.append(tag)

    title = str(node.get("title") or node.get("displayTitle") or cached.title or "").strip()
    desc = str(node.get("desc") or node.get("content") or node.get("description") or "").strip()
    author = str(
        user.get("nickname") or user.get("nickName") or user.get("name") or cached.author or ""
    ).strip()
    ip_location = str(node.get("ipLocation") or "").strip() or None
    published_dt = parse_timestamp(node.get("time")) or cached.published_at

    return {
        "title": title,
        "author": author,
        "published_dt": published_dt,
        "ip_location": ip_location,
        "desc": desc,
        "image_count": len(images),
        "tags": tags,
    }


def _needs_image_hint(desc: str, image_count: int) -> bool:
    if image_count <= 0:
        return False
    stripped = desc.strip()
    return len(stripped) < 120 or any(kw in stripped for kw in _IMAGE_HINT_KEYWORDS)


class XhsAdapter:
    """小红书业务适配层：隔离 MCP 原始接口、访问凭证、限流与结果裁剪。"""

    def __init__(
        self,
        mcp_client: MCPClientProtocol,
        *,
        rate_limiter: RateLimiter | None = None,
        cache: NoteCache | None = None,
        search_timeout: float = 30.0,
        detail_timeout: float = 90.0,
        default_detail_preview_chars: int = 1200,
    ) -> None:
        self.mcp = mcp_client
        self.rate_limiter = rate_limiter or RateLimiter()
        self.cache = cache if cache is not None else NoteCache()
        self.search_timeout = float(search_timeout)
        self.detail_timeout = float(detail_timeout)
        self.default_detail_preview_chars = int(default_detail_preview_chars)

    async def call_allowed_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        quota_category: str | None = None,
    ) -> MCPCallResult:
        """所有对 MCP Server 的工具调用唯一出口：强制白名单 + 串行限流。"""
        if name not in ALLOWED_MCP_TOOLS:
            raise MCPToolNotAllowedError(name)

        async with self.rate_limiter.acquire(category=quota_category):
            result = await self.mcp.call_tool(name, arguments or {}, timeout=timeout)

        if result.is_error:
            raise MCPToolExecutionError(name, result.text or "未知错误")
        return result

    async def validate_mcp_tools(self) -> dict[str, MCPToolInfo]:
        """启动预检：验证所有白名单工具存在且必填参数 Schema 符合预期。"""
        tools = await self.mcp.list_tools()
        by_name = {t.name: t for t in tools}

        missing = sorted(ALLOWED_MCP_TOOLS - by_name.keys())
        if missing:
            raise MCPToolSchemaError(f"MCP Server 缺少必需白名单工具: {missing}")

        for tool_name, expected_required in EXPECTED_MCP_TOOL_REQUIRED_PARAMS.items():
            tool_info = by_name[tool_name]
            schema = tool_info.input_schema or {}
            actual_required = set(schema.get("required") or [])
            props = set((schema.get("properties") or {}).keys())

            if not expected_required.issubset(props):
                raise MCPToolSchemaError(
                    f"MCP 工具 '{tool_name}' 缺少预期参数字段：期望 {sorted(expected_required)}，实际 properties={sorted(props)}"
                )
            unexpected_required = actual_required - expected_required
            if unexpected_required:
                raise MCPToolSchemaError(
                    f"MCP 工具 '{tool_name}' 新增了未适配的必填参数: {sorted(unexpected_required)}"
                )

        return {name: by_name[name] for name in sorted(ALLOWED_MCP_TOOLS)}

    async def check_login_status(self) -> dict[str, Any]:
        """预检登录状态（由 Runtime 调用，不注册给 LLM）。"""
        res = await self.call_allowed_tool("check_login_status", {}, timeout=30.0, quota_category=None)
        text_lower = res.text.lower()
        logged_in = (
            ("已登录" in res.text or "logged in" in text_lower)
            and "未登录" not in res.text
            and "not logged" not in text_lower
        )
        return {
            "logged_in": logged_in,
            "message": res.text,
        }

    async def search_notes(
        self,
        keyword: str,
        *,
        sort_by: str | None = None,
        publish_time: str | None = None,
        note_type: str | None = None,
        search_scope: str | None = None,
        location: str | None = None,
    ) -> dict[str, Any]:
        """搜索小红书笔记，将凭证存入 NoteCache，只返回精简列表给 LLM。"""
        cleaned_keyword = keyword.strip()
        if not cleaned_keyword:
            raise ValueError("keyword 不能为空")

        filters: dict[str, str] = {}
        raw_filters = {
            "sort_by": sort_by,
            "publish_time": publish_time,
            "note_type": note_type,
            "search_scope": search_scope,
            "location": location,
        }
        for key, val in raw_filters.items():
            if val is None or val == "":
                continue
            allowed_vals = VALID_SEARCH_FILTERS[key]
            if val not in allowed_vals:
                raise ValueError(f"筛选参数 {key}='{val}' 不合法，可选值：{sorted(allowed_vals)}")
            filters[key] = val

        args: dict[str, Any] = {"keyword": cleaned_keyword}
        if filters:
            args["filters"] = filters

        res = await self.call_allowed_tool(
            "search_feeds",
            args,
            timeout=self.search_timeout,
            quota_category="search",
        )
        raw_items = _extract_feed_items(res.parsed)

        trimmed_notes: list[dict[str, Any]] = []
        seen_short_ids: set[str] = set()
        for raw_item in raw_items:
            try:
                cached = self.cache.put(raw_item)
            except ValueError:
                continue
            if cached.short_id in seen_short_ids:
                continue
            seen_short_ids.add(cached.short_id)

            item_view: dict[str, Any] = {
                "note": cached.short_id,
                "title": cached.title or "(无标题)",
                "author": cached.author,
                "published_at": _format_dt_local(cached.published_at),
            }
            if cached.liked_count is not None:
                item_view["liked_count"] = cached.liked_count
            if cached.comment_count is not None:
                item_view["comment_count"] = cached.comment_count
            trimmed_notes.append(item_view)

        return {
            "keyword": cleaned_keyword,
            "count": len(trimmed_notes),
            "notes": trimmed_notes,
        }

    async def fetch_note_detail_raw(self, note: str) -> tuple[CachedNote, dict[str, Any], bool]:
        """内部接口：获取完整详情原始 JSON（供 get_note_detail 与阶段 4 抽取管道共用）。

        返回 (cached_note, detail_dict, from_cache)。
        """
        short_id = note.strip()
        cached = self.cache.get(short_id)
        if cached is None:
            raise UnknownNoteError(short_id)

        if cached.detail is not None:
            return cached, cached.detail, True

        if not cached.xsec_token:
            raise MCPError(f"笔记 '{short_id}' 缺少 xsec_token 凭证，无法获取详情。", retryable=False)

        res = await self.call_allowed_tool(
            "get_feed_detail",
            {"feed_id": cached.feed_id, "xsec_token": cached.xsec_token},
            timeout=self.detail_timeout,
            quota_category="detail",
        )
        detail_payload = res.parsed if isinstance(res.parsed, dict) else {"raw_text": res.text}
        updated = self.cache.set_detail(short_id, detail_payload)
        return updated, detail_payload, False

    async def get_note_detail(
        self,
        note: str,
        *,
        max_content_chars: int | None = None,
    ) -> dict[str, Any]:
        """预览单篇笔记详情（已缓存则不重复请求小红书），返回按字段裁剪后的精简 JSON。"""
        cached, raw_detail, from_cache = await self.fetch_note_detail_raw(note)
        fields = _extract_detail_fields(raw_detail, cached)

        limit = self.default_detail_preview_chars if max_content_chars is None else max_content_chars
        desc: str = fields["desc"]
        truncated = False
        if limit is not None and limit > 0 and len(desc) > limit:
            desc = desc[:limit] + "…"
            truncated = True

        return {
            "note": cached.short_id,
            "title": fields["title"] or "(无标题)",
            "author": fields["author"],
            "published_at": _format_dt_local(fields["published_dt"]),
            "ip_location": fields["ip_location"],
            "content": desc,
            "content_length": len(fields["desc"]),
            "content_truncated": truncated,
            "image_count": fields["image_count"],
            "has_images": fields["image_count"] > 0,
            "needs_image_hint": _needs_image_hint(fields["desc"], fields["image_count"]),
            "source_url": cached.source_url,
            "from_cache": from_cache,
        }
