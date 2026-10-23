from __future__ import annotations

import time
from typing import Any

import httpx

UA = "Mozilla/5.0 (compatible; jobhunt/0.1; personal job search)"


def make_client(timeout: float = 40.0) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA, "Accept": "application/json"}, timeout=timeout, follow_redirects=True)


def get_json(client: httpx.Client, url: str, params: dict[str, Any] | None = None, retries: int = 2) -> Any:
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = client.get(url, params=params)
            if r.status_code == 404:
                return None
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(1.5 * (attempt + 1))
                continue
            r.raise_for_status()
            return r.json()
        except (httpx.HTTPError, ValueError) as exc:
            last = exc
            time.sleep(0.8 * (attempt + 1))
    if last:
        raise last
    return None
