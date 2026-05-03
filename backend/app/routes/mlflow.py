"""Cockpit-side surface over the MLflow REST API.

We deliberately don't pull the `mlflow` SDK here — the optional extra is
200MB+ once boto3 lands, and most operators won't have it on the backend
host. Plain `urllib.request` against MLflow's public `/ajax-api/2.0`
JSON endpoints is enough for the cockpit's status chip + run feed.
"""

from __future__ import annotations

import json
import os
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from fastapi import APIRouter

router = APIRouter()


@router.get("/api/mlflow/status")
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

    The cockpit polls this every 5s.
    """
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
