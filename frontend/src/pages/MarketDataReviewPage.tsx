import { useEffect, useMemo, useState } from "react";

import { api, query } from "../api/client";
import { ChartPanel, type ChartPayload } from "../app/components/ChartPanel";
import { DataTable } from "../app/components/DataTable";
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

const tabs = ["Overview", "Coverage", "Chart", "Artifacts", "Preview", "Schema"];
const DEFAULT_CHART_FEATURE_GROUPS = ["core", "momentum"];
const DEFAULT_CHART_COLUMNS = ["vwap", "tema9", "tema20", "macd_line", "macd_signal", "macd_hist"];
const DEFAULT_CHART_SUPERVISION_GROUPS = ["method"];
const DEFAULT_CHART_MIN_CONFIDENCE = 0.7;
const DEFAULT_CHART_MARKER_LIMIT = 100;

export function MarketDataReviewPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [draft, setDraft] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
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
  }, [scope]);

  function applyScope() {
    if (!draft) return;
    setScope(draft);
    setEditingScope(false);
  }

  return (
    <>
      <PageIntro
        groupLabel="Market Data"
        title="Review Data"
        description="Inspect saved provider artifacts, coverage, schemas, sampled rows, and chart-ready feature/supervision overlays."
        actions={scope ? <ReviewScopeCard scope={scope} manifest={review?.manifest} /> : null}
      />
      <button className="button" onClick={() => setEditingScope(true)} type="button">Edit scope</button>
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
      {activeTab === "Chart" && scope && review ? <ChartTab scope={scope} records={review.records} /> : null}
      {activeTab === "Artifacts" && review ? <Artifacts records={review.records} /> : null}
      {activeTab === "Preview" && scope && review ? <Preview scope={scope} records={review.records} /> : null}
      {activeTab === "Schema" && scope && review ? <Schema scope={scope} records={review.records} /> : null}
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

function ReviewScopeCard({ scope, manifest }: { scope: Scope; manifest?: Record<string, unknown> }) {
  return (
    <div className="scope-card">
      <div className="scope-card-header">
        <div className="scope-title">Data Scope</div>
        <span className="meta-tag">Updated {String(manifest?.updated_at ?? "-")}</span>
      </div>
      <div className="scope-card-grid">
        <div>
          <ScopeItem label="Start" value={scope.start_date} />
          <ScopeItem label="End" value={scope.end_date} />
        </div>
        <div>
          <ScopeItem label="Processed root" value={scope.processed_root} />
          <ScopeItem label="Artifacts" value={String(manifest?.artifact_count ?? "-")} />
        </div>
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

function ChartTab({ scope, records }: { scope: Scope; records: RecordRow[] }) {
  const barRecords = records.filter((record) => record.group === "bars" && record.exists);
  const sessions = Array.from(new Set(barRecords.map((record) => record.session_date))).sort().reverse();
  const [session, setSession] = useState(sessions[0] ?? "");
  const timeframes = useMemo(
    () => Array.from(new Set(barRecords.filter((record) => record.session_date === session).map((record) => record.timeframe))).sort(timeframeSort),
    [barRecords, session]
  );
  const [timeframe, setTimeframe] = useState(timeframes[0] ?? "1m");
  const [ticker, setTicker] = useState("");
  const [featureGroups, setFeatureGroups] = useState(DEFAULT_CHART_FEATURE_GROUPS);
  const [visibleColumns, setVisibleColumns] = useState(DEFAULT_CHART_COLUMNS);
  const [payload, setPayload] = useState<ChartPayload | null>(null);

  useEffect(() => {
    api<ConfigDefaults>("/api/config/defaults").then((defaults) => {
      if (defaults.feature_groups?.length) setFeatureGroups(defaults.feature_groups);
    });
  }, []);

  useEffect(() => {
    if (!session || !timeframes.length) return;
    if (!timeframes.includes(timeframe)) setTimeframe(timeframes[0]);
  }, [session, timeframes, timeframe]);

  useEffect(() => {
    if (!session || !timeframe || ticker.trim()) return;
    api<{ ticker: string }>(`/api/market-data/chart/default-ticker${query({ processed_root: scope.processed_root, session_date: session, timeframe })}`).then((result) =>
      setTicker(result.ticker || "AAPL")
    );
  }, [scope.processed_root, session, timeframe, ticker]);

  useEffect(() => {
    if (!session || !timeframe || !ticker.trim()) return;
    api<ChartPayload>(
      `/api/market-data/chart${query({
        processed_root: scope.processed_root,
        session_date: session,
        timeframe,
        ticker: ticker.trim().toUpperCase(),
        feature_groups: featureGroups.join(","),
        columns: visibleColumns.join(","),
        supervision_groups: DEFAULT_CHART_SUPERVISION_GROUPS.join(","),
        min_confidence: DEFAULT_CHART_MIN_CONFIDENCE,
        marker_limit: DEFAULT_CHART_MARKER_LIMIT
      })}`
    ).then((nextPayload) => {
      setPayload(nextPayload);
      const nextFeatureGroups = nextPayload.options?.feature_groups ?? [];
      if (nextFeatureGroups.length && !sameList(nextFeatureGroups, featureGroups)) {
        setFeatureGroups(nextFeatureGroups);
      }
    });
  }, [scope.processed_root, session, timeframe, ticker, featureGroups, visibleColumns]);

  const indicatorOptions = payload?.options?.standard_indicators ?? DEFAULT_CHART_COLUMNS;
  const featureOptions = payload?.options?.feature_columns ?? [];

  if (!barRecords.length) return <div className="empty-state panel">No saved bar artifacts are available for charting.</div>;
  return (
    <section>
      <div className="chart-session-row">
        <div className="field" style={{ width: 230 }}>
          <label>Session</label>
          <select value={session} onChange={(event) => setSession(event.target.value)}>
            {sessions.map((item) => (
              <option value={item} key={item}>{item}</option>
            ))}
          </select>
        </div>
      </div>
      <ChartPanel
        featureOptions={featureOptions}
        indicatorOptions={indicatorOptions}
        onTickerChange={setTicker}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={setVisibleColumns}
        payload={payload}
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

function Preview({ scope, records }: { scope: Scope; records: RecordRow[] }) {
  const [recordKey, setRecordKey] = useState(records[0]?.key ?? "");
  const record = records.find((item) => item.key === recordKey) ?? records[0];
  const [rowLimit, setRowLimit] = useState(250);
  const [tickers, setTickers] = useState("");
  const [sample, setSample] = useState<{ columns: string[]; rows: Record<string, unknown>[] } | null>(null);
  const fillPanel = useViewportFillPanel(`${recordKey}:${rowLimit}:${tickers}:${sample?.rows.length ?? 0}`);
  useEffect(() => {
    if (!record) return;
    api<{ sample: { columns: string[]; rows: Record<string, unknown>[] } }>(
      `/api/market-data/preview${query({
        processed_root: scope.processed_root,
        group: record.group,
        timeframe: record.timeframe,
        session_date: record.session_date,
        row_limit: rowLimit,
        tickers
      })}`
    ).then((payload) => setSample(payload.sample));
  }, [scope.processed_root, record?.key, rowLimit, tickers]);
  if (!record) return <div className="empty-state">No records available.</div>;
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
        <InlineField label="Rows" type="number" value={String(rowLimit)} onChange={(value) => setRowLimit(Number(value))} />
        <InlineField label="Tickers" value={tickers} onChange={setTickers} />
      </div>
      <DataTable rows={sample?.rows ?? []} columns={sample?.columns} />
    </section>
  );
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
        <div className="empty-state">{schemaLoading ? "Loading schema..." : "No schema fields found for the selected artifact."}</div>
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

function InlineField({ label, value, onChange, type = "text" }: { label: string; value: string; onChange: (value: string) => void; type?: string }) {
  return (
    <div className="field" style={{ width: 150 }}>
      <label>{label}</label>
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
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

function ScopeItem({ label, value }: { label: string; value: string }) {
  return (
    <div className="scope-item">
      <span>{label}</span>
      <b title={value}>{value}</b>
    </div>
  );
}

function sameList(left: string[], right: string[]) {
  return left.length === right.length && left.every((value, index) => value === right[index]);
}

function timeframeSort(left: string, right: string) {
  const order = ["1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d"];
  return (order.indexOf(left) === -1 ? 999 : order.indexOf(left)) - (order.indexOf(right) === -1 ? 999 : order.indexOf(right)) || left.localeCompare(right);
}
