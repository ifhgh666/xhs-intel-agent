# 实习信息智能检索 Agent

基于大模型的实习信息检索 Agent：用户用自然语言描述需求，Agent 自主拆分关键词，通过 MCP 搜索小红书招聘笔记，抽取结构化岗位信息，去重后存入本地数据库，支持查询和导出 Excel。

## 文档

- [设计文档](docs/design.md)：架构、模块职责、工具设计、数据模型
- [开发方案](docs/development_plan.md)：分阶段任务、验收标准、技术选型

## 技术栈

- Python 3.11+ / uv
- DeepSeek（`deepseek-chat`，Function Calling + JSON 输出模式）
- MCP Python SDK + [xiaohongshu-mcp](https://github.com/xpzouying/xiaohongshu-mcp)
- Pydantic / SQLite / openpyxl

## 当前进度

- [ ] 阶段 0：环境准备
- [ ] 阶段 1：MCP 接入与数据验证
- [ ] 阶段 2：小红书适配层
- [ ] 阶段 3：Harness 核心
- [ ] 阶段 4：岗位抽取与存储
- [ ] 阶段 5：工程化与增强

## 使用说明

仅供个人学习使用。请保持低频访问，遵守平台规则；出现验证或账号异常提示时应立即停止运行。
