import {
  type AutoscaleInfo,
  CandlestickSeries,
  createChart,
  createSeriesMarkers,
  HistogramSeries,
  type ISeriesMarkersPluginApi,
  LineSeries,
  LineStyle,
  type IChartApi,
  type IPrimitivePaneRenderer,
  type IPrimitivePaneView,
  type IPriceLine,
  type ISeriesApi,
  type ISeriesPrimitive,
  type LineWidth,
  type LogicalRange,
  type SeriesMarker,
  type SeriesType,
  type Time
} from "lightweight-charts";
import {
  AlignCenterHorizontal,
  CalendarDays,
  CalendarRange,
  ChartNoAxesCombined,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Eye,
  EyeOff,
  Maximize2,
  Minimize2,
  RefreshCcw,
  Settings,
  SlidersHorizontal,
  X
} from "lucide-react";
import { Component, forwardRef, type CSSProperties, type ErrorInfo, type FormEvent, type ReactNode, useEffect, useImperativeHandle, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { displayName } from "../format";
import { buildSegmentButtonClassName } from "../selectionStyles";
import { Modal } from "./Modal";
import { TickerChangeBadge, TickerIdentity, TickerLogo } from "./TickerIdentity";

type Candle = { time: number; open: number; high: number; low: number; close: number };
type ChartSeries = {
  autoscaleMax?: number;
  autoscaleMin?: number;
  autoscaleScope?: "loaded-series";
  axisTitle?: string;
  bandFillColor?: string;
  bandFillOpacity?: number;
  chartRole?: string;
  colorMode?: "confidence-sign" | "sign";
  column: string;
  displayItemId?: string;
  label: string;
  paneKey?: string;
  style: "line" | "histogram";
  color: string;
  defaultVisible?: boolean;
  legend?: boolean;
  lastValueVisible?: boolean;
  lineStyle?: "solid" | "dashed" | "dotted";
  lineWidth: number;
  opacity?: number;
  priceScaleId?: "left" | "right";
  data: Array<{ color?: string; confidence?: number; time: number; tone?: "buy" | "neutral" | "sell"; value: number }>;
};
type RendererDatum = { time: Time; [key: string]: unknown };
type RendererDataCache = { data: RendererDatum[]; styleKey: string };
const rendererDataCache = new WeakMap<object, RendererDataCache>();
type Region = { start: number; end: number; color: string; label: string };
type TradeLabelPart = { text: string; tone?: "label" | "price" | "pnlLoss" | "pnlWin" | "reason" | "separator" | "size" };
type TradeFillAnnotation = {
  label?: string;
  labelParts?: TradeLabelPart[];
  price: number;
  quantity?: number;
  side: "BUY" | "SELL";
  time: number;
};
type TradeAnnotation = {
  color: string;
  entryLabel?: string;
  entryLabelParts?: TradeLabelPart[];
  entryLabelSide?: "left" | "right";
  entryPrice: number;
  entryTime: number;
  exitLabel?: string;
  exitLabelParts?: TradeLabelPart[];
  exitLabelSide?: "left" | "right";
  exitPrice: number;
  exitTime: number;
  fills?: TradeFillAnnotation[];
  id: string;
  pnl?: number;
  selected?: boolean;
  stopPrice?: number;
  triggerPrice?: number;
};
type PriceZone = {
  annotationKind?: "band" | "bos" | "choch" | "level" | "liquidity-resistance" | "liquidity-support" | "swing-high" | "swing-low";
  axisLabelDefault?: boolean;
  borderColor?: string;
  borderOpacity?: number;
  borderStyle?: string;
  borderWidth?: number;
  color: string;
  compactLabel?: string;
  confidence?: number;
  currentLevelDistanceRank?: number;
  currentLevelSide?: "support" | "resistance";
  currentLevelStrongest?: boolean;
  defaultVisible?: boolean;
  displayItemId?: string;
  end: number;
  extendToRightEdge?: boolean;
  eventTime?: number;
  fillColor?: string;
  fillOpacity?: number;
  historicalLabelsDefault?: boolean;
  historicalTagLimitDefault?: number;
  label: string;
  latest?: boolean;
  legendLabel?: string;
  lower: number;
  maxPixelHeight?: number;
  minPixelHeight?: number;
  renderMode?: "line" | "zone";
  settingsId?: string;
  start: number;
  strength?: number;
  upper: number;
  zoneHeightMode?: string;
};
export type LiveEntryLine = {
  color: string;
  labelParts?: TradeLabelPart[];
  onClose?: () => void;
  pnl: number;
  price: number;
  quantity: number;
};
export type ChartCatalogKnowledge = {
  bearishEvidence?: string;
  bullishEvidence?: string;
  calculation?: string;
  shortDescription?: string;
  detailedDescription?: string;
  theory?: string;
  interpretation?: string;
  readingGuide?: string;
  timeframeBehavior?: string;
  caveats?: string[];
  components?: Array<{ description: string; label: string; tone?: "buy" | "info" | "neutral" | "sell" | "warning" }>;
  equations?: Array<{ markdown: string; title: string; variables: Record<string, string> }>;
};
export type ChartReference = {
  endTime?: number;
  label?: string;
  minuteOfDay?: number;
  sessionDate?: string;
  startTime?: number;
  time?: number;
};
export type ChartCatalogItem = {
  id: string;
  column?: string;
  title: string;
  category: string;
  group?: string;
  artifactGroups?: string[];
  knowledge?: ChartCatalogKnowledge;
  leakage?: Record<string, unknown>;
  presentation?: {
    chartRole?: string;
    defaultVisible?: boolean;
    pane?: string;
    selectable?: boolean;
  };
};
export type ChartDisplayItem = ChartCatalogItem & {
  artifactGroups?: string[];
  featureGroups?: string[];
  sourceColumns?: string[];
};
export type ChartLabelOption = {
  group: string;
  id: string;
  knowledge?: ChartCatalogKnowledge;
  leakage?: Record<string, unknown>;
  lookahead?: boolean;
  title: string;
};
type AnySeriesApi = ISeriesApi<SeriesType>;
type CandleSeriesDatum = Candle | { time: number };
type ChartMarker = SeriesMarker<Time> & { displayItemId?: string };
type LegendPane = "price" | "oscillator";
type NumericBounds = { max: number; min: number } | null;
type OscillatorPaneRuntime = {
  layerSignature: string;
  paneIndex: number;
  primaryKey: string;
  renderer: AnySeriesApi | null;
  seriesKeys: Set<string>;
  timelineRenderer: AnySeriesApi | null;
  timelineSignature: string;
  zeroLine: IPriceLine | null;
  zeroLineRenderer: AnySeriesApi | null;
  zeroLineSeriesKey: string;
};
type OscillatorPaneGroup = {
  key: string;
  series: ChartSeries[];
};
type PriceZoneAxisLineRuntime = {
  line: IPriceLine;
  signature: string;
};
type CanvasBox = { bottom: number; left: number; right: number; top: number };
type HorizontalSpan = { left: number; right: number; width: number };
type LegendLineStyle = "solid" | "dashed" | "dotted";
type LegendSeriesSettings = {
  currentLevelCount?: number;
  color?: string;
  historyBars?: number;
  labelFontSize?: number;
  lineStyle?: LegendLineStyle;
  lineWidth?: number;
  maxHistoricalTags?: number;
  opacity?: number;
  showConnectors?: boolean;
  showAxisLabel?: boolean;
  showHistoricalLabels?: boolean;
  showLabels?: boolean;
  showValue?: boolean;
  visible?: boolean;
};
type LegendSettingsMap = Record<string, LegendSeriesSettings>;

type PriceZonePrimitiveState = {
  candles: Candle[];
  legendSettings: LegendSettingsMap;
  zones: PriceZone[];
};

class PriceZonePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private state: PriceZonePrimitiveState = { candles: [], legendSettings: {}, zones: [] };
  private readonly rendererImpl: IPrimitivePaneRenderer = {
    draw: (target) => {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        drawPriceZonePrimitiveGeometry(
          this.chart as IChartApi,
          this.series as ISeriesApi<"Candlestick">,
          context,
          mediaSize.width,
          mediaSize.height,
          this.state.zones,
          this.state.candles,
          this.state.legendSettings,
        );
      });
    },
  };
  private readonly paneView: IPrimitivePaneView = {
    renderer: () => this.rendererImpl,
    zOrder: () => "bottom",
  };

  attached({ chart, requestUpdate, series }: Parameters<NonNullable<ISeriesPrimitive<Time>["attached"]>>[0]) {
    this.chart = chart as IChartApi;
    this.series = series as ISeriesApi<"Candlestick">;
    this.requestUpdate = requestUpdate;
  }

  detached() {
    this.chart = null;
    this.series = null;
    this.requestUpdate = null;
  }

  paneViews() {
    return [this.paneView];
  }

  setState(state: PriceZonePrimitiveState) {
    this.state = state;
    this.requestUpdate?.();
  }
}
type OscillatorThresholdSettings = {
  color: string;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  value: number;
  visible: boolean;
};
type OscillatorThresholdSettingsMap = Record<string, OscillatorThresholdSettings>;
type DaySeparatorStyle = "solid" | "dashed" | "dotted";
type ChartAppearanceSettings = {
  afterHoursColor: string;
  afterHoursOpacity: number;
  borderDownColor: string;
  borderUpColor: string;
  borderVisible: boolean;
  candleSize: number;
  daySeparatorColor: string;
  daySeparatorStyle: DaySeparatorStyle;
  daySeparatorsVisible: boolean;
  downColor: string;
  premarketColor: string;
  premarketOpacity: number;
  upColor: string;
  wickDownColor: string;
  wickUpColor: string;
  wickVisible: boolean;
};

export type ChartPayload = {
  candles: Candle[];
  volume: Array<{ time: number; value: number; color: string }>;
  overlay_series: ChartSeries[];
  oscillator_series: ChartSeries[];
  markers: ChartMarker[];
  regions: Region[];
  trade_annotations?: TradeAnnotation[];
  price_zones?: PriceZone[];
  options?: ChartOptions;
};

export type ChartOptions = {
  feature_columns: string[];
  feature_groups: string[];
  display_items?: ChartDisplayItem[];
  standard_indicators: string[];
  supervision_groups: ChartLabelOption[];
};

export type ChartPanelHandle = {
  fitFirstDay: () => void;
  fitRecent: () => void;
  toggleFullscreen: () => void;
};

type ChartPanelProps = {
  catalogColumns?: ChartCatalogItem[];
  displayItemOptions?: ChartDisplayItem[];
  emptyMessage?: string;
  errorMessage?: string;
  infoMessage?: string;
  featureOptions: string[];
  indicatorOptions: string[];
  initialFitMode?: "default" | "last_market_day" | "live_first_10" | "recent";
  labelOptions?: ChartLabelOption[];
  canLoadEarlier?: boolean;
  loadingEarlier?: boolean;
  loading?: boolean;
  normalizeTicker?: boolean;
  onPeriodChange?: (start: string, end: string) => void;
  onTickerChange: (value: string) => void;
  onTimeframeChange: (value: string) => void;
  onVisibleColumnsChange: (value: string[]) => void;
  onVisibleSupervisionGroupsChange?: (value: string[]) => void;
  onLiveEntryClose?: () => void;
  onLoadEarlier?: () => void;
  payload: ChartPayload | null;
  periodEnd?: string;
  periodMax?: string;
  periodMin?: string;
  periodStart?: string;
  reference?: ChartReference | null;
  liveEntryLine?: LiveEntryLine | null;
  daySeparatorsVisible?: boolean;
  enableFullscreen?: boolean;
  showReferenceLine?: boolean;
  showIndicatorControls?: boolean;
  showSupervisionControls?: boolean;
  settingsStorageKey?: string;
  ticker: string;
  tickerChangeAsOf?: string;
  tickerEditable?: boolean;
  tickerLogoUrl?: string;
  tickerInputWidth?: number | string;
  tickerMaxLength?: number;
  timeframe: string;
  timeframes: string[];
  visibleColumns: string[];
  visibleSupervisionGroups?: string[];
};

const defaultChartAppearanceSettings: ChartAppearanceSettings = {
  afterHoursColor: "#78B8E8",
  afterHoursOpacity: 0.16,
  borderDownColor: "#CB093F",
  borderUpColor: "#1DB914",
  borderVisible: true,
  candleSize: 40,
  daySeparatorColor: "#94A3B8",
  daySeparatorStyle: "dashed",
  daySeparatorsVisible: true,
  downColor: "#FD0E50",
  premarketColor: "#F2A65A",
  premarketOpacity: 0.16,
  upColor: "#33E42A",
  wickUpColor: "#4DC746",
  wickDownColor: "#C52A55",
  wickVisible: true
};

const LEGEND_SETTINGS_STORAGE_KEY = "quant-research-workbench.chart.legend-settings.v1";
const OSCILLATOR_THRESHOLD_STORAGE_KEY = "quant-research-workbench.chart.oscillator-thresholds.v1";
const CHART_APPEARANCE_STORAGE_KEY = "quant-research-workbench.chart.appearance-settings.v1";
const CHART_PRICE_SCALE_MIN_WIDTH = 84;

type ChartPalette = {
  background: string;
  grid: string;
  text: string;
};

