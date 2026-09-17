import { useEffect, useMemo, useRef, useState } from "react";
import { api, query } from "../../api/client";
import { ChartPanel, type ChartPayload } from "./ChartPanel";
import type { LabelCandle, LabelRange } from "./ChartLabelOverlay";
import "./LabelerContainer.css";

type Scope = { session_date: string; session: string; ticker: string; timeframe: string; label_set: string };
type Review = { scope: Scope; revision: number; status: string; ranges: LabelRange[]; evidence_ids: string[]; source_token?: string };
type Row = Record<string, unknown> & { ticker: string; review_status: string; range_count: number };
type Bar = { start_ms: number; end_ms: number; open: number; high: number; low: number; close: number; volume: number };
type Window = { candles: Bar[]; evidence_id: string; source_token: string; next_window: number | null; window_count: number };
const frames = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "1h"];
const emptyPayload: ChartPayload = { candles: [], volume: [], overlay_series: [], oscillator_series: [], markers: [], regions: [] };
const message = (value: unknown) => value instanceof Error ? value.message : String(value);
const format = (value: unknown) => value === undefined || value === null ? "—" : Number(value).toLocaleString(undefined, { maximumFractionDigits: 2, notation: "compact" });
const time = (value: string) => new Date(value).toLocaleTimeString("en-US", { timeZone: "America/New_York", hour12: false, hour: "2-digit", minute: "2-digit", second: "2-digit", fractionalSecondDigits: 3 });

