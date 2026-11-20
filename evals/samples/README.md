# 评估集样本说明（`evals/samples/`）

本目录存放阶段 1 整理的人工标注样本（10–20 篇），供阶段 4 的 `evals/run_eval.py` 测量岗位抽取准确率、编造率和漏抽率。

## 标注规范

1. 每个样本保存为 `sample_XX_<feed_id>.json`（可参考 `_template.json`）。
2. 字段与 `docs/design.md` 第 10.1 节 `Internship` 模型保持一致：
   - 原文未提及的字段填 `null`（绝不猜测或补全）。
   - `evidence` 必须是从 `note.desc` 或 `note.title` 中原样摘录的片段。
   - 一篇笔记包含多个岗位时，`expected.jobs` 为多元素列表；非招聘笔记则为 `[]`。
   - 正文信息不足、主要 JD 在图片中的笔记，将 `expected.needs_image` 设为 `true`。
3. 完成人工核对后，将顶层 `annotated` 改为 `true`。
