export type AgentEvent = {
  kind: string;
  agent: string;
  data: Record<string, any>;
};

export type SpineEvent = {
  id: number;
  ts: string;
  agent: string;
  kind: string;
  sku: string | null;
  payload: Record<string, any>;
  artifact_id: string | null;
};

export type ArtifactMeta = {
  id: string;
  agent: string;
  ts: string;
  kind: string;
  title: string;
  refs?: string[];
  /**
   * Track 2 A2-A4 lifecycle stage. One of:
   *   draft, critique, peer_review, revision, final.
   * Optional — older artifacts predating the mesh upgrade carry no
   * stage; the UI treats absence as `final` so legacy reports remain
   * actionable.
   */
  stage?: string;
};

export type MeshStatus = {
  enabled: boolean;
  config: {
    max_revision_rounds: number;
    max_critic_per_draft: number;
    turn_token_budget: number;
    turn_wallclock_seconds: number;
  };
  recent_downgrade: { ts: string; payload: Record<string, unknown> } | null;
  downgrade_count_window: number;
  window_seconds: number;
};

export type ConfigInfo = {
  provider: string;
  model: string;
  has_key: boolean;
};

export type Kpi = {
  label: string;
  value: number;
  format: "currency" | "percent" | "number";
  delta: string;
};

export type CategoryOpportunity = {
  category: string;
  display_name: string;
  lifecycle_stage: string;
  margin_target: number;
  marketing_priority: number;
  weather_sensitivity: number;
  notes: string;
  sku_count: number;
  on_hand: number;
  inventory_value: number;
  sales_units: number;
  sales_revenue: number;
  order_units: number;
  order_revenue: number;
  order_margin: number;
  active_campaigns: number;
  days_cover: number;
  margin_rate: number;
  inventory_pressure: number;
  opportunity_score: number;
  recommended_push: boolean;
};

export type Campaign = {
  campaign_id: string;
  title: string;
  category: string;
  segment_id: string;
  segment_name?: string;
  channel: string;
  budget: number;
  offer: string;
  projected_lift: number;
  actual_lift: number | null;
  projected_roi: number;
  actual_roi: number | null;
  status: string;
  starts_at: string;
  ends_at: string;
};

export type Store = {
  store_id: string;
  name: string;
  region: string;
  capacity: number;
  labor_pressure: number;
  local_demand_signal: number;
  weather_signal: string;
  on_hand: number;
  inventory_capacity: number;
  order_units: number;
  revenue: number;
  margin: number;
  capacity_used: number;
  margin_rate: number;
};

export type ActionItem = {
  id: number;
  ts: string;
  owner: string;
  action_type: string;
  title: string;
  status: string;
  payload: Record<string, any>;
  artifact_id: string | null;
  external_actions?: ExternalAction[];
};

export type ExternalAction = {
  id: number;
  ts: string;
  system_id: string;
  action_queue_id: number | null;
  agent: string;
  action_type: string;
  title: string;
  status: string;
  external_domain: string;
  external_id: string | null;
  payload: Record<string, any>;
  result: Record<string, any>;
  requires_approval: boolean;
};

export type IntegrationSystem = {
  system_id: string;
  display_name: string;
  domain: string;
  enabled: boolean;
  configured: boolean;
  mode: string;
  last_status: string;
  last_sync_ts: string | null;
  last_error: string | null;
  docs_url: string;
  metadata: Record<string, any>;
  pending_actions: number;
  applied_actions: number;
};

export type SyncRun = {
  id: number;
  system_id: string;
  started_at: string;
  finished_at: string | null;
  status: string;
  records_read: number;
  records_written: number;
  error: string | null;
  summary: Record<string, any>;
};

export type InventorySku = {
  sku: string;
  name: string;
  category: string;
  vendor: string;
  on_hand: number;
  reorder_point: number;
  price: number;
  base_price: number;
  units_30d: number;
  days_cover: number;
  risk: "stockout" | "overstock" | "healthy";
};

export type InboundPo = {
  po_id: string;
  sku: string;
  vendor: string;
  eta: string;
  qty: number;
  status: string;
  reliability: number;
  category: string;
  sku_name: string;
};

