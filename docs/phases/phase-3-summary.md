# 阶段 3 完成总结：Harness 核心

> 对应开发方案：[development_plan.md](../development_plan.md#阶段-3harness-核心)  
> Git Tag：`phase-3`（提交 `b88ec54`）

---

## 1. 阶段目标与验收对照

| 验收标准 | 状态 | 实测结果与说明 |
|---|---|---|
| Agent 自动拆出多个关键词分别搜索并挑选招聘帖预览 | ✅ 已完成 | 通过 `AgentLoop` + `agent_system.md` 引导拆词（城市 + 方向 + 招/招聘/内推）并过滤 Vlog/面经 |
| 人为制造错误（参数错误/未知短 id/MCP 断线）不崩溃且 LLM 能自我调整 | ✅ 已完成 | `ExecutionEngine` 统一拦截并返回 `{"ok": False, "error": ..., "retryable": ...}` |
| 达到步数或调用上限时优雅收尾 | ✅ 已完成 | 到达 `max_steps` / 搜索上限 / 连续错误阈值时注入 `BUDGET_WRAP_UP_PROMPT` 并要求基于已有信息生成总结 |
| `traces/` 完整回看运行过程 | ✅ 已完成 | `TraceLogger` 为每次任务生成 `traces/{session_id}.jsonl`，记录完整请求、响应、工具调用与异常事件 |

---

## 2. 产出文件清单

| 文件 | 核心职责 |
|---|---|
| [xhs_agent/harness/registry.py](../../xhs_agent/harness/registry.py) | `ToolSpec` 与 `ToolRegistry`，统一输出 OpenAI Function Calling `schemas()` |
| [xhs_agent/harness/trace.py](../../xhs_agent/harness/trace.py) | `TraceLogger`，逐行写入结构化 JSONL 轨迹日志 |
| [xhs_agent/harness/executor.py](../../xhs_agent/harness/executor.py) | `ExecutionEngine` 与 `BudgetTracker`：工具存在性 → JSON 解析与 `jsonschema` 校验 → 次数上限 → `asyncio.wait_for` 超时控制 → 异常结构化封装 |
| [xhs_agent/harness/context.py](../../xhs_agent/harness/context.py) | `ContextManager`：管理 `system/user/assistant/tool` 消息，超长裁剪时保证 `tool_calls` 与 `tool` 响应成对处理，维护前缀稳定性以利 DeepSeek 上下文缓存 |
| [xhs_agent/harness/llm.py](../../xhs_agent/harness/llm.py) | `DeepSeekClient`（基于 `openai.AsyncOpenAI`）：统计 `prompt_tokens` / `completion_tokens` / `cache_hit_tokens` / `cache_miss_tokens`，支持 429/网络异常退避重试 |
| [xhs_agent/harness/loop.py](../../xhs_agent/harness/loop.py) | `AgentLoop`：主推理循环、工具执行闭环、预算耗尽收尾逻辑 |
| [xhs_agent/harness/runtime.py](../../xhs_agent/harness/runtime.py) | `AgentRuntime`：启动预检（MCP 连通 → 白名单工具 Schema → 小红书登录态）、工具注册与资源生命周期管理 |
| [xhs_agent/prompts/agent_system.md](../../xhs_agent/prompts/agent_system.md) | 系统提示词模板（注入当前日期、检索策略、时效偏好与防编造约束） |
| [xhs_agent/main.py](../../xhs_agent/main.py) / [\_\_main\_\_.py](../../xhs_agent/__main__.py) | `python -m xhs_agent` CLI 入口（Rich 终端输出） |
| [tests/test_phase3_harness.py](../../tests/test_phase3_harness.py) | 7 项 Harness 核心单测与模拟端到端故障恢复测试 |

---

## 3. 测试与实测验证

- `tests/test_phase3_harness.py`：**7/7 通过**，覆盖 `ToolRegistry` Schema、JSONL Trace 写入、`ContextManager` 成对裁剪协议、`ExecutionEngine` 异常与超时拦截、预算上限优雅收尾、未登录阻塞预检，以及多关键词搜索 + 故障恢复模拟链路。
- 真实运行时轨迹样本已记录在 `traces/s_b5c4580ca6.jsonl`。
