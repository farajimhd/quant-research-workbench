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
  ArrowLeft,
  CalendarDays,
  CalendarRange,
  ChartNoAxesCombined,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Eye,
  EyeOff,
  Layers3,
  Maximize2,
  Minimize2,
  Paintbrush,
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
type TradeLabelPart = { text: string; tone?: "exitLong" | "exitPriceLong" | "exitPriceShort" | "exitShort" | "label" | "long" | "pnlLoss" | "pnlWin" | "price" | "priceLong" | "priceShort" | "reason" | "separator" | "short" | "size" };
type TradeLabelPartSettings = Partial<Record<NonNullable<TradeLabelPart["tone"]>, StrategyPresentationStyleSettings>>;
type TradeFillAnnotation = {
  kind?: "add" | "profit_target" | "protective_stop" | "trailing_stop" | "position_exit" | "stop_change" | "target_change" | "protection_repair" | "entry_freeze";
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
  exitLabelColor?: string;
  exitLabelParts?: TradeLabelPart[];
  exitLabelSide?: "left" | "right";
  exitColor?: string;
  endTime?: number;
  exitPrice?: number;
  exitTime?: number;
  fills?: TradeFillAnnotation[];
  guideStartTime?: number;
  id: string;
  levelPrices?: number[];
  pnl?: number;
  selected?: boolean;
  status?: "open" | "closed";
  stopPrice?: number;
  targetPrices?: number[];
  triggerPrice?: number;
};
type StrategyPresentationStyleSettings = {
  borderColor: string;
  borderOpacity: number;
  borderStyle: LegendLineStyle;
  borderWidth: number;
  color: string;
  fillColor: string;
  fillBlur: number;
  fillOpacity: number;
  fontWeight: 400 | 500 | 600;
  labelPaddingX: number;
  labelPaddingY: number;
  labelSize: number;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  markerSize: number;
  opacity: number;
  visible: boolean;
};
type StrategyVisualElementKey =
  | "entryLine" | "entryArrow" | "entryLabel"
  | "entryDirectionPart" | "entryShortDirectionPart" | "entrySizePart" | "entrySeparatorPart" | "entryPricePart" | "entryShortPricePart"
  | "exitLine" | "exitArrow" | "exitLabel"
  | "exitReasonPart" | "exitShortReasonPart" | "exitSizePart" | "exitSeparatorPart" | "exitPricePart" | "exitShortPricePart" | "exitPnlPart" | "exitPnlLossPart"
  | "levelLine" | "levelLabel"
  | "stopLine" | "stopLabel"
  | "targetLine" | "targetLabel"
  | "adjustmentLine" | "adjustmentArrow" | "adjustmentLabel"
  | "connector";
type StrategyPresentationSettings = {
  avoidLabelCollisions: boolean;
  connectorThreshold: number;
  elements: Record<StrategyVisualElementKey, StrategyPresentationStyleSettings>;
  visible: boolean;
};
type StrategyPresentationSettingsUpdate = StrategyPresentationSettings | ((current: StrategyPresentationSettings) => StrategyPresentationSettings);
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
  minimumHoldProbability?: number;
  minimumPressureMagnitude?: number;
  opacity?: number;
  preset?: ChartPreset;
  showConnectors?: boolean;
  showAxisLabel?: boolean;
  showHistoricalLabels?: boolean;
  showLabels?: boolean;
  showUnifiedActive?: boolean;
  showUnifiedBroken?: boolean;
  showUnifiedHoldProbability?: boolean;
  showUnifiedResistance?: boolean;
  showUnifiedRoleFlipped?: boolean;
  showUnifiedSupport?: boolean;
  showValue?: boolean;
  upColor?: string;
  visible?: boolean;
};
type LegendSettingsMap = Record<string, LegendSeriesSettings>;

type PriceZonePrimitiveState = {
  appearanceSettings: ChartAppearanceSettings | null;
  candles: Candle[];
  legendSettings: LegendSettingsMap;
  regions: Region[];
  timeline: CandleSeriesDatum[];
  zones: PriceZone[];
};

type TradeAnnotationPrimitiveState = {
  candles: Candle[];
  executions: TradeFillAnnotation[];
  settings: StrategyPresentationSettings;
  timeline: Array<{ time: number }>;
  trades: TradeAnnotation[];
};

class PriceZonePrimitive implements ISeriesPrimitive<Time> {
  private chart: IChartApi | null = null;
  private requestUpdate: (() => void) | null = null;
  private series: ISeriesApi<"Candlestick"> | null = null;
  private state: PriceZonePrimitiveState = { appearanceSettings: null, candles: [], legendSettings: {}, regions: [], timeline: [], zones: [] };
  private readonly rendererImpl: IPrimitivePaneRenderer = {
    draw: (target) => {
      if (!this.chart || !this.series) return;
      target.useMediaCoordinateSpace(({ context, mediaSize }) => {
        if (this.state.appearanceSettings) drawSessionRegionPrimitiveGeometry(
          this.chart as IChartApi,
          context,
          mediaSize.width,
          mediaSize.height,
          this.state.regions,
          this.state.timeline,
          this.state.candles,
          this.state.appearanceSettings,
        );
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
  private state: TradeAnnotationPrimitiveState = { candles: [], executions: [], settings: defaultStrategyPresentationSettings, timeline: [], trades: [] };
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
          this.state.timeline,
          this.state.settings,
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

  autoscaleInfo(startLogical: number, endLogical: number): AutoscaleInfo | null {
    return tradeAnnotationAutoscaleInfo(this.state, startLogical, endLogical);
  }

  setState(state: TradeAnnotationPrimitiveState) {
    this.state = state;
    this.requestUpdate?.();
  }

  setSettings(settings: StrategyPresentationSettings) {
    this.state = { ...this.state, settings };
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

export type ChartTimelineEvent = {
  ariaLabel: string;
  id: string;
  kind: "split";
  label: string;
  time: number;
  title: string;
};

export type ChartPayload = {
  candles: Candle[];
  forecast_candles?: Candle[];
  volume: Array<{ time: number; value: number; color: string }>;
  overlay_series: ChartSeries[];
  oscillator_series: ChartSeries[];
  markers: ChartMarker[];
  regions: Region[];
  timeline_events?: ChartTimelineEvent[];
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
  dataStatus?: string;
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
  onShowSplitEventsChange?: (value: boolean) => void;
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
  showSplitEvents?: boolean;
  showIndicatorControls?: boolean;
  showSupervisionControls?: boolean;
  strategyPresentationEnabled?: boolean;
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
const STRATEGY_PRESENTATION_STORAGE_KEY = "quant-research-workbench.chart.strategy-presentation.v1";
const CHART_PRICE_SCALE_MIN_WIDTH = 84;

const strategyPresentationStyle = (
  color: string,
  lineStyle: LegendLineStyle,
  lineWidth: number,
  opacity: number,
  labelSize = 10,
  markerSize = 7,
  fillOpacity = 0.92,
): StrategyPresentationStyleSettings => ({
  borderColor: "",
  borderOpacity: 0.72,
  borderStyle: lineStyle,
  borderWidth: 1,
  color,
  fillColor: "",
  fillBlur: 0,
  fillOpacity,
  fontWeight: 600,
  labelPaddingX: 5,
  labelPaddingY: 4,
  labelSize,
  lineStyle,
  lineWidth,
  markerSize,
  opacity,
  visible: true,
});

const defaultStrategyPresentationSettings: StrategyPresentationSettings = {
  avoidLabelCollisions: true,
  connectorThreshold: 18,
  elements: {
    entryLine: strategyPresentationStyle("#3596FD", "solid", 2, 0.95),
    entryArrow: strategyPresentationStyle("", "solid", 2, 1, 10, 5, 1),
    entryLabel: { ...strategyPresentationStyle("#64748B", "solid", 1, 1, 10, 7, 0), borderColor: "#64748B", borderOpacity: 0.7, labelPaddingX: 5, labelPaddingY: 2 },
    entryDirectionPart: { ...strategyPresentationStyle("#007DFF", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#007DFF", labelPaddingX: 5, labelPaddingY: 2 },
    entryShortDirectionPart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#FF1744", labelPaddingX: 5, labelPaddingY: 2 },
    entrySizePart: { ...strategyPresentationStyle("", "solid", 1, 1, 12, 7, 0), fontWeight: 600, labelPaddingX: 4, labelPaddingY: 2 },
    entrySeparatorPart: { ...strategyPresentationStyle("", "solid", 1, 1, 10, 7, 1), borderOpacity: 0.5, labelPaddingX: 2, labelPaddingY: 2 },
    entryPricePart: { ...strategyPresentationStyle("#007DFF", "solid", 1, 1, 10, 7, 1), fillColor: "#FFFFFF", labelPaddingX: 4, labelPaddingY: 2 },
    entryShortPricePart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 1), fillColor: "#FFFFFF", labelPaddingX: 4, labelPaddingY: 2 },
    exitLine: strategyPresentationStyle("#FF3D47", "solid", 2, 0.9),
    exitArrow: strategyPresentationStyle("#FF4D55", "solid", 2, 1, 10, 5, 1),
    exitLabel: { ...strategyPresentationStyle("#64748B", "solid", 1, 1, 10, 7, 0), borderColor: "#64748B", borderOpacity: 0.7, labelPaddingX: 8, labelPaddingY: 2 },
    exitReasonPart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#FF1744", labelPaddingX: 6, labelPaddingY: 2 },
    exitShortReasonPart: { ...strategyPresentationStyle("#00A846", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#00A846", labelPaddingX: 6, labelPaddingY: 2 },
    exitSizePart: { ...strategyPresentationStyle("", "solid", 1, 1, 12, 7, 0), fontWeight: 600, labelPaddingX: 4, labelPaddingY: 2 },
    exitSeparatorPart: { ...strategyPresentationStyle("", "solid", 1, 0.9, 10, 7, 1), borderOpacity: 0.5, labelPaddingX: 2, labelPaddingY: 2 },
    exitPricePart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 1), fillColor: "#FFFFFF", labelPaddingX: 4, labelPaddingY: 2 },
    exitShortPricePart: { ...strategyPresentationStyle("#00A846", "solid", 1, 1, 10, 7, 1), fillColor: "#FFFFFF", labelPaddingX: 4, labelPaddingY: 2 },
    exitPnlPart: { ...strategyPresentationStyle("#00A846", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#00A846", fontWeight: 600, labelPaddingX: 5, labelPaddingY: 2 },
    exitPnlLossPart: { ...strategyPresentationStyle("#FF1744", "solid", 1, 1, 10, 7, 0.18), fillBlur: 2, fillColor: "#FF1744", fontWeight: 600, labelPaddingX: 5, labelPaddingY: 2 },
    levelLine: strategyPresentationStyle("", "dashed", 1, 0.9),
    levelLabel: { ...strategyPresentationStyle("", "solid", 1, 1, 8, 7, 1), borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 },
    stopLine: strategyPresentationStyle("", "dashed", 1, 0.95),
    stopLabel: { ...strategyPresentationStyle("", "dashed", 2, 1, 8, 7, 1), borderOpacity: 0.49, borderStyle: "solid", borderWidth: 0, labelPaddingX: 2, labelPaddingY: 2 },
    targetLine: strategyPresentationStyle("#008539", "dashed", 1, 1),
    targetLabel: { ...strategyPresentationStyle("", "dashed", 2, 1, 8, 7, 1), borderOpacity: 1, borderStyle: "solid", borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 },
    adjustmentLine: strategyPresentationStyle("#986B9E", "solid", 1, 0.92),
    adjustmentArrow: { ...strategyPresentationStyle("#8D6E96", "solid", 2, 1, 10, 4, 1), borderWidth: 0 },
    adjustmentLabel: { ...strategyPresentationStyle("#8C6E96", "solid", 2, 1, 8, 7, 0.92), borderWidth: 0, labelPaddingX: 2, labelPaddingY: 1 },
    connector: strategyPresentationStyle("", "dashed", 1, 0.7),
  },
  visible: true,
};

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
  dataStatus,
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
  onShowSplitEventsChange,
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
  showSplitEvents = false,
  showIndicatorControls = true,
  showSupervisionControls = false,
  strategyPresentationEnabled = false,
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
  const timelineEventLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const forecastCandleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
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
  const strategyPresentationSettingsRef = useRef<StrategyPresentationSettings>(defaultStrategyPresentationSettings);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const paneResizeObserverRef = useRef<ResizeObserver | null>(null);
  const initialFitTimerRef = useRef<number | null>(null);
  const overlayInteractionCleanupRef = useRef<(() => void) | null>(null);
  const crosshairInputCleanupRef = useRef<(() => void) | null>(null);
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
  const tradeAutoscaleViewportRef = useRef("");
  const userViewportClaimedRef = useRef(false);
  const candleWindowRef = useRef<{ first: number; last: number } | null>(null);
  const candleBoundsRef = useRef<NumericBounds>(null);
  const normalizeTickerValue = (value: string) => (normalizeTicker ? value.toUpperCase() : value);
  const [draftTicker, setDraftTicker] = useState(normalizeTickerValue(ticker));
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const [supervisionMenuOpen, setSupervisionMenuOpen] = useState(false);
  const [strategyPresentationOpen, setStrategyPresentationOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const [chartSettingsAnchor, setChartSettingsAnchor] = useState<HTMLButtonElement | null>(null);
  const legendStorageKey = settingsStorageKey ? `${settingsStorageKey}.legend` : LEGEND_SETTINGS_STORAGE_KEY;
  const oscillatorThresholdStorageKey = settingsStorageKey ? `${settingsStorageKey}.oscillator-thresholds` : OSCILLATOR_THRESHOLD_STORAGE_KEY;
  const appearanceStorageKey = settingsStorageKey ? `${settingsStorageKey}.appearance` : CHART_APPEARANCE_STORAGE_KEY;
  const strategyPresentationStorageKey = settingsStorageKey ? `${settingsStorageKey}.strategy-presentation` : STRATEGY_PRESENTATION_STORAGE_KEY;
  const paneLayoutStorageKey = settingsStorageKey ? `${settingsStorageKey}.pane-layout-v2` : `${LEGEND_SETTINGS_STORAGE_KEY}.pane-layout-v2`;
  const instanceAppearanceDefaults = normalizeChartAppearanceSettings({ ...defaultChartAppearanceSettings, ...appearanceDefaults });
  const [chartSettings, setChartSettings] = useState<ChartAppearanceSettings>(() => loadChartAppearanceSettings(appearanceStorageKey, instanceAppearanceDefaults));
  const [legendSettings, setLegendSettings] = useState<LegendSettingsMap>(() => loadLegendSettings(legendStorageKey));
  const [oscillatorThresholdSettings, setOscillatorThresholdSettings] = useState<OscillatorThresholdSettingsMap>(() => loadOscillatorThresholdSettings(oscillatorThresholdStorageKey));
  const [strategyPresentationSettings, setStrategyPresentationSettings] = useState<StrategyPresentationSettings>(() => loadStrategyPresentationSettings(strategyPresentationStorageKey));
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
  strategyPresentationSettingsRef.current = strategyPresentationSettings;
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
  const liveEntryLineKey = liveEntryLine ? `${liveEntryLine.price}:${liveEntryLine.quantity}:${liveEntryLine.pnl}:${liveEntryLine.color}:${JSON.stringify(liveEntryLine.labelParts ?? [])}` : "";
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

  const updateStrategyPresentationSettings = (update: StrategyPresentationSettingsUpdate) => {
    setStrategyPresentationSettings((current) => {
      const next = typeof update === "function" ? update(current) : update;
      const normalized = normalizeStrategyPresentationSettings(next);
      saveStrategyPresentationSettings(normalized, strategyPresentationStorageKey);
      return normalized;
    });
  };

  const resetStrategyPresentationSettings = () => {
    saveStrategyPresentationSettings(defaultStrategyPresentationSettings, strategyPresentationStorageKey);
    setStrategyPresentationSettings(defaultStrategyPresentationSettings);
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
    fitTradeAnnotationPriceScale();
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
    if (!columnMenuOpen && !supervisionMenuOpen && !periodMenuOpen && !strategyPresentationOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".chart-column-select") || target?.closest(".chart-column-menu-portal") || target?.closest(".chart-period-select")) return;
      setColumnMenuOpen(false);
      setSupervisionMenuOpen(false);
      setPeriodMenuOpen(false);
      setStrategyPresentationOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setColumnMenuOpen(false);
        setSupervisionMenuOpen(false);
        setPeriodMenuOpen(false);
        setStrategyPresentationOpen(false);
      }
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnMenuOpen, supervisionMenuOpen, periodMenuOpen, strategyPresentationOpen]);

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
    // Presentation edits own only primitive paint state. They must not replace
    // chart data, refit the time range, or re-enable price autoscaling.
    tradeAnnotationPrimitiveRef.current?.setSettings(strategyPresentationSettings);
  }, [strategyPresentationSettings]);

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
    forecastCandleRef.current = priceChart.addSeries(CandlestickSeries, {
      lastValueVisible: false,
      priceLineVisible: false,
      wickVisible: true,
    });
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
    crosshairInputCleanupRef.current = attachZoomNormalizedCrosshairInput(priceRef.current);
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
      tradeAutoscaleViewportRef.current = "";
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
    const timeline = chartTimelineData(payload.candles, timeframe, chartSettingsRef.current.hideEmptyIntervals, payload.timeline_events);
    const shouldFitTradeEvidence = Boolean(payload.trade_annotations?.length)
      && tradeAutoscaleViewportRef.current !== viewportIdentity;
    candleBoundsRef.current = candleValueBounds(payload.candles);
    // Trade guides participate in the candle series autoscale. Seed the
    // primitive before setData/fit operations so off-candle SL/TP prices are
    // included in the first visible price range rather than attached after it.
    syncTradeAnnotationPrimitive(payload, timeline);
    syncRendererData(candleRef.current, timeline as unknown as RendererDatum[], `candles:${timeframe}`);
    if (forecastCandleRef.current) {
      syncRendererData(
        forecastCandleRef.current,
        [...(payload.forecast_candles ?? [])].sort((left, right) => left.time - right.time) as unknown as RendererDatum[],
        `forecast-candles:${timeframe}`,
      );
    }
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
        if (shouldFitTradeEvidence) {
          tradeAutoscaleViewportRef.current = viewportIdentity;
          fitTradeAnnotationPriceScale();
        }
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
      if (shouldFitTradeEvidence) {
        tradeAutoscaleViewportRef.current = viewportIdentity;
        fitTradeAnnotationPriceScale();
      }
    }
  }, [deferInitialFitUntilLoaded, effectiveChartSettings.hideEmptyIntervals, initialFitMode, loading, payload, reference, referenceKey, ticker, timeframe]);

  useEffect(() => {
    if (!priceChartRef.current || !payload?.candles.length || !reference) return;
    suppressEarlierLoad();
    fitAroundReference(priceChartRef.current, payload.candles, reference, timeframe, chartSettingsRef.current.hideEmptyIntervals);
    drawCurrentRegions();
    fitTradeAnnotationPriceScale();
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
      appearanceSettings: chartSettingsRef.current,
      candles: currentPayload.candles,
      legendSettings: legendSettingsRef.current,
      regions: currentPayload.regions,
      timeline,
      zones: selectedZones,
    });
    syncTradeAnnotationPrimitive(currentPayload, timeline);
    syncPriceZoneAxisLines(candleRef.current, selectedZones, legendSettingsRef.current, priceZoneAxisLinesRef.current);
    drawRegions(chart, candleRef.current, priceLayerRef.current, currentPayload.candles, liveEntryLineRef.current);
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
    drawTimelineEvents(chart, timelineEventLayerRef.current, currentPayload.timeline_events ?? []);
  }