const ChartPanelCore = forwardRef<ChartPanelHandle, ChartPanelProps>(({
  catalogColumns = [],
  displayItemOptions = [],
  emptyMessage = "No chart data for the selected ticker/date range/timeframe.",
  errorMessage,
  infoMessage,
  featureOptions,
  indicatorOptions,
  initialFitMode = "default",
  labelOptions = [],
  canLoadEarlier = false,
  loadingEarlier = false,
  loading = false,
  normalizeTicker = true,
  onPeriodChange,
  onTickerChange,
  onTimeframeChange,
  onVisibleColumnsChange,
  onVisibleSupervisionGroupsChange,
  onLiveEntryClose,
  onLoadEarlier,
  periodEnd,
  periodMax,
  periodMin,
  periodStart,
  payload,
  reference = null,
  liveEntryLine = null,
  daySeparatorsVisible,
  enableFullscreen = true,
  showReferenceLine = true,
  showIndicatorControls = true,
  showSupervisionControls = false,
  settingsStorageKey,
  ticker,
  tickerChangeAsOf,
  tickerEditable = true,
  tickerLogoUrl,
  tickerInputWidth,
  tickerMaxLength = 10,
  timeframe,
  timeframes,
  visibleColumns,
  visibleSupervisionGroups = []
}, ref) => {
  const priceRef = useRef<HTMLDivElement | null>(null);
  const pricePaneOverlayRef = useRef<HTMLDivElement | null>(null);
  const oscillatorPaneRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const oscillatorLayerRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const shellRef = useRef<HTMLDivElement | null>(null);
  const priceLayerRef = useRef<HTMLDivElement | null>(null);
  const referenceLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const candleMarkersRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const indicatorSeriesRef = useRef<Map<string, AnySeriesApi>>(new Map());
  const indicatorSourceRef = useRef<Map<string, ChartSeries>>(new Map());
  const indicatorBoundsRef = useRef<Map<string, NumericBounds>>(new Map());
  const oscillatorPaneRuntimesRef = useRef<Map<string, OscillatorPaneRuntime>>(new Map());
  const priceZoneAxisLinesRef = useRef<Map<string, PriceZoneAxisLineRuntime>>(new Map());
  const priceZonePrimitiveRef = useRef<PriceZonePrimitive | null>(null);
  const payloadRef = useRef<ChartPayload | null>(payload);
  const liveEntryLineRef = useRef<LiveEntryLine | null>(null);
  const referenceRef = useRef<ChartReference | null>(reference ?? null);
  const showReferenceLineRef = useRef(showReferenceLine);
  const visibleSelectionRef = useRef<Set<string>>(new Set());
  const chartSettingsRef = useRef<ChartAppearanceSettings>(defaultChartAppearanceSettings);
  const legendSettingsRef = useRef<LegendSettingsMap>({});
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const paneResizeObserverRef = useRef<ResizeObserver | null>(null);
  const initialFitTimerRef = useRef<number | null>(null);
  const overlayInteractionCleanupRef = useRef<(() => void) | null>(null);
  const overlayRedrawFrameRef = useRef<number | null>(null);
  const overlayRedrawTimerRef = useRef<number | null>(null);
  const scaleStabilizationFrameRef = useRef<number | null>(null);
  const scaleRecoveryCountRef = useRef(0);
  const regionDrawRef = useRef<((range: LogicalRange | null) => void) | null>(null);
  const canLoadEarlierRef = useRef(canLoadEarlier);
  const loadingEarlierRef = useRef(loadingEarlier);
  const onLoadEarlierRef = useRef(onLoadEarlier);
  const suppressEarlierLoadUntilRef = useRef(0);
  const fittedChartKeyRef = useRef("");
  const candleWindowRef = useRef<{ first: number; last: number } | null>(null);
  const candleBoundsRef = useRef<NumericBounds>(null);
  const normalizeTickerValue = (value: string) => (normalizeTicker ? value.toUpperCase() : value);
  const [draftTicker, setDraftTicker] = useState(normalizeTickerValue(ticker));
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const [supervisionMenuOpen, setSupervisionMenuOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const legendStorageKey = settingsStorageKey ? `${settingsStorageKey}.legend` : LEGEND_SETTINGS_STORAGE_KEY;
  const oscillatorThresholdStorageKey = settingsStorageKey ? `${settingsStorageKey}.oscillator-thresholds` : OSCILLATOR_THRESHOLD_STORAGE_KEY;
  const appearanceStorageKey = settingsStorageKey ? `${settingsStorageKey}.appearance` : CHART_APPEARANCE_STORAGE_KEY;
  const paneLayoutStorageKey = settingsStorageKey ? `${settingsStorageKey}.pane-layout-v2` : `${LEGEND_SETTINGS_STORAGE_KEY}.pane-layout-v2`;
  const [chartSettings, setChartSettings] = useState<ChartAppearanceSettings>(() => loadChartAppearanceSettings(appearanceStorageKey));
  const [legendSettings, setLegendSettings] = useState<LegendSettingsMap>(() => loadLegendSettings(legendStorageKey));
  const [oscillatorThresholdSettings, setOscillatorThresholdSettings] = useState<OscillatorThresholdSettingsMap>(() => loadOscillatorThresholdSettings(oscillatorThresholdStorageKey));
  const [paneStretchFactors, setPaneStretchFactors] = useState<Record<string, number>>(() => loadPaneStretchFactors(paneLayoutStorageKey));
  const [periodMenuOpen, setPeriodMenuOpen] = useState(false);
  const [themeSignature, setThemeSignature] = useState(() => document.documentElement.dataset.shellTheme ?? "");
  const effectiveChartSettings = useMemo(
    () =>
      typeof daySeparatorsVisible === "boolean"
        ? normalizeChartAppearanceSettings({ ...chartSettings, daySeparatorsVisible })
        : chartSettings,
    [chartSettings, daySeparatorsVisible]
  );
  chartSettingsRef.current = effectiveChartSettings;
  legendSettingsRef.current = legendSettings;
  const visibleColumnKey = visibleColumns.map((column) => column.toLowerCase()).join("|");
  const visibleSupervisionKey = visibleSupervisionGroups.map((group) => group.toLowerCase()).join("|");
  const visibleColumnLookup = new Set(visibleColumns.map((column) => column.toLowerCase()));
  const visibleSelectionLookup = new Set(visibleColumnLookup);
  visibleSupervisionGroups.forEach((group) => {
    visibleSelectionLookup.add(group.toLowerCase());
    visibleSelectionLookup.add(`supervision:${group.toLowerCase()}`);
    defaultSupervisionSelectionIds(group).forEach((selection) => {
      visibleSelectionLookup.add(selection);
      visibleSelectionLookup.add(`supervision:${selection}`);
    });
  });
  visibleSelectionRef.current = visibleSelectionLookup;
  const displayedOverlaySeries = (payload?.overlay_series ?? []).filter((series) => visibleColumnLookup.has(seriesSelectionKey(series)));
  const displayedPriceZones = (payload?.price_zones ?? []).filter((zone) => !zone.displayItemId || visibleSelectionLookup.has(zone.displayItemId.toLowerCase()));
  const displayedOscillatorSeries = (payload?.oscillator_series ?? []).filter((series) => visibleColumnLookup.has(seriesSelectionKey(series)));
  const oscillatorPaneGroups = buildOscillatorPaneGroups(displayedOscillatorSeries);
  const oscillatorPaneTotalHeight = oscillatorPaneGroups.reduce((total, group) => total + defaultOscillatorPaneHeight(group), 0);
  const nativeChartHeight: CSSProperties["height"] = fullscreen
    ? `calc(100vh - 322px + ${oscillatorPaneTotalHeight}px)`
    : 620 + oscillatorPaneTotalHeight;
  const alignLeftPriceScale = oscillatorPaneGroups.some(oscillatorGroupUsesLeftScale);
  const priceLegendItems = [
    ...buildSeriesLegendItems(displayedOverlaySeries, "price", legendSettings, displayItemOptions, catalogColumns, chartSettings),
    ...buildPriceZoneLegendItems(displayedPriceZones, legendSettings, displayItemOptions, catalogColumns, chartSettings),
  ];
  const priceIndicatorCount = new Set([
    ...displayedOverlaySeries.map((series) => seriesSelectionKey(series)),
    ...displayedPriceZones.map((zone) => String(zone.displayItemId || zone.label).toLowerCase()),
  ]).size;
  const hasChartData = Boolean(payload?.candles.length);
  const referenceKey = reference ? `${reference.time ?? ""}:${reference.startTime ?? ""}:${reference.endTime ?? ""}:${reference.sessionDate ?? ""}:${reference.minuteOfDay ?? ""}:${reference.label ?? ""}` : "";
  const liveEntryLineKey = liveEntryLine ? `${liveEntryLine.price}:${liveEntryLine.quantity}:${liveEntryLine.pnl}:${liveEntryLine.color}` : "";
  const liveEntryLineForDraw = liveEntryLine ? { ...liveEntryLine, onClose: onLiveEntryClose } : null;
  liveEntryLineRef.current = liveEntryLineForDraw;
  referenceRef.current = reference ?? null;
  showReferenceLineRef.current = showReferenceLine;
  canLoadEarlierRef.current = canLoadEarlier;
  loadingEarlierRef.current = loadingEarlier;
  onLoadEarlierRef.current = onLoadEarlier;

  const updateChartSettings = <K extends keyof ChartAppearanceSettings>(key: K, value: ChartAppearanceSettings[K]) => {
    setChartSettings((current) => {
      const next = normalizeChartAppearanceSettings({ ...current, [key]: value });
      saveChartAppearanceSettings(next, appearanceStorageKey);
      return next;
    });
  };

  const resetChartSettings = () => {
    const next = { ...defaultChartAppearanceSettings };
    saveChartAppearanceSettings(next, appearanceStorageKey);
    setChartSettings(next);
  };

  const updateLegendSettings = (key: string, patch: LegendSeriesSettings) => {
    setLegendSettings((current) => {
      const next = { ...current, [key]: { ...(current[key] ?? {}), ...patch } };
      saveLegendSettings(next, legendStorageKey);
      return next;
    });
  };

  const resetLegendSettings = (key: string) => {
    setLegendSettings((current) => {
      const next = { ...current };
      delete next[key];
      saveLegendSettings(next, legendStorageKey);
      return next;
    });
  };

  const updateOscillatorThreshold = (group: OscillatorPaneGroup, patch: Partial<OscillatorThresholdSettings>) => {
    setOscillatorThresholdSettings((current) => {
      const next = { ...current, [group.key]: { ...resolveOscillatorThresholdSettings(current[group.key], group), ...patch } };
      saveOscillatorThresholdSettings(next, oscillatorThresholdStorageKey);
      return next;
    });
  };

  const resetOscillatorThreshold = (group: OscillatorPaneGroup) => {
    setOscillatorThresholdSettings((current) => {
      const next = { ...current };
      delete next[group.key];
      saveOscillatorThresholdSettings(next, oscillatorThresholdStorageKey);
      return next;
    });
  };

  const setOscillatorPaneRef = (key: string, node: HTMLDivElement | null) => {
    if (node) {
      oscillatorPaneRefs.current.set(key, node);
    } else {
      oscillatorPaneRefs.current.delete(key);
    }
  };

  const setOscillatorLayerRef = (key: string, node: HTMLDivElement | null) => {
    if (node) {
      oscillatorLayerRefs.current.set(key, node);
    } else {
      oscillatorLayerRefs.current.delete(key);
    }
  };

  useImperativeHandle(ref, () => ({
    fitFirstDay() {
      executeViewportCommand(() => fitLatestSession(priceChartRef.current, fitCandles(payload), timeframe));
    },
    fitRecent() {
      executeViewportCommand(() => centerReferenceOrLatest(priceChartRef.current, fitCandles(payload), reference, timeframe, initialFitMode));
    },
    toggleFullscreen() {
      setFullscreen((value) => !value);
      window.setTimeout(() => resizeCharts(), 30);
    }
  }));

  useEffect(() => {
    const timer = window.setTimeout(() => resizeCharts(), 0);
    return () => window.clearTimeout(timer);
  }, [paneStretchFactors]);

  useEffect(() => {
    const release = () => finishViewportInteraction();
    window.addEventListener("pointerup", release);
    window.addEventListener("pointercancel", release);
    return () => {
      window.removeEventListener("pointerup", release);
      window.removeEventListener("pointercancel", release);
    };
  }, []);

  function suppressEarlierLoad() {
    // Programmatic fits and pane synchronization also emit visible-range events.
    // Only genuine user navigation to the left edge may request older history.
    suppressEarlierLoadUntilRef.current = Date.now() + 750;
  }

  function cancelPendingInitialFit() {
    if (initialFitTimerRef.current !== null) {
      window.clearTimeout(initialFitTimerRef.current);
      initialFitTimerRef.current = null;
    }
  }

  function claimViewportForUser(_target: EventTarget | null) {
    cancelPendingInitialFit();
  }

  function finishViewportInteraction() {
    persistNativePaneLayout();
    scheduleScaleStabilization();
    scheduleOverlayRedrawBurst();
  }

  function scheduleScaleStabilization() {
    if (scaleStabilizationFrameRef.current !== null) return;
    scaleStabilizationFrameRef.current = window.requestAnimationFrame(() => {
      scaleStabilizationFrameRef.current = null;
      stabilizeNativePaneScales();
    });
  }

  function stabilizeNativePaneScales() {
    const chart = priceChartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return;
    let recovered = stabilizeSeriesScale(candle, chart.panes()[0]?.getHeight() ?? 0, candleBoundsRef.current);
    oscillatorPaneRuntimesRef.current.forEach((runtime) => {
      const paneHeight = chart.panes()[runtime.paneIndex]?.getHeight() ?? 0;
      runtime.seriesKeys.forEach((key) => {
        const renderer = indicatorSeriesRef.current.get(key);
        if (renderer) recovered = stabilizeSeriesScale(renderer, paneHeight, indicatorBoundsRef.current.get(key) ?? null) || recovered;
      });
    });
    if (recovered && shellRef.current) {
      scaleRecoveryCountRef.current += 1;
      shellRef.current.dataset.chartScaleRecoveries = String(scaleRecoveryCountRef.current);
    }
  }

  function executeViewportCommand(command: () => void) {
    cancelPendingInitialFit();
    suppressEarlierLoad();
    command();
    window.requestAnimationFrame(scheduleOverlayRedrawBurst);
  }

  function persistNativePaneLayout() {
    window.requestAnimationFrame(() => {
      const chart = priceChartRef.current;
      if (!chart) return;
      const next: Record<string, number> = {};
      const priceFactor = chart.panes()[0]?.getStretchFactor();
      if (Number.isFinite(priceFactor) && Number(priceFactor) > 0) next.price = Number(priceFactor);
      oscillatorPaneRuntimesRef.current.forEach((runtime, key) => {
        const factor = chart.panes()[runtime.paneIndex]?.getStretchFactor();
        if (Number.isFinite(factor) && Number(factor) > 0) next[key] = Number(factor);
      });
      if (!Object.keys(next).length) return;
      savePaneStretchFactors(next, paneLayoutStorageKey);
      setPaneStretchFactors(next);
      layoutNativePaneOverlays();
    });
  }

  useEffect(() => {
    const target = document.documentElement;
    const observer = new MutationObserver(() => {
      setThemeSignature(`${target.dataset.shellTheme ?? ""}:${target.getAttribute("style") ?? ""}`);
    });
    observer.observe(target, { attributes: true, attributeFilter: ["class", "data-shell-theme", "style"] });
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    setDraftTicker(normalizeTickerValue(ticker));
  }, [normalizeTicker, ticker]);

  useEffect(() => {
    if (!chartSettingsOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".chart-settings-slot") || target?.closest("[data-chart-settings-trigger='true']")) {
        return;
      }
      setChartSettingsOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setChartSettingsOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [chartSettingsOpen]);

  useEffect(() => {
    if (!columnMenuOpen && !supervisionMenuOpen && !periodMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".chart-column-select") || target?.closest(".chart-column-menu-portal") || target?.closest(".chart-period-select")) return;
      setColumnMenuOpen(false);
      setSupervisionMenuOpen(false);
      setPeriodMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setColumnMenuOpen(false);
        setSupervisionMenuOpen(false);
        setPeriodMenuOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnMenuOpen, supervisionMenuOpen, periodMenuOpen]);

  useEffect(() => {
    indicatorSeriesRef.current.forEach((renderer, key) => {
      const source = indicatorSourceRef.current.get(key);
      if (!source) return;
      const settings = resolveLegendSettings(legendSettings, key, source);
      applySeriesSettings(renderer, source, settings, key.startsWith("oscillator:"), chartSettingsRef.current);
    });
    drawCurrentRegions();
  }, [legendSettings]);

  useEffect(() => {
    oscillatorPaneGroups.forEach((group) => {
      const runtime = oscillatorPaneRuntimesRef.current.get(group.key);
      if (!runtime?.renderer || !runtime.primaryKey) return;
      syncOscillatorThresholdLine(runtime, runtime.renderer, runtime.primaryKey, resolveOscillatorThresholdSettings(oscillatorThresholdSettings[group.key], group));
    });
  }, [oscillatorThresholdSettings, themeSignature]);

  useEffect(() => {
    chartSettingsRef.current = effectiveChartSettings;
    applyChartAppearance();
  }, [effectiveChartSettings, themeSignature, timeframe]);

  useEffect(() => {
    if (!hasChartData) {
      cleanupChartRuntime();
      return undefined;
    }
    if (!priceRef.current || priceChartRef.current) return undefined;
    const palette = readChartPalette();
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current, timeframe, true, alignLeftPriceScale));
    priceChartRef.current = priceChart;
    const candleSeries = priceChart.addSeries(CandlestickSeries, {
      ...candleSeriesOptions(chartSettingsRef.current),
      autoscaleInfoProvider: padCandleAutoscale,
      priceLineVisible: false
    });
    candleRef.current = candleSeries;
    candleMarkersRef.current = createSeriesMarkers(candleSeries, []);
    const priceZonePrimitive = new PriceZonePrimitive();
    candleSeries.attachPrimitive(priceZonePrimitive);
    priceZonePrimitiveRef.current = priceZonePrimitive;
    const volume = priceChart.addSeries(HistogramSeries, {
      base: 0,
      lastValueVisible: false,
      priceFormat: { type: "volume" },
      priceLineVisible: false,
      priceScaleId: "",
    });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volumeRef.current = volume;
    const draw = (range: LogicalRange | null) => {
      scheduleOverlayRedraw();
      if (
        range
        && range.from <= 10
        && canLoadEarlierRef.current
        && !loadingEarlierRef.current
        && Date.now() >= suppressEarlierLoadUntilRef.current
      ) {
        onLoadEarlierRef.current?.();
      }
    };
    regionDrawRef.current = draw;
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    const observer = new ResizeObserver(() => {
      resizeCharts();
      drawCurrentRegions();
    });
    if (shellRef.current) observer.observe(shellRef.current);
    resizeObserverRef.current = observer;
    paneResizeObserverRef.current = new ResizeObserver(() => {
      layoutNativePaneOverlays();
      scheduleOverlayRedraw();
    });
    overlayInteractionCleanupRef.current = attachOverlayRedrawListeners(priceRef.current, scheduleOverlayRedraw, scheduleOverlayRedrawBurst);
    return () => cleanupChartRuntime();
  }, [hasChartData]);

  useEffect(() => {
    payloadRef.current = payload;
    if (!payload || !priceChartRef.current || !candleRef.current || !volumeRef.current) return;
    const fitKey = buildChartFitKey(ticker, timeframe, referenceKey, payload.candles);
    const shouldAutoFit = fitKey !== fittedChartKeyRef.current;
    const nextCandleWindow = candleWindow(payload.candles);
    const earlierBarsPrepended = Boolean(
      candleWindowRef.current
      && nextCandleWindow
      && nextCandleWindow.first < candleWindowRef.current.first
    );
    const currentRange = shouldAutoFit ? null : priceChartRef.current.timeScale().getVisibleLogicalRange();
    const currentTimeRange = !shouldAutoFit && earlierBarsPrepended ? priceChartRef.current.timeScale().getVisibleRange() : null;
    const timeline = chartTimelineData(payload.candles, timeframe);
    candleBoundsRef.current = candleValueBounds(payload.candles);
    syncRendererData(candleRef.current, timeline as unknown as RendererDatum[], `candles:${timeframe}`);
    syncRendererData(volumeRef.current, volumeDataForSettings(payload, chartSettingsRef.current) as unknown as RendererDatum[], volumeStyleKey(chartSettingsRef.current));
    candleWindowRef.current = nextCandleWindow;
    updateCandleMarkers();
    if (shouldAutoFit) {
      fittedChartKeyRef.current = fitKey;
      if (initialFitTimerRef.current !== null) {
        window.clearTimeout(initialFitTimerRef.current);
      }
      initialFitTimerRef.current = window.setTimeout(() => {
        const currentPayload = payloadRef.current;
        if (!currentPayload || !priceChartRef.current) return;
        suppressEarlierLoad();
        if (reference) {
          fitAroundReference(priceChartRef.current, currentPayload.candles, reference, timeframe);
        } else {
          fitInitialRange(priceChartRef.current, currentPayload.candles, timeframe, initialFitMode);
        }
        drawCurrentRegions();
        initialFitTimerRef.current = null;
      }, 20);
    } else {
      suppressEarlierLoad();
      if (earlierBarsPrepended && currentTimeRange) {
        priceChartRef.current.timeScale().setVisibleRange(currentTimeRange);
      } else if (currentRange) {
        priceChartRef.current.timeScale().setVisibleLogicalRange(currentRange);
      }
      drawCurrentRegions();
    }
  }, [initialFitMode, payload, reference, referenceKey, ticker, timeframe]);

  useEffect(() => {
    if (!priceChartRef.current || !payload?.candles.length || !reference) return;
    suppressEarlierLoad();
    fitAroundReference(priceChartRef.current, payload.candles, reference, timeframe);
    drawCurrentRegions();
  }, [referenceKey, timeframe]);

  useEffect(() => {
    if (!priceChartRef.current) return;
    updatePriceOverlaySeries(displayedOverlaySeries);
    updateCandleMarkers();
    drawCurrentRegions();
  }, [payload, visibleColumnKey, visibleSupervisionKey, liveEntryLineKey]);

  useEffect(() => {
    if (!priceChartRef.current) return;
    updateOscillatorPanes(oscillatorPaneGroups);
  }, [payload, visibleColumnKey, timeframe]);

  function applyChartAppearance() {
    const palette = readChartPalette();
    const priceChart = priceChartRef.current;
    if (priceChart && priceRef.current) {
      priceChart.applyOptions(chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current, timeframe, true, alignLeftPriceScale));
      candleRef.current?.applyOptions(candleSeriesOptions(chartSettingsRef.current));
      if (payloadRef.current && volumeRef.current) {
        syncRendererData(volumeRef.current, volumeDataForSettings(payloadRef.current, chartSettingsRef.current) as unknown as RendererDatum[], volumeStyleKey(chartSettingsRef.current));
      }
    }
    indicatorSeriesRef.current.forEach((renderer, key) => {
      const source = indicatorSourceRef.current.get(key);
      if (!source) return;
      applySeriesSettings(renderer, source, resolveLegendSettings(legendSettings, key, source), key.startsWith("oscillator:"), chartSettingsRef.current);
    });
    drawCurrentRegions();
  }

  function updateCandleMarkers() {
    const markerPlugin = candleMarkersRef.current;
    const currentPayload = payloadRef.current;
    if (!markerPlugin) return;
    if (!currentPayload) {
      markerPlugin.setMarkers([]);
      return;
    }
    markerPlugin.setMarkers(markersForSelection(currentPayload.markers, visibleSelectionRef.current));
  }

  function updatePriceOverlaySeries(seriesList: ChartSeries[]) {
    const priceChart = priceChartRef.current;
    if (!priceChart) return;
    const nextKeys = new Set(seriesList.map((series) => legendSeriesKey("price", series)));
    Array.from(indicatorSeriesRef.current.entries()).forEach(([key, renderer]) => {
      if (!key.startsWith("price:") || nextKeys.has(key)) return;
      priceChart.removeSeries(renderer);
      indicatorSeriesRef.current.delete(key);
      indicatorSourceRef.current.delete(key);
    });
    seriesList.forEach((series) => {
      const key = legendSeriesKey("price", series);
      const settings = resolveLegendSettings(legendSettings, key, series);
      const existing = indicatorSeriesRef.current.get(key);
      if (existing) {
        applySeriesSettings(existing, series, settings, false, chartSettingsRef.current);
      } else {
        const renderer = priceChart.addSeries(LineSeries, {
          color: colorWithOpacity(settings.color, effectiveSeriesOpacity(series, settings)),
          lineStyle: toChartLineStyle(settings.lineStyle),
          lineWidth: toLineWidth(settings.lineWidth),
          autoscaleInfoProvider: () => null,
          priceLineVisible: false,
          title: series.label,
          visible: settings.visible
        });
        syncRendererData(renderer, seriesDataForSettings(series, settings, chartSettingsRef.current) as unknown as RendererDatum[], seriesStyleKey(series, settings, chartSettingsRef.current));
        indicatorSeriesRef.current.set(key, renderer);
      }
      indicatorSourceRef.current.set(key, series);
      indicatorBoundsRef.current.set(key, seriesValueBounds(series));
    });
  }

  function updateOscillatorPanes(groups: OscillatorPaneGroup[]) {
    const chart = priceChartRef.current;
    if (!chart) return;
    const currentKeys = Array.from(oscillatorPaneRuntimesRef.current.entries())
      .sort((left, right) => left[1].paneIndex - right[1].paneIndex)
      .map(([key]) => key);
    const nextKeys = groups.map((group) => group.key);
    if (currentKeys.join("|") !== nextKeys.join("|")) {
      Array.from(oscillatorPaneRuntimesRef.current.entries())
        .sort((left, right) => right[1].paneIndex - left[1].paneIndex)
        .forEach(([key]) => removeOscillatorPaneRuntime(key));
    }
    groups.forEach((group, groupIndex) => {
      let runtime = oscillatorPaneRuntimesRef.current.get(group.key);
      if (!runtime) {
        runtime = {
          layerSignature: "",
          paneIndex: groupIndex + 1,
          primaryKey: "",
          renderer: null,
          seriesKeys: new Set<string>(),
          timelineRenderer: null,
          timelineSignature: "",
          zeroLine: null,
          zeroLineRenderer: null,
          zeroLineSeriesKey: ""
        };
        oscillatorPaneRuntimesRef.current.set(group.key, runtime);
      }
      updateOscillatorPaneTimeline(runtime, chartTimelineData(payloadRef.current?.candles ?? [], timeframe));
      updateOscillatorPaneSeries(runtime, group.series);
      chart.panes()[runtime.paneIndex]?.setStretchFactor(paneStretchFactors[group.key] ?? 1);
    });
    chart.panes()[0]?.setStretchFactor(paneStretchFactors.price ?? 3.25);
    layoutNativePaneOverlays();
  }

  function updateOscillatorPaneTimeline(runtime: OscillatorPaneRuntime, timeline: CandleSeriesDatum[]) {
    const signature = buildTimelineDataSignature(timeline);
    if (!runtime.timelineRenderer) {
      runtime.timelineRenderer = priceChartRef.current?.addSeries(LineSeries, {
        autoscaleInfoProvider: () => null,
        color: "rgba(0, 0, 0, 0)",
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        lineWidth: 1,
        priceLineVisible: false,
        visible: true,
        title: "",
      }, runtime.paneIndex) ?? null;
    }
    if (!runtime.timelineRenderer) return;
    if (runtime.timelineSignature === signature) return;
    syncRendererData(runtime.timelineRenderer, timeline.map((item) => ({ time: item.time as Time, value: 0 })), "timeline");
    runtime.timelineSignature = signature;
  }

  function updateOscillatorPaneSeries(runtime: OscillatorPaneRuntime, seriesList: ChartSeries[]) {
    const chart = priceChartRef.current;
    if (!chart) return;
    const layeredSeries = [...seriesList].sort((left, right) => Number(left.style === "line") - Number(right.style === "line"));
    const requestedPrimaryKey = seriesList[0] ? legendSeriesKey("oscillator", seriesList[0]) : "";
    const layerSignature = layeredSeries.map((series) => `${legendSeriesKey("oscillator", series)}:${series.style}:${series.priceScaleId || "right"}`).join("|");
    if (runtime.layerSignature && runtime.layerSignature !== layerSignature) {
      if (runtime.zeroLine && runtime.zeroLineRenderer) runtime.zeroLineRenderer.removePriceLine(runtime.zeroLine);
      runtime.zeroLine = null;
      runtime.zeroLineRenderer = null;
      runtime.zeroLineSeriesKey = "";
      runtime.seriesKeys.forEach((key) => {
        const renderer = indicatorSeriesRef.current.get(key);
        if (renderer) chart.removeSeries(renderer);
        indicatorSeriesRef.current.delete(key);
        indicatorSourceRef.current.delete(key);
        indicatorBoundsRef.current.delete(key);
      });
      runtime.seriesKeys.clear();
    }
    runtime.layerSignature = layerSignature;
    const nextKeys = new Set(layeredSeries.map((series) => legendSeriesKey("oscillator", series)));
    Array.from(runtime.seriesKeys).forEach((key) => {
      if (nextKeys.has(key)) return;
      const renderer = indicatorSeriesRef.current.get(key);
      if (renderer) {
        if (runtime.zeroLine && runtime.zeroLineSeriesKey === key) {
          renderer.removePriceLine(runtime.zeroLine);
          runtime.zeroLine = null;
          runtime.zeroLineRenderer = null;
          runtime.zeroLineSeriesKey = "";
        }
        chart.removeSeries(renderer);
      }
      runtime.seriesKeys.delete(key);
      indicatorSeriesRef.current.delete(key);
      indicatorSourceRef.current.delete(key);
    });
    let primaryRenderer: AnySeriesApi | null = null;
    let primaryKey = "";
    layeredSeries.forEach((series) => {
      const key = legendSeriesKey("oscillator", series);
      const settings = resolveLegendSettings(legendSettings, key, series);
      let renderer = indicatorSeriesRef.current.get(key);
      if (renderer) {
        applySeriesSettings(renderer, series, settings, true, chartSettingsRef.current);
      } else {
        renderer = addChartSeries(chart, series, settings, runtime.paneIndex);
        syncRendererData(renderer, seriesDataForSettings(series, settings, chartSettingsRef.current) as unknown as RendererDatum[], seriesStyleKey(series, settings, chartSettingsRef.current));
        indicatorSeriesRef.current.set(key, renderer);
      }
      indicatorSourceRef.current.set(key, series);
      indicatorBoundsRef.current.set(key, seriesValueBounds(series));
      runtime.seriesKeys.add(key);
      if (key === requestedPrimaryKey) {
        primaryRenderer = renderer;
        primaryKey = key;
      }
    });
    if (primaryRenderer) {
      runtime.primaryKey = primaryKey;
      runtime.renderer = primaryRenderer;
      const group = oscillatorPaneGroups.find((candidate) => candidate.key === oscillatorPaneKey(seriesList[0]));
      syncOscillatorThresholdLine(runtime, primaryRenderer, primaryKey, resolveOscillatorThresholdSettings(group ? oscillatorThresholdSettings[group.key] : undefined, group));
    }
  }

  function syncOscillatorThresholdLine(runtime: OscillatorPaneRuntime, renderer: AnySeriesApi, seriesKey: string, threshold: OscillatorThresholdSettings) {
    if (runtime.zeroLine && runtime.zeroLineSeriesKey !== seriesKey && runtime.zeroLineRenderer) {
      runtime.zeroLineRenderer.removePriceLine(runtime.zeroLine);
      runtime.zeroLine = null;
      runtime.zeroLineRenderer = null;
      runtime.zeroLineSeriesKey = "";
    }
    if (!runtime.zeroLine) {
      runtime.zeroLine = renderer.createPriceLine({
        axisLabelVisible: threshold.visible,
        color: threshold.color,
        lineStyle: toChartLineStyle(threshold.lineStyle),
        lineVisible: threshold.visible,
        lineWidth: toLineWidth(threshold.lineWidth),
        price: threshold.value,
        title: ""
      });
      runtime.zeroLineRenderer = renderer;
      runtime.zeroLineSeriesKey = seriesKey;
    } else {
      runtime.zeroLine.applyOptions({
        axisLabelVisible: threshold.visible,
        color: threshold.color,
        lineStyle: toChartLineStyle(threshold.lineStyle),
        lineVisible: threshold.visible,
        lineWidth: toLineWidth(threshold.lineWidth),
        price: threshold.value,
        title: ""
      });
    }
  }

  function removeOscillatorPaneRuntime(key: string) {
    const runtime = oscillatorPaneRuntimesRef.current.get(key);
    const chart = priceChartRef.current;
    if (!runtime || !chart) return;
    if (runtime.zeroLine && runtime.zeroLineRenderer) {
      runtime.zeroLineRenderer.removePriceLine(runtime.zeroLine);
    }
    runtime.seriesKeys.forEach((seriesKey) => {
      const renderer = indicatorSeriesRef.current.get(seriesKey);
      if (renderer) chart.removeSeries(renderer);
      indicatorSeriesRef.current.delete(seriesKey);
      indicatorSourceRef.current.delete(seriesKey);
      indicatorBoundsRef.current.delete(seriesKey);
    });
    if (runtime.timelineRenderer) chart.removeSeries(runtime.timelineRenderer);
    runtime.timelineRenderer = null;
    oscillatorPaneRuntimesRef.current.delete(key);
  }

  function drawCurrentRegions() {
    const chart = priceChartRef.current;
    const currentPayload = payloadRef.current;
    if (!chart || !currentPayload) return;
    const selectedZones = (currentPayload.price_zones ?? []).filter((zone) => !zone.displayItemId || visibleSelectionRef.current.has(zone.displayItemId.toLowerCase()));
    const timeline = chartTimelineData(currentPayload.candles, timeframe);
    priceZonePrimitiveRef.current?.setState({
      candles: currentPayload.candles,
      legendSettings: legendSettingsRef.current,
      zones: selectedZones,
    });
    syncPriceZoneAxisLines(candleRef.current, selectedZones, legendSettingsRef.current, priceZoneAxisLinesRef.current);
    drawRegions(chart, candleRef.current, priceLayerRef.current, currentPayload.regions, selectedZones, currentPayload.trade_annotations ?? [], currentPayload.candles, timeline, chartSettingsRef.current, legendSettingsRef.current, liveEntryLineRef.current);
    oscillatorPaneRuntimesRef.current.forEach((_runtime, key) => {
      drawSessionRegions(chart, oscillatorLayerRefs.current.get(key) ?? null, currentPayload.regions, timeline, currentPayload.candles, chartSettingsRef.current, false);
    });
    drawReferenceLine(chart, referenceLayerRef.current, currentPayload.candles, showReferenceLineRef.current ? referenceRef.current : null);
  }

  function scheduleOverlayRedraw() {
    if (overlayRedrawFrameRef.current !== null) return;
    overlayRedrawFrameRef.current = window.requestAnimationFrame(() => {
      overlayRedrawFrameRef.current = null;
      drawCurrentRegions();
    });
  }

  function scheduleOverlayRedrawBurst() {
    scheduleOverlayRedraw();
    if (overlayRedrawTimerRef.current !== null) {
      window.clearTimeout(overlayRedrawTimerRef.current);
    }
    overlayRedrawTimerRef.current = window.setTimeout(() => {
      scheduleOverlayRedraw();
      overlayRedrawTimerRef.current = null;
    }, 48);
  }

  function cleanupChartRuntime() {
    if (initialFitTimerRef.current !== null) {
      window.clearTimeout(initialFitTimerRef.current);
      initialFitTimerRef.current = null;
    }
    if (scaleStabilizationFrameRef.current !== null) {
      window.cancelAnimationFrame(scaleStabilizationFrameRef.current);
      scaleStabilizationFrameRef.current = null;
    }
    if (overlayRedrawFrameRef.current !== null) {
      window.cancelAnimationFrame(overlayRedrawFrameRef.current);
      overlayRedrawFrameRef.current = null;
    }
    if (overlayRedrawTimerRef.current !== null) {
      window.clearTimeout(overlayRedrawTimerRef.current);
      overlayRedrawTimerRef.current = null;
    }
    overlayInteractionCleanupRef.current?.();
    overlayInteractionCleanupRef.current = null;
    resizeObserverRef.current?.disconnect();
    resizeObserverRef.current = null;
    paneResizeObserverRef.current?.disconnect();
    paneResizeObserverRef.current = null;
    if (regionDrawRef.current && priceChartRef.current) {
      priceChartRef.current.timeScale().unsubscribeVisibleLogicalRangeChange(regionDrawRef.current);
      regionDrawRef.current = null;
    }
    oscillatorPaneRuntimesRef.current.clear();
    priceZoneAxisLinesRef.current.clear();
    candleMarkersRef.current?.detach();
    candleMarkersRef.current = null;
    if (priceZonePrimitiveRef.current && candleRef.current) {
      candleRef.current.detachPrimitive(priceZonePrimitiveRef.current);
    }
    priceZonePrimitiveRef.current = null;
    if (priceChartRef.current) {
      priceChartRef.current.remove();
    }
    priceChartRef.current = null;
    candleRef.current = null;
    volumeRef.current = null;
    indicatorSeriesRef.current.clear();
    indicatorSourceRef.current.clear();
    indicatorBoundsRef.current.clear();
    fittedChartKeyRef.current = "";
    candleWindowRef.current = null;
    candleBoundsRef.current = null;
    scaleRecoveryCountRef.current = 0;
    if (shellRef.current) delete shellRef.current.dataset.chartScaleRecoveries;
  }

  function resizeCharts() {
    const price = priceRef.current;
    if (price && priceChartRef.current) {
      priceChartRef.current.applyOptions({ width: price.clientWidth, height: price.clientHeight });
    }
    priceChartRef.current?.panes()[0]?.setStretchFactor(paneStretchFactors.price ?? 3.25);
    oscillatorPaneRuntimesRef.current.forEach((runtime, key) => {
      priceChartRef.current?.panes()[runtime.paneIndex]?.setStretchFactor(paneStretchFactors[key] ?? 1);
    });
    layoutNativePaneOverlays();
    scheduleOverlayRedrawBurst();
  }

  function layoutNativePaneOverlays() {
    const chart = priceChartRef.current;
    const root = priceRef.current;
    if (!chart || !root) return;
    const rootRect = root.getBoundingClientRect();
    const position = (overlay: HTMLElement | null, paneIndex: number) => {
      const paneElement = chart.panes()[paneIndex]?.getHTMLElement();
      if (!overlay || !paneElement) return;
      paneResizeObserverRef.current?.observe(paneElement);
      const paneRect = paneElement.getBoundingClientRect();
      overlay.style.left = `${paneRect.left - rootRect.left}px`;
      overlay.style.top = `${paneRect.top - rootRect.top}px`;
      overlay.style.width = `${paneRect.width}px`;
      overlay.style.height = `${paneRect.height}px`;
    };
    position(pricePaneOverlayRef.current, 0);
    oscillatorPaneRuntimesRef.current.forEach((runtime, key) => position(oscillatorPaneRefs.current.get(key) ?? null, runtime.paneIndex));
  }

  const closeOscillatorPane = (group: OscillatorPaneGroup) => {
    const paneItems = new Set(group.series.map((series) => seriesSelectionKey(series)));
    const nextColumns = visibleColumns.filter((column) => !paneItems.has(column.toLowerCase()));
    if (nextColumns.length !== visibleColumns.length) {
      onVisibleColumnsChange(nextColumns);
    }
  };

  const commitTicker = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = normalizeTickerValue(draftTicker.trim());
    if (!normalized) {
      setDraftTicker(normalizeTickerValue(ticker));
      return;
    }
    setDraftTicker(normalized);
    if (normalized !== normalizeTickerValue(ticker)) {
      onTickerChange(normalized);
    }
  };

  return (
    <div
      className={fullscreen ? "chart-shell fullscreen" : "chart-shell"}
      onPointerDownCapture={(event) => {
        if ((event.target as HTMLElement).closest(".chart-pane-canvas")) claimViewportForUser(event.target);
      }}
      onPointerMoveCapture={(event) => {
        if (event.buttons !== 0 && (event.target as HTMLElement).closest(".chart-pane-canvas")) {
          scheduleScaleStabilization();
        }
      }}
      onPointerCancelCapture={finishViewportInteraction}
      onPointerUpCapture={finishViewportInteraction}
      onWheelCapture={(event) => {
        if ((event.target as HTMLElement).closest(".chart-pane-canvas")) {
          claimViewportForUser(event.target);
          scheduleScaleStabilization();
          scheduleOverlayRedrawBurst();
        }
      }}
      ref={shellRef}
    >
      <div className="chart-component-toolbar">
        {tickerEditable ? <form className="chart-ticker-form" onSubmit={commitTicker}>
          <TickerLogo logoUrl={tickerLogoUrl} ticker={ticker} />
          <input
            aria-label="Ticker"
            className="chart-ticker-input"
            maxLength={tickerMaxLength}
            onChange={(event) => setDraftTicker(normalizeTickerValue(event.target.value))}
            spellCheck={false}
            style={{ textTransform: normalizeTicker ? "uppercase" : "none", width: tickerInputWidth }}
            value={draftTicker}
          />
        </form> : <TickerIdentity className="chart-ticker-readonly" logoUrl={tickerLogoUrl} ticker={ticker} />}
        {tickerChangeAsOf ? <TickerChangeBadge asOf={tickerChangeAsOf} ticker={ticker} /> : null}
        {periodStart && periodEnd && onPeriodChange ? (
          <ChartPeriodSelect
            end={periodEnd}
            max={periodMax}
            min={periodMin}
            onChange={onPeriodChange}
            onOpenChange={(value) => {
              setPeriodMenuOpen(value);
              if (value) {
                setColumnMenuOpen(false);
                setSupervisionMenuOpen(false);
                setChartSettingsOpen(false);
              }
            }}
            open={periodMenuOpen}
            start={periodStart}
          />
        ) : null}
        <span className="toolbar-divider" />
        <div className="chart-timeframe-row">
          {timeframes.map((item) => (
            <button className={buildSegmentButtonClassName(item === timeframe)} key={item} onClick={() => onTimeframeChange(item)} type="button">
              {formatTimeframeLabel(item)}
            </button>
          ))}
        </div>
        {showIndicatorControls || showSupervisionControls ? (
          <>
            <span className="toolbar-divider" />
            {showIndicatorControls ? (
              <IndicatorFeatureSelect
                catalogColumns={catalogColumns}
                displayItemOptions={displayItemOptions}
                featureOptions={featureOptions}
                indicatorOptions={indicatorOptions}
                onChange={onVisibleColumnsChange}
                onOpenChange={(value) => {
                  setColumnMenuOpen(value);
                  if (value) {
                    setSupervisionMenuOpen(false);
                    setChartSettingsOpen(false);
                    setPeriodMenuOpen(false);
                  }
                }}
                open={columnMenuOpen}
                values={visibleColumns}
              />
            ) : null}
            {showSupervisionControls ? (
              <SupervisionSelect
                catalogColumns={catalogColumns}
                displayItemOptions={displayItemOptions}
                labelOptions={labelOptions}
                onChange={onVisibleColumnsChange}
                onLabelChange={onVisibleSupervisionGroupsChange}
                onOpenChange={(value) => {
                  setSupervisionMenuOpen(value);
                  if (value) {
                    setColumnMenuOpen(false);
                    setChartSettingsOpen(false);
                    setPeriodMenuOpen(false);
                  }
                }}
                open={supervisionMenuOpen}
                values={visibleColumns}
                visibleLabels={visibleSupervisionGroups}
              />
            ) : null}
          </>
        ) : null}
        <div className="toolbar-spacer" />
        <button
          className="toolbar-button"
          data-chart-settings-trigger="true"
          type="button"
          title="Chart settings"
          onClick={() => {
            setColumnMenuOpen(false);
            setSupervisionMenuOpen(false);
            setPeriodMenuOpen(false);
            setChartSettingsOpen((value) => !value);
          }}
        >
          <Settings size={15} />
        </button>
        <span className="toolbar-divider" />
        <button aria-label={latestRangeActionLabel(timeframe)} className="toolbar-button" type="button" title={latestRangeActionLabel(timeframe)} onClick={() => executeViewportCommand(() => fitLatestSession(priceChartRef.current, fitCandles(payload), timeframe))}><CalendarDays size={15} /></button>
        <button aria-label={reference ? "Center trade" : "Center latest"} className="toolbar-button" type="button" title={reference ? "Center trade" : "Center latest"} onClick={() => executeViewportCommand(() => centerReferenceOrLatest(priceChartRef.current, fitCandles(payload), reference, timeframe))}><AlignCenterHorizontal size={15} /></button>
        <button aria-label="Reset view" className="toolbar-button" type="button" title="Reset view" onClick={() => executeViewportCommand(() => resetChartViewport(priceChartRef.current, fitCandles(payload), timeframe, priceRef.current?.clientWidth ?? 0, chartSettingsRef.current.candleSize))}><RefreshCcw size={15} /></button>
        {enableFullscreen ? (
          <>
            <span className="toolbar-divider" />
            <button
              className="toolbar-button"
              type="button"
              title={fullscreen ? "Exit fullscreen" : "Fullscreen"}
              onClick={() => {
                setFullscreen((value) => !value);
                window.setTimeout(() => resizeCharts(), 30);
              }}
            >
              {fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
            </button>
          </>
        ) : null}
      </div>
      {chartSettingsOpen ? (
        <ChartSettingsPopover
          onChange={updateChartSettings}
          onClose={() => setChartSettingsOpen(false)}
          onReset={resetChartSettings}
          settings={chartSettings}
        />
      ) : null}
      {loading && !hasChartData ? (
        <div className="empty-state chart-empty-state">
          <span className="loading-spinner" aria-hidden="true" />
          Loading chart data...
        </div>
      ) : errorMessage && !hasChartData ? (
        <div className="empty-state chart-empty-state">Chart data request failed: {errorMessage}</div>
      ) : !hasChartData ? (
        <div className="empty-state chart-empty-state">{emptyMessage}</div>
      ) : (
        <div className="chart-canvas-stack">
          {loading ? <div className="chart-update-status">Updating chart...</div> : null}
          {loadingEarlier ? <div className="chart-update-status">Loading earlier data...</div> : null}
          {infoMessage ? <div aria-label={infoMessage} className="chart-update-status info" role="status" title={infoMessage}>{infoMessage}</div> : null}
          {errorMessage ? <div aria-label={`Chart update failed: ${errorMessage}`} className="chart-update-status error" role="status" title={errorMessage}>Chart update failed</div> : null}
          <div className="chart-native-surface chart-price" style={{ height: nativeChartHeight }}>
            <div className="chart-pane-canvas" ref={priceRef} />
            <div className="chart-reference-stack-layer" ref={referenceLayerRef} />
            <div className="chart-native-pane-overlay" data-chart-pane="price" ref={pricePaneOverlayRef}>
              <div className="session-layer" ref={priceLayerRef} />
              <ChartLegend
                indicatorCount={priceIndicatorCount}
                items={priceLegendItems}
                onReset={resetLegendSettings}
                onUpdate={updateLegendSettings}
              />
            </div>
          {oscillatorPaneGroups.map((group) => {
            return (
              <div className="chart-native-pane-overlay chart-osc" key={group.key} ref={(node) => setOscillatorPaneRef(group.key, node)}>
                <div className="session-layer" ref={(node) => setOscillatorLayerRef(group.key, node)} />
                <button
                  aria-label={`Close ${formatOscillatorPaneLabel(group)} pane`}
                  className="chart-pane-close"
                  onClick={() => closeOscillatorPane(group)}
                  title={`Close ${formatOscillatorPaneLabel(group)} pane`}
                  type="button"
                >
                  <X size={12} />
                </button>
                <ChartLegend
                  indicatorCount={group.series.length}
                  items={buildSeriesLegendItems(group.series, "oscillator", legendSettings, displayItemOptions, catalogColumns, chartSettings)}
                  leftScale={oscillatorGroupUsesLeftScale(group)}
                  onReset={resetLegendSettings}
                  onThresholdReset={() => resetOscillatorThreshold(group)}
                  onThresholdUpdate={(patch) => updateOscillatorThreshold(group, patch)}
                  onUpdate={updateLegendSettings}
                  threshold={resolveOscillatorThresholdSettings(oscillatorThresholdSettings[group.key], group)}
                  title={formatOscillatorPaneLabel(group)}
                />
              </div>
            );
          })}
          </div>
        </div>
      )}
    </div>
  );
});

