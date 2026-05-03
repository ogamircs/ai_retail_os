"""Shared mesh-tool kit for action specialists (Track 2 A2 + A3).

Every action specialist (Pricing, Marketing, Replenishment, Merchandiser,
Fulfillment, Store Manager — and the Analyst when it produces drafts)
needs three things on top of its own substrate kit so the Chief's
review-loop works:

  1. `read_artifact`          — load a draft or critique by id.
  2. a `stage`-aware write_artifact wrapper — first emission lands as
     `draft`; revision rounds land as `revision`; peer reviews land as
     `peer_review`. The Chief flips the converged output to `final`
     in place via `artifacts.update_artifact_stage`.
  3. `peer_review` (A3)       — scoped read-only review of another
     specialist's draft, kind = `peer_review`. Uses the same shape
     as the Critic but the prompt context comes from the peer's own
     domain (Pricing reviewing Marketing's push, etc.).

The Critic remains a separate module — its prompt is generic-audit
shaped and it ships a different toolkit. Peer review is intentionally
weaker (no policy-wide checks, just "what does *my* domain notice
about this?").
"""

from __future__ import annotations

from typing import Any, Callable

from app.llm.base import Tool
from app.spine.artifacts import read_artifact, write_artifact
from app.spine.events import append_event
from app.spine import wiki as wiki_store


def build_read_artifact_tool() -> tuple[Tool, Callable[[dict], dict]]:
    def _impl(args: dict) -> dict:
        aid = args.get("artifact_id", "").strip()
        if not aid:
            return {"error": "read_artifact requires non-empty artifact_id"}
        art = read_artifact(aid)
        if not art:
            return {"error": f"unknown artifact {aid}"}
        return art

    tool = Tool(
        name="read_artifact",
        description=(
            "Fetch the full body + metadata of an artifact by id. Use this "
            "to load the draft you are revising, or a critique you are "
            "responding to."
        ),
        input_schema={
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    )
    return tool, _impl


def build_stage_write_artifact_tool(
    agent_name: str, default_kind: str = "report"
) -> tuple[Tool, Callable[[dict], dict]]:
    """Return a `write_artifact` tool that respects the optional `stage` arg.

    Specialists default to stage="draft". The Chief's review loop tasks the
    specialist with revising in light of a critique — the prompt asks the
    specialist to set stage="revision" and refs=[original_id, critique_id].
    """

    def _impl(args: dict) -> dict:
        stage = args.get("stage", "draft")
        refs = args.get("refs") or []
        if not isinstance(refs, list):
            refs = []
        # Revisions and peer-reviews must cite their source — otherwise the
        # mesh's stage chain is unreadable in the Reports tab.
        if stage in ("revision", "peer_review") and not refs:
            return {
                "error": (
                    f"write_artifact stage='{stage}' requires refs to a "
                    "non-empty list of source artifact ids (the original "
                    "draft and, for revisions, the critique you are "
                    "addressing)."
                )
            }
        aid = write_artifact(
            agent=agent_name,
            kind=args.get("kind", default_kind),
            title=args.get("title", f"{agent_name} {stage}"),
            body_md=args.get("body_md", ""),
            refs=refs,
            stage=stage,
        )
        eid = append_event(
            agent=agent_name,
            kind="observation",
            payload={
                "artifact_title": args.get("title", ""),
                "stage": stage,
                "refs": refs,
            },
            artifact_id=aid,
        )
        return {"artifact_id": aid, "event_id": eid, "stage": stage}

    tool = Tool(
        name="write_artifact",
        description=(
            "Persist a markdown artifact. `stage` defaults to 'draft' on "
            "the first emission. Set stage='revision' when responding to a "
            "Critic critique (refs MUST include the original draft id and "
            "the critique id). Set stage='peer_review' when reviewing "
            "another specialist's draft (refs MUST include the draft id). "
            "Returns artifact_id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
                "stage": {
                    "type": "string",
                    "enum": ["draft", "revision", "peer_review"],
                    "description": (
                        "Lifecycle stage. 'draft' is the default first "
                        "emission. 'revision' is the response to a "
                        "critique. 'peer_review' is a scoped read-only "
                        "review of another specialist's draft."
                    ),
                },
            },
            "required": ["title", "body_md"],
        },
    )
    return tool, _impl


