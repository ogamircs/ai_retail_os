from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
from app.llm.prompts import resolve_prompt
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


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="allocation_plan"
)


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
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
]

IMPLS = {
    "list_categories": _tool_list_categories,
    "category_detail": _tool_category_detail,
    "allocate_inventory": _tool_allocate_inventory,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=resolve_prompt(NAME, SYSTEM), tools=TOOLS, tool_impls=IMPLS, max_iters=10)
