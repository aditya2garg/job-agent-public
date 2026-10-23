"""Optional LLM deep-dive on the best heuristic matches.

The heuristic score is cheap and runs on thousands of postings.  This stage reads the full posting like a
critical recruiter would and returns a structured second opinion (fit score, role reality check, gaps with
mitigations, resume tweaks, interview prep).  Results are cached per posting content hash.
"""

from __future__ import annotations

import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from . import config, db
from .scoring import content_hash

SYSTEM = (
    "You are a skeptical senior technical recruiter and hiring manager evaluating how well ONE candidate fits ONE job posting. "
    "Be calibrated and critical: most postings are not a strong fit. Never invent experience the candidate does not have. "
    "Base every claim on the posting text or the candidate profile provided. Respond with a single JSON object and nothing else."
)

SCHEMA_HINT = """{
  "fit_score": <0-100 integer, holistic probability-weighted fit; 85+ only for near-ideal matches>,
  "recommendation": "apply_now" | "apply" | "stretch" | "skip",
  "summary": "<2-3 sentences, plain language>",
  "role_reality_check": "<what the day-to-day work really is; call out if the title oversells (e.g. sales engineering, research, internal tooling)>",
  "strengths": ["<specific reason this candidate wins, citing resume evidence>", ...],
  "gaps": [{"gap": "<missing requirement>", "severity": "blocker" | "major" | "minor", "mitigation": "<how to address in resume/interview>"}],
  "red_flags": ["<anything about the posting/company/comp/location worth worrying about>"],
  "resume_tweaks": ["<concrete edit to emphasise for THIS job>", ...],
  "interview_prep": ["<likely question or topic to prepare>", ...],
  "comp_note": "<comment on pay vs the candidate's targets, or 'not disclosed'>"
}"""


GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta"


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or None


def provider() -> tuple[str, str] | None:
    """(provider, model) when a Gemini API key is configured, else None."""
    if not api_key():
        return None
    cfg = config.preferences().get("llm", {})
    return "gemini", os.environ.get("JOBHUNT_GEMINI_MODEL", cfg.get("gemini_model", "gemini-2.5-flash"))


def candidate_brief() -> str:
    prof, prefs = config.profile(), config.preferences()
    skills = ", ".join(f"{k} ({v:.2f})" for k, v in sorted(prof["proficiency"].items(), key=lambda kv: -kv[1]) if v >= 0.3)
    none = ", ".join(k for k, v in prof["proficiency"].items() if v < 0.2)
    ev = "\n".join(f"- {e['statement']}" for e in prof.get("evidence", []))
    c = prefs["compensation"]
    return (
        f"CANDIDATE: {prof['name']} -- {prof['headline']}\n{prof['summary'].strip()}\n\n"
        f"Proficiency (0-1) where >=0.3: {skills}\nLittle/no experience: {none}\nKnown gaps: " + "; ".join(prof.get("known_gaps", [])) + "\n\n"
        f"Resume proof points:\n{ev}\n\n"
        f"PREFERENCES: roles = AI Engineer, Agentic AI Engineer, Analytics Engineer (AI); long shots = Data Engineer, Software Engineer (judge on real skill fit). "
        f"Locations = SF Bay Area, Austin, Remote(US). Total comp target >= ${c['preferred_min']:,}; minimum acceptable ${c['floor']:,}."
    )


def build_prompt(job: dict[str, Any], heuristic: dict[str, Any]) -> str:
    d = heuristic["details"]
    sk = d["skills"]
    summary = (
        f"Heuristic score {heuristic['heuristic']} ({heuristic['verdict']}); breakdown {heuristic['breakdown']}. "
        f"Matched: {', '.join(r['label'] for r in sk['matched'][:8])}. Gaps: {', '.join(r['label'] for r in sk['gaps'][:8])}. "
        f"Pay: {d['salary'].get('note')}. Location: {d['location']['label']}. "
        f"Visa sponsorship (candidate needs H-1B): {d.get('sponsorship', {}).get('note', 'unknown')}."
    )
    return (
        f"{candidate_brief()}\n\n---\nJOB\nCompany: {job['company']}\nTitle: {job['title']}\nLocation: {job['location']}\n"
        f"Posted pay: {job.get('salary_text') or d['salary'].get('note')}\nURL: {job['url']}\n\nDESCRIPTION:\n{(job['description'] or '')[:9000]}\n\n---\n"
        f"AUTOMATED PRE-SCREEN (may be wrong; verify against the posting): {summary}\n\n"
        f"Return JSON matching exactly this shape:\n{SCHEMA_HINT}"
    )


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in model output")
    return json.loads(text[start : end + 1])


