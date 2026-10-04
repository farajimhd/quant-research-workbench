import { ChartCandlestick, Microscope, Network } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import type { UTCTimestamp } from "lightweight-charts";
import { query } from "../../api/client";
import { researchApi as api } from "./researchApi";
import { ChartPanel, type ChartPayload } from "../../app/components/ChartPanel";
import { LoadingState } from "../../app/components/LoadingState";
import { ResearchCanvas } from "./ResearchCanvas";
import { researchBandColor, researchClock as clock } from "./researchChart";
import { useResearchState } from "./researchState";

type Pair = { liquidity_accepted?: boolean; liquidity_rejection_reason?: string; pair_id: number; short_episode: number | null; long_episode: number; start_us: number; end_us: number;
  best_start_price: number; best_end_price: number; best_stop: number; best_target: number; reference_range: number; reference_entry_us: number | null; reference_exit_us: number | null; reference_entry_price: number | null; reference_exit_price: number | null; best_entry_gain: number; best_exit_gain: number; carried_next_pair_value: number };
type Row = { liquidity_eligible?: boolean; liquidity_reason?: string; episode_liquidity_reason?: string; prior_trades_60s?: number; prior_shares_60s?: number; prior_active_seconds_60s?: number; prior_trade_age_seconds?: number | null; time_us: number; episode_id: number; pair_id: number; direction: number; close: number; action: string;
  entry_gain: number; entry_quality: number; exit_gain: number | null; exit_quality: number | null;
  entry_target_us?: number | null; entry_horizon_seconds?: number | null; hold_target_us?: number | null; hold_horizon_seconds?: number | null; hold_target_gain?: number | null;
  hold_discounted_gain?: number | null; liquidation_quality?: number | null; in_exit_cluster?: boolean;
  entry_basis: number | null; label_value: number | null; reference_action: string; carried_next_pair_value: number; both_opportunities: boolean };
type Experiment = { ticker: string; day: string; session: string; version: string; semantics: string;
  config: { liquidity_gate?: boolean; minimum_trades_60s?: number; minimum_shares_60s?: number; minimum_active_seconds_60s?: number; maximum_inactivity_seconds?: number; timeframe_seconds: number; half_life_seconds: number; stop_offset: number; minimum_position_seconds: number; quality_threshold: number };
  actions: { action: string; len: number }[]; teacher_actions?: { branch: string; action: string; len: number }[]; observed_price_candles: number; consumed_activity_rows: number;
  omitted_invalid_price_rows: number; absent_second_slots: number; approximate_volume: number;
  trades: number; total_price_pnl: number; both_opportunities: number; pairs: Pair[]; price_source: string;
  reporting?: { revision: string; counts: { n: number; delayed: number; unknown: number } } };
type Window = { ticker: string; candles: ChartPayload["candles"]; oscillator_series: ChartPayload["oscillator_series"];
  regions: ChartPayload["regions"]; labels: Row[]; start_us: number; end_us: number; previous_available: boolean; next_available: boolean };
const cache = new Map<string, Window>();
const number = (value: number | null | undefined) => value == null ? "—" : value.toFixed(4);
const count = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("en-US");
const titles = { architecture: "Price-action algorithm", analytics: "Session & episode values", chart: "Price-action candles & labels" };

