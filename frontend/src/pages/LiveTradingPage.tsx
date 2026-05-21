import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Eye, PauseCircle, Play, RotateCcw, ShieldAlert, SkipForward, Target, TrendingUp } from "lucide-react";
import type { Time } from "lightweight-charts";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartDisplayItem, type ChartLabelOption, type ChartPayload } from "../app/components/ChartPanel";
import { DataTable, type BackendTableQuery } from "../app/components/DataTable";
import { MetricStrip } from "../app/components/MetricStrip";
import { PageIntro } from "../app/components/PageIntro";

type Scope = {
  processed_root: string;
  raw_root: string;
  spread_root: string;
  start_date: string;
  end_date: string;
};

type RecordRow = {
  columns: string[];
  exists: boolean;
  group: string;
  key: string;
  path: string;
  session_date: string;
  timeframe: string;
};

type ReviewPayload = {
  records: RecordRow[];
};

type CatalogPayload = {
  columns: ChartCatalogItem[];
  displayItems?: ChartDisplayItem[];
};

type ScannerSnapshot = {
  bar_time: string;
  columns: string[];
  feature_groups: string[];
  has_more?: boolean;
  reason?: string;
  row_count: number;
  rows: Record<string, unknown>[];
  session_date: string;
  timeframe: string;
};

type ScannerSnapshotPayload = {
  snapshot: ScannerSnapshot;
};

type CockpitSettings = {
  barTime: string;
  maxPrice: number;
  minLast5mReturn: number;
  minPrice: number;
  minTransactions: number;
  minTransactionsRatio: number;
  minVolume: number;
  rowLimit: number;
  sessionDate: string;
};

type DecisionState = "approved" | "skipped" | "watching";

const LIVE_FEATURE_GROUPS = ["core", "session", "momentum", "volume_liquidity", "price_action", "shock", "market_structure"];
const LIVE_DISPLAY_ITEMS = ["vwap", "tema9", "tema20", "macd"];
const LIVE_SCANNER_COLUMNS = [
  "ticker",
  "bar_time_market",
  "minute_of_day",
  "current_open",
  "last_close",
  "last_open",
  "last_high",
  "last_low",
  "last_vwap",
  "last_day_high_so_far",
  "last_day_low_so_far",
  "last_5m_return",
  "last_volume",
  "last_transactions",
  "last_transactions_vs_prior_3",
  "last_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence_score",
  "current_open_above_last_2_body_high",
  "spread_bps_abs",
];

