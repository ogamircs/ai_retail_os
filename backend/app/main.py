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


@app.get("/api/wiki/pages")
def wiki_list_pages(status: str | None = "published", limit: int = 50, owner: str | None = None):
    """Track 5 W5 — list wiki pages, filtered by status / owner.

    Default `status='published'` so the cockpit's main view shows only
    finalised lessons. Pass `status=""` (explicit empty string) to
    bypass the filter and return drafts/deprecated alongside
    published — the cockpit's "all stages" toggle uses this.
    """
    from app.spine import wiki as wiki_store

    effective_status = status if (status not in (None, "")) else None
    pages = wiki_store.list_pages(status=effective_status, owner_agent=owner, limit=limit)
    return {"pages": [p.to_dict() for p in pages]}


@app.get("/api/wiki/pages/{slug:path}")
def wiki_get_page(slug: str):
    from app.spine import wiki as wiki_store

    page = wiki_store.get_page(slug)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    revisions = wiki_store.list_revisions(slug, limit=20)
    return {"page": page.to_dict(), "revisions": revisions}


@app.get("/api/wiki/search")
def wiki_search(q: str = "", limit: int = 20, status: str | None = None):
    """Mirror /api/wiki/pages: optional `status` filter so operators
    can search within `draft` / `deprecated` / `published` instead of
    seeing mixed-status results when they're triaging in-flight wiki
    edits."""
    from app.spine import wiki as wiki_store

    pages = wiki_store.search_pages(q, limit=limit)
    effective_status = status if (status not in (None, "")) else None
    if effective_status is not None:
        pages = [p for p in pages if p.status == effective_status]
    return {"pages": [p.to_dict() for p in pages]}


@app.post("/api/wiki/pages/{slug:path}/publish")
def wiki_publish(slug: str, body: dict | None = Body(default=None)):
    from app.spine import wiki as wiki_store

    by = (body or {}).get("by_agent", "Operator")
    page = wiki_store.publish_page(slug, by_agent=by)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@app.post("/api/wiki/pages/{slug:path}/deprecate")
def wiki_deprecate(slug: str, body: dict | None = Body(default=None)):
    from app.spine import wiki as wiki_store

    payload = body or {}
    by = payload.get("by_agent", "Operator")
    reason = payload.get("reason", "")
    page = wiki_store.deprecate_page(slug, by_agent=by, reason=reason)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@app.post("/api/wiki/pages/{slug:path}/pin")
def wiki_pin(slug: str, body: dict | None = Body(default=None)):
    from app.spine import wiki as wiki_store

    pinned = bool((body or {}).get("pinned", True))
    page = wiki_store.set_pinned(slug, pinned=pinned)
    if page is None:
        raise HTTPException(404, "wiki page not found")
    return {"page": page.to_dict()}


@app.get("/api/wiki/pinned")
def wiki_pinned():
    from app.spine import wiki as wiki_store

    return {"pages": [p.to_dict() for p in wiki_store.list_pinned()]}


@app.get("/api/mlflow/status")
def mlflow_status(limit_runs: int = 10):
    """Cockpit-side surface over the MLflow REST API (Track 4 follow-up).

    Returns:
      * `enabled`           — whether `MLFLOW_TRACKING_URI` is set.
      * `ui_url`            — same value, used by the iframe in the
                              cockpit's MLflow tab.
      * `reachable`         — whether the tracking server answered.
      * `experiments`       — recent experiments (id, name, last update).
      * `recent_runs`       — most recent runs across all experiments
                              (status, total_score / latency_ms when
                              the run carries those metrics).
      * `error`             — exception string when reachable is False.

    The cockpit polls this every 5s. We avoid pulling the `mlflow` SDK
    here intentionally — the optional extra is a 200MB+ install and
    most operators won't have it on the backend host. Plain
    `urllib.request` against MLflow's public `/ajax-api/2.0` JSON
    endpoints is enough.
    """
    import os
    import json
    from urllib.error import HTTPError, URLError
    from urllib.request import Request, urlopen

    uri = os.getenv("MLFLOW_TRACKING_URI", "").strip()
    if not uri:
        return {
            "enabled": False,
            "ui_url": None,
            "reachable": False,
            "experiments": [],
            "recent_runs": [],
            "error": None,
        }

    base = uri.rstrip("/")

    def _ajax(path: str, payload: dict | None = None, timeout: int = 4) -> dict | None:
        url = f"{base}/ajax-api/2.0/mlflow/{path}"
        body = json.dumps(payload).encode() if payload is not None else None
        req = Request(
            url,
            data=body,
            method="POST" if body else "GET",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except (HTTPError, URLError, OSError, json.JSONDecodeError):
            return None

    # 1. Recent experiments (page size capped to keep payload small).
    exp_data = _ajax(
        "experiments/search",
        {"max_results": 25, "order_by": ["last_update_time DESC"]},
    )
    if exp_data is None:
        # Try GET form (older MLflow versions used GET on search).
        exp_data = _ajax("experiments/list")
    if exp_data is None:
        return {
            "enabled": True,
            "ui_url": base,
            "reachable": False,
            "experiments": [],
            "recent_runs": [],
            "error": "MLflow tracking server unreachable",
        }
    raw_exps = exp_data.get("experiments") or []
    experiments = [
        {
            "id": e.get("experiment_id"),
            "name": e.get("name"),
            "lifecycle_stage": e.get("lifecycle_stage"),
            "last_update_time": e.get("last_update_time"),
        }
        for e in raw_exps
    ]

    # 2. Recent runs across the top-N experiments. MLflow's runs/search
    # accepts a list of experiment_ids; we cap at 10 experiments × the
    # caller's per-experiment limit so the response never balloons.
    exp_ids = [e["id"] for e in experiments[:10] if e.get("id")]
    recent_runs: list[dict] = []
    if exp_ids:
        runs_data = _ajax(
            "runs/search",
            {
                "experiment_ids": exp_ids,
                "max_results": max(1, min(int(limit_runs), 100)),
                "order_by": ["attributes.start_time DESC"],
            },
        )
        if runs_data:
            for r in runs_data.get("runs") or []:
                info = r.get("info") or {}
                metrics = {m["key"]: m["value"] for m in (r.get("data") or {}).get("metrics", [])}
                tags = {t["key"]: t["value"] for t in (r.get("data") or {}).get("tags", [])}
                recent_runs.append(
                    {
                        "run_id": info.get("run_id"),
                        "experiment_id": info.get("experiment_id"),
                        "experiment_name": next(
                            (e["name"] for e in experiments if e["id"] == info.get("experiment_id")),
                            None,
                        ),
                        "run_name": tags.get("mlflow.runName") or info.get("run_name"),
                        "status": info.get("status"),
                        "start_time": info.get("start_time"),
                        "end_time": info.get("end_time"),
                        "phase": tags.get("phase"),
                        "agent": tags.get("agent"),
                        "metrics": metrics,
                    }
                )

    return {
        "enabled": True,
        "ui_url": base,
        "reachable": True,
        "experiments": experiments,
        "recent_runs": recent_runs,
        "error": None,
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
