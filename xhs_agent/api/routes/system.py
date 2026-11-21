"""系统状态、小红书扫码登录与 MCP 重连接口（/api/system/*）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from xhs_agent.api.schemas import (
    DailyUsageSummary,
    LoginQrcodeResponse,
    ReconnectResponse,
    SystemStatusResponse,
)
from xhs_agent.api.state import WebAppState

router = APIRouter(prefix="/api/system", tags=["system"])


def _get_state(request: Request) -> WebAppState:
    return request.app.state.web_state


@router.get("/status", response_model=SystemStatusResponse)
async def get_system_status(
    request: Request,
    refresh: bool = Query(default=False, description="是否主动向 MCP 刷新小红书登录状态（扫码轮询时开启）"),
) -> SystemStatusResponse:
    state = _get_state(request)
    if refresh and state.mcp_connected:
        await state.refresh_login_status()

    db_count = state.store.count_jobs() if state.store is not None else 0
    return SystemStatusResponse(
        mcp_url=state.config.mcp.url,
        mcp_connected=state.mcp_connected,
        logged_in=state.logged_in,
        login_message=state.login_message,
        mcp_error=state.mcp_error,
        validated_tools=list(state.validated_tools),
        db_path=str(state.db_path),
        db_jobs_count=db_count,
        active_session_id=state.active_session_id,
        daily_usage=DailyUsageSummary.model_validate(state.get_daily_usage_dict()),
        config_summary={
            "agent_model": state.config.llm.agent_model,
            "extract_model": state.config.llm.extract_model,
            "max_steps": state.config.budget.max_steps,
            "max_search_calls": state.config.budget.max_search_calls,
            "max_detail_calls": state.config.budget.max_detail_calls,
            "min_interval_sec": state.config.rate_limit.min_interval_sec,
            "jitter_sec": state.config.rate_limit.jitter_sec,
            "pricing": state.config.pricing.model_dump(),
        },
    )


@router.post("/mcp/reconnect", response_model=ReconnectResponse)
async def reconnect_mcp(request: Request) -> ReconnectResponse:
    state = _get_state(request)
    res = await state.reconnect_mcp()
    return ReconnectResponse.model_validate(res)


@router.post("/login/qrcode", response_model=LoginQrcodeResponse)
async def fetch_login_qrcode(request: Request) -> LoginQrcodeResponse:
    state = _get_state(request)
    if not state.mcp_connected:
        await state.reconnect_mcp()
    if not state.mcp_connected or state.adapter is None:
        raise HTTPException(
            status_code=503,
            detail=f"MCP Server 未连接（{state.mcp_error or state.config.mcp.url}），请先启动 xiaohongshu-mcp 容器。",
        )
    try:
        qr = await state.adapter.get_login_qrcode()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"获取登录二维码失败: {exc}") from exc
    return LoginQrcodeResponse(
        image_base64=qr.get("image_base64"),
        hint_text=str(qr.get("hint_text") or ""),
    )
