"""学习经验与面经知识的数据模型（Pydantic v2）。"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator

ExperienceCategory = Literal["roadmap", "project", "interview", "tips"]
ExperienceStatus = Literal["unread", "reading", "starred", "archived"]

CATEGORY_LABELS: dict[ExperienceCategory, str] = {
    "roadmap": "学习路线",
    "project": "项目实战",
    "interview": "面试真题",
    "tips": "避坑复盘",
}


def _clean_str_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        parts = [s.strip() for s in re.split(r"[,，、;/；\n]+", value) if s.strip()]
        return parts
    if isinstance(value, list):
        return [str(s).strip() for s in value if str(s).strip()]
    return []


class ExperienceItem(BaseModel):
    """抽取 LLM 输出的单篇干货结构化知识卡片。"""

    category: ExperienceCategory
    topic: str
    title: str
    target_audience: str | None = None
    summary: str
    key_points: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    resources: list[str] = Field(default_factory=list)
    pitfalls: list[str] = Field(default_factory=list)

    # 防编造字段
    evidence: str
    confidence: float = Field(default=0.85, ge=0.0, le=1.0)

    @field_validator("target_audience", mode="before")
    @classmethod
    def _empty_to_none(cls, value: object) -> object:
        if isinstance(value, str):
            s = value.strip()
            if not s or s.lower() in {"null", "none", "n/a", "未知", "无"}:
                return None
            return s
        return value

    @field_validator("key_points", "tech_stack", "resources", "pitfalls", mode="before")
    @classmethod
    def _normalize_lists(cls, value: object) -> list[str]:
        return _clean_str_list(value)


class ExperienceExtractionPayload(BaseModel):
    """抽取 LLM 在 JSON 输出模式下返回的顶层对象。"""

    item: ExperienceItem | None = None
    needs_image: bool = False


class ExperienceRecord(ExperienceItem):
    """落库经验知识记录（来源与热度字段全部由代码填写）。"""

    id: int | None = None
    note_id: str
    source_url: str
    note_title: str | None = None
    note_author: str | None = None
    note_published_at: datetime | None = None
    liked_count: int | None = None
    collected_count: int | None = None
    crawled_at: datetime
    needs_image: bool = False
    status: ExperienceStatus = "unread"
