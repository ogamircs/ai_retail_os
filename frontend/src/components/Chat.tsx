import { useState, useRef, useEffect, useMemo } from "react";
import { chatStream, AgentEvent } from "../lib/api";
import { agentInkStyle } from "../lib/agentInk";
import "./Chat.css";

type ChatTurn = { role: "user" | "assistant"; text: string; events?: AgentEvent[]; ts: string };

const SUGGESTIONS = [
  "We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.",
  "Which category should Marketing push this week, and why?",
  "Did the category push work? Measure lift, ROI, margin impact, fulfillment cost, and remaining risks.",
];

interface Props {
  onEvent: () => void;
}

function nowHm(): string {
  return new Date().toISOString().slice(11, 16);
}

export default function Chat({ onEvent }: Props) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [showTrace, setShowTrace] = useState(false);
  const [history, setHistory] = useState<string[]>([]);
  const [historyIdx, setHistoryIdx] = useState<number | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 99999, behavior: "smooth" });
  }, [turns]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        inputRef.current?.focus();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const traceEvents = useMemo(
    () =>
      turns.flatMap((t) => t.events ?? []).filter(
        (e) =>
          e.kind === "tool_call" ||
          e.kind === "tool_result" ||
          e.kind === "agent_start" ||
          e.kind === "agent_end",
      ),
    [turns],
  );

  const send = async () => {
    const msg = input.trim();
    if (!msg || streaming) return;
    setInput("");
    setHistory((h) => [...h.slice(-19), msg]);
    setHistoryIdx(null);
    setStreaming(true);
    setTurns((t) => [
      ...t,
      { role: "user", text: msg, ts: nowHm() },
      { role: "assistant", text: "", events: [], ts: nowHm() },
    ]);

    await chatStream(
      msg,
      (ev) => {
        onEvent();
        setTurns((t) => {
          const copy = [...t];
          const last = copy[copy.length - 1];
          if (last.role !== "assistant") return copy;
          const events = [...(last.events || []), ev];
          let text = last.text;
          if (ev.kind === "agent_end" && ev.agent === "Chief of Staff") {
            text = ev.data?.text || text;
          } else if (ev.kind === "text" && ev.agent === "Chief of Staff" && !text) {
            text = ev.data?.text || "";
          }
          copy[copy.length - 1] = { ...last, text, events };
          return copy;
        });
      },
      () => setStreaming(false),
      (err) => {
        setTurns((t) => {
          const copy = [...t];
          const last = copy[copy.length - 1];
          if (last?.role === "assistant") copy[copy.length - 1] = { ...last, text: `error: ${err}` };
          return copy;
        });
        setStreaming(false);
      },
    );
  };

  const onKey = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
      return;
    }
    if (e.key === "ArrowUp" && (input === "" || historyIdx !== null)) {
      e.preventDefault();
      if (history.length === 0) return;
      const next = historyIdx === null ? history.length - 1 : Math.max(0, historyIdx - 1);
      setHistoryIdx(next);
      setInput(history[next]);
    } else if (e.key === "ArrowDown" && historyIdx !== null) {
      e.preventDefault();
      const next = historyIdx + 1;
      if (next >= history.length) {
        setHistoryIdx(null);
        setInput("");
      } else {
        setHistoryIdx(next);
        setInput(history[next]);
      }
    }
  };

  return (
    <section className="chat" data-testid="chat">
      <header className="chat-header">
        <span className="label">Chief of Staff</span>
        <span className="spacer" />
        <button
          className={`trace-toggle ${showTrace ? "on" : ""}`}
          onClick={() => setShowTrace((v) => !v)}
          data-testid="trace-toggle"
        >
          [ trace {showTrace ? "▾" : "▸"} ]
        </button>
      </header>
      <div className={`chat-body ${showTrace ? "split" : ""}`}>
        <div className="chat-thread" ref={scrollRef}>
          {turns.length === 0 && (
            <div className="chat-empty">
              <div className="label">try</div>
              <ul>
                {SUGGESTIONS.map((s) => (
                  <li key={s} onClick={() => setInput(s)} title={s}>
                    {s.length > 90 ? s.slice(0, 90) + "…" : s}
                  </li>
                ))}
              </ul>
            </div>
          )}
          {turns.map((t, i) => (
            <div key={i} className={`turn turn-${t.role}`}>
              {t.role === "user" ? (
                <div className="user-line">&gt; {t.text}</div>
              ) : (
                <div className="cos-line">
                  <span className="ts">[{t.ts}]</span>
                  <span className="who" style={agentInkStyle("Chief of Staff")}>
                    CHIEF
                  </span>
                  <span className="text">
                    {t.text || (streaming && i === turns.length - 1 ? "working…" : "")}
                  </span>
                </div>
              )}
            </div>
          ))}
        </div>
        {showTrace && (
          <aside className="chat-trace" data-testid="chat-trace">
            <div className="label">agent trace</div>
            {traceEvents.length === 0 && <div className="empty">── no trace yet ──</div>}
            {traceEvents.map((e, i) => (
              <div key={i} className="trace-row">
                <span className="who" style={agentInkStyle(e.agent)}>
                  {e.agent}
                </span>
                <span className="kind">{e.kind}</span>
                {e.kind === "tool_call" && <span className="detail">→ {e.data?.tool}</span>}
                {e.kind === "tool_result" && e.data?.result?.artifact_id && (
                  <span className="detail">→ artifact {e.data.result.artifact_id}</span>
                )}
                {e.kind === "agent_end" && e.data?.note && (
                  <span className={e.data?.incomplete ? "detail warn" : "detail"}>
                    ⚠ {e.data.note}
                  </span>
                )}
              </div>
            ))}
          </aside>
        )}
      </div>
      <footer className="chat-input">
        <span className="prompt">&gt;</span>
        <textarea
          ref={inputRef}
          value={input}
          onChange={(e) => {
            setInput(e.target.value);
            setHistoryIdx(null);
          }}
          onKeyDown={onKey}
          placeholder="ask the chief of staff…"
          disabled={streaming}
          rows={2}
          data-testid="chat-input"
        />
      </footer>
    </section>
  );
}
