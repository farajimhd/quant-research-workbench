import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { BookOpen, CircleHelp, Database, Filter, Search, SlidersHorizontal, Tags } from "lucide-react";
import katex from "katex";
import "katex/dist/katex.min.css";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartPayload, type ChartReference } from "../app/components/ChartPanel";
import { DataTable, type BackendTableQuery } from "../app/components/DataTable";
import { MetricStrip } from "../app/components/MetricStrip";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { Tabs } from "../app/components/Tabs";
import { displayName } from "../app/format";
import { useViewportFillPanel } from "../app/hooks/useViewportFillPanel";

type Scope = {
  raw_root: string;
  processed_root: string;
  start_date: string;
  end_date: string;
};

type RecordRow = {
  key: string;
  group: string;
  timeframe: string;
  session_date: string;
  rows: number;
  columns: string[];
  column_count: number;
  size: string;
  built_at: string;
  exists: boolean;
  path: string;
};

type ReviewPayload = {
  processed_root: string;
  manifest: Record<string, unknown>;
  metrics: Record<string, number | string>;
  records: RecordRow[];
  group_summary: Record<string, unknown>[];
  timeframe_summary: Record<string, unknown>[];
  latest: RecordRow[];
};

type ConfigDefaults = {
  feature_groups: string[];
};
type SchemaField = {
  column: string;
  dtype: string;
  kind: "boolean" | "numeric" | "other" | "temporal" | "text";
};
type PreviewSample = {
  columns: string[];
  has_more?: boolean;
  row_count: number;
  row_limit: number;
  row_offset: number;
  rows: Record<string, unknown>[];
  scanned_artifacts?: number;
};
type PreviewQueryState = {
  columns: string;
  conditions: BackendTableQuery;
  endDate: string;
  rowLimit: number;
  startDate: string;
  tickers: string;
  timeframe: string;
};
type ScannerQueryState = {
  backendQuery: BackendTableQuery;
  barTime: string;
  columns: string;
  featureGroups: string;
  rowLimit: number;
  rowOffset: number;
  sessionDate: string;
  timeframe: string;
};
type ScannerSnapshot = {
  bar_time: string;
  columns: string[];
  feature_groups: string[];
  has_more?: boolean;
  reason?: string;
  row_count: number;
  row_limit: number;
  row_offset: number;
  rows: Record<string, unknown>[];
  session_date: string;
  timeframe: string;
  total_columns: number;
};
type CatalogKnowledge = {
  shortDescription: string;
  detailedDescription: string;
  theory: string;
  interpretation: string;
  caveats: string[];
  equations: Array<{ markdown: string; title: string; variables: Record<string, string> }>;
};
type CatalogPresentation = Record<string, unknown>;
type CatalogItem = ChartCatalogItem & {
  dataShape?: string;
  dtype?: string;
  groups?: string[];
  knowledge?: CatalogKnowledge;
  leakage?: Record<string, unknown>;
  presentation?: CatalogPresentation;
  semantics?: Record<string, unknown>;
};
type CatalogMethod = {
  id: string;
  title: string;
  category: string;
  method?: string;
  knowledge?: CatalogKnowledge;
  presentation?: CatalogPresentation;
  thesis?: string;
};
type CatalogDisplayItem = ChartDisplayItem & {
  dataShape?: string;
  groups?: string[];
  knowledge?: CatalogKnowledge;
  presentation?: CatalogPresentation;
};
type PresentationPreset = {
  dataShapes?: string[];
  description?: string;
  label?: string;
  lockedFields?: string[];
  styleFields?: string[];
  target?: string;
};
type CatalogPayload = {
  catalogVersion: number;
  columns: CatalogItem[];
  displayItems: CatalogDisplayItem[];
  presentationOptions: Record<string, string[]>;
  presentationPresets?: Record<string, PresentationPreset>;
  scanners: CatalogMethod[];
  supervisionMethods: CatalogMethod[];
};
type CatalogPreviewSample = {
  bar_id?: string;
  session_date?: string;
  ticker?: string;
  timeframe?: string;
  time?: number | null;
};
type CatalogPreviewPayload = {
  payload?: ChartPayload | null;
  reason?: string;
  sample?: CatalogPreviewSample;
  sampled: boolean;
};
type CatalogKindFilter = "all" | "display" | "columns" | "methods" | "scanners";
type CatalogCardItem = CatalogItem & {
  catalogKind: "display" | "columns" | "methods" | "scanners";
  groupLabel: string;
  presentationType: string;
  sourceColumns?: string[];
  sourceLabel: string;
  summary: string;
};
type PreviewChartTarget = {
  rangeMode?: "session" | "surrounding";
  record: RecordRow;
  row: Record<string, unknown>;
};
type PreviewBarContext = {
  barId: string;
  minuteOfDay?: number;
  sessionDate: string;
  ticker: string;
  time?: number;
  timeframe: string;
  utcText: string;
};
type ParsedProviderBarId = Pick<PreviewBarContext, "barId" | "ticker" | "time" | "timeframe" | "utcText">;

const tabs = ["Overview", "Preview", "Scanner", "Chart", "Coverage", "Artifacts", "Schema", "Catalog"];
const DEFAULT_CHART_FEATURE_GROUPS = ["core", "momentum"];
const DEFAULT_CHART_DISPLAY_ITEMS = ["indicator.vwap", "indicator.tema_trend", "indicator.macd"];
const DEFAULT_CHART_MIN_CONFIDENCE = 0.7;
const CHART_DISPLAY_ITEMS_NONE = "__none__";
const PREVIEW_PAGE_SIZE = 1000;
const PRESENTATION_TYPE_ORDER = ["price_overlay", "composite_group", "lower_pane_line", "histogram_pane", "event_marker", "anchored_zone", "continuous_band", "background_state", "data_only", "other"];
const PRESENTATION_TYPE_LABELS: Record<string, string> = {
  all: "All",
  anchored_zone: "Anchored zone",
  background_state: "Background state",
  composite_group: "Grouped display",
  continuous_band: "Continuous band",
  data_only: "Data only",
  event_marker: "Event marker",
  histogram_pane: "Histogram pane",
  lower_pane_line: "Lower-pane line",
  other: "Other",
  price_overlay: "Price overlay",
  table_only: "Data only",
};
const FIXED_LOWER_PANES = ["macd", "pane_2", "pane_3"];
const NON_MACD_LOWER_PANES = ["pane_2", "pane_3"];
const PANE_LABELS: Record<string, string> = {
  macd: "MACD Pane",
  pane_2: "Pane 2",
  pane_3: "Pane 3",
};
const STYLE_COLOR_OPTIONS = [
  { label: "Black", value: "#030213" },
  { label: "Navy", value: "#1E3A5F" },
  { label: "Blue", value: "#2563EB" },
  { label: "Teal", value: "#0E7490" },
  { label: "Green", value: "#067647" },
  { label: "Amber", value: "#B7791F" },
  { label: "Orange", value: "#C2410C" },
  { label: "Red", value: "#B42318" },
  { label: "Candle direction", value: "inherit_candle_direction" },
];
const CATALOG_PREVIEW_CANDLES = [
  { close: 118, high: 91, low: 123, open: 103, volume: 18, x: 38 },
  { close: 78, high: 72, low: 108, open: 102, volume: 38, x: 64 },
  { close: 69, high: 60, low: 84, open: 80, volume: 54, x: 90 },
  { close: 82, high: 74, low: 94, open: 72, volume: 31, x: 116 },
  { close: 88, high: 78, low: 96, open: 82, volume: 20, x: 142 },
  { close: 96, high: 86, low: 105, open: 90, volume: 16, x: 168 },
  { close: 90, high: 83, low: 102, open: 98, volume: 22, x: 194 },
  { close: 78, high: 72, low: 92, open: 90, volume: 27, x: 220 },
  { close: 84, high: 76, low: 93, open: 80, volume: 19, x: 246 },
  { close: 76, high: 70, low: 88, open: 84, volume: 17, x: 272 },
  { close: 68, high: 62, low: 78, open: 77, volume: 24, x: 298 },
  { close: 60, high: 54, low: 70, open: 69, volume: 29, x: 324 },
  { close: 62, high: 55, low: 72, open: 58, volume: 22, x: 350 },
];
const CATALOG_PRICE_LINE_POINTS = "34,112 64,87 90,70 116,76 142,84 168,91 194,88 220,80 246,83 272,78 298,70 324,64 350,66";
const CATALOG_OSCILLATOR_LINE_POINTS = "34,199 64,184 90,176 116,181 142,190 168,202 194,207 220,200 246,189 272,182 298,180 324,174 350,178";
const CATALOG_PREVIEW_PANE_ZERO_Y = 194;
const CATALOG_PREVIEW_PANE_RANGE_TOP = 170;
const CATALOG_PREVIEW_PANE_RANGE_BOTTOM = 218;
const CATALOG_HISTOGRAM_BARS = [
  { value: 9, x: 38 },
  { value: 17, x: 64 },
  { value: 28, x: 90 },
  { value: 20, x: 116 },
  { value: 8, x: 142 },
  { value: -9, x: 168 },
  { value: -17, x: 194 },
  { value: -12, x: 220 },
  { value: 7, x: 246 },
  { value: 12, x: 272 },
  { value: 18, x: 298 },
  { value: 25, x: 324 },
  { value: 15, x: 350 },
];
const PRESENTATION_HELP = {
  selectable: "Controls whether this item appears in the chart Indicators & Features picker. Off keeps the catalog contract but hides it from chart selection.",
  defaultVisible: "Adds this item to charts automatically when the selected artifact contains the required column or label group.",
  legend: "Shows a legend row and live value for this item when it is drawn on the chart or in a lower pane.",
  chartRole: "Chooses the display type. The provider preset controls which style fields are valid, such as line controls for Price Overlay, zone controls for Anchored Zone, and value formatting for Data Only.",
  pane: "Chooses one of the fixed lower panes. MACD Pane is reserved for MACD only; other oscillators can use Pane 2 or Pane 3.",
  lineStyle: "Chooses the stroke pattern for line-like items. Solid is the default; dashed or dotted are for separating related overlays.",
  color: "Default display color. Use a hex color for a fixed line or marker color; inherit_candle_direction follows the candle up/down color where supported.",
  lineWidth: "Controls line or band stroke thickness in pixels. Larger values make the item visually heavier.",
  opacity: "Controls visual strength without changing the value. Important long-horizon overlays can be thicker but more transparent so they remain visible without dominating candles.",
  bandFillColor: "Controls the translucent fill used inside a band. This is separate from the band boundary stroke color.",
  bandFillOpacity: "Controls how visible the band shade is. Lower values keep candles readable; higher values make the band easier to scan.",
  borderOpacity: "Controls how strong the anchored-zone edge is. Keep this low so the boundary clarifies the zone without competing with candles.",
  zoneHeightMode: "Chooses whether an anchored item uses its true price range or a fixed-pixel level band. Swing lows/highs and similar point events should use fixed pixels; FVGs and order blocks should use price range.",
  minPixelHeight: "Minimum visual height for fixed-pixel anchored bands. Use small values for swing and break events so they read like thick translucent levels.",
  maxPixelHeight: "Maximum visual height for fixed-pixel anchored bands. This keeps point events from becoming visually oversized when the chart scale changes.",
  zonePaddingBps: "Adds price-based vertical padding for price-range zones. Fixed-pixel point events ignore this and use their semantic pixel height instead.",
  labelMode: "Controls whether event markers show no label, a short semantic label such as HH, the value, or the full catalog title.",
  labelText: "Optional custom label for marker and text-label displays. Leave blank to use the semantic default for the event.",
  markerSize: "Controls the marker symbol size on the candle chart.",
  precision: "Controls how many decimal places are shown in legends, tooltips, and readouts for numeric values.",
  markerShape: "Chooses the symbol used when the item renders as markers: circle, arrowUp, arrowDown, or square.",
  markerPosition: "Chooses where marker symbols sit relative to the candle: aboveBar, belowBar, or inBar.",
  valueFormat: "Chooses how values are formatted for the user: price, percent, number, integer, boolean, datetime, or text.",
};

type CatalogPresentationPatchValue = string | number | boolean;

export function MarketDataReviewPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(false);
  const [catalogError, setCatalogError] = useState("");
  const [activeTab, setActiveTab] = useState(tabs[0]);
  const [editingScope, setEditingScope] = useState(false);

  useEffect(() => {
    api<Scope>("/api/market-data/scope").then((payload) => {
      setScope(payload);
      setDraft(payload);
    });
  }, []);

  useEffect(() => {
    if (!scope) return;
    api<ReviewPayload>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then(setReview);
    let active = true;
    setCatalog(null);
    setCatalogLoading(true);
    setCatalogError("");
    api<CatalogPayload>(`/api/market-data/catalog${query({ processed_root: scope.processed_root })}`)
      .then((payload) => {
        if (!active) return;
        setCatalog(payload);
      })
      .catch((error: Error) => {
        if (!active) return;
        setCatalogError(error.message || "Catalog request failed.");
      })
      .finally(() => {
        if (active) setCatalogLoading(false);
      });
    return () => {
      active = false;
    };
  }, [scope]);

  function applyScope() {
    if (!draft) return;
    setScope(draft);
    setEditingScope(false);
  }

  return (
    <>
      <PageIntro
        className="review-data-intro"
        groupLabel="Market Data"
        title="Review Data"
        description="Inspect saved provider artifacts, coverage, schemas, sampled rows, and chart-ready feature overlays."
        actions={scope ? <ReviewScopeCard scope={scope} manifest={review?.manifest} onEdit={() => setEditingScope(true)} /> : null}
      />
      <MetricStrip
        items={[
          { label: "Artifacts", value: review?.metrics.artifacts ?? 0, kind: "number" },
          { label: "Groups", value: review?.metrics.groups ?? 0, kind: "number" },
          { label: "Frames", value: review?.metrics.timeframes ?? 0, kind: "number" },
          { label: "Sessions", value: review?.metrics.sessions ?? 0, kind: "number" },
          { label: "Rows", value: review?.metrics.rows ?? 0, kind: "number" },
          { label: "Size", value: review?.metrics.size_bytes ?? 0, kind: "bytes" },
          { label: "Schema", value: String(review?.manifest.schema_version ?? "-"), kind: "status" },
          { label: "Status", value: review?.records.length ? "ready" : "missing", kind: "status" }
        ]}
      />
      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
      {activeTab === "Overview" ? <Overview review={review} /> : null}
      {activeTab === "Coverage" && scope && review ? <Coverage scope={scope} records={review.records} /> : null}
      {activeTab === "Chart" && scope && review ? <ChartTab catalog={catalog} scope={scope} records={review.records} /> : null}
      {activeTab === "Artifacts" && review ? <Artifacts records={review.records} /> : null}
      {activeTab === "Preview" && scope && review ? <Preview catalog={catalog} scope={scope} records={review.records} /> : null}
      {activeTab === "Scanner" && scope && review ? <ScannerTab catalog={catalog} scope={scope} records={review.records} /> : null}
      {activeTab === "Schema" && scope && review ? <Schema scope={scope} records={review.records} /> : null}
      {activeTab === "Catalog" && scope ? <CatalogTab catalog={catalog} catalogError={catalogError} catalogLoading={catalogLoading} scope={scope} onCatalogChange={setCatalog} /> : null}
      {editingScope && draft ? (
        <Modal title="Update Review Scope" onClose={() => setEditingScope(false)}>
          <div className="form-grid">
            <Field label="Processed root" value={draft.processed_root} onChange={(value) => setDraft({ ...draft, processed_root: value })} />
            <Field label="Raw root" value={draft.raw_root} onChange={(value) => setDraft({ ...draft, raw_root: value })} />
            <Field label="Start" type="date" value={draft.start_date} onChange={(value) => setDraft({ ...draft, start_date: value })} />
            <Field label="End" type="date" value={draft.end_date} onChange={(value) => setDraft({ ...draft, end_date: value })} />
          </div>
          <div className="modal-actions">
            <button className="button" onClick={() => setEditingScope(false)} type="button">Cancel</button>
            <button className="button primary" onClick={applyScope} type="button">Apply</button>
          </div>
        </Modal>
      ) : null}
    </>
  );
}

function ReviewScopeCard({ scope, manifest, onEdit }: { scope: Scope; manifest?: Record<string, unknown>; onEdit: () => void }) {
  return (
    <div className="scope-card">
      <div className="scope-card-header">
        <div className="scope-title">Data Scope</div>
        <span className="meta-tag">Updated {String(manifest?.updated_at ?? "-")}</span>
        <button className="text-button scope-edit-button" onClick={onEdit} type="button">Edit scope</button>
      </div>
      <div className="scope-card-grid">
        <ScopeItem className="scope-item-small" label="Start" value={scope.start_date} />
        <ScopeItem className="scope-item-small" label="End" value={scope.end_date} />
        <ScopeItem className="scope-item-small" label="Artifacts" value={String(manifest?.artifact_count ?? "-")} />
        <ScopeItem className="scope-item-root" label="Raw root" value={scope.raw_root} />
        <ScopeItem className="scope-item-root" label="Processed root" value={scope.processed_root} />
      </div>
    </div>
  );
}

function Overview({ review }: { review: ReviewPayload | null }) {
  if (!review) return <div className="empty-state">No provider artifacts found.</div>;
  return (
    <div className="split-row">
      <section className="panel">
        <h2>Groups</h2>
        <DataTable rows={review.group_summary} />
      </section>
      <section className="panel">
        <h2>Timeframes</h2>
        <DataTable rows={review.timeframe_summary} />
      </section>
      <section className="panel" style={{ gridColumn: "1 / -1" }}>
        <h2>Latest Artifacts</h2>
        <DataTable rows={review.latest} columns={["built_at", "group", "timeframe", "session_date", "rows", "size", "exists", "path"]} />
      </section>
    </div>
  );
}

