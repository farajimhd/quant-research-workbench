import { BookOpen, Clock3, ExternalLink, FileCheck2, FileText, Flame, RefreshCw, Search, Snowflake } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { SEC_READER_CANVAS_ID, ensureSecReaderCanvas, focusCanvasUrl } from "../canvasWorkspace";
import { MarketTime } from "./MarketTime";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations } from "./TickerIdentity";

export type SecSettings = { content: string; label: string; lookbackHours: number; ticker: string };
type SecLabel = { id: string; label: string };
type SecRow = { accession_number: string; accepted_at_utc: string; accepted_at_source?: string; affected_security_scope?: string; cik: string; company_name: string; disclosure_title?: string; document_rows?: number; event_time_quality?: "date_only" | "exact"; filing_detail_url?: string; filing_label: string; filing_label_text: string; filing_size?: number; form_type: string; impact_label?: string; impact_rationale?: string; impact_score?: number; items: string[]; label_evidence: string[]; primary_document_url?: string; taxonomy_version?: string; text_chars?: number; text_rows?: number; tickers: string[]; xbrl_rows?: number };
type SecRowWire = Omit<SecRow, "items" | "label_evidence" | "tickers"> & { items?: unknown; label_evidence?: unknown; tickers?: unknown };
type SecPayload = { as_of: string; has_more: boolean; labels: SecLabel[]; next_before: string; next_before_accession: string; rows: SecRow[]; window_start: string };
type SecPayloadWire = Omit<SecPayload, "rows"> & { rows: SecRowWire[] };
type SecDocument = { description?: string; document_id: string; document_name: string; document_role: string; document_type: string; document_url?: string; extraction_status?: string; has_normalized_text?: number; sequence_number?: number };
type SecText = { document_id: string; text_char_count: number; text_kind: string };
type SecTextPage = SecText & { has_more: boolean; limit: number; next_offset: number; offset: number; text: string };
type SecFact = { fiscal_period?: string; fiscal_year?: number; period_end_date?: string; tag: string; unit_code: string; value: string };
type SecDetail = { documents: SecDocument[]; errors: Record<string, string>; facts: SecFact[]; facts_has_more: boolean; facts_next_offset: number; facts_total: number; filing: SecRow; identity: { exchange_code?: string; sic_description?: string; ticker?: string; tickers?: string[] }; status: string; texts: SecText[] };
type SecDetailWire = Omit<SecDetail, "filing"> & { filing: SecRowWire };
type SecFactsPage = { has_more: boolean; next_offset: number; row_count: number; rows: SecFact[] };
type Temperature = "hot" | "cold" | "old";
const SEC_SELECTION_EVENT = "quant-sec-selection";
const INITIAL_LABELS: SecLabel[] = [
  ["current_event", "Current event"], ["periodic_fundamentals", "Periodic fundamentals"], ["offering", "Offering"], ["corporate_transaction", "Corporate transaction"], ["ownership_activism", "Ownership activism"], ["insider_ownership", "Insider ownership"], ["governance", "Governance"], ["ownership", "Ownership"], ["fund_product_disclosure", "Fund product disclosure"], ["fund_dataset", "Fund dataset"], ["structured_finance", "Structured finance"], ["administrative", "Administrative"], ["other_disclosure", "Other disclosure"],
].map(([id, label]) => ({ id, label }));

