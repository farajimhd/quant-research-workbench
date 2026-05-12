import { useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { BookOpen, CircleHelp, Database, Filter, Search, SlidersHorizontal, Tags } from "lucide-react";
import katex from "katex";
import "katex/dist/katex.min.css";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartLabelOption, type ChartPayload, type ChartReference } from "../app/components/ChartPanel";
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
  row_count: number;
  row_limit: number;
  row_offset: number;
  rows: Record<string, unknown>[];
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
type CatalogKindFilter = "all" | "display" | "columns" | "methods" | "scanners";
type CatalogCardItem = CatalogItem & {
  catalogKind: "display" | "columns" | "methods" | "scanners";
  groupLabel: string;
  presentationType: string;
  sourceLabel: string;
  summary: string;
};
type PreviewChartTarget = {
  record: RecordRow;
  row: Record<string, unknown>;
};

const tabs = ["Overview", "Preview", "Chart", "Coverage", "Artifacts", "Schema", "Catalog"];
const DEFAULT_CHART_FEATURE_GROUPS = ["core", "momentum"];
const DEFAULT_CHART_DISPLAY_ITEMS = ["indicator.vwap", "indicator.tema_trend", "indicator.macd"];
const DEFAULT_CHART_MIN_CONFIDENCE = 0.7;
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
  pane: "Chooses the target pane: price overlays candles, macd groups MACD lines and histogram together, oscillator uses a lower pane, new creates a dedicated pane, and supervision groups labels.",
  lineStyle: "Chooses the stroke pattern for line-like items. Solid is the default; dashed or dotted are for separating related overlays.",
  color: "Default display color. Use a hex color for a fixed line or marker color; inherit_candle_direction follows the candle up/down color where supported.",
  lineWidth: "Controls line or band stroke thickness in pixels. Larger values make the item visually heavier.",
  opacity: "Controls visual strength without changing the value. Important long-horizon overlays can be thicker but more transparent so they remain visible without dominating candles.",
  bandFillColor: "Controls the translucent fill used inside a band. This is separate from the band boundary stroke color.",
  bandFillOpacity: "Controls how visible the band shade is. Lower values keep candles readable; higher values make the band easier to scan.",
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
  const [visibleSupervisionGroups, setVisibleSupervisionGroups] = useState<string[]>([]);
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
    const defaults = defaultCatalogSupervisionGroups(catalog);
    if (!defaults.length || visibleSupervisionGroups.length) return;
    setVisibleSupervisionGroups(defaults);
  }, [catalog, visibleSupervisionGroups.length]);

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
      `/api/market-data/chart${query({
        processed_root: scope.processed_root,
        start_date: rangeStart,
        end_date: rangeEnd,
        timeframe,
        ticker: ticker.trim().toUpperCase(),
        feature_groups: featureGroups.join(","),
        display_items: visibleColumns.join(","),
        supervision_groups: visibleSupervisionGroups.join(","),
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
  }, [scope.processed_root, rangeEnd, rangeStart, timeframe, ticker, featureGroups, visibleColumns, visibleSupervisionGroups]);

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
  const labelOptions = chartLabelOptions(catalog, payload?.options?.supervision_groups ?? []);

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
        labelOptions={labelOptions}
        loading={chartLoading}
        onPeriodChange={updateChartPeriod}
        onTickerChange={setTicker}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={setVisibleColumns}
        onVisibleSupervisionGroupsChange={setVisibleSupervisionGroups}
        payload={payload}
        periodEnd={rangeEnd}
        periodMax={availableSessions[availableSessions.length - 1] ?? scope.end_date}
        periodMin={availableSessions[0] ?? scope.start_date}
        periodStart={rangeStart}
        ticker={ticker}
        timeframe={timeframe}
        timeframes={timeframes}
        visibleColumns={visibleColumns}
        visibleSupervisionGroups={visibleSupervisionGroups}
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

function Preview({ catalog, scope, records }: { catalog: CatalogPayload | null; scope: Scope; records: RecordRow[] }) {
  const [recordKey, setRecordKey] = useState(records[0]?.key ?? "");
  const record = records.find((item) => item.key === recordKey) ?? records[0];
  const [rowLimit, setRowLimit] = useState(1000);
  const [loadAllRows, setLoadAllRows] = useState(false);
  const [previewOffset, setPreviewOffset] = useState(0);
  const [tickers, setTickers] = useState("");
  const [backendQuery, setBackendQuery] = useState<BackendTableQuery>({ conditions: [], matchMode: "all", sortDirection: "asc" });
  const [sample, setSample] = useState<PreviewSample | null>(null);
  const [sampleError, setSampleError] = useState("");
  const [sampleLoading, setSampleLoading] = useState(false);
  const [chartTarget, setChartTarget] = useState<PreviewChartTarget | null>(null);
  const backendQueryKey = useMemo(() => JSON.stringify(cleanPreviewBackendQuery(backendQuery)), [backendQuery]);
  const fillPanel = useViewportFillPanel(`${recordKey}:${rowLimit}:${loadAllRows}:${previewOffset}:${tickers}:${backendQueryKey}:${sample?.rows.length ?? 0}`);
  useEffect(() => {
    setPreviewOffset(0);
  }, [record?.key, loadAllRows, rowLimit, tickers, backendQueryKey]);
  useEffect(() => {
    if (!record) return;
    let active = true;
    const cleanedQuery = cleanPreviewBackendQuery(backendQuery);
    const tableQuery = previewBackendQueryIsActive(cleanedQuery) ? JSON.stringify(cleanedQuery) : undefined;
    setSampleError("");
    setSampleLoading(true);
    api<{ sample: PreviewSample }>(
      `/api/market-data/preview${query({
        processed_root: scope.processed_root,
        group: record.group,
        timeframe: record.timeframe,
        session_date: record.session_date,
        all_rows: loadAllRows,
        row_limit: loadAllRows ? PREVIEW_PAGE_SIZE : rowLimit,
        row_offset: loadAllRows ? previewOffset : 0,
        table_query: tableQuery,
        tickers
      })}`
    )
      .then((payload) => {
        if (!active) return;
        setSample(payload.sample);
      })
      .catch((error: Error) => {
        if (!active) return;
        setSample({ columns: record.columns, row_count: 0, row_limit: loadAllRows ? PREVIEW_PAGE_SIZE : rowLimit, row_offset: 0, rows: [] });
        setSampleError(error.message);
      })
      .finally(() => {
        if (active) setSampleLoading(false);
      });
    return () => {
      active = false;
    };
  }, [scope.processed_root, record?.key, loadAllRows, previewOffset, rowLimit, tickers, backendQuery, backendQueryKey]);
  if (!record) return <div className="empty-state">No records available.</div>;
  const previewTotalRows = sample?.row_count ?? 0;
  const previewStartRow = sample?.rows.length ? (sample.row_offset ?? 0) + 1 : 0;
  const previewEndRow = sample ? (sample.row_offset ?? 0) + sample.rows.length : 0;
  const canPageBack = loadAllRows && previewOffset > 0 && !sampleLoading;
  const canPageForward = loadAllRows && previewEndRow < previewTotalRows && !sampleLoading;
  return (
    <section className="panel table-fill-panel" ref={fillPanel.ref} style={fillPanel.style}>
      <div className="toolbar">
        <div className="field" style={{ flex: "1 1 360px", minWidth: 280 }}>
          <label>Artifact</label>
          <select value={recordKey} onChange={(event) => setRecordKey(event.target.value)}>
            {records.map((item) => (
              <option key={item.key} value={item.key}>
                {item.group} | {item.timeframe} | {item.session_date}
              </option>
            ))}
          </select>
        </div>
        <div className="preview-row-limit">
          <div className="field preview-row-limit-field">
            <div className="preview-row-limit-header">
              <label htmlFor="preview-row-limit-input">Rows</label>
              <label className="preview-all-rows-radio">
                <input checked={loadAllRows} onChange={(event) => setLoadAllRows(event.target.checked)} type="checkbox" />
                <span aria-hidden="true" />
                <b>All rows</b>
              </label>
            </div>
            <input
              disabled={loadAllRows}
              id="preview-row-limit-input"
              type="number"
              value={String(rowLimit)}
              onChange={(event) => {
                const next = Number(event.target.value);
                if (Number.isFinite(next)) {
                  setRowLimit(Math.max(10, Math.round(next)));
                  setLoadAllRows(false);
                }
              }}
            />
          </div>
        </div>
        <InlineField label="Tickers" value={tickers} onChange={setTickers} />
      </div>
      {sampleError ? <div className="preview-sample-status error">Preview request failed: {sampleError}</div> : null}
      {sampleLoading ? (
        <div className="preview-sample-status">
          <span className="loading-spinner" aria-hidden="true" />
          Loading preview rows...
        </div>
      ) : null}
      {loadAllRows && sample && !sampleError ? (
        <div className="preview-page-status">
          <span>
            Showing {previewStartRow.toLocaleString()}-{previewEndRow.toLocaleString()} of {previewTotalRows.toLocaleString()} rows
          </span>
          <button className="table-text-button" disabled={!canPageBack} onClick={() => setPreviewOffset((value) => Math.max(0, value - PREVIEW_PAGE_SIZE))} type="button">
            Previous
          </button>
          <button className="table-text-button" disabled={!canPageForward} onClick={() => setPreviewOffset((value) => value + PREVIEW_PAGE_SIZE)} type="button">
            Next
          </button>
        </div>
      ) : null}
      <DataTable
        backendQuery={{
          columns: record.columns,
          loading: sampleLoading,
          onChange: setBackendQuery,
          value: backendQuery,
        }}
        rowAction={{
          isAvailable: (row) => rowHasChartContext(row, record),
          label: "Open row in chart",
          onSelect: (row) => setChartTarget({ record, row }),
        }}
        rows={sample?.rows ?? []}
        columns={sample?.columns}
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
  const [visibleSupervisionGroups, setVisibleSupervisionGroups] = useState(initial.visibleSupervisionGroups);
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
      `/api/market-data/chart${query({
        processed_root: scope.processed_root,
        start_date: rangeStart,
        end_date: rangeEnd,
        timeframe,
        ticker,
        feature_groups: featureGroups.join(","),
        display_items: visibleColumns.join(","),
        supervision_groups: visibleSupervisionGroups.join(","),
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
  }, [featureGroups, rangeEnd, rangeStart, scope.processed_root, ticker, timeframe, visibleColumns, visibleSupervisionGroups]);

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
  const labelOptions = chartLabelOptions(catalog, payload?.options?.supervision_groups ?? []);
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
          labelOptions={labelOptions}
          loading={chartLoading}
          onPeriodChange={updateChartPeriod}
          onTickerChange={setTicker}
          onTimeframeChange={setTimeframe}
          onVisibleColumnsChange={setVisibleColumns}
          onVisibleSupervisionGroupsChange={setVisibleSupervisionGroups}
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
          visibleSupervisionGroups={visibleSupervisionGroups}
        />
      ) : (
        <div className="empty-state">This row does not include a ticker, so it cannot be opened on the chart.</div>
      )}
    </Modal>
  );
}

