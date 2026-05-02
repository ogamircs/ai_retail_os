"""GBrain MCP client (Track 6 G2).

Thin HTTP client over GBrain's `gbrain serve --http` endpoint. Speaks
the public read-only surface our agents need (`search`, `get`,
`query`, plus the optional `ingest` write path used by the Track 6 G3
chat-turn hook). Auth is a single bearer token.

When `GBRAIN_BASE_URL` + `GBRAIN_BEARER` are unset (the cockpit demo
default), the client returns canned mock responses sourced from the
existing `wiki_pages` table — same shape as Track 1's adapter mock
mode. The agents see a consistent tool surface either way.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any
from urllib import error as urlerror
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class GBrainConfig:
    base_url: str | None
    bearer: str | None
    timeout_s: float = 4.0

    @classmethod
    def from_env(cls) -> "GBrainConfig":
        return cls(
            base_url=(os.getenv("GBRAIN_BASE_URL") or "").strip() or None,
            bearer=(os.getenv("GBRAIN_BEARER") or "").strip() or None,
            timeout_s=float(os.getenv("GBRAIN_TIMEOUT_S", "4.0")),
        )

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.bearer)


class GBrainClient:
    """HTTP client. Lazy: each call hits the brain on demand. No
    connection pool — calls are infrequent and the cost of a fresh
    socket per request is negligible against the LLM round-trip
    they're embedded in.

    `mock_mode=True` (default when `GBRAIN_BEARER` isn't set) makes
    every method return the canned shape so the cockpit demo path
    runs without an external dependency.
    """

    def __init__(self, config: GBrainConfig | None = None):
        self.config = config or GBrainConfig.from_env()

    # ------------------------------------------------------------------
    # Public surface — every method is shaped identically in mock and
    # live mode so callers (the brain_* tools) don't branch.

    def search(self, query: str, limit: int = 10) -> dict:
        if not self.config.configured:
            return self._mock_search(query, limit)
        return self._post("/v1/search", {"query": query, "limit": limit})

    def get(self, slug: str) -> dict:
        if not self.config.configured:
            return self._mock_get(slug)
        return self._post("/v1/get", {"slug": slug})

    def query(self, query: str, limit: int = 5) -> dict:
        """Natural-language Q&A over the brain. Returns
        `{answer, citations: [{slug, title, snippet}]}`."""
        if not self.config.configured:
            return self._mock_query(query, limit)
        return self._post("/v1/query", {"query": query, "limit": limit})

    def ingest(self, pages: list[dict]) -> dict:
        """Write path used by G3. Each page is a dict with `slug`,
        `title`, `body`, `tags`. Mock mode is a no-op (we don't
        duplicate ingest traffic into the wiki — the wiki is its own
        source of truth)."""
        if not self.config.configured:
            return {"mock": True, "ingested": 0}
        return self._post("/v1/ingest", {"pages": pages})

    def code_lookup(self, kind: str, symbol: str) -> dict:
        """Code-graph queries used by G4. `kind` is one of:
        `callers`, `callees`, `def`, `refs`. Mock mode returns an
        empty list with `mock=True` so the Critic surfaces the gap
        instead of pretending it knows."""
        if kind not in ("callers", "callees", "def", "refs"):
            return {"error": f"unknown code-lookup kind: {kind}"}
        if not self.config.configured:
            return {"mock": True, "kind": kind, "symbol": symbol, "results": []}
        return self._post(f"/v1/code/{kind}", {"symbol": symbol})

    # ------------------------------------------------------------------
    # HTTP helper

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{self.config.base_url.rstrip('/')}{path}"
        body = json.dumps(payload).encode()
        req = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": f"Bearer {self.config.bearer}",
            },
        )
        try:
            with urlopen(req, timeout=self.config.timeout_s) as resp:
                raw = resp.read().decode("utf-8")
        except (urlerror.URLError, urlerror.HTTPError, TimeoutError) as e:
            return {"error": f"gbrain unreachable: {e}", "endpoint": path}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"error": "non-JSON response from gbrain", "raw": raw[:240]}

    # ------------------------------------------------------------------
    # Mock fallback — sources from substrate.wiki so the demo cockpit
    # exposes a coherent corpus without requiring a running gbrain.

    def _mock_search(self, query: str, limit: int) -> dict:
        from app.spine import wiki

        pages = wiki.search_pages(query or "", limit=limit, status="published")
        return {
            "mock": True,
            "query": query,
            "results": [
                {
                    "slug": p.slug,
                    "title": p.title,
                    "tier": "tier-2",
                    "updated_ts": p.updated_ts,
                    "snippet": (p.body_md or "")[:240],
                }
                for p in pages
            ],
        }

    def _mock_get(self, slug: str) -> dict:
        from app.spine import wiki

        page = wiki.get_page(slug)
        if page is None:
            return {"mock": True, "error": f"unknown brain page: {slug}"}
        return {
            "mock": True,
            "slug": page.slug,
            "title": page.title,
            "body": page.body_md,
            "tier": "tier-2",
            "updated_ts": page.updated_ts,
            "citations": page.refs,
        }

    def _mock_query(self, query: str, limit: int) -> dict:
        # Mock answer is the first hit's body excerpt — the goal is a
        # consistent shape, not a smart answer. Real gbrain does the
        # synthesis pass on the live LLM.
        from app.spine import wiki

        pages = wiki.search_pages(query or "", limit=limit, status="published")
        if not pages:
            return {
                "mock": True,
                "answer": "(brain has no published pages matching this query)",
                "citations": [],
            }
        top = pages[0]
        return {
            "mock": True,
            "answer": (top.body_md or "")[:480],
            "citations": [
                {
                    "slug": p.slug,
                    "title": p.title,
                    "snippet": (p.body_md or "")[:200],
                }
                for p in pages[: max(1, limit)]
            ],
        }


# Module-level singleton. Tests can rebind by writing to
# `app.llm.mcp._client = GBrainClient(...)` if they want a different
# config; production code calls `get_client()`.
_client: GBrainClient | None = None


def get_client() -> GBrainClient:
    global _client
    if _client is None:
        _client = GBrainClient()
    return _client


def reset_client() -> None:
    """Test helper — drops the cached client so the next get_client()
    rebuilds with the current env."""
    global _client
    _client = None
