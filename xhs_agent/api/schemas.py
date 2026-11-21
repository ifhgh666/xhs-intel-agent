"""FastAPI 请求与响应 Pydantic Schema 定义。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from xhs_agent.jobs.models import JobStatus
from xhs_agent.knowledge.models import ExperienceCategory, ExperienceStatus

AgentMode = Literal["auto", "jobs", "experience"]


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
    db_experiences_count: int = 0
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
    open_url: str = ""
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
    total_experiences: int = 0
    experience_category_counts: dict[str, int] = Field(default_factory=dict)
    total_sessions: int
    total_tokens: int
    total_cost_cny: float
    total_cache_savings_cny: float
    avg_cache_hit_rate: float
    daily_usage: DailyUsageSummary


class ExperienceItemResponse(BaseModel):
    id: int
    note_id: str
    category: ExperienceCategory
    category_label: str
    topic: str
    title: str
    target_audience: str | None = None
    summary: str
    key_points: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)
    evidence: str
    confidence: float
    source_url: str
    open_url: str = ""
    note_title: str | None = None
    note_author: str | None = None
    note_published_at: datetime | None = None
    liked_count: int | None = None
    collected_count: int | None = None
    crawled_at: datetime
    needs_image: bool = False
    status: ExperienceStatus = "unread"


class ExperienceFacetsResponse(BaseModel):
    category_counts: dict[str, int] = Field(default_factory=dict)
    status_counts: dict[str, int] = Field(default_factory=dict)
    topics: list[str] = Field(default_factory=list)


class ExperienceListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[ExperienceItemResponse] = Field(default_factory=list)
    facets: ExperienceFacetsResponse = Field(default_factory=ExperienceFacetsResponse)


class ExperienceStatusUpdateRequest(BaseModel):
    status: ExperienceStatus


class DeleteExperienceResponse(BaseModel):
    deleted: bool
    id: int


class CreateSessionRequest(BaseModel):
    max_steps: int | None = Field(default=None, ge=1, le=50)
    max_search: int | None = Field(default=None, ge=1, le=15)
    max_detail: int | None = Field(default=None, ge=1, le=50)
    mode: AgentMode = "auto"


class SendMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=2000)
    max_steps: int | None = Field(default=None, ge=1, le=50)
    max_search: int | None = Field(default=None, ge=1, le=15)
    max_detail: int | None = Field(default=None, ge=1, le=50)
    mode: AgentMode | None = None


class SendMessageResponse(BaseModel):
    run_id: str
    session_id: str
    turn: int
    status: str


class CancelRunResponse(BaseModel):
    cancelled: bool
    session_id: str
    run_id: str | None = None


class SessionMessageItem(BaseModel):
    role: str
    content: str
    turn: int
    created_at: str
    meta: dict[str, Any] = Field(default_factory=dict)


class SessionSummaryResponse(BaseModel):
    session_id: str
    created_at: str
    status: str
    turns: int
    total_llm_calls: int
    total_tokens: int
    total_cost_cny: float
    last_message: str = ""
    latest_run_id: str | None = None
    trace_path: str


class SessionDetailResponse(SessionSummaryResponse):
    total_usage: dict[str, int] = Field(default_factory=dict)
    cost: dict[str, Any] = Field(default_factory=dict)
    tool_calls: dict[str, int] = Field(default_factory=dict)
    messages: list[SessionMessageItem] = Field(default_factory=list)
    latest_events: list[dict[str, Any]] = Field(default_factory=list)


class NoteImageResponse(BaseModel):
    url: str
    preview_url: str
    width: int | None = None
    height: int | None = None


class NoteSnapshotResponse(BaseModel):
    feed_id: str
    title: str
    author: str | None = None
    ip_location: str | None = None
    published_at: datetime | None = None
    desc: str = ""
    tags: list[str] = Field(default_factory=list)
    images: list[NoteImageResponse] = Field(default_factory=list)
    source_url: str
    open_url: str
    has_detail: bool = False
