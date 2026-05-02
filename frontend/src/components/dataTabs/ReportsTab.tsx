import { useEffect, useMemo, useState } from "react";
import type { ArtifactMeta, DspyAgent, DspyJob } from "../../lib/api";
import {
  compileDspyAgent,
  getArtifact,
  getDspyJob,
  listDspyAgents,
} from "../../lib/api";
import { useDrawer } from "../../lib/drawerContext";
import { agentInkStyle } from "../../lib/agentInk";
import { useSort } from "./sortable";

type Props = {
  artifacts: ArtifactMeta[];
};

const BODY_CACHE: Map<string, string> = new Map();

// Stage chip class lookup. Falls back to a neutral class when stage is
// missing (legacy artifacts predating Track 2) so the UI stays readable.
const STAGE_CLASS: Record<string, string> = {
  draft: "stage-draft",
  critique: "stage-critique",
  peer_review: "stage-peer-review",
  revision: "stage-revision",
  final: "stage-final",
};

function stageOf(a: ArtifactMeta): string {
  // Treat absence as "final" — older artifacts should remain operator-actionable
  // and shouldn't be hidden by the default `final` filter.
  return a.stage || "final";
}

function matches(a: ArtifactMeta, q: string, body?: string): boolean {
  if (!q) return true;
  const needle = q.toLowerCase();
  if (a.title?.toLowerCase().includes(needle)) return true;
  if (a.agent?.toLowerCase().includes(needle)) return true;
  if (a.kind?.toLowerCase().includes(needle)) return true;
  if (a.id?.toLowerCase().includes(needle)) return true;
  if (a.stage?.toLowerCase().includes(needle)) return true;
  if (body && body.toLowerCase().includes(needle)) return true;
  return false;
}

