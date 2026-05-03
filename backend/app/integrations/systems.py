"""Backwards-compat shim — adapters live under `app.integrations.adapters`.

The original `systems.py` was a 3300-line catch-all owning every adapter,
shared HTTP, and the `ADAPTERS` registry. It split into:

    * `app.integrations.http`               — `JsonHttpClient` + helpers
    * `app.integrations.adapters.<system>`  — one module per adapter
    * `app.integrations.adapters.__init__`  — owns the canonical `ADAPTERS` list

This shim re-exports the same names so existing call sites
(`from app.integrations.systems import ShopifyAdapter`, etc.) keep working.
New code should import from the per-adapter modules directly.
"""

from __future__ import annotations

from app.integrations.adapters import (
    ADAPTERS,
    AkeneoAdapter,
    ERPNextAdapter,
    MauticAdapter,
    MedusaAdapter,
    OpenBoxesAdapter,
    ShopifyAdapter,
    SupersetAdapter,
)
from app.integrations.adapters.shopify import _shopify_gid_tail
from app.integrations.http import (
    JsonHttpClient,
    _coerce_id,
    _display,
    _safe_float,
    _safe_int,
    _slug,
)

__all__ = [
    "ADAPTERS",
    "AkeneoAdapter",
    "ERPNextAdapter",
    "JsonHttpClient",
    "MauticAdapter",
    "MedusaAdapter",
    "OpenBoxesAdapter",
    "ShopifyAdapter",
    "SupersetAdapter",
    "_coerce_id",
    "_display",
    "_safe_float",
    "_safe_int",
    "_shopify_gid_tail",
    "_slug",
]