function previewChartInitialState(target: PreviewChartTarget, records: RecordRow[], catalog: CatalogPayload | null) {
  const row = target.row;
  const timeframe = rowStringValue(row, "timeframe") || target.record.timeframe || "1m";
  const ticker = rowStringValue(row, "ticker").toUpperCase();
  const sessionDate = rowStringValue(row, "session_date") || target.record.session_date;
  const range = surroundingChartRange(records, timeframe, sessionDate);
  const visibleColumns = previewChartDisplayItems(target.record, catalog);
  const visibleSupervisionGroups = previewSupervisionGroups(target.record);
  return {
    featureGroups: previewFeatureGroups(target.record, catalog, visibleColumns),
    range,
    reference: previewChartReference(row, target.record),
    ticker,
    timeframe,
    visibleColumns,
    visibleSupervisionGroups,
  };
}

function rowHasChartContext(row: Record<string, unknown>, record: RecordRow) {
  return Boolean(rowStringValue(row, "ticker") && (rowStringValue(row, "session_date") || record.session_date));
}

function rowStringValue(row: Record<string, unknown>, column: string) {
  const value = row[column];
  return value === null || value === undefined ? "" : String(value);
}

function rowNumberValue(row: Record<string, unknown>, column: string) {
  const value = Number(row[column]);
  return Number.isFinite(value) ? value : undefined;
}

