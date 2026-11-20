"""Runtime：启动预检、工具注册、预算组装与资源生命周期管理。"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from xhs_agent.config import AppConfig, ROOT_DIR, load_config
from xhs_agent.harness.executor import BudgetTracker, ExecutionEngine
from xhs_agent.harness.llm import DeepSeekClient, LLMClientProtocol
from xhs_agent.harness.loop import AgentLoop, AgentRunResult, EventCallback
from xhs_agent.harness.registry import ToolRegistry, ToolSpec
from xhs_agent.xhs import MCPClient, RateLimiter, XhsAdapter
from xhs_agent.xhs.adapter import VALID_SEARCH_FILTERS

DEFAULT_AGENT_PROMPT_PATH = ROOT_DIR / "xhs_agent" / "prompts" / "agent_system.md"


class StartupPrecheckError(RuntimeError):
    """Runtime 启动预检失败（MCP 不可达、Schema 不匹配或未登录小红书）。"""


def render_agent_system_prompt(
    prompt_path: Path | None = None,
    *,
    current_date: str | None = None,
) -> str:
    path = prompt_path or DEFAULT_AGENT_PROMPT_PATH
    template = path.read_text(encoding="utf-8")
    today = current_date or date.today().isoformat()
    return template.format(current_date=today)


def build_phase3_registry(adapter: XhsAdapter, config: AppConfig) -> ToolRegistry:
    """注册阶段 3 可见给主 LLM 的两个核心只读业务工具：search_notes 与 get_note_detail。"""
    registry = ToolRegistry()

    registry.register(
        ToolSpec(
            name="search_notes",
            description=(
                "在小红书搜索笔记。返回精简列表（含短编号 n1/n2...、标题、作者、发布时间与互动数）。"
                "建议围绕城市、技术方向和“招/招聘/内推”拆分 2-4 个关键词分别搜索。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "keyword": {
                        "type": "string",
                        "minLength": 1,
                        "description": "搜索关键词，例如 '广州 AI Agent 实习 招'",
                    },
                    "sort_by": {
                        "type": "string",
                        "enum": sorted(VALID_SEARCH_FILTERS["sort_by"]),
                        "description": "可选排序方式：综合/最新/最多点赞/最多评论/最多收藏",
                    },
                    "publish_time": {
                        "type": "string",
                        "enum": sorted(VALID_SEARCH_FILTERS["publish_time"]),
                        "description": "可选发布时间筛选：不限/一天内/一周内/半年内",
                    },
                    "note_type": {
                        "type": "string",
                        "enum": sorted(VALID_SEARCH_FILTERS["note_type"]),
                        "description": "可选笔记类型：不限/视频/图文",
                    },
                },
                "required": ["keyword"],
                "additionalProperties": False,
            },
            handler=adapter.search_notes,
            read_only=True,
            timeout=35.0,
            max_calls_per_task=config.budget.max_search_calls,
            budget_group="search",
        )
    )

    registry.register(
        ToolSpec(
            name="get_note_detail",
            description=(
                "根据短编号（如 'n1'）获取单篇小红书笔记详情（标题、正文、发布时间、图片数、来源链接）。"
                "仅对筛选后确认为潜在招聘帖的笔记调用；已查看过的笔记会直接走本地缓存。"
            ),
            parameters={
                "type": "object",
                "properties": {
                    "note": {
                        "type": "string",
                        "minLength": 1,
                        "description": "笔记短编号，必须来自 search_notes 返回结果（如 'n1'、'n3'）",
                    },
                },
                "required": ["note"],
                "additionalProperties": False,
            },
            handler=adapter.get_note_detail,
            read_only=True,
            timeout=95.0,
            max_calls_per_task=config.budget.max_detail_calls,
            budget_group="detail",
        )
    )

    return registry


class AgentRuntime:
    """Agent 运行时容器：负责启动预检、装配 Harness 组件与安全退出。"""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        adapter: XhsAdapter | None = None,
        llm: LLMClientProtocol | None = None,
        traces_dir: Path | str | None = None,
        prompt_path: Path | None = None,
        current_date: str | None = None,
    ) -> None:
        self.config = config or load_config()
        self._injected_adapter = adapter
        self._injected_llm = llm
        self.traces_dir = Path(traces_dir) if traces_dir is not None else (ROOT_DIR / "traces")
        self.prompt_path = prompt_path
        self.current_date = current_date

        self._mcp_client: MCPClient | None = None
        self.adapter: XhsAdapter | None = None
        self.llm: LLMClientProtocol | None = None
        self.registry: ToolRegistry | None = None
        self.precheck_summary: dict[str, Any] = {}

    async def start(self) -> None:
        """启动预检：连接 MCP → 校验白名单工具与 Schema → 检查登录态 → 装配注册表与 LLM。"""
        if self._injected_adapter is not None:
            self.adapter = self._injected_adapter
        else:
            self._mcp_client = MCPClient(self.config.mcp.url)
            try:
                await self._mcp_client.connect()
            except Exception as exc:
                raise StartupPrecheckError(
                    f"无法连接 xiaohongshu-mcp ({self.config.mcp.url}): {exc}\n"
                    "请确认 MCP 容器或服务已启动（如 docker compose up -d）。"
                ) from exc

            limiter = RateLimiter(
                min_interval_sec=self.config.rate_limit.min_interval_sec,
                jitter_sec=self.config.rate_limit.jitter_sec,
                daily_limits={
                    "search": self.config.rate_limit.daily_search_limit,
                    "detail": self.config.rate_limit.daily_detail_limit,
                },
            )
            self.adapter = XhsAdapter(self._mcp_client, rate_limiter=limiter)

        # 1. 白名单工具与参数 Schema 预检
        try:
            validated = await self.adapter.validate_mcp_tools()
        except Exception as exc:
            await self.close()
            raise StartupPrecheckError(f"MCP 工具白名单/Schema 预检失败: {exc}") from exc

        # 2. 登录状态预检
        try:
            login = await self.adapter.check_login_status()
        except Exception as exc:
            await self.close()
            raise StartupPrecheckError(f"小红书登录状态预检失败: {exc}") from exc

        if not login.get("logged_in"):
            await self.close()
            raise StartupPrecheckError(
                f"小红书尚未登录（{login.get('message', '')}）。\n"
                "请先运行 `uv run python scripts/probe_login.py --save-qrcode` 获取二维码并扫码登录。"
            )

        # 3. 装配 LLM 客户端与业务工具注册表
        if self._injected_llm is not None:
            self.llm = self._injected_llm
        else:
            self.llm = DeepSeekClient(self.config.llm)

        self.registry = build_phase3_registry(self.adapter, self.config)
        self.precheck_summary = {
            "mcp_url": self.config.mcp.url,
            "validated_mcp_tools": sorted(validated.keys()),
            "logged_in": True,
            "registered_agent_tools": self.registry.names(),
        }

    async def close(self) -> None:
        if self._mcp_client is not None:
            await self._mcp_client.close()
            self._mcp_client = None
        if self._injected_llm is None and isinstance(self.llm, DeepSeekClient):
            await self.llm.close()

    async def __aenter__(self) -> AgentRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    async def run_task(
        self,
        task: str,
        *,
        session_id: str | None = None,
        on_event: EventCallback | None = None,
    ) -> AgentRunResult:
        if self.llm is None or self.registry is None or self.adapter is None:
            raise RuntimeError("AgentRuntime 尚未启动，请使用 async with AgentRuntime() 或先调用 start()。")

        budget = BudgetTracker.from_config(self.config.budget)
        executor = ExecutionEngine(self.registry, budget)
        system_prompt = render_agent_system_prompt(
            self.prompt_path,
            current_date=self.current_date,
        )
        loop = AgentLoop(
            llm=self.llm,
            registry=self.registry,
            executor=executor,
            budget=budget,
            system_prompt=system_prompt,
            traces_dir=self.traces_dir,
        )
        return await loop.run(task, session_id=session_id, on_event=on_event)
