# 阶段 4 完成总结：岗位抽取与存储

> 对应开发方案：[development_plan.md](../development_plan.md#阶段-4岗位抽取与存储)  
> Git Tag：`phase-4`（提交 `87febcd`）

---

## 1. 阶段目标与验收对照

| 验收标准 | 状态 | 实测结果与说明 |
|---|---|---|
| 示例需求端到端跑通，产出 SQLite 数据与可打开的 Excel（含来源链接列） | ✅ 已完成 | 真实端到端产物已生成：`data/xhs_jobs.db` 与 `data/gz_ai_agent_rag_interns.xlsx`（来源链接列设为可点击超链接） |
| 重复运行不会重复抓取已处理帖子，也不会产生重复岗位 | ✅ 已完成 | 双层去重：笔记级 `notes.processed_at` 跳过 + 岗位级 `dedup_key`（`normalize(company)\|normalize(position)\|normalize(city)`）合并保留最早来源与 `extra_sources` |
| 评估集编造率接近 0，核心字段准确率 ≥ 85% | ✅ 远超目标 | 20 篇真实样本实测：**核心字段准确率 97.8%**，**编造率 0.0%**，**漏抽率 0.0%**，**非招聘误抽率 0.0%** |
| 主 LLM 上下文中看不到笔记全文 | ✅ 已完成 | `extract_and_save_jobs(note)` 内部独立调用抽取 LLM，仅向主 LLM 返回一行精简摘要（新增/重复/无岗位/需看图） |

---

## 2. 产出文件清单

| 文件 | 核心职责 |
|---|---|
| [xhs_agent/jobs/models.py](../../xhs_agent/jobs/models.py) | `Internship`、`ExtractionPayload`、`JobRecord`（来源字段强制由代码填充）与 60 天过期 / `deadline` 解析函数 |
| [xhs_agent/jobs/store.py](../../xhs_agent/jobs/store.py) | `JobStore`：SQLite `notes`、`jobs`、`daily_usage` 三表管理、跨账号重复岗位合并及 `RateLimiter` 每日计数持久化 |
| [xhs_agent/jobs/dedup.py](../../xhs_agent/jobs/dedup.py) | `verify_evidence`（去空白/标点校验证据是否在原文出现）与 `make_dedup_key` 规范化去重键生成 |
| [xhs_agent/jobs/extract.py](../../xhs_agent/jobs/extract.py) | `JobExtractor`：DeepSeek JSON 模式（`temperature=0.0`）、Pydantic 校验失败自动重试一次、原文证据过滤与 `needs_image` 启发式判别 |
| [xhs_agent/jobs/tools.py](../../xhs_agent/jobs/tools.py) | `JobTools` 业务工具集：`extract_and_save_jobs`、`query_jobs`、`export_jobs`（`openpyxl` 格式化导出） |
| [xhs_agent/prompts/extract.md](../../xhs_agent/prompts/extract.md) | 结构化岗位抽取专用 Prompt |
| [evals/run_eval.py](../../evals/run_eval.py) | 基于阶段 1 的 20 篇标注样本自动计算抽取准确率、漏抽率、误抽率与编造率 |
| [tests/test_phase4_jobs.py](../../tests/test_phase4_jobs.py) | 7 项模型校验、证据防编造、去重合并、时效判断与上下文隔离测试 |

---

## 3. 20 篇真实样本评估实测指标（[data/eval_report_latest.json](../../data/eval_report_latest.json)）

| 评估指标 | 实测结果 | 验收标准 |
|---|---:|---|
| 招聘帖判别准确率（`is_job_accuracy`） | **100.0%** | - |
| 漏抽率（`miss_rate`） | **0.0%** | 越低越好 |
| 非招聘误抽率（`false_positive_rate`） | **0.0%** | 越低越好 |
| 核心字段综合准确率（`core_field_accuracy`） | **97.8%**（公司 93.3% / 岗位 100.0% / 城市 100.0%） | **≥ 85%** |
| 编造率（`fabrication_rate`） | **0.0%**（0 条未通过校验） | **接近 0** |
| `needs_image` 判别准确率 | **100.0%** | - |
| 累计抽取岗位 / Token 消耗 | 25 条 / 25,605 tokens（缓存命中 9,728） | - |

---

## 4. 单元测试结果

- `tests/test_phase4_jobs.py`：**7/7 通过**。
