"""Shared HTTP + utility primitives for integration adapters.

Every adapter under `app.integrations.adapters` reaches for the same handful of
helpers: a tiny JSON-over-HTTP client, slug/display string normalisers, safe
numeric coercions, and a strict id stringifier. Centralising them here keeps
each adapter module focused on its own quirks (Mautic OAuth, Shopify GraphQL,
ERPNext desk auth, …).
"""

from __future__ import annotations

import json
import re
from typing import Any
from urllib.request import Request, urlopen


def _slug(value: str | None, default: str = "uncategorized") -> str:
    text = (value or default).strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text).strip("_")
    return text or default


def _display(value: str) -> str:
    return value.replace("_", " ").title()


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value or default)
    except (TypeError, ValueError):
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value or default))
    except (TypeError, ValueError):
        return default


def _coerce_id(raw: Any) -> str | None:
    """Validate and stringify an external row id.

    `str(None)` is the truthy string "None" — a missing id would otherwise
    cache rows under a fake external_id, and multiple malformed rows would
    collide on it. Validate the raw value first, *then* cast.
    """
    if raw is None:
        return None
    if isinstance(raw, bool):
        # JSON bools shouldn't appear in id positions; treat as malformed.
        return None
    if isinstance(raw, (int, float)):
        return str(raw)
    if isinstance(raw, str):
        s = raw.strip()
        return s or None
    return None


class JsonHttpClient:
    def __init__(self, base_url: str, headers: dict[str, str] | None = None):
        self.base_url = base_url.rstrip("/")
        self.headers = headers or {}

    def request(self, path: str, method: str = "GET", payload: dict[str, Any] | None = None) -> Any:
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            **self.headers,
        }
        req = Request(f"{self.base_url}{path}", data=body, headers=headers, method=method)
        with urlopen(req, timeout=12) as resp:
            data = resp.read().decode("utf-8")
        return json.loads(data) if data else {}
