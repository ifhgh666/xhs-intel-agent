"""Web 后端共享应用状态（降级友好：MCP 未启动或未登录时仍可管理岗位库与查看 Trace）。"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from typing import Any

from xhs_agent.config import AppConfig, ROOT_DIR, load_config
from xhs_agent.harness.llm import LLMClientProtocol
from xhs_agent.jobs.store import DEFAULT_DB_PATH, JobStore
from xhs_agent.jobs.tools import DEFAULT_EXPORT_DIR
from xhs_agent.xhs import MCPClient, MCPClientProtocol, RateLimiter, XhsAdapter

logger = logging.getLogger("xhs_agent.api.state")


class WebAppState:
    """FastAPI 进程级状态容器，由 lifespan 统一初始化与清理。"""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        db_path: Path | str | None = None,
        traces_dir: Path | str | None = None,
        export_dir: Path | str | None = None,
        mcp_client: MCPClientProtocol | None = None,
        adapter: XhsAdapter | None = None,
        llm: LLMClientProtocol | None = None,
        extractor_llm: LLMClientProtocol | None = None,
        auto_connect_mcp: bool = True,
    ) -> None:
        self.config = config or load_config()
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.traces_dir = Path(traces_dir) if traces_dir is not None else (ROOT_DIR / "traces")
        self.export_dir = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
        self._injected_mcp_client = mcp_client
        self._injected_adapter = adapter
        self._injected_llm = llm
        self._injected_extractor_llm = extractor_llm
        self.auto_connect_mcp = auto_connect_mcp

        self.store: JobStore | None = None
        self.mcp_client: MCPClientProtocol | None = None
        self.adapter: XhsAdapter | None = None

        self.mcp_connected: bool = False
        self.logged_in: bool = False
        self.login_message: str = ""
        self.mcp_error: str | None = None
        self.validated_tools: list[str] = []
        self.active_session_id: str | None = None

    async def startup(self) -> None:
        self.traces_dir.mkdir(parents=True, exist_ok=True)
        self.export_dir.mkdir(parents=True, exist_ok=True)
        self.store = JobStore(self.db_path)

        if self._injected_adapter is not None:
            self.adapter = self._injected_adapter
        else:
            self.mcp_client = self._injected_mcp_client or MCPClient(self.config.mcp.url)
            limiter = RateLimiter(
                min_interval_sec=self.config.rate_limit.min_interval_sec,
                jitter_sec=self.config.rate_limit.jitter_sec,
                daily_limits={
                    "search": self.config.rate_limit.daily_search_limit,
                    "detail": self.config.rate_limit.daily_detail_limit,
                },
                counter=self.store,
            )
            self.adapter = XhsAdapter(self.mcp_client, rate_limiter=limiter)

        if self.auto_connect_mcp:
            await self.reconnect_mcp()

    async def shutdown(self) -> None:
        if self._injected_adapter is None and self._injected_mcp_client is None and self.mcp_client is not None:
            try:
                await self.mcp_client.close()
            except Exception:
                pass
        if self.store is not None:
            self.store.close()
            self.store = None

    async def reconnect_mcp(self) -> dict[str, Any]:
        """尝试连接 MCP、验证白名单工具并检查登录态；失败时不抛异常，进入降级态。"""
        assert self.adapter is not None
        self.mcp_error = None
        try:
            if self._injected_adapter is None and self.mcp_client is not None:
                await self.mcp_client.close()
                await self.mcp_client.connect()
            validated = await self.adapter.validate_mcp_tools()
            self.validated_tools = sorted(validated.keys())
            self.mcp_connected = True
        except Exception as exc:
            logger.warning("MCP 连接或预检失败（进入降级模式）: %s", exc)
            self.mcp_connected = False
            self.logged_in = False
            self.login_message = ""
            self.validated_tools = []
            self.mcp_error = str(exc)
            return {
                "mcp_connected": False,
                "logged_in": False,
                "login_message": "",
                "validated_tools": [],
                "error": self.mcp_error,
            }

        await self.refresh_login_status()
        return {
            "mcp_connected": self.mcp_connected,
            "logged_in": self.logged_in,
            "login_message": self.login_message,
            "validated_tools": list(self.validated_tools),
            "error": self.mcp_error,
        }

    async def refresh_login_status(self) -> bool:
        if not self.mcp_connected or self.adapter is None:
            self.logged_in = False
            return False
        try:
            res = await self.adapter.check_login_status()
            self.logged_in = bool(res.get("logged_in"))
            self.login_message = str(res.get("message") or "")
            self.mcp_error = None
            return self.logged_in
        except Exception as exc:
            logger.warning("刷新小红书登录状态失败: %s", exc)
            self.logged_in = False
            self.mcp_error = str(exc)
            return False

    def get_daily_usage_dict(self) -> dict[str, Any]:
        today = date.today().isoformat()
        search_used = self.store.get_count(today, "search") if self.store is not None else 0
        detail_used = self.store.get_count(today, "detail") if self.store is not None else 0
        return {
            "day": today,
            "search": {
                "used": search_used,
                "limit": self.config.rate_limit.daily_search_limit,
            },
            "detail": {
                "used": detail_used,
                "limit": self.config.rate_limit.daily_detail_limit,
            },
        }
