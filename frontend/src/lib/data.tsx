import {
  ReactNode,
  createContext,
  useCallback,
  useContext,
  useEffect,
  useState,
} from "react";
import {
  ActionItem,
  ArtifactMeta,
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
  listArtifacts,
  listCampaigns,
  listCategories,
  listIntegrationSystems,
  listSyncRuns,
  listStores,
} from "./api";

export type DashboardData = {
  kpis: KpiResponse | null;
  categories: CategoryOpportunity[];
  campaigns: Campaign[];
  stores: Store[];
  actions: ActionItem[];
  skus: InventorySku[];
  inbound: InboundPo[];
  systems: IntegrationSystem[];
  syncRuns: SyncRun[];
  artifacts: ArtifactMeta[];
};

const EMPTY: DashboardData = {
  kpis: null,
  categories: [],
  campaigns: [],
  stores: [],
  actions: [],
  skus: [],
  inbound: [],
  systems: [],
  syncRuns: [],
  artifacts: [],
};

type DashboardCtx = {
  data: DashboardData;
  loading: boolean;
  error: string;
  refresh: () => Promise<void>;
  bump: () => void;
};

const Ctx = createContext<DashboardCtx | null>(null);

export function DashboardProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<DashboardData>(EMPTY);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string>("");
  const [tick, setTick] = useState(0);

  const refresh = useCallback(async () => {
    try {
      setError("");
      const [kpis, categories, campaigns, stores, actions, inventory, systems, syncRuns, artifacts] =
        await Promise.all([
          getKpis(),
          listCategories(),
          listCampaigns(),
          listStores(),
          listActionQueue(),
          getInventoryHealth(),
          listIntegrationSystems(),
          listSyncRuns(),
          listArtifacts(),
        ]);
      setData({
        kpis,
        categories,
        campaigns,
        stores,
        actions,
        skus: inventory.skus,
        inbound: inventory.inbound_pos,
        systems,
        syncRuns,
        artifacts,
      });
    } catch (e: any) {
      setError(e?.message || "Dashboard data unavailable");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  useEffect(() => {
    if (tick > 0) refresh();
  }, [tick, refresh]);

  return (
    <Ctx.Provider
      value={{ data, loading, error, refresh, bump: () => setTick((t) => t + 1) }}
    >
      {children}
    </Ctx.Provider>
  );
}

export function useDashboardData() {
  const v = useContext(Ctx);
  if (!v) throw new Error("useDashboardData must be used inside DashboardProvider");
  return v;
}
