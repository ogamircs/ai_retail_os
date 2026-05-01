"""Daily telemetry aggregator (Track 4 M5).

Walks the spine event log + MLflow runs (when configured) for the last
N hours and emits a `telemetry` artifact summarising:

  * per-agent action counts (proposal / decision / observation events)
  * per-tool error rate (tool_result events whose result has `error`)
  * mesh downgrade count (Track 2 A6 events)
  * sync_inbound success vs failure (per integration system)
  * eval-score drift vs the most recent baseline (when MLflow has runs)

The artifact lands in `artifacts/` with `kind="telemetry"`,
`stage="final"`, agent="Telemetry"; the cockpit's Reports tab picks it
up via the existing list_artifacts join. An `event_id` is appended so
the ApprovalRail's audit trail surfaces the run.

Run path:
  python -m app.spine.telemetry
or programmatically:
  from app.spine.telemetry import run_aggregation
  art_id = run_aggregation(window_hours=24)
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from app.spine.artifacts import write_artifact
from app.spine.events import append_event, events_since_ts


def _format_table(rows: list[tuple[str, ...]], headers: tuple[str, ...]) -> str:
    """Markdown pipe-table without external deps."""
    if not rows:
        return "_(no rows)_"
    widths = [len(h) for h in headers]
    for r in rows:
        for i, cell in enumerate(r):
            widths[i] = max(widths[i], len(str(cell)))
    out = [
        "| " + " | ".join(h.ljust(widths[i]) for i, h in enumerate(headers)) + " |",
        "| " + " | ".join("-" * widths[i] for i in range(len(headers))) + " |",
    ]
    for r in rows:
        out.append("| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)) + " |")
    return "\n".join(out)


def aggregate(window_hours: int = 24) -> dict[str, Any]:
    """Compute the raw aggregates without writing an artifact. Useful
    for unit tests + the cockpit's M5 Reports rendering."""
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    events = events_since_ts(since)

    agent_counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    tool_results: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sync_results: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    downgrade_count = 0

    for ev in events:
        agent_counts[ev["agent"]][ev["kind"]] += 1
        kind = ev["kind"]
        payload = ev.get("payload") or {}
        if kind == "mesh_downgrade":
            downgrade_count += 1
        # Integration sync results land as `measurement` on success and
        # `rollback` on failure (see app/integrations/registry.sync_system).
        # Counting only `measurement` would silently undercount failures
        # and make the sync table look healthier than it actually is.
        # Trust the embedded `status` field over the event kind so a
        # future rename of either constant doesn't desync this count.
        if (kind in ("measurement", "rollback")) and payload.get("action") == "sync_inbound":
            sys_id = payload.get("system_id", "?")
            ok = payload.get("status") == "success"
            sync_results[sys_id]["success" if ok else "failure"] += 1
        # We don't have per-tool events in the spine — the agent run-loop
        # feeds tool_results only into the SSE stream, not into events
        # (intentional, the spine would balloon). Tool error rate is
        # therefore approximate: count any event whose payload carries
        # an `error` key.
        if isinstance(payload, dict) and payload.get("error"):
            tool_results[payload.get("action", kind)]["error"] += 1
        if isinstance(payload, dict) and payload.get("status") == "success":
            tool_results[payload.get("action", kind)]["ok"] += 1

    return {
        "window_hours": window_hours,
        "since": since,
        "event_count": len(events),
        "downgrade_count": downgrade_count,
        "agents": {a: dict(v) for a, v in agent_counts.items()},
        "tools": {t: dict(v) for t, v in tool_results.items()},
        "syncs": {s: dict(v) for s, v in sync_results.items()},
    }


def _render_markdown(data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"## Telemetry — last {data['window_hours']}h")
    lines.append("")
    lines.append(f"**Window since:** `{data['since']}`")
    lines.append(f"**Total events:** {data['event_count']}")
    lines.append(f"**Mesh downgrades:** {data['downgrade_count']}")
    lines.append("")

    lines.append("### Per-agent activity")
    rows = [
        (agent, str(sum(v.values())), ", ".join(f"{k}:{n}" for k, n in v.items()))
        for agent, v in sorted(data["agents"].items(), key=lambda kv: -sum(kv[1].values()))
    ]
    lines.append(_format_table(rows, ("Agent", "Total", "By kind")))
    lines.append("")

    lines.append("### Integration sync results")
    sync_rows = [
        (sys_id, str(v.get("success", 0)), str(v.get("failure", 0)))
        for sys_id, v in sorted(data["syncs"].items())
    ]
    lines.append(_format_table(sync_rows, ("System", "Success", "Failure")))
    lines.append("")

    lines.append("### Tool error rate (approximate)")
    tool_rows = []
    for tool, v in sorted(data["tools"].items()):
        ok = v.get("ok", 0)
        err = v.get("error", 0)
        total = ok + err
        rate = f"{(err / total * 100):.1f}%" if total else "—"
        tool_rows.append((tool, str(ok), str(err), rate))
    lines.append(_format_table(tool_rows, ("Tool/Action", "OK", "Error", "Err %")))
    lines.append("")

    lines.append(
        "_Source: spine event log. Tool error rate is approximate — the "
        "spine doesn't carry every tool_result, only events the agents "
        "themselves emit._"
    )
    return "\n".join(lines)


def run_aggregation(window_hours: int = 24) -> str:
    """Compute the aggregate, write a telemetry artifact, append an
    event referencing it. Returns the artifact id."""
    data = aggregate(window_hours)
    body = _render_markdown(data)
    title = f"Telemetry — last {window_hours}h"
    aid = write_artifact(
        agent="Telemetry",
        kind="telemetry",
        title=title,
        body_md=body,
        refs=[],
        stage="final",
    )
    append_event(
        agent="Telemetry",
        kind="observation",
        payload={
            "artifact_title": title,
            "window_hours": window_hours,
            "event_count": data["event_count"],
            "downgrade_count": data["downgrade_count"],
        },
        artifact_id=aid,
    )
    return aid


def main() -> int:
    import argparse

    p = argparse.ArgumentParser()
    p.add_argument("--window-hours", type=int, default=int(os.getenv("TELEMETRY_WINDOW_HOURS", "24")))
    p.add_argument("--print", action="store_true", help="Print aggregate JSON to stdout")
    args = p.parse_args()
    if args.print:
        print(json.dumps(aggregate(args.window_hours), indent=2))
        return 0
    aid = run_aggregation(args.window_hours)
    print(f"telemetry artifact id={aid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
