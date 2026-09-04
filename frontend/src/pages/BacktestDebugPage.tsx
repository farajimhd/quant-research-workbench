import { ArrowLeft, Bug, CheckCircle2, CircleStop, Pause, Play, Save, Square, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { api } from "../api/client";
import "./HistoricalWorkspace.css";
import { TradingModeLaunch, TradingModeSelectField } from "../app/components/TradingModeLaunch";
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
  required_watchlist_ids: string[];
  tickers: string[];
  watchlist_policy: "all_selected" | "any_selected" | "not_required";
};

type DebugRun = CanvasReplayRun & {
  debug_fixture?: { content_hash: string; derived_frame_count: number; fixture_id: string; market_event_count: number; signal_event_count: number; watchlist_event_count: number };
  mode: "backtest_debug";
};

type CompletedBacktestRun = CanvasReplayRun & {
  completed_at?: string;
  configuration_label?: string;
  configuration_revision?: number;
  fill_count?: number;
  mode: "backtest";
  net_pnl?: number | string | null;
  resident?: boolean;
  run_plan_name?: string;
  strategy_name?: string;
  strategy_revision?: number;
};

type StoredFixture = {
  conid?: number;
  derivedFrames: string;
  fixtureId: string;
  marketEvents: string;
  signalEvents?: string;
  watchlistEvents?: string;
  sessionDate: string;
  startTime: string;
  symbol: string;
};

type TestCandidateSummary = {
  candidate_id: string;
  candidate_revision: number;
  content_hash: string;
  label: string;
};

const STORAGE_KEY = "quant-research-workbench.backtest-debug-fixtures.v1";