class ChartPanelErrorBoundary extends Component<{ children: ReactNode; resetKey: string }, { error: string }> {
  state = { error: "" };

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error.message : "The chart renderer stopped unexpectedly." };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("Chart renderer failure", error, info.componentStack);
  }

  componentDidUpdate(previous: Readonly<{ children: ReactNode; resetKey: string }>) {
    if (previous.resetKey !== this.props.resetKey && this.state.error) this.setState({ error: "" });
  }

  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="empty-state chart-empty-state chart-renderer-error" role="alert">
        <strong>Chart renderer stopped</strong>
        <span>{this.state.error}</span>
        <button className="button secondary compact" onClick={() => this.setState({ error: "" })} type="button">Retry chart</button>
      </div>
    );
  }
}

export const ChartPanel = forwardRef<ChartPanelHandle, ChartPanelProps>((props, ref) => (
  <ChartPanelErrorBoundary resetKey={`${props.ticker}:${props.timeframe}:${props.periodStart ?? ""}:${props.periodEnd ?? ""}`}>
    <ChartPanelCore {...props} ref={ref} />
  </ChartPanelErrorBoundary>
));

ChartPanel.displayName = "ChartPanel";

function attachOverlayRedrawListeners(target: HTMLElement | null, redraw: () => void, redrawBurst: () => void) {
  if (!target) return () => undefined;
  const stopPointerRedraw = (redrawAfter = true) => {
    window.removeEventListener("pointermove", redraw);
    window.removeEventListener("pointerup", endPointerRedraw);
    window.removeEventListener("pointercancel", endPointerRedraw);
    if (redrawAfter) redrawBurst();
  };
  const endPointerRedraw = () => stopPointerRedraw(true);
  const startPointerRedraw = () => {
    redraw();
    window.addEventListener("pointermove", redraw);
    window.addEventListener("pointerup", endPointerRedraw);
    window.addEventListener("pointercancel", endPointerRedraw);
  };
  target.addEventListener("pointerdown", startPointerRedraw);
  target.addEventListener("wheel", redrawBurst, { passive: true });
  target.addEventListener("dblclick", redrawBurst);
  return () => {
    target.removeEventListener("pointerdown", startPointerRedraw);
    target.removeEventListener("wheel", redrawBurst);
    target.removeEventListener("dblclick", redrawBurst);
    stopPointerRedraw(false);
  };
}

