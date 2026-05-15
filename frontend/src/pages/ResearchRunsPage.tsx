import { Activity, BarChart3, CircleDollarSign, RefreshCw, Search, Sigma, Trophy } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import { DataTable } from "../app/components/DataTable";
import { PageIntro } from "../app/components/PageIntro";
import { displayName, formatMoney, formatNumber, formatPct } from "../app/format";

type RunComparisonRow = {
  created_at?: string | null;
  date_range?: string;
  return_pct?: number;
  run_dir?: string;
  run_id: string;
  run_name: string;
  status: string;
  strategy_name?: string;
  strategy_version?: string;
  total_pnl?: number;
  trade_count?: number;
};

type SortKey = "latest" | "pnl" | "return" | "trades";

const RUN_TABLE_COLUMNS = [
  "run_name",
  "strategy",
  "version",
  "status",
  "date_range",
  "return_pct",
  "total_pnl",
  "trade_count",
  "created_at",
];

const RETURN_HISTOGRAM_METRICS = [
  {
    color: "var(--foreground)",
    format: formatPct,
    id: "return_pct",
    label: "Total return",
    value: (run: RunComparisonRow) => numberValue(run.return_pct),
  },
];

export function ResearchRunsPage() {
  const [runs, setRuns] = useState<RunComparisonRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [strategyFilter, setStrategyFilter] = useState("all");
  const [versionFilter, setVersionFilter] = useState("all");
  const [statusFilter, setStatusFilter] = useState("all");
  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("latest");

  useEffect(() => {
    loadRuns();
  }, []);

  async function loadRuns() {
    setLoading(true);
    setError("");
    try {
      const payload = await api<{ runs: RunComparisonRow[] }>("/api/backtests/runs");
      setRuns(payload.runs ?? []);
    } catch (requestError) {
      setError(requestError instanceof Error ? requestError.message : "Could not load backtest runs.");
    } finally {
      setLoading(false);
    }
  }

  const strategies = useMemo(() => uniqueValues(runs.map((run) => run.strategy_name)), [runs]);
  const versions = useMemo(
    () => uniqueValues(runs.filter((run) => strategyFilter === "all" || run.strategy_name === strategyFilter).map((run) => run.strategy_version)),
    [runs, strategyFilter]
  );
  const statuses = useMemo(() => uniqueValues(runs.map((run) => run.status)), [runs]);
  const filteredRuns = useMemo(() => {
    const query = search.trim().toLowerCase();
    return runs
      .filter((run) => strategyFilter === "all" || run.strategy_name === strategyFilter)
      .filter((run) => versionFilter === "all" || run.strategy_version === versionFilter)
      .filter((run) => statusFilter === "all" || run.status === statusFilter)
      .filter((run) => {
        if (!query) return true;
        return [run.run_name, run.strategy_name, run.strategy_version, run.status, run.date_range].some((value) =>
          String(value ?? "").toLowerCase().includes(query)
        );
      })
      .sort((left, right) => compareRuns(left, right, sortKey));
  }, [runs, search, sortKey, statusFilter, strategyFilter, versionFilter]);

  const completeRuns = filteredRuns.filter((run) => normalizeStatus(run.status) === "complete");
  const bestReturn = maxBy(completeRuns, (run) => Number(run.return_pct ?? Number.NEGATIVE_INFINITY));
  const bestPnl = maxBy(completeRuns, (run) => Number(run.total_pnl ?? Number.NEGATIVE_INFINITY));
  const mostTrades = maxBy(completeRuns, (run) => Number(run.trade_count ?? Number.NEGATIVE_INFINITY));
  const latestRun = maxBy(filteredRuns, (run) => Date.parse(String(run.created_at ?? "")) || 0);
  const averageReturn = completeRuns.length ? completeRuns.reduce((total, run) => total + numberValue(run.return_pct), 0) / completeRuns.length : 0;
  const totalPnl = completeRuns.reduce((total, run) => total + numberValue(run.total_pnl), 0);

  const tableRows = filteredRuns.map((run) => ({
    created_at: formatDateTime(run.created_at),
    date_range: run.date_range ?? "-",
    return_pct: numberValue(run.return_pct),
    run_name: run.run_name,
    status: normalizeStatus(run.status),
    strategy: displayName(run.strategy_name ?? ""),
    total_pnl: numberValue(run.total_pnl),
    trade_count: Number(run.trade_count ?? 0),
    version: run.strategy_version ?? "-",
  }));
  const chartRuns = completeRuns.slice(0, 18);

  return (
    <div className="research-runs-page">
      <PageIntro
        actions={
          <button className="button" disabled={loading} onClick={loadRuns} type="button">
            <RefreshCw size={15} />
            Refresh
          </button>
        }
        description="Compare saved backtest results across strategies and versions. Use this page to spot which run deserves deeper inspection."
        groupLabel="Research"
        title="Run Comparison"
      />

      <div className="research-filter-bar">
        <SelectFilter label="Strategy" options={["all", ...strategies]} value={strategyFilter} onChange={(value) => { setStrategyFilter(value); setVersionFilter("all"); }} />
        <SelectFilter label="Version" options={["all", ...versions]} value={versionFilter} onChange={setVersionFilter} />
        <SelectFilter label="Status" options={["all", ...statuses]} value={statusFilter} onChange={setStatusFilter} />
        <SelectFilter label="Sort" options={["latest", "return", "pnl", "trades"]} value={sortKey} onChange={(value) => setSortKey(value as SortKey)} />
        <label className="research-search-field">
          <span>Search</span>
          <div>
            <Search size={14} />
            <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Run, strategy, version" />
          </div>
        </label>
      </div>

      {error ? <div className="error-panel">Run comparison failed: {error}</div> : null}

      <div className="research-metric-strip" aria-label="Run comparison metrics">
        <ResearchMetric icon={<Sigma size={16} />} label="Completed Runs" value={`${completeRuns.length.toLocaleString()} / ${filteredRuns.length.toLocaleString()}`} tone="info" />
        <ResearchMetric icon={<Activity size={16} />} label="Average Return" value={formatPct(averageReturn)} tone={averageReturn >= 0 ? "success" : "danger"} />
        <ResearchMetric icon={<CircleDollarSign size={16} />} label="Total P/L" value={formatMoney(totalPnl)} tone={totalPnl >= 0 ? "success" : "danger"} />
        <ResearchMetric icon={<Trophy size={16} />} label="Best Return" value={bestReturn ? formatPct(bestReturn.return_pct) : "-"} tone={numberValue(bestReturn?.return_pct) >= 0 ? "success" : "danger"} />
        <ResearchMetric icon={<BarChart3 size={16} />} label="Trades" value={formatNumber(completeRuns.reduce((total, run) => total + Number(run.trade_count ?? 0), 0))} tone="neutral" />
      </div>

      <div className="research-leader-grid">
        <RunLeaderCard label="Best Return" run={bestReturn} value={bestReturn ? formatPct(bestReturn.return_pct) : "-"} />
        <RunLeaderCard label="Best P/L" run={bestPnl} value={bestPnl ? formatMoney(bestPnl.total_pnl) : "-"} />
        <RunLeaderCard label="Most Trades" run={mostTrades} value={mostTrades ? formatNumber(mostTrades.trade_count) : "-"} />
        <RunLeaderCard label="Latest Run" run={latestRun} value={latestRun ? formatDateTime(latestRun.created_at) : "-"} />
      </div>

      <section className="panel research-chart-panel">
        <div className="research-section-header">
          <div>
            <h2>Run Result Histogram</h2>
            <p>Completed runs after the current filters, sorted by the selected order.</p>
          </div>
          <span>{chartRuns.length.toLocaleString()} runs</span>
        </div>
        {chartRuns.length ? <RunHistogramChart runs={chartRuns} /> : <div className="empty-state">No completed runs match the current filters.</div>}
      </section>

      <section className="panel research-table-panel">
        <div className="research-section-header">
          <div>
            <h2>Run Results</h2>
            <p>Every saved run matching the current filters.</p>
          </div>
          <span>{loading ? "Loading" : `${filteredRuns.length.toLocaleString()} rows`}</span>
        </div>
        <DataTable columns={RUN_TABLE_COLUMNS} rows={tableRows} empty={loading ? "Loading runs..." : "No runs match the current filters."} />
      </section>
    </div>
  );
}

