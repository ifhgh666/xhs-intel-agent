"""Runtime：启动预检、SQLite 初始化、全量业务工具注册、多轮会话与资源生命周期管理。"""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any

from xhs_agent.config import AppConfig, ROOT_DIR, load_config
from xhs_agent.harness.context import ContextManager
from xhs_agent.harness.executor import BudgetTracker, ExecutionEngine
from xhs_agent.harness.llm import DeepSeekClient, LLMClientProtocol, TokenUsage
from xhs_agent.harness.loop import AgentLoop, AgentRunResult, EventCallback
from xhs_agent.harness.pricing import CostEstimate, estimate_cost
from xhs_agent.harness.registry import ToolRegistry, ToolSpec
from xhs_agent.harness.trace import TraceLogger
from xhs_agent.jobs.store import DEFAULT_DB_PATH, JobStore
from xhs_agent.xhs import MCPClient, RateLimiter, XhsAdapter
from xhs_agent.xhs.adapter import VALID_SEARCH_FILTERS

if TYPE_CHECKING:
    from xhs_agent.jobs.tools import JobTools
    from xhs_agent.knowledge.tools import KnowledgeTools

DEFAULT_AGENT_PROMPT_PATH = ROOT_DIR / "xhs_agent" / "prompts" / "agent_system.md"
DEFAULT_EXPORT_DIR = ROOT_DIR / "data"
_VALID_JOB_STATUSES = ["new", "interested", "applied", "rejected", "expired"]
_VALID_EXP_CATEGORIES = ["roadmap", "project", "interview", "tips"]
_VALID_EXP_STATUSES = ["unread", "reading", "starred", "archived"]

MODE_PREFIX_HINTS: dict[str, str] = {
    "jobs": "[当前模式：仅岗位情报模式 — 请专注搜索招聘帖并使用 extract_and_save_jobs / query_jobs / export_jobs，不要抽取学习经验]\n",
    "experience": "[当前模式：仅经验知识模式 — 请专注搜索学习路线/项目/面经/避坑干货（优先按最多收藏或最多点赞排序），使用 extract_and_save_experience / query_experiences / export_experiences，不要调用 extract_and_save_jobs]\n",
}


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
    """注册只读检索工具：search_notes 与 get_note_detail。"""
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
                "可选预览工具：根据短编号（如 'n1'）预览笔记正文片段。"
                "仅在标题过于模糊、无法判断是否为招聘帖时使用；明确是招聘帖时请直接调用 extract_and_save_jobs。"
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


