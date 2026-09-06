import { ArrowLeft, CheckCircle2, CircleStop, Gauge, Pause, Play, RefreshCcw, Square, TriangleAlert, X, Zap } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { TradingLaunchEvidence, TradingModeLaunch, TradingModeSelectField } from "../app/components/TradingModeLaunch";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

type HistoricalCheck = {
  action?: { hash?: string; label?: string };
  evidence: unknown;
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

type BacktestConfigurationOptions = {
  candidates: Array<{ candidate_id: string; candidate_revision: number; label: string; content_hash: string }>;
  candidate_id: string;
  run_plan_id: string;
  available_run_plans: HistoricalPreflight["available_run_plans"];
  error: string;
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

type BacktestPeriodPreset = "premarket" | "regular" | "extended" | "custom";

type IndicatorWarmup = {
  bars: Array<{ bar_start: string; close: number }>;
  cache_hit: boolean;
  fetched_events: number;
  fetched_ordinal_ranges: number;
  required_bars: number;
  status: "ready" | "insufficient_history";
  ticker: string;
};

type IndicatorWarmupBatch = {
  items: IndicatorWarmup[];
  ready_count: number;
  required_bars: number;
  status: "ready" | "insufficient_history";
  ticker_count: number;
  tickers: string[];
};

export function HistoricalTradingPage({ mode }: { mode: "backtest" }) {
  const [sessionDate, setSessionDate] = useState(previousWeekdayIsoDate);
  const [initialCash, setInitialCash] = useState(10_000);
  const [structureBook, setStructureBook] = useState("");
  const [minimumPNorm, setMinimumPNorm] = useState(0.90);
  const [structureBooks, setStructureBooks] = useState<Array<{ id: string; ticker: string; start: string; end: string }>>([]);
  useEffect(() => { let active = true; api<{ items: typeof structureBooks }>("/api/trading/backtest/structure-books")
    .then((value) => { if (active) setStructureBooks(value.items); }).catch(() => { if (active) setError("Experimental level books could not be loaded."); });
    return () => { active = false; };
  }, []);
  const [simulationProfile, setSimulationProfile] = useState<"baseline" | "stress">("baseline");
  const [periodPreset, setPeriodPreset] = useState<BacktestPeriodPreset>("premarket");
  const [startTime, setStartTime] = useState("04:00:00");
  const [endTime, setEndTime] = useState("09:30:00");
  const [tickerInput, setTickerInput] = useState("");
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
  const [configurationOptions, setConfigurationOptions] = useState<BacktestConfigurationOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [optionsError, setOptionsError] = useState("");
  const [checkedSetupKey, setCheckedSetupKey] = useState("");
  const [indicatorWarmup, setIndicatorWarmup] = useState<IndicatorWarmupBatch | null>(null);
  const [warmingIndicators, setWarmingIndicators] = useState(false);
  const parsedTickers = useMemo(() => parseBacktestTickers(tickerInput), [tickerInput]);
  const normalizedTickers = parsedTickers.tickers;
  const tickerReady = normalizedTickers.length > 0 && normalizedTickers.length <= 100 && parsedTickers.invalid.length === 0;
  const periodReady = startTime >= "04:00:00" && endTime <= "20:00:00" && startTime < endTime;
  const anchorDate = nextIsoDate(sessionDate);
  const resolvedSessionMatches = preflight?.window.sessions.length === 1 && preflight.window.sessions[0] === sessionDate;
  const selectedPlan = configurationOptions?.candidate_id === candidateId
    ? configurationOptions.available_run_plans.find((plan) => plan.run_plan_id === runPlanId) : undefined;
  const setupKey = JSON.stringify([candidateId, runPlanId, sessionDate, startTime, endTime, normalizedTickers, refreshKey]);
  const currentPreflight = checkedSetupKey === setupKey && preflight?.configuration_revision_id === candidateId && preflight.run_plan_id === runPlanId;

  useEffect(() => {
    let cancelled = false;
    const controller = new AbortController();
    setLoadingOptions(true);
    setOptionsError("");
    api<BacktestConfigurationOptions>(`/api/trading/backtest/configuration-options?candidate_id=${encodeURIComponent(candidateId)}`, { signal: controller.signal, timeoutMs: 60_000 })
      .then((payload) => {
        if (cancelled) return;
        setConfigurationOptions(payload);
        setCandidateId(payload.candidate_id);
        setRunPlanId((current) => payload.available_run_plans.some((plan) => plan.run_plan_id === current) ? current : payload.run_plan_id);
        setOptionsError(payload.error);
      })
      .catch((reason) => { if (!cancelled) setOptionsError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!cancelled) setLoadingOptions(false); });
    return () => { cancelled = true; controller.abort(); };
  }, [candidateId, refreshKey]);

  useEffect(() => {
    if (!tickerReady) {
      setIndicatorWarmup(null);
      setWarmingIndicators(false);
      return;
    }
    let cancelled = false;
    setWarmingIndicators(true);
    setIndicatorWarmup(null);
    setError("");
    const timer = window.setTimeout(() => {
      api<IndicatorWarmupBatch>("/api/trading/backtest/indicator-warmup", {
        body: JSON.stringify({ session_date: sessionDate, tickers: normalizedTickers, timeframe: "1s", required_bars: 200 }),
        method: "POST",
        timeoutMs: 240_000,
      })
        .then((payload) => { if (!cancelled) setIndicatorWarmup(payload); })
        .catch((reason) => { if (!cancelled) setError(reason instanceof Error ? reason.message : String(reason)); })
        .finally(() => { if (!cancelled) setWarmingIndicators(false); });
    }, 450);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [normalizedTickers, refreshKey, sessionDate, tickerReady]);

  useEffect(() => {
    if (!candidateId || !selectedPlan || loadingOptions || optionsError || !tickerReady || indicatorWarmup?.status !== "ready") {
      setChecking(false);
      setPreflight(null);
      return;
    }
    let cancelled = false;
    setChecking(true);
    setPreflight(null);
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<HistoricalPreflight>("/api/trading/historical-preflight", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          configuration_revision_id: candidateId,
          mode,
          run_plan_id: runPlanId,
          session_count: 1,
          start_time: startTime,
          end_time: endTime,
          tickers: normalizedTickers,
        }),
        method: "POST",
        timeoutMs: 60_000,
      })
        .then((payload) => {
          if (!cancelled) {
            setPreflight(payload);
            setCheckedSetupKey(setupKey);
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
  }, [anchorDate, candidateId, endTime, indicatorWarmup?.status, loadingOptions, mode, normalizedTickers, optionsError, refreshKey, runPlanId, selectedPlan, setupKey, startTime, tickerReady]);

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
    if (!launchReady || checking || loadingOptions || !currentPreflight) return;
    setCreating(true);
    setError("");
    try {
      const created = await api<BacktestRun>("/api/trading/backtest/runs", {
        body: JSON.stringify({
          anchor_date: anchorDate,
          configuration_revision_id: candidateId,
          initial_cash: initialCash,
          run_plan_id: runPlanId,
          session_count: 1,
          simulation_profile: simulationProfile,
          experimental_structure_book: structureBook,
          minimum_p_norm: minimumPNorm,
          start_time: startTime,
          end_time: endTime,
          tickers: normalizedTickers,
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

  const warmupCheck: HistoricalCheck = {
    id: "indicator_warmup",
    label: "1-second indicator warm-up",
    required: true,
    status: indicatorWarmup?.status === "ready" ? "ready" : indicatorWarmup?.status === "insufficient_history" ? "blocked" : "blocked",
    summary: indicatorWarmup?.status === "ready"
      ? `${indicatorWarmup.ready_count}/${indicatorWarmup.ticker_count} ticker warm-ups are ready from canonical closes.`
      : indicatorWarmup?.status === "insufficient_history"
        ? `${indicatorWarmup.ready_count}/${indicatorWarmup.ticker_count} ticker warm-ups are ready; ${indicatorWarmup.items.filter((item) => item.status !== "ready").map((item) => item.ticker).join(", ")} lack the required history.`
        : warmingIndicators ? "Building bounded warm-ups from imported event ordinals…" : "Enter one or more tickers to prepare indicator history.",
    evidence: indicatorWarmup?.status === "ready"
      ? `${indicatorWarmup.items.reduce((total, item) => total + item.fetched_ordinal_ranges, 0)} ordinal range(s) · ${new Intl.NumberFormat("en-US").format(indicatorWarmup.items.reduce((total, item) => total + item.fetched_events, 0))} eligible trades`
      : "market_sip_compact/q_live imported events only",
  };
  const configurationCheck = {
    id: "selected_configuration", label: "Strategy selection", required: true,
    status: selectedPlan && !loadingOptions && !optionsError ? "ready" : "blocked",
    summary: loadingOptions ? "Loading saved candidates and compatible strategies." : optionsError || "Select a saved Test Candidate and strategy. Create a candidate in Test Candidates if none are available.",
    action: !configurationOptions?.candidates.length && !loadingOptions ? { hash: "#revision-configuration", label: "Test Candidates" } : undefined,
  };
  const launchChecks = [configurationCheck, warmupCheck, ...(currentPreflight ? preflight?.checks ?? [] : [])];
  const launchReady = Boolean(currentPreflight && selectedPlan && !loadingOptions && !optionsError && preflight?.strategy_run_ready && indicatorWarmup?.status === "ready" && tickerReady && periodReady && resolvedSessionMatches);

  return (
    <TradingModeLaunch
      actionLabel="Run Backtest"
      actionSummary={launchReady ? <><strong>{normalizedTickers.join(", ")}</strong> will run together on <strong>{sessionDate}</strong> from <strong>{startTime.slice(0, 5)}–{endTime.slice(0, 5)} ET</strong> using one shared simulated portfolio and strategy revision <strong>{selectedPlan?.strategy_revision}</strong> (candidate {preflight?.configuration_revision}).</> : !tickerReady ? parsedTickers.invalid.length ? `Remove invalid ticker${parsedTickers.invalid.length === 1 ? "" : "s"}: ${parsedTickers.invalid.join(", ")}.` : "Enter at least one valid ticker before starting." : warmingIndicators ? "Preparing persisted 1-second indicator warm-ups." : !periodReady ? "Choose a valid period inside 04:00–20:00 ET." : preflight && !resolvedSessionMatches ? "The selected date is not an exchange session. Choose a trading day." : "Resolve each required readiness item before starting."}
      busy={creating}
      checking={checking || warmingIndicators || loadingOptions}
      checkingLabel={loadingOptions ? "Loading strategy settings…" : warmingIndicators ? "Preparing indicators…" : "Checking strategy and services…"}
      checks={launchChecks}
      description="Evaluate an immutable Test Candidate across a bounded historical window using the same strategy, Portfolio, OMS, and journal contracts as Paper and Live."
      error={optionsError || error}
      eyebrow="Backtest"
      icon={Gauge}
      onAction={createRun}
      onRefresh={() => setRefreshKey((value) => value + 1)}
      ready={launchReady}
      secondary={results ? <HistoricalResults comparison={comparison} comparisonError={comparisonError} results={results} /> : null}
      title="Evaluate a strategy"
    >
              <TradingModeSelectField
                label="Test Candidate" disabled={loadingOptions || !configurationOptions?.candidates.length}
                help="Saved configuration containing the strategy, Portfolio, and OMS settings. The latest candidate is selected initially."
                onChange={(value) => { setPreflight(null); setRunPlanId(""); setCandidateId(value); }}
                options={configurationOptions?.candidates.length ? configurationOptions.candidates.map((row) => ({ value: row.candidate_id, label: `${row.candidate_revision} · ${row.label}` })) : [{ value: "", label: loadingOptions ? "Loading candidates…" : "No Test Candidates" }]}
                value={candidateId}
              />
              <TradingModeSelectField
                label="Strategy / Run Plan" disabled={loadingOptions || configurationOptions?.candidate_id !== candidateId || !configurationOptions?.available_run_plans.length || Boolean(optionsError)}
                help="Choose the strategy revision and its execution plan. Outdated strategy revisions are blocked by launch checks."
                onChange={(value) => { setPreflight(null); setRunPlanId(value); }}
                options={configurationOptions?.available_run_plans.length ? configurationOptions.available_run_plans.map((plan) => ({ value: plan.run_plan_id, label: `${plan.name} · strategy r${plan.strategy_revision}`, description: plan.profile_id })) : [{ value: "", label: loadingOptions ? "Loading strategies…" : "No compatible strategies" }]}
                value={runPlanId}
              />
              <label className="configuration-field mode-launch-ticker-field"><span>Tickers</span><textarea aria-invalid={!tickerReady && Boolean(tickerInput)} autoCapitalize="characters" onChange={(event) => setTickerInput(event.target.value.toUpperCase())} placeholder="SUGP, AAPL" rows={2} spellCheck={false} value={tickerInput} />{normalizedTickers.length ? <span aria-label="Selected tickers" className="mode-launch-ticker-chips">{normalizedTickers.map((ticker) => <span key={ticker}>{ticker}<button aria-label={`Remove ${ticker}`} onClick={(event) => { event.preventDefault(); setTickerInput(normalizedTickers.filter((value) => value !== ticker).join(", ")); }} type="button"><X aria-hidden="true" size={11} /></button></span>)}</span> : null}<small>Separate up to 100 symbols with commas, spaces, or new lines. They run together in one chronological event stream and share the simulated portfolio.</small></label>
              <label className="configuration-field"><span>Trading date</span><input onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} /><small>Must be an exchange trading session; weekends and holidays fail closed.</small></label>
              <TradingModeSelectField help="Presets bound the decision window while retaining causal warm-up evidence." label="Time period" onChange={(value) => applyPeriodPreset(value as BacktestPeriodPreset, setPeriodPreset, setStartTime, setEndTime)} options={[{ label: "Premarket · 04:00–09:30 ET", value: "premarket" }, { label: "Regular session · 09:30–16:00 ET", value: "regular" }, { label: "Whole extended session · 04:00–20:00 ET", value: "extended" }, { label: "Custom period", value: "custom" }]} value={periodPreset} />
              <label className="configuration-field"><span>Start time · ET</span><input aria-label="Start time" max="19:59:59" min="04:00:00" onChange={(event) => { setPeriodPreset("custom"); setStartTime(normalizeClockInput(event.target.value)); }} step="1" type="time" value={startTime} /><small>No new strategy actions are admitted before this time.</small></label>
              <label className="configuration-field"><span>End time · ET</span><input aria-label="End time" max="20:00:00" min="04:00:01" onChange={(event) => { setPeriodPreset("custom"); setEndTime(normalizeClockInput(event.target.value)); }} step="1" type="time" value={endTime} /><small>The run stops at this exact New York boundary.</small></label>
              <label className="configuration-field"><span>Initial cash</span><input max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} /><small>Applied to the isolated simulated account for the full run.</small></label>
              <TradingModeSelectField label="Level book" help={structureBook ? "Overlapping same-role levels merge causally. The strategy uses p_norm for entry, support stops and resistance targets. The chart threshold is independent." : "Uses the current v18 structural level contract. Select an Experimental ClickHouse book to test merged levels and normalized prominence."}
                value={structureBook} onChange={(value) => { setStructureBook(value); const book = structureBooks.find((row) => row.id === value); if (book) setTickerInput(book.ticker); }}
                options={[{ label: "Current v18", value: "" }, ...structureBooks.map((row) => ({ label: `Experimental ClickHouse · ${row.ticker} · through ${row.end}`, value: row.id }))]} />
              {structureBook ? <label className="configuration-field"><span>Strategy minimum p_norm</span><input aria-label="Strategy minimum p_norm" type="range" min={0} max={1} step={0.01} value={minimumPNorm} onChange={(event) => setMinimumPNorm(Number(event.target.value))} /><output>{minimumPNorm.toFixed(2)}</output><small>Applies to entry, support stops and resistance targets. Frozen prior-session normalization; default price range is 0 to twice the prior close.</small></label> : null}
              <TradingModeSelectField help="Both use $0.005 per share with a $1 minimum commission. Approval requires positive stress results." label="Execution realism" onChange={(value) => setSimulationProfile(value as "baseline" | "stress")} options={[{ label: "Baseline · 25% participation · 5 bps slippage", value: "baseline" }, { label: "Stress · 10% participation · 10 bps slippage", value: "stress" }]} value={simulationProfile} />
              <div className="historical-accelerated-engine-note"><Zap aria-hidden="true" size={17} /><div><strong>Accelerated causal engine</strong><span>{selectedPlan ? `Strategy revision ${selectedPlan.strategy_revision} · candidate ${configurationOptions?.candidates.find((row) => row.candidate_id === candidateId)?.candidate_revision}.` : "Select a strategy above."} Launch checks require current execution code and strategy. Results open in Charts &amp; Quotes with MACD, positions, lifecycle activity, and performance.</span></div></div>
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
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><TradingLaunchEvidence evidence={check.evidence} />{check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}</div></article>;
}

function parseBacktestTickers(value: string): { invalid: string[]; tickers: string[] } {
  const tokens = value.toUpperCase().split(/[\s,;]+/).map((token) => token.trim()).filter(Boolean);
  const unique = Array.from(new Set(tokens));
  return {
    invalid: unique.filter((ticker) => !/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)),
    tickers: unique.filter((ticker) => /^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)),
  };
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

function nextIsoDate(value: string) {
  const parsed = new Date(`${value}T12:00:00Z`);
  if (!Number.isFinite(parsed.getTime())) return value;
  parsed.setUTCDate(parsed.getUTCDate() + 1);
  return parsed.toISOString().slice(0, 10);
}

function normalizeClockInput(value: string) {
  return /^\d{2}:\d{2}:\d{2}$/.test(value) ? value : `${value}:00`;
}

function applyPeriodPreset(
  preset: BacktestPeriodPreset,
  setPreset: (value: BacktestPeriodPreset) => void,
  setStart: (value: string) => void,
  setEnd: (value: string) => void,
) {
  setPreset(preset);
  if (preset === "custom") return;
  const period = {
    premarket: ["04:00:00", "09:30:00"],
    regular: ["09:30:00", "16:00:00"],
    extended: ["04:00:00", "20:00:00"],
  }[preset];
  setStart(period[0]);
  setEnd(period[1]);
}
