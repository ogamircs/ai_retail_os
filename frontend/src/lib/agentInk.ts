import type { CSSProperties } from "react";

const AGENT_VARS: Record<string, string> = {
  "chief of staff": "--ag-chief",
  analyst: "--ag-analyst",
  pricing: "--ag-pricing",
  "pricing & promo": "--ag-pricing",
  marketing: "--ag-marketing",
  merchandiser: "--ag-merchandiser",
  fulfillment: "--ag-fulfillment",
  replenishment: "--ag-replenishment",
  "store manager": "--ag-store",
  integration: "--ag-integration",
};

export function agentInkVar(name: string): string {
  return AGENT_VARS[name.trim().toLowerCase()] ?? "--ink";
}

export function agentInkStyle(name: string): CSSProperties {
  return { color: `var(${agentInkVar(name)})` };
}
