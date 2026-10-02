import { ChartCandlestick, Microscope, Network } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { UTCTimestamp } from "lightweight-charts";
import { api, query } from "../../api/client";
import { ChartPanel, type ChartPayload } from "../../app/components/ChartPanel";
import { LoadingState } from "../../app/components/LoadingState";
import { ResearchCanvas } from "./ResearchCanvas";
import { researchBandColor, researchClock as clock } from "./researchChart";
import { useResearchState } from "./researchState";

type Pair = { pair_id: number; short_episode: number | null; long_episode: number; start_us: number; end_us: number;
  best_start_price: number; best_end_price: number; best_stop: number; best_target: number; reference_range: number };
type Row = { time_us: number; episode_id: number; direction: number; close: number; action: string;
  entry_value: number | null; wait_value: number; exit_value: number | null; hold_value: number | null;
  entry_basis: number | null; label_value: number; realized_price_pnl: number | null };
type Experiment = { ticker: string; day: string; session: string; version: string; semantics: string;
  config: { timeframe_seconds: number; half_life_seconds: number; stop_offset: number };
  actions: { action: string; len: number }[]; observed_price_candles: number; consumed_activity_rows: number;
  omitted_invalid_price_rows: number; absent_second_slots: number; approximate_volume: number;
  trades: number; total_price_pnl: number; initial_discounted_value: number; pairs: Pair[]; price_source: string };
type Window = { ticker: string; candles: ChartPayload["candles"]; oscillator_series: ChartPayload["oscillator_series"];
  regions: ChartPayload["regions"]; labels: Row[]; start_us: number; end_us: number; previous_available: boolean; next_available: boolean };
const cache = new Map<string, Window>();
const number = (value: number | null | undefined) => value == null ? "—" : value.toFixed(4);
const count = (value: number) => value.toLocaleString("en-US");
const titles = { architecture: "Price-action algorithm", analytics: "Session & episode values", chart: "Price-action candles & labels" };

