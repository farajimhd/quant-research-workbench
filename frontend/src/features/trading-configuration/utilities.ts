import { readCanvasRegistry, snapshotSharedCanvasProfile } from "../../app/canvasWorkspace";
import type { FieldDefinition, HelpContent } from "./components/ConfigurationFields";
import { readableLabel, round } from "./components/ConfigurationFields";
import type { AccountSection, AssignmentSection, ParameterMap, Primitive } from "./contracts";
export function field(path: string, label: string, help: HelpContent, kind: FieldDefinition["kind"], choices?: readonly string[], unit?: string, step?: number): FieldDefinition {
  return { path, label, help, kind: choices?.length ? "choice" : kind, choices, unit, step };
}

export function flattenPrimitives(value: ParameterMap, prefix = ""): Array<{ path: string; value: Primitive }> {
  return Object.entries(value).flatMap(([key, item]) => {
    const path = prefix ? `${prefix}.${key}` : key;
    if (item && typeof item === "object" && !Array.isArray(item)) return flattenPrimitives(item as ParameterMap, path);
    if (["boolean", "number", "string"].includes(typeof item)) return [{ path, value: item as Primitive }];
    return [];
  });
}

export function setPath(source: ParameterMap, path: string, value: Primitive): ParameterMap {
  const result = deepClone(source);
  const parts = path.split(".");
  let cursor = result;
  parts.slice(0, -1).forEach((part) => {
    cursor[part] = cursor[part] && typeof cursor[part] === "object" ? cursor[part] : {};
    cursor = cursor[part] as ParameterMap;
  });
  cursor[parts.at(-1) ?? path] = value;
  return result;
}

export function deepClone<T>(value: T): T {
  return JSON.parse(JSON.stringify(value)) as T;
}

export function controlFor(value: Primitive): FieldDefinition["kind"] {
  return typeof value === "boolean" ? "boolean" : typeof value === "number" ? "number" : "text";
}

export function choicesFor(path: string): readonly string[] | undefined {
  if (path === "protection.trailing.mode") return ["qualified_support", "support_distance"];
  if (path.startsWith("lifecycle.phase_modes.")) return ["automatic", "manual"];
  if (path.endsWith(".method")) return ["structure", "volatility", "hybrid"];
  if (path.endsWith(".trigger")) return ["acceleration_slowdown", "favorable_move_pct", "volatility_multiple"];
  if (path.endsWith(".side")) return ["long", "short"];
  if (path.endsWith(".capital_request.mode")) return ["fixed_quantity", "mandate_fraction", "risk_fraction", "all_available"];
  if (path.endsWith(".partial_fill_policy")) return ["complete_remainder", "accept_partial", "cancel_remainder"];
  if (/\.exit\.rule_sets\.\d+\.action$/.test(path)) return ["close", "reduce"];
  if (path.endsWith(".entry_urgency")) return ["patient", "regular", "urgent", "very_urgent"];
  if (path.endsWith(".exit_urgency")) return ["urgent", "very_urgent"];
  return undefined;
}

export function isDirectlyEditableStrategyParameter(path: string, value: Primitive) {
  return typeof value !== "string" || Boolean(choicesFor(path));
}

export function unitFor(path: string) {
  if (path.endsWith("_bps")) return "bps";
  if (path.endsWith("_pct")) return "%";
  if (path.endsWith("_ms")) return "ms";
  if (path.includes("quantity")) return "shares";
  if (path.endsWith("_fraction")) return "fraction";
  return undefined;
}

export function stepFor(value: Primitive) { return typeof value === "number" && Number.isInteger(value) ? 1 : 0.01; }

export const STRATEGY_PARAMETER_PRESENTATION: Record<string, { help: string; label: string }> = {
  "protection.trailing.mode": {
    help: "Qualified support advances the stop only with newer qualified support. Support distance freezes the initial support-derived distance and trails it behind the favorable price high.",
    label: "Trailing mode",
  },
  "protection.stop.maximum_risk_pct": {
    help: "Maximum permitted distance between the current price and the initial stop. A value of 1.5 limits initial price risk to 1.5%; if the selected structure or volatility boundary is farther away, Strategy moves the stop inward to this cap.",
    label: "Maximum risk",
  },
  "protection.stop.method": {
    help: "Selects the boundary used for the initial stop: Structure uses the latest causal swing, Volatility uses the configured volatility distance, and Hybrid uses whichever boundary allows more room while remaining inside Maximum risk.",
    label: "Stop method",
  },
  "protection.stop.structure_buffer_bps": {
    help: "Additional distance placed beyond the latest causal swing low for a long position or swing high for a short position. One basis point is 0.01%; 8 bps adds a 0.08% buffer beyond that structure.",
    label: "Structure buffer",
  },
  "protection.stop.volatility_multiple": {
    help: "Distance from the current price expressed in units of the registered volatility value. A value of 1.25 places the volatility boundary 1.25 times that value below a long entry or above a short entry.",
    label: "Volatility distance",
  },
};

export function labelForStrategyParameter(path: string) {
  return STRATEGY_PARAMETER_PRESENTATION[path]?.label ?? readableLabel(path.split(".").at(-1) ?? path);
}

export function helpForPath(path: string) {
  return STRATEGY_PARAMETER_PRESENTATION[path]?.help ?? `${readableLabel(path)} is interpreted by the pinned strategy definition. Its accepted range and runtime use are validated before publication.`;
}
export function registryGroupLabel(value: string) {
  return readableLabel(value).replace(/\bQmd\b/g, "QMD").replace(/\bSec\b/g, "SEC");
}
export function uniqueId(base: string, existing: string[]) { let value = base; let index = 2; while (existing.includes(value)) value = `${base}-${index++}`; return value; }
export function percent(value: number) { return `${round(value * 100)}%`; }
export function accountName(section: AccountSection, id: string) { return section.bindings.find((row) => row.account_key === id)?.name ?? id; }
export function deploymentName(section: AssignmentSection, id: string) { return section.deployments.find((row) => row.run_plan_id === id)?.name ?? id; }
export function urgencyOptions() { return ["patient", "regular", "urgent", "very_urgent"].map((value) => ({ label: readableLabel(value), value })); }

export function stableStringify(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") return `{${Object.entries(value as Record<string, unknown>).sort(([left], [right]) => left.localeCompare(right)).map(([key, item]) => `${JSON.stringify(key)}:${stableStringify(item)}`).join(",")}}`;
  return JSON.stringify(value);
}

export function canvasApprovalSnapshot() {
  const profile = snapshotSharedCanvasProfile(readCanvasRegistry());
  const states = Object.values(profile.workspaceStates ?? {});
  const containerCount = states.reduce((count, state) => count + state.openIds.length, 0);
  const serialized = stableStringify(profile);
  let hash = 2166136261;
  for (let index = 0; index < serialized.length; index += 1) {
    hash ^= serialized.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return { containerCount, profile, ready: containerCount > 0, revision: `canvas-${(hash >>> 0).toString(16).padStart(8, "0")}` };
}