export function AllSecContainer({ asOf, onSettingsChange, settings }: { asOf: string; onSettingsChange: (patch: Partial<SecSettings>) => void; settings: SecSettings }) {
  const [search, setSearch] = useState(""); const [committed, setCommitted] = useState(""); const [refreshKey, setRefreshKey] = useState(0);
  const state = useSecQuery({ asOf, refreshKey, search: committed, settings });
  const presentations = useTickerPresentations(state.rows.flatMap((row) => row.tickers ?? []));
  const labels = state.labels.length ? state.labels : INITIAL_LABELS;
  return <section className="news-all sec-all" aria-label="All SEC filings">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); setCommitted(search.trim()); }}>
      <label className="news-search"><Search size={13} /><input aria-label="Search all SEC filings" onChange={(event) => setSearch(event.target.value)} placeholder="Search company, form, accession or filing item" value={search} /></label>
      <button className="button secondary compact news-search-submit" type="submit">Search</button>
      <label><span>Window</span><select aria-label="SEC time window" onChange={(event) => onSettingsChange({ lookbackHours: Number(event.target.value) })} value={settings.lookbackHours}><option value={24}>24 hours</option><option value={72}>3 days</option><option value={168}>7 days</option><option value={720}>30 days</option><option value={8760}>1 year</option></select></label>
      <label><span>Ticker</span><input aria-label="Filter SEC by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any" value={settings.ticker} /></label>
      <label><span>Label</span><select aria-label="SEC filing label" onChange={(event) => onSettingsChange({ label: event.target.value })} value={settings.label}><option value="">All labels</option>{labels.map((label) => <option key={label.id} value={label.id}>{label.label}</option>)}</select></label>
      <label><span>Content</span><select aria-label="SEC filing content" onChange={(event) => onSettingsChange({ content: event.target.value })} value={settings.content}><option value="all">All</option><option value="readable">Readable text</option><option value="xbrl">XBRL facts</option></select></label>
      <button aria-label="Refresh SEC filings" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh" type="button"><RefreshCw size={13} /></button>
    </form>
    <SecStatus state={state} />
    <div className="news-table-wrap"><table className="news-table sec-table"><thead><tr><th>Accepted</th><th>Ticker</th><th>Filing</th><th>Company / disclosure</th><th>Items</th><th>Content</th></tr></thead><tbody>{state.rows.map((row) => <tr key={`${row.cik}-${row.accession_number}`}>
      <td><SecFilingTime row={row} /></td><td><TickerList presentations={presentations} tickers={row.tickers} /></td>
      <td><button className="sec-open-button" onClick={() => openSecPage(row)} type="button"><strong>{row.form_type}</strong><SecLabel label={row.filing_label_text} tone={temperature(row.accepted_at_utc, Date.parse(state.asOf || asOf))} /></button></td>
      <td><button className="news-headline-button" onClick={() => openSecPage(row)} type="button"><strong>{row.company_name}</strong><small>{row.disclosure_title || row.accession_number}</small></button></td>
      <td className="sec-items">{(row.items ?? []).slice(0, 3).join(" · ") || "—"}</td><td><ContentState row={row} /></td>
    </tr>)}</tbody></table>{!state.loading && !state.rows.length ? <SecEmpty label="No SEC filings match this query." /> : null}</div>
    {state.hasMore ? <button className="news-load-more" disabled={state.loadingMore} onClick={state.loadMore} type="button">{state.loadingMore ? "Loading…" : "Load older filings"}</button> : null}
  </section>;
}

export function TickerSecContainer({ asOf, onSymbolChange, settings, symbol }: { asOf: string; onSymbolChange?: (symbol: string) => void; settings: { lookbackHours: number }; symbol: string }) {
  const state = useSecQuery({ asOf, refreshKey: 0, search: "", settings: { content: "all", label: "", lookbackHours: settings.lookbackHours, ticker: symbol } });
  const presentations = useTickerPresentations([symbol]);
  const core = state.rows.filter((row) => ["periodic_fundamentals", "current_event", "offering", "corporate_transaction", "governance", "administrative"].includes(row.filing_label));
  const ownership = state.rows.filter((row) => !core.includes(row));
  return <section className="ticker-news ticker-sec" aria-label={`${symbol} SEC filings`}><header><div><TickerIdentityWithChange asOf={state.asOf || asOf} className="ticker-news-symbol" inputAriaLabel="Ticker SEC symbol" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} /><span>Accepted disclosures</span></div><small>{state.rows.length} filings · through <MarketTime value={state.asOf || asOf} /></small></header><SecStatus compact state={state} /><div className="ticker-news-feed"><TickerSecSection asOf={state.asOf || asOf} label="Core disclosures" rows={core} /><TickerSecSection asOf={state.asOf || asOf} label="Ownership & other" rows={ownership} />{!state.loading && !state.rows.length ? <SecEmpty label={`No ${symbol} filings in this window.`} /> : null}</div></section>;
}

