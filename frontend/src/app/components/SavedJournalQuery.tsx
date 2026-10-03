import { useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import { dateInTimeZone } from "../timeZones";
import { StrategyActivityContainer } from "./MarketScreenerContainers";
import { ExecutionsPreview, TradingDataTable } from "../../features/canvas/tradingPresentation";
import type { V4Page } from "./BacktestV4SavedReview";

type Domain = "activity" | "orders" | "fills";
type Page = { events: V4Page["events"]; commissions?: Array<Record<string, unknown>>; complete: boolean; next_sequence: number };
type Filters = { ticker: string; event_type: string; start: string; end: string };
const EMPTY: Filters = { ticker: "", event_type: "", start: "", end: "" };
const utc = (value: unknown) => String(value ?? "").replace(" ", "T").replace(/(?<!Z)$/, "Z");

/** Query results are independent of the eager, complete position projection. */
export function SavedJournalQuery({ runId, domain, asOf, onTickerSelect, activitySettings, fillSettings, strategyId, strategyRevision, journalOnly }: {
  runId: string; domain: Domain; asOf: string; onTickerSelect: (ticker: string) => void;
  activitySettings: Parameters<typeof StrategyActivityContainer>[0]["settings"];
  fillSettings: Parameters<typeof ExecutionsPreview>[0]["settings"];
  strategyId?: string; strategyRevision?: number;
  journalOnly?: boolean;
}) {
  const [facets, setFacets] = useState<{ tickers: string[]; events: string[] }>({ tickers: [], events: [] });
  const [draft, setDraft] = useState(EMPTY);
  const [applied, setApplied] = useState(EMPTY);
  const [page, setPage] = useState<Page | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [settings, setSettings] = useState(activitySettings);
  const request = useRef<AbortController | null>(null);
  const endpoint = `/api/trading/backtest/runs/${encodeURIComponent(runId)}/v4-journal-query`;
  useEffect(() => {
    const controller = new AbortController();
    setPage(null);
    api<typeof facets>(`${endpoint}?domain=${domain}&facets=true&journal_only=${journalOnly === true}`, { signal: controller.signal, timeoutMs: 60000 })
      .then(setFacets).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => { controller.abort(); request.current?.abort(); };
  }, [endpoint, domain, journalOnly]);
  async function query(more = false) {
    request.current?.abort();
    const controller = new AbortController(); request.current = controller;
    const filters = more ? applied : draft;
    setLoading(true); setError("");
    if (!more) { setPage(null); setApplied(filters); }
    try {
      const params = new URLSearchParams({ domain, ticker: filters.ticker, event_type: filters.event_type,
        after_sequence: String(more ? page?.next_sequence ?? 0 : 0), limit: "250", journal_only: String(journalOnly === true) });
      for (const key of ["start", "end"] as const) if (filters[key]) {
        const [date, time] = filters[key].split("T");
        params.set(key, dateInTimeZone(date, time, "America/New_York").toISOString());
      }
      const next = await api<Page>(`${endpoint}?${params}`, { signal: controller.signal, timeoutMs: 60000 });
      if (!controller.signal.aborted) setPage(current => ({ ...next, events: more ? [...(current?.events ?? []), ...next.events] : next.events,
        commissions: more ? [...(current?.commissions ?? []), ...(next.commissions ?? [])] : next.commissions }));
    } catch (reason) { if (!controller.signal.aborted) setError(String(reason)); }
    finally { if (!controller.signal.aborted) setLoading(false); }
  }
  const fees = new Map((page?.commissions ?? []).map(row => [row.execution_id, row]));
  const rows: Array<Record<string, unknown>> = (page?.events ?? []).map(({ event, detail, detail_family }) => ({
    ...detail, sequence: event.sequence, run_id: runId, record_id: "",
    entity_id: event.entity_id, strategy_id: detail?.strategy_id ?? strategyId,
    strategy_revision: detail?.strategy_revision ?? strategyRevision,
    event_time: utc(event.event_time), recorded_at: utc(event.recorded_at),
    event_type: event.category === "strategy" ? "decision" : event.category,
    ticker: detail?.ticker ?? "", action: detail?.action ?? event.entity_type,
    state: detail?.status ?? detail?.state ?? "", source: detail_family ?? "",
    event_evidence: { ...detail, journal_record_id: event.record_id, detail_family },
  }));
  return <section className="saved-journal-query">
    <form className="saved-journal-query-controls" onSubmit={event => { event.preventDefault(); void query(); }}>
      <label>Ticker<select aria-label="Query ticker" value={draft.ticker} onChange={event => setDraft({ ...draft, ticker: event.target.value })}><option value="">All tickers</option>{facets.tickers.map(value => <option key={value}>{value}</option>)}</select></label>
      <label>Event<select aria-label="Query event" value={draft.event_type} onChange={event => setDraft({ ...draft, event_type: event.target.value })}><option value="">All events</option>{facets.events.map(value => <option key={value} value={value}>{value.replaceAll("_", " ")}</option>)}</select></label>
      {(["start", "end"] as const).map(key => <label key={key}>{key === "start" ? "From" : "To"} · ET<input type="datetime-local" step="1" value={draft[key]} onChange={event => setDraft({ ...draft, [key]: event.target.value })} /></label>)}
      <button className="button secondary compact" disabled={loading} type="submit">{loading ? "Querying…" : "Query"}</button>
      {page && !page.complete ? <button className="button secondary compact" disabled={loading} onClick={() => void query(true)} type="button">Load more</button> : null}
      <span role="status">{page ? `${rows.length.toLocaleString()} results loaded${page.complete ? "" : " · more available"}` : "Choose filters and query"}</span>
    </form>
    {error ? <div className="facts-state" role="alert">{error}</div> : domain === "activity" ? <StrategyActivityContainer key={`${runId}:${JSON.stringify(applied)}`} asOf={asOf} historicalRows={rows} historicalPage={{ complete: true }} queryMode
      onSettingsChange={patch => setSettings(current => ({ ...current, ...patch }))} onTickerSelect={onTickerSelect} runId={runId}
      settings={{ ...settings, strategyId: "", runId: "", ticker: "", eventType: "" }} />
      : domain === "orders" ? <TradingDataTable rows={rows} columns={["event_time", "ticker", "side", "order_type", "quantity", "limit_price", "client_order_id"]} defaultSort="event_time" onSymbolSelect={onTickerSelect} searchPlaceholder="Search query results" />
      : <ExecutionsPreview queryMode data={{ executions: rows.map(row => ({ ...row, source_event_time: utc(row.source_event_time),
        instrument: { symbol: row.ticker }, side: row.side === "B" ? "BUY" : row.side === "S" ? "SELL" : row.side,
        commission: fees.get(row.execution_id)?.commission ?? null, commission_status: fees.get(row.execution_id)?.status ?? "pending" })), complete: false, stale: false, mode: "backtest", provider: "arte_typed_journal_v4", stale_reason: "Filtered query results, not the complete run.", as_of: asOf }} settings={fillSettings} onSymbolSelect={onTickerSelect} />}
  </section>;
}
