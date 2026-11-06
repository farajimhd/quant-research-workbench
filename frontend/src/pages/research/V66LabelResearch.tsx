import { useEffect, useRef, useState } from "react";
import { query } from "../../api/client";
import { researchApi as api } from "./researchApi";
import { EpisodeGroupingChart } from "./EpisodeGroupingChart";
import { PriceActionResearch } from "./PriceActionResearch";
import { LoadingState } from "../../app/components/LoadingState";
import { researchClock as clock } from "./researchChart";

type Catalog = { dataset_sha256: string; label_version?: string; supports_1c?: boolean; scope?: { quotes?: string }; days: { day: string; role: string; valid_rows: number; excluded_rows: number; selected_pairs: number }[] };
type Listing = { listing_id: string; ticker: string; valid_rows: number; excluded_rows: number };
type Decision = { listing_id: string; pair_id: number; selected: boolean; selection_reason: string; time_us: number | null; entry_target_us: number | null; selection_score: number | null; allocation_ratio: number; group_id: number | null; active_count: number | null };
type Market = { dataset_sha256: string; total: number; rows: Decision[]; groups: { group_id: number; start_us: number; end_us: number; members: number }[] };
const num = (v: number | null) => v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 6 });

export function V66LabelResearch() {
  const table=useRef<HTMLDetailsElement>(null);
  const chartAnchor=useRef<HTMLDivElement>(null);
  const [group,setGroup]=useState("");
  const [catalog, setCatalog] = useState<Catalog | null>(null), [day, setDay] = useState("");
  const [listings, setListings] = useState<Listing[]>([]), [ticker, setTicker] = useState("");
  const [focus, setFocus] = useState<{ pair_id: number; start_us: number; end_us?: number; phase: string; revision: number } | null>(null);
  const focusRevision = useRef(0);
  const inspect = (row: { listing_id: string; pair_id: number; time_us: number | null; entry_target_us: number | null }) => {
    if (row.time_us == null || row.entry_target_us == null) return;
    setTicker(row.listing_id);
    setFocus({ pair_id: row.pair_id, start_us: row.time_us, end_us: row.entry_target_us, phase: phase === "1c" ? "1c" : "1a", revision: ++focusRevision.current });
    chartAnchor.current?.scrollIntoView({block: "start", behavior: "smooth"});
  };
  const [phase, setPhase] = useState("1a"), [search, setSearch] = useState(""), [selection, setSelection] = useState("all");
  const [offset, setOffset] = useState(0), [market, setMarket] = useState<Market | null>(null);
  const [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); setError(""); setCatalog(null);
    api<Catalog>("/api/research/models/v66/labels", { signal: abort.signal }).then(r => { if (!abort.signal.aborted) { setCatalog(r); setDay(d => r.days.some(x => x.day === d) ? d : r.days[0]?.day ?? ""); } }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [attempt]);
  useEffect(() => {
    if (!day || !catalog) return;
    const abort = new AbortController(); setListings([]); setError("");
    api<{ dataset_sha256: string; listings: Listing[] }>(`/api/research/models/v66/labels/listings${query({ day })}`, { signal: abort.signal, timeoutMs: 300000 }).then(r => {
      if (abort.signal.aborted) return;
      if (r.dataset_sha256 !== catalog.dataset_sha256) throw new Error("Dataset changed; reload V66 labels.");
      setListings(r.listings); setTicker(t => r.listings.some(x => x.ticker === t && x.valid_rows) ? t : r.listings.find(x => x.valid_rows)?.ticker ?? "");
    }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [day, catalog]);
  useEffect(() => {
    if (!day || !catalog || phase === "1a") return;
    const abort = new AbortController(); setMarket(null);
    api<Market>(`/api/research/models/v66/labels/market${query({ day, search, selection, offset, group_id: group || undefined })}`, { signal: abort.signal, timeoutMs: 300000 }).then(r => {
      if (abort.signal.aborted) return;
      if (r.dataset_sha256 !== catalog.dataset_sha256) throw new Error("Dataset changed; reload V66 labels.");
      setMarket(r);
    }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [day, catalog, phase, search, selection, offset, group]);
  const selected = listings.find(x => x.ticker === ticker);
  const choices = listings.filter(x => x.ticker.toUpperCase().includes(search.toUpperCase()) || x.ticker === ticker);
  const index = choices.findIndex(x => x.ticker === ticker);
  return <div className="research-path-shell research-v66-workspace">
    <div className="research-controls research-saved-selection">
      <strong>V66 labels{catalog?.label_version?.includes('spread') ? " · spread-aware 1b" : ""}</strong>
      <label>Session<select aria-label="V66 label session" value={day} onChange={e => { setDay(e.target.value); setOffset(0); setFocus(null); setGroup(""); }}>{catalog?.days.map(d => <option key={d.day} value={d.day}>{d.day} · {d.role}</option>)}</select></label>
      <label>Phase<select aria-label="V66 label phase" value={phase} onChange={e => { setPhase(e.target.value); setFocus(null); }}><option value="1a">1a · Opportunities</option><option value="1b">1b · Selection & sizing</option>{catalog?.supports_1c && <option value="1c">1c · Selected position trajectory</option>}</select></label>
      <label>Find ticker<input aria-label="Find V66 ticker" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }}/></label>
      <label>Ticker<select aria-label="V66 label ticker" value={ticker} onChange={e => { setTicker(e.target.value); setFocus(null); }}>{choices.map(t => <option key={t.ticker} value={t.ticker}>{t.ticker}{t.valid_rows ? "" : " · no label candles"}</option>)}</select></label>
      <button className="button secondary compact" disabled={index <= 0} onClick={() => { setTicker(choices[index-1].ticker); setFocus(null); }}>Previous ticker</button>
      <button className="button secondary compact" disabled={index < 0 || index >= choices.length-1} onClick={() => { setTicker(choices[index+1].ticker); setFocus(null); }}>Next ticker</button>
      <button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Reload V66 labels</button>
    </div>
    {phase === "1c" && <p className="research-muted">One ENTRY and one EXIT per accepted position. State and age are measured before the action; remaining duration is a hindsight target to the selected exit. Flat duration is unavailable.</p>}
    {catalog?.scope?.quotes && <p className="research-muted">{catalog.label_version} · {catalog.scope.quotes}</p>}
    {phase !== "1a" && catalog && day && <div className="research-page research-v66-timeline"><EpisodeGroupingChart key={`${catalog.dataset_sha256}:${day}`} jobId={`${catalog.dataset_sha256}:${day}`} savedV66={{day,datasetSha:catalog.dataset_sha256}} sessionScore={0} focusedGroup={group}
      onGroup={id=>{setGroup(id);setOffset(0);setSelection("all");setSearch("");if(table.current)table.current.open=Boolean(id);}}
      onInspect={inspect} /></div>}
    {error && <div className="canvas-inline-error" role="alert">{error}</div>}
    <div ref={chartAnchor} className="research-v66-chart-anchor"/>
    {catalog && selected ? selected.valid_rows ? <PriceActionResearch key={`${catalog.dataset_sha256}:${day}:${ticker}:${focus?.phase ?? phase}:${focus?.pair_id ?? ""}:${focus?.revision ?? ""}`} saved={{day,listing_id:ticker,dataset_sha256:catalog.dataset_sha256,supports_combined:true,v66:true,phase,...(focus ?? {})}}/> : <div className="research-empty">{ticker}: no warmed-up valid price candles. {selected.excluded_rows.toLocaleString()} source rows remain accounted for as exclusions.</div> : !error && <LoadingState label="Verifying V66 label publication"/>}
    {phase !== "1a" && <details ref={table} className="research-label-detail research-v66-market"><summary>1b selection, overlap groups & relative sizing</summary>
      <p>Same reviewed 1b rules: fee $0.005/share/side, strict fee floor and ≥0.1% discounted return. Groups use half-open opportunity overlap. Allocation is relative to active selected opportunities, not executable cash. Universe: all stored V66 session tickers.</p>
      <div className="research-controls"><label>Group<select aria-label="V66 overlap group" value={group} onChange={e=>{setGroup(e.target.value);setOffset(0);}}><option value="">All groups</option>{market?.groups.map(g=><option key={g.group_id} value={g.group_id}>#{g.group_id} · {g.members} members</option>)}</select></label><label>Selection<select aria-label="V66 pair selection" value={selection} onChange={e => { setSelection(e.target.value); setOffset(0); }}><option value="all">All pairs</option><option value="selected">Selected</option><option value="rejected">Rejected</option></select></label><button className="button secondary compact" disabled={!offset} onClick={() => setOffset(Math.max(0,offset-100))}>Previous pairs</button><span>{market ? `${market.total ? offset+1 : 0}–${Math.min(offset+100,market.total)} / ${market.total} pairs · ${market.groups.length} groups` : "Loading saved decisions…"}</span><button className="button secondary compact" disabled={!market || offset+100>=market.total} onClick={() => setOffset(offset+100)}>Next pairs</button></div>
      <div className="research-v66-decisions"><table><thead><tr><th>Ticker / pair</th><th>Decision</th><th>Entry → target (ET)</th><th>Group</th><th>Score</th><th>Allocation</th><th>Active count</th></tr></thead><tbody>{market?.rows.map(r => <tr key={`${r.listing_id}:${r.pair_id}`}><td><button className="button secondary compact" disabled={r.time_us == null || r.entry_target_us == null} onClick={() => inspect(r)}>{r.listing_id} #{r.pair_id}</button></td><td>{r.selection_reason}</td><td>{r.time_us == null ? "—" : clock(r.time_us)} → {r.entry_target_us == null ? "—" : clock(r.entry_target_us)}</td><td>{r.group_id ?? "—"}</td><td>{num(r.selection_score)}</td><td>{num(r.allocation_ratio)}</td><td>{r.active_count ?? "—"}</td></tr>)}</tbody></table></div>
    </details>}
  </div>;
}
