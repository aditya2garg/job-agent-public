"""H-1B sponsorship evidence.

Two independent signals feed the score:

* role-level: what the posting text itself says (see extract.sponsorship_stance)
* company-level: has this employer actually filed (and had certified) H-1B Labor Condition
  Applications recently? Built from the US Dept. of Labor OFLC LCA disclosure files
  (`jobhunt h1b-update`), plus whether the employer's own postings say "no sponsorship".
"""

from __future__ import annotations

import json
import re
import urllib.request
from functools import lru_cache
from pathlib import Path
from typing import Any

from . import config

DOL_URL = "https://www.dol.gov/sites/dolgov/files/ETA/oflc/pdfs/LCA_Disclosure_Data_{fy}.xlsx"
DEFAULT_FILES = ["FY2026_Q1", "FY2025_Q4", "FY2025_Q3"]
INDEX_PATH = config.DATA_DIR / "h1b_employers.json"
DOL_DIR = config.DATA_DIR / "dol"

SUFFIX = {"inc", "incorporated", "llc", "l", "c", "corp", "corporation", "ltd", "limited", "co", "company", "lp", "llp", "plc", "pbc", "the", "opco", "pc"}
EXTRA_OK = {"services", "usa", "us", "america", "americas", "holdings", "technologies", "technology", "labs", "software", "systems", "platforms", "platform", "global", "group", "operations", "research", "international", "enterprise", "solutions", "data", "services"}
TECH_RE = re.compile(r"software|data (scientist|engineer|analyst)|machine learning|artificial intel|\bai\b|analytics|computer|database|\bml\b|research scientist|systems? (engineer|analyst)", re.IGNORECASE)


def normalize(name: str) -> str:
    name = name.lower().replace("&", " and ")
    toks = [t for t in re.sub(r"[^a-z0-9]+", " ", name).split() if t]
    while toks and toks[-1] in SUFFIX:
        toks.pop()
    toks = [t for t in toks if t not in {"the"}]
    return " ".join(toks)


def build_index(paths: list[Path], progress=None) -> dict[str, Any]:
    """Stream DOL LCA xlsx files and count certified H-1B LCAs per normalized employer."""
    from openpyxl import load_workbook

    agg: dict[str, list[int]] = {}
    for path in paths:
        wb = load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        rows = ws.iter_rows(values_only=True)
        header = [str(h or "").strip().upper() for h in next(rows)]
        col = {h: i for i, h in enumerate(header)}
        i_status, i_visa, i_emp = col["CASE_STATUS"], col["VISA_CLASS"], col["EMPLOYER_NAME"]
        i_soc, i_title = col.get("SOC_TITLE"), col.get("JOB_TITLE")
        i_pos = col.get("TOTAL_WORKER_POSITIONS")
        n = 0
        for r in rows:
            n += 1
            if not r[i_emp] or not str(r[i_status] or "").upper().startswith("CERTIFIED") or "H-1B" not in str(r[i_visa] or "").upper().replace("H1B", "H-1B"):
                continue
            key = normalize(str(r[i_emp]))
            positions = int(r[i_pos] or 1) if i_pos is not None and str(r[i_pos] or "1").isdigit() else 1
            cur = agg.setdefault(key, [0, 0])
            cur[0] += positions
            text = f"{r[i_soc] or ''} {r[i_title] or ''}" if i_soc is not None else ""
            if TECH_RE.search(text):
                cur[1] += positions
            if progress and n % 50000 == 0:
                progress(f"{path.name}: {n:,} rows")
        wb.close()
    index = {"employers": agg, "files": [p.name for p in paths]}
    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(json.dumps(index))
    load_index.cache_clear()
    return index


def download(files: list[str] | None = None, progress=None) -> list[Path]:
    DOL_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for fy in files or DEFAULT_FILES:
        dest = DOL_DIR / f"LCA_{fy}.xlsx"
        if not dest.exists() or dest.stat().st_size < 1_000_000:
            if progress:
                progress(f"downloading {fy} ...")
            req = urllib.request.Request(DOL_URL.format(fy=fy), headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=120) as r, open(dest, "wb") as fh:
                while chunk := r.read(1 << 20):
                    fh.write(chunk)
        out.append(dest)
    return out


@lru_cache(maxsize=1)
def load_index() -> dict[str, list[int]] | None:
    if not INDEX_PATH.exists():
        return None
    return json.loads(INDEX_PATH.read_text())["employers"]


@lru_cache(maxsize=1)
def _overrides() -> dict[str, Any]:
    path = config.CONFIG_DIR / "sponsorship.yaml"
    if not path.exists():
        return {"aliases": {}, "known_sponsors": [], "known_no_sponsorship": []}
    import yaml

    d = yaml.safe_load(path.read_text()) or {}
    return {
        "aliases": {normalize(k): [normalize(a) for a in v] for k, v in (d.get("aliases") or {}).items()},
        "known_sponsors": [normalize(x) for x in d.get("known_sponsors") or []],
        "known_no_sponsorship": [normalize(x) for x in d.get("known_no_sponsorship") or []],
    }


@lru_cache(maxsize=4096)
def company_history(company: str) -> dict[str, Any]:
    """H-1B filing history for an employer. `available` is False when the DOL index hasn't been built."""
    idx = load_index()
    ov = _overrides()
    q = normalize(company)
    if q in ov["known_no_sponsorship"]:
        return {"available": True, "certified": 0, "tech": 0, "override": "no", "matched": []}
    if q in ov["known_sponsors"]:
        return {"available": True, "certified": 999, "tech": 999, "override": "yes", "matched": []}
    if idx is None:
        return {"available": False, "certified": 0, "tech": 0, "matched": []}
    toks = q.split()
    while len(toks) > 1 and toks[-1] in EXTRA_OK:
        toks.pop()
    base = " ".join(toks)
    prefixes = list(dict.fromkeys([q, base] + ov["aliases"].get(q, []) + ov["aliases"].get(base, [])))
    cert = tech = 0
    matched: list[str] = []
    for key, (c, t) in idx.items():
        for pre in prefixes:
            if not pre:
                continue
            if key == pre or (key.startswith(pre + " ") and set(key[len(pre) + 1 :].split()) <= EXTRA_OK):
                cert += c
                tech += t
                matched.append(key)
                break
            if pre in ov["aliases"].get(q, []) + ov["aliases"].get(base, []) and key.startswith(pre):
                cert += c
                tech += t
                matched.append(key)
                break
    return {"available": True, "certified": cert, "tech": tech, "matched": matched[:5]}


def history_score(h: dict[str, Any], cfg: dict[str, Any]) -> tuple[float, str]:
    if not h["available"]:
        return 55.0, "H-1B history unknown (run `jobhunt h1b-update`)"
    if h.get("override") == "yes":
        return 95.0, "listed as a known sponsor in config/sponsorship.yaml"
    if h.get("override") == "no":
        return 0.0, "listed as a non-sponsor in config/sponsorship.yaml"
    n = h["tech"] or 0
    if n >= cfg["strong_history"]:
        return 95.0, f"established H-1B sponsor ({h['certified']:,} certified LCAs, {n:,} in tech roles)"
    if n >= cfg["some_history"]:
        return 85.0, f"sponsors H-1B ({h['certified']:,} certified LCAs, {n:,} in tech roles)"
    if h["certified"] > 0:
        return 62.0, f"limited H-1B history ({h['certified']} certified LCAs, {n} in tech roles)"
    return 25.0, "no certified H-1B filings found for this employer in recent DOL data"
