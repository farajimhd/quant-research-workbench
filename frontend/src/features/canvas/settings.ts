import {
  CANVAS_SETTINGS_STORAGE_KEY,
  type CanvasChartTimeframe,
  type CanvasRegistry,
} from "../../app/canvasWorkspace";
import type { ChartsQuotesLayoutSettings } from "../../app/components/MarketMicrostructureContainers";
import {
  SCANNER_TIMEFRAMES,
  migrateMarketScannerSettings,
  type MarketScannerSettings,
  type ScannerCustomColumn,
  type ScannerTimeframe,
  type SignalStreamSettings,
  type WatchUniverseSettings,
} from "../../app/components/MarketScreenerContainers";
import type { CanvasChartSettings, ContainerSettings } from "./contracts";
import {
  DEFAULT_SETTINGS,
  DEFAULT_WATCHLIST_TAB_IDS,
  HISTORICAL_TIMEFRAMES,
} from "./configuration";

function readSettings(): ContainerSettings {
  try {
    const stored = JSON.parse(window.localStorage.getItem(CANVAS_SETTINGS_STORAGE_KEY) ?? "{}") as Partial<ContainerSettings>;
    return normalizeSettings(stored);
  } catch {
    return cloneDefaultSettings();
  }
}

export function normalizeSettings(stored: Partial<ContainerSettings>): ContainerSettings {
  const storedIndicators = Array.isArray(stored.chart?.visibleIndicators) ? stored.chart.visibleIndicators : DEFAULT_SETTINGS.chart.visibleIndicators;
  const obsoleteDecisionIndicators = ["indicator.microstructure_outlook", "indicator.qmd_architecture", "indicator.qmd_structural_pressure", "indicator.qmd_decision"];
  const replacedStructure = storedIndicators.some((id) => ["indicator.qmd_liquidity_levels", "indicator.market_structure_levels", "indicator.qmd_level_confluence"].includes(id));
  const replacedDecision = storedIndicators.some((id) => obsoleteDecisionIndicators.includes(id));
  const canonicalIndicators = storedIndicators.map((id) => (
    id === "indicator.execution_vwap" ? "indicator.vwap" : id
  )).filter((id) => ![
    "indicator.qmd_liquidity_levels",
    "indicator.market_structure_levels",
    "indicator.qmd_level_confluence",
    ...obsoleteDecisionIndicators,
  ].includes(id));
  if (replacedStructure && !canonicalIndicators.includes("indicator.qmd_generic_structure")) canonicalIndicators.push("indicator.qmd_generic_structure");
  if (replacedDecision && !canonicalIndicators.includes("indicator.flow_structure_composite")) canonicalIndicators.push("indicator.flow_structure_composite");
  const migratedIndicators = stored.version === DEFAULT_SETTINGS.version || canonicalIndicators.includes("indicator.macd") ? canonicalIndicators : [...canonicalIndicators, "indicator.macd"];
  const visibleIndicators = stored.version === DEFAULT_SETTINGS.version
    ? migratedIndicators
    : Array.from(new Set([...migratedIndicators, "indicator.flow_structure_composite", "strategy.presentation"]));
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored.chart?.timeframe as CanvasChartTimeframe) ? stored.chart!.timeframe! : DEFAULT_SETTINGS.chart.timeframe;
  const storedPerformance = stored.performance_journal as (Partial<ContainerSettings["performance_journal"]> & { showFees?: boolean }) | undefined;
  const storedWatchlist = stored.watchlist as Partial<WatchUniverseSettings> | undefined;
  const storedWatchlistIds = Array.isArray(storedWatchlist?.watchlistIds) ? storedWatchlist.watchlistIds : [];
  const normalizedStoredWatchlistIds = Array.from(new Set([
    ...storedWatchlistIds.filter((value): value is string => typeof value === "string" && Boolean(value.trim())),
    String(storedWatchlist?.watchlistId ?? ""),
  ].filter(Boolean)));
  const legacyDefaultWatchlist = normalizedStoredWatchlistIds.length === 0
    || (normalizedStoredWatchlistIds.length === 1 && normalizedStoredWatchlistIds[0] === "top-penny-gainers");
  const migrateLegacyWatchlist = Number(stored.version ?? 0) < 27 && legacyDefaultWatchlist;
  const watchlistIds = migrateLegacyWatchlist
    ? [...DEFAULT_WATCHLIST_TAB_IDS]
    : normalizedStoredWatchlistIds;
  return {
    version: DEFAULT_SETTINGS.version,
    chart: { ...DEFAULT_SETTINGS.chart, ...(stored.chart ?? {}), timeframe, visibleIndicators: [...visibleIndicators] },
    charts_quotes: {
      main: normalizeChartSlot(stored.charts_quotes?.main, DEFAULT_SETTINGS.charts_quotes.main),
      month: { ...normalizeChartSlot(stored.charts_quotes?.month, DEFAULT_SETTINGS.charts_quotes.month), timeframe: "1mo" },
      daily: { ...normalizeChartSlot(stored.charts_quotes?.daily, DEFAULT_SETTINGS.charts_quotes.daily), timeframe: "1d" },
      layout: normalizeChartsQuotesLayout(stored.charts_quotes?.layout),
    },
    microstructure: { limit: 1024 },
    fills: { ...DEFAULT_SETTINGS.fills, ...(stored.fills ?? {}) },
    positions: { ...DEFAULT_SETTINGS.positions, ...(stored.positions ?? {}) },
    closed_trades: { ...DEFAULT_SETTINGS.closed_trades, ...(stored.closed_trades ?? {}) },
    activity: { ...DEFAULT_SETTINGS.activity, ...(stored.activity ?? {}) },
    performance_journal: {
      ...DEFAULT_SETTINGS.performance_journal,
      ...(storedPerformance ?? {}),
      showRiskMultiple: storedPerformance?.showRiskMultiple ?? storedPerformance?.showFees ?? DEFAULT_SETTINGS.performance_journal.showRiskMultiple,
    },
    news: { ...DEFAULT_SETTINGS.news, ...(stored.news ?? {}) },
    ticker_news: { ...DEFAULT_SETTINGS.ticker_news, ...(stored.ticker_news ?? {}) },
    news_detail: {},
    orders: { ...DEFAULT_SETTINGS.orders, ...(stored.orders ?? {}) },
    portfolio: { ...DEFAULT_SETTINGS.portfolio, ...(stored.portfolio ?? {}) },
    scanner: migrateMarketScannerSettings(
      normalizeTechnicalListSettings(DEFAULT_SETTINGS.scanner, stored.scanner),
      stored.version,
    ),
    signal_stream: {
      ...normalizeTechnicalListSettings(DEFAULT_SETTINGS.signal_stream, stored.signal_stream),
      signalStreamHiddenIds: Array.isArray(stored.signal_stream?.signalStreamHiddenIds) ? stored.signal_stream.signalStreamHiddenIds.map(String) : [],
      signalStreamId: String(stored.signal_stream?.signalStreamId ?? ""),
      signalStreamIds: Array.isArray(stored.signal_stream?.signalStreamIds) ? stored.signal_stream.signalStreamIds.map(String) : [],
    },
    watchlist: {
      ...DEFAULT_SETTINGS.watchlist,
      ...(stored.watchlist ?? {}),
      columns: Number(stored.version ?? 0) < 25 ? [] : normalizeScannerColumnKeys(stored.watchlist?.columns, stored.watchlist?.customColumns),
      customColumns: normalizeScannerCustomColumns(stored.watchlist?.customColumns),
      watchlistId: migrateLegacyWatchlist ? watchlistIds[0] : watchlistIds.includes(String(storedWatchlist?.watchlistId ?? "")) ? String(storedWatchlist?.watchlistId) : watchlistIds[0] ?? "",
      watchlistIds,
    },
    strategy_activity: { ...DEFAULT_SETTINGS.strategy_activity, ...(stored.strategy_activity ?? {}) },
    sec: { ...DEFAULT_SETTINGS.sec, ...(stored.sec ?? {}) },
    ticker_sec: { ...DEFAULT_SETTINGS.ticker_sec, ...(stored.ticker_sec ?? {}) },
    sec_detail: {},
    strategy: { ...DEFAULT_SETTINGS.strategy, ...(stored.strategy ?? {}) },
    xbrl: {
      metricLimit: Number((stored.xbrl as { metricLimit?: number; limit?: number } | undefined)?.metricLimit ?? (stored.xbrl as { limit?: number } | undefined)?.limit ?? DEFAULT_SETTINGS.xbrl.metricLimit),
      showRawTags: Boolean((stored.xbrl as { showRawTags?: boolean; showPeriod?: boolean } | undefined)?.showRawTags ?? (stored.xbrl as { showPeriod?: boolean } | undefined)?.showPeriod ?? DEFAULT_SETTINGS.xbrl.showRawTags),
    },
  };
}