export function LiveTradingPage() {
  const [scope, setScope] = useState<Scope | null>(null);
  const [review, setReview] = useState<ReviewPayload | null>(null);
  const [catalog, setCatalog] = useState<CatalogPayload | null>(null);
  const [settings, setSettings] = useState<CockpitSettings>({
    barTime: "04:00",
    maxPrice: 10,
    minLast5mReturn: 0.05,
    minPrice: 1,
    minTransactions: 150,
    minTransactionsRatio: 3,
    minVolume: 8_000,
    rowLimit: 300,
    sessionDate: "",
  });
  const [snapshot, setSnapshot] = useState<ScannerSnapshot | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [selectedRow, setSelectedRow] = useState<Record<string, unknown> | null>(null);
  const [chartPayload, setChartPayload] = useState<ChartPayload | null>(null);
  const [chartLoading, setChartLoading] = useState(false);
  const [chartError, setChartError] = useState("");
  const [visibleColumns, setVisibleColumns] = useState<string[]>(LIVE_DISPLAY_ITEMS);
  const [visibleSupervisionGroups, setVisibleSupervisionGroups] = useState<string[]>([]);
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({});

  useEffect(() => {
    let active = true;
    api<Scope>("/api/market-data/scope").then((payload) => {
      if (!active) return;
      setScope(payload);
      setSettings((current) => ({ ...current, sessionDate: payload.end_date || payload.start_date }));
    });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!scope) return;
    let active = true;
    api<ReviewPayload>(`/api/market-data/review${query({ processed_root: scope.processed_root, start_date: scope.start_date, end_date: scope.end_date })}`).then((payload) => {
      if (!active) return;
      setReview(payload);
      const latestSession = availableSessionDates(payload.records).at(-1);
      if (latestSession) setSettings((current) => ({ ...current, sessionDate: latestSession }));
    });
    api<CatalogPayload>(`/api/market-data/catalog${query({ processed_root: scope.processed_root })}`).then((payload) => {
      if (active) setCatalog(payload);
    });
    return () => {
      active = false;
    };
  }, [scope]);

  const sessions = useMemo(() => availableSessionDates(review?.records ?? []), [review]);
  const enrichedRows = useMemo(() => (snapshot?.rows ?? []).map((row) => enrichLiveCandidate(row, settings)), [settings, snapshot]);
  const selectedTicker = stringValue(selectedRow, "ticker");
  const selectedOpen = numberValue(selectedRow, "current_open") || numberValue(selectedRow, "open");
  const selectedTime = selectedRow ? rowTimestampSeconds(selectedRow, settings.sessionDate, settings.barTime) : null;
  const openOnlyPayload = useMemo(
    () => openOnlyChartPayload(chartPayload, selectedTime, selectedOpen, settings.barTime),
    [chartPayload, selectedOpen, selectedTime, settings.barTime]
  );
  const selectedProfile = selectedRow ? enrichLiveCandidate(selectedRow, settings) : null;
  const chartOptions = chartPayload?.options;

  useEffect(() => {
    if (!selectedRow && enrichedRows.length) setSelectedRow(enrichedRows[0]);
  }, [enrichedRows, selectedRow]);

  useEffect(() => {
    if (!scope || !selectedTicker || !settings.sessionDate) {
      setChartPayload(null);
      return;
    }
    let active = true;
    setChartLoading(true);
    setChartError("");
    api<ChartPayload>(
      `/api/market-data/chart${chartRequestQuery({
        processed_root: scope.processed_root,
        start_date: settings.sessionDate,
        end_date: settings.sessionDate,
        timeframe: "1m",
        ticker: selectedTicker,
        feature_groups: LIVE_FEATURE_GROUPS.join(","),
        display_items: visibleColumns.join(","),
        supervision_groups: visibleSupervisionGroups.join(","),
      })}`
    )
      .then((payload) => {
        if (active) setChartPayload(payload);
      })
      .catch((requestError: Error) => {
        if (!active) return;
        setChartPayload(null);
        setChartError(requestError.message || "Chart request failed.");
      })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [scope, selectedTicker, settings.sessionDate, visibleColumns, visibleSupervisionGroups]);

  function loadSnapshot() {
    if (!scope || !settings.sessionDate) return;
    setLoading(true);
    setError("");
    const tableQuery = liveScannerQuery(settings);
    api<ScannerSnapshotPayload>(
      `/api/market-data/scanner-snapshot${query({
        processed_root: scope.processed_root,
        session_date: settings.sessionDate,
        timeframe: "1m",
        bar_time: settings.barTime,
        feature_groups: LIVE_FEATURE_GROUPS.join(","),
        columns: LIVE_SCANNER_COLUMNS.join(","),
        table_query: JSON.stringify(tableQuery),
        row_limit: settings.rowLimit,
      })}`
    )
      .then((payload) => {
        setSnapshot(payload.snapshot);
        const firstRow = payload.snapshot.rows[0] ?? null;
        setSelectedRow(firstRow ? enrichLiveCandidate(firstRow, settings) : null);
      })
      .catch((requestError: Error) => {
        setSnapshot(null);
        setSelectedRow(null);
        setError(requestError.message || "Scanner request failed.");
      })
      .finally(() => setLoading(false));
  }

  function moveMinute(delta: number) {
    setSettings((current) => ({ ...current, barTime: addMinutesToClock(current.barTime, delta) }));
  }

  function updateDecision(state: DecisionState) {
    if (!selectedTicker) return;
    setDecisions((current) => ({ ...current, [selectedTicker]: state }));
  }

  const decisionCounts = Object.values(decisions).reduce(
    (counts, decision) => ({ ...counts, [decision]: (counts[decision] ?? 0) + 1 }),
    {} as Record<DecisionState, number>
  );
  const readyCount = enrichedRows.filter((row) => stringValue(row, "live_bias") === "Ready").length;
  const watchCount = enrichedRows.filter((row) => stringValue(row, "live_bias") === "Watch").length;
  const riskCount = enrichedRows.filter((row) => stringValue(row, "live_bias") === "Risk").length;

  return (
    <>
      <PageIntro
        groupLabel="Live Trading"
        title="Semi-Auto Cockpit"
        description="Open-by-open momentum review for small-cap candidates."
        actions={
          <div className="live-scope-card">
            <span>{scope?.processed_root ?? "Loading scope..."}</span>
            <strong>{settings.sessionDate || "-"}</strong>
          </div>
        }
      />
      <MetricStrip
        items={[
          { label: "Rows", value: snapshot?.rows.length ?? 0, kind: "number" },
          { label: "Ready", value: readyCount, kind: "number" },
          { label: "Watch", value: watchCount, kind: "number" },
          { label: "Risk", value: riskCount, kind: "number" },
          { label: "Approved", value: decisionCounts.approved ?? 0, kind: "number" },
          { label: "Skipped", value: decisionCounts.skipped ?? 0, kind: "number" },
          { label: "Mode", value: "open-only", kind: "status" },
        ]}
      />

      <section className="live-control-panel panel">
        <div className="live-control-grid">
          <LiveSelect label="Session" value={settings.sessionDate} values={sessions} onChange={(value) => setSettings({ ...settings, sessionDate: value })} />
          <LiveField label="Bar open" type="time" value={settings.barTime} onChange={(value) => setSettings({ ...settings, barTime: value })} />
          <LiveField label="Min price" type="number" value={String(settings.minPrice)} onChange={(value) => setSettings({ ...settings, minPrice: Number(value) })} />
          <LiveField label="Max price" type="number" value={String(settings.maxPrice)} onChange={(value) => setSettings({ ...settings, maxPrice: Number(value) })} />
          <LiveField label="5m return" step="0.01" type="number" value={String(settings.minLast5mReturn)} onChange={(value) => setSettings({ ...settings, minLast5mReturn: Number(value) })} />
          <LiveField label="Volume" type="number" value={String(settings.minVolume)} onChange={(value) => setSettings({ ...settings, minVolume: Number(value) })} />
          <LiveField label="Transactions" type="number" value={String(settings.minTransactions)} onChange={(value) => setSettings({ ...settings, minTransactions: Number(value) })} />
          <LiveField label="Tx ratio" step="0.1" type="number" value={String(settings.minTransactionsRatio)} onChange={(value) => setSettings({ ...settings, minTransactionsRatio: Number(value) })} />
        </div>
        <div className="live-control-actions">
          <button className="button secondary" onClick={() => moveMinute(-1)} type="button">
            <SkipForward className="flip-x" size={15} /> Prev
          </button>
          <button className="button primary" disabled={loading || !scope || !settings.sessionDate} onClick={loadSnapshot} type="button">
            {loading ? <span className="loading-spinner" aria-hidden="true" /> : <Play size={15} />} Load Open
          </button>
          <button className="button secondary" onClick={() => moveMinute(1)} type="button">
            <SkipForward size={15} /> Next
          </button>
          <button className="button secondary" onClick={() => setDecisions({})} type="button">
            <RotateCcw size={15} /> Reset Marks
          </button>
        </div>
      </section>

      {error ? <div className="preview-sample-status error">{error}</div> : null}
      {snapshot?.reason ? <div className="preview-sample-status error">{snapshot.reason}</div> : null}

      <section className="live-cockpit-grid">
        <div className="live-left-stack">
          <section className="live-decision-card panel">
            <div className="live-card-header">
              <div>
                <span>Selected</span>
                <strong>{selectedTicker || "-"}</strong>
              </div>
              <span className={`live-bias-pill ${String(selectedProfile?.live_bias ?? "empty").toLowerCase()}`}>{String(selectedProfile?.live_bias ?? "No row")}</span>
            </div>
            <div className="live-decision-actions">
              <button className="button primary" disabled={!selectedTicker} onClick={() => updateDecision("approved")} type="button">
                <Target size={15} /> Approve
              </button>
              <button className="button secondary" disabled={!selectedTicker} onClick={() => updateDecision("watching")} type="button">
                <Eye size={15} /> Watch
              </button>
              <button className="button secondary" disabled={!selectedTicker} onClick={() => updateDecision("skipped")} type="button">
                <PauseCircle size={15} /> Skip
              </button>
            </div>
            <div className="live-ticket-grid">
              <TicketMetric label="Open" value={selectedOpen ? money(selectedOpen) : "-"} />
              <TicketMetric label="VWAP" value={money(numberValue(selectedRow, "last_vwap"))} />
              <TicketMetric label="Entry" value={money(numberValue(selectedProfile, "suggested_entry"))} />
              <TicketMetric label="Stop" value={money(numberValue(selectedProfile, "suggested_stop"))} tone="risk" />
            </div>
            <div className="live-reason-columns">
              <ReasonList icon={<TrendingUp size={15} />} items={splitList(selectedProfile?.live_reasons)} title="Reasons" />
              <ReasonList icon={<ShieldAlert size={15} />} items={splitList(selectedProfile?.live_risks)} title="Risks" />
            </div>
          </section>

          <section className="live-table-card panel">
            <div className="live-section-heading">
              <div>
                <span>Scanner</span>
                <strong>{settings.barTime} open snapshot</strong>
              </div>
              <small>{snapshot ? `${snapshot.rows.length.toLocaleString()} rows` : "No snapshot loaded"}</small>
            </div>
            <DataTable
              columns={liveTableColumns(snapshot?.columns ?? [])}
              empty={loading ? "Loading snapshot..." : "Load a bar-open snapshot to show candidates."}
              onRowClick={(row) => setSelectedRow(row)}
              preserveFiltersOnDataChange
              rows={enrichedRows}
              transposeHelper
            />
          </section>
        </div>

        <section className="live-chart-card panel">
          <div className="live-section-heading">
            <div>
              <span>Chart</span>
              <strong>{selectedTicker || "Select a row"}</strong>
            </div>
            <small>{settings.sessionDate} {settings.barTime}</small>
          </div>
          <ChartPanel
            catalogColumns={catalog?.columns ?? []}
            displayItemOptions={chartOptions?.display_items ?? catalog?.displayItems ?? []}
            emptyMessage="Select a scanner row to load the open-only chart."
            errorMessage={chartError}
            featureOptions={chartOptions?.feature_columns ?? []}
            indicatorOptions={chartOptions?.standard_indicators ?? LIVE_DISPLAY_ITEMS}
            labelOptions={chartOptions?.supervision_groups ?? []}
            loading={chartLoading}
            onPeriodChange={() => undefined}
            onTickerChange={() => undefined}
            onTimeframeChange={() => undefined}
            onVisibleColumnsChange={(nextColumns) => setVisibleColumns(nextColumns)}
            onVisibleSupervisionGroupsChange={setVisibleSupervisionGroups}
            payload={openOnlyPayload}
            periodEnd={settings.sessionDate}
            periodStart={settings.sessionDate}
            reference={selectedTime ? { label: "Current open", sessionDate: settings.sessionDate, time: selectedTime } : null}
            showReferenceLine
            ticker={selectedTicker}
            tickerInputWidth={130}
            timeframe="1m"
            timeframes={["1m"]}
            visibleColumns={visibleColumns}
            visibleSupervisionGroups={visibleSupervisionGroups}
          />
        </section>
      </section>
    </>
  );
}

