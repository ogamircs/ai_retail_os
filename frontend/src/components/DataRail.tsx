import { useEffect, useRef, useState } from "react";
import { useDashboardData } from "../lib/data";
import CategoriesTab from "./dataTabs/CategoriesTab";
import StoresTab from "./dataTabs/StoresTab";
import InventoryTab from "./dataTabs/InventoryTab";
import CampaignsTab from "./dataTabs/CampaignsTab";
import IntegrationsTab from "./dataTabs/IntegrationsTab";
import ReportsTab from "./dataTabs/ReportsTab";
import "./DataRail.css";

const TABS = [
  { id: "cat", label: "CATEGORIES" },
  { id: "str", label: "STORES" },
  { id: "inv", label: "INVENTORY" },
  { id: "cmp", label: "CAMPAIGNS" },
  { id: "int", label: "INTEGRATIONS" },
  { id: "rep", label: "REPORTS" },
] as const;

type TabId = (typeof TABS)[number]["id"];

const STORAGE_KEY = "retail-os.datarail.tab";

export default function DataRail() {
  const { data, loading, error, refresh } = useDashboardData();
  const [tab, setTab] = useState<TabId>(() => {
    const stored = (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) || "cat";
    return (TABS.find((t) => t.id === stored)?.id ?? "cat") as TabId;
  });
  const sectionRef = useRef<HTMLElement>(null);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, tab);
  }, [tab]);

  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const idx = ["1", "2", "3", "4", "5", "6"].indexOf(e.key);
      if (idx >= 0) {
        e.preventDefault();
        setTab(TABS[idx].id);
      }
    };
    el.addEventListener("keydown", onKey);
    return () => el.removeEventListener("keydown", onKey);
  }, []);

  return (
    <section className="data-rail" ref={sectionRef} tabIndex={0} data-testid="data-rail">
      <header className="rail-header">
        {TABS.map((t) => (
          <button
            key={t.id}
            className={`tab ${t.id === tab ? "active" : ""}`}
            onClick={() => setTab(t.id)}
            data-testid={`tab-button-${t.id}`}
          >
            [{t.label}]
          </button>
        ))}
      </header>
      <div className="rail-body">
        {loading && data.kpis === null ? (
          <div className="rail-empty">── loading ──</div>
        ) : error ? (
          <div className="rail-empty err">── connection lost · retry in 5s ──</div>
        ) : (
          <>
            {tab === "cat" && <CategoriesTab rows={data.categories} />}
            {tab === "str" && <StoresTab rows={data.stores} />}
            {tab === "inv" && <InventoryTab skus={data.skus} />}
            {tab === "cmp" && <CampaignsTab rows={data.campaigns} />}
            {tab === "int" && <IntegrationsTab systems={data.systems} onRefresh={refresh} />}
            {tab === "rep" && <ReportsTab artifacts={data.artifacts} />}
          </>
        )}
      </div>
    </section>
  );
}
