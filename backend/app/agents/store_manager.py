from app.agents.base import Agent
from app.llm.base import Tool
from app.spine.events import append_event
from app.spine.artifacts import write_artifact
from app.substrate import omnichannel

NAME = "Store Manager"

SYSTEM = """You are the Store Manager specialist agent in the AI Retail OS.
You are the DRI for store-level execution: local exceptions, labor pressure, floor tasks,
BOPIS readiness, and transfer execution.

Decision pattern:
1. Read store pressure and local demand before creating tasks.
2. Avoid adding heavy work to stores with high labor pressure unless the business need is urgent.
3. Use create_store_task for concrete store work.
4. Always write a store-ops artifact when the plan changes store execution.
"""


def _tool_list_stores(args: dict) -> dict:
    return {"stores": omnichannel.list_stores()}


def _tool_create_store_task(args: dict) -> dict:
    return omnichannel.create_store_task(
        store_id=args["store_id"],
        title=args["title"],
        priority=args.get("priority", "normal"),
        reason=args.get("reason", ""),
    )


def _tool_write_artifact(args: dict) -> dict:
    aid = write_artifact(
        agent=NAME,
        kind=args.get("kind", "store_ops_plan"),
        title=args.get("title", "Store execution plan"),
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
        name="list_stores",
        description="List stores with capacity, labor pressure, local demand, inventory, and margin.",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="create_store_task",
        description="Create a store execution task in the action queue.",
        input_schema={
            "type": "object",
            "properties": {
                "store_id": {"type": "string"},
                "title": {"type": "string"},
                "priority": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["store_id", "title"],
        },
    ),
    Tool(
        name="write_artifact",
        description="Persist a store operations artifact. Returns artifact_id.",
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
    "list_stores": _tool_list_stores,
    "create_store_task": _tool_create_store_task,
    "write_artifact": _tool_write_artifact,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
