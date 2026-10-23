from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .. import config, db, queries

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="jobhunt", docs_url="/api/docs", redoc_url=None)

_refresh: dict[str, Any] = {"running": False, "stage": "idle", "message": "", "error": None}
_lock = threading.Lock()


class StatusBody(BaseModel):
    status: str
    notes: Optional[str] = None


@app.get("/api/stats")
def stats() -> dict[str, Any]:
    out = queries.stats()
    out["llm_available"] = bool(__import__("jobhunt.llm", fromlist=["provider"]).provider())
    out["refresh"] = dict(_refresh)
    out["verdict_thresholds"] = config.preferences()["verdicts"]
    out["compensation"] = config.preferences()["compensation"]
    return out


@app.get("/api/jobs")
def jobs(
    min_score: float = 0,
    tier: Optional[str] = None,
    verdict: Optional[str] = None,
    location: Optional[str] = Query(None, description="comma list of bay_area,austin,remote_us,us_other,unknown"),
    min_salary: Optional[float] = None,
    status: Optional[str] = None,
    q: Optional[str] = None,
    include_hidden: bool = False,
    hide_no_sponsor: bool = False,
    limit: int = 300,
    offset: int = 0,
) -> dict[str, Any]:
    rows, total = queries.ranked(min_score, tier, verdict, location, min_salary, status, q, include_hidden, hide_no_sponsor, limit, offset)
    return {"total": total, "jobs": rows}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: str) -> dict[str, Any]:
    d = queries.detail(job_id)
    if not d:
        raise HTTPException(404, "job not found")
    return d


@app.post("/api/jobs/{job_id}/status")
def set_status(job_id: str, body: StatusBody) -> dict[str, str]:
    try:
        queries.set_status(job_id, body.status, body.notes)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": "true"}


def _do_refresh() -> None:
    from ..llm import analyze_top, provider
    from ..pipeline import score_all
    from ..scrape import run_scrape

    try:
        _refresh.update(stage="scraping", message="Fetching postings from career boards...", error=None)
        stats = run_scrape()
        _refresh.update(stage="scoring", message=f"Scraped {stats.fetched:,} postings, kept {stats.kept:,}. Scoring...")
        score_all()
        if provider():
            _refresh.update(stage="reviewing", message="Running LLM deep review on top matches...")
            analyze_top()
            score_all()
        _refresh.update(stage="idle", message=f"Done: {stats.new} new postings.")
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        _refresh.update(stage="error", error=str(exc), message="Refresh failed")
    finally:
        _refresh["running"] = False


@app.post("/api/refresh")
def refresh() -> dict[str, Any]:
    with _lock:
        if _refresh["running"]:
            return {"started": False, **_refresh}
        _refresh["running"] = True
    threading.Thread(target=_do_refresh, daemon=True).start()
    return {"started": True, **_refresh}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
_ = db
