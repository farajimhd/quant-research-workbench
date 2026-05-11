import { useEffect, useMemo, useState, type MouseEvent as ReactMouseEvent, type ReactNode } from "react";
import { BookOpen, Database, Filter, Search, SlidersHorizontal, Tags } from "lucide-react";
import katex from "katex";
import "katex/dist/katex.min.css";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartLabelOption, type ChartPayload } from "../app/components/ChartPanel";
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
type CatalogPresentation = Record<string, string | number | boolean | undefined>;
type CatalogItem = ChartCatalogItem & {
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
type CatalogPayload = {
  catalogVersion: number;
  columns: CatalogItem[];
  presentationOptions: Record<string, string[]>;
  scanners: CatalogMethod[];
  supervisionMethods: CatalogMethod[];
};
type CatalogKindFilter = "all" | "columns" | "methods" | "scanners";
type CatalogCardItem = CatalogItem & {
  catalogKind: "columns" | "methods" | "scanners";
  groupLabel: string;
  sourceLabel: string;
  summary: string;
};

const tabs = ["Overview", "Preview", "Chart", "Coverage", "Artifacts", "Schema", "Catalog"];
const DEFAULT_CHART_FEATURE_GROUPS = ["core", "momentum"];
const DEFAULT_CHART_COLUMNS = ["vwap", "tema9", "tema20", "macd_line", "macd_signal", "macd_hist"];
const DEFAULT_CHART_MIN_CONFIDENCE = 0.7;
const PREVIEW_PAGE_SIZE = 1000;

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
      {activeTab === "Preview" && scope && review ? <Preview scope={scope} records={review.records} /> : null}
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
  const [visibleColumns, setVisibleColumns] = useState(DEFAULT_CHART_COLUMNS);
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
    const defaults = defaultCatalogChartColumns(catalog);
    if (!defaults.length || !sameList(visibleColumns, DEFAULT_CHART_COLUMNS)) return;
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
        columns: visibleColumns.join(","),
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

  const indicatorOptions = payload?.options?.standard_indicators ?? DEFAULT_CHART_COLUMNS;
  const featureOptions = payload?.options?.feature_columns ?? [];
  const labelOptions = chartLabelOptions(catalog, payload?.options?.supervision_groups ?? []);

  if (!barRecords.length) return <div className="empty-state panel">No saved bar artifacts are available for charting.</div>;
  return (
    <section>
      <ChartPanel
        catalogColumns={catalog?.columns ?? []}
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

function Preview({ scope, records }: { scope: Scope; records: RecordRow[] }) {
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
        rows={sample?.rows ?? []}
        columns={sample?.columns}
      />
    </section>
  );
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
  const [search, setSearch] = useState("");
  const [catalogWidth, setCatalogWidth] = useState(31);
  const [isResizing, setIsResizing] = useState(false);
  const [saveState, setSaveState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
  const allItems = useMemo(() => catalogItems(catalog), [catalog]);
  const categoryOptions = useMemo(() => catalogOptionValues(allItems.map((item) => item.category)), [allItems]);
  const groupOptions = useMemo(() => catalogOptionValues(allItems.map((item) => item.groupLabel)), [allItems]);
  const items = useMemo(
    () => filterCatalogItems(allItems, { category, group, kind, search }),
    [allItems, category, group, kind, search],
  );
  const groupedItems = useMemo(() => groupCatalogItems(items), [items]);
  const [selectedId, setSelectedId] = useState("");
  const selected = items.find((item) => item.id === selectedId) ?? items[0];
  const [draft, setDraft] = useState<CatalogPresentation>({});

  useEffect(() => {
    if (selected?.id && selected.id !== selectedId) setSelectedId(selected.id);
  }, [selected?.id, selectedId]);

  useEffect(() => {
    setDraft({ ...(selected?.presentation ?? {}) });
    setSaveState("idle");
  }, [selected?.id]);

  function updatePresentation(key: string, value: string | number | boolean) {
    setDraft((current) => ({ ...current, [key]: value }));
    setSaveState("idle");
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

  return (
    <section
      className={isResizing ? "catalog-workbench resizing" : "catalog-workbench"}
      onMouseLeave={stopResize}
      onMouseMove={resizeCatalog}
      onMouseUp={stopResize}
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
              <CatalogFilter label="Type" value={kind} onChange={(value) => setKind(value as CatalogKindFilter)} options={["all", "columns", "methods", "scanners"]} />
              <CatalogFilter label="Category" value={category} onChange={setCategory} options={categoryOptions} />
              <CatalogFilter label="Group" value={group} onChange={setGroup} options={groupOptions} />
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
            <div className="catalog-detail-metrics">
              <CatalogMetric icon={<Tags size={14} />} label="Category" value={selected.category} />
              <CatalogMetric icon={<Filter size={14} />} label="Group" value={selected.groupLabel} />
              <CatalogMetric icon={<SlidersHorizontal size={14} />} label="Chart role" value={String(draft.chartRole ?? selected.presentation?.chartRole ?? "table_only")} />
              <CatalogMetric icon={<BookOpen size={14} />} label="Value" value={String(draft.valueFormat ?? selected.presentation?.valueFormat ?? "-")} />
            </div>
            <div className="catalog-detail-grid">
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
              <section className="catalog-section-card presentation-editor">
                <h3>Presentation</h3>
                <div className="catalog-form-grid">
                  <CatalogCheckbox checked={Boolean(draft.selectable)} label="Selectable" onChange={(value) => updatePresentation("selectable", value)} />
                  <CatalogCheckbox checked={Boolean(draft.defaultVisible)} label="Default on" onChange={(value) => updatePresentation("defaultVisible", value)} />
                  <CatalogCheckbox checked={Boolean(draft.legend)} label="Legend" onChange={(value) => updatePresentation("legend", value)} />
                  <CatalogSelect label="Chart role" options={catalog?.presentationOptions.chartRoles ?? []} value={String(draft.chartRole ?? "table_only")} onChange={(value) => updatePresentation("chartRole", value)} />
                  <CatalogSelect label="Pane" options={catalog?.presentationOptions.panes ?? []} value={String(draft.pane ?? "price")} onChange={(value) => updatePresentation("pane", value)} />
                  <CatalogSelect label="Line style" options={catalog?.presentationOptions.lineStyles ?? []} value={String(draft.lineStyle ?? "solid")} onChange={(value) => updatePresentation("lineStyle", value)} />
                  <CatalogSelect label="Marker shape" options={catalog?.presentationOptions.markerShapes ?? []} value={String(draft.markerShape ?? "circle")} onChange={(value) => updatePresentation("markerShape", value)} />
                  <CatalogSelect label="Marker position" options={catalog?.presentationOptions.markerPositions ?? []} value={String(draft.markerPosition ?? "belowBar")} onChange={(value) => updatePresentation("markerPosition", value)} />
                  <CatalogSelect label="Value format" options={catalog?.presentationOptions.valueFormats ?? []} value={String(draft.valueFormat ?? "number")} onChange={(value) => updatePresentation("valueFormat", value)} />
                  <CatalogText label="Color" value={String(draft.color ?? "#1E3A5F")} onChange={(value) => updatePresentation("color", value)} />
                  <CatalogNumber label="Line width" max={6} min={1} value={Number(draft.lineWidth ?? 1)} onChange={(value) => updatePresentation("lineWidth", value)} />
                  <CatalogNumber label="Precision" max={8} min={0} value={Number(draft.precision ?? 2)} onChange={(value) => updatePresentation("precision", value)} />
                </div>
              </section>
            </div>
            <section className="catalog-section-card">
              <h3>Equations</h3>
              <div className="catalog-equation-grid">
                {selected.knowledge?.equations?.map((equation) => (
                  <CatalogEquation equation={equation} key={equation.title} />
                ))}
              </div>
            </section>
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

function CatalogFilter({ label, onChange, options, value }: { label: string; onChange: (value: string) => void; options: string[]; value: string }) {
  return (
    <label className="catalog-filter">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{option === "all" ? "All" : displayName(option)}</option>
        ))}
      </select>
    </label>
  );
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

function CatalogSelect({ label, onChange, options, value }: { label: string; onChange: (value: string) => void; options: string[]; value: string }) {
  return (
    <label className="catalog-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{option}</option>
        ))}
      </select>
    </label>
  );
}

