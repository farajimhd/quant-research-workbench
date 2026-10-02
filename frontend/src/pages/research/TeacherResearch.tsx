import { ArrowLeft, BarChart3, ChartCandlestick, Microscope, Network, RefreshCcw, ShieldCheck } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type Dispatch, type SetStateAction } from "react";
import type { UTCTimestamp } from "lightweight-charts";
import { api, query } from "../../api/client";
import { ChartPanel, type ChartPayload } from "../../app/components/ChartPanel";
import { LoadingState } from "../../app/components/LoadingState";
import { WorkspaceWindow, type WorkspaceWindowLayout } from "../../app/components/WorkspaceCanvas";
import "./TeacherResearch.css";

type Catalog = { models: { id: string; name: string; days: { day: string; role: string }[] }[]; heldout: string };
type Ticker = { listing_id: string; ticker: string; rows: number; episodes: number };
type Audit = { day: string; role: string; checks: string[]; candle_check: string; version: string; certificate_sha256: string; analytics: {
  classes: { action: string; rows: number; weighted_soft_mass: number }[];
  distributions: { branch: string; range: string; rows: number }[];
  branches: { branch: string; episodes: number; median_episode_rows: number; median_span_seconds: number; overlapping_clocks: number; extra_overlap_rows: number; mean_probability: number }[];
  tickers: Ticker[]; scope: string; coverage: string;
} };
type Chart = { selected_episode: string; label_config: { min_score: number; min_peak_headroom: number; fee_per_share: number }; oscillator_series: ChartPayload["oscillator_series"]; regions: ChartPayload["regions"]; reward_units: string; ticker: string; candles: ChartPayload["candles"]; labels: { episode_uid: string; time_us: number; probability: number; sample_weight: number; branch: string; reward: number | null }[]; start_us: number; end_us: number; episodes: string[]; previous_available: boolean; next_available: boolean; omitted_invalid_price_rows: number; source: string };
// Keep the Research workspace across route navigation; reload requires preflight.
const researchState = new Map<string, unknown>();
const chartCache = new Map<string, Chart>();
function useResearchState<T,>(key: string, initial: T): [T, Dispatch<SetStateAction<T>>] {
  const [value, setValue] = useState<T>(() => researchState.has(key) ? researchState.get(key) as T : initial);
  useEffect(() => { researchState.set(key, value); }, [key, value]);
  return [value, setValue];
}
type Id = "architecture" | "analytics" | "chart";
const titles: Record<Id, string> = { architecture: "Model architecture & details", analytics: "Teacher data & label analytics", chart: "Candles & hindsight labels" };
const icons = { architecture: <Network size={14} />, analytics: <BarChart3 size={14} />, chart: <ChartCandlestick size={14} /> };
const n = (value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 2 });
const clock = (us: number) => new Date(us / 1000).toLocaleTimeString("en-GB", { timeZone: "America/New_York", hour12: false });
const message = (reason: unknown) => reason instanceof Error ? reason.message : String(reason);

