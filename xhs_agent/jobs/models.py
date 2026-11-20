"""岗位抽取与存储的数据模型（Pydantic v2）及时效判断工具。

对照 docs/design.md 第 10 节：
- Internship：抽取 LLM 输出的单条岗位结构，原文没有就为 None，强制带 evidence 与 confidence
- JobRecord：入库完整记录，来源字段（note_id / source_url / note_published_at / crawled_at）
  全部由代码填写，绝不交给 LLM 生成
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from pydantic import BaseModel, Field, field_validator

JobStatus = Literal["new", "interested", "applied", "rejected", "expired"]


class Internship(BaseModel):
    """单条实习岗位结构化信息（原文没有就为 None）。"""

    company: str | None = None
    position: str | None = None
    city: str | None = None
    education: str | None = None
    graduation_year: str | None = None  # 保留原文，如 "2027/2028届"
    skills: list[str] = Field(default_factory=list)
    internship_duration: str | None = None
    attendance_days: str | None = None
    salary: str | None = None
    deadline: str | None = None
    apply_method: str | None = None  # 邮箱 / 链接 / 内推码 / 私信
    application_url: str | None = None

    # 防编造
    evidence: str  # 支撑本条信息的原文片段
    confidence: float = Field(default=0.8, ge=0.0, le=1.0)

    @field_validator(
        "company",
        "position",
        "city",
        "education",
        "graduation_year",
        "internship_duration",
        "attendance_days",
        "salary",
        "deadline",
        "apply_method",
        "application_url",
        mode="before",
    )
    @classmethod
    def _empty_str_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped or stripped.lower() in {"null", "none", "n/a", "未知", "无"}:
                return None
            return stripped
        return value

    @field_validator("skills", mode="before")
    @classmethod
    def _normalize_skills(cls, value: object) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            items = [s.strip() for s in re.split(r"[,，、;/；\n]+", value) if s.strip()]
            return items
        if isinstance(value, list):
            return [str(s).strip() for s in value if str(s).strip()]
        return []


class ExtractionPayload(BaseModel):
    """抽取 LLM 在 JSON 输出模式下返回的顶层结构。"""

    jobs: list[Internship] = Field(default_factory=list)
    needs_image: bool = False


class JobRecord(Internship):
    """落库岗位记录（来源字段全部由代码填充）。"""

    id: int | None = None
    note_id: str
    source_url: str
    note_title: str | None = None
    note_author: str | None = None
    note_published_at: datetime | None = None
    crawled_at: datetime
    needs_image: bool = False

    # 去重与时效扩展字段
    dedup_key: str | None = None
    extra_sources: list[str] = Field(default_factory=list)
    maybe_expired: bool = False

    # 业务状态
    status: JobStatus = "new"


def is_note_stale(
    published_at: datetime | None,
    *,
    now: datetime | None = None,
    stale_days: int = 60,
) -> bool:
    """笔记发布时间超过 stale_days 天（默认 60 天）时返回 True。"""
    if published_at is None:
        return False
    ref = now or datetime.now(timezone.utc)
    pub = published_at if published_at.tzinfo else published_at.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - pub) > timedelta(days=stale_days)


_FULL_DATE_RE = re.compile(r"(20\d{2})\s*[年./\-]\s*(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?")
_MONTH_DAY_RE = re.compile(r"(\d{1,2})\s*[月./\-]\s*(\d{1,2})\s*日?")


def parse_deadline_date(deadline: str | None, *, reference_date: date | None = None) -> date | None:
    """尽力解析常见中文/ISO 截止日期文本；无法可靠解析时返回 None。"""
    if not deadline:
        return None
    text = deadline.strip()
    m = _FULL_DATE_RE.search(text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None

    m2 = _MONTH_DAY_RE.search(text)
    if m2 and reference_date is not None:
        try:
            return date(reference_date.year, int(m2.group(1)), int(m2.group(2)))
        except ValueError:
            return None
    return None


def is_deadline_expired(deadline: str | None, *, today: date | None = None) -> bool:
    """当岗位存在可解析的 deadline 且早于 today 时返回 True。"""
    ref = today or date.today()
    parsed = parse_deadline_date(deadline, reference_date=ref)
    if parsed is None:
        return False
    return parsed < ref
