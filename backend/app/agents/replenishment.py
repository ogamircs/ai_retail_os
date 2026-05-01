from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
from app.spine.events import append_event
from app.substrate import inventory, pos, omnichannel

NAME = "Replenishment"

SYSTEM = """You are the Replenishment specialist agent in the AI Retail OS.
You are the DRI for stock levels: forecast demand, place purchase orders, hold or accelerate POs.

Decision pattern:
1. Identify SKUs at risk of stockout (on_hand near or below reorder_point).
2. Check recent sales velocity to size the order.
3. Place a PO via apply_po (90 days of cover at recent velocity is a reasonable default).
4. For overstocked summer apparel during a markdown: HOLD inbound POs (set qty=0 with reason).
5. Always write a plan artifact and log an event for each decision.
"""


def _tool_get_stock(args: dict) -> dict:
    sku = args.get("sku", "")
    s = inventory.get_stock(sku)
    return s or {"error": f"sku not found: {sku}"}


def _tool_list_skus(args: dict) -> dict:
    return {"skus": inventory.list_skus(category=args.get("category"))}


def _tool_query_sales(args: dict) -> dict:
    return pos.query_sales(
        sku=args.get("sku"),
        days=int(args.get("days", 30)),
        category=args.get("category"),
    )


def _tool_inventory_health(args: dict) -> dict:
    return omnichannel.inventory_health()


def _tool_hold_or_expedite_po(args: dict) -> dict:
    return omnichannel.hold_or_expedite_po(
        category=args["category"],
        mode=args.get("mode", "hold"),
        reason=args.get("reason", ""),
    )


def _tool_apply_po(args: dict) -> dict:
    sku = args["sku"]
    qty = int(args["qty"])
    vendor = args.get("vendor", "")
    reason = args.get("reason", "")
    if qty == 0:
        # Special: hold/cancel — log without mutating stock
        eid = append_event(
            agent=NAME,
            kind="action",
            sku=sku,
            payload={"action": "po_hold", "reason": reason, "qty": 0, "vendor": vendor},
        )
        return {"action": "hold", "sku": sku, "vendor": vendor, "event_id": eid}
    result = inventory.apply_po(sku, qty, vendor)
    eid = append_event(
        agent=NAME,
        kind="action",
        sku=sku,
        payload={"action": "po", "reason": reason, "vendor": vendor, **result},
    )
    return {**result, "event_id": eid}


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="plan"
)


TOOLS = [
    Tool(
        name="get_stock",
        description="Get stock for a SKU: on_hand, reorder_point, current price.",
        input_schema={
            "type": "object",
            "properties": {"sku": {"type": "string"}},
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
                "days": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="inventory_health",
        description="Read category/SKU inventory health and inbound POs.",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="hold_or_expedite_po",
        description="Hold or expedite inbound supplier POs for a category and log the action.",
        input_schema={
            "type": "object",
            "properties": {
                "category": {"type": "string"},
                "mode": {"type": "string", "description": "hold or expedite"},
                "reason": {"type": "string"},
            },
            "required": ["category"],
        },
    ),
    Tool(
        name="apply_po",
        description="Place a purchase order. Use qty=0 to mark a hold/cancel for an inbound PO.",
        input_schema={
            "type": "object",
            "properties": {
                "sku": {"type": "string"},
                "qty": {"type": "integer"},
                "vendor": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["sku", "qty"],
        },
    ),
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
]

IMPLS = {
    "get_stock": _tool_get_stock,
    "list_skus": _tool_list_skus,
    "query_sales": _tool_query_sales,
    "inventory_health": _tool_inventory_health,
    "hold_or_expedite_po": _tool_hold_or_expedite_po,
    "apply_po": _tool_apply_po,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=12)