export type KpiResponse = {
  kpis: Kpi[];
  recommended_category: CategoryOpportunity | null;
  last_campaign: Campaign | null;
  generated_at: string;
};

export async function getConfig(): Promise<ConfigInfo> {
  const r = await fetch("/api/config");
  return r.json();
}

export async function setProvider(provider: string): Promise<ConfigInfo> {
  const r = await fetch("/api/config", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  return r.json();
}

export async function listEvents(since = 0): Promise<SpineEvent[]> {
  const r = await fetch(`/api/events?since=${since}`);
  const j = await r.json();
  return j.events;
}

export async function listArtifacts(): Promise<ArtifactMeta[]> {
  const r = await fetch("/api/artifacts");
  const j = await r.json();
  return j.artifacts;
}

export async function getArtifact(
  id: string,
): Promise<ArtifactMeta & { body: string }> {
  const r = await fetch(`/api/artifacts/${id}`);
  return r.json();
}

export async function getKpis(): Promise<KpiResponse> {
  const r = await fetch("/api/kpis");
  return r.json();
}

export async function listCategories(): Promise<CategoryOpportunity[]> {
  const r = await fetch("/api/categories");
  const j = await r.json();
  return j.categories;
}

export async function listCampaigns(): Promise<Campaign[]> {
  const r = await fetch("/api/marketing/campaigns");
  const j = await r.json();
  return j.campaigns;
}

export async function getInventoryHealth(): Promise<{
  categories: CategoryOpportunity[];
  skus: InventorySku[];
  inbound_pos: InboundPo[];
}> {
  const r = await fetch("/api/inventory/health");
  return r.json();
}

export async function listStores(): Promise<Store[]> {
  const r = await fetch("/api/stores");
  const j = await r.json();
  return j.stores;
}

export async function listActionQueue(): Promise<ActionItem[]> {
  const r = await fetch("/api/action-queue");
  const j = await r.json();
  return j.actions;
}

export async function listIntegrationSystems(): Promise<IntegrationSystem[]> {
  const r = await fetch("/api/integrations/systems");
  const j = await r.json();
  return j.systems;
}

export async function listSyncRuns(): Promise<SyncRun[]> {
  const r = await fetch("/api/integrations/sync-runs?limit=12");
  const j = await r.json();
  return j.sync_runs;
}

export async function getMeshStatus(windowSeconds = 300): Promise<MeshStatus> {
  const r = await fetch(`/api/mesh/status?window_seconds=${windowSeconds}`);
  return await r.json();
}

export type MlflowExperiment = {
  id: string;
  name: string;
  lifecycle_stage?: string;
  last_update_time?: number;
};

export type MlflowRun = {
  run_id: string;
  experiment_id: string;
  experiment_name?: string | null;
  run_name?: string | null;
  status?: string;
  start_time?: number;
  end_time?: number;
  phase?: string;
  agent?: string;
  metrics?: Record<string, number>;
};

export type MlflowStatus = {
  enabled: boolean;
  ui_url: string | null;
  reachable: boolean;
  experiments: MlflowExperiment[];
  recent_runs: MlflowRun[];
  error: string | null;
};

export async function getMlflowStatus(limitRuns = 10): Promise<MlflowStatus> {
  const r = await fetch(`/api/mlflow/status?limit_runs=${limitRuns}`);
  return await r.json();
}

export type WikiPage = {
  slug: string;
  title: string;
  body_md: string;
  owner_agent: string;
  status: "draft" | "published" | "deprecated";
  version: number;
  updated_ts: string;
  refs: string[];
  pinned: boolean;
};

export async function listWikiPages(opts: { status?: string | null; limit?: number } = {}): Promise<WikiPage[]> {
  const params = new URLSearchParams();
  // null/undefined for status means "all stages" — pass an explicit
  // empty string so the backend's default (`published`) doesn't kick
  // in. Backend treats empty status as "no filter" (FastAPI allows
  // the str|None query param to come through as "" and the wiki
  // store's list_pages skips the filter when status is falsy).
  if (opts.status === undefined || opts.status === null) {
    params.set("status", "");
  } else {
    params.set("status", opts.status);
  }
  if (opts.limit) params.set("limit", String(opts.limit));
  const r = await fetch(`/api/wiki/pages?${params}`);
  const j = await r.json();
  return j.pages ?? [];
}

export async function searchWikiPages(
  q: string,
  opts: { status?: string | null; limit?: number } = {},
): Promise<WikiPage[]> {
  const params = new URLSearchParams({ q });
  if (opts.limit) params.set("limit", String(opts.limit));
  // Mirror listWikiPages: explicit empty string for "all stages" so
  // operator-driven status filters (draft / deprecated) aren't
  // silently dropped during search.
  if (opts.status === undefined || opts.status === null) {
    params.set("status", "");
  } else {
    params.set("status", opts.status);
  }
  const r = await fetch(`/api/wiki/search?${params}`);
  const j = await r.json();
  return j.pages ?? [];
}

export async function getWikiPage(slug: string): Promise<{ page: WikiPage; revisions: any[] }> {
  const r = await fetch(`/api/wiki/pages/${encodeURIComponent(slug)}`);
  if (!r.ok) throw new Error(`wiki page not found: ${slug}`);
  return await r.json();
}

export async function publishWikiPage(slug: string): Promise<WikiPage> {
  const r = await fetch(`/api/wiki/pages/${encodeURIComponent(slug)}/publish`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ by_agent: "Operator" }),
  });
  return (await r.json()).page;
}

