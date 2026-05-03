"""DSPy compile surface — list compileable agents, kick off a compile in a
background thread, poll job status. Job state is in-process; restarting
the backend clears the dict (jobs that survived a restart still show up
in MLflow if MLFLOW_TRACKING_URI was set)."""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException

router = APIRouter()


# In-process state for the optimizer route. Each compile is fire-and-
# forget from the operator's perspective — the cockpit polls
# `/api/dspy/jobs/{job_id}` for status. Restarting the backend clears
# the dict; jobs that survive the restart are visible in MLflow.
_DSPY_JOBS: dict[str, dict] = {}
_DSPY_JOBS_LOCK = threading.Lock()


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _run_dspy_compile_job(job_id: str, agent_slug: str, auto_promote: bool) -> None:
    """Background worker. Runs the optimizer in a thread so the
    HTTP response returns immediately. Errors are captured into the
    job dict — never raised — so the cockpit can render them in the
    job-status drawer."""
    import sys as _sys
    from pathlib import Path as _Path

    repo_root = _Path(__file__).resolve().parents[3]
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


@router.get("/api/dspy/agents")
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


@router.post("/api/dspy/optimize/{agent_slug}")
def dspy_optimize_route(agent_slug: str, auto_promote: bool = False):
    """Kick off a DSPy compile for `agent_slug` in a background thread.

    Returns `{job_id}` immediately. Operator polls
    `/api/dspy/jobs/{job_id}` for status. The compile is heavy (one
    LLM call per bootstrap demo, gated 4-8 demos by default) so we
    don't block the request.
    """
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


@router.get("/api/dspy/jobs/{job_id}")
def dspy_job_status(job_id: str):
    with _DSPY_JOBS_LOCK:
        job = _DSPY_JOBS.get(job_id)
    if job is None:
        raise HTTPException(404, f"unknown job '{job_id}'")
    return job


@router.get("/api/dspy/jobs")
def dspy_jobs_list(limit: int = 20):
    """Recent compile jobs, newest first. Cockpit's REPORTS tab pulls
    this so the operator can see what's compiled vs what's running."""
    with _DSPY_JOBS_LOCK:
        jobs = list(_DSPY_JOBS.values())
    jobs.sort(key=lambda j: j.get("started_at", ""), reverse=True)
    return {"jobs": jobs[: int(limit)]}
