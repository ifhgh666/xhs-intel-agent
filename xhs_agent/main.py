"""小红书实习情报 Agent CLI 入口（支持单轮任务、交互式多轮对话、用量/费用统计与日志分级）。

用法：
    uv run python -m xhs_agent "帮我搜索广州的 AI Agent、RAG 实习岗位，优先找适合 2028 届本科生的岗位，并导出 Excel。"
    uv run python -m xhs_agent --interactive
    uv run python -m xhs_agent "帮我找广州 AI Agent 实习" --verbose --max-search 3 --max-detail 6
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
from rich.table import Table

from xhs_agent.config import load_config
from xhs_agent.harness.loop import AgentRunResult
from xhs_agent.harness.runtime import AgentRuntime, StartupPrecheckError
from xhs_agent.logging_setup import configure_logging


def _make_cli_event_printer(console: Console, *, verbose: bool = False):
    def on_event(event: str, data: dict[str, Any]) -> None:
        if event == "llm_response" and verbose:
            u = data.get("usage") or {}
            console.print(
                f"  [dim][Step {data['step']}] LLM 响应 ({data['duration_ms']}ms): "
                f"tokens={u.get('total_tokens', 0)} (cache_hit={u.get('cache_hit_tokens', 0)}), "
                f"tool_calls={data.get('tool_call_count', 0)}[/dim]"
            )
        elif event == "tool_call":
            console.print(
                f"  [bold cyan][Step {data['step']}] 调用工具[/bold cyan] "
                f"[bold]{data['tool']}[/bold]({data['args_raw']})"
            )
        elif event == "tool_result":
            status = "[green]OK[/green]" if data["ok"] else "[red]ERR[/red]"
            detail_msg = data["error"] if (not data["ok"] and verbose and data.get("error")) else data["summary"]
            console.print(
                f"  [dim][Step {data['step']}] 返回 {status} ({data['duration_ms']}ms): "
                f"{detail_msg}[/dim]"
            )
        elif event == "budget_exhausted":
            console.print(f"  [bold yellow][Step {data['step']}] 触发结束条件：[/bold yellow]{data['reason']}")
        elif event == "wrap_up":
            console.print("  [yellow]正在基于已收集结果生成最终汇总...[/yellow]")

    return on_event


def render_usage_summary(
    console: Console,
    result: AgentRunResult,
    *,
    db_total_jobs: int | None = None,
) -> None:
    """以清晰表格输出本次任务的 LLM 调用次数、Token、预估费用与工具调用统计。"""
    u = result.total_usage
    cache_rate = (u.cache_hit_tokens / u.prompt_tokens * 100.0) if u.prompt_tokens > 0 else 0.0
    tools_str = ", ".join(f"{k}={v}" for k, v in result.tool_calls.items()) or "无"
    llm_detail = f"总计 {result.total_llm_calls} 次（主 Agent {result.llm_calls} + 抽取 {result.extract_llm_calls}）"
    token_detail = (
        f"总计 {u.total_tokens:,}（输入 {u.prompt_tokens:,} [缓存命中 {u.cache_hit_tokens:,} / {cache_rate:.0f}%]，"
        f"输出 {u.completion_tokens:,}）"
    )
    cost_detail = (
        f"¥{result.cost.total_cost:.4f} {result.cost.currency} "
        f"（缓存节省 ¥{result.cost.cache_savings:.4f}）"
    )

    table = Table(title="本次任务用量与费用统计", show_header=False, box=None, padding=(0, 1))
    table.add_column("项", style="bold dim", width=12)
    table.add_column("值")
    table.add_row("会话 / 状态", f"{result.session_id}  |  steps={result.steps}  |  stop={result.stop_reason}")
    table.add_row("LLM 调用", llm_detail)
    table.add_row("Token 用量", token_detail)
    table.add_row("预估费用", cost_detail)
    table.add_row("工具调用", tools_str)
    if db_total_jobs is not None:
        table.add_row("本地岗位库", f"累计 {db_total_jobs} 条岗位记录")
    table.add_row("Trace 轨迹", str(result.trace_path))
    console.print(Panel(table, border_style="dim"))


async def run_interactive(
    *,
    config_path: Path | None = None,
    db_path: Path | None = None,
    max_steps: int | None = None,
    max_search: int | None = None,
    max_detail: int | None = None,
    verbose: bool = False,
) -> int:
    """多轮交互式 CLI 模式（支持连续追问、筛选、标记状态、导出 Excel）。"""
    configure_logging(verbose=verbose)
    console = Console()
    cfg = load_config(config_path)
    if max_steps is not None:
        cfg.budget.max_steps = max_steps
    if max_search is not None:
        cfg.budget.max_search_calls = max_search
    if max_detail is not None:
        cfg.budget.max_detail_calls = max_detail

    console.print("[dim]正在执行启动预检（SQLite 初始化 → MCP 连通性 → 白名单 Schema → 登录态）...[/dim]")
    try:
        async with AgentRuntime(cfg, db_path=db_path) as rt:
            session = rt.new_session()
            console.print(
                Panel(
                    f"已进入多轮交互模式（session={session.session_id}）\n"
                    f"可用工具：{', '.join(rt.precheck_summary['registered_agent_tools'])}\n"
                    "内置命令：/jobs 查看本地岗位数 | /stats 查看本会话累计费用 | /exit 退出",
                    title="小红书实习情报 Agent（交互模式）",
                    border_style="cyan",
                )
            )
            try:
                while True:
                    try:
                        user_input = (await asyncio.to_thread(input, "\n你> ")).strip()
                    except (EOFError, KeyboardInterrupt):
                        break
                    if not user_input:
                        continue
                    if user_input.lower() in {"/exit", "/quit", "exit", "quit"}:
                        break
                    if user_input.lower() == "/jobs":
                        cnt = rt.store.count_jobs() if rt.store is not None else 0
                        console.print(f"[cyan]本地 SQLite 岗位库当前共有 {cnt} 条岗位记录。[/cyan]")
                        continue
                    if user_input.lower() == "/stats":
                        c = session.total_cost
                        console.print(
                            f"[cyan]本会话累计：轮次={session.turns} | LLM调用={session.total_llm_calls} | "
                            f"Token={session.total_usage.total_tokens:,} | 预估费用=¥{c.total_cost:.4f} | "
                            f"工具={session.total_tool_calls}[/cyan]"
                        )
                        continue

                    result = await session.ask(
                        user_input,
                        on_event=_make_cli_event_printer(console, verbose=verbose),
                    )
                    console.print()
                    console.print(Panel(Markdown(result.answer or "(无回答)"), title="助手回复", border_style="green"))
                    db_cnt = rt.store.count_jobs() if rt.store is not None else None
                    render_usage_summary(console, result, db_total_jobs=db_cnt)
            finally:
                session.finish()
                console.print(
                    f"\n[dim]会话已结束（session={session.session_id}，累计费用 ¥{session.total_cost.total_cost:.4f}，"
                    f"Trace：{session.trace_path}）[/dim]"
                )
    except StartupPrecheckError as exc:
        console.print(Panel(str(exc), title="启动预检失败", border_style="red"))
        return 2
    except Exception as exc:
        console.print(f"[bold red]运行异常：[/bold red]{exc}")
        return 1
    return 0


async def run_cli(
    task: str,
    *,
    config_path: Path | None = None,
    db_path: Path | None = None,
    max_steps: int | None = None,
    max_search: int | None = None,
    max_detail: int | None = None,
    verbose: bool = False,
) -> int:
    configure_logging(verbose=verbose)
    console = Console()
    cfg = load_config(config_path)
    if max_steps is not None:
        cfg.budget.max_steps = max_steps
    if max_search is not None:
        cfg.budget.max_search_calls = max_search
    if max_detail is not None:
        cfg.budget.max_detail_calls = max_detail

    console.print(Panel(task, title="任务需求", border_style="cyan"))
    console.print("[dim]正在执行启动预检（SQLite 初始化 → MCP 连通性 → 白名单 Schema → 登录态）...[/dim]")

    db_total_jobs: int | None = None
    try:
        async with AgentRuntime(cfg, db_path=db_path) as rt:
            console.print(
                f"[green]预检通过[/green] | 可用工具：{', '.join(rt.precheck_summary['registered_agent_tools'])} | "
                f"数据库：{rt.precheck_summary.get('db_path')} | "
                f"预算：steps≤{cfg.budget.max_steps}, search≤{cfg.budget.max_search_calls}, detail≤{cfg.budget.max_detail_calls}"
            )
            result = await rt.run_task(
                task,
                on_event=_make_cli_event_printer(console, verbose=verbose),
            )
            if rt.store is not None:
                db_total_jobs = rt.store.count_jobs()
    except StartupPrecheckError as exc:
        console.print(Panel(str(exc), title="启动预检失败", border_style="red"))
        return 2
    except Exception as exc:
        console.print(f"[bold red]运行异常：[/bold red]{exc}")
        return 1

    console.print()
    console.print(Panel(Markdown(result.answer or "(无回答)"), title="最终汇总", border_style="green"))
    render_usage_summary(console, result, db_total_jobs=db_total_jobs)
    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m xhs_agent",
        description="小红书实习情报 Agent CLI",
    )
    parser.add_argument("task", nargs="?", default=None, help="自然语言实习搜索需求（省略时可加 -i 进入交互模式）")
    parser.add_argument("-i", "--interactive", action="store_true", help="进入多轮交互式对话模式")
    parser.add_argument("-v", "--verbose", action="store_true", help="详细输出模式（显示每步 LLM 延迟、Token 与调试日志）")
    parser.add_argument("--config", type=Path, default=None, help="自定义 config.toml 路径")
    parser.add_argument("--db", type=Path, default=None, help="自定义 SQLite 数据库路径（默认 data/xhs_jobs.db）")
    parser.add_argument("--max-steps", type=int, default=None, help="覆盖最大循环步数")
    parser.add_argument("--max-search", type=int, default=None, help="覆盖本次最大搜索次数")
    parser.add_argument("--max-detail", type=int, default=None, help="覆盖本次最大详情拉取次数")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.interactive or not args.task:
        if not args.interactive and not args.task:
            parser.print_help()
            return 2
        return asyncio.run(
            run_interactive(
                config_path=args.config,
                db_path=args.db,
                max_steps=args.max_steps,
                max_search=args.max_search,
                max_detail=args.max_detail,
                verbose=args.verbose,
            )
        )
    return asyncio.run(
        run_cli(
            args.task,
            config_path=args.config,
            db_path=args.db,
            max_steps=args.max_steps,
            max_search=args.max_search,
            max_detail=args.max_detail,
            verbose=args.verbose,
        )
    )


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
