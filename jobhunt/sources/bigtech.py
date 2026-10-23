"""Big-tech career sites that don't use Greenhouse/Lever/Ashby.

* Amazon     -- amazon.jobs JSON search
* Microsoft  -- apply.careers.microsoft.com (Eightfold PCSX)
* Netflix    -- explore.jobs.netflix.net (Eightfold)
* Workday    -- NVIDIA, Salesforce, Adobe ... (public /wday/cxs JSON)
* LinkedIn   -- public guest job-search endpoints, used for Meta, Google, Apple, Uber (no usable public API of their own).
                LinkedIn lets us ask for "past week" server-side, which is exactly the freshness window we want.
"""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Callable

import httpx
from bs4 import BeautifulSoup

from ..extract import html_to_text
from ..models import Job
from ..roles import classify_role
from .base import get_json

QUERIES = ["ai engineer", "machine learning engineer", "llm", "agent", "generative ai", "data engineer", "analytics engineer", "software engineer ai"]
LINKEDIN_QUERIES = ["ai engineer", "machine learning engineer", "llm agent", "data engineer", "analytics engineer", "software engineer ai"]


def _iso_from_epoch(v) -> str | None:
    try:
        return dt.datetime.fromtimestamp(int(v), dt.timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return None


def _age(iso: str | None) -> float:
    if not iso:
        return 9999
    d = dt.datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if d.tzinfo is None:
        d = d.replace(tzinfo=dt.timezone.utc)
    return (dt.datetime.now(dt.timezone.utc) - d).total_seconds() / 86400


# ------------------------------------------------------------------ Amazon


def amazon(client: httpx.Client, cfg: dict, max_age_days: int) -> list[Job]:
    seen: dict[str, Job] = {}
    for q in cfg.get("queries", QUERIES + ["software development engineer ai", "business intelligence engineer"]):
        for page in range(3):
            data = get_json(client, "https://www.amazon.jobs/en/search.json", {
                "base_query": q, "result_limit": 100, "offset": page * 100, "sort": "recent", "country[]": "USA"})
            jobs = (data or {}).get("jobs", [])
            if not jobs:
                break
            fresh = 0
            for j in jobs:
                try:
                    posted = dt.datetime.strptime(j["posted_date"], "%B %d, %Y").replace(tzinfo=dt.timezone.utc).isoformat()
                except (KeyError, ValueError):
                    posted = None
                if _age(posted) > max_age_days:
                    continue
                fresh += 1
                desc = "\n\n".join(filter(None, [
                    html_to_text(j.get("description")),
                    "Basic Qualifications:\n" + html_to_text(j.get("basic_qualifications")) if j.get("basic_qualifications") else "",
                    "Preferred Qualifications:\n" + html_to_text(j.get("preferred_qualifications")) if j.get("preferred_qualifications") else "",
                ]))
                seen[j["id"]] = Job(
                    source="amazon", company="Amazon", title=j.get("title", ""), url="https://www.amazon.jobs" + j.get("job_path", ""),
                    description=desc, location=j.get("normalized_location") or j.get("location", ""), posted_at=posted,
                    department=j.get("job_category"), ext_id=str(j.get("id_icims") or j["id"]),
                )
            if fresh == 0:
                break
    return list(seen.values())


# ------------------------------------------------------------------ Eightfold (Microsoft PCSX / Netflix)


def eightfold(client: httpx.Client, cfg: dict, max_age_days: int) -> list[Job]:
    host, domain, name = cfg["host"], cfg["domain"], cfg["name"]
    pcsx = cfg.get("pcsx", False)
    seen: dict[str, Job] = {}
    for q in cfg.get("queries", QUERIES):
        for start in range(0, 60, 10 if pcsx else 20):
            if pcsx:
                data = get_json(client, f"https://{host}/api/pcsx/search", {"domain": domain, "query": q, "start": start, "sort_by": "timestamp"}, retries=3)
                positions = ((data or {}).get("data") or {}).get("positions", [])
            else:
                data = get_json(client, f"https://{host}/api/apply/v2/jobs", {"domain": domain, "start": start, "num": 20, "query": q, "sort_by": "timestamp"}, retries=2)
                positions = (data or {}).get("positions", [])
            if not positions:
                break
            fresh = 0
            for p in positions:
                posted = _iso_from_epoch(p.get("postedTs") or p.get("t_create"))
                if _age(posted) > max_age_days:
                    continue
                fresh += 1
                title = p.get("name") or p.get("posting_name") or ""
                pid = str(p["id"])
                if pid in seen or not classify_role(title):
                    continue
                locs = p.get("locations") or [p.get("location", "")]
                desc = p.get("job_description") or ""
                if not desc:
                    detail = get_json(client, f"https://{host}/api/apply/v2/jobs/{pid}", {"domain": domain}, retries=1) if not pcsx else \
                        get_json(client, f"https://{host}/api/pcsx/position_details", {"position_id": pid, "domain": domain, "hl": "en"}, retries=2)
                    if pcsx and detail:
                        detail = detail.get("data") or detail
                    desc = html_to_text((detail or {}).get("job_description") or (detail or {}).get("jobDescription") or "")
                else:
                    desc = html_to_text(desc)
                seen[pid] = Job(
                    source="eightfold", company=name, title=title, ext_id=pid, posted_at=posted, description=desc,
                    url=p.get("canonicalPositionUrl") or f"https://{host}/careers/job/{pid}", location=" | ".join(map(str, locs)),
                    department=p.get("department"), remote=True if str(p.get("work_location_option", "")).startswith("remote") else None,
                )
            if fresh == 0:
                break
            time.sleep(0.3)
    return list(seen.values())


# ------------------------------------------------------------------ Workday


def _workday_age(text: str) -> float:
    t = (text or "").lower()
    if "today" in t:
        return 0
    if "yesterday" in t:
        return 1
    m = re.search(r"(\d+)\+?\s*days?", t)
    return float(m.group(1)) + (0.5 if "+" in t else 0) if m else 9999


def workday(client: httpx.Client, cfg: dict, max_age_days: int) -> list[Job]:
    base = f"https://{cfg['tenant']}.{cfg.get('wd', 'wd5')}.myworkdayjobs.com"
    api = f"{base}/wday/cxs/{cfg['tenant']}/{cfg['site']}"
    seen: dict[str, Job] = {}
    for q in cfg.get("queries", QUERIES):
        for offset in range(0, 100, 20):
            try:
                r = client.post(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": q})
                r.raise_for_status()
                data = r.json()
            except (httpx.HTTPError, ValueError):
                break
            posts = data.get("jobPostings", [])
            if not posts:
                break
            for p in posts:
                path = p.get("externalPath", "")
                if path in seen or _workday_age(p.get("postedOn", "")) > max_age_days or not classify_role(p.get("title", "")):
                    continue
                detail = get_json(client, f"{api}{path}", retries=1)
                info = (detail or {}).get("jobPostingInfo") or {}
                if not info:
                    continue
                start = info.get("startDate")
                posted = f"{start}T00:00:00+00:00" if start else None
                if posted and _age(posted) > max_age_days:
                    continue
                locs = [info.get("location", "")] + list(info.get("additionalLocations") or [])
                seen[path] = Job(
                    source="workday", company=cfg["name"], title=info.get("title", p["title"]), url=info.get("externalUrl") or f"{base}/{cfg['site']}{path}",
                    description=html_to_text(info.get("jobDescription")), location=" | ".join(l for l in locs if l), posted_at=posted,
                    employment_type=info.get("timeType"), ext_id=info.get("jobReqId") or path,
                    remote=True if "remote" in (info.get("remoteType") or "").lower() else None,
                )
            time.sleep(0.2)
    return list(seen.values())


# ------------------------------------------------------------------ Apple


def apple(client: httpx.Client, cfg: dict, max_age_days: int) -> list[Job]:
    tok = client.get("https://jobs.apple.com/api/v1/CSRFToken").headers.get("x-apple-csrf-token", "")
    headers = {"X-Apple-CSRF-Token": tok, "Content-Type": "application/json", "browserlocale": "en-us", "locale": "en_US", "Referer": "https://jobs.apple.com/en-us/search", "Accept": "application/json"}
    fmt = {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"}
    hits: list[dict] = []
    for page in range(1, 80):
        r = client.post("https://jobs.apple.com/api/v1/search", headers=headers,
                        json={"query": "", "filters": {"locations": ["postLocation-USA"]}, "page": page, "locale": "en-us", "sort": "newest", "format": fmt})
        if r.status_code != 200:
            break
        results = r.json().get("res", {}).get("searchResults", [])
        if not results:
            break
        oldest = 0.0
        for x in results:
            posted = x.get("postDateInGMT")
            age = _age(posted)
            oldest = max(oldest, age if posted else 0)
            if age <= max_age_days and classify_role(x.get("postingTitle", "")):
                hits.append(x)
        if oldest > max_age_days + 1:
            break
        time.sleep(0.15)
    jobs: list[Job] = []
    for x in hits:
        d = get_json(client, f"https://jobs.apple.com/api/v1/jobDetails/{x['positionId']}", {"locale": "en-us"}, retries=1)
        info = (d or {}).get("res") or {}
        if not info:
            continue
        desc = "\n\n".join(filter(None, [
            info.get("jobSummary", ""), info.get("description", ""),
            "Responsibilities:\n" + info["responsibilities"] if info.get("responsibilities") else "",
            "Minimum Qualifications:\n" + info["minimumQualifications"] if info.get("minimumQualifications") else "",
            "Preferred Qualifications:\n" + info["preferredQualifications"] if info.get("preferredQualifications") else "",
        ]))
        locs = [f"{l.get('name') or l.get('city', '')}, {l.get('stateProvince', '')}".strip(", ") for l in (info.get("locations") or x.get("locations") or [])]
        jobs.append(Job(
            source="apple", company="Apple", title=x["postingTitle"], ext_id=x["positionId"], posted_at=x.get("postDateInGMT"),
            url=f"https://jobs.apple.com/en-us/details/{x['positionId']}/{x.get('transformedPostingTitle', '')}",
            description=html_to_text(desc), location=" | ".join(locs) + (", United States" if locs else ""), department=(x.get("team") or {}).get("teamName"),
            remote=True if info.get("homeOffice") else None,
        ))
        time.sleep(0.15)
    return jobs


# ------------------------------------------------------------------ LinkedIn guest search


def linkedin(client: httpx.Client, cfg: dict, max_age_days: int) -> list[Job]:
    """Public guest endpoints. Best effort: LinkedIn rate-limits (HTTP 429); we stop politely when that happens."""
    seconds = max(1, int(max_age_days)) * 86400
    cards: dict[str, dict] = {}
    for q in cfg.get("queries", LINKEDIN_QUERIES):
        for start in range(0, 50, 25):
            r = client.get(
                "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search",
                params={"keywords": q, "location": "United States", "f_C": cfg["company_id"], "f_TPR": f"r{seconds}", "start": start},
                headers={"Accept": "text/html"},
            )
            if r.status_code == 429:
                time.sleep(5)
                break
            if r.status_code != 200 or "base-search-card" not in r.text:
                break
            soup = BeautifulSoup(r.text, "html.parser")
            found = 0
            for li in soup.find_all("li"):
                card = li.find("div", class_="base-search-card")
                if not card:
                    continue
                found += 1
                m = re.search(r"jobPosting:(\d+)", card.get("data-entity-urn", ""))
                title_el = card.find(class_="base-search-card__title")
                if not m or not title_el:
                    continue
                title = title_el.get_text(strip=True)
                if not classify_role(title):
                    continue
                loc = card.find(class_="job-search-card__location")
                t = card.find("time")
                cards[m.group(1)] = {"title": title, "location": loc.get_text(strip=True) if loc else "", "date": t.get("datetime") if t else None}
            if found < 10:
                break
            time.sleep(1.0)
    jobs: list[Job] = []
    for jid, c in cards.items():
        time.sleep(0.8)
        r = client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}", headers={"Accept": "text/html"})
        if r.status_code == 429:
            time.sleep(6)
            r = client.get(f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{jid}", headers={"Accept": "text/html"})
        if r.status_code != 200:
            continue
        soup = BeautifulSoup(r.text, "html.parser")
        body = soup.find(class_="show-more-less-html__markup")
        desc = html_to_text(str(body)) if body else ""
        jobs.append(Job(
            source="linkedin", company=cfg["name"], title=c["title"], url=f"https://www.linkedin.com/jobs/view/{jid}",
            description=desc, location=c["location"], posted_at=f"{c['date']}T00:00:00+00:00" if c["date"] else None, ext_id=jid,
        ))
    return jobs


FETCHERS: dict[str, Callable[[httpx.Client, dict, int], list[Job]]] = {
    "amazon": amazon, "apple": apple, "eightfold": eightfold, "workday": workday, "linkedin": linkedin,
}
