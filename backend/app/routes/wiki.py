"""Lessons-wiki surface — list / search / publish / deprecate / pin. The
page model lives in `app.spine.wiki`; this router is a thin HTTP shell."""

from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from app.schemas import WikiPagesResponse
from app.spine import wiki as wiki_store

router = APIRouter()


@router.get("/api/wiki/pages")
def wiki_list_pages(status: str | None = "published", limit: int = 50, owner: str | None = None):
    """Track 5 W5 — list wiki pages, filtered by status / owner.

    Default `status='published'` so the cockpit's main view shows only
    finalised lessons. Pass `status=""` (explicit empty string) to
    bypass the filter and return drafts/deprecated alongside
    published — the cockpit's "all stages" toggle uses this.
    """
    effective_status = status if (status not in (None, "")) else None
    pages = wiki_store.list_pages(status=effective_status, owner_agent=owner, limit=limit)
    return {"pages": [p.to_dict() for p in pages]}


@router.get("/api/wiki/pages/{slug:path}")
def wiki_get_page(slug: str):
    page = wiki_store.get_page(slug)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    revisions = wiki_store.list_revisions(slug, limit=20)
    return {"page": page.to_dict(), "revisions": revisions}


@router.get("/api/wiki/search")
def wiki_search(q: str = "", limit: int = 20, status: str | None = None):
    """Mirror /api/wiki/pages: optional `status` filter so operators
    can search within `draft` / `deprecated` / `published` instead of
    seeing mixed-status results when they're triaging in-flight wiki
    edits. Filter is applied at the SQL layer (before LIMIT) so a
    broad query never gets its draft matches crowded out by
    published rows."""
    effective_status = status if (status not in (None, "")) else None
    pages = wiki_store.search_pages(q, limit=limit, status=effective_status)
    return {"pages": [p.to_dict() for p in pages]}


@router.post("/api/wiki/pages/{slug:path}/publish")
def wiki_publish(slug: str, body: dict | None = Body(default=None)):
    by = (body or {}).get("by_agent", "Operator")
    page = wiki_store.publish_page(slug, by_agent=by)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@router.post("/api/wiki/pages/{slug:path}/deprecate")
def wiki_deprecate(slug: str, body: dict | None = Body(default=None)):
    payload = body or {}
    by = payload.get("by_agent", "Operator")
    reason = payload.get("reason", "")
    page = wiki_store.deprecate_page(slug, by_agent=by, reason=reason)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@router.post("/api/wiki/pages/{slug:path}/pin")
def wiki_pin(slug: str, body: dict | None = Body(default=None)):
    pinned = bool((body or {}).get("pinned", True))
    page = wiki_store.set_pinned(slug, pinned=pinned)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@router.get("/api/wiki/pinned", response_model=WikiPagesResponse)
def wiki_pinned():
    return {"pages": [p.to_dict() for p in wiki_store.list_pinned()]}
