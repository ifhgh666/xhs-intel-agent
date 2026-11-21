"""SQLite 持久化存储层：notes、jobs、daily_usage 三张表。

对照 docs/design.md 第 10.2 节与 docs/development_plan.md 阶段 4：
- notes：持久化 NoteCache（feed_id 主键、xsec_token、标题、作者、发布时间、原始 JSON、详情 JSON、processed_at）
- jobs：持久化岗位记录，dedup_key 建索引；支持跨账号重复合并（保留最早来源并记录 extra_sources）
- daily_usage：持久化每日调用计数，实现 RateLimiter 的 UsageCounter 协议
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xhs_agent.config import ROOT_DIR
from xhs_agent.jobs.models import JobRecord, JobStatus

DEFAULT_DB_PATH = ROOT_DIR / "data" / "xhs_jobs.db"


def _dt_to_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


def _iso_to_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    """SQLite 存储管理器（线程安全短连接或持久连接，默认持久化到 data/xhs_jobs.db）。"""

    def __init__(self, db_path: Path | str | None = None) -> None:
        self.db_path = Path(db_path) if db_path is not None else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path))
        self._conn.row_factory = sqlite3.Row
        self.init_schema()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> JobStore:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS notes (
                    feed_id TEXT PRIMARY KEY,
                    xsec_token TEXT,
                    title TEXT,
                    author TEXT,
                    published_at TEXT,
                    raw_json TEXT,
                    detail_json TEXT,
                    processed_at TEXT,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS jobs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    dedup_key TEXT,
                    company TEXT,
                    position TEXT,
                    city TEXT,
                    education TEXT,
                    graduation_year TEXT,
                    skills_json TEXT NOT NULL DEFAULT '[]',
                    internship_duration TEXT,
                    attendance_days TEXT,
                    salary TEXT,
                    deadline TEXT,
                    apply_method TEXT,
                    application_url TEXT,
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    note_id TEXT NOT NULL,
                    source_url TEXT NOT NULL,
                    note_title TEXT,
                    note_author TEXT,
                    note_published_at TEXT,
                    crawled_at TEXT NOT NULL,
                    needs_image INTEGER NOT NULL DEFAULT 0,
                    maybe_expired INTEGER NOT NULL DEFAULT 0,
                    extra_sources_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'new',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_jobs_dedup_key ON jobs (dedup_key);
                CREATE INDEX IF NOT EXISTS idx_jobs_note_id ON jobs (note_id);
                CREATE INDEX IF NOT EXISTS idx_jobs_city ON jobs (city);
                CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs (status);

                CREATE TABLE IF NOT EXISTS daily_usage (
                    day TEXT NOT NULL,
                    category TEXT NOT NULL,
                    count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY (day, category)
                );
                """
            )

    # ------------------------------------------------------------------
    # UsageCounter 协议实现（无缝对接 RateLimiter）
    # ------------------------------------------------------------------

    def get_count(self, day: str, category: str) -> int:
        row = self._conn.execute(
            "SELECT count FROM daily_usage WHERE day = ? AND category = ?",
            (day, category),
        ).fetchone()
        return int(row["count"]) if row else 0

    def increment(self, day: str, category: str, delta: int = 1) -> int:
        with self._conn:
            self._conn.execute(
                """
                INSERT INTO daily_usage (day, category, count)
                VALUES (?, ?, ?)
                ON CONFLICT(day, category) DO UPDATE SET count = count + excluded.count
                """,
                (day, category, delta),
            )
        return self.get_count(day, category)

    # ------------------------------------------------------------------
    # notes 表操作（NoteCache 持久化 + 笔记级去重）
    # ------------------------------------------------------------------

    def upsert_note(
        self,
        *,
        feed_id: str,
        xsec_token: str | None = None,
        title: str | None = None,
        author: str | None = None,
        published_at: datetime | None = None,
        raw_feed: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        now = _utc_now_iso()
        raw_str = json.dumps(raw_feed, ensure_ascii=False) if raw_feed is not None else None
        detail_str = json.dumps(detail, ensure_ascii=False) if detail is not None else None
        pub_str = _dt_to_iso(published_at)

        with self._conn:
            self._conn.execute(
                """
                INSERT INTO notes (
                    feed_id, xsec_token, title, author, published_at,
                    raw_json, detail_json, processed_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
                ON CONFLICT(feed_id) DO UPDATE SET
                    xsec_token = COALESCE(NULLIF(excluded.xsec_token, ''), notes.xsec_token),
                    title = COALESCE(NULLIF(excluded.title, ''), notes.title),
                    author = COALESCE(NULLIF(excluded.author, ''), notes.author),
                    published_at = COALESCE(excluded.published_at, notes.published_at),
                    raw_json = COALESCE(excluded.raw_json, notes.raw_json),
                    detail_json = COALESCE(excluded.detail_json, notes.detail_json),
                    updated_at = excluded.updated_at
                """,
                (feed_id, xsec_token, title, author, pub_str, raw_str, detail_str, now),
            )

    def get_note(self, feed_id: str) -> dict[str, Any] | None:
        row = self._conn.execute("SELECT * FROM notes WHERE feed_id = ?", (feed_id,)).fetchone()
        if row is None:
            return None
        return {
            "feed_id": row["feed_id"],
            "xsec_token": row["xsec_token"],
            "title": row["title"],
            "author": row["author"],
            "published_at": _iso_to_dt(row["published_at"]),
            "raw_feed": json.loads(row["raw_json"]) if row["raw_json"] else None,
            "detail": json.loads(row["detail_json"]) if row["detail_json"] else None,
            "processed_at": _iso_to_dt(row["processed_at"]),
        }

    def is_note_processed(self, feed_id: str) -> bool:
        row = self._conn.execute(
            "SELECT processed_at FROM notes WHERE feed_id = ?",
            (feed_id,),
        ).fetchone()
        return bool(row and row["processed_at"])

    def mark_note_processed(self, feed_id: str, processed_at: datetime | None = None) -> None:
        ts = _dt_to_iso(processed_at or datetime.now(timezone.utc))
        now = _utc_now_iso()
        with self._conn:
            self._conn.execute(
                "UPDATE notes SET processed_at = ?, updated_at = ? WHERE feed_id = ?",
                (ts, now, feed_id),
            )

    # ------------------------------------------------------------------
    # jobs 表操作（岗位 upsert 去重、查询与状态更新）
    # ------------------------------------------------------------------

    def _row_to_job(self, row: sqlite3.Row) -> JobRecord:
        return JobRecord(
            id=int(row["id"]),
            dedup_key=row["dedup_key"],
            company=row["company"],
            position=row["position"],
            city=row["city"],
            education=row["education"],
            graduation_year=row["graduation_year"],
            skills=json.loads(row["skills_json"] or "[]"),
            internship_duration=row["internship_duration"],
            attendance_days=row["attendance_days"],
            salary=row["salary"],
            deadline=row["deadline"],
            apply_method=row["apply_method"],
            application_url=row["application_url"],
            evidence=row["evidence"],
            confidence=float(row["confidence"]),
            note_id=row["note_id"],
            source_url=row["source_url"],
            note_title=row["note_title"],
            note_author=row["note_author"],
            note_published_at=_iso_to_dt(row["note_published_at"]),
            crawled_at=_iso_to_dt(row["crawled_at"]) or datetime.now(timezone.utc),
            needs_image=bool(row["needs_image"]),
            maybe_expired=bool(row["maybe_expired"]),
            extra_sources=json.loads(row["extra_sources_json"] or "[]"),
            status=row["status"],
        )

    def upsert_job(self, record: JobRecord) -> tuple[JobRecord, bool]:
        """写入或按 dedup_key 合并岗位；返回 (落库记录, 是否新增)。"""
        now = _utc_now_iso()

        if record.dedup_key:
            existing_row = self._conn.execute(
                "SELECT * FROM jobs WHERE dedup_key = ? ORDER BY id ASC LIMIT 1",
                (record.dedup_key,),
            ).fetchone()
            if existing_row is not None:
                existing = self._row_to_job(existing_row)
                extra = list(existing.extra_sources)
                if record.source_url != existing.source_url and record.source_url not in extra:
                    extra.append(record.source_url)

                # 保留最早来源，但对缺失字段做温和补全（不覆盖已有非空字段）
                merged_skills = list(dict.fromkeys([*existing.skills, *record.skills]))
                with self._conn:
                    self._conn.execute(
                        """
                        UPDATE jobs SET
                            education = COALESCE(education, ?),
                            graduation_year = COALESCE(graduation_year, ?),
                            skills_json = ?,
                            internship_duration = COALESCE(internship_duration, ?),
                            attendance_days = COALESCE(attendance_days, ?),
                            salary = COALESCE(salary, ?),
                            deadline = COALESCE(deadline, ?),
                            apply_method = COALESCE(apply_method, ?),
                            application_url = COALESCE(application_url, ?),
                            extra_sources_json = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            record.education,
                            record.graduation_year,
                            json.dumps(merged_skills, ensure_ascii=False),
                            record.internship_duration,
                            record.attendance_days,
                            record.salary,
                            record.deadline,
                            record.apply_method,
                            record.application_url,
                            json.dumps(extra, ensure_ascii=False),
                            now,
                            existing.id,
                        ),
                    )
                updated_row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (existing.id,)).fetchone()
                return self._row_to_job(updated_row), False

        with self._conn:
            cur = self._conn.execute(
                """
                INSERT INTO jobs (
                    dedup_key, company, position, city, education, graduation_year,
                    skills_json, internship_duration, attendance_days, salary, deadline,
                    apply_method, application_url, evidence, confidence,
                    note_id, source_url, note_title, note_author, note_published_at,
                    crawled_at, needs_image, maybe_expired, extra_sources_json,
                    status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record.dedup_key,
                    record.company,
                    record.position,
                    record.city,
                    record.education,
                    record.graduation_year,
                    json.dumps(record.skills, ensure_ascii=False),
                    record.internship_duration,
                    record.attendance_days,
                    record.salary,
                    record.deadline,
                    record.apply_method,
                    record.application_url,
                    record.evidence,
                    record.confidence,
                    record.note_id,
                    record.source_url,
                    record.note_title,
                    record.note_author,
                    _dt_to_iso(record.note_published_at),
                    _dt_to_iso(record.crawled_at),
                    1 if record.needs_image else 0,
                    1 if record.maybe_expired else 0,
                    json.dumps(record.extra_sources, ensure_ascii=False),
                    record.status,
                    now,
                    now,
                ),
            )
            new_id = int(cur.lastrowid)

        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (new_id,)).fetchone()
        return self._row_to_job(row), True

    def get_jobs_by_note(self, note_id: str) -> list[JobRecord]:
        rows = self._conn.execute(
            "SELECT * FROM jobs WHERE note_id = ? ORDER BY id ASC",
            (note_id,),
        ).fetchall()
        return [self._row_to_job(r) for r in rows]

    def query_jobs(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        graduation_year: str | None = None,
        since: str | None = None,
        status: JobStatus | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        clauses: list[str] = []
        params: list[Any] = []

        if city:
            clauses.append("city LIKE ?")
            params.append(f"%{city.strip()}%")
        if graduation_year:
            clauses.append("graduation_year LIKE ?")
            params.append(f"%{graduation_year.strip()}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since:
            clauses.append("COALESCE(note_published_at, crawled_at) >= ?")
            params.append(since.strip())
        if keyword:
            kw = f"%{keyword.strip()}%"
            clauses.append(
                "(company LIKE ? OR position LIKE ? OR skills_json LIKE ? OR note_title LIKE ? OR evidence LIKE ?)"
            )
            params.extend([kw, kw, kw, kw, kw])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = f"""
            SELECT * FROM jobs
            {where_sql}
            ORDER BY COALESCE(note_published_at, crawled_at) DESC, id DESC
            LIMIT ?
        """
        params.append(max(1, min(int(limit), 500)))
        rows = self._conn.execute(sql, params).fetchall()
        return [self._row_to_job(r) for r in rows]

    def get_job(self, job_id: int) -> JobRecord | None:
        row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return self._row_to_job(row) if row is not None else None

    def update_job_status(self, job_id: int, status: JobStatus) -> bool:
        with self._conn:
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                (status, _utc_now_iso(), job_id),
            )
        return cur.rowcount > 0

    def count_jobs(self) -> int:
        row = self._conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
        return int(row["c"]) if row else 0
