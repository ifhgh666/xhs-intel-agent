"""小红书实习情报 Agent CLI 入口。

用法：
    uv run python -m xhs_agent "帮我找广州 AI Agent 实习"
    uv run python -m xhs_agent "帮我搜索广州的 AI Agent、RAG 实习岗位，优先找适合 2028 届本科生的岗位。" --max-search 3 --max-detail 6
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from xhs_agent.config import load_config
from xhs_agent.harness.runtime import AgentRuntime, StartupPrecheckError


def _make_cli_event_printer(console: Console):
    def on_event(event: str, data: dict[str, Any]) -> None:
        if event == "tool_call":
            console.print(
                f"  [bold cyan][Step {data['step']}] 调用工具[/bold cyan] "
                f"[bold]{data['tool']}[/bold]({data['args_raw']})"
            )
        elif event == "tool_result":
            status = "[green]OK[/green]" if data["ok"] else "[red]ERR[/red]"
            console.print(
                f"  [dim][Step {data['step']}] 返回 {status} ({data['duration_ms']}ms): "
                f"{data['summary']}[/dim]"
            )
        elif event == "budget_exhausted":
            console.print(f"  [bold yellow][Step {data['step']}] 触发结束条件：[/bold yellow]{data['reason']}")
        elif event == "wrap_up":
            console.print("  [yellow]正在基于已收集结果生成最终汇总...[/yellow]")

    return on_event


async def run_cli(
    task: str,
    *,
    config_path: Path | None = None,
    max_steps: int | None = None,
    max_search: int | None = None,
    max_detail: int | None = None,
) -> int:
    console = Console()
    cfg = load_config(config_path)
    if max_steps is not None:
        cfg.budget.max_steps = max_steps
    if max_search is not None:
        cfg.budget.max_search_calls = max_search
    if max_detail is not None:
        cfg.budget.max_detail_calls = max_detail

    console.print(Panel(task, title="任务需求", border_style="cyan"))
    console.print("[dim]正在执行启动预检（MCP 连通性 → 白名单 Schema → 登录态）...[/dim]")

    try:
        async with AgentRuntime(cfg) as rt:
            console.print(
                f"[green]预检通过[/green] | 可用工具：{', '.join(rt.precheck_summary['registered_agent_tools'])} | "
                f"预算：steps≤{cfg.budget.max_steps}, search≤{cfg.budget.max_search_calls}, detail≤{cfg.budget.max_detail_calls}"
            )
            result = await rt.run_task(task, on_event=_make_cli_event_printer(console))
    except StartupPrecheckError as exc:
        console.print(Panel(str(exc), title="启动预检失败", border_style="red"))
        return 2
    except Exception as exc:
        console.print(f"[bold red]运行异常：[/bold red]{exc}")
        return 1

    console.print()
    console.print(Panel(Markdown(result.answer or "(无回答)"), title="最终汇总", border_style="green"))

    cache_rate = (
        (result.usage.cache_hit_tokens / result.usage.prompt_tokens * 100.0)
        if result.usage.prompt_tokens > 0
        else 0.0
    )
    console.print(
        f"[dim]统计：session={result.session_id} | steps={result.steps} | llm_calls={result.llm_calls} | "
        f"tools={result.tool_calls} | tokens={result.usage.total_tokens} "
        f"(prompt={result.usage.prompt_tokens}, cache_hit={result.usage.cache_hit_tokens} [{cache_rate:.0f}%], "
        f"completion={result.usage.completion_tokens}) | stop={result.stop_reason}[/dim]"
    )
    console.print(f"[dim]Trace 文件：{result.trace_path}[/dim]")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m xhs_agent",
        description="小红书实习情报 Agent CLI",
    )
    parser.add_argument("task", help="自然语言实习搜索需求")
    parser.add_argument("--config", type=Path, default=None, help="自定义 config.toml 路径")
    parser.add_argument("--max-steps", type=int, default=None, help="覆盖最大循环步数")
    parser.add_argument("--max-search", type=int, default=None, help="覆盖本次最大搜索次数")
    parser.add_argument("--max-detail", type=int, default=None, help="覆盖本次最大详情拉取次数")
    args = parser.parse_args(argv)
    return asyncio.run(
        run_cli(
            args.task,
            config_path=args.config,
            max_steps=args.max_steps,
            max_search=args.max_search,
            max_detail=args.max_detail,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
