import { containerSupportsSymbolLink, type WorkspaceContainerId } from "./tradingWorkspace";
import type { WorkspaceWindowLayout } from "./components/WorkspaceCanvas";

export type CanvasLinkGroupId = "none" | "A" | "B" | "C" | "D" | "E" | "F" | "G";
export type CanvasAssignedLinkGroupId = Exclude<CanvasLinkGroupId, "none">;

export type CanvasLinkGroupDefinition = {
  color: string;
  id: CanvasAssignedLinkGroupId;
  label: string;
};

export type CanvasLinkContext = {
  symbol: string;
  timeframe: "1m" | "5m";
};

export type CanvasWorkspaceState = {
  layoutVersion: number;
  layouts: Record<string, WorkspaceWindowLayout>;
  openIds: WorkspaceContainerId[];
};

export type CanvasRecord = {
  id: string;
  label: string;
};

export type CanvasRegistry = {
  canvases: CanvasRecord[];
  defaultState?: CanvasWorkspaceState;
  linkAssignments: Partial<Record<WorkspaceContainerId, CanvasLinkGroupId>>;
  linkContexts: Record<CanvasAssignedLinkGroupId, CanvasLinkContext>;
  version: 1;
};

export const MAIN_CANVAS_ID = "main";
export const CANVAS_REGISTRY_STORAGE_KEY = "quant-research-workbench.canvas.registry.v1";
export const CANVAS_PREVIEW_CONTEXT_STORAGE_KEY = "quant-research-workbench.canvas.preview-context.v1";
export const CANVAS_SETTINGS_STORAGE_KEY = "quant-research-workbench.canvas.container-settings.v1";
export const MAIN_CANVAS_STORAGE_KEY = "quant-research-workbench.trading-workspace.global.v1";

export const CANVAS_LINK_GROUPS: readonly CanvasLinkGroupDefinition[] = [
  { color: "var(--canvas-link-blue)", id: "A", label: "Blue" },
  { color: "var(--canvas-link-green)", id: "B", label: "Green" },
  { color: "var(--canvas-link-amber)", id: "C", label: "Amber" },
  { color: "var(--canvas-link-violet)", id: "D", label: "Violet" },
  { color: "var(--canvas-link-rose)", id: "E", label: "Rose" },
  { color: "var(--canvas-link-cyan)", id: "F", label: "Cyan" },
  { color: "var(--canvas-link-orange)", id: "G", label: "Orange" },
];

const DEFAULT_LINK_CONTEXTS: CanvasRegistry["linkContexts"] = {
  A: { symbol: "AAPL", timeframe: "1m" },
  B: { symbol: "MSFT", timeframe: "1m" },
  C: { symbol: "NVDA", timeframe: "5m" },
  D: { symbol: "TSLA", timeframe: "1m" },
  E: { symbol: "AMZN", timeframe: "1m" },
  F: { symbol: "META", timeframe: "5m" },
  G: { symbol: "AMD", timeframe: "1m" },
};

const DEFAULT_LINK_ASSIGNMENTS: CanvasRegistry["linkAssignments"] = {
  chart: "A",
};

export function canvasWorkspaceStorageKey(canvasId: string) {
  return canvasId === MAIN_CANVAS_ID
    ? MAIN_CANVAS_STORAGE_KEY
    : `quant-research-workbench.trading-workspace.canvas.${canvasId}.v1`;
}

export function readCanvasRegistry(): CanvasRegistry {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(CANVAS_REGISTRY_STORAGE_KEY) || "null") as Partial<CanvasRegistry> | null;
    if (!parsed || parsed.version !== 1 || !Array.isArray(parsed.canvases)) return defaultCanvasRegistry();
    const canvases = parsed.canvases.some((canvas) => canvas.id === MAIN_CANVAS_ID)
      ? parsed.canvases
      : [{ id: MAIN_CANVAS_ID, label: "Main" }, ...parsed.canvases];
    return {
      canvases,
      defaultState: parsed.defaultState,
      linkAssignments: normalizeLinkAssignments(parsed.linkAssignments),
      linkContexts: {
        A: normalizeLinkContext(parsed.linkContexts?.A, DEFAULT_LINK_CONTEXTS.A),
        B: normalizeLinkContext(parsed.linkContexts?.B, DEFAULT_LINK_CONTEXTS.B),
        C: normalizeLinkContext(parsed.linkContexts?.C, DEFAULT_LINK_CONTEXTS.C),
        D: normalizeLinkContext(parsed.linkContexts?.D, DEFAULT_LINK_CONTEXTS.D),
        E: normalizeLinkContext(parsed.linkContexts?.E, DEFAULT_LINK_CONTEXTS.E),
        F: normalizeLinkContext(parsed.linkContexts?.F, DEFAULT_LINK_CONTEXTS.F),
        G: normalizeLinkContext(parsed.linkContexts?.G, DEFAULT_LINK_CONTEXTS.G),
      },
      version: 1,
    };
  } catch {
    return defaultCanvasRegistry();
  }
}

