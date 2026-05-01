from app.agents.base import Agent
from app.agents._mesh_tools import (
    build_wiki_search_tool,
    build_wiki_read_tool,
    build_wiki_propose_edit_tool,
    build_read_artifact_tool,
    build_stage_write_artifact_tool,
)
from app.llm.base import Tool
from app.llm.prompts import resolve_prompt
from app.spine.events import append_event
from app.substrate import omnichannel

NAME = "Marketing"

SYSTEM = """You are the Marketing specialist agent in the AI Retail OS.
You are the DRI for category pushes: deciding which categories to push, which segments to target,
which channels/offers/budgets to use, and how to measure ROI.

Decision pattern:
1. Use recommend_category_push or create_campaign_brief before launching anything.
2. Keep campaigns category-led, segment-specific, channel-aware, and budgeted.
3. Respect policy guardrails; request approval when budget or offer risk is high.
4. Launch a mock campaign only when the operator asks for action or a plan that should be applied.
5. Always write a campaign artifact and make measurement easy for the Analyst later.
"""


def _tool_recommend_category_push(args: dict) -> dict:
    return omnichannel.recommend_category_push(category=args.get("category"))


def _tool_create_campaign_brief(args: dict) -> dict:
    return omnichannel.create_campaign_brief(category=args.get("category"))


def _tool_launch_mock_campaign(args: dict) -> dict:
    return omnichannel.launch_mock_campaign(
        category=args["category"],
        segment_id=args["segment_id"],
        channel=args["channel"],
        budget=float(args["budget"]),
        offer=args["offer"],
        projected_lift=float(args.get("projected_lift", 0.15)),
        projected_roi=float(args.get("projected_roi", 1.6)),
        title=args.get("title"),
    )


def _tool_measure_campaign(args: dict) -> dict:
    return omnichannel.measure_campaign(campaign_id=args.get("campaign_id"))


def _tool_request_approval(args: dict) -> dict:
    return omnichannel.request_approval(
        owner=NAME,
        title=args["title"],
        reason=args["reason"],
        payload=args.get("payload", {}),
    )


_READ_ARTIFACT_TOOL, _read_artifact_impl = build_read_artifact_tool()
_WRITE_ARTIFACT_TOOL, _write_artifact_impl = build_stage_write_artifact_tool(
    NAME, default_kind="campaign_brief"
)
_WIKI_SEARCH_TOOL, _wiki_search_impl = build_wiki_search_tool()
_WIKI_READ_TOOL, _wiki_read_impl = build_wiki_read_tool()
_WIKI_PROPOSE_TOOL, _wiki_propose_impl = build_wiki_propose_edit_tool(NAME)


TOOLS = [
    Tool(
        name="recommend_category_push",
        description="Recommend the best category, segment, channel, offer, budget, and ROI for a marketing push.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
        },
    ),
    Tool(
        name="create_campaign_brief",
        description="Create an action-queue campaign brief for a category push.",
        input_schema={
            "type": "object",
            "properties": {"category": {"type": "string"}},
        },
    ),
    Tool(
        name="launch_mock_campaign",
        description="Launch a mocked marketing campaign and log a campaign_launch event.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "category": {"type": "string"},
                "segment_id": {"type": "string"},
                "channel": {"type": "string"},
                "budget": {"type": "number"},
                "offer": {"type": "string"},
                "projected_lift": {"type": "number"},
                "projected_roi": {"type": "number"},
            },
            "required": ["category", "segment_id", "channel", "budget", "offer"],
        },
    ),
    Tool(
        name="measure_campaign",
        description="Measure a launched campaign and log a measurement event. Defaults to latest campaign.",
        input_schema={
            "type": "object",
            "properties": {"campaign_id": {"type": "string"}},
        },
    ),
    Tool(
        name="request_approval",
        description="Put a marketing approval request into the action queue.",
        input_schema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "reason": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["title", "reason"],
        },
    ),
    _READ_ARTIFACT_TOOL,
    _WRITE_ARTIFACT_TOOL,
    _WIKI_SEARCH_TOOL,
    _WIKI_READ_TOOL,
    _WIKI_PROPOSE_TOOL,
]

IMPLS = {
    "recommend_category_push": _tool_recommend_category_push,
    "create_campaign_brief": _tool_create_campaign_brief,
    "launch_mock_campaign": _tool_launch_mock_campaign,
    "measure_campaign": _tool_measure_campaign,
    "request_approval": _tool_request_approval,
    "read_artifact": _read_artifact_impl,
    "write_artifact": _write_artifact_impl,
    "wiki_search": _wiki_search_impl,
    "wiki_read": _wiki_read_impl,
    "wiki_propose_edit": _wiki_propose_impl,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=resolve_prompt(NAME, SYSTEM), tools=TOOLS, tool_impls=IMPLS, max_iters=12)