function previewChartReference(row: Record<string, unknown>, record: RecordRow): ChartReference {
  const sessionDate = rowStringValue(row, "session_date") || record.session_date;
  const minuteOfDay = rowNumberValue(row, "minute_of_day");
  const timestamp = rowUtcTimestamp(row);
  const marketTime = rowStringValue(row, "bar_time_market") || rowStringValue(row, "bar_time_utc") || sessionDate;
  return {
    label: `${rowStringValue(row, "ticker").toUpperCase() || "Row"} ${formatReferenceTimeLabel(marketTime, minuteOfDay)}`,
    minuteOfDay,
    sessionDate,
    time: timestamp,
  };
}

function rowUtcTimestamp(row: Record<string, unknown>) {
  const utcValue = rowStringValue(row, "bar_time_utc");
  if (!utcValue) return undefined;
  const normalized = /z$|[+-]\d\d:?\d\d$/i.test(utcValue) ? utcValue : `${utcValue}Z`;
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

function previewSupervisionGroups(record: RecordRow) {
  if (!record.group.startsWith("supervision_")) return [];
  return [record.group.replace("supervision_", "")];
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

  useEffect(() => {
    if (selected?.id && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected?.id, selectedId]);

  useEffect(() => {
    setDraft({ ...(selected?.presentation ?? {}) });
    setStyleTarget("group");
    setSaveState("idle");
  }, [selected?.id]);

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
        parts[partIndex] = { ...parts[partIndex], chartRole: value, pane: defaultPaneForDisplayType(value, String(parts[partIndex].pane ?? current.pane ?? "price")) };
        return { ...current, parts };
      });
    } else {
      setDraft((current) => ({ ...current, chartRole: value, pane: defaultPaneForDisplayType(value, String(current.pane ?? "price")) }));
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
                    <span>{isTableOnlyPresentation ? "No chart color used" : `${displayName(presentationPane)} pane`}</span>
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
                          <CatalogSelect help={PRESENTATION_HELP.pane} label="Pane" options={catalog?.presentationOptions.panes ?? []} value={String(activePresentation.pane ?? draft.pane ?? "oscillator")} onChange={(value) => updateActivePresentation("pane", value)} />
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
                        <h4>Marker Shape</h4>
                        <div className="catalog-form-grid compact">
                          <CatalogSelect help={PRESENTATION_HELP.markerShape} label="Shape" options={catalog?.presentationOptions.markerShapes ?? []} value={String(activePresentation.markerShape ?? "circle")} onChange={(value) => updateActivePresentation("markerShape", value)} />
                          <CatalogSelect help={PRESENTATION_HELP.markerPosition} label="Position" options={catalog?.presentationOptions.markerPositions ?? []} value={String(activePresentation.markerPosition ?? "belowBar")} onChange={(value) => updateActivePresentation("markerPosition", value)} />
                        </div>
                      </div>
                    ) : null}
                    {activeRole === "anchored_zone" ? (
                      <div className="catalog-presentation-section">
                        <h4>Zone Behavior</h4>
                        <div className="catalog-form-grid compact">
                          <CatalogSelect help="Controls how the event-created zone extends after the source bar." label="Extend rule" options={catalog?.presentationOptions.extendRules ?? []} value={String(activePresentation.extendRule ?? "fixed_bars")} onChange={(value) => updateActivePresentation("extendRule", value)} />
                          <CatalogNumberField help="Maximum number of bars the anchored zone may extend." label="Max bars" max={240} min={1} value={Number(activePresentation.maxBars ?? activePresentation.extendBars ?? 24)} onChange={(value) => updateActivePresentation("maxBars", value)} />
                          <CatalogSelect help="Boundary stroke used around the zone." label="Border style" options={catalog?.presentationOptions.borderStyles ?? []} value={String(activePresentation.borderStyle ?? "solid")} onChange={(value) => updateActivePresentation("borderStyle", value)} />
                          <CatalogNumberField help="Zone boundary width in pixels." label="Border width" max={6} min={1} value={Number(activePresentation.borderWidth ?? 1)} onChange={(value) => updateActivePresentation("borderWidth", value)} />
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

function defaultPaneForDisplayType(displayType: string, currentPane: string): string {
  if (["price_overlay", "marker", "text_label", "continuous_band", "anchored_zone", "background_state", "band", "price_zone"].includes(displayType)) return "price";
  if (displayType === "oscillator" || displayType === "histogram") return currentPane === "price" ? "oscillator" : currentPane;
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
}: {
  itemTitle: string;
  presentation: CatalogPresentation;
  presentationType: string;
}) {
  const role = normalizeDisplayType(String(presentation.chartRole ?? "data_only"));
  const pane = String(presentation.pane ?? "price");
  const lineStyle = String(presentation.lineStyle ?? "solid");
  const lineWidth = boundedPresentationNumber(presentation.lineWidth, 1, 6, 2);
  const strokeColor = presentationColor(presentation.color);
  const strokeOpacity = boundedPresentationNumber(presentation.opacity, 0.05, 1, 1);
  const selectedStroke = typeof presentation.color === "string" && presentation.color.startsWith("#") ? colorWithOpacity(presentation.color, strokeOpacity) : strokeColor;
  const bandFill = colorWithOpacity(presentation.bandFillColor ?? presentation.color, boundedPresentationNumber(presentation.bandFillOpacity, 0, 0.6, 0.16));
  const dashArray = svgDashArray(lineStyle, lineWidth);
  const displayNameForItem = itemTitle || "Selected item";
  const isBandLike = role === "band" || role === "price_zone" || role === "continuous_band" || role === "anchored_zone";
  const parts = role === "composite" && Array.isArray(presentation.parts) ? presentation.parts.filter((part): part is Record<string, unknown> => Boolean(part && typeof part === "object")) : [];
  return (
    <aside className="catalog-preview-chart-card" aria-label={`Chart preview for ${displayNameForItem}`}>
      <div className="catalog-preview-chart-header">
        <div>
          <span>Preview</span>
          <strong>{displayNameForItem}</strong>
        </div>
        <small>{presentationTypeLabel(presentationType)}</small>
      </div>
      <svg className="catalog-contract-chart" viewBox="0 0 380 238" role="img" aria-label={`${displayNameForItem} presentation preview`}>
        <rect className="catalog-contract-chart-bg" x="0" y="0" width="380" height="238" rx="8" />
        <g className="catalog-contract-chart-grid">
          {[44, 74, 104, 134].map((y) => <line key={`py:${y}`} x1="20" x2="356" y1={y} y2={y} />)}
          {[58, 116, 174, 232, 290, 348].map((x) => <line key={`px:${x}`} x1={x} x2={x} y1="26" y2="151" />)}
          <line x1="20" x2="356" y1="170" y2="170" />
          <line x1="20" x2="356" y1="205" y2="205" />
          {[58, 116, 174, 232, 290, 348].map((x) => <line key={`ox:${x}`} x1={x} x2={x} y1="164" y2="218" />)}
        </g>
        {isBandLike ? (
          <g className="catalog-contract-selected-layer">
            <polygon fill={bandFill} points="34,88 66,75 98,79 130,92 162,86 194,72 226,76 258,68 290,63 322,66 350,60 350,100 322,106 290,102 258,109 226,112 194,106 162,114 130,121 98,112 66,106 34,119" />
            <polyline fill="none" points="34,88 66,75 98,79 130,92 162,86 194,72 226,76 258,68 290,63 322,66 350,60" stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
            <polyline fill="none" points="34,119 66,106 98,112 130,121 162,114 194,106 226,112 258,109 290,102 322,106 350,100" stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
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
        {role === "composite" && pane === "price" ? (
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
        {role === "marker" ? renderCatalogPreviewMarker(String(presentation.markerShape ?? "circle"), String(presentation.markerPosition ?? "belowBar"), selectedStroke) : null}
        {role === "oscillator" ? (
          <polyline className="catalog-contract-selected-line" fill="none" points={CATALOG_OSCILLATOR_LINE_POINTS} stroke={selectedStroke} strokeDasharray={dashArray} strokeWidth={lineWidth} />
        ) : null}
        {role === "composite" && pane !== "price" ? (
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
                        y={bar.value >= 0 ? 194 - bar.value : 194}
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
                  y={positive ? 194 - bar.value : 194}
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
          <text x="182" y="24">{pane === "price" ? "Price" : displayName(pane)}</text>
          <text x="326" y="24">11:30</text>
        </g>
      </svg>
      <p>{isDataOnlyRole(role) ? "This field stays available in tables and catalog, but is not drawn on the chart." : "Dummy candles are fixed; only this selected catalog item is drawn."}</p>
    </aside>
  );
}

function renderCatalogPreviewMarker(shape: string, position: string, color: string): ReactNode {
  const x = 222;
  const y = position === "aboveBar" ? 66 : position === "inBar" ? 104 : 138;
  if (shape === "arrowDown") return <polygon className="catalog-contract-marker" fill={color} points={`${x - 7},${y - 6} ${x + 7},${y - 6} ${x},${y + 8}`} />;
  if (shape === "arrowUp") return <polygon className="catalog-contract-marker" fill={color} points={`${x},${y - 8} ${x - 7},${y + 6} ${x + 7},${y + 6}`} />;
  if (shape === "square") return <rect className="catalog-contract-marker" fill={color} height="14" width="14" x={x - 7} y={y - 7} />;
  return <circle className="catalog-contract-marker" cx={x} cy={y} fill={color} r="7" />;
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
  const isMarker = chartRole === "marker";
  const showLineControls = isLineLike && (styleFields.has("lineStyle") || styleFields.has("lineWidth"));
  const showLineWidth = styleFields.has("lineWidth");
  const showBandControls = styleFields.has("bandFillColor") || styleFields.has("bandFillOpacity");
  const showOpacity = styleFields.has("opacity");
  const showPrecision = styleFields.has("precision");
  const resolvedBandFillOpacity = Math.max(0, Math.min(0.6, Number.isFinite(bandFillOpacity) ? bandFillOpacity : 0.16));
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
                max={0.6}
                min={0}
                step={0.01}
                type="range"
                value={String(resolvedBandFillOpacity)}
                onChange={(event) => onChange("bandFillOpacity", boundedNumber(event.target.value, 0, 0.6))}
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

function defaultCatalogSupervisionGroups(catalog: CatalogPayload | null): string[] {
  if (!catalog) return [];
  const groups = catalog.columns
    .filter((item) => item.presentation?.defaultVisible && item.presentation?.chartRole === "marker")
    .map(supervisionGroupForCatalogItem)
    .filter(Boolean) as string[];
  if (catalog.supervisionMethods.some((item) => item.presentation?.defaultVisible)) groups.push("method");
  if (catalog.scanners.some((item) => item.presentation?.defaultVisible)) groups.push("scanner");
  return Array.from(new Set(groups));
}

function chartLabelOptions(catalog: CatalogPayload | null, availableGroups: string[]): ChartLabelOption[] {
  if (!catalog) return [];
  const available = new Set(availableGroups);
  const candidates = [
    { column: "oracle_long_entry_signal", group: "bar", title: "Bar labels" },
    { column: "method_entry_signal", group: "method", title: "Method labels" },
    { column: "is_top_3", group: "scanner", title: "Scanner labels" },
  ];
  return candidates
    .filter((candidate) => available.has(candidate.group))
    .map((candidate) => {
      const item = catalog.columns.find((column) => column.column === candidate.column);
      return { group: candidate.group, id: item?.id ?? candidate.group, title: item?.title ?? candidate.title };
    });
}

function supervisionGroupForCatalogItem(item: CatalogItem): string | null {
  const groups = item.artifactGroups ?? [];
  if (groups.includes("supervision_bar")) return "bar";
  if (groups.includes("supervision_method")) return "method";
  if (groups.includes("supervision_scanner")) return "scanner";
  return null;
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

function timeframeSort(left: string, right: string) {
  const order = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"];
  return (order.indexOf(left) === -1 ? 999 : order.indexOf(left)) - (order.indexOf(right) === -1 ? 999 : order.indexOf(right)) || left.localeCompare(right);
}
