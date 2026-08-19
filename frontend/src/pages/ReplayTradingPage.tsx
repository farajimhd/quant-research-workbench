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
  configuration_revision: number;
  configuration_revision_id: string;
  canvas_profile: Record<string, unknown>;
  ready: boolean;
  run_plan_id: string;
  available_run_plans: Array<{ name: string; profile_id: string; run_plan_id: string; strategy_id: string; strategy_revision: number }>;
  tickers: string[];
};

const REPLAY_SPEEDS = [1, 5, 30, 120, 0] as const;

export function ReplayTradingPage() {
  const [sessionDate, setSessionDate] = useState(previousWeekdayIsoDate);
  const [startTime, setStartTime] = useState("09:45");
  const [initialCash, setInitialCash] = useState(100_000);
  const [preflight, setPreflight] = useState<ReplayPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [run, setRun] = useState<CanvasReplayRun | null>(null);
  const [recentRuns, setRecentRuns] = useState<CanvasReplayRun[]>([]);
  const [runPlanId, setRunPlanId] = useState("");
  const replayReady = Boolean(preflight?.ready);

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
    api<{ rows: CanvasReplayRun[] }>("/api/trading/replay/runs", { timeoutMs: 20_000 })
      .then((payload) => setRecentRuns(payload.rows.slice(0, 5)))
      .catch(() => setRecentRuns([]));
  }, [refreshKey, run]);

  useEffect(() => {
    if (run) return;
    let cancelled = false;
    const timer = window.setTimeout(() => {
      setChecking(true);
      setError("");
      api<ReplayPreflight>("/api/trading/replay/preflight", {
        body: JSON.stringify({
          configuration_revision_id: preflight?.configuration_revision_id ?? "",
          initial_cash: initialCash,
          run_plan_id: runPlanId,
          session_date: sessionDate,
          start_time: startTime,
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
  }, [initialCash, refreshKey, run, runPlanId, sessionDate, startTime]);

  useReplayRunEvents(
    run?.run_id,
    (update) => setRun((current) => latestReplayRun(current, update)),
    setError,
  );

  async function createRun() {
    if (!replayReady) return;
    setCreating(true);
    setError("");
    try {
      const created = await api<CanvasReplayRun>("/api/trading/replay/runs", {
        body: JSON.stringify({
          configuration_revision_id: preflight?.configuration_revision_id,
          initial_cash: initialCash,
          run_plan_id: runPlanId,
          session_date: sessionDate,
          start_time: startTime,
        }),
        method: "POST",
        timeoutMs: 60_000,
      });
      setRun(created);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    } finally {
      setCreating(false);
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
      actionSummary={<>The approved revision is pinned to a durable simulated run. Replay opens paused at <strong>{sessionDate} · {startTime} ET</strong>.</>}
      busy={creating}
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
      secondary={recentRuns.length ? <details className="mode-launch-history"><summary><span>Recent runs</span><small>Resume a run owned by this backend session</small></summary><div>{recentRuns.map((recent) => <button disabled={recent.status === "failed" || recent.status === "stopped"} key={recent.run_id} onClick={() => setRun(recent)} type="button"><span><strong>{recent.session_date}</strong><small>{formatReplayClock(recent.current_time)} ET · {recent.status.replaceAll("_", " ")}</small></span><em>{Math.round(recent.progress * 100)}%</em></button>)}</div></details> : null}
    >
              <label>
                <span>Strategy Run Plan</span>
                <select aria-label="Strategy Run Plan" onChange={(event) => setRunPlanId(event.target.value)} value={runPlanId}>{(preflight?.available_run_plans ?? []).map((plan) => <option key={plan.run_plan_id} value={plan.run_plan_id}>{plan.name} · {plan.strategy_id} r{plan.strategy_revision}</option>)}</select>
                <small>Published strategy and installed executor revision.</small>
              </label>
              <label>
                <span>Exchange date</span>
                <input onChange={(event) => setSessionDate(event.target.value)} type="date" value={sessionDate} />
                <small>One 04:00–20:00 New York session.</small>
              </label>
              <label>
                <span>Enter Canvas at</span>
                <input max="20:00" min="04:00" onChange={(event) => setStartTime(event.target.value)} step="1" type="time" value={startTime} />
                <small>Canvas opens paused at this event-time clock.</small>
              </label>
              <label>
                <span>Initial cash</span>
                <input max={1_000_000_000} min={1_000} onChange={(event) => setInitialCash(Math.max(1_000, Number(event.target.value) || 1_000))} step={1_000} type="number" value={initialCash} />
                <small>Applied to the Run Plan's simulated account boundaries.</small>
              </label>
    </TradingModeLaunch>
  );
}

function ReplayControls({ onExit, onRunChange, run }: { onExit: () => void; onRunChange: (run: CanvasReplayRun) => void; run: CanvasReplayRun }) {
  const [busy, setBusy] = useState("");
  const [controlError, setControlError] = useState("");
  const terminal = isTerminalReplayStatus(run.status);
  const active = ["running", "fast_forwarding"].includes(run.status);

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
    <button aria-label={terminal ? "Resume Replay checkpoint" : active ? "Pause Replay" : "Play Replay"} className="replay-control-primary" disabled={(terminal && !run.checkpoint?.resume_supported) || Boolean(busy)} onClick={() => terminal ? resumeCheckpoint() : command(active ? "pause" : "play")} type="button">{active ? <Pause size={14} /> : <Play size={14} />}</button>
    <button aria-label="Advance one event-time second" className="replay-control-button" disabled={terminal || Boolean(busy)} onClick={() => command("step", { step_seconds: 1 })} title="Step one second" type="button"><SkipForward size={13} /></button>
    <button aria-label="Fast-forward five event-time minutes" className="replay-control-button" disabled={terminal || Boolean(busy)} onClick={() => {
      const current = new Date(run.current_time);
      const end = new Date(run.session_end);
      const targetDate = new Date(Math.min(end.getTime(), current.getTime() + 5 * 60_000));
      const parts = new Intl.DateTimeFormat("en-CA", { hour: "2-digit", hour12: false, minute: "2-digit", second: "2-digit", timeZone: "America/New_York" }).formatToParts(targetDate);
      const value = (kind: Intl.DateTimeFormatPartTypes) => parts.find((part) => part.type === kind)?.value ?? "00";
      const target = `${value("hour")}:${value("minute")}:${value("second")}`;
      void command("fast_forward", { target_time: target });
    }} title="Process every event through the next five minutes without wall-clock pacing" type="button"><FastForward size={13} /></button>
    <label className="replay-speed-control"><Gauge aria-hidden="true" size={13} /><select aria-label="Replay speed" disabled={terminal || Boolean(busy)} onChange={(event) => command("set_speed", { speed: Number(event.target.value) })} value={run.speed}>{REPLAY_SPEEDS.map((speed) => <option key={speed} value={speed}>{speed === 0 ? "Max" : `${speed}×`}</option>)}</select></label>
    <div className="replay-run-state" data-status={run.status}><i aria-hidden="true" /><span><strong>{run.status.replaceAll("_", " ")}</strong><small>{run.status === "warming" ? `${compactEventCount(run.warmup_events)} events` : `${Math.round(run.progress * 100)}%`}</small></span></div>
    {controlError ? <span className="replay-control-error" title={controlError}>Control failed</span> : null}
  </div>;
}

function compactEventCount(value?: number) {
  if (!value) return "Preparing";
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: "compact" }).format(value);
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
