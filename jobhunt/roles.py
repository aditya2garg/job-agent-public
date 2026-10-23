from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from . import config


@dataclass
class RoleMatch:
    name: str
    tier: str  # preferred | adjacent | longshot
    base: float
    cfg: dict[str, Any]


@lru_cache(maxsize=1)
def _compiled() -> tuple[list[re.Pattern[str]], list[tuple[re.Pattern[str], dict[str, Any]]]]:
    prefs = config.preferences()
    excl = [re.compile(p, re.IGNORECASE) for p in prefs.get("exclude_title_patterns", [])]
    roles = [(re.compile(r["pattern"], re.IGNORECASE), r) for r in prefs["roles"]]
    return excl, roles


@lru_cache(maxsize=1)
def _soft() -> list[tuple[re.Pattern[str], float, str]]:
    return [(re.compile(r["pattern"], re.IGNORECASE), float(r["penalty"]), r["label"]) for r in config.preferences().get("soft_penalties", [])]


def soft_penalties(title: str) -> list[tuple[float, str]]:
    return [(pen, label) for pat, pen, label in _soft() if pat.search(title)]


def excluded_by(title: str) -> str | None:
    excl, _ = _compiled()
    for pat in excl:
        m = pat.search(title)
        if m:
            return m.group(0)
    return None


def classify_role(title: str) -> RoleMatch | None:
    """Best matching configured role for a title, or None if excluded / irrelevant."""
    if excluded_by(title):
        return None
    _, roles = _compiled()
    best: RoleMatch | None = None
    for pat, cfg in roles:
        if pat.search(title) and (best is None or cfg["score"] > best.base):
            best = RoleMatch(cfg["name"], cfg["tier"], float(cfg["score"]), cfg)
    return best