export function writeCanvasRegistry(registry: CanvasRegistry) {
  window.localStorage.setItem(CANVAS_REGISTRY_STORAGE_KEY, JSON.stringify(registry));
}

export function createCanvasRecord(registry: CanvasRegistry, label?: string): { canvas: CanvasRecord; registry: CanvasRegistry } {
  const id = `canvas-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 6)}`;
  const canvas = { id, label: label?.trim() || `Canvas ${registry.canvases.length}` };
  return { canvas, registry: { ...registry, canvases: [...registry.canvases, canvas] } };
}

export function removeCanvasRecord(registry: CanvasRegistry, canvasId: string): CanvasRegistry {
  if (canvasId === MAIN_CANVAS_ID) return registry;
  window.localStorage.removeItem(canvasWorkspaceStorageKey(canvasId));
  return { ...registry, canvases: registry.canvases.filter((canvas) => canvas.id !== canvasId) };
}

export function readCanvasWorkspaceState(canvasId: string): CanvasWorkspaceState | null {
  try {
    const parsed = JSON.parse(window.localStorage.getItem(canvasWorkspaceStorageKey(canvasId)) || "null") as CanvasWorkspaceState | null;
    return parsed && Array.isArray(parsed.openIds) && parsed.layouts ? parsed : null;
  } catch {
    return null;
  }
}

export function writeCanvasWorkspaceState(canvasId: string, state: CanvasWorkspaceState) {
  window.localStorage.setItem(canvasWorkspaceStorageKey(canvasId), JSON.stringify(state));
}

export function focusCanvasUrl(canvasId: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("canvas", canvasId);
  url.hash = "canvas-focus";
  return url.toString();
}

export function configurationCanvasUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("canvas");
  url.hash = "canvas-configuration";
  return url.toString();
}

function defaultCanvasRegistry(): CanvasRegistry {
  return {
    canvases: [{ id: MAIN_CANVAS_ID, label: "Main" }],
    linkAssignments: { ...DEFAULT_LINK_ASSIGNMENTS },
    linkContexts: {
      A: { ...DEFAULT_LINK_CONTEXTS.A },
      B: { ...DEFAULT_LINK_CONTEXTS.B },
      C: { ...DEFAULT_LINK_CONTEXTS.C },
      D: { ...DEFAULT_LINK_CONTEXTS.D },
      E: { ...DEFAULT_LINK_CONTEXTS.E },
      F: { ...DEFAULT_LINK_CONTEXTS.F },
      G: { ...DEFAULT_LINK_CONTEXTS.G },
    },
    version: 1,
  };
}

export function canvasLinkGroupDefinition(group: CanvasLinkGroupId): CanvasLinkGroupDefinition | undefined {
  return group === "none" ? undefined : CANVAS_LINK_GROUPS.find((candidate) => candidate.id === group);
}

function normalizeLinkContext(value: CanvasLinkContext | undefined, fallback: CanvasLinkContext): CanvasLinkContext {
  const symbol = value?.symbol?.trim().toUpperCase();
  return {
    symbol: symbol || fallback.symbol,
    timeframe: value?.timeframe === "5m" ? "5m" : "1m",
  };
}

function normalizeLinkAssignments(value: CanvasRegistry["linkAssignments"] | undefined): CanvasRegistry["linkAssignments"] {
  const candidates = { ...DEFAULT_LINK_ASSIGNMENTS, ...(value ?? {}) };
  const assignments: CanvasRegistry["linkAssignments"] = {};
  for (const [rawContainerId, rawGroup] of Object.entries(candidates)) {
    const containerId = rawContainerId as WorkspaceContainerId;
    if (containerSupportsSymbolLink(containerId) && isCanvasLinkGroupId(rawGroup)) assignments[containerId] = rawGroup;
  }
  return assignments;
}

function isCanvasLinkGroupId(value: unknown): value is CanvasLinkGroupId {
  return value === "none" || CANVAS_LINK_GROUPS.some((group) => group.id === value);
}
