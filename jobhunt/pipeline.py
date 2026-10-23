from __future__ import annotations

import json
from typing import Any

from . import db
from .scoring import content_hash, score_jobs
from .config import preferences
from .roles import classify_role


def score_all() -> dict[str, Any]:
    """Re-score every active posting and fold in any cached LLM analysis."""
    blend = float(preferences().get("llm", {}).get("blend", 0.4))
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM jobs WHERE active=1").fetchall()
        jobs = [db.row_to_job(r) for r in rows]
        stale = [j.id for j in jobs if classify_role(j.title) is None]
        if stale:
            conn.executemany("UPDATE jobs SET active=0 WHERE id=?", [(i,) for i in stale])
            jobs = [j for j in jobs if j.id not in set(stale)]
        results = score_jobs(jobs)
        llm_rows = {r["job_id"]: r for r in conn.execute("SELECT * FROM llm").fetchall()}
        by_id = {j.id: j for j in jobs}
        blended = 0
        for res in results:
            lr = llm_rows.get(res["job_id"])
            if lr and lr["content_hash"] == content_hash(by_id[res["job_id"]]):
                analysis = json.loads(lr["analysis"])
                if isinstance(analysis.get("fit_score"), (int, float)):
                    mixed = (1 - blend) * res["heuristic"] + blend * float(analysis["fit_score"])
                    res["final"] = round(min(mixed, res["heuristic"] + 12, res["details"]["cap"]), 1)
                    res["verdict"] = _verdict(res["final"])
                    blended += 1
            db.save_score(conn, res["job_id"], res)
        db.record_run(conn, "score", {"scored": len(results), "llm_blended": blended})
    return {"scored": len(results), "llm_blended": blended}


def _verdict(total: float) -> str:
    from .scoring import verdict_for

    return verdict_for(total, preferences())
