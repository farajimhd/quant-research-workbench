import {
  createChart,
  LineStyle,
  type IChartApi,
  type ISeriesApi,
  type LineWidth,
  type LogicalRange,
  type MouseEventParams,
  type SeriesType,
  type Time
} from "lightweight-charts";
import {
  CalendarRange,
  ChevronDown,
  ChevronRight,
  Eye,
  EyeOff,
  LocateFixed,
  Maximize2,
  Minimize2,
  Settings,
  SlidersHorizontal,
  X
} from "lucide-react";
import { forwardRef, type FormEvent, type ReactNode, useEffect, useImperativeHandle, useRef, useState } from "react";

import { buildSegmentButtonClassName } from "../selectionStyles";

type Candle = { time: number; open: number; high: number; low: number; close: number };
type ChartSeries = {
  column: string;
  label: string;
  style: "line" | "histogram";
  color: string;
  lineWidth: number;
  data: Array<{ color?: string; time: number; value: number }>;
};
type Region = { start: number; end: number; color: string; label: string };
type AnySeriesApi = ISeriesApi<SeriesType>;
type LegendPane = "price" | "oscillator";
type LegendLineStyle = "solid" | "dashed" | "dotted";
type LegendSeriesSettings = {
  color?: string;
  lineStyle?: LegendLineStyle;
  lineWidth?: number;
  showValue?: boolean;
  visible?: boolean;
};
type LegendSettingsMap = Record<string, LegendSeriesSettings>;

export type ChartPayload = {
  candles: Candle[];
  volume: Array<{ time: number; value: number; color: string }>;
  overlay_series: ChartSeries[];
  oscillator_series: ChartSeries[];
  markers: Array<Record<string, unknown>>;
  regions: Region[];
};

export type ChartPanelHandle = {
  fitFirstDay: () => void;
  fitRecent: () => void;
  toggleFullscreen: () => void;
};

type ChartPanelProps = {
  onSettingsToggle: () => void;
  onTickerChange: (value: string) => void;
  onTimeframeChange: (value: string) => void;
  payload: ChartPayload | null;
  settingsContent?: ReactNode;
  settingsOpen: boolean;
  ticker: string;
  timeframe: string;
  timeframes: string[];
};

const candleSettings = {
  upColor: "#33E42A",
  downColor: "#FD0E50",
  borderUpColor: "#1DB914",
  borderDownColor: "#CB093F",
  wickUpColor: "#4DC746",
  wickDownColor: "#C52A55"
};

const LEGEND_SETTINGS_STORAGE_KEY = "quant-research-workbench.chart.legend-settings.v1";

type ChartPalette = {
  background: string;
  grid: string;
  text: string;
};

