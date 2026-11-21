"""岗位情报库查询、状态管理、删除与 Excel 导出接口（/api/jobs/*）。"""

from __future__ import annotations

import tempfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from fastapi.responses import FileResponse

from xhs_agent.api.schemas import (
    DeleteJobResponse,
    JobFacetsResponse,
    JobItemResponse,
    JobListResponse,
    JobStatusUpdateRequest,
)
from xhs_agent.api.state import WebAppState
from xhs_agent.jobs.models import JobRecord, JobStatus
from xhs_agent.jobs.tools import export_jobs_to_excel
from xhs_agent.xhs.cache import build_open_url

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _get_store(request: Request):
    state: WebAppState = request.app.state.web_state
    if state.store is None:
        raise HTTPException(status_code=503, detail="数据库尚未初始化")
    return state.store


def _to_item(record: JobRecord, xsec_token: str | None = None) -> JobItemResponse:
    assert record.id is not None
    return JobItemResponse(
        id=record.id,
        company=record.company,
        position=record.position,
        city=record.city,
        education=record.education,
        graduation_year=record.graduation_year,
        skills=list(record.skills),
        internship_duration=record.internship_duration,
        attendance_days=record.attendance_days,
        salary=record.salary,
        deadline=record.deadline,
        apply_method=record.apply_method,
        application_url=record.application_url,
        evidence=record.evidence,
        confidence=record.confidence,
        note_id=record.note_id,
        source_url=record.source_url,
        open_url=build_open_url(record.note_id, xsec_token) if record.note_id else record.source_url,
        note_title=record.note_title,
        note_author=record.note_author,
        note_published_at=record.note_published_at,
        crawled_at=record.crawled_at,
        needs_image=record.needs_image,
        maybe_expired=record.maybe_expired,
        extra_sources=list(record.extra_sources),
        status=record.status,
    )


@router.get("", response_model=JobListResponse)
async def list_jobs(
    request: Request,
    city: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    graduation_year: str | None = Query(default=None),
    since: str | None = Query(default=None),
    status: JobStatus | None = Query(default=None),
    only_fresh: bool = Query(default=False, description="是否仅看 60 天内未标记过期的岗位"),
    needs_image: bool | None = Query(default=None, description="按是否需看图筛选"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=200),
) -> JobListResponse:
    store = _get_store(request)
    res = store.query_jobs_paginated(
        city=city,
        keyword=keyword,
        graduation_year=graduation_year,
        since=since,
        status=status,
        only_fresh=only_fresh,
        needs_image=needs_image,
        page=page,
        page_size=page_size,
        include_facets=True,
    )
    tokens = store.get_xsec_tokens([j.note_id for j in res["items"]])
    return JobListResponse(
        total=res["total"],
        page=res["page"],
        page_size=res["page_size"],
        items=[_to_item(j, tokens.get(j.note_id)) for j in res["items"]],
        facets=JobFacetsResponse.model_validate(res["facets"]),
    )


@router.get("/export")
async def export_jobs_excel(
    request: Request,
    background_tasks: BackgroundTasks,
    city: str | None = Query(default=None),
    keyword: str | None = Query(default=None),
    graduation_year: str | None = Query(default=None),
    since: str | None = Query(default=None),
    status: JobStatus | None = Query(default=None),
    only_fresh: bool = Query(default=False),
    needs_image: bool | None = Query(default=None),
) -> FileResponse:
    store = _get_store(request)
    jobs = store.query_jobs(
        city=city,
        keyword=keyword,
        graduation_year=graduation_year,
        since=since,
        status=status,
        only_fresh=only_fresh,
        needs_image=needs_image,
        limit=500,
    )
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    tmp = tempfile.NamedTemporaryFile(prefix="xhs_jobs_", suffix=".xlsx", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()

    export_jobs_to_excel(jobs, tmp_path)
    background_tasks.add_task(tmp_path.unlink, True)

    return FileResponse(
        path=str(tmp_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"xhs_internships_{ts}.xlsx",
    )


@router.get("/{job_id}", response_model=JobItemResponse)
async def get_job_detail(request: Request, job_id: int) -> JobItemResponse:
    store = _get_store(request)
    record = store.get_job(job_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"未找到 ID={job_id} 的岗位记录")
    tokens = store.get_xsec_tokens([record.note_id])
    return _to_item(record, tokens.get(record.note_id))


@router.patch("/{job_id}", response_model=JobItemResponse)
async def update_job_status(
    request: Request,
    job_id: int,
    body: JobStatusUpdateRequest,
) -> JobItemResponse:
    store = _get_store(request)
    updated_ok = store.update_job_status(job_id, body.status)
    if not updated_ok:
        raise HTTPException(status_code=404, detail=f"未找到 ID={job_id} 的岗位记录")
    record = store.get_job(job_id)
    assert record is not None
    tokens = store.get_xsec_tokens([record.note_id])
    return _to_item(record, tokens.get(record.note_id))


@router.delete("/{job_id}", response_model=DeleteJobResponse)
async def delete_job(request: Request, job_id: int) -> DeleteJobResponse:
    store = _get_store(request)
    deleted = store.delete_job(job_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"未找到 ID={job_id} 的岗位记录")
    return DeleteJobResponse(deleted=True, id=job_id)