function normalizeChartSlot(stored: Partial<CanvasChartSettings> | undefined, defaults: CanvasChartSettings): CanvasChartSettings {
  const timeframe = HISTORICAL_TIMEFRAMES.includes(stored?.timeframe as CanvasChartTimeframe) ? stored!.timeframe! : defaults.timeframe;
  const visibleIndicators = (Array.isArray(stored?.visibleIndicators) ? stored.visibleIndicators.filter((value): value is string => typeof value === "string") : defaults.visibleIndicators)
    .map((id) => id === "indicator.execution_vwap" ? "indicator.vwap" : id);
  const required = defaults.visibleIndicators.includes("strategy.presentation") ? ["strategy.presentation"] : [];
  return { ...defaults, ...(stored ?? {}), timeframe, visibleIndicators: Array.from(new Set([...visibleIndicators, ...required])) };
}

function normalizeChartsQuotesLayout(stored: Partial<ChartsQuotesLayoutSettings> | undefined): ChartsQuotesLayoutSettings {
  const tapeColumnPercent = normalizeLayoutValue(stored?.tapeColumnPercent, 14, 38, DEFAULT_SETTINGS.charts_quotes.layout.tapeColumnPercent);
  const storedReserved = Number(stored?.reservedColumnPercent);
  const hasReservedColumn = Number.isFinite(storedReserved);
  const storedMonth = Number(stored?.monthColumnPercent);
  const migratedMonth = !hasReservedColumn && Number.isFinite(storedMonth)
    ? storedMonth * (100 - tapeColumnPercent) / 100
    : stored?.monthColumnPercent;
  const monthColumnPercent = normalizeLayoutValue(migratedMonth, 20, 68, DEFAULT_SETTINGS.charts_quotes.layout.monthColumnPercent);
  const reservedColumnPercent = normalizeLayoutValue(
    stored?.reservedColumnPercent,
    12,
    80 - monthColumnPercent,
    DEFAULT_SETTINGS.charts_quotes.layout.reservedColumnPercent,
  );
  return {
    lowerRowPercent: normalizeLayoutValue(stored?.lowerRowPercent, 22, 58, DEFAULT_SETTINGS.charts_quotes.layout.lowerRowPercent),
    monthColumnPercent,
    reservedColumnPercent,
    tapeColumnPercent,
  };
}

