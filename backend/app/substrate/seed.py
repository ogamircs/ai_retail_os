"""Populate spine.db with mock SKUs, inventory, sales. Run as `python -m app.substrate.seed`."""

import random
from datetime import datetime, timedelta, timezone
import json
from app.spine.db import conn, init_db
from app.spine.kg import upsert_node, upsert_edge

random.seed(42)

CATEGORY_PROFILES = {
    "summer_apparel": {
        "display_name": "Summer Apparel",
        "lifecycle_stage": "late-season clearance",
        "margin_target": 0.38,
        "marketing_priority": 5,
        "weather_sensitivity": 0.92,
        "notes": "Overstocked, heatwave-sensitive, ideal for a short category push.",
    },
    "home": {
        "display_name": "Home",
        "lifecycle_stage": "evergreen margin builder",
        "margin_target": 0.44,
        "marketing_priority": 3,
        "weather_sensitivity": 0.18,
        "notes": "Stable demand and healthy margin, useful as a basket-builder.",
    },
    "electronics": {
        "display_name": "Electronics",
        "lifecycle_stage": "high demand constrained supply",
        "margin_target": 0.31,
        "marketing_priority": 2,
        "weather_sensitivity": 0.08,
        "notes": "Demand is strong, but low inventory makes broad pushes risky.",
    },
}

STORES = [
    ("sto-nyc", "SoHo Flagship", "Northeast", 1300, 0.82, 0.74, "humid heatwave"),
    ("sto-mia", "Miami Beach", "Southeast", 900, 0.68, 0.94, "extreme heat"),
    ("sto-chi", "Chicago Loop", "Midwest", 1050, 0.58, 0.62, "warm weekend"),
    ("sto-sea", "Seattle Market", "West", 820, 0.49, 0.42, "mild rain"),
    ("sto-dal", "Dallas NorthPark", "South", 1120, 0.76, 0.88, "dry heat"),
]

SEGMENTS = [
    ("seg-loyalists", "Loyal omnichannel shoppers", "gold", "email + app push", 118.0, "Responsive to member-only urgency."),
    ("seg-vacation", "Vacation planners", "silver", "paid social + app push", 94.0, "High summer apparel affinity when weather rises."),
    ("seg-home", "Home refreshers", "bronze", "email + onsite", 86.0, "Good add-on response and low return risk."),
    ("seg-tech", "Tech upgraders", "silver", "search + paid social", 142.0, "Strong electronics demand but promotion-sensitive."),
]

AFFINITY = {
    ("seg-loyalists", "summer_apparel"): (0.84, 0.18, "early-access 25% off selected summer looks"),
    ("seg-loyalists", "home"): (0.62, 0.09, "members get 15% off home accents"),
    ("seg-loyalists", "electronics"): (0.51, 0.06, "bundle accessories with free pickup"),
    ("seg-vacation", "summer_apparel"): (0.93, 0.24, "heatwave weekend: 30% off beach-ready picks"),
    ("seg-vacation", "home"): (0.28, 0.04, "travel-ready home refresh add-ons"),
    ("seg-vacation", "electronics"): (0.44, 0.07, "travel tech accessory bundle"),
    ("seg-home", "summer_apparel"): (0.33, 0.05, "summer hosting outfit add-on"),
    ("seg-home", "home"): (0.88, 0.15, "weekend home refresh event"),
    ("seg-home", "electronics"): (0.31, 0.05, "work-from-home cable essentials"),
    ("seg-tech", "summer_apparel"): (0.24, 0.03, "summer checkout add-on"),
    ("seg-tech", "home"): (0.37, 0.06, "smart desk refresh"),
    ("seg-tech", "electronics"): (0.91, 0.14, "limited accessory bundle"),
}

