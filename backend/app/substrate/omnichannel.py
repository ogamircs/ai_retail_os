"""Mock omnichannel retail substrate.

These helpers keep the PoC deliberately simple while making the retail story
feel broader than POS + inventory: categories, stores, segments, campaigns,
orders, inbound POs, and an executive action queue.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

from app.spine.db import conn
from app.spine.events import append_event
from app.spine.artifacts import write_artifact
from app.spine.kg import upsert_edge, upsert_node
from app.integrations import registry as integration_registry


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _cutoff(days: int) -> str:
    return (_now() - timedelta(days=days)).isoformat()


def _rowdict(row) -> dict:
    return dict(row) if row else {}


def _policy_value(key: str, default):
    with conn() as c:
        row = c.execute("SELECT value_json FROM policy_rules WHERE key = ?", (key,)).fetchone()
    if not row:
        return default
    return json.loads(row["value_json"])


def create_action(
    owner: str,
    action_type: str,
    title: str,
    status: str,
    payload: dict,
    artifact_id: str | None = None,
) -> dict:
    ts = _now().isoformat()
    with conn() as c:
        cur = c.execute(
            "INSERT INTO action_queue "
            "(ts, owner, action_type, title, status, payload_json, artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (ts, owner, action_type, title, status, json.dumps(payload), artifact_id),
        )
        action_id = cur.lastrowid
    return {
        "id": action_id,
        "ts": ts,
        "owner": owner,
        "action_type": action_type,
        "title": title,
        "status": status,
        "payload": payload,
        "artifact_id": artifact_id,
    }


def _propose_external_actions(action: dict, systems: list[str], payload: dict) -> list[dict]:
    proposals = []
    for system_id in systems:
        proposal = integration_registry.propose_outbound(
            system_id=system_id,
            action_queue_id=action["id"],
            agent=action["owner"],
            action_type=action["action_type"],
            title=action["title"],
            payload=payload,
        )
        if "error" not in proposal:
            proposals.append(proposal)
    return proposals


def list_action_queue(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT id, ts, owner, action_type, title, status, payload_json, artifact_id "
            "FROM action_queue ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    actions = [
        {
            "id": r["id"],
            "ts": r["ts"],
            "owner": r["owner"],
            "action_type": r["action_type"],
            "title": r["title"],
            "status": r["status"],
            "payload": json.loads(r["payload_json"]),
            "artifact_id": r["artifact_id"],
        }
        for r in rows
    ]
    outbox_by_action: dict[int, list[dict]] = {}
    for item in integration_registry.outbox_actions(limit=300):
        action_id = item.get("action_queue_id")
        if action_id is not None:
            outbox_by_action.setdefault(action_id, []).append(item)
    for action in actions:
        action["external_actions"] = outbox_by_action.get(action["id"], [])
    return actions


def list_campaigns(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT c.*, s.name AS segment_name "
            "FROM substrate_campaigns c "
            "LEFT JOIN substrate_customer_segments s ON s.segment_id = c.segment_id "
            "ORDER BY c.starts_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_rowdict(r) for r in rows]


def list_orders(limit: int = 50, category: str | None = None) -> list[dict]:
    with conn() as c:
        if category:
            rows = c.execute(
                "SELECT o.*, sku.name AS sku_name, st.name AS store_name, seg.name AS segment_name "
                "FROM substrate_orders o "
                "LEFT JOIN substrate_skus sku ON sku.sku = o.sku "
                "LEFT JOIN substrate_stores st ON st.store_id = o.store_id "
                "LEFT JOIN substrate_customer_segments seg ON seg.segment_id = o.segment_id "
                "WHERE o.category = ? ORDER BY o.ts DESC LIMIT ?",
                (category, limit),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT o.*, sku.name AS sku_name, st.name AS store_name, seg.name AS segment_name "
                "FROM substrate_orders o "
                "LEFT JOIN substrate_skus sku ON sku.sku = o.sku "
                "LEFT JOIN substrate_stores st ON st.store_id = o.store_id "
                "LEFT JOIN substrate_customer_segments seg ON seg.segment_id = o.segment_id "
                "ORDER BY o.ts DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_rowdict(r) for r in rows]


def list_stores() -> list[dict]:
    with conn() as c:
        rows = c.execute(
            "SELECT st.*, "
            "COALESCE(SUM(si.on_hand), 0) AS on_hand, "
            "COALESCE(SUM(si.capacity), 0) AS inventory_capacity, "
            "COALESCE(o.units, 0) AS order_units, "
            "COALESCE(o.revenue, 0) AS revenue, "
            "COALESCE(o.margin, 0) AS margin "
            "FROM substrate_stores st "
            "LEFT JOIN substrate_store_inventory si ON si.store_id = st.store_id "
            "LEFT JOIN ("
            "  SELECT store_id, SUM(units) AS units, SUM(revenue) AS revenue, SUM(margin) AS margin "
            "  FROM substrate_orders WHERE ts >= ? GROUP BY store_id"
            ") o ON o.store_id = st.store_id "
            "GROUP BY st.store_id ORDER BY st.region, st.name",
            (_cutoff(30),),
        ).fetchall()
    stores = []
    for row in rows:
        item = _rowdict(row)
        item["capacity_used"] = round(item["on_hand"] / max(item["inventory_capacity"], 1), 2)
        item["margin_rate"] = round(item["margin"] / max(item["revenue"], 1), 2)
        stores.append(item)
    return stores


def _category_base_rows(days: int = 30) -> list[dict]:
    cutoff = _cutoff(days)
    with conn() as c:
        rows = c.execute(
            "SELECT cat.*, "
            "COUNT(DISTINCT sku.sku) AS sku_count, "
            "COALESCE(SUM(inv.on_hand), 0) AS on_hand, "
            "COALESCE(SUM(inv.on_hand * inv.price), 0) AS inventory_value, "
            "COALESCE(sales.units, 0) AS sales_units, "
            "COALESCE(sales.revenue, 0) AS sales_revenue, "
            "COALESCE(ord.units, 0) AS order_units, "
            "COALESCE(ord.revenue, 0) AS order_revenue, "
            "COALESCE(ord.margin, 0) AS order_margin, "
            "COALESCE(camp.active_campaigns, 0) AS active_campaigns "
            "FROM substrate_categories cat "
            "LEFT JOIN substrate_skus sku ON sku.category = cat.category "
            "LEFT JOIN substrate_inventory inv ON inv.sku = sku.sku "
            "LEFT JOIN ("
            "  SELECT s.category, SUM(x.units) AS units, SUM(x.revenue) AS revenue "
            "  FROM substrate_sales x JOIN substrate_skus s ON s.sku = x.sku "
            "  WHERE x.ts >= ? GROUP BY s.category"
            ") sales ON sales.category = cat.category "
            "LEFT JOIN ("
            "  SELECT category, SUM(units) AS units, SUM(revenue) AS revenue, SUM(margin) AS margin "
            "  FROM substrate_orders WHERE ts >= ? GROUP BY category"
            ") ord ON ord.category = cat.category "
            "LEFT JOIN ("
            "  SELECT category, COUNT(*) AS active_campaigns "
            "  FROM substrate_campaigns WHERE status IN ('planned', 'launched') GROUP BY category"
            ") camp ON camp.category = cat.category "
            "GROUP BY cat.category "
            "ORDER BY cat.marketing_priority DESC, inventory_value DESC",
            (cutoff, cutoff),
        ).fetchall()
    return [_rowdict(r) for r in rows]


def list_categories(days: int = 30) -> list[dict]:
    categories = []
    for row in _category_base_rows(days):
        velocity = row["sales_units"] / max(days, 1)
        days_cover = round(row["on_hand"] / max(velocity, 0.25), 1)
        margin_rate = row["order_margin"] / max(row["order_revenue"], 1)
        inventory_pressure = min(1.0, days_cover / 90)
        opportunity_score = (
            row["marketing_priority"] * 10
            + inventory_pressure * 35
            + row["weather_sensitivity"] * 20
            + margin_rate * 20
        )
        item = {
            **row,
            "days_cover": days_cover,
            "margin_rate": round(margin_rate, 2),
            "inventory_pressure": round(inventory_pressure, 2),
            "opportunity_score": round(opportunity_score, 1),
            "recommended_push": opportunity_score >= 70,
        }
        categories.append(item)
    return sorted(categories, key=lambda c: c["opportunity_score"], reverse=True)


def category_detail(category: str) -> dict:
    cats = [c for c in list_categories() if c["category"] == category]
    if not cats:
        return {"error": f"unknown category: {category}"}
    with conn() as c:
        segments = c.execute(
            "SELECT a.*, s.name, s.channel_preference, s.loyalty_tier "
            "FROM substrate_segment_affinity a "
            "JOIN substrate_customer_segments s ON s.segment_id = a.segment_id "
            "WHERE a.category = ? ORDER BY a.affinity * a.expected_lift DESC",
            (category,),
        ).fetchall()
        inbound = c.execute(
            "SELECT p.*, sku.name AS sku_name FROM substrate_inbound_pos p "
            "JOIN substrate_skus sku ON sku.sku = p.sku "
            "WHERE sku.category = ? ORDER BY p.eta",
            (category,),
        ).fetchall()
    return {
        "category": cats[0],
        "segments": [_rowdict(r) for r in segments],
        "inbound_pos": [_rowdict(r) for r in inbound],
    }


def recommend_category_push(category: str | None = None) -> dict:
    categories = list_categories()
    if category:
        matches = [c for c in categories if c["category"] == category]
        if not matches:
            return {"error": f"unknown category: {category}"}
        chosen = matches[0]
    else:
        chosen = categories[0] if categories else {}
    detail = category_detail(chosen["category"])
    segment = detail["segments"][0] if detail.get("segments") else {}
    max_budget = float(_policy_value("max_campaign_budget", 65000))
    budget = min(max_budget, 18000 + chosen["opportunity_score"] * 420)
    projected_lift = round(segment.get("expected_lift", 0.12) + chosen["weather_sensitivity"] * 0.08, 2)
    projected_roi = round(1.3 + chosen["margin_rate"] + projected_lift * 2.2, 2)
    return {
        "category": chosen["category"],
        "display_name": chosen["display_name"],
        "segment_id": segment.get("segment_id", "seg-loyalists"),
        "segment_name": segment.get("name", "Loyal omnichannel shoppers"),
        "channel": segment.get("channel_preference", "email + paid social"),
        "offer": segment.get("preferred_offer", "category feature + targeted offer"),
        "budget": round(budget, 2),
        "projected_lift": projected_lift,
        "projected_roi": projected_roi,
        "confidence": 0.82,
        "rationale": [
            f"{chosen['display_name']} has {chosen['days_cover']} days of cover.",
            f"Inventory value is ${chosen['inventory_value']:,.0f}.",
            f"{segment.get('name', 'The top segment')} has the strongest affinity for this category.",
            "The current weather and lifecycle signals support a short campaign window.",
        ],
    }


def create_campaign_brief(category: str | None = None) -> dict:
    rec = recommend_category_push(category)
    if "error" in rec:
        return rec
    title = f"{rec['display_name']} Category Push"
    body = "\n".join(
        [
            f"# {title}",
            "",
            f"**Category:** {rec['display_name']}",
            f"**Audience:** {rec['segment_name']}",
            f"**Channel:** {rec['channel']}",
            f"**Offer:** {rec['offer']}",
            f"**Budget:** ${rec['budget']:,.0f}",
            f"**Projected lift:** {round(rec['projected_lift'] * 100)}%",
            f"**Projected ROI:** {rec['projected_roi']}x",
            "",
            "## Rationale",
            *[f"- {item}" for item in rec["rationale"]],
        ]
    )
    artifact_id = write_artifact(
        agent="Marketing",
        kind="campaign_brief",
        title=title,
        body_md=body,
        refs=[rec["category"], rec["segment_id"]],
    )
    action = create_action(
        owner="Marketing",
        action_type="campaign_brief",
        title=title,
        status="proposed",
        payload=rec,
        artifact_id=artifact_id,
    )
    external_actions = _propose_external_actions(action, ["mautic"], rec)
    append_event(
        agent="Marketing",
        kind="proposal",
        payload={"action": "campaign_brief", "title": title, **rec},
        artifact_id=artifact_id,
    )
    return {"title": title, "artifact_id": artifact_id, "external_actions": external_actions, **rec}


def request_approval(owner: str, title: str, reason: str, payload: dict | None = None) -> dict:
    body = {"action": "request_approval", "title": title, "reason": reason, **(payload or {})}
    event_id = append_event(agent=owner, kind="approval_required", payload=body)
    action = create_action(owner, "approval", title, "approval_required", body)
    return {"event_id": event_id, "action_id": action["id"], **body}


def create_promotion(category: str, offer: str, discount_percent: float, reason: str = "") -> dict:
    max_discount = float(_policy_value("max_markdown_percent", 40))
    if discount_percent > max_discount:
        return request_approval(
            owner="Pricing & Promo",
            title=f"Approve {discount_percent:.0f}% {category} promotion",
            reason="discount exceeds policy",
            payload={"category": category, "offer": offer, "discount_percent": discount_percent},
        )
    payload = {
        "action": "promotion",
        "category": category,
        "offer": offer,
        "discount_percent": discount_percent,
        "reason": reason,
    }
    event_id = append_event(agent="Pricing & Promo", kind="action", payload=payload)
    action = create_action("Pricing & Promo", "promotion", f"{category.replace('_', ' ').title()} promotion", "proposed", payload)
    external_actions = _propose_external_actions(action, ["erpnext"], payload)
    return {"event_id": event_id, "action_id": action["id"], "external_actions": external_actions, **payload}


def launch_mock_campaign(
    category: str,
    segment_id: str,
    channel: str,
    budget: float,
    offer: str,
    projected_lift: float,
    projected_roi: float,
    title: str | None = None,
) -> dict:
    max_budget = float(_policy_value("max_campaign_budget", 65000))
    if budget > max_budget:
        approval = request_approval(
            owner="Marketing",
            title=f"Approve {category.replace('_', ' ').title()} campaign budget",
            reason="budget exceeds policy",
            payload={
                "category": category,
                "segment_id": segment_id,
                "channel": channel,
                "budget": budget,
                "max_budget": max_budget,
                "offer": offer,
            },
        )
        return {"blocked": True, "max_budget": max_budget, **approval}

    campaign_id = f"cmp-{uuid.uuid4().hex[:8]}"
    starts_at = _now().isoformat()
    ends_at = (_now() + timedelta(days=10)).isoformat()
    campaign_title = title or f"{category.replace('_', ' ').title()} Push"
    with conn() as c:
        c.execute(
            "INSERT INTO substrate_campaigns "
            "(campaign_id, title, category, segment_id, channel, budget, offer, projected_lift, "
            "actual_lift, projected_roi, actual_roi, status, starts_at, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                campaign_id,
                campaign_title,
                category,
                segment_id,
                channel,
                budget,
                offer,
                projected_lift,
                None,
                projected_roi,
                None,
                "launched",
                starts_at,
                ends_at,
            ),
        )
    payload = {
        "action": "campaign_launch",
        "campaign_id": campaign_id,
        "title": campaign_title,
        "category": category,
        "segment_id": segment_id,
        "channel": channel,
        "budget": budget,
        "offer": offer,
        "projected_lift": projected_lift,
        "projected_roi": projected_roi,
    }
    event_id = append_event(agent="Marketing", kind="campaign_launch", payload=payload)
    action = create_action("Marketing", "campaign_launch", campaign_title, "launched", payload)
    external_actions = _propose_external_actions(action, ["mautic"], payload)
    upsert_node(campaign_id, "campaign", payload)
    upsert_edge(category, "promoted_by", campaign_id)
    upsert_edge(campaign_id, "targets", segment_id)
    return {"campaign_id": campaign_id, "event_id": event_id, "action_id": action["id"], "external_actions": external_actions, **payload}


def measure_campaign(campaign_id: str | None = None) -> dict:
    with conn() as c:
        if campaign_id:
            row = c.execute(
                "SELECT * FROM substrate_campaigns WHERE campaign_id = ?", (campaign_id,)
            ).fetchone()
        else:
            row = c.execute(
                "SELECT * FROM substrate_campaigns "
                "WHERE status IN ('launched', 'planned') ORDER BY starts_at DESC LIMIT 1"
            ).fetchone()
    if not row:
        return {"error": "no campaign available to measure"}
    campaign = _rowdict(row)
    detail = category_detail(campaign["category"])
    weather = detail.get("category", {}).get("weather_sensitivity", 0.2)
    actual_lift = round(min(0.55, campaign["projected_lift"] * 0.88 + weather * 0.07), 2)
    actual_roi = round(max(0.4, campaign["projected_roi"] * 0.92 + actual_lift), 2)
    with conn() as c:
        c.execute(
            "UPDATE substrate_campaigns SET actual_lift = ?, actual_roi = ?, status = ? "
            "WHERE campaign_id = ?",
            (actual_lift, actual_roi, "measured", campaign["campaign_id"]),
        )
    payload = {
        "action": "campaign_measurement",
        "campaign_id": campaign["campaign_id"],
        "title": campaign["title"],
        "category": campaign["category"],
        "projected_lift": campaign["projected_lift"],
        "actual_lift": actual_lift,
        "projected_roi": campaign["projected_roi"],
        "actual_roi": actual_roi,
        "status": "measured",
    }
    event_id = append_event(agent="Marketing", kind="measurement", payload=payload)
    action = create_action("Marketing", "campaign_measurement", campaign["title"], "measured", payload)
    return {"event_id": event_id, "action_id": action["id"], **payload}


def inventory_health() -> dict:
    categories = list_categories()
    with conn() as c:
        sku_rows = c.execute(
            "SELECT sku.sku, sku.name, sku.category, sku.vendor, inv.on_hand, inv.reorder_point, "
            "inv.price, inv.base_price, COALESCE(sales.units, 0) AS units_30d "
            "FROM substrate_skus sku "
            "JOIN substrate_inventory inv ON inv.sku = sku.sku "
            "LEFT JOIN ("
            "  SELECT sku, SUM(units) AS units FROM substrate_sales WHERE ts >= ? GROUP BY sku"
            ") sales ON sales.sku = sku.sku "
            "ORDER BY inv.on_hand DESC",
            (_cutoff(30),),
        ).fetchall()
        inbound_rows = c.execute(
            "SELECT p.*, sku.category, sku.name AS sku_name FROM substrate_inbound_pos p "
            "JOIN substrate_skus sku ON sku.sku = p.sku ORDER BY p.eta"
        ).fetchall()
    sku_health = []
    for row in sku_rows:
        item = _rowdict(row)
        velocity = item["units_30d"] / 30
        item["days_cover"] = round(item["on_hand"] / max(velocity, 0.1), 1)
        if item["on_hand"] <= item["reorder_point"]:
            item["risk"] = "stockout"
        elif item["days_cover"] > 120:
            item["risk"] = "overstock"
        else:
            item["risk"] = "healthy"
        sku_health.append(item)
    return {
        "categories": categories,
        "skus": sku_health,
        "inbound_pos": [_rowdict(r) for r in inbound_rows],
    }


def hold_or_expedite_po(category: str, mode: str = "hold", reason: str = "") -> dict:
    if mode not in {"hold", "expedite"}:
        return {"error": "mode must be hold or expedite"}
    status = "held" if mode == "hold" else "expedited"
    with conn() as c:
        rows = c.execute(
            "SELECT p.po_id, p.sku, p.vendor, p.qty, p.eta "
            "FROM substrate_inbound_pos p JOIN substrate_skus sku ON sku.sku = p.sku "
            "WHERE sku.category = ? AND p.status IN ('open', 'expedited') "
            "ORDER BY p.eta LIMIT 5",
            (category,),
        ).fetchall()
        po_ids = [r["po_id"] for r in rows]
        if po_ids:
            placeholders = ",".join("?" for _ in po_ids)
            c.execute(
                f"UPDATE substrate_inbound_pos SET status = ? WHERE po_id IN ({placeholders})",
                (status, *po_ids),
            )
    payload = {
        "action": f"po_{status}",
        "category": category,
        "mode": mode,
        "reason": reason,
        "pos": [_rowdict(r) for r in rows],
    }
    event_id = append_event(agent="Replenishment", kind="action", payload=payload)
    action = create_action("Replenishment", f"po_{status}", f"{status.title()} inbound POs", status, payload)
    external_actions = _propose_external_actions(action, ["erpnext", "openboxes"], payload)
    return {"event_id": event_id, "action_id": action["id"], "external_actions": external_actions, **payload}


def allocate_inventory(category: str) -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT st.store_id, st.name, st.region, st.local_demand_signal, st.labor_pressure, "
            "SUM(si.on_hand) AS on_hand, SUM(si.capacity) AS capacity "
            "FROM substrate_store_inventory si "
            "JOIN substrate_skus sku ON sku.sku = si.sku "
            "JOIN substrate_stores st ON st.store_id = si.store_id "
            "WHERE sku.category = ? GROUP BY st.store_id "
            "ORDER BY st.local_demand_signal DESC",
            (category,),
        ).fetchall()
    stores = [_rowdict(r) for r in rows]
    if len(stores) < 2:
        return {"error": f"not enough store data for {category}"}
    demand_store = stores[0]
    supply_store = sorted(stores, key=lambda s: (s["on_hand"], -s["local_demand_signal"]), reverse=True)[0]
    qty = max(12, int((supply_store["on_hand"] - demand_store["on_hand"]) * 0.18))
    payload = {
        "action": "store_transfer",
        "category": category,
        "from_store": supply_store["store_id"],
        "from_store_name": supply_store["name"],
        "to_store": demand_store["store_id"],
        "to_store_name": demand_store["name"],
        "qty": qty,
        "reason": "rebalance inventory toward stronger local demand",
    }
    event_id = append_event(agent="Merchandiser", kind="action", payload=payload)
    action = create_action("Merchandiser", "store_transfer", "Rebalance category inventory", "proposed", payload)
    external_actions = _propose_external_actions(action, ["erpnext", "medusa", "openboxes"], payload)
    return {"event_id": event_id, "action_id": action["id"], "external_actions": external_actions, **payload}


def route_fulfillment(category: str) -> dict:
    with conn() as c:
        rows = c.execute(
            "SELECT fulfillment_method, COUNT(*) AS orders, SUM(units) AS units, "
            "SUM(revenue) AS revenue, SUM(margin) AS margin "
            "FROM substrate_orders WHERE category = ? AND ts >= ? GROUP BY fulfillment_method",
            (category, _cutoff(30)),
        ).fetchall()
    mix = [_rowdict(r) for r in rows]
    payload = {
        "action": "fulfillment_routing",
        "category": category,
        "recommended_strategy": "favor BOPIS in high-demand regions and ship-from-store from overstocked locations",
        "method_mix": mix,
        "guardrail": "avoid stores with labor pressure above 0.78",
    }
    event_id = append_event(agent="Fulfillment", kind="action", payload=payload)
    action = create_action("Fulfillment", "fulfillment_routing", "Route omnichannel demand", "proposed", payload)
    external_actions = _propose_external_actions(action, ["medusa"], payload)
    return {"event_id": event_id, "action_id": action["id"], "external_actions": external_actions, **payload}


def create_store_task(store_id: str, title: str, priority: str = "normal", reason: str = "") -> dict:
    with conn() as c:
        row = c.execute("SELECT name, labor_pressure FROM substrate_stores WHERE store_id = ?", (store_id,)).fetchone()
    if not row:
        return {"error": f"unknown store: {store_id}"}
    payload = {
        "action": "store_task",
        "store_id": store_id,
        "store_name": row["name"],
        "title": title,
        "priority": priority,
        "reason": reason,
        "labor_pressure": row["labor_pressure"],
    }
    event_id = append_event(agent="Store Manager", kind="action", payload=payload)
    action = create_action("Store Manager", "store_task", title, "proposed", payload)
    return {"event_id": event_id, "action_id": action["id"], **payload}


def executive_kpis() -> dict:
    categories = list_categories()
    campaigns = list_campaigns()
    stores = list_stores()
    health = inventory_health()
    order_revenue = sum(c["order_revenue"] for c in categories)
    order_margin = sum(c["order_margin"] for c in categories)
    inventory_value = sum(c["inventory_value"] for c in categories)
    active_campaigns = len([c for c in campaigns if c["status"] in {"planned", "launched"}])
    supplier_risk = len([p for p in health["inbound_pos"] if p["status"] == "open" and p["reliability"] < 0.78])
    overstock = len([c for c in categories if c["inventory_pressure"] > 0.75])
    store_pressure = len([s for s in stores if s["labor_pressure"] > 0.75 or s["capacity_used"] > 0.82])
    return {
        "kpis": [
            {
                "label": "30d omnichannel revenue",
                "value": round(order_revenue, 2),
                "format": "currency",
                "delta": "+8.4% vs plan",
            },
            {
                "label": "Gross margin",
                "value": round(order_margin / max(order_revenue, 1), 2),
                "format": "percent",
                "delta": "policy floor 32%",
            },
            {
                "label": "Inventory at risk",
                "value": round(inventory_value, 2),
                "format": "currency",
                "delta": f"{overstock} categories pressured",
            },
            {
                "label": "Active campaigns",
                "value": active_campaigns,
                "format": "number",
                "delta": "category push queue",
            },
            {
                "label": "Supplier risks",
                "value": supplier_risk,
                "format": "number",
                "delta": "low-reliability inbound POs",
            },
            {
                "label": "Store exceptions",
                "value": store_pressure,
                "format": "number",
                "delta": "capacity or labor",
            },
        ],
        "recommended_category": categories[0] if categories else None,
        "last_campaign": campaigns[0] if campaigns else None,
        "generated_at": _now().isoformat(),
    }
