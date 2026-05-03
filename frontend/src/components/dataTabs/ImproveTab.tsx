import { useCallback, useEffect, useState } from "react";
import {
  ImprovementRun,
  ImprovementSuggestion,
  acceptImprovementSuggestion,
  dismissImprovementSuggestion,
  getImprovementRun,
  listImprovementRuns,
  listImprovementSuggestions,
  startImprovementsRun,
} from "../../lib/api";
import { renderSafeMarkdown } from "../../lib/safeMarkdown";
import "./WikiTab.css";

const SEVERITY_CHIP: Record<string, string> = {
  high: "stage-critique",
  medium: "stage-draft",
  low: "stage-revision",
};

const SUGGESTION_STATUS_CHIP: Record<string, string> = {
  open: "stage-draft",
  accepted: "stage-final",
  dismissed: "stage-revision",
};

const RUN_STATUS_CHIP: Record<string, string> = {
  ok: "stage-final",
  error: "stage-critique",
  running: "stage-draft",
};

const STATUS_FILTERS = ["open", "accepted", "dismissed", "all"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

function suggestionsEqual(
  a: ImprovementSuggestion[],
  b: ImprovementSuggestion[],
): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].status !== b[i].status) return false;
  }
  return true;
}

function runsEqual(a: ImprovementRun[], b: ImprovementRun[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id || a[i].status !== b[i].status) return false;
  }
  return true;
}

