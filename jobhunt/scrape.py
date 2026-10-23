from __future__ import annotations

import datetime as dt
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable

from rich.console import Console

from . import config, db
from .extract import classify_location
from .models import Job
from .roles import classify_role
from .sources.ats import FETCHERS
from .sources.base import make_client
from .sources.boards import BOARDS

console = Console()
SOURCE_PRIORITY = {"greenhouse": 0, "lever": 0, "ashby": 0, "hn": 2, "remotive": 3, "remoteok": 3, "himalayas": 3}


@dataclass
class ScrapeStats:
    boards_ok: int = 0
    boards_empty: int = 0
    boards_failed: list[str] = field(default_factory=list)
    fetched: int = 0
    kept: int = 0
    dropped_role: int = 0
    dropped_location: int = 0
    dropped_age: int = 0
    dropped_duplicate: int = 0
    new: int = 0
    updated: int = 0
    closed: int = 0
    per_source: dict[str, int] = field(default_factory=dict)


def _parse_dt(value: str | None) -> dt.datetime | None:
    if not value:
        return None
    try:
        d = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def keep_job(job: Job, stats: ScrapeStats, max_age_days: int, require_us: bool) -> bool:
    if not job.title or not job.url:
        return False
    if classify_role(job.title) is None:
        stats.dropped_role += 1
        return False
    posted = _parse_dt(job.posted_at)
    if posted and (dt.datetime.now(dt.timezone.utc) - posted).days > max_age_days:
        stats.dropped_age += 1
        return False
    if require_us and not classify_location(job.location, job.remote, job.description).us_eligible:
        stats.dropped_location += 1
        return False
    return True


def run_scrape(
    sources: list[str] | None = None,
    only_companies: list[str] | None = None,
    workers: int = 12,
    progress: Callable[[str], None] | None = None,
) -> ScrapeStats:
    prefs = config.preferences()["scrape"]
    cfg = config.companies()
    stats = ScrapeStats()
    started = db.now()
    wanted = set(sources or []) or None
    log = progress or (lambda m: None)

    collected: list[Job] = []
    scanned_boards: set[tuple[str, str]] = set()

    # ---- ATS company boards
    run_ats = wanted is None or wanted & {"ats", "greenhouse", "lever", "ashby"}
    if run_ats:
        boards = cfg.get("boards", [])
        if wanted and not ("ats" in wanted):
            boards = [b for b in boards if b["ats"] in wanted]
        if only_companies:
            lowered = {c.lower() for c in only_companies}
            boards = [b for b in boards if b["name"].lower() in lowered or b["token"].lower() in lowered]
        client = make_client()

        def work(b: dict) -> tuple[dict, list[Job] | Exception]:
            try:
                return b, FETCHERS[b["ats"]](client, b["token"], b.get("name"))
            except Exception as exc:  # noqa: BLE001
                return b, exc

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futs = [pool.submit(work, b) for b in boards]
            for i, fut in enumerate(as_completed(futs), 1):
                b, res = fut.result()
                label = f"{b['name']} ({b['ats']}:{b['token']})"
                if isinstance(res, Exception):
                    stats.boards_failed.append(f"{label}: {type(res).__name__}")
                elif not res:
                    stats.boards_empty += 1
                else:
                    stats.boards_ok += 1
                    stats.per_source[b["ats"]] = stats.per_source.get(b["ats"], 0) + len(res)
                    collected.extend(res)
                    for j in res[:1]:
                        scanned_boards.add((j.source, j.company))
                log(f"[{i}/{len(boards)}] {label}")

    # ---- aggregators
    agg_client = make_client()
    agg_names = [a for a in cfg.get("aggregators", []) if wanted is None or a in wanted]
    for name in agg_names:
        try:
            res = BOARDS[name](agg_client)
            stats.per_source[name] = len(res)
            collected.extend(res)
            log(f"{name}: {len(res)} postings")
        except Exception as exc:  # noqa: BLE001
            stats.boards_failed.append(f"{name}: {type(exc).__name__}: {exc}")

    if wanted and "jobspy" in wanted:
        from .sources.jobspy_src import jobspy_scrape

        res = jobspy_scrape()
        stats.per_source["jobspy"] = len(res)
        collected.extend(res)

    stats.fetched = len(collected)

    # ---- filter + dedupe
    survivors: dict[str, Job] = {}
    for job in collected:
        if not keep_job(job, stats, prefs["max_age_days"], prefs["require_us_eligible"]):
            continue
        prev = survivors.get(job.dedupe_key)
        if prev is None or SOURCE_PRIORITY.get(job.source.split(":")[0], 1) < SOURCE_PRIORITY.get(prev.source.split(":")[0], 1):
            if prev is not None:
                stats.dropped_duplicate += 1
            survivors[job.dedupe_key] = job
        else:
            stats.dropped_duplicate += 1

    with db.connect() as conn:
        # Skip aggregator copies of a posting we already hold from a higher-priority source.
        final: list[Job] = []
        for job in survivors.values():
            row = conn.execute("SELECT id, source FROM jobs WHERE dedupe_key=? AND id<>? AND active=1", (job.dedupe_key, job.id)).fetchone()
            if row and SOURCE_PRIORITY.get(row["source"].split(":")[0], 1) <= SOURCE_PRIORITY.get(job.source.split(":")[0], 1):
                stats.dropped_duplicate += 1
                continue
            final.append(job)
        stats.kept = len(final)
        stats.new, stats.updated = db.upsert_jobs(conn, final)
        if not only_companies and (wanted is None or run_ats):
            stats.closed = db.mark_inactive(conn, scanned_boards, started)
        db.record_run(conn, "scrape", stats.__dict__)
    return stats


def check_companies(workers: int = 16) -> list[dict]:
    """Probe every configured board. Useful for pruning dead tokens."""
    boards = config.companies().get("boards", [])
    client = make_client(timeout=30)
    out: list[dict] = []

    def probe(b: dict) -> dict:
        t = time.time()
        try:
            jobs = FETCHERS[b["ats"]](client, b["token"], b.get("name"))
            relevant = sum(1 for j in jobs if classify_role(j.title))
            return {**b, "status": "ok" if jobs else "empty/404", "jobs": len(jobs), "relevant": relevant, "secs": round(time.time() - t, 1)}
        except Exception as exc:  # noqa: BLE001
            return {**b, "status": f"error: {type(exc).__name__}", "jobs": 0, "relevant": 0, "secs": round(time.time() - t, 1)}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        out = list(pool.map(probe, boards))
    return out
