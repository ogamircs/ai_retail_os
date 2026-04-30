import type { Campaign } from "../../lib/api";
import { useSort } from "./sortable";

const cur = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

function statusChip(s: string): string {
  if (s === "measured" || s === "active") return "chip-good";
  if (s === "blocked") return "chip-bad";
  return "chip-warn";
}

export default function CampaignsTab({ rows }: { rows: Campaign[] }) {
  const { sorted, header } = useSort(rows, "starts_at" as keyof Campaign, "desc");
  return (
    <table data-testid="tab-campaigns">
      <thead>
        <tr>
          <th {...header("campaign_id", "ID")} />
          <th {...header("category", "Category")} />
          <th {...header("channel", "Channel")} />
          <th {...header("budget", "Budget", "num")} />
          <th {...header("projected_roi", "Proj ROI", "num")} />
          <th {...header("actual_roi", "Actual ROI", "num")} />
          <th {...header("status", "Status")} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((c) => (
          <tr key={c.campaign_id}>
            <td>{c.campaign_id}</td>
            <td>{c.category}</td>
            <td>{c.channel}</td>
            <td className="num">{cur.format(c.budget)}</td>
            <td className="num">{c.projected_roi.toFixed(2)}</td>
            <td className="num">{c.actual_roi !== null ? c.actual_roi.toFixed(2) : "—"}</td>
            <td><span className={`chip ${statusChip(c.status)}`}>{c.status}</span></td>
          </tr>
        ))}
        {sorted.length === 0 && (
          <tr>
            <td colSpan={7} style={{ textAlign: "center", color: "var(--ink-mute)" }}>── no rows ──</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
