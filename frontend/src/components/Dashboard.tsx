import { useEffect, useMemo, useState } from "react";
import {
  ActionItem,
  Campaign,
  CategoryOpportunity,
  InboundPo,
  IntegrationSystem,
  InventorySku,
  KpiResponse,
  Store,
  SyncRun,
  getInventoryHealth,
  getKpis,
  listActionQueue,
  listCampaigns,
  listCategories,
  listIntegrationSystems,
  listSyncRuns,
  listStores,
  syncIntegration,
} from "../lib/api";

interface Props {
  refreshKey: number;
}

type DashboardState = {
  kpis: KpiResponse | null;
  categories: CategoryOpportunity[];
  campaigns: Campaign[];
  stores: Store[];
  actions: ActionItem[];
  skus: InventorySku[];
  inbound: InboundPo[];
  systems: IntegrationSystem[];
  syncRuns: SyncRun[];
};

const money = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  notation: "compact",
  maximumFractionDigits: 1,
});

const number = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

function formatValue(value: number, format: string) {
  if (format === "currency") return money.format(value);
  if (format === "percent") return `${Math.round(value * 100)}%`;
  return number.format(value);
}

function categoryName(category: string) {
  return category.replace(/_/g, " ").replace(/\b\w/g, (m) => m.toUpperCase());
}

function statusClass(status: string) {
  return `status status-${status.replace(/[^a-z0-9]/gi, "-").toLowerCase()}`;
}

