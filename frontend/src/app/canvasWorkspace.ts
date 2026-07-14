import type { WorkspaceContainerId } from "./tradingWorkspace";
import type { WorkspaceWindowLayout } from "./components/WorkspaceCanvas";

export type CanvasLinkGroupId = "none" | "A" | "B" | "C";

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
  linkContexts: Record<Exclude<CanvasLinkGroupId, "none">, CanvasLinkContext>;
  version: 1;
};

export const MAIN_CANVAS_ID = "main";
export const CANVAS_REGISTRY_STORAGE_KEY = "quant-research-workbench.canvas.registry.v1";
export const CANVAS_PREVIEW_CONTEXT_STORAGE_KEY = "quant-research-workbench.canvas.preview-context.v1";
export const CANVAS_SETTINGS_STORAGE_KEY = "quant-research-workbench.canvas.container-settings.v1";
export const MAIN_CANVAS_STORAGE_KEY = "quant-research-workbench.trading-workspace.global.v1";

const DEFAULT_LINK_CONTEXTS: CanvasRegistry["linkContexts"] = {
  A: { symbol: "AAPL", timeframe: "1m" },
  B: { symbol: "MSFT", timeframe: "1m" },
  C: { symbol: "NVDA", timeframe: "5m" },
};

const DEFAULT_LINK_ASSIGNMENTS: CanvasRegistry["linkAssignments"] = {
  chart: "A",
  scanner: "A",
  news: "A",
  sec: "A",
  xbrl: "A",
  strategy: "B",
  portfolio: "B",
  orders: "B",
  fills: "B",
  journal: "B",
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
      linkAssignments: { ...DEFAULT_LINK_ASSIGNMENTS, ...(parsed.linkAssignments ?? {}) },
      linkContexts: {
        A: normalizeLinkContext(parsed.linkContexts?.A, DEFAULT_LINK_CONTEXTS.A),
        B: normalizeLinkContext(parsed.linkContexts?.B, DEFAULT_LINK_CONTEXTS.B),
        C: normalizeLinkContext(parsed.linkContexts?.C, DEFAULT_LINK_CONTEXTS.C),
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
    },
    version: 1,
  };
}

function normalizeLinkContext(value: CanvasLinkContext | undefined, fallback: CanvasLinkContext): CanvasLinkContext {
  const symbol = value?.symbol?.trim().toUpperCase();
  return {
    symbol: symbol || fallback.symbol,
    timeframe: value?.timeframe === "5m" ? "5m" : "1m",
  };
}