export default function ReportsTab({ artifacts }: Props) {
  const [q, setQ] = useState("");
  const [deep, setDeep] = useState(false);
  // A4: default to `final` only — operator's eye should land on the
  // converged output, not the in-flight drafts. Toggle exposes the
  // full mesh trace for debugging.
  const [showAllStages, setShowAllStages] = useState(false);
  const [bodies, setBodies] = useState<Map<string, string>>(BODY_CACHE);
  const [loading, setLoading] = useState(false);
  const { open } = useDrawer();

  // When deep search is on, lazily fetch any artifact body we don't already have cached.
  useEffect(() => {
    if (!deep) return;
    const missing = artifacts.filter((a) => !BODY_CACHE.has(a.id));
    if (missing.length === 0) return;
    let cancelled = false;
    setLoading(true);
    (async () => {
      for (const a of missing) {
        try {
          const full = await getArtifact(a.id);
          BODY_CACHE.set(a.id, full.body || "");
        } catch {
          // Don't poison the cache with an empty body on failure — that
          // makes BODY_CACHE.has() return true forever and the next deep
          // search silently treats this artifact as already fetched. Leave
          // the slot empty so the next attempt retries.
        }
        if (cancelled) return;
      }
      if (!cancelled) {
        setBodies(new Map(BODY_CACHE));
        setLoading(false);
      }
    })();
    return () => {
      // Clear the loading flag even when the effect is canceled mid-flight
      // (deep toggled off, component unmounted, artifacts list churned).
      // Otherwise the "loading bodies…" hint sticks forever.
      cancelled = true;
      setLoading(false);
    };
  }, [deep, artifacts]);

  const filtered = useMemo(
    () =>
      artifacts.filter((a) => {
        if (!showAllStages && stageOf(a) !== "final") return false;
        return matches(a, q, deep ? bodies.get(a.id) : undefined);
      }),
    [artifacts, q, deep, bodies, showAllStages],
  );

  const { sorted, header } = useSort(filtered, "ts" as keyof ArtifactMeta, "desc");

  return (
    <div className="reports-tab" data-testid="tab-reports">
      <DspyCompilePanel />
      <div className="reports-search">
        <span className="prompt">/</span>
        <input
          type="text"
          placeholder="search reports — title, agent, kind, stage, id…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          data-testid="reports-search-input"
          autoFocus
        />
        <label className="deep">
          <input
            type="checkbox"
            checked={deep}
            onChange={(e) => setDeep(e.target.checked)}
            data-testid="reports-deep-toggle"
          />
          deep
        </label>
        <label className="deep">
          <input
            type="checkbox"
            checked={showAllStages}
            onChange={(e) => setShowAllStages(e.target.checked)}
            data-testid="reports-all-stages-toggle"
          />
          all stages
        </label>
        <span className="count">{sorted.length}/{artifacts.length}</span>
        {loading && <span className="loading">loading bodies…</span>}
      </div>
      <table>
        <thead>
          <tr>
            <th {...header("ts", "When")} />
            <th {...header("agent", "Agent")} />
            <th {...header("stage" as keyof ArtifactMeta, "Stage")} />
            <th {...header("kind", "Kind")} />
            <th {...header("title", "Title")} />
            <th {...header("id", "ID")} />
          </tr>
        </thead>
        <tbody>
          {sorted.map((a) => {
            const stage = stageOf(a);
            return (
              <tr
                key={a.id}
                onClick={() => open({ kind: "artifact", artifactId: a.id })}
                className="clickable"
                data-testid={`report-row-${a.id}`}
                data-stage={stage}
              >
                <td>{a.ts ? new Date(a.ts).toUTCString().slice(5, 22) : "—"}</td>
                <td style={agentInkStyle(a.agent)}>{a.agent || "—"}</td>
                <td>
                  <span className={`chip ${STAGE_CLASS[stage] || "stage-final"}`}>
                    {stage.replace("_", " ")}
                  </span>
                </td>
                <td>{a.kind || "—"}</td>
                <td>{a.title}</td>
                <td className="id">{a.id.slice(0, 8)}</td>
              </tr>
            );
          })}
          {sorted.length === 0 && (
            <tr>
              <td colSpan={6} style={{ textAlign: "center", color: "var(--ink-mute)" }}>
                {q
                  ? `── no reports match "${q}" ──`
                  : showAllStages
                    ? "── no reports yet ──"
                    : "── no final reports yet — toggle `all stages` to see drafts ──"}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

// Track 7 D6 — DSPy compile panel. Sits at the top of the Reports tab
// because that's where the operator already looks for prompt-versioned
// output. Shows current prod/staging alias chips per registered agent
// and a `compile` action that kicks off `/api/dspy/optimize/<slug>`.
// Polls the job until terminal so the operator sees the new staging
// version land without a refresh.
function DspyCompilePanel() {
  const [agents, setAgents] = useState<DspyAgent[] | null>(null);
  const [jobByAgent, setJobByAgent] = useState<Record<string, DspyJob>>({});
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listDspyAgents()
      .then((list) => {
        if (!cancelled) setAgents(list);
      })
      .catch((e) => {
        if (!cancelled) setError(e?.message || String(e));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Refresh the agent list (with new aliases) when any tracked job
  // finishes. The job summary carries the freshly-bumped aliases but
  // re-fetching also catches manual `aliases.json` edits.
  const reloadAgents = async () => {
    try {
      const list = await listDspyAgents();
      setAgents(list);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  const onCompile = async (slug: string, autoPromote: boolean) => {
    setError(null);
    try {
      const { job_id } = await compileDspyAgent(slug, autoPromote);
      // Begin polling. Each tick refreshes the job; on terminal status
      // we reload the agent list so the alias chips re-paint.
      const tick = async () => {
        try {
          const job = await getDspyJob(job_id);
          setJobByAgent((m) => ({ ...m, [slug]: job }));
          if (job.status === "running") {
            setTimeout(tick, 2000);
          } else {
            await reloadAgents();
          }
        } catch (e: any) {
          setError(e?.message || String(e));
        }
      };
      setTimeout(tick, 500);
    } catch (e: any) {
      setError(e?.message || String(e));
    }
  };

  if (!agents || agents.length === 0) return null;

  return (
    <div className="dspy-panel" data-testid="dspy-panel">
      <div className="dspy-header">
        <span className="prompt">/</span>
        <span className="dspy-title">DSPy prompt registry</span>
        {error && <span className="dspy-error">! {error}</span>}
      </div>
      <table className="dspy-table">
        <thead>
          <tr>
            <th>Agent</th>
            <th>prod</th>
            <th>staging</th>
            <th>compile</th>
            <th>last job</th>
          </tr>
        </thead>
        <tbody>
          {agents.map((a) => {
            const job = jobByAgent[a.slug];
            const running = job?.status === "running";
            return (
              <tr key={a.slug} data-testid={`dspy-row-${a.slug}`}>
                <td>{a.slug}</td>
                <td>
                  <span className="chip stage-final">{a.prod || "—"}</span>
                </td>
                <td>
                  <span className="chip stage-draft">{a.staging || "—"}</span>
                </td>
                <td>
                  <button
                    onClick={() => onCompile(a.slug, false)}
                    disabled={running}
                    data-testid={`dspy-compile-${a.slug}`}
                  >
                    {running ? "compiling…" : "compile → staging"}
                  </button>
                  <button
                    onClick={() => onCompile(a.slug, true)}
                    disabled={running}
                    title="Compile, then run the eval gate; flip prod only on win"
                    data-testid={`dspy-compile-promote-${a.slug}`}
                  >
                    {running ? "…" : "compile + auto-promote"}
                  </button>
                </td>
                <td className="dspy-job">
                  {job ? (
                    <span className={`chip stage-${job.status === "ok" ? "final" : job.status === "error" ? "critique" : "draft"}`}>
                      {job.status}
                      {job.summary?.version ? ` · ${job.summary.version}` : ""}
                      {job.summary?.promoted ? " · promoted" : ""}
                    </span>
                  ) : (
                    <span className="dspy-job-none">—</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
