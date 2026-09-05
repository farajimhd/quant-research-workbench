import {
  MAIN_CANVAS_ID,
  canvasFocusHandoffUrl,
  readCanvasRegistry,
  readCanvasWorkspaceState,
  replayFocusCanvasUrl,
  snapshotSharedCanvasProfile,
  writeCanvasFocusHandoff,
  writeReplayCanvasFocusHandoff,
  type CanvasFocusRuntimeMode,
  type CanvasRegistry,
  type CanvasWorkspaceState,
} from "./canvasWorkspace";
import type { WorkspaceWindowLayout } from "./components/WorkspaceCanvas";
import { TRADING_WORKSPACE_LAYOUT_VERSION, type WorkspaceContainerId } from "./tradingWorkspace";
import { MAIN_CHART_DEFAULT_INDICATORS } from "../features/canvas/chartDefaults";

const CHARTS_QUOTES_FOCUS_INSTANCE_ID = "charts_quotes-focus";
const HISTORICAL_STRATEGY_REVIEW_INDICATORS = [
  ...MAIN_CHART_DEFAULT_INDICATORS,
  "indicator.execution_vwap",
] as const;

export type TickerChartsQuotesOpenOptions = {
  historicalRunMode?: "backtest" | "backtest_debug" | "replay";
  registry?: CanvasRegistry;
  replayRunId?: string;
  runtimeMode?: CanvasFocusRuntimeMode;
  workspaceState?: CanvasWorkspaceState | null;
};

export type TickerChartsQuotesOpenResult = "invalid-ticker" | "opened";

export function openTickerChartsQuotes(
  tickerValue: string,
  options: TickerChartsQuotesOpenOptions = {},
): TickerChartsQuotesOpenResult {
  const symbol = normalizeTicker(tickerValue);
  if (!symbol) return "invalid-ticker";
  const registry = options.registry ?? readCanvasRegistry();
  const workspaceState = options.workspaceState ?? readCanvasWorkspaceState(MAIN_CANVAS_ID) ?? registry.defaultState ?? null;
  const { profile, state } = chartsQuotesFocusProfile(
    registry,
    workspaceState,
    symbol,
    Boolean(options.replayRunId),
  );
  const token = options.replayRunId
    ? writeReplayCanvasFocusHandoff(profile, state)
    : writeCanvasFocusHandoff(profile, state, "Charts & Quotes");
  const url = options.replayRunId
    ? replayFocusCanvasUrl(options.replayRunId, token, options.historicalRunMode)
    : canvasFocusHandoffUrl(token, options.runtimeMode);
  // Ticker drilldowns must never replace the operator's current workspace,
  // including immutable Replay and Backtest review pages. The focus handoff
  // is written before opening so the new tab can restore the exact runtime
  // scope, symbol, chart settings, and historical run identity.
  // Browsers intentionally return null for a successful noopener window, so
  // the return value cannot distinguish that secure success from blocking.
  window.open(url, "_blank", "noopener,noreferrer");
  return "opened";
}

export function normalizeTicker(value: string) {
  const symbol = String(value || "").trim().toUpperCase();
  return /^[A-Z][A-Z0-9.\-]{0,15}$/.test(symbol) ? symbol : "";
}

