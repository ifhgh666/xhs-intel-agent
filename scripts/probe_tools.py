"""阶段 1 任务 1：连接 MCP，调用 list_tools()，记录工具名、描述与参数 Schema。

用法：
    uv run python scripts/probe_tools.py
    uv run python scripts/probe_tools.py --url http://localhost:18060/mcp
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_helper import FIXTURES_DIR, get_mcp_url, get_tool_input_schema, open_mcp_session, save_json, utc_now_iso

# 对照 docs/design.md 5.1 节工具白名单分类
WHITELIST_ROLES = {
    "check_login_status": "预检（Runtime 内部）",
    "search_feeds": "白名单（适配为 search_notes）",
    "get_feed_detail": "白名单（适配为 get_note_detail / 抽取管道）",
    "get_login_qrcode": "仅 CLI 登录流程",
}
FORBIDDEN_WRITE_TOOLS = {
    "delete_cookies",
    "publish_content",
    "publish_with_video",
    "post_comment_to_feed",
    "reply_comment_in_feed",
    "reply_notification",
    "like_feed",
    "like_notification",
    "favorite_feed",
}


async def run(url: str | None, out_path: Path) -> int:
    console = Console()
    target_url = get_mcp_url(url)
    console.print(f"[bold cyan]连接 MCP Server:[/bold cyan] {target_url}")

    try:
        async with open_mcp_session(target_url) as session:
            result = await session.list_tools()
    except Exception as exc:
        console.print(f"[bold red]连接 MCP 失败：[/bold red]{exc}")
        console.print("请确认 xiaohongshu-mcp 服务已启动，例如：")
        console.print(r"  .\tools\xiaohongshu-mcp\xiaohongshu-mcp-windows-amd64.exe")
        return 1

    tools_data = []
    table = Table(title=f"MCP Tools ({len(result.tools)} 个)", show_lines=True)
    table.add_column("工具名", style="bold cyan", no_wrap=True)
    table.add_column("设计文档分类", style="magenta")
    table.add_column("必填参数", style="green")
    table.add_column("可选参数", style="yellow")
    table.add_column("描述")

    discovered_names: set[str] = set()
    for tool in result.tools:
        discovered_names.add(tool.name)
        schema = get_tool_input_schema(tool)
        props = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = set(schema.get("required", []) if isinstance(schema, dict) else [])
        optional = [k for k in props if k not in required]

        if tool.name in WHITELIST_ROLES:
            role = WHITELIST_ROLES[tool.name]
        elif tool.name in FORBIDDEN_WRITE_TOOLS:
            role = "[red]禁止（写操作）[/red]"
        else:
            role = "未启用（第一版不注册）"

        desc = (tool.description or "").strip().replace("\n", " ")
        table.add_row(
            tool.name,
            role,
            ", ".join(sorted(required)) or "-",
            ", ".join(optional) or "-",
            desc[:120] + ("..." if len(desc) > 120 else ""),
        )

        tools_data.append(
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": schema,
            }
        )

    console.print(table)

    missing_required = [name for name in ("check_login_status", "search_feeds", "get_feed_detail") if name not in discovered_names]
    if missing_required:
        console.print(f"[bold red]警告：缺少核心白名单工具：{missing_required}[/bold red]")
    else:
        console.print("[bold green]核心白名单工具检查通过：check_login_status / search_feeds / get_feed_detail 均存在。[/bold green]")

    payload = {
        "captured_at": utc_now_iso(),
        "mcp_url": target_url,
        "tool_count": len(tools_data),
        "tools": tools_data,
    }
    saved = save_json(out_path, payload)
    console.print(f"[green]已保存工具 Schema 样本：[/green]{saved}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="探测 xiaohongshu-mcp 工具列表与参数 Schema")
    parser.add_argument("--url", default=None, help="MCP 服务地址，默认读取 config.toml")
    parser.add_argument(
        "--out",
        type=Path,
        default=FIXTURES_DIR / "tools.json",
        help="输出 JSON 路径（默认 tests/fixtures/tools.json）",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.out))


if __name__ == "__main__":
    raise SystemExit(main())
