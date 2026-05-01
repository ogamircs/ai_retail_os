from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
from app.spine.events import append_event
from app.substrate import inventory, pos, omnichannel

NAME = "Pricing & Promo"

SYSTEM = """You are the Pricing & Promo specialist agent in the AI Retail OS.
You are the DRI (directly responsible individual) for pricing decisions: markdowns, promos, and price experiments.

You have access to read SKU + inventory + sales data and to apply markdowns. Every action you take should:
1. Be backed by data — query sales/inventory first.
2. Produce an artifact (markdown plan) explaining your reasoning.
3. Log an event for closed-loop measurement (so the Analyst can later check if the markdown lifted sales).

Be decisive. When asked to act, propose a concrete plan and apply it. Use list_skus to discover SKUs in a category before acting.
Recommend markdown percentages between 15% and 40% for overstocked items. Apply markdowns to no more than 5 SKUs per task to keep things tractable.
"""


def _tool_get_sku(args: dict) -> dict:
    sku = args.get("sku", "")
    s = inventory.get_sku(sku)
    return s or {"error": f"sku not found: {sku}"}


def _tool_list_skus(args: dict) -> dict:
    category = args.get("category")
    return {"skus": inventory.list_skus(category=category)}


def _tool_query_sales(args: dict) -> dict:
    return pos.query_sales(
        sku=args.get("sku"),
        days=int(args.get("days", 30)),
        category=args.get("category"),
    )


def _tool_list_categories(args: dict) -> dict:
    return {"categories": omnichannel.list_categories(days=int(args.get("days", 30)))}


def _tool_create_promotion(args: dict) -> dict:
    return omnichannel.create_promotion(
        category=args["category"],
        offer=args["offer"],
        discount_percent=float(args["discount_percent"]),
        reason=args.get("reason", ""),
    )


def _tool_apply_markdown(args: dict) -> dict:
    sku = args["sku"]
    percent = float(args["percent"])
    reason = args.get("reason", "")
    result = inventory.apply_markdown(sku, percent)
    eid = append_event(
        agent=NAME,
        kind="action",
        sku=sku,
        payload={"action": "markdown", "reason": reason, **result},
    )
    return {**result, "event_id": eid}


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="plan"
)


TOOLS = [
    Tool(
        name="get_sku",
        description="Get full details for one SKU (name, category, on_hand, price, base_price).",
        input_schema={
            "type": "object",
            "properties": {"sku": {"type": "string", "description": "SKU id, e.g. SUM-002"}},
            "required": ["sku"],
        },
    ),
    Tool(
        name="list_skus",
        description="List SKUs, optionally filtered by category. Categories: summer_apparel, home, electronics.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
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
                "days": {"type": "integer", "default": 30},
            },
        },
    ),
    Tool(
        name="list_categories",
        description="List category momentum, inventory pressure, margin, and push score before pricing a promotion.",
        input_schema={"type": "object", "properties": {"days": {"type": "integer"}}},
    ),
    Tool(
        name="create_promotion",
        description="Create a category-level promotion proposal in the action queue, with policy guardrails.",
        input_schema={
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "offer": {"type": "string"},
                "discount_percent": {"type": "number"},
                "reason": {"type": "string"},
            },
            "required": ["category", "offer", "discount_percent"],
        },
    ),
    Tool(
        name="apply_markdown",
        description="Apply a markdown to a SKU. Updates substrate price and logs an event.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "percent": {"type": "number", "description": "Percent off, 0-100"},
                "reason": {"type": "string"},
            },
            "required": ["sku", "percent"],
        },
    ),
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
]

IMPLS = {
    "get_sku": _tool_get_sku,
    "list_skus": _tool_list_skus,
    "query_sales": _tool_query_sales,
    "list_categories": _tool_list_categories,
    "create_promotion": _tool_create_promotion,
    "apply_markdown": _tool_apply_markdown,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=12)
