"""Remote-job aggregators with open JSON APIs: Remotive, RemoteOK, Himalayas, plus HN 'Who is hiring'."""

from __future__ import annotations

import datetime as dt
import re
from typing import Any

import httpx

from ..extract import html_to_text
from ..models import Job
from .base import get_json

QUERIES = ["ai engineer", "llm", "agentic", "machine learning", "data engineer", "analytics engineer", "software engineer python"]


def _iso(epoch: Any) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(epoch), dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def remotive(client: httpx.Client) -> list[Job]:
    seen: dict[str, Job] = {}
    for cat in ("software-dev", "data", "ai-ml"):
        data = get_json(client, "https://remotive.com/api/remote-jobs", {"category": cat, "limit": 400})
        for j in (data or {}).get("jobs", []):
            salary = j.get("salary") or ""
            seen[str(j["id"])] = Job(
                source="remotive",
                company=j.get("company_name", ""),
                title=j.get("title", ""),
                url=j.get("url", ""),
                description=html_to_text(j.get("description")) + (f"\nSalary: {salary}" if salary else ""),
                location=f"Remote - {j.get('candidate_required_location') or 'Worldwide'}",
                remote=True,
                posted_at=j.get("publication_date"),
                employment_type=j.get("job_type"),
                ext_id=str(j["id"]),
                salary_text=salary or None,
            )
    for q in QUERIES[:4]:
        data = get_json(client, "https://remotive.com/api/remote-jobs", {"search": q, "limit": 100})
        for j in (data or {}).get("jobs", []):
            if str(j["id"]) in seen:
                continue
            salary = j.get("salary") or ""
            seen[str(j["id"])] = Job(
                source="remotive", company=j.get("company_name", ""), title=j.get("title", ""), url=j.get("url", ""),
                description=html_to_text(j.get("description")) + (f"\nSalary: {salary}" if salary else ""),
                location=f"Remote - {j.get('candidate_required_location') or 'Worldwide'}", remote=True,
                posted_at=j.get("publication_date"), employment_type=j.get("job_type"), ext_id=str(j["id"]),
                salary_text=salary or None,
            )
    return list(seen.values())


def remoteok(client: httpx.Client) -> list[Job]:
    data = get_json(client, "https://remoteok.com/api") or []
    jobs: list[Job] = []
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue
        lo, hi = float(j.get("salary_min") or 0), float(j.get("salary_max") or 0)
        jobs.append(
            Job(
                source="remoteok",
                company=j.get("company", ""),
                title=j.get("position", ""),
                url=j.get("url") or j.get("apply_url", ""),
                description=html_to_text(j.get("description")) + "\nTags: " + ", ".join(j.get("tags") or []),
                location=f"Remote - {j.get('location') or 'Worldwide'}",
                remote=True,
                posted_at=j.get("date"),
                ext_id=str(j.get("id")),
                salary_min=lo if lo >= 40000 else None,
                salary_max=hi if hi >= 40000 else None,
                salary_text=f"${lo:,.0f} - ${hi:,.0f}" if lo >= 40000 and hi >= 40000 else None,
            )
        )
    return jobs


def himalayas(client: httpx.Client, pages: int = 8) -> list[Job]:
    jobs: list[Job] = []
    for q in ("ai engineer", "llm", "machine learning", "data engineer", "analytics engineer"):
        offset = 0
        for _ in range(max(1, pages // 5)):
            data = get_json(client, "https://himalayas.app/jobs/api/search", {"q": q, "sort": "recent", "page": offset // 20 + 1})
            if not data:
                break
            for j in data.get("jobs", []):
                restr = j.get("locationRestrictions") or []
                lo, hi = j.get("minSalary"), j.get("maxSalary")
                usd = (j.get("currency") or "USD") == "USD" and (j.get("salaryPeriod") or "annual") == "annual"
                jobs.append(
                    Job(
                        source="himalayas",
                        company=j.get("companyName", ""),
                        title=j.get("title", ""),
                        url=j.get("applicationLink") or j.get("guid", ""),
                        description=html_to_text(j.get("description")),
                        location="Remote - " + (", ".join(restr) if restr else "Worldwide"),
                        remote=True,
                        posted_at=_iso(j.get("pubDate")),
                        employment_type=j.get("employmentType"),
                        ext_id=j.get("guid"),
                        salary_min=float(lo) if lo and usd else None,
                        salary_max=float(hi) if hi and usd else None,
                        salary_text=f"${lo:,.0f} - ${hi:,.0f}" if lo and hi and usd else None,
                    )
                )
            offset += 20
    return jobs


_ROLE_WORD = re.compile(r"engineer|developer|scientist|architect|ai\b|ml\b|machine learning|data|analytics|swe|founding", re.IGNORECASE)


def hn_whos_hiring(client: httpx.Client, months: int = 2) -> list[Job]:
    """Parse the latest 'Ask HN: Who is hiring?' threads. Top-level comments look like
    `Company | Role | Location | REMOTE | $salary` on the first line."""
    stories = get_json(client, "https://hn.algolia.com/api/v1/search_by_date", {"query": "Ask HN: Who is hiring?", "tags": "story,author_whoishiring", "hitsPerPage": 6}) or {}
    thread_ids = [h["objectID"] for h in stories.get("hits", []) if "who is hiring" in h.get("title", "").lower()][:months]
    jobs: list[Job] = []
    for tid in thread_ids:
        item = get_json(client, f"https://hn.algolia.com/api/v1/items/{tid}")
        for c in (item or {}).get("children", []):
            body = html_to_text(c.get("text"))
            if not body:
                continue
            first = body.split("\n", 1)[0]
            parts = [p.strip() for p in first.split("|")]
            if len(parts) < 3:
                continue
            company = parts[0][:80]
            roles = [p for p in parts[1:] if _ROLE_WORD.search(p) and len(p) < 120]
            if not roles:
                continue
            title = roles[0]
            loc_parts = [p for p in parts[1:] if p not in roles and not p.startswith("$") and len(p) < 80]
            location = " | ".join(loc_parts[:3])
            remote = any(re.search(r"\bremote\b", p, re.IGNORECASE) for p in parts)
            jobs.append(
                Job(
                    source="hn",
                    company=company,
                    title=title,
                    url=f"https://news.ycombinator.com/item?id={c['id']}",
                    description=body,
                    location=location or ("Remote" if remote else ""),
                    remote=True if remote and not re.search(r"\bnot remote\b|no remote", first, re.I) else None,
                    posted_at=c.get("created_at"),
                    ext_id=str(c["id"]),
                )
            )
    return jobs


BOARDS = {"remotive": remotive, "remoteok": remoteok, "himalayas": himalayas, "hn": hn_whos_hiring}
