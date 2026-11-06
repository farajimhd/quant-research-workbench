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

type Pair = { selected_entry_us?: number; selected_target_us?: number; selected_entry_ask?: number; selected_target_bid?: number; score_threshold?: number; right_censored?: boolean; selected?: boolean; selection_reason?: string; selection_score?: number; allocation_ratio?: number; group_id?: number; liquidity_accepted?: boolean; liquidity_rejection_reason?: string; pair_id: number; short_episode: number | null; long_episode: number; start_us: number; end_us: number;
  best_start_price: number; best_end_price: number; best_stop: number; best_target: number; reference_range: number; reference_entry_us: number | null; reference_exit_us: number | null; reference_entry_price: number | null; reference_exit_price: number | null; best_entry_gain: number; best_exit_gain: number; carried_next_pair_value: number };
type Row = { quote_source?: string; quote_age_seconds?: number; target_quote_age_seconds?: number; quote_book_reason?: string; target_quote_book_reason?: string; economic_label_valid?: boolean; spread_discounted_gain?: number; selected_entry_ask?: number; bid?: number; ask?: number; target_bid?: number; spread_score?: number; spread_threshold?: number; spread_entry_reason?: string; spread_exit_score?: number; scanner_member?: boolean; action_1a?: string; episode_selected?: boolean; allocation_ratio?: number; liquidity_eligible?: boolean; liquidity_reason?: string; episode_liquidity_reason?: string; prior_trades_60s?: number; prior_shares_60s?: number; prior_active_seconds_60s?: number; prior_trade_age_seconds?: number | null; time_us: number; episode_id: number; pair_id: number; direction: number; close: number; action: string;
  model_features?: { part: string; scalar: number[]; levels: number[][][]; input_close_us: number[]; input_padding: number | null; diagnostic_input_close_us?: number[] };
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
  overlay_series?: ChartPayload["overlay_series"]; context_candles?: number; session_candles?: number; offset?: number; previous_offset?: number; next_offset?: number; scalar_names?: string[]; level_names?: string[];
  regions: ChartPayload["regions"]; labels: Row[]; start_us: number; end_us: number; previous_available: boolean; next_available: boolean };
const cache = new Map<string, Window>();
const number = (value: number | null | undefined) => value == null ? "—" : value.toFixed(4);
const count = (value: number | null | undefined) => value == null ? "—" : value.toLocaleString("en-US");
const titles = { architecture: "Price-action algorithm", analytics: "Session & episode values", chart: "Price-action candles & labels" };