function chartsQuotesFocusProfile(
  registry: CanvasRegistry,
  workspaceState: CanvasWorkspaceState | null,
  symbol: string,
  historicalReview: boolean,
) {
  const sourceInstanceId = (workspaceState?.openIds ?? []).find(
    (instanceId) => workspaceContainerKind(instanceId, workspaceState) === "charts_quotes",
  ) ?? Object.keys(registry.instanceSettings).find(
    (instanceId) => instanceId === "charts_quotes" || instanceId.startsWith("charts_quotes-"),
  );
  const settings = recordValue(registry.instanceSettings[sourceInstanceId ?? "charts_quotes"]);
  const chart = recordValue(settings.chart);
  const chartsQuotes = recordValue(settings.charts_quotes);
  const daily = recordValue(chartsQuotes.daily);
  const main = recordValue(chartsQuotes.main);
  const month = recordValue(chartsQuotes.month);
  const focusedIndicators = Array.from(new Set([
    ...(historicalReview ? HISTORICAL_STRATEGY_REVIEW_INDICATORS : MAIN_CHART_DEFAULT_INDICATORS),
    ...stringArray(main.visibleIndicators),
  ]));
  const focusedSettings = {
    ...settings,
    chart: { ...chart, symbol },
    charts_quotes: {
      ...chartsQuotes,
      daily: { ...daily, symbol },
      main: { ...main, symbol, visibleIndicators: focusedIndicators },
      month: { ...month, symbol },
    },
  };
  const state: CanvasWorkspaceState = {
    groups: {},
    instances: { [CHARTS_QUOTES_FOCUS_INSTANCE_ID]: "charts_quotes" },
    layoutVersion: TRADING_WORKSPACE_LAYOUT_VERSION,
    layouts: { [CHARTS_QUOTES_FOCUS_INSTANCE_ID]: focusLayout() },
    openIds: [CHARTS_QUOTES_FOCUS_INSTANCE_ID],
  };
  const profile: CanvasRegistry = {
    ...snapshotSharedCanvasProfile(registry),
    canvases: [{ id: MAIN_CANVAS_ID, label: "Charts & Quotes" }],
    defaultState: state,
    instanceSettings: { [CHARTS_QUOTES_FOCUS_INSTANCE_ID]: focusedSettings },
    linkAssignments: { [CHARTS_QUOTES_FOCUS_INSTANCE_ID]: "none" },
    linkOwners: {},
    workspaceStates: { [MAIN_CANVAS_ID]: state },
  };
  return { profile, state };
}

export function ensureHistoricalChartsQuotesIndicators(
  profile: CanvasRegistry,
  state: CanvasWorkspaceState,
): CanvasRegistry {
  let changed = false;
  const instanceSettings = { ...profile.instanceSettings };
  for (const instanceId of state.openIds) {
    if (workspaceContainerKind(instanceId, state) !== "charts_quotes") continue;
    const settings = recordValue(instanceSettings[instanceId]);
    const chartsQuotes = recordValue(settings.charts_quotes);
    const main = recordValue(chartsQuotes.main);
    const visibleIndicators = Array.from(new Set([
      ...HISTORICAL_STRATEGY_REVIEW_INDICATORS,
      ...stringArray(main.visibleIndicators),
    ]));
    const currentIndicators = stringArray(main.visibleIndicators);
    if (
      visibleIndicators.length === currentIndicators.length
      && visibleIndicators.every((indicator, index) => indicator === currentIndicators[index])
    ) continue;
    changed = true;
    instanceSettings[instanceId] = {
      ...settings,
      charts_quotes: {
        ...chartsQuotes,
        main: { ...main, visibleIndicators },
      },
    };
  }
  return changed ? { ...profile, instanceSettings } : profile;
}

function focusLayout(): WorkspaceWindowLayout {
  const scale = Number(window.localStorage.getItem("quant-research-workbench.ui-scale")) || 1;
  return {
    fullscreen: true,
    h: Math.max(320, Math.floor(window.innerHeight / scale) - 62),
    minimized: false,
    w: Math.max(680, Math.floor(window.innerWidth / scale)),
    x: 0,
    y: 0,
    z: 1,
  };
}

function workspaceContainerKind(instanceId: string, state: CanvasWorkspaceState | null): WorkspaceContainerId {
  return state?.instances?.[instanceId] ?? instanceId.replace(/-\d+$/, "") as WorkspaceContainerId;
}

function recordValue(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value) ? value as Record<string, unknown> : {};
}

function stringArray(value: unknown) {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : [];
}
