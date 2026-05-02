"""Critic agent — read-only review of another agent's draft artifact.

Track 2 A1 of the agent-mesh upgrade. The Critic does not write substrate
and does not mint operator-facing recommendations on its own. It reads a
draft artifact, runs the same read-only spine queries that produced it,
and emits a `critique` artifact structured as Verified / Gaps / Risks /
Counter-recommendation. The Chief invokes the Critic via
`delegate_to_critic`; A2 (the round-trip) wires the original drafter to
read the critique and revise.
"""

from __future__ import annotations

from app.agents.base import Agent
from app.agents._mesh_tools import build_wiki_read_tool, build_wiki_search_tool
from app.llm.base import Tool
from app.llm.prompts import resolve_prompt
from app.spine.events import append_event, events_since_ts
from app.spine.artifacts import read_artifact, write_artifact
from app.substrate import omnichannel, pos


NAME = "Critic"

SYSTEM = """You are the Critic specialist in the AI Retail OS.
You do not propose new actions. You do not modify substrate. You audit a draft
artifact written by another specialist and produce a structured critique.

Your job is to push back, not rubber-stamp. For every draft, run the same
spine queries the original agent would have run and look for:
  (a) factual mistakes — claims that don't match what `query_sales`,
      `aggregate_by_category`, `inventory_health`, `get_kpis`, or
      `list_categories` actually return for the cited window;
  (b) logical gaps — recommendations that skip an obvious alternative
      ("why markdown 25% instead of 15%?", "why category-wide instead of
      a single SKU?", "what if the heatwave breaks?");
  (c) policy violations — discounts above 40%, campaign budgets above
      $65k, store-transfers from a low-on-hand store, replenishment holds
      that ignore on-hand depletion;
  (d) missing alternatives — at least one option the draft did not
      consider. Spell it out.
  (e) overclaim — recommendations whose projected impact isn't supported
      by the evidence cited (or any evidence).

Output exactly one artifact via `write_artifact` with:
  kind:  "critique"
  title: "Critique of <original_artifact.title>"
  refs:  [<original_artifact_id>]
  body:  markdown with these four headings, in order:
           ## Verified
           ## Gaps
           ## Risks
           ## Counter-recommendation
  Each heading is required. If you found nothing for a heading, write
  "*No material findings.*" — never delete the heading.

Be terse. Cite numbers. Quote the draft's own claims when contradicting them.
"""


def _tool_read_artifact(args: dict) -> dict:
    aid = args["artifact_id"]
    art = read_artifact(aid)
    if not art:
        return {"error": f"unknown artifact {aid}"}
    return art


def _tool_read_events(args: dict) -> dict:
    since = args.get("since_ts", "1970-01-01T00:00:00")
    return {"events": events_since_ts(since)}


def _tool_query_sales(args: dict) -> dict:
    return pos.query_sales(
        sku=args.get("sku"),
        days=int(args.get("days", 30)),
        category=args.get("category"),
    )


def _tool_aggregate_by_category(args: dict) -> dict:
    return pos.aggregate_by_category(days=int(args.get("days", 30)))


def _tool_daily_sales(args: dict) -> dict:
    return {"daily": pos.daily_sales(args["sku"], days=int(args.get("days", 30)))}


def _tool_get_kpis(_: dict) -> dict:
    return omnichannel.executive_kpis()


def _tool_list_categories(args: dict) -> dict:
    return {"categories": omnichannel.list_categories(days=int(args.get("days", 30)))}


def _tool_list_campaigns(_: dict) -> dict:
    return {"campaigns": omnichannel.list_campaigns()}


def _tool_inventory_health(_: dict) -> dict:
    return omnichannel.inventory_health()


def _tool_list_orders(args: dict) -> dict:
    return {
        "orders": omnichannel.list_orders(
            limit=int(args.get("limit", 50)),
            category=args.get("category"),
        )
    }


