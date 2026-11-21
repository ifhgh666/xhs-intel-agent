"""FastAPI 请求与响应 Pydantic Schema 定义。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from xhs_agent.jobs.models import JobStatus


class QuotaItem(BaseModel):
    used: int
    limit: int


class DailyUsageSummary(BaseModel):
    day: str
    search: QuotaItem
    detail: QuotaItem


class SystemStatusResponse(BaseModel):
    mcp_url: str
    mcp_connected: bool
    logged_in: bool
    login_message: str = ""
    mcp_error: str | None = None
    validated_tools: list[str] = Field(default_factory=list)
    db_path: str
    db_jobs_count: int
    active_session_id: str | None = None
    daily_usage: DailyUsageSummary
    config_summary: dict[str, Any] = Field(default_factory=dict)


class LoginQrcodeResponse(BaseModel):
    image_base64: str | None = None
    hint_text: str = ""


class ReconnectResponse(BaseModel):
    mcp_connected: bool
    logged_in: bool
    login_message: str = ""
    validated_tools: list[str] = Field(default_factory=list)
    error: str | None = None


class JobItemResponse(BaseModel):
    id: int
    company: str | None = None
    position: str | None = None
    city: str | None = None
    education: str | None = None
    graduation_year: str | None = None
    skills: list[str] = Field(default_factory=list)
    internship_duration: str | None = None
    attendance_days: str | None = None
    salary: str | None = None
    deadline: str | None = None
    apply_method: str | None = None
    application_url: str | None = None
    evidence: str
    confidence: float
    note_id: str
    source_url: str
    note_title: str | None = None
    note_author: str | None = None
    note_published_at: datetime | None = None
    crawled_at: datetime
    needs_image: bool = False
    maybe_expired: bool = False
    extra_sources: list[str] = Field(default_factory=list)
    status: JobStatus = "new"


class JobFacetsResponse(BaseModel):
    cities: list[str] = Field(default_factory=list)
    status_counts: dict[str, int] = Field(default_factory=dict)


class JobListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[JobItemResponse] = Field(default_factory=list)
    facets: JobFacetsResponse = Field(default_factory=JobFacetsResponse)


class JobStatusUpdateRequest(BaseModel):
    status: JobStatus


class DeleteJobResponse(BaseModel):
    deleted: bool
    id: int


class TraceSummaryItem(BaseModel):
    session_id: str
    started_at: str | None = None
    mode: str = "single"
    task: str = ""
    turns: int = 1
    steps: int = 0
    llm_calls: int = 0
    total_tokens: int = 0
    cache_hit_tokens: int = 0
    cache_hit_rate: float = 0.0
    total_cost_cny: float = 0.0
    cache_savings_cny: float = 0.0
    stop_reason: str = ""
    event_count: int = 0


class TraceDetailResponse(BaseModel):
    session_id: str
    event_count: int
    events: list[dict[str, Any]] = Field(default_factory=list)


class OverviewStatsResponse(BaseModel):
    total_jobs: int
    status_counts: dict[str, int] = Field(default_factory=dict)
    total_sessions: int
    total_tokens: int
    total_cost_cny: float
    total_cache_savings_cny: float
    avg_cache_hit_rate: float
    daily_usage: DailyUsageSummary