function ChartPeriodSelect({
  end,
  max,
  min,
  onChange,
  onOpenChange,
  open,
  start
}: {
  end: string;
  max?: string;
  min?: string;
  onChange: (start: string, end: string) => void;
  onOpenChange: (value: boolean) => void;
  open: boolean;
  start: string;
}) {
  const updateStart = (value: string) => {
    if (!value) return;
    onChange(value, end && value <= end ? end : value);
  };
  const updateEnd = (value: string) => {
    if (!value) return;
    onChange(start && start <= value ? start : value, value);
  };
  return (
    <div className="chart-period-select">
      <button
        aria-expanded={open}
        className="chart-period-select-button"
        onClick={() => onOpenChange(!open)}
        title="Chart period"
        type="button"
      >
        <CalendarRange size={15} />
        <span>{formatChartPeriodLabel(start, end)}</span>
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="chart-period-menu">
          <div className="chart-period-menu-title">Chart period</div>
          <div className="chart-period-grid">
            <label className="chart-period-field">
              <span>Start</span>
              <input
                max={end || max}
                min={min}
                onChange={(event) => updateStart(event.target.value)}
                onInput={(event) => updateStart(event.currentTarget.value)}
                type="date"
                value={start}
              />
            </label>
            <label className="chart-period-field">
              <span>End</span>
              <input
                max={max}
                min={start || min}
                onChange={(event) => updateEnd(event.target.value)}
                onInput={(event) => updateEnd(event.currentTarget.value)}
                type="date"
                value={end}
              />
            </label>
          </div>
          {min && max ? (
            <button className="chart-period-link" onClick={() => onChange(min, max)} type="button">
              Use full available range
            </button>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

type LegendItem = {
  color: string;
  configurable: boolean;
  currentLevelCount?: number;
  guideHelp?: ChartColumnHelp;
  guideTitle?: string;
  historyBars?: number;
  itemKind: "series" | "zone";
  key: string;
  labelFontSize?: number;
  label: string;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  maxHistoricalTags?: number;
  opacity: number;
  seriesStyle: "candlestick" | "histogram" | "line";
  semanticColor: boolean;
  semanticColors: { down: string; neutral: string; up: string };
  showConnectors?: boolean;
  showAxisLabel?: boolean;
  showHistoricalLabels?: boolean;
  showLabels?: boolean;
  showValue: boolean;
  supportsConnectors?: boolean;
  supportsCurrentLevelCount?: boolean;
  supportsAxisLabel?: boolean;
  supportsHistoricalLabels?: boolean;
  supportsHistoryWindow?: boolean;
  supportsStroke?: boolean;
  value: string;
  visible: boolean;
};

function ChartLegend({
  indicatorCount,
  items,
  leftScale = false,
  onReset,
  onUpdate,
  onThresholdReset,
  onThresholdUpdate,
  threshold,
  title,
}: {
  indicatorCount: number;
  items: LegendItem[];
  leftScale?: boolean;
  onReset: (key: string) => void;
  onUpdate: (key: string, patch: LegendSeriesSettings) => void;
  onThresholdReset?: () => void;
  onThresholdUpdate?: (patch: Partial<OscillatorThresholdSettings>) => void;
  threshold?: OscillatorThresholdSettings;
  title?: string;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  const [editorAnchor, setEditorAnchor] = useState<HTMLElement | null>(null);
  const [guideItem, setGuideItem] = useState<LegendItem | null>(null);
  if (!items.length) return null;
  const editingItem = items.find((item) => item.key === editingKey && item.configurable);
  return (
    <div className={`${collapsed ? "chart-legend collapsed" : "chart-legend"}${leftScale ? " left-scale" : ""}`}>
      <button
        aria-label={collapsed ? "Expand legend" : "Collapse legend"}
        className="chart-legend-header"
        onClick={() => {
          const nextCollapsed = !collapsed;
          setCollapsed(nextCollapsed);
          if (nextCollapsed) {
            setEditingKey(null);
            setEditorAnchor(null);
          }
        }}
        type="button"
      >
        {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        <b>{title || formatIndicatorCount(indicatorCount)}</b>
      </button>
      {!collapsed ? (
        <>
          <div className="chart-legend-rows">
            {items.map((item) => (
              <div className={item.visible ? "chart-legend-row" : "chart-legend-row muted"} key={item.key}>
                <span className={item.seriesStyle === "histogram" ? "legend-swatch histogram" : `legend-swatch ${item.lineStyle}`} style={{ color: item.color, opacity: item.opacity }}>
                  <i style={{ background: item.color }} />
                </span>
                <span className="legend-label">{item.label}</span>
                {item.showValue && item.visible ? <span className="legend-value" style={{ color: item.color, opacity: item.opacity }}>{item.value}</span> : null}
                {item.configurable ? (
                  <span className="legend-row-actions">
                    <button
                      aria-label={item.visible ? `Hide ${item.label}` : `Show ${item.label}`}
                      onClick={() => onUpdate(item.key, { visible: !item.visible })}
                      title={item.visible ? "Hide" : "Show"}
                      type="button"
                    >
                      {item.visible ? <Eye size={13} /> : <EyeOff size={13} />}
                    </button>
                    {item.guideHelp ? (
                      <button
                        aria-label={`Guide ${item.guideTitle || item.label}`}
                        onClick={() => {
                          setEditingKey(null);
                          setEditorAnchor(null);
                          setGuideItem(item);
                        }}
                        title="Guide"
                        type="button"
                      >
                        <CircleHelp size={13} />
                      </button>
                    ) : null}
                    <button
                      aria-label={`Configure ${item.label}`}
                      onClick={(event) => {
                        const closing = editingKey === item.key;
                        setEditingKey(closing ? null : item.key);
                        setEditorAnchor(closing ? null : event.currentTarget);
                      }}
                      title="Configure"
                      type="button"
                    >
                      <SlidersHorizontal size={13} />
                    </button>
                  </span>
                ) : null}
              </div>
            ))}
          </div>
          {editingItem ? (
            <LegendEditor
              anchor={editorAnchor}
              item={editingItem}
              onClose={() => {
                setEditingKey(null);
                setEditorAnchor(null);
              }}
              onReset={() => onReset(editingItem.key)}
              onThresholdReset={onThresholdReset}
              onThresholdUpdate={onThresholdUpdate}
              onUpdate={(patch) => onUpdate(editingItem.key, patch)}
              threshold={threshold}
            />
          ) : null}
          {guideItem?.guideHelp ? <IndicatorGuideModal help={guideItem.guideHelp} onClose={() => setGuideItem(null)} title={guideItem.guideTitle || guideItem.label} /> : null}
        </>
      ) : null}
    </div>
  );
}

function LegendEditor({
  anchor,
  item,
  onClose,
  onReset,
  onThresholdReset,
  onThresholdUpdate,
  threshold,
  onUpdate
}: {
  anchor: HTMLElement | null;
  item: LegendItem;
  onClose: () => void;
  onReset: () => void;
  onThresholdReset?: () => void;
  onThresholdUpdate?: (patch: Partial<OscillatorThresholdSettings>) => void;
  threshold?: OscillatorThresholdSettings;
  onUpdate: (patch: LegendSeriesSettings) => void;
}) {
  const editorRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, visibility: "hidden" as "hidden" | "visible" });

  useLayoutEffect(() => {
    const placeEditor = () => {
      const editor = editorRef.current;
      if (!anchor || !editor || !anchor.isConnected) return;
      const anchorRect = anchor.getBoundingClientRect();
      const editorRect = editor.getBoundingClientRect();
      const margin = 8;
      const below = anchorRect.bottom + 5;
      const above = anchorRect.top - editorRect.height - 5;
      const top = below + editorRect.height <= window.innerHeight - margin ? below : Math.max(margin, above);
      const left = Math.max(margin, Math.min(anchorRect.right - editorRect.width, window.innerWidth - editorRect.width - margin));
      setPosition({ left, top, visibility: "visible" });
    };
    placeEditor();
    window.addEventListener("resize", placeEditor);
    window.addEventListener("scroll", placeEditor, true);
    return () => {
      window.removeEventListener("resize", placeEditor);
      window.removeEventListener("scroll", placeEditor, true);
    };
  }, [anchor, item.key]);

  useEffect(() => {
    const closeOnPointer = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && (editorRef.current?.contains(target) || anchor?.contains(target))) return;
      onClose();
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("pointerdown", closeOnPointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnPointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [anchor, onClose]);

  if (!anchor) return null;
  return createPortal(
    <div className="chart-legend-editor" ref={editorRef} role="dialog" aria-label={`${item.label} indicator settings`} style={position}>
      <div className="chart-legend-editor-header">
        <span>{item.label}</span>
        <button aria-label="Close indicator settings" onClick={onClose} title="Close" type="button">
          <X size={13} />
        </button>
      </div>
      <label>
        Color
        {item.semanticColor ? (
          <span
            className="legend-semantic-colors"
            style={{
              "--legend-semantic-down": item.semanticColors.down,
              "--legend-semantic-neutral": item.semanticColors.neutral,
              "--legend-semantic-up": item.semanticColors.up,
            } as CSSProperties}
          >
            <i data-tone="buy" />+ <i data-tone="sell" />− <i data-tone="neutral" />0
          </span>
        ) : <input type="color" value={item.color} onChange={(event) => onUpdate({ color: event.target.value })} />}
      </label>
      {item.seriesStyle === "line" && item.supportsStroke !== false ? (
        <>
          <label>
            Shape
            <select value={item.lineStyle} onChange={(event) => onUpdate({ lineStyle: event.target.value as LegendLineStyle })}>
              <option value="solid">Solid</option>
              <option value="dashed">Dashed</option>
              <option value="dotted">Dotted</option>
            </select>
          </label>
          <label>
            Width
            <input min={1} max={4} type="range" value={item.lineWidth} onChange={(event) => onUpdate({ lineWidth: Number(event.target.value) })} />
          </label>
        </>
      ) : null}
      {item.itemKind === "zone" && item.supportsHistoricalLabels ? (
        <label>
          Connector label size
          <span className="legend-range-control">
            <input
              aria-label={`${item.label} label text size`}
              min={9}
              max={18}
              step={1}
              type="range"
              value={item.labelFontSize ?? 11}
              onChange={(event) => onUpdate({ labelFontSize: Number(event.target.value) })}
            />
            <output>{item.labelFontSize ?? 11}px</output>
          </span>
        </label>
      ) : null}
      {item.itemKind === "zone" && item.supportsHistoryWindow ? (
        <label>
          History
          <span className="legend-range-control">
            <input
              aria-label={`${item.label} history bars`}
              min={10}
              max={500}
              step={10}
              type="range"
              value={item.historyBars ?? 100}
              onChange={(event) => onUpdate({ historyBars: Number(event.target.value) })}
            />
            <output>{item.historyBars ?? 100} bars</output>
          </span>
        </label>
      ) : null}
      {item.itemKind === "zone" && item.supportsCurrentLevelCount ? (
        <label>
          Nearest levels per side
          <span className="legend-range-control">
            <input
              aria-label={`${item.label} nearest levels per side`}
              min={1}
              max={6}
              step={1}
              type="range"
              value={item.currentLevelCount ?? 3}
              onChange={(event) => onUpdate({ currentLevelCount: Number(event.target.value) })}
            />
            <output>{item.currentLevelCount ?? 3}</output>
          </span>
        </label>
      ) : null}
      <label>
        Opacity
        <span className="legend-range-control">
          <input
            aria-label={`${item.label} opacity`}
            min={0}
            max={100}
            step={1}
            type="range"
            value={Math.round(item.opacity * 100)}
            onChange={(event) => onUpdate({ opacity: Number(event.target.value) / 100 })}
          />
          <output>{Math.round(item.opacity * 100)}%</output>
        </span>
      </label>
      {item.itemKind === "zone" ? (
        <>
          {item.supportsAxisLabel ? (
            <label className="legend-checkbox">
              <input checked={Boolean(item.showAxisLabel)} type="checkbox" onChange={(event) => onUpdate({ showAxisLabel: event.target.checked })} />
              Compact tag on price axis
            </label>
          ) : null}
          {item.supportsHistoricalLabels ? (
            <>
              <label className="legend-checkbox">
                <input checked={item.showHistoricalLabels !== false} type="checkbox" onChange={(event) => onUpdate({ showHistoricalLabels: event.target.checked })} />
                Labels on historical lines
              </label>
              {item.showHistoricalLabels !== false ? (
                <label>
                  Connector label limit
                  <span className="legend-range-control">
                    <input min={0} max={16} step={1} type="range" value={item.maxHistoricalTags ?? 6} onChange={(event) => onUpdate({ maxHistoricalTags: Number(event.target.value) })} />
                    <output>{item.maxHistoricalTags ?? 6}</output>
                  </span>
                </label>
              ) : null}
            </>
          ) : null}
          {item.supportsConnectors ? (
            <label className="legend-checkbox">
              <input checked={item.showConnectors !== false} type="checkbox" onChange={(event) => onUpdate({ showConnectors: event.target.checked })} />
              Swing-to-break connectors
            </label>
          ) : null}
        </>
      ) : (
        <label className="legend-checkbox">
          <input checked={item.showValue} type="checkbox" onChange={(event) => onUpdate({ showValue: event.target.checked })} />
          Value in legend
        </label>
      )}
      {threshold && onThresholdUpdate ? (
        <>
          <div className="chart-legend-editor-section-title">Pane threshold</div>
          <label className="legend-checkbox">
            <input checked={threshold.visible} type="checkbox" onChange={(event) => onThresholdUpdate({ visible: event.target.checked })} />
            Show baseline
          </label>
          <label>
            Value
            <input className="legend-number-input" step="any" type="number" value={threshold.value} onChange={(event) => onThresholdUpdate({ value: Number(event.target.value) })} />
          </label>
          <label>
            Color
            <input type="color" value={threshold.color} onChange={(event) => onThresholdUpdate({ color: event.target.value })} />
          </label>
          <label>
            Shape
            <select value={threshold.lineStyle} onChange={(event) => onThresholdUpdate({ lineStyle: event.target.value as LegendLineStyle })}>
              <option value="solid">Solid</option>
              <option value="dashed">Dashed</option>
              <option value="dotted">Dotted</option>
            </select>
          </label>
          <label>
            Width
            <input min={1} max={4} type="range" value={threshold.lineWidth} onChange={(event) => onThresholdUpdate({ lineWidth: Number(event.target.value) })} />
          </label>
          {onThresholdReset ? <button className="legend-reset-button" onClick={onThresholdReset} type="button">Reset threshold</button> : null}
        </>
      ) : null}
      <button className="legend-reset-button" onClick={onReset} type="button">Reset</button>
    </div>,
    document.body
  );
}

function ChartColumnMenuPortal({
  anchor,
  children,
  className = ""
}: {
  anchor: HTMLElement | null;
  children: ReactNode;
  className?: string;
}) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ left: 8, top: 8, visibility: "hidden" as "hidden" | "visible" });

  useLayoutEffect(() => {
    const placeMenu = () => {
      const menu = menuRef.current;
      if (!anchor || !menu || !anchor.isConnected) return;
      const zoom = Number.parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--app-zoom")) || 1;
      const anchorRect = anchor.getBoundingClientRect();
      const menuRect = menu.getBoundingClientRect();
      const viewportWidth = window.innerWidth / zoom;
      const viewportHeight = window.innerHeight / zoom;
      const menuWidth = menuRect.width / zoom;
      const menuHeight = menuRect.height / zoom;
      const anchorLeft = anchorRect.left / zoom;
      const anchorBottom = anchorRect.bottom / zoom;
      const anchorTop = anchorRect.top / zoom;
      const margin = 8;
      const gap = 6;
      const below = anchorBottom + gap;
      const above = anchorTop - menuHeight - gap;
      const top = below + menuHeight <= viewportHeight - margin ? below : Math.max(margin, above);
      const left = Math.max(margin, Math.min(anchorLeft, viewportWidth - menuWidth - margin));
      setPosition({ left, top, visibility: "visible" });
    };
    placeMenu();
    const observer = new ResizeObserver(placeMenu);
    if (menuRef.current) observer.observe(menuRef.current);
    window.addEventListener("resize", placeMenu);
    window.addEventListener("scroll", placeMenu, true);
    return () => {
      observer.disconnect();
      window.removeEventListener("resize", placeMenu);
      window.removeEventListener("scroll", placeMenu, true);
    };
  }, [anchor]);

  if (!anchor) return null;
  return createPortal(
    <div className={`chart-column-menu chart-column-menu-portal${className ? ` ${className}` : ""}`} ref={menuRef} style={position}>
      {children}
    </div>,
    document.body
  );
}

function IndicatorFeatureSelect({
  catalogColumns,
  displayItemOptions,
  featureOptions,
  indicatorOptions,
  onChange,
  onOpenChange,
  open,
  values
}: {
  catalogColumns: ChartCatalogItem[];
  displayItemOptions: ChartDisplayItem[];
  featureOptions: string[];
  indicatorOptions: string[];
  onChange: (value: string[]) => void;
  onOpenChange: (value: boolean) => void;
  open: boolean;
  values: string[];
}) {
  const usesDisplayItems = displayItemOptions.length > 0;
  const indicatorSet = new Set(indicatorOptions);
  const visibleFeatures = featureOptions.filter((option) => !indicatorSet.has(option));
  const visibleOptions = [...indicatorOptions, ...visibleFeatures];
  const catalogByColumn = new Map(catalogColumns.map((item) => [item.column, item]));
  const displayItems = mergeSessionEquivalentDisplayItems(displayItemOptions.filter((item) => item.presentation?.selectable !== false));
  const standardDisplayItems = displayItems.filter((item) => !chartMenuItemUsesLookahead(item));
  const groupedDisplayItems = groupChartDisplayItems(standardDisplayItems);
  const groupedIndicatorOptions = groupColumnOptions(indicatorOptions, catalogByColumn, "Indicators");
  const groupedFeatureOptions = groupColumnOptions(visibleFeatures, catalogByColumn, "Features");
  const selected = new Set(values);
  const selectedCount = usesDisplayItems ? standardDisplayItems.filter((option) => selected.has(option.id)).length : visibleOptions.filter((option) => selected.has(option)).length;
  const labelForOption = (option: string) => catalogByColumn.get(option)?.title ?? displayName(option);
  const [helpKey, setHelpKey] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) setHelpKey(null);
  }, [open]);

  const toggleValue = (value: string) => {
    const nextSelected = new Set(values);
    if (nextSelected.has(value)) {
      nextSelected.delete(value);
    } else {
      nextSelected.add(value);
    }
    const ordered = usesDisplayItems ? displayItems.map((option) => option.id).filter((option) => nextSelected.has(option)) : visibleOptions.filter((option) => nextSelected.has(option));
    onChange(ordered);
  };

  const toggleHelp = (key: string) => setHelpKey((current) => (current === key ? null : key));
  const helpForColumn = (column: string) => chartColumnHelp(catalogByColumn.get(column), labelForOption(column));
  const helpForDisplayItem = (item: ChartDisplayItem) => {
    const sourceColumn = item.sourceColumns?.map((column) => catalogByColumn.get(column)).find((column) => column?.knowledge);
    return chartColumnHelp({
      ...item,
      knowledge: item.knowledge ?? sourceColumn?.knowledge,
      leakage: item.leakage ?? sourceColumn?.leakage,
    }, item.title, chartMenuItemUsesLookahead(item) || chartMenuItemUsesLookahead(sourceColumn));
  };

  return (
    <div className="chart-column-select">
      <button
        aria-expanded={open}
        className="chart-column-select-button"
        onClick={() => onOpenChange(!open)}
        ref={triggerRef}
        title="Indicators & Features"
        type="button"
      >
        <ChartNoAxesCombined size={19} />
        <span>{visibleFeatures.length ? "Indicators & Features" : "Indicators"}</span>
        {selectedCount ? <b>{selectedCount}</b> : null}
        <ChevronDown size={14} />
      </button>
      {open ? (
        <ChartColumnMenuPortal anchor={triggerRef.current}>
          {usesDisplayItems ? (
            <div className="chart-column-menu-grid">
              {groupedDisplayItems.map((section) => (
                <div className="chart-column-menu-column" key={section.key}>
                  <div className="chart-column-menu-title">{section.label}</div>
                  <div className="chart-column-menu-list feature-list">
                    {section.items.map((option) => (
                      <ChartColumnMenuItem
                        help={helpForDisplayItem(option)}
                        helpOpen={helpKey === `display:${option.id}`}
                        key={option.id}
                        onHelpToggle={() => toggleHelp(`display:${option.id}`)}
                        onToggle={() => toggleValue(option.id)}
                        selected={selected.has(option.id)}
                        subtitle={option.category ? displayName(option.category) : undefined}
                        title={option.title}
                      />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          ) : (
            <div className="chart-column-menu-grid">
              {[...groupedIndicatorOptions, ...groupedFeatureOptions].map((section) => (
                <div className="chart-column-menu-column" key={section.key}>
                  <div className="chart-column-menu-title">{section.label}</div>
                  <div className="chart-column-menu-list feature-list">
                    {section.items.map((option) => (
                      <ChartColumnMenuItem
                        help={helpForColumn(option)}
                        helpOpen={helpKey === `column:${option}`}
                        key={option}
                        onHelpToggle={() => toggleHelp(`column:${option}`)}
                        onToggle={() => toggleValue(option)}
                        selected={selected.has(option)}
                        title={labelForOption(option)}
                      />
                    ))}
                  </div>
                </div>
              ))}
              {visibleFeatures.length ? null : <div className="chart-column-menu-empty">No feature columns for this session.</div>}
            </div>
          )}
        </ChartColumnMenuPortal>
      ) : null}
    </div>
  );
}

function SupervisionSelect({
  catalogColumns,
  displayItemOptions,
  labelOptions,
  onChange,
  onLabelChange,
  onOpenChange,
  open,
  values,
  visibleLabels
}: {
  catalogColumns: ChartCatalogItem[];
  displayItemOptions: ChartDisplayItem[];
  labelOptions: ChartLabelOption[];
  onChange: (value: string[]) => void;
  onLabelChange?: (value: string[]) => void;
  onOpenChange: (value: boolean) => void;
  open: boolean;
  values: string[];
  visibleLabels: string[];
}) {
  const catalogByColumn = new Map(catalogColumns.map((item) => [item.column, item]));
  const displayItems = mergeSessionEquivalentDisplayItems(displayItemOptions.filter((item) => item.presentation?.selectable !== false));
  const lookaheadDisplayItems = displayItems.filter((item) => chartMenuItemUsesLookahead(item));
  const groupedLookaheadDisplayItems = groupChartDisplayItems(lookaheadDisplayItems);
  const selected = new Set(values);
  const selectedLabels = new Set(visibleLabels);
  const selectedCount = lookaheadDisplayItems.filter((option) => selected.has(option.id)).length + labelOptions.filter((option) => selectedLabels.has(option.group)).length;
  const [helpKey, setHelpKey] = useState<string | null>(null);
  const triggerRef = useRef<HTMLButtonElement | null>(null);

  useEffect(() => {
    if (!open) setHelpKey(null);
  }, [open]);

  const toggleValue = (value: string) => {
    const nextSelected = new Set(values);
    if (nextSelected.has(value)) {
      nextSelected.delete(value);
    } else {
      nextSelected.add(value);
    }
    onChange(displayItems.map((option) => option.id).filter((option) => nextSelected.has(option)));
  };

  const toggleLabel = (group: string) => {
    if (!onLabelChange) return;
    const nextSelected = new Set(visibleLabels);
    if (nextSelected.has(group)) {
      nextSelected.delete(group);
    } else {
      nextSelected.add(group);
    }
    onLabelChange(labelOptions.map((option) => option.group).filter((groupName) => nextSelected.has(groupName)));
  };

  const toggleHelp = (key: string) => setHelpKey((current) => (current === key ? null : key));
  const helpForDisplayItem = (item: ChartDisplayItem) => {
    const sourceColumn = item.sourceColumns?.map((column) => catalogByColumn.get(column)).find((column) => column?.knowledge);
    return chartColumnHelp({
      ...item,
      knowledge: item.knowledge ?? sourceColumn?.knowledge,
      leakage: item.leakage ?? sourceColumn?.leakage,
    }, item.title, true);
  };
  const helpForLabel = (option: ChartLabelOption) => chartColumnHelp(option, option.title, true);

  return (
    <div className="chart-column-select">
      <button
        aria-expanded={open}
        className="chart-column-select-button"
        onClick={() => onOpenChange(!open)}
        ref={triggerRef}
        title="Lookahead & Supervision"
        type="button"
      >
        <Eye size={18} />
        <span>Supervision</span>
        {selectedCount ? <b>{selectedCount}</b> : null}
        <ChevronDown size={14} />
      </button>
      {open ? (
        <ChartColumnMenuPortal anchor={triggerRef.current} className="chart-supervision-menu">
          <div className="chart-column-menu-grid">
            <div className="chart-column-menu-column lookahead" key="lookahead">
              <div className="chart-column-menu-title">Lookahead / Supervision</div>
              <div className="chart-column-menu-note">Future-bar labels and supervision outputs. Use them for review, training, and validation, not as live indicators.</div>
              {groupedLookaheadDisplayItems.map((section) => (
                <div className="chart-column-menu-block" key={section.key}>
                  <div className="chart-column-menu-subtitle">{section.label}</div>
                  <div className="chart-column-menu-list feature-list">
                    {section.items.map((option) => (
                      <ChartColumnMenuItem
                        help={helpForDisplayItem(option)}
                        helpOpen={helpKey === `display:${option.id}`}
                        key={option.id}
                        onHelpToggle={() => toggleHelp(`display:${option.id}`)}
                        onToggle={() => toggleValue(option.id)}
                        selected={selected.has(option.id)}
                        subtitle={option.category ? displayName(option.category) : undefined}
                        title={option.title}
                        tone="lookahead"
                      />
                    ))}
                  </div>
                </div>
              ))}
              {labelOptions.length ? (
                <div className="chart-column-menu-block">
                  <div className="chart-column-menu-subtitle">Labels</div>
                  <div className="chart-column-menu-list">
                    {labelOptions.map((option) => (
                      <ChartColumnMenuItem
                        help={helpForLabel(option)}
                        helpOpen={helpKey === `label:${option.group}`}
                        key={option.id}
                        onHelpToggle={() => toggleHelp(`label:${option.group}`)}
                        onToggle={() => toggleLabel(option.group)}
                        selected={selectedLabels.has(option.group)}
                        title={option.title}
                        tone="lookahead"
                      />
                    ))}
                  </div>
                </div>
              ) : null}
              {!groupedLookaheadDisplayItems.length && !labelOptions.length ? (
                <div className="chart-column-menu-empty">No supervision labels are available for this chart.</div>
              ) : null}
            </div>
          </div>
        </ChartColumnMenuPortal>
      ) : null}
    </div>
  );
}

type ChartColumnHelp = {
  bearishEvidence?: string;
  bullishEvidence?: string;
  calculation?: string;
  caveats: string[];
  components: Array<{ description: string; label: string; tone?: "buy" | "info" | "neutral" | "sell" | "warning" }>;
  detail?: string;
  futureLooking: boolean;
  readingGuide?: string;
  summary: string;
  timeframeBehavior?: string;
};

function ChartColumnMenuItem({
  help,
  helpOpen,
  onHelpToggle,
  onToggle,
  selected,
  subtitle,
  title,
  tone
}: {
  help: ChartColumnHelp;
  helpOpen: boolean;
  onHelpToggle: () => void;
  onToggle: () => void;
  selected: boolean;
  subtitle?: string;
  title: string;
  tone?: "lookahead";
}) {
  return (
    <div className={`chart-column-menu-item${selected ? " selected" : ""}${tone === "lookahead" ? " lookahead" : ""}`}>
      <button className="chart-column-menu-toggle" onClick={onToggle} type="button">
        <span className="chart-column-menu-check">{selected ? <Check size={13} /> : null}</span>
        <span className="chart-column-menu-label">
          <span>{title}</span>
          {subtitle ? <small>{subtitle}</small> : null}
        </span>
      </button>
      <button aria-expanded={helpOpen} aria-label={`Explain ${title}`} className="chart-column-help-button" onClick={onHelpToggle} type="button">
        <CircleHelp size={13} />
      </button>
      {helpOpen ? <IndicatorGuideModal help={help} onClose={onHelpToggle} title={title} /> : null}
    </div>
  );
}

function IndicatorGuideModal({ help, onClose, title }: { help: ChartColumnHelp; onClose: () => void; title: string }) {
  return createPortal(
    <Modal className="chart-indicator-guide-modal" onClose={onClose} title={`How to read: ${title}`}>
      <div className="chart-indicator-guide-content">
        {help.futureLooking ? <div className="chart-indicator-guide-alert"><strong>LOOKAHEAD ONLY</strong><span>This uses future bars. Use it for review, training, and validation—not as a live tradable signal.</span></div> : null}
        <div className="chart-indicator-guide-grid">
          <IndicatorGuideSection label="Read" text={help.readingGuide || help.summary} tone="read" />
          {help.components.length ? (
            <section className="chart-indicator-guide-section chart-indicator-guide-components" data-tone="read">
              <strong>What is drawn</strong>
              <div className="chart-indicator-guide-component-list">
                {help.components.map((component) => (
                  <div className="chart-indicator-guide-component" data-tone={component.tone || "neutral"} key={component.label}>
                    <span aria-hidden="true" className="chart-indicator-guide-component-swatch" />
                    <div><b>{component.label}</b><p>{component.description}</p></div>
                  </div>
                ))}
              </div>
            </section>
          ) : null}
          {help.bullishEvidence ? <IndicatorGuideSection label="Bullish evidence" text={help.bullishEvidence} tone="buy" /> : null}
          {help.bearishEvidence ? <IndicatorGuideSection label="Bearish evidence" text={help.bearishEvidence} tone="sell" /> : null}
          {help.calculation || help.detail ? <IndicatorGuideSection label="Calculation & scale" text={help.calculation || help.detail || ""} tone="info" /> : null}
          {help.timeframeBehavior ? <IndicatorGuideSection label="Timeframe behavior" text={help.timeframeBehavior} tone="info" /> : null}
          <section className="chart-indicator-guide-section" data-tone="warning">
            <strong>Do not overread</strong>
            {help.caveats.length ? <ul>{help.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}</ul> : <p>No single indicator is a complete forecast. Confirm the reading with price response, liquidity, and the trading regime.</p>}
          </section>
        </div>
      </div>
    </Modal>,
    document.body,
  );
}

function IndicatorGuideSection({ label, text, tone }: { label: string; text: string; tone: "buy" | "info" | "read" | "sell" }) {
  return <section className="chart-indicator-guide-section" data-tone={tone}><strong>{label}</strong><p>{text}</p></section>;
}

type ChartMenuHelpSource = {
  artifactGroups?: string[];
  category?: string;
  group?: string;
  id?: string;
  knowledge?: ChartCatalogKnowledge;
  leakage?: Record<string, unknown>;
};

function chartColumnHelp(source: ChartMenuHelpSource | undefined, title: string, futureLooking = false): ChartColumnHelp {
  const knowledge = source?.knowledge;
  const summary = compactHelpText(knowledge?.shortDescription) || `${title} is available from the provider catalog for chart review.`;
  const detailed = compactHelpText(knowledge?.detailedDescription || knowledge?.theory || knowledge?.interpretation);
  return {
    bearishEvidence: compactHelpText(knowledge?.bearishEvidence) || undefined,
    bullishEvidence: compactHelpText(knowledge?.bullishEvidence) || undefined,
    calculation: compactHelpText(knowledge?.calculation) || undefined,
    caveats: (knowledge?.caveats ?? []).map(compactHelpText).filter(Boolean),
    components: (knowledge?.components ?? []).map((component) => ({ ...component, description: compactHelpText(component.description), label: compactHelpText(component.label) })).filter((component) => component.label && component.description),
    detail: detailed && detailed !== summary ? detailed : undefined,
    futureLooking: futureLooking || chartMenuItemUsesLookahead(source),
    readingGuide: compactHelpText(knowledge?.readingGuide) || undefined,
    summary,
    timeframeBehavior: compactHelpText(knowledge?.timeframeBehavior) || undefined,
  };
}

function compactHelpText(value: string | undefined) {
  return String(value || "").replace(/\s+/g, " ").trim();
}

function chartMenuItemUsesLookahead(item: ChartMenuHelpSource | undefined) {
  if (!item) return false;
  if (item.leakage && Object.keys(item.leakage).length) return true;
  const values = [
    item.category,
    item.group,
    item.id,
    ...(item.artifactGroups ?? []),
  ].filter(Boolean).map((value) => String(value).toLowerCase());
  return values.some((value) => value.includes("supervision") || value.includes("oracle") || value.includes("label") || value.includes("scanner"));
}

type ChartColumnMenuSection<T> = { key: string; label: string; items: T[] };

const chartColumnGroupOrder = [
  "core",
  "session",
  "momentum",
  "volatility",
  "volume_liquidity",
  "price_action",
  "shock",
  "fvg",
  "market_structure",
  "order_blocks",
  "supervision_bar",
  "supervision_method",
  "supervision_scanner",
  "labels",
  "other",
];

function groupChartDisplayItems(items: ChartDisplayItem[]): Array<ChartColumnMenuSection<ChartDisplayItem>> {
  const sections = new Map<string, ChartDisplayItem[]>();
  items.forEach((item) => {
    const key = chartDisplayGroupKey(item);
    sections.set(key, [...(sections.get(key) ?? []), item]);
  });
  return Array.from(sections.entries()).map(([key, sectionItems]) => ({
    key,
    label: chartDisplayGroupLabel(key),
    items: sectionItems.sort((left, right) => left.title.localeCompare(right.title)),
  })).sort((left, right) => chartColumnGroupRank(left.key) - chartColumnGroupRank(right.key) || left.label.localeCompare(right.label));
}

function groupColumnOptions(options: string[], catalogByColumn: Map<string | undefined, ChartCatalogItem>, fallbackLabel: string): Array<ChartColumnMenuSection<string>> {
  const sections = new Map<string, string[]>();
  options.forEach((option) => {
    const key = catalogByColumn.get(option)?.group || fallbackLabel.toLowerCase();
    sections.set(key, [...(sections.get(key) ?? []), option]);
  });
  return Array.from(sections.entries()).map(([key, sectionItems]) => ({
    key,
    label: chartDisplayGroupLabel(key, fallbackLabel),
    items: sectionItems.sort((left, right) => displayName(left).localeCompare(displayName(right))),
  })).sort((left, right) => chartColumnGroupRank(left.key) - chartColumnGroupRank(right.key) || left.label.localeCompare(right.label));
}

function chartDisplayGroupKey(item: ChartDisplayItem) {
  return item.group || item.category || "other";
}

function chartDisplayGroupLabel(key: string, fallback = "Other") {
  if (!key) return fallback;
  if (key === "labels") return "Labels";
  return displayName(key);
}

function chartColumnGroupRank(key: string) {
  const index = chartColumnGroupOrder.indexOf(key);
  return index === -1 ? chartColumnGroupOrder.length : index;
}

function mergeSessionEquivalentDisplayItems(items: ChartDisplayItem[]): ChartDisplayItem[] {
  const merged = new Map<string, ChartDisplayItem>();
  items.forEach((item) => {
    const key = chartDisplaySemanticKey(item);
    const existing = merged.get(key);
    if (!existing) {
      merged.set(key, item);
      return;
    }
    merged.set(key, mergeChartDisplayItem(existing, item));
  });
  return Array.from(merged.values());
}

function mergeChartDisplayItem(left: ChartDisplayItem, right: ChartDisplayItem): ChartDisplayItem {
  const preferred = chartDisplayItemScore(right) > chartDisplayItemScore(left) ? right : left;
  const secondary = preferred === right ? left : right;
  return {
    ...preferred,
    artifactGroups: uniqueStrings([...(preferred.artifactGroups ?? []), ...(secondary.artifactGroups ?? [])]),
    featureGroups: uniqueStrings([...(preferred.featureGroups ?? []), ...(secondary.featureGroups ?? [])]),
    sourceColumns: uniqueStrings([...(preferred.sourceColumns ?? []), ...(secondary.sourceColumns ?? [])]),
  };
}

function chartDisplaySemanticKey(item: ChartDisplayItem) {
  if (item.group === "session") {
    const sessionTitle = canonicalSessionDisplayTitle(item);
    if (sessionTitle) return `session:${sessionTitle.toLowerCase()}`;
  }
  return String(item.id || item.title).toLowerCase();
}

function canonicalSessionDisplayTitle(item: ChartDisplayItem) {
  const sourceColumns = item.sourceColumns ?? [];
  const title = stripSessionDate(String(item.title || ""));
  const openingRangeColumn = sourceColumns.find((column) => /^or_\d+m_(high|low|range)$/.test(column));
  const openingRange = openingRangeColumn?.match(/^or_(\d+)m_/) || title.match(/\b(?:OR|Opening Range)\s*(\d+)\s*m\b/i);
  if (openingRange) return `Opening Range ${openingRange[1]}m`;
  if (sourceColumns.some((column) => column.startsWith("premarket_")) || /\bpremarket range\b/i.test(title)) return "Premarket Range";
  if (sourceColumns.some((column) => ["day_open", "day_high_so_far", "day_low_so_far"].includes(column)) || /\bsession range\b/i.test(title)) {
    return "Session Range";
  }
  return title;
}

function stripSessionDate(value: string) {
  return value
    .replace(/\b\d{4}-\d{2}-\d{2}\b/g, "")
    .replace(/\b\d{8}\b/g, "")
    .replace(/\s+/g, " ")
    .trim();
}

function chartDisplayItemScore(item: ChartDisplayItem) {
  const role = item.presentation?.chartRole || "";
  let score = item.sourceColumns?.length ?? 0;
  if (role === "composite" || role === "anchored_zone" || role === "price_zone") score += 10;
  if (String(item.id || "").startsWith("feature.")) score += 5;
  if (String(item.id || "").startsWith("column.")) score -= 5;
  return score;
}

function defaultSupervisionSelectionIds(group: string) {
  const key = group.toLowerCase();
  if (key === "bar") return ["bar:oracle_long_entry_signal", "bar:oracle_long_exit_signal"];
  if (key === "method") return ["method:method_entry_signal", "method:method_exit_signal"];
  if (key === "scanner") return ["scanner:is_top_3"];
  return [];
}

function uniqueStrings(values: string[]) {
  return Array.from(new Set(values.filter(Boolean)));
}

function ChartSettingsPopover({
  onChange,
  onClose,
  onReset,
  settings
}: {
  onChange: <K extends keyof ChartAppearanceSettings>(key: K, value: ChartAppearanceSettings[K]) => void;
  onClose: () => void;
  onReset: () => void;
  settings: ChartAppearanceSettings;
}) {
  return (
    <div className="chart-settings-slot">
      <div className="chart-settings-header">
        <div>
          <b>Chart Settings</b>
          <span>Appearance settings for candles, sessions, and day dividers.</span>
        </div>
        <button aria-label="Close chart settings" className="toolbar-button" onClick={onClose} title="Close" type="button">
          <X size={14} />
        </button>
      </div>

      <ChartSettingsSection title="Candles">
        <p className="chart-settings-help">
          Candle size changes horizontal spacing and visual candle width. It does not change the selected timeframe or source data.
        </p>
        <label className="chart-setting-row">
          Up candle
          <input type="color" value={settings.upColor} onChange={(event) => onChange("upColor", event.target.value)} />
        </label>
        <label className="chart-setting-row">
          Down candle
          <input type="color" value={settings.downColor} onChange={(event) => onChange("downColor", event.target.value)} />
        </label>
        <label className="chart-setting-row">
          Candle size
          <span className="chart-setting-inline">
            <input min={8} max={80} type="range" value={settings.candleSize} onChange={(event) => onChange("candleSize", Number(event.target.value))} />
            <b>{settings.candleSize}</b>
          </span>
        </label>
        <label className="chart-setting-toggle">
          <input checked={settings.borderVisible} type="checkbox" onChange={(event) => onChange("borderVisible", event.target.checked)} />
          Draw candle borders
        </label>
        {settings.borderVisible ? (
          <div className="chart-setting-two-column">
            <label>
              Up border
              <input type="color" value={settings.borderUpColor} onChange={(event) => onChange("borderUpColor", event.target.value)} />
            </label>
            <label>
              Down border
              <input type="color" value={settings.borderDownColor} onChange={(event) => onChange("borderDownColor", event.target.value)} />
            </label>
          </div>
        ) : null}
      </ChartSettingsSection>

      <ChartSettingsSection title="Wicks">
        <p className="chart-settings-help">
          Wick width is controlled by the chart renderer and follows candle spacing. These settings control wick visibility and color.
        </p>
        <label className="chart-setting-toggle">
          <input checked={settings.wickVisible} type="checkbox" onChange={(event) => onChange("wickVisible", event.target.checked)} />
          Show wicks
        </label>
        {settings.wickVisible ? (
          <div className="chart-setting-two-column">
            <label>
              Up wick
              <input type="color" value={settings.wickUpColor} onChange={(event) => onChange("wickUpColor", event.target.value)} />
            </label>
            <label>
              Down wick
              <input type="color" value={settings.wickDownColor} onChange={(event) => onChange("wickDownColor", event.target.value)} />
            </label>
          </div>
        ) : null}
      </ChartSettingsSection>

      <ChartSettingsSection title="Extended Hours">
        <p className="chart-settings-help">
          Region opacity changes only the session shading layer. Candles remain fitted from price data only.
        </p>
        <label className="chart-setting-row">
          Premarket
          <input type="color" value={settings.premarketColor} onChange={(event) => onChange("premarketColor", event.target.value)} />
        </label>
        <label className="chart-setting-row">
          Premarket opacity
          <span className="chart-setting-inline">
            <input min={0} max={60} type="range" value={Math.round(settings.premarketOpacity * 100)} onChange={(event) => onChange("premarketOpacity", Number(event.target.value) / 100)} />
            <b>{Math.round(settings.premarketOpacity * 100)}%</b>
          </span>
        </label>
        <label className="chart-setting-row">
          Post market
          <input type="color" value={settings.afterHoursColor} onChange={(event) => onChange("afterHoursColor", event.target.value)} />
        </label>
        <label className="chart-setting-row">
          Post market opacity
          <span className="chart-setting-inline">
            <input min={0} max={60} type="range" value={Math.round(settings.afterHoursOpacity * 100)} onChange={(event) => onChange("afterHoursOpacity", Number(event.target.value) / 100)} />
            <b>{Math.round(settings.afterHoursOpacity * 100)}%</b>
          </span>
        </label>
      </ChartSettingsSection>

      <ChartSettingsSection title="Day Separators">
        <p className="chart-settings-help">
          Day separators draw at the first visible candle of each new market date. They do not change candle timestamps.
        </p>
        <label className="chart-setting-toggle">
          <input checked={settings.daySeparatorsVisible} type="checkbox" onChange={(event) => onChange("daySeparatorsVisible", event.target.checked)} />
          Show day separators
        </label>
        {settings.daySeparatorsVisible ? (
          <>
            <label className="chart-setting-row">
              Separator color
              <input type="color" value={settings.daySeparatorColor} onChange={(event) => onChange("daySeparatorColor", event.target.value)} />
            </label>
            <label className="chart-setting-row">
              Separator style
              <select value={settings.daySeparatorStyle} onChange={(event) => onChange("daySeparatorStyle", event.target.value as DaySeparatorStyle)}>
                <option value="solid">Solid</option>
                <option value="dashed">Dashed</option>
                <option value="dotted">Dotted</option>
              </select>
            </label>
          </>
        ) : null}
      </ChartSettingsSection>

      <div className="chart-setting-actions">
        <button className="text-button" onClick={onReset} type="button">Reset</button>
      </div>
    </div>
  );
}

function ChartSettingsSection({ children, title }: { children: ReactNode; title: string }) {
  return (
    <section className="chart-settings-section">
      <h3>{title}</h3>
      {children}
    </section>
  );
}

function buildSeriesLegendItems(series: ChartSeries[], pane: LegendPane, settingsMap: LegendSettingsMap, displayItemOptions: ChartDisplayItem[], catalogColumns: ChartCatalogItem[], appearance = defaultChartAppearanceSettings): LegendItem[] {
  const displayItemById = new Map(displayItemOptions.map((item) => [item.id, item]));
  const catalogByColumn = new Map(catalogColumns.map((item) => [item.column, item]));
  return series.filter((item) => item.legend !== false).map((item) => {
    const key = legendSeriesKey(pane, item);
    const settings = resolveLegendSettings(settingsMap, key, item);
    const latest = latestSeriesValue(item.data);
    const displayItem = item.displayItemId ? displayItemById.get(item.displayItemId) : undefined;
    const sourceColumn = displayItem?.sourceColumns?.map((column) => catalogByColumn.get(column)).find((column) => column?.knowledge) ?? catalogByColumn.get(item.column);
    const guideTitle = displayItem?.title ?? sourceColumn?.title ?? item.label;
    const guideHelp = displayItem
      ? chartColumnHelp({
          ...displayItem,
          knowledge: displayItem.knowledge ?? sourceColumn?.knowledge,
          leakage: displayItem.leakage ?? sourceColumn?.leakage,
        }, guideTitle, chartMenuItemUsesLookahead(displayItem) || chartMenuItemUsesLookahead(sourceColumn))
      : sourceColumn ? chartColumnHelp(sourceColumn, guideTitle) : undefined;
    return {
      color: item.colorMode === "sign" ? signColor(latest, appearance) : settings.color,
      configurable: true,
      guideHelp,
      guideTitle,
      itemKind: "series" as const,
      key,
      label: item.label,
      lineStyle: settings.lineStyle,
      lineWidth: settings.lineWidth,
      opacity: settings.opacity,
      seriesStyle: item.style,
      semanticColor: item.colorMode === "sign",
      semanticColors: { down: appearance.downColor, neutral: readNeutralChartColor(), up: appearance.upColor },
      showValue: settings.showValue,
      value: latest === null ? "-" : formatPrice(latest),
      visible: settings.visible
    };
  });
}

function buildPriceZoneLegendItems(
  zones: PriceZone[],
  settingsMap: LegendSettingsMap,
  displayItemOptions: ChartDisplayItem[],
  catalogColumns: ChartCatalogItem[],
  appearance = defaultChartAppearanceSettings,
): LegendItem[] {
  const displayItemById = new Map(displayItemOptions.map((item) => [item.id, item]));
  const catalogByColumn = new Map(catalogColumns.map((item) => [item.column, item]));
  const grouped = new Map<string, PriceZone[]>();
  zones.forEach((zone) => {
    const id = zone.settingsId || zone.displayItemId || `zone:${zone.label}`;
    grouped.set(id, [...(grouped.get(id) ?? []), zone]);
  });
  return Array.from(grouped, ([id, itemZones]) => {
    const displayItem = displayItemById.get(itemZones[0]?.displayItemId || id);
    const sourceColumn = displayItem?.sourceColumns?.map((column) => catalogByColumn.get(column)).find((column) => column?.knowledge);
    const guideTitle = displayItem?.title ?? sourceColumn?.title ?? itemZones[0]?.label ?? "Price levels";
    const guideHelp = displayItem
      ? chartColumnHelp({
          ...displayItem,
          knowledge: displayItem.knowledge ?? sourceColumn?.knowledge,
          leakage: displayItem.leakage ?? sourceColumn?.leakage,
        }, guideTitle, chartMenuItemUsesLookahead(displayItem) || chartMenuItemUsesLookahead(sourceColumn))
      : sourceColumn ? chartColumnHelp(sourceColumn, guideTitle) : undefined;
    const key = priceZoneLegendKey(id);
    const settings = resolvePriceZoneLegendSettings(settingsMap, key, itemZones[0]);
    return {
      color: readNeutralChartColor(),
      configurable: true,
      currentLevelCount: settings.currentLevelCount,
      guideHelp,
      guideTitle,
      historyBars: settings.historyBars,
      itemKind: "zone" as const,
      key,
      label: itemZones[0]?.legendLabel ?? guideTitle,
      labelFontSize: settings.labelFontSize,
      lineStyle: settings.lineStyle,
      lineWidth: settings.lineWidth,
      maxHistoricalTags: settings.maxHistoricalTags,
      opacity: settings.opacity,
      seriesStyle: "line" as const,
      semanticColor: true,
      semanticColors: { down: appearance.downColor, neutral: readNeutralChartColor(), up: appearance.upColor },
      showConnectors: settings.showConnectors,
      showAxisLabel: settings.showAxisLabel,
      showHistoricalLabels: settings.showHistoricalLabels,
      showValue: true,
      supportsConnectors: itemZones.some((zone) => zone.annotationKind === "bos" || zone.annotationKind === "choch"),
      supportsCurrentLevelCount: itemZones.some((zone) => Boolean(zone.currentLevelSide)),
      supportsAxisLabel: itemZones.some((zone) => typeof zone.axisLabelDefault === "boolean"),
      supportsHistoricalLabels: itemZones.some((zone) => (zone.renderMode === "line" && Boolean(zone.compactLabel)) || isStructureBreakZone(zone)),
      supportsHistoryWindow: itemZones.some((zone) => !zone.latest),
      supportsStroke: !itemZones.some((zone) => Boolean(zone.currentLevelSide)),
      value: `${itemZones.length} level${itemZones.length === 1 ? "" : "s"}`,
      visible: settings.visible,
    };
  });
}

function latestSeriesValue(data: Array<{ value: number }>) {
  for (let index = data.length - 1; index >= 0; index -= 1) {
    const value = data[index]?.value;
    if (Number.isFinite(value)) return value;
  }
  return null;
}

function formatIndicatorCount(count: number) {
  return `${count} indicator${count === 1 ? "" : "s"}`;
}

function formatChartPeriodLabel(start: string, end: string) {
  if (!start && !end) return "Period";
  if (start === end) return start;
  return `${start} - ${end}`;
}

function buildOscillatorPaneGroups(series: ChartSeries[]): OscillatorPaneGroup[] {
  const groups = new Map<string, ChartSeries[]>();
  series.forEach((item) => {
    const key = oscillatorPaneKey(item);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });
  return Array.from(groups, ([key, items]) => ({ key, series: items }));
}

function formatOscillatorPaneLabel(group: OscillatorPaneGroup) {
  if (group.key === "oscillator:portfolio_risk") return "Portfolio Risk";
  if (group.key === "oscillator:microstructure") return "QMD Microstructure Outlook";
  if (group.key.startsWith("oscillator:qmd_")) {
    return group.series.length === 1 ? group.series[0].label : `QMD ${group.key.slice("oscillator:qmd_".length).replaceAll("_", " ")}`;
  }
  if (group.key === "oscillator:macd") return "MACD Pane";
  if (group.key === "oscillator:pane_2") return "Pane 2";
  if (group.key === "oscillator:pane_3") return "Pane 3";
  if (group.series.length === 1) return group.series[0].label;
  return `${group.series.length} indicators`;
}

function oscillatorGroupUsesLeftScale(group?: OscillatorPaneGroup) {
  return Boolean(group?.series.some((series) => series.priceScaleId === "left"));
}

function defaultOscillatorPaneHeight(group: OscillatorPaneGroup) {
  return group.key === "oscillator:microstructure" ? 200 : 190;
}

function oscillatorPaneKey(series: ChartSeries) {
  if (series.paneKey && series.paneKey !== "price") return `oscillator:${series.paneKey}`;
  if (series.displayItemId) return `oscillator:${series.displayItemId}`;
  const column = series.column.toLowerCase();
  if (column.startsWith("macd_")) return "oscillator:macd";
  return legendSeriesKey("oscillator", series);
}

function legendSeriesKey(pane: LegendPane, series: ChartSeries) {
  return `${pane}:${series.displayItemId || "column"}:${series.column || series.label}`;
}

function priceZoneLegendKey(displayItemId: string) {
  return `price-zone:${displayItemId}`;
}

function seriesSelectionKey(series: ChartSeries) {
  return String(series.displayItemId || series.column || series.label).toLowerCase();
}

function loadLegendSettings(storageKey = LEGEND_SETTINGS_STORAGE_KEY): LegendSettingsMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as LegendSettingsMap;
    if (!parsed || typeof parsed !== "object") return {};
    const staleQmdZonePattern = /^price-zone:indicator\.qmd_generic_structure\.(?:decision-zones|micro|tactical|context|unified\.(?:support|resistance)|(?:micro|tactical|context)\.(?:support|resistance|swings)|bos|choch|reference\.(?:session|premarket|52-week|prior-month))$/;
    const normalized = Object.fromEntries(Object.entries(parsed).filter(([key]) => !staleQmdZonePattern.test(key)));
    const agreementKey = Object.keys(normalized).find((key) => key.includes("indicator.qmd_generic_structure") && key.endsWith(":qmd_structure_agreement"));
    if (agreementKey && normalized[agreementKey]) {
      const { visible: _staleVisibility, ...agreementSettings } = normalized[agreementKey];
      normalized[agreementKey] = agreementSettings;
    }
    return normalized;
  } catch {
    return {};
  }
}

function saveLegendSettings(settings: LegendSettingsMap, storageKey = LEGEND_SETTINGS_STORAGE_KEY) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, JSON.stringify(settings));
}

function loadOscillatorThresholdSettings(storageKey = OSCILLATOR_THRESHOLD_STORAGE_KEY): OscillatorThresholdSettingsMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as OscillatorThresholdSettingsMap;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveOscillatorThresholdSettings(settings: OscillatorThresholdSettingsMap, storageKey = OSCILLATOR_THRESHOLD_STORAGE_KEY) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, JSON.stringify(settings));
}

