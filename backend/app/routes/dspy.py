"""DSPy compile surface — list compileable agents, kick off a compile in
a background thread, poll job status. Job state lives in SQLite via
`app.spine.jobs`, so terminal status survives backend restarts and the
cockpit's REPORTS tab can render historical compiles after a redeploy.
"""

from __future__ import annotations

import threading
import uuid

from fastapi import APIRouter, HTTPException

from app.spine import jobs as job_store

router = APIRouter()

_JOB_KIND = "dspy_compile"
_VALID_AGENT_SLUGS = ("analyst", "pricing_promo")


def _run_dspy_compile_job(job_id: str, agent_slug: str, auto_promote: bool) -> None:
    """Background worker. Runs the optimizer in a thread so the HTTP
    response returns immediately. Errors land on the job row — never
    raised — so the cockpit can render them in the job-status drawer.
    """
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
        job_store.complete_job(job_id, summary=summary)
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        job_store.complete_job(job_id, error=str(exc))


@router.get("/api/dspy/agents")
def dspy_list_agents():
    """List the agents the DSPy optimizer can compile + their current
    `prod` / `staging` aliases. The cockpit's REPORTS tab uses this to
    paint the prompt-version chip and the `compile prompt` action."""
    from app.llm.prompts import _read_aliases  # internal helper, fine here

    out: list[dict] = []
    for slug in _VALID_AGENT_SLUGS:
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
    if agent_slug not in _VALID_AGENT_SLUGS:
        raise HTTPException(404, f"unknown agent slug '{agent_slug}'")
    job_id = uuid.uuid4().hex[:12]
    job_store.create_job(
        job_id=job_id,
        kind=_JOB_KIND,
        title=f"DSPy compile · {agent_slug}",
        metadata={"agent": agent_slug, "auto_promote": bool(auto_promote)},
    )
    threading.Thread(
        target=_run_dspy_compile_job,
        args=(job_id, agent_slug, bool(auto_promote)),
        daemon=True,
        name=f"dspy-compile-{agent_slug}-{job_id}",
    ).start()
    return {"job_id": job_id, "status": "running"}


@router.get("/api/dspy/jobs/{job_id}")
def dspy_job_status(job_id: str):
    job = job_store.get_job(job_id)
    if job is None or job["kind"] != _JOB_KIND:
        raise HTTPException(404, f"unknown job '{job_id}'")
    return _legacy_shape(job)


@router.get("/api/dspy/jobs")
def dspy_jobs_list(limit: int = 20):
    """Recent compile jobs, newest first. Cockpit's REPORTS tab pulls
    this so the operator can see what's compiled vs what's running."""
    return {"jobs": [_legacy_shape(j) for j in job_store.list_jobs(kind=_JOB_KIND, limit=int(limit))]}


def _legacy_shape(job: dict) -> dict:
    """Preserve the JSON shape the cockpit's REPORTS tab already
    consumes — `id`, `agent`, `auto_promote`, `status`, `started_at`,
    `summary`, `error` — so this refactor stays a server-side change.
    """
    meta = job.get("metadata") or {}
    out: dict = {
        "id": job["id"],
        "agent": meta.get("agent"),
        "auto_promote": meta.get("auto_promote", False),
        "status": job["status"],
        "started_at": job["started_at"],
    }
    if job.get("ended_at"):
        out["ended_at"] = job["ended_at"]
    if job.get("summary"):
        out["summary"] = job["summary"]
    if job.get("error"):
        out["error"] = job["error"]
    return out