export function PriceActionResearch({ saved }: { saved?: { day: string; listing_id: string; dataset_sha256: string; supports_combined: boolean } }) {
  const prefix = saved ? `saved-labels:${saved.dataset_sha256}:${saved.day}:${saved.listing_id}` : "price-action";
  const endpoint = saved ? "/api/research/models/v6/saved-labels" : "/api/research/models/v6/price-action";
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [chart, setChart] = useState<Window | null>(null), [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0), [busy, setBusy] = useState(true);
  const [start, setStart] = useResearchState<number | null>(`${prefix}:start`, null);
  const [pairId, setPairId] = useResearchState(`${prefix}:pair`, 1);
  const [selectedClock, setSelectedClock] = useResearchState<number | null>(`${prefix}:clock`, null);
  const [threshold, setThreshold] = useResearchState(`${prefix}:v2:threshold`, .9);
  const [view, setView] = useResearchState(`${prefix}:v3:view`, saved && !saved.supports_combined ? "flat" : "combined");
  const [holdNumbers, setHoldNumbers] = useResearchState(`${prefix}:hold-values`, false);
  useEffect(() => {
    const abort = new AbortController(); setError("");
    api<Experiment>(`${endpoint}${saved ? "/metadata" : ""}${query({ day: saved?.day, listing_id: saved?.listing_id, quality_threshold: saved ? undefined : threshold })}`, { signal: abort.signal, timeoutMs: 300000 }).then(setExperiment)
      .catch(reason => { if (!abort.signal.aborted) { setError(String(reason)); setBusy(false); } });
    return () => abort.abort();
  }, [attempt, threshold]);
  const ready = Boolean(experiment);
  useEffect(() => {
    if (!ready) return;
    const abort = new AbortController(); setBusy(true); setError("");
    const url = `${endpoint}/chart${query({ day: saved?.day, listing_id: saved?.listing_id, dataset_sha256: saved?.dataset_sha256, start_us: start ?? undefined, seconds: 900, quality_threshold: saved ? undefined : threshold, view })}`;
    const cached = cache.get(url);
    if (cached && !attempt) { setChart(cached); setBusy(false); return () => abort.abort(); }
    api<Window>(url, { signal: abort.signal, timeoutMs: 300000 }).then(result => {
      if (!abort.signal.aborted) { if (cache.size >= 32) cache.delete(cache.keys().next().value!); cache.set(url, result); setChart(result); }
    }).catch(reason => { if (!abort.signal.aborted) setError(String(reason)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [ready, start, attempt, threshold, view]);
  const pair = experiment?.pairs.find(p => p.pair_id === pairId);
  const row = chart?.labels.find(r => r.time_us === selectedClock) ?? chart?.labels.find(r => r.action === "ENTRY") ?? chart?.labels[0];
  const payload = useMemo<ChartPayload>(() => ({ candles: chart?.candles ?? [], volume: [], overlay_series: [],
    oscillator_series: chart?.oscillator_series ?? [], regions: (chart?.regions ?? []).map(r => ({ ...r, color: researchBandColor(r.color) })),
    markers: (chart?.labels ?? []).filter(r => r.action !== "UNLABELLED").map((r, i) => ({ id: `price-action-${i}`, time: (r.time_us/1e6-1) as UTCTimestamp,
      position: r.action === "EXIT" ? "aboveBar" : "belowBar", size: .5,
      shape: r.action === "ENTRY" ? "arrowUp" : r.action === "EXIT" ? "arrowDown" : "circle",
      color: r.action === "ENTRY" ? "var(--success)" : r.action === "EXIT" ? "var(--danger)" : r.action === "HOLD" ? "var(--info)" : "var(--muted-foreground)",
      text: r.action === "WAIT" || (r.action === "HOLD" && !holdNumbers) ? "" : `${r.label_value?.toFixed(3) ?? ""}${r.action === "ENTRY" && r.entry_horizon_seconds != null ? ` · ${r.entry_horizon_seconds}s` : r.action === "HOLD" && r.hold_horizon_seconds != null ? ` · ${r.hold_horizon_seconds}s` : ""}`,
      secondaryText: r.action === "ENTRY" ? r.entry_gain.toFixed(4) : (r.action === "EXIT" || (r.action === "HOLD" && holdNumbers)) && r.exit_gain != null ? r.exit_gain.toFixed(4) : undefined })) }), [chart, holdNumbers, saved]);
  if (!experiment) return <div className="research-page">{error ? <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Retry experiment</button></div> : <LoadingState label="Loading saved price-action experiment" />}</div>;
  return <ResearchCanvas storageKey={saved ? prefix : "price-action:NVDA:2026-07-31"} titles={titles}
    icons={{ architecture: <Network size={14} />, analytics: <Microscope size={14} />, chart: <ChartCandlestick size={14} /> }}
    sources={{ architecture: saved ? "Current V6 teacher label authority" : "Experimental · not used in teacher training", analytics: saved ? "Published full-session shard" : "Full RTH session · saved price-action values", chart: "Saved 1s prices & labels · no fill model" }}
    toolbar={<><strong>{saved ? "Current V6 teacher labels" : "Long price-action experiment"}</strong><span>{experiment.ticker} · {experiment.day} · {saved ? "04:00–20:00" : "09:30–16:00"} ET</span></>}>
    {{ architecture: <div className="research-container"><h2>Price action only</h2><p>Targets are attached to candle close t, never open. Model features use only candles with close time &lt; t: the target candle is excluded, including its OHLC, indicators and held-position mark. Hindsight targets use later prices and are retrospective supervision.</p>{saved && <details><summary>Published dataset identity</summary><p className="research-source">SHA-256 {saved.dataset_sha256}</p></details>}<p>1s MACD · {experiment.config.half_life_seconds}s discount half-life · zero fees.</p>
      <p>Each short→long pair is one opportunity. ENTRY quality compares its discounted future gain with the pair’s best entry gain. EXIT compares liquidation now with discounted future holding; only the first contiguous winning cluster receives EXIT.</p>
      <p>Quality ≥ {(threshold*100).toFixed(0)}% gives an opportunity arrow. Several candles may qualify. One reference entry/exit pair measures the price change; opportunity arrows are alternatives, not repeated trades.</p>
      <p className="research-muted">Scores stay between 0 and 1. Carry to the next opportunity is separate. Stop/target are references.</p></div>,
      analytics: <div className="research-container research-analytics"><h2>Full-session results</h2><dl>
        <div><dt>Valid-price candles</dt><dd>{count(experiment.observed_price_candles)}</dd></div>
        <div><dt>Invalid-price activity rows</dt><dd>{count(experiment.omitted_invalid_price_rows)} · unlabelled</dd></div>
        <div><dt>Absent second slots</dt><dd>{count(experiment.absent_second_slots)}</dd></div>
        {!saved && experiment.approximate_volume != null && <div><dt>Approximate volume</dt><dd>{count(Math.round(experiment.approximate_volume))}</dd></div>}
        <div><dt>Selected reference pairs</dt><dd>{count(experiment.trades)}</dd></div>
        <div><dt>Sum of undiscounted price changes</dt><dd>{number(experiment.total_price_pnl)}</dd></div>
        <div><dt>Short → long pairs</dt><dd>{count(experiment.pairs.length)}</dd></div></dl>
      {saved ? <table><thead><tr><th>Branch</th><th>Teacher label</th><th>Candles</th></tr></thead><tbody>{experiment.teacher_actions?.map(a => <tr key={`${a.branch}:${a.action}`}><th>{a.branch}</th><td>{a.action}</td><td>{count(a.len)}</td></tr>)}</tbody></table> : <table><thead><tr><th>Label</th><th>Candles</th></tr></thead><tbody>{experiment.actions.map(a => <tr key={a.action}><th>{a.action}</th><td>{count(a.len)}</td></tr>)}</tbody></table>}
      <h2>Short → long pair</h2><label className="research-field">Episode pair<select aria-label="Price-action episode pair" value={pairId} onChange={e => {
        const id = Number(e.target.value); setPairId(id); setSelectedClock(null); setStart(experiment.pairs.find(p => p.pair_id === id)!.start_us);
      }}>{experiment.pairs.map(p => <option key={p.pair_id} value={p.pair_id}>#{p.pair_id} · {p.short_episode == null ? "Starts long" : `S${p.short_episode}`} → L{p.long_episode} · {clock(p.start_us-1e6)}</option>)}</select></label>
      {pair && <dl><div><dt>Pair starts / ends</dt><dd>{clock(pair.start_us-1e6)} / {clock(pair.end_us-1e6)}</dd></div>
        <div><dt>Best start reference</dt><dd>{number(pair.best_start_price)}</dd></div><div><dt>Best end reference</dt><dd>{number(pair.best_end_price)}</dd></div>
        <div><dt>Stop reference · offset {experiment.config.stop_offset}</dt><dd>{number(pair.best_stop)}</dd></div><div><dt>Target reference</dt><dd>{number(pair.best_target)}</dd></div>
        <div><dt>Reference range</dt><dd>{number(pair.reference_range)}</dd></div>
        <div><dt>Selected entry / exit close ET</dt><dd>{pair.reference_entry_us == null ? (pair.liquidity_accepted === false ? "Rejected by liquidity gate" : "No positive gain") : `${clock(pair.reference_entry_us)} / ${clock(pair.reference_exit_us!)}`}</dd></div>
        <div><dt>Selected entry / exit price</dt><dd>{number(pair.reference_entry_price)} / {number(pair.reference_exit_price)}</dd></div>
        <div><dt>Best discounted entry gain</dt><dd>{number(pair.best_entry_gain)}</dd></div><div><dt>Best exit price gain</dt><dd>{number(pair.best_exit_gain)}</dd></div></dl>}
      {experiment.config.liquidity_gate && <section aria-label="Episode liquidity audit"><h2>Episode liquidity audit</h2><p>Start requires {experiment.config.minimum_trades_60s} trades, {count(experiment.config.minimum_shares_60s)} shares and {experiment.config.minimum_active_seconds_60s} active seconds in the prior 60 seconds. Last trade must be within {experiment.config.maximum_inactivity_seconds}s. Target candle excluded.</p><p>{count(experiment.pairs.filter(p => p.liquidity_accepted).length)} accepted / {count(experiment.pairs.filter(p => p.liquidity_accepted === false).length)} rejected pairs. Original MACD runs remain visible; rejected opportunities become WAIT.</p>{pair && <p>Selected pair: {pair.liquidity_accepted ? "Accepted" : "Rejected"} · {pair.liquidity_rejection_reason?.replaceAll("_", " ")}</p>}</section>}
      <h2>Inspect one candle</h2><label className="research-field">Candle close ET<select aria-label="Price-action candle" value={row?.time_us ?? ""} onChange={e => setSelectedClock(Number(e.target.value))}>
        {chart?.labels.map(r => <option key={r.time_us} value={r.time_us}>{clock(r.time_us)} · {r.action}</option>)}</select></label>
      {row && <div className="research-candle-values"><dl><div><dt>Close / MACD episode</dt><dd>{number(row.close)} / {row.direction === 1 ? "L" : "S"}{row.episode_id}</dd></div>
        {row.liquidity_reason && <><div><dt>Prior 60s trades / shares / active seconds</dt><dd>{count(row.prior_trades_60s)} / {count(row.prior_shares_60s)} / {count(row.prior_active_seconds_60s)}</dd></div><div><dt>Prior trade age · seconds</dt><dd>{number(row.prior_trade_age_seconds)}</dd></div><div><dt>Candle liquidity / episode decision</dt><dd>{row.liquidity_reason.replaceAll("_", " ")} / {row.episode_liquidity_reason?.replaceAll("_", " ")}</dd></div></>}
        <div><dt>Displayed label / quality</dt><dd>{row.action} / {number(row.label_value)}</dd></div>
        <div><dt>ENTRY quality · if flat</dt><dd>{number(row.entry_quality)}</dd></div><div><dt>Discounted local entry gain</dt><dd>{number(row.entry_gain)}</dd></div>
        <div><dt>EXIT quality · reference entry</dt><dd>{number(row.exit_quality)}</dd></div><div><dt>Local exit price gain</dt><dd>{number(row.exit_gain)}</dd></div>
        <div><dt>Reference entry price</dt><dd>{number(row.entry_basis)}</dd></div><div><dt>Reference sequence label</dt><dd>{row.reference_action}</dd></div>
        <div><dt>ENTRY target close / horizon</dt><dd>{row.entry_target_us == null ? "—" : `${clock(row.entry_target_us)} / ${row.entry_horizon_seconds}s`}</dd></div>
        <div><dt>HOLD target close / horizon</dt><dd>{row.hold_target_us == null ? "—" : `${clock(row.hold_target_us)} / ${row.hold_horizon_seconds}s`}</dd></div>
        <div><dt>HOLD future target gain · $/share</dt><dd>{number(row.hold_target_gain ?? null)}</dd></div>
        <div><dt>HOLD discounted continuation · $/share</dt><dd>{number(row.hold_discounted_gain)}</dd></div>
        <div><dt>Liquidation comparison quality</dt><dd>{number(row.liquidation_quality)}</dd></div>
        <div><dt>Next opportunity carry · separate</dt><dd>{number(row.carried_next_pair_value)}</dd></div></dl></div>}
      <p className="research-muted">Arrow numbers: normalized quality above, raw gain ($/share) below. Gray dots = WAIT; blue dots = HOLD. Unlabelled held candles have no marker.</p>
      {experiment.both_opportunities > 0 && <p className="research-muted">{experiment.both_opportunities} candles qualify for both alternatives. Combined view shows EXIT; use ENTRY / WAIT to inspect their entry opportunities.</p>}
      <details><summary>Source & parameters</summary><p>{experiment.price_source}</p><p>{experiment.version} · tf={experiment.config.timeframe_seconds}s · {experiment.semantics}</p>
        {experiment.reporting && <><p>Reporting flags verified before bars were built. Delayed trades are excluded from OHLC and volume.</p><p>Whole source session: {count(experiment.reporting.counts.n)} trades · {count(experiment.reporting.counts.delayed)} delayed · {count(experiment.reporting.counts.unknown)} with unknown clocks. {experiment.reporting.revision}</p></>}
      </details></div>,
      chart: <div className="research-chart-container research-price-action-chart"><div className="research-controls">
        {!saved && <label>Opportunity quality<select aria-label="Opportunity quality threshold" value={threshold} onChange={e => setThreshold(Number(e.target.value))}>{[.8,.9,.95,1].map(t => <option key={t} value={t}>≥ {(t*100).toFixed(0)}%</option>)}</select></label>}
        <label>Label view<select aria-label="Opportunity label view" value={view} onChange={e => setView(e.target.value)}>{(!saved || saved.supports_combined) && <option value="combined">ENTRY / EXIT opportunities</option>}<option value="flat">ENTRY / WAIT</option><option value="held">EXIT / HOLD</option><option value="reference">Selected reference pair</option></select></label></div><div className="research-chart-nav">
        <button className="button secondary compact" disabled={busy || !chart?.previous_available} onClick={() => { setStart(chart!.start_us-900e6); setSelectedClock(null); }}>Previous 15 min</button>
        <span>{chart ? `${clock(chart.start_us-1e6)}–${clock(chart.end_us-1e6)} ET · 1s` : "Loading window"}</span>
        <button className="button secondary compact" disabled={busy || !chart?.next_available} onClick={() => { setStart(chart!.end_us); setSelectedClock(null); }}>Next 15 min</button></div>
      <p className="research-muted">1s MACD: green ≥ signal, red &lt; signal · ENTRY ↑ / EXIT ↓ · Top: quality 0–1 · Bottom: raw gain $/share · HOLD: blue · WAIT: gray</p>
      {experiment.config.liquidity_gate && <p className="research-muted">Liquidity-gated episodes: {count(experiment.pairs.filter(p => p.liquidity_accepted).length)} accepted / {count(experiment.pairs.filter(p => p.liquidity_accepted === false).length)} rejected. Inspect reasons and prior activity in Session &amp; episode values.</p>}
      <label className="research-checkbox"><input type="checkbox" checked={holdNumbers} onChange={e => setHoldNumbers(e.target.checked)} /><span>Show HOLD values</span></label>
      {busy ? <LoadingState fill label="Loading saved price-action labels" /> : error ? <div className="canvas-inline-error" role="alert">{error}<button onClick={() => setAttempt(a => a+1)}>Retry chart</button></div> : chart?.candles.length ?
        <ChartPanel ticker={experiment.ticker} timeframe="1s" timeframes={["1s"]} payload={payload}
          reference={row ? { time: row.time_us/1e6-1, startTime: row.time_us/1e6-1, endTime: row.time_us/1e6-1 } : undefined}
          featureOptions={[]} indicatorOptions={[]} visibleColumns={["macd_line", "macd_signal", "macd_histogram"]} visibleSupervisionGroups={[]}
          onTickerChange={() => {}} onTimeframeChange={() => {}} onVisibleColumnsChange={() => {}} onVisibleSupervisionGroupsChange={() => {}}
          tickerEditable={false} toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} fillHeight baseHeight={300}
          persistedOnly initialFitMode="last_market_day" settingsStorageKey={saved ? "research.saved-labels.chart.v1" : "research.price-action.chart.v2"}
          appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }} /> : <div className="research-empty">No valid-price candles in this window.</div>}
      <details className="research-label-detail"><summary>Read the labels on this chart</summary>
        <p>Minimum position: {experiment.config.minimum_position_seconds} seconds between closes. Short-duration entries become WAIT; held positions cannot EXIT earlier. ENTRY horizon reaches the strictly future L close maximizing discounted entry gain. HOLD horizon reaches the strictly future L close maximizing discounted continuation gain; its target gain is measured from the reference entry, separately from the existing current exit gain. Equal maxima choose the earliest close. Horizons use elapsed seconds between closes, including gaps; absent targets stay blank. These are hindsight audit attributes, excluded from model inputs.</p>
        <p>Each arrow has two rows: normalized quality first, then the saved unnormalized gain in dollars per share (four decimal places). ENTRY uses discounted future gain; EXIT uses undiscounted gain from the reference entry. {saved ? "Optional HOLD numbers show the quality complement and the same raw exit-gain target used by the held value head, including negative gains." : "Optional HOLD numbers show quality complements and current raw gain."} WAIT has no text; its raw entry gain remains available in the candle inspector.</p>
        <p>ENTRY gain = maximum of (later L close − current close) × 0.5^(elapsed seconds / {experiment.config.half_life_seconds}). ENTRY quality = this positive gain / best positive gain in the S→L pair. Quality ≥ {(threshold*100).toFixed(0)}% shows an up arrow.</p>
        <p>The reference entry is the pair’s best discounted entry. EXIT gain = current L close − reference entry close; Continuation = max positive later gain × 0.5^(seconds ahead / {experiment.config.half_life_seconds}). The main EXIT is the earliest positive gain ≥ continuation; its first contiguous winning cluster shows red arrows. Other held candles remain HOLD; no later EXIT cluster reopens the trade. Both exit clocks must follow the reference entry. The held branch shows only candles with a saved exit target; the remaining candles have no held supervision.</p>
        <p>Several arrows describe alternative opportunities. The “Selected reference pair” view shows just one chronological entry/exit pair per profitable S→L pair. Blue HOLD dots lie between those reference points; gray WAIT dots lie outside. Flat/held views preserve each conditional alternative. Carry is a discounted maximum with the next opportunity; it is not added to quality scores.</p>
      </details></div> }}
  </ResearchCanvas>;
}
