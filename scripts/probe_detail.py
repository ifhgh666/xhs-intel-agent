"""阶段 1 任务 4：从搜索样本中取若干篇笔记，用 feed_id + xsec_token 调用 get_feed_detail 并保存样本。

用法：
    uv run python scripts/probe_detail.py
    uv run python scripts/probe_detail.py --count 5
    uv run python scripts/probe_detail.py --count 20
    uv run python scripts/probe_detail.py --feed-id <id> --xsec-token <token>
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_helper import (
    FIXTURES_DIR,
    extract_detail_summary,
    extract_feed_items,
    extract_feed_summary,
    extract_tool_result,
    format_timestamp,
    get_mcp_url,
    get_tool_input_schema,
    load_json,
    open_mcp_session,
    polite_sleep,
    save_json,
    utc_now_iso,
)


def _resolve_detail_params(tools: list[Any]) -> tuple[str, str]:
    feed_param = "feed_id"
    token_param = "xsec_token"
    for tool in tools:
        if tool.name == "get_feed_detail":
            props = get_tool_input_schema(tool).get("properties", {})
            for cand in ("feed_id", "feedId", "note_id", "noteId", "id"):
                if cand in props:
                    feed_param = cand
                    break
            for cand in ("xsec_token", "xsecToken", "token"):
                if cand in props:
                    token_param = cand
                    break
    return feed_param, token_param


def collect_candidates_from_fixtures(fixtures_dir: Path) -> list[dict[str, Any]]:
    """从 tests/fixtures/search_*.json 中收集去重后的候选笔记。"""
    candidates: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for path in sorted(fixtures_dir.glob("search_*.json")):
        data = load_json(path)
        keyword = (data.get("arguments") or {}).get("keyword") or path.stem
        items = extract_feed_items(data.get("parsed"))
        for item in items:
            summary = extract_feed_summary(item)
            fid = summary["feed_id"]
            token = summary["xsec_token"]
            if not fid or not token or fid in seen_ids:
                continue
            seen_ids.add(fid)
            summary["source_keyword"] = keyword
            summary["source_fixture"] = path.name
            candidates.append(summary)
    return candidates


async def run(
    url: str | None,
    fixtures_dir: Path,
    count: int,
    offset: int,
    feed_id: str | None,
    xsec_token: str | None,
    skip_existing: bool,
    no_sleep: bool,
) -> int:
    console = Console()
    target_url = get_mcp_url(url)

    if feed_id and xsec_token:
        targets = [{"feed_id": feed_id, "xsec_token": xsec_token, "title": "(手动指定)", "source_keyword": "manual"}]
    else:
        all_candidates = collect_candidates_from_fixtures(fixtures_dir)
        if not all_candidates:
            console.print(f"[bold red]在 {fixtures_dir} 中未找到包含 feed_id 与 xsec_token 的搜索样本。[/bold red]")
            console.print("请先运行：uv run python scripts/probe_search.py")
            return 1
        targets = all_candidates[offset : offset + count]
        console.print(
            f"从搜索样本共收集到 [bold cyan]{len(all_candidates)}[/bold cyan] 篇去重笔记，"
            f"本次拉取第 {offset + 1}–{offset + len(targets)} 篇（共 {len(targets)} 篇）。"
        )

    existing_ids: set[str] = set()
    if skip_existing:
        for p in fixtures_dir.glob("detail_*.json"):
            try:
                d = load_json(p)
                fid = (d.get("arguments") or {}).get("feed_id") or (d.get("candidate") or {}).get("feed_id")
                if fid:
                    existing_ids.add(str(fid))
            except Exception:
                continue

    try:
        async with open_mcp_session(target_url) as session:
            tools_res = await session.list_tools()
            feed_param, token_param = _resolve_detail_params(tools_res.tools)
            console.print(
                f"检测到 get_feed_detail 参数名：[bold green]{feed_param}[/bold green], "
                f"[bold green]{token_param}[/bold green]"
            )

            summary_table = Table(title="笔记详情探测汇总", show_lines=True)
            summary_table.add_column("#", width=3)
            summary_table.add_column("feed_id", style="dim", max_width=24)
            summary_table.add_column("标题", max_width=32)
            summary_table.add_column("正文字数", justify="right", width=8)
            summary_table.add_column("图片数", justify="right", width=6)
            summary_table.add_column("发布时间", width=16)
            summary_table.add_column("状态", width=10)

            fetched_live = 0
            for idx, cand in enumerate(targets, start=offset + 1):
                fid = cand["feed_id"]
                if skip_existing and fid in existing_ids:
                    console.print(f"[dim][{idx}] 跳过已存在详情样本：{fid} ({cand.get('title', '')[:25]})[/dim]")
                    continue

                if fetched_live > 0 and not no_sleep:
                    await polite_sleep(reason=f"拉取第 {idx} 篇详情前等待")

                args = {feed_param: fid, token_param: cand["xsec_token"]}
                console.print(f"[bold cyan][{idx}] 拉取详情：[/bold cyan]{fid} | {cand.get('title', '')[:40]}")
                raw_res = await session.call_tool("get_feed_detail", args)
                info = extract_tool_result(raw_res)
                fetched_live += 1

                out_path = fixtures_dir / f"detail_{idx:02d}_{fid}.json"
                payload = {
                    "captured_at": utc_now_iso(),
                    "mcp_url": target_url,
                    "tool": "get_feed_detail",
                    "arguments": args,
                    "candidate": cand,
                    "is_error": info["is_error"],
                    "raw_text": info["text"],
                    "parsed": info["parsed"],
                }
                save_json(out_path, payload)

                if info["is_error"]:
                    summary_table.add_row(str(idx), fid, cand.get("title", ""), "-", "-", "-", "[red]ERROR[/red]")
                    console.print(f"  [red]错误：[/red]{info['text'][:200]}")
                    continue

                dsum = extract_detail_summary(info["parsed"])
                summary_table.add_row(
                    str(idx),
                    fid,
                    (dsum["title"] or cand.get("title") or "(无标题)")[:32],
                    str(len(dsum["desc"])),
                    str(dsum["image_count"]),
                    format_timestamp(dsum["published_at"]),
                    "[green]OK[/green]",
                )
                console.print(f"  已保存：[green]{out_path.name}[/green]")

            console.print(summary_table)
    except Exception as exc:
        console.print(f"[bold red]详情探测失败：[/bold red]{exc}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 xiaohongshu-mcp 的 get_feed_detail 接口并保存样本")
    parser.add_argument("--url", default=None, help="MCP 服务地址，默认读取 config.toml")
    parser.add_argument("--fixtures-dir", type=Path, default=FIXTURES_DIR, help="样本目录（默认 tests/fixtures）")
    parser.add_argument("--count", type=int, default=5, help="拉取详情篇数（默认 5）")
    parser.add_argument("--offset", type=int, default=0, help="从第几篇候选开始（默认 0）")
    parser.add_argument("--feed-id", default=None, help="手动指定单个 feed_id")
    parser.add_argument("--xsec-token", default=None, help="手动指定对应 xsec_token")
    parser.add_argument("--skip-existing", action="store_true", help="跳过 fixtures 目录中已抓取过的 feed_id")
    parser.add_argument("--no-sleep", action="store_true", help="跳过间隔等待（仅调试用）")
    args = parser.parse_args()
    return asyncio.run(
        run(
            args.url,
            args.fixtures_dir,
            args.count,
            args.offset,
            args.feed_id,
            args.xsec_token,
            args.skip_existing,
            args.no_sleep,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main())