export default function ImproveTab() {
  const [suggestions, setSuggestions] = useState<ImprovementSuggestion[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [recentRuns, setRecentRuns] = useState<ImprovementRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Derived: the most recent run, prioritising any in-flight run.
  const activeRun =
    recentRuns.find((r) => r.status === "running") ?? recentRuns[0] ?? null;
  const isRunning = activeRun?.status === "running";

  const refreshSuggestions = useCallback(async () => {
    try {
      const list = await listImprovementSuggestions({
        status: statusFilter,
        limit: 100,
      });
      // Skip the setter when the list hasn't changed — avoids a
      // re-render storm when the 4s poll fires identical payloads.
      setSuggestions((cur) => (suggestionsEqual(cur, list) ? cur : list));
      setSelectedId((cur) => {
        if (list.length === 0) return null;
        if (cur && list.some((s) => s.id === cur)) return cur;
        return list[0].id;
      });
      setError(null);
    } catch (e: any) {
      setError(e?.message ?? "fetch failed");
    }
  }, [statusFilter]);

  const refreshRuns = useCallback(async () => {
    try {
      const runs = await listImprovementRuns(8);
      setRecentRuns((cur) => (runsEqual(cur, runs) ? cur : runs));
    } catch {
      // Don't clobber existing error state — runs poll is best-effort.
    }
  }, []);

  useEffect(() => {
    refreshSuggestions();
    refreshRuns();
    const id = setInterval(() => {
      refreshSuggestions();
      refreshRuns();
    }, 4000);
    return () => clearInterval(id);
  }, [refreshSuggestions, refreshRuns]);

  // Aggressive 1.5s poll while a run is in flight so the chip flips
  // from `running` → `ok`/`error` quickly without waiting on the 4s
  // baseline poll.
  useEffect(() => {
    if (!activeRun || activeRun.status !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    const tick = async () => {
      if (cancelled) return;
      try {
        const fresh = await getImprovementRun(activeRun.id);
        if (cancelled) return;
        setRecentRuns((cur) => {
          const next = cur.map((r) => (r.id === fresh.id ? fresh : r));
          return runsEqual(cur, next) ? cur : next;
        });
        if (fresh.status !== "running") {
          refreshSuggestions();
          refreshRuns();
          return;
        }
      } catch {
        // 4s baseline poll covers it.
      }
      if (!cancelled) {
        timer = setTimeout(tick, 1500);
      }
    };
    timer = setTimeout(tick, 1500);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [activeRun?.id, activeRun?.status, refreshSuggestions, refreshRuns]);

  const onRun = async () => {
    setError(null);
    try {
      const { run_id } = await startImprovementsRun();
      setRecentRuns((cur) => [
        {
          id: run_id,
          started_ts: new Date().toISOString(),
          ended_ts: null,
          status: "running",
          summary: {},
          error: null,
        },
        ...cur,
      ]);
    } catch (e: any) {
      setError(e?.message ?? "audit failed to start");
    }
  };

  const onAccept = async (id: number) => {
    try {
      await acceptImprovementSuggestion(id);
      refreshSuggestions();
    } catch (e: any) {
      setError(e?.message ?? "accept failed");
    }
  };

  const onDismiss = async (id: number) => {
    try {
      await dismissImprovementSuggestion(id);
      refreshSuggestions();
    } catch (e: any) {
      setError(e?.message ?? "dismiss failed");
    }
  };

  const selected = selectedId
    ? suggestions.find((s) => s.id === selectedId)
    : null;

  return (
    <div className="wiki-tab" data-testid="tab-improve">
      <div className="wiki-pane wiki-list">
        <div className="wiki-search">
          <span className="prompt">/</span>
          <button
            onClick={onRun}
            disabled={isRunning}
            data-testid="improve-run-button"
            className="improve-run-button"
          >
            {isRunning ? "auditing…" : "audit now"}
          </button>
          <select
            value={statusFilter}
            onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
            data-testid="improve-status-filter"
          >
            {STATUS_FILTERS.map((s) => (
              <option key={s} value={s}>
                {s}
              </option>
            ))}
          </select>
          <span className="count">{suggestions.length}</span>
        </div>
        <div className="wiki-status-bar">
          {activeRun ? (
            <span
              className={`chip ${RUN_STATUS_CHIP[activeRun.status] ?? "stage-draft"}`}
              title={`run ${activeRun.id}`}
            >
              run · {activeRun.status}
              {activeRun.summary?.suggestions
                ? ` · ${activeRun.summary.suggestions} suggestions`
                : ""}
              {activeRun.summary?.elapsed_s
                ? ` · ${activeRun.summary.elapsed_s}s`
                : ""}
            </span>
          ) : (
            <span className="chip stage-draft">no audits yet</span>
          )}
          {error && <span className="wiki-error">! {error}</span>}
        </div>
        <ul className="wiki-page-list">
          {suggestions.map((s) => (
            <li
              key={s.id}
              className={selectedId === s.id ? "active" : ""}
              onClick={() => setSelectedId(s.id)}
              data-testid={`improve-row-${s.id}`}
            >
              <span className={`chip ${SEVERITY_CHIP[s.severity] || "stage-draft"}`}>
                {s.severity}
              </span>
              <span className="wiki-page-title">{s.title}</span>
              <span className="wiki-page-slug">{s.area}</span>
            </li>
          ))}
          {suggestions.length === 0 && (
            <li className="wiki-empty">
              {isRunning
                ? "── auditing… ──"
                : statusFilter === "open"
                  ? "── no open suggestions · click `audit now` ──"
                  : `── no ${statusFilter} suggestions ──`}
            </li>
          )}
        </ul>
      </div>
      <div className="wiki-pane wiki-detail">
        {!selected ? (
          <div className="wiki-empty">── select a suggestion ──</div>
        ) : (
          <>
            <div className="wiki-detail-header">
              <h3>{selected.title}</h3>
              <div className="wiki-detail-meta">
                <span className={`chip ${SEVERITY_CHIP[selected.severity] || "stage-draft"}`}>
                  {selected.severity}
                </span>
                <span className="wiki-page-slug">{selected.area}</span>
                <span className="wiki-page-slug">→ {selected.action_hint}</span>
                <span
                  className={`chip ${SUGGESTION_STATUS_CHIP[selected.status] ?? "stage-draft"}`}
                >
                  {selected.status}
                </span>
                {selected.ts && (
                  <span className="wiki-page-slug">{selected.ts.slice(0, 19)}</span>
                )}
              </div>
            </div>
            <div
              className="wiki-detail-body"
              dangerouslySetInnerHTML={{
                __html: renderSafeMarkdown(selected.body_md || ""),
              }}
            />
            {selected.refs && selected.refs.length > 0 && (
              <div className="wiki-detail-footer">
                <strong>refs:</strong>
                <ul>
                  {selected.refs.map((r, i) => (
                    <li key={`${r}-${i}`}>{r}</li>
                  ))}
                </ul>
              </div>
            )}
            {selected.status === "open" && (
              <div className="improve-actions">
                <button
                  onClick={() => onAccept(selected.id)}
                  data-testid={`improve-accept-${selected.id}`}
                >
                  accept
                </button>
                <button
                  onClick={() => onDismiss(selected.id)}
                  data-testid={`improve-dismiss-${selected.id}`}
                >
                  dismiss
                </button>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
