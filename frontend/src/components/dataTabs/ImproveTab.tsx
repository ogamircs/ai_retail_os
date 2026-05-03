import { useEffect, useMemo, useState } from "react";
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

/**
 * Track 8 — Improvement Auditor cockpit surface.
 *
 * Operator clicks `audit now`. Backend kicks off a daemon thread
 * that walks the cockpit's signals snapshot, runs an LLM agent, and
 * writes 3-8 suggestions. This tab polls the run until terminal,
 * then surfaces every open suggestion with accept/dismiss.
 *
 * Two-pane layout matching WikiTab so the operator's muscle memory
 * transfers — left: suggestion list (severity chip + title), right:
 * full body markdown + actions.
 */

const SEVERITY_CHIP: Record<string, string> = {
  high: "stage-critique",
  medium: "stage-draft",
  low: "stage-revision",
};

const STATUS_FILTERS = ["open", "accepted", "dismissed", "all"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

export default function ImproveTab() {
  const [suggestions, setSuggestions] = useState<ImprovementSuggestion[]>([]);
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("open");
  const [activeRun, setActiveRun] = useState<ImprovementRun | null>(null);
  const [recentRuns, setRecentRuns] = useState<ImprovementRun[]>([]);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refreshSuggestions = useMemo(
    () => async () => {
      try {
        const list = await listImprovementSuggestions({
          status: statusFilter,
          limit: 100,
        });
        setSuggestions(list);
        // Snap selection: keep prior id when still in list, else first.
        setSelectedId((cur) => {
          if (list.length === 0) return null;
          if (cur && list.some((s) => s.id === cur)) return cur;
          return list[0].id;
        });
        setError(null);
      } catch (e: any) {
        setError(e?.message ?? "fetch failed");
      }
    },
    [statusFilter],
  );

  const refreshRuns = useMemo(
    () => async () => {
      try {
        const runs = await listImprovementRuns(8);
        setRecentRuns(runs);
        // If a run is currently `running`, surface it as the active one.
        const live = runs.find((r) => r.status === "running");
        setActiveRun((cur) => live ?? cur);
      } catch {
        // Don't clobber existing error state — runs poll is best-effort.
      }
    },
    [],
  );

  useEffect(() => {
    refreshSuggestions();
    refreshRuns();
    const id = setInterval(() => {
      refreshSuggestions();
      refreshRuns();
    }, 4000);
    return () => clearInterval(id);
  }, [refreshSuggestions, refreshRuns]);

  // While a run is in flight, poll its status more aggressively so the
  // banner flips from `running` → `ok`/`error` quickly.
  useEffect(() => {
    if (!activeRun || activeRun.status !== "running") return;
    let cancelled = false;
    const tick = async () => {
      try {
        const fresh = await getImprovementRun(activeRun.id);
        if (cancelled) return;
        setActiveRun(fresh);
        if (fresh.status !== "running") {
          // Pull fresh suggestions immediately on terminal status.
          refreshSuggestions();
          refreshRuns();
        } else {
          setTimeout(tick, 1500);
        }
      } catch {
        // Ignore — overall 4s poll covers it.
      }
    };
    setTimeout(tick, 1500);
    return () => {
      cancelled = true;
    };
  }, [activeRun?.id, activeRun?.status, refreshSuggestions, refreshRuns]);

  const onRun = async () => {
    setError(null);
    try {
      const { run_id } = await startImprovementsRun();
      setActiveRun({
        id: run_id,
        started_ts: new Date().toISOString(),
        ended_ts: null,
        status: "running",
        summary: {},
        error: null,
      });
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

  const isRunning = activeRun?.status === "running";

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
              className={`chip ${
                activeRun.status === "ok"
                  ? "stage-final"
                  : activeRun.status === "error"
                    ? "stage-critique"
                    : "stage-draft"
              }`}
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
          ) : recentRuns.length > 0 ? (
            <span
              className="chip stage-final"
              title={`last run ${recentRuns[0].id}`}
            >
              last · {recentRuns[0].status} · {recentRuns[0].summary?.suggestions ?? 0} suggestions
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
                <span className={`chip stage-${selected.status === "accepted" ? "final" : selected.status === "dismissed" ? "revision" : "draft"}`}>
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