def revise_task_for(specialist: str, original_id: str, critique_id: str) -> str:
    """Build the Chief's tasking string for a revision round.

    Inlining this here keeps the Chief's `_run_delegate_with_review` short
    and ensures every specialist sees the exact same instructions about
    stage / refs hygiene — preventing drift across the seven specialists.
    """
    return (
        f"Revise your prior draft (artifact_id={original_id}) to address "
        f"the Critic's findings (critique_id={critique_id}).\n\n"
        "Step 1: read_artifact for both ids — get the full draft body and the "
        "full critique body.\n"
        "Step 2: address each Gap and each Risk surfaced. If a Counter-"
        "recommendation makes sense, fold it in; if not, explain why "
        "inline.\n"
        "Step 3: emit ONE write_artifact call with:\n"
        f"  - stage = 'revision'\n"
        f"  - refs  = ['{original_id}', '{critique_id}']\n"
        "  - body_md = the revised draft (keep what's right from v1, "
        "rewrite what the critique called out)\n"
        "  - title = your prior title with ' (revised)' appended\n"
        "Do not write any other artifact. Do not propose new outbox actions. "
        "If the critique returned `*No material findings.*` for both Gaps "
        "and Risks, reply with one line saying so and emit no revision."
    )


def peer_review_task_for(reviewer: str, target_id: str, scope: str) -> str:
    """Build the Chief's tasking string for an A3 peer-review round."""
    return (
        f"Peer-review the draft at artifact_id={target_id} from your "
        f"{reviewer}-domain perspective. Scope: {scope}\n\n"
        "Step 1: read_artifact to load the draft body.\n"
        "Step 2: run substrate queries you'd run *for your own domain* "
        "(don't redo the original specialist's work — surface what THEY "
        "missed from your angle).\n"
        "Step 3: emit ONE write_artifact call with:\n"
        f"  - stage = 'peer_review'\n"
        f"  - kind  = 'peer_review'\n"
        f"  - refs  = ['{target_id}']\n"
        "  - body_md = markdown with three headings: ## Agree, ## Disagree, "
        "## Add — at least one bullet under each. Cite numbers. Be terse.\n"
        "Do not propose new outbox actions. Do not write a second artifact."
    )


# ---------------------------------------------------------------------------
# Track 5 — agentic wiki tools shared by every specialist.
#
# All specialists get `wiki_search` + `wiki_read` so they can quote
# durable lessons in their drafts. Action specialists also get
# `wiki_propose_edit` so they can leave a learning behind for the next
# turn — drafts surface in the approval rail (W3); the Critic-gated
# auto-publish (W4) flips a clean draft to published without operator
# intervention.
# ---------------------------------------------------------------------------


_AGENT_SEARCH_STATUSES = ("published", "draft", "deprecated", "all")


