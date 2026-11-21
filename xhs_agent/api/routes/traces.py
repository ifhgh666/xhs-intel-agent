"""历史 Trace 轨迹与系统成本总览统计接口（/api/traces/* 与 /api/stats/overview）。"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from xhs_agent.api.schemas import (
    DailyUsageSummary,
    OverviewStatsResponse,
    TraceDetailResponse,
    TraceSummaryItem,
)
from xhs_agent.api.state import WebAppState
from xhs_agent.config import PricingConfig
from xhs_agent.harness.llm import TokenUsage
from xhs_agent.harness.pricing import estimate_cost

router = APIRouter(tags=["traces"])

_SAFE_SESSION_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,80}$")


def _parse_trace_summary(path: Path, pricing: PricingConfig) -> TraceSummaryItem | None:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return None
    if not lines:
        return None

    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if not events:
        return None

    session_id = str(events[0].get("session") or path.stem)
    started_at = events[0].get("ts")
    mode = "single"
    task = ""
    turns = 0
    steps = 0
    llm_calls = 0
    prompt_tokens = 0
    completion_tokens = 0
    total_tokens = 0
    cache_hit_tokens = 0
    cache_miss_tokens = 0
    total_cost_cny = 0.0
    cache_savings_cny = 0.0
    stop_reason = ""
    has_session_end_usage = False

    for ev in events:
        etype = ev.get("type")
        if etype == "session_start":
            if ev.get("mode"):
                mode = str(ev["mode"])
            if ev.get("task"):
                task = str(ev["task"])
        elif etype == "turn_start":
            turns += 1
            if not task and ev.get("user_input"):
                task = str(ev["user_input"])
        elif etype == "llm_response":
            # 若缺少 session_end（如中途异常），用各步累计兜底
            llm_calls += 1
            u = ev.get("usage") or {}
            prompt_tokens += int(u.get("prompt_tokens") or 0)
            completion_tokens += int(u.get("completion_tokens") or 0)
            total_tokens += int(u.get("total_tokens") or 0)
            cache_hit_tokens += int(u.get("cache_hit_tokens") or 0)
            cache_miss_tokens += int(u.get("cache_miss_tokens") or 0)
            if ev.get("step"):
                steps = max(steps, int(ev["step"]))
        elif etype == "session_end":
            stop_reason = str(ev.get("stop_reason") or "completed")
            if ev.get("steps") is not None:
                steps = int(ev["steps"])
            if ev.get("turns") is not None:
                turns = int(ev["turns"])
            u = ev.get("total_usage") or ev.get("usage")
            if isinstance(u, dict):
                has_session_end_usage = True
                prompt_tokens = int(u.get("prompt_tokens") or 0)
                completion_tokens = int(u.get("completion_tokens") or 0)
                total_tokens = int(u.get("total_tokens") or 0)
                cache_hit_tokens = int(u.get("cache_hit_tokens") or 0)
                cache_miss_tokens = int(u.get("cache_miss_tokens") or 0)
            if ev.get("total_llm_calls") is not None:
                llm_calls = int(ev["total_llm_calls"])
            elif ev.get("llm_calls") is not None:
                llm_calls = int(ev["llm_calls"])
            cost_obj = ev.get("cost")
            if isinstance(cost_obj, dict) and "total_cost" in cost_obj:
                total_cost_cny = float(cost_obj.get("total_cost") or 0.0)
                cache_savings_cny = float(cost_obj.get("cache_savings") or 0.0)

    if total_cost_cny == 0.0 and total_tokens > 0:
        est = estimate_cost(
            TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                cache_hit_tokens=cache_hit_tokens,
                cache_miss_tokens=cache_miss_tokens,
            ),
            pricing,
        )
        total_cost_cny = est.total_cost
        cache_savings_cny = est.cache_savings

    cache_hit_rate = (cache_hit_tokens / prompt_tokens) if prompt_tokens > 0 else 0.0
    if not stop_reason:
        stop_reason = "completed" if has_session_end_usage else "running_or_incomplete"

    return TraceSummaryItem(
        session_id=session_id,
        started_at=str(started_at) if started_at else None,
        mode=mode,
        task=task,
        turns=max(1, turns),
        steps=steps,
        llm_calls=llm_calls,
        total_tokens=total_tokens,
        cache_hit_tokens=cache_hit_tokens,
        cache_hit_rate=round(cache_hit_rate, 4),
        total_cost_cny=round(total_cost_cny, 6),
        cache_savings_cny=round(cache_savings_cny, 6),
        stop_reason=stop_reason,
        event_count=len(events),
    )


def scan_trace_summaries(traces_dir: Path, pricing: PricingConfig) -> list[TraceSummaryItem]:
    if not traces_dir.exists():
        return []
    files = sorted(traces_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    items: list[TraceSummaryItem] = []
    for path in files:
        summary = _parse_trace_summary(path, pricing)
        if summary is not None:
            items.append(summary)
    return items


@router.get("/api/traces", response_model=list[TraceSummaryItem])
async def list_traces(request: Request) -> list[TraceSummaryItem]:
    state: WebAppState = request.app.state.web_state
    return scan_trace_summaries(state.traces_dir, state.config.pricing)


@router.get("/api/traces/{session_id}", response_model=TraceDetailResponse)
async def get_trace_detail(request: Request, session_id: str) -> TraceDetailResponse:
    if not _SAFE_SESSION_ID_RE.match(session_id):
        raise HTTPException(status_code=400, detail="非法的 session_id 格式")

    state: WebAppState = request.app.state.web_state
    path = (state.traces_dir / f"{session_id}.jsonl").resolve()
    if state.traces_dir.resolve() not in path.parents:
        raise HTTPException(status_code=400, detail="非法的 session_id 路径")
    if not path.exists():
        raise HTTPException(status_code=404, detail=f"未找到会话轨迹: {session_id}")

    events = [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    return TraceDetailResponse(session_id=session_id, event_count=len(events), events=events)


@router.get("/api/stats/overview", response_model=OverviewStatsResponse)
async def get_overview_stats(request: Request) -> OverviewStatsResponse:
    state: WebAppState = request.app.state.web_state
    store = state.store
    total_jobs = store.count_jobs() if store is not None else 0
    status_counts = store.get_status_counts() if store is not None else {}

    traces = scan_trace_summaries(state.traces_dir, state.config.pricing)
    total_tokens = sum(t.total_tokens for t in traces)
    total_cost = sum(t.total_cost_cny for t in traces)
    total_savings = sum(t.cache_savings_cny for t in traces)
    rates = [t.cache_hit_rate for t in traces if t.total_tokens > 0]
    avg_rate = (sum(rates) / len(rates)) if rates else 0.0

    return OverviewStatsResponse(
        total_jobs=total_jobs,
        status_counts=status_counts,
        total_sessions=len(traces),
        total_tokens=total_tokens,
        total_cost_cny=round(total_cost, 6),
        total_cache_savings_cny=round(total_savings, 6),
        avg_cache_hit_rate=round(avg_rate, 4),
        daily_usage=DailyUsageSummary.model_validate(state.get_daily_usage_dict()),
    )