function CatalogText({ label, onChange, value }: { label: string; onChange: (value: string) => void; value: string }) {
  return (
    <label className="catalog-field">
      <span>{label}</span>
      <input value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function CatalogNumber({ label, max, min, onChange, value }: { label: string; max: number; min: number; onChange: (value: number) => void; value: number }) {
  return (
    <label className="catalog-field">
      <span>{label}</span>
      <input max={max} min={min} type="number" value={String(value)} onChange={(event) => onChange(Number(event.target.value))} />
    </label>
  );
}

function CatalogCheckbox({ checked, label, onChange }: { checked: boolean; label: string; onChange: (value: boolean) => void }) {
  return (
    <label className="catalog-checkbox">
      <input checked={checked} type="checkbox" onChange={(event) => onChange(event.target.checked)} />
      <span>{label}</span>
    </label>
  );
}

function catalogItems(catalog: CatalogPayload | null): CatalogCardItem[] {
  if (!catalog) return [];
  return [
    ...catalog.columns.map((item) => catalogColumnToCard(item)),
    ...catalog.supervisionMethods.map((item) => catalogMethodToItem(item, "methods")),
    ...catalog.scanners.map((item) => catalogMethodToItem(item, "scanners")),
  ].sort((left, right) =>
    left.catalogKind.localeCompare(right.catalogKind) ||
    left.groupLabel.localeCompare(right.groupLabel) ||
    left.title.localeCompare(right.title),
  );
}

function catalogColumnToCard(item: CatalogItem): CatalogCardItem {
  const groupLabel = item.group ?? item.groups?.[0] ?? item.category;
  return {
    ...item,
    catalogKind: "columns",
    groupLabel,
    sourceLabel: "Column",
    summary: item.knowledge?.shortDescription ?? `${item.title} provider column.`,
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
    sourceLabel: catalogKind === "methods" ? "Method" : "Scanner",
    summary: item.knowledge?.shortDescription ?? item.thesis ?? `${item.title} catalog item.`,
  };
}

function catalogOptionValues(values: string[]): string[] {
  return ["all", ...Array.from(new Set(values.filter(Boolean))).sort((left, right) => displayName(left).localeCompare(displayName(right)))];
}

function filterCatalogItems(
  items: CatalogCardItem[],
  filters: { category: string; group: string; kind: CatalogKindFilter; search: string },
): CatalogCardItem[] {
  const queryText = filters.search.trim().toLowerCase();
  return items.filter((item) =>
    (filters.kind === "all" || item.catalogKind === filters.kind) &&
    (filters.category === "all" || item.category === filters.category) &&
    (filters.group === "all" || item.groupLabel === filters.group) &&
    (!queryText ||
      [item.title, item.id, item.category, item.groupLabel, item.column, item.summary]
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

function defaultCatalogChartColumns(catalog: CatalogPayload | null): string[] {
  if (!catalog) return [];
  return catalog.columns
    .filter((item) => {
      const role = String(item.presentation?.chartRole ?? "");
      return Boolean(item.column && item.presentation?.defaultVisible && item.presentation?.selectable && !["marker", "table_only"].includes(role));
    })
    .map((item) => String(item.column));
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
