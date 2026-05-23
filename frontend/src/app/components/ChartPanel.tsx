import {
  type AutoscaleInfo,
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
  type ISeriesApi,
  type LineWidth,
  type LogicalRange,
  type MouseEventParams,
  type SeriesMarker,
  type SeriesType,
  type Time
} from "lightweight-charts";
import {
  CalendarRange,
  ChartNoAxesCombined,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  Eye,
  EyeOff,
  LocateFixed,
  Maximize2,
  Minimize2,
  Settings,
  SlidersHorizontal,
  X
} from "lucide-react";
import { forwardRef, type FormEvent, type ReactNode, useEffect, useImperativeHandle, useMemo, useRef, useState } from "react";

import { displayName } from "../format";
import { buildSegmentButtonClassName } from "../selectionStyles";

type Candle = { time: number; open: number; high: number; low: number; close: number };
type ChartSeries = {
  bandFillColor?: string;
  bandFillOpacity?: number;
  chartRole?: string;
  column: string;
  displayItemId?: string;
  label: string;
  paneKey?: string;
  style: "line" | "histogram";
  color: string;
  legend?: boolean;
  lineStyle?: "solid" | "dashed" | "dotted";
  lineWidth: number;
  opacity?: number;
  data: Array<{ color?: string; time: number; value: number }>;
};
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
  borderColor?: string;
  borderOpacity?: number;
  borderStyle?: string;
  borderWidth?: number;
  color: string;
  displayItemId?: string;
  end: number;
  fillColor?: string;
  fillOpacity?: number;
  label: string;
  lower: number;
  maxPixelHeight?: number;
  minPixelHeight?: number;
  start: number;
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
  shortDescription?: string;
  detailedDescription?: string;
  theory?: string;
  interpretation?: string;
  caveats?: string[];
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
type OscillatorPaneRuntime = {
  chart: IChartApi;
  primaryKey: string;
  renderer: AnySeriesApi | null;
  seriesKeys: Set<string>;
  timelineRenderer: AnySeriesApi | null;
  timelineSignature: string;
  valuesByTime: Map<number, number>;
  zeroLine: IPriceLine | null;
  zeroLineRenderer: AnySeriesApi | null;
  zeroLineSeriesKey: string;
};
type OscillatorPaneGroup = {
  key: string;
  series: ChartSeries[];
};
type LegendLineStyle = "solid" | "dashed" | "dotted";
type LegendSeriesSettings = {
  color?: string;
  lineStyle?: LegendLineStyle;
  lineWidth?: number;
  showValue?: boolean;
  visible?: boolean;
};
type LegendSettingsMap = Record<string, LegendSeriesSettings>;
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
  featureOptions: string[];
  indicatorOptions: string[];
  labelOptions?: ChartLabelOption[];
  loading?: boolean;
  normalizeTicker?: boolean;
  onPeriodChange?: (start: string, end: string) => void;
  onTickerChange: (value: string) => void;
  onTimeframeChange: (value: string) => void;
  onVisibleColumnsChange: (value: string[]) => void;
  onVisibleSupervisionGroupsChange?: (value: string[]) => void;
  onLiveEntryClose?: () => void;
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
  ticker: string;
  tickerInputWidth?: number | string;
  tickerMaxLength?: number;
  timeframe: string;
  timeframes: string[];
  visibleColumns: string[];
  visibleSupervisionGroups?: string[];
};

const defaultChartAppearanceSettings: ChartAppearanceSettings = {
  afterHoursColor: "#BFDBFE",
  afterHoursOpacity: 0.24,
  borderDownColor: "#CB093F",
  borderUpColor: "#1DB914",
  borderVisible: true,
  candleSize: 40,
  daySeparatorColor: "#94A3B8",
  daySeparatorStyle: "dashed",
  daySeparatorsVisible: true,
  downColor: "#FD0E50",
  premarketColor: "#FBBF24",
  premarketOpacity: 0.22,
  upColor: "#33E42A",
  wickUpColor: "#4DC746",
  wickDownColor: "#C52A55",
  wickVisible: true
};

const LEGEND_SETTINGS_STORAGE_KEY = "quant-research-workbench.chart.legend-settings.v1";
const CHART_APPEARANCE_STORAGE_KEY = "quant-research-workbench.chart.appearance-settings.v1";
const CHART_PRICE_SCALE_MIN_WIDTH = 84;

type ChartPalette = {
  background: string;
  grid: string;
  text: string;
};

