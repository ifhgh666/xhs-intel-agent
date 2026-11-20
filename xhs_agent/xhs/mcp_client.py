"""小红书 MCP Client 封装。

职责（见 docs/design.md 第 5 节与 docs/development_plan.md 阶段 2）：
- 管理 Streamable HTTP 长连接生命周期（connect / close / async with）
- list_tools() 返回统一工具元数据与 JSON Schema
- call_tool(name, args, timeout) 支持超时控制、自动解析 JSON 文本、断线自动重连一次
- 兼容 mcp 2.x（streamable_http_client / input_schema / is_error）与 1.x
"""

from __future__ import annotations

import asyncio
import json
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from typing import Any, Protocol

from mcp import ClientSession

try:
    from mcp.client.streamable_http import (
        StreamableHTTPError,
        streamable_http_client as _streamable_client,
    )
except ImportError:  # pragma: no cover - 兼容 mcp 1.x
    StreamableHTTPError = RuntimeError  # type: ignore[misc, assignment]
    from mcp.client.streamable_http import (  # type: ignore[attr-defined]
        streamablehttp_client as _streamable_client,
    )


class MCPError(RuntimeError):
    """MCP 客户端基础异常。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        self.retryable = retryable
        super().__init__(message)


class MCPConnectionError(MCPError):
    """MCP 连接或传输断线异常。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class MCPTimeoutError(MCPError):
    """MCP 调用超时异常。"""

    def __init__(self, message: str) -> None:
        super().__init__(message, retryable=True)


class MCPToolNotAllowedError(MCPError):
    """尝试调用白名单外工具时抛出。"""

    def __init__(self, tool_name: str) -> None:
        self.tool_name = tool_name
        super().__init__(f"工具 '{tool_name}' 不在 MCP 白名单内，已被拒绝执行。", retryable=False)


class MCPToolExecutionError(MCPError):
    """MCP Server 返回工具执行错误（is_error=True）时抛出。"""

    def __init__(self, tool_name: str, message: str, *, retryable: bool = False) -> None:
        self.tool_name = tool_name
        super().__init__(f"MCP 工具 '{tool_name}' 执行失败: {message}", retryable=retryable)


@dataclass(frozen=True)
class MCPToolInfo:
    name: str
    description: str
    input_schema: dict[str, Any] = field(default_factory=dict)


@dataclass
class MCPCallResult:
    tool_name: str
    is_error: bool
    text: str
    parsed: Any
    raw_content: list[dict[str, Any]] = field(default_factory=list)


class MCPClientProtocol(Protocol):
    """最小客户端协议，便于测试中直接注入基于 fixtures 的 MockClient。"""

    async def connect(self) -> None: ...

    async def close(self) -> None: ...

    async def list_tools(self) -> list[MCPToolInfo]: ...

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
    ) -> MCPCallResult: ...


def _get_tool_input_schema(tool: Any) -> dict[str, Any]:
    schema = getattr(tool, "input_schema", None)
    if schema is None:
        schema = getattr(tool, "inputSchema", None)
    return schema if isinstance(schema, dict) else {}


def parse_call_tool_result(tool_name: str, raw_result: Any) -> MCPCallResult:
    """将 MCP SDK 的 CallToolResult 解析为 MCPCallResult（自动解码 JSON 文本）。"""
    is_error = bool(getattr(raw_result, "is_error", getattr(raw_result, "isError", False)))
    content_items = getattr(raw_result, "content", None) or []
    structured = getattr(
        raw_result,
        "structured_content",
        getattr(raw_result, "structuredContent", None),
    )

    raw_content: list[dict[str, Any]] = []
    text_parts: list[str] = []

    for item in content_items:
        if hasattr(item, "model_dump"):
            raw_content.append(item.model_dump(mode="json"))
        else:
            raw_content.append({"type": getattr(item, "type", type(item).__name__), "repr": repr(item)})
        text_val = getattr(item, "text", None)
        if isinstance(text_val, str):
            text_parts.append(text_val)

    combined_text = "\n".join(text_parts).strip()
    parsed: Any = structured
    if parsed is None and combined_text:
        try:
            parsed = json.loads(combined_text)
        except json.JSONDecodeError:
            parsed = None

    return MCPCallResult(
        tool_name=tool_name,
        is_error=is_error,
        text=combined_text,
        parsed=parsed,
        raw_content=raw_content,
    )


