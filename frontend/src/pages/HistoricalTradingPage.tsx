import { ArrowLeft, CheckCircle2, CircleStop, Gauge, Pause, Play, RefreshCcw, Square, TriangleAlert, Zap } from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { TradingModeLaunch } from "../app/components/TradingModeLaunch";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

type HistoricalCheck = {
  action?: { hash?: string; label?: string };
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
  run_plan_id: string;
  available_run_plans: Array<{ name: string; profile_id: string; run_plan_id: string; strategy_id: string; strategy_revision: number }>;
  window: {
    end: string;
    session_count: number;
    sessions: string[];
    start: string;
  };
};

type BacktestRun = CanvasReplayRun & {
  configuration_revision: number;
  mode: "backtest";
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

type TestCandidateSummary = {
  candidate_id: string;
  candidate_revision: number;
  content_hash: string;
  label: string;
};

export function HistoricalTradingPage({ mode }: { mode: "backtest" }) {
  const [anchorDate, setAnchorDate] = useState(previousWeekdayIsoDate);
  const [sessionCount, setSessionCount] = useState(20);
  const [initialCash, setInitialCash] = useState(100_000);
  const [simulationProfile, setSimulationProfile] = useState<"baseline" | "stress">("baseline");
  const [sessionWindow, setSessionWindow] = useState<"extended" | "premarket">("extended");
  const [tickerScope, setTickerScope] = useState<"configured" | "single">("configured");
  const [ticker, setTicker] = useState("");
  const [preflight, setPreflight] = useState<HistoricalPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [results, setResults] = useState<BacktestResults | null>(null);
  const [comparison, setComparison] = useState<BacktestComparison | null>(null);
  const [comparisonError, setComparisonError] = useState("");
  const [controlBusy, setControlBusy] = useState("");
  const [runPlanId, setRunPlanId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [candidates, setCandidates] = useState<TestCandidateSummary[]>([]);
  const endTime = sessionWindow === "premarket" ? "09:30:00" : "20:00:00";
  const normalizedTicker = ticker.trim().toUpperCase();
  const tickerReady = tickerScope === "configured" || /^[A-Z][A-Z0-9.\-]{0,15}$/.test(normalizedTicker);

  useEffect(() => {
    let cancelled = false;
    api<{ rows: TestCandidateSummary[] }>("/api/trading/configuration/candidates")
      .then((payload) => {
        if (cancelled) return;
        setCandidates(payload.rows);
        setCandidateId((current) => current || payload.rows[0]?.candidate_id || "");
      })
      .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<HistoricalPreflight>("/api/trading/historical-preflight", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          configuration_revision_id: candidateId,
          mode,
          run_plan_id: runPlanId,
          session_count: sessionCount,
          simulation_profile: simulationProfile,
          end_time: endTime,
        }),
        method: "POST",
        timeoutMs: 60_000,
      })
        .then((payload) => {
          if (!cancelled) {
            setPreflight(payload);
            if (!runPlanId && payload.run_plan_id) setRunPlanId(payload.run_plan_id);
          }
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
  }, [anchorDate, candidateId, endTime, mode, refreshKey, runPlanId, sessionCount, simulationProfile]);

  usePollingTask({
    enabled: Boolean(run && !["completed", "stopped", "failed"].includes(run.status)),
    intervalMs: 1_000,
    onError: (reason) => setError(reason instanceof Error ? reason.message : String(reason)),
    restartKey: run?.run_id,
    task: async (signal) => {
      if (!run) return;
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}`, { signal, timeoutMs: 20_000 }));
    },
  });

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
          run_plan_id: runPlanId,
          session_count: sessionCount,
          simulation_profile: simulationProfile,
          end_time: endTime,
          tickers: tickerScope === "single" ? [normalizedTicker] : [],
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
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/commands`, {
        body: JSON.stringify({ command: "stop" }),
        method: "POST",
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  async function commandRun(command: "pause" | "play") {
    if (!run) return;
    setControlBusy(command);
    setError("");
    try {
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/commands`, {
        body: JSON.stringify({ command }),
        method: "POST",
      }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setControlBusy("");
    }
  }

  async function resumeRun() {
    if (!run) return;
    setControlBusy("resume");
    setError("");
    try {
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/resume`, { method: "POST" }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setControlBusy("");
    }
  }

  if (run) {
    const terminal = ["completed", "stopped", "failed"].includes(run.status);
    const runScope = run.tickers?.length ? run.tickers.join(", ") : "Configured strategy universe";
    return <CanvasWorkspaceSurface
      canvasId="main"
      manager={false}
      modeControls={<div className="historical-canvas-run-state historical-backtest-progress">
        <div className="historical-backtest-progress-actions"><button aria-label="Return to Backtest setup" className="button secondary compact" onClick={() => setRun(null)} type="button"><ArrowLeft size={14} /> Setup</button>{!terminal ? <><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void commandRun(run.status === "paused" ? "play" : "pause")} type="button">{run.status === "paused" ? <Play size={14} /> : <Pause size={14} />}{run.status === "paused" ? "Resume" : "Pause"}</button><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void stopRun()} type="button"><Square size={14} /> Stop</button></> : null}</div>
        <div className="historical-backtest-progress-heading"><strong>Backtest {run.status.replaceAll("_", " ")}</strong><b>{Math.round(run.progress * 100)}%</b></div>
        <div aria-label={`Backtest ${Math.round(run.progress * 100)} percent complete`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={Math.round(run.progress * 100)} className="historical-backtest-progress-track" role="progressbar"><span style={{ width: `${Math.max(0, Math.min(100, run.progress * 100))}%` }} /></div>
        <div className="historical-backtest-progress-facts"><span>{new Intl.NumberFormat("en-US").format(run.processed_events || 0)} exact events</span><span>Through {formatReplayTime(run.current_time)} ET</span><span>{runScope}</span><span><Zap aria-hidden="true" size={11} /> Accelerated causal engine</span></div>
      </div>}
      replayRun={run}
      runtimeWorkspaceId="main"
    />;
  }

  return (
    <TradingModeLaunch
      actionLabel="Run Backtest"
      actionSummary={preflight?.strategy_run_ready && tickerReady ? <>Revision <strong>{preflight.configuration_revision}</strong> will run <strong>{tickerScope === "single" ? normalizedTicker : "the configured strategy universe"}</strong> through <strong>{preflight.window.session_count} session{preflight.window.session_count === 1 ? "" : "s"}</strong> using the accelerated causal engine.</> : tickerScope === "single" && !tickerReady ? "Enter one valid ticker before starting." : "Resolve each required readiness item before starting."}
      busy={creating}
      checking={checking}
      checks={preflight?.checks ?? []}
      description="Evaluate an immutable Test Candidate across a bounded historical window using the same strategy, Portfolio, OMS, and journal contracts as Paper and Live."
      error={error}
      eyebrow="Backtest"
      icon={Gauge}
      onAction={createRun}
      onRefresh={() => setRefreshKey((value) => value + 1)}
      ready={Boolean(preflight?.strategy_run_ready && tickerReady)}
      secondary={results ? <HistoricalResults comparison={comparison} comparisonError={comparisonError} results={results} /> : null}
      title="Evaluate a strategy"
    >
              <label className="configuration-field"><span>Test Candidate</span><select aria-label="Test Candidate" onChange={(event) => setCandidateId(event.target.value)} value={candidateId}><option value="">Latest available candidate</option>{candidates.map((candidate) => <option key={candidate.candidate_id} value={candidate.candidate_id}>t{candidate.candidate_revision} · {candidate.label} · {candidate.content_hash.slice(0, 8)}</option>)}</select><small>An immutable configuration hash; creating it grants no Paper or Live authority.</small></label>
              <label className="configuration-field"><span>Strategy Run Plan</span><select aria-label="Strategy Run Plan" onChange={(event) => setRunPlanId(event.target.value)} value={runPlanId}>{(preflight?.available_run_plans ?? []).map((plan) => <option key={plan.run_plan_id} value={plan.run_plan_id}>{plan.name} · {plan.strategy_id} r{plan.strategy_revision}</option>)}</select><small>The exact Strategy Studio profile and installed executor revision used for this Backtest.</small></label>
              <label className="configuration-field"><span>Anchor date · exclusive</span><input onChange={(event) => setAnchorDate(event.target.value)} type="date" value={anchorDate} /><small>The selected date is never included in the result window.</small></label>
              <label className="configuration-field"><span>Prior exchange sessions</span><input max={260} min={1} onChange={(event) => setSessionCount(Math.max(1, Number(event.target.value) || 1))} type="number" value={sessionCount} /><small>Resolved backward from the exclusive anchor.</small></label>
              <label className="configuration-field"><span>Session window</span><select aria-label="Session window" onChange={(event) => setSessionWindow(event.target.value as "extended" | "premarket")} value={sessionWindow}><option value="extended">Whole extended session · 04:00–20:00 ET</option><option value="premarket">Premarket · 04:00–09:30 ET</option></select><small>The accelerated engine preserves the exact event order through the selected close.</small></label>
              <label className="configuration-field"><span>Backtest universe</span><select aria-label="Backtest universe" onChange={(event) => setTickerScope(event.target.value as "configured" | "single")} value={tickerScope}><option value="configured">All tickers eligible under the Run Plan</option><option value="single">One ticker only</option></select><small>One-ticker scope restricts data loading without changing Strategy, Portfolio, or OMS behavior.</small></label>
              {tickerScope === "single" ? <label className="configuration-field"><span>Ticker</span><input aria-invalid={!tickerReady} autoCapitalize="characters" onChange={(event) => setTicker(event.target.value.toUpperCase())} placeholder="SUGP" spellCheck={false} value={ticker} /><small>{tickerReady ? `Only ${normalizedTicker} will be loaded and evaluated.` : "Use a valid exchange ticker, for example SUGP."}</small></label> : null}
              <label className="configuration-field"><span>Initial cash</span><input max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} /><small>Applied to the isolated simulated account for the full run.</small></label>
              <label className="configuration-field"><span>Execution realism</span><select onChange={(event) => setSimulationProfile(event.target.value as "baseline" | "stress")} value={simulationProfile}><option value="baseline">Baseline · 25% participation · 5 bps slippage</option><option value="stress">Stress · 10% participation · 10 bps slippage</option></select><small>Both use $0.005 per share with a $1 minimum commission. Approval requires positive stress results.</small></label>
              <div className="historical-accelerated-engine-note"><Zap aria-hidden="true" size={17} /><div><strong>Accelerated causal backtest engine</strong><span>Batched QMD structural snapshots and one-batch-ahead prefetch reduce runtime while preserving point-in-time structure, fill ordering, fees, protection, Portfolio, and OMS state.</span></div></div>
    </TradingModeLaunch>
  );
}

function HistoricalResults({ comparison, comparisonError, results }: { comparison: BacktestComparison | null; comparisonError: string; results: BacktestResults }) {
  return <section className="historical-run-card historical-results-card">
            <header><div><span>Canonical results</span><strong>Portfolio and OMS journal projection</strong></div><small>{results.as_of}</small></header>
            <div className="historical-results-grid">
              <ResultMetric label="Net P&L" value={formatResultValue(results.performance_snapshot.net_pnl_today ?? results.portfolio.metrics?.net_pnl)} />
              <ResultMetric label="Open positions" value={String(results.positions.length)} />
              <ResultMetric label="Orders" value={String(results.orders.length)} />
              <ResultMetric label="Executions" value={String(results.executions.length)} />
              <ResultMetric label="Closed trades" value={String(results.closed_trades.length)} />
            </div>
            <BacktestAttribution report={results.performance_journal} />
            {comparison ? <BacktestComparisonTable comparison={comparison} currentRunId="" /> : null}
            {comparisonError ? <p className="historical-analysis-warning historical-analysis-standalone">Run comparison unavailable: {comparisonError}</p> : null}
          </section>;
}

function EvidenceCheck({ check }: { check: HistoricalCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small>{check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}</div></article>;
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

function formatReplayTime(value: string) {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) return "—";
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
    timeZone: "America/New_York",
  }).format(timestamp);
}

function previousWeekdayIsoDate() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}
