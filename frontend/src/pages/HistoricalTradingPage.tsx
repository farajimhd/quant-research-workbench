import { Modal } from "../app/components/Modal";
import { BacktestRecoveryFailure } from "../app/components/BacktestRecoveryFailure";
import { BacktestRecoveryState } from "../app/components/BacktestRecoveryState";
import { BacktestRunHistory } from "../app/components/BacktestRunHistory";
import { ArrowLeft, CheckCircle2, CircleStop, Gauge, LoaderCircle, Pause, Play, RefreshCcw, Square, TriangleAlert, X, Zap } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { FailedBacktestError, recoverBacktest } from "../app/backtestRecovery";
import { TradingLaunchEvidence, TradingModeLaunch, TradingModeSelectField } from "../app/components/TradingModeLaunch";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";
import { DEFAULT_BACKTEST_DATE, presetTickers, v6BookFor, type BacktestTickerPreset } from './backtestPresets';

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
  level_book_coverage?: {
    eligible_ticker_count: number;
    excluded_ticker_count: number;
    excluded: Array<{ ticker: string; session: string; reason: string }>;
  };
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

type BacktestPeriodPreset = "premarket" | "regular" | "after_hours" | "extended" | "custom";

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

const BACKTEST_RUN_KEY = "backtest.active-run.v1";

function readSelectedRun(): string {
  const fromUrl = new URL(window.location.href).searchParams.get("backtest_run");
  if (fromUrl) return fromUrl;
  try { return sessionStorage.getItem(BACKTEST_RUN_KEY) || ""; } catch { return ""; }
}

function persistSelectedRun(runId: string) {
  const url = new URL(window.location.href);
  if (runId) url.searchParams.set("backtest_run", runId);
  else url.searchParams.delete("backtest_run");
  window.history.replaceState(window.history.state, "", url);
  try {
    if (runId) sessionStorage.setItem(BACKTEST_RUN_KEY, runId);
    else sessionStorage.removeItem(BACKTEST_RUN_KEY);
  } catch { /* The URL remains the reload authority when storage is disabled. */ }
}

