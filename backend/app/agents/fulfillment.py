from app.agents.base import Agent
from app.llm.base import Tool
from app.spine.events import append_event
from app.spine.artifacts import write_artifact
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


def _tool_write_artifact(args: dict) -> dict:
    aid = write_artifact(
        agent=NAME,
        kind=args.get("kind", "fulfillment_plan"),
        title=args.get("title", "Fulfillment routing plan"),
        body_md=args.get("body_md", ""),
        refs=args.get("refs", []),
    )
    eid = append_event(
        agent=NAME,
        kind="proposal",
        payload={"artifact_title": args.get("title", "")},
        artifact_id=aid,
    )
    return {"artifact_id": aid, "event_id": eid}


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
    Tool(
        name="write_artifact",
        description="Persist a fulfillment artifact. Returns artifact_id.",
        input_schema={
            "type": "object",
            "properties": {
                "kind": {"type": "string"},
                "title": {"type": "string"},
                "body_md": {"type": "string"},
                "refs": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "body_md"],
        },
    ),
]

IMPLS = {
    "list_orders": _tool_list_orders,
    "route_fulfillment": _tool_route_fulfillment,
    "write_artifact": _tool_write_artifact,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
