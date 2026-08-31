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
import { LoadingState } from "./LoadingState";
import { Modal } from "./Modal";
import { TickerChangeBadge, TickerIdentity, TickerLogo } from "./TickerIdentity";

type Candle = { time: number; open: number; high: number; low: number; close: number; color?: string; borderColor?: string; wickColor?: string };
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
  kind?: "add" | "profit_target" | "protective_stop" | "trailing_stop" | "position_exit";
  label?: string;
  labelParts?: TradeLabelPart[];
  price: number;
  quantity?: number;
  side: "BUY" | "SELL";
  time: number;
};
type TradeAnnotation = {
  color: string;
  entryColor?: string;
  entryLabel?: string;
  entryLabelParts?: TradeLabelPart[];
  entryLabelSide?: "left" | "right";
  entryPrice: number;
  entryTime: number;
  exitLabel?: string;
  exitLabelParts?: TradeLabelPart[];
  exitLabelSide?: "left" | "right";
  exitColor?: string;
  exitPrice: number;
  exitTime: number;
  fills?: TradeFillAnnotation[];
  id: string;
  pnl?: number;
  selected?: boolean;
  stopPrice?: number;
  triggerPrice?: number;
};
type ChartPreset = "micro" | "tactical" | "context" | "axis-history" | "swing-rails";
type PriceZone = {
  annotationKind?: "band" | "bos" | "choch" | "level-footprint" | "swing-footprint" | "structure-break" | "level" | "luld-line" | "liquidity-resistance" | "liquidity-support" | "signal-episode-rail" | "signal-episode-range" | "swing-high" | "swing-low" | "unified-structure-level";
  axisLabelDefault?: boolean;
  borderColor?: string;
  borderOpacity?: number;
  borderStyle?: string;
  borderWidth?: number;
  breakProbability?: number;
  color: string;
  compactLabel?: string;
  confidence?: number;
  currentLevelDistanceRank?: number;
  currentLevelSide?: "support" | "resistance";
  currentLevelStrongest?: boolean;
  defaultVisible?: boolean;
  displayItemId?: string;
  end: number;
  episodeId?: number;
  episodeSteps?: Array<{
    confidence: number;
    end: number;
    lower: number;
    start: number;
    upper: number;
  }>;
  extendToRightEdge?: boolean;
  eventTime?: number;
  fillColor?: string;
  fillOpacity?: number;
  historicalLabelsDefault?: boolean;
  historyBarsDefault?: number;
  historyTimeframeSeconds?: number;
  holdProbability?: number;
  label: string;
  latest?: boolean;
  legendLabel?: string;
  lower: number;
  maxPixelHeight?: number;
  minPixelHeight?: number;
  opacityDefault?: number;
  preset?: ChartPreset;
  presetDefault?: ChartPreset;
  probabilityLineRatio?: number;
  probabilityLineWidth?: number;
  pressureBias?: number;
  renderMode?: "line" | "zone";
  roleFlipCount?: number;
  reversalProbability?: number;
  settingsId?: string;
  start: number;
  strength?: number;
  tone?: "buy" | "sell" | "neutral";
  totalVolume?: number;
  buyVolume?: number;
  sellVolume?: number;
  neutralVolume?: number;
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
  presetOptions?: Array<{ description?: string; label: string; value: ChartPreset }>;
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
type ChartMarker = SeriesMarker<Time> & {
  displayItemId?: string;
  preset?: ChartPreset;
  settingsId?: string;
};
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
  downColor?: string;
  historyBars?: number;
  labelFontSize?: number;
  lineStyle?: LegendLineStyle;
  lineWidth?: number;
  maximumBreakProbability?: number;
  minimumConfidence?: number;
  minimumHoldProbability?: number;
  minimumPressureMagnitude?: number;
  minimumReactionProbability?: number;
  minimumReversalProbability?: number;
  minimumSalience?: number;
  opacity?: number;
  preset?: ChartPreset;
  showConnectors?: boolean;
  showAxisLabel?: boolean;
  showHistoricalLabels?: boolean;
  showLabels?: boolean;
  showUnifiedActive?: boolean;
  showUnifiedBroken?: boolean;
  showUnifiedResistance?: boolean;
  showUnifiedRoleFlipped?: boolean;
  showUnifiedSupport?: boolean;
  showValue?: boolean;
  upColor?: string;
  visible?: boolean;
};
type LegendSettingsMap = Record<string, LegendSeriesSettings>;

type PriceZonePrimitiveState = {
  candles: Candle[];
  legendSettings: LegendSettingsMap;
  zones: PriceZone[];
};

