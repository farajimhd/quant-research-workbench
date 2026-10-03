import { useEffect, useState } from "react";
import { query } from "../../api/client";
import { researchApi as api } from "./researchApi";
import { LoadingState } from "../../app/components/LoadingState";
import { PriceActionResearch } from "./PriceActionResearch";
import { useResearchState } from "./researchState";

type Catalog = { dataset_sha256: string; days: { day: string; role: string; valid_rows: number; invalid_price_rows: number }[] };
type Listings = { dataset_sha256: string; listings: { listing_id: string; ticker: string; venue: string }[] };

export function SavedLabelResearch() {
  const [catalog, setCatalog] = useState<Catalog | null>(null), [listings, setListings] = useState<Listings | null>(null);
  const [day, setDay] = useResearchState("saved-labels:day", "2026-07-31");
  const [identity, setIdentity] = useResearchState("saved-labels:identity", "");
  const [search, setSearch] = useState(""), [error, setError] = useState(""), [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const abort = new AbortController(); setError("");
    api<Catalog>("/api/research/models/v6/saved-labels", { signal: abort.signal }).then(setCatalog)
      .catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [attempt]);
  useEffect(() => {
    if (!catalog) return;
    const abort = new AbortController(); setListings(null); setError("");
    api<Listings>(`/api/research/models/v6/saved-labels/listings${query({ day })}`, { signal: abort.signal, timeoutMs: 300000 }).then(result => {
      if (abort.signal.aborted) return;
      setListings(result);
      if (!result.listings.some(r => r.listing_id === identity)) setIdentity(result.listings.find(r => r.ticker === "NVDA")?.listing_id ?? result.listings[0]?.listing_id ?? "");
    }).catch(e => { if (!abort.signal.aborted) setError(String(e)); });
    return () => abort.abort();
  }, [day, attempt, catalog]);
  const selected = listings?.listings.find(r => r.listing_id === identity);
  const filtered = listings?.listings.filter(r => `${r.ticker} ${r.venue} ${r.listing_id}`.toUpperCase().includes(search.toUpperCase())) ?? [];
  const options = selected && !filtered.includes(selected) ? [selected, ...filtered] : filtered;
  const session = catalog?.days.find(d => d.day === day);
  return <div className="research-path-shell">
    <div className="research-controls research-saved-selection">
      <label>Saved session<select aria-label="Saved label session" value={day} onChange={e => setDay(e.target.value)}>{catalog?.days.map(d => <option key={d.day} value={d.day}>{d.day} · {d.role}</option>)}</select></label>
      <label>Find ticker<input aria-label="Find saved label ticker" placeholder="Ticker or venue" value={search} onChange={e => setSearch(e.target.value)} /></label>
      <label>Listing<select aria-label="Saved label listing" value={identity} onChange={e => setIdentity(e.target.value)}>{options.map(r => <option key={r.listing_id} value={r.listing_id}>{r.ticker} · {r.venue}</option>)}</select></label>
      <span>{session?.valid_rows.toLocaleString()} labelled candles · {session?.invalid_price_rows.toLocaleString()} invalid rows excluded</span>
    </div>
    {error ? <div className="canvas-inline-error" role="alert">{error}<button className="button secondary compact" onClick={() => setAttempt(a => a+1)}>Retry saved labels</button></div> : selected && listings && catalog ?
      <PriceActionResearch key={`${day}:${identity}`} saved={{ day, listing_id: identity, dataset_sha256: listings.dataset_sha256 }} /> : <LoadingState label="Checking published labels and listing identities" />}
  </div>;
}
