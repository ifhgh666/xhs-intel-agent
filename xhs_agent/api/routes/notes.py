"""本地缓存原帖快照接口（/api/notes/{feed_id}）。

提供免扫码、免跳转的本地原帖全文、标签、配图与带 xsec_token 的直达链接。
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from xhs_agent.api.schemas import NoteImageResponse, NoteSnapshotResponse
from xhs_agent.api.state import WebAppState

router = APIRouter(prefix="/api/notes", tags=["notes"])


@router.get("/{feed_id}", response_model=NoteSnapshotResponse)
async def get_note_snapshot(request: Request, feed_id: str) -> NoteSnapshotResponse:
    state: WebAppState = request.app.state.web_state
    if state.store is None:
        raise HTTPException(status_code=503, detail="数据库尚未初始化")
    snap = state.store.get_note_snapshot(feed_id)
    if snap is None:
        raise HTTPException(status_code=404, detail=f"未找到本地缓存笔记: {feed_id}")
    return NoteSnapshotResponse(
        feed_id=snap["feed_id"],
        title=snap["title"],
        author=snap["author"],
        ip_location=snap["ip_location"],
        published_at=snap["published_at"],
        desc=snap["desc"],
        tags=snap["tags"],
        images=[NoteImageResponse.model_validate(img) for img in snap["images"]],
        source_url=snap["source_url"],
        open_url=snap["open_url"],
        has_detail=snap["has_detail"],
    )
