"""多轮对话会话、异步任务执行、取消与 SSE 实时推流接口（/api/sessions/*）。"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from xhs_agent.api.schemas import (
    CancelRunResponse,
    CreateSessionRequest,
    SendMessageRequest,
    SendMessageResponse,
    SessionDetailResponse,
    SessionSummaryResponse,
)
from xhs_agent.api.task_manager import ActiveTaskConflictError, TaskManager

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


def _get_manager(request: Request) -> TaskManager:
    return request.app.state.task_manager


@router.post("", response_model=SessionSummaryResponse, status_code=201)
async def create_session(
    request: Request,
    body: CreateSessionRequest | None = None,
) -> SessionSummaryResponse:
    mgr = _get_manager(request)
    req = body or CreateSessionRequest()
    try:
        managed = mgr.create_session(
            max_steps=req.max_steps,
            max_search=req.max_search,
            max_detail=req.max_detail,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return SessionSummaryResponse.model_validate(managed.to_summary_dict())


@router.get("", response_model=list[SessionSummaryResponse])
async def list_sessions(request: Request) -> list[SessionSummaryResponse]:
    mgr = _get_manager(request)
    return [SessionSummaryResponse.model_validate(item) for item in mgr.list_sessions()]


@router.get("/{session_id}", response_model=SessionDetailResponse)
async def get_session_detail(request: Request, session_id: str) -> SessionDetailResponse:
    mgr = _get_manager(request)
    managed = mgr.get_session(session_id)
    if managed is None:
        raise HTTPException(status_code=404, detail=f"未找到会话: {session_id}")
    return SessionDetailResponse.model_validate(managed.to_detail_dict())


@router.post("/{session_id}/messages", response_model=SendMessageResponse, status_code=202)
async def send_message(
    request: Request,
    session_id: str,
    body: SendMessageRequest,
) -> SendMessageResponse:
    mgr = _get_manager(request)
    try:
        run = await mgr.start_turn(
            session_id,
            body.content,
            max_steps=body.max_steps,
            max_search=body.max_search,
            max_detail=body.max_detail,
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ActiveTaskConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return SendMessageResponse(
        run_id=run.run_id,
        session_id=run.session_id,
        turn=run.turn,
        status=run.status,
    )


@router.get("/{session_id}/events")
async def stream_session_events(
    request: Request,
    session_id: str,
    run_id: str | None = Query(default=None, description="可选指定轮次 run_id，默认订阅最新轮次"),
) -> StreamingResponse:
    mgr = _get_manager(request)
    if mgr.get_session(session_id) is None:
        raise HTTPException(status_code=404, detail=f"未找到会话: {session_id}")

    return StreamingResponse(
        mgr.subscribe_events(session_id, run_id=run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/{session_id}/cancel", response_model=CancelRunResponse)
async def cancel_session_run(request: Request, session_id: str) -> CancelRunResponse:
    mgr = _get_manager(request)
    try:
        cancelled, run_id = mgr.cancel_session(session_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return CancelRunResponse(cancelled=cancelled, session_id=session_id, run_id=run_id)
