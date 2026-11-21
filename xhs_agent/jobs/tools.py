"""阶段 4 业务工具集：extract_and_save_jobs / query_jobs / export_jobs。

对照 docs/design.md 第 7、9、10 节：
- extract_and_save_jobs(note)：固定确定性管道（查缓存/SQLite 跳过已处理 → 拉详情 → 抽取 LLM → 校验 → 去重 → SQLite upsert → 只返回一行精简摘要给主 LLM，主上下文看不到笔记全文！）
- query_jobs(...)：从 SQLite 按条件查询岗位，返回精简列表
- export_jobs(..., path)：将匹配岗位导出为带格式与来源链接列的 Excel (.xlsx)
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from xhs_agent.config import ROOT_DIR
from xhs_agent.jobs.dedup import make_dedup_key
from xhs_agent.jobs.extract import JobExtractor
from xhs_agent.jobs.models import (
    JobRecord,
    JobStatus,
    is_deadline_expired,
    is_note_stale,
)
from xhs_agent.jobs.store import JobStore
from xhs_agent.xhs.adapter import UnknownNoteError, XhsAdapter, _extract_detail_fields

DEFAULT_EXPORT_DIR = ROOT_DIR / "data"


def _format_dt_str(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.astimezone().strftime("%Y-%m-%d %H:%M")


def _job_to_compact_dict(job: JobRecord) -> dict[str, Any]:
    return {
        "id": job.id,
        "company": job.company,
        "position": job.position,
        "city": job.city,
        "education": job.education,
        "graduation_year": job.graduation_year,
        "skills": job.skills,
        "internship_duration": job.internship_duration,
        "attendance_days": job.attendance_days,
        "salary": job.salary,
        "deadline": job.deadline,
        "apply_method": job.apply_method,
        "application_url": job.application_url,
        "note_published_at": _format_dt_str(job.note_published_at) or None,
        "source_url": job.source_url,
        "extra_sources": job.extra_sources,
        "needs_image": job.needs_image,
        "maybe_expired": job.maybe_expired,
        "status": job.status,
    }


def export_jobs_to_excel(jobs: list[JobRecord], output_path: Path | str) -> Path:
    """将岗位列表导出为规范化的 Excel (.xlsx)，包含来源链接列与时效/看图提示。"""
    path = Path(output_path)
    if path.suffix.lower() != ".xlsx":
        path = path.with_suffix(".xlsx")
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    ws = wb.active
    ws.title = "实习岗位"

    headers = [
        ("ID", 8),
        ("公司/团队", 18),
        ("岗位名称", 26),
        ("城市", 14),
        ("学历要求", 16),
        ("届别要求", 16),
        ("关键技能", 26),
        ("实习时长", 14),
        ("每周出勤", 14),
        ("薪资待遇", 18),
        ("截止时间", 14),
        ("投递方式", 30),
        ("投递链接", 28),
        ("来源链接", 42),
        ("其他来源", 32),
        ("笔记发布时间", 18),
        ("可能已过期", 12),
        ("需看图", 10),
        ("状态", 12),
        ("原文证据", 38),
    ]

    header_fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    header_font = Font(color="FFFFFF", bold=True)

    for col_idx, (label, width) in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=label)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.column_dimensions[cell.column_letter].width = width

    ws.freeze_panes = "A2"

    for row_idx, job in enumerate(jobs, start=2):
        values = [
            job.id,
            job.company or "未具名",
            job.position or "",
            job.city or "",
            job.education or "",
            job.graduation_year or "",
            ", ".join(job.skills),
            job.internship_duration or "",
            job.attendance_days or "",
            job.salary or "",
            job.deadline or "",
            job.apply_method or "",
            job.application_url or "",
            job.source_url,
            "\n".join(job.extra_sources),
            _format_dt_str(job.note_published_at),
            "是" if job.maybe_expired else "否",
            "是" if job.needs_image else "否",
            job.status,
            job.evidence,
        ]
        for col_idx, val in enumerate(values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=val)
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            # 来源链接列设为可点击超链接
            if col_idx == 14 and isinstance(val, str) and val.startswith("http"):
                cell.hyperlink = val
                cell.font = Font(color="0563C1", underline="single")

    wb.save(path)
    return path


class JobTools:
    """聚合 extract_and_save_jobs / query_jobs / export_jobs 业务工具处理器。"""

    def __init__(
        self,
        *,
        adapter: XhsAdapter,
        store: JobStore,
        extractor: JobExtractor,
        export_dir: Path | str | None = None,
        stale_days: int = 60,
        today_fn=None,
    ) -> None:
        self.adapter = adapter
        self.store = store
        self.extractor = extractor
        self.export_dir = Path(export_dir) if export_dir is not None else DEFAULT_EXPORT_DIR
        self.stale_days = int(stale_days)
        self._today_fn = today_fn or date.today

    async def extract_and_save_jobs(self, note: str) -> dict[str, Any]:
        """确定性抽取管道：短 id → 笔记去重 → 取详情 → 抽取 LLM → 证据校验 → 岗位去重 → 入库 → 精简摘要。"""
        short_id = note.strip()
        cached = self.adapter.cache.get(short_id)
        if cached is None:
            raise UnknownNoteError(short_id)

        # 1. 笔记级去重：SQLite notes 表已处理过则直接跳过，不重复抓取也不重复调用抽取 LLM
        db_note = self.store.get_note(cached.feed_id)
        if db_note is not None and db_note.get("processed_at") is not None:
            existing_jobs = self.store.get_jobs_by_note(cached.feed_id)
            positions = [
                f"{j.company or '未具名'}-{j.position or '未具名岗位'}" for j in existing_jobs
            ]
            return {
                "note": short_id,
                "status": "already_processed",
                "created": 0,
                "duplicates": len(existing_jobs),
                "positions": positions,
                "needs_image": any(j.needs_image for j in existing_jobs),
                "maybe_expired": is_note_stale(
                    cached.published_at or db_note.get("published_at"),
                    stale_days=self.stale_days,
                ),
                "source_url": cached.source_url,
                "from_cache": True,
            }

        # 2. 获取笔记详情：优先使用内存 NoteCache 或 SQLite 已持久化详情，避免重复请求小红书
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

        # 持久化笔记原文与详情到 SQLite notes 表
        self.store.upsert_note(
            feed_id=cached.feed_id,
            xsec_token=cached.xsec_token,
            title=cached.title,
            author=cached.author,
            published_at=cached.published_at,
            raw_feed=cached.raw_feed,
            detail=raw_detail,
        )

        # 3. 抽取 LLM + Pydantic 校验 + 证据校验（独立上下文，不回传全文给主 LLM）
        fields = _extract_detail_fields(raw_detail, cached)
        extracted = await self.extractor.extract(
            title=fields["title"],
            desc=fields["desc"],
            image_count=fields["image_count"],
        )

        now_utc = datetime.now(timezone.utc)
        ref_today = self._today_fn()
        published_dt = fields["published_dt"] or cached.published_at
        stale = is_note_stale(published_dt, now=now_utc, stale_days=self.stale_days)

        # 4. 岗位级去重 + SQLite upsert（来源字段全部由代码填写）
        created_count = 0
        dup_count = 0
        positions_summary: list[str] = []

        for item in extracted.jobs:
            status: JobStatus = "expired" if is_deadline_expired(item.deadline, today=ref_today) else "new"
            dedup_key = make_dedup_key(item.company, item.position, item.city)
            record = JobRecord(
                **item.model_dump(),
                note_id=cached.feed_id,
                source_url=cached.source_url,
                note_title=fields["title"] or cached.title,
                note_author=fields["author"] or cached.author,
                note_published_at=published_dt,
                crawled_at=now_utc,
                needs_image=extracted.needs_image,
                dedup_key=dedup_key,
                maybe_expired=stale,
                status=status,
            )
            saved_job, was_created = self.store.upsert_job(record)
            if was_created:
                created_count += 1
            else:
                dup_count += 1
            label = f"{saved_job.company or '未具名'}-{saved_job.position or '实习岗位'}"
            if saved_job.city:
                label += f"({saved_job.city})"
            positions_summary.append(label)

        # 5. 标记该笔记已处理
        self.store.mark_note_processed(cached.feed_id, processed_at=now_utc)

        if created_count > 0:
            res_status = "ok"
        elif dup_count > 0:
            res_status = "duplicate"
        elif extracted.needs_image:
            res_status = "needs_image"
        else:
            res_status = "no_job"

        return {
            "note": short_id,
            "status": res_status,
            "created": created_count,
            "duplicates": dup_count,
            "dropped_unverified": extracted.dropped_unverified,
            "positions": positions_summary,
            "needs_image": extracted.needs_image,
            "maybe_expired": stale,
            "source_url": cached.source_url,
            "from_cache": from_cache,
        }

    async def query_jobs(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        graduation_year: str | None = None,
        since: str | None = None,
        status: JobStatus | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """按条件查询本地 SQLite 岗位库。"""
        records = self.store.query_jobs(
            city=city,
            keyword=keyword,
            graduation_year=graduation_year,
            since=since,
            status=status,
            limit=limit,
        )
        return {
            "count": len(records),
            "jobs": [_job_to_compact_dict(r) for r in records],
        }

    async def export_jobs(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        graduation_year: str | None = None,
        since: str | None = None,
        status: JobStatus | None = None,
        path: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        """将符合条件的本地岗位导出为 Excel (.xlsx) 并返回绝对路径。"""
        records = self.store.query_jobs(
            city=city,
            keyword=keyword,
            graduation_year=graduation_year,
            since=since,
            status=status,
            limit=limit,
        )
        if path and path.strip():
            target = Path(path.strip())
            if not target.is_absolute():
                target = self.export_dir / target
        else:
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            target = self.export_dir / f"jobs_export_{ts}.xlsx"

        saved_path = export_jobs_to_excel(records, target)
        return {
            "path": str(saved_path.resolve()),
            "count": len(records),
        }

    async def mark_job(self, job_id: int, status: JobStatus) -> dict[str, Any]:
        """更新单条岗位的业务跟进状态（new / interested / applied / rejected / expired）。"""
        existing = self.store.get_job(job_id)
        if existing is None:
            raise ValueError(f"未找到 ID={job_id} 的岗位记录，请先调用 query_jobs 确认岗位 ID。")

        old_status = existing.status
        self.store.update_job_status(job_id, status)
        updated = self.store.get_job(job_id)
        assert updated is not None
        return {
            "id": updated.id,
            "company": updated.company,
            "position": updated.position,
            "city": updated.city,
            "old_status": old_status,
            "status": updated.status,
            "source_url": updated.source_url,
        }
