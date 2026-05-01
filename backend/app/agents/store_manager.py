from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
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


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="store_ops_plan"
)


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
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
]

IMPLS = {
    "list_stores": _tool_list_stores,
    "create_store_task": _tool_create_store_task,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
