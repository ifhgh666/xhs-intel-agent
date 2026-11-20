"""阶段 2 验收验证脚本。

功能：
1. 默认离线模式（基于阶段 1 fixtures）：
   - 展示 3 份搜索样本裁剪前后的体积对比数据（记录到控制台）
   - 验证 search_notes → get_note_detail("n1") 连续调用无需传 xsec_token
   - 验证二次调用 get_note_detail("n1") 命中缓存，只请求 1 次 MCP
2. 可选真机冒烟模式（--live）：
   - 连接真实 http://localhost:18060/mcp
   - 执行启动预检 validate_mcp_tools() + check_login_status()
   - 执行 1 次 search_notes + 2 次 get_note_detail("n1")（验证间隔 >= min_interval_sec 与二次缓存命中）

用法：
    uv run python scripts/verify_phase2.py
    uv run python scripts/verify_phase2.py --live
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

from rich.console import Console
from rich.table import Table

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from xhs_agent.config import load_config
from xhs_agent.xhs import MCPClient, RateLimiter, XhsAdapter


async def run_offline(console: Console) -> int:
    from tests.test_phase2_xhs import FixtureMCPClient

    mock = FixtureMCPClient()
    adapter = XhsAdapter(mock, rate_limiter=RateLimiter(min_interval_sec=0, jitter_sec=0))

    await adapter.validate_mcp_tools()
    table = Table(title="阶段 2 离线裁剪效果对比（基于真实录制样本）", show_lines=True)
    table.add_column("搜索关键词", style="cyan")
    table.add_column("原始字节", justify="right")
    table.add_column("裁剪后字节", justify="right")
    table.add_column("压缩比例", justify="right", style="bold green")
    table.add_column("笔记数", justify="right")

    for kw, fixture in mock._searches.items():
        raw_bytes = len(fixture["raw_text"].encode("utf-8"))
        trimmed = await adapter.search_notes(kw)
        trimmed_bytes = len(json.dumps(trimmed, ensure_ascii=False).encode("utf-8"))
        reduction = (1.0 - trimmed_bytes / raw_bytes) * 100.0
        table.add_row(kw, f"{raw_bytes:,}", f"{trimmed_bytes:,}", f"-{reduction:.1f}%", str(trimmed["count"]))

    console.print(table)

    d1 = await adapter.get_note_detail("n1")
    d2 = await adapter.get_note_detail("n1")
    detail_mcp_calls = sum(1 for name, _ in mock.calls if name == "get_feed_detail")

    raw_detail_bytes = len(mock._details[adapter.cache.get("n1").feed_id]["raw_text"].encode("utf-8"))
    trimmed_detail_bytes = len(json.dumps(d1, ensure_ascii=False).encode("utf-8"))
    detail_reduction = (1.0 - trimmed_detail_bytes / raw_detail_bytes) * 100.0

    console.print(
        f"[bold green]详情裁剪对比（n1）：[/bold green]{raw_detail_bytes:,}B → {trimmed_detail_bytes:,}B "
        f"([bold]-{detail_reduction:.1f}%[/bold])"
    )
    console.print(
        f"[bold green]缓存命中验证：[/bold green]第 1 次 from_cache={d1['from_cache']}，"
        f"第 2 次 from_cache={d2['from_cache']}，实际 get_feed_detail MCP 调用次数={detail_mcp_calls}"
    )
    return 0


async def run_live(console: Console, keyword: str) -> int:
    cfg = load_config()
    limiter = RateLimiter(
        min_interval_sec=cfg.rate_limit.min_interval_sec,
        jitter_sec=cfg.rate_limit.jitter_sec,
        daily_limits={
            "search": cfg.rate_limit.daily_search_limit,
            "detail": cfg.rate_limit.daily_detail_limit,
        },
    )

    async with MCPClient(cfg.mcp.url) as client:
        adapter = XhsAdapter(client, rate_limiter=limiter)
        validated = await adapter.validate_mcp_tools()
        console.print(f"[green]启动预检通过：[/green]白名单工具 {sorted(validated.keys())}")

        login = await adapter.check_login_status()
        console.print(f"[green]登录检查：[/green]logged_in={login['logged_in']}")
        if not login["logged_in"]:
            console.print("[red]未登录小红书，请先扫码登录。[/red]")
            return 2

        t0 = time.monotonic()
        search_res = await adapter.search_notes(keyword)
        t1 = time.monotonic()
        console.print(
            f"[cyan]search_notes({keyword!r}) 返回 {search_res['count']} 条，首条："
            f"{search_res['notes'][0]['note']} | {search_res['notes'][0]['title']} | "
            f"{search_res['notes'][0]['published_at']}[/cyan]"
        )

        d1 = await adapter.get_note_detail("n1")
        t2 = time.monotonic()
        gap_sec = t2 - t1
        console.print(
            f"[cyan]get_note_detail('n1') 第 1 次：from_cache={d1['from_cache']}，"
            f"标题={d1['title']}，字数={d1['content_length']}，与上次搜索间隔={gap_sec:.2f}s[/cyan]"
        )

        d2 = await adapter.get_note_detail("n1")
        t3 = time.monotonic()
        console.print(
            f"[bold green]get_note_detail('n1') 第 2 次：from_cache={d2['from_cache']}，"
            f"耗时={(t3 - t2)*1000:.2f}ms，总限流等待={limiter.total_waited_sec:.2f}s，"
            f"今日用量 search={limiter.get_daily_usage('search')}, detail={limiter.get_daily_usage('detail')}[/bold green]"
        )

        assert d1["from_cache"] is False
        assert d2["from_cache"] is True
        assert limiter.get_daily_usage("detail") == 1
        assert limiter.total_waited_sec >= cfg.rate_limit.min_interval_sec
        console.print("[bold green]阶段 2 真机冒烟验收全部通过！[/bold green]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 2 小红书适配层验收验证脚本")
    parser.add_argument("--live", action="store_true", help="连接真实 MCP Server 做低频冒烟验证（1 次搜索 + 1 次详情）")
    parser.add_argument("--keyword", default="广州 AI实习", help="--live 模式下的搜索关键词")
    args = parser.parse_args()

    console = Console()
    if args.live:
        return asyncio.run(run_live(console, args.keyword))
    return asyncio.run(run_offline(console))


if __name__ == "__main__":
    raise SystemExit(main())