export const ChartPanel = forwardRef<ChartPanelHandle, ChartPanelProps>(({
  catalogColumns = [],
  displayItemOptions = [],
  emptyMessage = "No chart data for the selected ticker/date range/timeframe.",
  errorMessage,
  featureOptions,
  indicatorOptions,
  labelOptions = [],
  loading = false,
  normalizeTicker = true,
  onPeriodChange,
  onTickerChange,
  onTimeframeChange,
  onVisibleColumnsChange,
  onVisibleSupervisionGroupsChange,
  onLiveEntryClose,
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
  ticker,
  tickerInputWidth,
  tickerMaxLength = 10,
  timeframe,
  timeframes,
  visibleColumns,
  visibleSupervisionGroups = []
}, ref) => {
  const priceRef = useRef<HTMLDivElement | null>(null);
  const oscillatorPaneRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const shellRef = useRef<HTMLDivElement | null>(null);
  const priceLayerRef = useRef<HTMLDivElement | null>(null);
  const referenceLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const oscillatorChartRefs = useRef<Map<string, IChartApi>>(new Map());
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const indicatorSeriesRef = useRef<Map<string, AnySeriesApi>>(new Map());
  const indicatorSourceRef = useRef<Map<string, ChartSeries>>(new Map());
  const oscillatorPaneRuntimesRef = useRef<Map<string, OscillatorPaneRuntime>>(new Map());
  const payloadRef = useRef<ChartPayload | null>(payload);
  const visibleSelectionRef = useRef<Set<string>>(new Set());
  const chartSettingsRef = useRef<ChartAppearanceSettings>(defaultChartAppearanceSettings);
  const resizeObserverRef = useRef<ResizeObserver | null>(null);
  const initialFitTimerRef = useRef<number | null>(null);
  const rangeCleanupRef = useRef<(() => void) | null>(null);
  const crosshairCleanupRef = useRef<(() => void) | null>(null);
  const overlayInteractionCleanupRef = useRef<(() => void) | null>(null);
  const overlayRedrawFrameRef = useRef<number | null>(null);
  const overlayRedrawTimerRef = useRef<number | null>(null);
  const regionDrawRef = useRef<(() => void) | null>(null);
  const fittedChartKeyRef = useRef("");
  const normalizeTickerValue = (value: string) => (normalizeTicker ? value.toUpperCase() : value);
  const [draftTicker, setDraftTicker] = useState(normalizeTickerValue(ticker));
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const [supervisionMenuOpen, setSupervisionMenuOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const [chartSettings, setChartSettings] = useState<ChartAppearanceSettings>(() => loadChartAppearanceSettings());
  const [legendSettings, setLegendSettings] = useState<LegendSettingsMap>(() => loadLegendSettings());
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
  const displayedOscillatorSeries = (payload?.oscillator_series ?? []).filter((series) => visibleColumnLookup.has(seriesSelectionKey(series)));
  const oscillatorPaneGroups = buildOscillatorPaneGroups(displayedOscillatorSeries);
  const priceLegendItems = buildSeriesLegendItems(displayedOverlaySeries, "price", legendSettings);
  const hasChartData = Boolean(payload?.candles.length);
  const referenceKey = reference ? `${reference.time ?? ""}:${reference.startTime ?? ""}:${reference.endTime ?? ""}:${reference.sessionDate ?? ""}:${reference.minuteOfDay ?? ""}:${reference.label ?? ""}` : "";
  const liveEntryLineKey = liveEntryLine ? `${liveEntryLine.price}:${liveEntryLine.quantity}:${liveEntryLine.pnl}:${liveEntryLine.color}` : "";
  const liveEntryLineForDraw = liveEntryLine ? { ...liveEntryLine, onClose: onLiveEntryClose } : null;

  const updateChartSettings = <K extends keyof ChartAppearanceSettings>(key: K, value: ChartAppearanceSettings[K]) => {
    setChartSettings((current) => {
      const next = normalizeChartAppearanceSettings({ ...current, [key]: value });
      saveChartAppearanceSettings(next);
      return next;
    });
  };

  const resetChartSettings = () => {
    const next = { ...defaultChartAppearanceSettings };
    saveChartAppearanceSettings(next);
    setChartSettings(next);
  };

  const updateLegendSettings = (key: string, patch: LegendSeriesSettings) => {
    setLegendSettings((current) => {
      const next = { ...current, [key]: { ...(current[key] ?? {}), ...patch } };
      saveLegendSettings(next);
      return next;
    });
  };

  const resetLegendSettings = (key: string) => {
    setLegendSettings((current) => {
      const next = { ...current };
      delete next[key];
      saveLegendSettings(next);
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

  useImperativeHandle(ref, () => ({
    fitFirstDay() {
      fitFirstDay(priceChartRef.current, fitCandles(payload));
    },
    fitRecent() {
      fitReferenceOrRecent(priceChartRef.current, fitCandles(payload), reference, timeframe);
    },
    toggleFullscreen() {
      setFullscreen((value) => !value);
      window.setTimeout(() => resizeCharts(), 30);
    }
  }));

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
      if (target?.closest(".chart-column-select") || target?.closest(".chart-period-select")) return;
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
      applySeriesSettings(renderer, source, settings, key.startsWith("oscillator:"));
    });
  }, [legendSettings]);

  useEffect(() => {
    chartSettingsRef.current = effectiveChartSettings;
    applyChartAppearance();
  }, [effectiveChartSettings, themeSignature]);

  useEffect(() => {
    if (!hasChartData) {
      cleanupChartRuntime();
      return undefined;
    }
    if (!priceRef.current || priceChartRef.current) return undefined;
    const palette = readChartPalette();
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current));
    priceChartRef.current = priceChart;
    const candleSeries = priceChart.addCandlestickSeries({
      ...candleSeriesOptions(chartSettingsRef.current),
      autoscaleInfoProvider: padFlatAutoscale,
      priceLineVisible: true
    });
    candleRef.current = candleSeries;
    const volume = priceChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", base: 0 });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volumeRef.current = volume;
    const draw = () => scheduleOverlayRedraw();
    regionDrawRef.current = draw;
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    const observer = new ResizeObserver(() => {
      resizeCharts();
      drawCurrentRegions();
    });
    if (shellRef.current) observer.observe(shellRef.current);
    resizeObserverRef.current = observer;
    overlayInteractionCleanupRef.current = attachOverlayRedrawListeners(priceRef.current, scheduleOverlayRedraw, scheduleOverlayRedrawBurst);
    return () => cleanupChartRuntime();
  }, [hasChartData]);

  useEffect(() => {
    payloadRef.current = payload;
    if (!payload || !priceChartRef.current || !candleRef.current || !volumeRef.current) return;
    const fitKey = buildChartFitKey(ticker, timeframe, referenceKey, payload.candles);
    const shouldAutoFit = fitKey !== fittedChartKeyRef.current;
    const currentRange = shouldAutoFit ? null : priceChartRef.current.timeScale().getVisibleLogicalRange();
    const timeline = chartTimelineData(payload.candles, timeframe);
    candleRef.current.setData(timeline as never);
    volumeRef.current.setData(volumeDataForSettings(payload, chartSettingsRef.current) as never);
    updateCandleMarkers();
    if (shouldAutoFit) {
      fittedChartKeyRef.current = fitKey;
      if (initialFitTimerRef.current !== null) {
        window.clearTimeout(initialFitTimerRef.current);
      }
      initialFitTimerRef.current = window.setTimeout(() => {
        const currentPayload = payloadRef.current;
        if (!currentPayload || !priceChartRef.current) return;
        if (reference) {
          fitAroundReference(priceChartRef.current, currentPayload.candles, reference, timeframe);
        } else {
          fitInitialRange(priceChartRef.current, currentPayload.candles);
        }
        drawCurrentRegions();
        initialFitTimerRef.current = null;
      }, 20);
    } else {
      if (currentRange) priceChartRef.current.timeScale().setVisibleLogicalRange(currentRange);
      drawCurrentRegions();
    }
    refreshInteractionSync();
  }, [payload, reference, referenceKey, ticker, timeframe]);

  useEffect(() => {
    if (!priceChartRef.current || !payload?.candles.length || !reference) return;
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
    refreshInteractionSync();
  }, [payload, visibleColumnKey, timeframe]);

  function applyChartAppearance() {
    const palette = readChartPalette();
    const priceChart = priceChartRef.current;
    if (priceChart && priceRef.current) {
      priceChart.applyOptions(chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettingsRef.current));
      candleRef.current?.applyOptions(candleSeriesOptions(chartSettingsRef.current));
      if (payloadRef.current && volumeRef.current) {
        volumeRef.current.setData(volumeDataForSettings(payloadRef.current, chartSettingsRef.current) as never);
      }
    }
    oscillatorChartRefs.current.forEach((chart, key) => {
      const pane = oscillatorPaneRefs.current.get(key);
      if (pane) chart.applyOptions(chartOptions(pane.clientWidth, pane.clientHeight, false, palette, chartSettingsRef.current));
    });
    drawCurrentRegions();
  }

  function updateCandleMarkers() {
    const candleSeries = candleRef.current;
    const currentPayload = payloadRef.current;
    if (!candleSeries) return;
    if (!currentPayload) {
      candleSeries.setMarkers([]);
      return;
    }
    candleSeries.setMarkers(markersForSelection(currentPayload.markers, visibleSelectionRef.current));
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
        applySeriesSettings(existing, series, settings, false);
      } else {
        const renderer = priceChart.addLineSeries({
          color: seriesColorWithOpacity(series, settings.color),
          lineStyle: toChartLineStyle(settings.lineStyle),
          lineWidth: toLineWidth(settings.lineWidth),
          autoscaleInfoProvider: () => null,
          priceLineVisible: false,
          title: series.label,
          visible: settings.visible
        });
        renderer.setData(seriesDataForSettings(series, settings) as never);
        indicatorSeriesRef.current.set(key, renderer);
      }
      indicatorSourceRef.current.set(key, series);
    });
  }

  function updateOscillatorPanes(groups: OscillatorPaneGroup[]) {
    const nextPaneKeys = new Set(groups.map((group) => group.key));
    Array.from(oscillatorPaneRuntimesRef.current.keys()).forEach((key) => {
      if (!nextPaneKeys.has(key)) removeOscillatorPaneRuntime(key);
    });
    groups.forEach((group) => {
      const pane = oscillatorPaneRefs.current.get(group.key);
      if (!pane) return;
      let runtime = oscillatorPaneRuntimesRef.current.get(group.key);
      if (!runtime) {
        const chart = createChart(pane, chartOptions(pane.clientWidth, pane.clientHeight, false, readChartPalette(), chartSettingsRef.current));
        runtime = {
          chart,
          primaryKey: "",
          renderer: null,
          seriesKeys: new Set<string>(),
          timelineRenderer: null,
          timelineSignature: "",
          valuesByTime: new Map<number, number>(),
          zeroLine: null,
          zeroLineRenderer: null,
          zeroLineSeriesKey: ""
        };
        oscillatorPaneRuntimesRef.current.set(group.key, runtime);
        oscillatorChartRefs.current.set(group.key, chart);
      }
      updateOscillatorPaneTimeline(runtime, chartTimelineData(payloadRef.current?.candles ?? [], timeframe));
      updateOscillatorPaneSeries(runtime, group.series);
    });
  }

  function updateOscillatorPaneTimeline(runtime: OscillatorPaneRuntime, timeline: CandleSeriesDatum[]) {
    const signature = buildTimelineDataSignature(timeline);
    if (!runtime.timelineRenderer) {
      runtime.timelineRenderer = runtime.chart.addLineSeries({
        autoscaleInfoProvider: () => null,
        color: "rgba(0, 0, 0, 0)",
        crosshairMarkerVisible: false,
        lastValueVisible: false,
        lineWidth: 1,
        priceLineVisible: false,
        visible: true,
        title: "",
      });
    }
    if (runtime.timelineSignature === signature) return;
    runtime.timelineRenderer.setData(timeline.map((item) => ({ time: item.time, value: 0 })) as never);
    runtime.timelineSignature = signature;
  }

  function updateOscillatorPaneSeries(runtime: OscillatorPaneRuntime, seriesList: ChartSeries[]) {
    const nextKeys = new Set(seriesList.map((series) => legendSeriesKey("oscillator", series)));
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
        runtime.chart.removeSeries(renderer);
      }
      runtime.seriesKeys.delete(key);
      indicatorSeriesRef.current.delete(key);
      indicatorSourceRef.current.delete(key);
    });
    let primaryRenderer: AnySeriesApi | null = null;
    let primaryKey = "";
    let primaryValuesByTime = new Map<number, number>();
    seriesList.forEach((series) => {
      const key = legendSeriesKey("oscillator", series);
      const settings = resolveLegendSettings(legendSettings, key, series);
      let renderer = indicatorSeriesRef.current.get(key);
      if (renderer) {
        applySeriesSettings(renderer, series, settings, true);
      } else {
        renderer = addChartSeries(runtime.chart, series, settings);
        renderer.setData(seriesDataForSettings(series, settings) as never);
        indicatorSeriesRef.current.set(key, renderer);
      }
      indicatorSourceRef.current.set(key, series);
      runtime.seriesKeys.add(key);
      if (!primaryRenderer) {
        primaryRenderer = renderer;
        primaryKey = key;
        primaryValuesByTime = new Map(series.data.map((point) => [point.time, point.value]));
      }
    });
    if (primaryRenderer) {
      runtime.primaryKey = primaryKey;
      runtime.renderer = primaryRenderer;
      runtime.valuesByTime = primaryValuesByTime;
      syncOscillatorZeroLine(runtime, primaryRenderer, primaryKey);
    }
  }

  function syncOscillatorZeroLine(runtime: OscillatorPaneRuntime, renderer: AnySeriesApi, seriesKey: string) {
    if (runtime.zeroLine && runtime.zeroLineSeriesKey !== seriesKey && runtime.zeroLineRenderer) {
      runtime.zeroLineRenderer.removePriceLine(runtime.zeroLine);
      runtime.zeroLine = null;
      runtime.zeroLineRenderer = null;
      runtime.zeroLineSeriesKey = "";
    }
    if (!runtime.zeroLine) {
      runtime.zeroLine = renderer.createPriceLine({
        axisLabelVisible: false,
        color: "#000000",
        lineStyle: LineStyle.Solid,
        lineVisible: true,
        lineWidth: 1 as LineWidth,
        price: 0,
        title: ""
      });
      runtime.zeroLineRenderer = renderer;
      runtime.zeroLineSeriesKey = seriesKey;
    } else {
      runtime.zeroLine.applyOptions({
        axisLabelVisible: false,
        color: "#000000",
        lineStyle: LineStyle.Solid,
        lineVisible: true,
        lineWidth: 1 as LineWidth,
        price: 0,
        title: ""
      });
    }
  }

  function removeOscillatorPaneRuntime(key: string) {
    const runtime = oscillatorPaneRuntimesRef.current.get(key);
    if (!runtime) return;
    if (runtime.zeroLine && runtime.zeroLineRenderer) {
      runtime.zeroLineRenderer.removePriceLine(runtime.zeroLine);
    }
    runtime.seriesKeys.forEach((seriesKey) => {
      indicatorSeriesRef.current.delete(seriesKey);
      indicatorSourceRef.current.delete(seriesKey);
    });
    runtime.timelineRenderer = null;
    runtime.chart.remove();
    oscillatorPaneRuntimesRef.current.delete(key);
    oscillatorChartRefs.current.delete(key);
  }

  function refreshInteractionSync() {
    rangeCleanupRef.current?.();
    crosshairCleanupRef.current?.();
    rangeCleanupRef.current = null;
    crosshairCleanupRef.current = null;
    const priceChart = priceChartRef.current;
    const candleSeries = candleRef.current;
    const currentPayload = payloadRef.current;
    if (!priceChart || !candleSeries || !currentPayload) return;
    const panes = Array.from(oscillatorPaneRuntimesRef.current.values());
    rangeCleanupRef.current = syncChartRanges([priceChart, ...panes.map((pane) => pane.chart)]);
    const closeByTime = new Map(currentPayload.candles.map((candle) => [candle.time, candle.close]));
    crosshairCleanupRef.current = syncCrosshairs(priceChart, panes, candleSeries, closeByTime);
  }

  function drawCurrentRegions() {
    const chart = priceChartRef.current;
    const currentPayload = payloadRef.current;
    if (!chart || !currentPayload) return;
    const selectedZones = (currentPayload.price_zones ?? []).filter((zone) => !zone.displayItemId || visibleSelectionRef.current.has(zone.displayItemId.toLowerCase()));
    drawRegions(chart, candleRef.current, priceLayerRef.current, currentPayload.regions, selectedZones, currentPayload.trade_annotations ?? [], currentPayload.candles, chartSettingsRef.current, liveEntryLineForDraw);
    drawReferenceLine(chart, referenceLayerRef.current, currentPayload.candles, showReferenceLine ? reference : null);
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
    let remainingTicks = 12;
    const tick = () => {
      scheduleOverlayRedraw();
      remainingTicks -= 1;
      overlayRedrawTimerRef.current = remainingTicks > 0 ? window.setTimeout(tick, 16) : null;
    };
    overlayRedrawTimerRef.current = window.setTimeout(tick, 16);
  }

  function cleanupChartRuntime() {
    if (initialFitTimerRef.current !== null) {
      window.clearTimeout(initialFitTimerRef.current);
      initialFitTimerRef.current = null;
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
    rangeCleanupRef.current?.();
    crosshairCleanupRef.current?.();
    rangeCleanupRef.current = null;
    crosshairCleanupRef.current = null;
    if (regionDrawRef.current && priceChartRef.current) {
      priceChartRef.current.timeScale().unsubscribeVisibleLogicalRangeChange(regionDrawRef.current);
      regionDrawRef.current = null;
    }
    oscillatorPaneRuntimesRef.current.forEach((runtime) => runtime.chart.remove());
    oscillatorPaneRuntimesRef.current.clear();
    oscillatorChartRefs.current.clear();
    if (priceChartRef.current) {
      priceChartRef.current.remove();
    }
    priceChartRef.current = null;
    candleRef.current = null;
    volumeRef.current = null;
    indicatorSeriesRef.current.clear();
    indicatorSourceRef.current.clear();
    fittedChartKeyRef.current = "";
  }

  function resizeCharts() {
    const price = priceRef.current;
    if (price && priceChartRef.current) {
      priceChartRef.current.applyOptions({ width: price.clientWidth, height: price.clientHeight });
    }
    oscillatorChartRefs.current.forEach((chart, key) => {
      const pane = oscillatorPaneRefs.current.get(key);
      if (pane) chart.applyOptions({ width: pane.clientWidth, height: pane.clientHeight });
    });
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
    <div className={fullscreen ? "chart-shell fullscreen" : "chart-shell"} ref={shellRef}>
      <div className="chart-component-toolbar">
        <form className="chart-ticker-form" onSubmit={commitTicker}>
          <input
            aria-label="Ticker"
            className="chart-ticker-input"
            maxLength={tickerMaxLength}
            onChange={(event) => setDraftTicker(normalizeTickerValue(event.target.value))}
            spellCheck={false}
            style={{ textTransform: normalizeTicker ? "uppercase" : "none", width: tickerInputWidth }}
            value={draftTicker}
          />
        </form>
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
              {item}
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
        <button className="toolbar-button" type="button" title="Fit first day" onClick={() => fitFirstDay(priceChartRef.current, fitCandles(payload))}><CalendarRange size={15} /></button>
        <button className="toolbar-button" type="button" title={reference ? "Fit selected trade" : "Fit recent"} onClick={() => fitReferenceOrRecent(priceChartRef.current, fitCandles(payload), reference, timeframe)}><LocateFixed size={15} /></button>
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
          {errorMessage ? <div className="chart-update-status error">Chart update failed</div> : null}
          <div className="chart-reference-stack-layer" ref={referenceLayerRef} />
          <div className="chart-price">
            <div className="chart-pane-canvas" ref={priceRef} />
            <div className="session-layer" ref={priceLayerRef} />
            <ChartLegend
              indicatorCount={displayedOverlaySeries.length}
              items={priceLegendItems}
              onReset={resetLegendSettings}
              onUpdate={updateLegendSettings}
            />
          </div>
          {oscillatorPaneGroups.map((group) => {
            return (
              <div className="chart-osc" key={group.key}>
                <div className="chart-pane-canvas" ref={(node) => setOscillatorPaneRef(group.key, node)} />
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
                  items={buildSeriesLegendItems(group.series, "oscillator", legendSettings)}
                  onReset={resetLegendSettings}
                  onUpdate={updateLegendSettings}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
});

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
  key: string;
  label: string;
  lineStyle: LegendLineStyle;
  lineWidth: number;
  seriesStyle: "candlestick" | "histogram" | "line";
  showValue: boolean;
  value: string;
  visible: boolean;
};

function ChartLegend({
  indicatorCount,
  items,
  onReset,
  onUpdate
}: {
  indicatorCount: number;
  items: LegendItem[];
  onReset: (key: string) => void;
  onUpdate: (key: string, patch: LegendSeriesSettings) => void;
}) {
  const [collapsed, setCollapsed] = useState(true);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  if (!items.length) return null;
  const editingItem = items.find((item) => item.key === editingKey && item.configurable);
  return (
    <div className={collapsed ? "chart-legend collapsed" : "chart-legend"}>
      <button
        aria-label={collapsed ? "Expand legend" : "Collapse legend"}
        className="chart-legend-header"
        onClick={() => setCollapsed((value) => !value)}
        type="button"
      >
        {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        <b>{formatIndicatorCount(indicatorCount)}</b>
      </button>
      {!collapsed ? (
        <>
          <div className="chart-legend-rows">
            {items.map((item) => (
              <div className={item.visible ? "chart-legend-row" : "chart-legend-row muted"} key={item.key}>
                <span className={item.seriesStyle === "histogram" ? "legend-swatch histogram" : `legend-swatch ${item.lineStyle}`} style={{ color: item.color }}>
                  <i style={{ background: item.color }} />
                </span>
                <span className="legend-label">{item.label}</span>
                {item.showValue && item.visible ? <span className="legend-value" style={{ color: item.color }}>{item.value}</span> : null}
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
                    <button
                      aria-label={`Configure ${item.label}`}
                      onClick={() => setEditingKey((value) => (value === item.key ? null : item.key))}
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
              item={editingItem}
              onClose={() => setEditingKey(null)}
              onReset={() => onReset(editingItem.key)}
              onUpdate={(patch) => onUpdate(editingItem.key, patch)}
            />
          ) : null}
        </>
      ) : null}
    </div>
  );
}

function LegendEditor({
  item,
  onClose,
  onReset,
  onUpdate
}: {
  item: LegendItem;
  onClose: () => void;
  onReset: () => void;
  onUpdate: (patch: LegendSeriesSettings) => void;
}) {
  return (
    <div className="chart-legend-editor">
      <div className="chart-legend-editor-header">
        <span>{item.label}</span>
        <button aria-label="Close indicator settings" onClick={onClose} title="Close" type="button">
          <X size={13} />
        </button>
      </div>
      <label>
        Color
        <input type="color" value={item.color} onChange={(event) => onUpdate({ color: event.target.value })} />
      </label>
      {item.seriesStyle === "line" ? (
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
      <label className="legend-checkbox">
        <input checked={item.showValue} type="checkbox" onChange={(event) => onUpdate({ showValue: event.target.checked })} />
        Value in legend
      </label>
      <button className="legend-reset-button" onClick={onReset} type="button">Reset</button>
    </div>
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
        title="Indicators & Features"
        type="button"
      >
        <ChartNoAxesCombined size={19} />
        <span>Indicators &amp; Features</span>
        {selectedCount ? <b>{selectedCount}</b> : null}
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="chart-column-menu">
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
        </div>
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
        title="Lookahead & Supervision"
        type="button"
      >
        <Eye size={18} />
        <span>Supervision</span>
        {selectedCount ? <b>{selectedCount}</b> : null}
        <ChevronDown size={14} />
      </button>
      {open ? (
        <div className="chart-column-menu chart-supervision-menu">
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
        </div>
      ) : null}
    </div>
  );
}

type ChartColumnHelp = {
  caveats: string[];
  detail?: string;
  futureLooking: boolean;
  summary: string;
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
      {helpOpen ? (
        <div className="chart-column-help-panel">
          {help.futureLooking ? <b>Uses lookahead. This is derived from future bars and should not be used as a live tradable signal.</b> : null}
          <p>{help.summary}</p>
          {help.detail ? <p>{help.detail}</p> : null}
          {help.caveats.length ? (
            <ul>
              {help.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  );
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
    caveats: (knowledge?.caveats ?? []).map(compactHelpText).filter(Boolean).slice(0, 2),
    detail: detailed && detailed !== summary ? detailed : undefined,
    futureLooking: futureLooking || chartMenuItemUsesLookahead(source),
    summary,
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

function buildSeriesLegendItems(series: ChartSeries[], pane: LegendPane, settingsMap: LegendSettingsMap): LegendItem[] {
  return series.filter((item) => item.legend !== false).map((item) => {
    const key = legendSeriesKey(pane, item);
    const settings = resolveLegendSettings(settingsMap, key, item);
    const latest = latestSeriesValue(item.data);
    return {
      color: settings.color,
      configurable: true,
      key,
      label: item.label,
      lineStyle: settings.lineStyle,
      lineWidth: settings.lineWidth,
      seriesStyle: item.style,
      showValue: settings.showValue,
      value: latest === null ? "-" : formatPrice(latest),
      visible: settings.visible
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
  if (group.key === "oscillator:macd") return "MACD Pane";
  if (group.key === "oscillator:pane_2") return "Pane 2";
  if (group.key === "oscillator:pane_3") return "Pane 3";
  if (group.series.length === 1) return group.series[0].label;
  return `${group.series.length} indicators`;
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

function seriesSelectionKey(series: ChartSeries) {
  return String(series.displayItemId || series.column || series.label).toLowerCase();
}

function loadLegendSettings(): LegendSettingsMap {
  if (typeof window === "undefined") return {};
  try {
    const raw = window.localStorage.getItem(LEGEND_SETTINGS_STORAGE_KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as LegendSettingsMap;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function saveLegendSettings(settings: LegendSettingsMap) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(LEGEND_SETTINGS_STORAGE_KEY, JSON.stringify(settings));
}

function loadChartAppearanceSettings(): ChartAppearanceSettings {
  if (typeof window === "undefined") return { ...defaultChartAppearanceSettings };
  try {
    const raw = window.localStorage.getItem(CHART_APPEARANCE_STORAGE_KEY);
    if (!raw) return { ...defaultChartAppearanceSettings };
    return normalizeChartAppearanceSettings(JSON.parse(raw) as Partial<ChartAppearanceSettings>);
  } catch {
    return { ...defaultChartAppearanceSettings };
  }
}

function saveChartAppearanceSettings(settings: ChartAppearanceSettings) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(CHART_APPEARANCE_STORAGE_KEY, JSON.stringify(settings));
}

function normalizeChartAppearanceSettings(settings: Partial<ChartAppearanceSettings>): ChartAppearanceSettings {
  return {
    afterHoursColor: validHexColor(settings.afterHoursColor, defaultChartAppearanceSettings.afterHoursColor),
    afterHoursOpacity: clampNumber(settings.afterHoursOpacity, 0, 0.6, defaultChartAppearanceSettings.afterHoursOpacity),
    borderDownColor: validHexColor(settings.borderDownColor, defaultChartAppearanceSettings.borderDownColor),
    borderUpColor: validHexColor(settings.borderUpColor, defaultChartAppearanceSettings.borderUpColor),
    borderVisible: typeof settings.borderVisible === "boolean" ? settings.borderVisible : defaultChartAppearanceSettings.borderVisible,
    candleSize: Math.round(clampNumber(settings.candleSize, 8, 80, defaultChartAppearanceSettings.candleSize)),
    daySeparatorColor: validHexColor(settings.daySeparatorColor, defaultChartAppearanceSettings.daySeparatorColor),
    daySeparatorStyle: isDaySeparatorStyle(settings.daySeparatorStyle) ? settings.daySeparatorStyle : defaultChartAppearanceSettings.daySeparatorStyle,
    daySeparatorsVisible:
      typeof settings.daySeparatorsVisible === "boolean" ? settings.daySeparatorsVisible : defaultChartAppearanceSettings.daySeparatorsVisible,
    downColor: validHexColor(settings.downColor, defaultChartAppearanceSettings.downColor),
    premarketColor: validHexColor(settings.premarketColor, defaultChartAppearanceSettings.premarketColor),
    premarketOpacity: clampNumber(settings.premarketOpacity, 0, 0.6, defaultChartAppearanceSettings.premarketOpacity),
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
  for (let index = 0; index < sortedCandles.length; index += 1) {
    const candle = sortedCandles[index];
    if (index > 0) {
      const previous = sortedCandles[index - 1];
      const gap = candle.time - previous.time;
      if (gap > stepSeconds && gap <= maxFillGapSeconds) {
        for (let time = previous.time + stepSeconds; time < candle.time; time += stepSeconds) {
          data.push({ time });
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

function buildTimelineDataSignature(timeline: CandleSeriesDatum[]) {
  if (!timeline.length) return "empty";
  const first = timeline[0];
  const last = timeline[timeline.length - 1];
  return `${timeline.length}:${first.time}:${last.time}`;
}

function chartTimeframeSeconds(timeframe: string) {
  const normalized = timeframe.trim().toLowerCase();
  const match = normalized.match(/^(\d+)(m|h|d)$/);
  if (!match) return null;
  const value = Number(match[1]);
  if (!Number.isFinite(value) || value <= 0) return null;
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

function markerPosition(value: unknown): SeriesMarker<Time>["position"] {
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

function defaultLegendSettings(series: ChartSeries): Required<LegendSeriesSettings> {
  return {
    color: series.color,
    lineStyle: series.lineStyle ?? "solid",
    lineWidth: Math.max(1, Math.min(4, Math.round(series.lineWidth || 1))),
    showValue: true,
    visible: true
  };
}

function resolveLegendSettings(settingsMap: LegendSettingsMap, key: string, series: ChartSeries): Required<LegendSeriesSettings> {
  const defaults = defaultLegendSettings(series);
  const stored = settingsMap[key] ?? {};
  return {
    color: stored.color || defaults.color,
    lineStyle: stored.lineStyle || defaults.lineStyle,
    lineWidth: Math.max(1, Math.min(4, Math.round(stored.lineWidth ?? defaults.lineWidth))),
    showValue: stored.showValue ?? defaults.showValue,
    visible: stored.visible ?? defaults.visible
  };
}

function applySeriesSettings(renderer: AnySeriesApi, source: ChartSeries, settings: Required<LegendSeriesSettings>, useAdaptivePriceFormat: boolean) {
  const priceFormatOptions = useAdaptivePriceFormat ? { priceFormat: adaptiveSeriesPriceFormat(source) } : {};
  if (source.style === "histogram") {
    renderer.applyOptions({ color: settings.color, ...priceFormatOptions, visible: settings.visible } as never);
  } else {
    renderer.applyOptions({
      color: seriesColorWithOpacity(source, settings.color),
      lineStyle: toChartLineStyle(settings.lineStyle),
      lineWidth: toLineWidth(settings.lineWidth),
      ...priceFormatOptions,
      visible: settings.visible
    } as never);
  }
  renderer.setData(seriesDataForSettings(source, settings) as never);
}

function addChartSeries(chart: IChartApi, series: ChartSeries, settings: Required<LegendSeriesSettings>): AnySeriesApi {
  if (series.style === "histogram") {
    return chart.addHistogramSeries({
      autoscaleInfoProvider: includeZeroInAutoscale,
      color: settings.color,
      priceFormat: adaptiveSeriesPriceFormat(series),
      priceLineVisible: false,
      title: series.label,
      visible: settings.visible
    });
  }
  return chart.addLineSeries({
    autoscaleInfoProvider: includeZeroInAutoscale,
    color: seriesColorWithOpacity(series, settings.color),
    lineStyle: toChartLineStyle(settings.lineStyle),
    lineWidth: toLineWidth(settings.lineWidth),
    priceFormat: adaptiveSeriesPriceFormat(series),
    priceLineVisible: false,
    title: series.label,
    visible: settings.visible
  });
}

function adaptiveSeriesPriceFormat(series: ChartSeries) {
  const values = series.data.map((point) => Math.abs(Number(point.value))).filter((value) => Number.isFinite(value));
  const maxAbs = values.length ? Math.max(...values) : 0;
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
  const autoscale = baseImplementation();
  if (!autoscale) return autoscale;
  return {
    ...autoscale,
    priceRange: {
      minValue: Math.min(autoscale.priceRange.minValue, 0),
      maxValue: Math.max(autoscale.priceRange.maxValue, 0)
    }
  };
}

function padFlatAutoscale(baseImplementation: () => AutoscaleInfo | null): AutoscaleInfo | null {
  const autoscale = baseImplementation();
  if (!autoscale) return autoscale;
  const minValue = autoscale.priceRange.minValue;
  const maxValue = autoscale.priceRange.maxValue;
  if (maxValue !== minValue) return autoscale;
  const padding = Math.max(0.01, Math.abs(maxValue) * 0.01);
  return {
    ...autoscale,
    priceRange: {
      minValue: minValue - padding,
      maxValue: maxValue + padding
    }
  };
}

function seriesColorWithOpacity(series: ChartSeries, color: string) {
  if (series.style === "histogram" || series.opacity === undefined || series.opacity >= 0.99 || !validHexColor(color, "")) return color;
  return rgbaFromHex(color, series.opacity);
}

function seriesDataForSettings(series: ChartSeries, settings: Required<LegendSeriesSettings>) {
  if (!settings.visible) return [];
  if (series.style !== "histogram") return series.data;
  if (!settings.color || settings.color === series.color) return series.data;
  return series.data.map((point) => ({ ...point, color: settings.color }));
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
  settings: ChartAppearanceSettings = defaultChartAppearanceSettings
) {
  return {
    width: Math.max(320, width),
    height: Math.max(160, height),
    layout: { background: { color: palette.background }, textColor: palette.text },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid }
    },
    localization: {
      timeFormatter: (timeValue: Time) => formatMarketDateTime(timeValue)
    },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: palette.grid, minimumWidth: CHART_PRICE_SCALE_MIN_WIDTH },
    timeScale: {
      borderColor: palette.grid,
      rightOffset: compact ? 1 : 2,
      barSpacing: compact ? Math.max(12, Math.round(settings.candleSize * 0.55)) : settings.candleSize,
      minBarSpacing: 0.2,
      timeVisible: true,
      secondsVisible: false,
      tickMarkFormatter: (timeValue: Time) => formatMarketAxisTime(timeValue)
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

const marketDateTimeFormatter = new Intl.DateTimeFormat("en-US", {
  day: "2-digit",
  hour: "2-digit",
  hour12: false,
  minute: "2-digit",
  month: "short",
  timeZone: "America/New_York",
  year: "numeric"
});

function fitFirstDay(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  const firstDay = marketDate(candles[0].time);
  let lastIndex = 0;
  candles.forEach((candle, index) => {
    if (marketDate(candle.time) === firstDay) {
      lastIndex = index;
    }
  });
  chart.timeScale().setVisibleLogicalRange({ from: -1, to: Math.max(8, lastIndex + 1) });
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

function fitInitialRange(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  if (hasMultipleMarketDates(candles)) {
    chart.timeScale().setVisibleLogicalRange({ from: -1, to: Math.max(8, candles.length) });
    return;
  }
  fitFirstDay(chart, candles);
}

function fitRecent(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  const last = candles.length - 1;
  const span = Math.min(180, Math.max(60, Math.ceil(candles.length * 0.18)));
  const halfSpan = Math.ceil(span / 2);
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(-1, last - halfSpan), to: last + halfSpan });
}

function fitReferenceOrRecent(chart: IChartApi | null, candles: Candle[], reference: ChartReference | null | undefined, timeframe: string) {
  if (reference) {
    fitAroundReference(chart, candles, reference, timeframe);
    return;
  }
  fitRecent(chart, candles);
}

function fitAroundReference(chart: IChartApi | null, candles: Candle[], reference: ChartReference, timeframe: string) {
  if (!chart || !candles.length) return;
  const referenceTime = resolveFitReferenceTime(reference, candles);
  if (referenceTime === null) {
    fitInitialRange(chart, candles);
    return;
  }
  const timeline = candleDataForTimeframe(candles, timeframe);
  const referenceIndex = nearestTimelineIndex(timeline, referenceTime);
  const startIndex = typeof reference.startTime === "number" ? nearestTimelineIndex(timeline, reference.startTime) : referenceIndex;
  const endIndex = typeof reference.endTime === "number" ? nearestTimelineIndex(timeline, reference.endTime) : referenceIndex;
  const tradeSpan = Math.max(1, Math.abs(endIndex - startIndex));
  const span = Math.min(timeline.length, Math.max(60, Math.min(240, tradeSpan * 5)));
  const halfSpan = Math.ceil(span / 2);
  chart.timeScale().setVisibleLogicalRange({
    from: Math.max(-1, referenceIndex - halfSpan),
    to: Math.min(timeline.length + halfSpan, referenceIndex + halfSpan),
  });
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
  const first = candles[0];
  return `${ticker}:${timeframe}:${referenceKey || "no-reference"}:${first?.time ?? "empty"}`;
}

function hasMultipleMarketDates(candles: Candle[]) {
  if (candles.length < 2) return false;
  const first = marketDate(candles[0].time);
  return candles.some((candle) => marketDate(candle.time) !== first);
}

function syncChartRanges(charts: IChartApi[]) {
  if (charts.length < 2) return () => undefined;
  let syncing = false;
  const initialRange = charts[0].timeScale().getVisibleLogicalRange();
  if (initialRange) {
    charts.slice(1).forEach((target) => target.timeScale().setVisibleLogicalRange(initialRange));
  }
  const handlers = charts.map((source) => {
    const handler = (range: LogicalRange | null) => {
      if (syncing || !range) return;
      syncing = true;
      charts.forEach((target) => {
        if (target !== source) target.timeScale().setVisibleLogicalRange(range);
      });
      syncing = false;
    };
    source.timeScale().subscribeVisibleLogicalRangeChange(handler);
    return { handler, source };
  });
  return () => {
    handlers.forEach(({ handler, source }) => {
      source.timeScale().unsubscribeVisibleLogicalRangeChange(handler);
    });
  };
}

function syncCrosshairs(
  priceChart: IChartApi,
  oscillatorPanes: OscillatorPaneRuntime[],
  candleSeries: AnySeriesApi,
  closeByTime: Map<number, number>
) {
  if (!oscillatorPanes.length) return () => undefined;
  let syncing = false;

  const setOscillatorCrosshairs = (time: Time, excludedChart?: IChartApi) => {
    oscillatorPanes.forEach((pane) => {
      if (pane.chart === excludedChart) return;
      const value = pane.valuesByTime.get(Number(time));
      if (pane.renderer && typeof value === "number" && Number.isFinite(value)) {
        pane.chart.setCrosshairPosition(value, time, pane.renderer);
      } else if (pane.timelineRenderer) {
        pane.chart.setCrosshairPosition(0, time, pane.timelineRenderer);
      } else {
        pane.chart.clearCrosshairPosition();
      }
    });
  };

  const clearOscillatorCrosshairs = (excludedChart?: IChartApi) => {
    oscillatorPanes.forEach((pane) => {
      if (pane.chart !== excludedChart) pane.chart.clearCrosshairPosition();
    });
  };

  const syncToOscillators = (param: MouseEventParams<Time>) => {
    if (syncing) return;
    if (!param.time) {
      clearOscillatorCrosshairs();
      return;
    }
    syncing = true;
    setOscillatorCrosshairs(param.time);
    syncing = false;
  };

  const syncToPriceAndPeers = (sourceChart: IChartApi, param: MouseEventParams<Time>) => {
    if (syncing) return;
    if (!param.time) {
      priceChart.clearCrosshairPosition();
      clearOscillatorCrosshairs(sourceChart);
      return;
    }
    const value = closeByTime.get(Number(param.time));
    if (typeof value !== "number" || !Number.isFinite(value)) {
      priceChart.clearCrosshairPosition();
      clearOscillatorCrosshairs(sourceChart);
      return;
    }
    syncing = true;
    priceChart.setCrosshairPosition(value, param.time, candleSeries);
    setOscillatorCrosshairs(param.time, sourceChart);
    syncing = false;
  };

  priceChart.subscribeCrosshairMove(syncToOscillators);
  const paneHandlers = oscillatorPanes.map((pane) => {
    const handler = (param: MouseEventParams<Time>) => syncToPriceAndPeers(pane.chart, param);
    pane.chart.subscribeCrosshairMove(handler);
    return { pane, handler };
  });
  return () => {
    priceChart.unsubscribeCrosshairMove(syncToOscillators);
    paneHandlers.forEach(({ pane, handler }) => pane.chart.unsubscribeCrosshairMove(handler));
  };
}

function drawRegions(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement | null,
  regions: Region[],
  priceZones: PriceZone[],
  tradeAnnotations: TradeAnnotation[],
  candles: Candle[],
  settings: ChartAppearanceSettings,
  liveEntryLine?: LiveEntryLine | null
) {
  if (!layer) return;
  layer.innerHTML = "";
  const barWidth = estimateBarWidth(chart, candles);
  const candleDuration = estimateCandleDuration(candles);
  regions.forEach((region) => {
    const coordinates = regionCoordinates(chart, region, candles, barWidth, candleDuration);
    if (!coordinates) return;
    const left = Math.min(coordinates.start, coordinates.end);
    const width = Math.abs(coordinates.end - coordinates.start);
    if (width < 1) return;
    const node = document.createElement("div");
    node.className = "session-region";
    node.title = region.label;
    node.style.left = `${left}px`;
    node.style.width = `${width}px`;
    node.style.background = sessionRegionColor(region, settings);
    layer.appendChild(node);
  });
  drawPriceZones(chart, priceSeries, layer, priceZones, candles, barWidth, candleDuration);
  drawDaySeparators(chart, layer, candles, settings, barWidth);
  drawTradeAnnotations(chart, priceSeries, layer, tradeAnnotations, candles, barWidth);
  drawLiveEntryLine(chart, priceSeries, layer, candles, liveEntryLine);
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

function drawPriceZones(
  chart: IChartApi,
  priceSeries: ISeriesApi<"Candlestick"> | null,
  layer: HTMLDivElement,
  zones: PriceZone[],
  candles: Candle[],
  barWidth: number,
  candleDuration: number
) {
  if (!priceSeries || !zones.length) return;
  zones.forEach((zone) => {
    const coordinates = priceZoneCoordinates(chart, zone, candles, barWidth, candleDuration);
    if (!coordinates) return;
    const upper = priceSeries.priceToCoordinate(zone.upper);
    const lower = priceSeries.priceToCoordinate(zone.lower);
    if (upper === null || lower === null) return;
    const left = Math.min(coordinates.start, coordinates.end);
    const width = Math.abs(coordinates.end - coordinates.start);
    let top = Math.min(upper, lower);
    let height = Math.max(2, Math.abs(lower - upper));
    const center = (upper + lower) / 2;
    const minPixelHeight = clampNumber(zone.minPixelHeight, 0, 32, 0);
    const maxPixelHeight = clampNumber(zone.maxPixelHeight, 0, 96, 0);
    if (zone.zoneHeightMode === "fixed_px") {
      height = Math.max(2, minPixelHeight, maxPixelHeight || minPixelHeight || 3);
      top = center - height / 2;
    } else {
      if (minPixelHeight > 0 && height < minPixelHeight) {
        height = minPixelHeight;
        top = center - height / 2;
      }
      if (maxPixelHeight > 0 && height > maxPixelHeight) {
        height = maxPixelHeight;
        top = center - height / 2;
      }
    }
    if (width < 1 || height < 1) return;
    const node = document.createElement("div");
    node.className = "price-zone";
    node.title = zone.label;
    node.style.left = `${left}px`;
    node.style.top = `${top}px`;
    node.style.width = `${width}px`;
    node.style.height = `${height}px`;
    const fillColor = validHexColor(zone.fillColor, validHexColor(zone.color, "#1E3A5F"));
    const fillOpacity = clampNumber(zone.fillOpacity, 0.02, 0.35, 0.08);
    const borderColor = validHexColor(zone.borderColor, fillColor);
    const borderOpacity = clampNumber(zone.borderOpacity, 0, 0.35, Math.max(fillOpacity * 1.8, 0.12));
    node.style.borderColor = rgbaFromHex(borderColor, borderOpacity);
    node.style.borderStyle = zoneBorderStyle(zone.borderStyle);
    node.style.borderWidth = `${Math.max(0, Math.min(3, Math.round(zone.borderWidth ?? 1)))}px`;
    node.style.background = rgbaFromHex(fillColor, fillOpacity);
    const label = document.createElement("span");
    label.textContent = zone.label;
    label.style.color = fillColor;
    label.style.opacity = "1";
    node.appendChild(label);
    layer.appendChild(node);
  });
}

function priceZoneCoordinates(chart: IChartApi, zone: PriceZone, candles: Candle[], barWidth: number, candleDuration: number) {
  const coordinates = regionCoordinates(chart, { start: zone.start, end: zone.end, color: zone.color, label: zone.label }, candles, barWidth, candleDuration);
  if (!coordinates) return null;
  const exactStart = chart.timeScale().timeToCoordinate(zone.start as Time);
  if (exactStart === null) return coordinates;
  return { ...coordinates, start: exactStart };
}

function sessionRegionColor(region: Region, settings: ChartAppearanceSettings) {
  const label = region.label.toLowerCase();
  if (label.includes("pre")) return rgbaFromHex(settings.premarketColor, settings.premarketOpacity);
  if (label.includes("after") || label.includes("post")) return rgbaFromHex(settings.afterHoursColor, settings.afterHoursOpacity);
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
    if (coordinate === null) return;
    const node = document.createElement("div");
    node.className = "day-separator";
    node.title = currentDate;
    node.style.left = `${coordinate - barWidth / 2}px`;
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
    const left = Math.min(entryX, exitX) - barWidth / 2;
    const right = Math.max(entryX, exitX) + barWidth / 2;
    const width = Math.max(3, right - left);
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
    drawTradeArrow(layer, entryX, entryY, color, "entry", selected);
    drawTradeArrow(layer, exitX, exitY, color, "exit", selected);
    annotation.fills?.forEach((fill) => {
      const fillX = xForAnnotationTime(chart, fill.time, candles);
      const fillY = priceSeries.priceToCoordinate(fill.price);
      if (fillX === null || fillY === null) return;
      drawTradeFillMarker(layer, fillX, fillY, color, fill, selected);
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
  if (coordinate === null) return;
  const node = document.createElement("div");
  node.className = "chart-reference-line";
  node.title = reference.label || "Selected row";
  node.style.left = `${coordinate}px`;
  if (coordinate < 90) {
    node.classList.add("near-left");
  } else if (coordinate > layer.clientWidth - 90) {
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

function formatMarketAxisTime(timeValue: Time) {
  return marketAxisFormatter.format(new Date(timestampFromChartTime(timeValue) * 1000));
}

function formatMarketDateTime(timeValue: Time) {
  return marketDateTimeFormatter.format(new Date(timestampFromChartTime(timeValue) * 1000));
}

function formatPrice(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4 }).format(value);
}
