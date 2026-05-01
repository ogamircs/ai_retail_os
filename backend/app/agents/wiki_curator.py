"""Wiki Curator agent (Track 5 W6).

Read-only agent that runs *after* every operator turn. Walks the
turn's events + new artifacts and decides whether anything is
wiki-worthy. Emits up to `MAX_PROPOSALS_PER_TURN` `wiki_propose_edit`
calls — drafts; the Critic-gated auto-publish (W4) decides if any
land as `published`.

Rate-limit: at most 1 proposal per slug per day, capped at 3
proposals per turn. Operator can disable entirely with
`WIKI_CURATOR_ENABLED=0`.

The Curator does NOT delegate further. It does NOT propose outbox
actions. It is a single-pass over the turn's evidence: read the
artifacts, search the existing wiki, propose at most 3 lessons.
"""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_wiki_propose_edit_tool,
    build_wiki_read_tool,
    build_wiki_search_tool,
)
from app.llm.base import LLMProvider, Tool
from app.llm.prompts import resolve_prompt
from app.spine.events import append_event, events_since_ts
from app.spine.artifacts import read_artifact

NAME = "Wiki Curator"

MAX_PROPOSALS_PER_TURN = 3
PER_SLUG_RATE_LIMIT_HOURS = 24

SYSTEM = """You are the Wiki Curator in the AI Retail OS.

You run as a *post-turn observer*. The operator's chat turn just
finished; you see the resulting artifacts + spine events. Your only
job: spot durable lessons worth committing to the wiki, and propose
edits as drafts. The Critic decides if they ship.

Hard rules:
  1. **Never** propose more than 3 wiki edits per turn.
  2. Each edit MUST be agent-coined into one of these slug families:
       category/<slug>/<topic>
       vendor/<slug>/<topic>
       store/<id>/<topic>
       policy/<topic>
       playbook/<topic>
  3. Each edit MUST cite the spine event ids OR artifact ids that
     prove the lesson. Pages without refs are noise.
  4. If a published page on the same slug already exists and would
     not materially change, do NOT re-propose. Skip silently.
  5. Be terse. Wiki bodies are 5-15 lines max.

Output exactly:
  - 0..3 wiki_propose_edit calls
  - then a single text reply naming what you proposed (or "no
    proposals" if nothing worth keeping happened this turn).
"""


def _is_enabled() -> bool:
    return os.getenv("WIKI_CURATOR_ENABLED", "1").strip().lower() not in ("0", "false", "no", "")


def _check_rate_limit(slug: str) -> bool:
    """Return True if the slug was last proposed > PER_SLUG_RATE_LIMIT_HOURS ago.

    Walks recent wiki_edit events and rejects if any was authored by
    the Curator within the rate-limit window for this slug.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=PER_SLUG_RATE_LIMIT_HOURS)).isoformat()
    recent = events_since_ts(cutoff)
    for ev in recent:
        if ev.get("kind") != "wiki_edit":
            continue
        if ev.get("agent") != NAME:
            continue
        if (ev.get("payload") or {}).get("slug") == slug:
            return False
    return True


def _proposals_this_turn(turn_start_iso: str) -> int:
    """Count Curator proposals already emitted this turn — backstop for
    the per-turn cap so a single misbehaving LLM call can't spam the
    wiki even if the prompt's "max 3" guidance is ignored."""
    count = 0
    for ev in events_since_ts(turn_start_iso):
        if ev.get("kind") == "wiki_edit" and ev.get("agent") == NAME:
            count += 1
    return count


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WIKI_SEARCH_TOOL, _wiki_search_impl = build_wiki_search_tool()
_WIKI_READ_TOOL, _wiki_read_impl = build_wiki_read_tool()
_WIKI_PROPOSE_TOOL_BASE, _wiki_propose_impl_base = build_wiki_propose_edit_tool(NAME)


def _wiki_propose_with_caps(turn_start_iso: str):
    """Wrap the bare propose impl with per-turn + per-slug rate
    limiting so the Curator can't blow past either cap regardless of
    what the LLM does."""

    def _impl(args: dict) -> dict:
        if _proposals_this_turn(turn_start_iso) >= MAX_PROPOSALS_PER_TURN:
            return {
                "error": (
                    f"per-turn cap reached: max {MAX_PROPOSALS_PER_TURN} "
                    "Curator proposals per operator turn."
                )
            }
        slug = (args.get("slug") or "").strip()
        if slug and not _check_rate_limit(slug):
            return {
                "error": (
                    f"per-slug rate limit: '{slug}' was already proposed "
                    f"by the Curator within the last "
                    f"{PER_SLUG_RATE_LIMIT_HOURS}h. Skip."
                )
            }
        return _wiki_propose_impl_base(args)

    return _impl