def _is_connection_exception(exc: BaseException) -> bool:
    """判断异常是否属于网络/会话断开（可触发自动重连一次）。"""
    if isinstance(exc, (ConnectionError, OSError, StreamableHTTPError, MCPConnectionError)):
        return True
    name = type(exc).__name__.lower()
    if any(
        token in name
        for token in (
            "connect",
            "closed",
            "broken",
            "eof",
            "http",
            "transport",
            "remoteprotocol",
            "stream",
        )
    ):
        return True
    return False


class MCPClient:
    """基于官方 MCP Python SDK 的 Streamable HTTP 客户端。"""

    def __init__(self, url: str, *, connect_timeout: float = 15.0) -> None:
        self.url = url
        self.connect_timeout = float(connect_timeout)
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | None = None

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    async def connect(self) -> None:
        """建立并初始化 MCP 会话。若已连接则直接返回。"""
        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            transport_streams = await asyncio.wait_for(
                stack.enter_async_context(_streamable_client(self.url)),
                timeout=self.connect_timeout,
            )
            read_stream, write_stream = transport_streams[0], transport_streams[1]
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await asyncio.wait_for(session.initialize(), timeout=self.connect_timeout)
        except asyncio.TimeoutError as exc:
            await stack.aclose()
            raise MCPTimeoutError(f"连接 MCP Server ({self.url}) 超时（>{self.connect_timeout}s）") from exc
        except Exception as exc:
            await stack.aclose()
            raise MCPConnectionError(f"连接 MCP Server ({self.url}) 失败: {exc}") from exc

        self._stack = stack
        self._session = session

    async def close(self) -> None:
        """安全关闭 MCP 会话与底层连接。"""
        self._session = None
        if self._stack is not None:
            stack = self._stack
            self._stack = None
            try:
                await stack.aclose()
            except Exception:
                pass

    async def reconnect(self) -> None:
        """关闭旧连接并重新建立 MCP 会话。"""
        await self.close()
        await self.connect()

    async def __aenter__(self) -> MCPClient:
        await self.connect()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def _ensure_session(self) -> ClientSession:
        if self._session is None:
            await self.connect()
        assert self._session is not None
        return self._session

    async def list_tools(self, *, timeout: float = 15.0) -> list[MCPToolInfo]:
        """调用 list_tools() 并返回规范化工具信息列表。"""
        session = await self._ensure_session()
        try:
            res = await asyncio.wait_for(session.list_tools(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            raise MCPTimeoutError(f"list_tools 超时（>{timeout}s）") from exc
        except Exception as exc:
            if _is_connection_exception(exc):
                raise MCPConnectionError(f"list_tools 连接异常: {exc}") from exc
            raise MCPError(f"list_tools 调用失败: {exc}") from exc

        return [
            MCPToolInfo(
                name=t.name,
                description=t.description or "",
                input_schema=_get_tool_input_schema(t),
            )
            for t in res.tools
        ]

    async def call_tool(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        timeout: float = 30.0,
        reconnect_on_error: bool = True,
    ) -> MCPCallResult:
        """调用指定 MCP 工具；支持超时、JSON 自动解析，断线时自动重连一次。"""
        args = dict(arguments or {})
        attempts = 2 if reconnect_on_error else 1
        last_conn_err: Exception | None = None

        for attempt in range(attempts):
            try:
                session = await self._ensure_session()
                raw_res = await asyncio.wait_for(
                    session.call_tool(name, args),
                    timeout=timeout,
                )
                return parse_call_tool_result(name, raw_res)
            except asyncio.TimeoutError as exc:
                raise MCPTimeoutError(f"调用 MCP 工具 '{name}' 超时（>{timeout}s）") from exc
            except Exception as exc:
                if _is_connection_exception(exc):
                    last_conn_err = exc
                    if attempt + 1 < attempts:
                        try:
                            await self.reconnect()
                            continue
                        except Exception as reconn_exc:
                            raise MCPConnectionError(
                                f"调用 MCP 工具 '{name}' 断线且自动重连失败: {reconn_exc}"
                            ) from reconn_exc
                    raise MCPConnectionError(f"调用 MCP 工具 '{name}' 连接异常: {exc}") from exc
                raise MCPError(f"调用 MCP 工具 '{name}' 异常: {exc}") from exc

        raise MCPConnectionError(f"调用 MCP 工具 '{name}' 连接失败: {last_conn_err}")