export function ResearchWorkspacePage() {
  const [catalog, setCatalog] = useState<Catalog | null>(null), [day, setDay] = useResearchState("day", "2026-07-31");
  const [audit, setAudit] = useResearchState<Audit | null>("audit", null), [error, setError] = useState("");
  const [busy, setBusy] = useState(false), [workspace, setWorkspace] = useResearchState("workspace", false), [attempt, setAttempt] = useState(0);
  const pending = useRef<AbortController | null>(null);
  useEffect(() => {
    const abort = new AbortController();
    api<Catalog>("/api/research/models", { signal: abort.signal }).then(setCatalog).catch(reason => { if (!abort.signal.aborted) setError(message(reason)); });
    return () => { abort.abort(); pending.current?.abort(); };
  }, [attempt]);
  async function inspect() {
    pending.current?.abort(); const abort = new AbortController(); pending.current = abort;
    setBusy(true); setError(""); setAudit(null);
    try { const result = await api<Audit>(`/api/research/models/v6/preflight${query({ day })}`, { signal: abort.signal, timeoutMs: 300000 }); if (!abort.signal.aborted) setAudit(result); }
    catch (reason) { if (!abort.signal.aborted) setError(message(reason)); }
    finally { if (!abort.signal.aborted) setBusy(false); }
  }
  if (workspace && audit) return <TeacherCanvas audit={audit} onBack={() => setWorkspace(false)} />;
  return <div className="research-page research-setup">
    <header className="research-heading"><Microscope size={20} /><div><h1>Research</h1><p>Step 1 · Teacher training · Inspect saved hindsight supervision before training.</p></div></header>
    <section className="research-selection"><h2>Choose a research model</h2><div className="research-controls">
      <label>Model<select aria-label="Research model" disabled={!catalog}><option value="v6">RL trading V6</option></select></label>
      <label>Label session<select aria-label="Label session" value={day} disabled={busy || !catalog} onChange={e => { setDay(e.target.value); setAudit(null); setError(""); }}>{catalog?.models[0].days.map(d => <option key={d.day} value={d.day}>{d.day} · {d.role === "train" ? "Training" : "Development"}</option>)}</select></label>
      <button className="button secondary" disabled={busy || !catalog} onClick={() => void inspect()}><ShieldCheck size={15} />{busy ? "Checking sources…" : "Preflight labels"}</button>
    </div><p>Hindsight MACD 1s episodes · Original extended ENTRY / WAIT and HOLD / EXIT soft targets.</p><p className="research-muted">{catalog?.heldout ?? "August 26 heldout remains sealed"}. PPO training is a later step.</p></section>
    {error && <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => { setError(""); catalog ? void inspect() : setAttempt(v => v + 1); }}>Retry</button></div>}
    {busy && <LoadingState label="Verifying selected session certificates, label hashes and statistics" />}
    {audit && <section className="research-selection" aria-label="Preflight results"><h2><ShieldCheck size={16} />Ready for label inspection</h2><p>{audit.day} · {audit.role} · {n(audit.analytics.classes.reduce((sum, row) => sum + row.rows, 0))} label rows</p><ul className="research-checks">{audit.checks.map(check => <li key={check}>{check}</li>)}</ul><p className="research-muted">{audit.candle_check}. This opens an audit workspace; it does not certify learning or launch training.</p><details><summary>Label source certificate</summary><p className="research-source">{audit.version}<br />SHA-256 {audit.certificate_sha256}</p></details><button className="button primary" onClick={() => setWorkspace(true)}>Open teacher audit workspace</button></section>}
  </div>;
}

