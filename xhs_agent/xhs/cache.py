"""NoteCache：小红书搜索结果与笔记详情的内部缓存。

设计原则（见 docs/design.md 第 6 节）：
- NoteCache 仅供 Harness / 适配层内部使用，不作为工具暴露给 LLM
- 为每篇笔记分配短编号（n1、n2...），隐藏 feed_id 与 xsec_token 凭证
- 同一 feed_id 多次出现时复用同一个短编号，避免重复拉取详情
- 结合阶段 1 实测规律：search_feeds 不返回 time 字段，但 24 位 hex feed_id 的前 8 位即精确发布时间戳（误差 0s）
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

_HEX8_RE = re.compile(r"^[0-9a-fA-F]{8}")


def decode_feed_id_timestamp(feed_id: str | None) -> datetime | None:
    """从 24 位 hex feed_id 的前 8 位解码发布时间（UTC aware datetime）。"""
    if not feed_id or len(feed_id) < 8 or not _HEX8_RE.match(feed_id):
        return None
    ts = int(feed_id[:8], 16)
    # 合理范围：2015-01-01 ~ 2035-01-01
    if not (1_420_070_400 <= ts <= 2_051_222_400):
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def parse_timestamp(value: Any) -> datetime | None:
    """将毫秒/秒级时间戳或 ISO 字符串解析为 UTC aware datetime。"""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        if 1_420_070_400 <= ts <= 2_051_222_400:
            return datetime.fromtimestamp(ts, tz=timezone.utc)
        return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return parse_timestamp(int(text))
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def build_source_url(feed_id: str) -> str:
    return f"https://www.xiaohongshu.com/explore/{feed_id}"


def build_open_url(feed_id: str, xsec_token: str | None = None) -> str:
    """构造可直达小红书网页端的原帖链接（有 xsec_token 时自动拼接，减少风控拦截）。"""
    base = build_source_url(feed_id)
    tok = (xsec_token or "").strip()
    if not tok:
        return base
    query = urlencode({"xsec_token": tok, "xsec_source": "pc_search"})
    return f"{base}?{query}"


@dataclass
class CachedNote:
    short_id: str
    feed_id: str
    xsec_token: str
    title: str
    author: str
    published_at: datetime | None
    source_url: str
    raw_feed: dict[str, Any]
    liked_count: str | None = None
    comment_count: str | None = None
    collected_count: str | None = None
    detail: dict[str, Any] | None = None


class NoteCache:
    """笔记短编号与访问凭证缓存（内存版，阶段 4 可叠加 SQLite notes 表持久化）。"""

    def __init__(self) -> None:
        self._by_short: dict[str, CachedNote] = {}
        self._by_feed: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._by_short)

    def put(self, raw_feed: dict[str, Any]) -> CachedNote:
        """存入一条 search_feeds 原始记录并返回对应 CachedNote（同一 feed_id 去重）。"""
        note_card = raw_feed.get("noteCard") or raw_feed.get("note_card") or {}
        if not isinstance(note_card, dict):
            note_card = {}

        user = note_card.get("user") or raw_feed.get("user") or {}
        if not isinstance(user, dict):
            user = {}

        interact = note_card.get("interactInfo") or note_card.get("interact_info") or {}
        if not isinstance(interact, dict):
            interact = {}

        raw_feed_id = (
            raw_feed.get("id")
            or raw_feed.get("feed_id")
            or raw_feed.get("note_id")
            or raw_feed.get("noteId")
            or note_card.get("noteId")
            or note_card.get("id")
        )
        if not raw_feed_id:
            raise ValueError("搜索结果缺少 feed_id（id）字段，无法写入 NoteCache")
        feed_id = str(raw_feed_id)

        raw_token = (
            raw_feed.get("xsecToken")
            or raw_feed.get("xsec_token")
            or note_card.get("xsecToken")
            or note_card.get("xsec_token")
            or ""
        )
        xsec_token = str(raw_token)

        title = str(
            raw_feed.get("title")
            or note_card.get("displayTitle")
            or note_card.get("title")
            or ""
        ).strip()
        author = str(
            user.get("nickname")
            or user.get("nickName")
            or user.get("name")
            or raw_feed.get("author")
            or ""
        ).strip()

        published_at = (
            parse_timestamp(raw_feed.get("time"))
            or parse_timestamp(note_card.get("time"))
            or parse_timestamp(raw_feed.get("published_at"))
            or decode_feed_id_timestamp(feed_id)
        )

        liked_count = interact.get("likedCount")
        comment_count = interact.get("commentCount")
        collected_count = interact.get("collectedCount")

        existing_sid = self._by_feed.get(feed_id)
        if existing_sid is not None:
            existing = self._by_short[existing_sid]
            if xsec_token:
                existing.xsec_token = xsec_token
            if title and not existing.title:
                existing.title = title
            if author and not existing.author:
                existing.author = author
            if published_at and existing.published_at is None:
                existing.published_at = published_at
            return existing

        short_id = f"n{len(self._by_short) + 1}"
        entry = CachedNote(
            short_id=short_id,
            feed_id=feed_id,
            xsec_token=xsec_token,
            title=title,
            author=author,
            published_at=published_at,
            source_url=build_source_url(feed_id),
            raw_feed=raw_feed,
            liked_count=str(liked_count) if liked_count is not None else None,
            comment_count=str(comment_count) if comment_count is not None else None,
            collected_count=str(collected_count) if collected_count is not None else None,
        )
        self._by_short[short_id] = entry
        self._by_feed[feed_id] = short_id
        return entry

    def get(self, short_id: str) -> CachedNote | None:
        return self._by_short.get(short_id)

    def get_by_feed_id(self, feed_id: str) -> CachedNote | None:
        sid = self._by_feed.get(feed_id)
        return self._by_short.get(sid) if sid is not None else None

    def set_detail(self, short_id: str, detail: dict[str, Any]) -> CachedNote:
        """回填笔记详情，并补全标题/作者/发布时间。"""
        entry = self._by_short.get(short_id)
        if entry is None:
            raise KeyError(f"未知短编号: {short_id}")
        entry.detail = detail

        node = detail
        if isinstance(node.get("data"), dict):
            node = node["data"]
        if isinstance(node.get("note"), dict):
            node = node["note"]

        if isinstance(node, dict):
            if node.get("title"):
                entry.title = str(node["title"]).strip()
            user = node.get("user")
            if isinstance(user, dict) and (user.get("nickname") or user.get("nickName")):
                entry.author = str(user.get("nickname") or user.get("nickName")).strip()
            dt = parse_timestamp(node.get("time"))
            if dt is not None:
                entry.published_at = dt

        return entry

    def all_notes(self) -> list[CachedNote]:
        return list(self._by_short.values())