export function BacktestDebugPage() {
  const [workflow, setWorkflow] = useState<"review" | "fixture">("review");
  const [sessionDate, setSessionDate] = useState(previousWeekdayIsoDate);
  const [startTime, setStartTime] = useState("09:45:00");
  const [symbol, setSymbol] = useState("AAPL");
  const [conid, setConid] = useState(265598);
  const [fixtureId, setFixtureId] = useState("opening-range-case-1");
  const [marketEvents, setMarketEvents] = useState(() => fixtureMarketEvents(previousWeekdayIsoDate(), "AAPL"));
  const [derivedFrames, setDerivedFrames] = useState(() => fixtureDerivedFrames(previousWeekdayIsoDate(), "AAPL"));
  const [signalEvents, setSignalEvents] = useState("[]");
  const [watchlistEvents, setWatchlistEvents] = useState("[]");
  const [library, setLibrary] = useState<StoredFixture[]>(readFixtureLibrary);
  const [selectedFixture, setSelectedFixture] = useState("");
  const [preflight, setPreflight] = useState<DebugPreflight | null>(null);
  const [checking, setChecking] = useState(true);
  const [creating, setCreating] = useState(false);
  const [controlBusy, setControlBusy] = useState("");
  const [error, setError] = useState("");
  const [run, setRun] = useState<DebugRun | null>(null);
  const [reviewRun, setReviewRun] = useState<CompletedBacktestRun | null>(null);
  const [completedRuns, setCompletedRuns] = useState<CompletedBacktestRun[]>([]);
  const [selectedCompletedRunId, setSelectedCompletedRunId] = useState("");
  const [runPlanId, setRunPlanId] = useState("");
  const [candidateId, setCandidateId] = useState("");
  const [candidates, setCandidates] = useState<TestCandidateSummary[]>([]);
  const parsed = useMemo(() => parseFixture(marketEvents, derivedFrames, signalEvents, watchlistEvents), [derivedFrames, marketEvents, signalEvents, watchlistEvents]);
  const selectedCompletedRun = completedRuns.find((row) => row.run_id === selectedCompletedRunId);

  useEffect(() => {
    if (workflow !== "fixture") {
      setChecking(false);
      return;
    }
    let cancelled = false;
    api<{ rows: TestCandidateSummary[] }>("/api/trading/configuration/candidates")
      .then((payload) => {
        if (cancelled) return;
        setCandidates(payload.rows);
        setCandidateId((current) => current || payload.rows[0]?.candidate_id || "");
      })
      .catch((reason) => { if (!cancelled) setError(message(reason)); });
    return () => { cancelled = true; };
  }, [workflow]);

  useEffect(() => {
    if (workflow !== "fixture") {
      setChecking(false);
      return;
    }
    let cancelled = false;
    setChecking(true);
    setError("");
    const timer = window.setTimeout(() => {
      api<DebugPreflight>("/api/trading/backtest_debug/preflight", {
        body: JSON.stringify({ configuration_revision_id: candidateId, run_plan_id: runPlanId, session_date: sessionDate, start_time: startTime, tickers: [symbol] }),
        method: "POST",
        timeoutMs: 20_000,
      })
        .then((payload) => { if (!cancelled) { setPreflight(payload); if (!runPlanId && payload.run_plan_id) setRunPlanId(payload.run_plan_id); setWatchlistEvents((current) => current.trim() === "[]" ? fixtureWatchlistEvents(sessionDate, symbol, conid, payload.required_watchlist_ids) : current); } })
        .catch((reason) => { if (!cancelled) { setPreflight(null); setError(message(reason)); } })
        .finally(() => { if (!cancelled) setChecking(false); });
    }, 300);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [candidateId, runPlanId, sessionDate, startTime, symbol, workflow]);

  useEffect(() => {
    let cancelled = false;
    api<{ rows: CompletedBacktestRun[] }>("/api/trading/backtest/runs", { timeoutMs: 20_000 })
      .then((payload) => {
        if (cancelled) return;
        const rows = payload.rows.filter((row) => row.mode === "backtest" && row.status === "completed");
        setCompletedRuns(rows);
        setSelectedCompletedRunId((current) => current || rows[0]?.run_id || "");
      })
      .catch((reason) => { if (!cancelled) setError(message(reason)); });
    return () => { cancelled = true; };
  }, []);

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
    const nextTicker = nextSymbol.toUpperCase();
    const nextConid = nextTicker === symbol.toUpperCase() ? conid : nextTicker === "AAPL" ? 265598 : 0;
    setSessionDate(nextDate);
    setSymbol(nextTicker);
    setConid(nextConid);
    setMarketEvents(fixtureMarketEvents(nextDate, nextSymbol));
    setDerivedFrames(fixtureDerivedFrames(nextDate, nextSymbol));
    setSignalEvents("[]");
    setWatchlistEvents(fixtureWatchlistEvents(nextDate, nextSymbol, nextConid, preflight?.required_watchlist_ids ?? []));
  }

  function saveFixture() {
    if (!fixtureId.trim()) { setError("A stable Test Scenario ID is required before saving."); return; }
    const record = { conid, derivedFrames, fixtureId: fixtureId.trim(), marketEvents, signalEvents, watchlistEvents, sessionDate, startTime, symbol };
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
    setConid(record.conid ?? (record.symbol === "AAPL" ? 265598 : 0));
    setFixtureId(record.fixtureId);
    setMarketEvents(record.marketEvents);
    setSignalEvents(record.signalEvents ?? "[]");
    setWatchlistEvents(record.watchlistEvents ?? "[]");
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
          watchlist_events: parsed.watchlistEvents,
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

  async function openCompletedRun() {
    if (!selectedCompletedRunId) return;
    setCreating(true);
    setError("");
    try {
      const opened = await api<CompletedBacktestRun>(`/api/trading/backtest/runs/${encodeURIComponent(selectedCompletedRunId)}/review`, {
        method: "POST",
        timeoutMs: 60_000,
      });
      const selection = completedRuns.find((row) => row.run_id === selectedCompletedRunId);
      setReviewRun(selection ? {
        ...opened,
        completed_at: selection.completed_at,
        fill_count: selection.fill_count,
        net_pnl: selection.net_pnl,
      } : opened);
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

  if (reviewRun) {
    return <CanvasWorkspaceSurface
      canvasId="main"
      manager={false}
      modeControls={<div className="historical-canvas-run-state">
        <button aria-label="Return to completed Backtest selection" className="button secondary compact" onClick={() => setReviewRun(null)} type="button"><ArrowLeft size={14} /> Backtests</button>
        <strong>Completed Backtest review</strong>
        <span>{reviewRun.strategy_name || reviewRun.configuration_label || "Strategy"}{reviewRun.strategy_revision ? ` r${reviewRun.strategy_revision}` : ""} · completed {formatBacktestCompletionTime(reviewRun.completed_at || reviewRun.updated_at)} · {formatFillCount(reviewRun.fill_count)} · P&amp;L {formatBacktestPnl(reviewRun.net_pnl)} · {new Intl.NumberFormat("en-US", { notation: "compact" }).format(reviewRun.processed_events || 0)} events</span>
      </div>}
      replayRun={reviewRun}
      runtimeWorkspaceId="completed-review"
    />;
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
    actionLabel={workflow === "review" ? "Review Completed Backtest" : "Run Test Scenario"}
    actionSummary={workflow === "review" ? selectedCompletedRunId ? <>Open the immutable completed run at its <strong>end-of-session clock</strong> with its pinned Canvas, strategy journal, orders, fills, positions, and performance evidence.</> : "Complete a Backtest before opening review." : parsed.ok ? <><strong>{parsed.marketEvents.length}</strong> market events, <strong>{parsed.derivedFrames.length}</strong> derived frames, <strong>{parsed.signalEvents.length}</strong> signal occurrences, and <strong>{parsed.watchlistEvents.length}</strong> eligibility transitions will be content hashed.</> : parsed.error}
    busy={creating}
    checking={workflow === "fixture" && checking}
    checks={workflow === "fixture" ? preflight?.checks ?? [] : []}
    description={workflow === "review" ? "Inspect everything an already completed strategy Backtest did using the same historical Canvas as Replay, pinned at the session close." : "Reproduce a small, exact event sequence through the production historical runtime. Use the same Run Plan and Canvas contracts with deterministic scenario input."}
    error={error || (workflow === "fixture" && !parsed.ok ? parsed.error : "")}
    eyebrow="Debug"
    icon={Bug}
    onAction={workflow === "review" ? openCompletedRun : createRun}
    ready={workflow === "review" ? Boolean(selectedCompletedRunId) : Boolean(preflight?.ready && parsed.ok)}
    title={workflow === "review" ? "Review a completed strategy" : "Inspect an exact scenario"}
  >
            <TradingModeSelectField help="Review uses immutable real Backtest evidence. A test scenario uses a small manually supplied fixture to isolate one rule." label="Debug workflow" onChange={(value) => setWorkflow(value as "review" | "fixture")} options={[{ label: "Completed Backtest review", value: "review" }, { label: "Deterministic test scenario", value: "fixture" }]} value={workflow} />
            {workflow === "review" ? <TradingModeSelectField help={selectedCompletedRun ? <>Completed {formatBacktestCompletionTime(selectedCompletedRun.completed_at || selectedCompletedRun.updated_at)} · {formatFillCount(selectedCompletedRun.fill_count)} · P&amp;L {formatBacktestPnl(selectedCompletedRun.net_pnl)}</> : "The run is loaded read-only from its durable terminal checkpoint; no strategy or broker action is executed again."} label="Completed Backtest" onChange={setSelectedCompletedRunId} options={completedRuns.length ? completedRuns.map((row) => ({ description: `${formatBacktestCompletionTime(row.completed_at || row.updated_at)} · ${formatFillCount(row.fill_count)} · P&L ${formatBacktestPnl(row.net_pnl)} · config ${row.configuration_revision ?? "—"} · ${row.run_id.slice(0, 8)}`, label: `${row.session_date} · ${row.strategy_name || row.configuration_label || "Strategy"}${row.strategy_revision ? ` r${row.strategy_revision}` : ""}`, value: row.run_id })) : [{ disabled: true, label: "No completed Backtest available", value: "" }]} presentation="catalog" searchable value={selectedCompletedRunId} /> : <>
            <TradingModeSelectField help="The exact immutable configuration exercised by this deterministic scenario." label="Test Candidate" onChange={setCandidateId} options={[{ label: "Latest available candidate", value: "" }, ...candidates.map((candidate) => ({ label: `t${candidate.candidate_revision} · ${candidate.label} · ${candidate.content_hash.slice(0, 8)}`, value: candidate.candidate_id }))]} value={candidateId} />
            <TradingModeSelectField help="The test scenario runs through this exact Strategy Studio profile and installed executor." label="Strategy Run Plan" onChange={setRunPlanId} options={(preflight?.available_run_plans ?? []).map((plan) => ({ label: `${plan.name} · ${plan.strategy_id} r${plan.strategy_revision}`, value: plan.run_plan_id }))} value={runPlanId} />
            <TradingModeSelectField ariaLabel="Test Scenario library" help="Stored in this browser; exact submitted records are persisted with the backend run." label="Test Scenario library" onChange={loadFixture} options={[{ label: "Unsaved scenario", value: "" }, ...library.map((row) => ({ label: row.fixtureId, value: row.fixtureId }))]} value={selectedFixture} />
            <label className="configuration-field"><span>Stable scenario ID</span><input onChange={(event) => setFixtureId(event.target.value)} value={fixtureId} /><small>Used with the backend content hash to identify reproducible evidence.</small></label>
            <label className="configuration-field"><span>Session date</span><input onChange={(event) => updateTemplate(event.target.value, symbol)} type="date" value={sessionDate} /></label>
            <label className="configuration-field"><span>Start clock · New York</span><input onChange={(event) => setStartTime(event.target.value)} step="1" type="time" value={startTime} /></label>
            <label className="configuration-field"><span>Primary symbol</span><input maxLength={32} onChange={(event) => updateTemplate(sessionDate, event.target.value)} value={symbol} /></label>
            <label className="configuration-field"><span>Point-in-time conid</span><input min="1" onChange={(event) => { const value = Number(event.target.value); setConid(value); setWatchlistEvents(fixtureWatchlistEvents(sessionDate, symbol, value, preflight?.required_watchlist_ids ?? [])); }} type="number" value={conid || ""} /><small>Required for deterministic eligibility and simulated broker identity. Do not guess this value.</small></label>
          <div className="debug-fixture-actions"><button className="button secondary compact" onClick={saveFixture} type="button"><Save size={14} /> Save scenario</button><button aria-label="Delete selected scenario" className="button secondary compact" disabled={!selectedFixture} onClick={deleteFixture} type="button"><Trash2 size={14} /> Delete</button></div>
          <details className="mode-launch-advanced">
            <summary><span>Test Scenario payload</span><small>{parsed.ok ? `${parsed.marketEvents.length + parsed.derivedFrames.length + parsed.signalEvents.length + parsed.watchlistEvents.length} exact records` : "JSON needs attention"}</small></summary>
            <div className="debug-fixture-editors">
            <label><span>Canonical market events · JSON array</span><textarea aria-label="Canonical market events JSON" onChange={(event) => setMarketEvents(event.target.value)} spellCheck={false} value={marketEvents} /><small>Quote/trade records require timezone-aware <code>ts</code> values and causal ordering.</small></label>
            <label><span>Derived strategy frames · JSON array</span><textarea aria-label="Derived strategy frames JSON" onChange={(event) => setDerivedFrames(event.target.value)} spellCheck={false} value={derivedFrames} /><small>Frames drive normalized strategy observations through the same controller.</small></label>
            <label><span>Signal Stream occurrences · JSON array</span><textarea aria-label="Signal Stream occurrences JSON" onChange={(event) => setSignalEvents(event.target.value)} spellCheck={false} value={signalEvents} /><small>Optional external events use <code>signal_stream_id</code>, <code>available_at</code>, ticker, conid, and configured Data Field values.</small></label>
            <label><span>Watchlist eligibility transitions · JSON array</span><textarea aria-label="Watchlist eligibility transitions JSON" onChange={(event) => setWatchlistEvents(event.target.value)} spellCheck={false} value={watchlistEvents} /><small>{preflight?.required_watchlist_ids.length ? `Required by this Run Plan (${preflight.watchlist_policy.replaceAll("_", " ")}): ${preflight.required_watchlist_ids.join(", ")}.` : "This Run Plan does not require Watchlist membership."} Each addition pins a ticker, point-in-time conid, Watchlist, and effective clock.</small></label>
            </div>
          </details>
          </>}
  </TradingModeLaunch>;
}

function DebugCheckRow({ check }: { check: DebugCheck }) {
  return <article data-status={check.status}><div className="historical-evidence-icon">{check.status === "ready" ? <CheckCircle2 size={20} /> : <TriangleAlert size={20} />}</div><div><header><strong>{check.label}</strong></header><p>{check.summary}</p><small>{check.evidence}</small>{check.action?.hash ? <button className="button secondary compact" onClick={() => { window.location.hash = check.action?.hash || "#revision-configuration"; }} type="button">{check.action.label || "Resolve"}</button> : null}</div></article>;
}

function parseFixture(marketText: string, framesText: string, signalText: string, watchlistText: string): { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; signalEvents: Array<Record<string, unknown>>; watchlistEvents: Array<Record<string, unknown>>; ok: boolean } {
  try {
    const marketEvents = JSON.parse(marketText) as unknown;
    const derivedFrames = JSON.parse(framesText) as unknown;
    const signalEvents = JSON.parse(signalText) as unknown;
    const watchlistEvents = JSON.parse(watchlistText) as unknown;
    if (!Array.isArray(marketEvents) || !Array.isArray(derivedFrames) || !Array.isArray(signalEvents) || !Array.isArray(watchlistEvents)) throw new Error("All Test Scenario editors must contain JSON arrays.");
    if (![...marketEvents, ...derivedFrames, ...signalEvents, ...watchlistEvents].every((row) => row !== null && typeof row === "object" && !Array.isArray(row))) throw new Error("Every Test Scenario record must be a JSON object.");
    if (!marketEvents.length && !derivedFrames.length && !signalEvents.length) throw new Error("Add at least one market event, derived frame, or Signal Stream occurrence.");
    if (marketEvents.length + derivedFrames.length + signalEvents.length + watchlistEvents.length > 20_000) throw new Error("A Test Scenario may contain at most 20,000 records.");
    return { derivedFrames, error: "", marketEvents, signalEvents, watchlistEvents, ok: true } as { derivedFrames: Array<Record<string, unknown>>; error: string; marketEvents: Array<Record<string, unknown>>; signalEvents: Array<Record<string, unknown>>; watchlistEvents: Array<Record<string, unknown>>; ok: boolean };
  } catch (reason) {
    return { derivedFrames: [], error: message(reason), marketEvents: [], signalEvents: [], watchlistEvents: [], ok: false };
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

function fixtureWatchlistEvents(sessionDate: string, symbol: string, conid: number, watchlistIds: string[]) {
  const ticker = symbol.toUpperCase() || "AAPL";
  return JSON.stringify(conid > 0 ? watchlistIds.map((watchlistId) => ({ effective_at: `${sessionDate}T09:45:00${newYorkOffset(sessionDate)}`, event: "added", ibkr_conid: conid, ticker, watchlist_id: watchlistId })) : [], null, 2);
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

export function formatBacktestCompletionTime(value: string | undefined) {
  if (!value) return "time unavailable";
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) return "time unavailable";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(timestamp);
}

export function formatFillCount(value: number | undefined) {
  return value === undefined || value === null
    ? "fills unavailable"
    : `${new Intl.NumberFormat("en-US").format(value)} ${value === 1 ? "fill" : "fills"}`;
}

export function formatBacktestPnl(value: number | string | null | undefined) {
  if (value === undefined || value === null || value === "") return "unavailable";
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "unavailable";
  return new Intl.NumberFormat("en-US", {
    currency: "USD",
    currencyDisplay: "narrowSymbol",
    signDisplay: "exceptZero",
    style: "currency",
  }).format(numeric);
}

function terminal(status: string) { return ["completed", "failed", "stopped"].includes(status); }
function message(reason: unknown) { return reason instanceof Error ? reason.message : String(reason); }
function previousWeekdayIsoDate() { const value = new Date(); value.setDate(value.getDate() - 1); while (value.getDay() === 0 || value.getDay() === 6) value.setDate(value.getDate() - 1); const local = new Date(value.getTime() - value.getTimezoneOffset() * 60_000); return local.toISOString().slice(0, 10); }