function resolveOscillatorThresholdSettings(settings?: Partial<OscillatorThresholdSettings>, group?: OscillatorPaneGroup): OscillatorThresholdSettings {
  const defaultValue = group?.key === "oscillator:rsi" ? 50 : 0;
  const defaultColor = validHexColor(readNeutralChartColor(), "#667085");
  return {
    color: validHexColor(settings?.color, defaultColor),
    lineStyle: settings?.lineStyle === "solid" || settings?.lineStyle === "dotted" ? settings.lineStyle : "dashed",
    lineWidth: Math.max(1, Math.min(4, Math.round(Number(settings?.lineWidth) || 1))),
    value: Number.isFinite(Number(settings?.value)) ? Number(settings?.value) : defaultValue,
    visible: settings?.visible !== false,
  };
}

function loadChartAppearanceSettings(storageKey = CHART_APPEARANCE_STORAGE_KEY): ChartAppearanceSettings {
  if (typeof window === "undefined") return { ...defaultChartAppearanceSettings };
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return { ...defaultChartAppearanceSettings };
    return normalizeChartAppearanceSettings(JSON.parse(raw) as Partial<ChartAppearanceSettings>);
  } catch {
    return { ...defaultChartAppearanceSettings };
  }
}

function saveChartAppearanceSettings(settings: ChartAppearanceSettings, storageKey = CHART_APPEARANCE_STORAGE_KEY) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, JSON.stringify(settings));
}

function loadPaneStretchFactors(storageKey: string): Record<string, number> {
  if (typeof window === "undefined") return {};
  try {
    const parsed = JSON.parse(window.localStorage.getItem(storageKey) ?? "{}") as Record<string, number>;
    return Object.fromEntries(Object.entries(parsed).filter(([, value]) => Number.isFinite(value) && value > 0.01 && value <= 100));
  } catch {
    return {};
  }
}

function savePaneStretchFactors(factors: Record<string, number>, storageKey: string) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, JSON.stringify(factors));
}

function normalizeChartAppearanceSettings(settings: Partial<ChartAppearanceSettings>): ChartAppearanceSettings {
  const afterHoursColor = validHexColor(settings.afterHoursColor, defaultChartAppearanceSettings.afterHoursColor);
  const premarketColor = validHexColor(settings.premarketColor, defaultChartAppearanceSettings.premarketColor);
  return {
    afterHoursColor: afterHoursColor.toUpperCase() === "#BFDBFE" ? defaultChartAppearanceSettings.afterHoursColor : afterHoursColor,
    afterHoursOpacity: settings.afterHoursOpacity === 0.24 ? defaultChartAppearanceSettings.afterHoursOpacity : clampNumber(settings.afterHoursOpacity, 0, 0.6, defaultChartAppearanceSettings.afterHoursOpacity),
    borderDownColor: validHexColor(settings.borderDownColor, defaultChartAppearanceSettings.borderDownColor),
    borderUpColor: validHexColor(settings.borderUpColor, defaultChartAppearanceSettings.borderUpColor),
    borderVisible: typeof settings.borderVisible === "boolean" ? settings.borderVisible : defaultChartAppearanceSettings.borderVisible,
    candleSize: Math.round(clampNumber(settings.candleSize, 8, 80, defaultChartAppearanceSettings.candleSize)),
    daySeparatorColor: validHexColor(settings.daySeparatorColor, defaultChartAppearanceSettings.daySeparatorColor),
    daySeparatorStyle: isDaySeparatorStyle(settings.daySeparatorStyle) ? settings.daySeparatorStyle : defaultChartAppearanceSettings.daySeparatorStyle,
    daySeparatorsVisible:
      typeof settings.daySeparatorsVisible === "boolean" ? settings.daySeparatorsVisible : defaultChartAppearanceSettings.daySeparatorsVisible,
    downColor: validHexColor(settings.downColor, defaultChartAppearanceSettings.downColor),
    premarketColor: premarketColor.toUpperCase() === "#FBBF24" ? defaultChartAppearanceSettings.premarketColor : premarketColor,
    premarketOpacity: settings.premarketOpacity === 0.22 ? defaultChartAppearanceSettings.premarketOpacity : clampNumber(settings.premarketOpacity, 0, 0.6, defaultChartAppearanceSettings.premarketOpacity),
    upColor: validHexColor(settings.upColor, defaultChartAppearanceSettings.upColor),
    wickDownColor: validHexColor(settings.wickDownColor, defaultChartAppearanceSettings.wickDownColor),
    wickUpColor: validHexColor(settings.wickUpColor, defaultChartAppearanceSettings.wickUpColor),
    wickVisible: typeof settings.wickVisible === "boolean" ? settings.wickVisible : defaultChartAppearanceSettings.wickVisible
  };
}

function candleSeriesOptions(settings: ChartAppearanceSettings) {
  return {
    borderDownColor: settings.borderDownColor,
    borderUpColor: settings.borderUpColor,
    borderVisible: settings.borderVisible,
    downColor: settings.downColor,
    upColor: settings.upColor,
    wickDownColor: settings.wickDownColor,
    wickUpColor: settings.wickUpColor,
    wickVisible: settings.wickVisible
  };
}

function candleDataForTimeframe(candles: Candle[], timeframe: string): CandleSeriesDatum[] {
  const stepSeconds = chartTimeframeSeconds(timeframe);
  if (!stepSeconds || stepSeconds >= 24 * 60 * 60 || candles.length < 2) return candles;
  const sortedCandles = [...candles].sort((left, right) => left.time - right.time);
  const data: CandleSeriesDatum[] = [];
  const maxFillGapSeconds = 12 * 60 * 60;
  const stepMilliseconds = Math.max(1, Math.round(stepSeconds * 1_000));
  const maxSyntheticPoints = 20_000;
  let syntheticPoints = 0;
  for (let index = 0; index < sortedCandles.length; index += 1) {
    const candle = sortedCandles[index];
    if (index > 0) {
      const previous = sortedCandles[index - 1];
      const gap = candle.time - previous.time;
      if (gap > stepSeconds && gap <= maxFillGapSeconds) {
        const candleMilliseconds = Math.round(candle.time * 1_000);
        for (
          let timeMilliseconds = Math.round(previous.time * 1_000) + stepMilliseconds;
          timeMilliseconds < candleMilliseconds && syntheticPoints < maxSyntheticPoints;
          timeMilliseconds += stepMilliseconds
        ) {
          data.push({ time: timeMilliseconds / 1_000 });
          syntheticPoints += 1;
        }
      }
    }
    data.push(candle);
  }
  return data;
}

function chartTimelineData(candles: Candle[], timeframe: string): CandleSeriesDatum[] {
  return candleDataForTimeframe(candles, timeframe);
}

function candleValueBounds(candles: Candle[]): NumericBounds {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  candles.forEach((candle) => {
    [candle.low, candle.high].forEach((candidate) => {
      const value = Number(candidate);
      if (!Number.isFinite(value)) return;
      min = Math.min(min, value);
      max = Math.max(max, value);
    });
  });
  return Number.isFinite(min) && Number.isFinite(max) ? { max, min } : null;
}

function seriesValueBounds(series: ChartSeries): NumericBounds {
  let min = Number.POSITIVE_INFINITY;
  let max = Number.NEGATIVE_INFINITY;
  series.data.forEach((point) => {
    const value = Number(point.value);
    if (!Number.isFinite(value)) return;
    min = Math.min(min, value);
    max = Math.max(max, value);
  });
  return Number.isFinite(min) && Number.isFinite(max) ? { max, min } : null;
}

function stabilizeSeriesScale(renderer: AnySeriesApi, paneHeight: number, bounds: NumericBounds) {
  if (!bounds || paneHeight <= 1) return false;
  let top: number | null = null;
  let bottom: number | null = null;
  try {
    top = renderer.coordinateToPrice(0);
    bottom = renderer.coordinateToPrice(paneHeight);
  } catch {
    renderer.priceScale().applyOptions({ autoScale: true });
    return true;
  }

  const topValue = Number(top);
  const bottomValue = Number(bottom);
  const visibleMin = Math.min(topValue, bottomValue);
  const visibleMax = Math.max(topValue, bottomValue);
  const visibleSpan = visibleMax - visibleMin;
  const referenceMagnitude = Math.max(Math.abs(bounds.min), Math.abs(bounds.max), 1e-9);
  const referenceSpan = Math.max(bounds.max - bounds.min, referenceMagnitude / 10_000, 1e-9);
  const minimumVisibleSpan = referenceSpan / 10_000;
  const maximumVisibleSpan = referenceSpan * 10_000;
  const maximumVisibleMagnitude = referenceMagnitude + maximumVisibleSpan;
  const invalidTransform = (
    !Number.isFinite(topValue)
    || !Number.isFinite(bottomValue)
    || !Number.isFinite(visibleSpan)
    || visibleSpan < minimumVisibleSpan
    || visibleSpan > maximumVisibleSpan
    || Math.abs(visibleMin) > maximumVisibleMagnitude
    || Math.abs(visibleMax) > maximumVisibleMagnitude
  );
  if (invalidTransform) {
    renderer.priceScale().applyOptions({ autoScale: true });
    return true;
  }
  return false;
}

