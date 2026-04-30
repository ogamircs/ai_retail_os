import { useMemo, useState } from "react";
import type { ActionItem, ExternalAction } from "../lib/api";
import { useDashboardData } from "../lib/data";
import { useDrawer } from "../lib/drawerContext";
import { agentInkStyle } from "../lib/agentInk";
import "./ApprovalRail.css";

type Pending = {
  key: string;
  action: ActionItem;
  external?: ExternalAction;
  systems: string[];
};

// An external action is considered "pending operator approval" while it sits
// in any of these statuses. `approval_required` is what configured adapters
// emit; `mock_only` is the equivalent for adapters running in mock mode;
// `proposed` covers earlier seed states.
const PENDING_EXTERNAL_STATUSES = new Set(["approval_required", "mock_only", "proposed"]);

function buildPending(actions: ActionItem[]): Pending[] {
  const pending: Pending[] = [];
  for (const a of actions) {
    const externals = (a.external_actions ?? []).filter((x) =>
      PENDING_EXTERNAL_STATUSES.has(x.status),
    );
    const top = a.status === "approval_required";
    if (top && externals.length === 0) {
      pending.push({ key: `q-${a.id}`, action: a, systems: [] });
    } else if (externals.length > 0) {
      pending.push({
        key: `q-${a.id}`,
        action: a,
        external: externals[0],
        systems: externals.map((x) => x.system_id),
      });
    }
  }
  return pending;
}

function contextLine(a: ActionItem): string {
  const p = a.payload || {};
  if (p.summary && typeof p.summary === "string") return p.summary;
  if (p.reason) return String(p.reason);
  if (p.percent != null) return `${p.percent}%`;
  if (p.qty_ordered != null) return `qty=${p.qty_ordered}`;
  return a.action_type;
}

export default function ApprovalRail() {
  const { data, loading, error } = useDashboardData();
  const { open } = useDrawer();
  const [rejected, setRejected] = useState<Set<string>>(new Set());

  const pending = useMemo(
    () => buildPending(data.actions).filter((p) => !rejected.has(p.key)),
    [data.actions, rejected],
  );

  // Expose reject through window for the drawer's reject button (simple bridge).
  // Drawer dispatches a custom event; we listen here.
  useMemo(() => {
    const onReject = (e: Event) => {
      const detail = (e as CustomEvent<{ key: string }>).detail;
      if (detail?.key) setRejected((r) => new Set(r).add(detail.key));
    };
    window.addEventListener("approval-reject", onReject as EventListener);
    return () => window.removeEventListener("approval-reject", onReject as EventListener);
  }, []);

  return (
    <section className="approval-rail" data-testid="approval-rail">
      <div className="block pending">
        <header>
          <span className="label">Pending</span>
          <span className="count" data-testid="pending-count">{pending.length}</span>
        </header>
        <div className="rows">
          {loading && data.kpis === null && <div className="empty">── loading ──</div>}
          {error && <div className="empty err">── connection lost ──</div>}
          {!loading && !error && pending.length === 0 && (
            <div className="empty">── no pending approvals ──</div>
          )}
          {pending.map((p) => (
            <button
              key={p.key}
              className="row"
              data-testid={`pending-row-${p.action.id}`}
              onClick={() =>
                open({ kind: "approval", action: p.action, external: p.external })
              }
            >
              <div className="row-head">
                <span className="title">▶ {p.action.title}</span>
                <span className="who" style={agentInkStyle(p.action.owner)}>{p.action.owner}</span>
              </div>
              <div className="row-meta">
                <span className="ctx">{contextLine(p.action)}</span>
                {p.systems.length > 0 && <span className="systems">{p.systems.join("+")}</span>}
                <span className="ts">←{new Date(p.action.ts).toUTCString().slice(17, 22)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
      <div className="block artifacts">
        <header>
          <span className="label">Artifacts</span>
          <span className="count">{data.artifacts.length}</span>
        </header>
        <div className="rows">
          {data.artifacts.length === 0 && <div className="empty">── no artifacts ──</div>}
          {data.artifacts.slice(0, 6).map((a) => (
            <button
              key={a.id}
              className="row art"
              data-testid={`artifact-row-${a.id}`}
              onClick={() => open({ kind: "artifact", artifactId: a.id })}
            >
              <div className="row-head">
                <span className="title">{a.title}</span>
              </div>
              <div className="row-meta">
                <span style={agentInkStyle(a.agent)}>{a.agent}</span>
                <span className="ctx">{a.kind}</span>
                <span className="ts">{a.id.slice(0, 6)}</span>
              </div>
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