function LiveField({
  label,
  onChange,
  step,
  type,
  value,
}: {
  label: string;
  onChange: (value: string) => void;
  step?: string;
  type: string;
  value: string;
}) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <input step={step} type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </label>
  );
}

function LiveSelect({ label, onChange, value, values }: { label: string; onChange: (value: string) => void; value: string; values: string[] }) {
  return (
    <label className="live-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {values.length ? values.map((item) => <option key={item} value={item}>{item}</option>) : <option value={value}>{value || "-"}</option>}
      </select>
    </label>
  );
}

function TicketMetric({ label, tone, value }: { label: string; tone?: "risk"; value: string }) {
  return (
    <div className={tone ? `live-ticket-metric ${tone}` : "live-ticket-metric"}>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ReasonList({ icon, items, title }: { icon: ReactNode; items: string[]; title: string }) {
  return (
    <div className="live-reason-list">
      <div className="live-reason-title">{icon}<span>{title}</span></div>
      {items.length ? items.map((item) => <span key={item}>{item}</span>) : <small>-</small>}
    </div>
  );
}

function availableSessionDates(records: RecordRow[]) {
  return Array.from(new Set(records.filter((record) => record.exists && record.group === "bars" && record.timeframe === "1m").map((record) => record.session_date))).sort();
}

function liveScannerQuery(settings: CockpitSettings): BackendTableQuery {
  return {
    conditions: [
      { column: "current_open", id: "price", operator: "between", value: String(settings.minPrice), valueSecondary: String(settings.maxPrice) },
      { column: "last_5m_return", id: "return", operator: "gte", value: String(settings.minLast5mReturn) },
      { column: "last_volume", id: "volume", operator: "gte", value: String(settings.minVolume) },
      { column: "last_transactions", id: "transactions", operator: "gt", value: String(settings.minTransactions) },
      { column: "last_transactions_vs_prior_3", id: "transaction_ratio", operator: "gte", value: String(settings.minTransactionsRatio) },
    ],
    matchMode: "all",
    sortColumn: "last_5m_return",
    sortDirection: "desc",
  };
}

function enrichLiveCandidate(row: Record<string, unknown>, settings: CockpitSettings): Record<string, unknown> {
  const currentOpen = numberValue(row, "current_open") || numberValue(row, "open");
  const lastVwap = numberValue(row, "last_vwap");
  const lastClose = numberValue(row, "last_close");
  const lastOpen = numberValue(row, "last_open");
  const lastHigh = numberValue(row, "last_high");
  const dayHigh = numberValue(row, "last_day_high_so_far");
  const lastLow = numberValue(row, "last_low");
  const last5mReturn = numberValue(row, "last_5m_return");
  const transactions = numberValue(row, "last_transactions");
  const txRatio = numberValue(row, "last_transactions_vs_prior_3");
  const bvd = numberValue(row, "last_bearish_volume_divergence_score");
  const aboveVwap = lastVwap > 0 && currentOpen > lastVwap;
  const breakingBody = Boolean(row.current_open_above_last_2_body_high);
  const nearDayHigh = dayHigh > 0 && currentOpen >= dayHigh * 0.995;
  const lastRed = lastClose > 0 && lastOpen > 0 && lastClose < lastOpen;
  const extendedVwap = lastVwap > 0 ? (currentOpen / lastVwap) - 1 : 0;
  const reasons = [
    last5mReturn >= settings.minLast5mReturn ? `5m ${percent(last5mReturn)}` : "",
    transactions > settings.minTransactions ? `${integer(transactions)} tx` : "",
    txRatio >= settings.minTransactionsRatio ? `${number(txRatio, 1)}x tx` : "",
    aboveVwap ? `open > VWAP by ${percent(extendedVwap)}` : "",
    breakingBody ? "body break" : "",
    nearDayHigh ? "near day high" : "",
  ].filter(Boolean);
  const risks = [
    !aboveVwap ? "below VWAP" : "",
    lastRed ? "last candle red" : "",
    bvd > 50 ? `BVD ${number(bvd, 0)}` : "",
    extendedVwap > 0.12 ? `extended ${percent(extendedVwap)} from VWAP` : "",
  ].filter(Boolean);
  const ready = aboveVwap && !lastRed && bvd <= 50 && (breakingBody || nearDayHigh);
  const bias = ready ? "Ready" : risks.length >= 2 ? "Risk" : "Watch";
  const stopBase = lastVwap > 0 ? lastVwap * 0.99 : Math.min(lastLow || currentOpen * 0.98, currentOpen * 0.98);
  const suggestedEntry = currentOpen > 0 ? currentOpen : lastClose;
  return {
    ...row,
    live_bias: bias,
    live_reasons: reasons.join(" | "),
    live_risks: risks.join(" | "),
    suggested_entry: suggestedEntry,
    suggested_stop: stopBase,
    open_vs_vwap_pct: extendedVwap,
    day_high_pressure: nearDayHigh,
    body_break_open: breakingBody,
  };
}

function openOnlyChartPayload(payload: ChartPayload | null, cutoffTime: number | null, currentOpen: number, barTime: string): ChartPayload | null {
  if (!payload || !cutoffTime) return payload;
  const priorCandles = payload.candles.filter((candle) => candle.time < cutoffTime);
  const open = currentOpen || priorCandles.at(-1)?.close || 0;
  const currentCandle = open > 0 ? [{ time: cutoffTime, open, high: open, low: open, close: open }] : [];
  const visibleTimes = new Set([...priorCandles.map((candle) => candle.time), cutoffTime]);
  return {
    ...payload,
    candles: [...priorCandles, ...currentCandle],
    markers: [
      ...payload.markers.filter((marker) => Number(marker.time) < cutoffTime),
      {
        color: "#2563EB",
        position: "inBar",
        shape: "circle",
        size: 1.2,
        text: `${barTime} open`,
        time: cutoffTime as Time,
      },
    ],
    oscillator_series: payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    overlay_series: payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => Number(point.time) < cutoffTime) })),
    price_zones: (payload.price_zones ?? []).filter((zone) => zone.start < cutoffTime).map((zone) => ({ ...zone, end: Math.min(zone.end, cutoffTime) })),
    regions: payload.regions.filter((region) => region.start < cutoffTime).map((region) => ({ ...region, end: Math.min(region.end, cutoffTime) })),
    trade_annotations: [],
    volume: [...payload.volume.filter((point) => Number(point.time) < cutoffTime), { color: "rgba(37, 99, 235, 0.25)", time: cutoffTime, value: 0 }].filter((point) => visibleTimes.has(Number(point.time))),
  };
}