def build_wiki_search_tool() -> tuple[Tool, Callable[[dict], dict]]:
    def _impl(args: dict) -> dict:
        q = (args.get("query") or "").strip()
        limit = max(1, min(int(args.get("limit", 10)), 50))
        # Default to published-only so unreviewed drafts + retired
        # pages don't leak into agent context as evidence. Agents can
        # opt into a wider view (e.g. "all" for an explicit audit
        # task) but the safe default keeps the publish gate's intent
        # intact: only signed-off lessons influence new decisions.
        status_arg = (args.get("status") or "published").strip().lower()
        if status_arg not in _AGENT_SEARCH_STATUSES:
            status_arg = "published"
        effective_status = None if status_arg == "all" else status_arg
        pages = wiki_store.search_pages(q, limit=limit, status=effective_status)
        return {
            "query": q,
            "status_filter": status_arg,
            "results": [
                {
                    "slug": p.slug,
                    "title": p.title,
                    "owner_agent": p.owner_agent,
                    "status": p.status,
                    "version": p.version,
                    "updated_ts": p.updated_ts,
                    # Body excerpt only — keep token cost bounded; agents
                    # can call wiki_read when they want the full text.
                    "excerpt": p.body_md[:240],
                }
                for p in pages
            ],
        }

    tool = Tool(
        name="wiki_search",
        description=(
            "Search the agentic wiki for prior lessons. Defaults to "
            "`status='published'` so only signed-off lessons surface "
            "as evidence. Pass `status='draft'` / `'deprecated'` / "
            "`'all'` only when you are explicitly auditing in-flight "
            "or retired pages. Returns up to `limit` matching pages "
            "with slug + title + 240-char excerpt; call wiki_read for "
            "the full body."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": list(_AGENT_SEARCH_STATUSES),
                    "description": (
                        "Optional stage filter. Default 'published' — "
                        "agents must opt in to see drafts/deprecated."
                    ),
                },
            },
            "required": ["query"],
        },
    )
    return tool, _impl


