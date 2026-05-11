import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type MouseEventParams,
  type SeriesType,
  type Time
} from "lightweight-charts";
import { CalendarRange, LocateFixed, Maximize2, Minimize2, Settings } from "lucide-react";
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
  const [draftTicker, setDraftTicker] = useState(ticker.toUpperCase());
  const [fullscreen, setFullscreen] = useState(false);
  const [themeSignature, setThemeSignature] = useState(() => document.documentElement.dataset.shellTheme ?? "");

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
    if (!priceRef.current || !payload) return;
    const palette = readChartPalette();
    priceRef.current.innerHTML = "";
    if (oscRef.current) oscRef.current.innerHTML = "";
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
      const line = priceChart.addLineSeries({
        color: series.color,
        lineWidth: Math.max(1, Math.min(4, series.lineWidth)) as 1,
        autoscaleInfoProvider: () => null,
        priceLineVisible: false,
        title: series.label
      });
      line.setData(series.data as never);
    });

    let oscChart: IChartApi | null = null;
    let primaryOscillatorSeries: AnySeriesApi | null = null;
    if (oscRef.current && payload.oscillator_series.length) {
      oscChart = createChart(oscRef.current, chartOptions(oscRef.current.clientWidth, oscRef.current.clientHeight, true, palette));
      oscChartRef.current = oscChart;
      payload.oscillator_series.forEach((series) => {
        const renderer =
          series.style === "histogram"
            ? oscChart!.addHistogramSeries({ color: series.color, priceLineVisible: false, title: series.label })
            : oscChart!.addLineSeries({ color: series.color, lineWidth: 1, priceLineVisible: false, title: series.label });
        if (!primaryOscillatorSeries) primaryOscillatorSeries = renderer;
        renderer.setData(series.data as never);
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

  const priceLegendItems = buildPriceLegendItems(payload, ticker, timeframe);
  const oscillatorLegendItems = buildSeriesLegendItems(payload?.oscillator_series ?? []);

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
            <ChartLegend items={priceLegendItems} />
          </div>
          {payload.oscillator_series.length ? (
            <div className="chart-osc">
              <div className="chart-pane-canvas" ref={oscRef} />
              <ChartLegend items={oscillatorLegendItems} />
            </div>
          ) : null}
        </div>
      )}
    </div>
  );
});

type LegendItem = { color: string; label: string; value: string };

function ChartLegend({ items }: { items: LegendItem[] }) {
  if (!items.length) return null;
  return (
    <div className="chart-legend">
      {items.map((item) => (
        <span className="chart-legend-item" key={`${item.label}-${item.value}`}>
          <i style={{ background: item.color }} />
          <span>{item.label}</span>
          <b>{item.value}</b>
        </span>
      ))}
    </div>
  );
}

function buildPriceLegendItems(payload: ChartPayload | null, ticker: string, timeframe: string): LegendItem[] {
  if (!payload?.candles.length) return [];
  const candle = payload.candles[payload.candles.length - 1];
  const candleColor = candle.close >= candle.open ? candleSettings.upColor : candleSettings.downColor;
  return [
    {
      color: candleColor,
      label: `${ticker.toUpperCase()} ${timeframe}`,
      value: `O ${formatPrice(candle.open)} H ${formatPrice(candle.high)} L ${formatPrice(candle.low)} C ${formatPrice(candle.close)}`
    },
    ...buildSeriesLegendItems(payload.overlay_series)
  ];
}

function buildSeriesLegendItems(series: ChartSeries[]): LegendItem[] {
  return series.flatMap((item) => {
    const latest = latestSeriesValue(item.data);
    if (latest === null) return [];
    return [{ color: item.color, label: item.label, value: formatPrice(latest) }];
  });
}

function latestSeriesValue(data: Array<{ value: number }>) {
  for (let index = data.length - 1; index >= 0; index -= 1) {
    const value = data[index]?.value;
    if (Number.isFinite(value)) return value;
  }
  return null;
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