function TeacherCanvas({ audit, onBack }: { audit: Audit; onBack: () => void }) {
  const surface = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(0), [height, setHeight] = useState(760), [layouts, setLayouts] = useResearchState<Partial<Record<Id, WorkspaceWindowLayout>>>(`${audit.day}:layouts`, {});
  const [closed, setClosed] = useResearchState<Id[]>(`${audit.day}:closed`, []), [listing, setListing] = useResearchState(`${audit.day}:listing`, audit.analytics.tickers[0]?.listing_id ?? "");
  useEffect(() => {
    if (!surface.current) return;
    const element = surface.current;
    const measure = () => {
      setWidth(element.clientWidth);
      const zoom = Number(getComputedStyle(document.documentElement).getPropertyValue("--app-zoom")) || 1;
      // Viewport pixels -> app CSS pixels. Do not measure a content-sized parent:
      // that would feed the canvas height back into its own ResizeObserver.
      setHeight(Math.max(400, window.innerHeight / zoom - 180));
    };
    const observer = new ResizeObserver(measure);
    observer.observe(element); window.addEventListener("resize", measure);
    return () => { observer.disconnect(); window.removeEventListener("resize", measure); };
  }, []);
  const defaults = useMemo(() => {
    const scale = 1; // App shell CSS zoom is the global sizing authority.
    const stacked = width < 1040 * scale, gap = 12 * scale, left = Math.max(320, Math.floor(width * .36));
    const make = (x: number, y: number, w: number, h: number, z: number): WorkspaceWindowLayout => ({ x, y, w, h, z, minimized: false, fullscreen: false });
    return { architecture: make(0, 0, stacked ? width : left, 210 * scale, 1), analytics: make(0, 222 * scale, stacked ? width : left, stacked ? 560 : height - 222, 2), chart: make(stacked ? 0 : left + gap, stacked ? 794 * scale : 0, stacked ? width : width - left - gap, stacked ? 650 : height, 3) };
  }, [width, height]);
  const fullscreen = (Object.keys(titles) as Id[]).some(id => !closed.includes(id) && layouts[id]?.fullscreen);
  const extent = fullscreen ? height : Math.max(400, ...(Object.keys(titles) as Id[]).filter(id => !closed.includes(id)).map(id => { const l = layouts[id] ?? defaults[id]; return l.y + (l.minimized ? 24 : l.h) + 16; }));
  return <div className="research-page"><header className="research-toolbar"><button className="button secondary compact" onClick={onBack}><ArrowLeft size={14} />Models & preflight</button><strong>V6 · Teacher audit</strong><span>{audit.day} · {audit.role} · New York time</span><button className="button secondary compact" onClick={() => { setLayouts({}); setClosed([]); }}><RefreshCcw size={14} />Reset containers</button></header>
    {closed.length > 0 && <div className="research-controls">{closed.map(id => <button className="button secondary compact" key={id} onClick={() => setClosed(current => current.filter(item => item !== id))}>Restore {titles[id]}</button>)}</div>}
    <div className="research-canvas" ref={surface} style={{ height: extent }}>{(Object.keys(titles) as Id[]).filter(id => !closed.includes(id)).map(id => <div key={id} style={{ display: "contents", visibility: fullscreen && !layouts[id]?.fullscreen ? "hidden" : "visible" }}><WorkspaceWindow id={id} title={titles[id]} compact icon={icons[id]} layout={layouts[id] ?? defaults[id]} canvasTargets={[]} canPopOut={false} meta={{ status: "ready", sourceLabel: id === "architecture" ? "Reserved for model documentation" : "Certified V6 teacher sources" }} onClose={() => setClosed(current => [...current, id])} onFocus={() => {}} onLayoutChange={(_, patch) => setLayouts(current => ({ ...current, [id]: { ...(current[id] ?? defaults[id]), ...patch } }))} onMoveToCanvas={() => {}} onPopOut={() => {}}>
      {id === "architecture" ? <div className="research-container"><h2>RL trading V6</h2><p>Teacher training · Hindsight MACD 1s supervision</p><p className="research-muted">Architecture and model details will be filled in the next review.</p><dl><div><dt>Local classes</dt><dd>ENTRY · WAIT · HOLD · EXIT</dd></div><div><dt>Inspection</dt><dd>Saved targets, not predictions</dd></div></dl></div> : id === "analytics" ? <LabelAnalytics audit={audit} listing={listing} onListing={setListing} /> : <LabelChart key={listing} day={audit.day} listing={listing} tickers={audit.analytics.tickers} onListing={setListing} />}
    </WorkspaceWindow></div>)}</div></div>;
}

function LabelAnalytics({ audit, listing, onListing }: { audit: Audit; listing: string; onListing: (id: string) => void }) {
  const [search, setSearch] = useState(""); const a = audit.analytics;
  return <div className="research-container research-analytics"><p>{a.scope}</p><table><thead><tr><th>Hard class</th><th>Rows</th><th>Soft weight mass</th></tr></thead><tbody>{a.classes.map(row => <tr key={row.action}><th>{row.action}</th><td>{n(row.rows)}</td><td>{n(row.weighted_soft_mass)}</td></tr>)}</tbody></table><p className="research-muted">Hard class uses p ≥ 0.5. Soft mass = original episode weight × target probability; it excludes training class balance.</p>
    <h2>Episode coverage</h2>{a.branches.map(b => <div className="research-branch" key={b.branch}><strong>{b.branch === "flat" ? "Flat · ENTRY / WAIT" : "Hypothetical held · EXIT / HOLD"}</strong><dl><div><dt>Episodes</dt><dd>{n(b.episodes)}</dd></div><div><dt>Median rows / span</dt><dd>{n(b.median_episode_rows)} / {n(b.median_span_seconds)} s</dd></div><div><dt>Overlapping ticker clocks</dt><dd>{n(b.overlapping_clocks)}</dd></div><div><dt>Additional overlap rows</dt><dd>{n(b.extra_overlap_rows)}</dd></div><div><dt>Mean positive probability</dt><dd>{(b.mean_probability * 100).toFixed(1)}%</dd></div></dl></div>)}<p className="research-muted">{a.coverage}. Overlaps remain separate episode targets.</p>
    <h2>Positive-target distribution</h2><table><thead><tr><th>Probability bin</th><th>ENTRY rows</th><th>EXIT rows</th></tr></thead><tbody>{a.distributions.filter(d => d.branch === "flat").map((row, i) => <tr key={row.range}><th>{row.range}{i === 3 ? " inclusive" : " (upper excl.)"}</th><td>{n(row.rows)}</td><td>{n(a.distributions.filter(d => d.branch === "held")[i].rows)}</td></tr>)}</tbody></table>
    <h2>Inspect a ticker</h2><input aria-label="Filter label tickers" placeholder="Filter ticker" value={search} onChange={e => setSearch(e.target.value)} /><table><thead><tr><th>Ticker</th><th>Episodes</th><th>Label rows</th></tr></thead><tbody>{a.tickers.filter(row => row.ticker.includes(search.toUpperCase())).map(row => <tr key={row.listing_id} aria-selected={row.listing_id === listing}><td><button className="research-ticker-button" onClick={() => onListing(row.listing_id)}>{row.ticker}</button></td><td>{n(row.episodes)}</td><td>{n(row.rows)}</td></tr>)}</tbody></table>
  </div>;
}

