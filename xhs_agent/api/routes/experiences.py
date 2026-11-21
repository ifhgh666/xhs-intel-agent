"""经验知识库查询、状态管理、删除与 Markdown/Excel 导出接口（/api/experiences/*）。"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse

from xhs_agent.api.schemas import (
    DeleteExperienceResponse,
    ExperienceFacetsResponse,
    ExperienceItemResponse,
    ExperienceListResponse,
    ExperienceStatusUpdateRequest,
)
from xhs_agent.api.state import WebAppState
from xhs_agent.knowledge.models import (
    CATEGORY_LABELS,
    ExperienceCategory,
    ExperienceRecord,
    ExperienceStatus,
)
from xhs_agent.knowledge.tools import (
    export_experiences_to_excel,
    export_experiences_to_markdown,
)

router = APIRouter(prefix="/api/experiences", tags=["experiences"])


def _get_store(request: Request):
    state: WebAppState = request.app.state.web_state
    if state.store is None:
        raise HTTPException(status_code=503, detail="数据库尚未初始化")
    return state.store


def _to_item(record: ExperienceRecord) -> ExperienceItemResponse:
    assert record.id is not None
    return ExperienceItemResponse(
        id=record.id,
        note_id=record.note_id,
        category=record.category,
        category_label=CATEGORY_LABELS.get(record.category, record.category),
        topic=record.topic,
        title=record.title,
        target_audience=record.target_audience,
        summary=record.summary,
        key_points=list(record.key_points),
        tech_stack=list(record.tech_stack),
        resources=list(record.resources),
        pitfalls=list(record.pitfalls),
        evidence=record.evidence,
        confidence=record.confidence,
        source_url=record.source_url,
        note_title=record.note_title,
        note_author=record.note_author,
        note_published_at=record.note_published_at,
        liked_count=record.liked_count,
        collected_count=record.collected_count,
        crawled_at=record.crawled_at,
        needs_image=record.needs_image,
        status=record.status,
    )


@router.get("", response_model=ExperienceListResponse)
async def list_experiences(
    request: Request,
    category: ExperienceCategory | None = Query(default=None),
    topic: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    status: ExperienceStatus | None = Query(default=None),
    needs_image: bool | None = Query(default=None),
    sort_by: Literal["latest", "likes"] = Query(default="latest"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> ExperienceListResponse:
    store = _get_store(request)
    res = store.query_experiences_paginated(
        category=category,
        topic=topic,
        keyword=keyword,
        status=status,
        needs_image=needs_image,
        sort_by=sort_by,
        page=page,
        page_size=page_size,
        include_facets=True,
    )
    return ExperienceListResponse(
        total=res["total"],
        page=res["page"],
        page_size=res["page_size"],
        items=[_to_item(e) for e in res["items"]],
        facets=ExperienceFacetsResponse.model_validate(res["facets"]),
    )


@router.get("/export")
async def export_experiences_file(
    request: Request,
    background_tasks: BackgroundTasks,
    format: Literal["md", "markdown", "xlsx", "excel"] = Query(default="md"),
    category: ExperienceCategory | None = Query(default=None),
    topic: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    status: ExperienceStatus | None = Query(default=None),
    needs_image: bool | None = Query(default=None),
    sort_by: Literal["latest", "likes"] = Query(default="latest"),
) -> FileResponse:
    store = _get_store(request)
    items = store.query_experiences(
        category=category,
        topic=topic,
        keyword=keyword,
        status=status,
        needs_image=needs_image,
        sort_by=sort_by,
        limit=500,
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    is_md = format in {"md", "markdown"}
    suffix = ".md" if is_md else ".xlsx"
    media_type = (
        "text/markdown; charset=utf-8"
        if is_md
        else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    tmp = tempfile.NamedTemporaryFile(prefix="xhs_exp_", suffix=suffix, delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    if is_md:
        export_experiences_to_markdown(items, tmp_path)
    else:
        export_experiences_to_excel(items, tmp_path)
    background_tasks.add_task(tmp_path.unlink, True)

    return FileResponse(
        path=str(tmp_path),
        media_type=media_type,
        filename=f"xhs_experiences_{ts}{suffix}",
    )


@router.get("/{exp_id}", response_model=ExperienceItemResponse)
async def get_experience_detail(request: Request, exp_id: int) -> ExperienceItemResponse:
    store = _get_store(request)
    record = store.get_experience(exp_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"未找到 ID={exp_id} 的经验记录")
    return _to_item(record)


@router.patch("/{exp_id}", response_model=ExperienceItemResponse)
async def update_experience_status(
    request: Request,
    exp_id: int,
    body: ExperienceStatusUpdateRequest,
) -> ExperienceItemResponse:
    store = _get_store(request)
    ok = store.update_experience_status(exp_id, body.status)
    if not ok:
        raise HTTPException(status_code=404, detail=f"未找到 ID={exp_id} 的经验记录")
    record = store.get_experience(exp_id)
    assert record is not None
    return _to_item(record)


@router.delete("/{exp_id}", response_model=DeleteExperienceResponse)
async def delete_experience(request: Request, exp_id: int) -> DeleteExperienceResponse:
    store = _get_store(request)
    deleted = store.delete_experience(exp_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"未找到 ID={exp_id} 的经验记录")
    return DeleteExperienceResponse(deleted=True, id=exp_id)
