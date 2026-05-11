import {
  createChart,
  LineStyle,
  type IChartApi,
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

import { displayName } from "../format";
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
type ChartMarker = SeriesMarker<Time>;
type LegendPane = "price" | "oscillator";
type OscillatorPaneRuntime = {
  chart: IChartApi;
  renderer: AnySeriesApi;
  valuesByTime: Map<number, number>;
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
  options?: ChartOptions;
};

export type ChartOptions = {
  feature_columns: string[];
  feature_groups: string[];
  standard_indicators: string[];
  supervision_groups: string[];
};

export type ChartPanelHandle = {
  fitFirstDay: () => void;
  fitRecent: () => void;
  toggleFullscreen: () => void;
};

type ChartPanelProps = {
  featureOptions: string[];
  indicatorOptions: string[];
  onTickerChange: (value: string) => void;
  onTimeframeChange: (value: string) => void;
  onVisibleColumnsChange: (value: string[]) => void;
  payload: ChartPayload | null;
  ticker: string;
  timeframe: string;
  timeframes: string[];
  visibleColumns: string[];
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
const MARKER_REFERENCE_CANDLE_SIZE = defaultChartAppearanceSettings.candleSize;
const MIN_MARKER_SIZE = 0.45;

type ChartPalette = {
  background: string;
  grid: string;
  text: string;
};

export const ChartPanel = forwardRef<ChartPanelHandle, ChartPanelProps>(({
  featureOptions,
  indicatorOptions,
  onTickerChange,
  onTimeframeChange,
  onVisibleColumnsChange,
  payload,
  ticker,
  timeframe,
  timeframes,
  visibleColumns
}, ref) => {
  const priceRef = useRef<HTMLDivElement | null>(null);
  const oscillatorPaneRefs = useRef<Map<string, HTMLDivElement>>(new Map());
  const shellRef = useRef<HTMLDivElement | null>(null);
  const priceLayerRef = useRef<HTMLDivElement | null>(null);
  const priceChartRef = useRef<IChartApi | null>(null);
  const oscillatorChartRefs = useRef<Map<string, IChartApi>>(new Map());
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const indicatorSeriesRef = useRef<Map<string, AnySeriesApi>>(new Map());
  const indicatorSourceRef = useRef<Map<string, ChartSeries>>(new Map());
  const [draftTicker, setDraftTicker] = useState(ticker.toUpperCase());
  const [columnMenuOpen, setColumnMenuOpen] = useState(false);
  const [fullscreen, setFullscreen] = useState(false);
  const [chartSettingsOpen, setChartSettingsOpen] = useState(false);
  const [chartSettings, setChartSettings] = useState<ChartAppearanceSettings>(() => loadChartAppearanceSettings());
  const [legendSettings, setLegendSettings] = useState<LegendSettingsMap>(() => loadLegendSettings());
  const [themeSignature, setThemeSignature] = useState(() => document.documentElement.dataset.shellTheme ?? "");

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
    if (!columnMenuOpen) return;
    const closeOnOutsidePointer = (event: PointerEvent) => {
      const target = event.target as HTMLElement | null;
      if (target?.closest(".chart-column-select")) return;
      setColumnMenuOpen(false);
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setColumnMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsidePointer);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeOnOutsidePointer);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [columnMenuOpen]);

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
    let disposed = false;
    const palette = readChartPalette();
    priceRef.current.innerHTML = "";
    oscillatorPaneRefs.current.forEach((pane) => {
      pane.innerHTML = "";
    });
    oscillatorChartRefs.current.clear();
    indicatorSeriesRef.current.clear();
    indicatorSourceRef.current.clear();
    const priceChart = createChart(priceRef.current, chartOptions(priceRef.current.clientWidth, priceRef.current.clientHeight, false, palette, chartSettings));
    priceChartRef.current = priceChart;
    const candleSeries = priceChart.addCandlestickSeries({
      ...candleSeriesOptions(chartSettings),
      priceLineVisible: true
    });
    candleRef.current = candleSeries;
    candleSeries.setData(payload.candles as never);
    if (payload.markers.length) candleSeries.setMarkers(markerDataForSettings(payload.markers, chartSettings));
    const volume = priceChart.addHistogramSeries({ priceFormat: { type: "volume" }, priceScaleId: "", base: 0 });
    volume.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volume.setData(volumeDataForSettings(payload, chartSettings) as never);
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

    const oscillatorPanes: OscillatorPaneRuntime[] = [];
    buildOscillatorPaneGroups(payload.oscillator_series).forEach((group) => {
      const pane = oscillatorPaneRefs.current.get(group.key);
      if (!pane) return;
      const oscChart = createChart(pane, chartOptions(pane.clientWidth, pane.clientHeight, true, palette, chartSettings));
      oscillatorChartRefs.current.set(group.key, oscChart);
      let primaryRenderer: AnySeriesApi | null = null;
      let primaryValuesByTime = new Map<number, number>();
      group.series.forEach((series) => {
        const key = legendSeriesKey("oscillator", series);
        const settings = resolveLegendSettings(legendSettings, key, series);
        const renderer = addChartSeries(oscChart, series, settings);
        renderer.setData(seriesDataForSettings(series, settings) as never);
        indicatorSeriesRef.current.set(key, renderer);
        indicatorSourceRef.current.set(key, series);
        if (!primaryRenderer) {
          primaryRenderer = renderer;
          primaryValuesByTime = new Map(series.data.map((point) => [point.time, point.value]));
        }
      });
      if (!primaryRenderer) return;
      oscillatorPanes.push({
        chart: oscChart,
        renderer: primaryRenderer,
        valuesByTime: primaryValuesByTime
      });
    });
    const rangeCleanups = oscillatorPanes.map((pane) => syncRanges(priceChart, pane.chart));
    const closeByTime = new Map(payload.candles.map((candle) => [candle.time, candle.close]));
    const crosshairCleanup = syncCrosshairs(priceChart, oscillatorPanes, candleSeries, closeByTime);
    const draw = () => {
      if (disposed) return;
      drawRegions(priceChart, priceLayerRef.current, payload.regions, payload.candles, chartSettings);
    };
    priceChart.timeScale().subscribeVisibleLogicalRangeChange(draw);
    const initialFitTimer = window.setTimeout(() => {
      if (disposed) return;
      fitFirstDay(priceChart, payload.candles);
      draw();
    }, 20);
    const observer = new ResizeObserver(() => {
      resizeCharts();
      draw();
    });
    if (shellRef.current) observer.observe(shellRef.current);
    return () => {
      disposed = true;
      window.clearTimeout(initialFitTimer);
      observer.disconnect();
      crosshairCleanup();
      rangeCleanups.forEach((cleanup) => cleanup());
      priceChart.timeScale().unsubscribeVisibleLogicalRangeChange(draw);
      priceChart.remove();
      oscillatorPanes.forEach((pane) => pane.chart.remove());
      priceChartRef.current = null;
      oscillatorChartRefs.current.clear();
      indicatorSeriesRef.current.clear();
      indicatorSourceRef.current.clear();
    };
  }, [payload, themeSignature, chartSettings]);

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

  const priceLegendItems = buildSeriesLegendItems(payload?.overlay_series ?? [], "price", legendSettings);

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
        <span className="toolbar-divider" />
        <IndicatorFeatureSelect
          featureOptions={featureOptions}
          indicatorOptions={indicatorOptions}
          onChange={onVisibleColumnsChange}
          onOpenChange={(value) => {
            setColumnMenuOpen(value);
            if (value) setChartSettingsOpen(false);
          }}
          open={columnMenuOpen}
          values={visibleColumns}
        />
        <div className="toolbar-spacer" />
        <button
          className="toolbar-button"
          data-chart-settings-trigger="true"
          type="button"
          title="Chart settings"
          onClick={() => {
            setColumnMenuOpen(false);
            setChartSettingsOpen((value) => !value);
          }}
        >
          <Settings size={15} />
        </button>
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
      {chartSettingsOpen ? (
        <ChartSettingsPopover
          onChange={updateChartSettings}
          onClose={() => setChartSettingsOpen(false)}
          onReset={resetChartSettings}
          settings={chartSettings}
        />
      ) : null}
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
            />
          </div>
          {buildOscillatorPaneGroups(payload.oscillator_series).map((group) => {
            return (
              <div className="chart-osc" key={group.key}>
                <div className="chart-pane-canvas" ref={(node) => setOscillatorPaneRef(group.key, node)} />
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
  const [collapsed, setCollapsed] = useState(false);
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
  featureOptions,
  indicatorOptions,
  onChange,
  onOpenChange,
  open,
  values
}: {
  featureOptions: string[];
  indicatorOptions: string[];
  onChange: (value: string[]) => void;
  onOpenChange: (value: boolean) => void;
  open: boolean;
  values: string[];
}) {
  const indicatorSet = new Set(indicatorOptions);
  const visibleFeatures = featureOptions.filter((option) => !indicatorSet.has(option));
  const visibleOptions = [...indicatorOptions, ...visibleFeatures];
  const selected = new Set(values);
  const selectedCount = visibleOptions.filter((option) => selected.has(option)).length;

  const toggleValue = (value: string) => {
    const nextSelected = new Set(values);
    if (nextSelected.has(value)) {
      nextSelected.delete(value);
    } else {
      nextSelected.add(value);
    }
    if (!nextSelected.size) return;
    const ordered = visibleOptions.filter((option) => nextSelected.has(option));
    onChange(ordered);
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
          <div className="chart-column-menu-title">Indicators</div>
          <div className="chart-column-menu-list">
            {indicatorOptions.map((option) => (
              <button
                className={selected.has(option) ? "chart-column-menu-item selected" : "chart-column-menu-item"}
                key={option}
                onClick={() => toggleValue(option)}
                type="button"
              >
                <span className="chart-column-menu-check">{selected.has(option) ? <Check size={13} /> : null}</span>
                <span>{displayName(option)}</span>
              </button>
            ))}
          </div>
          <div className="chart-column-menu-divider" />
          <div className="chart-column-menu-title">Features</div>
          <div className="chart-column-menu-list feature-list">
            {visibleFeatures.length ? (
              visibleFeatures.map((option) => (
                <button
                  className={selected.has(option) ? "chart-column-menu-item selected" : "chart-column-menu-item"}
                  key={option}
                  onClick={() => toggleValue(option)}
                  type="button"
                >
                  <span className="chart-column-menu-check">{selected.has(option) ? <Check size={13} /> : null}</span>
                  <span>{displayName(option)}</span>
                </button>
              ))
            ) : (
              <div className="chart-column-menu-empty">No feature columns for this session.</div>
            )}
          </div>
        </div>
      ) : null}
    </div>
  );
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

function buildOscillatorPaneGroups(series: ChartSeries[]): OscillatorPaneGroup[] {
  const groups = new Map<string, ChartSeries[]>();
  series.forEach((item) => {
    const key = oscillatorPaneKey(item);
    groups.set(key, [...(groups.get(key) ?? []), item]);
  });
  return Array.from(groups, ([key, items]) => ({ key, series: items }));
}

function oscillatorPaneKey(series: ChartSeries) {
  const column = series.column.toLowerCase();
  if (column.startsWith("macd_")) return "oscillator:macd";
  return legendSeriesKey("oscillator", series);
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

function markerDataForSettings(markers: ChartMarker[], settings: ChartAppearanceSettings): ChartMarker[] {
  const markerSize = Math.max(MIN_MARKER_SIZE, Math.min(1, MARKER_REFERENCE_CANDLE_SIZE / Math.max(1, settings.candleSize)));
  return markers.map((marker) => ({
    ...marker,
    size: Math.min(marker.size ?? 1, markerSize)
  }));
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

function addChartSeries(chart: IChartApi, series: ChartSeries, settings: Required<LegendSeriesSettings>): AnySeriesApi {
  if (series.style === "histogram") {
    return chart.addHistogramSeries({ color: settings.color, priceLineVisible: false, title: series.label, visible: settings.visible });
  }
  return chart.addLineSeries({
    color: settings.color,
    lineStyle: toChartLineStyle(settings.lineStyle),
    lineWidth: toLineWidth(settings.lineWidth),
    priceLineVisible: false,
    title: series.label,
    visible: settings.visible
  });
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
    rightPriceScale: { borderColor: palette.grid },
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
      if (typeof value === "number" && Number.isFinite(value)) {
        pane.chart.setCrosshairPosition(value, time, pane.renderer);
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

function drawRegions(chart: IChartApi, layer: HTMLDivElement | null, regions: Region[], candles: Candle[], settings: ChartAppearanceSettings) {
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
  drawDaySeparators(chart, layer, candles, settings, barWidth);
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
