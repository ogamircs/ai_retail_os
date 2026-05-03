import json
import uuid
from datetime import UTC, datetime

from app.config import ARTIFACTS_DIR
from app.spine.db import conn

# Stage values flowing through the agent mesh (Track 2 A2-A4).
# `draft`     = first emission by an action specialist
# `critique`  = read-only review by the Critic
# `peer_review` = scoped peer review by another specialist (A3)
# `revision`  = revised draft after a critique/peer_review round
# `final`     = converged output (operator-facing). Drawer apply gates on this.
# Older artifacts predating A2 carry no `stage` field — treat absence as
# "final" so legacy reports remain operator-actionable.
KNOWN_STAGES = {"draft", "critique", "peer_review", "revision", "final"}


def write_artifact(
    agent: str,
    kind: str,
    title: str,
    body_md: str,
    refs: list[str] | None = None,
    stage: str = "draft",
) -> str:
    aid = uuid.uuid4().hex[:12]
    ts = datetime.now(UTC).isoformat()
    if stage not in KNOWN_STAGES:
        # Quietly normalise so a stale model can't poison the store, but
        # don't silently drop it — log via meta.original_stage for audit.
        meta_extra = {"original_stage": stage}
        stage = "draft"
    else:
        meta_extra = {}
    meta = {
        "id": aid,
        "agent": agent,
        "kind": kind,
        "stage": stage,
        "title": title,
        "ts": ts,
        "refs": refs or [],
        **meta_extra,
    }
    path = ARTIFACTS_DIR / f"{aid}.md"
    frontmatter = "---\n" + json.dumps(meta, indent=2) + "\n---\n\n"
    path.write_text(frontmatter + body_md)
    return aid


def update_artifact_stage(aid: str, stage: str) -> bool:
    """Rewrite the frontmatter of an existing artifact to flip its stage.

    Used by the Chief's review loop to mark the converged revision as
    `final` in place — so the operator sees one canonical artifact rather
    than a chain of nearly-identical drafts. Returns True on success,
    False if the artifact doesn't exist or can't be parsed.
    """
    if stage not in KNOWN_STAGES:
        return False
    path = ARTIFACTS_DIR / f"{aid}.md"
    if not path.exists():
        return False
    text = path.read_text()
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    if end < 0:
        return False
    try:
        meta = json.loads(text[4:end])
    except json.JSONDecodeError:
        return False
    body = text[end + 5 :]
    meta["stage"] = stage
    new_frontmatter = "---\n" + json.dumps(meta, indent=2) + "\n---\n\n"
    path.write_text(new_frontmatter + body)
    return True


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
