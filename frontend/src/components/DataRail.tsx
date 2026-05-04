import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useDashboardData, type DashboardData } from "../lib/data";
import CategoriesTab from "./dataTabs/CategoriesTab";
import StoresTab from "./dataTabs/StoresTab";
import InventoryTab from "./dataTabs/InventoryTab";
import CampaignsTab from "./dataTabs/CampaignsTab";
import IntegrationsTab from "./dataTabs/IntegrationsTab";
import ReportsTab from "./dataTabs/ReportsTab";
import MlflowTab from "./dataTabs/MlflowTab";
import WikiTab from "./dataTabs/WikiTab";
import BrainTab from "./dataTabs/BrainTab";
import ImproveTab from "./dataTabs/ImproveTab";
import "./DataRail.css";

/** Single source of truth for the rail's tabs.
 *
 *  Each entry owns:
 *    - `id`        — short stable key persisted in localStorage.
 *    - `label`     — header text.
 *    - `render`    — function that builds the panel from the rail's
 *                    fetched dashboard data + a `refresh()` handle.
 *
 *  Adding a tab = appending one entry. The header buttons, keyboard
 *  shortcut wiring, and panel rendering all read from this list, so
 *  drift across the three call sites is impossible by construction.
 */
type TabContext = {
  data: DashboardData;
  refresh: () => void;
};

type TabSpec = {
  id: string;
  label: string;
  render: (ctx: TabContext) => ReactNode;
};

const TABS: TabSpec[] = [
  { id: "cat", label: "CATEGORIES", render: ({ data }) => <CategoriesTab rows={data.categories} /> },
  { id: "str", label: "STORES", render: ({ data }) => <StoresTab rows={data.stores} /> },
  { id: "inv", label: "INVENTORY", render: ({ data }) => <InventoryTab skus={data.skus} /> },
  { id: "cmp", label: "CAMPAIGNS", render: ({ data }) => <CampaignsTab rows={data.campaigns} /> },
  {
    id: "int",
    label: "INTEGRATIONS",
    render: ({ data, refresh }) => <IntegrationsTab systems={data.systems} onRefresh={refresh} />,
  },
  { id: "rep", label: "REPORTS", render: ({ data }) => <ReportsTab artifacts={data.artifacts} /> },
  { id: "wik", label: "WIKI", render: () => <WikiTab /> },
  { id: "brn", label: "BRAIN", render: () => <BrainTab /> },
  { id: "mlf", label: "MLFLOW", render: () => <MlflowTab /> },
  { id: "imp", label: "IMPROVE", render: () => <ImproveTab /> },
];

const STORAGE_KEY = "retail-os.datarail.tab";
const KEY_BINDINGS = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0"] as const;

export default function DataRail() {
  const { data, loading, error, refresh } = useDashboardData();
  const [tabId, setTabId] = useState<string>(() => {
    const stored = (typeof localStorage !== "undefined" && localStorage.getItem(STORAGE_KEY)) || TABS[0].id;
    return TABS.find((t) => t.id === stored)?.id ?? TABS[0].id;
  });
  const sectionRef = useRef<HTMLElement>(null);
  const activeTab = useMemo(() => TABS.find((t) => t.id === tabId) ?? TABS[0], [tabId]);

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY, tabId);
  }, [tabId]);

  useEffect(() => {
    const el = sectionRef.current;
    if (!el) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return;
      const idx = KEY_BINDINGS.indexOf(e.key as (typeof KEY_BINDINGS)[number]);
      if (idx >= 0 && idx < TABS.length) {
        e.preventDefault();
        setTabId(TABS[idx].id);
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
            className={`tab ${t.id === tabId ? "active" : ""}`}
            onClick={() => setTabId(t.id)}
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
          activeTab.render({ data, refresh })
        )}
      </div>
    </section>
  );
}
