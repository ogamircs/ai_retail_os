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
    edits. Filter is applied at the SQL layer (before LIMIT) so a
    broad query never gets its draft matches crowded out by
    published rows."""
    from app.spine import wiki as wiki_store

    effective_status = status if (status not in (None, "")) else None
    pages = wiki_store.search_pages(q, limit=limit, status=effective_status)
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


@app.post("/api/improvements/run")
def improvements_run():
    """Kick off an audit. Background thread; returns `{run_id}`. The
    cockpit polls `/api/improvements/runs/{run_id}` for terminal
    status and pulls suggestions from `/api/improvements/suggestions`.

    The auditor is read-only — it never proposes outbox actions or
    edits substrate. It walks structured signals and records
    suggestions for operator review.
    """
    import threading
    import uuid

    from app.agents import improvement_auditor as ia

    try:
        llm = get_provider()
    except RuntimeError as e:
        raise HTTPException(500, str(e))

    run_id = uuid.uuid4().hex[:12]
    ia.create_run(run_id)

    def _worker():
        try:
            ia.run_audit(run_id, llm)
        except Exception as exc:  # noqa: BLE001
            ia.complete_run(run_id, {"phase": "worker"}, error=str(exc))

    threading.Thread(
        target=_worker, daemon=True, name=f"improvement-audit-{run_id}"
    ).start()
    return {"run_id": run_id, "status": "running"}


@app.get("/api/improvements/runs/{run_id}")
def improvements_run_status(run_id: str):
    from app.agents import improvement_auditor as ia

    run = ia.get_run(run_id)
    if not run:
        raise HTTPException(404, f"unknown run '{run_id}'")
    return run


@app.get("/api/improvements/runs")
def improvements_runs_list(limit: int = 20):
    from app.agents import improvement_auditor as ia

    return {"runs": ia.list_runs(limit=limit)}


@app.get("/api/improvements/suggestions")
def improvements_suggestions(
    status: str | None = "open",
    run_id: str | None = None,
    limit: int = 100,
):
    from app.agents import improvement_auditor as ia

    # Treat empty / 'all' as "no filter" so the cockpit can switch
    # between open / accepted / dismissed via a single dropdown.
    eff_status: str | None = status
    if not status or status == "all":
        eff_status = None
    return {
        "suggestions": ia.list_suggestions(
            run_id=run_id, status=eff_status, limit=limit
        )
    }


@app.post("/api/improvements/suggestions/{suggestion_id}/accept")
def improvements_accept(suggestion_id: int):
    from app.agents import improvement_auditor as ia

    out = ia.update_suggestion_status(suggestion_id, "accepted")
    if not out:
        raise HTTPException(404, f"unknown suggestion {suggestion_id}")
    return out


@app.post("/api/improvements/suggestions/{suggestion_id}/dismiss")
def improvements_dismiss(suggestion_id: int):
    from app.agents import improvement_auditor as ia

    out = ia.update_suggestion_status(suggestion_id, "dismissed")
    if not out:
        raise HTTPException(404, f"unknown suggestion {suggestion_id}")
    return out


@app.get("/api/improvements/signals")
def improvements_signals():
    """Debug endpoint — exposes the deterministic signals snapshot the
    audit agent sees, without invoking the LLM. Useful when tweaking
    `gather_signals` to verify it surfaces what you expect."""
    from app.agents import improvement_auditor as ia

    return ia.gather_signals()


@app.get("/api/brain/status")
def brain_status():
    """Track 6 G5 — cockpit-side surface for the GBrain integration.

    Returns:
      * `configured`     — whether `GBRAIN_BEARER` is set.
      * `reachable`      — whether the gbrain HTTP endpoint answered
                           a `search('')` probe (None when not
                           configured — mock mode is always 'reachable'
                           but flagged via `mock=true`).
      * `mock`           — true when the cockpit is using the
                           substrate-backed mock client.
      * `pages_count`    — count of recent pages surfaced by an empty
                           search (cheap probe for the status strip
                           chip).

    The cockpit's status strip polls this every ~5s; the BrainTab
    polls more aggressively while the operator types in the search
    box.
    """
    from app.llm.mcp import get_client

    client = get_client()
    if not client.config.configured:
        # Mock mode — surface what the wiki-backed client returns so
        # the cockpit chip shows a non-zero count (and the operator
        # gets a glimpse of what the brain WOULD look like with a
        # real GBrain instance).
        try:
            probe = client.search("", limit=20)
            results = probe.get("results") or []
        except Exception:
            results = []
        return {
            "configured": False,
            "reachable": True,
            "mock": True,
            "pages_count": len(results),
            "endpoint": None,
        }
    try:
        probe = client.search("", limit=20)
    except Exception as e:
        return {
            "configured": True,
            "reachable": False,
            "mock": False,
            "pages_count": 0,
            "endpoint": client.config.base_url,
            "error": str(e),
        }
    results = probe.get("results") or []
    return {
        "configured": True,
        "reachable": "error" not in probe,
        "mock": False,
        "pages_count": len(results),
        "endpoint": client.config.base_url,
        "error": probe.get("error"),
    }


@app.get("/api/brain/search")
def brain_search(q: str = "", limit: int = 20):
    from app.llm.mcp import get_client

    return get_client().search(q, limit=limit)


@app.get("/api/brain/pages/{slug:path}")
def brain_page(slug: str):
    from app.llm.mcp import get_client

    page = get_client().get(slug)
    if isinstance(page, dict) and page.get("error"):
        raise HTTPException(404, page["error"])
    return page


@app.get("/api/brain/recent")
def brain_recent(limit: int = 20):
    """Empty-query search returns the most recently updated pages —
    the BrainTab's default landing list when the operator hasn't
    typed anything yet."""
    from app.llm.mcp import get_client

    return get_client().search("", limit=limit)


@app.get("/api/dspy/agents")
def dspy_list_agents():
    """List the agents the DSPy optimizer can compile + their current
    `prod` / `staging` aliases. The cockpit's REPORTS tab uses this to
    paint the prompt-version chip and the `compile prompt` action."""
    from app.llm.prompts import _read_aliases  # internal helper, fine here

    # Keep the registry in lockstep with scripts/dspy_optimize.py —
    # only agents wired up there can actually be compiled.
    registered_slugs = ("analyst", "pricing_promo")
    out: list[dict] = []
    for slug in registered_slugs:
        aliases = _read_aliases(slug)
        out.append(
            {
                "slug": slug,
                "prod": aliases.get("prod"),
                "staging": aliases.get("staging"),
            }
        )
    return {"agents": out}


# In-process state for the optimizer route. Each compile is fire-and-
# forget from the operator's perspective — the cockpit polls
# `/api/dspy/jobs/{job_id}` for status. Restarting the backend clears
# the dict; jobs that survive the restart are visible in MLflow.
_DSPY_JOBS: dict[str, dict] = {}
_DSPY_JOBS_LOCK = __import__("threading").Lock()


def _run_dspy_compile_job(job_id: str, agent_slug: str, auto_promote: bool) -> None:
    """Background worker. Runs the optimizer in a thread so the
    HTTP response returns immediately. Errors are captured into the
    job dict — never raised — so the cockpit can render them in the
    job-status drawer."""
    import sys as _sys
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parent.parent.parent
    scripts_dir = repo_root / "scripts"
    if str(scripts_dir) not in _sys.path:
        _sys.path.insert(0, str(scripts_dir))
    try:
        import dspy_optimize  # type: ignore

        summary = dspy_optimize.compile_agent(
            agent_slug=agent_slug,
            auto_promote=auto_promote,
        )
        with _DSPY_JOBS_LOCK:
            _DSPY_JOBS[job_id].update(
                {"status": "ok", "summary": summary, "ended_at": _now_iso()}
            )
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        with _DSPY_JOBS_LOCK:
            _DSPY_JOBS[job_id].update(
                {"status": "error", "error": str(exc), "ended_at": _now_iso()}
            )


def _now_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat()


@app.post("/api/dspy/optimize/{agent_slug}")
def dspy_optimize_route(agent_slug: str, auto_promote: bool = False):
    """Kick off a DSPy compile for `agent_slug` in a background thread.

    Returns `{job_id}` immediately. Operator polls
    `/api/dspy/jobs/{job_id}` for status. The compile is heavy (one
    LLM call per bootstrap demo, gated 4-8 demos by default) so we
    don't block the request.
    """
    import threading
    import uuid

    valid_slugs = {"analyst", "pricing_promo"}
    if agent_slug not in valid_slugs:
        raise HTTPException(404, f"unknown agent slug '{agent_slug}'")
    job_id = uuid.uuid4().hex[:12]
    with _DSPY_JOBS_LOCK:
        _DSPY_JOBS[job_id] = {
            "id": job_id,
            "agent": agent_slug,
            "auto_promote": bool(auto_promote),
            "status": "running",
            "started_at": _now_iso(),
        }
    threading.Thread(
        target=_run_dspy_compile_job,
        args=(job_id, agent_slug, bool(auto_promote)),
        daemon=True,
        name=f"dspy-compile-{agent_slug}-{job_id}",
    ).start()
    return {"job_id": job_id, "status": "running"}


@app.get("/api/dspy/jobs/{job_id}")
def dspy_job_status(job_id: str):
    with _DSPY_JOBS_LOCK:
        job = _DSPY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job '{job_id}'")
    return job


@app.get("/api/dspy/jobs")
def dspy_jobs_list(limit: int = 20):
    """Recent compile jobs, newest first. Cockpit's REPORTS tab pulls
    this so the operator can see what's compiled vs what's running."""
    with _DSPY_JOBS_LOCK:
        jobs = list(_DSPY_JOBS.values())
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return {"jobs": jobs[: int(limit)]}


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
