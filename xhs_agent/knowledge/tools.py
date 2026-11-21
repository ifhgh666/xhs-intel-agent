"""学习经验与面经业务工具集：extract_and_save_experience / query_experiences / mark_experience / export_experiences。"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from xhs_agent.config import ROOT_DIR
from xhs_agent.knowledge.extract import ExperienceExtractor
from xhs_agent.knowledge.models import (
    CATEGORY_LABELS,
    ExperienceCategory,
    ExperienceRecord,
    ExperienceStatus,
)
from xhs_agent.xhs.adapter import UnknownNoteError, XhsAdapter, _extract_detail_fields

if TYPE_CHECKING:
    from xhs_agent.jobs.store import JobStore

DEFAULT_EXPORT_DIR = ROOT_DIR / "data"


def _parse_int_count(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, int):
        return raw
    s = str(raw).strip().lower()
    try:
        if s.endswith("万") or s.endswith("w"):
            return int(float(s[:-1]) * 10000)
        if s.endswith("k"):
            return int(float(s[:-1]) * 1000)
        return int(float(s))
    except ValueError:
        return None


def _format_dt_str(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _exp_to_compact_dict(exp: ExperienceRecord) -> dict[str, Any]:
    return {
        "id": exp.id,
        "category": exp.category,
        "category_label": CATEGORY_LABELS.get(exp.category, exp.category),
        "topic": exp.topic,
        "title": exp.title,
        "target_audience": exp.target_audience,
        "summary": exp.summary,
        "key_points": exp.key_points,
        "tech_stack": exp.tech_stack,
        "resources": exp.resources,
        "pitfalls": exp.pitfalls,
        "liked_count": exp.liked_count,
        "collected_count": exp.collected_count,
        "note_published_at": _format_dt_str(exp.note_published_at) or None,
        "source_url": exp.source_url,
        "needs_image": exp.needs_image,
        "status": exp.status,
    }


def export_experiences_to_markdown(items: list[ExperienceRecord], output_path: Path | str) -> Path:
    """将经验知识库条目导出为结构化 Markdown 学习笔记（适合 Obsidian / Notion 复习）。"""
    path = Path(output_path)
    if path.suffix.lower() != ".md":
        path = path.with_suffix(".md")
    path.parent.mkdir(parents=True, exist_ok=True)

    grouped: dict[str, list[ExperienceRecord]] = {
        "roadmap": [],
        "project": [],
        "interview": [],
        "tips": [],
    }
    for item in items:
        grouped.setdefault(item.category, []).append(item)

    lines: list[str] = [
        "# 小红书 AI / Agent 学习经验与面经汇编",
        "",
        f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M')} · 共收录 **{len(items)}** 篇干货（全部经原文防编造校验）",
        "",
    ]

    for cat_key in ("roadmap", "project", "interview", "tips"):
        cat_items = grouped.get(cat_key) or []
        if not cat_items:
            continue
        label = CATEGORY_LABELS.get(cat_key, cat_key)  # type: ignore[arg-type]
        lines.append(f"## {label}（{len(cat_items)} 篇）")
        lines.append("")

        for idx, exp in enumerate(cat_items, start=1):
            lines.append(f"### {idx}. {exp.title}")
            meta_parts = [f"主题：`{exp.topic}`"]
            if exp.target_audience:
                meta_parts.append(f"适用：{exp.target_audience}")
            if exp.liked_count is not None:
                meta_parts.append(f"点赞：{exp.liked_count}")
            if exp.note_published_at:
                meta_parts.append(f"发布：{_format_dt_str(exp.note_published_at)}")
            if exp.needs_image:
                meta_parts.append("提示：原帖含重要图片")
            lines.append("- " + " | ".join(meta_parts))
            if exp.tech_stack:
                lines.append("- 技术栈：" + " / ".join(f"`{t}`" for t in exp.tech_stack))
            lines.append(f"- 来源：[{exp.note_title or exp.source_url}]({exp.source_url})")
            lines.append("")
            lines.append(f"**核心总结**：{exp.summary}")
            lines.append("")
            if exp.key_points:
                lines.append("**关键要点 / 题目清单**：")
                for kp in exp.key_points:
                    lines.append(f"- {kp}")
                lines.append("")
            if exp.resources:
                lines.append("**提及资源 / 项目**：")
                for res in exp.resources:
                    lines.append(f"- {res}")
                lines.append("")
            if exp.pitfalls:
                lines.append("**避坑提醒**：")
                for pf in exp.pitfalls:
                    lines.append(f"- {pf}")
                lines.append("")
            lines.append(f"> 原文证据：{exp.evidence}")
            lines.append("")

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def export_experiences_to_excel(items: list[ExperienceRecord], output_path: Path | str) -> Path:
    """将经验知识库导出为规范化 Excel (.xlsx)。"""
    path = Path(output_path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "学习经验与面经"

    headers = [
        ("ID", 8),
        ("分类", 12),
        ("主题", 18),
        ("提炼标题", 32),
        ("适用人群", 16),
        ("核心总结", 44),
        ("关键要点/题目", 50),
        ("技术栈", 24),
        ("提及资源", 30),
        ("避坑提醒", 36),
        ("点赞数", 10),
        ("来源链接", 42),
        ("发布时间", 18),
        ("需看图", 10),
        ("学习状态", 12),
        ("原文证据", 38),
    ]
    header_fill = PatternFill(start_color="312E81", end_color="312E81", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for col_idx, (label, width) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = width

    ws.freeze_panes = "A2"

    for row_idx, exp in enumerate(items, start=2):
        values = [
            exp.id,
            CATEGORY_LABELS.get(exp.category, exp.category),
            exp.topic,
            exp.title,
            exp.target_audience or "",
            exp.summary,
            "\n".join(f"- {p}" for p in exp.key_points),
            ", ".join(exp.tech_stack),
            "\n".join(exp.resources),
            "\n".join(exp.pitfalls),
            exp.liked_count if exp.liked_count is not None else "",
            exp.source_url,
            _format_dt_str(exp.note_published_at),
            "是" if exp.needs_image else "否",
            exp.status,
            exp.evidence,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col_idx == 12 and isinstance(val, str) and val.startswith("http"):
                cell.hyperlink = val
                cell.font = Font(color="0563C1", underline="single")

    wb.save(path)
    return path


class KnowledgeTools:
    """聚合学习经验与面经相关业务工具处理器。"""

    def __init__(
        self,
        *,
        adapter: XhsAdapter,
        store: JobStore,
        extractor: ExperienceExtractor,
        export_dir: Path | str | None = None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.extractor = extractor
        self.export_dir = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR

    async def extract_and_save_experience(self, note: str) -> dict[str, Any]:
        """确定性经验抽取管道：复用笔记详情缓存 → 抽取干货 → 证据校验 → 入 experiences 表 → 返回精简摘要。"""
        short_id = note.strip()
        cached = self.adapter.cache.get(short_id)
        if cached is None:
            raise UnknownNoteError(short_id)

        # 1. 经验笔记级去重（与岗位 processed_at 完全隔离）
        db_note = self.store.get_note(cached.feed_id)
        if db_note is not None and db_note.get("exp_processed_at") is not None:
            existing = self.store.get_experience_by_note(cached.feed_id)
            return {
                "note": short_id,
                "status": "already_processed",
                "created": 0,
                "exp_id": existing.id if existing else None,
                "category": existing.category if existing else None,
                "title": existing.title if existing else cached.title,
                "key_points_count": len(existing.key_points) if existing else 0,
                "needs_image": existing.needs_image if existing else False,
                "source_url": cached.source_url,
                "from_cache": True,
            }

        # 2. 获取笔记详情：优先复用内存 NoteCache 或 SQLite notes.detail_json（跨模式零重复请求！）
        from_cache = False
        if cached.detail is not None:
            raw_detail = cached.detail
            from_cache = True
        elif db_note is not None and db_note.get("detail") is not None:
            raw_detail = db_note["detail"]
            cached = self.adapter.cache.set_detail(short_id, raw_detail)
            from_cache = True
        else:
            cached, raw_detail, from_cache = await self.adapter.fetch_note_detail_raw(short_id)

        self.store.upsert_note(
            feed_id=cached.feed_id,
            xsec_token=cached.xsec_token,
            title=cached.title,
            author=cached.author,
            published_at=cached.published_at,
            raw_feed=cached.raw_feed,
            detail=raw_detail,
        )

        # 3. 独立经验抽取 LLM + 原文防编造校验
        fields = _extract_detail_fields(raw_detail, cached)
        extracted = await self.extractor.extract(
            title=fields["title"],
            desc=fields["desc"],
            image_count=fields["image_count"],
        )

        now_utc = datetime.now(timezone.utc)
        self.store.mark_note_exp_processed(cached.feed_id, processed_at=now_utc)

        if extracted.item is None:
            return {
                "note": short_id,
                "status": "needs_image" if extracted.needs_image else "not_experience",
                "created": 0,
                "dropped_unverified": extracted.dropped_unverified,
                "needs_image": extracted.needs_image,
                "source_url": cached.source_url,
                "from_cache": from_cache,
            }

        # 尝试从 detail 或 NoteCache 读取互动数
        liked_cnt = _parse_int_count(cached.liked_count)
        collected_cnt = _parse_int_count(cached.collected_count)
        if isinstance(raw_detail, dict):
            node = raw_detail.get("data", {}).get("note", {}) if isinstance(raw_detail.get("data"), dict) else {}
            interact = node.get("interactInfo") if isinstance(node, dict) else None
            if isinstance(interact, dict):
                liked_cnt = _parse_int_count(interact.get("likedCount")) or liked_cnt
                collected_cnt = _parse_int_count(interact.get("collectedCount")) or collected_cnt

        record = ExperienceRecord(
            **extracted.item.model_dump(),
            note_id=cached.feed_id,
            source_url=cached.source_url,
            note_title=fields["title"] or cached.title,
            note_author=fields["author"] or cached.author,
            note_published_at=fields["published_dt"] or cached.published_at,
            liked_count=liked_cnt,
            collected_count=collected_cnt,
            crawled_at=now_utc,
            needs_image=extracted.needs_image,
            status="unread",
        )
        saved, created = self.store.upsert_experience(record)
        return {
            "note": short_id,
            "status": "ok" if created else "updated",
            "created": 1 if created else 0,
            "exp_id": saved.id,
            "category": saved.category,
            "category_label": CATEGORY_LABELS.get(saved.category, saved.category),
            "topic": saved.topic,
            "title": saved.title,
            "key_points_count": len(saved.key_points),
            "needs_image": saved.needs_image,
            "source_url": saved.source_url,
            "from_cache": from_cache,
        }

    async def query_experiences(
        self,
        *,
        category: ExperienceCategory | None = None,
        topic: str | None = None,
        keyword: str | None = None,
        status: ExperienceStatus | None = None,
        sort_by: Literal["latest", "likes"] = "latest",
        limit: int = 20,
    ) -> dict[str, Any]:
        records = self.store.query_experiences(
            category=category,
            topic=topic,
            keyword=keyword,
            status=status,
            sort_by=sort_by,
            limit=limit,
        )
        return {
            "count": len(records),
            "experiences": [_exp_to_compact_dict(r) for r in records],
        }

    async def mark_experience(self, exp_id: int, status: ExperienceStatus) -> dict[str, Any]:
        existing = self.store.get_experience(exp_id)
        if existing is None:
            raise ValueError(f"未找到 ID={exp_id} 的经验记录，请先调用 query_experiences 确认 ID。")
        old_status = existing.status
        self.store.update_experience_status(exp_id, status)
        updated = self.store.get_experience(exp_id)
        assert updated is not None
        return {
            "id": updated.id,
            "title": updated.title,
            "category": updated.category,
            "old_status": old_status,
            "status": updated.status,
            "source_url": updated.source_url,
        }

    async def export_experiences(
        self,
        *,
        category: ExperienceCategory | None = None,
        topic: str | None = None,
        keyword: str | None = None,
        status: ExperienceStatus | None = None,
        format: Literal["markdown", "excel"] = "markdown",
        path: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        records = self.store.query_experiences(
            category=category,
            topic=topic,
            keyword=keyword,
            status=status,
            limit=limit,
        )
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        ext = ".md" if format == "markdown" else ".xlsx"
        if path and path.strip():
            target = Path(path.strip())
            if not target.is_absolute():
                target = self.export_dir / target
        else:
            target = self.export_dir / f"xhs_experiences_{ts}{ext}"

        saved = (
            export_experiences_to_markdown(records, target)
            if format == "markdown"
            else export_experiences_to_excel(records, target)
        )
        return {
            "path": str(saved.resolve()),
            "count": len(records),
            "format": format,
        }