function SelectFilter({ label, onChange, options, value }: { label: string; onChange: (value: string) => void; options: string[]; value: string }) {
  return (
    <label className="research-select-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        {options.map((option) => (
          <option key={option} value={option}>{filterOptionLabel(option)}</option>
        ))}
      </select>
    </label>
  );
}

function filterOptionLabel(option: string) {
  if (option === "all") return "All";
  if (option === "pnl") return "P/L";
  if (option === "return") return "Return";
  if (option === "trades") return "Trades";
  if (option === "latest") return "Latest";
  return displayName(option);
}

function ResearchMetric({ icon, label, tone, value }: { icon: ReactNode; label: string; tone: "danger" | "info" | "neutral" | "success"; value: string }) {
  return (
    <article className="research-metric-card" data-tone={tone}>
      <span className="research-metric-icon">{icon}</span>
      <span className="research-metric-label">{label}</span>
      <span className="research-metric-value">{value}</span>
    </article>
  );
}

function RunLeaderCard({ label, run, value }: { label: string; run?: RunComparisonRow; value: string }) {
  const tone = numberValue(run?.return_pct) >= 0 ? "success" : "danger";
  return (
    <article className="research-leader-card" data-tone={run ? tone : "neutral"}>
      <div>
        <span>{label}</span>
        <strong>{value}</strong>
      </div>
      <p>{run?.run_name ?? "No run"}</p>
      <small>{run ? `${displayName(run.strategy_name ?? "")} ${run.strategy_version ?? ""} | ${run.date_range ?? "-"}` : "-"}</small>
    </article>
  );
}

