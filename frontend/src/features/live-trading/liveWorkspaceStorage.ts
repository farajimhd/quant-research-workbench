import type { BackendTableQuery } from "../../app/components/DataTable";
import type {
  WorkspaceCanvasTarget,
  WorkspaceWindowId,
  WorkspaceWindowLayout,
} from "../../app/components/WorkspaceCanvas";
import type { OrderRow, PositionRow, TradeRow } from "./portfolio";
import type {
  ChartWindow,
  DecisionState,
  SavedCanvasLayout,
  ScannerQueryGroup,
} from "./liveWorkspaceContracts";
import { buildDefaultCanvasLayout } from "./liveWorkspacePresentation";
import type { TradingSession } from "./time";

type LiveStorageKeys = {
  chartVisibility: string;
  layout: string;
  namedLayouts: string;
  scannerQuery: string;
  session: string;
  setup: string;
  sharedState: string;
};

type LiveWorkspaceStorageConfig = {
  defaultScannerQueryGroups: ScannerQueryGroup[];
  normalizeScannerQuery: (query: BackendTableQuery | null) => BackendTableQuery | null;
  prefix: string;
};

type LiveCanvasState = {
  chartWindows: ChartWindow[];
  layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>;
  windows: WorkspaceWindowId[];
};

const LIVE_LAYOUT_VERSION = 4;