function LabelChart({ day, listing, tickers, onListing }: { day: string; listing: string; tickers: Ticker[]; onListing: (id: string) => void }) {
  const [branch, setBranch] = useResearchState(`${day}:branch`, "flat"), [episode, setEpisode] = useResearchState(`${day}:${listing}:episode`, "");
  const [start, setStart] = useResearchState<number | null>(`${day}:${listing}:start`, null), [chart, setChart] = useState<Chart | null>(null);
  const [busy, setBusy] = useState(false), [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    if (!listing) return; const abort = new AbortController(); setBusy(true); setError(""); setChart(null);
    const url = `/api/research/models/v6/chart${query({ day, listing_id: listing, branch, episode_uid: episode || undefined, start_us: start ?? undefined, seconds: 900 })}`;
    const cached = chartCache.get(url);
    if (cached && !attempt) { setChart(cached); setBusy(false); return () => abort.abort(); }
    api<Chart>(url, { signal: abort.signal, timeoutMs: 600000 }).then(result => { if (!abort.signal.aborted) { if (chartCache.size >= 32) chartCache.delete(chartCache.keys().next().value!); chartCache.set(url, result); setChart(result); } }).catch(reason => { if (!abort.signal.aborted) setError(message(reason)); }).finally(() => { if (!abort.signal.aborted) setBusy(false); });
    return () => abort.abort();
  }, [day, listing, branch, episode, start, attempt]);
  const payload = useMemo<ChartPayload>(() => {
    const candleTimes = new Set(chart?.candles.map(c => c.time));
    const markers: ChartPayload["markers"] = [];
    for (const [i, label] of (chart?.labels ?? []).entries()) {
      const time = label.time_us / 1000000 - 1;
      // Never snap a target on an invalid-price activity row to a nearby bar.
      if (!candleTimes.has(time)) continue;
      const positive = label.probability >= .5;
      const exit = label.branch === "held" && positive;
      markers.push({ id: `target-${i}`, time: time as UTCTimestamp,
        position: exit ? "aboveBar" : "belowBar", size: .5,
        shape: positive ? (exit ? "arrowDown" : "arrowUp") : "circle",
        color: positive ? (exit ? "var(--danger)" : "var(--success)") : "var(--muted-foreground)",
        text: label.reward == null ? "" : label.reward.toFixed(3) });
    }
    const success = getComputedStyle(document.documentElement).getPropertyValue("--success").trim();
    const bandColor = /^#[0-9a-f]{6}$/i.test(success) ? `${success}1a` : success;
    return { candles: chart?.candles ?? [], volume: [], overlay_series: [], oscillator_series: chart?.oscillator_series ?? [], regions: (chart?.regions ?? []).map(r => ({ ...r, color: bandColor })), markers };
  }, [chart, branch]);
  return <div className="research-chart-container"><div className="research-controls"><label>Ticker<select aria-label="Chart ticker" value={listing} onChange={e => { setStart(null); setEpisode(""); onListing(e.target.value); }}>{tickers.map(t => <option value={t.listing_id} key={t.listing_id}>{t.ticker}</option>)}</select></label><label>Branch<select aria-label="Label branch" value={branch} onChange={e => { setStart(null); setEpisode(""); setBranch(e.target.value); }}><option value="flat">ENTRY / WAIT</option><option value="held">EXIT / HOLD</option></select></label><label>Episode<select aria-label="Episode" value={episode || chart?.selected_episode || ""} disabled={!chart} onChange={e => { setEpisode(e.target.value); setStart(null); }}>{chart?.episodes.map(id => <option key={id} value={id}>{id.split(":").at(-1)}</option>)}</select></label></div>
    <div className="research-chart-nav"><button className="button secondary compact" disabled={busy || !chart?.previous_available} onClick={() => setStart(chart!.start_us - 900000000)}>Previous 15 min</button><span>{chart ? `${clock(chart.start_us)}–${clock(chart.end_us)} ET · 1s` : "1s completed candles"}</span><button className="button secondary compact" disabled={busy || !chart?.next_available} onClick={() => setStart(chart!.end_us)}>Next 15 min</button></div><p className="research-muted">Original long episodes · Marker numbers: ENTRY discounted score; HOLD/EXIT net $/share</p>
    {busy ? <LoadingState fill label="Loading certified candles, MACD and labels" /> : error ? <div className="canvas-inline-error" role="alert">{error}<button onClick={() => setAttempt(v => v + 1)}>Retry chart</button></div> : chart?.candles.length ? <ChartPanel reference={chart.labels.length ? { time: chart.labels[0].time_us / 1000000 - 1, startTime: chart.labels[0].time_us / 1000000 - 1, endTime: chart.labels.at(-1)!.time_us / 1000000 - 1 } : undefined} ticker={chart.ticker} timeframe="1s" timeframes={["1s"]} payload={payload} featureOptions={[]} indicatorOptions={[]} visibleColumns={["macd_line", "macd_signal", "macd_histogram"]} visibleSupervisionGroups={[]} onTickerChange={() => {}} onTimeframeChange={() => {}} onVisibleColumnsChange={() => {}} onVisibleSupervisionGroupsChange={() => {}} tickerEditable={false} toolbarVariant="compact" showIndicatorControls={false} showSupervisionControls={false} fillHeight baseHeight={300} persistedOnly initialFitMode="last_market_day" settingsStorageKey="research.v6.teacher-chart.macd-v2" appearanceDefaults={{ legendGutterVisible: false, rightLegendGutterVisible: false }} /> : <div className="research-empty">No valid-price candles in this window. Use the adjacent windows.</div>}
    {chart && <details className="research-label-detail"><summary>How teacher labels are calculated</summary><p>Original candidate score = ((hindsight exit close - decision close) x 0.5^(hold seconds / 30) - 2 x $0.005) / (decision close + $0.005). This is the saved 30-second half-life score.</p><p>ENTRY probability = qualifying discounted candidate score / best qualifying score in this episode; WAIT probability = 1 - ENTRY. Qualification requires score &gt;= {chart.label_config.min_score} and at least {chart.label_config.min_peak_headroom * 100}% peak headroom. Missing qualifying score gives ENTRY = 0.</p><p>EXIT profit = close - hypothetical entry price - 2 x ${chart.label_config.fee_per_share}/share. EXIT probability = clip(profit / best episode profit, 0, 1) after 3 seconds when best profit is positive; otherwise 0. The final observed held candle is forced to EXIT = 1. HOLD probability = 1 - EXIT.</p><p>Training uses these soft probabilities, with original weight = 1 / branch rows in the episode. Marker arrows use p &gt;= 0.5 for display. Overlapping episodes remain separate training contexts; choose one episode and branch to verify each saved row.</p></details>}
    {chart && <details className="research-label-detail"><summary>{n(chart.labels.length)} saved targets · {chart.omitted_invalid_price_rows} invalid-price rows omitted from chart</summary><p>{chart.source}</p><p>{chart.reward_units}. Saved soft targets below; arrows use p &gt;= 0.5 only for display.</p><table><thead><tr><th>Close ET</th><th>Episode</th><th>Branch</th><th>Positive P</th><th>Reward</th><th>Weight</th></tr></thead><tbody>{chart.labels.map((l, i) => <tr key={i}><td>{clock(l.time_us)}</td><td title={l.episode_uid}>{l.episode_uid.split(":").at(-1)}</td><td>{l.branch}</td><td>{(l.probability * 100).toFixed(2)}%</td><td>{l.reward == null ? "" : l.reward.toFixed(3)}</td><td>{l.sample_weight.toPrecision(4)}</td></tr>)}</tbody></table></details>}
  </div>;
}
