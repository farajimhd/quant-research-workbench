import { ArrowLeft, Bug, CheckCircle2, CircleStop, Pause, Play, Save, Square, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { TradingModeLaunch } from "../app/components/TradingModeLaunch";
import { usePollingTask } from "../app/hooks/usePollingTask";
import type { CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

type DebugCheck = {
  action?: { hash?: string; label?: string };
  evidence: string;
  id: string;
  label: string;
  required: boolean;
  status: "blocked" | "error" | "ready";
  summary: string;
};

type DebugPreflight = {
  checks: DebugCheck[];
  configuration_revision: number;
  configuration_revision_id: string;
  ready: boolean;
  run_plan_id: string;
  available_run_plans: Array<{ name: string; profile_id: string; run_plan_id: string; strategy_id: string; strategy_revision: number }>;
  tickers: string[];
};

type DebugRun = CanvasReplayRun & {
  debug_fixture?: { content_hash: string; derived_frame_count: number; fixture_id: string; market_event_count: number; signal_event_count: number };
  mode: "backtest_debug";
};

type StoredFixture = {
  derivedFrames: string;
  fixtureId: string;
  marketEvents: string;
  signalEvents?: string;
  sessionDate: string;
  startTime: string;
  symbol: string;
};

const STORAGE_KEY = "quant-research-workbench.backtest-debug-fixtures.v1";

export function BacktestDebugPage() {
  const [sessionDate, setSessionDate] = useState(previousWeekdayIsoDate);
  const [startTime, setStartTime] = useState("09:45:00");
  const [symbol, setSymbol] = useState("AAPL");
  const [fixtureId, setFixtureId] = useState("opening-range-case-1");
  const [marketEvents, setMarketEvents] = useState(() => fixtureMarketEvents(previousWeekdayIsoDate(), "AAPL"));
  const [derivedFrames, setDerivedFrames] = useState(() => fixtureDerivedFrames(previousWeekdayIsoDate(), "AAPL"));
  const [signalEvents, setSignalEvents] = useState("[]");
  const [library, setLibrary] = useState<StoredFixture[]>(readFixtureLibrary);
  const [selectedFixture, setSelectedFixture] = useState("");
  const [preflight, setPreflight] = useState<DebugPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [creating, setCreating] = useState(false);
  const [controlBusy, setControlBusy] = useState("");
  const [error, setError] = useState("");
  const [run, setRun] = useState<DebugRun | null>(null);
  const [runPlanId, setRunPlanId] = useState("");
  const parsed = useMemo(() => parseFixture(marketEvents, derivedFrames, signalEvents), [derivedFrames, marketEvents, signalEvents]);

  useEffect(() => {
    let cancelled = false;
    setChecking(true);
    setError("");
    const timer = window.setTimeout(() => {
      api<DebugPreflight>("/api/trading/backtest_debug/preflight", {
        body: JSON.stringify({ run_plan_id: runPlanId, session_date: sessionDate, start_time: startTime, tickers: [symbol] }),
        method: "POST",
        timeoutMs: 20_000,
      })
        .then((payload) => { if (!cancelled) { setPreflight(payload); if (!runPlanId && payload.run_plan_id) setRunPlanId(payload.run_plan_id); } })
        .catch((reason) => { if (!cancelled) { setPreflight(null); setError(message(reason)); } })
        .finally(() => { if (!cancelled) setChecking(false); });
    }, 300);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [runPlanId, sessionDate, startTime, symbol]);

  usePollingTask({
    enabled: Boolean(run && !terminal(run.status)),
    intervalMs: 750,
    onError: (reason) => setError(message(reason)),
    restartKey: run?.run_id,
    task: async (signal) => {
      if (!run) return;
      setRun(await api<DebugRun>(`/api/trading/backtest_debug/runs/${encodeURIComponent(run.run_id)}`, { signal, timeoutMs: 20_000 }));
    },
  });

  function updateTemplate(nextDate: string, nextSymbol: string) {
    setSessionDate(nextDate);
    setSymbol(nextSymbol.toUpperCase());
    setMarketEvents(fixtureMarketEvents(nextDate, nextSymbol));
    setDerivedFrames(fixtureDerivedFrames(nextDate, nextSymbol));
    setSignalEvents("[]");
  }

  function saveFixture() {
    if (!fixtureId.trim()) { setError("A stable Test Scenario ID is required before saving."); return; }
    const record = { derivedFrames, fixtureId: fixtureId.trim(), marketEvents, signalEvents, sessionDate, startTime, symbol };
    const next = [...library.filter((row) => row.fixtureId !== record.fixtureId), record].sort((a, b) => a.fixtureId.localeCompare(b.fixtureId));
    setLibrary(next);
    setSelectedFixture(record.fixtureId);
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
    setError("");
  }

  function loadFixture(id: string) {
    setSelectedFixture(id);
    const record = library.find((row) => row.fixtureId === id);
    if (!record) return;
    setDerivedFrames(record.derivedFrames);
    setFixtureId(record.fixtureId);
    setMarketEvents(record.marketEvents);
    setSignalEvents(record.signalEvents ?? "[]");
    setSessionDate(record.sessionDate);
    setStartTime(record.startTime);
    setSymbol(record.symbol);
  }

  function deleteFixture() {
    if (!selectedFixture) return;
    const next = library.filter((row) => row.fixtureId !== selectedFixture);
    setLibrary(next);
    setSelectedFixture("");
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  }

  async function createRun() {
    if (!preflight?.ready || !parsed.ok) return;
    setCreating(true);
    setError("");
    try {
      const created = await api<DebugRun>("/api/trading/backtest_debug/runs", {
        body: JSON.stringify({
          configuration_revision_id: preflight.configuration_revision_id,
          derived_frames: parsed.derivedFrames,
          fixture_id: fixtureId,
          market_events: parsed.marketEvents,
          signal_events: parsed.signalEvents,
          run_plan_id: runPlanId,
          session_date: sessionDate,
          start_time: startTime,
          tickers: [symbol],
        }),
        method: "POST",
        timeoutMs: 60_000,
      });
      setRun(created);
    } catch (reason) {
      setError(message(reason));
    } finally {
      setCreating(false);
    }
  }

  async function stopRun() {
    if (!run) return;
    try {
      setRun(await api<DebugRun>(`/api/trading/backtest_debug/runs/${encodeURIComponent(run.run_id)}/commands`, {
        body: JSON.stringify({ command: "stop" }),
        method: "POST",
      }));
    } catch (reason) {
      setError(message(reason));
    }
  }

  async function commandRun(command: "pause" | "play") {
    if (!run) return;
    setControlBusy(command);
    setError("");
    try {
      setRun(await api<DebugRun>(`/api/trading/backtest_debug/runs/${encodeURIComponent(run.run_id)}/commands`, {
        body: JSON.stringify({ command }),
        method: "POST",
      }));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setControlBusy("");
    }
  }

  async function resumeRun() {
    if (!run) return;
    setControlBusy("resume");
    setError("");
    try {
      setRun(await api<DebugRun>(`/api/trading/backtest_debug/runs/${encodeURIComponent(run.run_id)}/resume`, { method: "POST" }));
    } catch (reason) {
      setError(message(reason));
    } finally {
      setControlBusy("");
    }
  }

  if (run) {
    const runTerminal = terminal(run.status);
    return <CanvasWorkspaceSurface
      canvasId="main"
      manager={false}
      modeControls={<div className="historical-canvas-run-state">
        <button aria-label="Return to Backtest Debug setup" className="button secondary compact" onClick={() => setRun(null)} type="button"><ArrowLeft size={14} /> Setup</button>
        <strong>Backtest Debug · {run.status.replaceAll("_", " ")}</strong>
        <span>{run.processed_events || 0} exact events</span>
        {!runTerminal ? <><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void commandRun(run.status === "paused" ? "play" : "pause")} type="button">{run.status === "paused" ? <Play size={14} /> : <Pause size={14} />}{run.status === "paused" ? "Resume" : "Pause"}</button><button className="button secondary compact" disabled={Boolean(controlBusy)} onClick={() => void stopRun()} type="button"><Square size={14} /> Stop</button></> : null}
      </div>}
      replayRun={run}
      runtimeWorkspaceId="main"
    />;
  }

  return <TradingModeLaunch
    actionLabel="Run Test Scenario"
    actionSummary={parsed.ok ? <><strong>{parsed.marketEvents.length}</strong> market events, <strong>{parsed.derivedFrames.length}</strong> derived frames, and <strong>{parsed.signalEvents.length}</strong> signal occurrences will be content hashed.</> : parsed.error}
    busy={creating}
    checking={checking}
    checks={preflight?.checks ?? []}
    description="Reproduce a small, exact event sequence through the production historical runtime. Use the same Run Plan and Canvas contracts with deterministic scenario input."
    error={error || (!parsed.ok ? parsed.error : "")}
    eyebrow="Debug"
    icon={Bug}
    onAction={createRun}
    ready={Boolean(preflight?.ready && parsed.ok)}
    title="Inspect an exact scenario"
  >
            <label className="configuration-field"><span>Strategy Run Plan</span><select aria-label="Strategy Run Plan" onChange={(event) => setRunPlanId(event.target.value)} value={runPlanId}>{(preflight?.available_run_plans ?? []).map((plan) => <option key={plan.run_plan_id} value={plan.run_plan_id}>{plan.name} · {plan.strategy_id} r{plan.strategy_revision}</option>)}</select><small>The test scenario runs through this exact Strategy Studio profile and installed executor.</small></label>
            <label className="configuration-field"><span>Test Scenario library</span><select onChange={(event) => loadFixture(event.target.value)} value={selectedFixture}><option value="">Unsaved scenario</option>{library.map((row) => <option key={row.fixtureId} value={row.fixtureId}>{row.fixtureId}</option>)}</select><small>Stored in this browser; exact submitted records are persisted with the backend run.</small></label>
            <label className="configuration-field"><span>Stable scenario ID</span><input onChange={(event) => setFixtureId(event.target.value)} value={fixtureId} /><small>Used with the backend content hash to identify reproducible evidence.</small></label>
            <label className="configuration-field"><span>Session date</span><input onChange={(event) => updateTemplate(event.target.value, symbol)} type="date" value={sessionDate} /></label>
            <label className="configuration-field"><span>Start clock · New York</span><input onChange={(event) => setStartTime(event.target.value)} step="1" type="time" value={startTime} /></label>
            <label className="configuration-field"><span>Primary symbol</span><input maxLength={32} onChange={(event) => updateTemplate(sessionDate, event.target.value)} value={symbol} /></label>
          <div className="debug-fixture-actions"><button className="button secondary compact" onClick={saveFixture} type="button"><Save size={14} /> Save scenario</button><button aria-label="Delete selected scenario" className="button secondary compact" disabled={!selectedFixture} onClick={deleteFixture} type="button"><Trash2 size={14} /> Delete</button></div>
          <details className="mode-launch-advanced">
            <summary><span>Test Scenario payload</span><small>{parsed.ok ? `${parsed.marketEvents.length + parsed.derivedFrames.length + parsed.signalEvents.length} exact records` : "JSON needs attention"}</small></summary>
            <div className="debug-fixture-editors">
            <label><span>Canonical market events · JSON array</span><textarea aria-label="Canonical market events JSON" onChange={(event) => setMarketEvents(event.target.value)} spellCheck={false} value={marketEvents} /><small>Quote/trade records require timezone-aware <code>ts</code> values and causal ordering.</small></label>
            <label><span>Derived strategy frames · JSON array</span><textarea aria-label="Derived strategy frames JSON" onChange={(event) => setDerivedFrames(event.target.value)} spellCheck={false} value={derivedFrames} /><small>Frames drive normalized strategy observations through the same controller.</small></label>
            <label><span>Signal Stream occurrences · JSON array</span><textarea aria-label="Signal Stream occurrences JSON" onChange={(event) => setSignalEvents(event.target.value)} spellCheck={false} value={signalEvents} /><small>Optional external events use <code>signal_stream_id</code>, <code>available_at</code>, ticker, conid, and configured Data Field values.</small></label>
            </div>
          </details>
  </TradingModeLaunch>;
}

function DebugCheckRow({ check }: { check: DebugCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small>{check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}</div></article>;
}

function parseFixture(marketText: string, framesText: string, signalText: string): { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; signalEvents: Array<Record<string, unknown>>; ok: boolean } {
  try {
    const marketEvents = JSON.parse(marketText) as unknown;
    const derivedFrames = JSON.parse(framesText) as unknown;
    const signalEvents = JSON.parse(signalText) as unknown;
    if (!Array.isArray(marketEvents) || !Array.isArray(derivedFrames) || !Array.isArray(signalEvents)) throw new Error("All Test Scenario editors must contain JSON arrays.");
    if (![...marketEvents, ...derivedFrames, ...signalEvents].every((row) => row !== null && typeof row === "object" && !Array.isArray(row))) throw new Error("Every Test Scenario record must be a JSON object.");
    if (!marketEvents.length && !derivedFrames.length && !signalEvents.length) throw new Error("Add at least one market event, derived frame, or Signal Stream occurrence.");
    if (marketEvents.length + derivedFrames.length + signalEvents.length > 20_000) throw new Error("A Test Scenario may contain at most 20,000 records.");
    return { derivedFrames, error: "", marketEvents, signalEvents, ok: true } as { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; signalEvents: Array<Record<string, unknown>>; ok: boolean };
  } catch (reason) {
    return { derivedFrames: [], error: message(reason), marketEvents: [], signalEvents: [], ok: false };
  }
}

function fixtureMarketEvents(sessionDate: string, symbol: string) {
  const offset = newYorkOffset(sessionDate);
  return JSON.stringify([
    { ask_price: 101.3, ask_size: 30, bid_price: 101.2, bid_size: 20, kind: "quote", sequence: 1, ticker: symbol.toUpperCase() || "AAPL", ts: `${sessionDate}T09:45:00${offset}` },
    { kind: "trade", price: 101.25, sequence: 2, size: 100, ticker: symbol.toUpperCase() || "AAPL", ts: `${sessionDate}T09:45:01${offset}` },
  ], null, 2);
}

function fixtureDerivedFrames(sessionDate: string, symbol: string) {
  return JSON.stringify([{ as_of: `${sessionDate}T09:45:01${newYorkOffset(sessionDate)}`, bar: { close: 101.25, high: 101.3, low: 101.2, open: 101.2, volume: 100 }, indicator: { close: 101.25, vwap: 101.22 }, sequence: 3, ticker: symbol.toUpperCase() || "AAPL", timeframe: "1m" }], null, 2);
}

function newYorkOffset(sessionDate: string) {
  const zone = new Intl.DateTimeFormat("en-US", { timeZone: "America/New_York", timeZoneName: "longOffset" })
    .formatToParts(new Date(`${sessionDate}T16:00:00Z`))
    .find((part) => part.type === "timeZoneName")?.value;
  const offset = zone?.replace("GMT", "");
  return offset && /^[+-]\d{2}:\d{2}$/.test(offset) ? offset : "-05:00";
}

function readFixtureLibrary(): StoredFixture[] {
  try {
    const rows = JSON.parse(window.localStorage.getItem(STORAGE_KEY) || "[]") as StoredFixture[];
    return Array.isArray(rows) ? rows.filter((row) => Boolean(row?.fixtureId)) : [];
  } catch { return []; }
}

function terminal(status: string) { return ["completed", "failed", "stopped"].includes(status); }
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }
function previousWeekdayIsoDate() { const value = new Date(); value.setDate(value.getDate() - 1); while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1); const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000); return local.toISOString().slice(0, 10); }
