import { containerSupportsSymbolLink, type WorkspaceContainerId } from "./tradingWorkspace";
import type { WorkspaceWindowLayout } from "./components/WorkspaceCanvas";
import { normalizeWorkspaceGroups, type WorkspaceGroup } from "./workspaceGroups";

export type CanvasLinkGroupId = "none" | "A" | "B" | "C" | "D" | "E" | "F" | "G";
export type CanvasAssignedLinkGroupId = Exclude<CanvasLinkGroupId, "none">;

export type CanvasLinkGroupDefinition = {
  color: string;
  id: CanvasAssignedLinkGroupId;
  label: string;
};

export type CanvasLinkContext = {
  symbol: string;
};

export type CanvasChartTimeframe = "100ms" | "1s" | "5s" | "10s" | "30s" | "1m" | "5m" | "1h" | "1d" | "1mo";

export type CanvasWorkspaceState = {
  groups: Record<string, WorkspaceGroup>;
  instances: Record<string, WorkspaceContainerId>;
  layoutVersion: number;
  layouts: Record<string, WorkspaceWindowLayout>;
  openIds: string[];
};

export type CanvasRecord = {
  id: string;
  label: string;
};

export type CanvasRegistry = {
  canvases: CanvasRecord[];
  defaultState?: CanvasWorkspaceState;
  instanceSettings: Record<string, unknown>;
  linkAssignments: Partial<Record<string, CanvasLinkGroupId>>;
  linkContexts: Record<CanvasAssignedLinkGroupId, CanvasLinkContext>;
  version: 2;
};

export const MAIN_CANVAS_ID = "main";
export const NEWS_READER_CANVAS_ID = "news-reader";
export const CANVAS_REGISTRY_UPDATED_EVENT = "quant-canvas-registry-updated";
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
  A: { symbol: "AAPL" },
  B: { symbol: "MSFT" },
  C: { symbol: "NVDA" },
  D: { symbol: "TSLA" },
  E: { symbol: "AMZN" },
  F: { symbol: "META" },
  G: { symbol: "AMD" },
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
    if (!parsed || ![1, 2].includes(Number(parsed.version)) || !Array.isArray(parsed.canvases)) return defaultCanvasRegistry();
    const canvases = parsed.canvases.some((canvas) => canvas.id === MAIN_CANVAS_ID)
      ? parsed.canvases
      : [{ id: MAIN_CANVAS_ID, label: "Main" }, ...parsed.canvases];
    return {
      canvases,
      defaultState: normalizeWorkspaceState(parsed.defaultState) ?? undefined,
      instanceSettings: parsed.instanceSettings && typeof parsed.instanceSettings === "object" ? parsed.instanceSettings : {},
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
      version: 2,
    };
  } catch {
    return defaultCanvasRegistry();
  }
}

export function writeCanvasRegistry(registry: CanvasRegistry) {
  window.localStorage.setItem(CANVAS_REGISTRY_STORAGE_KEY, JSON.stringify(registry));
}

export function ensureNewsReaderCanvas(): CanvasRecord {
  const registry = readCanvasRegistry();
  const existing = registry.canvases.find((canvas) => canvas.id === NEWS_READER_CANVAS_ID);
  if (existing) return existing;
  const canvas = { id: NEWS_READER_CANVAS_ID, label: "News Reader" };
  writeCanvasRegistry({ ...registry, canvases: [...registry.canvases, canvas] });
  window.dispatchEvent(new CustomEvent(CANVAS_REGISTRY_UPDATED_EVENT));
  return canvas;
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
    return normalizeWorkspaceState(parsed);
  } catch {
    return null;
  }
}

export function writeCanvasWorkspaceState(canvasId: string, state: CanvasWorkspaceState) {
  window.localStorage.setItem(canvasWorkspaceStorageKey(canvasId), JSON.stringify(state));
}

export function focusCanvasUrl(canvasId: string, containerId?: string) {
  const url = new URL(window.location.href);
  url.searchParams.set("canvas", canvasId);
  if (containerId) url.searchParams.set("container", containerId);
  else url.searchParams.delete("container");
  url.hash = "canvas-focus";
  return url.toString();
}

export function configurationCanvasUrl() {
  const url = new URL(window.location.href);
  url.searchParams.delete("canvas");
  url.searchParams.delete("container");
  url.hash = "canvas-configuration";
  return url.toString();
}

function defaultCanvasRegistry(): CanvasRegistry {
  return {
    canvases: [{ id: MAIN_CANVAS_ID, label: "Main" }],
    instanceSettings: {},
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
    version: 2,
  };
}

export function canvasLinkGroupDefinition(group: CanvasLinkGroupId): CanvasLinkGroupDefinition | undefined {
  return group === "none" ? undefined : CANVAS_LINK_GROUPS.find((candidate) => candidate.id === group);
}

function normalizeLinkContext(value: CanvasLinkContext | undefined, fallback: CanvasLinkContext): CanvasLinkContext {
  const symbol = value?.symbol?.trim().toUpperCase();
  return { symbol: symbol || fallback.symbol };
}

function normalizeLinkAssignments(value: CanvasRegistry["linkAssignments"] | undefined): CanvasRegistry["linkAssignments"] {
  const candidates = { ...DEFAULT_LINK_ASSIGNMENTS, ...(value ?? {}) };
  const assignments: CanvasRegistry["linkAssignments"] = {};
  for (const [rawContainerId, rawGroup] of Object.entries(candidates)) {
    const containerKind = rawContainerId.split("-")[0] as WorkspaceContainerId;
    if (containerSupportsSymbolLink(containerKind) && isCanvasLinkGroupId(rawGroup)) assignments[rawContainerId] = rawGroup;
  }
  return assignments;
}

function normalizeWorkspaceState(value: CanvasWorkspaceState | undefined | null): CanvasWorkspaceState | null {
  if (!value || !Array.isArray(value.openIds) || !value.layouts) return null;
  const instances = value.instances && typeof value.instances === "object"
    ? value.instances
    : Object.fromEntries(value.openIds.map((id) => [id, id.split("-")[0] as WorkspaceContainerId]));
  return { ...value, groups: normalizeWorkspaceGroups(value.groups, value.openIds), instances };
}

function isCanvasLinkGroupId(value: unknown): value is CanvasLinkGroupId {
  return value === "none" || CANVAS_LINK_GROUPS.some((group) => group.id === value);
}