type TradeAnnotationPrimitiveState = {
  candles: Candle[];
  executions: TradeFillAnnotation[];
  trades: TradeAnnotation[];
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
  private readonly labelRendererImpl: IPrimitivePaneRenderer = {
    draw: (target) => {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        drawPriceZonePrimitiveLabels(
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
  private readonly labelPaneView: IPrimitivePaneView = {
    renderer: () => this.labelRendererImpl,
    zOrder: () => "top",
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
    return [this.paneView, this.labelPaneView];
  }

  setState(state: PriceZonePrimitiveState) {
    this.state = state;
    this.requestUpdate?.();
  }
}

class TradeAnnotationPrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private state: TradeAnnotationPrimitiveState = { candles: [], executions: [], trades: [] };
  private readonly rendererImpl: IPrimitivePaneRenderer = {
    draw: (target) => {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        drawTradeAnnotationPrimitiveGeometry(
          this.chart as IChartApi,
          this.series as ISeriesApi<"Candlestick">,
          context,
          mediaSize.width,
          mediaSize.height,
          this.state.trades,
          this.state.executions,
          this.state.candles,
        );
      });
    },
  };
  private readonly paneView: IPrimitivePaneView = {
    renderer: () => this.rendererImpl,
    zOrder: () => "top",
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

  setState(state: TradeAnnotationPrimitiveState) {
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
  gridVisible: boolean;
  hideEmptyIntervals: boolean;
  legendGutterVisible: boolean;
  rightLegendGutterVisible: boolean;
  premarketColor: string;
  premarketOpacity: number;
  upColor: string;
  wickDownColor: string;
  wickUpColor: string;
  wickVisible: boolean;
};

export type ChartAppearanceDefaults = Partial<Pick<ChartAppearanceSettings,
  "daySeparatorsVisible" | "legendGutterVisible" | "rightLegendGutterVisible"
>>;

export type ChartPayload = {
  candles: Candle[];
  volume: Array<{ time: number; value: number; color: string }>;
  overlay_series: ChartSeries[];
  oscillator_series: ChartSeries[];
  markers: ChartMarker[];
  regions: Region[];
  execution_annotations?: TradeFillAnnotation[];
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
  appearanceDefaults?: ChartAppearanceDefaults;
  baseHeight?: number;
  catalogColumns?: ChartCatalogItem[];
  displayItemOptions?: ChartDisplayItem[];
  emptyMessage?: string;
  errorMessage?: string;
  featureOptions: string[];
  fillHeight?: boolean;
  indicatorOptions: string[];
  initialFitMode?: "default" | "last_market_day" | "live_first_10" | "recent";
  labelOptions?: ChartLabelOption[];
  canLoadEarlier?: boolean;
  deferInitialFitUntilLoaded?: boolean;
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
  toolbarVariant?: "full" | "compact";
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
  gridVisible: true,
  hideEmptyIntervals: true,
  legendGutterVisible: true,
  rightLegendGutterVisible: true,
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
  appearanceDefaults,
  baseHeight = 620,
  catalogColumns = [],
  displayItemOptions = [],
  emptyMessage = "No chart data for the selected ticker/date range/timeframe.",
  errorMessage,
  featureOptions,
  fillHeight = false,
  indicatorOptions,
  initialFitMode = "default",
  labelOptions = [],
  canLoadEarlier = false,
  deferInitialFitUntilLoaded = false,
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
  toolbarVariant = "full",
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
  const tradeAnnotationPrimitiveRef = useRef<TradeAnnotationPrimitive | null>(null);
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
  const scaleStabilizationRetryCountRef = useRef(0);
  const scaleRecoveryCountRef = useRef(0);
  const regionDrawRef = useRef<((range: LogicalRange | null) => void) | null>(null);
  const canLoadEarlierRef = useRef(canLoadEarlier);
  const loadingEarlierRef = useRef(loadingEarlier);
  const onLoadEarlierRef = useRef(onLoadEarlier);
  const suppressEarlierLoadUntilRef = useRef(0);
  const fittedChartKeyRef = useRef("");
  const viewportIdentityRef = useRef("");
  const userViewportClaimedRef = useRef(false);
  const candleWindowRef = useRef<{ first: number; last: number } | null>(null);
  const candleBoundsRef = useRef<NumericBounds>(null);
  const normalizeTickerValue = (value: string) => (normalizeTicker ? value.toUpperCase() : value);
  const [draftTicker, setDraftTicker] = useState(normalizeTickerValue(ticker));
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const [supervisionMenuOpen, setSupervisionMenuOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const [chartSettingsAnchor, setChartSettingsAnchor] = useState<HTMLButtonElement | null>(null);
  const legendStorageKey = settingsStorageKey ? `${settingsStorageKey}.legend` : LEGEND_SETTINGS_STORAGE_KEY;
  const oscillatorThresholdStorageKey = settingsStorageKey ? `${settingsStorageKey}.oscillator-thresholds` : OSCILLATOR_THRESHOLD_STORAGE_KEY;
  const appearanceStorageKey = settingsStorageKey ? `${settingsStorageKey}.appearance` : CHART_APPEARANCE_STORAGE_KEY;
  const paneLayoutStorageKey = settingsStorageKey ? `${settingsStorageKey}.pane-layout-v2` : `${LEGEND_SETTINGS_STORAGE_KEY}.pane-layout-v2`;
  const instanceAppearanceDefaults = normalizeChartAppearanceSettings({ ...defaultChartAppearanceSettings, ...appearanceDefaults });
  const [chartSettings, setChartSettings] = useState<ChartAppearanceSettings>(() => loadChartAppearanceSettings(appearanceStorageKey, instanceAppearanceDefaults));
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
    : baseHeight + oscillatorPaneTotalHeight;
  // Each chart instance can choose its initial price-scale gutters while
  // preserving explicit user choices in that instance's persisted settings.
  const alignLeftPriceScale = chartSettings.legendGutterVisible;
  const reserveRightPriceScale = chartSettings.rightLegendGutterVisible;
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
    const next = { ...instanceAppearanceDefaults };
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
      executeViewportCommand(() => fitLatestSession(priceChartRef.current, fitCandles(payload), timeframe, chartSettingsRef.current.hideEmptyIntervals));
    },
    fitRecent() {
      executeViewportCommand(() => centerReferenceOrLatest(priceChartRef.current, fitCandles(payload), reference, timeframe, initialFitMode, chartSettingsRef.current.hideEmptyIntervals));
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
    userViewportClaimedRef.current = true;
    cancelPendingInitialFit();
  }

  function finishViewportInteraction() {
    persistNativePaneLayout();
    scheduleScaleStabilization();
    scheduleOverlayRedrawBurst();
  }

  function scheduleScaleStabilization() {
    if (scaleStabilizationFrameRef.current !== null) return;
    scaleStabilizationRetryCountRef.current = 0;
    scaleStabilizationFrameRef.current = window.requestAnimationFrame(() => {
      scaleStabilizationFrameRef.current = null;
      stabilizeNativePaneScales();
    });
  }

  function stabilizeNativePaneScales() {
    const chart = priceChartRef.current;
    const candle = candleRef.current;
    if (!chart || !candle) return;
    let result = stabilizeSeriesScale(candle, chart.panes()[0]?.getHeight() ?? 0, candleBoundsRef.current);
    oscillatorPaneRuntimesRef.current.forEach((runtime) => {
      const paneHeight = chart.panes()[runtime.paneIndex]?.getHeight() ?? 0;
      runtime.seriesKeys.forEach((key) => {
        const renderer = indicatorSeriesRef.current.get(key);
        if (renderer) result = mergeScaleStabilizationResults(result, stabilizeSeriesScale(renderer, paneHeight, indicatorBoundsRef.current.get(key) ?? null));
      });
    });
    if (result.retry && scaleStabilizationRetryCountRef.current < 2) {
      scaleStabilizationRetryCountRef.current += 1;
      scaleStabilizationFrameRef.current = window.requestAnimationFrame(() => {
        scaleStabilizationFrameRef.current = null;
        stabilizeNativePaneScales();
      });
      return;
    }
    scaleStabilizationRetryCountRef.current = 0;
    if (result.recovered && shellRef.current) {
      scaleRecoveryCountRef.current += 1;
      shellRef.current.dataset.chartScaleRecoveries = String(scaleRecoveryCountRef.current);
    }
  }

  function executeViewportCommand(command: () => void) {
    // Toolbar and imperative viewport commands are explicit ownership choices.
    // Later data enrichment or history paging must preserve their result too.
    userViewportClaimedRef.current = true;
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
    updateCandleMarkers();
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
    if (!priceRef.current || priceChartRef.current) return undefined;
    if (fillHeight) {
      priceRef.current.parentElement?.style.setProperty(
        "--chart-runtime-height",
        `${baseHeight + oscillatorPaneTotalHeight}px`,
      );
    }
    const palette = readChartPalette();
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current, timeframe, true, alignLeftPriceScale, reserveRightPriceScale));
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
    const tradeAnnotationPrimitive = new TradeAnnotationPrimitive();
    candleSeries.attachPrimitive(tradeAnnotationPrimitive);
    tradeAnnotationPrimitiveRef.current = tradeAnnotationPrimitive;
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
    observer.observe(priceRef.current);
    resizeObserverRef.current = observer;
    paneResizeObserverRef.current = new ResizeObserver(() => {
      layoutNativePaneOverlays();
      scheduleOverlayRedraw();
    });
    overlayInteractionCleanupRef.current = attachOverlayRedrawListeners(priceRef.current, scheduleOverlayRedraw, scheduleOverlayRedrawBurst);
    window.requestAnimationFrame(() => resizeCharts());
    return () => cleanupChartRuntime();
  }, []);

  useEffect(() => {
    payloadRef.current = payload;
    if (!payload || !priceChartRef.current || !candleRef.current || !volumeRef.current) return;
    const viewportIdentity = `${ticker}:${timeframe}:${referenceKey || "no-reference"}`;
    if (viewportIdentityRef.current !== viewportIdentity) {
      cancelPendingInitialFit();
      viewportIdentityRef.current = viewportIdentity;
      userViewportClaimedRef.current = false;
      fittedChartKeyRef.current = "";
      candleWindowRef.current = null;
    }
    const fitKey = buildChartFitKey(ticker, timeframe, referenceKey, payload.candles);
    const nextCandleWindow = candleWindow(payload.candles);
    const earlierBarsPrepended = Boolean(
      candleWindowRef.current
      && nextCandleWindow
      && nextCandleWindow.first < candleWindowRef.current.first
    );
    const shouldAutoFit = fitKey !== fittedChartKeyRef.current && !userViewportClaimedRef.current;
    const autoFitDeferred = shouldAutoFit && deferInitialFitUntilLoaded && loading;
    const preserveViewport = !shouldAutoFit || autoFitDeferred;
    const currentRange = preserveViewport ? priceChartRef.current.timeScale().getVisibleLogicalRange() : null;
    const currentTimeRange = preserveViewport && earlierBarsPrepended ? priceChartRef.current.timeScale().getVisibleRange() : null;
    const timeline = chartTimelineData(payload.candles, timeframe, chartSettingsRef.current.hideEmptyIntervals);
    candleBoundsRef.current = candleValueBounds(payload.candles);
    syncRendererData(candleRef.current, timeline as unknown as RendererDatum[], `candles:${timeframe}`);
    syncRendererData(volumeRef.current, volumeDataForSettings(payload, chartSettingsRef.current) as unknown as RendererDatum[], volumeStyleKey(chartSettingsRef.current));
    candleWindowRef.current = nextCandleWindow;
    updateCandleMarkers();
    if (shouldAutoFit && !autoFitDeferred) {
      fittedChartKeyRef.current = fitKey;
      if (initialFitTimerRef.current !== null) {
        window.clearTimeout(initialFitTimerRef.current);
      }
      initialFitTimerRef.current = window.setTimeout(() => {
        const currentPayload = payloadRef.current;
        if (!currentPayload || !priceChartRef.current) return;
        suppressEarlierLoad();
        if (reference) {
          fitAroundReference(priceChartRef.current, currentPayload.candles, reference, timeframe, chartSettingsRef.current.hideEmptyIntervals);
        } else {
          fitInitialRange(priceChartRef.current, currentPayload.candles, timeframe, initialFitMode, chartSettingsRef.current.hideEmptyIntervals);
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
  }, [deferInitialFitUntilLoaded, effectiveChartSettings.hideEmptyIntervals, initialFitMode, loading, payload, reference, referenceKey, ticker, timeframe]);

  useEffect(() => {
    if (!priceChartRef.current || !payload?.candles.length || !reference) return;
    suppressEarlierLoad();
    fitAroundReference(priceChartRef.current, payload.candles, reference, timeframe, chartSettingsRef.current.hideEmptyIntervals);
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
      priceChart.applyOptions(chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current, timeframe, true, alignLeftPriceScale, reserveRightPriceScale));
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
    markerPlugin.setMarkers(markersForSelection(currentPayload.markers, visibleSelectionRef.current, legendSettingsRef.current));
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
      updateOscillatorPaneTimeline(runtime, chartTimelineData(payloadRef.current?.candles ?? [], timeframe, chartSettingsRef.current.hideEmptyIntervals));
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
    const selectedZones = (currentPayload.price_zones ?? []).filter((zone) => {
      if (zone.displayItemId && !visibleSelectionRef.current.has(zone.displayItemId.toLowerCase())) return false;
      const settingsId = zone.settingsId || zone.displayItemId || `zone:${zone.label}`;
      const settings = resolvePriceZoneLegendSettings(
        legendSettingsRef.current,
        priceZoneLegendKey(settingsId),
        zone,
      );
      return priceZoneMeetsUnifiedFilters(zone, settings);
    });
    const timeline = chartTimelineData(currentPayload.candles, timeframe, chartSettingsRef.current.hideEmptyIntervals);
    priceZonePrimitiveRef.current?.setState({
      candles: currentPayload.candles,
      legendSettings: legendSettingsRef.current,
      zones: selectedZones,
    });
    tradeAnnotationPrimitiveRef.current?.setState({
      candles: currentPayload.candles,
      executions: currentPayload.execution_annotations ?? [],
      trades: currentPayload.trade_annotations ?? [],
    });
    syncPriceZoneAxisLines(candleRef.current, selectedZones, legendSettingsRef.current, priceZoneAxisLinesRef.current);
    drawRegions(chart, candleRef.current, priceLayerRef.current, currentPayload.regions, currentPayload.candles, timeline, chartSettingsRef.current, liveEntryLineRef.current);
    oscillatorPaneRuntimesRef.current.forEach((_runtime, key) => {
      drawSessionRegions(
        chart,
        oscillatorLayerRefs.current.get(key) ?? null,
        currentPayload.regions.filter((region) => !region.label.startsWith("QMD ")),
        timeline,
        currentPayload.candles,
        chartSettingsRef.current,
        false,
      );
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
    if (tradeAnnotationPrimitiveRef.current && candleRef.current) {
      candleRef.current.detachPrimitive(tradeAnnotationPrimitiveRef.current);
    }
    tradeAnnotationPrimitiveRef.current = null;
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
    scaleStabilizationRetryCountRef.current = 0;
    viewportIdentityRef.current = "";
    userViewportClaimedRef.current = false;
    if (shellRef.current) delete shellRef.current.dataset.chartScaleRecoveries;
  }

  function resizeCharts() {
    const price = priceRef.current;
    if (price && priceChartRef.current) {
      const surface = price.parentElement;
      if (fillHeight && surface && shellRef.current) {
        const toolbar = shellRef.current.querySelector<HTMLElement>(":scope > .chart-component-toolbar");
        const availableHeight = shellRef.current.clientHeight - (toolbar?.clientHeight ?? 0);
        if (availableHeight >= 48) {
          surface.style.setProperty("--chart-runtime-height", `${availableHeight}px`);
        }
      }
      priceChartRef.current.applyOptions({ width: price.clientWidth, height: Math.max(2, price.clientHeight) });
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
    // DOM rectangles are post-zoom, but absolute child coordinates are
    // pre-zoom. Normalize them so overlays follow their native chart panes.
    const scaleX = root.offsetWidth > 0 ? rootRect.width / root.offsetWidth : 1;
    const scaleY = root.offsetHeight > 0 ? rootRect.height / root.offsetHeight : 1;
    const position = (overlay: HTMLElement | null, paneIndex: number) => {
      const paneElement = chart.panes()[paneIndex]?.getHTMLElement();
      if (!overlay || !paneElement) return;
      paneResizeObserverRef.current?.observe(paneElement);
      const paneRect = paneElement.getBoundingClientRect();
      overlay.style.left = `${(paneRect.left - rootRect.left) / scaleX}px`;
      overlay.style.top = `${(paneRect.top - rootRect.top) / scaleY}px`;
      overlay.style.width = `${paneRect.width / scaleX}px`;
      overlay.style.height = `${paneRect.height / scaleY}px`;
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
      className={`chart-shell${fullscreen ? " fullscreen" : ""}${fillHeight ? " fill-height" : ""}`}
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
      <div className={toolbarVariant === "compact" ? "chart-component-toolbar compact" : "chart-component-toolbar"}>
        {toolbarVariant === "compact" ? null : tickerEditable ? <form className="chart-ticker-form" onSubmit={commitTicker}>
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
        {toolbarVariant === "full" && tickerChangeAsOf ? <TickerChangeBadge asOf={tickerChangeAsOf} ticker={ticker} /> : null}
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
        {toolbarVariant === "full" ? <span className="toolbar-divider" /> : null}
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
          onClick={(event) => {
            setColumnMenuOpen(false);
            setSupervisionMenuOpen(false);
            setPeriodMenuOpen(false);
            setChartSettingsAnchor(event.currentTarget);
            setChartSettingsOpen((value) => !value);
          }}
        >
          <Settings size={15} />
        </button>
        <span className="toolbar-divider" />
        <button aria-label={latestRangeActionLabel(timeframe)} className="toolbar-button" type="button" title={latestRangeActionLabel(timeframe)} onClick={() => executeViewportCommand(() => fitLatestSession(priceChartRef.current, fitCandles(payload), timeframe, chartSettingsRef.current.hideEmptyIntervals))}><CalendarDays size={15} /></button>
        <button aria-label={reference ? "Center trade" : "Center latest"} className="toolbar-button" type="button" title={reference ? "Center trade" : "Center latest"} onClick={() => executeViewportCommand(() => centerReferenceOrLatest(priceChartRef.current, fitCandles(payload), reference, timeframe, undefined, chartSettingsRef.current.hideEmptyIntervals))}><AlignCenterHorizontal size={15} /></button>
        <button aria-label="Reset view" className="toolbar-button" type="button" title="Reset view" onClick={() => executeViewportCommand(() => resetChartViewport(priceChartRef.current, fitCandles(payload), timeframe, priceRef.current?.clientWidth ?? 0, chartSettingsRef.current.candleSize, chartSettingsRef.current.hideEmptyIntervals))}><RefreshCcw size={15} /></button>
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
          anchor={chartSettingsAnchor}
          onChange={updateChartSettings}
          onClose={() => setChartSettingsOpen(false)}
          onReset={resetChartSettings}
          settings={chartSettings}
        />
      ) : null}
      <div className="chart-canvas-stack">
        {!hasChartData ? (
          <div className={`chart-state-overlay${errorMessage ? " error" : ""}`} role={errorMessage ? "alert" : loading ? undefined : "status"}>
            {loading ? <LoadingState label="Loading chart data" /> : errorMessage ? `Chart data request failed: ${errorMessage}` : emptyMessage}
          </div>
        ) : null}
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
                  hideHeaderLabel
                  indicatorCount={group.series.length}
                  items={buildSeriesLegendItems(group.series, "oscillator", legendSettings, displayItemOptions, catalogColumns, chartSettings)}
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
  maximumBreakProbability?: number;
  minimumConfidence?: number;
  minimumHoldProbability?: number;
  minimumPressureMagnitude?: number;
  minimumReactionProbability?: number;
  minimumReversalProbability?: number;
  minimumSalience?: number;
  opacity: number;
  preset?: ChartPreset;
  presetOptions?: Array<{ description?: string; label: string; value: ChartPreset }>;
  seriesStyle: "candlestick" | "histogram" | "line";
  semanticColor: boolean;
  semanticColors: { down: string; neutral: string; up: string };
  showConnectors?: boolean;
  showAxisLabel?: boolean;
  showHistoricalLabels?: boolean;
  showLabels?: boolean;
  showUnifiedActive?: boolean;
  showUnifiedBroken?: boolean;
  showUnifiedResistance?: boolean;
  showUnifiedRoleFlipped?: boolean;
  showUnifiedSupport?: boolean;
  showValue: boolean;
  supportsConnectors?: boolean;
  supportsNeutralColorEditing?: boolean;
  supportsSemanticColorEditing?: boolean;
  supportsCurrentLevelCount?: boolean;
  supportsAxisLabel?: boolean;
  supportsHistoricalLabels?: boolean;
  supportsHistoryWindow?: boolean;
  supportsStroke?: boolean;
  supportsUnifiedFilters?: boolean;
  supportsPreset?: boolean;
  value: string;
  visible: boolean;
};

function ChartLegend({
  hideHeaderLabel = false,
  indicatorCount,
  items,
  onReset,
  onUpdate,
  onThresholdReset,
  onThresholdUpdate,
  threshold,
  title,
}: {
  hideHeaderLabel?: boolean;
  indicatorCount: number;
  items: LegendItem[];
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
    <div className={collapsed ? "chart-legend collapsed" : "chart-legend"}>
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
        {!hideHeaderLabel ? <b>{title || formatIndicatorCount(indicatorCount)}</b> : null}
      </button>
      {!collapsed ? (
        <>
          <div className="chart-legend-rows">
            {items.map((item) => (
              <div className={item.visible ? "chart-legend-row" : "chart-legend-row muted"} key={item.key}>
                <span className={item.seriesStyle === "histogram" ? "legend-swatch histogram" : `legend-swatch ${item.lineStyle}`} style={{ color: item.color, opacity: item.opacity }}>
                  <i style={{ background: item.color }} />
                </span>
                <span className="legend-label" title={item.label}>{item.label}</span>
                {item.showValue && item.visible ? <span className="legend-value" style={{ color: item.color, opacity: item.opacity }} title={item.value}>{item.value}</span> : null}
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
                      className="legend-configure-button"
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
  const allHistoryBars = (item.historyBars ?? 20) === 0;
  const limitedHistoryBars = allHistoryBars ? 200 : (item.historyBars ?? 20);

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
          item.supportsSemanticColorEditing ? (
            <span className="legend-semantic-color-inputs">
              <span><input aria-label={item.label.includes("footprint") ? "Buyer color" : "Bullish color"} type="color" value={item.semanticColors.up} onChange={(event) => onUpdate({ upColor: event.target.value })} />{item.label.includes("footprint") ? "Buyer" : "Bullish"}</span>
              <span><input aria-label={item.label.includes("footprint") ? "Seller color" : "Bearish color"} type="color" value={item.semanticColors.down} onChange={(event) => onUpdate({ downColor: event.target.value })} />{item.label.includes("footprint") ? "Seller" : "Bearish"}</span>
              {item.supportsNeutralColorEditing ? (
                <span><input aria-label="Neutral color" type="color" value={item.color} onChange={(event) => onUpdate({ color: event.target.value })} />Neutral</span>
              ) : null}
            </span>
          ) : (
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
          )
        ) : <input type="color" value={item.color} onChange={(event) => onUpdate({ color: event.target.value })} />}
      </label>
      {item.supportsPreset && item.presetOptions?.length ? (
        <label>
          Mode
          <select
            value={item.preset ?? "micro"}
            onChange={(event) => onUpdate({ preset: event.target.value as ChartPreset })}
          >
            {item.presetOptions.map((option) => (
              <option key={option.value} value={option.value}>{option.label}</option>
            ))}
          </select>
          <small>{item.presetOptions.find((option) => option.value === (item.preset ?? "micro"))?.description}</small>
        </label>
      ) : null}
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
          {item.supportsConnectors ? "Break label size" : "Line label size"}
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
        <fieldset className="legend-history-control">
          <legend>History</legend>
          <label className="legend-toggle-row">
            <input
              aria-label={`${item.label} show on all loaded bars`}
              checked={allHistoryBars}
              type="checkbox"
              onChange={(event) => onUpdate({ historyBars: event.target.checked ? 0 : 200 })}
            />
            <span>All loaded bars</span>
          </label>
          <span className="legend-range-control">
            <input
              aria-label={`${item.label} history bars`}
              disabled={allHistoryBars}
              min={20}
              max={1000}
              step={10}
              type="range"
              value={limitedHistoryBars}
              onChange={(event) => onUpdate({ historyBars: Number(event.target.value) })}
            />
            <output>{allHistoryBars ? "All" : `${limitedHistoryBars} bars`}</output>
          </span>
        </fieldset>
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
      {item.itemKind === "zone" && item.supportsUnifiedFilters ? (
        <fieldset className="legend-unified-filters">
          <legend>Minimum level scores</legend>
          <ScoreThresholdControl label="Importance" value={item.minimumSalience ?? 0} onChange={(minimumSalience) => onUpdate({ minimumSalience })} />
          <ScoreThresholdControl label="Reaction" value={item.minimumReactionProbability ?? 0} onChange={(minimumReactionProbability) => onUpdate({ minimumReactionProbability })} />
          <ScoreThresholdControl label="Hold" value={item.minimumHoldProbability ?? 0} onChange={(minimumHoldProbability) => onUpdate({ minimumHoldProbability })} />
          <ScoreThresholdControl label="Reversal" value={item.minimumReversalProbability ?? 0} onChange={(minimumReversalProbability) => onUpdate({ minimumReversalProbability })} />
          <ScoreThresholdControl label="Pressure magnitude" value={item.minimumPressureMagnitude ?? 0} onChange={(minimumPressureMagnitude) => onUpdate({ minimumPressureMagnitude })} />
          <ScoreThresholdControl label="Maximum break" value={item.maximumBreakProbability ?? 1} onChange={(maximumBreakProbability) => onUpdate({ maximumBreakProbability })} />
          <ScoreThresholdControl label="Confidence" value={item.minimumConfidence ?? 0} onChange={(minimumConfidence) => onUpdate({ minimumConfidence })} />
          <small>Levels must meet every enabled evidence threshold. Changes apply immediately to loaded chart data.</small>
          <span className="legend-filter-subtitle">Visible roles and states</span>
          <span className="legend-filter-grid">
            <UnifiedVisibilityToggle checked={item.showUnifiedSupport !== false} label="Support" onChange={(showUnifiedSupport) => onUpdate({ showUnifiedSupport })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedResistance !== false} label="Resistance" onChange={(showUnifiedResistance) => onUpdate({ showUnifiedResistance })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedActive !== false} label="Active" onChange={(showUnifiedActive) => onUpdate({ showUnifiedActive })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedBroken !== false} label="Broken" onChange={(showUnifiedBroken) => onUpdate({ showUnifiedBroken })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedRoleFlipped !== false} label="Role-flipped" onChange={(showUnifiedRoleFlipped) => onUpdate({ showUnifiedRoleFlipped })} />
          </span>
        </fieldset>
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
            <label className="legend-checkbox">
              <input checked={item.showHistoricalLabels !== false} type="checkbox" onChange={(event) => onUpdate({ showHistoricalLabels: event.target.checked })} />
              Labels on historical lines
            </label>
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

function ScoreThresholdControl({ label, onChange, value }: { label: string; onChange: (value: number) => void; value: number }) {
  const percent = Math.round(clampNumber(value, 0, 1, 0) * 100);
  return (
    <label>
      {label}
      <span className="legend-range-control">
        <input
          aria-label={`Minimum ${label.toLowerCase()} score`}
          min={0}
          max={100}
          step={1}
          type="range"
          value={percent}
          onChange={(event) => onChange(Number(event.target.value) / 100)}
        />
        <output>{percent}%</output>
      </span>
    </label>
  );
}

function UnifiedVisibilityToggle({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return (
    <label className="legend-checkbox">
      <input checked={checked} type="checkbox" onChange={(event) => onChange(event.target.checked)} />
      {label}
    </label>
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
  anchor,
  onChange,
  onClose,
  onReset,
  settings
}: {
  anchor: HTMLElement | null;
  onChange: <K extends keyof ChartAppearanceSettings>(key: K, value: ChartAppearanceSettings[K]) => void;
  onClose: () => void;
  onReset: () => void;
  settings: ChartAppearanceSettings;
}) {
  const panelRef = useRef<HTMLDivElement | null>(null);
  const [position, setPosition] = useState({ left: 8, maxHeight: 320, top: 8, visibility: "hidden" as "hidden" | "visible" });

  useLayoutEffect(() => {
    const placePanel = () => {
      const panel = panelRef.current;
      if (!anchor || !panel || !anchor.isConnected) return;
      const anchorRect = anchor.getBoundingClientRect();
      const margin = 8;
      const availableBelow = window.innerHeight - anchorRect.bottom - margin - 6;
      const availableAbove = anchorRect.top - margin - 6;
      const useBelow = availableBelow >= Math.min(420, panel.scrollHeight) || availableBelow >= availableAbove;
      const maxHeight = Math.max(220, useBelow ? availableBelow : availableAbove);
      const measuredWidth = panel.getBoundingClientRect().width;
      const left = Math.max(margin, Math.min(anchorRect.right - measuredWidth, window.innerWidth - measuredWidth - margin));
      const top = useBelow ? anchorRect.bottom + 6 : Math.max(margin, anchorRect.top - Math.min(panel.scrollHeight, maxHeight) - 6);
      setPosition({ left, maxHeight, top, visibility: "visible" });
    };
    placePanel();
    window.addEventListener("resize", placePanel);
    window.addEventListener("scroll", placePanel, true);
    return () => {
      window.removeEventListener("resize", placePanel);
      window.removeEventListener("scroll", placePanel, true);
    };
  }, [anchor]);

  useEffect(() => {
    const closeOnPointer = (event: PointerEvent) => {
      const target = event.target as Node | null;
      if (target && (panelRef.current?.contains(target) || anchor?.contains(target))) return;
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
    <div className="chart-settings-slot" ref={panelRef} role="dialog" aria-label="Chart settings" style={position}>
      <div className="chart-settings-header">
        <div>
          <b>Chart Settings</b>
          <span>Appearance settings for candles, sessions, grid, and layout.</span>
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
        <label className="chart-setting-toggle">
          <input checked={settings.hideEmptyIntervals} type="checkbox" onChange={(event) => onChange("hideEmptyIntervals", event.target.checked)} />
          Hide empty intervals
        </label>
        <p className="chart-settings-help">
          Compress periods with no bars so illiquid symbols remain readable. Turn this off to preserve uniform clock-time spacing.
        </p>
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

      <ChartSettingsSection title="Layout">
        <p className="chart-settings-help">
          Grid lines help align time and price across the plot. Reserved gutters keep plot widths aligned across panes.
        </p>
        <label className="chart-setting-toggle">
          <input checked={settings.gridVisible} type="checkbox" onChange={(event) => onChange("gridVisible", event.target.checked)} />
          Show chart grid
        </label>
        <label className="chart-setting-toggle">
          <input checked={settings.legendGutterVisible} type="checkbox" onChange={(event) => onChange("legendGutterVisible", event.target.checked)} />
          Reserve left legend gutter
        </label>
        <label className="chart-setting-toggle">
          <input checked={settings.rightLegendGutterVisible} type="checkbox" onChange={(event) => onChange("rightLegendGutterVisible", event.target.checked)} />
          Reserve right legend gutter
        </label>
      </ChartSettingsSection>

      <div className="chart-setting-actions">
        <button className="text-button" onClick={onReset} type="button">Reset</button>
      </div>
    </div>,
    document.body
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
    const selectedZones = itemZones.filter((zone) => (
      (!zone.preset || zone.preset === settings.preset)
      && priceZoneMeetsUnifiedFilters(zone, settings)
    ));
    const presetZoneCount = itemZones.filter((zone) => !zone.preset || zone.preset === settings.preset).length;
    const episodeIds = new Set(selectedZones.filter((zone) => zone.episodeId !== undefined).map((zone) => `${zone.preset}:${zone.episodeId}`));
    const supportsUnifiedFilters = itemZones.some((zone) => zone.annotationKind === "unified-structure-level");
    return {
      color: settings.color,
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
      maximumBreakProbability: settings.maximumBreakProbability,
      minimumConfidence: settings.minimumConfidence,
      minimumHoldProbability: settings.minimumHoldProbability,
      minimumPressureMagnitude: settings.minimumPressureMagnitude,
      minimumReactionProbability: settings.minimumReactionProbability,
      minimumReversalProbability: settings.minimumReversalProbability,
      minimumSalience: settings.minimumSalience,
      opacity: settings.opacity,
      preset: settings.preset,
      presetOptions: displayItem?.presetOptions,
      seriesStyle: "line" as const,
      semanticColor: itemZones.some((zone) => zone.tone === "buy" || zone.tone === "sell"),
      semanticColors: { down: settings.downColor, neutral: settings.color, up: settings.upColor },
      showConnectors: settings.showConnectors,
      showAxisLabel: settings.showAxisLabel,
      showHistoricalLabels: settings.showHistoricalLabels,
      showUnifiedActive: settings.showUnifiedActive,
      showUnifiedBroken: settings.showUnifiedBroken,
      showUnifiedResistance: settings.showUnifiedResistance,
      showUnifiedRoleFlipped: settings.showUnifiedRoleFlipped,
      showUnifiedSupport: settings.showUnifiedSupport,
      showValue: true,
      supportsConnectors: itemZones.some(isStructureBreakZone),
      supportsNeutralColorEditing: itemZones.some((zone) => !zone.tone),
      supportsSemanticColorEditing: itemZones.some((zone) => zone.tone === "buy" || zone.tone === "sell"),
      supportsCurrentLevelCount: itemZones.some((zone) => Boolean(zone.currentLevelSide)),
      supportsAxisLabel: itemZones.some((zone) => typeof zone.axisLabelDefault === "boolean"),
      supportsHistoricalLabels: itemZones.some((zone) => (zone.renderMode === "line" && Boolean(zone.compactLabel)) || isStructureBreakZone(zone)),
      supportsHistoryWindow: supportsUnifiedFilters || itemZones.some((zone) => !zone.latest),
      supportsStroke: !itemZones.some((zone) =>
        Boolean(zone.currentLevelSide)
        || zone.annotationKind === "level-footprint"
        || zone.annotationKind === "swing-footprint"),
      supportsPreset: Boolean(displayItem?.presetOptions?.length),
      supportsUnifiedFilters,
      value: itemZones.some((zone) => zone.annotationKind === "signal-episode-range")
        ? `${episodeIds.size} episode${episodeIds.size === 1 ? "" : "s"}`
        : selectedZones.length === presetZoneCount
          ? `${selectedZones.length.toLocaleString("en-US")} level${selectedZones.length === 1 ? "" : "s"}`
          : `${selectedZones.length.toLocaleString("en-US")} / ${presetZoneCount.toLocaleString("en-US")}`,
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

function loadChartAppearanceSettings(
  storageKey = CHART_APPEARANCE_STORAGE_KEY,
  defaults = defaultChartAppearanceSettings
): ChartAppearanceSettings {
  if (typeof window === "undefined") return { ...defaults };
  try {
    const raw = window.localStorage.getItem(storageKey);
    if (!raw) return { ...defaults };
    return normalizeChartAppearanceSettings(JSON.parse(raw) as Partial<ChartAppearanceSettings>, defaults);
  } catch {
    return { ...defaults };
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

function normalizeChartAppearanceSettings(
  settings: Partial<ChartAppearanceSettings>,
  defaults = defaultChartAppearanceSettings
): ChartAppearanceSettings {
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
      typeof settings.daySeparatorsVisible === "boolean" ? settings.daySeparatorsVisible : defaults.daySeparatorsVisible,
    downColor: validHexColor(settings.downColor, defaultChartAppearanceSettings.downColor),
    gridVisible: typeof settings.gridVisible === "boolean" ? settings.gridVisible : defaultChartAppearanceSettings.gridVisible,
    hideEmptyIntervals: typeof settings.hideEmptyIntervals === "boolean" ? settings.hideEmptyIntervals : defaultChartAppearanceSettings.hideEmptyIntervals,
    legendGutterVisible: typeof settings.legendGutterVisible === "boolean" ? settings.legendGutterVisible : defaults.legendGutterVisible,
    rightLegendGutterVisible: typeof settings.rightLegendGutterVisible === "boolean" ? settings.rightLegendGutterVisible : defaults.rightLegendGutterVisible,
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

function chartTimelineData(candles: Candle[], timeframe: string, hideEmptyIntervals = true): CandleSeriesDatum[] {
  if (hideEmptyIntervals) return [...candles].sort((left, right) => left.time - right.time);
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

type ScaleStabilizationResult = { recovered: boolean; retry: boolean };

function stabilizeSeriesScale(renderer: AnySeriesApi, paneHeight: number, bounds: NumericBounds): ScaleStabilizationResult {
  if (!bounds || paneHeight <= 1) return { recovered: false, retry: false };
  let top: number | null = null;
  let bottom: number | null = null;
  try {
    top = renderer.coordinateToPrice(0);
    bottom = renderer.coordinateToPrice(paneHeight);
  } catch {
    // Lightweight Charts can reject coordinate reads for one paint while
    // series data or pane geometry is changing. Retry after rendering settles;
    // a transient read failure must not erase a user's manual price scale.
    return { recovered: false, retry: true };
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
    return { recovered: true, retry: false };
  }
  return { recovered: false, retry: false };
}

function mergeScaleStabilizationResults(left: ScaleStabilizationResult, right: ScaleStabilizationResult): ScaleStabilizationResult {
  return { recovered: left.recovered || right.recovered, retry: left.retry || right.retry };
}

function buildTimelineDataSignature(timeline: CandleSeriesDatum[]) {
  if (!timeline.length) return "empty";
  const first = timeline[0];
  const last = timeline[timeline.length - 1];
  return `${timeline.length}:${first.time}:${last.time}`;
}

function chartTimeframeSeconds(timeframe: string) {
  const normalized = timeframe.trim().toLowerCase();
  if (normalized === "1w") return 7 * 24 * 60 * 60;
  if (normalized === "1mo") return 30 * 24 * 60 * 60;
  if (normalized === "1y") return 365 * 24 * 60 * 60;
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

function markersForSelection(
  markers: ChartMarker[],
  selected: Set<string>,
  settingsMap: LegendSettingsMap,
): SeriesMarker<Time>[] {
  return markers
    .filter((marker) => {
      if (marker.displayItemId && !selected.has(marker.displayItemId.toLowerCase())) return false;
      if (!marker.preset) return true;
      const settings = settingsMap[priceZoneLegendKey(marker.settingsId || marker.displayItemId || "")];
      const preset = settings?.preset === "tactical" || settings?.preset === "context" ? settings.preset : "micro";
      return marker.preset === preset;
    })
    .map((marker, index) => ({
      color: resolveChartColor(typeof marker.color === "string" ? marker.color : "#1E3A5F"),
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

function priceZonePresentationColors(
  zone: PriceZone,
  chartBackground: string,
  settings?: Pick<ResolvedPriceZoneLegendSettings, "color" | "downColor" | "upColor">,
) {
  const confidence = typeof zone.confidence === "number" && Number.isFinite(zone.confidence)
    ? clampNumber(zone.confidence, 0, 1, 0)
    : null;
  const configuredToneColor = zone.tone === "buy"
    ? settings?.upColor
    : zone.tone === "sell"
      ? settings?.downColor
      : settings?.color;
  const semanticFillColor = validHexColor(resolveChartColor(configuredToneColor || zone.fillColor || zone.color), "#1E3A5F");
  const semanticBorderColor = validHexColor(resolveChartColor(configuredToneColor || zone.borderColor || semanticFillColor), semanticFillColor);
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
    downColor: resolveChartColor("var(--danger)"),
    currentLevelCount: 3,
    historyBars: 20,
    labelFontSize: 11,
    lineStyle: series.lineStyle ?? "solid",
    lineWidth: Math.max(1, Math.min(4, Math.round(series.lineWidth || 1))),
    maximumBreakProbability: 1,
    minimumConfidence: 0,
    minimumHoldProbability: 0,
    minimumPressureMagnitude: 0,
    minimumReactionProbability: 0,
    minimumReversalProbability: 0,
    minimumSalience: 0,
    opacity: 1,
    preset: "micro",
    showConnectors: true,
    showAxisLabel: false,
    showHistoricalLabels: true,
    showLabels: true,
    showUnifiedActive: true,
    showUnifiedBroken: true,
    showUnifiedResistance: true,
    showUnifiedRoleFlipped: true,
    showUnifiedSupport: true,
    showValue: true,
    upColor: resolveChartColor("var(--success)"),
    visible: series.defaultVisible !== false
  };
}

function resolveLegendSettings(settingsMap: LegendSettingsMap, key: string, series: ChartSeries): Required<LegendSeriesSettings> {
  const defaults = defaultLegendSettings(series);
  const stored = settingsMap[key] ?? {};
  return {
    color: resolveChartColor(stored.color || defaults.color),
    downColor: validHexColor(stored.downColor, defaults.downColor),
    currentLevelCount: Math.max(1, Math.min(6, Math.round(stored.currentLevelCount ?? defaults.currentLevelCount))),
    historyBars: resolveHistoryBars(stored.historyBars, defaults.historyBars),
    labelFontSize: Math.max(9, Math.min(18, Math.round(stored.labelFontSize ?? defaults.labelFontSize))),
    lineStyle: stored.lineStyle || defaults.lineStyle,
    lineWidth: Math.max(1, Math.min(4, Math.round(stored.lineWidth ?? defaults.lineWidth))),
    maximumBreakProbability: clampNumber(stored.maximumBreakProbability, 0, 1, defaults.maximumBreakProbability),
    minimumConfidence: clampNumber(stored.minimumConfidence, 0, 1, defaults.minimumConfidence),
    minimumHoldProbability: clampNumber(stored.minimumHoldProbability, 0, 1, defaults.minimumHoldProbability),
    minimumPressureMagnitude: clampNumber(stored.minimumPressureMagnitude, 0, 1, defaults.minimumPressureMagnitude),
    minimumReactionProbability: clampNumber(stored.minimumReactionProbability, 0, 1, defaults.minimumReactionProbability),
    minimumReversalProbability: clampNumber(stored.minimumReversalProbability, 0, 1, defaults.minimumReversalProbability),
    minimumSalience: clampNumber(stored.minimumSalience, 0, 1, defaults.minimumSalience),
    opacity: clampNumber(stored.opacity ?? defaults.opacity, 0, 1, 1),
    preset: stored.preset === "tactical" || stored.preset === "context" ? stored.preset : defaults.preset,
    showConnectors: stored.showConnectors ?? defaults.showConnectors,
    showAxisLabel: stored.showAxisLabel ?? defaults.showAxisLabel,
    showHistoricalLabels: stored.showHistoricalLabels ?? defaults.showHistoricalLabels,
    showLabels: stored.showLabels ?? defaults.showLabels,
    showUnifiedActive: stored.showUnifiedActive ?? defaults.showUnifiedActive,
    showUnifiedBroken: stored.showUnifiedBroken ?? defaults.showUnifiedBroken,
    showUnifiedResistance: stored.showUnifiedResistance ?? defaults.showUnifiedResistance,
    showUnifiedRoleFlipped: stored.showUnifiedRoleFlipped ?? defaults.showUnifiedRoleFlipped,
    showUnifiedSupport: stored.showUnifiedSupport ?? defaults.showUnifiedSupport,
    showValue: stored.showValue ?? defaults.showValue,
    upColor: validHexColor(stored.upColor, defaults.upColor),
    visible: stored.visible ?? defaults.visible
  };
}

type ResolvedPriceZoneLegendSettings = {
  color: string;
  currentLevelCount: number;
  downColor: string;
  historyBars: number;
  labelFontSize: number;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  maximumBreakProbability: number;
  minimumConfidence: number;
  minimumHoldProbability: number;
  minimumPressureMagnitude: number;
  minimumReactionProbability: number;
  minimumReversalProbability: number;
  minimumSalience: number;
  opacity: number;
  preset: ChartPreset;
  showConnectors: boolean;
  showAxisLabel: boolean;
  showHistoricalLabels: boolean;
  showUnifiedActive: boolean;
  showUnifiedBroken: boolean;
  showUnifiedResistance: boolean;
  showUnifiedRoleFlipped: boolean;
  showUnifiedSupport: boolean;
  upColor: string;
  visible: boolean;
};

function resolvePriceZoneLegendSettings(settingsMap: LegendSettingsMap, key: string, zone?: PriceZone): ResolvedPriceZoneLegendSettings {
  const stored = settingsMap[key] ?? {};
  return {
    color: validHexColor(stored.color, resolveChartColor(zone?.color || "var(--muted-foreground)")),
    currentLevelCount: Math.max(1, Math.min(6, Math.round(stored.currentLevelCount ?? 3))),
    downColor: validHexColor(stored.downColor, resolveChartColor("var(--danger)")),
    historyBars: resolveHistoryBars(stored.historyBars, zone?.historyBarsDefault ?? 20),
    labelFontSize: Math.max(9, Math.min(18, Math.round(stored.labelFontSize ?? 11))),
    lineStyle: stored.lineStyle ?? zoneBorderStyle(zone?.borderStyle),
    lineWidth: Math.max(1, Math.min(4, Math.round(stored.lineWidth ?? zone?.borderWidth ?? 1))),
    maximumBreakProbability: clampNumber(stored.maximumBreakProbability, 0, 1, 1),
    minimumConfidence: clampNumber(stored.minimumConfidence, 0, 1, 0),
    minimumHoldProbability: clampNumber(stored.minimumHoldProbability, 0, 1, 0),
    minimumPressureMagnitude: clampNumber(stored.minimumPressureMagnitude, 0, 1, 0),
    minimumReactionProbability: clampNumber(stored.minimumReactionProbability, 0, 1, 0),
    minimumReversalProbability: clampNumber(stored.minimumReversalProbability, 0, 1, 0),
    minimumSalience: clampNumber(stored.minimumSalience, 0, 1, 0),
    opacity: clampNumber(stored.opacity ?? zone?.opacityDefault ?? 1, 0, 1, 1),
    preset: stored.preset === "tactical"
      || stored.preset === "context"
      || stored.preset === "axis-history"
      || stored.preset === "swing-rails"
      ? stored.preset
      : zone?.presetDefault ?? zone?.preset ?? "micro",
    showConnectors: stored.showConnectors !== false,
    showAxisLabel: stored.showAxisLabel ?? zone?.axisLabelDefault ?? false,
    showHistoricalLabels: stored.showHistoricalLabels ?? zone?.historicalLabelsDefault ?? false,
    showUnifiedActive: stored.showUnifiedActive !== false,
    showUnifiedBroken: stored.showUnifiedBroken !== false,
    showUnifiedResistance: stored.showUnifiedResistance !== false,
    showUnifiedRoleFlipped: stored.showUnifiedRoleFlipped !== false,
    showUnifiedSupport: stored.showUnifiedSupport !== false,
    upColor: validHexColor(stored.upColor, resolveChartColor("var(--success)")),
    visible: stored.visible ?? zone?.defaultVisible ?? true,
  };
}

function priceZoneMeetsUnifiedFilters(zone: PriceZone, settings: ResolvedPriceZoneLegendSettings) {
  if (zone.annotationKind !== "unified-structure-level") return true;
  const roleVisible = zone.tone === "buy" ? settings.showUnifiedSupport : settings.showUnifiedResistance;
  const stateVisible = zone.latest ? settings.showUnifiedActive : settings.showUnifiedBroken;
  const flipVisible = !(Number(zone.roleFlipCount) > 0) || settings.showUnifiedRoleFlipped;
  return roleVisible && stateVisible && flipVisible
    && clampNumber(zone.strength, 0, 1, 0) >= settings.minimumSalience
    && clampNumber(zone.probabilityLineRatio, 0, 1, 0) >= settings.minimumReactionProbability
    && clampNumber(zone.holdProbability, 0, 1, 0) >= settings.minimumHoldProbability
    && clampNumber(zone.reversalProbability, 0, 1, 0) >= settings.minimumReversalProbability
    && Math.abs(clampNumber(zone.pressureBias, -1, 1, 0)) >= settings.minimumPressureMagnitude
    && clampNumber(zone.breakProbability, 0, 1, 0) <= settings.maximumBreakProbability
    && clampNumber(zone.confidence, 0, 1, 0) >= settings.minimumConfidence;
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
  showLeftPriceScale = true,
  reserveRightPriceScale = true,
) {
  const timeframeSeconds = chartTimeframeSeconds(timeframe);
  const showSeconds = timeframeSeconds !== null && timeframeSeconds < 60;
  const macroTimeframe = isMacroTimeframe(timeframe);
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
      vertLines: { color: palette.grid, visible: settings.gridVisible },
      horzLines: { color: palette.grid, visible: settings.gridVisible }
    },
    localization: {
      timeFormatter: (timeValue: Time) => formatMarketDateTime(timeValue, timeframe)
    },
    crosshair: {
      horzLine: { color: palette.text, labelBackgroundColor: palette.text, labelVisible: true, style: LineStyle.Dotted, visible: true, width: 1 as LineWidth },
      mode: 0,
      vertLine: { color: palette.grid, labelBackgroundColor: palette.text, labelVisible: true, style: LineStyle.Dotted, visible: true, width: 1 as LineWidth },
    },
    // Price-axis labels must remain at their actual value coordinate. The library's
    // default collision alignment stacks dense indicator tags at screen-stable offsets,
    // which visually detaches them from the scale while the chart is panned vertically.
    rightPriceScale: { alignLabels: false, borderColor: palette.grid, minimumWidth: reserveRightPriceScale ? CHART_PRICE_SCALE_MIN_WIDTH : 0 },
    leftPriceScale: { alignLabels: false, borderColor: palette.grid, minimumWidth: CHART_PRICE_SCALE_MIN_WIDTH, visible: showLeftPriceScale },
    timeScale: {
      borderColor: palette.grid,
      // The left edge must remain movable so panning can cross the loaded boundary
      // and trigger the incremental history callback.
      fixLeftEdge: false,
      // Leave the future side navigable so traders can reserve working space for
      // bars that have not arrived yet.
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
const DAILY_MACRO_WINDOW_BARS = 756;
const WEEKLY_MACRO_WINDOW_BARS = 156;
const MONTHLY_MACRO_WINDOW_BARS = 36;
const YEARLY_MACRO_WINDOW_BARS = 20;

function fitLatestSession(target: ChartRangeTarget, candles: Candle[], timeframe = "", hideEmptyIntervals = true) {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = chartTimelineData(candles, timeframe, hideEmptyIntervals);
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
  const sessionSpan = lastIndex - firstIndex + 1;
  const edgePadding = Math.max(2, Math.min(60, Math.ceil(sessionSpan * 0.025)));
  setChartLogicalRange(charts, { from: firstIndex - edgePadding, to: lastIndex + edgePadding });
}

function resetChartViewport(chart: IChartApi | null, candles: Candle[], timeframe: string, chartWidth: number, candleSize: number, hideEmptyIntervals = true) {
  if (!chart) return;
  const timeScale = chart.timeScale();
  const normalizedCandleSize = clampNumber(candleSize, 8, 80, defaultChartAppearanceSettings.candleSize);
  timeScale.applyOptions({
    barSpacing: normalizedCandleSize,
    rightOffset: 2
  });
  const timelineLength = chartTimelineData(candles, timeframe, hideEmptyIntervals).length;
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
  return timeframe === "1d" || timeframe === "1w" || timeframe === "1mo" || timeframe === "1y";
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

function fitInitialRange(chart: IChartApi | null, candles: Candle[], timeframe = "", mode: ChartPanelProps["initialFitMode"] = "default", hideEmptyIntervals = true) {
  if (!chart || !candles.length) return;
  if (mode === "live_first_10") {
    fitLiveFirstTenMinutes(chart, candles, timeframe, hideEmptyIntervals);
    return;
  }
  if (mode === "recent") {
    centerLatest(chart, candles, timeframe, hideEmptyIntervals);
    return;
  }
  if (mode === "last_market_day") {
    fitLastMarketDay(chart, candles, timeframe, hideEmptyIntervals);
    return;
  }
  if (hasMultipleMarketDates(candles)) {
    const timeline = chartTimelineData(candles, timeframe, hideEmptyIntervals);
    chart.timeScale().setVisibleLogicalRange({ from: -1, to: Math.max(8, timeline.length) });
    return;
  }
  fitLatestSession(chart, candles, timeframe, hideEmptyIntervals);
}

function fitLiveFirstTenMinutes(target: ChartRangeTarget, candles: Candle[], timeframe: string, hideEmptyIntervals = true) {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = chartTimelineData(candles, timeframe, hideEmptyIntervals);
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

function fitLastMarketDay(chart: IChartApi | null, candles: Candle[], timeframe: string, hideEmptyIntervals = true) {
  fitLatestSession(chart, candles, timeframe, hideEmptyIntervals);
}

function centerLatest(target: ChartRangeTarget, candles: Candle[], timeframe = "", hideEmptyIntervals = true) {
  const charts = chartRangeTargets(target);
  if (!charts.length || !candles.length) return;
  const timeline = chartTimelineData(candles, timeframe, hideEmptyIntervals);
  const lastCandle = candles[candles.length - 1];
  const last = nearestTimelineIndex(timeline, lastCandle.time);
  if (isMacroTimeframe(timeframe)) {
    const requestedBars = timeframe === "1y"
      ? YEARLY_MACRO_WINDOW_BARS
      : timeframe === "1mo"
        ? MONTHLY_MACRO_WINDOW_BARS
        : timeframe === "1w"
          ? WEEKLY_MACRO_WINDOW_BARS
          : DAILY_MACRO_WINDOW_BARS;
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

function centerReferenceOrLatest(target: ChartRangeTarget, candles: Candle[], reference: ChartReference | null | undefined, timeframe: string, mode: ChartPanelProps["initialFitMode"] = "default", hideEmptyIntervals = true) {
  if (reference) {
    fitAroundReference(target, candles, reference, timeframe, hideEmptyIntervals);
    return;
  }
  if (mode === "live_first_10") {
    fitLiveFirstTenMinutes(target, candles, timeframe, hideEmptyIntervals);
    return;
  }
  if (mode === "last_market_day") {
    fitLatestSession(target, candles, timeframe, hideEmptyIntervals);
    return;
  }
  centerLatest(target, candles, timeframe, hideEmptyIntervals);
}

function fitAroundReference(target: ChartRangeTarget, candles: Candle[], reference: ChartReference, timeframe: string, hideEmptyIntervals = true) {
  const charts = chartRangeTargets(target);
  const chart = charts[0];
  if (!chart || !candles.length) return;
  const referenceTime = resolveFitReferenceTime(reference, candles);
  if (referenceTime === null) {
    fitInitialRange(chart, candles, timeframe, "default", hideEmptyIntervals);
    return;
  }
  const timeline = chartTimelineData(candles, timeframe, hideEmptyIntervals);
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
  candles: Candle[],
  timeline: CandleSeriesDatum[],
  settings: ChartAppearanceSettings,
  liveEntryLine?: LiveEntryLine | null
) {
  if (!layer) return;
  const plotLayer = drawSessionRegions(chart, layer, regions, timeline, candles, settings, true);
  if (!plotLayer) return;
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
) {
  if (!layer) return null;
  clearOverlayLayer(layer);
  const plotLayer = document.createElement("div");
  plotLayer.className = "session-plot-region";
  plotLayer.style.left = `${priceScaleWidth(chart, "left")}px`;
  plotLayer.style.right = `${priceScaleWidth(chart, "right")}px`;
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

function priceScaleWidth(chart: IChartApi, scaleId: "left" | "right") {
  try {
    const width = chart.priceScale(scaleId).width();
    return Number.isFinite(width) ? width : 0;
  } catch {
    // Lightweight Charts has no price-axis widget while a scale is hidden or
    // being recreated. An absent axis occupies no plot gutter.
    return 0;
  }
}

function clearOverlayLayer(layer: HTMLDivElement) {
  layer.replaceChildren();
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
  line.style.borderColor = liveEntryLine.color;

  const control = document.createElement("div");
  control.className = "live-entry-position-control";

  const sizeBadge = document.createElement("span");
  sizeBadge.className = "live-entry-size-badge";
  sizeBadge.style.background = liveEntryLine.color;
  if (liveEntryLine.labelParts?.length) {
    liveEntryLine.labelParts.forEach((part) => {
      const piece = document.createElement("b");
      piece.className = `trade-label-part ${part.tone ?? "label"}`;
      piece.textContent = part.text;
      sizeBadge.appendChild(piece);
    });
  } else {
    sizeBadge.textContent = liveEntryLine.quantity.toLocaleString();
  }
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
    const presentationColor = priceZonePresentationColors(zone, chartBackground, settings).borderColor;
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
    const historyStart = priceZoneHistoryStart(candles, itemZones, settings.historyBars);
    const selectedZones = itemZones.filter((zone) => !zone.preset || zone.preset === settings.preset);
    if (selectedZones.some((zone) => zone.annotationKind === "level-footprint")) {
      drawLevelFootprintProfile(
        priceSeries,
        context,
        width,
        height,
        selectedZones,
        settings,
        chartBackground,
      );
      return;
    }
    if (selectedZones.some((zone) => zone.annotationKind === "swing-footprint")) {
      drawSwingFootprintRails(
        chart,
        priceSeries,
        context,
        width,
        height,
        selectedZones,
        settings,
        chartBackground,
        candles,
        candleDuration,
        historyStart,
      );
      return;
    }
    if (selectedZones.some((zone) => zone.annotationKind === "luld-line")) {
      drawContinuousReferenceLines(
        chart,
        priceSeries,
        context,
        width,
        height,
        selectedZones,
        settings,
        chartBackground,
        candles,
        candleDuration,
        historyStart,
      );
      return;
    }
    selectedZones.forEach((zone) => {
      if (!priceZoneWithinHistory(zone, historyStart)) return;
      if (
        zone.currentLevelSide
        && !zone.currentLevelStrongest
        && (zone.currentLevelDistanceRank ?? Number.POSITIVE_INFINITY) > settings.currentLevelCount
      ) return;
      if (
        (zone.annotationKind === "signal-episode-range"
          || zone.annotationKind === "signal-episode-rail")
        && zone.episodeSteps?.length
      ) {
        drawSignalEpisodePrimitive(
          chart,
          priceSeries,
          context,
          width,
          height,
          zone,
          settings,
          chartBackground,
          candles,
          barWidth,
          candleDuration,
        );
        return;
      }
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
      const { borderColor, confidence, fillColor } = priceZonePresentationColors(zone, chartBackground, settings);
      const lineOnly = zone.renderMode === "line" || isStructureBreakZone(zone);
      const baseFillOpacity = clampNumber(zone.fillOpacity, 0.02, 0.35, 0.08);
      const fillOpacity = lineOnly ? 0 : baseFillOpacity * (confidence === null ? 1 : 0.45 + 0.55 * confidence) * settings.opacity;
      const borderOpacity = zone.annotationKind === "signal-episode-range" || zone.annotationKind === "unified-structure-level"
        ? 0
        : lineOnly
        ? settings.opacity
        : zone.currentLevelSide ? 0 : confidence === null
        ? clampNumber(zone.borderOpacity, 0, 0.35, Math.max(baseFillOpacity * 1.8, 0.12)) * settings.opacity
        : (0.24 + 0.7 * confidence) * settings.opacity;
      const lineWidth = zone.annotationKind === "signal-episode-rail" && confidence !== null
        ? Math.max(1, Math.min(6, settings.lineWidth * (0.75 + 1.75 * confidence)))
        : lineOnly || confidence === null
        ? settings.lineWidth
        : Math.max(1, Math.min(6, settings.lineWidth * (0.75 + 1.25 * confidence)));
      context.save();
      context.fillStyle = rgbaFromHex(fillColor, fillOpacity);
      context.strokeStyle = rgbaFromHex(borderColor, borderOpacity);
      context.lineWidth = lineWidth;
      context.setLineDash(canvasLineDash(settings.lineStyle, lineWidth));
      if (isStructureBreakZone(zone)) {
        if (settings.showConnectors) {
          const eventX = zone.eventTime
            ? xForStructureEventTime(chart, zone.eventTime, candles, candleDuration)
            : coordinates.end;
          const connector = clippedHorizontalSpan(coordinates.start, eventX ?? coordinates.end, width);
          if (connector) {
            // Break connectors reuse the originating swing's price. Clear the
            // swing reference beneath this segment so dashed break styles do
            // not expose a second semantic color through their gaps.
            context.save();
            context.strokeStyle = chartBackground;
            context.lineWidth = lineWidth + 2;
            context.setLineDash([]);
            context.beginPath();
            context.moveTo(connector.left, center);
            context.lineTo(connector.right, center);
            context.stroke();
            context.restore();
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
        if (zone.annotationKind === "unified-structure-level") {
          const probability = clampNumber(zone.probabilityLineRatio, 0, 1, 0);
          const probabilityWidth = clampNumber(zone.probabilityLineWidth, 1, 6, 2);
          if (probability > 0) {
            context.strokeStyle = rgbaFromHex(borderColor, 0.82 * settings.opacity);
            context.lineWidth = probabilityWidth;
            context.setLineDash([]);
            context.beginPath();
            context.moveTo(span.left, center);
            context.lineTo(span.left + span.width * probability, center);
            context.stroke();
          }
        }
      }
      context.restore();
    });
  });
}

function drawContinuousReferenceLines(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  zones: PriceZone[],
  settings: ResolvedPriceZoneLegendSettings,
  chartBackground: string,
  candles: Candle[],
  candleDuration: number,
  historyStart: number,
) {
  const grouped = new Map<string, PriceZone[]>();
  zones
    .filter((zone) => zone.annotationKind === "luld-line" && priceZoneWithinHistory(zone, historyStart))
    .forEach((zone) => {
      const key = zone.compactLabel || zone.label;
      grouped.set(key, [...(grouped.get(key) ?? []), zone]);
    });
  grouped.forEach((lineZones) => {
    const ordered = [...lineZones].sort((left, right) => left.start - right.start);
    if (!ordered.length) return;
    const { borderColor } = priceZonePresentationColors(ordered[0], chartBackground, settings);
    context.save();
    context.strokeStyle = rgbaFromHex(borderColor, settings.opacity);
    context.lineWidth = settings.lineWidth;
    context.setLineDash(canvasLineDash(settings.lineStyle, settings.lineWidth));
    context.lineJoin = "round";
    context.beginPath();
    let drawing = false;
    let previousEnd = Number.NaN;
    let previousY = Number.NaN;
    ordered.forEach((zone) => {
      const startX = xForStructureEventTime(chart, zone.start, candles, candleDuration);
      const endX = xForStructureEventTime(chart, zone.end, candles, candleDuration);
      const y = priceSeries.priceToCoordinate(zone.lower);
      if (startX === null || endX === null || y === null || y < 0 || y > height) {
        drawing = false;
        previousEnd = Number.NaN;
        previousY = Number.NaN;
        return;
      }
      const start = Math.max(0, Math.min(width, startX));
      const end = Math.max(0, Math.min(width, endX));
      if (end <= start) return;
      const contiguous = drawing
        && Math.abs(zone.start - previousEnd) <= Math.max(0.001, candleDuration * 0.51);
      if (!contiguous) {
        context.moveTo(start, y);
      } else {
        // QMD updates these estimated bands discretely. A step joins the prior
        // observation to the next without inventing an unobserved diagonal.
        context.lineTo(start, previousY);
        context.lineTo(start, y);
      }
      context.lineTo(end, y);
      drawing = true;
      previousEnd = zone.end;
      previousY = y;
    });
    context.stroke();
    context.restore();
  });
}

function drawSignalEpisodePrimitive(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  zone: PriceZone,
  settings: ReturnType<typeof resolvePriceZoneLegendSettings>,
  chartBackground: string,
  candles: Candle[],
  barWidth: number,
  candleDuration: number,
) {
  const steps = zone.episodeSteps ?? [];
  if (!steps.length) return;
  const { borderColor, fillColor } = priceZonePresentationColors(zone, chartBackground, settings);
  context.save();
  context.lineCap = "butt";
  const renderedSteps = steps.flatMap((step) => {
    const coordinates = signalEpisodeStepCoordinates(
      chart,
      candles,
      step.start,
      step.end,
      barWidth,
      candleDuration,
      width,
    );
    if (!coordinates) return [];
    const left = coordinates.start;
    const right = coordinates.end;
    if (right < 0 || left > width || right <= left) return [];
    return [{ ...step, left, right }];
  });
  if (!renderedSteps.length) {
    context.restore();
    return;
  }
  if (zone.annotationKind === "signal-episode-range") {
    renderedSteps.forEach((step) => {
      const upper = priceSeries.priceToCoordinate(step.upper);
      const lower = priceSeries.priceToCoordinate(step.lower);
      if (upper === null || lower === null) return;
      const top = Math.max(0, Math.min(upper, lower));
      const bottom = Math.min(height, Math.max(upper, lower));
      if (bottom <= top) return;
      const confidence = clampNumber(step.confidence, 0, 1, 0);
      const opacity = clampNumber(zone.fillOpacity, 0.02, 0.35, 0.08)
        * (0.45 + 0.55 * confidence)
        * settings.opacity;
      context.fillStyle = rgbaFromHex(fillColor, opacity);
      // Adjacent full-slot rectangles intentionally overlap by one device
      // pixel. Canvas sub-pixel rounding otherwise exposes white seams while
      // panning or scaling an episode rendered across multiple chart bars.
      context.fillRect(step.left, top, step.right - step.left + 1, bottom - top);
    });
    context.restore();
    return;
  }
  const railCoordinate = priceSeries.priceToCoordinate(zone.lower);
  if (railCoordinate !== null && railCoordinate >= 0 && railCoordinate <= height) {
    const confidence = Math.max(
      ...renderedSteps.map((step) => clampNumber(step.confidence, 0, 1, 0)),
    );
    const lineWidth = Math.max(1, Math.min(6, settings.lineWidth * (0.75 + 1.75 * confidence)));
    context.strokeStyle = rgbaFromHex(borderColor, settings.opacity);
    context.lineWidth = lineWidth;
    context.setLineDash(canvasLineDash(settings.lineStyle, lineWidth));
    context.beginPath();
    context.moveTo(renderedSteps[0].left, railCoordinate);
    context.lineTo(renderedSteps[renderedSteps.length - 1].right, railCoordinate);
    context.stroke();
  }
  context.restore();
}

function drawLevelFootprintProfile(
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  zones: PriceZone[],
  settings: ResolvedPriceZoneLegendSettings,
  chartBackground: string,
) {
  const validZones = zones.filter((zone) =>
    zone.annotationKind === "level-footprint"
    && Number.isFinite(zone.totalVolume)
    && Number(zone.totalVolume) > 0,
  );
  if (!validZones.length) return;
  const rowHeight = Math.max(4, Math.min(9, height / 110));
  const renderedBins = new Map<number, {
    buyVolume: number;
    center: number;
    neutralVolume: number;
    sellVolume: number;
    totalVolume: number;
  }>();
  validZones.forEach((zone) => {
    const center = priceSeries.priceToCoordinate(zone.lower);
    if (center === null || center < -rowHeight || center > height + rowHeight) return;
    const bucket = Math.round(center / rowHeight);
    const current = renderedBins.get(bucket) ?? {
      buyVolume: 0,
      center: bucket * rowHeight,
      neutralVolume: 0,
      sellVolume: 0,
      totalVolume: 0,
    };
    current.buyVolume += Math.max(0, Number(zone.buyVolume) || 0);
    current.neutralVolume += Math.max(0, Number(zone.neutralVolume) || 0);
    current.sellVolume += Math.max(0, Number(zone.sellVolume) || 0);
    current.totalVolume += Math.max(0, Number(zone.totalVolume) || 0);
    renderedBins.set(bucket, current);
  });
  const bins = [...renderedBins.values()].filter((bin) => bin.totalVolume > 0);
  if (!bins.length) return;
  const orderedVolumes = bins.map((bin) => bin.totalVolume).sort((left, right) => left - right);
  const referenceIndex = Math.max(0, Math.min(orderedVolumes.length - 1, Math.ceil(orderedVolumes.length * 0.95) - 1));
  const referenceVolume = orderedVolumes[referenceIndex];
  if (!(referenceVolume > 0)) return;
  const profileWidth = Math.min(280, Math.max(120, width * 0.2));
  const right = width - 5;
  const profileLeft = right - profileWidth;
  const upColor = validHexColor(settings.upColor, resolveChartColor("var(--success)"));
  const downColor = validHexColor(settings.downColor, resolveChartColor("var(--danger)"));
  const neutralColor = validHexColor(resolveChartColor("var(--muted-foreground)"), "#73778A");
  context.save();
  context.globalCompositeOperation = "source-over";
  context.fillStyle = rgbaFromHex(chartBackground, 0.1 + settings.opacity * 0.08);
  context.fillRect(profileLeft, 0, profileWidth, height);
  bins.forEach((bin) => {
    const barWidth = profileWidth * Math.min(1, bin.totalVolume / referenceVolume);
    const buyWidth = barWidth * bin.buyVolume / bin.totalVolume;
    const sellWidth = barWidth * bin.sellVolume / bin.totalVolume;
    const neutralWidth = Math.max(0, barWidth - buyWidth - sellWidth);
    let cursor = right - barWidth;
    const top = bin.center - rowHeight * 0.43;
    const heightPx = Math.max(2, rowHeight * 0.86);
    if (buyWidth > 0) {
      context.fillStyle = rgbaFromHex(upColor, 0.2 + settings.opacity * 0.72);
      context.fillRect(cursor, top, buyWidth, heightPx);
      cursor += buyWidth;
    }
    if (neutralWidth > 0) {
      context.fillStyle = rgbaFromHex(neutralColor, 0.14 + settings.opacity * 0.42);
      context.fillRect(cursor, top, neutralWidth, heightPx);
      cursor += neutralWidth;
    }
    if (sellWidth > 0) {
      context.fillStyle = rgbaFromHex(downColor, 0.2 + settings.opacity * 0.72);
      context.fillRect(cursor, top, sellWidth, heightPx);
    }
  });
  context.restore();
}

function drawSwingFootprintRails(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  zones: PriceZone[],
  settings: ResolvedPriceZoneLegendSettings,
  chartBackground: string,
  candles: Candle[],
  candleDuration: number,
  historyStart: number,
) {
  const validZones = zones.filter((zone) =>
    zone.annotationKind === "swing-footprint"
    && zone.start >= historyStart
    && Number.isFinite(zone.totalVolume)
    && Number(zone.totalVolume) > 0,
  );
  if (!validZones.length) return;
  const barWidth = estimateBarWidth(chart, candles);
  const trackWidth = Math.max(38, Math.min(96, barWidth * 4));
  const railHeight = Math.max(4, Math.min(7, barWidth * 0.32));
  const railGap = Math.max(2, Math.min(4, railHeight * 0.55));
  const labelClearance = Math.max(15, Math.min(24, (settings.labelFontSize || 11) + barWidth * 0.55));
  const upColor = validHexColor(settings.upColor, resolveChartColor("var(--success)"));
  const downColor = validHexColor(settings.downColor, resolveChartColor("var(--danger)"));
  const trackColor = mixHexColors(
    chartBackground,
    validHexColor(resolveChartColor("var(--muted-foreground)"), "#73778A"),
    0.28,
  );
  context.save();
  context.globalCompositeOperation = "source-over";
  validZones.forEach((zone) => {
    const totalVolume = Number(zone.totalVolume) || 0;
    const price = Number(zone.lower);
    const x = xForStructureEventTime(chart, zone.start, candles, candleDuration);
    const lineY = priceSeries.priceToCoordinate(price);
    if (x === null || lineY === null || x < -trackWidth || x > width + trackWidth) return;
    const buyShare = clampNumber((Number(zone.buyVolume) || 0) / totalVolume, 0, 1, 0);
    const sellShare = clampNumber((Number(zone.sellVolume) || 0) / totalVolume, 0, 1, 0);
    // The swing-candle center is the causal x-origin. Keep that anchor
    // invariant and extend both volume tracks to the right rather than
    // straddling the candle.
    const left = Math.max(4, x);
    const visibleTrackWidth = Math.min(trackWidth, width - 4 - left);
    if (visibleTrackWidth < 2) return;
    const cardHeight = railHeight * 2 + railGap;
    const preferredTop = lineY + labelClearance;
    const cardTop = preferredTop + cardHeight <= height - 3
      ? preferredTop
      : Math.max(3, lineY - labelClearance - cardHeight);
    const buyTop = cardTop;
    const sellTop = cardTop + railHeight + railGap;
    if (buyTop > height || sellTop + railHeight < 0) return;
    context.fillStyle = rgbaFromHex(trackColor, 0.24 + settings.opacity * 0.28);
    context.fillRect(left, buyTop, visibleTrackWidth, railHeight);
    context.fillRect(left, sellTop, visibleTrackWidth, railHeight);
    if (buyShare > 0) {
      context.fillStyle = rgbaFromHex(upColor, 0.34 + settings.opacity * 0.62);
      context.fillRect(left, buyTop, Math.min(visibleTrackWidth, trackWidth * buyShare), railHeight);
    }
    if (sellShare > 0) {
      context.fillStyle = rgbaFromHex(downColor, 0.34 + settings.opacity * 0.62);
      context.fillRect(left, sellTop, Math.min(visibleTrackWidth, trackWidth * sellShare), railHeight);
    }
  });
  context.restore();
}

function signalEpisodeStepCoordinates(
  chart: IChartApi,
  candles: Candle[],
  start: number,
  end: number,
  barWidth: number,
  candleDuration: number,
  width: number,
) {
  let firstIndex = lowerBoundCandleTime(candles, start - candleDuration);
  while (
    firstIndex < candles.length
    && !(candles[firstIndex].time < end && candles[firstIndex].time + candleDuration > start)
  ) firstIndex += 1;
  const lastIndex = lowerBoundCandleTime(candles, end) - 1;
  if (firstIndex >= candles.length || lastIndex < firstIndex) return null;
  const first = chart.timeScale().timeToCoordinate(candles[firstIndex].time as Time);
  const last = chart.timeScale().timeToCoordinate(candles[lastIndex].time as Time);
  if (first === null || last === null) return null;
  const previous = firstIndex > 0
    ? chart.timeScale().timeToCoordinate(candles[firstIndex - 1].time as Time)
    : null;
  const next = lastIndex + 1 < candles.length
    ? chart.timeScale().timeToCoordinate(candles[lastIndex + 1].time as Time)
    : null;
  const leftHalfStep = previous === null ? barWidth / 2 : Math.abs(first - previous) / 2;
  const rightHalfStep = next === null ? barWidth / 2 : Math.abs(next - last) / 2;
  return {
    start: Math.max(0, first - leftHalfStep),
    end: Math.min(width, last + rightHalfStep),
  };
}

function drawPriceZonePrimitiveLabels(
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
  const plotBottom = height;
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
  const orderedLabelGroups = Array.from(historicalBySettings.entries()).sort(([, firstZones], [, secondZones]) => (
    priceZoneLabelGroupPriority(firstZones) - priceZoneLabelGroupPriority(secondZones)
  ));
  orderedLabelGroups.forEach(([id, itemZones]) => {
    const settings = resolvePriceZoneLegendSettings(legendSettings, priceZoneLegendKey(id), itemZones[itemZones.length - 1]);
    if (!settings.visible) return;
    if (itemZones.some((zone) =>
      zone.annotationKind === "level-footprint"
      || zone.annotationKind === "swing-footprint")) return;
    const historyStart = priceZoneHistoryStart(candles, itemZones, settings.historyBars);
    const eligibleZones = itemZones.filter((zone) => {
      if (zone.preset && zone.preset !== settings.preset) return false;
      if (!priceZoneWithinHistory(zone, historyStart)) return false;
      if (!zone.currentLevelSide) return true;
      return Boolean(zone.currentLevelStrongest)
        || (zone.currentLevelDistanceRank ?? Number.POSITIVE_INFINITY) <= settings.currentLevelCount;
    });
    const historicalTagZones = new Set(
      eligibleZones
        .filter((zone) => Boolean(zone.compactLabel))
        .sort((first, second) => priceZoneLabelTime(first) - priceZoneLabelTime(second))
        .slice(settings.historyBars === 0 ? 0 : -settings.historyBars),
    );
    const orderedLabelZones = [...eligibleZones].sort((first, second) => (
      priceZoneLabelPriority(first) - priceZoneLabelPriority(second)
      || priceZoneLabelTime(second) - priceZoneLabelTime(first)
    ));
    orderedLabelZones.forEach((zone) => {
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
      const { borderColor } = priceZonePresentationColors(zone, chartBackground, settings);
      let labelSpan: HorizontalSpan | null = span;
      if (isStructureBreakZone(zone)) {
        const eventX = zone.eventTime
          ? xForStructureEventTime(chart, zone.eventTime, candles, candleDuration)
          : coordinates.end;
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
      if (
        zone.compactLabel
        && (labelSpan || isStructureBreakZone(zone))
        && settings.showHistoricalLabels
        && historicalTagZones.has(zone)
      ) {
        if (isStructureBreakZone(zone)) {
          candleBoxes ??= visibleCandleBoxes(chart, priceSeries, candles, barWidth, width, plotBottom);
          const eventX = xForStructureEventTime(
            chart,
            zone.eventTime ?? zone.end,
            candles,
            candleDuration,
          ) ?? coordinates.end;
          drawAnchoredStructureBreakLabel(
            context,
            zone.compactLabel,
            coordinates.start,
            eventX,
            center,
            borderColor,
            chartBackground,
            zone.tone === "buy" ? "above" : "below",
            settings,
            lineLabelBoxes,
            candleBoxes,
            width,
            plotBottom,
            barWidth,
          );
        } else if (labelSpan && isSwingReferenceZone(zone)) {
          candleBoxes ??= visibleCandleBoxes(chart, priceSeries, candles, barWidth, width, plotBottom);
          drawAnchoredSwingLabel(
            context,
            zone.compactLabel,
            coordinates.start,
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
        } else if (labelSpan) {
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
      }
    });
  });
}

function canvasInterfaceFont() {
  return getComputedStyle(document.documentElement).getPropertyValue("--font-body").trim() || "sans-serif";
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
  context.font = `700 ${fontSize}px ${canvasInterfaceFont()}`;
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
  return zone.annotationKind === "bos"
    || zone.annotationKind === "choch"
    || zone.annotationKind === "structure-break";
}

function priceZoneHistoryStart(candles: Candle[], zones: PriceZone[], historyBars: number) {
  if (historyBars === 0) return Number.NEGATIVE_INFINITY;
  const latestTime = candles[candles.length - 1]?.time;
  const sourceSeconds = zones.find((zone) =>
    Number.isFinite(zone.historyTimeframeSeconds) && Number(zone.historyTimeframeSeconds) > 0
  )?.historyTimeframeSeconds;
  if (Number.isFinite(latestTime) && Number.isFinite(sourceSeconds) && Number(sourceSeconds) > 0) {
    return Number(latestTime) - historyBars * Number(sourceSeconds);
  }
  return candles[Math.max(0, candles.length - historyBars)]?.time ?? Number.NEGATIVE_INFINITY;
}

function resolveHistoryBars(value: number | undefined, fallback: number) {
  const rounded = Math.round(value ?? fallback);
  return rounded === 0 ? 0 : Math.max(20, Math.min(1000, rounded));
}

function priceZoneWithinHistory(zone: PriceZone, historyStart: number) {
  if (zone.annotationKind === "level-footprint") return true;
  if (zone.annotationKind === "swing-footprint") return zone.start >= historyStart;
  if (isStructureBreakZone(zone)) return (zone.eventTime ?? zone.end) >= historyStart;
  if (zone.annotationKind === "swing-high" || zone.annotationKind === "swing-low") {
    return zone.start >= historyStart;
  }
  return Boolean(zone.latest) || zone.end > historyStart;
}

function isSwingReferenceZone(zone: PriceZone) {
  return zone.annotationKind === "swing-high" || zone.annotationKind === "swing-low";
}

function priceZoneLabelPriority(zone: PriceZone) {
  if (zone.currentLevelSide) return 0;
  if (isStructureBreakZone(zone)) return 1;
  if (isSwingReferenceZone(zone)) return 2;
  return 3;
}

function priceZoneLabelGroupPriority(zones: PriceZone[]) {
  return zones.reduce((priority, zone) => Math.min(priority, priceZoneLabelPriority(zone)), 3);
}

function priceZoneLabelTime(zone: PriceZone) {
  return zone.eventTime ?? zone.end ?? zone.start;
}

function isVisibleCoordinate(coordinate: number | null, viewportWidth: number, overscan = 24) {
  return coordinate !== null && Number.isFinite(coordinate) && coordinate >= -overscan && coordinate <= viewportWidth + overscan;
}

function priceZoneLineLabelPlacement(zone: PriceZone): "above" | "below" {
  if (zone.annotationKind === "swing-low" || zone.annotationKind === "liquidity-support") return "below";
  if (isStructureBreakZone(zone) && zone.compactLabel?.endsWith("-")) return "below";
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

function expandCanvasBox(box: CanvasBox, padding: number): CanvasBox {
  return {
    bottom: box.bottom + padding,
    left: box.left - padding,
    right: box.right + padding,
    top: box.top - padding,
  };
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
  if (lineY < 2 || lineY > plotBottom - 2 || span.width < 8) return false;
  const fontSize = Math.max(9, settings.labelFontSize);
  context.save();
  context.font = `600 ${fontSize}px ${canvasInterfaceFont()}`;
  const textWidth = Math.ceil(context.measureText(text).width);
  const labelWidth = textWidth + 8;
  const labelHeight = fontSize + 5;
  if (labelWidth + 4 > span.width) {
    context.restore();
    return false;
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
    context.globalAlpha = 1;
    context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
    context.globalAlpha = settings.opacity;
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.fillText(text, selected.left + 4, selected.top + labelHeight / 2);
    placed.push(selected);
  }
  context.restore();
  return selected !== null;
}

function drawAnchoredSwingLabel(
  context: CanvasRenderingContext2D,
  text: string,
  pivotX: number,
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
  if (!Number.isFinite(pivotX) || lineY < 2 || lineY > plotBottom - 2) return false;
  const fontSize = Math.max(9, settings.labelFontSize);
  context.save();
  context.font = `600 ${fontSize}px ${canvasInterfaceFont()}`;
  const labelWidth = Math.ceil(context.measureText(text).width) + 8;
  const labelHeight = fontSize + 5;
  const leftCandidates = [
    pivotX - labelWidth / 2,
    pivotX + 4,
    pivotX - labelWidth - 4,
  ];
  const direction = placement === "above" ? -1 : 1;
  let selected: CanvasBox | null = null;
  let inBoundsFallback: CanvasBox | null = null;
  for (let lane = 1; lane <= 3 && !selected; lane += 1) {
    const top = direction < 0
      ? lineY - lane * (labelHeight + 3)
      : lineY + 3 + (lane - 1) * (labelHeight + 3);
    for (const left of leftCandidates) {
      const candidate = { bottom: top + labelHeight, left, right: left + labelWidth, top };
      const insidePlot = candidate.left >= 2
        && candidate.right <= layerWidth - 2
        && candidate.top >= 2
        && candidate.bottom <= plotBottom - 2;
      if (!insidePlot) continue;
      inBoundsFallback ??= candidate;
      if (placed.some((item) => boxesOverlap(candidate, item, 3))) continue;
      if (candleBoxes.some((candleBox) => boxesOverlap(candidate, candleBox, 2))) continue;
      selected = candidate;
      break;
    }
  }
  // Every selected-timeframe swing is an audit object. If candles occupy every
  // nearby lane, retain the causal pivot anchor rather than silently dropping
  // the SH/SL label.
  selected ??= inBoundsFallback;
  if (selected) {
    context.fillStyle = chartBackground;
    context.globalAlpha = 1;
    context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
    context.globalAlpha = settings.opacity;
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.fillText(text, selected.left + 4, selected.top + labelHeight / 2);
    placed.push(selected);
  }
  context.restore();
  return selected !== null;
}

function drawAnchoredStructureBreakLabel(
  context: CanvasRenderingContext2D,
  text: string,
  pivotX: number,
  eventX: number,
  lineY: number,
  color: string,
  chartBackground: string,
  fallbackPlacement: "above" | "below",
  settings: ResolvedPriceZoneLegendSettings,
  placed: CanvasBox[],
  candleBoxes: CanvasBox[],
  layerWidth: number,
  plotBottom: number,
  barWidth: number,
) {
  if (!Number.isFinite(pivotX) || !Number.isFinite(eventX) || lineY < 2 || lineY > plotBottom - 2) return false;
  const configuredFontSize = Math.max(9, settings.labelFontSize);
  // Zoom changes typography only. The semantic x anchor remains the exact
  // visual midpoint between the causal pivot and accepted break candle.
  const zoomScale = Math.max(0.62, Math.min(1, barWidth / 8));
  const fontSize = Math.max(7, Math.round(configuredFontSize * zoomScale * 2) / 2);
  context.save();
  context.font = `600 ${fontSize}px ${canvasInterfaceFont()}`;
  const labelWidth = Math.ceil(context.measureText(text).width) + 12;
  const labelHeight = fontSize + 5;
  const anchorX = (pivotX + eventX) / 2;
  const left = anchorX - labelWidth / 2;
  const direction = fallbackPlacement === "above" ? -1 : 1;
  const candidates = [1, 2, 3].map((lane) => (
    direction < 0
      ? lineY - lane * (labelHeight + 3)
      : lineY + 3 + (lane - 1) * (labelHeight + 3)
  ));
  let selected: CanvasBox | null = null;
  for (const top of candidates) {
    const candidate = { bottom: top + labelHeight, left, right: left + labelWidth, top };
    const insidePlot = candidate.left >= 2
      && candidate.right <= layerWidth - 2
      && candidate.top >= 2
      && candidate.bottom <= plotBottom - 2;
    if (!insidePlot || placed.some((item) => boxesOverlap(candidate, item, 3))) continue;
    if (candleBoxes.some((candleBox) => boxesOverlap(candidate, candleBox, 2))) continue;
    selected = candidate;
    break;
  }
  if (selected) {
    context.fillStyle = chartBackground;
    context.globalAlpha = 1;
    context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
    context.globalAlpha = settings.opacity;
    context.fillStyle = color;
    context.textBaseline = "middle";
    context.fillText(text, selected.left + 6, selected.top + labelHeight / 2);
    // A break label has higher semantic priority than a nearby swing tag.
    // Reserve a small visual corridor around it so the later swing-label pass
    // cannot create a stacked CHoCH/SH or BoS/SL cluster.
    placed.push(expandCanvasBox(selected, 6));
  }
  context.restore();
  return selected !== null;
}

function priceZoneCoordinates(chart: IChartApi, zone: PriceZone, candles: Candle[], barWidth: number, candleDuration: number) {
  if (isStructureTimelineZone(zone)) {
    const start = xForStructureEventTime(chart, zone.start, candles, candleDuration);
    const end = xForStructureEventTime(chart, zone.end, candles, candleDuration);
    return start === null || end === null ? null : { end, start };
  }
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

function isStructureTimelineZone(zone: PriceZone) {
  return isStructureBreakZone(zone)
    || zone.annotationKind === "swing-high"
    || zone.annotationKind === "swing-low";
}

function xForStructureEventTime(
  chart: IChartApi,
  time: number,
  candles: Candle[],
  candleDuration: number,
) {
  if (!candles.length || !Number.isFinite(time)) return null;
  const insertionIndex = lowerBoundCandleTime(candles, time);
  if (insertionIndex < candles.length && candles[insertionIndex].time === time) {
    return chart.timeScale().timeToCoordinate(candles[insertionIndex].time as Time);
  }
  const previousIndex = insertionIndex - 1;
  if (
    previousIndex >= 0
    && time >= candles[previousIndex].time
    && time < candles[previousIndex].time + candleDuration
  ) {
    return chart.timeScale().timeToCoordinate(candles[previousIndex].time as Time);
  }
  const nearest = candles[nearestCandleIndex(candles, time)];
  return nearest ? chart.timeScale().timeToCoordinate(nearest.time as Time) : null;
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

function drawTradeAnnotationPrimitiveGeometry(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick">,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  annotations: TradeAnnotation[],
  executions: TradeFillAnnotation[],
  candles: Candle[],
) {
  if (!candles.length || width < 1 || height < 1 || (!annotations.length && !executions.length)) return;
  const chartBackground = validHexColor(readChartPalette().background, "#ffffff");
  context.save();
  context.globalCompositeOperation = "source-over";
  context.lineCap = "round";
  context.lineJoin = "round";
  annotations.forEach((annotation) => {
    const entryX = xForAnnotationTime(chart, annotation.entryTime, candles);
    const exitX = xForAnnotationTime(chart, annotation.exitTime, candles);
    const entryY = priceSeries.priceToCoordinate(annotation.entryPrice);
    const exitY = priceSeries.priceToCoordinate(annotation.exitPrice);
    if (entryX === null || exitX === null || entryY === null || exitY === null) return;
    const span = clippedTradeSpan(entryX, exitX, width);
    if (!span) return;
    const entryColor = validHexColor(annotation.entryColor, "#16a34a");
    const exitColor = validHexColor(annotation.exitColor, "#dc2626");
    const lineWidth = annotation.selected ? 3 : 2;
    drawCanvasTradeLine(context, span.left, span.right, entryY, entryColor, lineWidth);
    drawCanvasTradeLine(context, span.left, span.right, exitY, exitColor, lineWidth);
    drawCanvasTradeArrow(context, entryX, entryY, entryColor, "entry", annotation.selected === true);
    drawCanvasTradeArrow(context, exitX, exitY, exitColor, "exit", annotation.selected === true);
    drawCanvasTradeLabel(
      context,
      compactTradeLabel(annotation.entryLabelParts, annotation.entryLabel, "Entry"),
      entryX,
      entryY + 14,
      entryColor,
      chartBackground,
      annotation.entryLabelSide ?? "left",
      width,
      height,
    );
    drawCanvasTradeLabel(
      context,
      compactTradeLabel(annotation.exitLabelParts, annotation.exitLabel, "Exit"),
      exitX,
      exitY - 25,
      exitColor,
      chartBackground,
      annotation.exitLabelSide ?? "right",
      width,
      height,
    );
    annotation.fills?.forEach((fill) => {
      const x = xForAnnotationTime(chart, fill.time, candles);
      const y = priceSeries.priceToCoordinate(fill.price);
      if (x === null || y === null || x < -70 || x > width + 70) return;
      drawCanvasPositionAdjustment(context, x, y, fill, chartBackground, width, height);
    });
    if (typeof annotation.stopPrice === "number" && Number.isFinite(annotation.stopPrice)) {
      const y = priceSeries.priceToCoordinate(annotation.stopPrice);
      if (y !== null) drawCanvasTradeGuide(context, span.left, span.right, y, "#dc2626", "Stop", chartBackground, width, height);
    }
    if (typeof annotation.triggerPrice === "number" && Number.isFinite(annotation.triggerPrice)) {
      const y = priceSeries.priceToCoordinate(annotation.triggerPrice);
      if (y !== null) drawCanvasTradeGuide(context, span.left, span.right, y, "#2563eb", "Trigger", chartBackground, width, height);
    }
  });
  executions.forEach((fill) => {
    const x = xForAnnotationTime(chart, fill.time, candles);
    const y = priceSeries.priceToCoordinate(fill.price);
    if (x === null || y === null || x < -20 || x > width + 20) return;
    const color = fill.side === "BUY" ? "#16a34a" : "#dc2626";
    drawCanvasTradeArrow(context, x, y, color, fill.side === "BUY" ? "entry" : "exit", false, 5);
  });
  context.restore();
}

function clippedTradeSpan(start: number, end: number, viewportWidth: number): HorizontalSpan | null {
  const span = clippedHorizontalSpan(start, end, viewportWidth);
  if (span) return span;
  if (!Number.isFinite(start) || !Number.isFinite(end) || !(viewportWidth > 0)) return null;
  const anchor = (start + end) / 2;
  if (anchor < -24 || anchor > viewportWidth + 24) return null;
  // A complete position can open and close inside one candle or even one
  // rendered pixel. Keep its exact event-time arrows and give the lifecycle
  // line a small visible span instead of dropping the whole annotation.
  const left = Math.max(-24, anchor - 3);
  const right = Math.min(viewportWidth + 24, anchor + 3);
  return { left, right, width: right - left };
}

function drawCanvasTradeLine(
  context: CanvasRenderingContext2D,
  left: number,
  right: number,
  y: number,
  color: string,
  width: number,
) {
  context.beginPath();
  context.strokeStyle = rgbaFromHex(color, 0.88);
  context.lineWidth = width;
  context.moveTo(left, y);
  context.lineTo(right, y);
  context.stroke();
}

function drawCanvasTradeArrow(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  color: string,
  kind: "entry" | "exit",
  selected: boolean,
  radius = 7,
) {
  // The triangle tip is the exact event-time / execution-price coordinate.
  // Everything else extends away from the candle so the semantic anchor never
  // changes when the chart is panned, scaled, or rendered at another interval.
  const direction = kind === "entry" ? 1 : -1;
  const size = selected ? radius + 2 : radius;
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x - size, y + direction * (size + 4));
  context.lineTo(x + size, y + direction * (size + 4));
  context.closePath();
  context.fillStyle = color;
  context.fill();
}

function drawCanvasPositionAdjustment(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  fill: TradeFillAnnotation,
  background: string,
  width: number,
  height: number,
) {
  const color = fill.side === "BUY" ? "#16a34a" : "#dc2626";
  context.beginPath();
  context.strokeStyle = rgbaFromHex(color, 0.86);
  context.lineWidth = 1.5;
  context.moveTo(x - 42, y);
  context.lineTo(x, y);
  context.stroke();
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x - 7, y - 4);
  context.lineTo(x - 7, y + 4);
  context.closePath();
  context.fillStyle = color;
  context.fill();
  drawCanvasTradeLabel(
    context,
    compactTradeLabel(fill.labelParts, fill.label, {
      add: "Add",
      profit_target: "Target",
      protective_stop: "Stop",
      trailing_stop: "Trail",
      position_exit: "Exit",
    }[fill.kind ?? "position_exit"]),
    x - 45,
    y - 8,
    color,
    background,
    "right",
    width,
    height,
  );
}

function drawCanvasTradeGuide(
  context: CanvasRenderingContext2D,
  left: number,
  right: number,
  y: number,
  color: string,
  label: string,
  background: string,
  width: number,
  height: number,
) {
  context.save();
  context.setLineDash([4, 3]);
  drawCanvasTradeLine(context, left, right, y, color, 1);
  context.restore();
  drawCanvasTradeLabel(context, label, left, y + 3, color, background, "left", width, height);
}

function drawCanvasTradeLabel(
  context: CanvasRenderingContext2D,
  text: string,
  anchorX: number,
  top: number,
  color: string,
  background: string,
  side: "left" | "right",
  width: number,
  height: number,
) {
  if (!text) return;
  context.font = `600 10px ${canvasInterfaceFont()}`;
  context.textBaseline = "middle";
  const labelWidth = Math.ceil(context.measureText(text).width) + 10;
  const labelHeight = 17;
  const preferredLeft = side === "right" ? anchorX - labelWidth : anchorX;
  const left = Math.max(3, Math.min(preferredLeft, width - labelWidth - 3));
  const clampedTop = Math.max(3, Math.min(top, height - labelHeight - 3));
  context.fillStyle = rgbaFromHex(background, 0.92);
  context.fillRect(left, clampedTop, labelWidth, labelHeight);
  context.strokeStyle = rgbaFromHex(color, 0.36);
  context.lineWidth = 1;
  context.strokeRect(left + 0.5, clampedTop + 0.5, labelWidth - 1, labelHeight - 1);
  context.fillStyle = color;
  context.fillText(text, left + 5, clampedTop + labelHeight / 2);
}

function compactTradeLabel(parts: TradeLabelPart[] | undefined, fallback: string | undefined, defaultLabel: string) {
  const fromParts = parts?.map((part) => part.text.trim()).filter(Boolean).join(" ");
  return fromParts || fallback || defaultLabel;
}

function xForAnnotationTime(chart: IChartApi, time: number, candles: Candle[]) {
  const exact = chart.timeScale().timeToCoordinate(time as Time);
  if (exact !== null) return exact;
  const rightIndex = lowerBoundCandleTime(candles, time);
  const leftIndex = rightIndex - 1;
  if (leftIndex >= 0 && rightIndex < candles.length) {
    const leftCandle = candles[leftIndex];
    const rightCandle = candles[rightIndex];
    const leftX = chart.timeScale().timeToCoordinate(leftCandle.time as Time);
    const rightX = chart.timeScale().timeToCoordinate(rightCandle.time as Time);
    const duration = rightCandle.time - leftCandle.time;
    if (leftX !== null && rightX !== null && duration > 0) {
      const ratio = clampNumber((time - leftCandle.time) / duration, 0, 1, 0);
      return leftX + (rightX - leftX) * ratio;
    }
  }
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
  if (timeframe === "1mo" || timeframe === "1y") return marketMonthlyAxisFormatter.format(timestamp);
  if (timeframe === "1d" || timeframe === "1w") return marketDailyAxisFormatter.format(timestamp);
  const seconds = chartTimeframeSeconds(timeframe);
  if (seconds !== null && seconds < 1) return marketSubsecondAxisFormatter.format(timestamp);
  if (seconds !== null && seconds < 60) return marketSecondAxisFormatter.format(timestamp);
  return marketAxisFormatter.format(timestamp);
}

function formatMarketDateTime(timeValue: Time, timeframe = "1m") {
  const timestamp = new Date(timestampFromChartTime(timeValue) * 1000);
  if (isMacroTimeframe(timeframe)) return marketMacroDateTimeFormatter.format(timestamp);
  const seconds = chartTimeframeSeconds(timeframe);
  if (seconds !== null && seconds < 1) return marketSubsecondDateTimeFormatter.format(timestamp);
  if (seconds !== null && seconds < 60) return marketSecondDateTimeFormatter.format(timestamp);
  return marketDateTimeFormatter.format(timestamp);
}

function formatTimeframeLabel(timeframe: string) {
  if (timeframe === "1d") return "1D";
  if (timeframe === "1w") return "1W";
  if (timeframe === "1mo") return "1M";
  if (timeframe === "1y") return "1Y";
  return timeframe;
}

function formatPrice(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4 }).format(value);
}
