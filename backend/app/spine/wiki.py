"""Agentic wiki — durable, searchable agent knowledge (Track 5).

Two tables (defined in `db.py`):

  * `wiki_pages` — one row per slug; always carries the canonical body
    of the latest published revision, or the latest draft when nothing
    has been published yet. Read path is O(1) (no joins).
  * `wiki_revisions` — full version history per slug. Every edit
    appends a row; the page's `version` column points at the latest
    revision id.

Spine event log captures the audit trail via three new kinds:

  * `wiki_edit`        — a draft revision was created (W3)
  * `wiki_publish`     — a draft was promoted to published (W4)
  * `wiki_deprecate`   — a page was retired

The Critic-gated auto-publish (W4) flips `status` from `draft` to
`published` when the Critic returns no Risks/Gaps; an operator can
override via the cockpit's approval rail. Reads do *not* gate on
status — the cockpit's WikiTab filters separately so an operator can
scrub draft history when investigating a bad answer.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from app.spine.db import conn
from app.spine.events import append_event

WIKI_STATUSES = ("draft", "published", "deprecated")


@dataclass
class WikiPage:
    slug: str
    title: str
    body_md: str
    owner_agent: str
    status: str
    version: int
    updated_ts: str
    refs: list[str]
    pinned: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "title": self.title,
            "body_md": self.body_md,
            "owner_agent": self.owner_agent,
            "status": self.status,
            "version": self.version,
            "updated_ts": self.updated_ts,
            "refs": self.refs,
            "pinned": self.pinned,
        }


def _row_to_page(row) -> WikiPage:
    return WikiPage(
        slug=row["slug"],
        title=row["title"],
        body_md=row["body_md"],
        owner_agent=row["owner_agent"],
        status=row["status"],
        version=int(row["version"]),
        updated_ts=row["updated_ts"],
        refs=json.loads(row["refs_json"] or "[]"),
        pinned=bool(row["pinned"]),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()


def get_page(slug: str) -> WikiPage | None:
    with conn() as c:
        row = c.execute("SELECT * FROM wiki_pages WHERE slug = ?", (slug,)).fetchone()
    return _row_to_page(row) if row else None


def list_pages(
    status: str | None = "published",
    owner_agent: str | None = None,
    limit: int = 50,
) -> list[WikiPage]:
    """List pages ordered by most-recently-updated first.

    `status=None` returns all stages — the cockpit's WikiTab default
    asks for `published` so operators don't see in-flight drafts on
    the main view. The Curator agent (W6) reads with `status="draft"`
    when checking what's currently waiting on review.
    """
    sql = "SELECT * FROM wiki_pages"
    args: list[Any] = []
    where: list[str] = []
    if status is not None:
        where.append("status = ?")
        args.append(status)
    if owner_agent is not None:
        where.append("owner_agent = ?")
        args.append(owner_agent)
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY updated_ts DESC LIMIT ?"
    args.append(int(limit))
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_page(r) for r in rows]


def search_pages(
    query: str,
    limit: int = 20,
    status: str | None = None,
) -> list[WikiPage]:
    """Cheap LIKE-based search over title + body.

    `status` filters at the SQL layer (NOT post-fetch) so a broad
    query like `q=foo&status=draft` doesn't get its draft rows
    crowded out by published rows that happened to fill the first
    `limit` slots. Applying the filter before LIMIT is the only way
    to make draft triage reliable.

    SQLite FTS5 is the right answer when the wiki grows past a few
    hundred pages — but for the demo footprint, three LIKE clauses
    are plenty and don't require a separate virtual table. Switching
    to FTS5 later is a one-table migration; the public surface here
    (search_pages) stays the same.
    """
    q = (query or "").strip()
    if not q:
        return list_pages(status=status, limit=limit)
    needle = f"%{q}%"
    where = "(slug LIKE ? OR title LIKE ? OR body_md LIKE ?)"
    args: list[Any] = [needle, needle, needle]
    if status is not None:
        where += " AND status = ?"
        args.append(status)
    sql = (
        f"SELECT * FROM wiki_pages WHERE {where} "
        "ORDER BY status = 'published' DESC, updated_ts DESC LIMIT ?"
    )
    args.append(int(limit))
    with conn() as c:
        rows = c.execute(sql, args).fetchall()
    return [_row_to_page(r) for r in rows]


def propose_edit(
    slug: str,
    title: str,
    body_md: str,
    author_agent: str,
    refs: list[str] | None = None,
) -> WikiPage:
    """Create-or-update a page as a `draft` revision.

    First-write idempotency: when the slug doesn't exist, the page is
    created at version=1 with status='draft'. Subsequent edits bump
    the version, append a wiki_revisions row, and overwrite the
    canonical body. The page's status is preserved across edits — a
    revision-on-published page lands as a `draft` again so the W4
    gate fires before the new body becomes operator-visible (this
    matches the operator's expectation of "drafts queue up; published
    requires approval").
    """
    import sqlite3 as _sqlite3

    refs = list(refs or [])
    refs_json = json.dumps(refs)
    now = _now()
    # Concurrent /api/chat turns + Curator passes can race the SELECT
    # → INSERT/UPDATE pair on the same slug. Both readers see the
    # same existing.version, both compute version+1, and the second
    # INSERT into wiki_revisions blows up on UNIQUE(slug, version).
    # Retry on integrity / locked errors — the next SELECT sees the
    # winner's commit and bumps version one higher. Three attempts is
    # enough; in 30M+ years of operator turns we have not yet seen a
    # 3-way concurrent write to the same slug.
    for attempt in range(3):
        try:
            with conn() as c:
                existing = c.execute(
                    "SELECT * FROM wiki_pages WHERE slug = ?", (slug,)
                ).fetchone()
                if existing is None:
                    new_version = 1
                    c.execute(
                        "INSERT INTO wiki_pages (slug, title, body_md, owner_agent, status, version, updated_ts, refs_json, pinned) "
                        "VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, 0)",
                        (slug, title, body_md, author_agent, new_version, now, refs_json),
                    )
                else:
                    new_version = int(existing["version"]) + 1
                    c.execute(
                        "UPDATE wiki_pages SET title = ?, body_md = ?, status = 'draft', "
                        "version = ?, updated_ts = ?, refs_json = ? WHERE slug = ?",
                        (title, body_md, new_version, now, refs_json, slug),
                    )
                c.execute(
                    "INSERT INTO wiki_revisions (slug, version, title, body_md, author_agent, status, refs_json, ts) "
                    "VALUES (?, ?, ?, ?, ?, 'draft', ?, ?)",
                    (slug, new_version, title, body_md, author_agent, refs_json, now),
                )
            break
        except _sqlite3.IntegrityError:
            # UNIQUE(slug, version) tripped — another writer beat us
            # to this version number. Retry; the next SELECT sees
            # their commit and bumps version one higher.
            if attempt == 2:
                raise
        except _sqlite3.OperationalError:
            # Database is locked (BEGIN IMMEDIATE contention). Same
            # retry strategy.
            if attempt == 2:
                raise
    append_event(
        agent=author_agent,
        kind="wiki_edit",
        payload={
            "slug": slug,
            "version": new_version,
            "title": title,
            "refs": refs,
        },
    )
    page = get_page(slug)
    assert page is not None
    return page


def publish_page(
    slug: str,
    by_agent: str,
    expected_version: int | None = None,
) -> WikiPage | None:
    """Flip the latest revision to status='published'. Idempotent —
    re-publishing an already-published page is a no-op.

    `expected_version` makes the publish atomic against concurrent
    edits: the UPDATE only fires when the page is still at the version
    the caller validated (and still in 'draft'). If a racing turn
    bumped the version between the caller's read and our UPDATE, the
    rowcount is 0, no event is emitted, and we return the current page
    unchanged. This closes the W4 TOCTOU gap where the auto-publisher
    would otherwise promote a body the Critic never reviewed.
    """
    page = get_page(slug)
    if page is None:
        return None
    if page.status == "published":
        return page
    if expected_version is not None and page.version != expected_version:
        return page
    target_version = page.version
    now = _now()
    with conn() as c:
        cur = c.execute(
            "UPDATE wiki_pages SET status = 'published', updated_ts = ? "
            "WHERE slug = ? AND version = ? AND status = 'draft'",
            (now, slug, target_version),
        )
        if cur.rowcount == 0:
            # Concurrent writer changed status/version between our read
            # and our write. Don't fall through to the revisions update
            # or fire a wiki_publish event for a body we didn't validate.
            return get_page(slug)
        c.execute(
            "UPDATE wiki_revisions SET status = 'published' WHERE slug = ? AND version = ?",
            (slug, target_version),
        )
    append_event(
        agent=by_agent,
        kind="wiki_publish",
        payload={"slug": slug, "version": target_version, "promoted_by": by_agent},
    )
    return get_page(slug)


def deprecate_page(slug: str, by_agent: str, reason: str = "") -> WikiPage | None:
    page = get_page(slug)
    if page is None:
        return None
    now = _now()
    with conn() as c:
        c.execute(
            "UPDATE wiki_pages SET status = 'deprecated', updated_ts = ? WHERE slug = ?",
            (now, slug),
        )
        # Also flip the latest revision's status so list_revisions /
        # /api/wiki/pages/{slug} report consistent stage history.
        # Without this the revision row would still read 'published'
        # or 'draft' after the page is retired, misleading any audit
        # consumer that walks revisions instead of the page row.
        c.execute(
            "UPDATE wiki_revisions SET status = 'deprecated' WHERE slug = ? AND version = ?",
            (slug, page.version),
        )
    append_event(
        agent=by_agent,
        kind="wiki_deprecate",
        payload={"slug": slug, "version": page.version, "reason": reason},
    )
    return get_page(slug)


def list_revisions(slug: str, limit: int = 20) -> list[dict[str, Any]]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, slug, version, title, body_md, author_agent, status, refs_json, ts "
            "FROM wiki_revisions WHERE slug = ? ORDER BY version DESC LIMIT ?",
            (slug, int(limit)),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "slug": r["slug"],
            "version": int(r["version"]),
            "title": r["title"],
            "body_md": r["body_md"],
            "author_agent": r["author_agent"],
            "status": r["status"],
            "refs": json.loads(r["refs_json"] or "[]"),
            "ts": r["ts"],
        }
        for r in rows
    ]


def set_pinned(slug: str, pinned: bool) -> WikiPage | None:
    """Operator-driven pin toggle. Pinned slugs surface in the cockpit
    status strip as a chip so the operator can return to a page during
    a chat turn without re-searching."""
    with conn() as c:
        c.execute(
            "UPDATE wiki_pages SET pinned = ?, updated_ts = ? WHERE slug = ?",
            (1 if pinned else 0, _now(), slug),
        )
    return get_page(slug)


def list_pinned() -> list[WikiPage]:
    with conn() as c:
        rows = c.execute(
            "SELECT * FROM wiki_pages WHERE pinned = 1 ORDER BY updated_ts DESC"
        ).fetchall()
    return [_row_to_page(r) for r in rows]