export async function deprecateWikiPage(slug: string, reason = ""): Promise<WikiPage> {
  const r = await fetch(`/api/wiki/pages/${encodeURIComponent(slug)}/deprecate`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ by_agent: "Operator", reason }),
  });
  return (await r.json()).page;
}

export async function pinWikiPage(slug: string, pinned: boolean): Promise<WikiPage> {
  const r = await fetch(`/api/wiki/pages/${encodeURIComponent(slug)}/pin`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ pinned }),
  });
  return (await r.json()).page;
}

export async function listPinnedWikiPages(): Promise<WikiPage[]> {
  const r = await fetch("/api/wiki/pinned");
  const j = await r.json();
  return j.pages ?? [];
}

export async function syncIntegration(systemId: string): Promise<{
  sync_run: SyncRun;
  result: Record<string, any>;
}> {
  const r = await fetch(`/api/integrations/${systemId}/sync`, {
    method: "POST",
  });
  if (!r.ok) {
    throw new Error(`Sync failed: HTTP ${r.status}`);
  }
  return r.json();
}

export async function applyIntegrationAction(
  systemId: string,
  actionId: number,
): Promise<ExternalAction> {
  const r = await fetch(`/api/integrations/${systemId}/actions/${actionId}/apply`, {
    method: "POST",
  });
  if (!r.ok) {
    throw new Error(`Apply failed: HTTP ${r.status}`);
  }
  return r.json();
}

/** Stream chat events via fetch + SSE parser (POST body required, EventSource is GET-only). */
export async function chatStream(
  message: string,
  onEvent: (ev: AgentEvent) => void,
  onDone: () => void,
  onError: (err: string) => void,
) {
  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message }),
    });
    if (!resp.ok || !resp.body) {
      onError(`HTTP ${resp.status}`);
      return;
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    const findBoundary = (s: string): number => {
      const a = s.indexOf("\n\n");
      const b = s.indexOf("\r\n\r\n");
      if (a === -1) return b;
      if (b === -1) return a;
      return Math.min(a, b);
    };
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx: number;
      while ((idx = findBoundary(buf)) !== -1) {
        const sep = buf.slice(idx, idx + 4) === "\r\n\r\n" ? 4 : 2;
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + sep);
        for (const line of chunk.split(/\r?\n/)) {
          if (line.startsWith("data: ")) {
            try {
              const payload: AgentEvent = JSON.parse(line.slice(6));
              onEvent(payload);
            } catch {
              /* ignore malformed */
            }
          }
        }
      }
    }
    onDone();
  } catch (e: any) {
    onError(e?.message || String(e));
  }
}