export function PriceActionResearch({ saved }: { saved?: { day: string; listing_id: string; dataset_sha256: string; supports_combined: boolean; v66?: boolean; phase?: string; pair_id?: number; start_us?: number; end_us?: number } }) {
  const prefix = saved ? `saved-labels:${saved.v66 ? saved.phase + ":" + (saved.pair_id ?? "") + ":" : ""}${saved.dataset_sha256}:${saved.day}:${saved.listing_id}` : "price-action";
  const endpoint = saved?.v66 ? "/api/research/models/v66/labels" : saved ? "/api/research/models/v6/saved-labels" : "/api/research/models/v6/price-action";
  const [experiment, setExperiment] = useState<Experiment | null>(null);
  const [chart, setChart] = useState<Window | null>(null), [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0), [busy, setBusy] = useState(true);
  const [start, setStart] = useResearchState<number | null>(`${prefix}:actual-candles:start`, saved?.start_us ?? null);
  const [offset, setOffset] = useResearchState(`${prefix}:actual-candles:offset`, 0);
  const [showLevels, setShowLevels] = useResearchState(`${prefix}:model-levels`, true);
  const [pairId, setPairId] = useResearchState(`${prefix}:pair`, saved?.pair_id ?? 1);
  const [selectedClock, setSelectedClock] = useResearchState<number | null>(`${prefix}:clock`, null);
  const [threshold, setThreshold] = useResearchState(`${prefix}:v2:threshold`, .9);
  const [view, setView] = useResearchState(`${prefix}:v3:view`, saved && !saved.supports_combined ? "flat" : "combined");
  const [holdNumbers, setHoldNumbers] = useResearchState(`${prefix}:hold-values`, false);
  useEffect(() => {
    if (saved?.v66 && saved.end_us != null) {
      setStart(saved.start_us ?? null); setOffset(0); setView("combined");
    }
  }, []);
  useEffect(() => {
    const abort = new AbortController(); setError("");
    api<Experiment>(`${endpoint}${saved ? "/metadata" : ""}${query({ phase: saved?.phase, day: saved?.day, listing_id: saved?.listing_id, quality_threshold: saved ? undefined : threshold })}`, { signal: abort.signal, timeoutMs: 300000 }).then(setExperiment)
      .catch(reason => { if (!abort.signal.aborted) { setError(String(reason)); setBusy(false); } });
    return () => abort.abort();
  }, [attempt, threshold]);
  const ready = Boolean(experiment);
  useEffect(() => {
    if (!ready) return;
    const abort = new AbortController(); setBusy(true); setError("");
    const url = `${endpoint}/chart${query({ phase: saved?.phase, day: saved?.day, listing_id: saved?.listing_id, dataset_sha256: saved?.dataset_sha256, candle_offset: saved ? offset : undefined, start_us: start ?? undefined, segment_end_us: saved?.v66 && start === saved.start_us ? saved.end_us : undefined, seconds: 900, quality_threshold: saved ? undefined : threshold, view })}`;
    const cached = cache.get(url);
    if (cached && !attempt) { setChart(cached); setBusy(false); return () => abort.abort(); }
    api<Window>(url, { signal: abort.signal, timeoutMs: 300000 }).then(result => {
      if (!abort.signal.aborted) { if (cache.size >= 32) cache.delete(cache.keys().next().value!); cache.set(url, result); setChart(result); }
    }).catch(reason => { if (!abort.signal.aborted) setError(String(reason)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [ready, start, offset, attempt, threshold, view]);
  const pair = experiment?.pairs.find(p => p.pair_id === pairId);
  const row = chart?.labels.find(r => r.time_us === selectedClock) ?? chart?.labels.find(r => r.action === "ENTRY") ?? chart?.labels[0];
  const payload = useMemo<ChartPayload>(() => ({ candles: chart?.candles ?? [], volume: [], overlay_series: chart?.overlay_series ?? [],
    position_ranges: saved?.v66 ? (experiment?.pairs ?? []).filter(p => saved.phase !== "1b" || p.selected).flatMap(p => {
      const focused = p.pair_id === saved.pair_id && saved.start_us != null && saved.end_us != null;
      const spread = saved.phase === "1b" && p.selected_entry_us != null;
      const entryTime = focused ? saved.start_us! / 1e6 - 1 : ((spread ? p.selected_entry_us : p.reference_entry_us) ?? 0) / 1e6 - 1;
      const exitTime = focused ? saved.end_us! / 1e6 - 1 : ((spread ? p.selected_target_us : p.reference_exit_us) ?? 0) / 1e6 - 1;
      const entryPrice = focused ? chart?.candles.find(c => c.time === entryTime)?.close ?? null : spread ? p.selected_entry_ask ?? null : p.reference_entry_price;
      const exitPrice = focused ? chart?.candles.find(c => c.time === exitTime)?.close ?? null : spread ? p.selected_target_bid ?? null : p.reference_exit_price;
      return entryTime >= 0 && exitTime > entryTime && entryPrice != null && exitPrice != null
        ? [{ id: String(p.pair_id), entryTime, exitTime, entryPrice, exitPrice }] : [];
    }) : [],
    oscillator_series: chart?.oscillator_series ?? [], regions: (chart?.regions ?? []).map(r => ({ ...r, color: researchBandColor(r.color) })),
    markers: (chart?.labels ?? []).filter(r => !["UNLABELLED","CONTEXT"].includes(r.action)).map((r, i) => ({ id: `price-action-${i}`, time: (r.time_us/1e6-1) as UTCTimestamp,
      position: r.action === "EXIT" ? "aboveBar" : "belowBar", size: .5,
      shape: r.action === "ENTRY" ? "arrowUp" : r.action === "EXIT" ? "arrowDown" : "circle",
      color: r.action === "ENTRY" ? "var(--success)" : r.action === "EXIT" ? "var(--danger)" : r.action === "HOLD" ? "var(--info)" : "var(--muted-foreground)",
      text: r.action === "WAIT" || (r.action === "HOLD" && !holdNumbers) ? "" : `${r.label_value?.toFixed(3) ?? ""}${r.action === "ENTRY" && r.entry_horizon_seconds != null ? ` · ${r.entry_horizon_seconds}s` : r.action === "HOLD" && r.hold_horizon_seconds != null ? ` · ${r.hold_horizon_seconds}s` : ""}`,
      secondaryText: r.action === "ENTRY" ? (saved?.phase === "1b" && r.spread_discounted_gain != null ? r.spread_discounted_gain : r.entry_gain).toFixed(4) : (r.action === "EXIT" || (r.action === "HOLD" && holdNumbers)) && r.exit_gain != null ? (saved?.phase === "1b" && r.bid != null && r.selected_entry_ask != null ? r.bid-r.selected_entry_ask : r.exit_gain).toFixed(4) : undefined })) }), [chart, holdNumbers, saved, experiment]);
  if (!experiment) return <div className="research-page">{error ? <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Retry experiment</button></div> : <LoadingState label="Loading saved price-action experiment" />}</div>;
  return <ResearchCanvas stacked={saved?.v66} storageKey={saved ? prefix : "price-action:NVDA:2026-07-31"} titles={titles}
    icons={{ architecture: <Network size={14} />, analytics: <Microscope size={14} />, chart: <ChartCandlestick size={14} /> }}
    sources={{ architecture: saved?.v66 ? "V66 review-only labels" : saved ? "Published 1a label authority" : "Experimental · not used in teacher training", analytics: saved ? "Published full-session shard" : "Full RTH session · saved price-action values", chart: "Saved 1s prices & labels · no fill model" }}
    toolbar={<><strong>{saved?.v66 ? `${experiment.ticker} · Price-action candles & labels` : saved ? "1a · Original labels" : "Long price-action experiment"}</strong><span>{saved?.v66 ? `${saved.phase} labels · ${experiment.day}` : `${experiment.ticker} · ${experiment.day}`} · {saved?.v66 ? "04:00–09:30" : saved ? "04:00–20:00" : "09:30–16:00"} ET{saved?.v66 && saved.end_us != null ? ` · Pair #${saved.pair_id} · ${clock(saved.start_us!)}–${clock(saved.end_us)} ET` : ""}</span></>}>
    {{ architecture: <div className="research-container"><h2>Price action only</h2>{saved?.v66 ? <p>Saved premarket shard prices and session-reset MACD. Labels attach to candle closes. 1b uses all stored tickers; scanner membership is shown separately. Final pairs are bounded by the stored data.</p> : <p>Targets are attached to candle close t, never open. Model features use only candles with close time &lt; t: the target candle is excluded, including its OHLC, indicators and held-position mark. Hindsight targets use later prices and are retrospective supervision.</p>}{saved && <details><summary>Published dataset identity</summary><p className="research-source">SHA-256 {saved.dataset_sha256}</p></details>}<p>1s MACD · {experiment.config.half_life_seconds}s discount half-life · {saved?.v66 ? "1a price-only gains; 1b applies its fee/return selection floors." : "zero fees."}</p>
      {saved && !saved.v66 && <p>The 120 context + 120 session chart is an audit view. Model supervision uses a rolling 120 actual bank rows strictly before the target, including unpriced rows with masks. VWAP and the ten V7 slots decode stored float32 features; training then converts units and applies train-only normalization.</p>}
      <p>Each liquidity-qualified short→long pair is one opportunity. ENTRY quality compares its discounted future gain with the pair’s best entry gain. EXIT compares liquidation now with discounted future holding; only the first contiguous winning cluster receives EXIT.</p>
      <p>Quality ≥ {(threshold*100).toFixed(0)}% gives an opportunity arrow. Several candles may qualify. One reference entry/exit pair measures the price change; opportunity arrows are alternatives, not repeated trades.</p>
      <p className="research-muted">Scores stay between 0 and 1. Carry to the next opportunity is separate. Stop/target are references.</p></div>,
      analytics: <div className="research-container research-analytics"><h2>Full-session results</h2><dl>
        <div><dt>Valid-price candles</dt><dd>{count(experiment.observed_price_candles)}</dd></div>
        <div><dt>{saved?.v66 ? "Invalid price / MACD warm-up rows" : "Invalid-price activity rows"}</dt><dd>{count(experiment.omitted_invalid_price_rows)} · unlabelled</dd></div>
        <div><dt>Absent second slots</dt><dd>{count(experiment.absent_second_slots)}</dd></div>
        {!saved && experiment.approximate_volume != null && <div><dt>Approximate volume</dt><dd>{count(Math.round(experiment.approximate_volume))}</dd></div>}
        <div><dt>Selected reference pairs</dt><dd>{count(experiment.trades)}</dd></div>
        <div><dt>Sum of undiscounted price changes</dt><dd>{number(experiment.total_price_pnl)}</dd></div>
        <div><dt>Short → long pairs</dt><dd>{count(experiment.pairs.length)}</dd></div></dl>
      {saved && !saved.v66 ? <table><thead><tr><th>Branch</th><th>Teacher label</th><th>Candles</th></tr></thead><tbody>{experiment.teacher_actions?.map(a => <tr key={`${a.branch}:${a.action}`}><th>{a.branch}</th><td>{a.action}</td><td>{count(a.len)}</td></tr>)}</tbody></table> : <table><thead><tr><th>Label</th><th>Candles</th></tr></thead><tbody>{experiment.actions.map(a => <tr key={a.action}><th>{a.action}</th><td>{count(a.len)}</td></tr>)}</tbody></table>}
      <h2>Short → long pair</h2><label className="research-field">Episode pair<select aria-label="Price-action episode pair" value={pairId} onChange={e => {
        const id = Number(e.target.value); setPairId(id); setSelectedClock(null); setStart(experiment.pairs.find(p => p.pair_id === id)!.start_us);
      }}>{experiment.pairs.map(p => <option key={p.pair_id} value={p.pair_id}>#{p.pair_id} · {p.short_episode == null ? "Starts long" : `S${p.short_episode}`} → L{p.long_episode} · {clock(p.start_us-1e6)}{p.liquidity_accepted === false ? " · rejected" : ""}</option>)}</select></label>
      {pair && <dl>{saved?.v66 && <><div><dt>Stored-data boundary</dt><dd>{pair.right_censored ? "Right-censored at final observed candle" : "Complete MACD pair"}</dd></div><div><dt>1b selection</dt><dd>{pair.selection_reason} · group {pair.group_id ?? "—"}</dd></div><div><dt>1b score / relative allocation</dt><dd>{number(pair.selection_score)} / {number(pair.allocation_ratio)}</dd></div></>}<div><dt>Pair starts / ends</dt><dd>{clock(pair.start_us-1e6)} / {clock(pair.end_us-1e6)}</dd></div>
        {pair.selected_entry_ask != null && <><div><dt>1b entry ask / target bid</dt><dd>{number(pair.selected_entry_ask)} / {number(pair.selected_target_bid)}</dd></div><div><dt>1b net discounted score / floor</dt><dd>{pair.selection_score == null ? "Rejected" : `${(pair.selection_score*100).toFixed(4)}%`} / {pair.score_threshold == null ? "—" : `${(pair.score_threshold*100).toFixed(4)}%`}</dd></div></>}
        <div><dt>Best start reference</dt><dd>{number(pair.best_start_price)}</dd></div><div><dt>Best end reference</dt><dd>{number(pair.best_end_price)}</dd></div>
        <div><dt>Stop reference · offset {experiment.config.stop_offset}</dt><dd>{number(pair.best_stop)}</dd></div><div><dt>Target reference</dt><dd>{number(pair.best_target)}</dd></div>
        <div><dt>Reference range</dt><dd>{number(pair.reference_range)}</dd></div>
        <div><dt>Selected entry / exit close ET</dt><dd>{pair.reference_entry_us == null ? (pair.liquidity_accepted === false ? "Rejected by liquidity gate" : "No positive gain") : `${clock(pair.reference_entry_us)} / ${clock(pair.reference_exit_us!)}`}</dd></div>
        <div><dt>Selected entry / exit price</dt><dd>{number(pair.reference_entry_price)} / {number(pair.reference_exit_price)}</dd></div>
        <div><dt>Best discounted entry gain</dt><dd>{number(pair.best_entry_gain)}</dd></div><div><dt>Best exit price gain</dt><dd>{number(pair.best_exit_gain)}</dd></div></dl>}
      {experiment.config.liquidity_gate && <section aria-label="Episode liquidity audit"><h2>Episode liquidity audit</h2><p>Start requires {experiment.config.minimum_trades_60s} trades, {count(experiment.config.minimum_shares_60s)} shares and {experiment.config.minimum_active_seconds_60s} active seconds in the prior 60 seconds. Last trade age upper bound must be within {experiment.config.maximum_inactivity_seconds}s. Target candle excluded.</p><p>{count(experiment.pairs.filter(p => p.liquidity_accepted).length)} accepted / {count(experiment.pairs.filter(p => p.liquidity_accepted === false).length)} rejected pairs. Original MACD runs remain visible; rejected opportunities become WAIT.</p>{pair && <p>Selected pair: {pair.liquidity_accepted ? "Accepted" : "Rejected"} · {pair.liquidity_rejection_reason?.replaceAll("_", " ")}</p>}</section>}
      <h2>Inspect one candle</h2><label className="research-field">Candle close ET<select aria-label="Price-action candle" value={row?.time_us ?? ""} onChange={e => setSelectedClock(Number(e.target.value))}>
        {chart?.labels.map(r => <option key={r.time_us} value={r.time_us}>{clock(r.time_us)} · {r.action}</option>)}</select></label>
      {row && <div className="research-candle-values"><dl><div><dt>Close / MACD episode</dt><dd>{number(row.close)} / {row.action === "CONTEXT" ? "Historical context" : `${row.direction === 1 ? "L" : "S"}${row.episode_id}`}</dd></div>
        {row.quote_source && <><div><dt>Pricing source</dt><dd>Certified execution liquidity · standing book</dd></div><div><dt>Quote age · seconds</dt><dd>{number(row.quote_age_seconds)} · target {number(row.target_quote_age_seconds)}</dd></div><div><dt>Book evidence · current / target</dt><dd>{row.quote_book_reason} / {row.target_quote_book_reason ?? 'Not applicable'}</dd></div><div><dt>Economic label evidence</dt><dd>{row.economic_label_valid ? 'Valid' : 'Unknown · excluded from supervision'}</dd></div></>}
        {experiment.version.includes('spread') && <><div><dt>Saved bid / ask</dt><dd>{number(row.bid)} / {number(row.ask)}</dd></div><div><dt>Entry target bid</dt><dd>{number(row.target_bid)}</dd></div><div><dt>Spread-adjusted entry score / floor</dt><dd>{number(row.spread_score == null ? null : row.spread_score*100)}% / {number(row.spread_threshold == null ? null : row.spread_threshold*100)}%</dd></div><div><dt>Entry eligibility</dt><dd>{row.spread_entry_reason}</dd></div><div><dt>Canonical exit score</dt><dd>{number(row.spread_exit_score == null ? null : row.spread_exit_score*100)}%</dd></div></>}
        {row.model_features && <><div><dt>Displayed candle source</dt><dd>{row.model_features.part === "context" ? "Historical context · no current-session target" : "Current session"}</dd></div><div><dt>Model input before this target</dt><dd>{row.model_features.part === "context" ? "Context source row" : `${row.model_features.input_close_us.length} actual rows · ${row.model_features.input_padding} padding slots · target excluded`}</dd></div><div><dt>Input first / last close ET</dt><dd>{row.model_features.input_close_us.length ? `${clock(row.model_features.input_close_us[0])} / ${clock(row.model_features.input_close_us.at(-1)!)}` : "—"}</dd></div></>}
        {row.liquidity_reason && <><div><dt>Prior 60s trades / shares / active seconds</dt><dd>{count(row.prior_trades_60s)} / {count(row.prior_shares_60s)} / {count(row.prior_active_seconds_60s)}</dd></div><div><dt>Prior trade age upper bound · seconds</dt><dd>{number(row.prior_trade_age_seconds)}</dd></div><div><dt>Candle liquidity / episode decision</dt><dd>{row.liquidity_reason.replaceAll("_", " ")} / {row.episode_liquidity_reason?.replaceAll("_", " ")}</dd></div></>}
        <div><dt>Displayed label / quality</dt><dd>{row.action} / {number(row.label_value)}</dd></div>
        {saved?.v66 && <><div><dt>Causal top-five member</dt><dd>{row.scanner_member ? "Yes" : "No"}</dd></div><div><dt>Original 1a / selected by 1b</dt><dd>{row.action_1a} / {row.episode_selected == null ? "Outside pair" : row.episode_selected ? "Yes" : "No"}</dd></div><div><dt>1b allocation target</dt><dd>{number(row.allocation_ratio)}</dd></div></>}<div><dt>ENTRY quality · if flat</dt><dd>{number(row.entry_quality)}</dd></div><div><dt>Discounted local entry gain</dt><dd>{number(row.entry_gain)}</dd></div>
        <div><dt>EXIT quality · reference entry</dt><dd>{number(row.exit_quality)}</dd></div><div><dt>Local exit price gain</dt><dd>{number(row.exit_gain)}</dd></div>
        <div><dt>Reference entry price</dt><dd>{number(row.entry_basis)}</dd></div><div><dt>Reference sequence label</dt><dd>{row.reference_action}</dd></div>
        <div><dt>ENTRY target close / horizon</dt><dd>{row.entry_target_us == null ? "—" : `${clock(row.entry_target_us)} / ${row.entry_horizon_seconds}s`}</dd></div>
        <div><dt>HOLD target close / horizon</dt><dd>{row.hold_target_us == null ? "—" : `${clock(row.hold_target_us)} / ${row.hold_horizon_seconds}s`}</dd></div>
        <div><dt>HOLD future target gain · $/share</dt><dd>{number(row.hold_target_gain ?? null)}</dd></div>
        <div><dt>HOLD discounted continuation · $/share</dt><dd>{number(row.hold_discounted_gain)}</dd></div>
        <div><dt>Liquidation comparison quality</dt><dd>{number(row.liquidation_quality)}</dd></div>
        <div><dt>Next opportunity carry · separate</dt><dd>{number(row.carried_next_pair_value)}</dd></div></dl>
        {row.model_features && <details><summary>Exact stored model features · VWAP and V7</summary><p>These float32 bank values are the source features. Training converts distances to bps and applies train-only normalization. V7 shows the five below and five above slots; masked slots are absent. The existing local ResNet diagnostic uses an inclusive target window, unlike the main V6 strict-prior teacher path.</p><p>Local ResNet diagnostic window: {row.model_features.diagnostic_input_close_us?.length ?? 0} actual rows · includes target candle. This audit does not change training alignment.</p><table><thead><tr><th>Scalar</th><th>Stored value</th></tr></thead><tbody>{row.model_features.scalar.map((v,i) => <tr key={i}><th>{chart?.scalar_names?.[i]}</th><td>{v.toPrecision(9)}</td></tr>)}</tbody></table><table><thead><tr><th>V7 slot</th><th>Role / presence</th><th>Stored V7 fields · manifest order</th></tr></thead><tbody>{row.model_features.levels.flatMap((slots,side) => slots.map((v,slot) => <tr key={`${side}:${slot}`}><th>{side ? "Above" : "Below"} {slot+1}</th><td>{!v[10] ? "Absent" : v[6] ? "Support" : v[7] ? "Resistance" : "Transition"}</td><td>{v.map(x=>x.toPrecision(9)).join(" / ")}</td></tr>))}</tbody></table></details>}
        </div>}
      <p className="research-muted">Arrow numbers: normalized quality above, raw gain ($/share) below. Gray dots = WAIT; blue dots = HOLD. Unlabelled held candles have no marker.</p>
      {experiment.both_opportunities > 0 && <p className="research-muted">{experiment.both_opportunities} candles qualify for both alternatives. Combined view shows EXIT; use ENTRY / WAIT to inspect their entry opportunities.</p>}
      <details><summary>Source & parameters</summary><p>{experiment.price_source}</p><p>{experiment.version} · tf={experiment.config.timeframe_seconds}s · {experiment.semantics}</p>
        {experiment.reporting && <><p>Reporting flags verified before bars were built. Delayed trades are excluded from OHLC and volume.</p><p>Whole source session: {count(experiment.reporting.counts.n)} trades · {count(experiment.reporting.counts.delayed)} delayed · {count(experiment.reporting.counts.unknown)} with unknown clocks. {experiment.reporting.revision}</p></>}
      </details></div>,
      chart: <div className="research-chart-container research-price-action-chart"><div className="research-controls">
        {!saved && <label>Opportunity quality<select aria-label="Opportunity quality threshold" value={threshold} onChange={e => setThreshold(Number(e.target.value))}>{[.8,.9,.95,1].map(t => <option key={t} value={t}>≥ {(t*100).toFixed(0)}%</option>)}</select></label>}
        <label>Label view<select aria-label="Opportunity label view" value={view} onChange={e => setView(e.target.value)}>{(!saved || saved.supports_combined) && <option value="combined">ENTRY / EXIT opportunities</option>}<option value="flat">ENTRY / WAIT</option><option value="held">EXIT / HOLD</option><option value="reference">Selected reference pair</option></select></label></div><div className="research-chart-nav">
        <button className="button secondary compact" disabled={busy || !chart?.previous_available} onClick={() => { setStart(saved ? null : chart!.start_us-900e6); if (saved) setOffset(chart!.previous_offset!); setSelectedClock(null); }}>{saved ? "Previous 120 candles" : "Previous 15 min"}</button>
        <span>{chart ? saved?.v66 ? `${clock(chart.start_us)}–${clock(chart.end_us-1e6)} ET · ${chart.session_candles} saved candles · 1s` : saved ? `${chart.context_candles} context + ${chart.session_candles} session candles · 1s` : `${clock(chart.start_us-1e6)}–${clock(chart.end_us-1e6)} ET · 1s` : "Loading window"}</span>
        <button className="button secondary compact" disabled={busy || !chart?.next_available} onClick={() => { setStart(saved ? null : chart!.end_us); if (saved) setOffset(chart!.next_offset!); setSelectedClock(null); }}>{saved ? "Next 120 candles" : "Next 15 min"}</button></div>
      <div className="research-chart-toggles"><label className="research-checkbox"><input type="checkbox" checked={holdNumbers} onChange={e => setHoldNumbers(e.target.checked)} /><span>Show HOLD values</span></label>
      {saved && !saved.v66 && <label className="research-checkbox"><input type="checkbox" checked={showLevels} onChange={e=>setShowLevels(e.target.checked)} /><span title="Nearest five below and above price at each candle; slot identities may change.">V7 ranked slots</span></label>}</div>
      {busy ? <LoadingState fill label="Loading saved price-action labels" /> : error ? <div className="canvas-inline-error" role="alert">{error}<button onClick={() => setAttempt(a => a+1)}>Retry chart</button></div> : chart?.candles.length ?
        <ChartPanel ticker={experiment.ticker} timeframe="1s" timeframes={["1s"]} payload={payload}
          reference={saved?.v66 && start === saved.start_us && saved.start_us != null && saved.end_us != null ? { time: saved.start_us/1e6-1, startTime: saved.start_us/1e6-1, endTime: saved.end_us/1e6-1, highlightRange: true } : !saved && row ? { time: row.time_us/1e6-1, startTime: row.time_us/1e6-1, endTime: row.time_us/1e6-1 } : undefined}
          featureOptions={[]} indicatorOptions={[]} visibleColumns={["macd_line", "macd_signal", "macd_histogram", "bar_vwap", "session_vwap", ...(showLevels ? (chart.overlay_series ?? []).filter(s=>s.column.endsWith("_center")).map(s=>s.column) : [])]} visibleSupervisionGroups={[]}
          onTickerChange={() => {}} onTimeframeChange={() => {}} onVisibleColumnsChange={() => {}} onVisibleSupervisionGroupsChange={() => {}}
          tickerEditable={false} toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} fillHeight baseHeight={300}
          persistedOnly initialFitMode={saved ? "default" : "last_market_day"} settingsStorageKey={saved?.v66 ? "research.v66-labels.candles.v2" : saved ? "research.saved-labels.model-candles.v1" : "research.price-action.chart.v2"}
          appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }} /> : <div className="research-empty">{saved ? "This listing has no valid-price candles in the certified session or available context. Its activity rows have no price targets." : "No valid-price candles in this window."}</div>}
      <details className="research-label-detail"><summary>Chart guide</summary>{saved?.v66 ? <p>Saved V66 premarket candles only; no V7 or previous-session context. Missing-price and MACD warm-up rows have no label. The table shows saved 1b allocation, selection and scanner membership separately.</p> : <p>Historical context has no teacher markers. VWAP and V7 values come from the saved model bank. V7 lines are ranked slots, not persistent level identities: a slot can switch levels as price crosses their centers. MACD colors compare line with signal. HOLD is blue; WAIT is gray. Episode liquidity decisions are in Session &amp; episode values.</p>}
        {experiment.version.includes('spread') && <p>Spread-aware 1b uses the saved entry ask and target bid, time discount and round-trip fees. In the 1b view, arrow gains and horizontal prices use quotes; 1a retains the original trade-price comparison. Pair and candle details below show scores, floors and quote exclusions.</p>}
        <p>Minimum position: {experiment.config.minimum_position_seconds} seconds between closes. Short-duration entries become WAIT; held positions cannot EXIT earlier. ENTRY horizon reaches the strictly future L close maximizing discounted entry gain. HOLD horizon reaches the strictly future L close maximizing discounted continuation gain; its target gain is measured from the reference entry, separately from the existing current exit gain. Equal maxima choose the earliest close. Horizons use elapsed seconds between closes, including gaps; absent targets stay blank. These are hindsight audit attributes, excluded from model inputs.</p>
        <p>Each arrow has two rows: normalized quality first, then the saved unnormalized gain in dollars per share (four decimal places). ENTRY uses discounted future gain; EXIT uses undiscounted gain from the reference entry. {saved ? "Optional HOLD numbers show the quality complement and the same raw exit-gain target used by the held value head, including negative gains." : "Optional HOLD numbers show quality complements and current raw gain."} WAIT has no text; its raw entry gain remains available in the candle inspector.</p>
        <p>ENTRY gain = maximum of (later L close − current close) × 0.5^(elapsed seconds / {experiment.config.half_life_seconds}). ENTRY quality = this positive gain / best positive gain in the S→L pair. Quality ≥ {(threshold*100).toFixed(0)}% shows an up arrow.</p>
        <p>The reference entry is the pair’s best discounted entry. EXIT gain = current L close − reference entry close; Continuation = max positive later gain × 0.5^(seconds ahead / {experiment.config.half_life_seconds}). The main EXIT is the earliest positive gain ≥ continuation; its first contiguous winning cluster shows red arrows. Other held candles remain HOLD; no later EXIT cluster reopens the trade. Both exit clocks must follow the reference entry. The held branch shows only candles with a saved exit target; the remaining candles have no held supervision.</p>
        <p>Several arrows describe alternative opportunities. The “Selected reference pair” view shows just one chronological entry/exit pair per profitable S→L pair. Blue HOLD dots lie between those reference points; gray WAIT dots lie outside. Flat/held views preserve each conditional alternative. Carry is a discounted maximum with the next opportunity; it is not added to quality scores.</p>
      </details></div> }}
  </ResearchCanvas>;
}