function liveTableColumns(snapshotColumns: string[]) {
  return [
    "ticker",
    "live_bias",
    "current_open",
    "last_5m_return",
    "last_volume",
    "last_transactions",
    "last_transactions_vs_prior_3",
    "last_vwap",
    "open_vs_vwap_pct",
    "last_bearish_volume_divergence_score",
    "live_reasons",
    "live_risks",
    "suggested_entry",
    "suggested_stop",
    ...snapshotColumns.filter((column) => !["ticker", "current_open", "last_5m_return", "last_volume", "last_transactions", "last_transactions_vs_prior_3", "last_vwap", "last_bearish_volume_divergence_score"].includes(column)),
  ];
}

function chartRequestQuery(params: Record<string, string | number | boolean | null | undefined>) {
  return query({ min_confidence: 0.4, ...params });
}

function rowTimestampSeconds(row: Record<string, unknown>, sessionDate: string, fallbackClock: string) {
  const raw = stringValue(row, "bar_time_market") || `${sessionDate}T${fallbackClock}:00-04:00`;
  const parsed = Date.parse(raw);
  return Number.isFinite(parsed) ? Math.floor(parsed / 1000) : null;
}

function addMinutesToClock(value: string, delta: number) {
  const [hourRaw, minuteRaw] = value.split(":");
  const hour = Number(hourRaw);
  const minute = Number(minuteRaw);
  const total = Math.max(0, Math.min(23 * 60 + 59, (Number.isFinite(hour) ? hour : 4) * 60 + (Number.isFinite(minute) ? minute : 0) + delta));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function stringValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberValue(row: Record<string, unknown> | null | undefined, key: string) {
  const value = row?.[key];
  const numeric = typeof value === "number" ? value : Number(value);
  return Number.isFinite(numeric) ? numeric : 0;
}

function splitList(value: unknown) {
  return String(value || "").split("|").map((item) => item.trim()).filter(Boolean);
}

function money(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "-";
  return `$${value.toFixed(value >= 10 ? 2 : 4)}`;
}

function percent(value: number) {
  if (!Number.isFinite(value)) return "-";
  return `${(value * 100).toFixed(Math.abs(value) >= 0.1 ? 1 : 2)}%`;
}

function integer(value: number) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "-";
}

function number(value: number, digits: number) {
  return Number.isFinite(value) ? value.toFixed(digits) : "-";
}