def build_v1_registry(
    adapter: XhsAdapter,
    config: AppConfig,
    job_tools: JobTools | None = None,
    knowledge_tools: KnowledgeTools | None = None,
) -> ToolRegistry:
    """注册双模式全量业务工具（搜索/预览 + 岗位 4 工具 + 经验 4 工具）。"""
    registry = build_phase3_registry(adapter, config)
    if job_tools is not None:
        registry.register(
            ToolSpec(
                name="extract_and_save_jobs",
                description=(
                    "岗位入库管道：自动获取笔记详情（优先走缓存）、独立抽取结构化招聘岗位、做原文防编造校验、"
                    "笔记级与岗位级去重并写入本地 SQLite jobs 表，仅返回一行精简摘要。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "minLength": 1,
                            "description": "笔记短编号，必须来自 search_notes 返回结果（如 'n1'）",
                        },
                    },
                    "required": ["note"],
                    "additionalProperties": False,
                },
                handler=job_tools.extract_and_save_jobs,
                read_only=False,
                timeout=150.0,
                max_calls_per_task=config.budget.max_detail_calls,
                budget_group="detail",
            )
        )

        query_filter_props: dict[str, Any] = {
            "city": {"type": "string", "description": "城市关键词，如 '广州'"},
            "keyword": {"type": "string", "description": "公司、岗位、技能或标题关键词，如 'Agent'、'RAG'"},
            "graduation_year": {"type": "string", "description": "届别关键词，如 '2028'"},
            "since": {"type": "string", "description": "起始发布时间（ISO 日期，如 '2026-09-20'）"},
            "status": {
                "type": "string",
                "enum": _VALID_JOB_STATUSES,
                "description": "岗位状态筛选：new / interested / applied / rejected / expired",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "最多返回条数，默认 20",
            },
        }

        registry.register(
            ToolSpec(
                name="query_jobs",
                description=(
                    "按城市、关键词、届别、时间或状态查询本地 SQLite 岗位库，返回已验证的结构化岗位列表及来源链接。"
                ),
                parameters={
                    "type": "object",
                    "properties": query_filter_props,
                    "additionalProperties": False,
                },
                handler=job_tools.query_jobs,
                read_only=True,
                timeout=15.0,
            )
        )

        export_props = dict(query_filter_props)
        export_props["path"] = {
            "type": "string",
            "description": "可选导出文件路径或文件名（如 'gz_ai_interns.xlsx'），不填则自动保存到 data/ 目录",
        }

        registry.register(
            ToolSpec(
                name="export_jobs",
                description=(
                    "将符合筛选条件的本地岗位导出为 Excel (.xlsx) 文件（包含来源链接列、过期提醒与看图标记），"
                    "返回生成的文件绝对路径与导出条数。"
                ),
                parameters={
                    "type": "object",
                    "properties": export_props,
                    "additionalProperties": False,
                },
                handler=job_tools.export_jobs,
                read_only=False,
                timeout=30.0,
            )
        )

        registry.register(
            ToolSpec(
                name="mark_job",
                description=(
                    "更新本地岗位库中某条岗位的跟进状态：new（新发现）、interested（感兴趣）、"
                    "applied（已投递）、rejected（已拒绝/不合适）、expired（已过期）。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "岗位 ID（来自 query_jobs 返回的 id 字段）",
                        },
                        "status": {
                            "type": "string",
                            "enum": _VALID_JOB_STATUSES,
                            "description": "目标状态：new / interested / applied / rejected / expired",
                        },
                    },
                    "required": ["job_id", "status"],
                    "additionalProperties": False,
                },
                handler=job_tools.mark_job,
                read_only=False,
                timeout=15.0,
            )
        )

    if knowledge_tools is not None:
        registry.register(
            ToolSpec(
                name="extract_and_save_experience",
                description=(
                    "经验知识入库管道：自动获取笔记详情（优先走本地缓存）、独立抽取学习路线/项目实战/面经真题/避坑经验、"
                    "做原文防编造校验并写入本地 SQLite experiences 表，仅返回一行精简摘要（不暴露全文给主上下文）。"
                ),
                parameters={
                    "type": "object",
                    "properties": {
                        "note": {
                            "type": "string",
                            "minLength": 1,
                            "description": "笔记短编号，必须来自 search_notes 返回结果（如 'n2'）",
                        },
                    },
                    "required": ["note"],
                    "additionalProperties": False,
                },
                handler=knowledge_tools.extract_and_save_experience,
                read_only=False,
                timeout=150.0,
                max_calls_per_task=config.budget.max_detail_calls,
                budget_group="detail",
            )
        )

        exp_filter_props: dict[str, Any] = {
            "category": {
                "type": "string",
                "enum": _VALID_EXP_CATEGORIES,
                "description": "经验分类：roadmap(学习路线) / project(项目实战) / interview(面试真题) / tips(避坑复盘)",
            },
            "topic": {"type": "string", "description": "主题关键词，如 'AI Agent'、'RAG'、'MCP'"},
            "keyword": {"type": "string", "description": "全文检索关键词（匹配标题、要点、技术栈与总结）"},
            "status": {
                "type": "string",
                "enum": _VALID_EXP_STATUSES,
                "description": "学习状态：unread / reading / starred / archived",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 200,
                "description": "最多返回条数，默认 20",
            },
        }

        query_exp_props = dict(exp_filter_props)
        query_exp_props["sort_by"] = {
            "type": "string",
            "enum": ["latest", "likes"],
            "description": "排序方式：latest(最新发布) 或 likes(点赞最高)",
        }

        registry.register(
            ToolSpec(
                name="query_experiences",
                description=(
                    "按分类、主题、关键词或学习状态查询本地 SQLite 经验知识库，返回结构化总结、要点、资源与来源链接。"
                ),
                parameters={
                    "type": "object",
                    "properties": query_exp_props,
                    "additionalProperties": False,
                },
                handler=knowledge_tools.query_experiences,
                read_only=True,
                timeout=15.0,
            )
        )

        registry.register(
            ToolSpec(
                name="mark_experience",
                description="更新本地经验知识库中某条干货的学习状态：unread / reading / starred / archived。",
                parameters={
                    "type": "object",
                    "properties": {
                        "exp_id": {"type": "integer", "minimum": 1, "description": "经验 ID（来自 query_experiences）"},
                        "status": {
                            "type": "string",
                            "enum": _VALID_EXP_STATUSES,
                            "description": "目标状态：unread / reading / starred / archived",
                        },
                    },
                    "required": ["exp_id", "status"],
                    "additionalProperties": False,
                },
                handler=knowledge_tools.mark_experience,
                read_only=False,
                timeout=15.0,
            )
        )

        export_exp_props = dict(exp_filter_props)
        export_exp_props["format"] = {
            "type": "string",
            "enum": ["markdown", "excel"],
            "description": "导出格式：markdown（默认，生成 .md 学习笔记）或 excel（生成 .xlsx）",
        }
        export_exp_props["path"] = {
            "type": "string",
            "description": "可选导出文件名或路径，不填则自动保存到 data/ 目录",
        }

        registry.register(
            ToolSpec(
                name="export_experiences",
                description="将本地经验知识库导出为结构化 Markdown 学习笔记 (.md) 或 Excel (.xlsx)，返回文件路径与条数。",
                parameters={
                    "type": "object",
                    "properties": export_exp_props,
                    "additionalProperties": False,
                },
                handler=knowledge_tools.export_experiences,
                read_only=False,
                timeout=30.0,
            )
        )

    return registry