function buildTimelineDataSignature(timeline: CandleSeriesDatum[]) {
  if (!timeline.length) return "empty";
  const first = timeline[0];
  const last = timeline[timeline.length - 1];
  return `${timeline.length}:${first.time}:${last.time}`;
}

function chartTimeframeSeconds(timeframe: string) {
  const normalized = timeframe.trim().toLowerCase();
  if (normalized === "1mo") return 30 * 24 * 60 * 60;
  const match = normalized.match(/^(\d+)(ms|s|m|h|d)$/);
  if (!match) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
  if (match[2] === "ms") return value / 1_000;
  if (match[2] === "s") return value;
  if (match[2] === "m") return value * 60;
  if (match[2] === "h") return value * 60 * 60;
  return value * 24 * 60 * 60;
}

function volumeDataForSettings(payload: ChartPayload, settings: ChartAppearanceSettings) {
  return payload.volume.map((point, index) => {
    const candle = payload.candles[index];
    if (!candle) return point;
    return {
      ...point,
      color: candle.close >= candle.open ? rgbaFromHex(settings.upColor, 0.25) : rgbaFromHex(settings.downColor, 0.23)
    };
  });
}

function validHexColor(value: unknown, fallback: string) {
  return typeof value === "string" && /^#[0-9a-f]{6}$/i.test(value) ? value : fallback;
}

function isDaySeparatorStyle(value: unknown): value is DaySeparatorStyle {
  return value === "solid" || value === "dashed" || value === "dotted";
}

function clampNumber(value: unknown, min: number, max: number, fallback: number) {
  if (typeof value !== "number" || !Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(max, value));
}

function markersForSelection(markers: ChartMarker[], selected: Set<string>): SeriesMarker<Time>[] {
  return markers
    .filter((marker) => !marker.displayItemId || selected.has(marker.displayItemId.toLowerCase()))
    .map((marker, index) => ({
      color: typeof marker.color === "string" ? marker.color : "#1E3A5F",
      id: marker.id ?? `${marker.displayItemId ?? "marker"}:${marker.time}:${index}`,
      position: markerPosition(marker.position),
      shape: markerShape(marker.shape),
      size: clampNumber(marker.size, 0.1, 4, 1),
      text: typeof marker.text === "string" && marker.text.trim() ? marker.text : undefined,
      time: marker.time as Time
    }));
}

function markerPosition(value: unknown): "aboveBar" | "belowBar" | "inBar" {
  return value === "aboveBar" || value === "belowBar" || value === "inBar" ? value : "belowBar";
}

function markerShape(value: unknown): SeriesMarker<Time>["shape"] {
  return value === "arrowDown" || value === "arrowUp" || value === "square" || value === "circle" ? value : "circle";
}

function zoneBorderStyle(value: unknown): "solid" | "dashed" | "dotted" {
  return value === "dashed" || value === "dotted" ? value : "solid";
}

function rgbaFromHex(hex: string, opacity: number) {
  const normalized = validHexColor(hex, "#000000").replace("#", "");
  const red = parseInt(normalized.slice(0, 2), 16);
  const green = parseInt(normalized.slice(2, 4), 16);
  const blue = parseInt(normalized.slice(4, 6), 16);
  return `rgba(${red}, ${green}, ${blue}, ${clampNumber(opacity, 0, 1, 1)})`;
}

function mixHexColors(background: string, foreground: string, foregroundWeight: number) {
  const from = validHexColor(background, "#ffffff").replace("#", "");
  const to = validHexColor(foreground, "#344054").replace("#", "");
  const weight = clampNumber(foregroundWeight, 0, 1, 1);
  const channel = (offset: number) => Math.round(
    parseInt(from.slice(offset, offset + 2), 16) * (1 - weight)
      + parseInt(to.slice(offset, offset + 2), 16) * weight,
  ).toString(16).padStart(2, "0");
  return `#${channel(0)}${channel(2)}${channel(4)}`;
}

function priceZonePresentationColors(zone: PriceZone, chartBackground: string) {
  const confidence = typeof zone.confidence === "number" && Number.isFinite(zone.confidence)
    ? clampNumber(zone.confidence, 0, 1, 0)
    : null;
  const semanticFillColor = validHexColor(resolveChartColor(zone.fillColor || zone.color), "#1E3A5F");
  const semanticBorderColor = validHexColor(resolveChartColor(zone.borderColor || semanticFillColor), semanticFillColor);
  return {
    borderColor: confidence === null
      ? semanticBorderColor
      : mixHexColors(chartBackground, semanticBorderColor, 0.34 + 0.66 * confidence),
    confidence,
    fillColor: confidence === null
      ? semanticFillColor
      : mixHexColors(chartBackground, semanticFillColor, 0.28 + 0.72 * confidence),
  };
}

function defaultLegendSettings(series: ChartSeries): Required<LegendSeriesSettings> {
  return {
    color: resolveChartColor(series.color),
    currentLevelCount: 3,
    historyBars: 100,
    labelFontSize: 11,
    lineStyle: series.lineStyle ?? "solid",
    lineWidth: Math.max(1, Math.min(4, Math.round(series.lineWidth || 1))),
    maxHistoricalTags: 10,
    opacity: 1,
    showConnectors: true,
    showAxisLabel: false,
    showHistoricalLabels: true,
    showLabels: true,
    showValue: true,
    visible: series.defaultVisible !== false
  };
}

function resolveLegendSettings(settingsMap: LegendSettingsMap, key: string, series: ChartSeries): Required<LegendSeriesSettings> {
  const defaults = defaultLegendSettings(series);
  const stored = settingsMap[key] ?? {};
  return {
    color: resolveChartColor(stored.color || defaults.color),
    currentLevelCount: Math.max(1, Math.min(6, Math.round(stored.currentLevelCount ?? defaults.currentLevelCount))),
    historyBars: Math.max(10, Math.min(500, Math.round(stored.historyBars ?? defaults.historyBars))),
    labelFontSize: Math.max(9, Math.min(18, Math.round(stored.labelFontSize ?? defaults.labelFontSize))),
    lineStyle: stored.lineStyle || defaults.lineStyle,
    lineWidth: Math.max(1, Math.min(4, Math.round(stored.lineWidth ?? defaults.lineWidth))),
    maxHistoricalTags: Math.max(0, Math.min(30, Math.round(stored.maxHistoricalTags ?? defaults.maxHistoricalTags))),
    opacity: clampNumber(stored.opacity ?? defaults.opacity, 0, 1, 1),
    showConnectors: stored.showConnectors ?? defaults.showConnectors,
    showAxisLabel: stored.showAxisLabel ?? defaults.showAxisLabel,
    showHistoricalLabels: stored.showHistoricalLabels ?? defaults.showHistoricalLabels,
    showLabels: stored.showLabels ?? defaults.showLabels,
    showValue: stored.showValue ?? defaults.showValue,
    visible: stored.visible ?? defaults.visible
  };
}

type ResolvedPriceZoneLegendSettings = {
  currentLevelCount: number;
  historyBars: number;
  labelFontSize: number;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  maxHistoricalTags: number;
  opacity: number;
  showConnectors: boolean;
  showAxisLabel: boolean;
  showHistoricalLabels: boolean;
  visible: boolean;
};

function resolvePriceZoneLegendSettings(settingsMap: LegendSettingsMap, key: string, zone?: PriceZone): ResolvedPriceZoneLegendSettings {
  const stored = settingsMap[key] ?? {};
  return {
    currentLevelCount: Math.max(1, Math.min(6, Math.round(stored.currentLevelCount ?? 3))),
    historyBars: Math.max(10, Math.min(500, Math.round(stored.historyBars ?? 100))),
    labelFontSize: Math.max(9, Math.min(18, Math.round(stored.labelFontSize ?? 11))),
    lineStyle: stored.lineStyle ?? zoneBorderStyle(zone?.borderStyle),
    lineWidth: Math.max(1, Math.min(4, Math.round(stored.lineWidth ?? zone?.borderWidth ?? 1))),
    maxHistoricalTags: Math.max(0, Math.min(30, Math.round(stored.maxHistoricalTags ?? zone?.historicalTagLimitDefault ?? 10))),
    opacity: clampNumber(stored.opacity ?? 1, 0, 1, 1),
    showConnectors: stored.showConnectors !== false,
    showAxisLabel: stored.showAxisLabel ?? zone?.axisLabelDefault ?? false,
    showHistoricalLabels: stored.showHistoricalLabels ?? zone?.historicalLabelsDefault ?? false,
    visible: stored.visible ?? zone?.defaultVisible ?? true,
  };
}

function applySeriesSettings(renderer: AnySeriesApi, source: ChartSeries, settings: Required<LegendSeriesSettings>, useAdaptivePriceFormat: boolean, appearance = defaultChartAppearanceSettings) {
  const priceFormatOptions = useAdaptivePriceFormat ? { priceFormat: adaptiveSeriesPriceFormat(source) } : {};
  // Price overlays share the candle chart and must never widen its price range.
  // Oscillators live on independent charts and own their zero/range contract.
  const autoscaleInfoProvider = useAdaptivePriceFormat ? seriesAutoscaleInfoProvider(source) : () => null;
  if (source.style === "histogram") {
    renderer.applyOptions({ autoscaleInfoProvider, color: colorWithOpacity(settings.color, effectiveSeriesOpacity(source, settings)), lastValueVisible: source.lastValueVisible ?? true, ...priceFormatOptions, title: source.axisTitle ?? source.label, visible: settings.visible } as never);
  } else {
    renderer.applyOptions({
      autoscaleInfoProvider,
      color: colorWithOpacity(settings.color, effectiveSeriesOpacity(source, settings)),
      crosshairMarkerBorderWidth: 2,
      crosshairMarkerRadius: 4,
      crosshairMarkerVisible: true,
      lineStyle: toChartLineStyle(settings.lineStyle),
      lineWidth: toLineWidth(settings.lineWidth),
      lastValueVisible: source.lastValueVisible ?? true,
      ...priceFormatOptions,
      title: source.axisTitle ?? source.label,
      visible: settings.visible
    } as never);
  }
  syncRendererData(renderer, seriesDataForSettings(source, settings, appearance) as unknown as RendererDatum[], seriesStyleKey(source, settings, appearance));
}

function syncRendererData(renderer: AnySeriesApi, data: RendererDatum[], styleKey: string) {
  const previous = rendererDataCache.get(renderer as object);
  if (!previous || previous.styleKey !== styleKey || !canIncrementallyApply(previous.data, data)) {
    renderer.setData(data as never);
    rendererDataCache.set(renderer as object, { data, styleKey });
    return;
  }
  if (previous.data.length === data.length && rendererDatumEqual(previous.data.at(-1), data.at(-1))) {
    rendererDataCache.set(renderer as object, { data, styleKey });
    return;
  }
  const updateFrom = Math.max(0, previous.data.length - 1);
  for (let index = updateFrom; index < data.length; index += 1) {
    if (index < previous.data.length && rendererDatumEqual(previous.data[index], data[index])) continue;
    renderer.update(data[index] as never);
  }
  rendererDataCache.set(renderer as object, { data, styleKey });
}

function canIncrementallyApply(previous: RendererDatum[], next: RendererDatum[]): boolean {
  if (!previous.length) return next.length === 0;
  if (next.length < previous.length || next[0]?.time !== previous[0]?.time) return false;
  const priorTailIndex = previous.length - 1;
  if (next[priorTailIndex]?.time !== previous[priorTailIndex]?.time) return false;
  const sampleIndexes = new Set([0, Math.floor(priorTailIndex / 2), Math.max(0, priorTailIndex - 1)]);
  return [...sampleIndexes].every((index) => rendererDatumEqual(previous[index], next[index]));
}

function rendererDatumEqual(left: RendererDatum | undefined, right: RendererDatum | undefined): boolean {
  if (left === right) return true;
  if (!left || !right) return false;
  const leftKeys = Object.keys(left);
  const rightKeys = Object.keys(right);
  return leftKeys.length === rightKeys.length && leftKeys.every((key) => left[key] === right[key]);
}

function seriesStyleKey(source: ChartSeries, settings: Required<LegendSeriesSettings>, appearance: ChartAppearanceSettings): string {
  return [source.style, source.colorMode ?? "", source.opacity ?? 1, settings.color, settings.opacity, appearance.upColor, appearance.downColor, readNeutralChartColor()].join(":");
}

function volumeStyleKey(appearance: ChartAppearanceSettings): string {
  return `volume:${appearance.upColor}:${appearance.downColor}`;
}

function addChartSeries(chart: IChartApi, series: ChartSeries, settings: Required<LegendSeriesSettings>, paneIndex = 0): AnySeriesApi {
  const autoscaleInfoProvider = seriesAutoscaleInfoProvider(series);
  if (series.style === "histogram") {
    return chart.addSeries(HistogramSeries, {
      autoscaleInfoProvider,
      color: colorWithOpacity(settings.color, effectiveSeriesOpacity(series, settings)),
      priceFormat: adaptiveSeriesPriceFormat(series),
      priceLineVisible: false,
      priceScaleId: series.priceScaleId,
      lastValueVisible: series.lastValueVisible ?? true,
      title: series.axisTitle ?? series.label,
      visible: settings.visible
    }, paneIndex);
  }
  return chart.addSeries(LineSeries, {
    autoscaleInfoProvider,
    color: colorWithOpacity(settings.color, effectiveSeriesOpacity(series, settings)),
    crosshairMarkerBorderWidth: 2,
    crosshairMarkerRadius: 4,
    crosshairMarkerVisible: true,
    lineStyle: toChartLineStyle(settings.lineStyle),
    lineWidth: toLineWidth(settings.lineWidth),
    priceFormat: adaptiveSeriesPriceFormat(series),
    priceLineVisible: false,
    priceScaleId: series.priceScaleId,
    lastValueVisible: series.lastValueVisible ?? true,
    title: series.axisTitle ?? series.label,
    visible: settings.visible
  }, paneIndex);
}

function seriesAutoscaleInfoProvider(series: ChartSeries) {
  let loadedMin = 0;
  let loadedMax = 0;
  if (series.autoscaleScope === "loaded-series") {
    series.data.forEach((point) => {
      const value = Number(point.value);
      if (!Number.isFinite(value)) return;
      loadedMin = Math.min(loadedMin, value);
      loadedMax = Math.max(loadedMax, value);
    });
  }
  const minValue = Math.min(series.autoscaleMin ?? 0, loadedMin);
  const maxValue = Math.max(series.autoscaleMax ?? 0, loadedMax);
  return (baseImplementation: () => AutoscaleInfo | null) => includeRangeInAutoscale(baseImplementation, minValue, maxValue);
}

function adaptiveSeriesPriceFormat(series: ChartSeries) {
  let maxAbs = 0;
  series.data.forEach((point) => {
    const value = Math.abs(Number(point.value));
    if (Number.isFinite(value)) maxAbs = Math.max(maxAbs, value);
  });
  if (maxAbs > 0 && maxAbs < 0.0001) return seriesPriceFormat(8, 0.00000001);
  if (maxAbs > 0 && maxAbs < 0.001) return seriesPriceFormat(7, 0.0000001);
  if (maxAbs > 0 && maxAbs < 0.01) return seriesPriceFormat(6, 0.000001);
  if (maxAbs > 0 && maxAbs < 0.1) return seriesPriceFormat(5, 0.00001);
  if (maxAbs > 0 && maxAbs < 1) return seriesPriceFormat(4, 0.0001);
  if (maxAbs > 0 && maxAbs < 10) return seriesPriceFormat(3, 0.001);
  return seriesPriceFormat(2, 0.01);
}

function seriesPriceFormat(precision: number, minMove: number) {
  return { type: "price" as const, precision, minMove };
}

function includeZeroInAutoscale(baseImplementation: () => AutoscaleInfo | null): AutoscaleInfo | null {
  return includeRangeInAutoscale(baseImplementation, 0, 0);
}

function includeRangeInAutoscale(baseImplementation: () => AutoscaleInfo | null, minValue: number, maxValue: number): AutoscaleInfo | null {
  const autoscale = baseImplementation();
  if (!autoscale?.priceRange) return autoscale;
  return {
    ...autoscale,
    priceRange: {
      minValue: Math.min(autoscale.priceRange.minValue, minValue),
      maxValue: Math.max(autoscale.priceRange.maxValue, maxValue)
    }
  };
}

function padCandleAutoscale(baseImplementation: () => AutoscaleInfo | null): AutoscaleInfo | null {
  const autoscale = baseImplementation();
  if (!autoscale?.priceRange) return autoscale;
  const minValue = autoscale.priceRange.minValue;
  const maxValue = autoscale.priceRange.maxValue;
  const range = Math.abs(maxValue - minValue);
  const padding = Math.max(0.01, range * 0.18, Math.abs(maxValue) * 0.003);
  return {
    ...autoscale,
    priceRange: {
      minValue: minValue - padding,
      maxValue: maxValue + padding
    }
  };
}

function effectiveSeriesOpacity(series: ChartSeries, settings: Required<LegendSeriesSettings>) {
  return clampNumber((series.opacity ?? 1) * settings.opacity, 0, 1, 1);
}

function colorWithOpacity(color: string, opacity: number) {
  const resolved = resolveChartColor(color);
  if (opacity >= 0.999 || !validHexColor(resolved, "")) return resolved;
  return rgbaFromHex(resolved, opacity);
}

function resolveChartColor(color: string) {
  const value = String(color || "").trim();
  const variable = value.match(/^var\((--[a-z0-9-_]+)\)$/i);
  if (!variable || typeof document === "undefined") return value || "#344054";
  return window.getComputedStyle(document.documentElement).getPropertyValue(variable[1]).trim() || "#344054";
}

function seriesDataForSettings(series: ChartSeries, settings: Required<LegendSeriesSettings>, appearance = defaultChartAppearanceSettings) {
  if (!settings.visible) return [];
  const defaultColor = defaultLegendSettings(series).color;
  const opacity = effectiveSeriesOpacity(series, settings);
  const applyOpacity = (color: string) => colorWithOpacity(color, opacity);
  const neutralColor = readNeutralChartColor();
  if (series.colorMode === "sign") {
    return series.data.map(({ tone: _tone, ...point }) => ({
      ...point,
      color: applyOpacity(signColor(point.value, appearance)),
    }));
  }
  if (series.colorMode === "confidence-sign") {
    return series.data.map(({ tone: _tone, ...point }) => ({
      ...point,
      color: colorWithOpacity(signColor(point.value, appearance), opacity * (0.3 + 0.7 * clampNumber(point.confidence, 0, 1, 0))),
    }));
  }
  if (series.style !== "histogram") {
    if (settings.color && settings.color !== defaultColor) {
      return series.data.map(({ tone: _tone, ...point }) => ({ ...point, color: applyOpacity(settings.color) }));
    }
    return series.data.map(({ tone, ...point }) => ({
      ...point,
      ...(tone === "buy"
        ? { color: applyOpacity(appearance.upColor) }
        : tone === "sell"
          ? { color: applyOpacity(appearance.downColor) }
          : tone === "neutral"
            ? { color: applyOpacity(neutralColor) }
          : point.color
            ? { color: applyOpacity(point.color) }
            : {}),
    }));
  }
  if (!settings.color || settings.color === defaultColor) {
    if (series.column === "macd_histogram") {
      return series.data.map((point) => ({ ...point, color: applyOpacity(point.value >= 0 ? appearance.upColor : appearance.downColor) }));
    }
    return series.data.map(({ tone, ...point }) => ({
      ...point,
      ...(tone === "buy"
        ? { color: applyOpacity(appearance.upColor) }
        : tone === "sell"
          ? { color: applyOpacity(appearance.downColor) }
          : tone === "neutral"
            ? { color: applyOpacity(neutralColor) }
          : point.color
            ? { color: applyOpacity(point.color) }
            : {}),
    }));
  }
  return series.data.map((point) => ({ ...point, color: applyOpacity(settings.color) }));
}

function signColor(value: number | null, appearance = defaultChartAppearanceSettings) {
  if (value != null && value > 0) return appearance.upColor;
  if (value != null && value < 0) return appearance.downColor;
  return readNeutralChartColor();
}

function readNeutralChartColor() {
  if (typeof window === "undefined") return "#667085";
  const styles = window.getComputedStyle(document.documentElement);
  return styles.getPropertyValue("--muted-foreground").trim() || readChartPalette().text;
}

function toChartLineStyle(style: LegendLineStyle) {
  if (style === "dashed") return LineStyle.Dashed;
  if (style === "dotted") return LineStyle.Dotted;
  return LineStyle.Solid;
}

function toLineWidth(value: number): LineWidth {
  const width = Math.max(1, Math.min(4, Math.round(value)));
  return width as LineWidth;
}

function readChartPalette(): ChartPalette {
  const styles = window.getComputedStyle(document.documentElement);
  return {
    background: styles.getPropertyValue("--chart-background").trim() || styles.getPropertyValue("--card").trim() || "#ffffff",
    grid: styles.getPropertyValue("--chart-grid").trim() || styles.getPropertyValue("--border").trim() || "#f2f4f7",
    text: styles.getPropertyValue("--chart-text").trim() || styles.getPropertyValue("--muted-foreground").trim() || "#344054"
  };
}

function chartOptions(
  width: number,
  height: number,
  compact = false,
  palette: ChartPalette = readChartPalette(),
  settings: ChartAppearanceSettings = defaultChartAppearanceSettings,
  timeframe = "1m",
  showTimeScale = true,
  showLeftPriceScale = false,
) {
  const timeframeSeconds = chartTimeframeSeconds(timeframe);
  const showSeconds = timeframeSeconds !== null && timeframeSeconds < 60;
  const macroTimeframe = timeframe === "1d" || timeframe === "1mo";
  return {
    width: Math.max(320, width),
    // The chart must render at the height allocated by the pane stack. A larger
    // internal minimum pushes the bottom-owned time scale below a resized pane,
    // where the chart shell clips it until the user moves a pane separator.
    height: Math.max(1, Math.floor(height)),
    layout: {
      attributionLogo: false,
      background: { color: palette.background },
      panes: {
        enableResize: true,
        separatorColor: palette.grid,
        separatorHoverColor: colorWithOpacity(palette.text, 0.16),
      },
      textColor: palette.text,
    },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid }
    },
    localization: {
      timeFormatter: (timeValue: Time) => formatMarketDateTime(timeValue, timeframe)
    },
    crosshair: {
      horzLine: { color: palette.text, labelBackgroundColor: palette.text, labelVisible: true, style: LineStyle.Dotted, visible: true, width: 1 as LineWidth },
      mode: 0,
      vertLine: { color: palette.grid, labelBackgroundColor: palette.text, labelVisible: true, style: LineStyle.Dotted, visible: true, width: 1 as LineWidth },
    },
    rightPriceScale: { borderColor: palette.grid, minimumWidth: CHART_PRICE_SCALE_MIN_WIDTH },
    leftPriceScale: { borderColor: palette.grid, minimumWidth: CHART_PRICE_SCALE_MIN_WIDTH, visible: showLeftPriceScale },
    timeScale: {
      borderColor: palette.grid,
      fixLeftEdge: true,
      // Keep history bounded on the left, but leave the future side navigable so
      // traders can move the latest bar away from the price scale and reserve
      // working space for bars that have not arrived yet.
      fixRightEdge: false,
      rightOffset: compact ? 1 : 2,
      shiftVisibleRangeOnNewBar: true,
      barSpacing: compact ? Math.max(12, Math.round(settings.candleSize * 0.55)) : settings.candleSize,
      minBarSpacing: 0.2,
      visible: showTimeScale,
      timeVisible: !macroTimeframe,
      secondsVisible: showSeconds,
      tickMarkFormatter: (timeValue: Time) => formatMarketAxisTime(timeValue, timeframe)
    }
  };
}

const marketDateFormatter = new Intl.DateTimeFormat("en-CA", {
  day: "2-digit",
  month: "2-digit",
  timeZone: "America/New_York",
  year: "numeric"
});
const marketDateKeyFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  month: "2-digit",
  timeZone: "America/New_York",
  year: "numeric"
});
const marketTimePartFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  timeZone: "America/New_York"
});

const marketAxisFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  timeZone: "America/New_York"
});

const marketDailyAxisFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  month: "short",
  timeZone: "America/New_York"
});

const marketMonthlyAxisFormatter = new Intl.DateTimeFormat("en-US", {
  month: "short",
  timeZone: "America/New_York",
  year: "2-digit"
});

const marketMacroDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  month: "short",
  timeZone: "America/New_York",
  year: "numeric"
});

const marketSecondAxisFormatter = new Intl.DateTimeFormat("en-US", {
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  second: "2-digit",
  timeZone: "America/New_York"
});

const marketSubsecondAxisFormatter = new Intl.DateTimeFormat("en-US", {
  fractionalSecondDigits: 1,
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  second: "2-digit",
  timeZone: "America/New_York"
});

const marketDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  month: "short",
  timeZone: "America/New_York",
  year: "numeric"
});

const marketSecondDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  month: "short",
  second: "2-digit",
  timeZone: "America/New_York",
  year: "numeric"
});

const marketSubsecondDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  fractionalSecondDigits: 1,
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  month: "short",
  second: "2-digit",
  timeZone: "America/New_York",
  year: "numeric"
});

type ChartRangeTarget = IChartApi | null | IChartApi[];
const DAILY_MACRO_WINDOW_BARS = 180;
const MONTHLY_MACRO_WINDOW_BARS = 24;