def build_agent(turn_start_iso: str | None = None) -> Agent:
    """Curator builder. `turn_start_iso` scopes the per-turn cap; if
    None, uses now() — useful for ad-hoc invocations / tests."""
    if turn_start_iso is None:
        turn_start_iso = datetime.now(timezone.utc).isoformat()
    impls = {
        "read_artifact": _read_artifact_impl,
        "wiki_search": _wiki_search_impl,
        "wiki_read": _wiki_read_impl,
        "wiki_propose_edit": _wiki_propose_with_caps(turn_start_iso),
    }
    tools = [_READ_ARTIFACT_TOOL, _WIKI_SEARCH_TOOL, _WIKI_READ_TOOL, _WIKI_PROPOSE_TOOL_BASE]
    return Agent(
        name=NAME,
        system_prompt=resolve_prompt(NAME, SYSTEM),
        tools=tools,
        tool_impls=impls,
        max_iters=6,
    )


def curate_turn(
    turn_start_iso: str,
    operator_input: str,
    llm: LLMProvider,
) -> dict:
    """Drive the Curator over a finished turn. Idempotent — caller is
    expected to invoke at most once per turn. Returns a small summary
    dict (count, slugs_proposed, error) for the orchestrator's logs.
    """
    if not _is_enabled():
        return {"enabled": False, "count": 0, "slugs": []}
    # Walk events from this turn so the Curator has the evidence it
    # needs to ground its proposals.
    turn_events = events_since_ts(turn_start_iso)
    # Pull artifact bodies for any artifact_id that landed during the
    # turn — gives the Curator the actual draft text instead of just
    # event metadata.
    artifact_bodies: list[dict] = []
    seen_aids: set[str] = set()
    for ev in turn_events:
        aid = ev.get("artifact_id")
        if aid and aid not in seen_aids:
            seen_aids.add(aid)
            art = read_artifact(aid)
            if art:
                artifact_bodies.append(
                    {
                        "id": art.get("id"),
                        "title": art.get("title"),
                        "kind": art.get("kind"),
                        "stage": art.get("stage"),
                        "body": (art.get("body") or "")[:2000],
                    }
                )

    task = (
        f"Operator's prompt this turn: {operator_input!r}\n\n"
        f"Events in this turn: {len(turn_events)} total.\n\n"
        f"Artifacts produced (id, title, kind, stage, body excerpt):\n"
        + "\n---\n".join(
            f"id={a['id']} kind={a.get('kind')} stage={a.get('stage')} "
            f"title={a.get('title')!r}\n{a.get('body','')}"
            for a in artifact_bodies
        )
        + "\n\nPropose at most 3 wiki edits as drafts. Cite artifact_id "
        "values from the list above in `refs`. If nothing is worth "
        "keeping, reply with one line saying so and emit no edits."
    )

    agent = build_agent(turn_start_iso=turn_start_iso)
    final_text = ""
    slugs_proposed: list[str] = []
    t0 = time.monotonic()
    try:
        for ev in agent.run(task, llm):
            if ev.kind == "tool_result":
                r = ev.data.get("result", {})
                if isinstance(r, dict) and r.get("slug"):
                    slugs_proposed.append(r["slug"])
            elif ev.kind == "agent_end":
                final_text = ev.data.get("text", "")
            elif ev.kind == "error":
                # Curator errors are non-fatal — caller catches +
                # ignores. Surface in the summary so operators can
                # check it via the spine event log.
                append_event(
                    agent=NAME,
                    kind="observation",
                    payload={
                        "error": ev.data.get("error"),
                        "stage": "curator_error",
                    },
                )
                return {
                    "enabled": True,
                    "count": len(slugs_proposed),
                    "slugs": slugs_proposed,
                    "error": ev.data.get("error"),
                }
    except Exception as exc:
        return {
            "enabled": True,
            "count": len(slugs_proposed),
            "slugs": slugs_proposed,
            "error": str(exc),
        }
    elapsed = round(time.monotonic() - t0, 2)
    append_event(
        agent=NAME,
        kind="observation",
        payload={
            "stage": "curator_summary",
            "count": len(slugs_proposed),
            "slugs": slugs_proposed,
            "elapsed_s": elapsed,
            "summary": final_text[:200],
        },
    )
    return {
        "enabled": True,
        "count": len(slugs_proposed),
        "slugs": slugs_proposed,
        "elapsed_s": elapsed,
    }
