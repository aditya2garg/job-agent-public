from __future__ import annotations

import datetime as dt
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from . import config
from .models import Job

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY, source TEXT, company TEXT, title TEXT, url TEXT, location TEXT, remote INTEGER,
  posted_at TEXT, employment_type TEXT, department TEXT, description TEXT,
  salary_min REAL, salary_max REAL, salary_text TEXT, dedupe_key TEXT,
  first_seen TEXT, last_seen TEXT, active INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe ON jobs(dedupe_key);
CREATE TABLE IF NOT EXISTS scores (
  job_id TEXT PRIMARY KEY, heuristic REAL, final REAL, verdict TEXT, role_name TEXT, role_tier TEXT,
  breakdown TEXT, details TEXT, scored_at TEXT
);
CREATE TABLE IF NOT EXISTS llm (
  job_id TEXT PRIMARY KEY, content_hash TEXT, model TEXT, analysis TEXT, created_at TEXT
);
CREATE TABLE IF NOT EXISTS state (
  job_id TEXT PRIMARY KEY, status TEXT, notes TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, kind TEXT, stats TEXT
);
"""

STATUSES = ["new", "saved", "applied", "interviewing", "rejected", "ignored"]


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    config.DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def upsert_jobs(conn: sqlite3.Connection, jobs: list[Job]) -> tuple[int, int]:
    """Insert or refresh postings. Returns (new, updated)."""
    ts = now()
    new = updated = 0
    for j in jobs:
        row = conn.execute("SELECT id FROM jobs WHERE id=?", (j.id,)).fetchone()
        vals = (
            j.source, j.company, j.title, j.url, j.location, None if j.remote is None else int(j.remote), j.posted_at,
            j.employment_type, j.department, j.description, j.salary_min, j.salary_max, j.salary_text, j.dedupe_key,
        )
        if row:
            conn.execute(
                "UPDATE jobs SET source=?,company=?,title=?,url=?,location=?,remote=?,posted_at=?,employment_type=?,"
                "department=?,description=?,salary_min=?,salary_max=?,salary_text=?,dedupe_key=?,last_seen=?,active=1 WHERE id=?",
                (*vals, ts, j.id),
            )
            updated += 1
        else:
            conn.execute(
                "INSERT INTO jobs (source,company,title,url,location,remote,posted_at,employment_type,department,description,"
                "salary_min,salary_max,salary_text,dedupe_key,first_seen,last_seen,id) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (*vals, ts, ts, j.id),
            )
            conn.execute("INSERT OR IGNORE INTO state (job_id,status,updated_at) VALUES (?,?,?)", (j.id, "new", ts))
            new += 1
    return new, updated


def mark_inactive(conn: sqlite3.Connection, source_companies: set[tuple[str, str]], run_started: str) -> int:
    """Postings from fully-scanned boards that were not seen this run are considered closed."""
    n = 0
    for source, company in source_companies:
        cur = conn.execute(
            "UPDATE jobs SET active=0 WHERE source=? AND company=? AND last_seen < ? AND active=1", (source, company, run_started)
        )
        n += cur.rowcount
    return n


def row_to_job(row: sqlite3.Row) -> Job:
    return Job(
        source=row["source"], company=row["company"], title=row["title"], url=row["url"], description=row["description"] or "",
        location=row["location"] or "", remote=None if row["remote"] is None else bool(row["remote"]), posted_at=row["posted_at"],
        employment_type=row["employment_type"], department=row["department"], stored_id=row["id"],
        salary_min=row["salary_min"], salary_max=row["salary_max"], salary_text=row["salary_text"],
    )


def save_score(conn: sqlite3.Connection, job_id: str, result: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO scores (job_id,heuristic,final,verdict,role_name,role_tier,breakdown,details,scored_at) VALUES (?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(job_id) DO UPDATE SET heuristic=excluded.heuristic, final=excluded.final, verdict=excluded.verdict, "
        "role_name=excluded.role_name, role_tier=excluded.role_tier, breakdown=excluded.breakdown, details=excluded.details, scored_at=excluded.scored_at",
        (
            job_id, result["heuristic"], result["final"], result["verdict"], result["role_name"], result["role_tier"],
            json.dumps(result["breakdown"]), json.dumps(result["details"]), now(),
        ),
    )


def record_run(conn: sqlite3.Connection, kind: str, stats: dict[str, Any]) -> None:
    conn.execute("INSERT INTO runs (started_at,kind,stats) VALUES (?,?,?)", (now(), kind, json.dumps(stats)))
