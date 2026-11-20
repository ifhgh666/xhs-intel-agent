"""阶段 1 任务 5：不用 LLM 的端到端数据链路验证脚本。

固定关键词 → search_feeds → 取前 N 篇 get_feed_detail → 打印标题、作者、时间、正文与图片数。
支持：
1. 在线模式（默认）：直连 xiaohongshu-mcp 走完搜索 → 详情链路；
2. 离线回放模式（--from-fixtures）：直接读取 tests/fixtures/detail_*.json 打印并做统计；
3. 生成人工评估草稿（--export-review）：将已采集的笔记自动写入 evals/manual_review.md 表格
   并在 evals/samples/ 生成待标注 JSON 草稿，极大加速阶段 1 任务 6、7。

用法：
    uv run python scripts/pipeline_no_llm.py --keyword "广州 AI实习" --top 5
    uv run python scripts/pipeline_no_llm.py --from-fixtures
    uv run python scripts/pipeline_no_llm.py --from-fixtures --export-review
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_helper import (
    EVALS_DIR,
    FIXTURES_DIR,
    extract_detail_summary,
    extract_feed_items,
    extract_feed_summary,
    extract_tool_result,
    format_timestamp,
    get_mcp_url,
    load_json,
    open_mcp_session,
    polite_sleep,
    save_json,
    utc_now_iso,
)


def _guess_needs_image(desc: str, image_count: int) -> bool:
    if image_count <= 0:
        return False
    hints = ("见图", "详见图", "看图", "图里", "图片里", "如图", "长图")
    return len(desc.strip()) < 60 or any(h in desc for h in hints)


def _build_note_url(feed_id: str | None) -> str:
    return f"https://www.xiaohongshu.com/explore/{feed_id}" if feed_id else ""


def _render_notes(console: Console, notes: list[dict[str, Any]]) -> None:
    if not notes:
        console.print("[yellow]没有可展示的笔记数据。[/yellow]")
        return

    for idx, note in enumerate(notes, start=1):
        fid = note.get("feed_id") or "-"
        title = note.get("title") or "(无标题)"
        author = note.get("author") or "-"
        pub = format_timestamp(note.get("published_at"))
        img_cnt = note.get("image_count", 0)
        desc = note.get("desc") or "(无正文)"
        needs_img = _guess_needs_image(desc, img_cnt)
        tags = ", ".join(note.get("tags") or []) or "-"

        body = (
            f"[bold]标题：[/bold]{title}\n"
            f"[bold]作者：[/bold]{author}    [bold]发布时间：[/bold]{pub}    "
            f"[bold]图片数：[/bold]{img_cnt}    [bold]疑似需看图：[/bold]{'是' if needs_img else '否'}\n"
            f"[bold]标签：[/bold]{tags}\n"
            f"[bold]链接：[/bold]{_build_note_url(note.get('feed_id'))}\n"
            f"[bold]正文（{len(desc)} 字）：[/bold]\n{desc}"
        )
        console.print(Panel(body, title=f"[{idx}/{len(notes)}] feed_id={fid}", border_style="cyan"))


def export_review_artifacts(notes: list[dict[str, Any]], max_notes: int = 20) -> tuple[Path, list[Path]]:
    """根据已拉取的笔记详情生成人工评估表草稿与 evals/samples 标注模板。"""
    selected = notes[:max_notes]
    rows: list[str] = []
    sample_paths: list[Path] = []
    samples_dir = EVALS_DIR / "samples"
    samples_dir.mkdir(parents=True, exist_ok=True)

    for idx, note in enumerate(selected, start=1):
        fid = note.get("feed_id") or f"unknown_{idx:02d}"
        title = (note.get("title") or "(无标题)").replace("|", "｜").replace("\n", " ")[:40]
        pub = format_timestamp(note.get("published_at"))
        img_cnt = note.get("image_count", 0)
        desc_len = len(note.get("desc") or "")
        hint = "疑似看图" if _guess_needs_image(note.get("desc") or "", img_cnt) else "正文为主"
        rows.append(
            f"| {idx} | `{fid}` | {title} | {pub} | {desc_len} / {img_cnt}图 ({hint}) "
            f"| 待标注 | 待标注 | 待标注 | 待标注 | |"
        )

        sample_file = samples_dir / f"sample_{idx:02d}_{fid}.json"
        if not sample_file.exists():
            save_json(
                sample_file,
                {
                    "sample_id": f"sample_{idx:02d}",
                    "annotated": False,
                    "note": {
                        "feed_id": fid,
                        "source_url": _build_note_url(note.get("feed_id")),
                        "title": note.get("title") or "",
                        "author": note.get("author") or "",
                        "published_at": note.get("published_at"),
                        "image_count": img_cnt,
                        "desc": note.get("desc") or "",
                    },
                    "expected": {
                        "is_job_post": None,
                        "needs_image": _guess_needs_image(note.get("desc") or "", img_cnt),
                        "jobs": [
                            {
                                "company": None,
                                "position": None,
                                "city": None,
                                "education": None,
                                "graduation_year": None,
                                "skills": [],
                                "internship_duration": None,
                                "attendance_days": None,
                                "salary": None,
                                "deadline": None,
                                "apply_method": None,
                                "application_url": None,
                                "evidence": "",
                            }
                        ],
                    },
                    "notes": "人工核对后将 annotated 改为 true；若不是招聘帖则将 jobs 设为 []。",
                },
            )
        sample_paths.append(sample_file)

    review_md = EVALS_DIR / "manual_review.md"
    table_body = "\n".join(rows) if rows else "| 1 | - | （尚未采集样本） | - | - | - | - | - | - | - |"
    content = f"""# 阶段 1：20 篇目标笔记人工评估表