function normalizeLayoutValue(value: unknown, minimum: number, maximum: number, fallback: number) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? Math.max(minimum, Math.min(maximum, numeric)) : fallback;
}

function normalizeTechnicalListSettings<T extends MarketScannerSettings | SignalStreamSettings>(
  defaults: T,
  stored: Partial<T> | undefined,
): T {
  return {
    ...defaults,
    ...(stored ?? {}),
    columns: normalizeScannerColumnKeys(stored?.columns, stored?.customColumns),
    customColumns: normalizeScannerCustomColumns(stored?.customColumns),
  };
}

function normalizeScannerCustomColumns(value: unknown): ScannerCustomColumn[] {
  if (!Array.isArray(value)) return [];
  const allowedMetrics = new Set(["change_pct", "dollar_volume", "high", "low", "quote_count", "range_pct", "relative_volume", "trade_count", "volume"]);
  const unique = new Map<string, ScannerCustomColumn>();
  for (const item of value) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const metric = String(record.metric ?? "");
    if (!allowedMetrics.has(metric)) continue;
    if (["vwap", "vwap_distance_pct"].includes(metric)) {
      const anchor = record.anchor === "regular_session" ? "regular_session" : "extended_session";
      const source = record.source === "trade_price" ? "trade_price" : "hlc3";
      const key = `technical__${metric}__${anchor}__${source}`;
      unique.set(key, { anchor, key, metric: metric as ScannerCustomColumn["metric"], source });
      continue;
    }
    if (metric === "relative_volume") {
      const key = "technical__relative_volume__extended_session";
      unique.set(key, { anchor: "extended_session", key, lookbackSessions: 20, metric: "relative_volume" });
      continue;
    }
    const timeframe = String(record.timeframe ?? "");
    if (!SCANNER_TIMEFRAMES.includes(timeframe as ScannerTimeframe)) continue;
    const key = `technical__${metric}__${timeframe}`;
    unique.set(key, { key, metric: metric as ScannerCustomColumn["metric"], timeframe: timeframe as ScannerTimeframe });
  }
  return [...unique.values()];
}

function normalizeScannerColumnKeys(columns: unknown, customColumns: unknown): string[] {
  if (!Array.isArray(columns)) return [];
  const migrated = new Map<string, string>();
  if (Array.isArray(customColumns)) {
    for (const item of customColumns) {
      if (!item || typeof item !== "object") continue;
      const record = item as Record<string, unknown>;
      const oldKey = String(record.key ?? "");
      const metric = String(record.metric ?? "");
      if (!oldKey || !metric) continue;
      if (["vwap", "vwap_distance_pct"].includes(metric)) {
        const anchor = record.anchor === "regular_session" ? "regular_session" : "extended_session";
        const source = record.source === "trade_price" ? "trade_price" : "hlc3";
        migrated.set(oldKey, `technical__${metric}__${anchor}__${source}`);
      } else if (metric === "relative_volume") {
        migrated.set(oldKey, "technical__relative_volume__extended_session");
      }
    }
  }
  return columns.map(String).map((key) => migrated.get(key) ?? key).filter((key, index, values) => values.indexOf(key) === index);
}

export function cloneDefaultSettings() { return normalizeSettings(DEFAULT_SETTINGS); }
export function instanceSettings(registry: CanvasRegistry, instanceId: string) {
  const stored = registry.instanceSettings?.[instanceId] as Partial<ContainerSettings> | undefined;
  return stored ? normalizeSettings(stored) : instanceId === "chart" ? readSettings() : cloneDefaultSettings();
}
