import {
  ArrowLeft,
  CheckCircle2,
  CircleStop,
  Clock3,
  Database,
  FastForward,
  Gauge,
  Pause,
  Play,
  RefreshCcw,
  ShieldCheck,
  SkipForward,
  Sparkles,
  TriangleAlert,
  WalletCards,
} from "lucide-react";
import { useEffect, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { MAIN_CANVAS_ID } from "../app/canvasWorkspace";
import { TradingModeLaunch } from "../app/components/TradingModeLaunch";
import { isTerminalReplayStatus, latestReplayRun, useReplayRunEvents, type CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

type ReplayCheck = {
  action?: { hash?: string; label?: string };
  evidence: string;
  id: string;
  label: string;
  required: boolean;
  status: "blocked" | "ready";
  summary: string;
};

type ReplayAssignment = {
  account_key: string;
  assignment_id: string;
  status: string;
  ticker: string;
};

type ReplayPreflight = {
  account_mapping: Record<string, string>;
  assignments: ReplayAssignment[];
  canvas_revision: string;
  checks: ReplayCheck[];
  coverage: { event_count?: number; ticker_count?: number };
  configuration_content_hash: string;
  configuration_label: string;
  configuration_release_state: "approved" | "test_candidate";
  configuration_revision: number;
  configuration_revision_id: string;
  canvas_profile: Record<string, unknown>;
  ready: boolean;
  run_plan_id: string;
  execution_mode: "manual" | "strategy";
  session_profile_id?: string;
  execution_route_id?: string;
  available_run_plans: Array<{ name: string; profile_id: string; run_plan_id: string; strategy_id: string; strategy_revision: number }>;
  tickers: string[];
};

type ReplayRunSummary = Pick<CanvasReplayRun, "checkpoint" | "current_time" | "progress" | "run_id" | "session_date" | "status"> & {
  resident: boolean;
};

const REPLAY_SPEEDS = [1, 5, 30, 120, 0] as const;

export function ReplayTradingPage() {
  const [sessionDate, setSessionDate] = useState("");
  const [startTime, setStartTime] = useState("04:00");
  const [initialCash, setInitialCash] = useState(10_000);
  const [preflight, setPreflight] = useState<ReplayPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [run, setRun] = useState<CanvasReplayRun | null>(null);
  const [preparingRun, setPreparingRun] = useState<CanvasReplayRun | null>(null);
  const [recentRuns, setRecentRuns] = useState<ReplayRunSummary[]>([]);
  const [runPlanId, setRunPlanId] = useState("");
  const [executionMode, setExecutionMode] = useState<"manual" | "strategy">("strategy");
  const [symbol, setSymbol] = useState("AAPL");
  const replayReady = Boolean(preflight?.ready);
  const selectedRunPlanId = runPlanId || preflight?.run_plan_id || "";

  useEffect(() => {
    let cancelled = false;
    api<{ session_date?: string }>("/api/trading/canvas-context", { timeoutMs: 5_000 })
      .then((payload) => {
        if (!cancelled) setSessionDate(payload.session_date || previousWeekdayIsoDate());
      })
      .catch(() => {
        if (!cancelled) setSessionDate(previousWeekdayIsoDate());
      });
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    const refreshOnEntry = () => {
      if (window.location.hash === "#replay-trading") {
        setRefreshKey((value) => value + 1);
      }
    };
    window.addEventListener("hashchange", refreshOnEntry);
    window.addEventListener("quant-trading-configuration-published", refreshOnEntry);
    return () => {
      window.removeEventListener("hashchange", refreshOnEntry);
      window.removeEventListener("quant-trading-configuration-published", refreshOnEntry);
    };
  }, []);

  useEffect(() => {
    if (run) return;
    api<{ rows: ReplayRunSummary[] }>("/api/trading/replay/runs", { timeoutMs: 20_000 })
      .then((payload) => setRecentRuns(payload.rows.slice(0, 5)))
      .catch(() => setRecentRuns([]));
  }, [refreshKey, run]);

  useEffect(() => {
    if (run) return;
    if (!sessionDate) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<ReplayPreflight>("/api/trading/replay/preflight", {
        body: JSON.stringify({
          configuration_revision_id: preflight?.configuration_revision_id ?? "",
          initial_cash: initialCash,
          execution_mode: executionMode,
          run_plan_id: runPlanId,
          session_date: sessionDate,
          start_time: startTime,
          tickers: executionMode === "manual" && symbol.trim() ? [symbol.trim().toUpperCase()] : [],
        }),
        method: "POST",
        timeoutMs: 60_000,
      })
        .then((payload) => {
          if (!cancelled) {
            setPreflight(payload);
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
  }, [executionMode, initialCash, refreshKey, run, runPlanId, sessionDate, startTime, symbol]);

  useReplayRunEvents(
    run?.run_id,
    (update) => setRun((current) => latestReplayRun(current, update)),
    setError,
  );

  useReplayRunEvents(
    preparingRun?.run_id,
    (update) => setPreparingRun((current) => latestReplayRun(current, update)),
    setError,
  );

  useEffect(() => {
    if (!preparingRun) return;
    if (replayPrepared(preparingRun)) {
      setRun(preparingRun);
      setPreparingRun(null);
      return;
    }
    if (isTerminalReplayStatus(preparingRun.status)) {
      setError(preparingRun.error || `Replay preparation ${preparingRun.status}.`);
      setPreparingRun(null);
    }
  }, [preparingRun]);

  async function createRun() {
    if (!replayReady) return;
    setCreating(true);
    setError("");
    try {
      const created = await api<CanvasReplayRun>("/api/trading/replay/runs", {
        body: JSON.stringify({
          configuration_revision_id: preflight?.configuration_revision_id,
          initial_cash: initialCash,
          execution_mode: executionMode,
          run_plan_id: selectedRunPlanId,
          session_date: sessionDate,
          start_time: startTime,
          tickers: executionMode === "manual" && symbol.trim() ? [symbol.trim().toUpperCase()] : [],
        }),
        method: "POST",
        timeoutMs: 60_000,
      });
      if (replayPrepared(created)) setRun(created);
      else setPreparingRun(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreating(false);
    }
  }

  async function openRecentRun(recent: ReplayRunSummary) {
    setCreating(true);
    setError("");
    try {
      const loaded = await api<CanvasReplayRun>(`/api/trading/replay/runs/${encodeURIComponent(recent.run_id)}`, { timeoutMs: 20_000 });
      if (replayPrepared(loaded)) setRun(loaded);
      else setPreparingRun(loaded);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreating(false);
    }
  }

  async function cancelPreparation() {
    if (!preparingRun) return;
    setError("");
    try {
      await api<CanvasReplayRun>(`/api/trading/replay/runs/${encodeURIComponent(preparingRun.run_id)}/commands`, {
        body: JSON.stringify({ command: "stop" }),
        method: "POST",
      });
      setPreparingRun(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  }

  if (run) {
    return (
      <CanvasWorkspaceSurface
        canvasId={MAIN_CANVAS_ID}
        manager={false}
        modeControls={<ReplayControls onExit={() => setRun(null)} onRunChange={(update) => setRun((current) => latestReplayRun(current, update))} run={run} />}
        replayRun={run}
      />
    );
  }

  return (
    <TradingModeLaunch
      actionLabel="Open Replay Canvas"
      actionSummary={<>{preflight?.configuration_release_state === "test_candidate" ? "The immutable Test Candidate" : "The approved revision"} is pinned to a durable simulated run. Replay opens paused at <strong>{sessionDate} · {startTime} ET</strong>.</>}
      busy={creating || Boolean(preparingRun)}
      busyLabel={creating ? "Starting preparation…" : "Preparing Replay…"}
      checking={checking}
      checks={preflight?.checks ?? []}
      description="Practice manual and strategy-assisted trading against the historical quote and trade sequence. Earlier events warm the workspace causally."
      error={error}
      eyebrow="Replay"
      icon={FastForward}
      onAction={createRun}
      onRefresh={() => setRefreshKey((value) => value + 1)}
      ready={replayReady}
      title="Open a historical session"
      secondary={preparingRun || recentRuns.length ? <>{preparingRun ? <ReplayPreparation onCancel={() => void cancelPreparation()} run={preparingRun} /> : null}{recentRuns.length ? <details className="mode-launch-history"><summary><span>Recent runs</span><small>Reopen a run owned by this backend session</small></summary><div>{recentRuns.map((recent) => <button disabled={Boolean(preparingRun) || recent.resident === false || isTerminalReplayStatus(recent.status)} key={recent.run_id} onClick={() => void openRecentRun(recent)} type="button"><span><strong>{recent.session_date}</strong><small>{formatReplayClock(recent.current_time)} ET · {recent.status.replaceAll("_", " ")}</small></span><em>{Math.round(recent.progress * 100)}%</em></button>)}</div></details> : null}</> : null}
    >
              <label className="configuration-field">
                <span>Execution</span>
                <select aria-label="Replay execution mode" disabled={Boolean(preparingRun)} onChange={(event) => setExecutionMode(event.target.value as "manual" | "strategy")} value={executionMode}><option value="manual">Manual / trading actions</option><option value="strategy">Strategy deployment</option></select>
                <small>Manual uses the Session Profile directly. Strategy adds a published Run Plan.</small>
              </label>
              {executionMode === "strategy" ? <label className="configuration-field">
                <span>Strategy Run Plan</span>
                <select aria-label="Strategy Run Plan" disabled={Boolean(preparingRun)} onChange={(event) => setRunPlanId(event.target.value)} value={selectedRunPlanId}>{(preflight?.available_run_plans ?? []).map((plan) => <option key={plan.run_plan_id} value={plan.run_plan_id}>{plan.name} · {plan.strategy_id} r{plan.strategy_revision}</option>)}</select>
                <small>Its Signal Streams and causal Watchlists own the market-wide ticker population.</small>
              </label> : <label className="configuration-field"><span>Starting symbol</span><input aria-label="Replay symbol" disabled={Boolean(preparingRun)} maxLength={12} onChange={(event) => setSymbol(event.target.value.toUpperCase())} value={symbol} /><small>The Canvas may change symbols later; this only seeds the manual historical event stream.</small></label>}
              <label className="configuration-field">
                <span>Exchange date</span>
                <input disabled={Boolean(preparingRun)} onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} />
                <small>One 04:00–20:00 New York session.</small>
              </label>
              <label className="configuration-field">
                <span>Enter Canvas at</span>
                <input disabled={Boolean(preparingRun)} max="20:00" min="04:00" onChange={(event) => setStartTime(event.target.value)} step="1" type="time" value={startTime} />
                <small>Canvas opens paused at this event-time clock.</small>
              </label>
              <label className="configuration-field">
                <span>Initial cash</span>
                <input disabled={Boolean(preparingRun)} max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} />
                <small>Applied to the Session Profile's simulated account and Portfolio boundaries.</small>
              </label>
    </TradingModeLaunch>
  );
}

function ReplayPreparation({ onCancel, run }: { onCancel: () => void; run: CanvasReplayRun }) {
  const [wallClock, setWallClock] = useState(() => Date.now());
  useEffect(() => {
    setWallClock(Date.now());
    const timer = window.setInterval(() => setWallClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [run.run_id]);
  const presentation = replayPreparationPresentation(run);
  const elapsed = Math.max(0, Math.floor((wallClock - Date.parse(run.created_at || run.updated_at)) / 1_000));
  const cache = run.preparation_cache?.strategy_frames;
  const cacheLabel = cache === "hit" || cache === "run_checkpoint"
    ? "Prepared frame cache reused"
    : cache === "miss"
      ? "Building restart-safe frame cache"
      : cache === "not_required" || cache === "fixture"
        ? "Frame cache not required"
        : "Frame cache check follows Watchlist prep";
  return <section aria-live="polite" className="replay-preparation-card" role="status">
    <header><span><Sparkles aria-hidden="true" size={15} /><strong>Preparing Replay before Canvas opens</strong></span><em>{formatElapsed(elapsed)}</em></header>
    <div className="replay-preparation-body">
      <div><strong>{presentation.source}</strong><small>{presentation.progressLabel} · {cacheLabel}</small></div>
      <progress aria-label={`${presentation.source} progress`} max={presentation.total || undefined} value={presentation.total ? Math.min(presentation.completed, presentation.total) : undefined} />
      <p>The historical signal, Watchlist, strategy-frame, and market-stream authorities are being pinned now. Canvas opens only after the run reaches a usable event-time state.</p>
    </div>
    <button className="button secondary compact" onClick={onCancel} type="button"><CircleStop aria-hidden="true" size={13} /> Cancel preparation</button>
  </section>;
}

function ReplayControls({ onExit, onRunChange, run }: { onExit: () => void; onRunChange: (run: CanvasReplayRun) => void; run: CanvasReplayRun }) {
  const [busy, setBusy] = useState("");
  const [controlError, setControlError] = useState("");
  const [wallClock, setWallClock] = useState(() => Date.now());
  const terminal = isTerminalReplayStatus(run.status);
  const active = ["running", "fast_forwarding"].includes(run.status);
  const navigationActive = run.navigation_search?.active === true;
  const navigationPreparing = navigationActive && run.navigation_search?.phase === "preparing";
  const runtimePreparing = !terminal && !navigationActive && run.runtime_ready !== true;
  const transportPreparing = active && runtimePreparing;
  const preparingLabel = run.transport_mode === "fast_forward" ? "Preparing time jump" : run.transport_mode === "step" ? "Preparing one-second step" : "Preparing simulation";
  const preparation = replayPreparationPresentation(run);
  const preparationCompleted = preparation.completed;
  const preparationTotal = preparation.total;
  const preparationSource = preparation.source;
  const preparingDetail = run.transport_mode === "fast_forward" ? `+5 minutes queued · ${preparationSource}` : run.transport_mode === "step" ? `One-second step queued · ${preparationSource}` : `Play queued · ${preparationSource}`;
  useEffect(() => {
    if (!navigationActive) return;
    setWallClock(Date.now());
    const timer = window.setInterval(() => setWallClock(Date.now()), 1_000);
    return () => window.clearInterval(timer);
  }, [navigationActive, run.navigation_search?.started_at]);

  async function command(name: string, payload: Record<string, unknown> = {}) {
    setBusy(name);
    setControlError("");
    try {
      const updated = await api<CanvasReplayRun>(`/api/trading/replay/runs/${encodeURIComponent(run.run_id)}/commands`, {
        body: JSON.stringify({ command: name, ...payload }),
        method: "POST",
      });
      onRunChange(updated);
      return updated;
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : String(reason));
      return null;
    } finally {
      setBusy("");
    }
  }

  async function resumeCheckpoint() {
    setBusy("resume");
    setControlError("");
    try {
      onRunChange(await api<CanvasReplayRun>(`/api/trading/replay/runs/${encodeURIComponent(run.run_id)}/resume`, { method: "POST" }));
    } catch (reason) {
      setControlError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setBusy("");
    }
  }

  return <div className="replay-canvas-controls" aria-label="Replay controls">
    <button aria-label="Return to Replay setup" className="replay-control-back" onClick={async () => {
      if (!terminal && active) await command("pause");
      onExit();
    }} title="Replay setup" type="button"><ArrowLeft size={14} /></button>
    <button aria-label={terminal ? "Resume Replay checkpoint" : active ? "Pause Replay" : "Play Replay"} className="replay-control-primary" disabled={(terminal && !run.checkpoint?.resume_supported) || Boolean(busy)} onClick={() => terminal ? resumeCheckpoint() : command(active ? "pause" : "play")} title={terminal ? "Resume from the latest durable checkpoint" : active ? "Pause the simulation" : "Continuously process QMD events at the selected speed"} type="button">{active ? <Pause size={14} /> : <Play size={14} />}</button>
    <button aria-label="Advance one event-time second" className="replay-control-button" disabled={terminal || Boolean(busy)} onClick={() => command("step", { step_seconds: 1 })} title="Step one second" type="button"><SkipForward size={13} /></button>
    <button aria-label="Fast-forward to the next strategy action" className="replay-control-action" data-active={navigationActive || undefined} disabled={terminal || Boolean(busy) || navigationActive} onClick={() => command("next_action")} title="Search causally for a watch start, strategy decision, or order update, then pause" type="button"><Sparkles size={13} /><span>{busy === "next_action" ? "Starting…" : navigationPreparing ? "Preparing…" : navigationActive ? "Scanning…" : "Next strategy action"}</span></button>
    <button aria-label="Advance five event-time minutes and pause" className="replay-control-button replay-control-jump" disabled={terminal || Boolean(busy)} onClick={() => {
      const current = new Date(run.current_time);
      const end = new Date(run.session_end);
      const targetDate = new Date(Math.min(end.getTime(), current.getTime() + 5 * 60_000));
      const parts = new Intl.DateTimeFormat("en-CA", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).formatToParts(targetDate);
      const value = (kind: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === kind)?.value ?? "00";
      const target = `${value("hour")}:${value("minute")}:${value("second")}`;
      void command("fast_forward", { target_time: target });
    }} title="Process every QMD event through the next five event-time minutes, then pause" type="button"><FastForward size={13} /><span>+5 min</span></button>
    <label className="replay-speed-control"><Gauge aria-hidden="true" size={13} /><select aria-label="Replay speed" disabled={terminal || Boolean(busy)} onChange={(event) => command("set_speed", { speed: Number(event.target.value) })} value={run.speed}>{REPLAY_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed === 0 ? "Max" : `${speed}×`}</option>)}</select></label>
    <div className={`replay-run-state${navigationActive || runtimePreparing ? " is-navigation" : ""}`} data-status={run.status}>
      <i aria-hidden="true" />
      <span>
        <strong aria-live="polite">{navigationPreparing ? "Preparing causal scan" : navigationActive ? "Finding next action" : runtimePreparing ? transportPreparing ? preparingLabel : "Preparing Replay" : run.status.replaceAll("_", " ")}</strong>
        <small>{navigationPreparing ? `Loading signal + Watchlist history · ${navigationElapsedSeconds(run.navigation_search?.started_at, wallClock)}s` : navigationActive ? `${compactEventCount(run.navigation_search?.scanned_events)} market events · ${formatReplayClock(run.current_time)} ET · ${navigationElapsedSeconds(run.navigation_search?.started_at, wallClock)}s` : runtimePreparing ? transportPreparing ? preparingDetail : preparationSource : run.navigation_action ? `${formatReplayClock(run.navigation_action.event_time)} ET · ${run.navigation_action.ticker ? `${run.navigation_action.ticker} · ` : ""}${run.navigation_action.label}` : run.status === "warming" ? `${compactEventCount(run.warmup_events)} events` : `${Math.round(run.progress * 100)}%`}</small>
        {runtimePreparing ? <progress aria-label={`${preparationSource} progress`} max={preparationTotal || undefined} value={preparationTotal ? Math.min(preparationCompleted, preparationTotal) : undefined} /> : null}
      </span>
    </div>
    {controlError ? <span className="replay-control-error" title={controlError}>Control failed</span> : null}
  </div>;
}

function replayPrepared(run: CanvasReplayRun) {
  return run.runtime_ready === true && !["created", "warming"].includes(run.status);
}

function replayPreparationPresentation(run: CanvasReplayRun) {
  const completed = Math.max(0, Number(run.preparation_progress?.completed ?? 0));
  const total = Math.max(0, Number(run.preparation_progress?.total ?? 0));
  const frameProgress = total ? ` (${completed}/${total})` : "";
  const source = run.preparation_stage === "signal_occurrences"
    ? "Loading squeeze occurrences"
    : run.preparation_stage === "watchlist_membership"
      ? "Resolving causal liquidity Watchlist"
      : run.preparation_stage === "strategy_runtime"
        ? "Building simulated strategy runtime"
        : run.preparation_stage === "strategy_frames"
          ? `Loading indicator frames${frameProgress}`
          : run.preparation_stage === "market_events"
            ? "Opening QMD market stream"
            : run.preparation_stage === "ready"
              ? "Finalizing the event-time starting state"
              : "Loading causal QMD sources";
  return {
    completed,
    progressLabel: total ? `${completed} of ${total} frame streams` : "Causal source validation in progress",
    source,
    total,
  };
}

function formatElapsed(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return minutes ? `${minutes}m ${String(remainder).padStart(2, "0")}s` : `${remainder}s`;
}

function compactEventCount(value?: number) {
  if (value === undefined) return "Preparing";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value);
}

function navigationElapsedSeconds(startedAt: string | null | undefined, now: number) {
  if (!startedAt) return 0;
  return Math.max(0, Math.floor((now - Date.parse(startedAt)) / 1_000));
}

function ReplayCheckRow({ check }: { check: ReplayCheck }) {
  return <article data-status={check.status}>
    <span className="replay-check-icon">{check.status === "ready" ? <CheckCircle2 size={18} /> : <TriangleAlert size={18} />}</span>
    <div><header><strong>{check.label}</strong>{check.required ? <em>Required</em> : <em>Optional</em>}</header><p>{check.summary}</p><small>{check.evidence}</small>{check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}</div>
  </article>;
}

function previousWeekdayIsoDate() {
  const value = new Date();
  value.setDate(value.getDate() - 1);
  while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1);
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 10);
}

function formatInteger(value: unknown) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(number) : "0";
}

function formatReplayClock(value: string) {
  return new Intl.DateTimeFormat("en-US", {
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    second: "2-digit",
    timeZone: "America/New_York",
  }).format(new Date(value));
}
