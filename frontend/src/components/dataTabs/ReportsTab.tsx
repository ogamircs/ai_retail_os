import { useEffect, useMemo, useState } from "react";
import type { ArtifactMeta } from "../../lib/api";
import { getArtifact } from "../../lib/api";
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