class AgentSession:
    """多轮交互式对话会话：跨轮次保持上下文历史、Trace 文件、NoteCache 与累计费用统计。"""

    def __init__(self, runtime: AgentRuntime, *, session_id: str | None = None) -> None:
        if runtime.llm is None or runtime.registry is None:
            raise RuntimeError("AgentRuntime 尚未启动。")
        self.runtime = runtime
        self.system_prompt = render_agent_system_prompt(
            runtime.prompt_path,
            current_date=runtime.current_date,
        )
        self.ctx = ContextManager(self.system_prompt, session_id=session_id)
        self.trace = TraceLogger(self.ctx.session_id, traces_dir=runtime.traces_dir)
        self.turns = 0
        self.total_usage = TokenUsage()
        self.total_llm_calls = 0
        self.total_tool_calls: dict[str, int] = {}
        self.trace.log("session_start", mode="interactive", tools=runtime.registry.names())

    @property
    def session_id(self) -> str:
        return self.ctx.session_id

    @property
    def trace_path(self) -> Path:
        return self.trace.path

    @property
    def total_cost(self) -> CostEstimate:
        return estimate_cost(self.total_usage, self.runtime.config.pricing)

    async def ask(
        self,
        user_input: str,
        *,
        on_event: EventCallback | None = None,
        cancel_event: asyncio.Event | None = None,
        max_steps: int | None = None,
        max_search_calls: int | None = None,
        max_detail_calls: int | None = None,
        mode: str = "auto",
    ) -> AgentRunResult:
        """在当前会话中发送一条用户消息并执行完整 Agent Loop（每轮独立重置单轮预算）。"""
        assert self.runtime.llm is not None and self.runtime.registry is not None
        self.turns += 1
        budget = BudgetTracker.from_config(self.runtime.config.budget)
        if max_steps is not None:
            budget.max_steps = max_steps
        if max_search_calls is not None:
            budget.max_search_calls = max_search_calls
        if max_detail_calls is not None:
            budget.max_detail_calls = max_detail_calls
        executor = ExecutionEngine(self.runtime.registry, budget)
        extract_stats_fn = self.runtime.combined_extract_stats
        loop = AgentLoop(
            llm=self.runtime.llm,
            registry=self.runtime.registry,
            executor=executor,
            budget=budget,
            system_prompt=self.system_prompt,
            traces_dir=self.runtime.traces_dir,
            pricing=self.runtime.config.pricing,
            extract_stats_fn=extract_stats_fn,
        )
        prefix = MODE_PREFIX_HINTS.get(mode, "")
        effective_input = f"{prefix}{user_input}" if prefix else user_input
        result = await loop.run_turn(
            effective_input,
            ctx=self.ctx,
            trace=self.trace,
            on_event=on_event,
            turn_index=self.turns,
            cancel_event=cancel_event,
        )

        self.total_usage.add(result.total_usage)
        self.total_llm_calls += result.total_llm_calls
        for tool_name, cnt in result.tool_calls.items():
            self.total_tool_calls[tool_name] = self.total_tool_calls.get(tool_name, 0) + cnt
        return result

    def finish(self) -> None:
        self.trace.log(
            "session_end",
            mode="interactive",
            turns=self.turns,
            total_llm_calls=self.total_llm_calls,
            total_usage=self.total_usage.to_dict(),
            cost=self.total_cost.to_dict(),
            tool_calls=dict(self.total_tool_calls),
        )