CATEGORIES = {
    "summer_apparel": [
        ("Linen Sundress", 79.0, "BreezeCo"),
        ("Cotton Shorts", 39.0, "BreezeCo"),
        ("Sun Hat", 29.0, "SunGoods"),
        ("Beach Tote", 49.0, "SunGoods"),
        ("Sandals", 65.0, "BreezeCo"),
        ("Linen Shirt", 69.0, "BreezeCo"),
        ("Swim Trunks", 45.0, "SunGoods"),
        ("Tank Top", 25.0, "BreezeCo"),
        ("Maxi Dress", 95.0, "BreezeCo"),
        ("Flip Flops", 19.0, "SunGoods"),
    ],
    "home": [
        ("Throw Pillow", 35.0, "Hearth"),
        ("Ceramic Mug", 18.0, "Hearth"),
        ("Wool Blanket", 120.0, "Hearth"),
        ("Picture Frame", 24.0, "Hearth"),
        ("Candle", 28.0, "Hearth"),
        ("Bath Towel", 32.0, "Hearth"),
        ("Cutting Board", 55.0, "Hearth"),
        ("Storage Basket", 42.0, "Hearth"),
        ("Wall Clock", 65.0, "Hearth"),
        ("Doormat", 30.0, "Hearth"),
    ],
    "electronics": [
        ("Wireless Earbuds", 129.0, "Volt"),
        ("USB-C Cable", 15.0, "Volt"),
        ("Phone Charger", 25.0, "Volt"),
        ("Bluetooth Speaker", 89.0, "Volt"),
        ("Power Bank", 45.0, "Volt"),
        ("HDMI Cable", 20.0, "Volt"),
        ("Webcam", 75.0, "Volt"),
        ("Mouse Pad", 18.0, "Volt"),
        ("USB Hub", 35.0, "Volt"),
        ("Screen Cleaner", 12.0, "Volt"),
    ],
}


