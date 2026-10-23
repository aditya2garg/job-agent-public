"""Public applicant-tracking-system job board APIs: Greenhouse, Lever, Ashby.

These are the same JSON feeds the companies' own careers pages use. No scraping of HTML needed,
and they frequently include pay-transparency ranges.
"""

from __future__ import annotations

from typing import Any

import httpx

from ..extract import classify_location, html_to_text
from ..models import Job
from .base import get_json


def _hints(location: str, remote: bool | None) -> tuple[str, ...]:
    kind = classify_location(location, remote).kind
    if kind == "bay_area":
        return ("san francisco", "bay area", "california", "sf", "zone 1", "tier 1", "us metro")
    if kind == "austin":
        return ("austin", "texas", "zone 2", "tier 2", "all other")
    return ("remote", "all other", "united states", "us ")


def _annual(amount: Any, interval: str | None) -> float | None:
    if amount in (None, ""):
        return None
    v = float(amount)
    interval = (interval or "").lower()
    if "hour" in interval:
        v *= 2080
    elif "month" in interval:
        v *= 12
    elif "week" in interval:
        v *= 52
    return v


# ------------------------------------------------------------------- Greenhouse


def greenhouse(client: httpx.Client, token: str, name: str | None = None) -> list[Job]:
    data = get_json(client, f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs", {"content": "true", "pay_transparency": "true"})
    if not data:
        return []
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        offices = [o.get("location") or o.get("name") for o in j.get("offices", []) if o]
        loc = (j.get("location") or {}).get("name", "")
        # Offices often hold the full multi-city list when `location` is a summary.
        if offices and len(", ".join(filter(None, offices))) > len(loc):
            loc = " | ".join(filter(None, offices))
        meta = {m.get("name"): m.get("value") for m in j.get("metadata") or [] if m}
        workplace = str(meta.get("Location Type") or meta.get("Workplace Type") or "").lower()
        remote = True if "remote" in workplace else None
        sal_min = sal_max = None
        sal_text = None
        ranges = [r for r in j.get("pay_input_ranges") or [] if r.get("max_cents") and r.get("currency_type", "USD") == "USD"]
        if ranges:
            hints = _hints(loc, remote)
            local = [r for r in ranges if any(h in f"{r.get('title', '')} {r.get('blurb', '')}".lower() for h in hints)]
            best = max(local or ranges, key=lambda r: r.get("max_cents") or 0)
            if best.get("max_cents"):
                sal_min, sal_max = (best.get("min_cents") or 0) / 100, best["max_cents"] / 100
                sal_text = f"${sal_min:,.0f} - ${sal_max:,.0f}"
        jobs.append(
            Job(
                source="greenhouse",
                company=j.get("company_name") or name or token,
                title=(j.get("title") or "").strip(),
                url=j.get("absolute_url", ""),
                description=html_to_text(j.get("content")),
                location=loc,
                remote=remote,
                posted_at=j.get("first_published") or j.get("updated_at"),
                department=((j.get("departments") or [{}])[0] or {}).get("name"),
                ext_id=str(j.get("id")),
                salary_min=sal_min,
                salary_max=sal_max,
                salary_text=sal_text,
            )
        )
    return jobs


# ------------------------------------------------------------------- Lever


def lever(client: httpx.Client, token: str, name: str | None = None) -> list[Job]:
    data = get_json(client, f"https://api.lever.co/v0/postings/{token}", {"mode": "json"})
    if not data:
        return []
    jobs: list[Job] = []
    for j in data:
        cats = j.get("categories") or {}
        loc = cats.get("location") or ""
        if cats.get("allLocations"):
            loc = " | ".join(cats["allLocations"])
        wt = (j.get("workplaceType") or "").lower()
        sal = j.get("salaryRange") or {}
        interval = sal.get("interval") or ""
        lo, hi = _annual(sal.get("min"), interval), _annual(sal.get("max"), interval)
        if sal.get("currency") and sal["currency"].upper() != "USD":
            lo = hi = None
        desc_parts = [j.get("descriptionPlain") or html_to_text(j.get("description"))]
        for lst in j.get("lists") or []:
            desc_parts.append(f"\n{lst.get('text', '')}:\n" + html_to_text(lst.get("content")))
        desc_parts.append(j.get("additionalPlain") or html_to_text(j.get("additional")))
        if j.get("salaryDescriptionPlain"):
            desc_parts.append(j["salaryDescriptionPlain"])
        created = j.get("createdAt")
        jobs.append(
            Job(
                source="lever",
                company=name or token,
                title=(j.get("text") or "").strip(),
                url=j.get("hostedUrl", ""),
                description="\n".join(p for p in desc_parts if p),
                location=loc,
                remote=True if wt == "remote" else None,
                posted_at=_ms_to_iso(created),
                employment_type=cats.get("commitment"),
                department=cats.get("team") or cats.get("department"),
                ext_id=j.get("id"),
                salary_min=lo,
                salary_max=hi,
                salary_text=(f"${lo:,.0f} - ${hi:,.0f}" if lo and hi else None),
            )
        )
    return jobs


def _ms_to_iso(ms: Any) -> str | None:
    if not ms:
        return None
    import datetime as dt

    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, dt.timezone.utc).isoformat()
    except (ValueError, OSError):
        return None


# ------------------------------------------------------------------- Ashby


def ashby(client: httpx.Client, token: str, name: str | None = None) -> list[Job]:
    data = get_json(client, f"https://api.ashbyhq.com/posting-api/job-board/{token}", {"includeCompensation": "true"})
    if not data:
        return []
    jobs: list[Job] = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locs = [j.get("location") or ""] + [s.get("location", "") if isinstance(s, dict) else str(s) for s in j.get("secondaryLocations") or []]
        loc = " | ".join(dict.fromkeys(l for l in locs if l))
        comp = j.get("compensation") or {}
        lo = hi = None
        text = comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary")
        remote = j.get("isRemote")
        if remote is None and (j.get("workplaceType") or "").lower() == "remote":
            remote = True
        hints = _hints(loc, remote)
        candidates: list[tuple[str, dict]] = []
        for tier in comp.get("compensationTiers") or []:
            for c in tier.get("components") or []:
                if c.get("compensationType") == "Salary" and c.get("maxValue") and c.get("currencyCode", "USD") == "USD":
                    candidates.append(((tier.get("tierSummary") or "").lower(), c))
        if not candidates:
            for c in comp.get("summaryComponents") or []:
                if c.get("compensationType") == "Salary" and c.get("maxValue") and c.get("currencyCode", "USD") == "USD":
                    candidates.append(("", c))
        if candidates:
            local = [c for c in candidates if any(h in c[0] for h in hints)]
            best = max(local or candidates, key=lambda c: c[1]["maxValue"])[1]
            lo, hi = _annual(best.get("minValue"), best.get("interval")), _annual(best.get("maxValue"), best.get("interval"))
            text = f"${lo:,.0f} - ${hi:,.0f}"
        jobs.append(
            Job(
                source="ashby",
                company=name or token,
                title=(j.get("title") or "").strip(),
                url=j.get("jobUrl", ""),
                description=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml")),
                location=loc,
                remote=remote,
                posted_at=j.get("publishedAt"),
                employment_type=j.get("employmentType"),
                department=j.get("department") or j.get("team"),
                ext_id=j.get("id"),
                salary_min=lo,
                salary_max=hi,
                salary_text=text,
            )
        )
    return jobs


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby}
