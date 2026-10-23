"""Optional: LinkedIn / Indeed / Glassdoor / Google Jobs via python-jobspy.

Install with `uv pip install -e ".[jobspy]"`. These sites aggressively rate-limit and block, so this
source is best-effort and off by default. Enable with `jobhunt scrape --sources jobspy`.
"""

from __future__ import annotations

from ..extract import html_to_text
from ..models import Job

SEARCHES = [
    ("AI Engineer", "San Francisco, CA"),
    ("Agentic AI Engineer", "San Francisco, CA"),
    ("AI Engineer", "Austin, TX"),
    ("LLM Engineer", "Austin, TX"),
    ("AI Engineer", "United States"),
    ("Analytics Engineer", "United States"),
]


def jobspy_scrape(results_per_search: int = 40, hours_old: int = 24 * 14) -> list[Job]:
    try:
        from jobspy import scrape_jobs  # type: ignore
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError('python-jobspy is not installed. Run: uv pip install -e ".[jobspy]"') from exc

    jobs: list[Job] = []
    for term, where in SEARCHES:
        try:
            df = scrape_jobs(
                site_name=["linkedin", "indeed", "glassdoor", "google"],
                search_term=term,
                google_search_term=f"{term} jobs near {where} since last 2 weeks",
                location=where,
                results_wanted=results_per_search,
                hours_old=hours_old,
                country_indeed="USA",
                is_remote=where == "United States",
            )
        except Exception:  # noqa: BLE001 - one site failing should not kill the sweep
            continue
        for _, r in df.iterrows():
            lo, hi = r.get("min_amount"), r.get("max_amount")
            interval = str(r.get("interval") or "").lower()
            mult = {"hourly": 2080, "monthly": 12, "weekly": 52}.get(interval, 1)
            has_pay = lo == lo and hi == hi and lo and hi  # NaN-safe
            jobs.append(
                Job(
                    source=f"jobspy:{r.get('site')}",
                    company=str(r.get("company") or ""),
                    title=str(r.get("title") or ""),
                    url=str(r.get("job_url") or ""),
                    description=html_to_text(str(r.get("description") or "")),
                    location=str(r.get("location") or ""),
                    remote=bool(r.get("is_remote")) if r.get("is_remote") == r.get("is_remote") else None,
                    posted_at=str(r.get("date_posted") or "") or None,
                    employment_type=str(r.get("job_type") or "") or None,
                    ext_id=str(r.get("id") or r.get("job_url")),
                    salary_min=float(lo) * mult if has_pay else None,
                    salary_max=float(hi) * mult if has_pay else None,
                    salary_text=f"${float(lo) * mult:,.0f} - ${float(hi) * mult:,.0f}" if has_pay else None,
                )
            )
    return jobs
