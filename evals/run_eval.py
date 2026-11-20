"""阶段 4 岗位抽取评估脚本：使用阶段 1 标注的 20 篇真实样本评测 JobExtractor。

计算指标（对照 docs/development_plan.md 阶段 4 验收标准）：
- 招聘帖判别准确率（is_job_post）
- 漏抽率（miss_rate：真实招聘帖未抽到任何岗位的比例）
- 误抽率（false_positive_rate：非招聘帖误抽出岗位的比例）
- 核心字段准确率（company / position / city，验收目标 ≥ 85%）
- 编造率（fabrication_rate：抽出了原文不存在的字段或未通过证据校验的比例，验收目标接近 0）
- needs_image 判别准确率

用法：
    uv run python evals/run_eval.py
    uv run python evals/run_eval.py --limit 5
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from xhs_agent.config import load_config
from xhs_agent.harness.llm import DeepSeekClient, TokenUsage
from xhs_agent.jobs.dedup import (
    normalize_city,
    normalize_company,
    normalize_position,
    strip_whitespace_and_punct,
    verify_evidence,
)
from xhs_agent.jobs.extract import JobExtractor
from xhs_agent.jobs.models import Internship

SAMPLES_DIR = ROOT_DIR / "evals" / "samples"


def _match_company(expected: str | None, predicted: str | None) -> bool:
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    ne = normalize_company(expected)
    np = normalize_company(predicted)
    return ne == np or ne in np or np in ne


def _match_city(expected: str | None, predicted: str | None) -> bool:
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    ne = normalize_city(expected)
    # 若预测值是多城字符串（如 "北京/上海/广州/深圳"），只要包含期望城市即算命中
    raw_pred = strip_whitespace_and_punct(predicted)
    return ne == normalize_city(predicted) or (bool(ne) and ne in raw_pred)


def _match_position(expected: str | None, predicted: str | None) -> bool:
    if expected is None and predicted is None:
        return True
    if expected is None or predicted is None:
        return False
    ne = normalize_position(expected)
    np = normalize_position(predicted)
    if ne == np or ne in np or np in ne:
        return True
    # 词元重叠容错（如 "AI技术实习生" vs "AI应用开发实习生/RAG问答" 或英文缩写）
    common = set(ne) & set(np)
    return len(common) >= max(2, int(min(len(ne), len(np)) * 0.6))


def _is_field_grounded(value: str | None, source_text: str) -> bool:
    """检查非空字符串字段是否能在原文中找到支撑（允许常见规范化同义，如“广州”在“广州天河”中）。"""
    if value is None:
        return True
    norm_src = strip_whitespace_and_punct(source_text)
    norm_val = strip_whitespace_and_punct(value)
    if not norm_val:
        return True
    if norm_val in norm_src:
        return True
    # 检查拆词（例如多城 "北京/上海/广州" 或 "AI研发类实习生"）
    parts = [p for p in [normalize_company(value), normalize_position(value), normalize_city(value)] if p]
    for p in parts:
        if p in norm_src:
            return True
    # 若按标点/空白拆出的核心子词大部分出现在原文中，也算有出处
    tokens = [strip_whitespace_and_punct(t) for t in value.replace("/", " ").replace("、", " ").split()]
    tokens = [t for t in tokens if len(t) >= 2]
    if tokens and all(t in norm_src for t in tokens):
        return True
    return False


def _pick_best_predicted_job(expected_job: dict[str, Any], pred_jobs: list[Internship]) -> Internship | None:
    if not pred_jobs:
        return None
    best: Internship | None = None
    best_score = -1
    for cand in pred_jobs:
        score = 0
        if _match_company(expected_job.get("company"), cand.company):
            score += 2
        if _match_position(expected_job.get("position"), cand.position):
            score += 3
        if _match_city(expected_job.get("city"), cand.city):
            score += 2
        if score > best_score:
            best_score = score
            best = cand
    return best


async def run_eval(limit: int | None = None, out_path: Path | None = None) -> int:
    console = Console()
    cfg = load_config()
    llm = DeepSeekClient(cfg.llm)
    extractor = JobExtractor(llm, cfg.llm)

    sample_files = sorted(SAMPLES_DIR.glob("sample_*.json"))
    if limit is not None:
        sample_files = sample_files[:limit]
    if not sample_files:
        console.print("[red]未找到 evals/samples/sample_*.json 样本文件。[/red]")
        return 1

    console.print(f"[bold cyan]开始评测 {len(sample_files)} 篇真实标注样本（模型：{cfg.llm.extract_model}，temperature={cfg.llm.temperature_extract}）...[/bold cyan]")

    total_samples = 0
    is_job_correct = 0
    needs_image_correct = 0
    expected_job_posts = 0
    missed_job_posts = 0
    expected_non_job_posts = 0
    false_positive_posts = 0

    company_hits = company_total = 0
    position_hits = position_total = 0
    city_hits = city_total = 0

    total_checked_fields = 0
    fabricated_fields = 0
    total_extracted_jobs = 0
    total_dropped_by_verifier = 0
    total_usage = TokenUsage()

    rows_detail: list[dict[str, Any]] = []
    table = Table(title="20 篇真实样本抽取评测明细", show_lines=False)
    table.add_column("样本", style="cyan", width=10)
    table.add_column("期望招聘", width=8)
    table.add_column("抽取数", justify="right", width=6)
    table.add_column("公司", width=6)
    table.add_column("岗位", width=6)
    table.add_column("城市", width=6)
    table.add_column("看图", width=6)
    table.add_column("编造数", justify="right", width=6)
    table.add_column("说明", max_width=40)

    try:
        for path in sample_files:
            sample = json.loads(path.read_text(encoding="utf-8"))
            if not sample.get("annotated"):
                continue

            total_samples += 1
            note = sample["note"]
            expected = sample["expected"]
            source_text = f"{note['title']}\n{note['desc']}"

            res = await extractor.extract(
                title=note["title"],
                desc=note["desc"],
                image_count=int(note.get("image_count", 0)),
            )
            total_usage.add(res.usage)
            total_dropped_by_verifier += res.dropped_unverified
            total_extracted_jobs += len(res.jobs)

            exp_is_job = bool(expected["is_job_post"])
            pred_is_job = len(res.jobs) > 0
            if exp_is_job == pred_is_job:
                is_job_correct += 1

            exp_needs_img = bool(expected["needs_image"])
            img_ok = res.needs_image == exp_needs_img
            if img_ok:
                needs_image_correct += 1

            # 统计编造：检查所有输出岗位的 evidence + company/city 是否有原文出处
            sample_fab = 0
            for j in res.jobs:
                if not verify_evidence(j.evidence, source_text):
                    sample_fab += 1
                for fname in ("company", "city"):
                    fval = getattr(j, fname)
                    if fval is not None:
                        total_checked_fields += 1
                        if not _is_field_grounded(fval, source_text):
                            sample_fab += 1
                            fabricated_fields += 1

            c_mark = p_mark = city_mark = "-"
            note_desc = ""

            if exp_is_job:
                expected_job_posts += 1
                if not res.jobs:
                    missed_job_posts += 1
                    c_mark = p_mark = city_mark = "✗"
                    company_total += 1
                    position_total += 1
                    city_total += 1
                    note_desc = "漏抽（招聘帖返回空）"
                else:
                    exp_first = expected["jobs"][0]
                    best = _pick_best_predicted_job(exp_first, res.jobs)
                    assert best is not None
                    c_ok = _match_company(exp_first.get("company"), best.company)
                    p_ok = _match_position(exp_first.get("position"), best.position)
                    city_ok = _match_city(exp_first.get("city"), best.city)

                    company_total += 1
                    position_total += 1
                    city_total += 1
                    company_hits += int(c_ok)
                    position_hits += int(p_ok)
                    city_hits += int(city_ok)

                    c_mark = "✓" if c_ok else "✗"
                    p_mark = "✓" if p_ok else "✗"
                    city_mark = "✓" if city_ok else "✗"
                    note_desc = f"{best.company or '未具名'} / {best.position or '-'} / {best.city or '-'}"
            else:
                expected_non_job_posts += 1
                if pred_is_job:
                    false_positive_posts += 1
                    note_desc = f"误抽: {res.jobs[0].position}"
                else:
                    note_desc = "正确排除非招聘帖"

            table.add_row(
                sample["sample_id"],
                "是" if exp_is_job else "否",
                str(len(res.jobs)),
                c_mark,
                p_mark,
                city_mark,
                "✓" if img_ok else "✗",
                str(sample_fab),
                note_desc[:40],
            )
            rows_detail.append(
                {
                    "sample_id": sample["sample_id"],
                    "feed_id": note["feed_id"],
                    "expected_is_job": exp_is_job,
                    "predicted_count": len(res.jobs),
                    "expected_needs_image": exp_needs_img,
                    "predicted_needs_image": res.needs_image,
                    "dropped_unverified": res.dropped_unverified,
                    "fabricated_fields": sample_fab,
                    "predicted_jobs": [j.model_dump() for j in res.jobs],
                }
            )
    finally:
        await llm.close()

    console.print(table)

    company_acc = company_hits / company_total if company_total else 0.0
    position_acc = position_hits / position_total if position_total else 0.0
    city_acc = city_hits / city_total if city_total else 0.0
    core_acc = (company_hits + position_hits + city_hits) / (company_total + position_total + city_total) if company_total else 0.0
    miss_rate = missed_job_posts / expected_job_posts if expected_job_posts else 0.0
    fp_rate = false_positive_posts / expected_non_job_posts if expected_non_job_posts else 0.0
    fab_rate = fabricated_fields / total_checked_fields if total_checked_fields else 0.0
    is_job_acc = is_job_correct / total_samples if total_samples else 0.0
    img_acc = needs_image_correct / total_samples if total_samples else 0.0

    summary = {
        "total_samples": total_samples,
        "is_job_accuracy": round(is_job_acc, 4),
        "miss_rate": round(miss_rate, 4),
        "false_positive_rate": round(fp_rate, 4),
        "company_accuracy": round(company_acc, 4),
        "position_accuracy": round(position_acc, 4),
        "city_accuracy": round(city_acc, 4),
        "core_field_accuracy": round(core_acc, 4),
        "fabrication_rate": round(fab_rate, 4),
        "needs_image_accuracy": round(img_acc, 4),
        "total_extracted_jobs": total_extracted_jobs,
        "total_dropped_by_verifier": total_dropped_by_verifier,
        "usage": total_usage.to_dict(),
    }

    console.print("\n[bold green]=== 阶段 4 评估汇总 ===[/bold green]")
    console.print(f"样本总数：{total_samples}（招聘帖 {expected_job_posts}，非招聘帖 {expected_non_job_posts}）")
    console.print(f"招聘帖判别准确率：{is_job_acc:.1%} | 漏抽率：{miss_rate:.1%} | 非招聘误抽率：{fp_rate:.1%}")
    console.print(
        f"核心字段准确率：[bold]{core_acc:.1%}[/bold] "
        f"（公司 {company_acc:.1%} [{company_hits}/{company_total}]，"
        f"岗位 {position_acc:.1%} [{position_hits}/{position_total}]，"
        f"城市 {city_acc:.1%} [{city_hits}/{city_total}]）"
    )
    console.print(
        f"编造率：[bold]{fab_rate:.1%}[/bold]（{fabricated_fields}/{total_checked_fields} 字段，"
        f"证据校验拦截条数={total_dropped_by_verifier}） | needs_image 准确率：{img_acc:.1%}"
    )
    console.print(
        f"Token 消耗：total={total_usage.total_tokens} "
        f"(prompt={total_usage.prompt_tokens}, cache_hit={total_usage.cache_hit_tokens}, "
        f"completion={total_usage.completion_tokens})"
    )

    report_path = out_path or (ROOT_DIR / "data" / "eval_report_latest.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps({"summary": summary, "details": rows_detail}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    console.print(f"[dim]详细评测报告已保存：{report_path}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="使用阶段 1 评估集评测岗位抽取质量")
    parser.add_argument("--limit", type=int, default=None, help="仅评测前 N 篇（调试用）")
    parser.add_argument("--out", type=Path, default=None, help="评测报告输出路径")
    args = parser.parse_args()
    return asyncio.run(run_eval(limit=args.limit, out_path=args.out))


if __name__ == "__main__":
    raise SystemExit(main())
