import { useEffect, useState } from "react";
import { query } from "../../api/client";
import { researchApi as api } from "./researchApi";
import { PriceActionResearch } from "./PriceActionResearch";
import { LoadingState } from "../../app/components/LoadingState";
import { researchClock as clock } from "./researchChart";

type Catalog = { dataset_sha256: string; days: { day: string; role: string; valid_rows: number; excluded_rows: number; selected_pairs: number }[] };
type Listing = { listing_id: string; ticker: string; valid_rows: number; excluded_rows: number };
type Decision = { listing_id: string; pair_id: number; selected: boolean; selection_reason: string; time_us: number | null; entry_target_us: number | null; selection_score: number | null; allocation_ratio: number; group_id: number | null; active_count: number | null };
type Market = { dataset_sha256: string; total: number; rows: Decision[]; groups: { group_id: number; start_us: number; end_us: number; members: number }[] };
const num = (v: number | null) => v == null ? "—" : v.toLocaleString(undefined, { maximumFractionDigits: 6 });

export function V66LabelResearch() {
  const [catalog, setCatalog] = useState<Catalog | null>(null), [day, setDay] = useState("");
  const [listings, setListings] = useState<Listing[]>([]), [ticker, setTicker] = useState("");
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
    if (!day || !catalog || phase !== "1b") return;
    const abort = new AbortController(); setMarket(null);
    api<Market>(`/api/research/models/v66/labels/market${query({ day, search, selection, offset })}`, { signal: abort.signal, timeoutMs: 300000 }).then(r => {
      if (abort.signal.aborted) return;
      if (r.dataset_sha256 !== catalog.dataset_sha256) throw new Error("Dataset changed; reload V66 labels.");
      setMarket(r);
    }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [day, catalog, phase, search, selection, offset]);
  const selected = listings.find(x => x.ticker === ticker);
  const choices = listings.filter(x => x.ticker.toUpperCase().includes(search.toUpperCase()) || x.ticker === ticker);
  const index = choices.findIndex(x => x.ticker === ticker);
  return <div className="research-path-shell">
    <div className="research-controls">
      <strong>V66 labels · awaiting review</strong>
      <label>Session<select aria-label="V66 label session" value={day} onChange={e => { setDay(e.target.value); setOffset(0); }}>{catalog?.days.map(d => <option key={d.day} value={d.day}>{d.day} · {d.role}</option>)}</select></label>
      <label>Phase<select aria-label="V66 label phase" value={phase} onChange={e => setPhase(e.target.value)}><option value="1a">1a · ticker opportunities</option><option value="1b">1b · selected labels & grouping</option></select></label>
      <label>Find ticker<input aria-label="Find V66 ticker" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }}/></label>
      <label>Ticker<select aria-label="V66 label ticker" value={ticker} onChange={e => setTicker(e.target.value)}>{choices.map(t => <option key={t.ticker} value={t.ticker}>{t.ticker}{t.valid_rows ? "" : " · no label candles"}</option>)}</select></label>
      <button className="button secondary compact" disabled={index <= 0} onClick={() => setTicker(choices[index-1].ticker)}>Previous ticker</button>
      <button className="button secondary compact" disabled={index < 0 || index >= choices.length-1} onClick={() => setTicker(choices[index+1].ticker)}>Next ticker</button>
      <button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Reload V66 labels</button>
    </div>
    {phase === "1b" && <details className="research-label-detail"><summary>1b selection, overlap groups & relative sizing</summary>
      <p>Same reviewed 1b rules: fee $0.005/share/side, strict fee floor and ≥0.1% discounted return. Groups use half-open opportunity overlap. Allocation is relative to active selected opportunities, not executable cash. Universe: all stored V66 session tickers.</p>
      <div className="research-controls"><label>Selection<select aria-label="V66 pair selection" value={selection} onChange={e => { setSelection(e.target.value); setOffset(0); }}><option value="all">All pairs</option><option value="selected">Selected</option><option value="rejected">Rejected</option></select></label><button className="button secondary compact" disabled={!offset} onClick={() => setOffset(Math.max(0,offset-100))}>Previous pairs</button><span>{market ? `${market.total ? offset+1 : 0}–${Math.min(offset+100,market.total)} / ${market.total} pairs · ${market.groups.length} groups` : "Loading saved decisions…"}</span><button className="button secondary compact" disabled={!market || offset+100>=market.total} onClick={() => setOffset(offset+100)}>Next pairs</button></div>
      <div className="research-v66-decisions"><table><thead><tr><th>Ticker / pair</th><th>Decision</th><th>Entry → target (ET)</th><th>Group</th><th>Score</th><th>Allocation</th><th>Active count</th></tr></thead><tbody>{market?.rows.map(r => <tr key={`${r.listing_id}:${r.pair_id}`}><td><button className="button secondary compact" onClick={() => setTicker(r.listing_id)}>{r.listing_id} #{r.pair_id}</button></td><td>{r.selection_reason}</td><td>{r.time_us == null ? "—" : clock(r.time_us)} → {r.entry_target_us == null ? "—" : clock(r.entry_target_us)}</td><td>{r.group_id ?? "—"}</td><td>{num(r.selection_score)}</td><td>{num(r.allocation_ratio)}</td><td>{r.active_count ?? "—"}</td></tr>)}</tbody></table></div>
    </details>}
    {error && <div className="canvas-inline-error" role="alert">{error}</div>}
    {catalog && selected ? selected.valid_rows ? <PriceActionResearch key={`${catalog.dataset_sha256}:${day}:${ticker}:${phase}`} saved={{day,listing_id:ticker,dataset_sha256:catalog.dataset_sha256,supports_combined:true,v66:true,phase}}/> : <div className="research-empty">{ticker}: no warmed-up valid price candles. {selected.excluded_rows.toLocaleString()} source rows remain accounted for as exclusions.</div> : !error && <LoadingState label="Verifying V66 label publication"/>}
  </div>;
}