function fitLatestSession(target: ChartRangeTarget, candles: Candle[], timeframe = "") {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = candleDataForTimeframe(candles, timeframe);
  if (isMacroTimeframe(timeframe)) {
    setChartLogicalRange(charts, loadedRange(timeline.length, 0.025));
    return;
  }
  const latestDay = marketDate(candles[candles.length - 1].time);
  let firstIndex = -1;
  let lastIndex = -1;
  timeline.forEach((item, index) => {
    if (marketDate(item.time) === latestDay) {
      if (firstIndex < 0) firstIndex = index;
      lastIndex = index;
    }
  });
  if (firstIndex < 0 || lastIndex < 0) return;
  setChartLogicalRange(charts, { from: Math.max(-1, firstIndex - 1), to: Math.max(firstIndex + 1, lastIndex + 1) });
}

function resetChartViewport(chart: IChartApi | null, candles: Candle[], timeframe: string, chartWidth: number, candleSize: number) {
  if (!chart) return;
  const timeScale = chart.timeScale();
  const normalizedCandleSize = clampNumber(candleSize, 8, 80, defaultChartAppearanceSettings.candleSize);
  timeScale.applyOptions({
    barSpacing: normalizedCandleSize,
    rightOffset: 2
  });
  const timelineLength = candleDataForTimeframe(candles, timeframe).length;
  if (!timelineLength || chartWidth <= 0) {
    timeScale.scrollToPosition(2, false);
    return;
  }
  const rightEdge = timelineLength + 1;
  const visibleBars = Math.max(5, chartWidth / normalizedCandleSize);
  timeScale.setVisibleLogicalRange({ from: rightEdge - visibleBars, to: rightEdge });
}

function loadedRange(length: number, paddingRatio: number) {
  const last = Math.max(0, length - 1);
  const padding = Math.max(0.5, Math.min(4, length * paddingRatio));
  return { from: -padding, to: last + padding };
}

function latestRangeActionLabel(timeframe: string) {
  if (isMacroTimeframe(timeframe)) return "Fit range";
  return "Fit session";
}

function isMacroTimeframe(timeframe: string) {
  return timeframe === "1d" || timeframe === "1mo";
}

function fitCandles(payload: ChartPayload | null | undefined) {
  return (payload?.candles ?? []).filter(
    (candle) =>
      Number.isFinite(candle.time) &&
      Number.isFinite(candle.open) &&
      Number.isFinite(candle.high) &&
      Number.isFinite(candle.low) &&
      Number.isFinite(candle.close)
  );
}

function candleWindow(candles: Candle[]) {
  if (!candles.length) return null;
  return { first: candles[0].time, last: candles[candles.length - 1].time };
}

function fitInitialRange(chart: IChartApi | null, candles: Candle[], timeframe = "", mode: ChartPanelProps["initialFitMode"] = "default") {
  if (!chart || !candles.length) return;
  if (mode === "live_first_10") {
    fitLiveFirstTenMinutes(chart, candles, timeframe);
    return;
  }
  if (mode === "recent") {
    centerLatest(chart, candles, timeframe);
    return;
  }
  if (mode === "last_market_day") {
    fitLastMarketDay(chart, candles, timeframe);
    return;
  }
  if (hasMultipleMarketDates(candles)) {
    const timeline = candleDataForTimeframe(candles, timeframe);
    chart.timeScale().setVisibleLogicalRange({ from: -1, to: Math.max(8, timeline.length) });
    return;
  }
  fitLatestSession(chart, candles, timeframe);
}

function fitLiveFirstTenMinutes(target: ChartRangeTarget, candles: Candle[], timeframe: string) {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = candleDataForTimeframe(candles, timeframe);
  const lastCandle = candles[candles.length - 1];
  const lastIndex = nearestTimelineIndex(timeline, lastCandle.time);
  const stepSeconds = chartTimeframeSeconds(timeframe) ?? 60;
  const targetBars = Math.max(4, Math.ceil((10 * 60) / stepSeconds));
  const halfSpan = Math.max(2, Math.ceil(targetBars / 2));
  setChartLogicalRange(charts, {
    from: Math.max(-1, lastIndex - halfSpan),
    to: Math.min(timeline.length + halfSpan, lastIndex + halfSpan),
  });
}

function fitLastMarketDay(chart: IChartApi | null, candles: Candle[], timeframe: string) {
  fitLatestSession(chart, candles, timeframe);
}

function centerLatest(target: ChartRangeTarget, candles: Candle[], timeframe = "") {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = candleDataForTimeframe(candles, timeframe);
  const lastCandle = candles[candles.length - 1];
  const last = nearestTimelineIndex(timeline, lastCandle.time);
  if (isMacroTimeframe(timeframe)) {
    const requestedBars = timeframe === "1mo" ? MONTHLY_MACRO_WINDOW_BARS : DAILY_MACRO_WINDOW_BARS;
    const span = Math.max(1, Math.min(requestedBars, timeline.length));
    const leftPadding = Math.max(0.5, Math.min(2, span * 0.025));
    const growthSpace = Math.max(0.75, Math.min(3, span * 0.06));
    setChartLogicalRange(charts, { from: Math.max(-leftPadding, last - span + 1 - leftPadding), to: last + growthSpace });
    return;
  }
  const preferredSpan = Math.ceil(timeline.length * 0.18);
  const span = Math.min(180, Math.max(60, preferredSpan));
  const futureSpace = Math.max(3, Math.ceil(span * 0.22));
  setChartLogicalRange(charts, { from: last - (span - futureSpace), to: last + futureSpace });
}

function centerReferenceOrLatest(target: ChartRangeTarget, candles: Candle[], reference: ChartReference | null | undefined, timeframe: string, mode: ChartPanelProps["initialFitMode"] = "default") {
  if (reference) {
    fitAroundReference(target, candles, reference, timeframe);
    return;
  }
  if (mode === "live_first_10") {
    fitLiveFirstTenMinutes(target, candles, timeframe);
    return;
  }
  if (mode === "last_market_day") {
    fitLatestSession(target, candles, timeframe);
    return;
  }
  centerLatest(target, candles, timeframe);
}

function fitAroundReference(target: ChartRangeTarget, candles: Candle[], reference: ChartReference, timeframe: string) {
  const charts = chartRangeTargets(target);
  const chart = charts[0];
  if (!chart || !candles.length) return;
  const referenceTime = resolveFitReferenceTime(reference, candles);
  if (referenceTime === null) {
    fitInitialRange(chart, candles, timeframe);
    return;
  }
  const timeline = candleDataForTimeframe(candles, timeframe);
  const referenceIndex = nearestTimelineIndex(timeline, referenceTime);
  const startIndex = typeof reference.startTime === "number" ? nearestTimelineIndex(timeline, reference.startTime) : referenceIndex;
  const endIndex = typeof reference.endTime === "number" ? nearestTimelineIndex(timeline, reference.endTime) : referenceIndex;
  const tradeSpan = Math.max(1, Math.abs(endIndex - startIndex));
  const span = Math.min(timeline.length, Math.max(60, Math.min(240, tradeSpan * 5)));
  const halfSpan = Math.ceil(span / 2);
  setChartLogicalRange(charts, {
    from: Math.max(-1, referenceIndex - halfSpan),
    to: Math.min(timeline.length + halfSpan, referenceIndex + halfSpan),
  });
}

function chartRangeTargets(target: ChartRangeTarget) {
  return (Array.isArray(target) ? target : [target]).filter((chart): chart is IChartApi => Boolean(chart));
}

function setChartLogicalRange(charts: IChartApi[], range: { from: number; to: number }) {
  charts.forEach((chart) => chart.timeScale().setVisibleLogicalRange(range as LogicalRange));
}

function resolveFitReferenceTime(reference: ChartReference, candles: Candle[]) {
  if (typeof reference.time === "number" && Number.isFinite(reference.time)) {
    return reference.time;
  }
  return resolveReferenceTime(reference, candles);
}

function nearestTimelineIndex(timeline: CandleSeriesDatum[], targetTime: number) {
  if (!timeline.length) return 0;
  let nearest = 0;
  let nearestDistance = Number.POSITIVE_INFINITY;
  timeline.forEach((item, index) => {
    const distance = Math.abs(item.time - targetTime);
    if (distance < nearestDistance) {
      nearest = index;
      nearestDistance = distance;
    }
  });
  return nearest;
}

function buildChartFitKey(ticker: string, timeframe: string, referenceKey: string, candles: Candle[]) {
  return `${ticker}:${timeframe}:${referenceKey || "no-reference"}:${candles.length ? "data" : "empty"}`;
}

function hasMultipleMarketDates(candles: Candle[]) {
  if (candles.length < 2) return false;
  const first = marketDate(candles[0].time);
  return candles.some((candle) => marketDate(candle.time) !== first);
}

// A view more than four orders of magnitude wider or narrower than its loaded
// data has no analytical value and approaches unstable canvas transforms.
function drawRegions(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement | null,
  regions: Region[],
  priceZones: PriceZone[],
  tradeAnnotations: TradeAnnotation[],
  candles: Candle[],
  timeline: CandleSeriesDatum[],
  settings: ChartAppearanceSettings,
  legendSettings: LegendSettingsMap,
  liveEntryLine?: LiveEntryLine | null
) {
  if (!layer) return;
  const plotLayer = drawSessionRegions(chart, layer, regions, timeline, candles, settings, true, true);
  if (!plotLayer) return;
  const barWidth = estimateBarWidth(chart, candles);
  const candleDuration = estimateCandleDuration(candles);
  drawPriceZones(chart, priceSeries, layer, priceZones, candles, barWidth, candleDuration, legendSettings);
  drawTradeAnnotations(chart, priceSeries, layer, tradeAnnotations, candles, barWidth);
  drawLiveEntryLine(chart, priceSeries, layer, candles, liveEntryLine);
}

function drawSessionRegions(
  chart: IChartApi,
  layer: HTMLDivElement | null,
  regions: Region[],
  timeline: CandleSeriesDatum[],
  candles: Candle[],
  settings: ChartAppearanceSettings,
  drawSeparators: boolean,
  preservePriceZoneCanvas = false,
) {
  if (!layer) return null;
  clearOverlayLayer(layer, preservePriceZoneCanvas);
  const plotLayer = document.createElement("div");
  plotLayer.className = "session-plot-region";
  plotLayer.style.left = `${chart.priceScale("left").width()}px`;
  plotLayer.style.right = `${chart.priceScale("right").width()}px`;
  plotLayer.style.bottom = `${chart.timeScale().height()}px`;
  layer.appendChild(plotLayer);
  const barWidth = estimateBarWidth(chart, candles);
  regions.forEach((region) => {
    const coordinates = sessionRegionCoordinates(chart, region, timeline);
    if (!coordinates) return;
    const span = clippedHorizontalSpan(coordinates.start, coordinates.end, layer.clientWidth);
    if (!span) return;
    const node = document.createElement("div");
    node.className = "session-region";
    node.title = region.label;
    node.style.left = `${span.left}px`;
    node.style.width = `${span.width}px`;
    node.style.background = sessionRegionColor(region, settings);
    plotLayer.appendChild(node);
  });
  if (drawSeparators) drawDaySeparators(chart, plotLayer, candles, settings, barWidth);
  return plotLayer;
}

function clearOverlayLayer(layer: HTMLDivElement, preservePriceZoneCanvas: boolean) {
  Array.from(layer.children).forEach((child) => {
    if (preservePriceZoneCanvas && child.classList.contains("price-zone-canvas")) return;
    child.remove();
  });
}

function drawLiveEntryLine(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement,
  candles: Candle[],
  liveEntryLine?: LiveEntryLine | null
) {
  if (!priceSeries || !candles.length || !liveEntryLine || !Number.isFinite(liveEntryLine.price)) return;
  const y = priceSeries.priceToCoordinate(liveEntryLine.price);
  if (y === null) return;
  const left = 0;
  const width = Math.max(80, layer.clientWidth);
  const line = document.createElement("div");
  line.className = "live-entry-price-line";
  line.style.left = `${left}px`;
  line.style.top = `${y}px`;
  line.style.width = `${width}px`;
  line.style.borderColor = "#2563eb";

  const control = document.createElement("div");
  control.className = "live-entry-position-control";

  const sizeBadge = document.createElement("span");
  sizeBadge.className = "live-entry-size-badge";
  sizeBadge.textContent = liveEntryLine.quantity.toLocaleString();
  control.appendChild(sizeBadge);

  const pnlBadge = document.createElement("span");
  pnlBadge.className = liveEntryLine.pnl >= 0 ? "live-entry-pnl-badge positive" : "live-entry-pnl-badge negative";
  pnlBadge.textContent = formatMoneyValue(liveEntryLine.pnl);
  control.appendChild(pnlBadge);

  if (liveEntryLine.onClose) {
    const closeButton = document.createElement("button");
    closeButton.className = "live-entry-close-button";
    closeButton.type = "button";
    closeButton.title = "Close position";
    closeButton.setAttribute("aria-label", "Close position");
    closeButton.textContent = "x";
    closeButton.addEventListener("pointerdown", (event) => event.stopPropagation());
    closeButton.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      liveEntryLine.onClose?.();
    });
    control.appendChild(closeButton);
  }
  line.appendChild(control);
  layer.appendChild(line);
}

function formatMoneyValue(value: number) {
  const sign = value < 0 ? "-" : "";
  return `${sign}$${Math.abs(value).toFixed(2)}`;
}

function syncPriceZoneAxisLines(
  priceSeries: ISeriesApi<"Candlestick"> | null,
  zones: PriceZone[],
  legendSettings: LegendSettingsMap,
  runtimes: Map<string, PriceZoneAxisLineRuntime>,
) {
  if (!priceSeries) return;
  const nextKeys = new Set<string>();
  const chartBackground = validHexColor(readChartPalette().background, "#ffffff");
  zones.forEach((zone) => {
    if (!zone.latest || typeof zone.axisLabelDefault !== "boolean") return;
    const compactLabel = zone.compactLabel?.trim();
    const price = (zone.lower + zone.upper) / 2;
    if (!compactLabel || !Number.isFinite(price) || price <= 0) return;
    const settingsId = zone.settingsId || zone.displayItemId || `zone:${zone.label}`;
    const settings = resolvePriceZoneLegendSettings(legendSettings, priceZoneLegendKey(settingsId), zone);
    if (!settings.visible || !settings.showAxisLabel || settings.opacity <= 0) return;
    const key = `${settingsId}:${compactLabel}`;
    const presentationColor = priceZonePresentationColors(zone, chartBackground).borderColor;
    // Lightweight Charts intentionally converts price-axis label colors to opaque RGB
    // while deriving contrast text, so an RGBA alpha channel is discarded. Precompose
    // the requested opacity against the active chart surface to preserve the same visible
    // result across themes without bypassing the library's accessible text-color choice.
    const axisLabelColor = mixHexColors(chartBackground, presentationColor, settings.opacity);
    const signature = `${compactLabel}|${price}|${axisLabelColor}`;
    const existing = runtimes.get(key);
    nextKeys.add(key);
    if (!existing) {
      runtimes.set(key, {
        line: priceSeries.createPriceLine({
          axisLabelColor,
          axisLabelVisible: true,
          color: axisLabelColor,
          lineVisible: false,
          price,
          title: compactLabel,
        }),
        signature,
      });
    } else if (existing.signature !== signature) {
      existing.line.applyOptions({
        axisLabelColor,
        axisLabelVisible: true,
        color: axisLabelColor,
        lineVisible: false,
        price,
        title: compactLabel,
      });
      existing.signature = signature;
    }
  });
  runtimes.forEach((runtime, key) => {
    if (nextKeys.has(key)) return;
    priceSeries.removePriceLine(runtime.line);
    runtimes.delete(key);
  });
}

function drawPriceZonePrimitiveGeometry(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  zones: PriceZone[],
  candles: Candle[],
  legendSettings: LegendSettingsMap,
) {
  if (!zones.length || width < 1 || height < 1) return;
  const barWidth = estimateBarWidth(chart, candles);
  const candleDuration = estimateCandleDuration(candles);
  const chartBackground = validHexColor(readChartPalette().background, "#ffffff");
  const historicalBySettings = new Map<string, PriceZone[]>();
  zones.forEach((zone) => {
    const id = zone.settingsId || zone.displayItemId || `zone:${zone.label}`;
    const group = historicalBySettings.get(id);
    if (group) group.push(zone);
    else historicalBySettings.set(id, [zone]);
  });
  historicalBySettings.forEach((itemZones, id) => {
    const settings = resolvePriceZoneLegendSettings(legendSettings, priceZoneLegendKey(id), itemZones[itemZones.length - 1]);
    if (!settings.visible) return;
    const historyStart = candles[Math.max(0, candles.length - settings.historyBars)]?.time ?? Number.NEGATIVE_INFINITY;
    itemZones.forEach((zone) => {
      if (!(zone.latest || zone.end > historyStart)) return;
      if (
        zone.currentLevelSide
        && !zone.currentLevelStrongest
        && (zone.currentLevelDistanceRank ?? Number.POSITIVE_INFINITY) > settings.currentLevelCount
      ) return;
      const coordinates = priceZoneCoordinates(chart, zone, candles, barWidth, candleDuration);
      if (!coordinates) return;
      const upper = priceSeries.priceToCoordinate(zone.upper);
      const lower = priceSeries.priceToCoordinate(zone.lower);
      if (upper === null || lower === null) return;
      const center = (upper + lower) / 2;
      if (center < 0 || center > height) return;
      const span = clippedHorizontalSpan(
        coordinates.start,
        zone.extendToRightEdge ? Math.max(coordinates.end, chart.timeScale().width() - 4) : coordinates.end,
        width,
      );
      if (!span) return;
      let top = Math.min(upper, lower);
      let zoneHeight = Math.max(2, Math.abs(lower - upper));
      const minPixelHeight = clampNumber(zone.minPixelHeight, 0, 32, 0);
      const maxPixelHeight = clampNumber(zone.maxPixelHeight, 0, 96, 0);
      if (zone.zoneHeightMode === "fixed_px") {
        zoneHeight = Math.max(2, minPixelHeight, maxPixelHeight || minPixelHeight || 3);
        top = center - zoneHeight / 2;
      } else {
        if (minPixelHeight > 0 && zoneHeight < minPixelHeight) {
          zoneHeight = minPixelHeight;
          top = center - zoneHeight / 2;
        }
        if (maxPixelHeight > 0 && zoneHeight > maxPixelHeight) {
          zoneHeight = maxPixelHeight;
          top = center - zoneHeight / 2;
        }
      }
      if (span.width < 1 || zoneHeight < 1) return;
      const { borderColor, confidence, fillColor } = priceZonePresentationColors(zone, chartBackground);
      const lineOnly = zone.renderMode === "line" || zone.annotationKind === "bos" || zone.annotationKind === "choch";
      const baseFillOpacity = clampNumber(zone.fillOpacity, 0.02, 0.35, 0.08);
      const fillOpacity = lineOnly ? 0 : baseFillOpacity * (confidence === null ? 1 : 0.45 + 0.55 * confidence) * settings.opacity;
      const borderOpacity = lineOnly
        ? settings.opacity
        : zone.currentLevelSide ? 0 : confidence === null
        ? clampNumber(zone.borderOpacity, 0, 0.35, Math.max(baseFillOpacity * 1.8, 0.12)) * settings.opacity
        : (0.24 + 0.7 * confidence) * settings.opacity;
      const lineWidth = lineOnly || confidence === null
        ? settings.lineWidth
        : Math.max(1, Math.min(6, settings.lineWidth * (0.75 + 1.25 * confidence)));
      context.save();
      context.fillStyle = rgbaFromHex(fillColor, fillOpacity);
      context.strokeStyle = rgbaFromHex(borderColor, borderOpacity);
      context.lineWidth = lineWidth;
      context.setLineDash(canvasLineDash(settings.lineStyle, lineWidth));
      if (zone.annotationKind === "bos" || zone.annotationKind === "choch") {
        if (settings.showConnectors) {
          const eventX = zone.eventTime ? chart.timeScale().timeToCoordinate(zone.eventTime as Time) : coordinates.end;
          const connector = clippedHorizontalSpan(coordinates.start, eventX ?? coordinates.end, width);
          if (connector) {
            context.beginPath();
            context.moveTo(connector.left, center);
            context.lineTo(connector.right, center);
            context.stroke();
          }
        }
      } else if (lineOnly) {
        context.beginPath();
        context.moveTo(span.left, center);
        context.lineTo(span.right, center);
        context.stroke();
      } else {
        context.fillRect(span.left, top, span.width, zoneHeight);
        if (borderOpacity > 0 && lineWidth > 0) context.strokeRect(span.left, top, span.width, zoneHeight);
      }
      context.restore();
    });
  });
}

function drawPriceZones(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement,
  zones: PriceZone[],
  candles: Candle[],
  barWidth: number,
  candleDuration: number,
  legendSettings: LegendSettingsMap,
) {
  const width = Math.max(1, layer.clientWidth);
  const height = Math.max(1, layer.clientHeight);
  const pixelRatio = Math.max(1, window.devicePixelRatio || 1);
  let canvas = Array.from(layer.children).find((child): child is HTMLCanvasElement => child instanceof HTMLCanvasElement && child.classList.contains("price-zone-canvas"));
  if (!canvas) {
    canvas = document.createElement("canvas");
    canvas.className = "price-zone-canvas";
    layer.prepend(canvas);
  }
  const bitmapWidth = Math.max(1, Math.round(width * pixelRatio));
  const bitmapHeight = Math.max(1, Math.round(height * pixelRatio));
  if (canvas.width !== bitmapWidth) canvas.width = bitmapWidth;
  if (canvas.height !== bitmapHeight) canvas.height = bitmapHeight;
  canvas.style.width = `${width}px`;
  canvas.style.height = `${height}px`;
  const context = canvas.getContext("2d");
  if (!context) return;
  context.setTransform(1, 0, 0, 1, 0, 0);
  context.clearRect(0, 0, bitmapWidth, bitmapHeight);
  if (!priceSeries || !zones.length) return;
  context.setTransform(pixelRatio, 0, 0, pixelRatio, 0, 0);
  const plotBottom = Math.max(0, height - chart.timeScale().height());
  const chartBackground = validHexColor(readChartPalette().background, "#ffffff");
  const lineLabelBoxes: CanvasBox[] = [];
  let candleBoxes: CanvasBox[] | null = null;
  const historicalBySettings = new Map<string, PriceZone[]>();
  zones.forEach((zone) => {
    const id = zone.settingsId || zone.displayItemId || `zone:${zone.label}`;
    const group = historicalBySettings.get(id);
    if (group) group.push(zone);
    else historicalBySettings.set(id, [zone]);
  });
  historicalBySettings.forEach((itemZones, id) => {
    const settings = resolvePriceZoneLegendSettings(legendSettings, priceZoneLegendKey(id), itemZones[itemZones.length - 1]);
    if (!settings.visible) return;
    const historyStart = candles[Math.max(0, candles.length - settings.historyBars)]?.time ?? Number.NEGATIVE_INFINITY;
    const eligibleZones = itemZones.filter((zone) => {
      if (!(zone.latest || zone.end > historyStart)) return false;
      if (!zone.currentLevelSide) return true;
      return Boolean(zone.currentLevelStrongest)
        || (zone.currentLevelDistanceRank ?? Number.POSITIVE_INFINITY) <= settings.currentLevelCount;
    });
    const historicalTagZones = new Set(
      settings.maxHistoricalTags > 0
        ? eligibleZones.filter((zone) => Boolean(zone.compactLabel) && (!zone.latest || isStructureBreakZone(zone))).slice(-settings.maxHistoricalTags)
        : [],
    );
    eligibleZones.forEach((zone) => {
      const coordinates = priceZoneCoordinates(chart, zone, candles, barWidth, candleDuration);
      if (!coordinates) return;
      const upper = priceSeries.priceToCoordinate(zone.upper);
      const lower = priceSeries.priceToCoordinate(zone.lower);
      if (upper === null || lower === null) return;
      const center = (upper + lower) / 2;
      if (center < 0 || center > plotBottom) return;
      const span = clippedHorizontalSpan(
        coordinates.start,
        zone.extendToRightEdge ? Math.max(coordinates.end, chart.timeScale().width() - 4) : coordinates.end,
        width,
      );
      if (!span) return;
      const { borderColor } = priceZonePresentationColors(zone, chartBackground);
      let labelSpan: HorizontalSpan | null = span;
      if (zone.annotationKind === "bos" || zone.annotationKind === "choch") {
        const eventX = zone.eventTime ? chart.timeScale().timeToCoordinate(zone.eventTime as Time) : coordinates.end;
        labelSpan = settings.showConnectors ? clippedHorizontalSpan(coordinates.start, eventX ?? coordinates.end, width) : null;
      }
      if (zone.currentLevelSide && zone.compactLabel && labelSpan) {
        candleBoxes ??= visibleCandleBoxes(chart, priceSeries, candles, barWidth, width, plotBottom);
        drawCurrentLevelConfidenceLabel(
          context,
          zone.compactLabel,
          labelSpan,
          center,
          borderColor,
          chartBackground,
          settings,
          lineLabelBoxes,
          candleBoxes,
          width,
          plotBottom,
        );
      }
      if (zone.compactLabel && labelSpan && settings.showHistoricalLabels && historicalTagZones.has(zone)) {
        candleBoxes ??= visibleCandleBoxes(chart, priceSeries, candles, barWidth, width, plotBottom);
        drawPriceZoneLineLabel(
          context,
          zone.compactLabel,
          labelSpan,
          center,
          borderColor,
          chartBackground,
          priceZoneLineLabelPlacement(zone),
          settings,
          lineLabelBoxes,
          candleBoxes,
          width,
          plotBottom,
        );
      }
    });
  });
}