class AgentRuntime:
    """Agent 运行时容器：负责启动预检、初始化 SQLite、装配 Harness 组件与安全退出。"""

    def __init__(
        self,
        config: AppConfig | None = None,
        *,
        adapter: XhsAdapter | None = None,
        llm: LLMClientProtocol | None = None,
        extractor_llm: LLMClientProtocol | None = None,
        store: JobStore | None = None,
        db_path: Path | str | None = None,
        export_dir: Path | str | None = None,
        traces_dir: Path | str | None = None,
        prompt_path: Path | None = None,
        current_date: str | None = None,
        enable_job_tools: bool = True,
    ) -> None:
        self.config = config or load_config()
        self._injected_adapter = adapter
        self._injected_llm = llm
        self._injected_extractor_llm = extractor_llm
        self._injected_store = store
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.export_dir = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
        self.traces_dir = Path(traces_dir) if traces_dir is not None else (ROOT_DIR / "traces")
        self.prompt_path = prompt_path
        self.current_date = current_date
        self.enable_job_tools = enable_job_tools

        self._mcp_client: MCPClient | None = None
        self.store: JobStore | None = None
        self.adapter: XhsAdapter | None = None
        self.llm: LLMClientProtocol | None = None
        self.job_tools: JobTools | None = None
        self.knowledge_tools: KnowledgeTools | None = None
        self.registry: ToolRegistry | None = None
        self.precheck_summary: dict[str, Any] = {}

    def combined_extract_stats(self) -> tuple[int, TokenUsage]:
        """合并岗位抽取 LLM 与经验抽取 LLM 的累计调用次数与 Token 用量。"""
        total_calls = 0
        total_usage = TokenUsage()
        if self.job_tools is not None:
            c, u = self.job_tools.extractor.snapshot_stats()
            total_calls += c
            total_usage.add(u)
        if self.knowledge_tools is not None:
            c, u = self.knowledge_tools.extractor.snapshot_stats()
            total_calls += c
            total_usage.add(u)
        return total_calls, total_usage

    async def start(self) -> None:
        """启动预检：初始化 SQLite → 连接 MCP → 校验白名单工具与 Schema → 检查登录态 → 装配注册表与 LLM。"""
        if self._injected_store is not None:
            self.store = self._injected_store
        elif self.enable_job_tools:
            self.store = JobStore(self.db_path)

        if self._injected_adapter is not None:
            self.adapter = self._injected_adapter
        else:
            self._mcp_client = MCPClient(self.config.mcp.url)
            try:
                await self._mcp_client.connect()
            except Exception as exc:
                await self.close()
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
                counter=self.store,
            )
            self.adapter = XhsAdapter(self._mcp_client, rate_limiter=limiter)

        try:
            validated = await self.adapter.validate_mcp_tools()
        except Exception as exc:
            await self.close()
            raise StartupPrecheckError(f"MCP 工具白名单/Schema 预检失败: {exc}") from exc

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

        if self._injected_llm is not None:
            self.llm = self._injected_llm
        else:
            self.llm = DeepSeekClient(self.config.llm)

        if self.enable_job_tools and self.store is not None:
            from xhs_agent.jobs.extract import JobExtractor
            from xhs_agent.jobs.tools import JobTools
            from xhs_agent.knowledge.extract import ExperienceExtractor
            from xhs_agent.knowledge.tools import KnowledgeTools

            ext_llm = self._injected_extractor_llm or self.llm
            extractor = JobExtractor(ext_llm, self.config.llm)
            self.job_tools = JobTools(
                adapter=self.adapter,
                store=self.store,
                extractor=extractor,
                export_dir=self.export_dir,
            )
            exp_extractor = ExperienceExtractor(ext_llm, self.config.llm)
            self.knowledge_tools = KnowledgeTools(
                adapter=self.adapter,
                store=self.store,
                extractor=exp_extractor,
                export_dir=self.export_dir,
            )
            self.registry = build_v1_registry(
                self.adapter,
                self.config,
                self.job_tools,
                self.knowledge_tools,
            )
        else:
            self.registry = build_phase3_registry(self.adapter, self.config)

        self.precheck_summary = {
            "mcp_url": self.config.mcp.url,
            "validated_mcp_tools": sorted(validated.keys()),
            "logged_in": True,
            "db_path": str(self.store.db_path) if self.store is not None else None,
            "registered_agent_tools": self.registry.names(),
        }

    async def close(self) -> None:
        if self._mcp_client is not None:
            await self._mcp_client.close()
            self._mcp_client = None
        if self._injected_llm is None and isinstance(self.llm, DeepSeekClient):
            await self.llm.close()
        if self._injected_store is None and self.store is not None:
            self.store.close()
            self.store = None

    async def __aenter__(self) -> AgentRuntime:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        await self.close()

    def new_session(self, *, session_id: str | None = None) -> AgentSession:
        """创建一个支持多轮对话的 AgentSession。"""
        return AgentSession(self, session_id=session_id)

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
        extract_stats_fn = self.combined_extract_stats
        loop = AgentLoop(
            llm=self.llm,
            registry=self.registry,
            executor=executor,
            budget=budget,
            system_prompt=system_prompt,
            traces_dir=self.traces_dir,
            pricing=self.config.pricing,
            extract_stats_fn=extract_stats_fn,
        )
        return await loop.run(task, session_id=session_id, on_event=on_event)
