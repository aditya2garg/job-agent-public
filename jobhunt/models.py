from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Job:
    source: str
    company: str
    title: str
    url: str
    description: str = ""
    location: str = ""
    remote: bool | None = None
    posted_at: str | None = None
    employment_type: str | None = None
    department: str | None = None
    ext_id: str | None = None
    # Structured pay when the source provides it (annual USD). Otherwise parsed from the text.
    salary_min: float | None = None
    salary_max: float | None = None
    salary_text: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)
    stored_id: str | None = None

    @property
    def id(self) -> str:
        if self.stored_id:
            return self.stored_id
        key = f"{self.source}|{self.company.lower()}|{self.ext_id or self.url}"
        return hashlib.sha1(key.encode()).hexdigest()[:12]

    @property
    def dedupe_key(self) -> str:
        norm = lambda s: "".join(ch for ch in s.lower() if ch.isalnum())
        return f"{norm(self.company)}|{norm(self.title)}|{norm(self.location)[:40]}"

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["id"] = self.id
        d.pop("stored_id", None)
        return d
