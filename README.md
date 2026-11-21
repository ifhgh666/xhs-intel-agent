# 实习信息智能检索 Agent

基于大模型的实习信息检索 Agent：用户用自然语言描述需求，Agent 自主拆分关键词，通过 MCP 搜索小红书招聘笔记，抽取结构化岗位信息并做原文防编造校验，去重后存入本地 SQLite 数据库，支持多轮追问、岗位状态管理与导出 Excel。

## 文档

- [设计文档](docs/design.md)：架构、模块职责、工具设计、数据模型
- [开发方案](docs/development_plan.md)：分阶段任务、验收标准、技术选型
- [前后端设计方案](docs/web_fullstack_plan.md)：FastAPI + SSE 后端与 React/Vite Web 工作台设计方案
- [阶段 1 人工评估表](evals/manual_review.md)：20 篇真实笔记评估与图片识别优先级结论

## 技术栈

- Python 3.11+ / `uv`
- DeepSeek（`deepseek-chat`，Function Calling + JSON 输出模式 + 上下文缓存）
- MCP Python SDK + [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)（Docker 运行）
- Pydantic / SQLite / openpyxl / Rich

## 快速使用

```powershell
# 1. 启动 xiaohongshu-mcp（Docker）
docker compose up -d

# 2. 检查登录状态（未登录时可加 --save-qrcode 拉取二维码扫码）
uv run python scripts/probe_login.py

# 3. 单轮搜索、抽取入库并导出 Excel
uv run python -m xhs_agent "帮我搜索广州的 AI Agent、RAG 实习岗位，优先找适合 2028 届本科生的岗位，入库后导出 Excel。"

# 4. 多轮交互模式（支持追问筛选、mark_job 标记感兴趣/已投递、随时导出 Excel）
uv run python -m xhs_agent -i

# 5. 运行全量离线测试（不依赖真实小红书与 LLM）
uv run pytest -v

# 6. 运行 20 篇真实样本抽取评估
uv run python evals/run_eval.py
```

## 当前进度

- [x] 阶段 0：环境准备（Docker MCP 已启动并登录、Python/uv 环境就绪、DeepSeek API 验证通过）
- [x] 阶段 1：MCP 接入与数据验证（`phase-1`：18 个工具 Schema、3 份搜索样本、20 份详情样本、20 篇评估与标注集）
- [x] 阶段 2：小红书适配层（`phase-2`：MCPClient、RateLimiter、NoteCache、XhsAdapter，搜索裁剪 -85.8%）
- [x] 阶段 3：Harness 核心（`phase-3`：ToolRegistry、Trace、ExecutionEngine、ContextManager、DeepSeekClient、AgentLoop、Runtime、CLI）
- [x] 阶段 4：岗位抽取与存储（`phase-4`：SQLite notes/jobs/daily_usage、JSON 抽取 + 原文证据校验、双层去重、Excel 导出，20 篇评估集核心字段准确率 97.8% / 编造率 0.0%）
- [x] 阶段 5：工程化与增强（`phase-5`：全链路主/抽取 Token 与人民币费用统计、`mark_job` 状态管理、多轮交互式 CLI `-i`、日志分级 `-v`、48 项离线单测与端到端模拟测试全通过）

## 使用说明

仅供个人学习使用。请保持低频访问，遵守平台规则；出现验证或账号异常提示时应立即停止运行。
