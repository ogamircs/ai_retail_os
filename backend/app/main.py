import json
import asyncio
from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from app.config import settings
from app.spine.db import init_db
from app.spine import events as ev_store
from app.spine import artifacts as art_store
from app.spine import kg as kg_store
from app.substrate import omnichannel
from app.integrations import registry as integration_registry
from app.llm import get_provider
from app.agents.chief_of_staff import run_chief
from app.schemas import ChatRequest, ConfigOut, ConfigSet


app = FastAPI(title="AI Retail OS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()
    integration_registry.refresh_systems()


@app.get("/api/config", response_model=ConfigOut)
def get_config():
    return ConfigOut(
        provider=settings.provider,
        model=settings.model,
        has_key=bool(settings.api_key),
    )


@app.post("/api/config", response_model=ConfigOut)
def set_config(body: ConfigSet):
    settings.set_provider(body.provider)
    return ConfigOut(
        provider=settings.provider,
        model=settings.model,
        has_key=bool(settings.api_key),
    )


@app.get("/api/events")
def list_events(since: int = 0, limit: int = 200):
    return {"events": ev_store.list_events(since_id=since, limit=limit)}


@app.get("/api/artifacts")
def list_artifacts():
    return {"artifacts": art_store.list_artifacts()}


@app.get("/api/artifacts/{aid}")
def get_artifact(aid: str):
    art = art_store.read_artifact(aid)
    if not art:
        raise HTTPException(404, "artifact not found")
    return art


@app.get("/api/kpis")
def get_kpis():
    return omnichannel.executive_kpis()


@app.get("/api/categories")
def get_categories():
    return {"categories": omnichannel.list_categories()}


@app.get("/api/marketing/campaigns")
def get_campaigns():
    return {"campaigns": omnichannel.list_campaigns()}


@app.get("/api/inventory/health")
def get_inventory_health():
    return omnichannel.inventory_health()


@app.get("/api/stores")
def get_stores():
    return {"stores": omnichannel.list_stores()}


@app.get("/api/orders")
def get_orders(limit: int = 50, category: str | None = None):
    return {"orders": omnichannel.list_orders(limit=limit, category=category)}


@app.get("/api/action-queue")
def get_action_queue():
    return {"actions": omnichannel.list_action_queue()}


@app.get("/api/kg/neighborhood")
def get_kg_neighborhood(id: str):
    return kg_store.neighborhood(id)


@app.get("/api/integrations/systems")
def get_integration_systems():
    return {"systems": integration_registry.list_systems()}


@app.post("/api/integrations/{system}/sync")
def sync_integration(system: str):
    result = integration_registry.sync_system(system)
    if "error" in result:
        raise HTTPException(404, result["error"])
    return result


@app.get("/api/integrations/sync-runs")
def get_integration_sync_runs(limit: int = 30, system: str | None = None):
    return {"sync_runs": integration_registry.list_sync_runs(limit=limit, system_id=system)}


@app.get("/api/integrations/records")
def get_integration_records(
    system: str | None = None,
    domain: str | None = None,
    local_id: str | None = None,
    limit: int = 50,
):
    return integration_registry.list_records(
        system_id=system,
        domain=domain,
        local_id=local_id,
        limit=limit,
    )


@app.post("/api/integrations/{system}/actions/{action_id}/apply")
def apply_integration_action(system: str, action_id: int):
    result = integration_registry.apply_outbound(system, action_id)
    # Top-level "error" key = registry-level rejection (unknown system_id, etc.).
    if "error" in result:
        raise HTTPException(404, result["error"])
    # `status == "error"` = adapter ran but the external write didn't land
    # (no matching ERPNext row, empty payload, ERPNext rejected the doc).
    # The outbox row is already updated to status=error in the DB; we surface
    # it as 422 so the cockpit drawer's apply path catches and renders the
    # red chip instead of the green "applied" chip on a no-op.
    if result.get("status") == "error":
        nested = result.get("result") or {}
        message = (
            nested.get("error")
            or nested.get("message")
            or "external apply failed; outbox left in error state"
        )
        raise HTTPException(422, message)
    return result


@app.post("/api/integrations/mautic/webhook")
def mautic_webhook(payload: dict = Body(default_factory=dict)):
    return integration_registry.handle_mautic_webhook(payload)


@app.get("/api/mesh/status")
def mesh_status(window_seconds: int = 300):
    """Track 2 A6 — surface recent guardrail downgrades to the cockpit.

    Returns the most recent `mesh_downgrade` event in the last
    `window_seconds`, plus the active mesh config so the status strip
    can render a chip + tooltip without hitting the events endpoint
    directly.
    """
    from datetime import datetime, timedelta, timezone
    from app.config import mesh as mesh_settings

    cutoff = (datetime.now(timezone.utc) - timedelta(seconds=window_seconds)).isoformat()
    recent = [e for e in ev_store.events_since_ts(cutoff) if e["kind"] == "mesh_downgrade"]
    last = recent[-1] if recent else None
    return {
        "enabled": mesh_settings.enabled,
        "config": {
            "max_revision_rounds": mesh_settings.max_revision_rounds,
            "max_critic_per_draft": mesh_settings.max_critic_per_draft,
            "turn_token_budget": mesh_settings.turn_token_budget,
            "turn_wallclock_seconds": mesh_settings.turn_wallclock_seconds,
        },
        "recent_downgrade": last,
        "downgrade_count_window": len(recent),
        "window_seconds": window_seconds,
    }


@app.post("/api/chat")
async def chat(req: ChatRequest):
    """SSE stream of agent events."""
    try:
        llm = get_provider()
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    async def event_gen():
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue = asyncio.Queue()
        SENTINEL = object()

        def producer():
            try:
                for ev in run_chief(req.message, llm):
                    payload = {
                        "kind": ev.kind,
                        "agent": ev.agent,
                        "data": ev.data,
                    }
                    asyncio.run_coroutine_threadsafe(queue.put(payload), loop)
            except Exception as e:
                asyncio.run_coroutine_threadsafe(
                    queue.put({"kind": "error", "agent": "system", "data": {"error": str(e)}}),
                    loop,
                )
            finally:
                asyncio.run_coroutine_threadsafe(queue.put(SENTINEL), loop)

        task = loop.run_in_executor(None, producer)
        try:
            while True:
                item = await queue.get()
                if item is SENTINEL:
                    break
                yield {"event": "agent", "data": json.dumps(item)}
        finally:
            await task

    return EventSourceResponse(event_gen())
