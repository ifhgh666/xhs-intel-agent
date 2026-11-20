"""Context Manager：管理对话消息历史、工具结果回填、成对裁剪与会话隔离。

核心约束（见 docs/design.md 第 11 节）：
1. system prompt 固定放在最前，消息仅向后追加（最大化 DeepSeek 前缀缓存命中）
2. 每个 tool_call_id 对应一条 role="tool" 消息
3. 超过长度阈值时，按“轮次组”成对处理：assistant(tool_calls) 与其全部 tool 响应要么成对保留，要么成对压缩/删除，
   绝不破坏 API 要求的 assistant(tool_calls) -> tool 配对协议。
"""

from __future__ import annotations

import json
import uuid
from typing import Any


def new_session_id(prefix: str = "s") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _estimate_chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)


def _summarize_tool_message(msg: dict[str, Any]) -> dict[str, Any]:
    """将较早的 tool 结果替换为一行紧凑合法 JSON 摘要（不直接按字符暴力截断破坏 JSON）。"""
    raw = msg.get("content", "")
    summary_text = "历史工具结果已压缩"
    try:
        payload = json.loads(raw) if isinstance(raw, str) else raw
        if isinstance(payload, dict):
            if payload.get("ok") is False:
                summary_text = f"历史错误: {str(payload.get('error', ''))[:80]}"
            else:
                data = payload.get("data", payload)
                if isinstance(data, dict):
                    if "notes" in data and isinstance(data["notes"], list):
                        kw = data.get("keyword", "")
                        ids = [n.get("note") for n in data["notes"][:5] if isinstance(n, dict)]
                        summary_text = (
                            f"搜索({kw})返回 {len(data['notes'])} 篇，前几篇={ids}"
                        )
                    elif "note" in data and "title" in data:
                        summary_text = (
                            f"已查看笔记 {data.get('note')}《{str(data.get('title', ''))[:30]}》"
                        )
                    elif "status" in data and "note" in data:
                        summary_text = (
                            f"已抽取笔记 {data.get('note')} status={data.get('status')} "
                            f"created={data.get('created', 0)}"
                        )
    except Exception:
        pass

    return {
        "role": "tool",
        "tool_call_id": msg["tool_call_id"],
        "content": json.dumps({"ok": True, "summary": summary_text}, ensure_ascii=False),
    }


def _group_turns(messages: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    """将非头部消息按原子轮次分组：assistant(tool_calls) + 紧随其后的全部 tool 消息为同一组。"""
    groups: list[list[dict[str, Any]]] = []
    i = 0
    n = len(messages)
    while i < n:
        msg = messages[i]
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            group = [msg]
            i += 1
            while i < n and messages[i].get("role") == "tool":
                group.append(messages[i])
                i += 1
            groups.append(group)
        else:
            groups.append([msg])
            i += 1
    return groups


class ContextManager:
    """单会话上下文管理器。"""

    def __init__(
        self,
        system_prompt: str,
        *,
        session_id: str | None = None,
        max_chars: int = 48_000,
        keep_recent_turns: int = 6,
    ) -> None:
        self.session_id = session_id or new_session_id()
        self.max_chars = int(max_chars)
        self.keep_recent_turns = int(keep_recent_turns)
        self._messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
        ]

    def add_user(self, content: str) -> None:
        self._messages.append({"role": "user", "content": content})

    def add_system(self, content: str) -> None:
        self._messages.append({"role": "system", "content": content})

    def add_assistant(
        self,
        content: str | None,
        tool_calls: list[dict[str, Any]] | None = None,
    ) -> None:
        msg: dict[str, Any] = {
            "role": "assistant",
            "content": content or "",
        }
        if tool_calls:
            msg["tool_calls"] = tool_calls
        self._messages.append(msg)

    def add_tool_result(self, tool_call_id: str, result: Any) -> None:
        content = (
            result
            if isinstance(result, str)
            else json.dumps(result, ensure_ascii=False)
        )
        self._messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": content,
            }
        )

    def raw_messages(self) -> list[dict[str, Any]]:
        return list(self._messages)

    def messages(self) -> list[dict[str, Any]]:
        """返回用于发给 LLM 的消息列表；仅超过 max_chars 时执行安全成对裁剪。"""
        if _estimate_chars(self._messages) <= self.max_chars:
            return list(self._messages)

        # 始终保留：首条 system prompt + 原始 user 请求（索引 0 与 1，若存在）
        head_end = 1
        if len(self._messages) > 1 and self._messages[1].get("role") == "user":
            head_end = 2

        head = list(self._messages[:head_end])
        tail = list(self._messages[head_end:])
        groups = _group_turns(tail)

        if len(groups) <= self.keep_recent_turns:
            return head + [m for g in groups for m in g]

        older_groups = groups[: -self.keep_recent_turns]
        recent_groups = groups[-self.keep_recent_turns :]

        # 第一阶段裁剪：先把较早轮次中的 tool 返回替换为一行合法 JSON 摘要（保留配对结构）
        compressed_older: list[list[dict[str, Any]]] = []
        for group in older_groups:
            new_group = [
                _summarize_tool_message(m) if m.get("role") == "tool" else m
                for m in group
            ]
            compressed_older.append(new_group)

        candidate = head + [m for g in compressed_older for m in g] + [m for g in recent_groups for m in g]
        if _estimate_chars(candidate) <= self.max_chars:
            return candidate

        # 第二阶段裁剪：如果仍超限，则按原子轮次成对丢弃最早的 older_groups
        while compressed_older and _estimate_chars(
            head + [m for g in compressed_older for m in g] + [m for g in recent_groups for m in g]
        ) > self.max_chars:
            compressed_older.pop(0)

        return head + [m for g in compressed_older for m in g] + [m for g in recent_groups for m in g]
