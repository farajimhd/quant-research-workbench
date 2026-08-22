import type { ChartPayload } from "../../app/components/ChartPanel";
import {
  buildSplitWorkspaceLayouts,
  buildWorkspaceWindowSummaries,
  workspaceMinHeight,
  type WorkspaceWindowId,
  type WorkspaceWindowLayout,
} from "../../app/components/WorkspaceCanvas";
import type { ChartWindow } from "./liveWorkspaceContracts";

export const CORE_WINDOW_IDS: WorkspaceWindowId[] = ["portfolio", "scanner"];

const LIVE_METRICS_DOCK_HEIGHT = 86;
const LIVE_PORTFOLIO_DEFAULT_HEIGHT = 210;

export function buildDefaultCanvasLayout(childCanvas: boolean): { chartWindows: ChartWindow[]; layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>; windows: WorkspaceWindowId[] } {
  const layouts = buildSplitWorkspaceLayouts({ bottomId: "scanner", primaryId: "chart", topHeight: LIVE_PORTFOLIO_DEFAULT_HEIGHT, topId: "portfolio", topInset: LIVE_METRICS_DOCK_HEIGHT, viewportHeight: window.innerHeight, viewportWidth: window.innerWidth });
  return { chartWindows: [], layouts, windows: childCanvas ? [] : [...CORE_WINDOW_IDS] };
}

export function buildLiveWindowSummaries(openWindows: WorkspaceWindowId[], chartWindows: ChartWindow[], layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>) {
  return buildWorkspaceWindowSummaries(openWindows, layouts, (id) => {
    const chart = chartWindows.find((item) => item.id === id);
    return { kind: chart ? "chart" : "core", title: chart?.ticker ?? coreWindowTitle(id) };
  });
}

export function liveWorkspaceMinHeight(openWindows: WorkspaceWindowId[], layouts: Record<WorkspaceWindowId, WorkspaceWindowLayout>, compact: boolean) {
  return workspaceMinHeight(openWindows, layouts, compact);
}

export function coreWindowTitle(id: WorkspaceWindowId) {
  if (id === "portfolio") return "Portfolio";
  if (id === "scanner") return "Scanner";
  return id === "chart" ? "Chart" : id;
}

export function signedMetricTone(value: number) {
  if (value > 0) return "success";
  if (value < 0) return "danger";
  return "muted";
}

export function chartOpenAtTime(payload: ChartPayload | null, timestamp: number | null) {
  if (!payload || !timestamp) return 0;
  const candle = payload.candles.find((item) => item.time === timestamp);
  return candle?.open ?? 0;
}