export function PriceActionResearch() {
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [chart, setChart] = useState<Window | null>(null), [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0), [busy, setBusy] = useState(true);
  const [start, setStart] = useResearchState<number | null>("price-action:start", null);
  const [pairId, setPairId] = useResearchState("price-action:pair", 1);
  const [selectedClock, setSelectedClock] = useResearchState<number | null>("price-action:clock", null);
  const [holdNumbers, setHoldNumbers] = useResearchState("price-action:hold-values", false);
  useEffect(() => {
    const abort = new AbortController(); setError("");
    api<Experiment>("/api/research/models/v6/price-action", { signal: abort.signal }).then(setExperiment)
      .catch(reason => { if (!abort.signal.aborted) { setError(String(reason)); setBusy(false); } });
    return () => abort.abort();
  }, [attempt]);
  useEffect(() => {
    if (!experiment) return;
    const abort = new AbortController(); setBusy(true); setError("");
    const url = `/api/research/models/v6/price-action/chart${query({ start_us: start ?? undefined, seconds: 900 })}`;
    const cached = cache.get(url);
    if (cached && !attempt) { setChart(cached); setBusy(false); return () => abort.abort(); }
    api<Window>(url, { signal: abort.signal }).then(result => {
      if (!abort.signal.aborted) { if (cache.size >= 32) cache.delete(cache.keys().next().value!); cache.set(url, result); setChart(result); }
    }).catch(reason => { if (!abort.signal.aborted) setError(String(reason)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [experiment, start, attempt]);
  const pair = experiment?.pairs.find(p => p.pair_id === pairId);
  const row = chart?.labels.find(r => r.time_us === selectedClock) ?? chart?.labels.find(r => r.action === "ENTRY") ?? chart?.labels[0];
  const payload = useMemo<ChartPayload>(() => ({ candles: chart?.candles ?? [], volume: [], overlay_series: [],
    oscillator_series: chart?.oscillator_series ?? [], regions: (chart?.regions ?? []).map(r => ({ ...r, color: researchBandColor(r.color) })),
    markers: (chart?.labels ?? []).map((r, i) => ({ id: `price-action-${i}`, time: (r.time_us/1e6-1) as UTCTimestamp,
      position: r.action === "EXIT" ? "aboveBar" : "belowBar", size: .5,
      shape: r.action === "ENTRY" ? "arrowUp" : r.action === "EXIT" ? "arrowDown" : "circle",
      color: r.action === "ENTRY" ? "var(--success)" : r.action === "EXIT" ? "var(--danger)" : "var(--muted-foreground)",
      text: r.action === "WAIT" || (r.action === "HOLD" && !holdNumbers) ? "" : r.label_value.toFixed(3) })) }), [chart, holdNumbers]);
  if (!experiment) return <div className="research-page">{error ? <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Retry experiment</button></div> : <LoadingState label="Loading saved price-action experiment" />}</div>;
  return <ResearchCanvas storageKey="price-action:NVDA:2026-07-31" titles={titles}
    icons={{ architecture: <Network size={14} />, analytics: <Microscope size={14} />, chart: <ChartCandlestick size={14} /> }}
    sources={{ architecture: "Experimental · not used in teacher training", analytics: "Full RTH session · saved price-action values", chart: "Certified 1s prices · no fill model" }}
    toolbar={<><strong>Long price-action experiment</strong><span>{experiment.ticker} · {experiment.day} · 09:30–16:00 ET</span></>}>
    {{ architecture: <div className="research-container"><h2>Price action only</h2><p>1s MACD · {experiment.config.half_life_seconds}s discount half-life · zero fees.</p>
      <p>At each close, compare entering now with waiting. While holding, compare exiting now with holding. The larger value chooses the label; ties prefer WAIT / HOLD.</p>
      <p>Future price changes carry backward across episode pairs. A trade’s price change is counted once, at exit. Short→long extrema, stop and target are references; they do not trigger exits.</p>
      <p className="research-muted">Long-only hindsight experiment. Labels may occur in either MACD direction. These are new experimental labels, not targets from the saved teacher run.</p></div>,
      analytics: <div className="research-container research-analytics"><h2>Full-session results</h2><dl>
        <div><dt>Valid-price candles</dt><dd>{count(experiment.observed_price_candles)}</dd></div>
        <div><dt>Invalid-price activity rows</dt><dd>{count(experiment.omitted_invalid_price_rows)} · unlabelled</dd></div>
        <div><dt>Absent second slots</dt><dd>{count(experiment.absent_second_slots)}</dd></div>
        <div><dt>Approximate volume</dt><dd>{count(Math.round(experiment.approximate_volume))}</dd></div>
        <div><dt>Closed price-action pairs</dt><dd>{count(experiment.trades)}</dd></div>
        <div><dt>Sum of undiscounted price changes</dt><dd>{number(experiment.total_price_pnl)}</dd></div>
        <div><dt>Discounted value at session start</dt><dd>{number(experiment.initial_discounted_value)}</dd></div></dl>
      <table><thead><tr><th>Label</th><th>Candles</th></tr></thead><tbody>{experiment.actions.map(a => <tr key={a.action}><th>{a.action}</th><td>{count(a.len)}</td></tr>)}</tbody></table>
      <h2>Short → long pair</h2><label className="research-field">Episode pair<select aria-label="Price-action episode pair" value={pairId} onChange={e => {
        const id = Number(e.target.value); setPairId(id); setSelectedClock(null); setStart(experiment.pairs.find(p => p.pair_id === id)!.start_us);
      }}>{experiment.pairs.map(p => <option key={p.pair_id} value={p.pair_id}>#{p.pair_id} · {p.short_episode == null ? "Starts long" : `S${p.short_episode}`} → L{p.long_episode} · {clock(p.start_us)}</option>)}</select></label>
      {pair && <dl><div><dt>Pair starts / ends</dt><dd>{clock(pair.start_us-1e6)} / {clock(pair.end_us-1e6)}</dd></div>
        <div><dt>Best start reference</dt><dd>{number(pair.best_start_price)}</dd></div><div><dt>Best end reference</dt><dd>{number(pair.best_end_price)}</dd></div>
        <div><dt>Stop reference · offset {experiment.config.stop_offset}</dt><dd>{number(pair.best_stop)}</dd></div><div><dt>Target reference</dt><dd>{number(pair.best_target)}</dd></div>
        <div><dt>Reference range · not a realized trade</dt><dd>{number(pair.reference_range)}</dd></div></dl>}
      <h2>Inspect one candle</h2><label className="research-field">Candle close ET<select aria-label="Price-action candle" value={row?.time_us ?? ""} onChange={e => setSelectedClock(Number(e.target.value))}>
        {chart?.labels.map(r => <option key={r.time_us} value={r.time_us}>{clock(r.time_us)} · {r.action}</option>)}</select></label>
      {row && <div className="research-candle-values"><dl><div><dt>Close / MACD episode</dt><dd>{number(row.close)} / {row.direction === 1 ? "L" : "S"}{row.episode_id}</dd></div>
        <div><dt>Chosen label / value</dt><dd>{row.action} / {number(row.label_value)}</dd></div>
        <div><dt>ENTRY value · if flat</dt><dd>{number(row.entry_value)}</dd></div><div><dt>WAIT value · if flat</dt><dd>{number(row.wait_value)}</dd></div>
        <div><dt>EXIT value · actual held context</dt><dd>{number(row.exit_value)}</dd></div><div><dt>HOLD value · actual held context</dt><dd>{number(row.hold_value)}</dd></div>
        <div><dt>Held entry price</dt><dd>{number(row.entry_basis)}</dd></div><div><dt>Price change closed now</dt><dd>{number(row.realized_price_pnl)}</dd></div>
        {row.entry_basis != null && <div><dt>Carried future value after exit</dt><dd>{number(row.exit_value!-(row.close-row.entry_basis))}</dd></div>}</dl></div>}
      <p className="research-muted">Values are price units, not probabilities. Flat alternatives are shown on every valid candle; held alternatives exist only after a sequence entry. No costs, fills or position sizing.</p>
      <details><summary>Source & parameters</summary><p>{experiment.price_source}</p><p>{experiment.version} · tf={experiment.config.timeframe_seconds}s · {experiment.semantics}</p></details></div>,
      chart: <div className="research-chart-container research-price-action-chart"><div className="research-chart-nav">
        <button className="button secondary compact" disabled={busy || !chart?.previous_available} onClick={() => { setStart(chart!.start_us-900e6); setSelectedClock(null); }}>Previous 15 min</button>
        <span>{chart ? `${clock(chart.start_us-1e6)}–${clock(chart.end_us-1e6)} ET · 1s` : "Loading window"}</span>
        <button className="button secondary compact" disabled={busy || !chart?.next_available} onClick={() => { setStart(chart!.end_us); setSelectedClock(null); }}>Next 15 min</button></div>
      <p className="research-muted">1s MACD: green ≥ signal, red &lt; signal · Arrows: ENTRY ↑ / EXIT ↓ · Numbers: discounted action value</p>
      <label className="research-checkbox"><input type="checkbox" checked={holdNumbers} onChange={e => setHoldNumbers(e.target.checked)} /><span>Show HOLD values</span></label>
      {busy ? <LoadingState fill label="Loading saved price-action labels" /> : error ? <div className="canvas-inline-error" role="alert">{error}<button onClick={() => setAttempt(a => a+1)}>Retry chart</button></div> : chart?.candles.length ?
        <ChartPanel ticker={experiment.ticker} timeframe="1s" timeframes={["1s"]} payload={payload}
          reference={row ? { time: row.time_us/1e6-1, startTime: row.time_us/1e6-1, endTime: row.time_us/1e6-1 } : undefined}
          featureOptions={[]} indicatorOptions={[]} visibleColumns={["macd_line", "macd_signal", "macd_histogram"]} visibleSupervisionGroups={[]}
          onTickerChange={() => {}} onTimeframeChange={() => {}} onVisibleColumnsChange={() => {}} onVisibleSupervisionGroupsChange={() => {}}
          tickerEditable={false} toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} fillHeight baseHeight={300}
          persistedOnly initialFitMode="last_market_day" settingsStorageKey="research.price-action.chart.v1"
          appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }} /> : <div className="research-empty">No valid-price candles in this window.</div>}
      <details className="research-label-detail"><summary>Read the labels on this chart</summary>
        <p>ENTRY chooses the best later close-price exit plus the next opportunity, discounted back to now. WAIT carries the best flat value from the next candle. EXIT closes the current price change and adds the next flat opportunity; HOLD discounts the best later exit. Price changes are counted only once.</p>
        <p>Discount halves every {experiment.config.half_life_seconds} elapsed seconds. Green/red shading marks MACD episodes, not trades. Pair extrema provide context; this first version allows several trades within a pair and can carry across pairs. Small circles mark WAIT / HOLD; WAIT has no text.</p>
      </details></div> }}
  </ResearchCanvas>;
}
