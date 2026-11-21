# 阶段 2 完成总结：小红书适配层

> 对应开发方案：[development_plan.md](../development_plan.md#阶段-2小红书适配层)  
> Git Tag：`phase-2`（提交 `5b16df0`）

---

## 1. 阶段目标与验收对照

| 验收标准 | 状态 | 实测结果与说明 |
|---|---|---|
| 连续调用 `search_notes` → `get_note_detail("n1")` 无需手动传 `xsec_token` | ✅ 已完成 | `NoteCache` 自动映射短编号 `n1/n2/...` 到 `feed_id` 与 `xsec_token` |
| 同一笔记调用两次 `get_note_detail`，只请求一次小红书 | ✅ 已完成 | 第 2 次调用返回 `from_cache=True`，`get_feed_detail` MCP 调用次数保持为 1 |
| 两次 MCP 调用间隔不低于配置值 | ✅ 已完成 | `RateLimiter` 采用全局 `asyncio.Lock` 串行锁 + `min_interval_sec + random(0, jitter_sec)` + 每日上限检查 |
| 精简后的搜索结果明显小于原始结果 | ✅ 已完成 | 3 份真实搜索样本裁剪比例分别为 **-85.6% / -86.0% / -85.9%**；详情裁剪 **-79.8%** |
| 全部测试基于 fixtures 离线通过 | ✅ 已完成 | `tests/test_phase2_xhs.py` 23 项测试全部离线通过，不依赖真实小红书 |

---

## 2. 产出文件清单

| 文件 | 核心职责 |
|---|---|
| [xhs_agent/config.py](../../xhs_agent/config.py) | 加载 `config.toml`（`LLMConfig`、`MCPConfig`、`RateLimitConfig`、`BudgetConfig`、`PricingConfig`） |
| [xhs_agent/xhs/mcp_client.py](../../xhs_agent/xhs/mcp_client.py) | `MCPClient`：基于 `streamablehttp_client` 的长连接、超时控制、断线自动重连一次与工具调用封装 |
| [xhs_agent/xhs/rate_limiter.py](../../xhs_agent/xhs/rate_limiter.py) | `RateLimiter`：串行锁、最小间隔 + 随机抖动、每日计数与限额保护（预留 `UsageCounter` 协议供阶段 4 接 SQLite） |
| [xhs_agent/xhs/cache.py](../../xhs_agent/xhs/cache.py) | `NoteCache`：短编号分配、跨搜索同 `feed_id` 复用、详情缓存（阶段 4 已无缝升级至 SQLite 持久化） |
| [xhs_agent/xhs/adapter.py](../../xhs_agent/xhs/adapter.py) | `XhsAdapter`：强制 `ALLOWED_MCP_TOOLS` 白名单、`validate_mcp_tools()`、`search_notes()`、`get_note_detail()` 与字段裁剪 |
| [scripts/verify_phase2.py](../../scripts/verify_phase2.py) | 阶段 2 离线裁剪对比 + 可选 `--live` 真机冒烟脚本 |
| [tests/test_phase2_xhs.py](../../tests/test_phase2_xhs.py) | 23 项适配层单元测试（含 9 个写工具拦截、重连、限流串行、裁剪体积对比） |

---

## 3. 离线验证实测数据（`uv run python scripts/verify_phase2.py`）

| 搜索关键词 | 原始字节 | 裁剪后字节 | 压缩比例 | 返回条数 |
|---|---:|---:|---:|---:|
| `广州 AI实习` | 25,822 B | 3,721 B | **-85.6%** | 20 |
| `RAG 实习` | 25,558 B | 3,578 B | **-86.0%** | 20 |
| `Agent 实习 2028届` | 25,685 B | 3,617 B | **-85.9%** | 20 |

- 详情裁剪对比（`n1`）：`7,578 B` → `1,530 B`（**-79.8%**）。
- 缓存命中验证：第 1 次 `from_cache=False`，第 2 次 `from_cache=True`，实际 `get_feed_detail` 调用次数为 `1`。
