import { useEffect, useMemo, useState } from "react";
import {
  WikiPage,
  deprecateWikiPage,
  listWikiPages,
  pinWikiPage,
  publishWikiPage,
  searchWikiPages,
} from "../../lib/api";
import { agentInkStyle } from "../../lib/agentInk";
import { renderSafeMarkdown } from "../../lib/safeMarkdown";
import "./WikiTab.css";

/**
 * Track 5 W5 — agentic wiki cockpit surface.
 *
 * Two-pane layout:
 *  - left: search + filter (status: published / draft / all) + page list
 *  - right: full markdown body + actions (publish / pin / deprecate)
 *
 * Polls the page list every 5s so a Curator-driven new draft surfaces
 * automatically. The right pane only re-fetches on selection change.
 */
type StatusFilter = "published" | "draft" | "deprecated" | "all";

const STATUS_CHIP: Record<string, string> = {
  draft: "stage-draft",
  published: "stage-final",
  deprecated: "stage-revision",
};

export default function WikiTab() {
  const [pages, setPages] = useState<WikiPage[]>([]);
  const [q, setQ] = useState("");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("published");
  const [selected, setSelected] = useState<WikiPage | null>(null);
  const [error, setError] = useState<string>("");

  const refresh = useMemo(
    () => async () => {
      try {
        const list = q.trim()
          ? await searchWikiPages(q.trim(), 50)
          : await listWikiPages({
              status: statusFilter === "all" ? null : statusFilter,
              limit: 50,
            });
        setPages(list);
        // Keep current selection if still in the list, otherwise pick the first.
        setSelected((cur) =>
          cur && list.some((p) => p.slug === cur.slug) ? cur : list[0] ?? null,
        );
        setError("");
      } catch (e: any) {
        setError(e?.message ?? "wiki fetch failed");
      }
    },
    [q, statusFilter],
  );

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 5000);
    return () => clearInterval(id);
  }, [refresh]);

  const onPublish = async () => {
    if (!selected) return;
    const updated = await publishWikiPage(selected.slug);
    setSelected(updated);
    refresh();
  };
  const onDeprecate = async () => {
    if (!selected) return;
    const updated = await deprecateWikiPage(selected.slug);
    setSelected(updated);
    refresh();
  };
  const onPinToggle = async () => {
    if (!selected) return;
    const updated = await pinWikiPage(selected.slug, !selected.pinned);
    setSelected(updated);
    refresh();
  };

  return (
    <div className="wiki-tab" data-testid="tab-wiki">
      <header className="wiki-toolbar">
        <span className="prompt">/</span>
        <input
          type="text"
          placeholder="search wiki — slug, title, body…"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          data-testid="wiki-search-input"
        />
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value as StatusFilter)}
          data-testid="wiki-status-filter"
        >
          <option value="published">published</option>
          <option value="draft">draft</option>
          <option value="deprecated">deprecated</option>
          <option value="all">all</option>
        </select>
        <span className="count">{pages.length}</span>
      </header>

      <div className="wiki-body">
        <aside className="wiki-list" data-testid="wiki-list">
          {error && <div className="wiki-empty err">── {error} ──</div>}
          {!error && pages.length === 0 && (
            <div className="wiki-empty">── no pages match ──</div>
          )}
          {pages.map((p) => (
            <button
              key={p.slug}
              className={`wiki-list-item ${selected?.slug === p.slug ? "active" : ""}`}
              onClick={() => setSelected(p)}
              data-testid={`wiki-list-item-${p.slug}`}
            >
              <div className="row1">
                <span className="slug">{p.slug}</span>
                {p.pinned && <span className="pin">★</span>}
              </div>
              <div className="row2">
                <span className="title">{p.title}</span>
              </div>
              <div className="row3">
                <span className="agent" style={agentInkStyle(p.owner_agent)}>{p.owner_agent}</span>
                <span className={`chip ${STATUS_CHIP[p.status] ?? "stage-draft"}`}>{p.status}</span>
                <span className="ver">v{p.version}</span>
              </div>
            </button>
          ))}
        </aside>

        <section className="wiki-detail" data-testid="wiki-detail">
          {!selected ? (
            <div className="wiki-empty">── pick a page ──</div>
          ) : (
            <>
              <header className="wiki-detail-header">
                <span className="slug" data-testid="wiki-detail-slug">{selected.slug}</span>
                <h2 className="title">{selected.title}</h2>
                <div className="meta">
                  <span className="agent" style={agentInkStyle(selected.owner_agent)}>
                    {selected.owner_agent}
                  </span>
                  <span className={`chip ${STATUS_CHIP[selected.status] ?? "stage-draft"}`}>
                    {selected.status}
                  </span>
                  <span className="ver">v{selected.version}</span>
                  <span className="ts">
                    {selected.updated_ts ? new Date(selected.updated_ts).toUTCString().slice(5, 22) : "—"}
                  </span>
                </div>
              </header>
              <div
                className="wiki-detail-body md"
                data-testid="wiki-detail-body"
                dangerouslySetInnerHTML={{ __html: renderSafeMarkdown(selected.body_md) }}
              />
              {selected.refs.length > 0 && (
                <footer className="wiki-detail-refs">
                  <strong>refs:</strong>
                  <ul>
                    {selected.refs.map((r) => (
                      <li key={r}><code>{r}</code></li>
                    ))}
                  </ul>
                </footer>
              )}
              <div className="wiki-detail-actions">
                {selected.status === "draft" && (
                  <button
                    className="primary"
                    onClick={onPublish}
                    data-testid="wiki-publish"
                  >
                    publish
                  </button>
                )}
                {selected.status !== "deprecated" && (
                  <button onClick={onDeprecate} data-testid="wiki-deprecate">
                    deprecate
                  </button>
                )}
                <button onClick={onPinToggle} data-testid="wiki-pin-toggle">
                  {selected.pinned ? "unpin ★" : "pin ☆"}
                </button>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
