from __future__ import annotations

import csv
import io
from typing import Any

from . import queries


def to_csv(rows: list[dict[str, Any]]) -> str:
    buf = io.StringIO()
    cols = ["score", "verdict", "company", "title", "role_name", "role_tier", "location_label", "salary_note", "status", "url"]
    w = csv.writer(buf)
    w.writerow(cols)
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return buf.getvalue()


def to_markdown(rows: list[dict[str, Any]], top: int = 50) -> str:
    out = ["# Job matches", "", "| Score | Verdict | Company | Role | Location | Pay | Why |", "|---|---|---|---|---|---|---|"]
    for r in rows[:top]:
        why = "; ".join(r["pros"][:2])
        pay = r["salary_note"] or "n/a"
        out.append(f"| {r['score']:.0f} | {r['verdict']} | {r['company']} | [{r['title']}]({r['url']}) | {r['location_label']} | {pay} | {why} |")
    return "\n".join(out) + "\n"


def full_report(job_id: str) -> str:
    """Plain-text deep dive for one job (used by `jobhunt show`)."""
    d = queries.detail(job_id)
    if not d:
        return "Job not found."
    det = d["details"]
    lines = [
        f"{d['company']} -- {d['title']}",
        f"{d['location']}  |  {d['url']}",
        f"SCORE {d['score']:.1f} ({d['verdict']})  heuristic {d['heuristic']:.1f}  role={d['role_name']} [{d['role_tier']}]",
        "",
        "Breakdown: " + "  ".join(f"{k} {v:.0f}" for k, v in d["breakdown"].items()),
        f"Role      : {det['role']['reason']}",
        f"Skills    : {len(det['skills']['matched'])} matched / {len(det['skills']['partial'])} partial / {len(det['skills']['gaps'])} gaps (base {det['skills']['base']}, penalty x{det['skills']['penalty']})",
        f"Seniority : {det['seniority']['note']}; title level '{det['seniority']['title_level']}'",
        f"Comp      : {det['salary'].get('note')}",
        f"Location  : {det['location']['label']} (matched '{det['location']['matched']}')",
    ]
    if det.get("caps"):
        lines.append("Caps      : " + ", ".join(det["caps"]))
    lines += ["", "PROS"] + [f"  + {p}" for p in d["pros"]] + ["CONS"] + [f"  - {c}" for c in d["cons"]]
    lines += ["", "MATCHED SKILLS: " + ", ".join(f"{r['label']}" for r in det["skills"]["matched"][:12])]
    lines += ["GAPS: " + ", ".join(f"{r['label']} ({r['section']})" for r in det["skills"]["gaps"][:12])]
    lines += ["", "TALKING POINTS FROM YOUR RESUME"] + [f"  * {e['statement']}  [{', '.join(e['themes'])}]" for e in det["evidence"]]
    if d["llm"]:
        L = d["llm"]
        lines += ["", f"LLM REVIEW ({L['model']}): fit {L['fit_score']} -> {L.get('recommendation')}", f"  {L.get('summary', '')}", f"  Reality check: {L.get('role_reality_check', '')}"]
        lines += ["  Strengths:"] + [f"    + {s}" for s in L["strengths"]]
        lines += ["  Gaps:"] + [f"    - [{g.get('severity')}] {g.get('gap')} -> {g.get('mitigation')}" for g in L["gaps"]]
        lines += ["  Resume tweaks:"] + [f"    * {s}" for s in L["resume_tweaks"]]
        lines += ["  Interview prep:"] + [f"    ? {s}" for s in L["interview_prep"]]
    return "\n".join(lines)
