from app.agents.base import Agent
from app.llm.base import Tool
from app.spine.events import append_event, events_since_ts
from app.spine.artifacts import write_artifact
from app.substrate import pos, omnichannel

NAME = "Analyst"

SYSTEM = """You are the Analyst specialist agent in the AI Retail OS.
You answer ad-hoc questions about the business by querying the spine (sales, events) and writing diagnostic artifacts.

You do not modify substrate. Your job is observation, measurement, and reporting.
When the operator asks "did X work?" — query events since the action timestamp + sales tables — and report quantitative deltas.
Always write a diagnostic artifact summarizing what you found.
"""


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


def _tool_read_events(args: dict) -> dict:
    since = args.get("since_ts", "1970-01-01T00:00:00")
    return {"events": events_since_ts(since)}


def _tool_get_kpis(args: dict) -> dict:
    return omnichannel.executive_kpis()


def _tool_list_categories(args: dict) -> dict:
    return {"categories": omnichannel.list_categories(days=int(args.get("days", 30)))}


def _tool_list_campaigns(args: dict) -> dict:
    return {"campaigns": omnichannel.list_campaigns()}


def _tool_inventory_health(args: dict) -> dict:
    return omnichannel.inventory_health()


def _tool_list_orders(args: dict) -> dict:
    return {
        "orders": omnichannel.list_orders(
            limit=int(args.get("limit", 50)),
            category=args.get("category"),
        )
    }


def _tool_write_artifact(args: dict) -> dict:
    aid = write_artifact(
        agent=NAME,
        kind=args.get("kind", "report"),
        title=args.get("title", "Analyst report"),
        body_md=args.get("body_md", ""),
        refs=args.get("refs", []),
    )
    eid = append_event(
        agent=NAME,
        kind="observation",
        payload={"artifact_title": args.get("title", "")},
        artifact_id=aid,
    )
    return {"artifact_id": aid, "event_id": eid}


TOOLS = [
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
        description="Daily sales for one SKU over the last N days. Use to detect trends and breakpoints.",
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
        name="read_events",
        description="Read all spine events since an ISO 8601 timestamp. Use to find prior actions (markdowns, POs).",
        input_schema={
            "type": "object",
            "properties": {"since_ts": {"type": "string"}},
        },
    ),
    Tool(
        name="get_kpis",
        description="Read executive omnichannel KPIs: revenue, margin, inventory risk, campaigns, supplier/store exceptions.",
        input_schema={"type": "object", "properties": {}},
    ),
    Tool(
        name="list_categories",
        description="List category momentum, inventory pressure, margin, and marketing push score.",
        input_schema={"type": "object", "properties": {"days": {"type": "integer"}}},
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
        description="Persist a markdown artifact (diagnostic report). Returns artifact_id.",
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
    "query_sales": _tool_query_sales,
    "aggregate_by_category": _tool_aggregate_by_category,
    "daily_sales": _tool_daily_sales,
    "read_events": _tool_read_events,
    "get_kpis": _tool_get_kpis,
    "list_categories": _tool_list_categories,
    "list_campaigns": _tool_list_campaigns,
    "inventory_health": _tool_inventory_health,
    "list_orders": _tool_list_orders,
    "write_artifact": _tool_write_artifact,
}


def build_agent() -> Agent:
    return Agent(name=NAME, system_prompt=SYSTEM, tools=TOOLS, tool_impls=IMPLS, max_iters=10)
