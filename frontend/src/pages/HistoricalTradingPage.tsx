import { CheckCircle2, CircleStop, Gauge, Play, RefreshCcw, Square, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import { MarketStatusBadge, historicalMarketStatus } from "../app/components/MarketStatusBadge";

type HistoricalCheck = {
  evidence: string;
  id: string;
  label: string;
  required: boolean;
  status: "blocked" | "error" | "ready";
  summary: string;
};

type HistoricalPreflight = {
  automatic_strategy_count: number;
  checks: HistoricalCheck[];
  strategy_run_ready: boolean;
  configuration_revision_id: string;
  configuration_revision: number;
  configuration_content_hash: string;
  window: {
    end: string;
    session_count: number;
    sessions: string[];
    start: string;
  };
};

type BacktestRun = {
  configuration_revision: number;
  current_time: string;
  error: string;
  mode: "backtest";
  processed_events: number;
  progress: number;
  run_id: string;
  session_date: string;
  session_end: string;
  status: string;
};

type BacktestResults = {
  as_of: string;
  closed_trades: Array<Record<string, unknown>>;
  executions: Array<Record<string, unknown>>;
  orders: Array<Record<string, unknown>>;
  performance_journal: {
    scope?: { attribution_coverage?: unknown };
    strategies?: Array<Record<string, unknown>>;
    summary?: Record<string, unknown>;
  };
  performance_snapshot: Record<string, unknown>;
  portfolio: { metrics?: Record<string, unknown>; position_count?: number };
  positions: Array<Record<string, unknown>>;
};

type BacktestComparison = {
  authority: "canonical_performance_journal";
  run_count: number;
  runs: Array<Record<string, unknown>>;
  strategies: Array<Record<string, unknown>>;
  warnings: Array<{ code: string; detail: string; run_id: string }>;
};

export function HistoricalTradingPage({ mode }: { mode: "backtest" }) {
  const [anchorDate, setAnchorDate] = useState(previousWeekdayIsoDate);
  const [sessionCount, setSessionCount] = useState(20);
  const [initialCash, setInitialCash] = useState(100_000);
  const [preflight, setPreflight] = useState<HistoricalPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [results, setResults] = useState<BacktestResults | null>(null);
  const [comparison, setComparison] = useState<BacktestComparison | null>(null);
  const [comparisonError, setComparisonError] = useState("");

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<HistoricalPreflight>("/api/trading/historical-preflight", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          mode,
          session_count: sessionCount,
        }),
        method: "POST",
        timeoutMs: 60_000,
      })
        .then((payload) => {
          if (!cancelled) setPreflight(payload);
        })
        .catch((reason) => {
          if (!cancelled) {
            setPreflight(null);
            setError(reason instanceof Error ? reason.message : String(reason));
          }
        })
        .finally(() => {
          if (!cancelled) setChecking(false);
        });
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [anchorDate, mode, refreshKey, sessionCount]);

  useEffect(() => {
    if (!run || ["completed", "stopped", "failed"].includes(run.status)) return;
    const timer = window.setInterval(() => {
      api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}`, { timeoutMs: 20_000 })
        .then(setRun)
        .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
    }, 1_000);
    return () => window.clearInterval(timer);
  }, [run]);

  useEffect(() => {
    if (!run || !["completed", "stopped", "failed"].includes(run.status)) return;
    api<BacktestResults>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/results`, { timeoutMs: 60_000 })
      .then(setResults)
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
    setComparisonError("");
    api<BacktestComparison>("/api/trading/backtest/comparison?limit=10", { timeoutMs: 60_000 })
      .then(setComparison)
      .catch((reason) => {
        setComparison(null);
        setComparisonError(reason instanceof Error ? reason.message : String(reason));
      });
  }, [run?.run_id, run?.status]);

  async function createRun() {
    if (!preflight?.strategy_run_ready) return;
    setCreating(true);
    setError("");
    try {
      const created = await api<BacktestRun>("/api/trading/backtest/runs", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          configuration_revision_id: preflight.configuration_revision_id,
          initial_cash: initialCash,
          session_count: sessionCount,
        }),
        method: "POST",
        timeoutMs: 60_000,
      });
      setResults(null);
      setComparison(null);
      setComparisonError("");
      setRun(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreating(false);
    }
  }

  async function stopRun() {
    if (!run) return;
    try {
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/stop`, { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  return (
    <div className="historical-home">
      <header className="historical-goal-hero">
        <div className="historical-goal-copy">
          <h1>Backtest a strategy</h1>
          <p>Choose an exclusive anchor date and the prior exchange sessions to evaluate.</p>
        </div>
        <MarketStatusBadge value={historicalMarketStatus(anchorDate)} />
      </header>

      {error ? <div className="historical-error-banner"><TriangleAlert size={18} /><div><strong>Preflight failed</strong><span>{error}</span></div></div> : null}

      <div className="historical-home-grid">
        <main className="historical-primary-column">
          <section className="historical-run-card">
            <header>
              <div><span>Run definition</span><strong>Sessions before the anchor</strong></div>
              <button className="button secondary" disabled={checking} onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCcw size={16} /> Check again</button>
            </header>
            <div className="historical-large-fields">
              <label><span>Anchor date · exclusive</span><input onChange={(event) => setAnchorDate(event.target.value)} type="date" value={anchorDate} /><small>The selected date is never included in the result window.</small></label>
              <label><span>Prior exchange sessions</span><input max={260} min={1} onChange={(event) => setSessionCount(Math.max(1, Number(event.target.value) || 1))} type="number" value={sessionCount} /><small>Resolved backward from the exclusive anchor.</small></label>
              <label><span>Initial cash</span><input max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} /><small>Applied to each isolated simulated account for the full run.</small></label>
            </div>
            <header className="historical-evidence-header"><div><span>Preflight</span><strong>Verified dependencies and data</strong></div>{checking ? <span className="historical-checking"><Gauge size={15} /> Checking</span> : null}</header>
            <div className="historical-check-list">
              {preflight?.checks.map((check) => <EvidenceCheck check={check} key={check.id} />)}
            </div>
          </section>
          {results ? <section className="historical-run-card historical-results-card">
            <header><div><span>Canonical results</span><strong>Portfolio and OMS journal projection</strong></div><small>{results.as_of}</small></header>
            <div className="historical-results-grid">
              <ResultMetric label="Net P&L" value={formatResultValue(results.performance_snapshot.net_pnl_today ?? results.portfolio.metrics?.net_pnl)} />
              <ResultMetric label="Open positions" value={String(results.positions.length)} />
              <ResultMetric label="Orders" value={String(results.orders.length)} />
              <ResultMetric label="Executions" value={String(results.executions.length)} />
              <ResultMetric label="Closed trades" value={String(results.closed_trades.length)} />
            </div>
            <BacktestAttribution report={results.performance_journal} />
            {comparison ? <BacktestComparisonTable comparison={comparison} currentRunId={run?.run_id || ""} /> : null}
            {comparisonError ? <p className="historical-analysis-warning historical-analysis-standalone">Run comparison unavailable: {comparisonError}</p> : null}
          </section> : null}
        </main>

        <aside className="historical-action-column">
          <section className={`historical-primary-action ${preflight?.strategy_run_ready ? "" : "blocked"}`}>
            {preflight?.strategy_run_ready ? <Play size={24} /> : <CircleStop size={24} />}
            <div><strong>{run ? `Backtest ${run.status.replaceAll("_", " ")}` : preflight?.strategy_run_ready ? "Backtest is ready" : "Backtest execution is blocked"}</strong><p>{run
              ? `${Math.round(run.progress * 100)}% complete · ${new Intl.NumberFormat("en-US", { notation: "compact" }).format(run.processed_events)} events · ${run.current_time}`
              : preflight?.strategy_run_ready
                ? `Revision ${preflight.configuration_revision} will run through ${preflight.window.session_count} sessions using one simulated Portfolio/OMS state.`
                : "Resolve every required preflight item before starting."}</p></div>
            {run && !["completed", "stopped", "failed"].includes(run.status)
              ? <button className="button secondary" onClick={stopRun} type="button"><Square size={15} /> Stop</button>
              : <button className="button primary" disabled={checking || creating || !preflight?.strategy_run_ready} onClick={createRun} type="button"><Play size={16} /> {creating ? "Creating run…" : "Run backtest"}</button>}
            {run?.error ? <small>{run.error}</small> : null}
          </section>
        </aside>
      </div>
    </div>
  );
}

function EvidenceCheck({ check }: { check: HistoricalCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small></div></article>;
}

function ResultMetric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>;
}

function BacktestAttribution({ report }: { report: BacktestResults["performance_journal"] }) {
  const rows = report.strategies || [];
  const coverage = Number(report.scope?.attribution_coverage ?? 0);
  return <section className="historical-analysis-section">
    <header><div><span>Current run attribution</span><strong>Strategy revisions</strong></div><small>{formatPercent(coverage)} attributed</small></header>
    {rows.length ? <div className="historical-attribution-grid">{rows.map((row) => <article key={`${row.strategy_id}-${row.strategy_revision}`}>
      <div><strong>{String(row.strategy_id || "Unattributed")}</strong><span>revision {String(row.strategy_revision ?? 0)}</span></div>
      <b data-tone={Number(row.net_pnl || 0) >= 0 ? "positive" : "negative"}>{formatResultValue(row.net_pnl)}</b>
      <small>{String(row.episode_count || 0)} episodes · {formatPercent(Number(row.win_rate || 0))} win rate</small>
    </article>)}</div> : <p className="historical-analysis-empty">No closed flat-to-flat episodes are available for attribution.</p>}
  </section>;
}

function BacktestComparisonTable({ comparison, currentRunId }: { comparison: BacktestComparison; currentRunId: string }) {
  return <section className="historical-analysis-section">
    <header><div><span>Comparative analysis</span><strong>Last {comparison.run_count} terminal runs</strong></div><small>Canonical journal authority</small></header>
    {comparison.runs.length ? <div className="historical-comparison-scroll"><table><thead><tr><th>Run</th><th>Revision</th><th>Episodes</th><th>Net P&amp;L</th><th>Win rate</th><th>Expectancy</th><th>Max drawdown</th><th>Attributed</th></tr></thead><tbody>{comparison.runs.map((row) => <tr className={row.run_id === currentRunId ? "is-current" : undefined} key={String(row.run_id)}><td><strong>{shortRunId(row.run_id)}</strong><small>{String(row.status || "unknown")}</small></td><td>{String(row.configuration_revision ?? "—")}</td><td>{String(row.episode_count || 0)}</td><td data-tone={Number(row.net_pnl || 0) >= 0 ? "positive" : "negative"}>{formatResultValue(row.net_pnl)}</td><td>{formatPercent(Number(row.win_rate || 0))}</td><td>{formatResultValue(row.expectancy)}</td><td>{formatResultValue(row.maximum_drawdown)}</td><td>{formatPercent(Number(row.attribution_coverage || 0))}</td></tr>)}</tbody></table></div> : <p className="historical-analysis-empty">Complete a Backtest run to create a comparison baseline.</p>}
    {comparison.warnings.length ? <small className="historical-analysis-warning">{comparison.warnings.length} run result{comparison.warnings.length === 1 ? " was" : "s were"} unavailable and excluded.</small> : null}
  </section>;
}

function formatPercent(value: number) {
  if (!Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, style: "percent" }).format(value);
}

function shortRunId(value: unknown) {
  const text = String(value || "");
  return text.length > 12 ? `${text.slice(0, 8)}…` : text;
}

function formatResultValue(value: unknown) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: 2, style: "currency" }).format(number);
}

function previousWeekdayIsoDate() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
