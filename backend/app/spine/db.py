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