export const ChartPanel = forwardRef<ChartPanelHandle, ChartPanelProps>(({
  onSettingsToggle,
  onTickerChange,
  onTimeframeChange,
  payload,
  settingsContent,
  settingsOpen,
  ticker,
  timeframe,
  timeframes
}, ref) => {
  const priceRef = useRef<HTMLDivElement | null>(null);
  const oscRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const priceLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const oscChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const indicatorSeriesRef = useRef<Map<string, AnySeriesApi>>(new Map());
  const indicatorSourceRef = useRef<Map<string, ChartSeries>>(new Map());
  const [draftTicker, setDraftTicker] = useState(ticker.toUpperCase());
  const [fullscreen, setFullscreen] = useState(false);
  const [legendSettings, setLegendSettings] = useState<LegendSettingsMap>(() => loadLegendSettings());
  const [themeSignature, setThemeSignature] = useState(() => document.documentElement.dataset.shellTheme ?? "");

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

  useImperativeHandle(ref, () => ({
    fitFirstDay() {
      fitFirstDay(priceChartRef.current, payload?.candles ?? []);
    },
    fitRecent() {
      fitRecent(priceChartRef.current, payload?.candles ?? []);
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
    setDraftTicker(ticker.toUpperCase());
  }, [ticker]);

  useEffect(() => {
    indicatorSeriesRef.current.forEach((renderer, key) => {
      const source = indicatorSourceRef.current.get(key);
      if (!source) return;
      const settings = resolveLegendSettings(legendSettings, key, source);
      applySeriesSettings(renderer, source, settings);
    });
  }, [legendSettings]);

  useEffect(() => {
    if (!priceRef.current || !payload) return;
    const palette = readChartPalette();
    priceRef.current.innerHTML = "";
    if (oscRef.current) oscRef.current.innerHTML = "";
    indicatorSeriesRef.current.clear();
    indicatorSourceRef.current.clear();
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette));
    priceChartRef.current = priceChart;
    const candleSeries = priceChart.addCandlestickSeries({
      ...candleSettings,
      borderVisible: true,
      wickVisible: true,
      priceLineVisible: true
    });
    candleRef.current = candleSeries;
    candleSeries.setData(payload.candles as never);
    if (payload.markers.length) candleSeries.setMarkers(payload.markers as never);
    const volume = priceChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", base: 0 });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(payload.volume as never);
    payload.overlay_series.forEach((series) => {
      const key = legendSeriesKey("price", series);
      const settings = resolveLegendSettings(legendSettings, key, series);
      const line = priceChart.addLineSeries({
        color: settings.color,
        lineStyle: toChartLineStyle(settings.lineStyle),
        lineWidth: toLineWidth(settings.lineWidth),
        autoscaleInfoProvider: () => null,
        priceLineVisible: false,
        title: series.label,
        visible: settings.visible
      });
      line.setData(seriesDataForSettings(series, settings) as never);
      indicatorSeriesRef.current.set(key, line);
      indicatorSourceRef.current.set(key, series);
    });

    let oscChart: IChartApi | null = null;
    let primaryOscillatorSeries: AnySeriesApi | null = null;
    if (oscRef.current && payload.oscillator_series.length) {
      oscChart = createChart(oscRef.current, chartOptions(oscRef.current.clientWidth, oscRef.current.clientHeight, true, palette));
      oscChartRef.current = oscChart;
      payload.oscillator_series.forEach((series) => {
        const key = legendSeriesKey("oscillator", series);
        const settings = resolveLegendSettings(legendSettings, key, series);
        const renderer =
          series.style === "histogram"
            ? oscChart!.addHistogramSeries({ color: settings.color, priceLineVisible: false, title: series.label, visible: settings.visible })
            : oscChart!.addLineSeries({
                color: settings.color,
                lineStyle: toChartLineStyle(settings.lineStyle),
                lineWidth: toLineWidth(settings.lineWidth),
                priceLineVisible: false,
                title: series.label,
                visible: settings.visible
              });
        if (!primaryOscillatorSeries) primaryOscillatorSeries = renderer;
        renderer.setData(seriesDataForSettings(series, settings) as never);
        indicatorSeriesRef.current.set(key, renderer);
        indicatorSourceRef.current.set(key, series);
      });
    } else {
      oscChartRef.current = null;
    }
    const rangeCleanup = oscChart ? syncRanges(priceChart, oscChart) : () => undefined;
    const closeByTime = new Map(payload.candles.map((candle) => [candle.time, candle.close]));
    const oscillatorByTime = new Map((payload.oscillator_series[0]?.data ?? []).map((point) => [point.time, point.value]));
    const crosshairCleanup =
      oscChart && primaryOscillatorSeries
        ? syncCrosshairs(priceChart, oscChart, candleSeries, primaryOscillatorSeries, closeByTime, oscillatorByTime)
        : () => undefined;
    const draw = () => drawRegions(priceChart, priceLayerRef.current, payload.regions, payload.candles);
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    window.setTimeout(() => {
      fitFirstDay(priceChart, payload.candles);
      draw();
    }, 20);
    const observer = new ResizeObserver(() => {
      resizeCharts();
      draw();
    });
    if (shellRef.current) observer.observe(shellRef.current);
    return () => {
      observer.disconnect();
      crosshairCleanup();
      rangeCleanup();
      priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(draw);
      priceChart.remove();
      oscChart?.remove();
      priceChartRef.current = null;
      oscChartRef.current = null;
      indicatorSeriesRef.current.clear();
      indicatorSourceRef.current.clear();
    };
  }, [payload, themeSignature]);

  function resizeCharts() {
    const price = priceRef.current;
    const osc = oscRef.current;
    if (price && priceChartRef.current) {
      priceChartRef.current.applyOptions({ width: price.clientWidth, height: price.clientHeight });
    }
    if (osc && oscChartRef.current) {
      oscChartRef.current.applyOptions({ width: osc.clientWidth, height: osc.clientHeight });
    }
  }

  const priceLegendItems = buildPriceLegendItems(payload, ticker, timeframe, legendSettings);
  const oscillatorLegendItems = buildSeriesLegendItems(payload?.oscillator_series ?? [], "oscillator", legendSettings);

  const commitTicker = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const normalized = draftTicker.trim().toUpperCase();
    if (!normalized) {
      setDraftTicker(ticker.toUpperCase());
      return;
    }
    setDraftTicker(normalized);
    if (normalized !== ticker.toUpperCase()) {
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
            maxLength={10}
            onChange={(event) => setDraftTicker(event.target.value.toUpperCase())}
            spellCheck={false}
            value={draftTicker}
          />
        </form>
        <div className="chart-timeframe-row">
          {timeframes.map((item) => (
            <button className={buildSegmentButtonClassName(item === timeframe)} key={item} onClick={() => onTimeframeChange(item)} type="button">
              {item}
            </button>
          ))}
        </div>
        <div className="toolbar-spacer" />
        <button className="toolbar-button" type="button" title="Settings" onClick={onSettingsToggle}><Settings size={15} /></button>
        <span className="toolbar-divider" />
        <button className="toolbar-button" type="button" title="Fit first day" onClick={() => fitFirstDay(priceChartRef.current, payload?.candles ?? [])}><CalendarRange size={15} /></button>
        <button className="toolbar-button" type="button" title="Fit recent" onClick={() => fitRecent(priceChartRef.current, payload?.candles ?? [])}><LocateFixed size={15} /></button>
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
      </div>
      {settingsOpen ? <div className="chart-settings-slot">{settingsContent}</div> : null}
      {!payload || !payload.candles.length ? (
        <div className="empty-state chart-empty-state">No chart data for the selected ticker/session/timeframe.</div>
      ) : (
        <div className="chart-canvas-stack">
          <div className="chart-price">
            <div className="chart-pane-canvas" ref={priceRef} />
            <div className="session-layer" ref={priceLayerRef} />
            <ChartLegend
              indicatorCount={payload.overlay_series.length}
              items={priceLegendItems}
              onReset={resetLegendSettings}
              onUpdate={updateLegendSettings}
              title="Price"
            />
          </div>
          {payload.oscillator_series.length ? (
            <div className="chart-osc">
              <div className="chart-pane-canvas" ref={oscRef} />
              <ChartLegend
                indicatorCount={payload.oscillator_series.length}
                items={oscillatorLegendItems}
                onReset={resetLegendSettings}
                onUpdate={updateLegendSettings}
                title="Pane"
              />
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
});

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
  onUpdate,
  title
}: {
  indicatorCount: number;
  items: LegendItem[];
  onReset: (key: string) => void;
  onUpdate: (key: string, patch: LegendSeriesSettings) => void;
  title: string;
}) {
  const [collapsed, setCollapsed] = useState(false);
  const [editingKey, setEditingKey] = useState<string | null>(null);
  if (!items.length) return null;
  const editingItem = items.find((item) => item.key === editingKey && item.configurable);
  return (
    <div className={collapsed ? "chart-legend collapsed" : "chart-legend"}>
      <button className="chart-legend-header" onClick={() => setCollapsed((value) => !value)} type="button">
        {collapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
        <span>{title}</span>
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
                {item.showValue && item.visible ? <b>{item.value}</b> : null}
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

function buildPriceLegendItems(payload: ChartPayload | null, ticker: string, timeframe: string, settingsMap: LegendSettingsMap): LegendItem[] {
  if (!payload?.candles.length) return [];
  const candle = payload.candles[payload.candles.length - 1];
  const candleColor = candle.close >= candle.open ? candleSettings.upColor : candleSettings.downColor;
  return [
    {
      color: candleColor,
      configurable: false,
      key: "price:candles",
      label: `${ticker.toUpperCase()} ${timeframe}`,
      lineStyle: "solid",
      lineWidth: 1,
      seriesStyle: "candlestick",
      showValue: true,
      value: `O ${formatPrice(candle.open)} H ${formatPrice(candle.high)} L ${formatPrice(candle.low)} C ${formatPrice(candle.close)}`,
      visible: true
    },
    ...buildSeriesLegendItems(payload.overlay_series, "price", settingsMap)
  ];
}

function buildSeriesLegendItems(series: ChartSeries[], pane: LegendPane, settingsMap: LegendSettingsMap): LegendItem[] {
  return series.map((item) => {
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

function legendSeriesKey(pane: LegendPane, series: ChartSeries) {
  return `${pane}:${series.column || series.label}`;
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

function defaultLegendSettings(series: ChartSeries): Required<LegendSeriesSettings> {
  return {
    color: series.color,
    lineStyle: "solid",
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

function applySeriesSettings(renderer: AnySeriesApi, source: ChartSeries, settings: Required<LegendSeriesSettings>) {
  if (source.style === "histogram") {
    renderer.applyOptions({ color: settings.color, visible: settings.visible } as never);
  } else {
    renderer.applyOptions({
      color: settings.color,
      lineStyle: toChartLineStyle(settings.lineStyle),
      lineWidth: toLineWidth(settings.lineWidth),
      visible: settings.visible
    } as never);
  }
  renderer.setData(seriesDataForSettings(source, settings) as never);
}

function seriesDataForSettings(series: ChartSeries, settings: Required<LegendSeriesSettings>) {
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

function chartOptions(width: number, height: number, compact = false, palette: ChartPalette = readChartPalette()) {
  return {
    width: Math.max(320, width),
    height: Math.max(160, height),
    layout: { background: { color: palette.background }, textColor: palette.text },
    grid: {
      vertLines: { color: palette.grid },
      horzLines: { color: palette.grid }
    },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: palette.grid },
    timeScale: {
      borderColor: palette.grid,
      rightOffset: compact ? 1 : 2,
      barSpacing: compact ? 22 : 40,
      minBarSpacing: 0.2,
      timeVisible: true,
      secondsVisible: false
    }
  };
}

const marketDateFormatter = new Intl.DateTimeFormat("en-CA", {
  day: "2-digit",
  month: "2-digit",
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

function fitRecent(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  const last = candles.length - 1;
  const span = Math.min(180, Math.max(60, Math.ceil(candles.length * 0.18)));
  const halfSpan = Math.ceil(span / 2);
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(-1, last - halfSpan), to: last + halfSpan });
}

function syncRanges(source: IChartApi, target: IChartApi) {
  let syncing = false;
  const sourceHandler = (range: LogicalRange | null) => {
    if (syncing || !range) return;
    syncing = true;
    target.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  };
  const targetHandler = (range: LogicalRange | null) => {
    if (syncing || !range) return;
    syncing = true;
    source.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  };
  source.timeScale().subscribeVisibleLogicalRangeChange(sourceHandler);
  target.timeScale().subscribeVisibleLogicalRangeChange(targetHandler);
  return () => {
    source.timeScale().unsubscribeVisibleLogicalRangeChange(sourceHandler);
    target.timeScale().unsubscribeVisibleLogicalRangeChange(targetHandler);
  };
}

function syncCrosshairs(
  priceChart: IChartApi,
  oscillatorChart: IChartApi,
  candleSeries: AnySeriesApi,
  oscillatorSeries: AnySeriesApi,
  closeByTime: Map<number, number>,
  oscillatorByTime: Map<number, number>
) {
  let syncing = false;

  const syncToOscillator = (param: MouseEventParams<Time>) => {
    if (syncing) return;
    if (!param.time) {
      oscillatorChart.clearCrosshairPosition();
      return;
    }
    const value = oscillatorByTime.get(Number(param.time));
    if (typeof value !== "number" || !Number.isFinite(value)) {
      oscillatorChart.clearCrosshairPosition();
      return;
    }
    syncing = true;
    oscillatorChart.setCrosshairPosition(value, param.time, oscillatorSeries);
    syncing = false;
  };

  const syncToPrice = (param: MouseEventParams<Time>) => {
    if (syncing) return;
    if (!param.time) {
      priceChart.clearCrosshairPosition();
      return;
    }
    const value = closeByTime.get(Number(param.time));
    if (typeof value !== "number" || !Number.isFinite(value)) {
      priceChart.clearCrosshairPosition();
      return;
    }
    syncing = true;
    priceChart.setCrosshairPosition(value, param.time, candleSeries);
    syncing = false;
  };

  priceChart.subscribeCrosshairMove(syncToOscillator);
  oscillatorChart.subscribeCrosshairMove(syncToPrice);
  return () => {
    priceChart.unsubscribeCrosshairMove(syncToOscillator);
    oscillatorChart.unsubscribeCrosshairMove(syncToPrice);
  };
}

function drawRegions(chart: IChartApi, layer: HTMLDivElement | null, regions: Region[], candles: Candle[]) {
  if (!layer) return;
  layer.innerHTML = "";
  const barWidth = estimateBarWidth(chart, candles);
  regions.forEach((region) => {
    const coordinates = regionCoordinates(chart, region, candles, barWidth);
    if (!coordinates) return;
    const left = Math.min(coordinates.start, coordinates.end);
    const width = Math.abs(coordinates.end - coordinates.start);
    if (width < 1) return;
    const node = document.createElement("div");
    node.className = "session-region";
    node.title = region.label;
    node.style.left = `${left}px`;
    node.style.width = `${width}px`;
    node.style.background = region.color;
    layer.appendChild(node);
  });
}

function regionCoordinates(chart: IChartApi, region: Region, candles: Candle[], barWidth: number) {
  const start = chart.timeScale().timeToCoordinate(region.start as Time);
  const end = chart.timeScale().timeToCoordinate(region.end as Time);
  if (start !== null && end !== null) return { end, start };

  const regionCandles = candles.filter((candle) => candle.time >= region.start && candle.time <= region.end);
  if (!regionCandles.length) return null;
  const first = chart.timeScale().timeToCoordinate(regionCandles[0]?.time as Time);
  const last = chart.timeScale().timeToCoordinate(regionCandles[regionCandles.length - 1]?.time as Time);
  if (first === null || last === null) return null;
  return { end: last + barWidth / 2, start: first - barWidth / 2 };
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

function marketDate(time: number) {
  return marketDateFormatter.format(new Date(time * 1000));
}

function formatPrice(value: number) {
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: Math.abs(value) >= 100 ? 2 : 4 }).format(value);
}
