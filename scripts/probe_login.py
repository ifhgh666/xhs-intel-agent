"""阶段 1 任务 2：调用 check_login_status 确认小红书登录状态。

用法：
    uv run python scripts/probe_login.py
    uv run python scripts/probe_login.py --save-qrcode
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _mcp_helper import FIXTURES_DIR, ROOT_DIR, extract_tool_result, get_mcp_url, open_mcp_session, save_json, utc_now_iso


async def run(url: str | None, save_qrcode: bool, save_fixture: bool) -> int:
    console = Console()
    target_url = get_mcp_url(url)
    console.print(f"[bold cyan]连接 MCP Server:[/bold cyan] {target_url}")

    try:
        async with open_mcp_session(target_url) as session:
            raw_res = await session.call_tool("check_login_status", {})
            info = extract_tool_result(raw_res)

            console.print(
                Panel(
                    info["text"] or "(空返回)",
                    title="check_login_status 返回",
                    border_style="red" if info["is_error"] else "cyan",
                )
            )

            if save_fixture:
                out = save_json(
                    FIXTURES_DIR / "login_status.json",
                    {
                        "captured_at": utc_now_iso(),
                        "mcp_url": target_url,
                        "tool": "check_login_status",
                        "result": info,
                    },
                )
                console.print(f"[green]已保存登录检查样本：[/green]{out}")

            text_lower = info["text"].lower()
            is_logged_in = (
                not info["is_error"]
                and ("已登录" in info["text"] or "logged in" in text_lower)
                and "未登录" not in info["text"]
                and "not logged" not in text_lower
            )

            if is_logged_in:
                console.print("[bold green]登录状态正常：已登录小红书。[/bold green]")
                return 0

            console.print("[bold yellow]当前未登录小红书。[/bold yellow]")
            if save_qrcode:
                console.print("正在调用 get_login_qrcode 获取登录二维码...")
                qr_raw = await session.call_tool("get_login_qrcode", {})
                qr_info = extract_tool_result(qr_raw)
                console.print("get_login_qrcode 文本返回：", qr_info["text"])
                for idx, item in enumerate(qr_info["raw_content"]):
                    if item.get("type") == "image" and item.get("data"):
                        img_path = ROOT_DIR / "data" / f"login_qrcode_{idx + 1}.png"
                        img_path.parent.mkdir(parents=True, exist_ok=True)
                        img_path.write_bytes(base64.b64decode(item["data"]))
                        console.print(f"[bold green]二维码图片已保存：[/bold green]{img_path}")
            else:
                console.print("请先运行登录工具完成扫码：")
                console.print(r"  .\tools\xiaohongshu-mcp\xiaohongshu-login-windows-amd64.exe")
                console.print("或者带参数 --save-qrcode 通过 MCP 获取二维码图片。")
            return 2

    except Exception as exc:
        console.print(f"[bold red]调用失败：[/bold red]{exc}")
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 xiaohongshu-mcp 登录状态")
    parser.add_argument("--url", default=None, help="MCP 服务地址，默认读取 config.toml")
    parser.add_argument("--save-qrcode", action="store_true", help="未登录时顺便调用 get_login_qrcode 保存二维码")
    parser.add_argument("--no-save", action="store_true", help="不写入 tests/fixtures/login_status.json")
    args = parser.parse_args()
    return asyncio.run(run(args.url, args.save_qrcode, not args.no_save))


if __name__ == "__main__":
    raise SystemExit(main())
