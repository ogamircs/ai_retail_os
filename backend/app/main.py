"""FastAPI app entry — wiring only.

Each domain surface lives in `app.routes.<module>`. This file:
  1. Builds the FastAPI app + CORS middleware.
  2. Runs the startup hook (init_db + integration registry refresh).
  3. Mounts every router.

Anything heavier than that should land in a route module, not here.
"""

from __future__ import annotations

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