function drawCurrentLevelConfidenceLabel(
  context: CanvasRenderingContext2D,
  text: string,
  span: HorizontalSpan,
  centerY: number,
  color: string,
  chartBackground: string,
  settings: ResolvedPriceZoneLegendSettings,
  placed: CanvasBox[],
  candleBoxes: CanvasBox[],
  layerWidth: number,
  plotBottom: number,
) {
  const fontSize = 10;
  context.save();
  context.font = `700 ${fontSize}px ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  const labelWidth = Math.ceil(context.measureText(text).width) + 8;
  const labelHeight = fontSize + 5;
  const candidates = [span.right - labelWidth - 6, span.left + 6, span.left + span.width / 2 - labelWidth / 2];
  let selected: CanvasBox | null = null;
  for (const left of candidates) {
    const top = centerY - labelHeight / 2;
    const box = { bottom: top + labelHeight, left, right: left + labelWidth, top };
    if (box.left < 2 || box.right > layerWidth - 2 || box.top < 2 || box.bottom > plotBottom - 2) continue;
    if (placed.some((item) => boxesOverlap(box, item, 3))) continue;
    if (candleBoxes.some((candle) => boxesOverlap(box, candle, 2))) continue;
    selected = box;
    break;
  }
  if (selected) {
    context.fillStyle = rgbaFromHex(chartBackground, 0.88 * settings.opacity);
    context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
    context.fillStyle = rgbaFromHex(color, settings.opacity);
    context.textBaseline = "middle";
    context.fillText(text, selected.left + 4, selected.top + labelHeight / 2);
    placed.push(selected);
  }
  context.restore();
}

function clippedHorizontalSpan(start: number, end: number, viewportWidth: number, overscan = 24): HorizontalSpan | null {
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(viewportWidth > 0)) return null;
  const rawLeft = Math.min(start, end);
  const rawRight = Math.max(start, end);
  if (rawRight < -overscan || rawLeft > viewportWidth + overscan) return null;
  const left = Math.max(-overscan, Math.min(viewportWidth + overscan, rawLeft));
  const right = Math.max(-overscan, Math.min(viewportWidth + overscan, rawRight));
  const width = right - left;
  return width >= 1 ? { left, right, width } : null;
}

function isStructureBreakZone(zone: PriceZone) {
  return zone.annotationKind === "bos" || zone.annotationKind === "choch";
}

function isVisibleCoordinate(coordinate: number | null, viewportWidth: number, overscan = 24) {
  return coordinate !== null && Number.isFinite(coordinate) && coordinate >= -overscan && coordinate <= viewportWidth + overscan;
}

function priceZoneLineLabelPlacement(zone: PriceZone): "above" | "below" {
  if (zone.annotationKind === "swing-low" || zone.annotationKind === "liquidity-support") return "below";
  if ((zone.annotationKind === "bos" || zone.annotationKind === "choch") && zone.compactLabel?.endsWith("-")) return "below";
  return "above";
}

function canvasLineDash(style: LegendLineStyle, lineWidth: number) {
  if (style === "dashed") return [Math.max(4, lineWidth * 4), Math.max(3, lineWidth * 3)];
  if (style === "dotted") return [Math.max(1, lineWidth), Math.max(3, lineWidth * 3)];
  return [];
}

function visibleCandleBoxes(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  candles: Candle[],
  barWidth: number,
  layerWidth: number,
  plotBottom: number,
) {
  const halfWidth = Math.max(1.5, barWidth * 0.46);
  const boxes: CanvasBox[] = [];
  candles.forEach((candle) => {
    const x = chart.timeScale().timeToCoordinate(candle.time as Time);
    if (x === null || x + halfWidth < 0 || x - halfWidth > layerWidth) return;
    const highY = priceSeries.priceToCoordinate(candle.high);
    const lowY = priceSeries.priceToCoordinate(candle.low);
    if (highY === null || lowY === null) return;
    const top = Math.max(0, Math.min(highY, lowY));
    const bottom = Math.min(plotBottom, Math.max(highY, lowY));
    if (bottom < 0 || top > plotBottom) return;
    boxes.push({ bottom, left: x - halfWidth, right: x + halfWidth, top });
  });
  return boxes;
}

function boxesOverlap(first: CanvasBox, second: CanvasBox, gap = 0) {
  return first.left < second.right + gap
    && first.right + gap > second.left
    && first.top < second.bottom + gap
    && first.bottom + gap > second.top;
}

function drawPriceZoneLineLabel(
  context: CanvasRenderingContext2D,
  text: string,
  span: HorizontalSpan,
  lineY: number,
  color: string,
  chartBackground: string,
  placement: "above" | "below",
  settings: ResolvedPriceZoneLegendSettings,
  placed: CanvasBox[],
  candleBoxes: CanvasBox[],
  layerWidth: number,
  plotBottom: number,
) {
  if (lineY < 2 || lineY > plotBottom - 2 || span.width < 8) return;
  const fontSize = Math.max(9, settings.labelFontSize);
  context.save();
  context.font = `600 ${fontSize}px ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
  const textWidth = Math.ceil(context.measureText(text).width);
  const labelWidth = textWidth + 8;
  const labelHeight = fontSize + 5;
  if (labelWidth + 4 > span.width) {
    context.restore();
    return;
  }
  const candidateFractions = [0.5, 0.38, 0.62, 0.25, 0.75];
  let selected: CanvasBox | null = null;
  for (const fraction of candidateFractions) {
    const centerX = span.left + span.width * fraction;
    const left = centerX - labelWidth / 2;
    const top = placement === "above" ? lineY - labelHeight - 2 : lineY + 2;
    const box = { bottom: top + labelHeight, left, right: left + labelWidth, top };
    const insideSpan = box.left >= span.left + 2 && box.right <= span.right - 2;
    const insidePlot = box.left >= 2 && box.right <= layerWidth - 2 && box.top >= 2 && box.bottom <= plotBottom - 2;
    if (!insideSpan || !insidePlot) continue;
    if (placed.some((item) => boxesOverlap(box, item, 3))) continue;
    if (candleBoxes.some((candleBox) => boxesOverlap(box, candleBox, 2))) continue;
    selected = box;
    break;
  }
  if (selected) {
    context.fillStyle = chartBackground;
    context.globalAlpha = 0.96 * settings.opacity;
    context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
    context.globalAlpha = settings.opacity;
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.fillText(text, selected.left + 4, selected.top + labelHeight / 2);
    placed.push(selected);
  }
  context.restore();
}

function priceZoneCoordinates(chart: IChartApi, zone: PriceZone, candles: Candle[], barWidth: number, candleDuration: number) {
  let firstIndex = lowerBoundCandleTime(candles, zone.start - candleDuration);
  while (firstIndex < candles.length && !(candles[firstIndex].time < zone.end && candles[firstIndex].time + candleDuration > zone.start)) firstIndex += 1;
  const endIndex = lowerBoundCandleTime(candles, zone.end);
  const lastIndex = endIndex - 1;
  let coordinates: { end: number; start: number } | null = null;
  if (firstIndex < candles.length && lastIndex >= firstIndex) {
    const first = chart.timeScale().timeToCoordinate(candles[firstIndex].time as Time);
    const last = chart.timeScale().timeToCoordinate(candles[lastIndex].time as Time);
    if (first !== null && last !== null) coordinates = { end: last + barWidth / 2, start: first - barWidth / 2 };
  }
  if (!coordinates) {
    const start = chart.timeScale().timeToCoordinate(zone.start as Time);
    const end = chart.timeScale().timeToCoordinate(zone.end as Time);
    if (start === null || end === null) return null;
    coordinates = { end, start };
  }
  const exactStart = chart.timeScale().timeToCoordinate(zone.start as Time);
  return exactStart === null ? coordinates : { ...coordinates, start: exactStart };
}

function lowerBoundCandleTime(candles: Candle[], target: number) {
  let left = 0;
  let right = candles.length;
  while (left < right) {
    const middle = left + Math.floor((right - left) / 2);
    if (candles[middle].time < target) left = middle + 1;
    else right = middle;
  }
  return left;
}

function sessionRegionColor(region: Region, settings: ChartAppearanceSettings) {
  const label = region.label.toLowerCase();
  const styles = window.getComputedStyle(document.documentElement);
  const themedPremarket = styles.getPropertyValue("--chart-premarket").trim() || settings.premarketColor;
  const themedAfterHours = styles.getPropertyValue("--chart-after-hours").trim() || settings.afterHoursColor;
  const premarketColor = settings.premarketColor === defaultChartAppearanceSettings.premarketColor ? themedPremarket : settings.premarketColor;
  const afterHoursColor = settings.afterHoursColor === defaultChartAppearanceSettings.afterHoursColor ? themedAfterHours : settings.afterHoursColor;
  if (label.includes("pre")) return rgbaFromHex(premarketColor, settings.premarketOpacity);
  if (label.includes("after") || label.includes("post")) return rgbaFromHex(afterHoursColor, settings.afterHoursOpacity);
  return region.color;
}

function drawDaySeparators(chart: IChartApi, layer: HTMLDivElement, candles: Candle[], settings: ChartAppearanceSettings, barWidth: number) {
  if (!settings.daySeparatorsVisible || candles.length < 2) return;
  let previousDate = marketDate(candles[0].time);
  candles.slice(1).forEach((candle) => {
    const currentDate = marketDate(candle.time);
    if (currentDate === previousDate) return;
    previousDate = currentDate;
    const coordinate = chart.timeScale().timeToCoordinate(candle.time as Time);
    if (!isVisibleCoordinate(coordinate, layer.clientWidth)) return;
    const visibleCoordinate = Number(coordinate);
    const node = document.createElement("div");
    node.className = "day-separator";
    node.title = currentDate;
    node.style.left = `${visibleCoordinate - barWidth / 2}px`;
    node.style.borderLeft = `1px ${settings.daySeparatorStyle} ${rgbaFromHex(settings.daySeparatorColor, 0.78)}`;
    layer.appendChild(node);
  });
}

function drawTradeAnnotations(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement,
  annotations: TradeAnnotation[],
  candles: Candle[],
  barWidth: number
) {
  if (!priceSeries || !annotations.length || !candles.length) return;
  annotations.forEach((annotation) => {
    const entryX = xForAnnotationTime(chart, annotation.entryTime, candles);
    const exitX = xForAnnotationTime(chart, annotation.exitTime, candles);
    const entryY = priceSeries.priceToCoordinate(annotation.entryPrice);
    const exitY = priceSeries.priceToCoordinate(annotation.exitPrice);
    if (entryX === null || exitX === null || entryY === null || exitY === null) return;
    const span = clippedHorizontalSpan(entryX - barWidth / 2, exitX + barWidth / 2, layer.clientWidth);
    if (!span) return;
    const left = span.left;
    const width = Math.max(3, span.width);
    const color = validHexColor(annotation.color, annotation.pnl !== undefined && annotation.pnl < 0 ? "#dc2626" : "#16a34a");
    const selected = annotation.selected === true;
    const region = document.createElement("div");
    region.className = selected ? "trade-annotation-region selected" : "trade-annotation-region";
    region.title = annotation.pnl !== undefined ? `Trade P/L ${annotation.pnl.toFixed(2)}` : "Trade";
    region.style.left = `${left}px`;
    region.style.top = `${Math.min(entryY, exitY)}px`;
    region.style.width = `${width}px`;
    region.style.height = `${Math.max(7, Math.abs(exitY - entryY))}px`;
    region.style.background = rgbaFromHex(color, selected ? 0.12 : 0.06);
    region.style.borderColor = rgbaFromHex(color, selected ? 0.28 : 0.12);
    layer.appendChild(region);

    drawTradePriceLine(layer, left, width, entryY, color, annotation.entryLabel ?? "Entry", annotation.entryLabelParts, "entry", selected, annotation.entryLabelSide ?? "left");
    drawTradePriceLine(layer, left, width, exitY, color, annotation.exitLabel ?? "Exit", annotation.exitLabelParts, "exit", selected, annotation.exitLabelSide ?? "right");
    if (isVisibleCoordinate(entryX, layer.clientWidth)) drawTradeArrow(layer, entryX, entryY, color, "entry", selected);
    if (isVisibleCoordinate(exitX, layer.clientWidth)) drawTradeArrow(layer, exitX, exitY, color, "exit", selected);
    annotation.fills?.forEach((fill) => {
      const fillX = xForAnnotationTime(chart, fill.time, candles);
      const fillY = priceSeries.priceToCoordinate(fill.price);
      if (fillX === null || fillY === null) return;
      if (isVisibleCoordinate(fillX, layer.clientWidth)) drawTradeFillMarker(layer, fillX, fillY, color, fill, selected);
    });
    if (typeof annotation.stopPrice === "number" && Number.isFinite(annotation.stopPrice)) {
      const stopY = priceSeries.priceToCoordinate(annotation.stopPrice);
      if (stopY !== null) drawTradeGuideLine(layer, left, width, stopY, "#dc2626", "Stop", "stop");
    }
    if (typeof annotation.triggerPrice === "number" && Number.isFinite(annotation.triggerPrice)) {
      const triggerY = priceSeries.priceToCoordinate(annotation.triggerPrice);
      if (triggerY !== null) drawTradeGuideLine(layer, left, width, triggerY, "#2563eb", "Trigger", "trigger");
    }
  });
}

function drawTradePriceLine(layer: HTMLDivElement, left: number, width: number, y: number, color: string, label: string, parts: TradeLabelPart[] | undefined, kind: "entry" | "exit", selected: boolean, labelSide: "left" | "right") {
  const line = document.createElement("div");
  line.className = `trade-price-line ${kind} label-${labelSide}${selected ? " selected" : ""}`;
  line.style.left = `${left}px`;
  line.style.top = `${y}px`;
  line.style.width = `${width}px`;
  line.style.borderColor = color;
  const text = document.createElement("span");
  if (parts?.length) {
    parts.forEach((part) => {
      const piece = document.createElement("b");
      piece.className = `trade-label-part ${part.tone ?? "label"}`;
      piece.textContent = part.text;
      text.appendChild(piece);
    });
  } else {
    text.textContent = label;
  }
  text.style.color = color;
  text.style.borderColor = rgbaFromHex(color, 0.32);
  line.appendChild(text);
  layer.appendChild(line);
}

function drawTradeFillMarker(layer: HTMLDivElement, x: number, y: number, color: string, fill: TradeFillAnnotation, selected: boolean) {
  const marker = document.createElement("div");
  marker.className = `trade-fill-marker ${fill.side === "BUY" ? "entry" : "exit"}${selected ? " selected" : ""}`;
  marker.style.left = `${x}px`;
  marker.style.top = `${y}px`;
  marker.style.borderColor = color;
  const label = document.createElement("span");
  if (fill.labelParts?.length) {
    fill.labelParts.forEach((part) => {
      const piece = document.createElement("b");
      piece.className = `trade-label-part ${part.tone ?? "label"}`;
      piece.textContent = part.text;
      label.appendChild(piece);
    });
  } else {
    label.textContent = fill.label ?? `${fill.side} @${fill.price.toFixed(2)}`;
  }
  label.style.borderColor = rgbaFromHex(color, 0.28);
  marker.appendChild(label);
  layer.appendChild(marker);
}

function drawTradeGuideLine(layer: HTMLDivElement, left: number, width: number, y: number, color: string, label: string, kind: string) {
  const line = document.createElement("div");
  line.className = `trade-guide-line ${kind}`;
  line.style.left = `${left}px`;
  line.style.top = `${y}px`;
  line.style.width = `${width}px`;
  line.style.borderColor = rgbaFromHex(color, 0.78);
  const text = document.createElement("span");
  text.textContent = label;
  text.style.color = color;
  text.style.borderColor = rgbaFromHex(color, 0.26);
  line.appendChild(text);
  layer.appendChild(line);
}

function drawTradeArrow(layer: HTMLDivElement, x: number, y: number, color: string, kind: "entry" | "exit", selected: boolean) {
  const arrow = document.createElement("div");
  arrow.className = `trade-arrow ${kind}${selected ? " selected" : ""}`;
  arrow.style.left = `${x}px`;
  arrow.style.top = `${kind === "entry" ? y + 7 : y - 7}px`;
  arrow.style.borderColor = color;
  layer.appendChild(arrow);
}

function xForAnnotationTime(chart: IChartApi, time: number, candles: Candle[]) {
  const exact = chart.timeScale().timeToCoordinate(time as Time);
  if (exact !== null) return exact;
  const nearest = candles[nearestCandleIndex(candles, time)];
  return nearest ? chart.timeScale().timeToCoordinate(nearest.time as Time) : null;
}

function drawReferenceLine(chart: IChartApi, layer: HTMLDivElement | null, candles: Candle[], reference?: ChartReference | null) {
  if (!layer) return;
  layer.innerHTML = "";
  if (!reference || !candles.length) return;
  const referenceTime = resolveReferenceTime(reference, candles);
  if (referenceTime === null) return;
  const coordinate = chart.timeScale().timeToCoordinate(referenceTime as Time);
  if (!isVisibleCoordinate(coordinate, layer.clientWidth)) return;
  const visibleCoordinate = Number(coordinate);
  const node = document.createElement("div");
  node.className = "chart-reference-line";
  node.title = reference.label || "Selected row";
  node.style.left = `${visibleCoordinate}px`;
  if (visibleCoordinate < 90) {
    node.classList.add("near-left");
  } else if (visibleCoordinate > layer.clientWidth - 90) {
    node.classList.add("near-right");
  }
  if (reference.label) {
    const label = document.createElement("span");
    label.textContent = reference.label;
    node.appendChild(label);
  }
  layer.appendChild(node);
}

function resolveReferenceTime(reference: ChartReference, candles: Candle[]) {
  if (typeof reference.time === "number" && Number.isFinite(reference.time)) {
    return candles[nearestCandleIndex(candles, reference.time)]?.time ?? reference.time;
  }
  if (!reference.sessionDate) return null;
  const sameSession = candles
    .map((candle, index) => ({ candle, index }))
    .filter((item) => marketDateKey(item.candle.time) === reference.sessionDate);
  if (!sameSession.length) return null;
  if (typeof reference.minuteOfDay !== "number" || !Number.isFinite(reference.minuteOfDay)) {
    return sameSession[0].candle.time;
  }
  const nearest = sameSession.reduce((best, item) => {
    const distance = Math.abs(marketMinuteOfDay(item.candle.time) - Number(reference.minuteOfDay));
    return distance < best.distance ? { distance, time: item.candle.time } : best;
  }, { distance: Number.POSITIVE_INFINITY, time: sameSession[0].candle.time });
  return nearest.time;
}

function nearestCandleIndex(candles: Candle[], targetTime: number) {
  let bestIndex = 0;
  let bestDistance = Number.POSITIVE_INFINITY;
  candles.forEach((candle, index) => {
    const distance = Math.abs(candle.time - targetTime);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  return bestIndex;
}

function regionCoordinates(chart: IChartApi, region: Region, candles: Candle[], barWidth: number, candleDuration: number) {
  const overlappingCandles = candles.filter((candle) => candle.time < region.end && candle.time + candleDuration > region.start);
  if (overlappingCandles.length) {
    const first = chart.timeScale().timeToCoordinate(overlappingCandles[0]?.time as Time);
    const last = chart.timeScale().timeToCoordinate(overlappingCandles[overlappingCandles.length - 1]?.time as Time);
    if (first !== null && last !== null) return { end: last + barWidth / 2, start: first - barWidth / 2 };
  }

  const start = chart.timeScale().timeToCoordinate(region.start as Time);
  const end = chart.timeScale().timeToCoordinate(region.end as Time);
  if (start !== null && end !== null) return { end, start };

  return null;
}

function sessionRegionCoordinates(chart: IChartApi, region: Region, timeline: CandleSeriesDatum[]) {
  if (!timeline.length || region.end <= region.start) return null;
  const firstIndex = lowerBoundTimelineTime(timeline, region.start);
  const endIndex = lowerBoundTimelineTime(timeline, region.end);
  const lastIndex = endIndex - 1;
  if (firstIndex >= timeline.length || lastIndex < firstIndex) return null;

  const firstTime = Number(timeline[firstIndex]?.time);
  const lastTime = Number(timeline[lastIndex]?.time);
  if (!Number.isFinite(firstTime) || !Number.isFinite(lastTime) || firstTime >= region.end || lastTime < region.start) return null;

  const start = timelinePointEdgeCoordinate(chart, timeline, firstIndex, "leading");
  const end = timelinePointEdgeCoordinate(chart, timeline, lastIndex, "trailing");
  return start === null || end === null ? null : { end, start };
}

function lowerBoundTimelineTime(timeline: CandleSeriesDatum[], target: number) {
  let left = 0;
  let right = timeline.length;
  while (left < right) {
    const middle = left + Math.floor((right - left) / 2);
    if (Number(timeline[middle]?.time) < target) left = middle + 1;
    else right = middle;
  }
  return left;
}

function timelinePointEdgeCoordinate(chart: IChartApi, timeline: CandleSeriesDatum[], index: number, edge: "leading" | "trailing") {
  const center = chart.timeScale().timeToCoordinate(timeline[index]?.time as Time);
  if (center === null) return null;
  const neighborIndex = edge === "leading" ? index - 1 : index + 1;
  const neighborTime = timeline[neighborIndex]?.time;
  const neighbor = neighborTime === undefined ? null : chart.timeScale().timeToCoordinate(neighborTime as Time);
  if (neighbor !== null) return (center + neighbor) / 2;

  const fallbackIndex = edge === "leading" ? index + 1 : index - 1;
  const fallbackTime = timeline[fallbackIndex]?.time;
  const fallback = fallbackTime === undefined ? null : chart.timeScale().timeToCoordinate(fallbackTime as Time);
  if (fallback === null) return center;
  const spacing = Math.abs(center - fallback) / 2;
  return edge === "leading" ? center - spacing : center + spacing;
}

function estimateBarWidth(chart: IChartApi, candles: Candle[]) {
  const coordinates = candles
    .slice(0, 80)
    .map((candle) => chart.timeScale().timeToCoordinate(candle.time as Time))
    .filter((value) => value !== null)
    .map((value) => Number(value))
    .sort((left, right) => left - right);
  const deltas = coordinates
    .slice(1)
    .map((value, index) => value - coordinates[index])
    .filter((value) => value > 0);
  if (!deltas.length) return 4;
  deltas.sort((left, right) => left - right);
  return Math.max(2, Math.min(24, deltas[Math.floor(deltas.length / 2)] ?? 4));
}

function estimateCandleDuration(candles: Candle[]) {
  const deltas = candles
    .slice(1)
    .map((candle, index) => candle.time - candles[index].time)
    .filter((value) => value > 0)
    .sort((left, right) => left - right);
  return deltas[Math.floor(deltas.length / 2)] ?? 60;
}

function marketDate(time: number) {
  return marketDateFormatter.format(new Date(time * 1000));
}

function marketDateKey(time: number) {
  const parts = Object.fromEntries(marketDateKeyFormatter.formatToParts(new Date(time * 1000)).map((part) => [part.type, part.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}

function marketMinuteOfDay(time: number) {
  const parts = Object.fromEntries(marketTimePartFormatter.formatToParts(new Date(time * 1000)).map((part) => [part.type, part.value]));
  const hour = Number(parts.hour) % 24;
  const minute = Number(parts.minute);
  return hour * 60 + minute;
}

function timestampFromChartTime(timeValue: Time) {
  if (typeof timeValue === "number") return timeValue;
  if (typeof timeValue === "string") return Date.parse(`${timeValue}T00:00:00Z`) / 1000;
  return Date.UTC(timeValue.year, timeValue.month - 1, timeValue.day) / 1000;
}

function formatMarketAxisTime(timeValue: Time, timeframe = "1m") {
  const timestamp = new Date(timestampFromChartTime(timeValue) * 1000);
  if (timeframe === "1mo") return marketMonthlyAxisFormatter.format(timestamp);
  if (timeframe === "1d") return marketDailyAxisFormatter.format(timestamp);
  const seconds = chartTimeframeSeconds(timeframe);
  if (seconds !== null && seconds < 1) return marketSubsecondAxisFormatter.format(timestamp);
  if (seconds !== null && seconds < 60) return marketSecondAxisFormatter.format(timestamp);
  return marketAxisFormatter.format(timestamp);
}

function formatMarketDateTime(timeValue: Time, timeframe = "1m") {
  const timestamp = new Date(timestampFromChartTime(timeValue) * 1000);
  if (timeframe === "1mo" || timeframe === "1d") return marketMacroDateTimeFormatter.format(timestamp);
  const seconds = chartTimeframeSeconds(timeframe);
  if (seconds !== null && seconds < 1) return marketSubsecondDateTimeFormatter.format(timestamp);
  if (seconds !== null && seconds < 60) return marketSecondDateTimeFormatter.format(timestamp);
  return marketDateTimeFormatter.format(timestamp);
}

function formatTimeframeLabel(timeframe: string) {
  if (timeframe === "1d") return "1D";
  if (timeframe === "1mo") return "1M";
  return timeframe;
}

function formatPrice(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4 }).format(value);
}
