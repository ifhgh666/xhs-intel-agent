"""阶段 1 任务 3：用 3 个关键词调用 search_feeds，保存真实返回样本。

用法：
    uv run python scripts/probe_search.py
    uv run python scripts/probe_search.py --keywords "广州 AI实习" "RAG 实习" "Agent 实习 2028届"
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
    extract_feed_items,
    extract_feed_summary,
    extract_tool_result,
    format_timestamp,
    get_mcp_url,
    get_tool_input_schema,
    open_mcp_session,
    polite_sleep,
    save_json,
    slugify_keyword,
    utc_now_iso,
)

DEFAULT_KEYWORDS = [
    "广州 AI实习",
    "RAG 实习",
    "Agent 实习 2028届",
]


def _resolve_keyword_param(tools: list[Any]) -> str:
    for tool in tools:
        if tool.name == "search_feeds":
            props = get_tool_input_schema(tool).get("properties", {})
            for candidate in ("keyword", "query", "q"):
                if candidate in props:
                    return candidate
            if props:
                return next(iter(props.keys()))
    return "keyword"


async def run(url: str | None, keywords: list[str], out_dir: Path, no_sleep: bool) -> int:
    console = Console()
    target_url = get_mcp_url(url)
    console.print(f"[bold cyan]连接 MCP Server:[/bold cyan] {target_url}")

    try:
        async with open_mcp_session(target_url) as session:
            tools_res = await session.list_tools()
            kw_param = _resolve_keyword_param(tools_res.tools)
            console.print(f"检测到 search_feeds 关键词参数名：[bold green]{kw_param}[/bold green]")

            for idx, keyword in enumerate(keywords, start=1):
                if idx > 1 and not no_sleep:
                    await polite_sleep(reason=f"第 {idx}/{len(keywords)} 次搜索前等待")

                console.print(f"\n[bold cyan][{idx}/{len(keywords)}] 搜索关键词：[/bold cyan]{keyword}")
                args = {kw_param: keyword}
                raw_res = await session.call_tool("search_feeds", args)
                info = extract_tool_result(raw_res)

                slug = slugify_keyword(keyword)
                out_path = out_dir / f"search_{idx:02d}_{slug}.json"
                payload = {
                    "captured_at": utc_now_iso(),
                    "mcp_url": target_url,
                    "tool": "search_feeds",
                    "arguments": args,
                    "is_error": info["is_error"],
                    "raw_text": info["text"],
                    "parsed": info["parsed"],
                }
                save_json(out_path, payload)

                if info["is_error"]:
                    console.print(f"[bold red]搜索返回错误：[/bold red]{info['text']}")
                    continue

                items = extract_feed_items(info["parsed"])
                top_keys = list(info["parsed"].keys()) if isinstance(info["parsed"], dict) else type(info["parsed"]).__name__
                sample_keys = list(items[0].keys()) if items else []
                console.print(
                    f"返回条数：[bold green]{len(items)}[/bold green] | "
                    f"顶层结构：{top_keys} | 单条字段：{sample_keys}"
                )
                console.print(f"已保存样本：[green]{out_path}[/green]")

                if items:
                    table = Table(title=f"前 5 条预览：{keyword}", show_lines=False)
                    table.add_column("#", width=3)
                    table.add_column("feed_id", style="dim", max_width=26)
                    table.add_column("has_token", width=9)
                    table.add_column("标题", max_width=40)
                    table.add_column("作者", max_width=16)
                    table.add_column("时间", max_width=18)
                    for i, raw_item in enumerate(items[:5], start=1):
                        s = extract_feed_summary(raw_item)
                        table.add_row(
                            str(i),
                            s["feed_id"] or "-",
                            "✓" if s["xsec_token"] else "✗",
                            s["title"] or "(无标题)",
                            s["author"] or "-",
                            format_timestamp(s["published_at"]),
                        )
                    console.print(table)
    except Exception as exc:
        console.print(f"[bold red]搜索探测失败：[/bold red]{exc}")
        return 1

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 xiaohongshu-mcp 的 search_feeds 接口并保存样本")
    parser.add_argument("--url", default=None, help="MCP 服务地址，默认读取 config.toml")
    parser.add_argument(
        "--keywords",
        nargs="+",
        default=DEFAULT_KEYWORDS,
        help="搜索关键词列表（默认 3 个实习相关关键词）",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=FIXTURES_DIR,
        help="样本保存目录（默认 tests/fixtures）",
    )
    parser.add_argument("--no-sleep", action="store_true", help="跳过间隔等待（仅调试用，真实调用不建议开启）")
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.keywords, args.out_dir, args.no_sleep))


if __name__ == "__main__":
    raise SystemExit(main())
