import json
import uuid
from datetime import datetime, timezone
from app.config import ARTIFACTS_DIR
from app.spine.db import conn


def write_artifact(
    agent: str,
    kind: str,
    title: str,
    body_md: str,
    refs: list[str] | None = None,
) -> str:
    aid = uuid.uuid4().hex[:12]
    ts = datetime.now(timezone.utc).isoformat()
    meta = {
        "id": aid,
        "agent": agent,
        "kind": kind,
        "title": title,
        "ts": ts,
        "refs": refs or [],
    }
    path = ARTIFACTS_DIR / f"{aid}.md"
    frontmatter = "---\n" + json.dumps(meta, indent=2) + "\n---\n\n"
    path.write_text(frontmatter + body_md)
    return aid


def read_artifact(aid: str) -> dict | None:
    path = ARTIFACTS_DIR / f"{aid}.md"
    if not path.exists():
        return None
    text = path.read_text()
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end > 0:
            meta = json.loads(text[4:end])
            body = text[end + 5 :]
            return {**meta, "body": body}
    return {"id": aid, "body": text, "title": aid}


def list_artifacts(limit: int = 50) -> list[dict]:
    out = []
    with conn() as c:
        rows = c.execute(
            "SELECT artifact_id, MAX(id) AS last_event_id FROM events "
            "WHERE artifact_id IS NOT NULL GROUP BY artifact_id "
            "ORDER BY last_event_id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    paths = [ARTIFACTS_DIR / f"{row['artifact_id']}.md" for row in rows]
    for path in paths:
        if not path.exists():
            continue
        text = path.read_text()
        if text.startswith("---\n"):
            end = text.find("\n---\n", 4)
            if end > 0:
                try:
                    out.append(json.loads(text[4:end]))
                    continue
                except json.JSONDecodeError:
                    pass
        out.append({"id": path.stem, "title": path.stem, "ts": "", "agent": "", "kind": ""})
    return out
