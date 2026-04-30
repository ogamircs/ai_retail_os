import json
import asyncio
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from app.config import settings
from app.spine.db import init_db
from app.spine import events as ev_store
from app.spine import artifacts as art_store
from app.spine import kg as kg_store
from app.substrate import omnichannel
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
