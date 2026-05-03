from datetime import UTC, datetime, timedelta

from app.spine.db import conn


def query_sales(sku: str | None = None, days: int = 30, category: str | None = None) -> dict:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with conn() as c:
        if sku:
            row = c.execute(
                "SELECT COALESCE(SUM(units), 0) AS units, COALESCE(SUM(revenue), 0) AS revenue "
                "FROM substrate_sales WHERE sku = ? AND ts >= ?",
                (sku, cutoff),
            ).fetchone()
            return {"sku": sku, "days": days, "units": row["units"], "revenue": round(row["revenue"], 2)}

        if category:
            rows = c.execute(
                "SELECT s.sku, s.name, COALESCE(SUM(x.units), 0) AS units, "
                "COALESCE(SUM(x.revenue), 0) AS revenue "
                "FROM substrate_skus s LEFT JOIN substrate_sales x "
                "ON x.sku = s.sku AND x.ts >= ? "
                "WHERE s.category = ? GROUP BY s.sku ORDER BY units DESC",
                (cutoff, category),
            ).fetchall()
        else:
            rows = c.execute(
                "SELECT s.sku, s.name, COALESCE(SUM(x.units), 0) AS units, "
                "COALESCE(SUM(x.revenue), 0) AS revenue "
                "FROM substrate_skus s LEFT JOIN substrate_sales x "
                "ON x.sku = s.sku AND x.ts >= ? GROUP BY s.sku ORDER BY units DESC",
                (cutoff,),
            ).fetchall()
    return {
        "days": days,
        "category": category,
        "skus": [
            {"sku": r["sku"], "name": r["name"], "units": r["units"], "revenue": round(r["revenue"], 2)}
            for r in rows
        ],
    }


def aggregate_by_category(days: int = 30) -> dict:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with conn() as c:
        rows = c.execute(
            "SELECT s.category, COALESCE(SUM(x.units), 0) AS units, "
            "COALESCE(SUM(x.revenue), 0) AS revenue "
            "FROM substrate_skus s LEFT JOIN substrate_sales x "
            "ON x.sku = s.sku AND x.ts >= ? GROUP BY s.category",
            (cutoff,),
        ).fetchall()
    return {
        "days": days,
        "categories": [
            {"category": r["category"], "units": r["units"], "revenue": round(r["revenue"], 2)}
            for r in rows
        ],
    }


def daily_sales(sku: str, days: int = 30) -> list[dict]:
    cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat()
    with conn() as c:
        rows = c.execute(
            "SELECT substr(ts, 1, 10) AS day, SUM(units) AS units, SUM(revenue) AS revenue "
            "FROM substrate_sales WHERE sku = ? AND ts >= ? GROUP BY day ORDER BY day",
            (sku, cutoff),
        ).fetchall()
    return [{"day": r["day"], "units": r["units"], "revenue": round(r["revenue"], 2)} for r in rows]
