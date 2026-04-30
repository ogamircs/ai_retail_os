import { useEffect, useState } from "react";
import { getConfig, setProvider, ConfigInfo } from "../lib/api";

export default function AgentBadge() {
  const [cfg, setCfg] = useState<ConfigInfo | null>(null);

  useEffect(() => {
    getConfig().then(setCfg).catch(() => {});
  }, []);

  const change = async (p: string) => {
    const c = await setProvider(p);
    setCfg(c);
  };

  if (!cfg) return null;
  return (
    <div className="agent-badge">
      <span className="label">Provider</span>
      <select value={cfg.provider} onChange={(e) => change(e.target.value)}>
        <option value="anthropic">Anthropic</option>
        <option value="openai">OpenAI</option>
        <option value="google">Google</option>
      </select>
      <span className="model">{cfg.model}</span>
      <span className={`key-status ${cfg.has_key ? "ok" : "missing"}`}>
        {cfg.has_key ? "✓ key" : "no key"}
      </span>
    </div>
  );
}
