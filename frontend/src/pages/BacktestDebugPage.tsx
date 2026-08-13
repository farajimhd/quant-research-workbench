import { Bug, CheckCircle2, CircleStop, Pause, Play, Save, Square, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import type { CanvasReplayRun } from "../app/replayRun";
import { CanvasWorkspaceSurface } from "./CanvasConfigurationPage";

type DebugCheck = {
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
  debug_fixture?: { content_hash: string; derived_frame_count: number; fixture_id: string; market_event_count: number };
  mode: "backtest_debug";
};

type StoredFixture = {
  derivedFrames: string;
  fixtureId: string;
  marketEvents: string;
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
  const [library, setLibrary] = useState<StoredFixture[]>(readFixtureLibrary);
  const [selectedFixture, setSelectedFixture] = useState("");
  const [preflight, setPreflight] = useState<DebugPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [creating, setCreating] = useState(false);
  const [controlBusy, setControlBusy] = useState("");
  const [error, setError] = useState("");
  const [run, setRun] = useState<DebugRun | null>(null);
  const [runPlanId, setRunPlanId] = useState("");
  const parsed = useMemo(() => parseFixture(marketEvents, derivedFrames), [derivedFrames, marketEvents]);

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

  useEffect(() => {
    if (!run || terminal(run.status)) return;
    const timer = window.setInterval(() => {
      api<DebugRun>(`/api/trading/backtest_debug/runs/${encodeURIComponent(run.run_id)}`, { timeoutMs: 20_000 })
        .then(setRun)
        .catch((reason) => setError(message(reason)));
    }, 750);
    return () => window.clearInterval(timer);
  }, [run]);

  function updateTemplate(nextDate: string, nextSymbol: string) {
    setSessionDate(nextDate);
    setSymbol(nextSymbol.toUpperCase());
    setMarketEvents(fixtureMarketEvents(nextDate, nextSymbol));
    setDerivedFrames(fixtureDerivedFrames(nextDate, nextSymbol));
  }

  function saveFixture() {
    if (!fixtureId.trim()) { setError("A stable fixture ID is required before saving."); return; }
    const record = { derivedFrames, fixtureId: fixtureId.trim(), marketEvents, sessionDate, startTime, symbol };
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

  return <div className="historical-home backtest-debug-page">
    <header className="historical-goal-hero">
      <div className="historical-goal-copy"><h1>Backtest Debug</h1><p>Run a small, exact event sequence through the production historical runtime contracts.</p></div>
      <span className="trading-mode-badge" data-mode="backtest_debug"><Bug size={14} /> Deterministic fixture</span>
    </header>
    {error || !parsed.ok ? <div className="historical-error-banner"><TriangleAlert size={18} /><div><strong>Fixture needs attention</strong><span>{error || parsed.error}</span></div></div> : null}
    <div className="historical-home-grid">
      <main className="historical-primary-column">
        <section className="historical-run-card">
          <header><div><span>Fixture identity</span><strong>Saved local cases and causal scope</strong></div><div className="debug-fixture-actions"><button className="button secondary" onClick={saveFixture} type="button"><Save size={15} /> Save</button><button aria-label="Delete selected fixture" className="button secondary" disabled={!selectedFixture} onClick={deleteFixture} type="button"><Trash2 size={15} /></button></div></header>
          <div className="historical-large-fields">
            <label><span>Strategy Run Plan</span><select aria-label="Strategy Run Plan" onChange={(event) => setRunPlanId(event.target.value)} value={runPlanId}>{(preflight?.available_run_plans ?? []).map((plan) => <option key={plan.run_plan_id} value={plan.run_plan_id}>{plan.name} · {plan.strategy_id} r{plan.strategy_revision}</option>)}</select><small>The fixture runs through this exact Strategy Studio profile and installed executor.</small></label>
            <label><span>Fixture library</span><select onChange={(event) => loadFixture(event.target.value)} value={selectedFixture}><option value="">Unsaved fixture</option>{library.map((row) => <option key={row.fixtureId} value={row.fixtureId}>{row.fixtureId}</option>)}</select><small>Stored in this browser; exact submitted records are persisted with the backend run.</small></label>
            <label><span>Stable fixture ID</span><input onChange={(event) => setFixtureId(event.target.value)} value={fixtureId} /><small>Used with the backend content hash to identify evidence.</small></label>
            <label><span>Session date</span><input onChange={(event) => updateTemplate(event.target.value, symbol)} type="date" value={sessionDate} /></label>
            <label><span>Start clock · New York</span><input onChange={(event) => setStartTime(event.target.value)} step="1" type="time" value={startTime} /></label>
            <label><span>Primary symbol</span><input maxLength={32} onChange={(event) => updateTemplate(sessionDate, event.target.value)} value={symbol} /></label>
          </div>
          <div className="debug-fixture-editors">
            <label><span>Canonical market events · JSON array</span><textarea aria-label="Canonical market events JSON" onChange={(event) => setMarketEvents(event.target.value)} spellCheck={false} value={marketEvents} /><small>Quote/trade records require timezone-aware <code>ts</code> values and causal ordering.</small></label>
            <label><span>Derived strategy frames · JSON array</span><textarea aria-label="Derived strategy frames JSON" onChange={(event) => setDerivedFrames(event.target.value)} spellCheck={false} value={derivedFrames} /><small>Frames drive normalized strategy observations through the same controller.</small></label>
          </div>
          <header className="historical-evidence-header"><div><span>Preflight</span><strong>Configuration and isolated runtime</strong></div>{checking ? <small>Checking…</small> : null}</header>
          <div className="historical-check-list">{preflight?.checks.map((check) => <DebugCheckRow check={check} key={check.id} />)}</div>
        </section>
      </main>
      <aside className="historical-action-column"><section className={`historical-primary-action ${preflight?.ready && parsed.ok ? "" : "blocked"}`}>
        {preflight?.ready && parsed.ok ? <Play size={24} /> : <CircleStop size={24} />}
        <div><strong>{run ? `Debug run ${run.status.replaceAll("_", " ")}` : "Exact-input execution"}</strong><p>{run ? `${Math.round(run.progress * 100)}% · ${run.processed_events || 0} events · ${run.current_time}` : parsed.ok ? `${parsed.marketEvents.length} market events and ${parsed.derivedFrames.length} derived frames will be content hashed.` : parsed.error}</p></div>
        {run && !terminal(run.status) ? <div className="historical-command-buttons"><button className="button secondary" disabled={Boolean(controlBusy)} onClick={() => commandRun(run.status === "paused" ? "play" : "pause")} type="button">{run.status === "paused" ? <Play size={15} /> : <Pause size={15} />} {run.status === "paused" ? "Resume" : "Pause"}</button><button className="button secondary" disabled={Boolean(controlBusy)} onClick={stopRun} type="button"><Square size={15} /> Stop</button></div> : run?.checkpoint?.resume_supported && run.status !== "completed" ? <button className="button primary" disabled={Boolean(controlBusy)} onClick={resumeRun} type="button"><Play size={16} /> {controlBusy === "resume" ? "Restoring…" : "Resume checkpoint"}</button> : <button className="button primary" disabled={checking || creating || !preflight?.ready || !parsed.ok} onClick={createRun} type="button"><Play size={16} /> {creating ? "Creating…" : "Run fixture"}</button>}
        {run?.debug_fixture ? <small>{run.debug_fixture.fixture_id} · {run.debug_fixture.content_hash.slice(0, 12)}</small> : null}
        {run ? <small>{run.checkpoint?.status === "available" ? `Checkpoint ${run.checkpoint.processed_events.toLocaleString()} events · ${run.checkpoint.event_time}` : `Checkpoint pending · every ${run.checkpoint?.interval_events ?? 1_000} events`} · {run.checkpoint?.resume_supported ? "restart-safe" : "resume unavailable"}</small> : null}
      </section></aside>
    </div>
    {run ? <section className="historical-runtime-canvas" aria-label="Backtest Debug Canvas workspace"><CanvasWorkspaceSurface canvasId="main" manager={false} modeControls={<div className="historical-canvas-run-state"><strong>Backtest Debug · {run.status.replaceAll("_", " ")}</strong><span>{run.processed_events || 0} exact events</span></div>} replayRun={run} runtimeWorkspaceId="main" /></section> : null}
  </div>;
}

function DebugCheckRow({ check }: { check: DebugCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small></div></article>;
}

function parseFixture(marketText: string, framesText: string): { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; ok: boolean } {
  try {
    const marketEvents = JSON.parse(marketText) as unknown;
    const derivedFrames = JSON.parse(framesText) as unknown;
    if (!Array.isArray(marketEvents) || !Array.isArray(derivedFrames)) throw new Error("Both fixture editors must contain JSON arrays.");
    if (![...marketEvents, ...derivedFrames].every((row) => row !== null && typeof row === "object" && !Array.isArray(row))) throw new Error("Every fixture record must be a JSON object.");
    if (!marketEvents.length && !derivedFrames.length) throw new Error("Add at least one market event or derived frame.");
    if (marketEvents.length + derivedFrames.length > 20_000) throw new Error("A fixture may contain at most 20,000 records.");
    return { derivedFrames, error: "", marketEvents, ok: true } as { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; ok: boolean };
  } catch (reason) {
    return { derivedFrames: [], error: message(reason), marketEvents: [], ok: false };
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
