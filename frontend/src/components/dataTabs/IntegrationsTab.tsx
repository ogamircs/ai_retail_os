import { useState } from "react";
import type { IntegrationSystem } from "../../lib/api";
import { syncIntegration } from "../../lib/api";
import { useSort } from "./sortable";

function modeChip(mode: string): string {
  if (mode === "connected") return "chip-good";
  if (mode === "mock") return "chip-info";
  return "chip-warn";
}

function statusChip(s: string): string {
  if (s === "success" || s === "connected") return "chip-good";
  if (s === "mock") return "chip-info";
  if (s === "error") return "chip-bad";
  return "chip-warn";
}

export default function IntegrationsTab({
  systems,
  onRefresh,
}: {
  systems: IntegrationSystem[];
  onRefresh: () => void;
}) {
  const { sorted, header } = useSort(systems, "system_id" as keyof IntegrationSystem, "asc");
  const [busy, setBusy] = useState<string>("");

  const sync = async (id: string) => {
    setBusy(id);
    try {
      await syncIntegration(id);
      onRefresh();
    } catch {
      /* ignore */
    } finally {
      setBusy("");
    }
  };

  return (
    <table data-testid="tab-integrations">
      <thead>
        <tr>
          <th {...header("system_id", "System")} />
          <th {...header("domain", "Domain")} />
          <th {...header("mode", "Mode")} />
          <th {...header("last_sync_ts", "Last sync")} />
          <th {...header("last_status", "Status")} />
          <th>Action</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((s) => (
          <tr key={s.system_id}>
            <td>{s.system_id}</td>
            <td>{s.domain}</td>
            <td><span className={`chip ${modeChip(s.mode)}`}>{s.mode}</span></td>
            <td>{s.last_sync_ts ? new Date(s.last_sync_ts).toUTCString().slice(17, 22) : "—"}</td>
            <td><span className={`chip ${statusChip(s.last_status)}`}>{s.last_status}</span></td>
            <td>
              <button onClick={() => sync(s.system_id)} disabled={busy === s.system_id}>
                {busy === s.system_id ? "syncing…" : "sync"}
              </button>
            </td>
          </tr>
        ))}
        {sorted.length === 0 && (
          <tr>
            <td colSpan={6} style={{ textAlign: "center", color: "var(--ink-mute)" }}>── no rows ──</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