export function HistoricalTradingPage({ mode }: { mode: "backtest" }) {
  const [detailsOpen, setDetailsOpen] = useState(false);
  const [selectedRunId, setSelectedRunId] = useState(readSelectedRun);
  const [sessionDate, setSessionDate] = useState(DEFAULT_BACKTEST_DATE);
  const [tickerPreset, setTickerPreset] = useState<BacktestTickerPreset>('SUGP');
  const [batchRuns, setBatchRuns] = useState<BacktestRun[]>([]);
  const [initialCash, setInitialCash] = useState(10_000);
  const [structureBook, setStructureBook] = useState("");
  const [minimumPNorm, setMinimumPNorm] = useState(0.80);
  const [structureBooks, setStructureBooks] = useState<Array<{ id: string; ticker: string; start: string; end: string; version: string; selection_contract?: string }>>([]);
  useEffect(() => { if (selectedRunId) return; let active = true; api<{ items: typeof structureBooks }>("/api/trading/backtest/structure-books")
    .then((value) => { if (active) setStructureBooks(value.items); }).catch(() => { if (active) setError("Experimental level books could not be loaded."); });
    return () => { active = false; };
  }, [selectedRunId]);
  const [simulationProfile, setSimulationProfile] = useState<"baseline" | "stress">("baseline");
  const [periodPreset, setPeriodPreset] = useState<BacktestPeriodPreset>("custom");
  const [startTime, setStartTime] = useState("04:00:00");
  const [endTime, setEndTime] = useState("04:30:00");
  const [tickerInput, setTickerInput] = useState("SUGP");
  const batchPreset = tickerPreset === 'both' || tickerPreset === 'all';
  const fullMarket = tickerPreset === 'market';
  useEffect(() => {
    if (tickerPreset === 'custom') return;
    const tickers = presetTickers(tickerPreset, sessionDate, structureBooks);
    setTickerInput(tickers.join(', '));
  }, [tickerPreset, sessionDate, structureBooks]);
  function chooseTickerPreset(value: string) {
    setTickerPreset(value as BacktestTickerPreset);
    if (value === 'market') {
      applyPeriodPreset('extended', setPeriodPreset, setStartTime, setEndTime);
    }
  }
  const [preflight, setPreflight] = useState<HistoricalPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [run, setRun] = useState<BacktestRun | null>(null);
  const [restoreError, setRestoreError] = useState("");
  const [restoreFailed, setRestoreFailed] = useState(false);
  const [restoreAttempt, setRestoreAttempt] = useState(0);
  const [results, setResults] = useState<BacktestResults | null>(null);
  const [comparison, setComparison] = useState<BacktestComparison | null>(null);
  const [comparisonError, setComparisonError] = useState("");
  const [controlBusy, setControlBusy] = useState("");
  const [runPlanId, setRunPlanId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const resolvedOptionsRequest = useRef<string | null>(null);
  const [configurationOptions, setConfigurationOptions] = useState<BacktestConfigurationOptions | null>(null);
  const [loadingOptions, setLoadingOptions] = useState(true);
  const [optionsError, setOptionsError] = useState("");
  const [checkedSetupKey, setCheckedSetupKey] = useState("");
  const [indicatorWarmup, setIndicatorWarmup] = useState<IndicatorWarmupBatch | null>(null);
  const [warmingIndicators, setWarmingIndicators] = useState(false);
  const parsedTickers = useMemo(() => parseBacktestTickers(tickerInput), [tickerInput]);
  const normalizedTickers = useMemo(() => fullMarket ? [] : parsedTickers.tickers, [fullMarket, parsedTickers]);
  useEffect(() => {
    if (fullMarket) { setStructureBook('level-book-v7'); return; }
    const selected = parseBacktestTickers(tickerInput);
    setStructureBook(selected.invalid.length === 0 && selected.tickers.length === 1
      ? v6BookFor(selected.tickers[0], sessionDate, structureBooks)?.id ?? '' : '');
  }, [tickerInput, sessionDate, structureBooks, fullMarket]);
  const tickerReady = fullMarket || (normalizedTickers.length > 0 && normalizedTickers.length <= 100 && parsedTickers.invalid.length === 0);
  const periodReady = startTime >= "04:00:00" && endTime <= "20:00:00" && startTime < endTime;
  const anchorDate = nextIsoDate(sessionDate);
  const resolvedSessionMatches = preflight?.window.sessions.length === 1 && preflight.window.sessions[0] === sessionDate;
  const selectedPlan = configurationOptions?.candidate_id === candidateId
    ? configurationOptions.available_run_plans.find((plan) => plan.run_plan_id === runPlanId) : undefined;
  const setupKey = JSON.stringify([candidateId, runPlanId, sessionDate, startTime, endTime, normalizedTickers, structureBook, tickerPreset, refreshKey]);
  const currentPreflight = checkedSetupKey === setupKey && preflight?.configuration_revision_id === candidateId && preflight.run_plan_id === runPlanId;

  useEffect(() => {
    if (!selectedRunId) return;
    persistSelectedRun(selectedRunId);
    if (run?.run_id === selectedRunId) return;
    const controller = new AbortController();
    setRestoreError("");
    setRestoreFailed(false);
    void recoverBacktest<BacktestRun>(selectedRunId, controller.signal)
      .then((value) => { if (!controller.signal.aborted) setRun(value); })
      .catch((reason) => {
        if (!controller.signal.aborted) {
          setRestoreFailed(reason instanceof FailedBacktestError);
          setRestoreError(reason instanceof Error ? reason.message : String(reason));
        }
      });
    return () => controller.abort();
  }, [selectedRunId, restoreAttempt]);

  function returnToSetup() {
    persistSelectedRun("");
    setSelectedRunId("");
    setRun(null);
    setRestoreError("");
  }

  useEffect(() => {
    if (selectedRunId) return;
    const requestKey = `${candidateId}:${refreshKey}`;
    if (resolvedOptionsRequest.current === requestKey) {
      resolvedOptionsRequest.current = null;
      setLoadingOptions(false);
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setLoadingOptions(true);
    setOptionsError("");
    // Defer dispatch so React's development setup/cleanup probe cannot send
    // an immediately aborted duplicate request to the backend.
    const timer = window.setTimeout(() => {
      api<BacktestConfigurationOptions>(`/api/trading/backtest/configuration-options?candidate_id=${encodeURIComponent(candidateId)}`, { signal: controller.signal, timeoutMs: 60_000 })
        .then((payload) => {
          if (cancelled) return;
          if (payload.candidate_id !== candidateId) {
            resolvedOptionsRequest.current = `${payload.candidate_id}:${refreshKey}`;
          }
          setConfigurationOptions(payload);
          setCandidateId(payload.candidate_id);
          setRunPlanId((current) => payload.available_run_plans.some((plan) => plan.run_plan_id === current) ? current : payload.run_plan_id);
          setOptionsError(payload.error);
        })
        .catch((reason) => { if (!cancelled) setOptionsError(reason instanceof Error ? reason.message : String(reason)); })
        .finally(() => { if (!cancelled) setLoadingOptions(false); });
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timer); controller.abort(); };
  }, [candidateId, refreshKey, selectedRunId]);

  useEffect(() => {
    if (selectedRunId) return;
    if (!tickerReady || fullMarket) {
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
  }, [normalizedTickers, refreshKey, sessionDate, tickerReady, selectedRunId, fullMarket]);

  useEffect(() => {
    if (selectedRunId) return;
    if (!candidateId || !selectedPlan || loadingOptions || optionsError || !tickerReady || (!fullMarket && indicatorWarmup?.status !== "ready")) {
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
  }, [anchorDate, candidateId, endTime, indicatorWarmup?.status, loadingOptions, mode, normalizedTickers, optionsError, refreshKey, runPlanId, selectedPlan, setupKey, startTime, tickerReady, selectedRunId, fullMarket]);

  usePollingTask({
    enabled: Boolean(run && (run.work_progress?.active || !["completed", "stopped", "failed"].includes(run.status))),
    intervalMs: 1_000,
    onError: (reason) => setError(reason instanceof Error ? reason.message : String(reason)),
    restartKey: run?.run_id,
    task: async (signal) => {
      if (!run) return;
      const update = await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}?compact=true`, { signal, timeoutMs: 20_000 });
      if (!signal.aborted) {
        // The pinned profile is immutable. Preserve its object identity so a
        // progress tick does not rebuild and persist the whole workspace.
        setRun((current) => current?.run_id === update.run_id
          ? { ...current, ...update, canvas_profile: current.canvas_profile }
          : update);
        setError("");
      }
    },
  });

  useEffect(() => {
    if (selectedRunId || !run || !["completed", "stopped", "failed"].includes(run.status)) return;
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
      const jobs = batchPreset ? normalizedTickers.map(ticker => ({tickers:[ticker],
        book:v6BookFor(ticker,sessionDate,structureBooks)?.id ?? '', start:startTime,end:endTime}))
        : [{tickers:normalizedTickers,book:structureBook,start:startTime,end:endTime}];
      if (tickerPreset !== 'custom' && jobs.some(job => !job.book)) throw Error('A matching V7 book is required for every preset ticker.');
      const createdRuns: BacktestRun[] = [];
      for (const job of jobs) {
        const created = await api<BacktestRun>("/api/trading/backtest/runs", {
          body: JSON.stringify({
            anchor_date: anchorDate,
            configuration_revision_id: candidateId,
            initial_cash: initialCash,
            run_plan_id: runPlanId,
            session_count: 1,
            simulation_profile: simulationProfile,
            experimental_structure_book: job.book,
            minimum_p_norm: minimumPNorm,
            start_time: job.start,
            end_time: job.end,
            tickers: job.tickers,
            ...(fullMarket ? { new_order_activation_delay_ms: 0 } : {}),
          }),
          method: "POST",
          timeoutMs: fullMarket ? 180_000 : 60_000,
        });
        createdRuns.push(created);
        setBatchRuns([...createdRuns]);
      }
      const created = createdRuns[0];
      setResults(null);
      setComparison(null);
      setComparisonError("");
      setRun(created);
      persistSelectedRun(created.run_id);
      setSelectedRunId(created.run_id);
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
      setRun(await api<BacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(run.run_id)}/resume`, { method: "POST", timeoutMs: 180_000 }));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setControlBusy("");
    }
  }

  if (run) {
    const terminal = ["completed", "stopped", "failed"].includes(run.status);
    const warming = !terminal && (run.status === "warming" || run.runtime_ready === false);
    const work = run.work_progress;
    const checkpointing = Boolean(work?.active && (work.phase.startsWith('checkpoint_') || work.phase === 'finalizing'));
    const workLabel = checkpointing ? work?.phase === 'finalizing' ? 'Finalizing backtest' : work?.phase === 'checkpoint_capture' ? 'Capturing checkpoint' : 'Saving checkpoint' : warming ? 'Preparing backtest' : `Backtest ${run.status.replaceAll("_", " ")}`;
    const preparation = run.preparation_progress;
    const progressKnown = !checkpointing && (!warming || Boolean(preparation && preparation.total > 0));
    const phaseProgress = warming
      ? preparation && preparation.total > 0 ? preparation.completed / preparation.total : 0
      : run.progress;
    const progressPercent = Math.round(Math.max(0, Math.min(1, phaseProgress || 0)) * 100);
    const progressLabel = warming ? "Backtest warm-up" : "Backtest";
    const runScope = run.tickers?.length ? run.tickers.join(", ") : "Configured strategy universe";
    return <CanvasWorkspaceSurface
      canvasId="main"
      manager={false}
      modeControls={<div className="historical-canvas-run-state historical-backtest-progress">
        {batchRuns.length > 1 ? <TradingModeSelectField label="Batch run" help="Each ticker has its own portfolio, time window and V7 book." value={run.run_id}
          options={batchRuns.map(item => ({value:item.run_id,label:(item.tickers ?? []).join(', ')}))}
          onChange={id => { setRun(null); setSelectedRunId(id); persistSelectedRun(id); }} /> : null}
        <div className="historical-backtest-progress-actions"><span className="historical-backtest-engine" title="Accelerated causal engine"><Zap aria-hidden="true" size={11} /><span>Accelerated causal engine</span></span><button className="button secondary compact" aria-haspopup="dialog" onClick={() => setDetailsOpen(true)} type="button">Details{run.level_book_coverage?.excluded_ticker_count ? ` · ${run.level_book_coverage.excluded_ticker_count} excluded` : ""}</button><button aria-label="Return to Backtest setup" className="button secondary compact" onClick={returnToSetup} type="button"><ArrowLeft size={14} /> Setup</button>{terminal && run.status !== "completed" && run.checkpoint?.resume_supported ? <button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void resumeRun()} type="button"><Play size={14} />{controlBusy === "resume" ? "Resuming…" : "Resume from checkpoint"}</button> : null}{!terminal ? <><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void commandRun(run.status === "paused" ? "play" : "pause")} type="button">{run.status === "paused" ? <Play size={14} /> : <Pause size={14} />}{run.status === "paused" ? "Resume" : "Pause"}</button><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void stopRun()} type="button"><Square size={14} /> Stop</button></> : null}</div>
        <div className="historical-backtest-progress-heading"><strong>{warming || checkpointing ? <LoaderCircle aria-hidden="true" className="spin" size={12} /> : null} {workLabel}</strong><b>{checkpointing ? `${Math.floor(work?.elapsed_seconds ?? 0)}s` : progressKnown ? `${progressPercent}%` : "Preparing"}</b></div>

        <div aria-label={`${progressLabel} progress`} aria-valuemax={100} aria-valuemin={0} aria-valuenow={progressKnown ? progressPercent : undefined} aria-valuetext={checkpointing ? `${workLabel} - ${Math.floor(work?.elapsed_seconds ?? 0)} seconds` : warming && preparation?.total ? `${preparation.completed.toLocaleString()} of ${preparation.total.toLocaleString()} prepared · ${progressPercent}%` : progressKnown ? `${progressPercent}%` : "Preparing"} className="historical-backtest-progress-track" role="progressbar"><span style={{ width: `${progressPercent}%` }} /></div>
        <div className="historical-backtest-progress-facts" role="status">{checkpointing ? <><span>{work?.stop_requested ? 'Stop requested · finishing durable checkpoint' : 'Engine held at a causal boundary'}</span><span>Progress updates remain available</span></> : warming ? <>
          <span>{run.preparation_stage === "strategy_frames" ? "Strategy streams" : run.preparation_stage?.replaceAll("_", " ") || "Preparing"}</span>
          <span>{progressKnown && preparation ? `${preparation.completed.toLocaleString()} / ${preparation.total.toLocaleString()} prepared` : "Waiting for preparation totals"}</span>
        </> : <><span>{new Intl.NumberFormat("en-US").format(run.processed_events || 0)} exact events</span><span>Through {formatReplayTime(run.current_time)} ET</span><span>{runScope}</span></>}</div>
        {detailsOpen ? <Modal title="Backtest preparation" onClose={() => setDetailsOpen(false)} closeOnBackdrop className="backtest-preparation-modal">
          <div className="backtest-preparation-content">
            <dl><div><dt>Stage</dt><dd>{work?.phase.replaceAll('_', ' ') || run.preparation_stage?.replaceAll('_', ' ') || run.status}</dd></div>
              <div><dt>Prepared streams</dt><dd>{run.preparation_progress?.completed.toLocaleString() ?? '—'} / {run.preparation_progress?.total.toLocaleString() ?? '—'}</dd></div>
              <div><dt>Eligible tickers</dt><dd>{run.level_book_coverage?.eligible_ticker_count.toLocaleString() ?? '—'}</dd></div></dl>
            <h3>Excluded tickers · {run.level_book_coverage?.excluded_ticker_count ?? 0}</h3>
            {run.level_book_coverage?.excluded.length ? <div className="backtest-exclusion-table"><table><thead><tr><th>Ticker</th><th>Session</th><th>Reason</th></tr></thead><tbody>
              {run.level_book_coverage.excluded.map(row => <tr key={`${row.ticker}:${row.session}`}><td>{row.ticker}</td><td>{row.session}</td><td>{row.reason}</td></tr>)}
            </tbody></table></div> : <p>No exclusions reported.</p>}
          </div>
        </Modal> : null}
        {error ? <div className="canvas-inline-error" role="alert">{error}</div> : null}
      </div>}
      replayRun={run}
      runtimeWorkspaceId="main"
    />;
  }

  if (selectedRunId && restoreFailed) return <div className="canvas-config-page"><BacktestRecoveryFailure error={restoreError} onSetup={returnToSetup} /></div>;
  if (selectedRunId) return <BacktestRecoveryState error={restoreError}
    onRetry={() => { setRestoreError(""); setRestoreAttempt(value => value + 1); }} onSetup={returnToSetup} />;

  const warmupCheck: HistoricalCheck = {
    id: "indicator_warmup",
    label: fullMarket ? "Indicator preparation" : "1-second indicator warm-up",
    required: true,
    status: fullMarket || indicatorWarmup?.status === "ready" ? "ready" : "blocked",
    summary: fullMarket ? "The backend prepares causal indicator history for admitted tickers before simulation starts." : indicatorWarmup?.status === "ready"
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
  const requiresV7 = selectedPlan?.profile_id === 'v6-structural-support-recovery';
  const selectedBook = structureBooks.find(book => book.id === structureBook);
  const booksReady = fullMarket ? structureBooks.length > 0 : batchPreset
    ? normalizedTickers.length > 0 && normalizedTickers.every(ticker => Boolean(v6BookFor(ticker,sessionDate,structureBooks)))
    : selectedBook
      ? normalizedTickers.length === 1 && selectedBook.ticker === normalizedTickers[0]
        && selectedBook.start <= sessionDate && sessionDate <= selectedBook.end
        && (!requiresV7 || selectedBook.version === 'causal-level-book-v7-mle-1')
      : normalizedTickers.length > 0 && normalizedTickers.every(ticker => Boolean(v6BookFor(ticker,sessionDate,structureBooks)));
  const launchChecks = [configurationCheck, warmupCheck, {id:'preset_books',label:fullMarket ? 'V7 coverage policy' : 'V7 book coverage',required:true,
    status:booksReady ? 'ready' as const : 'blocked' as const, summary:fullMarket ? 'V7 catalog available. Before strategy preparation, the backend verifies preceding-session books and logs excluded tickers.' : booksReady ? 'Matching books available.' : 'A published V7 book covering this date is required for each selected ticker.',evidence:normalizedTickers}, ...(currentPreflight ? preflight?.checks ?? [] : [])];
  const launchReady = Boolean(booksReady && currentPreflight && selectedPlan && !loadingOptions && !optionsError && preflight?.strategy_run_ready && (fullMarket || indicatorWarmup?.status === "ready") && tickerReady && periodReady && resolvedSessionMatches);

  return (
    <TradingModeLaunch
      actionLabel={fullMarket ? 'Run Full-market Backtest' : batchPreset ? `Run ${normalizedTickers.length} Backtests` : 'Run Backtest'}
      actionSummary={batchPreset ? `Creates one separate portfolio run per ticker on ${sessionDate}, each with its V7 book. Every ticker uses ${startTime.slice(0,5)}–${endTime.slice(0,5)} ET.` : launchReady ? <><strong>{fullMarket ? 'The Run Plan’s signal-admitted market' : normalizedTickers.join(", ")}</strong> will run together on <strong>{sessionDate}</strong> from <strong>{startTime.slice(0, 5)}–{endTime.slice(0, 5)} ET</strong> using one shared simulated portfolio and strategy revision <strong>{selectedPlan?.strategy_revision}</strong> (candidate {preflight?.configuration_revision}).</> : !tickerReady ? parsedTickers.invalid.length ? `Remove invalid ticker${parsedTickers.invalid.length === 1 ? "" : "s"}: ${parsedTickers.invalid.join(", ")}.` : "Enter at least one valid ticker before starting." : warmingIndicators ? "Preparing persisted 1-second indicator warm-ups." : !periodReady ? "Choose a valid period inside 04:00–20:00 ET." : preflight && !resolvedSessionMatches ? "The selected date is not an exchange session. Choose a trading day." : "Resolve each required readiness item before starting."}
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
      secondary={<><BacktestRunHistory
        onReview={id => { setBatchRuns([]); setRun(null); setSelectedRunId(id); persistSelectedRun(id); }}
        onResumed={value => { setBatchRuns([]); setRun(value as BacktestRun); setSelectedRunId(value.run_id); persistSelectedRun(value.run_id); }}
      />{results ? <HistoricalResults comparison={comparison} comparisonError={comparisonError} results={results} /> : null}</>}
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
              <TradingModeSelectField label="Ticker preset" searchable value={tickerPreset} onChange={chooseTickerPreset}
                options={[{value:'market',label:'Full market · shared portfolio',description:'The selected Run Plan’s signals admit tickers causally into one portfolio'}, {value:'SUGP',label:'SUGP'},{value:'JUNS',label:'JUNS'},
                  {value:'both',label:'SUGP and JUNS · separate runs'},{value:'all',label:'All V7 tickers · separate runs',description:'Separate portfolio per ticker; up to 100 tickers'},
                  {value:'custom',label:'Custom tickers'}]} help={fullMarket ? 'The selected Run Plan controls ticker admission; cash is shared across tickers.' : batchPreset ? 'Separate runs use the selected period and a V7 book for each ticker.' : 'Select a ticker to load its V7 book; the selected period is preserved.'} />
              {fullMarket ? <p className="configuration-help">Tickers enter causally through the selected Run Plan's signals and share portfolio cash. The selected date must have prepared historical signals. Missing V7 books are excluded and reported before strategy preparation.</p> : null}
              {tickerPreset === 'custom' ? <label className="configuration-field"><span>Tickers</span><textarea aria-label="Tickers" value={tickerInput} onChange={event => setTickerInput(event.target.value.toUpperCase())} /><small>Up to 100 symbols, separated by commas or spaces.</small></label> : null}
              {batchPreset ? <div className="configuration-help">{normalizedTickers.map(ticker => <p key={ticker}>{ticker} · {startTime.slice(0,5)}–{endTime.slice(0,5)} ET · {v6BookFor(ticker,sessionDate,structureBooks) ? 'V7 book selected' : 'V7 book unavailable'}</p>)}</div> : null}
              {batchRuns.length ? <div className="configuration-help">Created runs: {batchRuns.map(item => <button type="button" className="button secondary compact" key={item.run_id} onClick={() => {setSelectedRunId(item.run_id);persistSelectedRun(item.run_id);}}>{item.tickers?.join(', ')} · {item.run_id.slice(0,8)}</button>)}</div> : null}
              <label className="configuration-field"><span>Trading date</span><input onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} /><small>Must be an exchange trading session; weekends and holidays fail closed.</small></label>
              <TradingModeSelectField help="Presets bound the decision window while retaining causal warm-up evidence." label="Time period" onChange={(value) => applyPeriodPreset(value as BacktestPeriodPreset, setPeriodPreset, setStartTime, setEndTime)} options={[{ label: "Premarket · 04:00–09:30 ET", value: "premarket" }, { label: "Regular session · 09:30–16:00 ET", value: "regular" }, { label: "After hours · 16:00–20:00 ET", value: "after_hours" }, { label: "Whole extended session · 04:00–20:00 ET", value: "extended" }, { label: "Custom period", value: "custom" }]} value={periodPreset} />
              <label className="configuration-field"><span>Start time · ET</span><input aria-label="Start time" max="19:59:59" min="04:00:00" onChange={(event) => { setPeriodPreset("custom"); setStartTime(normalizeClockInput(event.target.value)); }} step="1" type="time" value={startTime} /><small>No new strategy actions are admitted before this time.</small></label>
              <label className="configuration-field"><span>End time · ET</span><input aria-label="End time" max="20:00:00" min="04:00:01" onChange={(event) => { setPeriodPreset("custom"); setEndTime(normalizeClockInput(event.target.value)); }} step="1" type="time" value={endTime} /><small>The run stops at this exact New York boundary.</small></label>
              <label className="configuration-field"><span>Initial cash</span><input max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} /><small>Applied to the isolated simulated account for the full run.</small></label>
              <TradingModeSelectField disabled={batchPreset || fullMarket} label="Level book" help="V7 loads each ticker's verified preceding-session checkpoint and refits adaptive MLE bands from causal completed 1s candles."
                value={structureBook} onChange={(value) => { setTickerPreset('custom'); setStructureBook(value); const book = structureBooks.find((row) => row.id === value); if (book) setTickerInput(book.ticker); }}
                options={fullMarket ? [{ label: 'Automatic V7 coverage check per ticker', value: 'level-book-v7' }] : [{ label: "Automatic V7 book per ticker", value: "" }, ...structureBooks.map((row) => ({ label: `Level book V7 - ${row.ticker} - ${row.start} to ${row.end}`, value: row.id }))]} />
              <p className="configuration-help">V7 uses fitted reaction-price bands. Checkpoint integrity and exact preceding-session coverage are verified when loading; no legacy book is substituted.</p>
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
    after_hours: ["16:00:00", "20:00:00"],
    extended: ["04:00:00", "20:00:00"],
  }[preset];
  setStart(period[0]);
  setEnd(period[1]);
}