function TickerSecSection({ asOf, label, rows }: { asOf: string; label: string; rows: SecRow[] }) { return <section className="ticker-news-section"><header><strong>{label}</strong><span>{rows.length}</span></header>{rows.map((row) => { const tone = temperature(row.accepted_at_utc, Date.parse(asOf)); const Icon = temperatureIcon(tone); return <article data-tone={tone} key={row.accession_number}><div className="ticker-news-marker"><Icon size={14} /></div><div><div className="ticker-news-meta"><SecFilingTime row={row} /><em data-tone={tone}>{tone}</em><SecLabel label={row.filing_label_text} tone={tone} /></div><button className="ticker-news-open" onClick={() => openSecPage(row)} type="button"><strong>{row.form_type} · {row.company_name}</strong><p>{row.disclosure_title || (row.items ?? []).join(" · ") || row.accession_number}</p></button></div></article>; })}</section>; }

export function SecDetailContainer({ asOf, canvasId, requestedCik, requestedAccession }: { asOf: string; canvasId: string; requestedCik?: string; requestedAccession?: string }) {
  const initial = requestedCik && requestedAccession ? `${requestedCik}/${requestedAccession}` : readSelectedSec(canvasId);
  const [key, setKey] = useState(initial); const [detail, setDetail] = useState<SecDetail | null>(null); const [loading, setLoading] = useState(false); const [error, setError] = useState(""); const [documentId, setDocumentId] = useState("");
  const [textPage, setTextPage] = useState<SecTextPage | null>(null); const [textLoading, setTextLoading] = useState(false); const [textError, setTextError] = useState("");
  const [facts, setFacts] = useState<SecFact[]>([]); const [factsLoading, setFactsLoading] = useState(false);
  useEffect(() => { const listener = (event: Event) => { const value = (event as CustomEvent<{ canvasId: string; key: string }>).detail; if (value.canvasId === canvasId) setKey(value.key); }; window.addEventListener(SEC_SELECTION_EVENT, listener); return () => window.removeEventListener(SEC_SELECTION_EVENT, listener); }, [canvasId]);
  useEffect(() => { if (!key) return; const [cik, accession] = key.split("/"); const controller = new AbortController(); setLoading(true); setError(""); setTextPage(null); api<SecDetailWire>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}${query({ as_of: asOf })}`, { signal: controller.signal, timeoutMs: 20000 }).then((value) => { const normalized = normalizeSecDetail(value); setDetail(normalized); setFacts(normalized.facts); setDocumentId(normalized.texts[0]?.document_id ?? ""); }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [asOf, key]);
  const loadTextPage = useCallback((offset: number) => { if (!key || !documentId) return; const [cik, accession] = key.split("/"); setTextLoading(true); setTextError(""); api<SecTextPage>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/text/${encodeURIComponent(documentId)}${query({ as_of: asOf, limit: 32000, offset })}`, { timeoutMs: 15000 }).then(setTextPage).catch((reason) => { setTextPage(null); setTextError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => setTextLoading(false)); }, [asOf, documentId, key]);
  useEffect(() => { setTextPage(null); setTextError(""); if (detail?.texts.some((text) => text.document_id === documentId)) loadTextPage(0); }, [detail?.texts, documentId, loadTextPage]);
  const loadMoreFacts = useCallback(() => { if (!key || !detail?.facts_has_more) return; const [cik, accession] = key.split("/"); setFactsLoading(true); api<SecFactsPage>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/facts${query({ as_of: asOf, limit: 100, offset: facts.length })}`, { timeoutMs: 15000 }).then((page) => { setFacts((current) => [...current, ...page.rows]); setDetail((current) => current ? { ...current, facts_has_more: page.has_more, facts_next_offset: page.next_offset, facts_total: page.row_count } : current); }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setFactsLoading(false)); }, [asOf, detail?.facts_has_more, facts.length, key]);
  const detailTickers = detail?.identity.tickers ?? detail?.filing.tickers ?? []; const presentations = useTickerPresentations(detailTickers);
  if (!key) return <SecEmpty label="Choose a filing in All SEC or Ticker SEC to read it here." />; if (loading && !detail) return <div className="canvas-preview-loading">Loading filing…</div>; if (error && !detail) return <SecEmpty label={error} />; if (!detail) return null;
  const row = detail.filing; const tickers = detailTickers; const selectedText = detail.texts.find((text) => text.document_id === documentId); const tone = temperature(row.accepted_at_utc, Date.parse(asOf));
  return <article className="news-reader sec-reader"><header><div className="news-reader-kicker"><TemperatureTag tone={tone} /><SecFilingTime row={row} /><SecLabel label={row.filing_label_text} tone={tone} /><span>{row.form_type}</span>{row.impact_score ? <ImpactBadge label={row.impact_label} score={row.impact_score} /> : null}</div><h1>{row.disclosure_title || row.company_name}</h1>{row.disclosure_title ? <p className="sec-reader-company">{row.company_name}</p> : null}<div className="news-reader-byline"><span>CIK {row.cik} · {row.accession_number}</span><TickerList presentations={presentations} tickers={tickers} /></div>{row.items?.length ? <div className="news-reader-tags">{row.items.map((item) => <span key={item}>Item {item}</span>)}</div> : null}<details className="news-classification-evidence"><summary>Why this classification</summary><p>{(row.label_evidence ?? []).join(" · ")}</p>{row.impact_rationale ? <p>{row.impact_rationale}</p> : null}</details></header>
    <section className="sec-document-inventory"><div className="sec-reader-section-title"><div><strong>Filing documents</strong><small>{detail.documents.length} source documents · {detail.texts.length} readable texts</small></div></div><div>{detail.documents.map((document) => <div className="sec-document-choice" key={document.document_id}><button data-active={document.document_id === documentId ? "true" : "false"} onClick={() => setDocumentId(document.document_id)} type="button"><FileText size={14} /><span><strong>{document.document_name}</strong><small>{document.document_type} · {document.document_role}</small></span></button>{safeUrl(document.document_url) ? <a aria-label={`Open ${document.document_name} on SEC.gov`} href={document.document_url} rel="noreferrer" target="_blank"><ExternalLink size={12} /></a> : null}</div>)}</div></section>
    {selectedText ? <section className="news-reader-body sec-reader-body"><div className="sec-reader-section-title"><div><strong>Readable filing</strong><small>{selectedText.text_kind} · {formatCount(selectedText.text_char_count)} characters</small></div>{textPage ? <span>{formatCount(textPage.offset + 1)}–{formatCount(textPage.next_offset)} of {formatCount(textPage.text_char_count)}</span> : null}</div>{textLoading ? <div className="canvas-preview-loading">Loading text page…</div> : textError ? <SecEmpty label={textError} /> : textPage ? <>{paragraphs(textPage.text).map((paragraph, index) => <p key={`${textPage.offset}-${index}-${paragraph.slice(0, 18)}`}>{paragraph}</p>)}<div className="sec-page-controls"><button disabled={textPage.offset === 0 || textLoading} onClick={() => loadTextPage(Math.max(0, textPage.offset - textPage.limit))} type="button">Previous page</button><button disabled={!textPage.has_more || textLoading} onClick={() => loadTextPage(textPage.next_offset)} type="button">Next page</button></div></> : null}</section> : <SecEmpty label="No readable text is available for the selected document." />}
    <section className="sec-facts"><div className="sec-reader-section-title"><div><strong>XBRL facts</strong><small>{formatCount(detail.facts_total)} filing-linked facts · {formatCount(facts.length)} loaded</small></div></div>{facts.length ? <div className="news-table-wrap"><table className="news-table"><thead><tr><th>Concept</th><th>Value</th><th>Period</th></tr></thead><tbody>{facts.map((fact, index) => <tr key={`${fact.tag}-${index}`}><td>{fact.tag}</td><td>{fact.value} <small>{fact.unit_code}</small></td><td>{fact.fiscal_period || fact.period_end_date || "—"}</td></tr>)}</tbody></table></div> : <SecEmpty label="This filing has no linked XBRL facts." />}{detail.facts_has_more ? <button className="news-load-more" disabled={factsLoading} onClick={loadMoreFacts} type="button">{factsLoading ? "Loading…" : "Load more facts"}</button> : null}</section>
    <footer>{safeUrl(row.filing_detail_url) ? <a href={row.filing_detail_url} rel="noreferrer" target="_blank">Open filing on SEC.gov <ExternalLink size={12} /></a> : null}</footer></article>;
}

function useSecQuery({ asOf, refreshKey, search, settings }: { asOf: string; refreshKey: number; search: string; settings: SecSettings }) { const [payload, setPayload] = useState<SecPayload | null>(null); const [rows, setRows] = useState<SecRow[]>([]); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState(""); const load = useCallback(async (before = "", beforeAccession = "", pageAsOf = "") => { const response = await api<SecPayloadWire>(`/api/trading/sec${query({ as_of: pageAsOf || asOf, before: before || undefined, before_accession: beforeAccession || undefined, content: settings.content, label: settings.label || undefined, limit: 100, lookback_hours: settings.lookbackHours, search: search || undefined, ticker: settings.ticker || undefined })}`, { timeoutMs: 30000 }); const next = normalizeSecPayload(response); setPayload(next); setRows((current) => before ? [...current, ...next.rows.filter((row) => !current.some((item) => item.accession_number === row.accession_number))] : next.rows); setError(""); }, [asOf, search, settings.content, settings.label, settings.lookbackHours, settings.ticker]); useEffect(() => { setLoading(true); load().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoading(false)); }, [load, refreshKey]); const loadMore = useCallback(() => { if (!payload?.next_before) return; setLoadingMore(true); load(payload.next_before, payload.next_before_accession, payload.as_of).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoadingMore(false)); }, [load, payload]); return { asOf: payload?.as_of ?? asOf, error, hasMore: Boolean(payload?.has_more), labels: payload?.labels ?? [], loadMore, loading, loadingMore, rows, windowStart: payload?.window_start ?? "" }; }

function normalizeSecPayload(value: SecPayloadWire): SecPayload {
  return { ...value, rows: Array.isArray(value.rows) ? value.rows.map(normalizeSecRow) : [] };
}

function normalizeSecDetail(value: SecDetailWire): SecDetail {
  return { ...value, filing: normalizeSecRow(value.filing) };
}

function normalizeSecRow(value: SecRowWire): SecRow {
  return {
    ...value,
    items: normalizeSecStringList(value.items),
    label_evidence: normalizeSecStringList(value.label_evidence),
    tickers: normalizeSecStringList(value.tickers).map((ticker) => ticker.toUpperCase()),
  };
}

function normalizeSecStringList(value: unknown): string[] {
  if (Array.isArray(value)) return [...new Set(value.map((item) => String(item).trim()).filter(Boolean))];
  if (typeof value !== "string") return [];
  const trimmed = value.trim();
  if (!trimmed) return [];
  if (trimmed.startsWith("[") && trimmed.endsWith("]")) {
    try {
      const parsed = JSON.parse(trimmed);
      if (Array.isArray(parsed)) return normalizeSecStringList(parsed);
    } catch {
      // Fall through to the legacy comma-delimited representation.
    }
  }
  return [...new Set(trimmed.split(",").map((item) => item.trim()).filter(Boolean))];
}
function SecStatus({ compact, state }: { compact?: boolean; state: ReturnType<typeof useSecQuery> }) { return <div className="news-status" data-compact={compact ? "true" : "false"}>{state.loading ? <span>Querying filings…</span> : state.error ? <strong>{state.error}</strong> : <><span>{state.rows.length} returned</span>{!compact && state.windowStart ? <span className="news-window-start"><span>Since</span><MarketTime dateStyle="short" includeDate layout="inline" value={state.windowStart} /></span> : null}<span className="news-source-label">Point-in-time</span></>}</div>; }
function SecEmpty({ label }: { label: string }) { return <div className="news-empty"><BookOpen size={18} /><span>{label}</span></div>; }
function SecLabel({ label, tone }: { label: string; tone: Temperature }) { return <span className="sec-label" data-tone={tone}><FileCheck2 size={11} />{label}</span>; }
function ImpactBadge({ label, score }: { label?: string; score: number }) { const tone = score >= 4 ? "high" : score >= 2 ? "medium" : "low"; return <span className="sec-impact-badge" data-impact={tone}>Impact {score}/5{label ? ` · ${label}` : ""}</span>; }
function SecFilingTime({ row }: { row: SecRow }) { if (row.event_time_quality === "date_only") return <span className="sec-date-only" title="The SEC source published a filing date but no acceptance time."><strong>{formatDateOnly(row.accepted_at_utc)}</strong><small>Time unresolved</small></span>; return <MarketTime className="news-row-time" dateStyle="short" includeDate value={row.accepted_at_utc} />; }
function ContentState({ row }: { row: SecRow }) { return <span className="sec-content-state"><b>{row.text_rows ? "Text" : "Metadata"}</b>{row.xbrl_rows ? <b>XBRL</b> : null}<small>{row.document_rows ?? 0} docs</small></span>; }
function TickerList({ presentations, tickers = [] }: { presentations: ReturnType<typeof useTickerPresentations>; tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <b key={ticker}><TickerIdentity logoUrl={presentations[ticker]?.logo_url} ticker={ticker} /></b>)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function TemperatureTag({ tone }: { tone: Temperature }) { const Icon = temperatureIcon(tone); return <span className="news-temperature" data-tone={tone}><Icon size={12} /><em>{tone[0].toUpperCase() + tone.slice(1)}</em></span>; }
function temperature(value: string, asOf: number): Temperature { const age = Math.max(0, (asOf - Date.parse(value)) / 60000); return age <= 240 ? "hot" : age <= 1440 ? "cold" : "old"; }
function temperatureIcon(value: Temperature) { return value === "hot" ? Flame : value === "cold" ? Snowflake : Clock3; }
function formatDateOnly(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "America/New_York" }).format(parsed); }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.sec-selection.${canvasId}`; }
function readSelectedSec(canvasId: string) { return window.localStorage.getItem(selectionKey(canvasId)) || ""; }
function selectSec(canvasId: string, key: string) { window.localStorage.setItem(selectionKey(canvasId), key); window.dispatchEvent(new CustomEvent(SEC_SELECTION_EVENT, { detail: { canvasId, key } })); }
function openSecPage(row: SecRow) { ensureSecReaderCanvas(); const key = `${row.cik}/${row.accession_number}`; selectSec(SEC_READER_CANVAS_ID, key); const url = new URL(focusCanvasUrl(SEC_READER_CANVAS_ID, "sec_detail")); url.searchParams.set("sec_cik", row.cik); url.searchParams.set("sec_accession", row.accession_number); window.open(url.toString(), "quant-sec-reader"); }
function safeUrl(value?: string) { if (!value) return false; try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; } }
function paragraphs(value: string) { const explicit = value.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.split(/(?<=[.!?])\s+(?=[A-Z0-9])/).filter(Boolean); const result: string[] = []; for (let index = 0; index < sentences.length; index += 4) result.push(sentences.slice(index, index + 4).join(" ")); return result.length ? result : [value]; }
function formatCount(value: number) { return new Intl.NumberFormat("en-US", { notation: "compact" }).format(value || 0); }
