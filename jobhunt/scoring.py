"""Explainable match scoring.

final = weighted sum of six 0-100 dimensions, then clamped by hard gates (excluded role, non-US,
clearance, pay far below your floor ...).  Every number is traceable in `details` so the UI/CLI can
show *why* a job scored the way it did.

Dimensions
  role        how close the title is to what you want, adjusted by what the JD really asks for
  skills      weighted coverage of the skills the JD names by your honest proficiency in each,
              with requirement-section weighting and penalties for hard gaps
  semantic    TF-IDF cosine between your resume and the posting (percentile within the batch)
  seniority   title level and "N+ years" requirement vs. your relevant experience
  compensation  posted pay vs. your preferred / minimum total comp
  location    Bay Area / Austin / US-remote vs elsewhere
"""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from sklearn.feature_extraction.text import TfidfVectorizer

from . import config
from .extract import (
    LocationInfo,
    Salary,
    classify_location,
    extract_skills,
    find_flags,
    job_location_hints,
    parse_salary,
    years_required,
)
from .models import Job
from .roles import RoleMatch, classify_role, excluded_by, soft_penalties
from .taxonomy import AI_CATEGORIES, BY_ID

_EQUITY_RE = re.compile(r"\bequity\b|\brsus?\b|stock options|restricted stock|annual bonus|performance bonus|signing bonus|\bbonus\b", re.IGNORECASE)
GENERIC_DATA = {"python", "sql"}


@dataclass
class Context:
    prefs: dict[str, Any]
    profile: dict[str, Any]
    seniority_rules: list[tuple[re.Pattern[str], float]]
    years: dict[str, float]

    @classmethod
    def load(cls) -> "Context":
        prefs, prof = config.preferences(), config.profile()
        rules = [(re.compile(r["pattern"], re.IGNORECASE), float(r["score"])) for r in prefs["seniority"]["title_scores"]]
        tl = prof["timeline"]
        today = dt.date.today()

        def yrs(start: str) -> float:
            y, m = map(int, start.split("-"))
            return round((today - dt.date(y, m, 1)).days / 365.25, 1)

        ai, data, total = yrs(tl["ai_engineering_start"]), yrs(tl["data_start"]), yrs(tl["career_start"])
        years = {
            "ai": round(ai + 0.4 * max(0.0, data - ai), 1),
            "ai_pure": ai,
            "data": data,
            "software": float(tl.get("software_equivalent_years", 3.5)),
            "general": data,
            "total": total,
        }
        return cls(prefs, prof, rules, years)


# ------------------------------------------------------------------ dimension scorers


