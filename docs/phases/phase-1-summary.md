# 阶段 1 完成总结：MCP 接入与数据验证

> 对应开发方案：[development_plan.md](../development_plan.md#阶段-1mcp-接入与数据验证)  
> Git Tag：`phase-1`（提交 `b43de03`、`6e33ecd`）

---

## 1. 阶段目标与验收对照

| 验收标准 | 状态 | 实测结果与说明 |
|---|---|---|
| 掌握 `search_feeds` / `get_feed_detail` 参数与返回结构 | ✅ 已完成 | 通过 `probe_tools.py` 获取 `xiaohongshu-mcp` v2.7.4 全部 18 个工具 Schema 并保存为 `tests/fixtures/tools.json` |
| `tests/fixtures/` 至少 3 份搜索、5 份详情样本 | ✅ 超额完成 | 录制 3 份搜索样本（每份 20 条）和 **20 份**真实详情样本（`detail_01`–`detail_20`） |
| 完成 20 篇人工评估并决定图片识别优先级 | ✅ 已完成 | 输出 [evals/manual_review.md](../../evals/manual_review.md) 及 20 份结构化标注样本 `evals/samples/sample_01`–`sample_20` |
| 跑通无 LLM 的基础数据链路 | ✅ 已完成 | `scripts/pipeline_no_llm.py` 实现“搜索 → 详情 → 标题/正文打印”完整串联 |

---

## 2. 关键数据发现与决策

1. **工具清单与安全边界（18 个工具）**：
   - 白名单只读工具：`check_login_status`、`search_feeds`、`get_feed_detail`。
   - 必须禁止的写/破坏性工具（9 个）：`delete_cookies`、`publish_content`、`publish_with_video`、`post_comment_to_feed`、`reply_comment_in_feed`、`reply_notification`、`like_feed`、`like_notification`、`favorite_feed`。
2. **`feed_id` 隐含精确发布时间**：
   - `search_feeds` 响应本身不返回时间字段，但实测 20/20 篇笔记的 `int(feed_id[:8], 16)` **精确等于** `get_feed_detail` 中的 `data.note.time // 1000`（0 秒误差），使得搜索结果无需拉详情即可获得 `published_at`。
3. **20 篇人工评估统计**（详情见 [evals/manual_review.md](../../evals/manual_review.md)）：
   - 招聘帖占比：**15 / 20（75.0%）**；非招聘帖 5 / 20（4 篇日常 Vlog + 1 篇面经反例）。
   - 多岗位汇总帖占比：**8 / 15（53.3%）**，确认抽取结构必须为 `list[Internship]`；同时存在跨账号重复投递同一公司岗位（如小米 3 篇、科大讯飞 2 篇），验证岗位级去重的必要性。
   - **关键决策点（图片识别优先级）**：15 篇招聘帖中 **0 篇（0.0%）** 为纯图片无正文，**13 篇（86.7%）** 正文信息完整，仅 **2 篇（13.3%）** 属于部分要求在图中的混合帖。远低于 50% 提前阈值，因此**不提前图片识别**，阶段 4 仅实现 `needs_image` 标记，图片识别留待后续扩展。

---

## 3. 产出文件清单

| 文件/目录 | 说明 |
|---|---|
| [scripts/_mcp_helper.py](../../scripts/_mcp_helper.py) | 探测脚本公共工具（MCP 连接、响应解析、时间戳解码、数据摘要提取） |
| [scripts/probe_tools.py](../../scripts/probe_tools.py) / [probe_login.py](../../scripts/probe_login.py) / [probe_search.py](../../scripts/probe_search.py) / [probe_detail.py](../../scripts/probe_detail.py) | MCP 工具 Schema、登录态、搜索与详情探测脚本 |
| [scripts/pipeline_no_llm.py](../../scripts/pipeline_no_llm.py) | 无 LLM 数据链路验证脚本 |
| `tests/fixtures/` | `tools.json`、`login_status.json`、3 份 `search_*.json`、20 份 `detail_*.json` |
| [evals/manual_review.md](../../evals/manual_review.md) | 20 篇真实笔记人工评估表与图片识别决策记录 |
| `evals/samples/sample_01_*.json` – `sample_20_*.json` | 20 份人工标注的标准抽取评估集 |
| [tests/test_phase1_helpers.py](../../tests/test_phase1_helpers.py) | 6 项数据解析、时间戳解码与样本完整性离线测试 |

---

## 4. 测试结果

- `tests/test_phase1_helpers.py`：**6/6 通过**（校验 helper 解析、`feed_id` 时间戳解码及 fixtures/eval samples 完整性）。