> 生成时间：{utc_now_iso()}（已自动填充 {len(selected)} 篇已采集笔记的基础元数据，请人工核对并填写“待标注”列）

## 1. 逐篇评估记录

| # | feed_id | 标题 | 发布时间 | 正文字数/图片 | 是否招聘帖 (是/否) | 信息位置 (正文/图片/混合) | 岗位数 | 投递方式 (邮箱/链接/私信/内推码/无) | 备注 |
|---|---|---|---|---|---|---|---:|---|---|
{table_body}

## 2. 统计汇总（标注完成后填写）

- 评估总篇数：{len(selected)}
- 招聘帖数量 / 占比：待统计
- 仅在图片中的招聘帖数量 / 占比：待统计
- 单篇含多个岗位的比例：待统计
- 常见投递方式分布：待统计
- 发布时间分布（30 天内 / 60 天内 / 超过 60 天）：待统计

## 3. 观察到的 MCP 字段结构

- `search_feeds` 顶层字段与单条字段：待确认（参考 `tests/fixtures/search_*.json`）
- `get_feed_detail` 顶层字段与正文/图片字段：待确认（参考 `tests/fixtures/detail_*.json`）

## 4. 关键决策点（图片识别优先级）

> 规则（见 `docs/development_plan.md`）：如果超过一半（>50%）的招聘信息只在图片里，图片识别从阶段 5 提前到阶段 4；否则第一版只做 `needs_image` 标记。