  function syncTradeAnnotationPrimitive(currentPayload: ChartPayload, timeline: Array<{ time: number }>) {
    tradeAnnotationPrimitiveRef.current?.setState({
      candles: currentPayload.candles,
      executions: currentPayload.execution_annotations ?? [],
      settings: strategyPresentationSettingsRef.current,
      // Autoscale logical indexes belong to the rendered series timeline;
      // raw candles omit explicit whitespace bars and are not index-compatible.
      timeline,
      trades: currentPayload.trade_annotations ?? [],
    });
  }

  function fitTradeAnnotationPriceScale() {
    if (!payloadRef.current?.trade_annotations?.length) return;
    candleRef.current?.priceScale().applyOptions({ autoScale: true });
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
    crosshairInputCleanupRef.current?.();
    crosshairInputCleanupRef.current = null;
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
    forecastCandleRef.current = null;
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
        {dataStatus ? <span className="chart-data-status" title="Historical prices and share quantities use the recorded stock-split basis.">{dataStatus}</span> : null}
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
                    setStrategyPresentationOpen(false);
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
                    setStrategyPresentationOpen(false);
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
        {strategyPresentationEnabled ? (
          <StrategyPresentationSelect
            annotationCount={payload?.trade_annotations?.length ?? 0}
            onChange={updateStrategyPresentationSettings}
            onOpenChange={(value) => {
              setStrategyPresentationOpen(value);
              if (value) {
                setColumnMenuOpen(false);
                setSupervisionMenuOpen(false);
                setChartSettingsOpen(false);
                setPeriodMenuOpen(false);
              }
            }}
            onReset={resetStrategyPresentationSettings}
            open={strategyPresentationOpen}
            settings={strategyPresentationSettings}
          />
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
            setStrategyPresentationOpen(false);
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
          onReset={() => {
            resetChartSettings();
            onShowSplitEventsChange?.(timeframe === "1d");
          }}
          onShowSplitEventsChange={onShowSplitEventsChange}
          showSplitEvents={showSplitEvents}
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
            <div className="chart-timeline-event-layer" ref={timelineEventLayerRef}>
              {(payload?.timeline_events ?? []).map((event) => (
                <span
                  aria-label={event.ariaLabel}
                  className="chart-timeline-event"
                  data-chart-timeline-event-id={event.id}
                  data-kind={event.kind}
                  key={event.id}
                  role="img"
                  tabIndex={0}
                  title={event.title}
                >
                  {event.label}
                </span>
              ))}
            </div>
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

function attachZoomNormalizedCrosshairInput(target: HTMLElement | null) {
  if (!target) return () => undefined;
  const normalizedEvents = new WeakSet<MouseEvent>();
  const normalizeCrosshairMove = (event: MouseEvent) => {
    if (normalizedEvents.has(event) || event.buttons !== 0) return;
    const eventTarget = event.target;
    if (!(eventTarget instanceof HTMLElement)) return;
    const bounds = eventTarget.getBoundingClientRect();
    const scaleX = eventTarget.offsetWidth > 0 ? bounds.width / eventTarget.offsetWidth : 1;
    const scaleY = eventTarget.offsetHeight > 0 ? bounds.height / eventTarget.offsetHeight : 1;
    if (!Number.isFinite(scaleX) || !Number.isFinite(scaleY) || scaleX <= 0 || scaleY <= 0) return;
    if (Math.abs(scaleX - 1) < 0.001 && Math.abs(scaleY - 1) < 0.001) return;

    // Lightweight Charts subtracts the post-zoom DOM origin from pointer
    // coordinates, then consumes that distance in its pre-zoom chart space.
    // Re-dispatch one normalized move so the library's native crosshair,
    // labels, panes, and series hit-testing all share the real cursor point.
    event.stopImmediatePropagation();
    const normalized = new MouseEvent("mousemove", {
      altKey: event.altKey,
      bubbles: true,
      button: event.button,
      buttons: event.buttons,
      cancelable: event.cancelable,
      clientX: bounds.left + (event.clientX - bounds.left) / scaleX,
      clientY: bounds.top + (event.clientY - bounds.top) / scaleY,
      ctrlKey: event.ctrlKey,
      metaKey: event.metaKey,
      screenX: event.screenX,
      screenY: event.screenY,
      shiftKey: event.shiftKey,
      view: window,
    });
    normalizedEvents.add(normalized);
    eventTarget.dispatchEvent(normalized);
  };
  target.addEventListener("mousemove", normalizeCrosshairMove, { capture: true });
  return () => target.removeEventListener("mousemove", normalizeCrosshairMove, { capture: true });
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
  minimumHoldProbability?: number;
  minimumPressureMagnitude?: number;
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
  showUnifiedHoldProbability?: boolean;
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
          <legend>Observed lifecycle filters</legend>
          <ScoreThresholdControl label="Minimum hold" value={item.minimumHoldProbability ?? 0} onChange={(minimumHoldProbability) => onUpdate({ minimumHoldProbability })} />
          <ScoreThresholdControl label="Pressure magnitude" value={item.minimumPressureMagnitude ?? 0} onChange={(minimumPressureMagnitude) => onUpdate({ minimumPressureMagnitude })} />
          <ScoreThresholdControl label="Maximum break" value={item.maximumBreakProbability ?? 1} onChange={(maximumBreakProbability) => onUpdate({ maximumBreakProbability })} />
          <small>Filters use recorded holds, accepted breaks, and executed pressure. Changes apply immediately to loaded chart data.</small>
          <span className="legend-filter-subtitle">Visible roles and states</span>
          <span className="legend-filter-grid">
            <UnifiedVisibilityToggle checked={item.showUnifiedSupport !== false} label="Support" onChange={(showUnifiedSupport) => onUpdate({ showUnifiedSupport })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedResistance !== false} label="Resistance" onChange={(showUnifiedResistance) => onUpdate({ showUnifiedResistance })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedActive !== false} label="Active" onChange={(showUnifiedActive) => onUpdate({ showUnifiedActive })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedBroken !== false} label="Broken" onChange={(showUnifiedBroken) => onUpdate({ showUnifiedBroken })} />
            <UnifiedVisibilityToggle checked={item.showUnifiedRoleFlipped !== false} label="Role-flipped" onChange={(showUnifiedRoleFlipped) => onUpdate({ showUnifiedRoleFlipped })} />
          </span>
          <span className="legend-filter-subtitle">Chart labels</span>
          <span className="legend-filter-grid">
            <UnifiedVisibilityToggle checked={item.showUnifiedHoldProbability !== false} label="Hold probability" onChange={(showUnifiedHoldProbability) => onUpdate({ showUnifiedHoldProbability })} />
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

function StrategyPresentationSelect({
  annotationCount,
  onChange,
  onOpenChange,
  onReset,
  open,
  settings,
}: {
  annotationCount: number;
  onChange: (settings: StrategyPresentationSettingsUpdate) => void;
  onOpenChange: (value: boolean) => void;
  onReset: () => void;
  open: boolean;
  settings: StrategyPresentationSettings;
}) {
  const triggerRef = useRef<HTMLButtonElement | null>(null);
  const [styleElement, setStyleElement] = useState<StrategyVisualElementKey | null>(null);
  const enabledCount = strategyVisualElementDefinitions.filter((item) => settings.elements[item.key].visible).length;
  const updateElement = (key: StrategyVisualElementKey, patch: Partial<StrategyPresentationStyleSettings>) => onChange((current) => ({
    ...current,
    elements: { ...current.elements, [key]: { ...current.elements[key], ...patch } },
  }));
  const palette = readChartPalette();
  const selectedDefinition = strategyVisualElementDefinitions.find((item) => item.key === styleElement) ?? null;

  useEffect(() => {
    if (!open) setStyleElement(null);
  }, [open]);

  return <div className="chart-column-select chart-strategy-presentation-select">
    <button
      aria-expanded={open}
      className="chart-column-select-button"
      onClick={() => onOpenChange(!open)}
      ref={triggerRef}
      title="Strategy Presentation"
      type="button"
    >
      <Layers3 size={18} />
      <span>Strategy Presentation</span>
      <b>{settings.visible ? enabledCount : 0}</b>
      <ChevronDown size={14} />
    </button>
    {open ? <ChartColumnMenuPortal anchor={triggerRef.current} className="strategy-presentation-menu">
      {selectedDefinition && styleElement ? <StrategyPresentationStylePage
        definition={selectedDefinition}
        elements={settings.elements}
        fallbackColor={strategyVisualElementFallbackColor(styleElement, palette.text)}
        onBack={() => setStyleElement(null)}
        onChange={(patch) => updateElement(styleElement, patch)}
        onElementChange={updateElement}
        onReset={() => {
          const partDefinitions = strategyLabelPartDefinitions[styleElement as StrategyCompositeLabelKey];
          const keys = partDefinitions ? [styleElement, ...partDefinitions.map((part) => part.key)] : [styleElement];
          onChange((current) => ({
            ...current,
            elements: keys.reduce((elements, key) => ({ ...elements, [key]: defaultStrategyPresentationSettings.elements[key] }), current.elements),
          }));
        }}
        settings={settings.elements[styleElement]}
      /> : <>
        <div className="strategy-presentation-header">
          <div>
            <strong>Strategy Presentation</strong>
            <span>Canonical position evidence · {annotationCount} lifecycle{annotationCount === 1 ? "" : "s"}</span>
          </div>
          <button className="button secondary compact" onClick={onReset} type="button">Reset all</button>
        </div>
        <label className="chart-setting-toggle strategy-presentation-master">
          <input checked={settings.visible} onChange={(event) => onChange((current) => ({ ...current, visible: event.target.checked }))} type="checkbox" />
          <span><strong>Show strategy presentation</strong><small>One switch for all position evidence. Individual elements remain configured below.</small></span>
        </label>
        <section className="strategy-presentation-behavior" aria-label="Presentation behavior">
          <label><input checked={settings.avoidLabelCollisions} onChange={(event) => onChange((current) => ({ ...current, avoidLabelCollisions: event.target.checked }))} type="checkbox" /><span><strong>Avoid label collisions</strong><small>Moves labels to the nearest clear position while preserving their anchor.</small></span></label>
          <label><span><strong>Distant-marker connector</strong><small>Minimum vertical distance before a connector is drawn.</small></span><span className="chart-setting-inline"><input aria-label="Distant marker connector threshold" disabled={!settings.elements.connector.visible} max={48} min={8} onChange={(event) => onChange((current) => ({ ...current, connectorThreshold: Number(event.target.value) }))} type="range" value={settings.connectorThreshold} /><b>{settings.connectorThreshold}px</b></span></label>
        </section>
        <div className="strategy-presentation-element-list" data-disabled={!settings.visible || undefined}>
          {strategyVisualElementDefinitions.map((definition) => {
            const element = settings.elements[definition.key];
            const fallbackColor = strategyVisualElementFallbackColor(definition.key, palette.text);
            return <div className="strategy-presentation-element" key={definition.key}>
              <label>
                <input checked={element.visible} onChange={(event) => updateElement(definition.key, { visible: event.target.checked })} type="checkbox" />
                <span><strong>{definition.title}</strong><small>{definition.help}</small></span>
              </label>
              <span className="strategy-presentation-style-summary">
                <span className="strategy-presentation-style-swatches">
                  <i title={definition.kind === "label" ? "Text color" : "Element color"} style={{ background: strategyVisualElementSwatch(definition.key, element, fallbackColor) }} />
                  {definition.kind === "label" ? <i title="Box fill" style={{ background: strategyPresentationColor(element.fillColor, palette.background), borderColor: strategyPresentationColor(element.borderColor, strategyPresentationColor(element.color, fallbackColor)) }} /> : null}
                </span>
                <span>{strategyVisualStyleSummary(definition.key, element, definition.kind)}</span>
              </span>
              <button aria-label={`Customize ${definition.title} style`} className="strategy-presentation-style-button" onClick={() => setStyleElement(definition.key)} title={`Customize ${definition.title} style`} type="button"><Paintbrush size={14} /></button>
            </div>;
          })}
        </div>
      </>}
    </ChartColumnMenuPortal> : null}
  </div>;
}

type StrategyVisualElementDefinition = { help: string; key: StrategyVisualElementKey; kind: "connector" | "label" | "line" | "marker"; title: string };

const strategyVisualElementDefinitions: StrategyVisualElementDefinition[] = [
  { key: "entryLine", kind: "line", title: "Entry price line", help: "Position entry price across the lifecycle." },
  { key: "entryArrow", kind: "marker", title: "Entry arrow", help: "Exact entry time and execution price." },
  { key: "entryLabel", kind: "label", title: "Entry label", help: "One label with independently styled direction, size, separator, and price." },
  { key: "exitLine", kind: "line", title: "Exit price line", help: "Final exit price across the lifecycle." },
  { key: "exitArrow", kind: "marker", title: "Exit arrow", help: "Exact exit time and execution price." },
  { key: "exitLabel", kind: "label", title: "Exit label", help: "One label with independently styled action, size, price, and realized result." },
  { key: "levelLine", kind: "line", title: "Structural level lines", help: "Entry-frozen resistance or support evidence." },
  { key: "levelLabel", kind: "label", title: "Structural level labels", help: "L1–L3 and trigger identifiers." },
  { key: "stopLine", kind: "line", title: "Protective stop line", help: "Current or immutable entry-plan protection." },
  { key: "stopLabel", kind: "label", title: "Protective stop label", help: "Compact SL identifier attached to protection." },
  { key: "targetLine", kind: "line", title: "Profit target lines", help: "Current or immutable entry-plan targets." },
  { key: "targetLabel", kind: "label", title: "Profit target labels", help: "Compact TP identifiers attached to targets." },
  { key: "adjustmentLine", kind: "line", title: "Position-change line", help: "Exact-price rail for adds and protection revisions." },
  { key: "adjustmentArrow", kind: "marker", title: "Position-change arrow", help: "Exact event time and revised price." },
  { key: "adjustmentLabel", kind: "label", title: "Position-change label", help: "Add, target, stop, trail, and repair detail." },
  { key: "connector", kind: "connector", title: "Distant-marker connector", help: "Dashed link from a distant event marker to its candle." },
];

type StrategyCompositeLabelKey = "entryLabel" | "exitLabel";
type StrategyLabelPartDefinition = { help: string; key: StrategyVisualElementKey; title: string };

const strategyLabelPartDefinitions: Record<StrategyCompositeLabelKey, StrategyLabelPartDefinition[]> = {
  entryLabel: [
    { key: "entryDirectionPart", title: "Long", help: "Long-direction text and soft background." },
    { key: "entryShortDirectionPart", title: "Short", help: "Short-direction text and soft background." },
    { key: "entrySizePart", title: "Size", help: "Position quantity, emphasized independently." },
    { key: "entrySeparatorPart", title: "@", help: "Separator between size and execution price." },
    { key: "entryPricePart", title: "Long price", help: "Entry price for a long position." },
    { key: "entryShortPricePart", title: "Short price", help: "Entry price for a short position." },
  ],
  exitLabel: [
    { key: "exitReasonPart", title: "Close long", help: "Exit action for a long position." },
    { key: "exitShortReasonPart", title: "Cover short", help: "Exit action for a short position." },
    { key: "exitSizePart", title: "Size", help: "Final exit quantity, emphasized independently." },
    { key: "exitSeparatorPart", title: "Separators", help: "The @ and realized-result separators." },
    { key: "exitPricePart", title: "Long-exit price", help: "Execution price when closing a long." },
    { key: "exitShortPricePart", title: "Short-exit price", help: "Execution price when covering a short." },
    { key: "exitPnlPart", title: "Profit", help: "Positive realized result." },
    { key: "exitPnlLossPart", title: "Loss", help: "Negative realized result." },
  ],
};

function StrategyPresentationStylePage({
  definition,
  elements,
  fallbackColor,
  onBack,
  onChange,
  onElementChange,
  onReset,
  settings,
}: {
  definition: StrategyVisualElementDefinition;
  elements: Record<StrategyVisualElementKey, StrategyPresentationStyleSettings>;
  fallbackColor: string;
  onBack: () => void;
  onChange: (patch: Partial<StrategyPresentationStyleSettings>) => void;
  onElementChange: (key: StrategyVisualElementKey, patch: Partial<StrategyPresentationStyleSettings>) => void;
  onReset: () => void;
  settings: StrategyPresentationStyleSettings;
}) {
  const compositeKey = definition.key === "entryLabel" || definition.key === "exitLabel" ? definition.key : null;
  if (compositeKey) return <StrategyCompositeLabelStylePage
    definition={definition}
    elements={elements}
    fallbackColor={fallbackColor}
    labelKey={compositeKey}
    onBack={onBack}
    onElementChange={onElementChange}
    onReset={onReset}
    settings={settings}
  />;
  const color = validHexColor(settings.color, validHexColor(fallbackColor, "#111827"));
  const showsLine = definition.kind === "line" || definition.kind === "connector";
  const showsMarker = definition.kind === "marker";
  const showsLabel = definition.kind === "label";
  const fillColor = validHexColor(settings.fillColor, validHexColor(readChartPalette().background, "#ffffff"));
  const borderColor = validHexColor(settings.borderColor, color);
  const defaultTextColorLabel = definition.key === "exitLabel" || definition.key === "adjustmentLabel" ? "Semantic color" : "Theme color";
  return <section className="strategy-presentation-style-page">
    <div className="strategy-presentation-header">
      <button className="strategy-presentation-back" onClick={onBack} type="button"><ArrowLeft size={15} /> Elements</button>
      <div><strong>{definition.title}</strong><span>Visual style · changes apply immediately</span></div>
      <button className="button secondary compact" onClick={onReset} type="button">Reset style</button>
    </div>
    <div className="strategy-presentation-style-intro"><i style={{ background: showsLabel ? fillColor : color, borderColor: showsLabel ? borderColor : undefined }} /><span><strong>{definition.title}</strong><small>{definition.help}</small></span></div>
    <div className="strategy-presentation-controls">
      {showsLabel ? <>
        <section className="strategy-presentation-control-section">
          <header><strong>Text</strong><span>Foreground appearance, independent of the label box.</span></header>
          <div className="strategy-presentation-control-grid">
            <StrategyStyleColor defaultLabel={defaultTextColorLabel} fallbackColor={color} label="Text color" onChange={(value) => onChange({ color: value })} value={settings.color} />
            <StrategyStyleRange label="Text opacity" max={100} min={15} onChange={(value) => onChange({ opacity: value / 100 })} suffix="%" value={Math.round(settings.opacity * 100)} />
            <StrategyStyleRange label="Text size" max={16} min={8} onChange={(labelSize) => onChange({ labelSize })} suffix="px" value={settings.labelSize} />
            <StrategyFontWeightSelect onChange={(fontWeight) => onChange({ fontWeight })} value={settings.fontWeight} />
          </div>
        </section>
        <section className="strategy-presentation-control-section">
          <header><strong>Box</strong><span>Fill, dimensions, and edge are configured separately.</span></header>
          <div className="strategy-presentation-control-grid">
            <StrategyStyleColor defaultLabel="Chart background" fallbackColor={fillColor} label="Fill color" onChange={(value) => onChange({ fillColor: value, ...(value && settings.fillOpacity === 0 ? { fillOpacity: 1 } : {}) })} value={settings.fillColor} />
            <StrategyStyleRange label="Fill opacity" max={100} min={0} onChange={(value) => onChange({ fillOpacity: value / 100 })} suffix="%" value={Math.round(settings.fillOpacity * 100)} />
            <StrategyStyleRange label="Background blur" max={8} min={0} onChange={(fillBlur) => onChange({ fillBlur })} suffix="px" value={settings.fillBlur} />
            <StrategyStyleRange label="Horizontal padding" max={14} min={2} onChange={(labelPaddingX) => onChange({ labelPaddingX })} suffix="px" value={settings.labelPaddingX} />
            <StrategyStyleRange label="Vertical padding" max={10} min={1} onChange={(labelPaddingY) => onChange({ labelPaddingY })} suffix="px" value={settings.labelPaddingY} />
            <StrategyStyleColor defaultLabel="Text color" fallbackColor={borderColor} label="Edge color" onChange={(value) => onChange({ borderColor: value, ...(value && settings.borderOpacity === 0 ? { borderOpacity: 1 } : {}) })} value={settings.borderColor} />
            <StrategyStyleRange label="Edge opacity" max={100} min={0} onChange={(value) => onChange({ borderOpacity: value / 100 })} suffix="%" value={Math.round(settings.borderOpacity * 100)} />
            <StrategyStyleSelect label="Edge type" onChange={(borderStyle) => onChange({ borderStyle })} value={settings.borderStyle} />
            <StrategyStyleRange label="Edge size" max={4} min={0} onChange={(borderWidth) => onChange({ borderWidth })} suffix="px" value={settings.borderWidth} />
          </div>
        </section>
      </> : <div className="strategy-presentation-control-grid">
        <StrategyStyleColor defaultLabel="Theme color" fallbackColor={color} label="Color" onChange={(value) => onChange({ color: value })} value={settings.color} />
        <StrategyStyleRange label="Opacity" max={100} min={15} onChange={(value) => onChange({ opacity: value / 100 })} suffix="%" value={Math.round(settings.opacity * 100)} />
        {showsLine ? <><StrategyStyleSelect label="Edge type" onChange={(lineStyle) => onChange({ lineStyle })} value={settings.lineStyle} /><StrategyStyleRange label="Edge size" max={5} min={1} onChange={(lineWidth) => onChange({ lineWidth })} suffix="px" value={settings.lineWidth} /></> : null}
        {showsMarker ? <><StrategyStyleRange label="Marker size" max={14} min={4} onChange={(markerSize) => onChange({ markerSize })} suffix="px" value={settings.markerSize} /><StrategyStyleRange label="Fill opacity" max={100} min={15} onChange={(value) => onChange({ fillOpacity: value / 100 })} suffix="%" value={Math.round(settings.fillOpacity * 100)} /><StrategyStyleSelect label="Edge type" onChange={(borderStyle) => onChange({ borderStyle })} value={settings.borderStyle} /><StrategyStyleRange label="Edge size" max={4} min={0} onChange={(borderWidth) => onChange({ borderWidth })} suffix="px" value={settings.borderWidth} /></> : null}
      </div>}
    </div>
  </section>;
}

function StrategyCompositeLabelStylePage({
  definition,
  elements,
  fallbackColor,
  labelKey,
  onBack,
  onElementChange,
  onReset,
  settings,
}: {
  definition: StrategyVisualElementDefinition;
  elements: Record<StrategyVisualElementKey, StrategyPresentationStyleSettings>;
  fallbackColor: string;
  labelKey: StrategyCompositeLabelKey;
  onBack: () => void;
  onElementChange: (key: StrategyVisualElementKey, patch: Partial<StrategyPresentationStyleSettings>) => void;
  onReset: () => void;
  settings: StrategyPresentationStyleSettings;
}) {
  const partDefinitions = strategyLabelPartDefinitions[labelKey];
  const [selectedPartKey, setSelectedPartKey] = useState<StrategyVisualElementKey>(partDefinitions[0].key);
  const selectedPart = partDefinitions.find((part) => part.key === selectedPartKey) ?? partDefinitions[0];
  const partSettings = elements[selectedPart.key];
  const palette = readChartPalette();
  const borderColor = validHexColor(settings.borderColor, validHexColor(settings.color, fallbackColor));
  const fillColor = validHexColor(settings.fillColor, palette.background);
  return <section className="strategy-presentation-style-page strategy-presentation-composite-label-page">
    <div className="strategy-presentation-header">
      <button className="strategy-presentation-back" onClick={onBack} type="button"><ArrowLeft size={15} /> Elements</button>
      <div><strong>{definition.title}</strong><span>Unified box · independently styled text parts</span></div>
      <button className="button secondary compact" onClick={onReset} type="button">Reset label</button>
    </div>
    <StrategyCompositeLabelPreview elements={elements} labelKey={labelKey} settings={settings} />
    <div className="strategy-presentation-controls">
      <section className="strategy-presentation-control-section">
        <header><strong>Unified label box</strong><span>One fill and one continuous edge around the complete label.</span></header>
        <div className="strategy-presentation-control-grid">
          <StrategyStyleColor defaultLabel="Chart background" fallbackColor={fillColor} label="Box fill" onChange={(value) => onElementChange(labelKey, { fillColor: value, ...(value && settings.fillOpacity === 0 ? { fillOpacity: 1 } : {}) })} value={settings.fillColor} />
          <StrategyStyleRange label="Box fill opacity" max={100} min={0} onChange={(value) => onElementChange(labelKey, { fillOpacity: value / 100 })} suffix="%" value={Math.round(settings.fillOpacity * 100)} />
          <StrategyStyleColor defaultLabel="Semantic color" fallbackColor={borderColor} label="Unified edge color" onChange={(value) => onElementChange(labelKey, { borderColor: value, ...(value && settings.borderOpacity === 0 ? { borderOpacity: 1 } : {}) })} value={settings.borderColor} />
          <StrategyStyleRange label="Unified edge opacity" max={100} min={0} onChange={(value) => onElementChange(labelKey, { borderOpacity: value / 100 })} suffix="%" value={Math.round(settings.borderOpacity * 100)} />
          <StrategyStyleSelect label="Unified edge type" onChange={(borderStyle) => onElementChange(labelKey, { borderStyle })} value={settings.borderStyle} />
          <StrategyStyleRange label="Unified edge size" max={4} min={0} onChange={(borderWidth) => onElementChange(labelKey, { borderWidth })} suffix="px" value={settings.borderWidth} />
        </div>
      </section>
      <section className="strategy-presentation-control-section">
        <header><strong>Label parts</strong><span>Select a part, then configure its text and background below.</span></header>
        <div aria-label={`${definition.title} parts`} className="strategy-presentation-part-tabs" role="tablist">
          {partDefinitions.map((part) => <button
            aria-selected={part.key === selectedPart.key}
            className={part.key === selectedPart.key ? "is-active" : undefined}
            key={part.key}
            onClick={() => setSelectedPartKey(part.key)}
            role="tab"
            type="button"
          >
            <i style={{ background: strategyPresentationColor(elements[part.key].fillColor, palette.background), borderColor: strategyPresentationColor(elements[part.key].color, fallbackColor) }} />
            {part.title}
          </button>)}
        </div>
        <label className="strategy-presentation-part-enabled"><input checked={partSettings.visible} onChange={(event) => onElementChange(selectedPart.key, { visible: event.target.checked })} type="checkbox" /><span><strong>{selectedPart.title}</strong><small>{selectedPart.help}</small></span></label>
        <div className="strategy-presentation-control-grid">
          <StrategyStyleColor defaultLabel="Semantic color" fallbackColor={validHexColor(partSettings.color, fallbackColor)} label="Text color" onChange={(value) => onElementChange(selectedPart.key, { color: value })} value={partSettings.color} />
          <StrategyStyleRange label="Text opacity" max={100} min={15} onChange={(value) => onElementChange(selectedPart.key, { opacity: value / 100 })} suffix="%" value={Math.round(partSettings.opacity * 100)} />
          <StrategyStyleRange label="Text size" max={16} min={8} onChange={(labelSize) => onElementChange(selectedPart.key, { labelSize })} suffix="px" value={partSettings.labelSize} />
          <StrategyFontWeightSelect onChange={(fontWeight) => onElementChange(selectedPart.key, { fontWeight })} value={partSettings.fontWeight} />
          <StrategyStyleColor defaultLabel="Chart background" fallbackColor={validHexColor(partSettings.fillColor, palette.background)} label="Background color" onChange={(value) => onElementChange(selectedPart.key, { fillColor: value, ...(value && partSettings.fillOpacity === 0 ? { fillOpacity: 1 } : {}) })} value={partSettings.fillColor} />
          <StrategyStyleRange label="Background opacity" max={100} min={0} onChange={(value) => onElementChange(selectedPart.key, { fillOpacity: value / 100 })} suffix="%" value={Math.round(partSettings.fillOpacity * 100)} />
          <StrategyStyleRange label="Background blur" max={8} min={0} onChange={(fillBlur) => onElementChange(selectedPart.key, { fillBlur })} suffix="px" value={partSettings.fillBlur} />
          <StrategyStyleRange label="Horizontal padding" max={14} min={2} onChange={(labelPaddingX) => onElementChange(selectedPart.key, { labelPaddingX })} suffix="px" value={partSettings.labelPaddingX} />
          <StrategyStyleRange label="Vertical padding" max={10} min={1} onChange={(labelPaddingY) => onElementChange(selectedPart.key, { labelPaddingY })} suffix="px" value={partSettings.labelPaddingY} />
        </div>
      </section>
    </div>
  </section>;
}

function StrategyCompositeLabelPreview({ elements, labelKey, settings }: { elements: Record<StrategyVisualElementKey, StrategyPresentationStyleSettings>; labelKey: StrategyCompositeLabelKey; settings: StrategyPresentationStyleSettings }) {
  const palette = readChartPalette();
  const rows: Array<Array<{ key: StrategyVisualElementKey; text: string }>> = labelKey === "entryLabel" ? [
    [{ key: "entryDirectionPart", text: "Long" }, { key: "entrySizePart", text: "2,543" }, { key: "entrySeparatorPart", text: "@" }, { key: "entryPricePart", text: "4.40" }],
    [{ key: "entryShortDirectionPart", text: "Short" }, { key: "entrySizePart", text: "2,543" }, { key: "entrySeparatorPart", text: "@" }, { key: "entryShortPricePart", text: "4.40" }],
  ] : [
    [{ key: "exitReasonPart", text: "Exit" }, { key: "exitSizePart", text: "2,518" }, { key: "exitSeparatorPart", text: "@" }, { key: "exitPricePart", text: "4.42" }, { key: "exitSeparatorPart", text: "·" }, { key: "exitPnlPart", text: "+$23.39" }],
    [{ key: "exitShortReasonPart", text: "Cover" }, { key: "exitSizePart", text: "2,518" }, { key: "exitSeparatorPart", text: "@" }, { key: "exitShortPricePart", text: "4.42" }, { key: "exitSeparatorPart", text: "·" }, { key: "exitPnlLossPart", text: "−$18.20" }],
  ];
  const borderColor = strategyPresentationColor(settings.borderColor, settings.color || palette.text);
  const containerStyle: CSSProperties = {
    background: rgbaFromHex(strategyPresentationColor(settings.fillColor, palette.background), settings.fillOpacity),
    borderColor: rgbaFromHex(borderColor, settings.borderOpacity),
    borderStyle: settings.borderStyle,
    borderWidth: settings.borderWidth,
  };
  return <div className="strategy-presentation-label-preview" aria-label={`${labelKey === "entryLabel" ? "Entry" : "Exit"} label preview`}>
    <span>Preview</span>
    <div>
      {rows.map((row, rowIndex) => <div className="strategy-presentation-label-preview-box" key={rowIndex} style={containerStyle}>
        {row.map((part, partIndex) => {
          const partStyle = elements[part.key];
          const color = strategyPresentationColor(partStyle.color, palette.text);
          const background = strategyPresentationColor(partStyle.fillColor, palette.background);
          return partStyle.visible ? <span key={`${part.key}:${partIndex}`} style={{
            background: rgbaFromHex(background, partStyle.fillOpacity),
            boxShadow: partStyle.fillBlur > 0 ? `0 0 ${partStyle.fillBlur}px ${rgbaFromHex(background, partStyle.fillOpacity)}` : undefined,
            color: rgbaFromHex(color, partStyle.opacity),
            fontSize: partStyle.labelSize,
            fontWeight: partStyle.fontWeight,
            padding: `${partStyle.labelPaddingY}px ${partStyle.labelPaddingX}px`,
          }}>{part.text}</span> : null;
        })}
      </div>)}
    </div>
  </div>;
}

function StrategyStyleColor({ defaultLabel, fallbackColor, label, onChange, value }: { defaultLabel: string; fallbackColor: string; label: string; onChange: (value: string) => void; value: string }) {
  const displayedColor = validHexColor(value, fallbackColor);
  return <label><span>{label}</span><span className="strategy-presentation-color"><input
    aria-label={label}
    onBlur={(event) => { if (!value) onChange(event.currentTarget.value); }}
    onFocus={() => { if (!value) onChange(displayedColor); }}
    onInput={(event) => onChange(event.currentTarget.value)}
    type="color"
    value={displayedColor}
  /><code>{displayedColor.toUpperCase()}</code>{value
    ? <button onClick={() => onChange("")} type="button">Use default</button>
    : <button aria-label={`Apply ${displayedColor.toUpperCase()} to ${label}`} onClick={() => onChange(displayedColor)} type="button">Apply color</button>
  }<em>{value ? "Custom" : defaultLabel}</em></span></label>;
}

function StrategyStyleRange({ label, max, min, onChange, suffix, value }: { label: string; max: number; min: number; onChange: (value: number) => void; suffix: string; value: number }) {
  return <label><span>{label}</span><span className="chart-setting-inline"><input aria-label={label} max={max} min={min} onChange={(event) => onChange(Number(event.target.value))} type="range" value={value} /><b>{value}{suffix}</b></span></label>;
}

function StrategyStyleSelect({ label, onChange, value }: { label: string; onChange: (value: LegendLineStyle) => void; value: LegendLineStyle }) {
  return <label><span>{label}</span><select aria-label={label} onChange={(event) => onChange(event.target.value as LegendLineStyle)} value={value}><option value="solid">Solid</option><option value="dashed">Dashed</option><option value="dotted">Dotted</option></select></label>;
}

function StrategyFontWeightSelect({ onChange, value }: { onChange: (value: 400 | 500 | 600) => void; value: 400 | 500 | 600 }) {
  return <label><span>Text weight</span><select aria-label="Text weight" onChange={(event) => onChange(Number(event.target.value) as 400 | 500 | 600)} value={value}><option value={400}>Regular</option><option value={500}>Medium</option><option value={600}>Semibold</option></select></label>;
}

function strategyVisualElementFallbackColor(key: StrategyVisualElementKey, neutral: string) {
  if (key.startsWith("entry")) return chartSemanticColor("--chart-strategy-entry", "#007DFF");
  if (key.startsWith("stop")) return chartSemanticColor("--chart-strategy-stop", "#FF1744");
  if (key.startsWith("target")) return chartSemanticColor("--chart-strategy-target", "#00B84F");
  if (key.startsWith("exit")) return "#C2410C";
  return neutral;
}

function strategyVisualElementSwatch(key: StrategyVisualElementKey, settings: StrategyPresentationStyleSettings, fallbackColor: string) {
  if (settings.color) return strategyPresentationColor(settings.color, fallbackColor);
  if (key === "exitLabel") {
    return `linear-gradient(90deg, ${chartSemanticColor("--chart-strategy-target", "#00B84F")} 0 50%, ${chartSemanticColor("--danger", "#DC2626")} 50% 100%)`;
  }
  if (key.startsWith("adjustment") || key === "connector") {
    return `linear-gradient(90deg, ${chartSemanticColor("--chart-strategy-entry", "#007DFF")} 0 34%, ${chartSemanticColor("--chart-strategy-target", "#00B84F")} 34% 67%, ${chartSemanticColor("--chart-strategy-stop", "#FF1744")} 67% 100%)`;
  }
  return strategyPresentationColor(settings.color, fallbackColor);
}

function strategyVisualStyleSummary(key: StrategyVisualElementKey, settings: StrategyPresentationStyleSettings, kind: StrategyVisualElementDefinition["kind"]) {
  const semantic = !settings.color && (key === "exitLabel" || key.startsWith("adjustment") || key === "connector") ? "Semantic · " : "";
  if (key === "entryLabel") return `6 parts · ${settings.borderWidth}px ${settings.borderStyle} edge`;
  if (key === "exitLabel") return `8 parts · ${settings.borderWidth}px ${settings.borderStyle} edge`;
  if (kind === "label") return `${semantic}${settings.labelSize}px/${settings.fontWeight} · ${settings.labelPaddingX}×${settings.labelPaddingY}px box`;
  if (kind === "marker") return `${semantic}${settings.markerSize}px marker · ${Math.round(settings.opacity * 100)}%`;
  return `${semantic}${settings.lineWidth}px ${settings.lineStyle} · ${Math.round(settings.opacity * 100)}%`;
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
  onShowSplitEventsChange,
  showSplitEvents,
  settings
}: {
  anchor: HTMLElement | null;
  onChange: <K extends keyof ChartAppearanceSettings>(key: K, value: ChartAppearanceSettings[K]) => void;
  onClose: () => void;
  onReset: () => void;
  onShowSplitEventsChange?: (value: boolean) => void;
  showSplitEvents: boolean;
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

      {onShowSplitEventsChange ? <ChartSettingsSection title="Corporate Actions">
        <p className="chart-settings-help">
          Split markers use the chart's point-in-time corporate-action authority and are placed on the execution date.
        </p>
        <label className="chart-setting-toggle">
          <input checked={showSplitEvents} type="checkbox" onChange={(event) => onShowSplitEventsChange(event.target.checked)} />
          Show stock split events
        </label>
      </ChartSettingsSection> : null}

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
      minimumHoldProbability: settings.minimumHoldProbability,
      minimumPressureMagnitude: settings.minimumPressureMagnitude,
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
      showUnifiedHoldProbability: settings.showUnifiedHoldProbability,
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

function loadStrategyPresentationSettings(storageKey = STRATEGY_PRESENTATION_STORAGE_KEY): StrategyPresentationSettings {
  if (typeof window === "undefined") return defaultStrategyPresentationSettings;
  try {
    const raw = window.localStorage.getItem(storageKey);
    return raw
      ? normalizeStrategyPresentationSettings(JSON.parse(raw) as Partial<StrategyPresentationSettings>)
      : defaultStrategyPresentationSettings;
  } catch {
    return defaultStrategyPresentationSettings;
  }
}

function saveStrategyPresentationSettings(settings: StrategyPresentationSettings, storageKey = STRATEGY_PRESENTATION_STORAGE_KEY) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(storageKey, JSON.stringify(settings));
}

function normalizeStrategyPresentationStyle(
  settings: Partial<StrategyPresentationStyleSettings> | undefined,
  defaults: StrategyPresentationStyleSettings,
): StrategyPresentationStyleSettings {
  const fontWeight = Number(settings?.fontWeight);
  return {
    borderColor: settings?.borderColor === "" ? "" : validHexColor(settings?.borderColor, defaults.borderColor || ""),
    borderOpacity: clampNumber(settings?.borderOpacity, 0, 1, defaults.borderOpacity),
    borderStyle: settings?.borderStyle === "solid" || settings?.borderStyle === "dashed" || settings?.borderStyle === "dotted" ? settings.borderStyle : defaults.borderStyle,
    borderWidth: Math.round(clampNumber(settings?.borderWidth, 0, 4, defaults.borderWidth)),
    color: settings?.color === "" ? "" : validHexColor(settings?.color, defaults.color || ""),
    fillColor: settings?.fillColor === "" ? "" : validHexColor(settings?.fillColor, defaults.fillColor || ""),
    fillBlur: Math.round(clampNumber(settings?.fillBlur, 0, 8, defaults.fillBlur)),
    fillOpacity: clampNumber(settings?.fillOpacity, 0, 1, defaults.fillOpacity),
    fontWeight: fontWeight === 400 || fontWeight === 500 || fontWeight === 600 ? fontWeight : defaults.fontWeight,
    labelPaddingX: Math.round(clampNumber(settings?.labelPaddingX, 2, 14, defaults.labelPaddingX)),
    labelPaddingY: Math.round(clampNumber(settings?.labelPaddingY, 1, 10, defaults.labelPaddingY)),
    labelSize: Math.round(clampNumber(settings?.labelSize, 8, 16, defaults.labelSize)),
    lineStyle: settings?.lineStyle === "solid" || settings?.lineStyle === "dashed" || settings?.lineStyle === "dotted" ? settings.lineStyle : defaults.lineStyle,
    lineWidth: Math.round(clampNumber(settings?.lineWidth, 1, 5, defaults.lineWidth)),
    markerSize: Math.round(clampNumber(settings?.markerSize, 4, 14, defaults.markerSize)),
    opacity: clampNumber(settings?.opacity, 0.15, 1, defaults.opacity),
    visible: typeof settings?.visible === "boolean" ? settings.visible : defaults.visible,
  };
}

function normalizeStrategyPresentationSettings(settings: Partial<StrategyPresentationSettings>): StrategyPresentationSettings {
  const legacy = settings as Partial<StrategyPresentationSettings> & Partial<Record<"adjustments" | "entry" | "exit" | "levels" | "stop" | "targets", Partial<StrategyPresentationStyleSettings>>>;
  const legacyByElement: Record<StrategyVisualElementKey, Partial<StrategyPresentationStyleSettings> | undefined> = {
    entryLine: legacy.entry, entryArrow: legacy.entry, entryLabel: legacy.entry,
    entryDirectionPart: undefined, entryShortDirectionPart: undefined, entrySizePart: undefined, entrySeparatorPart: undefined, entryPricePart: undefined, entryShortPricePart: undefined,
    exitLine: legacy.exit, exitArrow: legacy.exit, exitLabel: legacy.exit ? { ...legacy.exit, color: "" } : undefined,
    exitReasonPart: undefined, exitShortReasonPart: undefined, exitSizePart: undefined, exitSeparatorPart: undefined, exitPricePart: undefined, exitShortPricePart: undefined, exitPnlPart: undefined, exitPnlLossPart: undefined,
    levelLine: legacy.levels, levelLabel: legacy.levels,
    stopLine: legacy.stop, stopLabel: legacy.stop,
    targetLine: legacy.targets, targetLabel: legacy.targets,
    adjustmentLine: legacy.adjustments, adjustmentArrow: legacy.adjustments, adjustmentLabel: legacy.adjustments,
    connector: legacy.adjustments,
  };
  const elements = Object.fromEntries((Object.keys(defaultStrategyPresentationSettings.elements) as StrategyVisualElementKey[]).map((key) => [
    key,
    normalizeStrategyPresentationStyle(settings.elements?.[key] ?? legacyByElement[key], defaultStrategyPresentationSettings.elements[key]),
  ])) as Record<StrategyVisualElementKey, StrategyPresentationStyleSettings>;
  return {
    avoidLabelCollisions: typeof settings.avoidLabelCollisions === "boolean" ? settings.avoidLabelCollisions : true,
    connectorThreshold: Math.round(clampNumber(settings.connectorThreshold, 8, 48, defaultStrategyPresentationSettings.connectorThreshold)),
    elements,
    visible: typeof settings.visible === "boolean" ? settings.visible : defaultStrategyPresentationSettings.visible,
  };
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

function chartTimelineData(candles: Candle[], timeframe: string, hideEmptyIntervals = true, events: ChartTimelineEvent[] = []): CandleSeriesDatum[] {
  const data = hideEmptyIntervals
    ? [...candles].sort((left, right) => left.time - right.time)
    : candleDataForTimeframe(candles, timeframe);
  if (!events.length) return data;
  const occupiedTimes = new Set(data.map((item) => Number(item.time)));
  const anchors = events
    .filter((event) => Number.isFinite(event.time) && !occupiedTimes.has(event.time))
    .map((event) => ({ time: event.time }));
  return [...data, ...anchors].sort((left, right) => Number(left.time) - Number(right.time));
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
    minimumHoldProbability: 0,
    minimumPressureMagnitude: 0,
    opacity: 1,
    preset: "micro",
    showConnectors: true,
    showAxisLabel: false,
    showHistoricalLabels: true,
    showLabels: true,
    showUnifiedActive: true,
    showUnifiedBroken: true,
    showUnifiedHoldProbability: true,
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
    minimumHoldProbability: clampNumber(stored.minimumHoldProbability, 0, 1, defaults.minimumHoldProbability),
    minimumPressureMagnitude: clampNumber(stored.minimumPressureMagnitude, 0, 1, defaults.minimumPressureMagnitude),
    opacity: clampNumber(stored.opacity ?? defaults.opacity, 0, 1, 1),
    preset: stored.preset === "tactical" || stored.preset === "context" ? stored.preset : defaults.preset,
    showConnectors: stored.showConnectors ?? defaults.showConnectors,
    showAxisLabel: stored.showAxisLabel ?? defaults.showAxisLabel,
    showHistoricalLabels: stored.showHistoricalLabels ?? defaults.showHistoricalLabels,
    showLabels: stored.showLabels ?? defaults.showLabels,
    showUnifiedActive: stored.showUnifiedActive ?? defaults.showUnifiedActive,
    showUnifiedBroken: stored.showUnifiedBroken ?? defaults.showUnifiedBroken,
    showUnifiedHoldProbability: stored.showUnifiedHoldProbability ?? defaults.showUnifiedHoldProbability,
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
  minimumHoldProbability: number;
  minimumPressureMagnitude: number;
  opacity: number;
  preset: ChartPreset;
  showConnectors: boolean;
  showAxisLabel: boolean;
  showHistoricalLabels: boolean;
  showUnifiedActive: boolean;
  showUnifiedBroken: boolean;
  showUnifiedHoldProbability: boolean;
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
    minimumHoldProbability: clampNumber(stored.minimumHoldProbability, 0, 1, 0),
    minimumPressureMagnitude: clampNumber(stored.minimumPressureMagnitude, 0, 1, 0),
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
    showUnifiedHoldProbability: stored.showUnifiedHoldProbability !== false,
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
    && clampNumber(zone.holdProbability, 0, 1, 0) >= settings.minimumHoldProbability
    && Math.abs(clampNumber(zone.pressureBias, -1, 1, 0)) >= settings.minimumPressureMagnitude
    && clampNumber(zone.breakProbability, 0, 1, 0) <= settings.maximumBreakProbability;
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
  candles: Candle[],
  liveEntryLine?: LiveEntryLine | null
) {
  if (!layer) return;
  clearOverlayLayer(layer);
  drawLiveEntryLine(chart, priceSeries, layer, candles, liveEntryLine);
}

function drawSessionRegionPrimitiveGeometry(
  chart: IChartApi,
  context: CanvasRenderingContext2D,
  width: number,
  height: number,
  regions: Region[],
  timeline: CandleSeriesDatum[],
  candles: Candle[],
  settings: ChartAppearanceSettings,
) {
  if (!timeline.length || width < 1 || height < 1) return;
  context.save();
  context.globalCompositeOperation = "source-over";
  regions.forEach((region) => {
    const coordinates = sessionRegionCoordinates(chart, region, timeline);
    if (!coordinates) return;
    const span = clippedHorizontalSpan(coordinates.start, coordinates.end, width);
    if (!span) return;
    context.fillStyle = sessionRegionColor(region, settings);
    context.fillRect(span.left, 0, span.width, height);
  });
  if (settings.daySeparatorsVisible && candles.length > 1) {
    const barWidth = estimateBarWidth(chart, candles);
    let previousDate = marketDate(candles[0].time);
    context.beginPath();
    context.setLineDash(canvasLineDash(settings.daySeparatorStyle, 1));
    context.strokeStyle = rgbaFromHex(settings.daySeparatorColor, 0.78);
    context.lineWidth = 1;
    candles.slice(1).forEach((candle) => {
      const currentDate = marketDate(candle.time);
      if (currentDate === previousDate) return;
      previousDate = currentDate;
      const coordinate = chart.timeScale().timeToCoordinate(candle.time as Time);
      if (!isVisibleCoordinate(coordinate, width)) return;
      const x = Number(coordinate) - barWidth / 2;
      context.moveTo(x, 0);
      context.lineTo(x, height);
    });
    context.stroke();
  }
  context.restore();
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
      if (
        zone.annotationKind === "unified-structure-level"
        && zone.latest
        && settings.showUnifiedHoldProbability
        && Number.isFinite(zone.holdProbability)
      ) {
        drawUnifiedHoldProbabilityLabel(
          context,
          `H${Math.round(clampNumber(zone.holdProbability, 0, 1, 0) * 100)}%`,
          span,
          center,
          borderColor,
          chartBackground,
          settings,
          lineLabelBoxes,
          width,
          plotBottom,
        );
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

function drawUnifiedHoldProbabilityLabel(
  context: CanvasRenderingContext2D,
  text: string,
  span: HorizontalSpan,
  centerY: number,
  color: string,
  chartBackground: string,
  settings: ResolvedPriceZoneLegendSettings,
  placed: CanvasBox[],
  layerWidth: number,
  plotBottom: number,
) {
  const fontSize = Math.max(8, Math.min(10, settings.labelFontSize - 1));
  context.save();
  context.font = `700 ${fontSize}px ${canvasInterfaceFont()}`;
  const labelWidth = Math.ceil(context.measureText(text).width) + 6;
  const labelHeight = fontSize + 4;
  const horizontalFractions = [1, 0.82, 0.64, 0.46, 0.28];
  const verticalOffsets = [0, -(labelHeight + 1), labelHeight + 1];
  let selected: CanvasBox | null = null;
  for (const offsetY of verticalOffsets) {
    for (const fraction of horizontalFractions) {
      const right = span.left + span.width * fraction - 3;
      const left = right - labelWidth;
      const top = centerY - labelHeight / 2 + offsetY;
      const box = { bottom: top + labelHeight, left, right, top };
      const insideSpan = box.left >= span.left + 2 && box.right <= span.right - 2;
      const insidePlot = box.left >= 2 && box.right <= layerWidth - 2 && box.top >= 2 && box.bottom <= plotBottom - 2;
      if (!insideSpan || !insidePlot) continue;
      if (placed.some((item) => boxesOverlap(box, item, 2))) continue;
      selected = box;
      break;
    }
    if (selected) break;
  }
  if (!selected) {
    context.restore();
    return false;
  }
  if (Math.abs((selected.top + selected.bottom) / 2 - centerY) > 1) {
    const connectorX = selected.right;
    context.strokeStyle = rgbaFromHex(color, Math.max(0.45, settings.opacity));
    context.lineWidth = 1;
    context.beginPath();
    context.moveTo(connectorX, centerY);
    context.lineTo(connectorX, selected.top > centerY ? selected.top : selected.bottom);
    context.stroke();
  }
  context.fillStyle = rgbaFromHex(chartBackground, 0.92);
  context.fillRect(selected.left, selected.top, labelWidth, labelHeight);
  context.fillStyle = rgbaFromHex(color, Math.max(0.72, settings.opacity));
  context.textBaseline = "middle";
  context.fillText(text, selected.left + 3, selected.top + labelHeight / 2);
  placed.push(selected);
  context.restore();
  return true;
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

function lowerBoundCandleTime(candles: Array<{ time: number }>, target: number) {
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
  timeline: Array<{ time: number }>,
  settings: StrategyPresentationSettings,
) {
  if (!settings.visible || !timeline.length || width < 1 || height < 1 || (!annotations.length && !executions.length)) return;
  const elements = settings.elements;
  const chartBackground = validHexColor(readChartPalette().background, "#ffffff");
  const neutralColor = chartSemanticColor("--foreground", "#111827");
  const successColor = chartSemanticColor("--chart-strategy-target", "#00B84F");
  const dangerColor = chartSemanticColor("--danger", "#DC2626");
  const stopColor = chartSemanticColor("--chart-strategy-stop", "#FF1744");
  const infoColor = chartSemanticColor("--chart-strategy-entry", "#007DFF");
  const exitFallbackColor = "#C2410C";
  const labelLayout: CanvasAnnotationLayout | undefined = settings.avoidLabelCollisions ? { boxes: [] } : undefined;
  context.save();
  context.globalCompositeOperation = "source-over";
  context.lineCap = "round";
  context.lineJoin = "round";
  annotations.forEach((annotation) => {
    const entryX = xForAnnotationTime(chart, annotation.entryTime, timeline);
    const endTime = annotation.endTime ?? annotation.exitTime ?? annotation.entryTime;
    const resolvedEndX = xForAnnotationTime(chart, endTime, timeline);
    // Lifecycle geometry is always owned by event time. Tying an open
    // position to the pane edge makes it float while the user pans.
    const exitX = resolvedEndX;
    const entryY = priceSeries.priceToCoordinate(annotation.entryPrice);
    const exitY = typeof annotation.exitPrice === "number" ? priceSeries.priceToCoordinate(annotation.exitPrice) : null;
    if (entryX === null || exitX === null || entryY === null) return;
    const span = clippedTradeSpan(entryX, exitX, width);
    if (!span) return;
    const guideStartX = annotation.guideStartTime === undefined
      ? entryX
      : xForAnnotationTime(chart, annotation.guideStartTime, timeline);
    const guideSpan = guideStartX === null
      ? span
      : clippedTradeSpan(guideStartX, exitX, width) ?? span;
    const entryLineColor = strategyPresentationColor(elements.entryLine.color, infoColor);
    const entryArrowColor = strategyPresentationColor(elements.entryArrow.color, infoColor);
    const entryLabelColor = strategyPresentationColor(elements.entryLabel.color, infoColor);
    const exitLineColor = strategyPresentationColor(elements.exitLine.color, exitFallbackColor);
    const exitArrowColor = strategyPresentationColor(elements.exitArrow.color, exitFallbackColor);
    const exitLabelFallback = Number(annotation.pnl) > 0 ? successColor : Number(annotation.pnl) < 0 ? dangerColor : validHexColor(annotation.exitLabelColor, exitFallbackColor);
    const exitLabelColor = strategyPresentationColor(elements.exitLabel.color, exitLabelFallback);
    const entryLabelPartSettings: TradeLabelPartSettings = {
      long: elements.entryDirectionPart,
      priceLong: elements.entryPricePart,
      priceShort: elements.entryShortPricePart,
      separator: elements.entrySeparatorPart,
      short: elements.entryShortDirectionPart,
      size: elements.entrySizePart,
    };
    const exitLabelPartSettings: TradeLabelPartSettings = {
      exitLong: elements.exitReasonPart,
      exitPriceLong: elements.exitPricePart,
      exitPriceShort: elements.exitShortPricePart,
      exitShort: elements.exitShortReasonPart,
      pnlLoss: elements.exitPnlLossPart,
      pnlWin: elements.exitPnlPart,
      separator: elements.exitSeparatorPart,
      size: elements.exitSizePart,
    };
    if (elements.entryLine.visible) {
      drawCanvasTradeLine(context, span.left, span.right, entryY, entryLineColor, annotation.selected ? Math.min(5, elements.entryLine.lineWidth + 1) : elements.entryLine.lineWidth, elements.entryLine.lineStyle, elements.entryLine.opacity);
    }
    if (elements.exitLine.visible && annotation.status !== "open" && exitY !== null) {
      drawCanvasTradeLine(context, span.left, span.right, exitY, exitLineColor, annotation.selected ? Math.min(5, elements.exitLine.lineWidth + 1) : elements.exitLine.lineWidth, elements.exitLine.lineStyle, elements.exitLine.opacity);
    }
    if (elements.entryArrow.visible) {
      if (elements.connector.visible) drawCanvasCandleConnector(context, priceSeries, candles, annotation.entryTime, entryX, entryY, entryArrowColor, elements.connector, settings.connectorThreshold);
      drawCanvasTradeArrow(context, entryX, entryY, entryArrowColor, "entry", annotation.selected === true, elements.entryArrow);
    }
    if (elements.entryLabel.visible) {
      drawCanvasTradeLabel(context, compactTradeLabel(annotation.entryLabelParts, annotation.entryLabel, "Entry"), entryX, entryY + elements.entryArrow.markerSize + 7, entryLabelColor, chartBackground, annotation.entryLabelSide ?? "left", width, height, elements.entryLabel, labelLayout, elements.connector, annotation.entryLabelParts, entryLabelPartSettings);
    }
    if (annotation.status !== "open" && exitY !== null) {
      if (elements.exitArrow.visible) {
        if (elements.connector.visible) drawCanvasCandleConnector(context, priceSeries, candles, annotation.exitTime ?? endTime, exitX, exitY, exitArrowColor, elements.connector, settings.connectorThreshold);
        drawCanvasTradeArrow(context, exitX, exitY, exitArrowColor, "exit", annotation.selected === true, elements.exitArrow);
      }
      if (elements.exitLabel.visible) drawCanvasTradeLabel(context, compactTradeLabel(annotation.exitLabelParts, annotation.exitLabel, "Exit"), exitX, exitY - elements.exitLabel.labelSize - elements.exitArrow.markerSize - 8, exitLabelColor, chartBackground, annotation.exitLabelSide ?? "right", width, height, elements.exitLabel, labelLayout, elements.connector, annotation.exitLabelParts, exitLabelPartSettings);
    }
    // Protection evidence is decision-critical. Paint it after lifecycle
    // labels so dense, fast entry/exit clusters cannot hide every SL/TP line.
    if ((elements.stopLine.visible || elements.stopLabel.visible) && typeof annotation.stopPrice === "number" && Number.isFinite(annotation.stopPrice)) {
      const y = priceSeries.priceToCoordinate(annotation.stopPrice);
      if (y !== null) drawCanvasTradeGuide(context, guideSpan.left, guideSpan.right, y, stopColor, "SL", chartBackground, width, height, elements.stopLine, elements.stopLabel, labelLayout, elements.connector);
    }
    if (elements.levelLine.visible || elements.levelLabel.visible) annotation.levelPrices?.slice(0, 3).forEach((price, index) => {
      const y = priceSeries.priceToCoordinate(price);
      if (y !== null) drawCanvasTradeGuide(context, guideSpan.left, guideSpan.right, y, neutralColor, `L${index + 1}`, chartBackground, width, height, elements.levelLine, elements.levelLabel, labelLayout, elements.connector);
    });
    if (elements.targetLine.visible || elements.targetLabel.visible) annotation.targetPrices?.forEach((price, index) => {
      const y = priceSeries.priceToCoordinate(price);
      if (y !== null) drawCanvasTradeGuide(context, guideSpan.left, guideSpan.right, y, successColor, annotation.targetPrices?.length === 1 ? "TP" : `TP${index + 1}`, chartBackground, width, height, elements.targetLine, elements.targetLabel, labelLayout, elements.connector);
    });
    if ((elements.levelLine.visible || elements.levelLabel.visible) && typeof annotation.triggerPrice === "number" && Number.isFinite(annotation.triggerPrice)) {
      const y = priceSeries.priceToCoordinate(annotation.triggerPrice);
      if (y !== null) drawCanvasTradeGuide(context, span.left, span.right, y, infoColor, "Trigger", chartBackground, width, height, elements.levelLine, elements.levelLabel, labelLayout, elements.connector);
    }
    if (elements.adjustmentLine.visible || elements.adjustmentArrow.visible || elements.adjustmentLabel.visible) annotation.fills?.forEach((fill) => {
      const x = xForAnnotationTime(chart, fill.time, timeline);
      const y = priceSeries.priceToCoordinate(fill.price);
      if (x === null || y === null || x < -70 || x > width + 70) return;
      const adjustmentSemanticColor = fill.kind === "stop_change" || fill.kind === "protective_stop" || fill.kind === "trailing_stop" || fill.kind === "protection_repair"
        ? stopColor
        : fill.kind === "profit_target" || fill.kind === "target_change"
          ? successColor
          : fill.kind === "add"
            ? infoColor
            : exitFallbackColor;
      const adjustmentArrowColor = strategyPresentationColor(elements.adjustmentArrow.color, adjustmentSemanticColor);
      if (elements.connector.visible && elements.adjustmentArrow.visible) drawCanvasCandleConnector(context, priceSeries, candles, fill.time, x, y, adjustmentArrowColor, elements.connector, settings.connectorThreshold);
      drawCanvasPositionAdjustment(context, x, y, fill, chartBackground, width, height, elements.adjustmentLine, elements.adjustmentArrow, elements.adjustmentLabel, { danger: stopColor, entry: infoColor, exit: exitFallbackColor, success: successColor }, labelLayout, elements.connector);
    });
  });
  if (elements.adjustmentArrow.visible) executions.forEach((fill) => {
    const x = xForAnnotationTime(chart, fill.time, timeline);
    const y = priceSeries.priceToCoordinate(fill.price);
    if (x === null || y === null || x < -20 || x > width + 20) return;
    const color = strategyPresentationColor(elements.adjustmentArrow.color, fill.side === "BUY" ? infoColor : exitFallbackColor);
    if (elements.connector.visible) drawCanvasCandleConnector(context, priceSeries, candles, fill.time, x, y, color, elements.connector, settings.connectorThreshold);
    drawCanvasTradeArrow(context, x, y, color, fill.side === "BUY" ? "entry" : "exit", false, elements.adjustmentArrow);
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
  lineStyle: LegendLineStyle = "solid",
  opacity = 0.88,
) {
  context.beginPath();
  context.setLineDash(canvasLineDash(lineStyle, width));
  context.strokeStyle = rgbaFromHex(color, opacity);
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
  settings: StrategyPresentationStyleSettings,
) {
  // The triangle tip is the exact event-time / execution-price coordinate.
  // Everything else extends away from the candle so the semantic anchor never
  // changes when the chart is panned, scaled, or rendered at another interval.
  const direction = kind === "entry" ? 1 : -1;
  const size = selected ? settings.markerSize + 2 : settings.markerSize;
  context.beginPath();
  context.moveTo(x, y);
  context.lineTo(x - size, y + direction * (size + 4));
  context.lineTo(x + size, y + direction * (size + 4));
  context.closePath();
  context.fillStyle = rgbaFromHex(color, settings.fillOpacity);
  context.fill();
  if (settings.borderWidth > 0) {
    context.setLineDash(canvasLineDash(settings.borderStyle, settings.borderWidth));
    context.strokeStyle = rgbaFromHex(color, settings.opacity);
    context.lineWidth = settings.borderWidth;
    context.stroke();
  }
}

function drawCanvasPositionAdjustment(
  context: CanvasRenderingContext2D,
  x: number,
  y: number,
  fill: TradeFillAnnotation,
  background: string,
  width: number,
  height: number,
  lineSettings: StrategyPresentationStyleSettings,
  arrowSettings: StrategyPresentationStyleSettings,
  labelSettings: StrategyPresentationStyleSettings,
  semanticColors: { danger: string; entry: string; exit: string; success: string },
  labelLayout?: CanvasAnnotationLayout,
  connectorSettings?: StrategyPresentationStyleSettings,
) {
  const semanticColor = fill.kind === "add"
    ? semanticColors.entry
    : fill.kind === "profit_target" || fill.kind === "target_change"
      ? semanticColors.success
      : fill.kind === "protective_stop" || fill.kind === "trailing_stop" || fill.kind === "stop_change" || fill.kind === "protection_repair"
        ? semanticColors.danger
        : semanticColors.exit;
  const lineColor = strategyPresentationColor(lineSettings.color, semanticColor);
  const arrowColor = strategyPresentationColor(arrowSettings.color, semanticColor);
  const labelColor = strategyPresentationColor(labelSettings.color, semanticColor);
  if (lineSettings.visible) drawCanvasTradeLine(context, x - 42, x, y, lineColor, lineSettings.lineWidth, lineSettings.lineStyle, lineSettings.opacity);
  if (arrowSettings.visible) {
    context.save();
    context.translate(x, y);
    context.rotate(Math.PI / 2);
    drawCanvasTradeArrow(context, 0, 0, arrowColor, "entry", false, arrowSettings);
    context.restore();
  }
  if (labelSettings.visible) drawCanvasTradeLabel(
    context,
    compactTradeLabel(fill.labelParts, fill.label, {
      add: "Add",
      profit_target: "Target",
      protective_stop: "Stop",
      trailing_stop: "Trail",
      position_exit: "Exit",
      stop_change: "SL",
      target_change: "TP",
      protection_repair: "Reconcile",
      entry_freeze: "Entry frozen",
    }[fill.kind ?? "position_exit"]),
    x - 45,
    y + 5,
    labelColor,
    background,
    "right",
    width,
    height,
    labelSettings,
    labelLayout,
    connectorSettings,
  );
}

function drawCanvasTradeGuide(
  context: CanvasRenderingContext2D,
  left: number,
  right: number,
  y: number,
  fallbackColor: string,
  label: string,
  background: string,
  width: number,
  height: number,
  lineSettings: StrategyPresentationStyleSettings,
  labelSettings: StrategyPresentationStyleSettings,
  labelLayout?: CanvasAnnotationLayout,
  connectorSettings?: StrategyPresentationStyleSettings,
) {
  const minimumWidth = Math.min(56, width);
  const rawLeft = Math.min(left, right);
  const rawRight = Math.max(left, right);
  const center = clampNumber((rawLeft + rawRight) / 2, 0, width, 0);
  let renderedLeft = Math.max(0, rawLeft);
  let renderedRight = Math.min(width, rawRight);
  if (renderedRight - renderedLeft < minimumWidth) {
    renderedLeft = clampNumber(center - minimumWidth / 2, 0, Math.max(0, width - minimumWidth), 0);
    renderedRight = Math.min(width, renderedLeft + minimumWidth);
  }
  const lineColor = strategyPresentationColor(lineSettings.color, fallbackColor);
  const labelColor = strategyPresentationColor(labelSettings.color, fallbackColor);
  context.save();
  // Entry/exit arrows retain exact event-time authority. Very short positions
  // receive a bounded guide footprint so their SL/TP evidence stays legible.
  if (lineSettings.visible) {
    drawCanvasTradeLine(context, renderedLeft, renderedRight, y, lineColor, lineSettings.lineWidth, lineSettings.lineStyle, lineSettings.opacity);
    const capRadius = Math.max(1.5, Math.min(3.5, lineSettings.lineWidth));
    context.fillStyle = rgbaFromHex(lineColor, lineSettings.opacity);
    context.fillRect(renderedLeft - capRadius, y - capRadius, capRadius * 2, capRadius * 2);
    context.fillRect(renderedRight - capRadius, y - capRadius, capRadius * 2, capRadius * 2);
  }
  context.restore();
  if (labelSettings.visible) drawCanvasTradeLabel(context, label, (renderedLeft + renderedRight) / 2, y + 3, labelColor, background, "left", width, height, labelSettings, labelLayout, connectorSettings);
}

type CanvasLabelBox = { bottom: number; left: number; right: number; top: number };
type CanvasAnnotationLayout = { boxes: CanvasLabelBox[] };

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
  settings: StrategyPresentationStyleSettings,
  layout?: CanvasAnnotationLayout,
  connectorSettings?: StrategyPresentationStyleSettings,
  parts?: TradeLabelPart[],
  partSettings?: TradeLabelPartSettings,
) {
  if (!text) return;
  const segmentInputs = parts?.length
    ? parts.map((part) => ({
      color: strategyPresentationColor(partSettings?.[part.tone ?? "label"]?.color ?? "", color),
      settings: partSettings?.[part.tone ?? "label"] ?? settings,
      text: part.text,
    }))
    : [{ color, settings, text }];
  const segments = segmentInputs
    .filter((segment) => segment.text && segment.settings.visible)
    .map((segment) => {
      context.font = `${segment.settings.fontWeight} ${segment.settings.labelSize}px ${canvasInterfaceFont()}`;
      return {
        ...segment,
        height: segment.settings.labelSize + segment.settings.labelPaddingY * 2,
        width: Math.ceil(context.measureText(segment.text).width) + segment.settings.labelPaddingX * 2,
      };
    });
  if (!segments.length) return;
  context.textBaseline = "middle";
  const labelWidth = segments.reduce((total, segment) => total + segment.width, 0);
  const labelHeight = Math.max(...segments.map((segment) => segment.height));
  const preferredLeft = side === "right" ? anchorX - labelWidth : anchorX;
  const preferredCenterY = top + labelHeight / 2;
  const horizontalCandidates = [preferredLeft, preferredLeft - labelWidth / 2, preferredLeft + labelWidth / 2];
  const verticalStep = labelHeight + 3;
  const verticalOffsets = Array.from({ length: 33 }, (_, index) => index === 0 ? 0 : Math.ceil(index / 2) * (index % 2 === 1 ? -1 : 1) * verticalStep);
  // Labels remain in the same chart-coordinate space as their price/time
  // anchors. Do not clamp them to the pane: canvas clipping lets them enter
  // and leave naturally as the user pans or rescales the chart.
  const candidates = verticalOffsets.flatMap((offset) => horizontalCandidates.map((left) => {
    const candidateTop = top + offset;
    return { bottom: candidateTop + labelHeight, left, right: left + labelWidth, top: candidateTop };
  }));
  const occupied = layout?.boxes ?? [];
  const box = candidates.find((candidate) => occupied.every((placed) => !canvasLabelBoxesOverlap(candidate, placed)))
    ?? candidates.reduce((best, candidate) => canvasLabelOverlapArea(candidate, occupied) < canvasLabelOverlapArea(best, occupied) ? candidate : best, candidates[0]);
  if (!box) return;
  if (box.right <= 0 || box.left >= width || box.bottom <= 0 || box.top >= height) return;
  layout?.boxes.push(box);
  const left = box.left;
  top = box.top;
  const labelCenterY = top + labelHeight / 2;
  if (connectorSettings?.visible !== false && (Math.abs(labelCenterY - preferredCenterY) > 4 || Math.abs(left - preferredLeft) > 4)) {
    const connectorX = clampNumber(anchorX, left, left + labelWidth, anchorX);
    const connectorColor = strategyPresentationColor(connectorSettings?.color ?? "", color);
    const connectorWidth = connectorSettings?.lineWidth ?? 1;
    context.save();
    context.beginPath();
    context.setLineDash(canvasLineDash(connectorSettings?.lineStyle ?? "dashed", connectorWidth));
    context.strokeStyle = rgbaFromHex(connectorColor, connectorSettings?.opacity ?? Math.min(0.8, settings.opacity));
    context.lineWidth = connectorWidth;
    context.moveTo(connectorX, labelCenterY < preferredCenterY ? top + labelHeight : top);
    context.lineTo(anchorX, preferredCenterY);
    context.stroke();
    context.restore();
  }
  const containerFillColor = strategyPresentationColor(settings.fillColor, background);
  context.fillStyle = rgbaFromHex(containerFillColor, settings.fillOpacity);
  context.fillRect(left, top, labelWidth, labelHeight);
  let segmentLeft = left;
  segments.forEach((segment) => {
    const segmentTop = top + (labelHeight - segment.height) / 2;
    const fillColor = strategyPresentationColor(segment.settings.fillColor, background);
    context.save();
    if (segment.settings.fillBlur > 0) context.filter = `blur(${segment.settings.fillBlur}px)`;
    context.fillStyle = rgbaFromHex(fillColor, segment.settings.fillOpacity);
    const blurInset = Math.min(segment.settings.fillBlur, Math.min(segment.width, segment.height) / 3);
    context.fillRect(segmentLeft + blurInset / 2, segmentTop + blurInset / 2, segment.width - blurInset, segment.height - blurInset);
    context.restore();
    context.font = `${segment.settings.fontWeight} ${segment.settings.labelSize}px ${canvasInterfaceFont()}`;
    context.fillStyle = rgbaFromHex(segment.color, segment.settings.opacity);
    context.fillText(segment.text, segmentLeft + segment.settings.labelPaddingX, segmentTop + segment.height / 2);
    segmentLeft += segment.width;
  });
  if (settings.borderWidth > 0 && settings.borderOpacity > 0) {
    context.save();
    const borderColor = strategyPresentationColor(settings.borderColor, color);
    context.setLineDash(canvasLineDash(settings.borderStyle, settings.borderWidth));
    context.strokeStyle = rgbaFromHex(borderColor, settings.borderOpacity);
    context.lineWidth = settings.borderWidth;
    context.strokeRect(left + settings.borderWidth / 2, top + settings.borderWidth / 2, labelWidth - settings.borderWidth, labelHeight - settings.borderWidth);
    context.restore();
  }
}

function canvasLabelBoxesOverlap(first: CanvasLabelBox, second: CanvasLabelBox) {
  const gap = 2;
  return first.left < second.right + gap && first.right + gap > second.left && first.top < second.bottom + gap && first.bottom + gap > second.top;
}

function canvasLabelOverlapArea(candidate: CanvasLabelBox, placed: CanvasLabelBox[]) {
  return placed.reduce((total, box) => total + Math.max(0, Math.min(candidate.right, box.right) - Math.max(candidate.left, box.left)) * Math.max(0, Math.min(candidate.bottom, box.bottom) - Math.max(candidate.top, box.top)), 0);
}

function drawCanvasCandleConnector(
  context: CanvasRenderingContext2D,
  priceSeries: ISeriesApi<"Candlestick">,
  candles: Candle[],
  time: number,
  x: number,
  eventY: number,
  fallbackColor: string,
  settings: StrategyPresentationStyleSettings,
  threshold: number,
) {
  if (!candles.length) return;
  const candle = candles[nearestCandleIndex(candles, time)];
  if (!candle) return;
  const highY = priceSeries.priceToCoordinate(candle.high);
  const lowY = priceSeries.priceToCoordinate(candle.low);
  if (highY === null || lowY === null) return;
  const color = strategyPresentationColor(settings.color, fallbackColor);
  const candleOffset = 5;
  let startY: number | null = null;
  let endY: number | null = null;
  if (eventY < highY - threshold) {
    startY = eventY + 7;
    endY = highY - candleOffset;
  } else if (eventY > lowY + threshold) {
    startY = eventY - 7;
    endY = lowY + candleOffset;
  }
  if (startY === null || endY === null) return;
  context.save();
  context.beginPath();
  context.setLineDash(canvasLineDash(settings.lineStyle, settings.lineWidth));
  context.strokeStyle = rgbaFromHex(color, settings.opacity);
  context.lineWidth = settings.lineWidth;
  context.moveTo(x, startY);
  context.lineTo(x, endY);
  context.stroke();
  context.restore();
}

function tradeAnnotationAutoscaleInfo(
  state: TradeAnnotationPrimitiveState,
  startLogical: number,
  endLogical: number,
): AutoscaleInfo | null {
  if (!state.timeline.length) return null;
  const visibleStart = Math.max(0, Math.floor(Math.min(startLogical, endLogical)));
  const visibleEnd = Math.min(state.timeline.length - 1, Math.ceil(Math.max(startLogical, endLogical)));
  if (visibleStart > visibleEnd) return null;
  const prices: number[] = [];
  state.trades.forEach((trade) => {
    const tradeStart = Math.max(0, lowerBoundCandleTime(state.timeline, Math.min(trade.guideStartTime ?? trade.entryTime, trade.entryTime)) - 1);
    const tradeEnd = trade.status === "open"
      ? visibleEnd
      : Math.min(state.timeline.length - 1, lowerBoundCandleTime(state.timeline, trade.endTime ?? trade.exitTime ?? trade.entryTime));
    if (tradeEnd < visibleStart || tradeStart > visibleEnd) return;
    // Autoscale evidence is data authority, not presentation state. Keeping the
    // same evidence set when an element is styled or hidden prevents a visual
    // configuration edit from changing the current price viewport.
    prices.push(trade.entryPrice);
    if (trade.status !== "open" && typeof trade.exitPrice === "number") prices.push(trade.exitPrice);
    prices.push(...(trade.levelPrices?.slice(0, 3) ?? []));
    if (typeof trade.triggerPrice === "number") prices.push(trade.triggerPrice);
    if (typeof trade.stopPrice === "number") prices.push(trade.stopPrice);
    prices.push(...(trade.targetPrices ?? []));
    prices.push(...(trade.fills?.map((fill) => fill.price) ?? []));
  });
  state.executions.forEach((fill) => {
    const logical = lowerBoundCandleTime(state.timeline, fill.time);
    if (logical >= visibleStart && logical <= visibleEnd) prices.push(fill.price);
  });
  const finitePrices = prices.filter((price) => Number.isFinite(price));
  if (!finitePrices.length) return null;
  return { priceRange: { minValue: Math.min(...finitePrices), maxValue: Math.max(...finitePrices) } };
}

function strategyPresentationColor(value: string, fallback: string) {
  return validHexColor(value, validHexColor(fallback, "#111827"));
}

function chartSemanticColor(property: string, fallback: string) {
  if (typeof window === "undefined") return fallback;
  return validHexColor(window.getComputedStyle(document.documentElement).getPropertyValue(property).trim(), fallback);
}

function compactTradeLabel(parts: TradeLabelPart[] | undefined, fallback: string | undefined, defaultLabel: string) {
  const fromParts = parts?.map((part) => part.text.trim()).filter(Boolean).join(" ");
  return fromParts || fallback || defaultLabel;
}

function xForAnnotationTime(chart: IChartApi, time: number, candles: Array<{ time: number }>) {
  if (!candles.length) return null;
  const candleDuration = estimateCandleDuration(candles);
  if (time < candles[0].time || time >= candles[candles.length - 1].time + candleDuration) return null;
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
  if (leftIndex === candles.length - 1) {
    return chart.timeScale().timeToCoordinate(candles[leftIndex].time as Time);
  }
  return null;
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

function drawTimelineEvents(chart: IChartApi, layer: HTMLDivElement | null, events: ChartTimelineEvent[]) {
  if (!layer) return;
  const eventsById = new Map(events.map((event) => [event.id, event]));
  const stackByTime = new Map<number, number>();
  Array.from(layer.querySelectorAll<HTMLElement>("[data-chart-timeline-event-id]")).forEach((node) => {
    const event = eventsById.get(node.dataset.chartTimelineEventId || "");
    const coordinate = event ? chart.timeScale().timeToCoordinate(event.time as Time) : null;
    if (!event || !isVisibleCoordinate(coordinate, layer.clientWidth)) {
      node.style.visibility = "hidden";
      return;
    }
    const stack = stackByTime.get(event.time) ?? 0;
    stackByTime.set(event.time, stack + 1);
    node.style.left = `${Number(coordinate)}px`;
    node.style.bottom = `${chart.timeScale().height() + 6 + stack * 26}px`;
    node.style.visibility = "visible";
  });
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

function nearestCandleIndex(candles: Array<{ time: number }>, targetTime: number) {
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

function estimateCandleDuration(candles: Array<{ time: number }>) {
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
