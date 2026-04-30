import type { InventorySku } from "../../lib/api";
import { useSort } from "./sortable";

const cur = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 2,
});
const num = new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 });

function riskChip(s: InventorySku): { label: string; cls: string } | null {
  if (s.on_hand < s.reorder_point) return { label: "short", cls: "chip-bad" };
  if (s.reorder_point > 0 && s.on_hand > s.reorder_point * 3) return { label: "over", cls: "chip-warn" };
  return null;
}

export default function InventoryTab({ skus }: { skus: InventorySku[] }) {
  const { sorted, header } = useSort(skus, "on_hand" as keyof InventorySku, "desc");
  return (
    <table data-testid="tab-inventory">
      <thead>
        <tr>
          <th {...header("sku", "SKU")} />
          <th {...header("category", "Category")} />
          <th {...header("on_hand", "On hand", "num")} />
          <th {...header("reorder_point", "Reorder", "num")} />
          <th {...header("price", "Price", "num")} />
          <th {...header("base_price", "Base", "num")} />
          <th>Risk</th>
        </tr>
      </thead>
      <tbody>
        {sorted.map((s) => {
          const chip = riskChip(s);
          return (
            <tr key={s.sku}>
              <td>{s.sku}</td>
              <td>{s.category}</td>
              <td className="num">{num.format(s.on_hand)}</td>
              <td className="num">{num.format(s.reorder_point)}</td>
              <td className="num">{cur.format(s.price)}</td>
              <td className="num">{cur.format(s.base_price)}</td>
              <td>{chip ? <span className={`chip ${chip.cls}`}>{chip.label}</span> : ""}</td>
            </tr>
          );
        })}
        {sorted.length === 0 && (
          <tr>
            <td colSpan={7} style={{ textAlign: "center", color: "var(--ink-mute)" }}>── no rows ──</td>
          </tr>
        )}
      </tbody>
    </table>
  );
}
