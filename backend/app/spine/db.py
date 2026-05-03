import sqlite3
from contextlib import contextmanager

from app.config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    agent TEXT NOT NULL,
    kind TEXT NOT NULL,
    sku TEXT,
    payload_json TEXT NOT NULL,
    artifact_id TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts);
CREATE INDEX IF NOT EXISTS idx_events_sku ON events(sku);

CREATE TABLE IF NOT EXISTS kg_nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    props_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kg_edges (
    src TEXT NOT NULL,
    rel TEXT NOT NULL,
    dst TEXT NOT NULL,
    props_json TEXT,
    PRIMARY KEY (src, rel, dst)
);

CREATE TABLE IF NOT EXISTS substrate_skus (
    sku TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    vendor TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS substrate_inventory (
    sku TEXT PRIMARY KEY,
    on_hand INTEGER NOT NULL,
    reorder_point INTEGER NOT NULL,
    price REAL NOT NULL,
    base_price REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS substrate_sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sku TEXT NOT NULL,
    ts TEXT NOT NULL,
    units INTEGER NOT NULL,
    revenue REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sales_sku_ts ON substrate_sales(sku, ts);

CREATE TABLE IF NOT EXISTS substrate_categories (
    category TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    lifecycle_stage TEXT NOT NULL,
    margin_target REAL NOT NULL,
    marketing_priority INTEGER NOT NULL,
    weather_sensitivity REAL NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS substrate_stores (
    store_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    region TEXT NOT NULL,
    capacity INTEGER NOT NULL,
    labor_pressure REAL NOT NULL,
    local_demand_signal REAL NOT NULL,
    weather_signal TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS substrate_store_inventory (
    store_id TEXT NOT NULL,
    sku TEXT NOT NULL,
    on_hand INTEGER NOT NULL,
    capacity INTEGER NOT NULL,
    PRIMARY KEY (store_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_store_inventory_sku ON substrate_store_inventory(sku);

CREATE TABLE IF NOT EXISTS substrate_customer_segments (
    segment_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    loyalty_tier TEXT NOT NULL,
    channel_preference TEXT NOT NULL,
    avg_order_value REAL NOT NULL,
    notes TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS substrate_segment_affinity (
    segment_id TEXT NOT NULL,
    category TEXT NOT NULL,
    affinity REAL NOT NULL,
    expected_lift REAL NOT NULL,
    preferred_offer TEXT NOT NULL,
    PRIMARY KEY (segment_id, category)
);

CREATE TABLE IF NOT EXISTS substrate_orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    sku TEXT NOT NULL,
    category TEXT NOT NULL,
    channel TEXT NOT NULL,
    fulfillment_method TEXT NOT NULL,
    store_id TEXT,
    segment_id TEXT NOT NULL,
    units INTEGER NOT NULL,
    revenue REAL NOT NULL,
    margin REAL NOT NULL,
    return_risk REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_orders_category_ts ON substrate_orders(category, ts);
CREATE INDEX IF NOT EXISTS idx_orders_store_ts ON substrate_orders(store_id, ts);

CREATE TABLE IF NOT EXISTS substrate_inbound_pos (
    po_id TEXT PRIMARY KEY,
    sku TEXT NOT NULL,
    vendor TEXT NOT NULL,
    eta TEXT NOT NULL,
    qty INTEGER NOT NULL,
    status TEXT NOT NULL,
    reliability REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS substrate_campaigns (
    campaign_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    segment_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    budget REAL NOT NULL,
    offer TEXT NOT NULL,
    projected_lift REAL NOT NULL,
    actual_lift REAL,
    projected_roi REAL NOT NULL,
    actual_roi REAL,
    status TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    ends_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    owner TEXT NOT NULL,
    action_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    artifact_id TEXT
);

CREATE TABLE IF NOT EXISTS policy_rules (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS integration_systems (
    system_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    domain TEXT NOT NULL,
    enabled INTEGER NOT NULL,
    configured INTEGER NOT NULL,
    mode TEXT NOT NULL,
    last_status TEXT NOT NULL,
    last_sync_ts TEXT,
    last_error TEXT,
    docs_url TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    system_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    records_read INTEGER NOT NULL DEFAULT 0,
    records_written INTEGER NOT NULL DEFAULT 0,
    error TEXT,
    summary_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sync_runs_system ON sync_runs(system_id, id);

CREATE TABLE IF NOT EXISTS external_refs (
    system_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    local_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    external_url TEXT,
    synced_at TEXT NOT NULL,
    props_json TEXT NOT NULL,
    PRIMARY KEY (system_id, domain, local_id, external_id)
);
CREATE INDEX IF NOT EXISTS idx_external_refs_local ON external_refs(local_id);

CREATE TABLE IF NOT EXISTS record_cache (
    system_id TEXT NOT NULL,
    domain TEXT NOT NULL,
    external_id TEXT NOT NULL,
    local_id TEXT,
    payload_json TEXT NOT NULL,
    synced_at TEXT NOT NULL,
    record_hash TEXT NOT NULL,
    PRIMARY KEY (system_id, domain, external_id)
);
CREATE INDEX IF NOT EXISTS idx_record_cache_local ON record_cache(system_id, domain, local_id);

CREATE TABLE IF NOT EXISTS outbox_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    system_id TEXT NOT NULL,
    action_queue_id INTEGER,
    agent TEXT NOT NULL,
    action_type TEXT NOT NULL,
    title TEXT NOT NULL,
    status TEXT NOT NULL,
    external_domain TEXT NOT NULL,
    external_id TEXT,
    payload_json TEXT NOT NULL,
    result_json TEXT NOT NULL,
    requires_approval INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_outbox_actions_queue ON outbox_actions(action_queue_id);
CREATE INDEX IF NOT EXISTS idx_outbox_actions_system ON outbox_actions(system_id, status);

-- Track 5 W1 — agentic wiki. One markdown page per topic. Pages are
-- agent-coined slugs (e.g. category/summer_apparel/markdown_playbook).
-- Each edit appends a row in wiki_revisions; wiki_pages always points
-- at the latest published revision (or the latest draft when nothing
-- has been published yet) so reads are O(1) without joining.
CREATE TABLE IF NOT EXISTS wiki_pages (
    slug TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL,
    owner_agent TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'draft', -- 'draft' | 'published' | 'deprecated'
    version INTEGER NOT NULL DEFAULT 1,
    updated_ts TEXT NOT NULL,
    refs_json TEXT NOT NULL DEFAULT '[]',
    pinned INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_status ON wiki_pages(status, updated_ts DESC);
CREATE INDEX IF NOT EXISTS idx_wiki_pages_owner ON wiki_pages(owner_agent, status);

CREATE TABLE IF NOT EXISTS wiki_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    slug TEXT NOT NULL,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    body_md TEXT NOT NULL,
    author_agent TEXT NOT NULL,
    status TEXT NOT NULL, -- 'draft' | 'published' | 'deprecated' at time of write
    refs_json TEXT NOT NULL DEFAULT '[]',
    ts TEXT NOT NULL,
    UNIQUE(slug, version)
);
CREATE INDEX IF NOT EXISTS idx_wiki_revisions_slug ON wiki_revisions(slug, version DESC);

-- Track 8 — Improvement Auditor.
-- One row per audit run kicked off via /api/improvements/run. The audit
-- agent walks substrate / spine / wiki / brain signals, then emits 0..N
-- improvement_suggestions rows scoped to its run_id. Status flips
-- pending → ok / error when the agent finishes; suggestions stay live
-- until the operator dismisses or accepts them via the cockpit's
-- [IMPROVE] tab.
CREATE TABLE IF NOT EXISTS improvement_runs (
    id TEXT PRIMARY KEY,
    started_ts TEXT NOT NULL,
    ended_ts TEXT,
    status TEXT NOT NULL DEFAULT 'running', -- 'running' | 'ok' | 'error'
    summary_json TEXT NOT NULL DEFAULT '{}',
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_improvement_runs_started ON improvement_runs(started_ts DESC);

CREATE TABLE IF NOT EXISTS improvement_suggestions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL,
    area TEXT NOT NULL,        -- e.g. 'pricing' | 'replenishment' | 'wiki_coverage'
    severity TEXT NOT NULL,    -- 'high' | 'medium' | 'low'
    title TEXT NOT NULL,
    body_md TEXT NOT NULL,
    action_hint TEXT NOT NULL DEFAULT 'operator_review',
    status TEXT NOT NULL DEFAULT 'open', -- 'open' | 'accepted' | 'dismissed'
    refs_json TEXT NOT NULL DEFAULT '[]',
    ts TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_improvement_suggestions_run ON improvement_suggestions(run_id, severity);
CREATE INDEX IF NOT EXISTS idx_improvement_suggestions_open ON improvement_suggestions(status, ts DESC);
"""


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    try:
        yield c
        c.commit()
    finally:
        c.close()


def init_db() -> None:
    with conn() as c:
        c.executescript(SCHEMA)
