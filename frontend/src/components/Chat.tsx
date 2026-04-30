import { useState, useRef, useEffect } from "react";
import { chatStream, AgentEvent } from "../lib/api";

type ChatTurn = {
  role: "user" | "assistant";
  text: string;
  events?: AgentEvent[];
};

interface Props {
  onEvent: () => void; // ping to refresh side panels
}

export default function Chat({ onEvent }: Props) {
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [input, setInput] = useState("");
  const [streaming, setStreaming] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 99999, behavior: "smooth" });
  }, [turns]);

  const send = async () => {
    const msg = input.trim();
    if (!msg || streaming) return;
    setInput("");
    setStreaming(true);
    setTurns((t) => [
      ...t,
      { role: "user", text: msg },
      { role: "assistant", text: "", events: [] },
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
          // Use the final text from CoS agent_end event
          if (ev.kind === "agent_end" && ev.agent === "Chief of Staff") {
            text = ev.data?.text || text;
          } else if (
            ev.kind === "text" &&
            ev.agent === "Chief of Staff" &&
            !text
          ) {
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
          if (last?.role === "assistant") {
            copy[copy.length - 1] = { ...last, text: `Error: ${err}` };
          }
          return copy;
        });
        setStreaming(false);
      },
    );
  };

  return (
    <div className="chat">
      <div className="chat-scroll" ref={scrollRef}>
        {turns.length === 0 && (
          <div className="chat-empty">
            <h2>Chief of Staff</h2>
            <ul className="suggestion-list">
              <li
                onClick={() =>
                  setInput(
                    "We have excess summer inventory, uneven store demand, and a weekend heatwave. Build a marketing push for the right categories, decide markdowns, route fulfillment, rebalance stores, hold risky inbound POs, and show expected margin impact.",
                  )
                }
              >
                Build the heatwave category push.
              </li>
              <li
                onClick={() => setInput("Which category should Marketing push this week, and why?")}
              >
                Pick this week&apos;s Marketing push.
              </li>
              <li
                onClick={() => setInput("Did the category push work? Measure lift, ROI, margin impact, fulfillment cost, and remaining risks.")}
              >
                Measure the category push.
              </li>
            </ul>
          </div>
        )}
        {turns.map((t, i) => (
          <div key={i} className={`turn turn-${t.role}`}>
            <div className="turn-role">
              {t.role === "user" ? "Operator" : "Chief of Staff"}
            </div>
            <div className="turn-text">{t.text || (streaming && i === turns.length - 1 ? "Working..." : "")}</div>
            {t.events && t.events.length > 0 && (
              <details className="turn-events">
                <summary>{t.events.length} agent events</summary>
                <ul>
                  {t.events.map((e, j) => (
                    <li key={j}>
                      <span className={`badge agent-${e.agent.toLowerCase().replace(/[^a-z]/g, "-")}`}>
                        {e.agent}
                      </span>
                      <span className="kind">{e.kind}</span>
                      {e.kind === "tool_call" && (
                        <span className="tool-name">→ {e.data.tool}</span>
                      )}
                      {e.kind === "text" && (
                        <span className="event-detail">
                          {(e.data.text || "").slice(0, 100)}
                        </span>
                      )}
                      {e.kind === "tool_result" && e.data.result?.artifact_id && (
                        <span className="event-detail">
                          → artifact {e.data.result.artifact_id}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        ))}
      </div>
      <div className="chat-input">
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
          placeholder="Ask the Chief of Staff..."
          disabled={streaming}
          rows={3}
        />
        <button onClick={send} disabled={streaming || !input.trim()}>
          {streaming ? "..." : "Send"}
        </button>
      </div>
    </div>
  );
}
