import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type Time
} from "lightweight-charts";
import { Maximize2, Minimize2, RotateCw, Settings, Shrink } from "lucide-react";
import { forwardRef, type ReactNode, useEffect, useImperativeHandle, useRef, useState } from "react";

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
        priceLineVisible: false,
        title: series.label
      });
      line.setData(series.data as never);
    });

    let oscChart: IChartApi | null = null;
    if (oscRef.current && payload.oscillator_series.length) {
      oscChart = createChart(oscRef.current, chartOptions(oscRef.current.clientWidth, oscRef.current.clientHeight, true, palette));
      oscChartRef.current = oscChart;
      payload.oscillator_series.forEach((series) => {
        const renderer =
          series.style === "histogram"
            ? oscChart!.addHistogramSeries({ color: series.color, priceLineVisible: false, title: series.label })
            : oscChart!.addLineSeries({ color: series.color, lineWidth: 1, priceLineVisible: false, title: series.label });
        renderer.setData(series.data as never);
      });
      syncRanges(priceChart, oscChart);
    } else {
      oscChartRef.current = null;
    }
    const draw = () => drawRegions(priceChart, priceLayerRef.current, payload.regions);
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

  return (
    <div className={fullscreen ? "chart-shell fullscreen" : "chart-shell"} ref={shellRef}>
      <div className="chart-component-toolbar">
        <input className="chart-ticker-input" value={ticker} maxLength={10} onChange={(event) => onTickerChange(event.target.value.toUpperCase())} aria-label="Ticker" />
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
        <button className="toolbar-button" type="button" title="Fit first day" onClick={() => fitFirstDay(priceChartRef.current, payload?.candles ?? [])}><RotateCw size={15} /></button>
        <button className="toolbar-button" type="button" title="Fit recent" onClick={() => fitRecent(priceChartRef.current, payload?.candles ?? [])}><Shrink size={15} /></button>
        <span className="toolbar-divider" />
        <button className="toolbar-button" type="button" title={fullscreen ? "Exit fullscreen" : "Fullscreen"} onClick={() => setFullscreen((value) => !value)}>
          {fullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
        </button>
      </div>
      {settingsOpen ? <div className="chart-settings-slot">{settingsContent}</div> : null}
      {!payload || !payload.candles.length ? (
        <div className="empty-state chart-empty-state">No chart data for the selected ticker/session/timeframe.</div>
      ) : (
        <div className="chart-canvas-stack">
          <div className="chart-price" ref={priceRef}>
            <div className="session-layer" ref={priceLayerRef} />
          </div>
          {payload.oscillator_series.length ? <div className="chart-osc" ref={oscRef} /> : null}
        </div>
      )}
    </div>
  );
});

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

function fitFirstDay(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  const firstDay = new Date(candles[0].time * 1000).toISOString().slice(0, 10);
  let lastIndex = 0;
  candles.forEach((candle, index) => {
    if (new Date(candle.time * 1000).toISOString().slice(0, 10) === firstDay) {
      lastIndex = index;
    }
  });
  chart.timeScale().setVisibleLogicalRange({ from: 0, to: Math.max(30, lastIndex + 2) });
}

function fitRecent(chart: IChartApi | null, candles: Candle[]) {
  if (!chart || !candles.length) return;
  const last = candles.length - 1;
  chart.timeScale().setVisibleLogicalRange({ from: Math.max(0, last - 90), to: last + 4 });
}

function syncRanges(source: IChartApi, target: IChartApi) {
  let syncing = false;
  source.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
    if (syncing || !range) return;
    syncing = true;
    target.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });
  target.timeScale().subscribeVisibleLogicalRangeChange((range: LogicalRange | null) => {
    if (syncing || !range) return;
    syncing = true;
    source.timeScale().setVisibleLogicalRange(range);
    syncing = false;
  });
}

function drawRegions(chart: IChartApi, layer: HTMLDivElement | null, regions: Region[]) {
  if (!layer) return;
  layer.innerHTML = "";
  regions.forEach((region) => {
    const start = chart.timeScale().timeToCoordinate(region.start as Time);
    const end = chart.timeScale().timeToCoordinate(region.end as Time);
    if (start === null || end === null) return;
    const left = Math.min(start, end);
    const width = Math.abs(end - start);
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
