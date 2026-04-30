import type { CategoryOpportunity } from "../../lib/api";
import { useSort } from "./sortable";

const num = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });
const cur = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});
const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

export default function CategoriesTab({ rows }: { rows: CategoryOpportunity[] }) {
  const { sorted, header } = useSort(rows, "opportunity_score" as keyof CategoryOpportunity, "desc");
  return (
    <table data-testid="tab-categories">
      <thead>
        <tr>
          <th {...header("display_name", "Category")} />
          <th {...header("sales_revenue", "Rev 30d", "num")} />
          <th {...header("margin_rate", "Margin", "num")} />
          <th {...header("on_hand", "Units", "num")} />
          <th {...header("inventory_pressure", "Pressure", "num")} />
          <th {...header("opportunity_score", "Push", "num")} />
          <th {...header("lifecycle_stage", "Lifecycle")} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.category}>
            <td>{r.display_name}</td>
            <td className="num">{cur.format(r.sales_revenue)}</td>
            <td className="num">{pct(r.margin_rate)}</td>
            <td className="num">{num.format(r.on_hand)}</td>
            <td className="num">{r.inventory_pressure.toFixed(2)}</td>
            <td className="num">{r.opportunity_score.toFixed(2)}</td>
            <td>{r.lifecycle_stage}</td>
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
