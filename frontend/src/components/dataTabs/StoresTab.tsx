import type { Store } from "../../lib/api";
import { useSort } from "./sortable";

const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });
const pct = (n: number) => `${(n * 100).toFixed(0)}%`;

export default function StoresTab({ rows }: { rows: Store[] }) {
  const { sorted, header } = useSort(rows, "local_demand_signal" as keyof Store, "desc");
  return (
    <table data-testid="tab-stores">
      <thead>
        <tr>
          <th {...header("store_id", "ID")} />
          <th {...header("name", "Name")} />
          <th {...header("region", "Region")} />
          <th {...header("capacity", "Capacity", "num")} />
          <th {...header("labor_pressure", "Labor", "num")} />
          <th {...header("local_demand_signal", "Demand", "num")} />
          <th {...header("weather_signal", "Weather")} />
          <th {...header("capacity_used", "Used", "num")} />
        </tr>
      </thead>
      <tbody>
        {sorted.map((r) => (
          <tr key={r.store_id}>
            <td>{r.store_id}</td>
            <td>{r.name}</td>
            <td>{r.region}</td>
            <td className="num">{num.format(r.capacity)}</td>
            <td className="num">{r.labor_pressure.toFixed(2)}</td>
            <td className="num">{r.local_demand_signal.toFixed(2)}</td>
            <td>{r.weather_signal}</td>
            <td className="num">{pct(r.capacity_used)}</td>
          </tr>
        ))}
        {sorted.length === 0 && (
          <tr>
            <td colSpan={8} style={{ textAlign: "center", color: "var(--ink-mute)" }}>── no rows ──</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
