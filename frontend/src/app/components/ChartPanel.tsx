import {
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LogicalRange,
  type Time
} from "lightweight-charts";
import { forwardRef, useEffect, useImperativeHandle, useRef, useState } from "react";

type Candle = { time: number; open: number; high: number; low: number; close: number };
type ChartSeries = {
  column: string;
  label: string;
  style: "line" | "histogram";
  color: string;
  lineWidth: number;
  data: Array<{ time: number; value: number }>;
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

const candleSettings = {
  upColor: "#33E42A",
  downColor: "#FD0E50",
  borderUpColor: "#1DB914",
  borderDownColor: "#CB093F",
  wickUpColor: "#4DC746",
  wickDownColor: "#C52A55"
};

export const ChartPanel = forwardRef<ChartPanelHandle, { payload: ChartPayload | null }>(({ payload }, ref) => {
  const priceRef = useRef<HTMLDivElement | null>(null);
  const oscRef = useRef<HTMLDivElement | null>(null);
  const shellRef = useRef<HTMLDivElement | null>(null);
  const priceLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const oscChartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const [fullscreen, setFullscreen] = useState(false);

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
    if (!priceRef.current || !payload) return;
    priceRef.current.innerHTML = "";
    if (oscRef.current) oscRef.current.innerHTML = "";
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight));
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
      oscChart = createChart(oscRef.current, chartOptions(oscRef.current.clientWidth, oscRef.current.clientHeight, true));
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
  }, [payload]);

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

  if (!payload || !payload.candles.length) {
    return <div className="empty-state panel">No chart data for the selected ticker/session/timeframe.</div>;
  }
  return (
    <div className={fullscreen ? "chart-shell fullscreen" : "chart-shell"} ref={shellRef}>
      <div className="chart-price" ref={priceRef}>
        <div className="session-layer" ref={priceLayerRef} />
      </div>
      {payload.oscillator_series.length ? <div className="chart-osc" ref={oscRef} /> : null}
    </div>
  );
});

function chartOptions(width: number, height: number, compact = false) {
  return {
    width: Math.max(320, width),
    height: Math.max(160, height),
    layout: { background: { color: "#ffffff" }, textColor: "#344054" },
    grid: {
      vertLines: { color: "#f2f4f7" },
      horzLines: { color: "#f2f4f7" }
    },
    crosshair: { mode: 0 },
    rightPriceScale: { borderColor: "#eaecf0" },
    timeScale: {
      borderColor: "#eaecf0",
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