def _clip(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def score_skills(job: Job, hits: dict, ctx: Context) -> tuple[float, dict[str, Any]]:
    prof = ctx.profile["proficiency"]
    num = den = 0.0
    rows: list[dict[str, Any]] = []
    for sid, hit in hits.items():
        sk = BY_ID[sid]
        w = sk.weight * hit.mult * (1 + 0.15 * min(hit.mentions - 1, 3) if hit.mentions else 1)
        p = float(prof.get(sid, 0.0))
        num += w * p
        den += w
        rows.append(
            {"id": sid, "label": sk.label, "category": sk.category, "weight": round(w, 2), "proficiency": p,
             "section": hit.section, "in_title": hit.in_title, "mentions": hit.mentions}
        )
    prior = 3.0
    base = 100 * (num + prior * 0.5) / (den + prior)

    critical = [r for r in rows if r["section"] == "required" and r["weight"] >= 2.5 and r["proficiency"] < 0.2 and r["category"] != "data"]
    penalty = max(0.75, 0.93 ** len(critical))
    score = _clip(base * penalty)

    matched = sorted((r for r in rows if r["proficiency"] >= 0.6), key=lambda r: -r["weight"])
    gaps = sorted((r for r in rows if r["proficiency"] < 0.35), key=lambda r: -r["weight"])
    partial = [r for r in rows if 0.35 <= r["proficiency"] < 0.6]
    total_w = sum(r["weight"] for r in rows) or 1.0
    ai_w = sum(r["weight"] for r in rows if r["category"] in AI_CATEGORIES or r["category"] == "ml")
    data_w = sum(r["weight"] for r in rows if r["category"] == "data" and r["id"] not in GENERIC_DATA)
    return score, {
        "base": round(base, 1),
        "penalty": round(penalty, 2),
        "n_skills": len(rows),
        "matched": matched,
        "partial": partial,
        "gaps": gaps,
        "critical_gaps": [r["label"] for r in critical],
        "ai_share": round(ai_w / total_w, 2),
        "data_share": round(data_w / total_w, 2),
    }


def score_role(title: str, role: RoleMatch | None, skill_info: dict[str, Any], excluded: str | None) -> tuple[float, dict[str, Any]]:
    if role is None:
        return 0.0, {"name": None, "tier": "excluded", "reason": f"title excluded by '{excluded}'" if excluded else "title does not match any target role"}
    focus_ai = min(1.0, skill_info["ai_share"] / 0.40)
    focus_data = min(1.0, skill_info["data_share"] / 0.35)
    focus = max(focus_ai, 0.9 * focus_data)
    cfg = role.cfg
    score = role.base
    reason = f"title matches {role.name} ({role.tier})"
    if role.tier in ("preferred", "adjacent") and cfg.get("ai_boost"):
        score += cfg["ai_boost"] * focus_ai
        reason += f"; JD is {int(focus_ai * 100)}% AI-focused"
    elif role.tier == "longshot":
        score += cfg.get("focus_boost", 35) * focus
        score = min(score, cfg.get("max_score", 75))
        reason += f"; judged on skills: JD AI/data focus {int(focus * 100)}%"
        if focus < 0.15:
            score = min(score, 36)
            reason += " (generic role, little AI/data content -> capped)"
    elif role.tier == "preferred" and role.name == "AI Engineer" and skill_info["ai_share"] < 0.12 and skill_info["n_skills"] >= 4:
        score -= 10
        reason += "; but the JD has little actual AI content"
    penalties = soft_penalties(title)
    if penalties:
        score -= min(30.0, sum(p for p, _ in penalties))
        reason += "; " + ", ".join(label for _, label in penalties)
    return _clip(score), {"name": role.name, "tier": role.tier, "reason": reason, "focus": round(focus, 2), "penalties": [l for _, l in penalties]}


def score_seniority(title: str, years_req: float | None, fam: str, ctx: Context) -> tuple[float, dict[str, Any]]:
    level = float(ctx.prefs["seniority"]["default_score"])
    level_label = "unspecified"
    for pat, sc in ctx.seniority_rules:
        m = pat.search(title)
        if m:
            level, level_label = sc, m.group(0).strip().lower()
            break
    mine = ctx.years.get(fam, ctx.years["general"])
    if years_req is None:
        yscore, note = 85.0, "no explicit years requirement"
    else:
        gap = years_req - mine
        yscore = 100.0 if gap <= 0 else _clip(100 - 14 * gap)
        note = f"asks {years_req:g}+ yrs ({fam}); your relevant experience ~{mine:g} yrs"
    return 0.55 * level + 0.45 * yscore, {"title_level": level_label, "level_score": level, "years_required": years_req, "years_family": fam, "your_years": mine, "note": note}


def score_comp(salary: Salary, equity: bool, prefs: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    c = prefs["compensation"]
    if not salary.known:
        return float(c["unknown_score"]), {"known": False, "note": "no pay posted -- verify before investing time"}
    mult = float(c["equity_multiplier"]) if equity else 1.0
    lo, hi = salary.low * mult, salary.high * mult
    x = 0.5 * ((lo + hi) / 2) + 0.5 * hi
    pref, floor, hard = c["preferred_min"], c["floor"], c["hard_floor"]
    if x >= pref:
        s = 100.0
    elif x >= floor:
        s = 60 + 40 * (x - floor) / (pref - floor)
    elif x >= hard:
        s = 60 * (x - hard) / (floor - hard)
    else:
        s = 0.0
    note = f"posted ${salary.low:,.0f}-${salary.high:,.0f}"
    if mult != 1.0:
        note += f" (x{mult:.2f} for equity/bonus => ${lo:,.0f}-${hi:,.0f})"
    if salary.multiple:
        note += "; posting lists several ranges"
    return s, {
        "known": True, "low": salary.low, "high": salary.high, "eff_low": round(lo), "eff_high": round(hi), "score_basis": round(x),
        "multiplier": mult, "meets_preferred": hi >= pref, "below_floor": hi < floor, "note": note, "source": salary.source,
    }


def score_location(loc: LocationInfo, prefs: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    p = prefs["locations"]
    pref = set(p["preferred"])
    if loc.kind in pref:
        s = 100.0
    elif loc.kind == "us_other":
        s = float(p["us_other_score"])
    elif loc.kind == "unknown":
        s = float(p["unknown_score"])
    else:
        s = 0.0
    labels = {"bay_area": "SF Bay Area", "austin": "Austin", "remote_us": "Remote (US-eligible)", "us_other": "Elsewhere in US (relocation)", "non_us": "Outside US", "unknown": "Unclear"}
    return s, {"kind": loc.kind, "label": labels[loc.kind], "remote": loc.remote, "matched": loc.matched}


# ------------------------------------------------------------------ evidence / narrative


def match_evidence(hits: dict, ctx: Context, limit: int = 4) -> list[dict[str, Any]]:
    scored = []
    for ev in ctx.profile.get("evidence", []):
        overlap = [t for t in ev["themes"] if t in hits]
        if not overlap:
            continue
        s = sum(BY_ID[t].weight * hits[t].mult for t in overlap)
        scored.append((s, ev, overlap))
    scored.sort(key=lambda t: -t[0])
    return [
        {"id": ev["id"], "statement": ev["statement"], "themes": [BY_ID[t].label for t in overlap]}
        for _, ev, overlap in scored[:limit]
    ]


def verdict_for(total: float, prefs: dict[str, Any]) -> str:
    v = prefs["verdicts"]
    if total >= v["strong"]:
        return "Apply now"
    if total >= v["good"]:
        return "Apply"
    if total >= v["stretch"]:
        return "Stretch"
    return "Skip"


def _headline(dims: dict[str, float], d: dict[str, Any]) -> tuple[list[str], list[str]]:
    pos: list[str] = []
    neg: list[str] = []
    if dims["role"] >= 85:
        pos.append(f"Title is a direct hit ({d['role']['name']})")
    elif d["role"]["tier"] == "longshot" and dims["role"] >= 60:
        pos.append(f"Long-shot {d['role']['name']} role, but the JD is genuinely AI/data heavy")
    elif d["role"]["tier"] == "longshot":
        neg.append(f"Long-shot {d['role']['name']} role with limited AI/data content")
    m = d["skills"]["matched"][:4]
    if m:
        pos.append("Strong overlap on " + ", ".join(r["label"] for r in m))
    g = [r for r in d["skills"]["gaps"] if r["weight"] >= 2][:3]
    if g:
        neg.append("Gaps: " + ", ".join(r["label"] for r in g))
    if d["salary"].get("known"):
        (pos if d["salary"]["meets_preferred"] else neg).append(d["salary"]["note"])
    else:
        neg.append("Pay not disclosed")
    if d["location"]["kind"] in ("bay_area", "austin", "remote_us"):
        pos.append(d["location"]["label"])
    else:
        neg.append(f"Location: {d['location']['label']}")
    for _, msg in d["flags"]:
        neg.append(msg)
    return pos, neg


# ------------------------------------------------------------------ main entry points


def analyze(job: Job, ctx: Context) -> dict[str, Any]:
    """Everything except the corpus-relative semantic score."""
    title = job.title
    desc = job.description or ""
    role = classify_role(title)
    excl = excluded_by(title)
    loc = classify_location(job.location, job.remote, desc)

    if job.salary_min and job.salary_max:
        salary = Salary(job.salary_min, job.salary_max, job.salary_text, source="structured")
    else:
        salary = parse_salary(desc, job_location_hints(job))
    equity = bool(_EQUITY_RE.search(desc)) or "equity" in (job.salary_text or "").lower()

    hits = extract_skills(title, desc)
    sk_score, sk_info = score_skills(job, hits, ctx)
    role_score, role_info = score_role(title, role, sk_info, excl)
    yreq, yfam = years_required(desc)
    if yfam == "general" and role:
        yfam = {"preferred": "ai", "adjacent": "ai"}.get(role.tier, "data" if "data" in role.name.lower() else "software")
    sen_score, sen_info = score_seniority(title, yreq, yfam, ctx)
    comp_score, comp_info = score_comp(salary, equity, ctx.prefs)
    loc_score, loc_info = score_location(loc, ctx.prefs)
    flags = find_flags(desc)
    if job.employment_type and re.search(r"contract|temp|intern", job.employment_type, re.IGNORECASE):
        flags.append(("contract", "Contract / temporary position"))

    return {
        "dims": {"role": role_score, "skills": sk_score, "seniority": sen_score, "compensation": comp_score, "location": loc_score},
        "details": {
            "role": role_info, "skills": sk_info, "seniority": sen_info, "salary": comp_info, "location": loc_info,
            "flags": flags, "evidence": match_evidence(hits, ctx),
        },
    }


def combine(analysis: dict[str, Any], semantic: float, ctx: Context) -> dict[str, Any]:
    w = ctx.prefs["weights"]
    dims = {**analysis["dims"], "semantic": semantic}
    heuristic = sum(w[k] * dims[k] for k in w)
    d = analysis["details"]
    cap, caps = 100.0, []

    def gate(limit: float, why: str) -> None:
        nonlocal cap
        if limit < cap:
            cap = limit
        caps.append(why)

    if d["role"]["tier"] == "excluded":
        gate(20, "role excluded")
    if d["location"]["kind"] == "non_us":
        gate(25, "outside the US")
    keys = {k for k, _ in d["flags"]}
    if keys & {"clearance", "citizenship"}:
        gate(40, "clearance / citizenship requirement")
    if "phd" in keys:
        gate(55, "PhD requirement")
    if "contract" in keys:
        gate(60, "contract role")
    if d["salary"].get("below_floor"):
        gate(58, "posted pay below your minimum")
    if d["skills"]["n_skills"] == 0:
        gate(45, "no recognizable skills in the posting (thin description)")
    heuristic_final = min(heuristic, cap)
    pos, neg = _headline(dims, d)
    return {
        "heuristic": round(heuristic_final, 1),
        "final": round(heuristic_final, 1),
        "verdict": verdict_for(heuristic_final, ctx.prefs),
        "role_name": d["role"]["name"],
        "role_tier": d["role"]["tier"],
        "breakdown": {k: round(v, 1) for k, v in dims.items()},
        "details": {**d, "caps": caps, "cap": cap, "raw_weighted": round(heuristic, 1), "pros": pos, "cons": neg},
    }


def semantic_scores(jobs: list[Job], resume: str) -> list[float]:
    """TF-IDF cosine similarity to the resume, converted to a percentile (0-100) within the batch."""
    if not jobs:
        return []
    docs = [f"{j.title}. {j.title}. {j.description[:6000]}" for j in jobs]
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2, max_df=0.6, sublinear_tf=True, max_features=60000)
    try:
        mat = vec.fit_transform(docs + [resume])
    except ValueError:
        return [50.0] * len(jobs)
    sims = (mat[:-1] @ mat[-1].T).toarray().ravel()
    order = sims.argsort().argsort()
    pct = 100.0 * order / max(1, len(sims) - 1)
    return [float(round(p, 1)) for p in pct]


def score_jobs(jobs: list[Job]) -> list[dict[str, Any]]:
    ctx = Context.load()
    sem = semantic_scores(jobs, config.resume_text())
    out = []
    for job, s in zip(jobs, sem):
        res = combine(analyze(job, ctx), s, ctx)
        res["job_id"] = job.id
        out.append(res)
    return out


def content_hash(job: Job) -> str:
    return hashlib.sha1(f"{job.title}|{job.location}|{job.description}".encode()).hexdigest()[:16]

