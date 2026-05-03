import json

from app.spine.db import conn


def upsert_node(node_id: str, node_type: str, props: dict) -> None:
    with conn() as c:
        c.execute(
            "INSERT INTO kg_nodes (id, type, props_json) VALUES (?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET type=excluded.type, props_json=excluded.props_json",
            (node_id, node_type, json.dumps(props)),
        )


def get_node(node_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(
            "SELECT id, type, props_json FROM kg_nodes WHERE id = ?", (node_id,)
        ).fetchone()
    if not row:
        return None
    return {"id": row["id"], "type": row["type"], "props": json.loads(row["props_json"])}


def upsert_edge(src: str, rel: str, dst: str, props: dict | None = None) -> None:
    with conn() as c:
        c.execute(
            "INSERT OR REPLACE INTO kg_edges (src, rel, dst, props_json) VALUES (?, ?, ?, ?)",
            (src, rel, dst, json.dumps(props or {})),
        )


def neighborhood(node_id: str, limit: int = 40) -> dict:
    center = get_node(node_id)
    with conn() as c:
        edge_rows = c.execute(
            "SELECT src, rel, dst, props_json FROM kg_edges "
            "WHERE src = ? OR dst = ? ORDER BY rel, src, dst LIMIT ?",
            (node_id, node_id, limit),
        ).fetchall()
        linked_ids = {node_id}
        for row in edge_rows:
            linked_ids.add(row["src"])
            linked_ids.add(row["dst"])
        placeholders = ",".join("?" for _ in linked_ids)
        node_rows = []
        if placeholders:
            node_rows = c.execute(
                f"SELECT id, type, props_json FROM kg_nodes WHERE id IN ({placeholders})",
                tuple(linked_ids),
            ).fetchall()
    return {
        "center": center,
        "nodes": [
            {"id": r["id"], "type": r["type"], "props": json.loads(r["props_json"])}
            for r in node_rows
        ],
        "edges": [
            {
                "src": r["src"],
                "rel": r["rel"],
                "dst": r["dst"],
                "props": json.loads(r["props_json"] or "{}"),
            }
            for r in edge_rows
        ],
    }
