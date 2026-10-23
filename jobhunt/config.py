from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(os.environ.get("JOBHUNT_ROOT", Path(__file__).resolve().parents[1]))
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
DB_PATH = Path(os.environ.get("JOBHUNT_DB", DATA_DIR / "jobs.db"))


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


_load_dotenv()


def _load(name: str) -> dict[str, Any]:
    with open(CONFIG_DIR / name, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def preferences() -> dict[str, Any]:
    return _load("preferences.yaml")


@lru_cache(maxsize=1)
def profile() -> dict[str, Any]:
    return _load("profile.yaml")


@lru_cache(maxsize=1)
def companies() -> dict[str, Any]:
    return _load("companies.yaml")


def resume_text() -> str:
    """Text of the resume PDF (or a .txt/.md), or the profile summary as a fallback."""
    rel = preferences().get("resume_path")
    if rel:
        path = (ROOT / rel) if not Path(rel).is_absolute() else Path(rel)
        if path.exists():
            if path.suffix.lower() == ".pdf":
                from pypdf import PdfReader

                return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
            return path.read_text(encoding="utf-8", errors="ignore")
    prof = profile()
    evidence = " ".join(e["statement"] for e in prof.get("evidence", []))
    return f"{prof.get('headline', '')} {prof.get('summary', '')} {evidence}"