def call_model(client: httpx.Client, prov: tuple[str, str], prompt: str) -> dict[str, Any]:
    _, model = prov
    key = api_key()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is not set")
    r = client.post(
        f"{os.environ.get('GEMINI_BASE_URL', GEMINI_BASE)}/models/{model}:generateContent",
        headers={"x-goog-api-key": key, "content-type": "application/json"},
        json={
            "systemInstruction": {"parts": [{"text": SYSTEM}]},
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            # Gemini 2.5 models spend part of this budget on internal reasoning, so keep it generous.
            "generationConfig": {"temperature": 0.2, "maxOutputTokens": 8192, "responseMimeType": "application/json"},
        },
    )
    r.raise_for_status()
    data = r.json()
    cands = data.get("candidates") or []
    if not cands:
        raise ValueError(f"Gemini returned no candidates ({data.get('promptFeedback') or 'empty response'})")
    parts = (cands[0].get("content") or {}).get("parts") or []
    return _extract_json("".join(p.get("text", "") for p in parts if not p.get("thought")))


def _normalize(a: dict[str, Any]) -> dict[str, Any]:
    try:
        a["fit_score"] = max(0, min(100, int(round(float(a.get("fit_score", 0))))))
    except (TypeError, ValueError):
        a["fit_score"] = None
    for key in ("strengths", "red_flags", "resume_tweaks", "interview_prep"):
        a[key] = [str(x) for x in (a.get(key) or [])][:8]
    a["gaps"] = [g if isinstance(g, dict) else {"gap": str(g), "severity": "minor", "mitigation": ""} for g in (a.get("gaps") or [])][:8]
    return a


def analyze_top(top_n: int | None = None, force: bool = False, progress=None, workers: int = 4) -> dict[str, int]:
    prov = provider()
    if not prov:
        raise RuntimeError("No Gemini key found. Set GEMINI_API_KEY (get one at https://aistudio.google.com/apikey).")
    cfg = config.preferences().get("llm", {})
    top_n = top_n or int(cfg.get("top_n", 25))
    todo: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT j.*, s.heuristic, s.verdict, s.breakdown, s.details FROM jobs j JOIN scores s ON s.job_id=j.id "
            "WHERE j.active=1 AND s.heuristic >= ? ORDER BY s.heuristic DESC LIMIT ?",
            (config.preferences()["verdicts"]["stretch"], top_n * 3),
        ).fetchall()
        cached = {r["job_id"]: r["content_hash"] for r in conn.execute("SELECT job_id, content_hash FROM llm")}
        state = {r["job_id"]: r["status"] for r in conn.execute("SELECT job_id, status FROM state")}
        for r in rows:
            if state.get(r["id"]) in ("ignored", "rejected", "applied"):
                continue
            job = dict(r)
            h = content_hash(db.row_to_job(r))
            if not force and cached.get(r["id"]) == h:
                continue
            heur = {"heuristic": r["heuristic"], "verdict": r["verdict"], "breakdown": json.loads(r["breakdown"]), "details": json.loads(r["details"])}
            todo.append((job, heur, h))
            if len(todo) >= top_n:
                break

    done = failed = 0
    client = httpx.Client(timeout=180)

    def work(item: tuple[dict[str, Any], dict[str, Any], str]):
        job, heur, h = item
        try:
            return job, h, _normalize(call_model(client, prov, build_prompt(job, heur))), None
        except Exception as exc:  # noqa: BLE001
            return job, h, None, exc

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(work, todo))
    with db.connect() as conn:
        for job, h, analysis, err in results:
            if err or analysis is None:
                failed += 1
                if progress:
                    progress(f"failed {job['company']} / {job['title']}: {err}")
                continue
            conn.execute(
                "INSERT OR REPLACE INTO llm (job_id, content_hash, model, analysis, created_at) VALUES (?,?,?,?,?)",
                (job["id"], h, f"{prov[0]}:{prov[1]}", json.dumps(analysis), db.now()),
            )
            done += 1
            if progress:
                progress(f"{job['company']} / {job['title']}: LLM fit {analysis['fit_score']}")
    return {"analyzed": done, "failed": failed, "provider": f"{prov[0]}:{prov[1]}"}  # type: ignore[dict-item]
