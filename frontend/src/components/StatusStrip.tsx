import { useEffect, useState } from "react";
import { ConfigInfo, getConfig, setProvider, Kpi } from "../lib/api";
import { useDashboardData } from "../lib/data";
import { useDrawer } from "../lib/drawerContext";
import "./StatusStrip.css";

const CHIP_LABELS: Record<string, string> = {
  "30d omnichannel revenue": "REV.30D",
  "Gross margin": "MGN",
  "Inventory at risk": "RISK",
  "Active campaigns": "CAMP",
  "Supplier risks": "SUPP",
  "Store exceptions": "STORE",
};

const cur = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});
const num = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

function formatKpi(k: Kpi): string {
  if (k.format === "currency") return cur.format(k.value);
  if (k.format === "percent") return `${Math.round(k.value * 100)}%`;
  return num.format(k.value);
}

function deltaClass(delta: string): string {
  const t = delta.trim();
  if (!t) return "";
  if (t.startsWith("▲") || t.startsWith("+")) return "delta-good";
  if (t.startsWith("▼") || t.startsWith("-")) return "delta-bad";
  return "delta-flat";
}

// Mirror of ApprovalRail.PENDING_EXTERNAL_STATUSES — must stay in lockstep so
// the strip's "APPROVE n!" chip and the rail's row count never disagree.
const PENDING_EXTERNAL_STATUSES = new Set(["approval_required", "mock_only", "proposed"]);

export function approvalCount(actions: { status: string; external_actions?: { status: string }[] }[]): number {
  let n = 0;
  for (const a of actions) {
    let added = 0;
    if (a.status === "approval_required") {
      n++;
      added++;
    }
    for (const ex of a.external_actions ?? []) {
      if (PENDING_EXTERNAL_STATUSES.has(ex.status) && added === 0) {
        n++;
        added++;
      }
    }
  }
  return n;
}

function utcClock(): string {
  return new Date().toISOString().slice(11, 19);
}

export default function StatusStrip() {
  const { data, error } = useDashboardData();
  const { open } = useDrawer();
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);
  const [now, setNow] = useState<string>(utcClock());

  useEffect(() => {
    getConfig().then(setCfg).catch(() => {});
  }, []);

  useEffect(() => {
    const id = setInterval(() => setNow(utcClock()), 1000);
    return () => clearInterval(id);
  }, []);

  const change = async (p: string) => {
    try {
      const c = await setProvider(p);
      setCfg(c);
    } catch {
      /* ignore */
    }
  };

  const approveN = approvalCount(data.actions);
  const kpis = data.kpis?.kpis ?? [];

  return (
    <div className="status-strip" data-testid="status-strip">
      <span className="brand">RETAIL-OS</span>
      {cfg && (
        <span className="provider">
          provider=
          <select value={cfg.provider} onChange={(e) => change(e.target.value)} data-testid="provider-select">
            <option value="anthropic">anthropic</option>
            <option value="openai">openai</option>
            <option value="google">google</option>
          </select>
          <span className={`key ${cfg.has_key ? "good" : "bad"}`}>{cfg.has_key ? "✓" : "✗"}</span>
        </span>
      )}
      <span className="clock" data-testid="utc-clock">{now} UTC</span>
      <span className="spacer" />
      {kpis.map((k) => (
        <span key={k.label} className="kpi">
          <span className="kpi-label">{CHIP_LABELS[k.label] ?? k.label.slice(0, 4).toUpperCase()}</span>
          <span className="kpi-value">{formatKpi(k)}</span>
          {k.delta && <span className={`kpi-delta ${deltaClass(k.delta)}`}>{k.delta}</span>}
        </span>
      ))}
      {error && <span className="link-err">data link · err</span>}
      <span className="spacer" />
      <button
        className={`approve ${approveN > 0 ? "approve-on" : ""}`}
        data-testid="approve-button"
        onClick={() => {
          const first = data.actions.find(
            (a) =>
              a.status === "approval_required" ||
              (a.external_actions ?? []).some((x) => x.status === "mock_only" || x.status === "proposed"),
          );
          if (first) {
            const ex = (first.external_actions ?? []).find(
              (x) => x.status === "mock_only" || x.status === "proposed",
            );
            open({ kind: "approval", action: first, external: ex });
          }
        }}
        disabled={approveN === 0}
        title={approveN === 0 ? "no pending approvals" : "open first pending approval"}
      >
        APPROVE {approveN}
        {approveN > 0 ? "!" : ""}
      </button>
    </div>
  );
}