function RunHistogramChart({ runs }: { runs: RunComparisonRow[] }) {
  const metrics = RETURN_HISTOGRAM_METRICS;
  const plotLeft = 56;
  const plotTop = 18;
  const plotHeight = 190;
  const labelHeight = 104;
  const plotBottom = plotTop + plotHeight;
  const chartHeight = plotBottom + labelHeight;
  const groupWidth = Math.max(52, metrics.length * 18 + 34);
  const chartWidth = Math.max(860, plotLeft + 24 + runs.length * groupWidth);
  const barWidth = Math.min(16, Math.max(8, (groupWidth - 20) / metrics.length - 3));
  const values = runs.flatMap((run) => metrics.map((metric) => metric.value(run)));
  const minValue = Math.min(0, ...values);
  const maxValue = Math.max(0, ...values);
  const span = maxValue - minValue || 0.01;
  const yForValue = (value: number) => plotTop + ((maxValue - value) / span) * plotHeight;
  const zeroY = yForValue(0);
  const gridValues = Array.from({ length: 5 }, (_, index) => maxValue - (span * index) / 4);

  return (
    <div className="research-histogram-shell">
      <div className="research-histogram-toolbar">
        <div className="research-histogram-legend">
          {metrics.map((metric) => (
            <span className="research-histogram-legend-item" key={metric.id}>
              <i style={{ background: metric.color }} />
              {metric.label}
            </span>
          ))}
        </div>
        <span>{runs.length.toLocaleString()} plotted runs</span>
      </div>
      <div className="research-histogram-scroll" role="img" aria-label="Run result histogram">
        <svg className="research-histogram-svg" height={chartHeight} viewBox={`0 0 ${chartWidth} ${chartHeight}`} width={chartWidth}>
          {gridValues.map((value) => {
            const y = yForValue(value);
            return (
              <g key={value.toFixed(6)}>
                <line className="research-histogram-grid" x1={plotLeft} x2={chartWidth - 12} y1={y} y2={y} />
                <text className="research-histogram-y-label" dominantBaseline="middle" textAnchor="end" x={plotLeft - 10} y={y}>
                  {formatPct(value)}
                </text>
              </g>
            );
          })}
          <line className="research-histogram-axis" x1={plotLeft} x2={chartWidth - 12} y1={plotBottom} y2={plotBottom} />
          <line className="research-histogram-axis" x1={plotLeft} x2={plotLeft} y1={plotTop} y2={plotBottom} />
          <line className="research-histogram-zero" x1={plotLeft} x2={chartWidth - 12} y1={zeroY} y2={zeroY} />
          {runs.map((run, runIndex) => {
            const groupX = plotLeft + 12 + runIndex * groupWidth;
            const labelX = groupX + groupWidth / 2;
            return (
              <g key={run.run_id}>
                {metrics.map((metric, metricIndex) => {
                  const value = metric.value(run);
                  const y = yForValue(value);
                  const barX = groupX + 10 + metricIndex * (barWidth + 4);
                  const barY = Math.min(y, zeroY);
                  const barHeight = Math.max(1, Math.abs(zeroY - y));
                  return (
                    <rect
                      className="research-histogram-bar"
                      data-tone={value >= 0 ? "success" : "danger"}
                      height={barHeight}
                      key={metric.id}
                      rx={3}
                      width={barWidth}
                      x={barX}
                      y={barY}
                    >
                      <title>{`${run.run_name} | ${metric.label}: ${metric.format(value)}`}</title>
                    </rect>
                  );
                })}
                <text className="research-histogram-x-label" textAnchor="start" transform={`translate(${labelX - 5} ${plotBottom + 12}) rotate(90)`}>
                  {histogramRunLabel(run)}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
    </div>
  );
}

function uniqueValues(values: Array<string | null | undefined>) {
  return Array.from(new Set(values.filter((value): value is string => Boolean(value)))).sort((left, right) => left.localeCompare(right));
}

function compareRuns(left: RunComparisonRow, right: RunComparisonRow, sortKey: SortKey) {
  if (sortKey === "return") return numberValue(right.return_pct) - numberValue(left.return_pct);
  if (sortKey === "pnl") return numberValue(right.total_pnl) - numberValue(left.total_pnl);
  if (sortKey === "trades") return Number(right.trade_count ?? 0) - Number(left.trade_count ?? 0);
  return (Date.parse(String(right.created_at ?? "")) || 0) - (Date.parse(String(left.created_at ?? "")) || 0);
}

function maxBy<T>(items: T[], score: (item: T) => number): T | undefined {
  return items.reduce<T | undefined>((best, item) => {
    if (!best) return item;
    return score(item) > score(best) ? item : best;
  }, undefined);
}

function normalizeStatus(status: unknown) {
  return String(status ?? "unknown").toLowerCase();
}

function numberValue(value: unknown) {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function histogramRunLabel(run: RunComparisonRow) {
  const label = run.run_name || `${displayName(run.strategy_name ?? "")} ${run.strategy_version ?? ""}`.trim();
  return label.length > 28 ? `${label.slice(0, 25)}...` : label;
}

function formatDateTime(value: unknown) {
  const timestamp = Date.parse(String(value ?? ""));
  if (!Number.isFinite(timestamp)) return "-";
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    month: "short",
    year: "numeric",
  }).format(new Date(timestamp));
}