export function LabelerContainer({ instanceId }: { instanceId: string }) {
  const selectionKey = `quant-research-workbench.labeler.selection.${instanceId}`;
  const [day, setDay] = useState(() => window.localStorage.getItem(selectionKey) || "");
  const [contextError, setContextError] = useState("");
  const [session, setSession] = useState("regular");
  const [timeframe, setTimeframe] = useState("1h");
  const [ticker, setTicker] = useState("");
  const [rows, setRows] = useState<Row[]>([]);
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState("all");
  const [sort, setSort] = useState("ticker");
  const [tablePage, setTablePage] = useState(0);
  const [sessionBounds, setSessionBounds] = useState<[number, number] | null>(null);
  const [review, setReview] = useState<Review | null>(null);
  const [bars, setBars] = useState<Bar[]>([]);
  const [evidence, setEvidence] = useState<string[]>([]);
  const [complete, setComplete] = useState(false);
  const [progress, setProgress] = useState("");
  const [error, setError] = useState("");
  const [chartFailed, setChartFailed] = useState(false);
  const [universeError, setUniverseError] = useState("");
  const [busy, setBusy] = useState(false);
  const [universeBusy, setUniverseBusy] = useState(false);
  const [marketState, setMarketState] = useState("");
  const [marketComplete, setMarketComplete] = useState(false);
  const [saveState, setSaveState] = useState("Saved");
  const [draft, setDraft] = useState<LabelRange[] | null>(null);
  const [draftStatus, setDraftStatus] = useState("in_progress");
  const [candidate, setCandidate] = useState<LabelRange | null>(null);
  const [mode, setMode] = useState<"select" | "LONG" | "SHORT" | "entry" | "exit">("select");
  const [pending, setPending] = useState<LabelCandle | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [undo, setUndo] = useState<LabelRange[][]>([]);
  const [redo, setRedo] = useState<LabelRange[][]>([]);
  const [reload, setReload] = useState(0);
  const mounted = useRef(true);
  const scope = useMemo<Scope>(() => ({ session_date: day, session, timeframe, ticker, label_set: "default" }), [day, session, timeframe, ticker]);
  const locked = busy || draft !== null || candidate !== null;
  const ranges = draft ?? review?.ranges ?? [];
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    if (day) { window.localStorage.setItem(selectionKey, day); return; }
    const controller = new AbortController();
    setContextError("");
    api<{ session_date?: string }>("/api/trading/canvas-context", { signal: controller.signal, timeoutMs: 30000 })
      .then(value => { if (!controller.signal.aborted) { if (!value.session_date) throw new Error("No covered historical session is available. Choose a covered date."); setDay(value.session_date); } })
      .catch(reason => { if (!controller.signal.aborted) setContextError(`Cannot resolve the latest covered session: ${message(reason)}`); });
    return () => controller.abort();
  }, [day, selectionKey, reload]);
  useEffect(() => {
    const controller = new AbortController();
    if (!day) return () => controller.abort();
    setUniverseBusy(true); setUniverseError(""); setMarketComplete(false); setMarketState(""); setSort("ticker"); setRows([]); setTicker(""); setTimeframe("1h");
    api<{ rows: Row[]; start: string; end: string }>(`/api/research/labeler/universe${query({ session_date: day, session, include_market: false })}`, { signal: controller.signal, timeoutMs: 210000 })
      .then(async result => {
        if (controller.signal.aborted) return;
        setRows(result.rows); setSessionBounds([Date.parse(result.start), Date.parse(result.end)]); setTablePage(0); setTicker(result.rows[0]?.ticker ?? ""); setUniverseBusy(false);
        setMarketState("Loading session statistics...");
        const enrich = (market: { rows: Record<string, unknown>[] }) => {
          const values = new Map(market.rows.map(row => [String(row.symbol), row]));
          setRows(current => current.map(row => ({ ...row, ...values.get(row.ticker) })));
        };
        try {
          const market = await api<{ rows: Record<string, unknown>[] }>(`/api/research/labeler/market${query({ session_date: day, session })}`, { signal: controller.signal, timeoutMs: 210000 });
          if (controller.signal.aborted) return;
          enrich(market); setMarketComplete(true); setMarketState("");
        } catch (reason) { if (!controller.signal.aborted) setMarketState(`Statistics unavailable: ${message(reason)}. Reload session to retry.`); }
      })
      .catch(reason => { if (!controller.signal.aborted) setUniverseError(message(reason)); })
      .finally(() => { if (!controller.signal.aborted) setUniverseBusy(false); });
    return () => controller.abort();
  }, [day, session, reload]);
  useEffect(() => {
    const controller = new AbortController();
    setReview(null); setBars([]); setEvidence([]); setComplete(false); setError(""); setPending(null); setSelected(null); setUndo([]); setRedo([]); setMode("select"); setSaveState("Saved");
    setChartFailed(false);
    if (!ticker) { setProgress(""); return () => controller.abort(); }
    setProgress("Loading review and certified chart…");
    void (async () => {
      try {
        const saved = await api<Review>(`/api/research/labeler/review${query(scope)}`, { signal: controller.signal });
        if (controller.signal.aborted) return;
        setReview(saved); setEvidence(saved.evidence_ids);
        let window: number | null = 0;
        let source = "";
        while (window !== null) {
          const result: Window = await api<Window>(`/api/research/labeler/chart${query({ ...scope, window })}`, { signal: controller.signal, timeoutMs: 210000 });
          if (controller.signal.aborted) return;
          if ((source && source !== result.source_token) || (saved.source_token && saved.source_token !== result.source_token)) throw new Error("Source revision changed. Existing annotations are preserved; this review requires explicit migration before editing.");
          source = result.source_token;
          setBars(current => [...current, ...result.candles]);
          setEvidence(current => [...new Set([...current, result.evidence_id])]);
          setProgress(`Certified window ${window + 1} / ${result.window_count}`);
          window = result.next_window;
        }
        setComplete(true); setProgress("Full session certified");
      } catch (reason) { if (!controller.signal.aborted) { setChartFailed(true); setError(message(reason)); setProgress("Chart coverage incomplete"); } }
    })();
    return () => controller.abort();
  }, [scope, reload]);
  useEffect(() => {
    const escape = (event: KeyboardEvent) => { if (event.key === "Escape") { setPending(null); setMode("select"); } };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, []);
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (locked) event.preventDefault(); };
    const navigation = (event: Event) => { if (locked) { event.preventDefault(); setError("Submit, retry, or discard the pending range before leaving Labeler."); } };
    const closing = (event: Event) => { if ((event as CustomEvent).detail?.instanceIds?.includes(instanceId)) navigation(event); };
    window.addEventListener("beforeunload", guard);
    window.addEventListener("workspace-before-navigate", navigation);
    window.addEventListener("workspace-before-close", closing);
    return () => { window.removeEventListener("beforeunload", guard); window.removeEventListener("workspace-before-navigate", navigation); window.removeEventListener("workspace-before-close", closing); };
  }, [locked, instanceId]);

  async function persist(next: LabelRange[], status = "in_progress") {
    if (!review || busy) return false;
    setBusy(true); setDraft(next); setDraftStatus(status); setSaveState("Saving…"); setError("");
    try {
      const saved = await api<Review>("/api/research/labeler/review", { method: "PUT", body: JSON.stringify({ scope, expected_revision: review.revision, status, ranges: next, evidence_ids: evidence }) });
      if (!mounted.current) return false;
      setReview(saved); setDraft(null); setCandidate(null); setSaveState(`Saved · revision ${saved.revision}`);
      setRows(current => current.map(row => row.ticker === ticker ? { ...row, review_status: saved.status, range_count: saved.ranges.length } : row));
      return true;
    } catch (reason) { if (mounted.current) { setError(message(reason)); setSaveState("Save failed — draft retained"); } return false; }
    finally { if (mounted.current) setBusy(false); }
  }
  function edit(next: LabelRange[]) {
    const sorted = [...next].sort((a, b) => Date.parse(a.entry_timestamp) - Date.parse(b.entry_timestamp));
    if (sessionBounds && sorted.some(r => Date.parse(r.entry_timestamp) < sessionBounds[0] || Date.parse(r.exit_timestamp) > sessionBounds[1])) { setError("The selected candle crosses the session boundary. Choose a finer timeframe for this endpoint."); return; }
    if (sorted.some((r, i) => Date.parse(r.exit_timestamp) <= Date.parse(r.entry_timestamp) || (i > 0 && Date.parse(r.entry_timestamp) < Date.parse(sorted[i - 1].exit_timestamp)))) { setError("Ranges must run forward and cannot overlap, including opposite directions."); return; }
    setUndo(current => [...current, ranges]); setRedo([]); void persist(sorted);
  }
  function move(id: string, boundary: "entry" | "exit", candle: LabelCandle) {
    if (locked || chartFailed || !bars.length) return;
    if (ranges.find(r => r.id === id)?.annotation_timeframe !== timeframe) { setError("Switch to this range's original annotation timeframe to edit its endpoints."); return; }
    edit(ranges.map(r => r.id !== id ? r : boundary === "entry" ? { ...r, entry_timestamp: new Date(Math.round(candle.time * 1000)).toISOString(), entry_price: candle.open } : { ...r, exit_timestamp: new Date(Math.round(candle.endTime! * 1000)).toISOString(), exit_price: candle.close }));
    setMode("select");
  }
  function pick(candle: LabelCandle) {
    if (locked || chartFailed || !bars.length) return;
    if ((mode === "entry" || mode === "exit") && selected) { move(selected, mode, candle); return; }
    if (mode !== "LONG" && mode !== "SHORT") return;
    if (!pending) { setPending(candle); return; }
    const id = crypto.randomUUID();
    const next: LabelRange = { id, direction: mode, annotation_timeframe: timeframe, entry_timestamp: new Date(Math.round(pending.time * 1000)).toISOString(), exit_timestamp: new Date(Math.round(candle.endTime! * 1000)).toISOString(), entry_price: pending.open, exit_price: candle.close };
    if (Date.parse(next.exit_timestamp) <= Date.parse(next.entry_timestamp) || (sessionBounds && (Date.parse(next.entry_timestamp) < sessionBounds[0] || Date.parse(next.exit_timestamp) > sessionBounds[1])) || ranges.some(r => Date.parse(next.entry_timestamp) < Date.parse(r.exit_timestamp) && Date.parse(next.exit_timestamp) > Date.parse(r.entry_timestamp))) {
      setError("Choose a forward, nonoverlapping interval inside this session. Use a finer timeframe at session boundaries."); setPending(null); return;
    }
    setCandidate(next); setSaveState("Range ready to submit"); setError("");
    setPending(null); setSelected(id); setMode("select");
  }
  async function finish(status: string) {
    if (await persist(ranges, status)) {
      const index = visible.findIndex(row => row.ticker === ticker);
      const next = [...visible.slice(index + 1), ...visible.slice(0, index)].find(row => !["completed", "no_opportunity"].includes(row.review_status));
      if (next) { setTicker(next.ticker); setTimeframe("1h"); setTablePage(Math.floor(visible.indexOf(next) / 100)); }
    }
  }
  const visible = rows.filter(r => r.ticker.includes(search.toUpperCase()) && (filter === "all" || r.review_status === filter)).sort((a, b) => sort === "ticker" ? a.ticker.localeCompare(b.ticker) : Number(b[sort] ?? -Infinity) - Number(a[sort] ?? -Infinity));
  const pageCount = Math.max(1, Math.ceil(visible.length / 100));
  const effectivePage = Math.min(tablePage, pageCount - 1);
  const payload = useMemo<ChartPayload>(() => {
    const color = getComputedStyle(document.documentElement).getPropertyValue("--chart-text").trim();
    return { ...emptyPayload, timeframe, candles: bars.map(b => ({ time: b.start_ms / 1000, endTime: b.end_ms / 1000, isClosed: true, open: b.open, high: b.high, low: b.low, close: b.close })), volume: bars.map(b => ({ time: b.start_ms / 1000, value: b.volume, color })) };
  }, [bars, timeframe]);
  const chosen = ranges.find(r => r.id === selected);
  const ready = !chartFailed && !locked && bars.length > 0 && !!review;
  return <div className="labeler-page" data-labeler-instance={instanceId}>

    <div className="labeler-workspace">
      <aside className="labeler-sidebar"><div className="labeler-controls">
        <label>Session date<input type="date" value={day} disabled={locked} onChange={e => { if (e.target.value) setDay(e.target.value); }} /></label>
        <label>Session<select value={session} disabled={locked} onChange={e => setSession(e.target.value)}><option value="regular">Regular hours</option><option value="extended">04:00–20:00 ET</option></select></label>

        <label>Find ticker<input value={search} onChange={e => setSearch(e.target.value)} placeholder="Search symbols" /></label>
        <label>Review status<select value={filter} onChange={e => setFilter(e.target.value)}>{["all", "unreviewed", "in_progress", "completed", "no_opportunity"].map(s => <option key={s} value={s}>{s.replaceAll("_", " ")}</option>)}</select></label>
        <button disabled={locked || universeBusy} onClick={() => setReload(v => v + 1)}>Reload session</button>
      </div>
      {contextError ? <p role="alert">{contextError}</p> : !day ? <p role="status">Finding the latest covered session…</p> : null}
      {universeBusy ? <p role="status">Loading historical universe…</p> : null}
      {universeError ? <p role="alert">{universeError}</p> : null}
      {marketState ? <p className="labeler-market-state" role="status">{marketState}</p> : null}
      <div className="labeler-table-scroll"><table className="market-list-table"><thead><tr>{[["ticker", "Ticker"], ["float_shares", "Float"], ["change_pct", "Session %"], ["volume", "Volume"]].map(([key, title]) => <th key={key}><button disabled={!marketComplete && ["change_pct", "volume"].includes(key)} title={key === "change_pct" ? "Last trade versus first trade in the selected session" : key === "volume" ? "Total trade volume in the selected session" : title} onClick={() => setSort(key)}>{title}</button></th>)}<th>Review</th></tr></thead><tbody>
        {visible.slice(effectivePage * 100, (effectivePage + 1) * 100).map(row => <tr key={row.ticker} aria-selected={ticker === row.ticker} onClick={() => { if (!locked && ticker !== row.ticker) { setTicker(row.ticker); setTimeframe("1h"); } }}><td><button disabled={locked} onClick={() => { setTicker(row.ticker); setTimeframe("1h"); }}>{row.ticker}</button></td><td title={`Historical float · ${row.float_quality ?? "unavailable"} · ${row.float_source ?? "source unavailable"}`}>{format(row.float_shares)}</td><td>{format(row.change_pct)}</td><td>{format(row.volume ?? row.day_volume)}</td><td>{row.review_status.replaceAll("_", " ")}{row.range_count ? ` (${row.range_count})` : ""}</td></tr>)}
      </tbody></table>{!universeBusy && !universeError && !visible.length ? <p>No matching tickers.</p> : null}</div>
      <div className="labeler-pagination"><button disabled={effectivePage === 0} onClick={() => setTablePage(effectivePage - 1)}>Previous</button><span>Page {effectivePage + 1} / {pageCount}</span><button disabled={effectivePage + 1 >= pageCount} onClick={() => setTablePage(effectivePage + 1)}>Next</button></div><div className="labeler-summary"><span>{rows.filter(r => ["completed", "no_opportunity"].includes(r.review_status)).length} / {rows.length} reviewed</span><button onClick={async () => { try { const data = await api("/api/research/labeler/export"); const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" })); const a = document.createElement("a"); a.href = url; a.download = "chart-labels.json"; a.click(); URL.revokeObjectURL(url); } catch (reason) { setError(message(reason)); } }}>Export completed</button></div></aside>
      <main className="labeler-main"><div className="labeler-status" role="status"><strong>{ticker || "Choose a session"}</strong><span>{review?.status.replaceAll("_", " ")}</span><span>{progress}</span><span>{saveState}</span></div>
        <div className="labeler-session-actions"><label>View timeframe<select aria-label="View timeframe" value={timeframe} disabled={locked} onChange={e => setTimeframe(e.target.value)}>{frames.map(f => <option key={f}>{f}</option>)}</select></label><button disabled={!ready || !complete || ranges.length > 0} onClick={() => void finish("no_opportunity")}>No opportunity & next</button><button disabled={!ready || !complete || !ranges.length} onClick={() => void finish("completed")}>Complete & next</button></div>
        {error ? <div className="labeler-error" role="alert">{error}{chartFailed ? <button onClick={() => setReload(v => v + 1)}>Retry chart</button> : null}{draft && !busy ? <><button onClick={() => void persist(draft, draftStatus)}>Retry save</button><button onClick={() => { setDraft(null); setCandidate(null); setReload(v => v + 1); }}>Discard unsaved draft and reload</button></> : null}</div> : null}
        {bars.length > 0 ? <ChartPanel key={`${day}:${session}:${ticker}:${timeframe}`} ticker={ticker} timeframe={timeframe} timeframes={frames} payload={payload} featureOptions={[]} indicatorOptions={[]} visibleColumns={[]} visibleSupervisionGroups={[]} onTickerChange={() => {}} onTimeframeChange={value => { if (!locked) setTimeframe(value); }} onVisibleColumnsChange={() => {}} onVisibleSupervisionGroupsChange={() => {}} tickerEditable={false} toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} fillHeight baseHeight={300}
          initialFitMode="last_market_day" appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }} settingsStorageKey={`chart-labeler.${instanceId}`}
          labeling={{ ranges: candidate && !draft ? [...ranges, candidate] : ranges, active: ready && mode !== "select", selected, pendingTime: pending ? Math.round(pending.time * 1000) : undefined, onPick: pick, onSelect: setSelected, onMove: move }}
          toolbarActions={<div className="labeler-tools">{(["select", "LONG", "SHORT"] as const).map(tool => <button disabled={!ready} key={tool} aria-pressed={mode === tool} onClick={() => { setMode(tool); setPending(null); }}>{tool === "select" ? "Select" : `${tool === "LONG" ? "Long" : "Short"} range`}</button>)}
            <button disabled={!ready || !undo.length} onClick={() => { const previous = undo.at(-1)!; setUndo(undo.slice(0, -1)); setRedo([...redo, ranges]); void persist(previous); }}>Undo</button>
            <button disabled={!ready || !redo.length} onClick={() => { const next = redo.at(-1)!; setRedo(redo.slice(0, -1)); setUndo([...undo, ranges]); void persist(next); }}>Redo</button>
            <button disabled={!ready || !chosen} onClick={() => { edit(ranges.filter(r => r.id !== selected)); setSelected(null); }}>Delete</button>
            {candidate && !draft ? <div className="labeler-submit"><strong>{candidate.direction}</strong> {time(candidate.entry_timestamp)} → {time(candidate.exit_timestamp)}<button disabled={busy} onClick={() => { setUndo(current => [...current, ranges]); setRedo([]); void persist([...ranges, candidate].sort((a, b) => Date.parse(a.entry_timestamp) - Date.parse(b.entry_timestamp))); }}>Submit range</button><button disabled={busy} onClick={() => { setCandidate(null); setSelected(null); setSaveState("Saved"); }}>Discard range</button></div> : null}
          </div>} /> : <div className="labeler-chart-placeholder" role="status">{complete ? "No observed candles in this session. A negative review cannot be certified." : progress || "Select a ticker to load its hourly chart."}</div>}
        <div className="labeler-inspector" hidden={!ranges.length && !candidate && !pending}><span>{pending ? "Choose the exit candle. Escape cancels." : mode === "LONG" || mode === "SHORT" ? "Choose the entry candle, then the exit candle." : "Select a range or drag an endpoint. Boundaries snap to candle open and close."}</span>
          {chosen ? <div><strong>{chosen.direction}</strong> {time(chosen.entry_timestamp)} → {time(chosen.exit_timestamp)} · {chosen.entry_price} → {chosen.exit_price} · {((Date.parse(chosen.exit_timestamp) - Date.parse(chosen.entry_timestamp)) / 1000).toFixed(3)} s · Gross reference {((chosen.exit_price / chosen.entry_price - 1) * (chosen.direction === "LONG" ? 100 : -100)).toFixed(2)}%
            <button disabled={!ready} onClick={() => setMode("entry")}>Choose new entry</button><button disabled={!ready} onClick={() => setMode("exit")}>Choose new exit</button></div> : null}
          <div className="labeler-range-list">{ranges.map((r, index) => <button key={r.id} aria-pressed={selected === r.id} onClick={() => setSelected(r.id)}>{index + 1}. {r.direction} {time(r.entry_timestamp)}–{time(r.exit_timestamp)}</button>)}</div>
        </div>
        <footer className="labeler-footer"><span>Times: New York  /  Submit saves each range  /  Complete marks remaining time as wait</span></footer>
      </main>
    </div>
  </div>;
}