function Coverage({ scope, records }: { scope: Scope; records: RecordRow[] }) {
  const groups = useMemo(() => Array.from(new Set(records.map((record) => record.group))).sort(), [records]);
  const [group, setGroup] = useState(groups[0] ?? "bars");
  const [rows, setRows] = useState<Record<string, unknown>[]>([]);
  const fillPanel = useViewportFillPanel(`${group}:${rows.length}`);
  useEffect(() => {
    if (!group) return;
    api<{ rows: Record<string, unknown>[] }>(
      `/api/market-data/coverage${query({ processed_root: scope.processed_root, group, start_date: scope.start_date, end_date: scope.end_date })}`
    ).then((payload) => setRows(payload.rows));
  }, [scope, group]);
  return (
    <section className="panel coverage-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="toolbar">
        <div className="field" style={{ width: 260 }}>
          <label>Group</label>
          <select value={group} onChange={(event) => setGroup(event.target.value)}>
            {groups.map((item) => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </div>
      </div>
      <DataTable rows={rows} />
    </section>
  );
}

function ChartTab({ catalog, scope, records }: { catalog: CatalogPayload | null; scope: Scope; records: RecordRow[] }) {
  const barRecords = useMemo(() => records.filter((record) => record.group === "bars" && record.exists), [records]);
  const availableSessions = useMemo(() => Array.from(new Set(barRecords.map((record) => record.session_date))).sort(), [barRecords]);
  const defaultRange = useMemo(() => {
    const first = availableSessions[0] ?? scope.start_date;
    const last = availableSessions[availableSessions.length - 1] ?? scope.end_date;
    return {
      start: availableSessions.find((item) => item >= scope.start_date) ?? first,
      end: [...availableSessions].reverse().find((item) => item <= scope.end_date) ?? last
    };
  }, [availableSessions, scope.start_date, scope.end_date]);
  const [startDate, setStartDate] = useState(defaultRange.start);
  const [endDate, setEndDate] = useState(defaultRange.end);
  const rangeStart = startDate <= endDate ? startDate : endDate;
  const rangeEnd = endDate >= startDate ? endDate : startDate;
  const timeframes = useMemo(
    () =>
      Array.from(
        new Set(
          barRecords
            .filter((record) => record.session_date >= rangeStart && record.session_date <= rangeEnd)
            .map((record) => record.timeframe)
        )
      ).sort(timeframeSort),
    [barRecords, rangeEnd, rangeStart]
  );
  const [timeframe, setTimeframe] = useState(timeframes[0] ?? "1m");
  const [ticker, setTicker] = useState("");
  const [featureGroups, setFeatureGroups] = useState(DEFAULT_CHART_FEATURE_GROUPS);
  const [visibleColumns, setVisibleColumns] = useState(DEFAULT_CHART_DISPLAY_ITEMS);
  const [payload, setPayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");

  useEffect(() => {
    if (!availableSessions.length) return;
    setStartDate((current) => (current && current >= defaultRange.start && current <= defaultRange.end ? current : defaultRange.start));
    setEndDate((current) => (current && current >= defaultRange.start && current <= defaultRange.end ? current : defaultRange.end));
  }, [availableSessions.length, defaultRange.end, defaultRange.start]);

  useEffect(() => {
    api<ConfigDefaults>("/api/config/defaults").then((defaults) => {
      if (defaults.feature_groups?.length) setFeatureGroups(defaults.feature_groups);
    });
  }, []);

  useEffect(() => {
    const defaults = defaultCatalogDisplayItems(catalog);
    if (!defaults.length || !sameList(visibleColumns, DEFAULT_CHART_DISPLAY_ITEMS)) return;
    setVisibleColumns(defaults);
  }, [catalog, visibleColumns]);

  useEffect(() => {
    if (!rangeStart || !rangeEnd || !timeframes.length) return;
    if (!timeframes.includes(timeframe)) setTimeframe(timeframes[0]);
  }, [rangeEnd, rangeStart, timeframes, timeframe]);

  useEffect(() => {
    if (!rangeStart || !rangeEnd || !timeframe || ticker.trim()) return;
    let active = true;
    api<{ ticker: string }>(
      `/api/market-data/chart/default-ticker${query({ processed_root: scope.processed_root, start_date: rangeStart, end_date: rangeEnd, timeframe })}`
    ).then(
      (result) => {
        if (active) setTicker(result.ticker || "AAPL");
      }
    ).catch((error: Error) => {
      if (active) setChartError(chartRequestErrorMessage(error));
    });
    return () => {
      active = false;
    };
  }, [scope.processed_root, rangeEnd, rangeStart, timeframe, ticker]);

  useEffect(() => {
    if (!rangeStart || !rangeEnd || !timeframe || !ticker.trim()) return;
    let active = true;
    setChartLoading(true);
    setChartError("");
    api<ChartPayload>(
      `/api/market-data/chart${chartRequestQuery({
        processed_root: scope.processed_root,
        start_date: rangeStart,
        end_date: rangeEnd,
        timeframe,
        ticker: ticker.trim().toUpperCase(),
        feature_groups: featureGroups.join(","),
        display_items: chartDisplayItemsRequestValue(visibleColumns),
        min_confidence: DEFAULT_CHART_MIN_CONFIDENCE
      })}`
    ).then((nextPayload) => {
      if (!active) return;
      setPayload(nextPayload);
      const nextFeatureGroups = nextPayload.options?.feature_groups ?? [];
      if (nextFeatureGroups.length && !sameList(nextFeatureGroups, featureGroups)) {
        setFeatureGroups(nextFeatureGroups);
      }
    }).catch((error: Error) => {
      if (!active) return;
      setPayload(null);
      setChartError(chartRequestErrorMessage(error));
    }).finally(() => {
      if (active) setChartLoading(false);
    });
    return () => {
      active = false;
    };
  }, [scope.processed_root, rangeEnd, rangeStart, timeframe, ticker, featureGroups, visibleColumns]);

  function updateChartPeriod(start: string, end: string) {
    if (start <= end) {
      setStartDate(start);
      setEndDate(end);
    } else {
      setStartDate(end);
      setEndDate(start);
    }
  }

  const displayItemOptions = payload?.options?.display_items ?? defaultCatalogDisplayItemOptions(catalog);
  const indicatorOptions = payload?.options?.standard_indicators ?? DEFAULT_CHART_DISPLAY_ITEMS;
  const featureOptions = payload?.options?.feature_columns ?? [];

  if (!barRecords.length) return <div className="empty-state panel">No saved bar artifacts are available for charting.</div>;
  return (
    <section>
      <ChartPanel
        catalogColumns={catalog?.columns ?? []}
        displayItemOptions={displayItemOptions}
        emptyMessage="No chart data for the selected ticker/date range/timeframe."
        errorMessage={chartError}
        featureOptions={featureOptions}
        indicatorOptions={indicatorOptions}
        loading={chartLoading}
        onPeriodChange={updateChartPeriod}
        onTickerChange={setTicker}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={(nextColumns) => updateChartVisibleColumns(nextColumns, setVisibleColumns, setPayload)}
        payload={payload}
        periodEnd={rangeEnd}
        periodMax={availableSessions[availableSessions.length - 1] ?? scope.end_date}
        periodMin={availableSessions[0] ?? scope.start_date}
        periodStart={rangeStart}
        ticker={ticker}
        timeframe={timeframe}
        timeframes={timeframes}
        visibleColumns={visibleColumns}
      />
    </section>
  );
}

function Artifacts({ records }: { records: RecordRow[] }) {
  const [group, setGroup] = useState("All");
  const [timeframe, setTimeframe] = useState("All");
  const [search, setSearch] = useState("");
  const groups = ["All", ...Array.from(new Set(records.map((record) => record.group))).sort()];
  const timeframes = ["All", ...Array.from(new Set(records.map((record) => record.timeframe))).sort(timeframeSort)];
  const rows = records.filter(
    (record) =>
      (group === "All" || record.group === group) &&
      (timeframe === "All" || record.timeframe === timeframe) &&
      (!search || record.path.toLowerCase().includes(search.toLowerCase()))
  );
  const fillPanel = useViewportFillPanel(`${group}:${timeframe}:${search}:${rows.length}`);
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="toolbar">
        <Select label="Group" value={group} options={groups} onChange={setGroup} />
        <Select label="Timeframe" value={timeframe} options={timeframes} onChange={setTimeframe} />
        <div className="field" style={{ width: 360 }}>
          <label>Path contains</label>
          <input value={search} onChange={(event) => setSearch(event.target.value)} />
        </div>
      </div>
      <DataTable rows={rows} columns={["group", "timeframe", "session_date", "rows", "column_count", "size", "built_at", "exists", "path"]} />
    </section>
  );
}

function ScannerTab({ catalog, scope, records }: { catalog: CatalogPayload | null; scope: Scope; records: RecordRow[] }) {
  const barRecords = useMemo(() => records.filter((record) => record.exists && record.group === "bars" && record.timeframe !== "1d"), [records]);
  const sessions = useMemo(() => Array.from(new Set(barRecords.map((record) => record.session_date))).sort(), [barRecords]);
  const [sessionDate, setSessionDate] = useState(sessions.find((item) => item >= scope.start_date && item <= scope.end_date) ?? sessions[0] ?? scope.start_date);
  const timeframes = useMemo(
    () => Array.from(new Set(barRecords.filter((record) => record.session_date === sessionDate).map((record) => record.timeframe))).sort(timeframeSort),
    [barRecords, sessionDate]
  );
  const [timeframe, setTimeframe] = useState(timeframes[0] ?? "1m");
  const featureOptions = useMemo(
    () =>
      Array.from(
        new Set(
          records
            .filter((record) => record.exists && record.group.startsWith("features_") && record.timeframe === timeframe && record.session_date === sessionDate)
            .map((record) => record.group.replace(/^features_/, ""))
        )
      ).sort(),
    [records, sessionDate, timeframe]
  );
  const defaultFeatures = useMemo(() => featureOptions.filter((group) => ["core", "session", "momentum"].includes(group)), [featureOptions]);
  const [barTime, setBarTime] = useState("09:30");
  const [featureGroups, setFeatureGroups] = useState(defaultFeatures.join(","));
  const [columns, setColumns] = useState("");
  const [rowLimit, setRowLimit] = useState(2000);
  const [backendQuery, setBackendQuery] = useState<BackendTableQuery>({ conditions: [], matchMode: "all", sortDirection: "asc" });
  const [filterOpen, setFilterOpen] = useState(false);
  const [scannerRunId, setScannerRunId] = useState(0);
  const [appliedScannerQuery, setAppliedScannerQuery] = useState<ScannerQueryState | null>(null);
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [chartTarget, setChartTarget] = useState<PreviewChartTarget | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const selectedFeatureGroups = useMemo(() => parseCommaList(featureGroups), [featureGroups]);
  const availableColumns = useMemo(() => scannerAvailableColumns(records, sessionDate, timeframe, selectedFeatureGroups), [records, selectedFeatureGroups, sessionDate, timeframe]);
  const queryKey = useMemo(
    () =>
      appliedScannerQuery
        ? JSON.stringify({ ...appliedScannerQuery, backendQuery: cleanPreviewBackendQuery(appliedScannerQuery.backendQuery) })
        : "scanner-idle",
    [appliedScannerQuery]
  );
  const fillPanel = useViewportFillPanel(`${queryKey}:${snapshot?.rows.length ?? 0}`);

  useEffect(() => {
    if (!sessions.length) return;
    setSessionDate((current) => (sessions.includes(current) ? current : sessions[0]));
  }, [sessions]);
  useEffect(() => {
    if (!timeframes.length) return;
    setTimeframe((current) => (timeframes.includes(current) ? current : timeframes[0]));
  }, [timeframes]);
  useEffect(() => {
    if (!featureGroups && defaultFeatures.length) setFeatureGroups(defaultFeatures.join(","));
  }, [defaultFeatures, featureGroups]);
  useEffect(() => {
    if (scannerRunId === 0 || !appliedScannerQuery) return;
    if (!appliedScannerQuery.sessionDate || !appliedScannerQuery.timeframe || !appliedScannerQuery.barTime) return;
    let active = true;
    const cleanedQuery = cleanPreviewBackendQuery(appliedScannerQuery.backendQuery);
    const tableQuery = previewBackendQueryIsActive(cleanedQuery) ? JSON.stringify(cleanedQuery) : undefined;
    setLoading(true);
    setError("");
    api<{ snapshot: ScannerSnapshot }>(
      `/api/market-data/scanner-snapshot${query({
        processed_root: scope.processed_root,
        session_date: appliedScannerQuery.sessionDate,
        timeframe: appliedScannerQuery.timeframe,
        bar_time: appliedScannerQuery.barTime,
        feature_groups: appliedScannerQuery.featureGroups,
        columns: appliedScannerQuery.columns,
        row_limit: appliedScannerQuery.rowLimit,
        row_offset: appliedScannerQuery.rowOffset,
        table_query: tableQuery,
      })}`
    )
      .then((payload) => {
        if (!active) return;
        setSnapshot(payload.snapshot);
      })
      .catch((requestError: Error) => {
        if (!active) return;
        setSnapshot(null);
        setError(requestError.message || "Scanner request failed.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [appliedScannerQuery, scannerRunId, scope.processed_root]);

  if (!barRecords.length) return <div className="empty-state panel">No intraday bar artifacts are available for scanner review.</div>;
  const startRow = snapshot?.rows.length ? (snapshot.row_offset ?? 0) + 1 : 0;
  const endRow = snapshot ? (snapshot.row_offset ?? 0) + snapshot.rows.length : 0;
  const timeframeStep = timeframeMinutes(timeframe);
  const scannerChartRecord = snapshot ? scannerSnapshotRecord(snapshot) : null;
  const loadScannerSnapshot = () => {
    setAppliedScannerQuery({
      backendQuery: cleanPreviewBackendQuery(backendQuery),
      barTime,
      columns,
      featureGroups,
      rowLimit: Math.max(10, Math.min(5000, Math.round(Number(rowLimit) || 2000))),
      rowOffset: 0,
      sessionDate,
      timeframe,
    });
    setScannerRunId((value) => value + 1);
  };
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="scanner-query-shell">
        <div className="toolbar scanner-query-bar">
          <InlineField label="Day" type="date" value={sessionDate} onChange={setSessionDate} />
          <Select label="Timeframe" value={timeframe} options={timeframes.length ? timeframes : [timeframe]} onChange={setTimeframe} />
          <InlineField label="Bar start" type="time" value={barTime} onChange={setBarTime} />
          <button className="button" onClick={() => setBarTime((value) => shiftBarTime(value, -timeframeStep))} type="button">Previous</button>
          <button className="button" onClick={() => setBarTime((value) => shiftBarTime(value, timeframeStep))} type="button">Next</button>
          <button className="button primary" disabled={loading} onClick={loadScannerSnapshot} type="button">Load</button>
          <button className={filterOpen ? "button active" : "button"} onClick={() => setFilterOpen((value) => !value)} type="button">
            <Filter size={16} />
            Filters
          </button>
          <div className="preview-query-summary">
            <span>{snapshot?.rows.length ?? 0} rows</span>
            <span>{snapshot?.feature_groups?.length ? snapshot.feature_groups.join(", ") : "bars only"}</span>
            <span>{snapshot?.total_columns ?? 0} columns</span>
          </div>
        </div>
        {filterOpen ? (
          <ScannerFilterPanel
            availableColumns={availableColumns}
            backendQuery={backendQuery}
            columns={columns}
            featureGroups={featureGroups}
            featureOptions={featureOptions}
            onBackendQueryChange={setBackendQuery}
            onColumnsChange={setColumns}
            onFeatureGroupsChange={setFeatureGroups}
            onRowLimitChange={setRowLimit}
            rowLimit={rowLimit}
          />
        ) : null}
      </div>
      {error ? <div className="preview-sample-status error">Scanner request failed: {error}</div> : null}
      {snapshot?.reason ? <div className="preview-sample-status error">{snapshot.reason}</div> : null}
      {loading ? (
        <div className="preview-sample-status">
          <span className="loading-spinner" aria-hidden="true" />
          Loading scanner snapshot...
        </div>
      ) : null}
      {!snapshot && !loading && !error ? (
        <div className="preview-sample-status">Set the scanner query and press Load to fetch rows.</div>
      ) : null}
      {snapshot ? (
        <div className="preview-page-status">
          <span>
            Showing {startRow.toLocaleString()}-{endRow.toLocaleString()}
            {snapshot.has_more ? " with more rows available" : ""}
          </span>
          <button
            className="table-text-button"
            disabled={!appliedScannerQuery || appliedScannerQuery.rowOffset <= 0 || loading}
            onClick={() => setAppliedScannerQuery((current) => (current ? { ...current, rowOffset: Math.max(0, current.rowOffset - current.rowLimit) } : current))}
            type="button"
          >
            Previous page
          </button>
          <button
            className="table-text-button"
            disabled={!appliedScannerQuery || !snapshot.has_more || loading}
            onClick={() => setAppliedScannerQuery((current) => (current ? { ...current, rowOffset: current.rowOffset + current.rowLimit } : current))}
            type="button"
          >
            Next page
          </button>
        </div>
      ) : null}
      <DataTable
        columns={snapshot?.columns}
        empty={scannerRunId === 0 ? "Load a scanner snapshot to show rows." : "No rows."}
        onRowClick={
          scannerChartRecord && snapshot
            ? (row) => setChartTarget({ rangeMode: "session", record: scannerChartRecord, row: scannerChartRow(row, snapshot) })
            : undefined
        }
        rowAction={
          scannerChartRecord && snapshot
            ? {
                isAvailable: (row) => rowHasChartContext(scannerChartRow(row, snapshot), scannerChartRecord),
                label: "Open scanner row in chart",
                onSelect: (row) => setChartTarget({ rangeMode: "session", record: scannerChartRecord, row: scannerChartRow(row, snapshot) }),
              }
            : undefined
        }
        rows={snapshot?.rows ?? []}
      />
      {chartTarget ? (
        <PreviewRowChartModal
          catalog={catalog}
          key={`${chartTarget.record.key}:${rowStringValue(chartTarget.row, "ticker")}:${rowStringValue(chartTarget.row, "bar_time_market")}:${rowStringValue(chartTarget.row, "minute_of_day")}`}
          onClose={() => setChartTarget(null)}
          records={records}
          scope={scope}
          target={chartTarget}
        />
      ) : null}
    </section>
  );
}

function ScannerFilterPanel({
  availableColumns,
  backendQuery,
  columns,
  featureGroups,
  featureOptions,
  onBackendQueryChange,
  onColumnsChange,
  onFeatureGroupsChange,
  onRowLimitChange,
  rowLimit,
}: {
  availableColumns: string[];
  backendQuery: BackendTableQuery;
  columns: string;
  featureGroups: string;
  featureOptions: string[];
  onBackendQueryChange: (value: BackendTableQuery) => void;
  onColumnsChange: (value: string) => void;
  onFeatureGroupsChange: (value: string) => void;
  onRowLimitChange: (value: number) => void;
  rowLimit: number;
}) {
  const conditions = backendQuery.conditions;
  function updateConditions(next: BackendTableQuery) {
    onBackendQueryChange(next);
  }
  return (
    <div className="preview-query-panel">
      <div className="preview-query-grid">
        <InlineField label="Feature groups" value={featureGroups} onChange={onFeatureGroupsChange} />
        <InlineField label="Rows" type="number" value={String(rowLimit)} onChange={(value) => onRowLimitChange(Math.max(10, Math.min(5000, Math.round(Number(value) || 2000))))} />
        <Select label="Sort column" value={backendQuery.sortColumn ?? ""} options={["", ...availableColumns]} onChange={(value) => updateConditions({ ...backendQuery, sortColumn: value || undefined })} />
        <Select label="Sort direction" value={backendQuery.sortDirection ?? "asc"} options={["asc", "desc"]} onChange={(value) => updateConditions({ ...backendQuery, sortDirection: value === "desc" ? "desc" : "asc" })} />
        <Select label="Match" value={backendQuery.matchMode ?? "all"} options={["all", "any"]} onChange={(value) => updateConditions({ ...backendQuery, matchMode: value === "any" ? "any" : "all" })} />
      </div>
      <div className="field preview-columns-field">
        <label>Columns</label>
        <textarea
          placeholder={availableColumns.slice(0, 14).join(", ") || "Leave blank for scanner defaults"}
          value={columns}
          onChange={(event) => onColumnsChange(event.target.value)}
        />
      </div>
      <div className="preview-query-conditions">
        <div className="preview-query-section-header">
          <span>Conditions</span>
          <button className="table-text-button" onClick={() => updateConditions({ ...backendQuery, conditions: [...conditions, newPreviewCondition(availableColumns)] })} type="button">
            Add condition
          </button>
        </div>
        {conditions.length ? (
          conditions.map((condition) => {
            const operator = PREVIEW_QUERY_OPERATORS.find((item) => item.value === condition.operator) ?? PREVIEW_QUERY_OPERATORS[0];
            return (
              <div className="preview-query-condition" key={condition.id}>
                <select value={condition.column} onChange={(event) => updateConditions(updatePreviewCondition(backendQuery, condition.id, { column: event.target.value }))}>
                  {availableColumns.map((column) => (
                    <option key={column} value={column}>{displayName(column)}</option>
                  ))}
                </select>
                <select value={condition.operator} onChange={(event) => updateConditions(updatePreviewCondition(backendQuery, condition.id, { operator: event.target.value as BackendTableQuery["conditions"][number]["operator"] }))}>
                  {PREVIEW_QUERY_OPERATORS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
                {operator.needsValue ? <input value={condition.value} onChange={(event) => updateConditions(updatePreviewCondition(backendQuery, condition.id, { value: event.target.value }))} /> : null}
                {operator.needsSecondValue ? <input value={condition.valueSecondary ?? ""} onChange={(event) => updateConditions(updatePreviewCondition(backendQuery, condition.id, { valueSecondary: event.target.value }))} /> : null}
                <button className="table-text-button danger" onClick={() => updateConditions({ ...backendQuery, conditions: conditions.filter((item) => item.id !== condition.id) })} type="button">Remove</button>
              </div>
            );
          })
        ) : (
          <div className="preview-query-empty">No scanner filters. The snapshot will show all tickers available at that bar time.</div>
        )}
      </div>
      {featureOptions.length ? <div className="preview-query-empty">Available feature groups: {featureOptions.join(", ")}</div> : null}
    </div>
  );
}

function scannerSnapshotRecord(snapshot: ScannerSnapshot): RecordRow {
  return {
    built_at: "",
    column_count: snapshot.columns.length,
    columns: snapshot.columns,
    exists: true,
    group: "bars",
    key: `scanner|${snapshot.timeframe}|${snapshot.session_date}|${snapshot.bar_time}`,
    path: "",
    rows: snapshot.row_count,
    session_date: snapshot.session_date,
    size: "",
    timeframe: snapshot.timeframe,
  };
}

function scannerChartRow(row: Record<string, unknown>, snapshot: ScannerSnapshot): Record<string, unknown> {
  const minuteOfDay = rowNumberValue(row, "minute_of_day") ?? barTimeMinuteOfDay(snapshot.bar_time);
  return {
    ...row,
    bar_time_market: rowStringValue(row, "bar_time_market") || `${snapshot.session_date} ${snapshot.bar_time}`,
    minute_of_day: minuteOfDay,
    session_date: rowStringValue(row, "session_date") || snapshot.session_date,
    timeframe: rowStringValue(row, "timeframe") || snapshot.timeframe,
  };
}

function Preview({ catalog, scope, records }: { catalog: CatalogPayload | null; scope: Scope; records: RecordRow[] }) {
  const groups = useMemo(() => Array.from(new Set(records.filter((record) => record.exists).map((record) => record.group))).sort(), [records]);
  const [group, setGroup] = useState(groups[0] ?? "bars");
  const [appliedGroup, setAppliedGroup] = useState(groups[0] ?? "bars");
  const groupRecords = useMemo(() => records.filter((record) => record.exists && record.group === group), [group, records]);
  const timeframes = useMemo(() => Array.from(new Set(groupRecords.map((record) => record.timeframe))).sort(timeframeSort), [groupRecords]);
  const defaultTimeframe = timeframes[0] ?? "1m";
  const defaultQuery = useMemo<PreviewQueryState>(
    () => ({
      columns: "",
      conditions: { conditions: [], matchMode: "all", sortDirection: "asc" },
      endDate: scope.end_date,
      rowLimit: PREVIEW_PAGE_SIZE,
      startDate: scope.start_date,
      tickers: "",
      timeframe: defaultTimeframe,
    }),
    [defaultTimeframe, scope.end_date, scope.start_date]
  );
  const [queryDraft, setQueryDraft] = useState<PreviewQueryState>(defaultQuery);
  const [appliedQuery, setAppliedQuery] = useState<PreviewQueryState>(defaultQuery);
  const [queryOpen, setQueryOpen] = useState(false);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [queryRunId, setQueryRunId] = useState(0);
  const [sample, setSample] = useState<PreviewSample | null>(null);
  const [sampleError, setSampleError] = useState("");
  const [sampleLoading, setSampleLoading] = useState(false);
  const [chartTarget, setChartTarget] = useState<PreviewChartTarget | null>(null);
  const availableColumns = useMemo(
    () => Array.from(new Set(groupRecords.filter((record) => record.timeframe === queryDraft.timeframe).flatMap((record) => record.columns))).sort(),
    [groupRecords, queryDraft.timeframe]
  );
  const appliedQueryKey = useMemo(() => JSON.stringify({ group: appliedGroup, previewOffset, ...appliedQuery, conditions: cleanPreviewBackendQuery(appliedQuery.conditions) }), [appliedGroup, appliedQuery, previewOffset]);
  const activeRecord = useMemo<RecordRow>(
    () => ({
      built_at: "",
      column_count: sample?.columns.length ?? availableColumns.length,
      columns: sample?.columns ?? availableColumns,
      exists: true,
      group: appliedGroup,
      key: `${appliedGroup}|${appliedQuery.timeframe}|${appliedQuery.startDate}..${appliedQuery.endDate}`,
      path: "",
      rows: sample?.row_count ?? 0,
      session_date: appliedQuery.startDate,
      size: "",
      timeframe: appliedQuery.timeframe,
    }),
    [appliedGroup, appliedQuery.endDate, appliedQuery.startDate, appliedQuery.timeframe, availableColumns, sample]
  );
  const fillPanel = useViewportFillPanel(`${appliedQueryKey}:${sample?.rows.length ?? 0}`);
  useEffect(() => {
    if (!groups.length) return;
    setGroup((current) => (groups.includes(current) ? current : groups[0]));
    setAppliedGroup((current) => (groups.includes(current) ? current : groups[0]));
  }, [groups]);
  useEffect(() => {
    setQueryDraft((current) => {
      const nextTimeframe = timeframes.includes(current.timeframe) ? current.timeframe : defaultTimeframe;
      return { ...current, timeframe: nextTimeframe };
    });
    setPreviewOffset(0);
  }, [defaultTimeframe, group, timeframes]);
  useEffect(() => {
    if (queryRunId === 0) return;
    if (!appliedGroup || !appliedQuery.timeframe || !appliedQuery.startDate || !appliedQuery.endDate) return;
    let active = true;
    const cleanedQuery = cleanPreviewBackendQuery(appliedQuery.conditions);
    const tableQuery = previewBackendQueryIsActive(cleanedQuery) ? JSON.stringify(cleanedQuery) : undefined;
    setSampleError("");
    setSampleLoading(true);
    api<{ sample: PreviewSample }>(
      `/api/market-data/preview${query({
        processed_root: scope.processed_root,
        group: appliedGroup,
        timeframe: appliedQuery.timeframe,
        start_date: appliedQuery.startDate,
        end_date: appliedQuery.endDate,
        columns: appliedQuery.columns,
        row_limit: appliedQuery.rowLimit,
        row_offset: previewOffset,
        table_query: tableQuery,
        tickers: appliedQuery.tickers
      })}`
    )
      .then((payload) => {
        if (!active) return;
        setSample(payload.sample);
      })
      .catch((error: Error) => {
        if (!active) return;
        setSample({ columns: availableColumns, row_count: 0, row_limit: appliedQuery.rowLimit, row_offset: previewOffset, rows: [] });
        setSampleError(error.message);
      })
      .finally(() => {
        if (active) setSampleLoading(false);
      });
    return () => {
      active = false;
    };
  }, [appliedGroup, appliedQueryKey, availableColumns, queryRunId, scope.processed_root]);
  if (!groups.length) return <div className="empty-state">No records available.</div>;
  const previewStartRow = sample?.rows.length ? (sample.row_offset ?? 0) + 1 : 0;
  const previewEndRow = sample ? (sample.row_offset ?? 0) + sample.rows.length : 0;
  const canPageBack = previewOffset > 0 && !sampleLoading;
  const canPageForward = Boolean(sample?.has_more) && !sampleLoading;
  function runPreviewQuery() {
    setAppliedGroup(group);
    setAppliedQuery({
      ...queryDraft,
      conditions: cleanPreviewBackendQuery(queryDraft.conditions),
      endDate: queryDraft.endDate >= queryDraft.startDate ? queryDraft.endDate : queryDraft.startDate,
      rowLimit: Math.max(10, Math.min(5000, Math.round(Number(queryDraft.rowLimit) || PREVIEW_PAGE_SIZE))),
      startDate: queryDraft.startDate <= queryDraft.endDate ? queryDraft.startDate : queryDraft.endDate,
    });
    setPreviewOffset(0);
    setQueryRunId((value) => value + 1);
    setQueryOpen(false);
  }
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="preview-query-shell">
        <div className="toolbar preview-query-bar">
          <Select label="Artifact group" value={group} options={groups} onChange={(value) => { setGroup(value); setPreviewOffset(0); }} />
          <button className={queryOpen ? "button active" : "button"} onClick={() => setQueryOpen((value) => !value)} type="button">
            <SlidersHorizontal size={16} />
            Query
          </button>
          <button className="button primary" disabled={sampleLoading} onClick={runPreviewQuery} type="button">Run Query</button>
          <div className="preview-query-summary">
            <span>{appliedGroup}</span>
            <span>{appliedQuery.timeframe}</span>
            <span>{appliedQuery.startDate} to {appliedQuery.endDate}</span>
            <span>{appliedQuery.tickers.trim() ? appliedQuery.tickers.trim().toUpperCase() : "All tickers"}</span>
            <span>{sample?.scanned_artifacts ?? 0} files</span>
          </div>
        </div>
        {queryOpen ? (
          <PreviewQueryPanel
            availableColumns={availableColumns}
            onChange={setQueryDraft}
            query={queryDraft}
            timeframes={timeframes}
          />
        ) : null}
      </div>
      {sampleError ? <div className="preview-sample-status error">Preview request failed: {sampleError}</div> : null}
      {sampleLoading ? (
        <div className="preview-sample-status">
          <span className="loading-spinner" aria-hidden="true" />
          Running lazy preview query...
        </div>
      ) : null}
      {!sample && !sampleLoading && !sampleError ? (
        <div className="preview-sample-status">Set the query and press Run Query to load rows.</div>
      ) : null}
      {sample && !sampleError ? (
        <div className="preview-page-status">
          <span>
            Showing {previewStartRow.toLocaleString()}-{previewEndRow.toLocaleString()}
            {sample.has_more ? " with more rows available" : ""}
          </span>
          <button className="table-text-button" disabled={!canPageBack} onClick={() => setPreviewOffset((value) => Math.max(0, value - appliedQuery.rowLimit))} type="button">
            Previous
          </button>
          <button className="table-text-button" disabled={!canPageForward} onClick={() => setPreviewOffset((value) => value + appliedQuery.rowLimit)} type="button">
            Next
          </button>
        </div>
      ) : null}
      <DataTable
        rowAction={{
          isAvailable: (row) => rowHasChartContext(row, activeRecord),
          label: "Open row in chart",
          onSelect: (row) => setChartTarget({ record: activeRecord, row }),
        }}
        rows={sample?.rows ?? []}
        columns={sample?.columns}
        empty={queryRunId === 0 ? "Run a query to load preview rows." : "No rows."}
      />
      {chartTarget ? (
        <PreviewRowChartModal
          catalog={catalog}
          key={`${chartTarget.record.key}:${rowStringValue(chartTarget.row, "ticker")}:${rowStringValue(chartTarget.row, "bar_id")}:${rowStringValue(chartTarget.row, "bar_time_market")}`}
          onClose={() => setChartTarget(null)}
          records={records}
          scope={scope}
          target={chartTarget}
        />
      ) : null}
    </section>
  );
}

const PREVIEW_QUERY_OPERATORS: Array<{ label: string; needsSecondValue?: boolean; needsValue?: boolean; value: BackendTableQuery["conditions"][number]["operator"] }> = [
  { label: "Contains", needsValue: true, value: "contains" },
  { label: "Equals", needsValue: true, value: "eq" },
  { label: "Greater than", needsValue: true, value: "gt" },
  { label: "Greater or equal", needsValue: true, value: "gte" },
  { label: "Less than", needsValue: true, value: "lt" },
  { label: "Less or equal", needsValue: true, value: "lte" },
  { label: "Between", needsSecondValue: true, needsValue: true, value: "between" },
  { label: "Is blank", value: "is_null" },
  { label: "Is not blank", value: "is_not_null" },
];

function PreviewQueryPanel({
  availableColumns,
  onChange,
  query,
  timeframes,
}: {
  availableColumns: string[];
  onChange: (query: PreviewQueryState) => void;
  query: PreviewQueryState;
  timeframes: string[];
}) {
  const columnsText = availableColumns.slice(0, 12).join(", ");
  const conditions = query.conditions.conditions;
  function updateConditions(next: BackendTableQuery) {
    onChange({ ...query, conditions: next });
  }
  return (
    <div className="preview-query-panel">
      <div className="preview-query-grid">
        <InlineField label="Start" type="date" value={query.startDate} onChange={(value) => onChange({ ...query, startDate: value })} />
        <InlineField label="End" type="date" value={query.endDate} onChange={(value) => onChange({ ...query, endDate: value })} />
        <Select label="Timeframe" value={query.timeframe} options={timeframes.length ? timeframes : [query.timeframe]} onChange={(value) => onChange({ ...query, timeframe: value })} />
        <InlineField label="Tickers" value={query.tickers} onChange={(value) => onChange({ ...query, tickers: value })} />
        <InlineField label="Rows" type="number" value={String(query.rowLimit)} onChange={(value) => onChange({ ...query, rowLimit: Math.max(10, Math.min(5000, Math.round(Number(value) || PREVIEW_PAGE_SIZE))) })} />
        <Select label="Sort column" value={query.conditions.sortColumn ?? ""} options={["", ...availableColumns]} onChange={(value) => updateConditions({ ...query.conditions, sortColumn: value || undefined })} />
        <Select label="Sort direction" value={query.conditions.sortDirection ?? "asc"} options={["asc", "desc"]} onChange={(value) => updateConditions({ ...query.conditions, sortDirection: value === "desc" ? "desc" : "asc" })} />
        <Select label="Match" value={query.conditions.matchMode ?? "all"} options={["all", "any"]} onChange={(value) => updateConditions({ ...query.conditions, matchMode: value === "any" ? "any" : "all" })} />
      </div>
      <div className="field preview-columns-field">
        <label>Columns</label>
        <textarea
          placeholder={columnsText ? `Default: ${columnsText}` : "Leave blank for default preview columns"}
          value={query.columns}
          onChange={(event) => onChange({ ...query, columns: event.target.value })}
        />
      </div>
      <div className="preview-query-conditions">
        <div className="preview-query-section-header">
          <span>Conditions</span>
          <button className="table-text-button" onClick={() => updateConditions({ ...query.conditions, conditions: [...conditions, newPreviewCondition(availableColumns)] })} type="button">
            Add condition
          </button>
        </div>
        {conditions.length ? (
          conditions.map((condition) => {
            const operator = PREVIEW_QUERY_OPERATORS.find((item) => item.value === condition.operator) ?? PREVIEW_QUERY_OPERATORS[0];
            return (
              <div className="preview-query-condition" key={condition.id}>
                <select value={condition.column} onChange={(event) => updateConditions(updatePreviewCondition(query.conditions, condition.id, { column: event.target.value }))}>
                  {availableColumns.map((column) => (
                    <option key={column} value={column}>{displayName(column)}</option>
                  ))}
                </select>
                <select value={condition.operator} onChange={(event) => updateConditions(updatePreviewCondition(query.conditions, condition.id, { operator: event.target.value as BackendTableQuery["conditions"][number]["operator"] }))}>
                  {PREVIEW_QUERY_OPERATORS.map((item) => (
                    <option key={item.value} value={item.value}>{item.label}</option>
                  ))}
                </select>
                {operator.needsValue ? (
                  <input value={condition.value} onChange={(event) => updateConditions(updatePreviewCondition(query.conditions, condition.id, { value: event.target.value }))} />
                ) : null}
                {operator.needsSecondValue ? (
                  <input value={condition.valueSecondary ?? ""} onChange={(event) => updateConditions(updatePreviewCondition(query.conditions, condition.id, { valueSecondary: event.target.value }))} />
                ) : null}
                <button className="table-text-button danger" onClick={() => updateConditions({ ...query.conditions, conditions: conditions.filter((item) => item.id !== condition.id) })} type="button">
                  Remove
                </button>
              </div>
            );
          })
        ) : (
          <div className="preview-query-empty">No conditions. The query will use only group, timeframe, date range, ticker, and selected columns.</div>
        )}
      </div>
    </div>
  );
}

function newPreviewCondition(columns: string[]): BackendTableQuery["conditions"][number] {
  return {
    column: columns[0] ?? "",
    id: `preview-condition-${Date.now()}-${Math.random().toString(16).slice(2)}`,
    operator: "contains",
    value: "",
  };
}

function updatePreviewCondition(query: BackendTableQuery, id: string, patch: Partial<BackendTableQuery["conditions"][number]>): BackendTableQuery {
  return {
    ...query,
    conditions: query.conditions.map((condition) => (condition.id === id ? { ...condition, ...patch } : condition)),
  };
}

function PreviewRowChartModal({
  catalog,
  onClose,
  records,
  scope,
  target,
}: {
  catalog: CatalogPayload | null;
  onClose: () => void;
  records: RecordRow[];
  scope: Scope;
  target: PreviewChartTarget;
}) {
  const initial = useMemo(() => previewChartInitialState(target, records, catalog), [catalog, records, target]);
  const [timeframe, setTimeframe] = useState(initial.timeframe);
  const [ticker, setTicker] = useState(initial.ticker);
  const [rangeStart, setRangeStart] = useState(initial.range.start);
  const [rangeEnd, setRangeEnd] = useState(initial.range.end);
  const [featureGroups, setFeatureGroups] = useState(initial.featureGroups);
  const [visibleColumns, setVisibleColumns] = useState(initial.visibleColumns);
  const [payload, setPayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const timeframes = useMemo(() => chartTimeframesForRange(records, rangeStart, rangeEnd, timeframe), [records, rangeEnd, rangeStart, timeframe]);

  useEffect(() => {
    if (!timeframes.includes(timeframe) && timeframes.length) setTimeframe(timeframes[0]);
  }, [timeframe, timeframes]);

  useEffect(() => {
    if (!ticker || !timeframe || !rangeStart || !rangeEnd) return;
    let active = true;
    setChartLoading(true);
    setChartError("");
    api<ChartPayload>(
      `/api/market-data/chart${chartRequestQuery({
        processed_root: scope.processed_root,
        start_date: rangeStart,
        end_date: rangeEnd,
        timeframe,
        ticker,
        feature_groups: featureGroups.join(","),
        display_items: chartDisplayItemsRequestValue(visibleColumns),
        min_confidence: DEFAULT_CHART_MIN_CONFIDENCE,
      })}`
    )
      .then((nextPayload) => {
        if (!active) return;
        setPayload(nextPayload);
        const nextFeatureGroups = nextPayload.options?.feature_groups ?? [];
        const nextSelectedFeatureGroups = nextFeatureGroups.filter((group) => featureGroups.includes(group));
        if (nextSelectedFeatureGroups.length && !sameList(nextSelectedFeatureGroups, featureGroups)) {
          setFeatureGroups(nextSelectedFeatureGroups);
        }
      })
      .catch((error: Error) => {
        if (!active) return;
        setPayload(null);
        setChartError(chartRequestErrorMessage(error));
      })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [featureGroups, rangeEnd, rangeStart, scope.processed_root, ticker, timeframe, visibleColumns]);

  function updateChartPeriod(start: string, end: string) {
    if (start <= end) {
      setRangeStart(start);
      setRangeEnd(end);
    } else {
      setRangeStart(end);
      setRangeEnd(start);
    }
  }

  const displayItemOptions = payload?.options?.display_items ?? defaultCatalogDisplayItemOptions(catalog);
  const indicatorOptions = payload?.options?.standard_indicators ?? initial.visibleColumns;
  const featureOptions = payload?.options?.feature_columns ?? [];
  const periodBounds = chartPeriodBounds(records, timeframe, scope);

  return (
    <Modal className="chart-context-modal-panel" onClose={onClose} title="Row Chart Context">
      <div className="chart-context-summary">
        <div>
          <span>Artifact</span>
          <b>{target.record.group} / {target.record.timeframe} / {target.record.session_date}</b>
        </div>
        <div>
          <span>Ticker</span>
          <b>{ticker || "-"}</b>
        </div>
        <div>
          <span>Focus</span>
          <b>{initial.reference.label ?? target.record.session_date}</b>
        </div>
      </div>
      {ticker ? (
        <ChartPanel
          catalogColumns={catalog?.columns ?? []}
          displayItemOptions={displayItemOptions}
          emptyMessage="No chart data around the selected row."
          errorMessage={chartError}
          featureOptions={featureOptions}
          indicatorOptions={indicatorOptions}
          loading={chartLoading}
          onPeriodChange={updateChartPeriod}
          onTickerChange={setTicker}
          onTimeframeChange={setTimeframe}
          onVisibleColumnsChange={(nextColumns) => updateChartVisibleColumns(nextColumns, setVisibleColumns, setPayload)}
          payload={payload}
          periodEnd={rangeEnd}
          periodMax={periodBounds.max}
          periodMin={periodBounds.min}
          periodStart={rangeStart}
          reference={initial.reference}
          ticker={ticker}
          timeframe={timeframe}
          timeframes={timeframes}
          visibleColumns={visibleColumns}
        />
      ) : (
        <div className="empty-state">This row does not include a ticker, so it cannot be opened on the chart.</div>
      )}
    </Modal>
  );
}

function previewChartInitialState(target: PreviewChartTarget, records: RecordRow[], catalog: CatalogPayload | null) {
  const row = target.row;
  const barContext = previewBarContext(row, target.record);
  const timeframe = barContext.timeframe;
  const ticker = barContext.ticker.toUpperCase();
  const sessionDate = barContext.sessionDate;
  const range = target.rangeMode === "session" ? { start: sessionDate, end: sessionDate } : surroundingChartRange(records, timeframe, sessionDate);
  const visibleColumns = previewChartDisplayItems(target.record, catalog);
  return {
    featureGroups: previewFeatureGroups(target.record, catalog, visibleColumns),
    range,
    reference: previewChartReference(row, target.record, barContext),
    ticker,
    timeframe,
    visibleColumns,
  };
}

function rowHasChartContext(row: Record<string, unknown>, record: RecordRow) {
  const context = previewBarContext(row, record);
  return Boolean(context.ticker && context.sessionDate && context.timeframe);
}

function rowStringValue(row: Record<string, unknown>, column: string) {
  const value = row[column];
  return value === null || value === undefined ? "" : String(value);
}

function rowNumberValue(row: Record<string, unknown>, column: string) {
  const value = Number(row[column]);
  return Number.isFinite(value) ? value : undefined;
}

function previewBarContext(row: Record<string, unknown>, record: RecordRow): PreviewBarContext {
  const parsed = parseProviderBarId(rowStringValue(row, "bar_id"));
  const timeframe = rowStringValue(row, "timeframe") || parsed?.timeframe || record.timeframe || "1m";
  const ticker = rowStringValue(row, "ticker") || parsed?.ticker || "";
  const sessionDate = rowStringValue(row, "session_date") || record.session_date || "";
  const minuteOfDay = rowNumberValue(row, "minute_of_day");
  const rowTimestamp = rowUtcTimestamp(row);
  return {
    barId: rowStringValue(row, "bar_id") || parsed?.barId || "",
    minuteOfDay,
    sessionDate,
    ticker,
    time: rowTimestamp ?? parsed?.time,
    timeframe,
    utcText: rowStringValue(row, "bar_time_utc") || parsed?.utcText || "",
  };
}

function parseProviderBarId(barId: string): ParsedProviderBarId | null {
  const parts = barId.split("|");
  if (parts.length < 3) return null;
  const [timeframe, ticker, rawUtc] = parts;
  const utcText = normalizeUtcDateText(rawUtc);
  const parsed = Date.parse(utcText);
  return {
    barId,
    ticker: ticker || "",
    time: Number.isFinite(parsed) ? Math.floor(parsed / 1000) : undefined,
    timeframe: timeframe || "1m",
    utcText,
  };
}

function normalizeUtcDateText(value: string) {
  let normalized = value.trim();
  normalized = normalized.replace(/\.(\d{3})\d+/, ".$1");
  normalized = normalized.replace(/([+-]\d{2})(\d{2})$/, "$1:$2");
  return normalized || value;
}

function previewChartReference(row: Record<string, unknown>, record: RecordRow, context = previewBarContext(row, record)): ChartReference {
  const sessionDate = context.sessionDate;
  const minuteOfDay = context.minuteOfDay;
  const timestamp = context.time;
  const marketTime = rowStringValue(row, "bar_time_market") || context.utcText || sessionDate;
  return {
    label: `${context.ticker.toUpperCase() || "Row"} ${formatReferenceTimeLabel(marketTime, minuteOfDay)}`,
    minuteOfDay,
    sessionDate,
    time: timestamp,
  };
}

function rowUtcTimestamp(row: Record<string, unknown>) {
  const utcValue = rowStringValue(row, "bar_time_utc");
  if (!utcValue) return undefined;
  const utcText = normalizeUtcDateText(utcValue);
  const normalized = /z$|[+-]\d\d:?\d\d$/i.test(utcText) ? utcText : `${utcText}Z`;
  const timestamp = Date.parse(normalized);
  return Number.isFinite(timestamp) ? Math.floor(timestamp / 1000) : undefined;
}

function formatReferenceTimeLabel(value: string, minuteOfDay?: number) {
  if (value && value.includes("T")) return value.replace("T", " ").slice(0, 16);
  if (typeof minuteOfDay === "number" && Number.isFinite(minuteOfDay)) {
    const hour = Math.floor(minuteOfDay / 60);
    const minute = Math.round(minuteOfDay % 60);
    return `${String(hour).padStart(2, "0")}:${String(minute).padStart(2, "0")}`;
  }
  return value || "selected row";
}

function chartRequestQuery(params: Record<string, string | number | boolean | null | undefined>) {
  const search = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => {
    if (value === null || value === undefined) return;
    search.set(key, String(value));
  });
  const text = search.toString();
  return text ? `?${text}` : "";
}

function chartDisplayItemsRequestValue(items: string[]) {
  return items.length ? items.join(",") : CHART_DISPLAY_ITEMS_NONE;
}

function updateChartVisibleColumns(
  nextColumns: string[],
  setVisibleColumns: (value: string[]) => void,
  setPayload: (updater: (payload: ChartPayload | null) => ChartPayload | null) => void,
) {
  const normalized = Array.from(new Set(nextColumns));
  setVisibleColumns(normalized);
  setPayload((current) => (current ? filterChartPayloadForDisplayItems(current, normalized) : current));
}

function filterChartPayloadForDisplayItems(payload: ChartPayload, selectedItems: string[]): ChartPayload {
  const selected = new Set(selectedItems.map((item) => item.toLowerCase()));
  return {
    ...payload,
    markers: payload.markers.filter((marker) => markerBelongsToSelection(marker, selected)),
    oscillator_series: payload.oscillator_series.filter((series) => selected.has(chartPayloadSeriesSelectionKey(series))),
    overlay_series: payload.overlay_series.filter((series) => selected.has(chartPayloadSeriesSelectionKey(series))),
    price_zones: (payload.price_zones ?? []).filter((zone) => !zone.displayItemId || selected.has(String(zone.displayItemId).toLowerCase())),
  };
}

function chartPayloadSeriesSelectionKey(series: ChartPayload["overlay_series"][number]) {
  return String(series.displayItemId || series.column || series.label).toLowerCase();
}

function markerBelongsToSelection(marker: ChartPayload["markers"][number], selectedItems: Set<string>) {
  const displayItemId = "displayItemId" in marker ? String(marker.displayItemId ?? "") : "";
  return !displayItemId || selectedItems.has(displayItemId.toLowerCase());
}

function previewChartDisplayItems(record: RecordRow, catalog: CatalogPayload | null) {
  const recordFeatureGroup = artifactFeatureGroup(record.group);
  const matching = (catalog?.displayItems ?? []).filter((item) => {
    const featureGroups = item.featureGroups ?? [];
    const sourceColumns = item.sourceColumns ?? [];
    return (
      item.presentation?.selectable !== false &&
      ((recordFeatureGroup && featureGroups.includes(recordFeatureGroup)) || sourceColumns.some((column) => record.columns.includes(column)))
    );
  });
  if (matching.length) return matching.map((item) => item.id);
  const defaults = defaultCatalogDisplayItems(catalog);
  return defaults.length ? defaults : DEFAULT_CHART_DISPLAY_ITEMS;
}

function previewFeatureGroups(record: RecordRow, catalog: CatalogPayload | null, columns: string[]) {
  const groups = new Set<string>();
  const recordGroup = artifactFeatureGroup(record.group);
  if (recordGroup) groups.add(recordGroup);
  const catalogByDisplayItem = new Map((catalog?.displayItems ?? []).map((item) => [item.id, item]));
  columns.forEach((itemId) => {
    const item = catalogByDisplayItem.get(itemId);
    (item?.featureGroups ?? []).forEach((group) => {
      if (group) groups.add(group);
    });
  });
  if (!groups.size) {
    DEFAULT_CHART_FEATURE_GROUPS.forEach((group) => groups.add(group));
  }
  return Array.from(groups);
}

function artifactFeatureGroup(group: string) {
  return group.startsWith("features_") ? group.replace("features_", "") : "";
}

function surroundingChartRange(records: RecordRow[], timeframe: string, sessionDate: string) {
  const sessions = Array.from(
    new Set(records.filter((record) => record.group === "bars" && record.timeframe === timeframe && record.exists).map((record) => record.session_date))
  ).sort();
  if (!sessions.length || !sessionDate) return { start: sessionDate, end: sessionDate };
  const exactIndex = sessions.indexOf(sessionDate);
  const insertionIndex = exactIndex >= 0 ? exactIndex : sessions.findIndex((session) => session > sessionDate);
  const anchorIndex = insertionIndex >= 0 ? insertionIndex : sessions.length - 1;
  return {
    start: sessions[Math.max(0, anchorIndex - 1)] ?? sessionDate,
    end: sessions[Math.min(sessions.length - 1, anchorIndex + 1)] ?? sessionDate,
  };
}

function chartTimeframesForRange(records: RecordRow[], start: string, end: string, current: string) {
  const timeframes = Array.from(
    new Set(
      records
        .filter((record) => record.group === "bars" && record.exists && record.session_date >= start && record.session_date <= end)
        .map((record) => record.timeframe)
    )
  ).sort(timeframeSort);
  if (current && !timeframes.includes(current)) timeframes.unshift(current);
  return timeframes.length ? timeframes : [current || "1m"];
}

function chartPeriodBounds(records: RecordRow[], timeframe: string, scope: Scope) {
  const sessions = records
    .filter((record) => record.group === "bars" && record.timeframe === timeframe && record.exists)
    .map((record) => record.session_date)
    .sort();
  return {
    max: sessions[sessions.length - 1] ?? scope.end_date,
    min: sessions[0] ?? scope.start_date,
  };
}

function cleanPreviewBackendQuery(queryValue: BackendTableQuery): BackendTableQuery {
  return {
    conditions: queryValue.conditions.filter((condition) => {
      if (!condition.column || !condition.operator) return false;
      if (condition.operator === "is_null" || condition.operator === "is_not_null") return true;
      if (!condition.value.trim()) return false;
      if (condition.operator === "between" && !condition.valueSecondary?.trim()) return false;
      return true;
    }),
    matchMode: queryValue.matchMode === "any" ? "any" : "all",
    sortColumn: queryValue.sortColumn,
    sortDirection: queryValue.sortDirection ?? "asc",
  };
}

function previewBackendQueryIsActive(queryValue: BackendTableQuery): boolean {
  return queryValue.conditions.length > 0 || Boolean(queryValue.sortColumn);
}

function CatalogTab({
  catalog,
  catalogError,
  catalogLoading,
  onCatalogChange,
  scope
}: {
  catalog: CatalogPayload | null;
  catalogError: string;
  catalogLoading: boolean;
  onCatalogChange: (catalog: CatalogPayload) => void;
  scope: Scope;
}) {
  const [kind, setKind] = useState<CatalogKindFilter>("all");
  const [category, setCategory] = useState("all");
  const [group, setGroup] = useState("all");
  const [presentationType, setPresentationType] = useState("all");
  const [search, setSearch] = useState("");
  const [catalogWidth, setCatalogWidth] = useState(25);
  const [isResizing, setIsResizing] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const allItems = useMemo(() => catalogItems(catalog), [catalog]);
  const categoryOptions = useMemo(() => catalogOptionValues(allItems.map((item) => item.category)), [allItems]);
  const groupOptions = useMemo(() => catalogOptionValues(allItems.map((item) => item.groupLabel)), [allItems]);
  const presentationTypeOptions = useMemo(() => catalogPresentationTypeOptions(allItems), [allItems]);
  const items = useMemo(
    () => filterCatalogItems(allItems, { category, group, kind, presentationType, search }),
    [allItems, category, group, kind, presentationType, search],
  );
  const groupedItems = useMemo(() => groupCatalogItems(items), [items]);
  const [selectedId, setSelectedId] = useState("");
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const [draft, setDraft] = useState<CatalogPresentation>({});
  const [styleTarget, setStyleTarget] = useState("group");
  const [catalogPreview, setCatalogPreview] = useState<CatalogPreviewPayload | null>(null);
  const [catalogPreviewLoading, setCatalogPreviewLoading] = useState(false);

  useEffect(() => {
    if (selected?.id && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected?.id, selectedId]);

  useEffect(() => {
    setDraft({ ...(selected?.presentation ?? {}) });
    setStyleTarget("group");
    setCatalogPreview(null);
    setSaveState("idle");
  }, [selected?.id]);

  useEffect(() => {
    if (!selected?.id) return;
    let cancelled = false;
    setCatalogPreviewLoading(true);
    api<CatalogPreviewPayload>(`/api/market-data/catalog/preview${query({ processed_root: scope.processed_root, item_id: selected.id })}`)
      .then((payload) => {
        if (!cancelled) setCatalogPreview(payload);
      })
      .catch((error: Error) => {
        if (!cancelled) setCatalogPreview({ sampled: false, reason: error.message, payload: null });
      })
      .finally(() => {
        if (!cancelled) setCatalogPreviewLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [scope.processed_root, selected?.id]);

  function updatePresentation(key: string, value: CatalogPresentationPatchValue) {
    setDraft((current) => ({ ...current, [key]: value }));
    setSaveState("idle");
  }

  function updatePresentationPart(index: number, key: string, value: CatalogPresentationPatchValue) {
    setDraft((current) => {
      const parts = clonePresentationParts(current.parts);
      if (!parts[index]) return current;
      parts[index] = { ...parts[index], [key]: value };
      return { ...current, parts };
    });
    setSaveState("idle");
  }

  function updateDisplayType(value: string) {
    if (styleTarget !== "group") {
      const partIndex = Number(styleTarget.replace("part:", ""));
      setDraft((current) => {
        const parts = clonePresentationParts(current.parts);
        if (!parts[partIndex]) return current;
        const isMacdTarget = presentationIsMacdTarget(selected, parts[partIndex]);
        parts[partIndex] = { ...parts[partIndex], chartRole: value, pane: defaultPaneForDisplayType(value, String(parts[partIndex].pane ?? current.pane ?? "price"), isMacdTarget) };
        return { ...current, parts };
      });
    } else {
      setDraft((current) => ({ ...current, chartRole: value, pane: defaultPaneForDisplayType(value, String(current.pane ?? "price"), presentationIsMacdTarget(selected, current)) }));
    }
    setSaveState("idle");
  }

  function updateActivePresentation(key: string, value: CatalogPresentationPatchValue) {
    if (styleTarget !== "group") {
      const partIndex = Number(styleTarget.replace("part:", ""));
      updatePresentationPart(partIndex, key, value);
      return;
    }
    updatePresentation(key, value);
  }

  function savePresentation() {
    if (!selected) return;
    setSaveState("saving");
    api<{ catalog: CatalogPayload }>("/api/market-data/catalog/presentation", {
      method: "PATCH",
      body: JSON.stringify({ processed_root: scope.processed_root, item_id: selected.id, presentation: draft })
    }).then((payload) => {
      onCatalogChange(payload.catalog);
      setSaveState("saved");
    }).catch(() => setSaveState("failed"));
  }

  function startResize() {
    setIsResizing(true);
  }

  function stopResize() {
    setIsResizing(false);
  }

  function resizeCatalog(event: ReactMouseEvent<HTMLDivElement>) {
    if (!isResizing) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const next = ((event.clientX - rect.left) / rect.width) * 100;
    setCatalogWidth(Math.max(24, Math.min(46, next)));
  }

  const presentationRole = normalizeDisplayType(String(draft.chartRole ?? selected?.presentation?.chartRole ?? "data_only"));
  const presentationPane = String(draft.pane ?? selected?.presentation?.pane ?? "price");
  const selectedPresentationType = selected ? presentationTypeForItem({ ...selected, presentation: draft }) : "data_only";
  const isTableOnlyPresentation = isDataOnlyRole(presentationRole);
  const groupedPresentationParts = presentationRole === "composite" ? clonePresentationParts(draft.parts) : [];
  const activePartIndex = styleTarget.startsWith("part:") ? Number(styleTarget.replace("part:", "")) : -1;
  const activePresentation = activePartIndex >= 0 ? groupedPresentationParts[activePartIndex] ?? draft : draft;
  const activeRole = normalizeDisplayType(String(activePresentation.chartRole ?? activePresentation.style ?? presentationRole));
  const activeDataShape = String(activePresentation.dataShape ?? draft.dataShape ?? selected?.dataShape ?? selected?.presentation?.dataShape ?? "continuous_series");
  const activePreset = presentationPreset(catalog, activeRole);
  const activeStyleFields = new Set(activePreset.styleFields ?? []);
  const displayTypeOptions = displayTypeOptionsForTarget(catalog, activeDataShape, styleTarget === "group" && groupedPresentationParts.length > 0);
  const styleTargetOptions = groupedPresentationParts.length
    ? ["group", ...groupedPresentationParts.map((part, index) => `part:${index}`)]
    : ["group"];
  const styleTargetLabels = Object.fromEntries(styleTargetOptions.map((value) => [value, styleTargetLabel(value, groupedPresentationParts)]));
  const isMarkerPresentation = activeRole === "marker" || activeRole === "text_label";
  const isDataOnlyPresentation = isDataOnlyRole(activeRole);
  const showVisualStyle = !isDataOnlyPresentation && activeStyleFields.has("color");
  const showPaneField = activeStyleFields.has("pane") && !["price", "none", "chart_background"].includes(String(activePreset.target ?? ""));
  const showValueFormat = isDataOnlyPresentation || activeStyleFields.has("valueFormat") || activeStyleFields.has("precision");
  const activeIsMacdTarget = presentationIsMacdTarget(selected, activePresentation);
  const contractPaneOptions = (catalog?.presentationOptions.panes ?? FIXED_LOWER_PANES).filter((pane) => FIXED_LOWER_PANES.includes(pane));
  const allowedPaneOptions = lowerPaneOptionsForTarget(activeIsMacdTarget);
  const activePaneOptions = allowedPaneOptions.filter((pane) => contractPaneOptions.includes(pane)).length
    ? allowedPaneOptions.filter((pane) => contractPaneOptions.includes(pane))
    : allowedPaneOptions;
  const activePaneValue = normalizedLowerPane(activePresentation.pane ?? draft.pane, activeIsMacdTarget);
  const fillPanel = useViewportFillPanel(`${catalogLoading}:${allItems.length}:${items.length}:${selected?.id ?? ""}`);

  return (
    <section
      className={isResizing ? "catalog-workbench resizing" : "catalog-workbench"}
      onMouseLeave={stopResize}
      onMouseMove={resizeCatalog}
      onMouseUp={stopResize}
      ref={fillPanel.ref}
      style={fillPanel.style}
    >
      <aside className="catalog-rail" style={{ width: `${catalogWidth}%` }}>
        <div className={catalogLoading ? "catalog-rail-card busy" : "catalog-rail-card"}>
          <div className="catalog-rail-header">
            <div className="catalog-title-row">
              <Database size={16} />
              <h2>Provider Catalog</h2>
            </div>
            <p>Browse the provider-owned contract for chartable indicators, features, labels, methods, and scanner outputs.</p>
            <div className="catalog-summary-strip">
              <CatalogStat label="Items" value={allItems.length} />
              <CatalogStat label="Visible" value={allItems.filter((item) => item.presentation?.defaultVisible).length} />
              <CatalogStat label="Shown" value={items.length} />
            </div>
            <label className="catalog-search-field" aria-label="Search provider catalog">
              <Search size={14} />
              <input placeholder="Search names, groups, or descriptions" value={search} onChange={(event) => setSearch(event.target.value)} />
            </label>
            <div className="catalog-filter-grid">
              <CatalogFilter label="Type" value={kind} onChange={(value) => setKind(value as CatalogKindFilter)} options={["all", "display", "columns", "methods", "scanners"]} />
              <CatalogFilter label="Category" value={category} onChange={setCategory} options={categoryOptions} />
              <CatalogFilter label="Group" value={group} onChange={setGroup} options={groupOptions} />
              <CatalogFilter label="Presentation" value={presentationType} onChange={setPresentationType} options={presentationTypeOptions} labels={PRESENTATION_TYPE_LABELS} />
            </div>
          </div>
          <div className="catalog-list">
            {catalogError ? <div className="catalog-error">{catalogError}</div> : null}
            {groupedItems.map((section) => (
              <div className="catalog-list-section" key={section.label}>
                <div className="catalog-list-section-header">
                  <span>{section.label}</span>
                  <small>{section.items.length}</small>
                </div>
                {section.items.map((item) => (
                  <button className={selected?.id === item.id ? "catalog-item-card selected" : "catalog-item-card"} key={item.id} onClick={() => setSelectedId(item.id)} type="button">
                    <div className="catalog-item-card-top">
                      <span>{item.title}</span>
                      <small>{item.presentation?.defaultVisible ? "on" : "off"}</small>
                    </div>
                    <p>{item.summary}</p>
                    <div className="catalog-item-meta-row">
                      <span>{item.sourceLabel}</span>
                      <span>{item.category}</span>
                      <span>{presentationTypeLabel(item.presentationType)}</span>
                      {item.dtype ? <span>{item.dtype}</span> : null}
                    </div>
                  </button>
                ))}
              </div>
            ))}
            {catalogLoading && !allItems.length ? (
              <div className="catalog-loading-card">
                <span className="loading-spinner" aria-hidden="true" />
                Loading catalog...
              </div>
            ) : null}
            {!catalogLoading && !items.length ? <div className="catalog-empty-card">No catalog items match the current filters.</div> : null}
          </div>
          {catalogLoading ? <div className="catalog-busy-overlay"><span className="loading-spinner" aria-hidden="true" />Loading catalog...</div> : null}
        </div>
      </aside>
      <div aria-label="Resize catalog detail" className={isResizing ? "catalog-resize-handle active" : "catalog-resize-handle"} onMouseDown={startResize} role="separator" />
      <article className="catalog-detail-pane" style={{ width: `${100 - catalogWidth}%` }}>
        {selected ? (
          <div className="catalog-detail-stack">
            <div className="catalog-detail-card catalog-detail-hero">
              <div>
                <div className="catalog-kicker">{selected.sourceLabel}</div>
                <h2>{selected.title}</h2>
                <p>{selected.summary}</p>
              </div>
              <div className="catalog-detail-actions">
                <span className="catalog-status-pill">{selected.presentation?.defaultVisible ? "Default on" : "Default off"}</span>
                <button className="button primary" disabled={saveState === "saving"} onClick={savePresentation} type="button">
                  {saveState === "saving" ? "Saving..." : saveState === "saved" ? "Saved" : "Save presentation"}
                </button>
              </div>
            </div>
            {saveState === "failed" ? <div className="error-panel">Catalog presentation update failed.</div> : null}
            <section className="catalog-presentation-card">
              <div className="catalog-presentation-header">
                <div>
                  <div className="catalog-kicker">Presentation</div>
                  <h3>Chart Display Contract</h3>
                </div>
                <div className={isTableOnlyPresentation ? "catalog-presentation-preview table-only" : "catalog-presentation-preview"}>
                  {isTableOnlyPresentation ? null : <span className="catalog-preview-swatch" style={{ background: presentationColor(activePresentation.color ?? draft.color) }} />}
                  <div>
                    <strong>{presentationTypeLabel(selectedPresentationType)}</strong>
                    <span>{isTableOnlyPresentation ? "No chart color used" : paneDisplayLabel(presentationPane)}</span>
                  </div>
                </div>
              </div>
              <div className="catalog-presentation-layout">
                <div className="catalog-presentation-controls">
                  <div className="catalog-presentation-grid">
                    <div className="catalog-presentation-section catalog-visibility-section">
                      <h4>Visibility</h4>
                      <div className="catalog-check-grid">
                        <CatalogCheckbox checked={Boolean(draft.selectable)} help={PRESENTATION_HELP.selectable} label="Selectable" onChange={(value) => updatePresentation("selectable", value)} />
                        <CatalogCheckbox checked={Boolean(draft.defaultVisible)} help={PRESENTATION_HELP.defaultVisible} label="Default on" onChange={(value) => updatePresentation("defaultVisible", value)} />
                        <CatalogCheckbox checked={Boolean(draft.legend)} help={PRESENTATION_HELP.legend} label="Legend" onChange={(value) => updatePresentation("legend", value)} />
                      </div>
                    </div>
                    <div className="catalog-presentation-section">
                      <h4>Display Target</h4>
                      <div className="catalog-form-grid compact">
                        {styleTargetOptions.length > 1 ? (
                          <CatalogSelect help="Choose whether to edit the group-level defaults or a specific child item." labels={styleTargetLabels} label="Item" options={styleTargetOptions} value={styleTarget} onChange={setStyleTarget} />
                        ) : null}
                        <CatalogSelect help={PRESENTATION_HELP.chartRole} label="Display type" options={displayTypeOptions} value={activeRole} onChange={updateDisplayType} />
                        {showPaneField ? (
                          <CatalogSelect help={PRESENTATION_HELP.pane} label="Pane" labels={PANE_LABELS} options={activePaneOptions} value={activePaneValue} onChange={(value) => updateActivePresentation("pane", normalizedLowerPane(value, activeIsMacdTarget))} />
                        ) : null}
                      </div>
                      <p className="catalog-preset-note">{activePreset.description ?? "The selected display type controls the available style fields."}</p>
                    </div>
                    {showVisualStyle ? (
                      <div className="catalog-presentation-section">
                        <h4>{styleSectionTitle(activeRole)}</h4>
                        <CatalogStylePopover
                          bandFillColor={String(activePresentation.bandFillColor ?? activePresentation.color ?? "#1E3A5F")}
                          bandFillOpacity={Number(activePresentation.bandFillOpacity ?? 0.16)}
                          chartRole={activeRole}
                          color={String(activePresentation.color ?? "#1E3A5F")}
                          label={styleEditorLabel(activeRole)}
                          lineStyle={String(activePresentation.lineStyle ?? "solid")}
                          lineStyleOptions={catalog?.presentationOptions.lineStyles ?? []}
                          lineWidth={Number(activePresentation.lineWidth ?? 1)}
                          opacity={Number(activePresentation.opacity ?? 1)}
                          precision={Number(activePresentation.precision ?? draft.precision ?? 2)}
                          styleFields={activeStyleFields}
                          onChange={updateActivePresentation}
                        />
                      </div>
                    ) : null}
                    {isMarkerPresentation ? (
                      <div className="catalog-presentation-section">
                        <h4>Event Annotation</h4>
                        <div className="catalog-form-grid compact">
                          {activeRole === "marker" ? (
                            <CatalogSelect help={PRESENTATION_HELP.markerShape} label="Shape" options={catalog?.presentationOptions.markerShapes ?? []} value={String(activePresentation.markerShape ?? "circle")} onChange={(value) => updateActivePresentation("markerShape", value)} />
                          ) : null}
                          <CatalogSelect help={PRESENTATION_HELP.markerPosition} label="Position" options={catalog?.presentationOptions.markerPositions ?? []} value={String(activePresentation.markerPosition ?? "belowBar")} onChange={(value) => updateActivePresentation("markerPosition", value)} />
                          <CatalogSelect help={PRESENTATION_HELP.labelMode} label="Label mode" options={catalog?.presentationOptions.labelModes ?? ["none", "short", "value", "full"]} value={String(activePresentation.labelMode ?? (activeRole === "text_label" ? "short" : "none"))} onChange={(value) => updateActivePresentation("labelMode", value)} />
                          <CatalogTextField help={PRESENTATION_HELP.labelText} label="Label text" value={String(activePresentation.labelText ?? "")} onChange={(value) => updateActivePresentation("labelText", value)} />
                          {activeRole === "marker" ? (
                            <CatalogNumberField help={PRESENTATION_HELP.markerSize} label="Size" max={4} min={0.1} value={Number(activePresentation.markerSize ?? 1)} onChange={(value) => updateActivePresentation("markerSize", value)} />
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                    {activeRole === "anchored_zone" ? (
                      <div className="catalog-presentation-section">
                        <h4>Zone Behavior</h4>
                        <div className="catalog-form-grid compact">
                          <CatalogSelect help="Controls how the event-created zone extends after the source bar." label="Extend rule" options={catalog?.presentationOptions.extendRules ?? []} value={String(activePresentation.extendRule ?? "fixed_bars")} onChange={(value) => updateActivePresentation("extendRule", value)} />
                          <CatalogNumberField help="Maximum number of bars the anchored zone may extend." label="Max bars" max={240} min={1} value={Number(activePresentation.maxBars ?? activePresentation.extendBars ?? 24)} onChange={(value) => updateActivePresentation("maxBars", value)} />
                          <CatalogSelect help={PRESENTATION_HELP.zoneHeightMode} label="Height mode" options={catalog?.presentationOptions.zoneHeightModes ?? ["price_range", "fixed_px"]} value={String(activePresentation.zoneHeightMode ?? "price_range")} onChange={(value) => updateActivePresentation("zoneHeightMode", value)} />
                          {String(activePresentation.zoneHeightMode ?? "price_range") === "fixed_px" ? (
                            <>
                              <CatalogNumberField help={PRESENTATION_HELP.minPixelHeight} label="Min px" max={32} min={0} value={Number(activePresentation.minPixelHeight ?? 3)} onChange={(value) => updateActivePresentation("minPixelHeight", value)} />
                              <CatalogNumberField help={PRESENTATION_HELP.maxPixelHeight} label="Max px" max={96} min={0} value={Number(activePresentation.maxPixelHeight ?? 4)} onChange={(value) => updateActivePresentation("maxPixelHeight", value)} />
                            </>
                          ) : (
                            <CatalogNumberField help={PRESENTATION_HELP.zonePaddingBps} label="Padding bps" max={100} min={0} value={Number(activePresentation.zonePaddingBps ?? 0)} onChange={(value) => updateActivePresentation("zonePaddingBps", value)} />
                          )}
                          <CatalogSelect help="Boundary stroke used around the zone." label="Border style" options={catalog?.presentationOptions.borderStyles ?? []} value={String(activePresentation.borderStyle ?? "solid")} onChange={(value) => updateActivePresentation("borderStyle", value)} />
                          <CatalogNumberField help="Zone boundary width in pixels." label="Border width" max={6} min={1} value={Number(activePresentation.borderWidth ?? 1)} onChange={(value) => updateActivePresentation("borderWidth", value)} />
                          <CatalogNumberField help={PRESENTATION_HELP.borderOpacity} label="Border opacity" max={0.35} min={0} value={Number(activePresentation.borderOpacity ?? 0.14)} onChange={(value) => updateActivePresentation("borderOpacity", value)} />
                          <CatalogCheckbox checked={Boolean(activePresentation.stopOnMitigation)} help="Stops the zone when price revisits or mitigates the event range, where supported by the renderer." label="Stop on mitigation" onChange={(value) => updateActivePresentation("stopOnMitigation", value)} />
                        </div>
                      </div>
                    ) : null}
                    {showValueFormat ? (
                      <div className="catalog-presentation-section">
                        <h4>Value Format</h4>
                        <div className="catalog-form-grid compact">
                          {activeStyleFields.has("valueFormat") || isDataOnlyPresentation ? (
                            <CatalogSelect help={PRESENTATION_HELP.valueFormat} label="Value format" options={catalog?.presentationOptions.valueFormats ?? []} value={String(activePresentation.valueFormat ?? draft.valueFormat ?? "number")} onChange={(value) => updateActivePresentation("valueFormat", value)} />
                          ) : null}
                          {activeStyleFields.has("precision") || isDataOnlyPresentation ? (
                            <CatalogNumberField help={PRESENTATION_HELP.precision} label="Precision" max={8} min={0} value={Number(activePresentation.precision ?? draft.precision ?? 2)} onChange={(value) => updateActivePresentation("precision", value)} />
                          ) : null}
                        </div>
                      </div>
                    ) : null}
                    {groupedPresentationParts.length ? (
                      <CatalogGroupedPartsEditor
                        parts={groupedPresentationParts}
                      />
                    ) : null}
                  </div>
                </div>
                <CatalogPresentationChartPreview
                  itemTitle={selected.title}
                  presentation={draft}
                  presentationType={selectedPresentationType}
                  realPreview={catalogPreview}
                  realPreviewLoading={catalogPreviewLoading}
                />
              </div>
            </section>
            <div className="catalog-detail-metrics">
              <CatalogMetric icon={<Tags size={14} />} label="Category" value={selected.category} />
              <CatalogMetric icon={<Filter size={14} />} label="Group" value={selected.groupLabel} />
              <CatalogMetric icon={<SlidersHorizontal size={14} />} label="Presentation" value={presentationTypeLabel(selectedPresentationType)} />
              <CatalogMetric icon={<BookOpen size={14} />} label="Value" value={String(draft.valueFormat ?? selected.presentation?.valueFormat ?? "-")} />
            </div>
            <div className="catalog-knowledge-grid">
              <section className="catalog-section-card">
                <h3>Knowledge</h3>
                <p>{selected.knowledge?.shortDescription}</p>
                <p>{selected.knowledge?.detailedDescription}</p>
                <div className="catalog-copy-block">
                  <h4>Theory</h4>
                  <p>{selected.knowledge?.theory}</p>
                </div>
                <div className="catalog-copy-block">
                  <h4>Interpretation</h4>
                  <p>{selected.knowledge?.interpretation}</p>
                </div>
                {selected.knowledge?.caveats?.length ? (
                  <div className="catalog-copy-block">
                    <h4>Caveats</h4>
                    <ul>
                      {selected.knowledge.caveats.map((caveat) => <li key={caveat}>{caveat}</li>)}
                    </ul>
                  </div>
                ) : null}
              </section>
              <section className="catalog-section-card">
                <h3>Equations</h3>
                <div className="catalog-equation-grid">
                  {selected.knowledge?.equations?.map((equation) => (
                    <CatalogEquation equation={equation} key={equation.title} />
                  ))}
                </div>
              </section>
            </div>
          </div>
        ) : (
          <div className="catalog-detail-empty">
            {catalogLoading ? (
              <>
                <span className="loading-spinner" aria-hidden="true" />
                Loading catalog...
              </>
            ) : (
              "Select a catalog item to inspect its contract and presentation settings."
            )}
          </div>
        )}
      </article>
    </section>
  );
}

function CatalogStat({ label, value }: { label: string; value: number }) {
  return (
    <span className="catalog-stat">
      <small>{label}</small>
      <strong>{value.toLocaleString()}</strong>
    </span>
  );
}

function CatalogMetric({ icon, label, value }: { icon: ReactNode; label: string; value: string }) {
  return (
    <div className="catalog-metric">
      <div>{icon}<span>{label}</span></div>
      <strong>{value}</strong>
    </div>
  );
}

function CatalogFilter({
  label,
  labels,
  onChange,
  options,
  value,
}: {
  label: string;
  labels?: Record<string, string>;
  onChange: (value: string) => void;
  options: string[];
  value: string;
}) {
  return (
    <label className="catalog-filter">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{labels?.[option] ?? (option === "all" ? "All" : displayName(option))}</option>
        ))}
      </select>
    </label>
  );
}

function presentationColor(value: unknown): string {
  const color = String(value ?? "");
  if (color === "inherit_candle_direction") return "#33E42A";
  return /^#[0-9a-f]{6}$/i.test(color) ? color : "#1E3A5F";
}

function colorInputValue(value: unknown, fallback = "#1E3A5F"): string {
  const color = presentationColor(value);
  return /^#[0-9a-f]{6}$/i.test(color) ? color : fallback;
}

function colorWithOpacity(value: unknown, opacity: number): string {
  const color = colorInputValue(value);
  const alpha = Math.max(0, Math.min(1, opacity));
  const red = parseInt(color.slice(1, 3), 16);
  const green = parseInt(color.slice(3, 5), 16);
  const blue = parseInt(color.slice(5, 7), 16);
  return `rgba(${red}, ${green}, ${blue}, ${alpha.toFixed(2)})`;
}

function clonePresentationParts(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) return [];
  return value.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object")).map((part) => ({ ...part }));
}

function boundedPresentationNumber(value: unknown, min: number, max: number, fallback: number): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return fallback;
  return Math.max(min, Math.min(max, numeric));
}

function svgDashArray(lineStyle: string, lineWidth: number): string | undefined {
  if (lineStyle === "dashed") return `${Math.max(5, lineWidth * 4)} ${Math.max(4, lineWidth * 3)}`;
  if (lineStyle === "dotted") return `1 ${Math.max(3, lineWidth * 2.5)}`;
  return undefined;
}

function opacityLabel(value: number): string {
  return `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%`;
}

function CatalogEquation({ equation }: { equation: CatalogKnowledge["equations"][number] }) {
  const variables = Object.entries(equation.variables ?? {});
  const formulas = mathBlocks(equation.markdown);
  return (
    <div className="catalog-equation">
      <strong>{equation.title}</strong>
      <div className="catalog-equation-formula">
        {formulas.length ? (
          formulas.map((formula, index) => (
            <div dangerouslySetInnerHTML={{ __html: renderFormula(formula) }} key={`${equation.title}:${index}`} />
          ))
        ) : (
          <code>{equation.markdown}</code>
        )}
      </div>
      {variables.length ? (
        <div className="catalog-equation-variables">
          {variables.map(([name, description]) => (
            <span key={name}>
              <b>{name}</b>
              {description}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function mathBlocks(markdown: string): string[] {
  const matches = [...markdown.matchAll(/\$\$([\s\S]*?)\$\$/g)].map((match) => match[1]?.trim()).filter(Boolean);
  return matches.length ? matches : markdown.trim() ? [markdown.trim()] : [];
}

function renderFormula(formula: string): string {
  return katex.renderToString(formula, {
    displayMode: true,
    output: "html",
    strict: false,
    throwOnError: false,
  });
}

function Schema({ scope, records }: { scope: Scope; records: RecordRow[] }) {
  const [recordKey, setRecordKey] = useState(records[0]?.key ?? "");
  const [schema, setSchema] = useState<Record<string, unknown>[]>([]);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const record = records.find((item) => item.key === recordKey) ?? records[0];
  const fields = schema.map(toSchemaField);
  const numericCount = fields.filter((field) => field.kind === "numeric").length;
  const temporalCount = fields.filter((field) => field.kind === "temporal").length;
  const booleanCount = fields.filter((field) => field.kind === "boolean").length;
  const textCount = fields.filter((field) => field.kind === "text").length;
  const fillPanel = useViewportFillPanel(`${recordKey}:${fields.length}:${schemaLoading}`);
  useEffect(() => {
    if (!record) return;
    let active = true;
    setSchemaLoading(true);
    setSchema([]);
    api<{ schema: Record<string, unknown>[] }>(
      `/api/market-data/schema${query({ processed_root: scope.processed_root, group: record.group, timeframe: record.timeframe, session_date: record.session_date })}`
    ).then((payload) => {
      if (!active) return;
      setSchema(payload.schema);
      setSchemaLoading(false);
    });
    return () => {
      active = false;
    };
  }, [scope.processed_root, record?.key]);
  if (!record) return <div className="empty-state">No records available.</div>;
  return (
    <section className="panel schema-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="schema-toolbar">
        <div className="field" style={{ flex: "1 1 420px", minWidth: 300 }}>
          <label>Artifact</label>
          <select value={recordKey} onChange={(event) => setRecordKey(event.target.value)}>
            {records.map((item) => (
              <option key={item.key} value={item.key}>
                {item.group} | {item.timeframe} | {item.session_date}
              </option>
            ))}
          </select>
        </div>
        <div className="schema-artifact-meta">
          <span>{record.group}</span>
          <span>{record.timeframe}</span>
          <span>{record.session_date}</span>
          <span>{record.rows.toLocaleString()} rows</span>
        </div>
      </div>
      <div className="schema-summary-grid">
        <SchemaSummary label="Fields" value={fields.length} />
        <SchemaSummary label="Numeric" value={numericCount} />
        <SchemaSummary label="Temporal" value={temporalCount} />
        <SchemaSummary label="Boolean" value={booleanCount} />
        <SchemaSummary label="Text" value={textCount} />
      </div>
      {fields.length ? (
        <div className="schema-card-grid">
          {fields.map((field, index) => (
            <SchemaFieldCard field={field} index={index} key={`${field.column}:${field.dtype}`} />
          ))}
        </div>
      ) : (
        <div className="empty-state">
          {schemaLoading ? (
            <>
              <span className="loading-spinner" aria-hidden="true" />
              Loading schema...
            </>
          ) : (
            "No schema fields found for the selected artifact."
          )}
        </div>
      )}
    </section>
  );
}

function SchemaSummary({ label, value }: { label: string; value: number }) {
  return (
    <div className="schema-summary-card">
      <span>{label}</span>
      <b>{value.toLocaleString()}</b>
    </div>
  );
}

function SchemaFieldCard({ field, index }: { field: SchemaField; index: number }) {
  return (
    <article className="schema-field-card">
      <div className="schema-field-top">
        <span className="schema-field-index">{String(index + 1).padStart(2, "0")}</span>
        <span className={`schema-type-badge ${field.kind}`}>{field.kind}</span>
      </div>
      <div>
        <div className="schema-field-name">{displayName(field.column)}</div>
        <div className="schema-field-column">{field.column}</div>
      </div>
      <div className="schema-field-type">{field.dtype}</div>
    </article>
  );
}

function toSchemaField(row: Record<string, unknown>): SchemaField {
  const column = String(row.column ?? "");
  const dtype = String(row.dtype ?? "");
  return { column, dtype, kind: schemaKind(dtype) };
}

function schemaKind(dtype: string): SchemaField["kind"] {
  const lower = dtype.toLowerCase();
  if (lower.includes("date") || lower.includes("time")) return "temporal";
  if (lower.includes("bool")) return "boolean";
  if (lower.includes("float") || lower.includes("int") || lower.includes("decimal") || lower.includes("uint")) return "numeric";
  if (lower.includes("str") || lower.includes("utf") || lower.includes("categorical")) return "text";
  return "other";
}

function Select({ label, value, options, onChange }: { label: string; value: string; options: string[]; onChange: (value: string) => void }) {
  return (
    <div className="field" style={{ width: 220 }}>
      <label>{label}</label>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </div>
  );
}

function InlineField({
  disabled = false,
  label,
  value,
  onChange,
  type = "text"
}: {
  disabled?: boolean;
  label: string;
  value: string;
  onChange: (value: string) => void;
  type?: string;
}) {
  return (
    <div className="field" style={{ width: 150 }}>
      <label>{label}</label>
      <input disabled={disabled} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function Field({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return (
    <div className="field">
      <label>{label}</label>
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function ScopeItem({ className, label, value }: { className?: string; label: string; value: string }) {
  return (
    <div className={className ? `scope-item ${className}` : "scope-item"}>
      <span>{label}</span>
      <b title={value}>{value}</b>
    </div>
  );
}

function CatalogHelpButton({ help, label }: { help: string; label: string }) {
  return (
    <button aria-label={`Help for ${label}`} className="parameter-help-button catalog-help-button" data-help={help} type="button">
      <CircleHelp size={12} />
    </button>
  );
}

function CatalogFieldLabel({ help, label }: { help: string; label: string }) {
  return (
    <span className="catalog-field-label">
      <span>{label}</span>
      <CatalogHelpButton help={help} label={label} />
    </span>
  );
}

function CatalogSelect({
  help,
  label,
  labels = {},
  onChange,
  options,
  value,
}: {
  help: string;
  label: string;
  labels?: Record<string, string>;
  onChange: (value: string) => void;
  options: string[];
  value: string;
}) {
  return (
    <div className="catalog-field">
      <CatalogFieldLabel help={help} label={label} />
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{labels[option] ?? displayName(option)}</option>
        ))}
      </select>
    </div>
  );
}

function CatalogNumberField({
  help,
  label,
  max,
  min,
  onChange,
  value,
}: {
  help: string;
  label: string;
  max: number;
  min: number;
  onChange: (value: number) => void;
  value: number;
}) {
  return (
    <div className="catalog-field">
      <CatalogFieldLabel help={help} label={label} />
      <input max={max} min={min} type="number" value={String(value)} onChange={(event) => onChange(boundedNumber(event.target.value, min, max))} />
    </div>
  );
}

function CatalogTextField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: string) => void; value: string }) {
  return (
    <div className="catalog-field">
      <CatalogFieldLabel help={help} label={label} />
      <input maxLength={24} type="text" value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function CatalogCheckbox({ checked, help, label, onChange }: { checked: boolean; help: string; label: string; onChange: (value: boolean) => void }) {
  return (
    <div className="catalog-checkbox">
      <label className="catalog-checkbox-control">
        <input checked={checked} type="checkbox" onChange={(event) => onChange(event.target.checked)} />
        <span>{label}</span>
      </label>
      <CatalogHelpButton help={help} label={label} />
    </div>
  );
}

function CatalogGroupedPartsEditor({ parts }: { parts: Array<Record<string, unknown>> }) {
  return (
    <div className="catalog-presentation-section catalog-grouped-parts-section">
      <div className="catalog-grouped-parts-header">
        <div>
          <h4>Grouped Items</h4>
          <p>Select an item above to edit its valid display preset and style fields.</p>
        </div>
      </div>
      <div className="catalog-grouped-parts-grid">
        {parts.map((part, index) => (
          <div className="catalog-grouped-part-card" key={`${String(part.column ?? part.id ?? index)}:${index}`}>
            <div className="catalog-grouped-part-title">
              <span className="catalog-part-swatch" style={{ background: presentationColor(part.color) }} />
              <div>
                <strong>{String(part.label ?? part.column ?? part.id ?? `Part ${index + 1}`)}</strong>
                <small>{part.column ? String(part.column) : displayName(String(part.chartRole ?? "part"))}</small>
              </div>
            </div>
            <span className="catalog-part-role">{displayName(normalizeDisplayType(String(part.chartRole ?? part.style ?? "price_overlay")))}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function presentationIsMacdTarget(selected: CatalogCardItem | undefined, presentation: Record<string, unknown>): boolean {
  const candidates = [
    selected?.id,
    selected?.column,
    presentation.id,
    presentation.column,
    presentation.groupKey,
    selected?.presentation?.groupKey,
    ...(Array.isArray(selected?.sourceColumns) ? selected.sourceColumns : []),
    ...(Array.isArray(presentation.sourceColumns) ? presentation.sourceColumns : []),
  ].filter(Boolean).map(String);
  return candidates.some((candidate) => {
    const value = candidate.toLowerCase();
    return value === "macd" || value.startsWith("macd_") || value.startsWith("indicator.macd");
  });
}

function lowerPaneOptionsForTarget(isMacdTarget: boolean): string[] {
  return isMacdTarget ? ["macd"] : NON_MACD_LOWER_PANES;
}

function paneDisplayLabel(value: string): string {
  return PANE_LABELS[value] ?? displayName(value);
}

function normalizedLowerPane(value: unknown, isMacdTarget: boolean): string {
  const current = String(value ?? "").toLowerCase();
  const aliases: Record<string, string> = {
    "": "pane_2",
    macd_pane: "macd",
    "macd pane": "macd",
    new: "pane_2",
    oscillator: "pane_2",
    participation: "pane_2",
    pane2: "pane_2",
    "pane 2": "pane_2",
    pane3: "pane_3",
    "pane 3": "pane_3",
    shock: "pane_2",
    stochastic: "pane_2",
    supervision: "pane_2",
  };
  const normalized = aliases[current] ?? current;
  const options = lowerPaneOptionsForTarget(isMacdTarget);
  return options.includes(normalized) ? normalized : options[0] ?? "pane_2";
}

function defaultPaneForDisplayType(displayType: string, currentPane: string, isMacdTarget: boolean): string {
  if (["price_overlay", "marker", "text_label", "continuous_band", "anchored_zone", "background_state", "band", "price_zone"].includes(displayType)) return "price";
  if (displayType === "oscillator" || displayType === "histogram") return normalizedLowerPane(currentPane, isMacdTarget);
  return currentPane;
}

function normalizeDisplayType(value: string): string {
  if (value === "table_only") return "data_only";
  if (value === "price_zone") return "anchored_zone";
  if (value === "band") return "continuous_band";
  return value || "data_only";
}

function isDataOnlyRole(value: string): boolean {
  return normalizeDisplayType(value) === "data_only";
}

function presentationPreset(catalog: CatalogPayload | null, displayType: string): PresentationPreset {
  const normalized = normalizeDisplayType(displayType);
  return catalog?.presentationPresets?.[normalized] ?? {
    dataShapes: ["any"],
    description: normalized === "data_only" ? "Available in tables and catalog, but not rendered on the chart." : "The selected display type controls the available style fields.",
    label: displayName(normalized),
    styleFields: normalized === "data_only" ? ["valueFormat", "precision"] : ["color", "lineStyle", "lineWidth", "valueFormat", "precision"],
    target: normalized === "data_only" ? "none" : "price",
  };
}

function displayTypeOptionsForTarget(catalog: CatalogPayload | null, dataShape: string, isGroup: boolean): string[] {
  if (isGroup) return ["composite"];
  const presets = catalog?.presentationPresets ?? {};
  const options = Object.keys(presets).filter((key) => {
    if (key === "composite") return isGroup;
    const shapes = presets[key]?.dataShapes ?? [];
    return shapes.includes("any") || shapes.includes(dataShape);
  });
  if (options.length) return options;
  return catalog?.presentationOptions.chartRoles?.map(normalizeDisplayType).filter((value, index, values) => values.indexOf(value) === index) ?? ["data_only"];
}

function styleTargetLabel(value: string, parts: Array<Record<string, unknown>>): string {
  if (value === "group") return "Group";
  const index = Number(value.replace("part:", ""));
  const part = parts[index] ?? {};
  return String(part.label ?? part.column ?? part.id ?? `Item ${index + 1}`);
}

function styleSectionTitle(displayType: string): string {
  if (displayType === "price_overlay" || displayType === "oscillator") return "Visual Style";
  if (displayType === "histogram") return "Histogram Style";
  if (displayType === "marker") return "Marker Color";
  if (displayType === "text_label") return "Text Label Style";
  if (displayType === "continuous_band") return "Continuous Band Style";
  if (displayType === "anchored_zone") return "Anchored Zone Style";
  if (displayType === "band" || displayType === "price_zone") return "Band Style";
  if (displayType === "composite") return "Base Style";
  return "Visual Style";
}

function styleEditorLabel(displayType: string): string {
  if (displayType === "price_overlay" || displayType === "oscillator") return "Style";
  if (displayType === "histogram") return "Histogram bars";
  if (displayType === "marker") return "Marker color";
  if (displayType === "text_label") return "Text label";
  if (displayType === "continuous_band") return "Continuous band";
  if (displayType === "anchored_zone") return "Anchored zone";
  if (displayType === "band" || displayType === "price_zone") return "Band and boundary";
  return "Base style";
}

function CatalogPresentationChartPreview({
  itemTitle,
  presentation,
  presentationType,
  realPreview,
  realPreviewLoading,
}: {
  itemTitle: string;
  presentation: CatalogPresentation;
  presentationType: string;
  realPreview: CatalogPreviewPayload | null;
  realPreviewLoading: boolean;
}) {
  const role = normalizeDisplayType(String(presentation.chartRole ?? "data_only"));
  const pane = String(presentation.pane ?? "price");
  const lineStyle = String(presentation.lineStyle ?? "solid");
  const lineWidth = boundedPresentationNumber(presentation.lineWidth, 1, 6, 2);
  const strokeColor = presentationColor(presentation.color);
  const strokeOpacity = boundedPresentationNumber(presentation.opacity, 0.05, 1, 1);
  const selectedStroke = typeof presentation.color === "string" && presentation.color.startsWith("#") ? colorWithOpacity(presentation.color, strokeOpacity) : strokeColor;
  const bandFillOpacity = boundedPresentationNumber(presentation.bandFillOpacity, 0, 0.35, 0.08);
  const bandFill = colorWithOpacity(presentation.bandFillColor ?? presentation.color, bandFillOpacity);
  const bandBorder = colorWithOpacity(presentation.borderColor ?? presentation.bandFillColor ?? presentation.color, boundedPresentationNumber(presentation.borderOpacity, 0, 0.35, Math.max(bandFillOpacity * 1.8, 0.12)));
  const borderStyle = String(presentation.borderStyle ?? lineStyle);
  const borderWidth = boundedPresentationNumber(presentation.borderWidth, 0, 6, lineWidth);
  const fixedZonePreviewHeight = boundedPresentationNumber(presentation.maxPixelHeight ?? presentation.minPixelHeight, 2, 18, 4);
  const dashArray = svgDashArray(lineStyle, lineWidth);
  const zoneDashArray = svgDashArray(borderStyle, borderWidth);
  const displayNameForItem = itemTitle || "Selected item";
  const isBandLike = role === "band" || role === "price_zone" || role === "continuous_band" || role === "anchored_zone";
  const markerText = markerPreviewText(displayNameForItem, presentation);
  const showMarkerText = role === "text_label" || String(presentation.labelMode ?? "none") !== "none";
  const parts = role === "composite" && Array.isArray(presentation.parts) ? presentation.parts.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object")) : [];
  const compositeHasLowerPane = role === "composite" && parts.some((part) => {
    const partRole = normalizeDisplayType(String(part.chartRole ?? part.style ?? ""));
    return String(part.pane ?? pane) !== "price" || partRole === "oscillator" || partRole === "histogram";
  });
  const hasPreviewPane = role === "oscillator" || role === "histogram" || compositeHasLowerPane || (role === "composite" && pane !== "price");
  const showRealPreview = !isDataOnlyRole(role) && Boolean(realPreview?.sampled && realPreview.payload?.candles?.length);
  return (
    <aside className="catalog-preview-chart-card" aria-label={`Chart preview for ${displayNameForItem}`}>
      <div className="catalog-preview-chart-header">
        <div>
          <span>Preview</span>
          <strong>{displayNameForItem}</strong>
        </div>
        <small>{presentationTypeLabel(presentationType)}</small>
      </div>
      {showRealPreview ? (
        <CatalogRealSampleChart itemTitle={displayNameForItem} presentation={presentation} preview={realPreview as CatalogPreviewPayload} />
      ) : (
      <svg className="catalog-contract-chart" viewBox="0 0 380 238" role="img" aria-label={`${displayNameForItem} presentation preview`}>
        <rect className="catalog-contract-chart-bg" x="0" y="0" width="380" height="238" rx="8" />
        <g className="catalog-contract-chart-grid">
          {[44, 74, 104, 134].map((y) => <line key={`py:${y}`} x1="20" x2="356" y1={y} y2={y} />)}
          {[58, 116, 174, 232, 290, 348].map((x) => <line key={`px:${x}`} x1={x} x2={x} y1="26" y2="151" />)}
        </g>
        {hasPreviewPane ? (
          <g className="catalog-contract-pane">
            <rect x="20" y="164" width="336" height="60" rx="5" />
            <g className="catalog-contract-chart-grid">
              <line x1="20" x2="356" y1="178" y2="178" />
              <line x1="20" x2="356" y1="210" y2="210" />
              {[58, 116, 174, 232, 290, 348].map((x) => <line key={`ox:${x}`} x1={x} x2={x} y1="164" y2="222" />)}
            </g>
            <line className="catalog-contract-zero-line" x1="20" x2="356" y1={CATALOG_PREVIEW_PANE_ZERO_Y} y2={CATALOG_PREVIEW_PANE_ZERO_Y} />
          </g>
        ) : null}
        {isBandLike ? (
          <g className="catalog-contract-selected-layer">
            {role === "anchored_zone" && String(presentation.zoneHeightMode ?? "price_range") === "fixed_px" ? (
              <rect fill={bandFill} height={fixedZonePreviewHeight} stroke={bandBorder} strokeDasharray={zoneDashArray} strokeWidth={borderWidth} width="316" x="34" y={88 - fixedZonePreviewHeight / 2} />
            ) : (
              <>
                <polygon fill={bandFill} points="34,88 66,75 98,79 130,92 162,86 194,72 226,76 258,68 290,63 322,66 350,60 350,100 322,106 290,102 258,109 226,112 194,106 162,114 130,121 98,112 66,106 34,119" />
                <polyline fill="none" points="34,88 66,75 98,79 130,92 162,86 194,72 226,76 258,68 290,63 322,66 350,60" stroke={bandBorder} strokeDasharray={role === "anchored_zone" ? zoneDashArray : dashArray} strokeWidth={role === "anchored_zone" ? borderWidth : lineWidth} />
                <polyline fill="none" points="34,119 66,106 98,112 130,121 162,114 194,106 226,112 258,109 290,102 322,106 350,100" stroke={bandBorder} strokeDasharray={role === "anchored_zone" ? zoneDashArray : dashArray} strokeWidth={role === "anchored_zone" ? borderWidth : lineWidth} />
              </>
            )}
          </g>
        ) : null}
        {isDataOnlyRole(role) ? null : (
          <g className="catalog-contract-candles">
            {CATALOG_PREVIEW_CANDLES.map((candle) => {
              const bullish = candle.close < candle.open;
              const color = bullish ? "#33E42A" : "#FD0E50";
              const top = Math.min(candle.open, candle.close);
              const height = Math.max(3, Math.abs(candle.close - candle.open));
              return (
                <g key={candle.x}>
                  <line className="catalog-contract-wick" x1={candle.x} x2={candle.x} y1={candle.high} y2={candle.low} stroke={color} />
                  <rect className="catalog-contract-candle-body" fill={color} height={height} rx="1" width="9" x={candle.x - 4.5} y={top} />
                  <rect className="catalog-contract-volume" fill={color} height={candle.volume} opacity="0.28" width="9" x={candle.x - 4.5} y={151 - candle.volume} />
                </g>
              );
            })}
          </g>
        )}
        {role === "price_overlay" ? (
          <polyline className="catalog-contract-selected-line" fill="none" points={CATALOG_PRICE_LINE_POINTS} stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
        ) : null}
        {role === "composite" && !hasPreviewPane && pane === "price" ? (
          <g className="catalog-contract-selected-layer">
            {(parts.length ? parts : [{ color: strokeColor }, { color: "#B7791F" }]).slice(0, 4).map((part, index) => (
              <polyline
                fill="none"
                key={`composite-price:${index}`}
                points={CATALOG_PRICE_LINE_POINTS}
                stroke={presentationColor(part.color ?? strokeColor)}
                strokeDasharray={svgDashArray(String(part.lineStyle ?? (index % 2 ? "dashed" : "solid")), boundedPresentationNumber(part.lineWidth, 1, 6, lineWidth))}
                strokeWidth={boundedPresentationNumber(part.lineWidth, 1, 6, lineWidth)}
                transform={`translate(0 ${index * 7})`}
              />
            ))}
          </g>
        ) : null}
        {role === "marker" || role === "text_label" ? renderCatalogPreviewMarker(String(presentation.markerShape ?? "circle"), String(presentation.markerPosition ?? "belowBar"), selectedStroke, showMarkerText ? markerText : "") : null}
        {role === "oscillator" ? (
          <polyline className="catalog-contract-selected-line" fill="none" points={CATALOG_OSCILLATOR_LINE_POINTS} stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
        ) : null}
        {role === "composite" && hasPreviewPane ? (
          <g className="catalog-contract-selected-layer">
            {(parts.length ? parts : [{ color: strokeColor }, { color: "#B7791F" }]).slice(0, 4).map((part, index) => {
              const partRole = String(part.chartRole ?? part.style ?? "");
              if (partRole === "histogram") {
                return (
                  <g className="catalog-contract-histogram" key={`composite-hist:${index}`}>
                    {CATALOG_HISTOGRAM_BARS.map((bar) => (
                      <rect
                        fill={String(part.color) === "inherit_candle_direction" ? (bar.value >= 0 ? "#33E42A" : "#FD0E50") : presentationColor(part.color ?? strokeColor)}
                        height={Math.abs(bar.value)}
                        key={`${index}:${bar.x}`}
                        width="12"
                        x={bar.x - 6}
                        y={bar.value >= 0 ? CATALOG_PREVIEW_PANE_ZERO_Y - bar.value : CATALOG_PREVIEW_PANE_ZERO_Y}
                      />
                    ))}
                  </g>
                );
              }
              return (
                <polyline
                  className="catalog-contract-selected-line"
                  fill="none"
                  key={`composite-line:${index}`}
                  points={CATALOG_OSCILLATOR_LINE_POINTS}
                  stroke={presentationColor(part.color ?? strokeColor)}
                  strokeDasharray={svgDashArray(String(part.lineStyle ?? (index % 2 ? "dashed" : "solid")), boundedPresentationNumber(part.lineWidth, 1, 6, lineWidth))}
                  strokeWidth={boundedPresentationNumber(part.lineWidth, 1, 6, lineWidth)}
                  transform={`translate(0 ${index * 8 - 6})`}
                />
              );
            })}
          </g>
        ) : null}
        {role === "histogram" ? (
          <g className="catalog-contract-histogram">
            {CATALOG_HISTOGRAM_BARS.map((bar) => {
              const positive = bar.value >= 0;
              const fill = String(presentation.color) === "inherit_candle_direction" ? (positive ? "#33E42A" : "#FD0E50") : strokeColor;
              return (
                <rect
                  fill={fill}
                  height={Math.abs(bar.value)}
                  key={bar.x}
                  width="13"
                  x={bar.x - 6.5}
                  y={positive ? CATALOG_PREVIEW_PANE_ZERO_Y - bar.value : CATALOG_PREVIEW_PANE_ZERO_Y}
                />
              );
            })}
          </g>
        ) : null}
        {isDataOnlyRole(role) ? (
          <g className="catalog-contract-table-only">
            <rect x="108" y="76" width="164" height="34" rx="8" />
            <text x="190" y="97">Data only</text>
          </g>
        ) : null}
        <g className="catalog-contract-axis-labels">
          <text x="26" y="24">09:30</text>
          <text x="182" y="24">{hasPreviewPane ? paneDisplayLabel(pane === "price" ? "pane_2" : pane) : "Price"}</text>
          <text x="326" y="24">11:30</text>
        </g>
      </svg>
      )}
      <p>
        {realPreviewLoading ? "Finding saved provider data for this contract..." :
          showRealPreview ? realPreviewDescription(realPreview as CatalogPreviewPayload) :
          isDataOnlyRole(role) ? "This field stays available in tables and catalog, but is not drawn on the chart." :
          realPreview?.reason ? `Synthetic fallback: ${realPreview.reason}` : "Synthetic fallback; only this selected catalog item is drawn."}
      </p>
    </aside>
  );
}

function CatalogRealSampleChart({ itemTitle, presentation, preview }: { itemTitle: string; presentation: CatalogPresentation; preview: CatalogPreviewPayload }) {
  const payload = preview.payload;
  const role = normalizeDisplayType(String(presentation.chartRole ?? "data_only"));
  const pane = String(presentation.pane ?? "price");
  const lineStyle = String(presentation.lineStyle ?? "solid");
  const lineWidth = boundedPresentationNumber(presentation.lineWidth, 1, 6, 2);
  const strokeColor = presentationColor(presentation.color);
  const strokeOpacity = boundedPresentationNumber(presentation.opacity, 0.05, 1, 1);
  const selectedStroke = typeof presentation.color === "string" && presentation.color.startsWith("#") ? colorWithOpacity(presentation.color, strokeOpacity) : strokeColor;
  const bandFillOpacity = boundedPresentationNumber(presentation.bandFillOpacity, 0, 0.35, 0.08);
  const bandFill = colorWithOpacity(presentation.bandFillColor ?? presentation.color, bandFillOpacity);
  const bandBorder = colorWithOpacity(presentation.borderColor ?? presentation.bandFillColor ?? presentation.color, boundedPresentationNumber(presentation.borderOpacity, 0, 0.35, Math.max(bandFillOpacity * 1.8, 0.12)));
  const borderStyle = String(presentation.borderStyle ?? lineStyle);
  const borderWidth = boundedPresentationNumber(presentation.borderWidth, 0, 6, lineWidth);
  const fixedZonePreviewHeight = boundedPresentationNumber(presentation.maxPixelHeight ?? presentation.minPixelHeight, 2, 18, 4);
  const dashArray = svgDashArray(lineStyle, lineWidth);
  const zoneDashArray = svgDashArray(borderStyle, borderWidth);
  const parts = role === "composite" && Array.isArray(presentation.parts) ? presentation.parts.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object")) : [];
  const compositeHasLowerPane = role === "composite" && parts.some((part) => {
    const partRole = normalizeDisplayType(String(part.chartRole ?? part.style ?? ""));
    return String(part.pane ?? pane) !== "price" || partRole === "oscillator" || partRole === "histogram";
  });
  const isZoneRole = role === "band" || role === "price_zone" || role === "continuous_band" || role === "anchored_zone";
  const isLowerPaneRole = role === "oscillator" || role === "histogram" || compositeHasLowerPane || (role === "composite" && pane !== "price");
  const isPriceSeriesRole = role === "price_overlay" || (role === "composite" && !isLowerPaneRole && pane === "price");
  const isMarkerRole = role === "marker" || role === "text_label";
  const markerText = markerPreviewText(itemTitle, presentation);
  const showMarkerText = role === "text_label" || String(presentation.labelMode ?? "none") !== "none";
  const allCandles = payload?.candles ?? [];
  const referenceTime = Number(preview.sample?.time ?? 0);
  const referenceIndex = referenceTime ? Math.max(0, allCandles.findIndex((candle) => candle.time >= referenceTime)) : 0;
  const startIndex = Math.max(0, referenceIndex - 28);
  const candles = allCandles.slice(startIndex, Math.min(allCandles.length, startIndex + 72));
  const times = candles.map((candle) => candle.time);
  const minTime = times[0] ?? 0;
  const maxTime = times[times.length - 1] ?? 0;
  const realDataSeries = [...(payload?.overlay_series ?? []), ...(payload?.oscillator_series ?? [])];
  const visibleDataSeries = realDataSeries
    .map((series) => ({ ...series, data: series.data.filter((point) => point.time >= minTime && point.time <= maxTime && Number.isFinite(point.value)) }))
    .filter((series) => series.data.length);
  const visibleZones = isZoneRole ? (payload?.price_zones ?? []).filter((zone) => zone.end >= minTime && zone.start <= maxTime) : [];
  const visibleMarkers = isMarkerRole ? (payload?.markers ?? []).filter((marker) => times.includes(Number(marker.time))).slice(0, 40) : [];
  const referenceCandle = candles.find((candle) => candle.time === referenceTime) ?? candles[Math.max(0, Math.min(candles.length - 1, referenceIndex - startIndex))] ?? candles[Math.floor(candles.length / 2)];
  const priceValues = [
    ...candles.flatMap((candle) => [candle.high, candle.low]),
    ...visibleZones.flatMap((zone) => [zone.upper, zone.lower]),
    ...(isPriceSeriesRole ? visibleDataSeries.flatMap((series) => series.data.map((point) => point.value)) : []),
  ].filter((value) => Number.isFinite(value));
  const priceMin = Math.min(...priceValues);
  const priceMax = Math.max(...priceValues);
  const pricePad = Math.max((priceMax - priceMin) * 0.08, Math.abs(priceMax) * 0.0004, 0.01);
  const priceScale = (value: number) => scaleLinear(value, priceMin - pricePad, priceMax + pricePad, 151, 34);
  const xForTime = (time: number) => {
    if (!times.length) return 24;
    if (time <= times[0]) return 24;
    if (time >= times[times.length - 1]) return 356;
    const exact = times.indexOf(time);
    if (exact >= 0) return 24 + exact * (332 / Math.max(1, times.length - 1));
    const index = times.findIndex((candidate) => candidate > time);
    const leftIndex = Math.max(0, index - 1);
    const rightIndex = Math.max(leftIndex + 1, index);
    const span = Math.max(1, times[rightIndex] - times[leftIndex]);
    return 24 + (leftIndex + (time - times[leftIndex]) / span) * (332 / Math.max(1, times.length - 1));
  };
  const oscillatorPoints = visibleDataSeries.flatMap((series) => series.data);
  const hasPreviewPane = isLowerPaneRole;
  const oscValues = oscillatorPoints.map((point) => point.value).filter((value) => Number.isFinite(value));
  const oscAbsMax = oscValues.length ? Math.max(...oscValues.map((value) => Math.abs(value)), 0.01) : 1;
  const oscPad = Math.max(oscAbsMax * 0.12, 0.01);
  const oscScale = (value: number) =>
    scaleLinear(
      value,
      -(oscAbsMax + oscPad),
      oscAbsMax + oscPad,
      CATALOG_PREVIEW_PANE_RANGE_BOTTOM,
      CATALOG_PREVIEW_PANE_RANGE_TOP,
    );
  const zeroY = CATALOG_PREVIEW_PANE_ZERO_Y;
  const candleWidth = Math.max(3, Math.min(8, 240 / Math.max(1, candles.length)));
  const referenceX = referenceTime ? xForTime(referenceTime) : null;
  const fallbackZoneX = referenceCandle ? (referenceX ?? xForTime(referenceCandle.time)) : null;
  const fallbackZoneRect = isZoneRole && !visibleZones.length && referenceCandle && fallbackZoneX !== null
    ? {
      height: role === "anchored_zone" && String(presentation.zoneHeightMode ?? "price_range") === "fixed_px"
        ? fixedZonePreviewHeight
        : Math.max(3, Math.abs(priceScale(referenceCandle.low) - priceScale(referenceCandle.high))),
      width: Math.max(26, Math.min(92, 356 - fallbackZoneX)),
      x: fallbackZoneX,
      y: role === "anchored_zone" && String(presentation.zoneHeightMode ?? "price_range") === "fixed_px"
        ? priceScale((referenceCandle.high + referenceCandle.low) / 2) - fixedZonePreviewHeight / 2
        : Math.min(priceScale(referenceCandle.high), priceScale(referenceCandle.low)),
    }
    : null;
  const seriesPoints = (series: (typeof visibleDataSeries)[number], scale: (value: number) => number) =>
    series.data.map((point) => `${xForTime(point.time)},${scale(point.value)}`).join(" ");
  const partForIndex = (index: number) => parts[index] ?? {};
  const seriesStroke = (index: number) => presentationColor(partForIndex(index).color ?? selectedStroke);
  const seriesLineWidth = (index: number) => boundedPresentationNumber(partForIndex(index).lineWidth, 1, 6, lineWidth);
  const seriesDashArray = (index: number) => svgDashArray(String(partForIndex(index).lineStyle ?? lineStyle), seriesLineWidth(index));

  return (
    <svg className="catalog-contract-chart" viewBox="0 0 380 238" role="img" aria-label={`${itemTitle} real provider preview`}>
      <rect className="catalog-contract-chart-bg" x="0" y="0" width="380" height="238" rx="8" />
      {(payload?.regions ?? []).filter((region) => region.end >= minTime && region.start <= maxTime).map((region, index) => {
        const start = xForTime(region.start);
        const end = xForTime(region.end);
        const width = Math.max(0, end - start);
        return width > 0 ? <rect fill={region.color} key={`region:${index}`} opacity="0.65" x={start} y="31" width={width} height="123" /> : null;
      })}
      <g className="catalog-contract-chart-grid">
        {[54, 84, 114, 144].map((y) => <line key={`real-py:${y}`} x1="20" x2="356" y1={y} y2={y} />)}
      </g>
      {hasPreviewPane ? (
        <g className="catalog-contract-pane">
          <rect x="20" y="164" width="336" height="60" rx="5" />
          <g className="catalog-contract-chart-grid">
            <line x1="20" x2="356" y1="178" y2="178" />
            <line x1="20" x2="356" y1="210" y2="210" />
            {[58, 116, 174, 232, 290, 348].map((x) => <line key={`real-ox:${x}`} x1={x} x2={x} y1="164" y2="222" />)}
          </g>
          <line className="catalog-contract-zero-line" x1="20" x2="356" y1={zeroY} y2={zeroY} />
        </g>
      ) : null}
      {fallbackZoneRect ? (
        <rect
          className="catalog-contract-real-zone"
          fill={bandFill}
          height={fallbackZoneRect.height}
          stroke={bandBorder}
          strokeDasharray={zoneDashArray}
          strokeWidth={borderWidth}
          width={fallbackZoneRect.width}
          x={fallbackZoneRect.x}
          y={fallbackZoneRect.y}
        />
      ) : null}
      {visibleZones.map((zone, index) => {
        const left = xForTime(zone.start);
        const right = xForTime(zone.end);
        const top = priceScale(zone.upper);
        const bottom = priceScale(zone.lower);
        const center = (top + bottom) / 2;
        const zoneHeightMode = String(presentation.zoneHeightMode ?? zone.zoneHeightMode ?? "price_range");
        const minPixelHeight = boundedPresentationNumber(presentation.minPixelHeight ?? zone.minPixelHeight, 0, 32, 0);
        const maxPixelHeight = boundedPresentationNumber(presentation.maxPixelHeight ?? zone.maxPixelHeight, 0, 96, 0);
        const zoneFillOpacity = boundedPresentationNumber(presentation.bandFillOpacity ?? zone.fillOpacity, 0.02, 0.35, 0.08);
        const zoneBorderOpacity = boundedPresentationNumber(presentation.borderOpacity ?? zone.borderOpacity, 0, 0.35, Math.max(zoneFillOpacity * 1.8, 0.12));
        let zoneHeight = Math.max(2, Math.abs(bottom - top));
        let zoneY = Math.min(top, bottom);
        if (zoneHeightMode === "fixed_px") {
          zoneHeight = Math.max(2, minPixelHeight, maxPixelHeight || minPixelHeight || 3);
          zoneY = center - zoneHeight / 2;
        } else {
          if (minPixelHeight > 0 && zoneHeight < minPixelHeight) {
            zoneHeight = minPixelHeight;
            zoneY = center - zoneHeight / 2;
          }
          if (maxPixelHeight > 0 && zoneHeight > maxPixelHeight) {
            zoneHeight = maxPixelHeight;
            zoneY = center - zoneHeight / 2;
          }
        }
        return (
          <rect
            className="catalog-contract-real-zone"
            fill={colorWithOpacity(presentation.bandFillColor ?? presentation.color ?? zone.fillColor ?? zone.color, zoneFillOpacity)}
            height={zoneHeight}
            key={`zone:${index}`}
            stroke={colorWithOpacity(presentation.borderColor ?? presentation.bandFillColor ?? presentation.color ?? zone.borderColor ?? zone.fillColor ?? zone.color, zoneBorderOpacity)}
            strokeDasharray={svgDashArray(String(presentation.borderStyle ?? zone.borderStyle ?? "solid"), boundedPresentationNumber(presentation.borderWidth ?? zone.borderWidth, 0, 6, 1))}
            strokeWidth={boundedPresentationNumber(presentation.borderWidth ?? zone.borderWidth, 0, 6, 1)}
            width={Math.max(2, right - left)}
            x={left}
            y={zoneY}
          />
        );
      })}
      <g className="catalog-contract-candles">
        {candles.map((candle, index) => {
          const x = xForTime(candle.time);
          const bullish = candle.close >= candle.open;
          const color = bullish ? "#33E42A" : "#FD0E50";
          const openY = priceScale(candle.open);
          const closeY = priceScale(candle.close);
          return (
            <g key={`${candle.time}:${index}`}>
              <line className="catalog-contract-wick" x1={x} x2={x} y1={priceScale(candle.high)} y2={priceScale(candle.low)} stroke={color} />
              <rect fill={color} height={Math.max(2, Math.abs(closeY - openY))} rx="1" width={candleWidth} x={x - candleWidth / 2} y={Math.min(openY, closeY)} />
            </g>
          );
        })}
      </g>
      {isPriceSeriesRole ? (
        visibleDataSeries.length ? visibleDataSeries.slice(0, 4).map((series, index) => (
          <polyline
            fill="none"
            key={`overlay:${series.column}:${index}`}
            points={seriesPoints(series, priceScale)}
            stroke={role === "composite" ? seriesStroke(index) : selectedStroke}
            strokeDasharray={role === "composite" ? seriesDashArray(index) : dashArray}
            strokeWidth={role === "composite" ? seriesLineWidth(index) : lineWidth}
          />
        )) : <polyline className="catalog-contract-selected-line" fill="none" points={CATALOG_PRICE_LINE_POINTS} stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
      ) : null}
      {isLowerPaneRole ? (
        visibleDataSeries.length ? visibleDataSeries.slice(0, 4).map((series, index) => {
          const part = partForIndex(index);
          const lowerRole = role === "histogram" ? "histogram" : normalizeDisplayType(String(part.chartRole ?? series.chartRole ?? "oscillator"));
          if (lowerRole === "histogram" || series.style === "histogram") {
            const width = Math.max(3, Math.min(9, candleWidth));
            return (
              <g className="catalog-contract-histogram" key={`osc-hist:${series.column}:${index}`}>
                {series.data.map((point) => {
                  const positive = point.value >= 0;
                  const y = oscScale(point.value);
                  return (
                    <rect
                      fill={String(presentation.color) === "inherit_candle_direction" ? (positive ? "#33E42A" : "#FD0E50") : seriesStroke(index)}
                      height={Math.max(1, Math.abs(zeroY - y))}
                      key={`${series.column}:${point.time}`}
                      width={width}
                      x={xForTime(point.time) - width / 2}
                      y={positive ? y : zeroY}
                    />
                  );
                })}
              </g>
            );
          }
          return (
            <polyline
              fill="none"
              key={`osc:${series.column}:${index}`}
              points={seriesPoints(series, oscScale)}
              stroke={role === "composite" ? seriesStroke(index) : selectedStroke}
              strokeDasharray={role === "composite" ? seriesDashArray(index) : dashArray}
              strokeWidth={role === "composite" ? seriesLineWidth(index) : lineWidth}
            />
          );
        }) : role === "histogram" ? (
          <g className="catalog-contract-histogram">
            {CATALOG_HISTOGRAM_BARS.map((bar) => {
              const positive = bar.value >= 0;
              return (
                <rect
                  fill={String(presentation.color) === "inherit_candle_direction" ? (positive ? "#33E42A" : "#FD0E50") : selectedStroke}
                  height={Math.abs(bar.value)}
                  key={`fallback-real-hist:${bar.x}`}
                  width="13"
                  x={bar.x - 6.5}
                  y={positive ? CATALOG_PREVIEW_PANE_ZERO_Y - bar.value : CATALOG_PREVIEW_PANE_ZERO_Y}
                />
              );
            })}
          </g>
        ) : <polyline className="catalog-contract-selected-line" fill="none" points={CATALOG_OSCILLATOR_LINE_POINTS} stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
      ) : null}
      {isMarkerRole ? (
        visibleMarkers.length ? visibleMarkers.map((marker, index) => (
          <g key={`marker:${index}`}>
            {(() => {
              const markerPosition = String(presentation.markerPosition ?? marker.position ?? "belowBar");
              const markerY = markerPosition === "aboveBar" ? 44 : markerPosition === "inBar" ? 94 : 146;
              const markerX = xForTime(Number(marker.time));
              return (
                <>
                  {renderCatalogRealMarker(
                    String(presentation.markerShape ?? marker.shape ?? "circle"),
                    markerX,
                    markerY,
                    selectedStroke,
                  )}
                  {showMarkerText ? <text className="catalog-contract-marker-text" fill={selectedStroke} x={markerX + 7} y={markerY - 5}>{markerText}</text> : null}
                </>
              );
            })()}
          </g>
        )) : referenceX !== null ? (
          <g>
            {(() => {
              const markerPosition = String(presentation.markerPosition ?? "belowBar");
              const markerY = markerPosition === "aboveBar" ? 44 : markerPosition === "inBar" ? 94 : 146;
              return (
                <>
                  {renderCatalogRealMarker(String(presentation.markerShape ?? "circle"), referenceX, markerY, selectedStroke)}
                  {showMarkerText ? <text className="catalog-contract-marker-text" fill={selectedStroke} x={referenceX + 7} y={markerY - 5}>{markerText}</text> : null}
                </>
              );
            })()}
          </g>
        ) : null
      ) : null}
      {referenceX !== null ? (
        <g className="catalog-contract-reference-line">
          <line x1={referenceX} x2={referenceX} y1="30" y2="218" />
        </g>
      ) : null}
      <g className="catalog-contract-axis-labels">
        <text x="26" y="24">{preview.sample?.ticker}</text>
        <text x="182" y="24">{preview.sample?.timeframe}</text>
        <text x="326" y="24">{preview.sample?.session_date}</text>
      </g>
    </svg>
  );
}

function scaleLinear(value: number, domainMin: number, domainMax: number, rangeMin: number, rangeMax: number) {
  if (!Number.isFinite(value) || domainMax === domainMin) return (rangeMin + rangeMax) / 2;
  return rangeMin + ((value - domainMin) / (domainMax - domainMin)) * (rangeMax - rangeMin);
}

function realPreviewDescription(preview: CatalogPreviewPayload) {
  const sample = preview.sample;
  return sample?.ticker && sample.session_date && sample.timeframe
    ? `Real provider data: ${sample.ticker}, ${sample.timeframe}, ${sample.session_date}.`
    : "Real provider data from the saved tables.";
}

function markerPreviewText(itemTitle: string, presentation: CatalogPresentation): string {
  const explicit = String(presentation.labelText ?? "").trim();
  if (explicit) return explicit.slice(0, 24);
  const mode = String(presentation.labelMode ?? (presentation.chartRole === "text_label" ? "short" : "none"));
  if (mode === "full") return itemTitle.slice(0, 32);
  const signal = String(presentation.signalColumn ?? presentation.column ?? itemTitle);
  return shortEventLabel(signal);
}

function shortEventLabel(value: string): string {
  const key = value.toLowerCase().split(".").pop() ?? "";
  const labels: Record<string, string> = {
    bearish_displacement: "OB-",
    bearish_fvg: "FVG-",
    bos_down: "BOS-",
    bos_up: "BOS+",
    breaks_high20: "BH20",
    breaks_low20: "BL20",
    bullish_displacement: "OB+",
    bullish_fvg: "FVG+",
    higher_high: "HH",
    lower_low: "LL",
    swing_high_3: "SH3",
    swing_high_5: "SH5",
    swing_low_3: "SL3",
    swing_low_5: "SL5",
  };
  if (labels[key]) return labels[key];
  const words = key.split(/[^a-z0-9]+/).filter(Boolean);
  return words.length ? words.slice(0, 4).map((word) => word[0]).join("").toUpperCase() : "EV";
}

function renderCatalogPreviewMarker(shape: string, position: string, color: string, label = ""): ReactNode {
  const x = 222;
  const y = position === "aboveBar" ? 66 : position === "inBar" ? 104 : 138;
  const marker = shape === "arrowDown"
    ? <polygon className="catalog-contract-marker" fill={color} points={`${x - 7},${y - 6} ${x + 7},${y - 6} ${x},${y + 8}`} />
    : shape === "arrowUp"
      ? <polygon className="catalog-contract-marker" fill={color} points={`${x},${y - 8} ${x - 7},${y + 6} ${x + 7},${y + 6}`} />
      : shape === "square"
        ? <rect className="catalog-contract-marker" fill={color} height="14" width="14" x={x - 7} y={y - 7} />
        : <circle className="catalog-contract-marker" cx={x} cy={y} fill={color} r="7" />;
  return (
    <g>
      {marker}
      {label ? <text className="catalog-contract-marker-text" fill={color} x={x + 9} y={y - 5}>{label}</text> : null}
    </g>
  );
}

function renderCatalogRealMarker(shape: string, x: number, y: number, color: string): ReactNode {
  if (shape === "arrowDown") return <polygon className="catalog-contract-marker" fill={color} points={`${x - 6},${y - 5} ${x + 6},${y - 5} ${x},${y + 7}`} />;
  if (shape === "arrowUp") return <polygon className="catalog-contract-marker" fill={color} points={`${x},${y - 7} ${x - 6},${y + 5} ${x + 6},${y + 5}`} />;
  if (shape === "square") return <rect className="catalog-contract-marker" fill={color} height="11" width="11" x={x - 5.5} y={y - 5.5} />;
  return <circle className="catalog-contract-marker" cx={x} cy={y} fill={color} r="5.5" />;
}

function CatalogStylePopover({
  bandFillColor,
  bandFillOpacity,
  chartRole,
  color,
  label = "Style editor",
  lineStyle,
  lineStyleOptions,
  lineWidth,
  opacity,
  precision,
  styleFields,
  onChange
}: {
  bandFillColor: string;
  bandFillOpacity: number;
  chartRole: string;
  color: string;
  label?: string;
  lineStyle: string;
  lineStyleOptions: string[];
  lineWidth: number;
  opacity: number;
  precision: number;
  styleFields: Set<string>;
  onChange: (key: string, value: CatalogPresentationPatchValue) => void;
}) {
  const [open, setOpen] = useState(false);
  const [customColor, setCustomColor] = useState(color.startsWith("#") ? color : "");
  const popoverRef = useRef<HTMLDivElement>(null);
  const panelRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [panelPosition, setPanelPosition] = useState<{ left: number; maxHeight: number; top: number } | null>(null);
  const resolvedLineStyles = lineStyleOptions.length ? lineStyleOptions : ["solid", "dashed", "dotted"];
  const colorLabel = STYLE_COLOR_OPTIONS.find((option) => option.value === color)?.label ?? color;
  const isBand = chartRole === "continuous_band" || chartRole === "anchored_zone" || chartRole === "band" || chartRole === "price_zone";
  const isLineLike = ["price_overlay", "oscillator", "continuous_band", "anchored_zone", "band", "price_zone", "composite"].includes(chartRole);
  const isHistogram = chartRole === "histogram";
  const isMarker = chartRole === "marker" || chartRole === "text_label";
  const showLineControls = isLineLike && (styleFields.has("lineStyle") || styleFields.has("lineWidth"));
  const showLineWidth = styleFields.has("lineWidth");
  const showBandControls = styleFields.has("bandFillColor") || styleFields.has("bandFillOpacity");
  const showOpacity = styleFields.has("opacity");
  const showPrecision = styleFields.has("precision");
  const resolvedBandFillOpacity = Math.max(0, Math.min(0.35, Number.isFinite(bandFillOpacity) ? bandFillOpacity : 0.08));
  const resolvedLineWidth = Math.max(1, Math.min(6, Number.isFinite(lineWidth) ? lineWidth : 1));
  const resolvedOpacity = Math.max(0.05, Math.min(1, Number.isFinite(opacity) ? opacity : 1));
  const previewLineStyle = resolvedLineStyles.includes(lineStyle) ? lineStyle : "solid";
  const strokeColor = presentationColor(color);
  const displayStrokeColor = color.startsWith("#") ? colorWithOpacity(color, resolvedOpacity) : strokeColor;
  const shadeColor = colorWithOpacity(bandFillColor, resolvedBandFillOpacity);
  const previewClassName = ["catalog-style-preview", isBand ? "is-band" : "", isHistogram ? "is-histogram" : "", isMarker ? "is-marker" : ""].filter(Boolean).join(" ");

  useEffect(() => {
    setCustomColor(color.startsWith("#") ? color : "");
  }, [color]);

  useEffect(() => {
    if (!open) return;
    function closeOnOutsideClick(event: MouseEvent) {
      const target = event.target as Node;
      if (!popoverRef.current?.contains(target) && !panelRef.current?.contains(target)) setOpen(false);
    }
    function closeOnEscape(event: KeyboardEvent) {
      if (event.key === "Escape") setOpen(false);
    }
    function updatePosition() {
      setPanelPosition(stylePanelPosition(triggerRef.current, isBand));
    }
    updatePosition();
    document.addEventListener("mousedown", closeOnOutsideClick);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      document.removeEventListener("mousedown", closeOnOutsideClick);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [isBand, open]);

  function setCustomHex(value: string) {
    setCustomColor(value);
    if (/^#[0-9a-f]{6}$/i.test(value)) onChange("color", value.toUpperCase());
  }

  function toggleOpen() {
    if (!open) setPanelPosition(stylePanelPosition(triggerRef.current, isBand));
    setOpen((value) => !value);
  }

  const panel = open && typeof document !== "undefined" ? createPortal(
    <div
      className="catalog-style-popover-panel"
      ref={panelRef}
      role="dialog"
      aria-label="Visual style editor"
      style={panelPosition ? { left: panelPosition.left, maxHeight: panelPosition.maxHeight, top: panelPosition.top } : undefined}
    >
      <section className="catalog-style-popover-section">
        <h5>Preview</h5>
        <div className={previewClassName}>
          <div className="catalog-style-preview-chart" aria-hidden="true">
            <span className="catalog-style-preview-axis horizontal" />
            <span className="catalog-style-preview-axis vertical" />
            {isBand ? <span className="catalog-style-preview-band" style={{ background: shadeColor }} /> : null}
            {isHistogram ? (
              <span className="catalog-style-preview-bars">
                {[18, 34, 25, 44, 30, 39].map((height, index) => (
                  <span key={`style-preview-bar:${index}`} style={{ background: displayStrokeColor, height }} />
                ))}
              </span>
            ) : isMarker ? (
              <span className="catalog-style-preview-marker" style={{ background: displayStrokeColor, borderColor: displayStrokeColor }} />
            ) : (
              <span
                className={`catalog-style-preview-line ${previewLineStyle}`}
                style={{
                  borderColor: displayStrokeColor,
                  borderTopWidth: resolvedLineWidth
                }}
              />
            )}
          </div>
          <div className="catalog-style-preview-copy">
            <strong>{displayName(chartRole)}</strong>
            <span>{stylePreviewDescription(chartRole, lineStyle, resolvedBandFillOpacity)}</span>
          </div>
        </div>
      </section>
      <section className="catalog-style-popover-section">
        <h5>Color</h5>
        <div className="catalog-color-swatch-grid">
          {STYLE_COLOR_OPTIONS.map((option) => (
            <button
              className={color === option.value ? "catalog-color-swatch-option selected" : "catalog-color-swatch-option"}
              key={option.value}
              onClick={() => onChange("color", option.value)}
              type="button"
            >
              <span className="catalog-style-swatch" style={{ background: presentationColor(option.value) }} />
              <span>{option.label}</span>
            </button>
          ))}
        </div>
        <label className="catalog-style-color-picker">
          <span>Color picker</span>
          <input
            aria-label="Style color picker"
            type="color"
            value={colorInputValue(color)}
            onChange={(event) => onChange("color", event.target.value.toUpperCase())}
          />
        </label>
        <label className="catalog-style-inline-field">
          <span>Custom hex</span>
          <input maxLength={7} placeholder="#1E3A5F" value={customColor} onChange={(event) => setCustomHex(event.target.value)} />
        </label>
      </section>
      {isBand && showBandControls ? (
        <section className="catalog-style-popover-section">
          <h5>Band Shade</h5>
          <div className="catalog-band-style-grid">
            <label className="catalog-style-color-picker">
              <span>
                Shade color
                <CatalogHelpButton help={PRESENTATION_HELP.bandFillColor} label="Shade color" />
              </span>
              <input
                aria-label="Band shade color picker"
                type="color"
                value={colorInputValue(bandFillColor)}
                onChange={(event) => onChange("bandFillColor", event.target.value.toUpperCase())}
              />
            </label>
            <label className="catalog-style-range-field">
              <span>
                Opacity
                <CatalogHelpButton help={PRESENTATION_HELP.bandFillOpacity} label="Band shade opacity" />
                <b>{opacityLabel(resolvedBandFillOpacity)}</b>
              </span>
              <input
                max={0.35}
                min={0}
                step={0.01}
                type="range"
                value={String(resolvedBandFillOpacity)}
                onChange={(event) => onChange("bandFillOpacity", boundedNumber(event.target.value, 0, 0.35))}
              />
            </label>
          </div>
        </section>
      ) : null}
      {showLineControls && styleFields.has("lineStyle") ? (
        <section className="catalog-style-popover-section">
          <h5>Line Style</h5>
          <div className="catalog-line-style-grid">
            {resolvedLineStyles.map((option) => (
              <button className={lineStyle === option ? "catalog-line-style-option selected" : "catalog-line-style-option"} key={option} onClick={() => onChange("lineStyle", option)} type="button">
                <span className={`catalog-line-preview ${option}`} />
                <span>{displayName(option)}</span>
              </button>
            ))}
          </div>
        </section>
      ) : null}
      {showLineWidth || showOpacity || showPrecision ? (
      <section className="catalog-style-popover-section">
        <h5>Readout</h5>
        <div className="catalog-style-number-grid">
          {showLineWidth ? (
            <label className="catalog-style-inline-field">
              <span>Line width</span>
              <input max={6} min={1} type="number" value={String(resolvedLineWidth)} onChange={(event) => onChange("lineWidth", boundedNumber(event.target.value, 1, 6))} />
            </label>
          ) : null}
          {showOpacity ? (
            <label className="catalog-style-range-field compact">
              <span>
                Opacity
                <CatalogHelpButton help={PRESENTATION_HELP.opacity} label="Opacity" />
                <b>{opacityLabel(resolvedOpacity)}</b>
              </span>
              <input max={1} min={0.05} step={0.01} type="range" value={String(resolvedOpacity)} onChange={(event) => onChange("opacity", boundedNumber(event.target.value, 0.05, 1))} />
            </label>
          ) : null}
          {showPrecision ? (
            <label className="catalog-style-inline-field">
              <span>Precision</span>
              <input max={8} min={0} type="number" value={String(precision)} onChange={(event) => onChange("precision", boundedNumber(event.target.value, 0, 8))} />
            </label>
          ) : null}
        </div>
      </section>
      ) : null}
    </div>,
    document.body,
  ) : null;

  return (
    <div className="catalog-style-popover" ref={popoverRef}>
      <CatalogFieldLabel help={`${PRESENTATION_HELP.color} ${PRESENTATION_HELP.lineStyle} ${PRESENTATION_HELP.lineWidth}`} label={label} />
      <button aria-expanded={open} className="catalog-style-trigger" onClick={toggleOpen} ref={triggerRef} type="button">
        <span className="catalog-style-trigger-swatch" style={{ background: displayStrokeColor }} />
        <span>
          <strong>{colorLabel}</strong>
          <small>{styleTriggerSummary({ chartRole, isBand, isHistogram, lineStyle, precision, resolvedBandFillOpacity, resolvedLineWidth, resolvedOpacity, showOpacity })}</small>
        </span>
      </button>
      {panel}
    </div>
  );
}

function stylePanelPosition(trigger: HTMLButtonElement | null, isBand: boolean): { left: number; maxHeight: number; top: number } | null {
  if (!trigger) return null;
  const panelWidth = 320;
  const viewportPadding = 12;
  const rect = trigger.getBoundingClientRect();
  const estimatedHeight = isBand ? 640 : 540;
  const rightBound = Math.max(viewportPadding, window.innerWidth - panelWidth - viewportPadding);
  const left = Math.min(Math.max(viewportPadding, rect.left), rightBound);
  const belowTop = rect.bottom + 8;
  const belowSpace = window.innerHeight - belowTop - viewportPadding;
  const aboveSpace = rect.top - viewportPadding - 8;
  const opensAbove = belowSpace < 360 && aboveSpace > belowSpace;
  const availableHeight = Math.max(160, opensAbove ? aboveSpace : belowSpace);
  const maxHeight = Math.min(estimatedHeight, availableHeight);
  const top = opensAbove ? Math.max(viewportPadding, rect.top - maxHeight - 8) : Math.max(viewportPadding, belowTop);
  return {
    left,
    maxHeight,
    top,
  };
}

function stylePreviewDescription(chartRole: string, lineStyle: string, bandFillOpacity: number): string {
  if (["band", "price_zone", "continuous_band", "anchored_zone"].includes(chartRole)) return `${opacityLabel(bandFillOpacity)} band shade`;
  if (chartRole === "histogram") return "Histogram bar color";
  if (chartRole === "marker") return "Marker color";
  if (chartRole === "text_label") return "Text label color";
  return `${displayName(lineStyle)} stroke`;
}

function styleTriggerSummary({
  chartRole,
  isBand,
  isHistogram,
  lineStyle,
  precision,
  resolvedBandFillOpacity,
  resolvedLineWidth,
  resolvedOpacity,
  showOpacity,
}: {
  chartRole: string;
  isBand: boolean;
  isHistogram: boolean;
  lineStyle: string;
  precision: number;
  resolvedBandFillOpacity: number;
  resolvedLineWidth: number;
  resolvedOpacity: number;
  showOpacity: boolean;
}): string {
  const opacity = showOpacity ? ` | ${opacityLabel(resolvedOpacity)}` : "";
  if (isHistogram) return `Histogram bars | ${precision} dp${opacity}`;
  if (chartRole === "marker") return `Marker | ${precision} dp`;
  if (chartRole === "text_label") return `Text label | ${precision} dp`;
  return `${displayName(lineStyle)} | ${resolvedLineWidth}px | ${precision} dp${opacity}${isBand ? ` | ${opacityLabel(resolvedBandFillOpacity)} shade` : ""}`;
}

function boundedNumber(value: string, min: number, max: number): number {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return min;
  return Math.max(min, Math.min(max, numeric));
}

function catalogItems(catalog: CatalogPayload | null): CatalogCardItem[] {
  if (!catalog) return [];
  return [
    ...(catalog.displayItems ?? []).map((item) => catalogDisplayItemToCard(item)),
    ...catalog.columns.map((item) => catalogColumnToCard(item)),
    ...catalog.supervisionMethods.map((item) => catalogMethodToItem(item, "methods")),
    ...catalog.scanners.map((item) => catalogMethodToItem(item, "scanners")),
  ].sort((left, right) =>
    catalogKindOrder(left.catalogKind) - catalogKindOrder(right.catalogKind) ||
    left.groupLabel.localeCompare(right.groupLabel) ||
    left.title.localeCompare(right.title),
  );
}

function catalogKindOrder(kind: CatalogCardItem["catalogKind"]) {
  return { display: 0, columns: 1, methods: 2, scanners: 3 }[kind] ?? 9;
}

function catalogColumnToCard(item: CatalogItem): CatalogCardItem {
  const groupLabel = item.group ?? item.groups?.[0] ?? item.category;
  return {
    ...item,
    catalogKind: "columns",
    groupLabel,
    presentationType: presentationTypeForItem(item),
    sourceLabel: "Column",
    summary: item.knowledge?.shortDescription ?? `${item.title} provider column.`,
  };
}

function catalogDisplayItemToCard(item: CatalogDisplayItem): CatalogCardItem {
  const groupLabel = item.group ?? item.groups?.[0] ?? item.category;
  return {
    ...item,
    catalogKind: "display",
    groupLabel,
    presentationType: presentationTypeForItem(item),
    sourceLabel: "Display item",
    summary: item.knowledge?.shortDescription ?? `${item.title} grouped chart display.`,
  };
}

function catalogMethodToItem(item: CatalogMethod, catalogKind: "methods" | "scanners"): CatalogCardItem {
  const groupLabel = item.method ?? item.category;
  return {
    id: item.id,
    title: item.title,
    category: item.category,
    catalogKind,
    group: groupLabel,
    groupLabel,
    knowledge: item.knowledge,
    presentation: item.presentation,
    presentationType: presentationTypeForItem(item),
    sourceLabel: catalogKind === "methods" ? "Method" : "Scanner",
    summary: item.knowledge?.shortDescription ?? item.thesis ?? `${item.title} catalog item.`,
  };
}

function catalogOptionValues(values: string[]): string[] {
  return ["all", ...Array.from(new Set(values.filter(Boolean))).sort((left, right) => displayName(left).localeCompare(displayName(right)))];
}

function catalogPresentationTypeOptions(items: CatalogCardItem[]): string[] {
  const available = new Set(items.map((item) => item.presentationType).filter(Boolean));
  const ordered = PRESENTATION_TYPE_ORDER.filter((type) => available.has(type));
  const extra = Array.from(available).filter((type) => !PRESENTATION_TYPE_ORDER.includes(type)).sort();
  return ["all", ...ordered, ...extra];
}

function presentationTypeForItem(item: Pick<CatalogItem | CatalogMethod | CatalogDisplayItem, "category" | "presentation">): string {
  const presentation = item.presentation ?? {};
  const role = normalizeDisplayType(String(presentation.chartRole ?? "data_only"));
  if (role === "composite") return "composite_group";
  if (role === "anchored_zone") return "anchored_zone";
  if (role === "continuous_band") return "continuous_band";
  if (role === "background_state") return "background_state";
  if (role === "price_overlay") return "price_overlay";
  if (role === "oscillator") return "lower_pane_line";
  if (role === "histogram") return "histogram_pane";
  if (role === "marker" || role === "text_label") return "event_marker";
  if (role === "data_only") return "data_only";
  return item.category === "bar" ? "data_only" : "other";
}

function presentationTypeLabel(value: string): string {
  return PRESENTATION_TYPE_LABELS[value] ?? displayName(value);
}

function filterCatalogItems(
  items: CatalogCardItem[],
  filters: { category: string; group: string; kind: CatalogKindFilter; presentationType: string; search: string },
): CatalogCardItem[] {
  const queryText = filters.search.trim().toLowerCase();
  return items.filter((item) =>
    (filters.kind === "all" || item.catalogKind === filters.kind) &&
    (filters.category === "all" || item.category === filters.category) &&
    (filters.group === "all" || item.groupLabel === filters.group) &&
    (filters.presentationType === "all" || item.presentationType === filters.presentationType) &&
    (!queryText ||
      [item.title, item.id, item.category, item.groupLabel, item.column, item.summary, presentationTypeLabel(item.presentationType)]
        .some((value) => String(value ?? "").toLowerCase().includes(queryText))),
  );
}

function groupCatalogItems(items: CatalogCardItem[]): Array<{ label: string; items: CatalogCardItem[] }> {
  const sections = new Map<string, CatalogCardItem[]>();
  items.forEach((item) => {
    const label = displayName(item.groupLabel);
    sections.set(label, [...(sections.get(label) ?? []), item]);
  });
  return Array.from(sections.entries()).map(([label, sectionItems]) => ({ label, items: sectionItems }));
}

function defaultCatalogDisplayItems(catalog: CatalogPayload | null): string[] {
  if (!catalog) return [];
  return catalog.displayItems
    .filter((item) => item.presentation?.defaultVisible && item.presentation?.selectable !== false)
    .map((item) => item.id);
}

function defaultCatalogDisplayItemOptions(catalog: CatalogPayload | null): ChartDisplayItem[] {
  return (catalog?.displayItems ?? []).filter((item) => item.presentation?.selectable !== false);
}

function sameList(left: string[], right: string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function chartRequestErrorMessage(error: Error) {
  const status = "status" in error ? (error as Error & { status?: number }).status : undefined;
  if (status === 422 && /session_date|field required|required/i.test(error.message)) {
    return "The running backend is still using the old single-session chart API. Restart the backend, then refresh this page.";
  }
  return error.message || "Unknown chart API error.";
}

function parseCommaList(value: string) {
  return value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
}

function scannerAvailableColumns(records: RecordRow[], sessionDate: string, timeframe: string, featureGroups: string[]) {
  const groups = new Set(["bars", ...featureGroups.map((group) => `features_${group}`)]);
  return Array.from(
    new Set(
      records
        .filter((record) => record.exists && record.session_date === sessionDate && record.timeframe === timeframe && groups.has(record.group))
        .flatMap((record) => record.columns)
    )
  ).sort();
}

function shiftBarTime(value: string, minutes: number) {
  const current = barTimeMinuteOfDay(value) ?? 9 * 60 + 30;
  const next = Math.max(0, Math.min(23 * 60 + 59, current + minutes));
  return `${String(Math.floor(next / 60)).padStart(2, "0")}:${String(next % 60).padStart(2, "0")}`;
}

function barTimeMinuteOfDay(value: string) {
  const [hourText, minuteText] = value.split(":");
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!Number.isFinite(hour) || !Number.isFinite(minute)) return undefined;
  return Math.max(0, Math.min(23 * 60 + 59, hour * 60 + minute));
}

function timeframeMinutes(value: string) {
  if (value.endsWith("m")) return Math.max(1, Number(value.slice(0, -1)) || 1);
  if (value.endsWith("h")) return Math.max(1, (Number(value.slice(0, -1)) || 1) * 60);
  return 1;
}

function timeframeSort(left: string, right: string) {
  const order = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"];
  return (order.indexOf(left) === -1 ? 999 : order.indexOf(left)) - (order.indexOf(right) === -1 ? 999 : order.indexOf(right)) || left.localeCompare(right);
}
