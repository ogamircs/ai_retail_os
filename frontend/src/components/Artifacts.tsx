import { useEffect, useState } from "react";
import { marked } from "marked";
import { listArtifacts, getArtifact, ArtifactMeta } from "../lib/api";

interface Props {
  refreshKey: number;
}

export default function Artifacts({ refreshKey }: Props) {
  const [items, setItems] = useState<ArtifactMeta[]>([]);
  const [open, setOpen] = useState<{ meta: ArtifactMeta; body: string } | null>(
    null,
  );

  const refresh = async () => {
    try {
      const a = await listArtifacts();
      setItems(a);
    } catch {
      /* ignore */
    }
  };

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 3000);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    refresh();
  }, [refreshKey]);

  const openArtifact = async (id: string) => {
    const art = await getArtifact(id);
    setOpen({ meta: art as ArtifactMeta, body: art.body });
  };

  return (
    <div className="panel artifacts">
      <div className="panel-header">
        <span>Artifact Store</span>
        <span className="count">{items.length}</span>
      </div>
      <div className="panel-body">
        {items.length === 0 && <div className="empty">No artifacts yet.</div>}
        {items.map((a) => (
          <div
            key={a.id}
            className="artifact-row"
            onClick={() => openArtifact(a.id)}
          >
            <div className="artifact-title">{a.title}</div>
            <div className="artifact-meta">
              <span>{a.agent}</span>
              <span>·</span>
              <span>{a.kind}</span>
              <span>·</span>
              <span>{a.id.slice(0, 6)}</span>
            </div>
          </div>
        ))}
      </div>
      {open && (
        <div className="modal-backdrop" onClick={() => setOpen(null)}>
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>{open.meta.title}</h3>
              <button onClick={() => setOpen(null)}>×</button>
            </div>
            <div className="modal-meta">
              {open.meta.agent} · {open.meta.kind} · {open.meta.id}
            </div>
            <div
              className="modal-body markdown"
              dangerouslySetInnerHTML={{ __html: marked.parse(open.body) as string }}
            />
          </div>
        </div>
      )}
    </div>
  );
}
