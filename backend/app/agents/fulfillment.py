from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
from app.substrate import omnichannel

NAME = "Fulfillment"

SYSTEM = """You are the Fulfillment specialist agent in the AI Retail OS.
You are the DRI for OMS-style fulfillment decisions: BOPIS, ship-from-store, and DC shipment.

Decision pattern:
1. Read recent orders and fulfillment mix before acting.
2. Favor profitable, fast fulfillment without overwhelming stores.
3. Use route_fulfillment to create a concrete routing recommendation.
4. Always write a fulfillment artifact for executive review.
"""


def _tool_list_orders(args: dict) -> dict:
    return {
        "orders": omnichannel.list_orders(
            limit=int(args.get("limit", 50)),
            category=args.get("category"),
        )
    }


def _tool_route_fulfillment(args: dict) -> dict:
    return omnichannel.route_fulfillment(args["category"])


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="fulfillment_plan"
)


TOOLS = [
    Tool(
        name="list_orders",
        description="List recent omnichannel orders, optionally by category.",
        input_schema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer"},
                "category": {"type": "string"},
            },
        },
    ),
    Tool(
        name="route_fulfillment",
        description="Create a proposed omnichannel fulfillment routing plan for a category.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    ),
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
]

IMPLS = {
    "list_orders": _tool_list_orders,
    "route_fulfillment": _tool_route_fulfillment,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