def build_wiki_read_tool() -> tuple[Tool, Callable[[dict], dict]]:
    def _impl(args: dict) -> dict:
        slug = (args.get("slug") or "").strip()
        if not slug:
            return {"error": "wiki_read requires non-empty slug"}
        page = wiki_store.get_page(slug)
        if page is None:
            return {"error": f"unknown wiki slug: {slug}"}
        return page.to_dict()

    tool = Tool(
        name="wiki_read",
        description=(
            "Fetch the full body + metadata of one wiki page by slug. "
            "Use after wiki_search when you need the full text to "
            "quote, or when you already know the slug from a prior "
            "turn / pinned chip."
        ),
        input_schema={
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
    )
    return tool, _impl


def build_wiki_propose_edit_tool(
    agent_name: str,
) -> tuple[Tool, Callable[[dict], dict]]:
    """W3 — write tool. Lands the page as `draft`. The Chief's review
    loop (W4) decides whether to publish, or the operator approves it
    via the cockpit's WikiTab.
    """

    def _impl(args: dict) -> dict:
        slug = (args.get("slug") or "").strip()
        title = (args.get("title") or "").strip()
        body_md = args.get("body_md") or ""
        refs = args.get("refs") or []
        if not slug or not title or not body_md:
            return {
                "error": "wiki_propose_edit requires non-empty slug, title, body_md"
            }
        if not isinstance(refs, list):
            refs = []
        page = wiki_store.propose_edit(
            slug=slug,
            title=title,
            body_md=body_md,
            author_agent=agent_name,
            refs=[str(r) for r in refs if isinstance(r, (str, int))],
        )
        return {
            "slug": page.slug,
            "version": page.version,
            "status": page.status,
            "title": page.title,
        }

    tool = Tool(
        name="wiki_propose_edit",
        description=(
            "Persist a learning to the agentic wiki as a `draft`. "
            "Slug shape is namespaced (e.g. "
            "`category/summer_apparel/markdown_playbook`, "
            "`vendor/breezeco/reliability_notes`, "
            "`policy/margin_floors`). The draft surfaces in the "
            "approval rail; the Critic-gated auto-publish promotes it "
            "to `published` if no Risks/Gaps surface. `refs` should "
            "include the spine event ids / artifact ids that prove "
            "the lesson — the cockpit renders these as inline links."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "slug": {"type": "string"},
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["slug", "title", "body_md"],
        },
    )
    return tool, _impl


# ---------------------------------------------------------------------------
# Track 6 — GBrain MCP tools (G2). Read-only specialists (Analyst, Critic)
# get brain_search / brain_read / brain_query so they can quote prior
# decisions, vendor history, code call-graph context. The Critic also picks
# up code_callers / code_def / code_refs (G4).
#
# All four tools route through `app.llm.mcp.get_client()` which falls back
# to a substrate-backed mock when GBRAIN_BEARER isn't set — same pattern as
# Track 1 integration adapters. Agents see a uniform tool surface either
# way.
# ---------------------------------------------------------------------------


def build_brain_search_tool() -> tuple[Tool, Callable[[dict], dict]]:
    from app.llm.mcp import get_client

    def _impl(args: dict) -> dict:
        q = (args.get("query") or "").strip()
        if not q:
            return {"error": "brain_search requires non-empty query"}
        limit = max(1, min(int(args.get("limit", 10)), 50))
        return get_client().search(q, limit=limit)

    tool = Tool(
        name="brain_search",
        description=(
            "Search the operator's persistent brain (GBrain) for prior "
            "decisions, vendor notes, policy snippets. Returns up to "
            "`limit` matching pages with slug + title + 240-char "
            "snippet; call brain_read for the full body. Mock-mode "
            "fallback when no GBRAIN_BEARER is configured — surfaces "
            "the cockpit's wiki pages so the agent's tool surface is "
            "consistent."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    )
    return tool, _impl


def build_brain_read_tool() -> tuple[Tool, Callable[[dict], dict]]:
    from app.llm.mcp import get_client

    def _impl(args: dict) -> dict:
        slug = (args.get("slug") or "").strip()
        if not slug:
            return {"error": "brain_read requires non-empty slug"}
        return get_client().get(slug)

    tool = Tool(
        name="brain_read",
        description=(
            "Fetch the full body + metadata of one brain page by slug. "
            "Use after brain_search when you want the full text to "
            "quote, or when you already know the slug from a prior "
            "turn."
        ),
        input_schema={
            "type": "object",
            "properties": {"slug": {"type": "string"}},
            "required": ["slug"],
        },
    )
    return tool, _impl


def build_brain_query_tool() -> tuple[Tool, Callable[[dict], dict]]:
    from app.llm.mcp import get_client

    def _impl(args: dict) -> dict:
        q = (args.get("query") or "").strip()
        if not q:
            return {"error": "brain_query requires non-empty query"}
        limit = max(1, min(int(args.get("limit", 5)), 20))
        return get_client().query(q, limit=limit)

    tool = Tool(
        name="brain_query",
        description=(
            "Ask the brain a natural-language question. Returns "
            "`{answer, citations: [{slug, title, snippet}]}`. The "
            "citations are what the agent should quote — answer alone "
            "is unverified. Mock-mode synthesises from the wiki."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
        },
    )
    return tool, _impl


_CODE_LOOKUP_KINDS = ("callers", "callees", "def", "refs")


def build_code_lookup_tool(kind: str) -> tuple[Tool, Callable[[dict], dict]]:
    """Track 6 G4 — Critic-only code-graph tools. `kind` ∈ {callers,
    callees, def, refs}. Each returns the GBrain code-graph response
    for `symbol`. Mock-mode returns an empty result with `mock=True`
    so the Critic surfaces the gap rather than hallucinating."""
    if kind not in _CODE_LOOKUP_KINDS:
        raise ValueError(f"unknown code-lookup kind: {kind}")
    from app.llm.mcp import get_client

    def _impl(args: dict) -> dict:
        symbol = (args.get("symbol") or "").strip()
        if not symbol:
            return {"error": f"code_{kind} requires non-empty symbol"}
        return get_client().code_lookup(kind, symbol)

    desc_map = {
        "callers": "List call sites that invoke `symbol`. Use when the "
        "Critic wants to verify how a policy / helper is actually used.",
        "callees": "List functions called BY `symbol`. Useful when "
        "asking 'does this draft's recommended helper actually do what "
        "the draft says it does?'",
        "def": "Locate the definition of `symbol` (file + line + "
        "snippet). Use to ground a citation in the actual source.",
        "refs": "All references to `symbol` (definitions + call sites + "
        "imports). The broadest variant.",
    }
    tool = Tool(
        name=f"code_{kind}",
        description=desc_map[kind],
        input_schema={
            "type": "object",
            "properties": {"symbol": {"type": "string"}},
            "required": ["symbol"],
        },
    )
    return tool, _impl
