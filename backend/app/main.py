"""FastAPI app entry — wiring only.

Each domain surface lives in `app.routes.<module>`. This file:
  1. Builds the FastAPI app + CORS middleware.
  2. Runs the startup hook (init_db + integration registry refresh).
  3. Mounts every router.

Anything heavier than that should land in a route module, not here.
"""

from __future__ import annotations

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.integrations import registry as integration_registry
from app.routes import (
    brain,
    chat,
    core,
    dspy,
    improvements,
    integrations,
    mesh,
    mlflow,
    wiki,
)
from app.spine.db import init_db


def _cors_origins() -> list[str]:
    """Comma-split `CORS_ORIGINS` env, else `["*"]` for the demo path.

    Set `CORS_ORIGINS=https://retail.example.com,https://cockpit.example.com`
    in any deployment that exposes the API beyond a developer laptop. The
    `*` default keeps the local + Tauri shell paths trivial — both
    talk to `http://127.0.0.1:8000` from arbitrary origins.
    """
    raw = os.getenv("CORS_ORIGINS", "").strip()
    if not raw:
        return ["*"]
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


app = FastAPI(title="AI Retail OS")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def _startup():
    init_db()
    integration_registry.refresh_systems()


for module in (
    core,
    integrations,
    mesh,
    wiki,
    mlflow,
    improvements,
    brain,
    dspy,
    chat,
):
    app.include_router(module.router)
