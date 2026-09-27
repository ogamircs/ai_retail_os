"""GBrain MCP surface — `status`, `search`, page reads, and a `recent`
shortcut. Mock mode falls back to the wiki-backed client so the cockpit
shows non-zero counts without a real GBrain instance."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.llm.mcp import get_client
from app.schemas import BrainStatus

router = APIRouter()


@router.get("/api/brain/status", response_model=BrainStatus)
def brain_status():
    """Track 6 G5 — cockpit-side surface for the GBrain integration.

    Returns:
      * `configured`     — whether `GBRAIN_BEARER` is set.
      * `reachable`      — whether the gbrain HTTP endpoint answered
                           a `search('')` probe (None when not
                           configured — mock mode is always 'reachable'
                           but flagged via `mock=true`).
      * `mock`           — true when the cockpit is using the
                           substrate-backed mock client.
      * `pages_count`    — count of recent pages surfaced by an empty
                           search (cheap probe for the status strip
                           chip).
    """
    client = get_client()
    if not client.config.configured:
        # Mock mode — surface what the wiki-backed client returns so
        # the cockpit chip shows a non-zero count (and the operator
        # gets a glimpse of what the brain WOULD look like with a
        # real GBrain instance).
        try:
            probe = client.search("", limit=20)
            results = probe.get("results") or []
        except Exception:
            results = []
        return {
            "configured": False,
            "reachable": True,
            "mock": True,
            "pages_count": len(results),
            "endpoint": None,
        }
    try:
        probe = client.search("", limit=20)
    except Exception as e:
        return {
            "configured": True,
            "reachable": False,
            "mock": False,
            "pages_count": 0,
            "endpoint": client.config.base_url,
            "error": str(e),
        }
    results = probe.get("results") or []
    return {
        "configured": True,
        "reachable": "error" not in probe,
        "mock": False,
        "pages_count": len(results),
        "endpoint": client.config.base_url,
        "error": probe.get("error"),
    }


@router.get("/api/brain/search")
def brain_search(q: str = "", limit: int = 20):
    return get_client().search(q, limit=limit)


@router.get("/api/brain/pages/{slug:path}")
def brain_page(slug: str):
    page = get_client().get(slug)
    if isinstance(page, dict) and page.get("error"):
        raise HTTPException(404, page["error"])
    return page


@router.get("/api/brain/recent")
def brain_recent(limit: int = 20):
    """Empty-query search returns the most recently updated pages —
    the BrainTab's default landing list when the operator hasn't
    typed anything yet."""
    return get_client().search("", limit=limit)
