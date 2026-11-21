"""SQLite 持久化存储层：notes、jobs、daily_usage 三张表。

对照 docs/design.md 第 10.2 节与 docs/development_plan.md 阶段 4：
- notes：持久化 NoteCache（feed_id 主键、xsec_token、标题、作者、发布时间、原始 JSON、详情 JSON、processed_at）
- jobs：持久化岗位记录，dedup_key 建索引；支持跨账号重复合并（保留最早来源并记录 extra_sources）
- daily_usage：持久化每日调用计数，实现 RateLimiter 的 UsageCounter 协议
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from xhs_agent.config import ROOT_DIR
from xhs_agent.jobs.models import JobRecord, JobStatus
from xhs_agent.knowledge.models import (
    ExperienceCategory,
    ExperienceRecord,
    ExperienceStatus,
)

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
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA journal_mode=WAL;")
            self._conn.execute("PRAGMA busy_timeout=5000;")
        self.init_schema()

    def close(self) -> None:
        with self._lock:
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

                CREATE TABLE IF NOT EXISTS experiences (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    note_id TEXT NOT NULL UNIQUE,
                    category TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_audience TEXT,
                    summary TEXT NOT NULL,
                    key_points_json TEXT NOT NULL DEFAULT '[]',
                    tech_stack_json TEXT NOT NULL DEFAULT '[]',
                    resources_json TEXT NOT NULL DEFAULT '[]',
                    pitfalls_json TEXT NOT NULL DEFAULT '[]',
                    evidence TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    source_url TEXT NOT NULL,
                    note_title TEXT,
                    note_author TEXT,
                    note_published_at TEXT,
                    liked_count INTEGER,
                    collected_count INTEGER,
                    crawled_at TEXT NOT NULL,
                    needs_image INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'unread',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_experiences_category ON experiences (category);
                CREATE INDEX IF NOT EXISTS idx_experiences_topic ON experiences (topic);
                CREATE INDEX IF NOT EXISTS idx_experiences_status ON experiences (status);
                """
            )
            # 向后兼容迁移：给旧库 notes 表补加 exp_processed_at 列
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(notes)").fetchall()}
            if "exp_processed_at" not in cols:
                self._conn.execute("ALTER TABLE notes ADD COLUMN exp_processed_at TEXT")

    # ------------------------------------------------------------------
    # UsageCounter 协议实现（无缝对接 RateLimiter）
    # ------------------------------------------------------------------

    def get_count(self, day: str, category: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT count FROM daily_usage WHERE day = ? AND category = ?",
                (day, category),
            ).fetchone()
        return int(row["count"]) if row else 0

    def increment(self, day: str, category: str, delta: int = 1) -> int:
        with self._lock:
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

        with self._lock:
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
        with self._lock:
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
            "exp_processed_at": _iso_to_dt(row["exp_processed_at"]) if "exp_processed_at" in row.keys() else None,
        }

    def is_note_processed(self, feed_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT processed_at FROM notes WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        return bool(row and row["processed_at"])

    def mark_note_processed(self, feed_id: str, processed_at: datetime | None = None) -> None:
        ts = _dt_to_iso(processed_at or datetime.now(timezone.utc))
        now = _utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE notes SET processed_at = ?, updated_at = ? WHERE feed_id = ?",
                    (ts, now, feed_id),
                )

    def is_note_exp_processed(self, feed_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT exp_processed_at FROM notes WHERE feed_id = ?",
                (feed_id,),
            ).fetchone()
        return bool(row and row["exp_processed_at"])

    def mark_note_exp_processed(self, feed_id: str, processed_at: datetime | None = None) -> None:
        ts = _dt_to_iso(processed_at or datetime.now(timezone.utc))
        now = _utc_now_iso()
        with self._lock:
            with self._conn:
                self._conn.execute(
                    "UPDATE notes SET exp_processed_at = ?, updated_at = ? WHERE feed_id = ?",
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

        with self._lock:
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
        with self._lock:
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
        only_fresh: bool = False,
        needs_image: bool | None = None,
        limit: int = 50,
    ) -> list[JobRecord]:
        res = self.query_jobs_paginated(
            city=city,
            keyword=keyword,
            graduation_year=graduation_year,
            since=since,
            status=status,
            only_fresh=only_fresh,
            needs_image=needs_image,
            page=1,
            page_size=max(1, min(int(limit), 500)),
            include_facets=False,
        )
        return res["items"]

    def _build_job_filters(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        graduation_year: str | None = None,
        since: str | None = None,
        status: JobStatus | None = None,
        only_fresh: bool = False,
        needs_image: bool | None = None,
    ) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []

        if city and city.strip():
            clauses.append("city LIKE ?")
            params.append(f"%{city.strip()}%")
        if graduation_year and graduation_year.strip():
            clauses.append("graduation_year LIKE ?")
            params.append(f"%{graduation_year.strip()}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if since and since.strip():
            clauses.append("COALESCE(note_published_at, crawled_at) >= ?")
            params.append(since.strip())
        if only_fresh:
            clauses.append("maybe_expired = 0")
        if needs_image is not None:
            clauses.append("needs_image = ?")
            params.append(1 if needs_image else 0)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            clauses.append(
                "(company LIKE ? OR position LIKE ? OR skills_json LIKE ? OR note_title LIKE ? OR evidence LIKE ?)"
            )
            params.extend([kw, kw, kw, kw, kw])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        return where_sql, params

    def query_jobs_paginated(
        self,
        *,
        city: str | None = None,
        keyword: str | None = None,
        graduation_year: str | None = None,
        since: str | None = None,
        status: JobStatus | None = None,
        only_fresh: bool = False,
        needs_image: bool | None = None,
        page: int = 1,
        page_size: int = 20,
        include_facets: bool = True,
    ) -> dict[str, Any]:
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 500))
        offset = (safe_page - 1) * safe_size

        where_sql, params = self._build_job_filters(
            city=city,
            keyword=keyword,
            graduation_year=graduation_year,
            since=since,
            status=status,
            only_fresh=only_fresh,
            needs_image=needs_image,
        )

        with self._lock:
            total_row = self._conn.execute(f"SELECT COUNT(*) AS c FROM jobs {where_sql}", params).fetchone()
            total = int(total_row["c"]) if total_row else 0

            rows = self._conn.execute(
                f"""
                SELECT * FROM jobs
                {where_sql}
                ORDER BY COALESCE(note_published_at, crawled_at) DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, safe_size, offset],
            ).fetchall()
            items = [self._row_to_job(r) for r in rows]

            facets: dict[str, Any] = {}
            if include_facets:
                facets = {
                    "cities": self.get_distinct_cities(),
                    "status_counts": self.get_status_counts(),
                }

        return {
            "total": total,
            "page": safe_page,
            "page_size": safe_size,
            "items": items,
            "facets": facets,
        }

    def get_status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {
            "new": 0,
            "interested": 0,
            "applied": 0,
            "rejected": 0,
            "expired": 0,
        }
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS c FROM jobs GROUP BY status").fetchall()
        for row in rows:
            counts[str(row["status"])] = int(row["c"])
        return counts

    def get_distinct_cities(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT city FROM jobs WHERE city IS NOT NULL AND TRIM(city) != '' ORDER BY city ASC"
            ).fetchall()
        return [str(r["city"]).strip() for r in rows if r["city"]]

    def get_job(self, job_id: int) -> JobRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM jobs WHERE id = ?", (int(job_id),)).fetchone()
        return self._row_to_job(row) if row is not None else None

    def update_job_status(self, job_id: int, status: JobStatus) -> bool:
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE jobs SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _utc_now_iso(), int(job_id)),
                )
            return cur.rowcount > 0

    def delete_job(self, job_id: int) -> bool:
        with self._lock:
            with self._conn:
                cur = self._conn.execute("DELETE FROM jobs WHERE id = ?", (int(job_id),))
            return cur.rowcount > 0

    def count_jobs(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM jobs").fetchone()
        return int(row["c"]) if row else 0

    # ------------------------------------------------------------------
    # experiences 表操作（双模式经验知识库：路线/项目/面经/避坑）
    # ------------------------------------------------------------------

    def _row_to_experience(self, row: sqlite3.Row) -> ExperienceRecord:
        return ExperienceRecord(
            id=int(row["id"]),
            note_id=row["note_id"],
            category=row["category"],
            topic=row["topic"],
            title=row["title"],
            target_audience=row["target_audience"],
            summary=row["summary"],
            key_points=json.loads(row["key_points_json"] or "[]"),
            tech_stack=json.loads(row["tech_stack_json"] or "[]"),
            resources=json.loads(row["resources_json"] or "[]"),
            pitfalls=json.loads(row["pitfalls_json"] or "[]"),
            evidence=row["evidence"],
            confidence=float(row["confidence"]),
            source_url=row["source_url"],
            note_title=row["note_title"],
            note_author=row["note_author"],
            note_published_at=_iso_to_dt(row["note_published_at"]),
            liked_count=int(row["liked_count"]) if row["liked_count"] is not None else None,
            collected_count=int(row["collected_count"]) if row["collected_count"] is not None else None,
            crawled_at=_iso_to_dt(row["crawled_at"]) or datetime.now(timezone.utc),
            needs_image=bool(row["needs_image"]),
            status=row["status"],
        )

    def upsert_experience(self, record: ExperienceRecord) -> tuple[ExperienceRecord, bool]:
        """按 note_id 唯一写入或更新经验卡片；返回 (落库记录, 是否新增)。"""
        now = _utc_now_iso()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM experiences WHERE note_id = ?",
                (record.note_id,),
            ).fetchone()
            if existing is not None:
                with self._conn:
                    self._conn.execute(
                        """
                        UPDATE experiences SET
                            category = ?, topic = ?, title = ?, target_audience = ?,
                            summary = ?, key_points_json = ?, tech_stack_json = ?,
                            resources_json = ?, pitfalls_json = ?, evidence = ?,
                            confidence = ?, liked_count = COALESCE(?, liked_count),
                            collected_count = COALESCE(?, collected_count),
                            needs_image = ?, updated_at = ?
                        WHERE id = ?
                        """,
                        (
                            record.category,
                            record.topic,
                            record.title,
                            record.target_audience,
                            record.summary,
                            json.dumps(record.key_points, ensure_ascii=False),
                            json.dumps(record.tech_stack, ensure_ascii=False),
                            json.dumps(record.resources, ensure_ascii=False),
                            json.dumps(record.pitfalls, ensure_ascii=False),
                            record.evidence,
                            record.confidence,
                            record.liked_count,
                            record.collected_count,
                            1 if record.needs_image else 0,
                            now,
                            int(existing["id"]),
                        ),
                    )
                row = self._conn.execute("SELECT * FROM experiences WHERE id = ?", (int(existing["id"]),)).fetchone()
                return self._row_to_experience(row), False

            with self._conn:
                cur = self._conn.execute(
                    """
                    INSERT INTO experiences (
                        note_id, category, topic, title, target_audience, summary,
                        key_points_json, tech_stack_json, resources_json, pitfalls_json,
                        evidence, confidence, source_url, note_title, note_author,
                        note_published_at, liked_count, collected_count, crawled_at,
                        needs_image, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.note_id,
                        record.category,
                        record.topic,
                        record.title,
                        record.target_audience,
                        record.summary,
                        json.dumps(record.key_points, ensure_ascii=False),
                        json.dumps(record.tech_stack, ensure_ascii=False),
                        json.dumps(record.resources, ensure_ascii=False),
                        json.dumps(record.pitfalls, ensure_ascii=False),
                        record.evidence,
                        record.confidence,
                        record.source_url,
                        record.note_title,
                        record.note_author,
                        _dt_to_iso(record.note_published_at),
                        record.liked_count,
                        record.collected_count,
                        _dt_to_iso(record.crawled_at),
                        1 if record.needs_image else 0,
                        record.status,
                        now,
                        now,
                    ),
                )
                new_id = int(cur.lastrowid)
            row = self._conn.execute("SELECT * FROM experiences WHERE id = ?", (new_id,)).fetchone()
            return self._row_to_experience(row), True

    def get_experience(self, exp_id: int) -> ExperienceRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM experiences WHERE id = ?", (int(exp_id),)).fetchone()
        return self._row_to_experience(row) if row is not None else None

    def get_experience_by_note(self, note_id: str) -> ExperienceRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM experiences WHERE note_id = ?", (note_id,)).fetchone()
        return self._row_to_experience(row) if row is not None else None

    def query_experiences(
        self,
        *,
        category: ExperienceCategory | None = None,
        topic: str | None = None,
        keyword: str | None = None,
        status: ExperienceStatus | None = None,
        needs_image: bool | None = None,
        sort_by: str = "latest",
        limit: int = 50,
    ) -> list[ExperienceRecord]:
        res = self.query_experiences_paginated(
            category=category,
            topic=topic,
            keyword=keyword,
            status=status,
            needs_image=needs_image,
            sort_by=sort_by,
            page=1,
            page_size=max(1, min(int(limit), 500)),
            include_facets=False,
        )
        return res["items"]

    def query_experiences_paginated(
        self,
        *,
        category: ExperienceCategory | None = None,
        topic: str | None = None,
        keyword: str | None = None,
        status: ExperienceStatus | None = None,
        needs_image: bool | None = None,
        sort_by: str = "latest",
        page: int = 1,
        page_size: int = 20,
        include_facets: bool = True,
    ) -> dict[str, Any]:
        safe_page = max(1, int(page))
        safe_size = max(1, min(int(page_size), 500))
        offset = (safe_page - 1) * safe_size

        clauses: list[str] = []
        params: list[Any] = []
        if category:
            clauses.append("category = ?")
            params.append(category)
        if topic and topic.strip():
            clauses.append("topic LIKE ?")
            params.append(f"%{topic.strip()}%")
        if status:
            clauses.append("status = ?")
            params.append(status)
        if needs_image is not None:
            clauses.append("needs_image = ?")
            params.append(1 if needs_image else 0)
        if keyword and keyword.strip():
            kw = f"%{keyword.strip()}%"
            clauses.append(
                "(title LIKE ? OR topic LIKE ? OR summary LIKE ? OR key_points_json LIKE ? OR tech_stack_json LIKE ? OR evidence LIKE ?)"
            )
            params.extend([kw, kw, kw, kw, kw, kw])

        where_sql = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        order_sql = (
            "ORDER BY COALESCE(liked_count, 0) DESC, COALESCE(note_published_at, crawled_at) DESC"
            if sort_by == "likes"
            else "ORDER BY COALESCE(note_published_at, crawled_at) DESC, id DESC"
        )

        with self._lock:
            total_row = self._conn.execute(f"SELECT COUNT(*) AS c FROM experiences {where_sql}", params).fetchone()
            total = int(total_row["c"]) if total_row else 0
            rows = self._conn.execute(
                f"SELECT * FROM experiences {where_sql} {order_sql} LIMIT ? OFFSET ?",
                [*params, safe_size, offset],
            ).fetchall()
            items = [self._row_to_experience(r) for r in rows]

            facets: dict[str, Any] = {}
            if include_facets:
                facets = {
                    "category_counts": self.get_experience_category_counts(),
                    "status_counts": self.get_experience_status_counts(),
                    "topics": self.get_experience_topics(),
                }

        return {
            "total": total,
            "page": safe_page,
            "page_size": safe_size,
            "items": items,
            "facets": facets,
        }

    def get_experience_category_counts(self) -> dict[str, int]:
        counts = {"roadmap": 0, "project": 0, "interview": 0, "tips": 0}
        with self._lock:
            rows = self._conn.execute("SELECT category, COUNT(*) AS c FROM experiences GROUP BY category").fetchall()
        for r in rows:
            counts[str(r["category"])] = int(r["c"])
        return counts

    def get_experience_status_counts(self) -> dict[str, int]:
        counts = {"unread": 0, "reading": 0, "starred": 0, "archived": 0}
        with self._lock:
            rows = self._conn.execute("SELECT status, COUNT(*) AS c FROM experiences GROUP BY status").fetchall()
        for r in rows:
            counts[str(r["status"])] = int(r["c"])
        return counts

    def get_experience_topics(self) -> list[str]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT topic FROM experiences WHERE topic IS NOT NULL AND TRIM(topic) != '' ORDER BY topic ASC"
            ).fetchall()
        return [str(r["topic"]).strip() for r in rows if r["topic"]]

    def update_experience_status(self, exp_id: int, status: ExperienceStatus) -> bool:
        with self._lock:
            with self._conn:
                cur = self._conn.execute(
                    "UPDATE experiences SET status = ?, updated_at = ? WHERE id = ?",
                    (status, _utc_now_iso(), int(exp_id)),
                )
            return cur.rowcount > 0

    def delete_experience(self, exp_id: int) -> bool:
        with self._lock:
            with self._conn:
                cur = self._conn.execute("DELETE FROM experiences WHERE id = ?", (int(exp_id),))
            return cur.rowcount > 0

    def count_experiences(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS c FROM experiences").fetchone()
        return int(row["c"]) if row else 0