def seed() -> None:
    init_db()
    with conn() as c:
        c.execute("DELETE FROM substrate_skus")
        c.execute("DELETE FROM substrate_inventory")
        c.execute("DELETE FROM substrate_sales")
        c.execute("DELETE FROM substrate_categories")
        c.execute("DELETE FROM substrate_stores")
        c.execute("DELETE FROM substrate_store_inventory")
        c.execute("DELETE FROM substrate_customer_segments")
        c.execute("DELETE FROM substrate_segment_affinity")
        c.execute("DELETE FROM substrate_orders")
        c.execute("DELETE FROM substrate_inbound_pos")
        c.execute("DELETE FROM substrate_campaigns")
        c.execute("DELETE FROM action_queue")
        c.execute("DELETE FROM policy_rules")
        c.execute("DELETE FROM events")
        c.execute("DELETE FROM kg_nodes")
        c.execute("DELETE FROM kg_edges")

        for category, profile in CATEGORY_PROFILES.items():
            c.execute(
                "INSERT INTO substrate_categories "
                "(category, display_name, lifecycle_stage, margin_target, marketing_priority, "
                "weather_sensitivity, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    category,
                    profile["display_name"],
                    profile["lifecycle_stage"],
                    profile["margin_target"],
                    profile["marketing_priority"],
                    profile["weather_sensitivity"],
                    profile["notes"],
                ),
            )

        for store in STORES:
            c.execute(
                "INSERT INTO substrate_stores "
                "(store_id, name, region, capacity, labor_pressure, local_demand_signal, weather_signal) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                store,
            )

        for segment in SEGMENTS:
            c.execute(
                "INSERT INTO substrate_customer_segments "
                "(segment_id, name, loyalty_tier, channel_preference, avg_order_value, notes) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                segment,
            )

        for (segment_id, category), (affinity, expected_lift, offer) in AFFINITY.items():
            c.execute(
                "INSERT INTO substrate_segment_affinity "
                "(segment_id, category, affinity, expected_lift, preferred_offer) "
                "VALUES (?, ?, ?, ?, ?)",
                (segment_id, category, affinity, expected_lift, offer),
            )

        policies = {
            "max_campaign_budget": 65000,
            "min_margin_rate": 0.32,
            "max_markdown_percent": 40,
            "store_labor_pressure_ceiling": 0.78,
            "supplier_reliability_floor": 0.78,
        }
        for key, value in policies.items():
            c.execute(
                "INSERT INTO policy_rules (key, value_json) VALUES (?, ?)",
                (key, json.dumps(value)),
            )

        all_skus = []
        for category, items in CATEGORIES.items():
            for i, (name, price, vendor) in enumerate(items):
                sku = f"{category[:3].upper()}-{i:03d}"
                all_skus.append((sku, name, category, vendor, price))

        for sku, name, category, vendor, price in all_skus:
            c.execute(
                "INSERT INTO substrate_skus (sku, name, category, vendor) VALUES (?, ?, ?, ?)",
                (sku, name, category, vendor),
            )
            # Mix overstocked, healthy, low stock
            r = random.random()
            if r < 0.3:
                on_hand = random.randint(400, 800)  # overstocked
            elif r < 0.85:
                on_hand = random.randint(80, 250)  # healthy
            else:
                on_hand = random.randint(5, 30)  # low
            c.execute(
                "INSERT INTO substrate_inventory (sku, on_hand, reorder_point, price, base_price) "
                "VALUES (?, ?, ?, ?, ?)",
                (sku, on_hand, 50, price, price),
            )
            for store_id, _store_name, _region, capacity, _labor, local_demand, _weather in STORES:
                if category == "summer_apparel":
                    demand_bias = 0.7 + local_demand
                elif category == "electronics":
                    demand_bias = 1.2 - local_demand * 0.25
                else:
                    demand_bias = 0.75 + random.random() * 0.5
                store_qty = max(3, int(on_hand * random.uniform(0.035, 0.09) * demand_bias))
                c.execute(
                    "INSERT INTO substrate_store_inventory (store_id, sku, on_hand, capacity) "
                    "VALUES (?, ?, ?, ?)",
                    (store_id, sku, store_qty, max(40, int(capacity / 20))),
                )

        # 90 days sales — summer apparel sales taper (overstock signal)
        now = datetime.now(timezone.utc)
        for sku, name, category, vendor, price in all_skus:
            base = random.randint(1, 8)
            for d in range(90, 0, -1):
                ts = (now - timedelta(days=d)).isoformat()
                if category == "summer_apparel":
                    decay = max(0.2, (d / 90.0))
                    units = max(0, int(random.gauss(base * decay, 2)))
                else:
                    units = max(0, int(random.gauss(base, 2)))
                if units > 0:
                    c.execute(
                        "INSERT INTO substrate_sales (sku, ts, units, revenue) VALUES (?, ?, ?, ?)",
                        (sku, ts, units, units * price),
                    )

        by_category = {
            category: [item for item in all_skus if item[2] == category]
            for category in CATEGORIES
        }
        channels = ["web", "app", "storefront", "marketplace"]
        methods = ["dc_ship", "ship_from_store", "bopis"]
        for d in range(45, 0, -1):
            day = now - timedelta(days=d)
            for category, items in by_category.items():
                if category == "summer_apparel":
                    daily_orders = random.randint(7, 15) if d > 16 else random.randint(4, 10)
                elif category == "electronics":
                    daily_orders = random.randint(10, 20)
                else:
                    daily_orders = random.randint(6, 13)
                for _ in range(daily_orders):
                    sku, _name, _cat, _vendor, price = random.choice(items)
                    store = random.choice(STORES)
                    segment_id = random.choice([s[0] for s in SEGMENTS])
                    channel = random.choices(channels, weights=[32, 28, 30, 10], k=1)[0]
                    method = random.choices(methods, weights=[45, 30, 25], k=1)[0]
                    units = random.choices([1, 2, 3], weights=[70, 24, 6], k=1)[0]
                    revenue = round(units * price * random.uniform(0.9, 1.04), 2)
                    margin_rate = CATEGORY_PROFILES[category]["margin_target"] + random.uniform(-0.05, 0.07)
                    if method == "ship_from_store":
                        margin_rate -= 0.035
                    if channel == "marketplace":
                        margin_rate -= 0.05
                    margin = round(revenue * max(0.18, margin_rate), 2)
                    return_risk = round(random.uniform(0.05, 0.18) + (0.05 if category == "summer_apparel" else 0), 2)
                    c.execute(
                        "INSERT INTO substrate_orders "
                        "(ts, sku, category, channel, fulfillment_method, store_id, segment_id, "
                        "units, revenue, margin, return_risk) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (
                            day.isoformat(),
                            sku,
                            category,
                            channel,
                            method,
                            store[0],
                            segment_id,
                            units,
                            revenue,
                            margin,
                            return_risk,
                        ),
                    )

        po_counter = 1
        for category, items in by_category.items():
            for sku, _name, _cat, vendor, _price in items[:4]:
                eta = (now + timedelta(days=random.randint(3, 28))).isoformat()
                if category == "summer_apparel":
                    qty = random.randint(90, 180)
                    reliability = random.uniform(0.62, 0.86)
                elif category == "electronics":
                    qty = random.randint(35, 90)
                    reliability = random.uniform(0.72, 0.93)
                else:
                    qty = random.randint(50, 120)
                    reliability = random.uniform(0.8, 0.96)
                c.execute(
                    "INSERT INTO substrate_inbound_pos "
                    "(po_id, sku, vendor, eta, qty, status, reliability) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (f"po-{po_counter:03d}", sku, vendor, eta, qty, "open", round(reliability, 2)),
                )
                po_counter += 1

        c.execute(
            "INSERT INTO substrate_campaigns "
            "(campaign_id, title, category, segment_id, channel, budget, offer, projected_lift, "
            "actual_lift, projected_roi, actual_roi, status, starts_at, ends_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "cmp-weekend-heat",
                "Weekend Heatwave Summer Push",
                "summer_apparel",
                "seg-vacation",
                "paid social + app push",
                42000,
                "heatwave weekend: 30% off beach-ready picks",
                0.28,
                None,
                2.05,
                None,
                "planned",
                now.isoformat(),
                (now + timedelta(days=10)).isoformat(),
            ),
        )
        c.execute(
            "INSERT INTO action_queue "
            "(ts, owner, action_type, title, status, payload_json, artifact_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                now.isoformat(),
                "Marketing",
                "campaign_brief",
                "Weekend Heatwave Summer Push",
                "proposed",
                json.dumps(
                    {
                        "category": "summer_apparel",
                        "segment_id": "seg-vacation",
                        "channel": "paid social + app push",
                        "budget": 42000,
                        "projected_lift": 0.28,
                    }
                ),
                None,
            ),
        )

    # Mirror SKU + vendor into knowledge graph
    for category, profile in CATEGORY_PROFILES.items():
        upsert_node(category, "category", profile)
    for sku, name, category, vendor, price in all_skus:
        upsert_node(sku, "sku", {"name": name, "category": category, "vendor": vendor, "price": price})
        upsert_node(vendor, "vendor", {"name": vendor})
        upsert_edge(category, "contains", sku)
        upsert_edge(vendor, "supplies", sku)
    for store_id, name, region, capacity, labor_pressure, local_demand, weather in STORES:
        upsert_node(
            store_id,
            "store",
            {
                "name": name,
                "region": region,
                "capacity": capacity,
                "labor_pressure": labor_pressure,
                "local_demand_signal": local_demand,
                "weather_signal": weather,
            },
        )
        for category in CATEGORIES:
            upsert_edge(store_id, "sells", category)
    for segment_id, name, loyalty_tier, channel_preference, avg_order_value, notes in SEGMENTS:
        upsert_node(
            segment_id,
            "segment",
            {
                "name": name,
                "loyalty_tier": loyalty_tier,
                "channel_preference": channel_preference,
                "avg_order_value": avg_order_value,
                "notes": notes,
            },
        )
        for (affinity_segment_id, category), (affinity, expected_lift, offer) in AFFINITY.items():
            if affinity_segment_id == segment_id:
                upsert_edge(
                    segment_id,
                    "responds_to",
                    category,
                    {"affinity": affinity, "expected_lift": expected_lift, "offer": offer},
                )
    upsert_node(
        "cmp-weekend-heat",
        "campaign",
        {
            "title": "Weekend Heatwave Summer Push",
            "category": "summer_apparel",
            "segment_id": "seg-vacation",
            "status": "planned",
        },
    )
    upsert_edge("summer_apparel", "promoted_by", "cmp-weekend-heat")
    upsert_edge("cmp-weekend-heat", "targets", "seg-vacation")

    print(f"seeded {len(all_skus)} SKUs, 90 days sales, omnichannel orders, campaigns, stores")


if __name__ == "__main__":
    seed()
