"""Per-adapter integration modules.

Each open-source retail system gets its own module; this package is the
public façade over them. `ADAPTERS` is the canonical ordered list — every
caller should iterate this rather than reaching into individual modules.
"""

from __future__ import annotations

from app.integrations.adapters.akeneo import AkeneoAdapter
from app.integrations.adapters.erpnext import ERPNextAdapter
from app.integrations.adapters.mautic import MauticAdapter
from app.integrations.adapters.medusa import MedusaAdapter
from app.integrations.adapters.openboxes import OpenBoxesAdapter
from app.integrations.adapters.shopify import ShopifyAdapter
from app.integrations.adapters.superset import SupersetAdapter
from app.integrations.base import IntegrationAdapter

ADAPTERS: list[IntegrationAdapter] = [
    ERPNextAdapter(),
    MauticAdapter(),
    MedusaAdapter(),
    OpenBoxesAdapter(),
    AkeneoAdapter(),
    SupersetAdapter(),
    ShopifyAdapter(),
]

__all__ = [
    "ADAPTERS",
    "AkeneoAdapter",
    "ERPNextAdapter",
    "MauticAdapter",
    "MedusaAdapter",
    "OpenBoxesAdapter",
    "ShopifyAdapter",
    "SupersetAdapter",
]
