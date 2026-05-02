import { useEffect, useState } from "react";
import {
  BrainPage,
  BrainStatus,
  getBrainPage,
  getBrainStatus,
  searchBrainPages,
} from "../../lib/api";
import { renderSafeMarkdown } from "../../lib/safeMarkdown";
import "./WikiTab.css";

/**
 * Track 6 G5 — GBrain cockpit surface.
 *
 * Two-pane layout, same shape as WikiTab so the operator's muscle
 * memory transfers. Left: search + page list, right: full body +
 * citations. The data source is GBrain (live or mock); the cockpit
 * never writes to the brain from this tab — ingest happens on the
 * Chief's chat-turn hook (G3) and via the operator's chat itself.
 */
const TIER_CHIP: Record<string, string> = {
  "tier-1": "stage-final",
  "tier-2": "stage-draft",
  "tier-3": "stage-revision",
};

export default function BrainTab() {
  const [status, setStatus] = useState<BrainStatus | null>(null);
  const [pages, setPages] = useState<BrainPage[]>([]);
  const [q, setQ] = useState("");
  const [selectedSlug, setSelectedSlug] = useState<string | null>(null);
  const [selected, setSelected] = useState<BrainPage | null>(null);
  const [error, setError] = useState<string>("");

  // Status poll — drives the mock/live banner + page count chip.
  useEffect(() => {
    let cancelled = false;
    const tick = async () => {
      try {
        const s = await getBrainStatus();
        if (!cancelled) setStatus(s);
      } catch {
        if (!cancelled) setStatus(null);
      }
    };
    tick();
    const id = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  // Page list — refreshes whenever the search query changes (debounced
  // by the natural keystroke cadence; no setTimeout dance — this corpus
  // is tiny in mock mode and live GBrain is a local HTTP call).
  useEffect(() => {
    let cancelled = false;
    (async () => {
      try {
        const r = await searchBrainPages(q, 30);
        if (cancelled) return;
        const results = (r.results ?? []) as BrainPage[];
        setPages(results);
        // Snap selection to the first result when the prior selection
        // is no longer in the filtered list — otherwise the right pane
        // keeps showing a page that doesn't match the current search.
        setSelectedSlug((cur) => {
          if (results.length === 0) return null;
          if (cur && results.some((p) => p.slug === cur)) return cur;
          return results[0].slug;
        });
        setError("");
      } catch (e: any) {
        if (!cancelled) setError(e?.message ?? "brain fetch failed");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [q]);

  // Right pane — fetch full body when the selected slug changes.
  useEffect(() => {
    if (!selectedSlug) {
      setSelected(null);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const page = await getBrainPage(selectedSlug);
        if (!cancelled) setSelected(page);
      } catch (e: any) {
        if (!cancelled) {
          setError(e?.message ?? "brain page fetch failed");
          setSelected(null);
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [selectedSlug]);

  return (
    <div className="wiki-tab" data-testid="tab-brain">
      <div className="wiki-pane wiki-list">
        <div className="wiki-search">
          <span className="prompt">/</span>
          <input
            type="text"
            placeholder="search brain — pages, citations, code refs…"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            data-testid="brain-search-input"
            autoFocus
          />
          <span className="count">{pages.length}</span>
        </div>
        <div className="wiki-status-bar">
          {status?.mock ? (
            <span className="chip stage-draft" title="Mock mode — no GBRAIN_BEARER set">
              mock
            </span>
          ) : status?.reachable ? (
            <span className="chip stage-final">live · {status.endpoint}</span>
          ) : status ? (
            <span className="chip stage-critique" title={status.error || ""}>
              unreachable
            </span>
          ) : (
            <span className="chip stage-draft">checking…</span>
          )}
          {error && <span className="wiki-error">! {error}</span>}
        </div>
        <ul className="wiki-page-list">
          {pages.map((p) => (
            <li
              key={p.slug}
              className={selectedSlug === p.slug ? "active" : ""}
              onClick={() => setSelectedSlug(p.slug)}
              data-testid={`brain-row-${p.slug}`}
            >
              <span className={`chip ${TIER_CHIP[p.tier || "tier-2"] || "stage-draft"}`}>
                {p.tier || "tier-2"}
              </span>
              <span className="wiki-page-title">{p.title}</span>
              <span className="wiki-page-slug">{p.slug}</span>
            </li>
          ))}
          {pages.length === 0 && (
            <li className="wiki-empty">── no pages match ──</li>
          )}
        </ul>
      </div>
      <div className="wiki-pane wiki-detail">
        {!selected ? (
          <div className="wiki-empty">── select a page ──</div>
        ) : (
          <>
            <div className="wiki-detail-header">
              <h3>{selected.title}</h3>
              <div className="wiki-detail-meta">
                <span className="wiki-page-slug">{selected.slug}</span>
                <span className={`chip ${TIER_CHIP[selected.tier || "tier-2"] || "stage-draft"}`}>
                  {selected.tier || "tier-2"}
                </span>
                {selected.updated_ts && (
                  <span className="wiki-page-slug">{selected.updated_ts.slice(0, 19)}</span>
                )}
              </div>
            </div>
            <div
              className="wiki-detail-body"
              dangerouslySetInnerHTML={{ __html: renderSafeMarkdown(selected.body || "") }}
            />
            {selected.citations && selected.citations.length > 0 && (
              <div className="wiki-detail-footer">
                <strong>citations:</strong>
                <ul>
                  {selected.citations.map((c, i) => (
                    <li key={`${c}-${i}`}>{c}</li>
                  ))}
                </ul>
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}
