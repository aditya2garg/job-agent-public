from __future__ import annotations

import json
from typing import Any

from . import db

BASE_SQL = """
SELECT j.id, j.source, j.company, j.title, j.url, j.location, j.remote, j.posted_at, j.employment_type, j.first_seen, j.last_seen,
       j.salary_text, s.heuristic, s.final, s.verdict, s.role_name, s.role_tier, s.breakdown, s.details,
       COALESCE(st.status, 'new') AS status, st.notes AS notes,
       (l.job_id IS NOT NULL) AS has_llm
FROM jobs j
JOIN scores s ON s.job_id = j.id
LEFT JOIN state st ON st.job_id = j.id
LEFT JOIN llm l ON l.job_id = j.id
WHERE j.active = 1
"""


def _summary(row: Any) -> dict[str, Any]:
    d = json.loads(row["details"])
    sal = d["salary"]
    return {
        "id": row["id"], "company": row["company"], "title": row["title"], "url": row["url"], "source": row["source"],
        "location": row["location"], "location_label": d["location"]["label"], "location_kind": d["location"]["kind"],
        "remote": bool(d["location"]["remote"]), "posted_at": row["posted_at"], "first_seen": row["first_seen"],
        "score": row["final"], "heuristic": row["heuristic"], "verdict": row["verdict"], "role_name": row["role_name"], "role_tier": row["role_tier"],
        "breakdown": json.loads(row["breakdown"]),
        "salary_known": bool(sal.get("known")), "salary_low": sal.get("low"), "salary_high": sal.get("high"),
        "salary_note": sal.get("note"), "pros": d.get("pros", []), "cons": d.get("cons", []), "flags": [m for _, m in d.get("flags", [])],
        "status": row["status"], "notes": row["notes"], "has_llm": bool(row["has_llm"]),
    }


def ranked(
    min_score: float = 0,
    tier: str | None = None,
    verdict: str | None = None,
    location_kind: str | None = None,
    min_salary: float | None = None,
    status: str | None = None,
    q: str | None = None,
    include_hidden: bool = False,
    limit: int = 500,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    sql = BASE_SQL + " AND s.final >= ?"
    params: list[Any] = [min_score]
    if tier:
        sql += " AND s.role_tier = ?"
        params.append(tier)
    if verdict:
        sql += " AND s.verdict = ?"
        params.append(verdict)
    if status:
        sql += " AND COALESCE(st.status,'new') = ?"
        params.append(status)
    elif not include_hidden:
        sql += " AND COALESCE(st.status,'new') NOT IN ('ignored','rejected')"
    if q:
        sql += " AND (j.title LIKE ? OR j.company LIKE ? OR j.location LIKE ?)"
        params += [f"%{q}%"] * 3
    sql += " ORDER BY s.final DESC, j.company"
    with db.connect() as conn:
        rows = [_summary(r) for r in conn.execute(sql, params).fetchall()]
    if location_kind:
        wanted = set(location_kind.split(","))
        rows = [r for r in rows if r["location_kind"] in wanted]
    if min_salary:
        rows = [r for r in rows if r["salary_known"] and (r["salary_high"] or 0) >= min_salary]
    total = len(rows)
    return rows[offset : offset + limit], total


def detail(job_id: str) -> dict[str, Any] | None:
    with db.connect() as conn:
        row = conn.execute(BASE_SQL + " AND j.id = ?", (job_id,)).fetchone()
        if not row:
            return None
        full = conn.execute("SELECT description FROM jobs WHERE id=?", (job_id,)).fetchone()
        llm = conn.execute("SELECT analysis, model, created_at FROM llm WHERE job_id=?", (job_id,)).fetchone()
    out = _summary(row)
    out["details"] = json.loads(row["details"])
    out["description"] = full["description"]
    out["llm"] = {**json.loads(llm["analysis"]), "model": llm["model"], "created_at": llm["created_at"]} if llm else None
    return out


def set_status(job_id: str, status: str, notes: str | None = None) -> None:
    if status not in db.STATUSES:
        raise ValueError(f"status must be one of {db.STATUSES}")
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO state (job_id,status,notes,updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET status=excluded.status, notes=COALESCE(excluded.notes, state.notes), updated_at=excluded.updated_at",
            (job_id, status, notes, db.now()),
        )


def stats() -> dict[str, Any]:
    with db.connect() as conn:
        total = conn.execute("SELECT COUNT(*) c FROM jobs WHERE active=1").fetchone()["c"]
        by_verdict = {r["verdict"]: r["c"] for r in conn.execute("SELECT s.verdict, COUNT(*) c FROM scores s JOIN jobs j ON j.id=s.job_id WHERE j.active=1 GROUP BY s.verdict")}
        by_tier = {r["role_tier"]: r["c"] for r in conn.execute("SELECT s.role_tier, COUNT(*) c FROM scores s JOIN jobs j ON j.id=s.job_id WHERE j.active=1 GROUP BY s.role_tier")}
        by_source = {r["source"]: r["c"] for r in conn.execute("SELECT source, COUNT(*) c FROM jobs WHERE active=1 GROUP BY source")}
        last = conn.execute("SELECT started_at FROM runs WHERE kind='scrape' ORDER BY id DESC LIMIT 1").fetchone()
        new_today = conn.execute("SELECT COUNT(*) c FROM jobs WHERE active=1 AND first_seen >= datetime('now','-1 day')").fetchone()["c"]
        llm_n = conn.execute("SELECT COUNT(*) c FROM llm").fetchone()["c"]
    return {"total": total, "by_verdict": by_verdict, "by_tier": by_tier, "by_source": by_source, "last_scrape": last["started_at"] if last else None, "new_24h": new_today, "llm_analyzed": llm_n}