export function createLiveWorkspaceStorage({
  defaultScannerQueryGroups,
  normalizeScannerQuery,
  prefix,
}: LiveWorkspaceStorageConfig) {
  const keys: LiveStorageKeys = {
    chartVisibility: `${prefix}.chart-visibility.v1`,
    layout: `${prefix}.layout`,
    namedLayouts: `${prefix}.named-layouts`,
    scannerQuery: `${prefix}.scanner-query.v2`,
    session: `${prefix}.session`,
    setup: `${prefix}.scanner-queries.v2`,
    sharedState: `${prefix}.shared-state`,
  };

  function readStoredSession(): TradingSession | null {
    try {
      const value = JSON.parse(window.localStorage.getItem(keys.session) || "null");
      return value?.sessionDate ? value : null;
    } catch {
      return null;
    }
  }

  function canvasStorageKey(canvasId: string) {
    return `${keys.layout}.${canvasId}`;
  }

  function canvasTransferKey(canvasId: string) {
    return `${keys.layout}.transfer.${canvasId}`;
  }

  function writeCanvasState(canvasId: string, state: LiveCanvasState) {
    window.localStorage.setItem(canvasStorageKey(canvasId), JSON.stringify({ ...state, layoutVersion: LIVE_LAYOUT_VERSION }));
  }

  function readCanvasLayoutState(canvasId: string): LiveCanvasState {
    const defaults = buildDefaultCanvasLayout(canvasId !== "main");
    try {
      const parsed = JSON.parse(window.localStorage.getItem(canvasStorageKey(canvasId)) || "null") as Partial<LiveCanvasState & { layoutVersion: number }> | null;
      if (!parsed || parsed.layoutVersion !== LIVE_LAYOUT_VERSION) return defaults;
      return {
        chartWindows: Array.isArray(parsed.chartWindows) ? parsed.chartWindows : defaults.chartWindows,
        layouts: { ...defaults.layouts, ...(parsed.layouts ?? {}) },
        windows: Array.isArray(parsed.windows) ? parsed.windows : defaults.windows,
      };
    } catch {
      return defaults;
    }
  }

  function listKnownLiveCanvases(currentCanvasId: string): WorkspaceCanvasTarget[] {
    const colors = ["#2563eb", "#16a34a", "#f97316", "#9333ea", "#0891b2", "#dc2626", "#4f46e5"];
    try {
      const canvasIds = new Set<string>(["main", currentCanvasId]);
      const storagePrefix = `${keys.layout}.`;
      for (let index = 0; index < window.localStorage.length; index += 1) {
        const key = window.localStorage.key(index);
        if (!key?.startsWith(storagePrefix)) continue;
        const suffix = key.slice(storagePrefix.length);
        if (!suffix) continue;
        canvasIds.add(suffix.startsWith("transfer.") ? suffix.slice("transfer.".length) : suffix);
      }
      return Array.from(canvasIds)
        .sort((left, right) => (left === "main" ? -1 : right === "main" ? 1 : left.localeCompare(right)))
        .map((id, index) => ({
          color: colors[index % colors.length],
          id,
          isCurrent: id === currentCanvasId,
          label: id === "main" ? "Main" : `Canvas ${index}`,
        }));
    } catch {
      return [{ color: colors[0], id: currentCanvasId, isCurrent: true, label: currentCanvasId === "main" ? "Main" : "Canvas 1" }];
    }
  }

  function readStoredCanvas(canvasId: string, isChildCanvas: boolean): LiveCanvasState {
    const defaults = buildDefaultCanvasLayout(isChildCanvas);
    const transfer = readCanvasTransfer(canvasId);
    if (transfer) {
      const chartWindows = transfer.chartWindows.filter((chart) => chart.id === transfer.windowId);
      return {
        chartWindows,
        layouts: { ...defaults.layouts, [transfer.windowId]: transfer.layout ?? defaults.layouts.chart },
        windows: [transfer.windowId],
      };
    }
    try {
      const parsed = JSON.parse(window.localStorage.getItem(canvasStorageKey(canvasId)) || "null") as Partial<LiveCanvasState & { layoutVersion: number }> | null;
      if (!parsed || parsed.layoutVersion !== LIVE_LAYOUT_VERSION) return defaults;
      return {
        chartWindows: Array.isArray(parsed.chartWindows) ? parsed.chartWindows : defaults.chartWindows,
        layouts: { ...defaults.layouts, ...(parsed.layouts ?? {}) },
        windows: Array.isArray(parsed.windows) ? parsed.windows : defaults.windows,
      };
    } catch {
      return defaults;
    }
  }

  function readCanvasTransfer(canvasId: string): { chartWindows: ChartWindow[]; layout?: WorkspaceWindowLayout; windowId: WorkspaceWindowId } | null {
    try {
      const key = canvasTransferKey(canvasId);
      const parsed = JSON.parse(window.localStorage.getItem(key) || "null");
      window.localStorage.removeItem(key);
      return parsed?.windowId ? parsed : null;
    } catch {
      return null;
    }
  }

  function readSavedCanvasLayouts(): SavedCanvasLayout[] {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(keys.namedLayouts) || "[]");
      return Array.isArray(parsed) ? parsed.filter((layout) => layout?.layoutVersion === LIVE_LAYOUT_VERSION) : [];
    } catch {
      return [];
    }
  }

  function readSharedTradingState(): { decisions: Record<string, DecisionState>; orders: OrderRow[]; positions: PositionRow[]; trades: TradeRow[] } {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(keys.sharedState) || "null");
      return {
        decisions: parsed?.decisions ?? {},
        orders: Array.isArray(parsed?.orders) ? parsed.orders : [],
        positions: Array.isArray(parsed?.positions) ? parsed.positions : [],
        trades: Array.isArray(parsed?.trades) ? parsed.trades : [],
      };
    } catch {
      return { decisions: {}, orders: [], positions: [], trades: [] };
    }
  }

  function readStoredScannerQueryGroups(): ScannerQueryGroup[] {
    try {
      const defaultGroupById = new Map(defaultScannerQueryGroups.map((group) => [group.id, group]));
      const parsed = JSON.parse(window.localStorage.getItem(keys.setup) || "[]");
      return Array.isArray(parsed) && parsed.length
        ? parsed
            .filter((item): item is ScannerQueryGroup => Boolean(item?.id && item?.name && item?.query?.conditions))
            .map((item) => defaultGroupById.get(item.id) ?? { ...item, query: normalizeScannerQuery(item.query) ?? item.query })
        : defaultScannerQueryGroups;
    } catch {
      return defaultScannerQueryGroups;
    }
  }

  function readStoredScannerQuery(): BackendTableQuery | null {
    try {
      const storedName = readStoredScannerQueryName();
      const defaultGroup = defaultScannerQueryGroups.find((group) => group.name === storedName);
      if (defaultGroup) return defaultGroup.query;
      const parsed = JSON.parse(window.localStorage.getItem(keys.scannerQuery) || "null");
      return parsed?.conditions ? parsed : null;
    } catch {
      return null;
    }
  }

  function readStoredScannerQueryName() {
    try {
      return window.localStorage.getItem(`${keys.scannerQuery}.name`) || "";
    } catch {
      return "";
    }
  }

  function readStoredLiveChartVisibility() {
    try {
      const parsed = JSON.parse(window.localStorage.getItem(keys.chartVisibility) || "null") as Partial<{ day: boolean; fiveMinute: boolean }> | null;
      return {
        day: typeof parsed?.day === "boolean" ? parsed.day : true,
        fiveMinute: typeof parsed?.fiveMinute === "boolean" ? parsed.fiveMinute : true,
      };
    } catch {
      return { day: true, fiveMinute: true };
    }
  }

  function stableScannerQueryId(name: string) {
    return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "") || `query-${Date.now()}`;
  }

  return {
    canvasStorageKey,
    canvasTransferKey,
    keys,
    layoutVersion: LIVE_LAYOUT_VERSION,
    listKnownLiveCanvases,
    readCanvasLayoutState,
    readCanvasTransfer,
    readSavedCanvasLayouts,
    readSharedTradingState,
    readStoredCanvas,
    readStoredLiveChartVisibility,
    readStoredScannerQuery,
    readStoredScannerQueryGroups,
    readStoredScannerQueryName,
    readStoredSession,
    stableScannerQueryId,
    writeCanvasState,
  };
}