- **当前结论**：待完成 20 篇人工核对后填写
- **理由**：待填写
"""
    review_md.parent.mkdir(parents=True, exist_ok=True)
    review_md.write_text(content, encoding="utf-8")
    return review_md, sample_paths


def load_notes_from_fixtures(fixtures_dir: Path) -> list[dict[str, Any]]:
    notes: list[dict[str, Any]] = []
    for path in sorted(fixtures_dir.glob("detail_*.json")):
        data = load_json(path)
        if data.get("is_error"):
            continue
        summary = extract_detail_summary(data.get("parsed"))
        cand = data.get("candidate") or {}
        if not summary["feed_id"]:
            summary["feed_id"] = cand.get("feed_id")
        if not summary["title"]:
            summary["title"] = cand.get("title") or ""
        if not summary["author"]:
            summary["author"] = cand.get("author") or ""
        if not summary["published_at"]:
            summary["published_at"] = cand.get("published_at")
        notes.append(summary)
    return notes


async def run_live(url: str | None, keyword: str, top_n: int, no_sleep: bool) -> list[dict[str, Any]]:
    console = Console()
    target_url = get_mcp_url(url)
    console.print(f"[bold cyan]在线模式连接 MCP：[/bold cyan]{target_url}")
    console.print(f"[bold cyan]搜索关键词：[/bold cyan]{keyword}（取前 {top_n} 篇）")

    results: list[dict[str, Any]] = []
    async with open_mcp_session(target_url) as session:
        raw_search = await session.call_tool("search_feeds", {"keyword": keyword})
        s_info = extract_tool_result(raw_search)
        if s_info["is_error"]:
            raise RuntimeError(f"search_feeds 返回错误：{s_info['text']}")

        items = extract_feed_items(s_info["parsed"])
        console.print(f"搜索返回 [bold green]{len(items)}[/bold green] 条笔记。")

        picked: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in items:
            s = extract_feed_summary(item)
            if s["feed_id"] and s["xsec_token"] and s["feed_id"] not in seen:
                seen.add(s["feed_id"])
                picked.append(s)
            if len(picked) >= top_n:
                break

        for idx, cand in enumerate(picked, start=1):
            if not no_sleep:
                await polite_sleep(reason=f"拉取第 {idx}/{len(picked)} 篇详情前等待")
            raw_detail = await session.call_tool(
                "get_feed_detail",
                {"feed_id": cand["feed_id"], "xsec_token": cand["xsec_token"]},
            )
            d_info = extract_tool_result(raw_detail)
            if d_info["is_error"]:
                console.print(f"[red][{idx}] 拉取失败 {cand['feed_id']}：{d_info['text']}[/red]")
                continue
            dsum = extract_detail_summary(d_info["parsed"])
            if not dsum["feed_id"]:
                dsum["feed_id"] = cand["feed_id"]
            if not dsum["title"]:
                dsum["title"] = cand["title"]
            if not dsum["author"]:
                dsum["author"] = cand["author"]
            if not dsum["published_at"]:
                dsum["published_at"] = cand["published_at"]
            results.append(dsum)

    return results


def main() -> int:
    parser = argparse.ArgumentParser(description="不用 LLM 的端到端数据链路验证脚本")
    parser.add_argument("--url", default=None, help="MCP 服务地址，默认读取 config.toml")
    parser.add_argument("--keyword", default="广州 AI实习", help="搜索关键词（在线模式）")
    parser.add_argument("--top", type=int, default=5, help="获取前 N 篇详情（在线模式，默认 5）")
    parser.add_argument("--from-fixtures", action="store_true", help="离线模式：读取 tests/fixtures/detail_*.json")
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR, help="样本目录（默认 tests/fixtures）")
    parser.add_argument("--export-review", action="store_true", help="自动导出 evals/manual_review.md 与 evals/samples 草稿")
    parser.add_argument("--no-sleep", action="store_true", help="跳过间隔等待（仅调试用）")
    args = parser.parse_args()

    console = Console()
    try:
        if args.from_fixtures:
            notes = load_notes_from_fixtures(args.fixtures_dir)
            console.print(f"[bold cyan]离线回放模式：[/bold cyan]从 {args.fixtures_dir} 读取了 {len(notes)} 篇详情样本。")
        else:
            notes = asyncio.run(run_live(args.url, args.keyword, args.top, args.no_sleep))
    except Exception as exc:
        console.print(f"[bold red]运行失败：[/bold red]{exc}")
        return 1

    _render_notes(console, notes)

    if args.export_review:
        review_md, sample_files = export_review_artifacts(notes)
        console.print(f"\n[bold green]已生成人工评估表：[/bold green]{review_md}")
        console.print(f"[bold green]已生成/保留样本草稿：[/bold green]{len(sample_files)} 份（位于 evals/samples/）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