def _tool_write_artifact(args: dict) -> dict:
    """Persist the critique. We force kind='critique' regardless of what the
    model passes so the artifact viewer can route critique vs report cleanly,
    and so A4's stage chip in the cockpit doesn't depend on prompt fidelity.
    Refs are required AND each ref must resolve to a real artifact — a critique
    without a real backlink (empty list, or a hallucinated/mistyped id) breaks
    the contract and would orphan the critique in downstream review/revision
    flows.
    """
    raw_refs = args.get("refs")
    refs = [r.strip() for r in raw_refs if isinstance(r, str) and r.strip()] if isinstance(raw_refs, list) else []
    if not refs:
        return {
            "error": (
                "write_artifact requires refs to be a non-empty list of source "
                "artifact ids — the critique must link back to the draft it audits."
            )
        }
    unresolved = [rid for rid in refs if read_artifact(rid) is None]
    if unresolved:
        return {
            "error": (
                f"write_artifact refs do not resolve to existing artifacts: "
                f"{unresolved}. Each ref must be an artifact id you fetched via "
                f"read_artifact in this session."
            )
        }
    aid = write_artifact(
        agent=NAME,
        kind="critique",
        title=args.get("title", "Critique"),
        body_md=args.get("body_md", ""),
        refs=refs,
        stage="critique",
    )
    eid = append_event(
        agent=NAME,
        kind="observation",
        payload={"artifact_title": args.get("title", ""), "refs": refs},
        artifact_id=aid,
    )
    return {"artifact_id": aid, "event_id": eid}


TOOLS = [
    Tool(
        name="read_artifact",
        description=(
            "Fetch the full body + metadata of an artifact by id. Use this to "
            "load the draft you are reviewing."
        ),
        input_schema={
            "type": "object",
            "properties": {"artifact_id": {"type": "string"}},
            "required": ["artifact_id"],
        },
    ),
    Tool(
        name="read_events",
        description="Read all spine events since an ISO 8601 timestamp.",
        input_schema={
            "type": "object",
            "properties": {"since_ts": {"type": "string"}},
        },
    ),
    Tool(
        name="query_sales",
        description="Query sales for a SKU or category over the last N days.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "category": {"type": "string"},
                "days": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="aggregate_by_category",
        description="Aggregate sales by category over the last N days.",
        input_schema={
            "type": "object",
            "properties": {"days": {"type": "integer"}},
        },
    ),
    Tool(
        name="daily_sales",
        description="Daily sales for one SKU over the last N days.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "days": {"type": "integer"},
            },
            "required": ["sku"],
        },
    ),
    Tool(
        name="get_kpis",
        description="Read executive omnichannel KPIs (revenue, margin, inventory risk, …).",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_categories",
        description="List category momentum, inventory pressure, margin, and push score.",
        input_schema={
            "type": "object",
            "properties": {"days": {"type": "integer"}},
        },
    ),
    Tool(
        name="list_campaigns",
        description="List marketing campaigns with projected and actual lift/ROI.",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="inventory_health",
        description="Read SKU/category/inbound PO inventory health.",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_orders",
        description="List recent omnichannel orders, optionally filtered by category.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "category": {"type": "string"},
            },
        },
    ),
    Tool(
        name="write_artifact",
        description=(
            "Persist the critique as a markdown artifact (kind is forced to "
            "'critique' regardless of what you pass). `refs` MUST contain at "
            "least the id of the audited draft — a critique without a "
            "backlink is rejected. Returns artifact_id."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "refs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "description": "Source artifact ids being critiqued. Must include the audited draft.",
                },
            },
            "required": ["title", "body_md", "refs"],
        },
    ),
]

# Track 5 W2: Critic gets read-only access to the agentic wiki so it
# can cite a published lesson when contradicting a draft. No
# wiki_propose_edit — Critic doesn't author, it audits.
_CRITIC_WIKI_SEARCH_TOOL, _critic_wiki_search_impl = build_wiki_search_tool()
_CRITIC_WIKI_READ_TOOL, _critic_wiki_read_impl = build_wiki_read_tool()
TOOLS.extend([_CRITIC_WIKI_SEARCH_TOOL, _CRITIC_WIKI_READ_TOOL])


IMPLS = {
    "read_artifact": _tool_read_artifact,
    "read_events": _tool_read_events,
    "query_sales": _tool_query_sales,
    "aggregate_by_category": _tool_aggregate_by_category,
    "daily_sales": _tool_daily_sales,
    "get_kpis": _tool_get_kpis,
    "list_categories": _tool_list_categories,
    "list_campaigns": _tool_list_campaigns,
    "inventory_health": _tool_inventory_health,
    "list_orders": _tool_list_orders,
    "write_artifact": _tool_write_artifact,
    "wiki_search": _critic_wiki_search_impl,
    "wiki_read": _critic_wiki_read_impl,
}


def build_agent() -> Agent:
    return Agent(
        name=NAME,
        system_prompt=resolve_prompt(NAME, SYSTEM),
        tools=TOOLS,
        tool_impls=IMPLS,
        max_iters=10,
    )
