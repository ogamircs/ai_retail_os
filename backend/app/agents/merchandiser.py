from app.agents.base import Agent
from app.llm.base import Tool
from app.spine.events import append_event
from app.spine.artifacts import write_artifact
from app.substrate import omnichannel

NAME = "Merchandiser"

SYSTEM = """You are the Merchandiser specialist agent in the AI Retail OS.
You are the DRI for assortment health, category lifecycle, allocation, and store rebalancing.

Decision pattern:
1. Read category health before recommending moves.
2. Use lifecycle, inventory pressure, margin, and local demand to decide where inventory should go.
3. Use allocate_inventory for a concrete store-transfer recommendation.
4. Always write an allocation or category-health artifact when making a recommendation.
"""


def _tool_list_categories(args: dict) -> dict:
    return {"categories": omnichannel.list_categories(days=int(args.get("days", 30)))}


def _tool_category_detail(args: dict) -> dict:
    return omnichannel.category_detail(args["category"])


def _tool_allocate_inventory(args: dict) -> dict:
    return omnichannel.allocate_inventory(args["category"])


def _tool_write_artifact(args: dict) -> dict:
    aid = write_artifact(
        agent=NAME,
        kind=args.get("kind", "allocation_plan"),
        title=args.get("title", "Merchandising allocation plan"),
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
        name="list_categories",
        description="List category momentum, inventory pressure, margin, and push score.",
        input_schema={"type": "object", "properties": {"days": {"type": "integer"}}},
    ),
    Tool(
        name="category_detail",
        description="Get category details including customer segment affinities and inbound POs.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    ),
    Tool(
        name="allocate_inventory",
        description="Create a proposed store transfer for a category based on supply and local demand.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
            "required": ["category"],
        },
    ),
    Tool(
        name="write_artifact",
        description="Persist a merchandising artifact. Returns artifact_id.",
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
    "list_categories": _tool_list_categories,
    "category_detail": _tool_category_detail,
    "allocate_inventory": _tool_allocate_inventory,
    "write_artifact": _tool_write_artifact,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
