"""Core read APIs over the spine + substrate.

Config, audit (events / artifacts), KPI / category / campaign / inventory
roll-ups, store + order listings, action queue, knowledge-graph
neighbourhoods. None of these mutate state beyond `POST /api/config`,
which flips the active LLM provider for the running process.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from app.config import settings
from app.schemas import ConfigOut, ConfigSet
from app.spine import artifacts as art_store
from app.spine import events as ev_store
from app.spine import kg as kg_store
from app.substrate import omnichannel

router = APIRouter()


@router.get("/api/config", response_model=ConfigOut)
def get_config():
    return ConfigOut(
        provider=settings.provider,
        model=settings.model,
        has_key=bool(settings.api_key),
    )


@router.post("/api/config", response_model=ConfigOut)
def set_config(body: ConfigSet):
    settings.set_provider(body.provider)
    return ConfigOut(
        provider=settings.provider,
        model=settings.model,
        has_key=bool(settings.api_key),
    )


@router.get("/api/events")
def list_events(since: int = 0, limit: int = 200):
    return {"events": ev_store.list_events(since_id=since, limit=limit)}


@router.get("/api/artifacts")
def list_artifacts():
    return {"artifacts": art_store.list_artifacts()}


@router.get("/api/artifacts/{aid}")
def get_artifact(aid: str):
    art = art_store.read_artifact(aid)
    if not art:
        raise HTTPException(404, "artifact not found")
    return art


@router.get("/api/kpis")
def get_kpis():
    return omnichannel.executive_kpis()


@router.get("/api/categories")
def get_categories():
    return {"categories": omnichannel.list_categories()}


@router.get("/api/marketing/campaigns")
def get_campaigns():
    return {"campaigns": omnichannel.list_campaigns()}


@router.get("/api/inventory/health")
def get_inventory_health():
    return omnichannel.inventory_health()


@router.get("/api/stores")
def get_stores():
    return {"stores": omnichannel.list_stores()}


@router.get("/api/orders")
def get_orders(limit: int = 50, category: str | None = None):
    return {"orders": omnichannel.list_orders(limit=limit, category=category)}


@router.get("/api/action-queue")
def get_action_queue():
    return {"actions": omnichannel.list_action_queue()}


@router.get("/api/kg/neighborhood")
def get_kg_neighborhood(id: str):
    return kg_store.neighborhood(id)
