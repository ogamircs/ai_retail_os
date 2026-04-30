from app.spine.db import conn


def get_sku(sku: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT s.sku, s.name, s.category, s.vendor, "
            "i.on_hand, i.reorder_point, i.price, i.base_price "
            "FROM substrate_skus s JOIN substrate_inventory i ON i.sku = s.sku "
            "WHERE s.sku = ?",
            (sku,),
        ).fetchone()
    return dict(row) if row else None


def list_skus(category: str | None = None) -> list[dict]:
    with conn() as c:
        if category:
            rows = c.execute(
                "SELECT s.sku, s.name, s.category, s.vendor, "
                "i.on_hand, i.reorder_point, i.price, i.base_price "
                "FROM substrate_skus s JOIN substrate_inventory i ON i.sku = s.sku "
                "WHERE s.category = ?",
                (category,),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT s.sku, s.name, s.category, s.vendor, "
                "i.on_hand, i.reorder_point, i.price, i.base_price "
                "FROM substrate_skus s JOIN substrate_inventory i ON i.sku = s.sku"
            ).fetchall()
    return [dict(r) for r in rows]


def get_stock(sku: str) -> dict | None:
    s = get_sku(sku)
    if not s:
        return None
    return {
        "sku": s["sku"],
        "on_hand": s["on_hand"],
        "reorder_point": s["reorder_point"],
        "price": s["price"],
        "base_price": s["base_price"],
    }


def apply_markdown(sku: str, percent: float) -> dict:
    s = get_sku(sku)
    if not s:
        raise ValueError(f"unknown sku: {sku}")
    if percent <= 0 or percent >= 100:
        raise ValueError("percent must be in (0, 100)")
    new_price = round(s["base_price"] * (1 - percent / 100.0), 2)
    with conn() as c:
        c.execute("UPDATE substrate_inventory SET price = ? WHERE sku = ?", (new_price, sku))
    return {
        "sku": sku,
        "old_price": s["price"],
        "new_price": new_price,
        "percent": percent,
    }


def apply_po(sku: str, qty: int, vendor: str) -> dict:
    s = get_sku(sku)
    if not s:
        raise ValueError(f"unknown sku: {sku}")
    new_on_hand = s["on_hand"] + qty
    with conn() as c:
        c.execute(
            "UPDATE substrate_inventory SET on_hand = ? WHERE sku = ?", (new_on_hand, sku)
        )
    return {
        "sku": sku,
        "vendor": vendor,
        "qty_ordered": qty,
        "old_on_hand": s["on_hand"],
        "new_on_hand": new_on_hand,
    }