export default function Dashboard({ refreshKey }: Props) {
  const [state, setState] = useState<DashboardState>({
    kpis: null,
    categories: [],
    campaigns: [],
    stores: [],
    actions: [],
    skus: [],
    inbound: [],
    systems: [],
    syncRuns: [],
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [syncing, setSyncing] = useState("");

  const refresh = async () => {
    try {
      setError("");
      const [kpis, categories, campaigns, stores, actions, inventory, systems, syncRuns] =
        await Promise.all([
          getKpis(),
          listCategories(),
          listCampaigns(),
          listStores(),
          listActionQueue(),
          getInventoryHealth(),
          listIntegrationSystems(),
          listSyncRuns(),
        ]);
      setState({
        kpis,
        categories,
        campaigns,
        stores,
        actions,
        skus: inventory.skus,
        inbound: inventory.inbound_pos,
        systems,
        syncRuns,
      });
    } catch (e: any) {
      setError(e?.message || "Dashboard data unavailable");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  const risks = useMemo(
    () =>
      state.skus
        .filter((sku) => sku.risk !== "healthy")
        .sort((a, b) => b.days_cover - a.days_cover)
        .slice(0, 6),
    [state.skus],
  );

  const stressedStores = useMemo(
    () =>
      [...state.stores]
        .sort(
          (a, b) =>
            b.labor_pressure +
            b.capacity_used +
            b.local_demand_signal -
            (a.labor_pressure + a.capacity_used + a.local_demand_signal),
        )
        .slice(0, 5),
    [state.stores],
  );

  const recommendation = state.kpis?.recommended_category;
  const connectedCount = state.systems.filter((system) => system.configured).length;
  const latestSyncBySystem = useMemo(() => {
    const bySystem = new Map<string, SyncRun>();
    for (const run of state.syncRuns) {
      if (!bySystem.has(run.system_id)) bySystem.set(run.system_id, run);
    }
    return bySystem;
  }, [state.syncRuns]);

  const runSync = async (systemId: string) => {
    try {
      setSyncing(systemId);
      setError("");
      await syncIntegration(systemId);
      await refresh();
    } catch (e: any) {
      setError(e?.message || "Integration sync failed");
    } finally {
      setSyncing("");
    }
  };

  return (
    <div className="dashboard">
      <section className="control-band">
        <div>
          <p className="eyebrow">Omnichannel command center</p>
          <h1>Retail operating picture</h1>
        </div>
        {recommendation && (
          <div className="recommendation">
            <span>Next push</span>
            <strong>{recommendation.display_name}</strong>
            <em>{Math.round(recommendation.opportunity_score)} opportunity score</em>
          </div>
        )}
      </section>

      <section className="provenance-strip">
        <span>Retail spine</span>
        <strong>{connectedCount} connected</strong>
        <em>{state.systems.length - connectedCount} mock-ready systems</em>
      </section>

      {error && <div className="inline-error">{error}</div>}
      {loading && <div className="loading-line">Loading retail spine...</div>}

      <section className="kpi-grid">
        {state.kpis?.kpis.map((kpi) => (
          <div className="kpi-tile" key={kpi.label}>
            <span>{kpi.label}</span>
            <strong>{formatValue(kpi.value, kpi.format)}</strong>
            <em>{kpi.delta}</em>
          </div>
        ))}
      </section>

      <section className="dashboard-grid">
        <div className="ops-panel category-panel">
          <div className="section-header">
            <h2>Category Opportunities</h2>
            <span>{state.categories.length} live categories</span>
          </div>
          <div className="category-list">
            {state.categories.map((category) => (
              <div className="category-row" key={category.category}>
                <div className="category-main">
                  <strong>{category.display_name}</strong>
                  <span>{category.lifecycle_stage}</span>
                </div>
                <div className="metric-stack">
                  <span>Cover</span>
                  <strong>{Math.round(category.days_cover)}d</strong>
                </div>
                <div className="metric-stack">
                  <span>Margin</span>
                  <strong>{Math.round(category.margin_rate * 100)}%</strong>
                </div>
                <div className="score-cell">
                  <span>{Math.round(category.opportunity_score)}</span>
                  <div className="score-track">
                    <div
                      className="score-fill"
                      style={{
                        width: `${Math.min(100, category.opportunity_score)}%`,
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="ops-panel campaign-panel">
          <div className="section-header">
            <h2>Marketing Queue</h2>
            <span>{state.campaigns.length} campaigns</span>
          </div>
          <div className="campaign-list">
            {state.campaigns.slice(0, 4).map((campaign) => (
              <div className="campaign-row" key={campaign.campaign_id}>
                <div>
                  <strong>{campaign.title}</strong>
                  <span>
                    {categoryName(campaign.category)} · {campaign.channel}
                  </span>
                </div>
                <div className="campaign-metrics">
                  <span className={statusClass(campaign.status)}>
                    {campaign.status}
                  </span>
                  <strong>{money.format(campaign.budget)}</strong>
                  <em>
                    ROI{" "}
                    {campaign.actual_roi != null
                      ? campaign.actual_roi.toFixed(2)
                      : campaign.projected_roi.toFixed(2)}
                    x
                  </em>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="ops-panel action-panel">
          <div className="section-header">
            <h2>Action Queue</h2>
            <span>{state.actions.length} actions</span>
          </div>
          <div className="action-list">
            {state.actions.slice(0, 7).map((action) => (
              <div className="action-row" key={action.id}>
                <span className={statusClass(action.status)}>{action.status}</span>
                <div>
                  <strong>{action.title}</strong>
                  <em>
                    {action.owner} · {action.action_type.replace(/_/g, " ")}
                  </em>
                  {!!action.external_actions?.length && (
                    <span className="external-line">
                      {action.external_actions
                        .map(
                          (external) =>
                            `${external.system_id}: ${external.status.replace(/_/g, " ")}`,
                        )
                        .join(" · ")}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="ops-panel integration-panel">
          <div className="section-header">
            <h2>Open-Source Systems</h2>
            <span>{connectedCount} connected</span>
          </div>
          <div className="integration-list">
            {state.systems.map((system) => {
              const latest = latestSyncBySystem.get(system.system_id);
              const domains = latest?.summary?.domains
                ? Object.keys(latest.summary.domains).slice(0, 2).join(", ")
                : system.domain;
              const syncDetail = latest
                ? `${latest.records_written} synced · ${domains || latest.status}`
                : system.domain;
              return (
                <div className="integration-row" key={system.system_id}>
                  <div>
                    <strong>{system.display_name}</strong>
                    <span>{syncDetail}</span>
                    <em>
                      {latest?.error ||
                        `${system.pending_actions} pending · ${system.applied_actions} applied`}
                    </em>
                  </div>
                  <div className="integration-status">
                    <span className={statusClass(system.configured ? system.last_status : "mock-only")}>
                      {system.configured ? system.last_status : "mock-only"}
                    </span>
                    <button
                      type="button"
                      onClick={() => runSync(system.system_id)}
                      disabled={syncing === system.system_id}
                    >
                      {syncing === system.system_id ? "Syncing" : "Sync"}
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        </div>

        <div className="ops-panel risk-panel">
          <div className="section-header">
            <h2>Inventory Risk</h2>
            <span>{risks.length} exceptions</span>
          </div>
          <div className="risk-list">
            {risks.map((sku) => (
              <div className="risk-row" key={sku.sku}>
                <span className={`risk-dot risk-${sku.risk}`} />
                <div>
                  <strong>{sku.name}</strong>
                  <em>
                    {sku.sku} · {categoryName(sku.category)}
                  </em>
                </div>
                <span>{Math.round(sku.days_cover)}d</span>
              </div>
            ))}
          </div>
        </div>

        <div className="ops-panel store-panel">
          <div className="section-header">
            <h2>Store Exceptions</h2>
            <span>{state.stores.length} stores</span>
          </div>
          <div className="store-list">
            {stressedStores.map((store) => (
              <div className="store-row" key={store.store_id}>
                <div>
                  <strong>{store.name}</strong>
                  <span>
                    {store.region} · {store.weather_signal}
                  </span>
                </div>
                <div className="store-bars">
                  <label>
                    Labor
                    <span>
                      <i style={{ width: `${store.labor_pressure * 100}%` }} />
                    </span>
                  </label>
                  <label>
                    Demand
                    <span>
                      <i style={{ width: `${store.local_demand_signal * 100}%` }} />
                    </span>
                  </label>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="ops-panel supplier-panel">
          <div className="section-header">
            <h2>Supplier Risk</h2>
            <span>{state.inbound.length} inbound POs</span>
          </div>
          <div className="po-list">
            {state.inbound.slice(0, 6).map((po) => (
              <div className="po-row" key={po.po_id}>
                <div>
                  <strong>{po.vendor}</strong>
                  <span>
                    {po.sku_name} · {po.qty} units
                  </span>
                </div>
                <div>
                  <span className={statusClass(po.status)}>{po.status}</span>
                  <em>{Math.round(po.reliability * 100)}% reliable</em>
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>
    </div>
  );
}
