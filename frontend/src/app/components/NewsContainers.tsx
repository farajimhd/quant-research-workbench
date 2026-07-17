import { Bot, Building2, Clock3, ExternalLink, FileCheck2, Flame, Globe2, Layers3, Lightbulb, Megaphone, Newspaper, RefreshCw, Search, Snowflake, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState, type ReactElement } from "react";

import { api, query } from "../../api/client";
import { NEWS_READER_CANVAS_ID, ensureNewsReaderCanvas, focusCanvasUrl } from "../canvasWorkspace";
import { MarketTime } from "./MarketTime";
import { TickerIdentity, useTickerPresentations, type TickerPresentation } from "./TickerIdentity";

type NewsRow = {
  article_url?: string;
  author?: string;
  canonical_news_id: string;
  channels?: string[];
  full_text_chars?: number;
  has_external_text?: boolean;
  has_pdf?: boolean;
  is_title_only?: boolean;
  classification?: NewsClassification;
  classification_confidence?: number;
  classification_evidence?: string[];
  is_company_news?: boolean;
  news_format?: NewsFormat;
  news_kind?: NewsKindValue;
  news_origin?: NewsOrigin;
  news_scope?: NewsScope;
  news_topics?: string[];
  provider_tags?: string[];
  published_at_utc: string;
  text_preview?: string;
  ticker_link_sample?: string[];
  title: string;
  url_domain?: string;
};

type NewsPayload = {
  as_of: string;
  has_more: boolean;
  next_before: string;
  next_before_id: string;
  rows: NewsRow[];
  window_start: string;
};

type NewsDetailPayload = {
  article: {
    article_url: string;
    author: string;
    channels: string[];
    classification: NewsClassification;
    news_kind: NewsKindValue;
    provider_tags: string[];
    published_at_utc: string;
    text: string;
    title: string;
    url_domain: string;
  };
  tickers: string[];
};

const NEWS_SELECTION_EVENT = "quant-news-selection";
type NewsKindValue = "ai" | "analyst" | "company" | "editorial" | "insights" | "market" | "multi" | "regulatory" | "why_moving";
type NewsOrigin = "analyst" | "automated" | "editorial" | "issuer" | "regulatory" | "third_party" | "unknown";
type NewsScope = "market_wide" | "multi_ticker" | "single_ticker";
type NewsFormat = "ai_generated" | "analyst_action" | "company_announcement" | "earnings_flash" | "editorial_coverage" | "general" | "insights" | "macro_release" | "multi_company_coverage" | "regulatory_filing" | "trading_halt" | "why_moving";
type NewsClassification = { confidence: number; evidence: string[]; format: NewsFormat; is_company_news: boolean; kind: NewsKindValue; origin: NewsOrigin; scope: NewsScope; topics: string[]; version: string };
type NewsTemperature = "cold" | "hot" | "old";
type AllNewsSettings = { content: string; kind: string; lookbackHours: number; ticker: string };

// Product-wide contract: hot is neon red (<= 4h), cold is neon blue (<= 24h),
// and old is neutral gray. Never substitute success/danger/info semantic colors.
const NEWS_HOT_MINUTES = 4 * 60;
const NEWS_COLD_MINUTES = 24 * 60;

export function AllNewsContainer({ asOf, live = false, onSettingsChange, settings }: { asOf: string; live?: boolean; onSettingsChange: (patch: Partial<AllNewsSettings>) => void; settings: AllNewsSettings }) {
  const [search, setSearch] = useState("");
  const [committedSearch, setCommittedSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const state = useNewsQuery({ asOf, content: settings.content, hours: settings.lookbackHours, kind: settings.kind, live, refreshKey, search: committedSearch, ticker: settings.ticker });
  const presentations = useTickerPresentations(state.rows.flatMap((row) => row.ticker_link_sample ?? []));

  return <section className="news-all" aria-label="All news">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); setCommittedSearch(search.trim()); }}>
      <label className="news-search"><Search size={13} /><input aria-label="Search all news" onChange={(event) => setSearch(event.target.value)} placeholder="Search headlines, text, author or source" value={search} /></label>
      <button className="button secondary compact news-search-submit" type="submit">Search</button>
      <label><span>Window</span><select aria-label="News time window" onChange={(event) => onSettingsChange({ lookbackHours: Number(event.target.value) })} value={settings.lookbackHours}><option value={1}>1 hour</option><option value={6}>6 hours</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option></select></label>
      <label><span>Ticker</span><input aria-label="Filter by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any" value={settings.ticker} /></label>
      <label><span>Type</span><select aria-label="News type" onChange={(event) => onSettingsChange({ kind: event.target.value })} value={settings.kind}><option value="all">All types</option><option value="company">Company</option><option value="regulatory">Regulatory</option><option value="why_moving">Why moving</option><option value="analyst">Analyst</option><option value="editorial">Editorial</option><option value="insights">Insights</option><option value="multi">Multi-company</option><option value="ai">AI</option><option value="market">Market</option></select></label>
      <label><span>Text</span><select aria-label="News text coverage" onChange={(event) => onSettingsChange({ content: event.target.value })} value={settings.content}><option value="all">All</option><option value="full">Full text</option><option value="title">Title only</option></select></label>
      <button aria-label="Refresh news" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh" type="button"><RefreshCw size={13} /></button>
    </form>
    <NewsStatus state={state} />
    <div className="news-table-wrap">
      <table className="news-table"><thead><tr><th>Time</th><th>Ticker</th><th>Type</th><th>Headline</th><th>Source</th><th>Text</th></tr></thead><tbody>
        {state.rows.map((row) => <tr key={row.canonical_news_id} tabIndex={0}>
          <td><MarketTime className="news-row-time" dateStyle="short" includeDate value={row.published_at_utc} /></td>
          <td><TickerList presentations={presentations} tickers={row.ticker_link_sample} /></td>
          <td><NewsKind classification={classificationFromRow(row)} /><NewsTopics kind={classificationFromRow(row).kind} topics={row.news_topics ?? row.classification?.topics} /></td>
          <td><button className="news-headline-button" onClick={() => openNewsPage(row.canonical_news_id)} type="button"><strong>{row.title || "Untitled story"}</strong>{row.text_preview ? <small>{row.text_preview}</small> : null}</button></td>
          <td>{row.url_domain || "—"}</td><td><NewsTextState row={row} /></td>
        </tr>)}
      </tbody></table>
      {!state.loading && !state.rows.length ? <NewsEmpty label="No news matches this query." /> : null}
    </div>
    {state.hasMore ? <button className="news-load-more" disabled={state.loadingMore} onClick={state.loadMore} type="button">{state.loadingMore ? "Loading…" : "Load older news"}</button> : null}
  </section>;
}

export function TickerNewsContainer({ asOf, live = false, settings, symbol }: { asOf: string; live?: boolean; settings: { lookbackHours: number; showTeaser: boolean }; symbol: string }) {
  const state = useNewsQuery({ asOf, content: "all", hours: settings.lookbackHours, kind: "all", live, refreshKey: 0, search: "", ticker: symbol });
  const presentations = useTickerPresentations([symbol]);
  const effectiveAsOf = state.asOf || asOf;
  const asOfMs = Date.parse(effectiveAsOf);
  const orderedRows = [...state.rows].sort(compareNewsRecency);
  const companyRows = orderedRows.filter(isCompanyNews);
  const otherRows = orderedRows.filter((row) => !isCompanyNews(row));
  return <section className="ticker-news" aria-label={`${symbol} news`}>
    <header><div><TickerIdentity className="ticker-news-symbol" logoUrl={presentations[symbol]?.logo_url} ticker={symbol} /><span>Recent coverage</span></div><small>{state.rows.length} stories · through <MarketTime value={effectiveAsOf} /></small></header>
    <NewsStatus state={state} compact />
    <div className="ticker-news-feed">
      <TickerNewsSection asOf={effectiveAsOf} asOfMs={asOfMs} emptyLabel="No company-specific news in this window." label="Company news" rows={companyRows} showTeaser={settings.showTeaser} />
      <TickerNewsSection asOf={effectiveAsOf} asOfMs={asOfMs} emptyLabel="No broader coverage in this window." label="Other coverage" rows={otherRows} showTeaser={settings.showTeaser} />
      {!state.loading && !state.rows.length ? <NewsEmpty label={`No ${symbol} news in the last ${settings.lookbackHours} hours.`} /> : null}
    </div>
  </section>;
}

function TickerNewsSection({ asOf, asOfMs, emptyLabel, label, rows, showTeaser }: { asOf: string; asOfMs: number; emptyLabel: string; label: string; rows: NewsRow[]; showTeaser: boolean }) {
  return <section className="ticker-news-section" aria-label={label}>
    <header><strong>{label}</strong><span>{rows.length}</span></header>
    {rows.map((row) => <TickerNewsStory asOf={asOf} asOfMs={asOfMs} key={row.canonical_news_id} row={row} showTeaser={showTeaser} />)}
    {!rows.length ? <small className="ticker-news-section-empty">{emptyLabel}</small> : null}
  </section>;
}

function TickerNewsStory({ asOf, asOfMs, row, showTeaser }: { asOf: string; asOfMs: number; row: NewsRow; showTeaser: boolean }) {
  const tone = newsTemperature(row.published_at_utc, asOfMs);
  const TemperatureIcon = newsTemperaturePresentation(tone).Icon;
  return <article data-tone={tone}>
    <div aria-label={`${tone} news`} className="ticker-news-marker" title={`${tone} news`}><TemperatureIcon size={14} /></div>
    <div><div className="ticker-news-meta"><MarketTime dateStyle="short" includeDate={!sameExchangeDate(row.published_at_utc, asOf)} value={row.published_at_utc} /><em data-tone={tone}>{tone}</em><NewsKind classification={classificationFromRow(row)} /><NewsTopics kind={classificationFromRow(row).kind} topics={row.news_topics ?? row.classification?.topics} compact /><span>{row.url_domain}</span></div><button className="ticker-news-open" onClick={() => openNewsPage(row.canonical_news_id)} type="button"><strong>{row.title}</strong>{showTeaser && row.text_preview ? <p>{row.text_preview}</p> : null}</button></div>
  </article>;
}

export function NewsDetailContainer({ asOf, canvasId, requestedNewsId }: { asOf: string; canvasId: string; requestedNewsId?: string }) {
  const [newsId, setNewsId] = useState(() => requestedNewsId || readSelectedNews(canvasId));
  const [detail, setDetail] = useState<NewsDetailPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const onSelection = (event: Event) => {
      const selected = (event as CustomEvent<{ canvasId: string; newsId: string }>).detail;
      if (selected.canvasId === canvasId) setNewsId(selected.newsId);
    };
    window.addEventListener(NEWS_SELECTION_EVENT, onSelection);
    const onStorage = (event: StorageEvent) => {
      if (event.key === selectionKey(canvasId) && event.newValue) setNewsId(event.newValue);
    };
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(NEWS_SELECTION_EVENT, onSelection);
      window.removeEventListener("storage", onStorage);
    };
  }, [canvasId]);
  useEffect(() => {
    if (!newsId) { setDetail(null); return; }
    const controller = new AbortController(); setLoading(true); setError("");
    api<NewsDetailPayload>(`/api/trading/news/detail/${encodeURIComponent(newsId)}`, { signal: controller.signal, timeoutMs: 30000 })
      .then(setDetail).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [newsId]);
  const detailTickers = detail?.tickers ?? [];
  const presentations = useTickerPresentations(detailTickers);
  if (!newsId) return <NewsEmpty label="Choose a headline in All News or Ticker News to read it here." />;
  if (loading && !detail) return <div className="canvas-preview-loading">Loading article…</div>;
  if (error) return <NewsEmpty label={error} />;
  if (!detail) return null;
  const row = detail.article;
  const title = row.title || "Untitled story";
  const body = row.text;
  const classification = row.classification;
  const tags = Array.from(new Set(classification.topics.concat(row.channels, row.provider_tags))).slice(0, 16);
  const tone = newsTemperature(row.published_at_utc, Date.parse(asOf));
  const kind = isNewsKind(row.news_kind) ? row.news_kind : classification.kind;
  return <article className="news-reader">
    <header><div className="news-reader-kicker"><NewsTemperatureTag tone={tone} /><MarketTime includeDate value={row.published_at_utc} /><NewsKind classification={{ ...classification, kind }} /><span>{row.url_domain || "News"}</span></div><h1><MarketNumberText text={title} /></h1><div className="news-reader-byline"><span>{row.author || "Unknown author"}</span><TickerList presentations={presentations} tickers={detailTickers} /></div>{tags.length ? <div className="news-reader-tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}<ClassificationEvidence classification={classification} /></header>
    {body ? <div className="news-reader-body">{articleParagraphs(body).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`}><MarketNumberText text={paragraph} /></p>)}</div> : <NewsEmpty label="This record contains title metadata but no readable article text." />}
    <footer>{row.article_url ? <a href={row.article_url} rel="noreferrer" target="_blank">Open original source <ExternalLink size={12} /></a> : null}</footer>
  </article>;
}

function useNewsQuery({ asOf, content, hours, kind, live, refreshKey, search, ticker }: { asOf: string; content: string; hours: number; kind: string; live: boolean; refreshKey: number; search: string; ticker: string }) {
  const [rows, setRows] = useState<NewsRow[]>([]); const [payload, setPayload] = useState<NewsPayload | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false);
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveError, setLiveError] = useState("");
  const latestRevision = useRef<number | null>(null);
  const load = useCallback(async (before = "", beforeId = "", signal?: AbortSignal, pageAsOf = "") => {
    const queryAsOf = pageAsOf || (live ? new Date().toISOString() : asOf);
    const next = await api<NewsPayload>(`/api/trading/news${query({ as_of: queryAsOf, before: before || undefined, before_id: beforeId || undefined, content, kind: kind === "all" ? undefined : kind, limit: 100, lookback_hours: hours, search: search || undefined, ticker: ticker || undefined })}`, { signal, timeoutMs: 30000 });
    if (signal?.aborted) return;
    setError("");
    setPayload(next); setRows((current) => before ? [...current, ...next.rows.filter((row) => !current.some((item) => item.canonical_news_id === row.canonical_news_id))] : next.rows);
  }, [asOf, content, hours, kind, live, search, ticker]);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(""); load("", "", controller.signal).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load, refreshKey]);
  useEffect(() => {
    if (!live) { setLiveConnected(false); latestRevision.current = null; return; }
    let closed = false;
    let retryTimer = 0;
    let socket: WebSocket | null = null;
    let refreshController: AbortController | null = null;
    const connect = () => {
      const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
      socket = new WebSocket(`${protocol}//${window.location.host}/api/trading/news/stream${query({ ticker: ticker || undefined })}`);
      socket.onopen = () => { if (!closed) { setLiveConnected(true); setLiveError(""); } };
      socket.onmessage = (event) => {
        if (closed) return;
        try {
          const message = JSON.parse(String(event.data)) as { error?: string; revision?: number };
          if (message.error) { setLiveError(message.error); socket?.close(); return; }
          const revision = Number(message.revision);
          if (!Number.isFinite(revision) || latestRevision.current === revision) return;
          const firstSnapshot = latestRevision.current === null;
          latestRevision.current = revision;
          if (firstSnapshot) return;
          refreshController?.abort();
          refreshController = new AbortController();
          load("", "", refreshController.signal).catch((reason) => { if (!refreshController?.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
        } catch (reason) {
          setLiveError(reason instanceof Error ? reason.message : String(reason));
        }
      };
      socket.onclose = () => {
        if (closed) return;
        setLiveConnected(false);
        retryTimer = window.setTimeout(connect, 2000);
      };
      socket.onerror = () => socket?.close();
    };
    connect();
    return () => { closed = true; window.clearTimeout(retryTimer); refreshController?.abort(); socket?.close(); };
  }, [live, load, ticker]);
  const loadMore = useCallback(() => { if (!payload?.next_before) return; setLoadingMore(true); load(payload.next_before, payload.next_before_id, undefined, payload.as_of).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoadingMore(false)); }, [load, payload]);
  return { asOf: payload?.as_of, error, hasMore: Boolean(payload?.has_more), live, liveConnected, liveError, loadMore, loading, loadingMore, rows, windowStart: payload?.window_start };
}

// Product UI reports user-relevant freshness only. Never render database/table
// names, storage paths, raw service errors, implementation notes, or agent/chat text.
function NewsStatus({ compact, state }: { compact?: boolean; state: ReturnType<typeof useNewsQuery> }) { return <div className="news-status" data-compact={compact ? "true" : "false"}>{state.loading ? <span>Querying news…</span> : state.error ? <strong>{state.error}</strong> : <><span>{state.rows.length} returned</span>{!compact && state.windowStart ? <span className="news-window-start"><span>Since</span><MarketTime dateStyle="short" includeDate layout="inline" value={state.windowStart} /></span> : null}<span className="news-source-label">{state.live ? state.liveConnected ? "Live updates" : "Reconnecting…" : "Point-in-time"}</span></>}</div>; }
function NewsEmpty({ label }: { label: string }) { return <div className="news-empty"><Newspaper size={18} /><span>{label}</span></div>; }
function TickerList({ presentations, tickers = [] }: { presentations: Record<string, TickerPresentation>; tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <b key={ticker}><TickerIdentity logoUrl={presentations[ticker]?.logo_url} ticker={ticker} /></b>)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function NewsTextState({ row }: { row: NewsRow }) { return <span className="news-text-state" data-state={row.is_title_only ? "title" : "full"}>{row.is_title_only ? "Title" : row.has_pdf ? "PDF" : row.has_external_text ? "Full" : "Body"}</span>; }
function NewsKind({ classification }: { classification: NewsClassification }) { const values = { ai: { Icon: Bot, label: "AI" }, analyst: { Icon: TrendingUp, label: "Analyst" }, company: { Icon: Building2, label: classification.format === "earnings_flash" ? "Company earnings" : "Company" }, editorial: { Icon: Newspaper, label: "Editorial" }, insights: { Icon: Lightbulb, label: "Insights" }, market: { Icon: Globe2, label: classification.format === "trading_halt" ? "Trading halt" : "Market" }, multi: { Icon: Layers3, label: "Multi-company" }, regulatory: { Icon: FileCheck2, label: "Regulatory" }, why_moving: { Icon: Megaphone, label: "Why moving" } }; const value = values[classification.kind]; return <span className="news-kind" data-kind={classification.kind} title={`${Math.round(classification.confidence * 100)}% classification confidence`}><value.Icon size={11} />{value.label}</span>; }
function NewsTopics({ compact = false, kind, topics = [] }: { compact?: boolean; kind?: NewsKindValue; topics?: string[] }) { const redundant = kind === "analyst" ? "analyst" : kind === "why_moving" ? "why moving" : kind === "ai" ? "AI generated" : ""; const relevant = topics.filter((topic) => topic !== redundant); const visible = relevant.slice(0, compact ? 1 : 2); if (!visible.length) return null; return <span className="news-topic-list">{visible.map((topic) => <span key={topic}>{topic}</span>)}{relevant.length > visible.length ? <span>+{relevant.length - visible.length}</span> : null}</span>; }
function ClassificationEvidence({ classification }: { classification: NewsClassification }) { return <details className="news-classification-evidence"><summary>Why this label</summary><dl><dt>Origin</dt><dd>{readableLabel(classification.origin)}</dd><dt>Format</dt><dd>{readableLabel(classification.format)}</dd><dt>Scope</dt><dd>{readableLabel(classification.scope)}</dd><dt>Confidence</dt><dd>{Math.round(classification.confidence * 100)}%</dd>{classification.evidence.length ? <><dt>Evidence</dt><dd>{classification.evidence.join(" · ")}</dd></> : null}</dl></details>; }
function MarketNumberText({ text }: { text: string }) { const matches = Array.from(text.matchAll(MARKET_NUMBER_PATTERN)); if (!matches.length) return text; const parts: Array<string | ReactElement> = []; let cursor = 0; matches.forEach((match, index) => { const start = match.index; if (start > cursor) parts.push(text.slice(cursor, start)); const value = match[0]; const kind = /%|percent|basis|bps/i.test(value) ? "rate" : "price"; parts.push(<span className="market-number" data-market-number={kind} key={`${start}-${index}`}>{value}</span>); cursor = start + value.length; }); if (cursor < text.length) parts.push(text.slice(cursor)); return <>{parts}</>; }
function NewsTemperatureTag({ tone }: { tone: NewsTemperature }) { const value = newsTemperaturePresentation(tone); return <span className="news-temperature" data-tone={tone}><value.Icon size={12} /><em>{value.label}</em></span>; }
function newsTemperature(publishedAt: string, asOfMs: number): NewsTemperature { const publishedMs = Date.parse(publishedAt); const ageMinutes = Number.isFinite(publishedMs) && Number.isFinite(asOfMs) ? Math.max(0, (asOfMs - publishedMs) / 60_000) : Number.POSITIVE_INFINITY; return ageMinutes <= NEWS_HOT_MINUTES ? "hot" : ageMinutes <= NEWS_COLD_MINUTES ? "cold" : "old"; }
function newsTemperaturePresentation(tone: NewsTemperature) { return tone === "hot" ? { Icon: Flame, label: "Hot" } : tone === "cold" ? { Icon: Snowflake, label: "Cold" } : { Icon: Clock3, label: "Old" }; }
function isNewsKind(value: unknown): value is NewsKindValue { return ["ai", "analyst", "company", "editorial", "insights", "market", "multi", "regulatory", "why_moving"].includes(String(value)); }
function classificationFromRow(row: NewsRow): NewsClassification { if (row.classification) return row.classification; const kind = isNewsKind(row.news_kind) ? row.news_kind : "market"; return { confidence: row.classification_confidence ?? 0.65, evidence: row.classification_evidence ?? [], format: row.news_format ?? "general", is_company_news: row.is_company_news ?? (kind === "company" || kind === "regulatory"), kind, origin: row.news_origin ?? "unknown", scope: row.news_scope ?? ((row.ticker_link_sample?.length ?? 0) === 1 ? "single_ticker" : (row.ticker_link_sample?.length ?? 0) > 1 ? "multi_ticker" : "market_wide"), topics: row.news_topics ?? [], version: "news_rules_v1" }; }
function isCompanyNews(row: NewsRow) { return classificationFromRow(row).is_company_news; }
function readableLabel(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
const MARKET_NUMBER_PATTERN = /(?:[+\-−]\s*)?(?:[$€£¥]\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:thousand|million|billion|trillion|[KMBT]))?|(?:USD|CAD|EUR|GBP|JPY|CNY|HKD|AUD)\s*\$?\s*\d[\d,]*(?:\.\d+)?(?:\s*(?:thousand|million|billion|trillion|[KMBT]))?|\d[\d,]*(?:\.\d+)?\s*(?:USD|CAD|EUR|GBP|JPY|CNY|HKD|AUD)|\d[\d,]*(?:\.\d+)?\s*(?:%|percent(?:age points?)?|basis points?|bps))/gi;
function articleParagraphs(value: string) { const explicit = value.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.split(/(?<=[.!?])\s+(?=["“‘']?[A-Z0-9])/).map((item) => item.trim()).filter(Boolean); const paragraphs: string[] = []; for (let index = 0; index < sentences.length; index += 4) paragraphs.push(sentences.slice(index, index + 4).join(" ")); return paragraphs.length ? paragraphs : [value]; }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.map(String).filter(Boolean) : []; }
function sameExchangeDate(left: string, right: string) { return exchangeDateKey(left) === exchangeDateKey(right); }
function exchangeDateKey(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "" : new Intl.DateTimeFormat("en-CA", { day: "2-digit", month: "2-digit", timeZone: "America/New_York", year: "numeric" }).format(date); }
function compareNewsRecency(left: NewsRow, right: NewsRow) { return Date.parse(right.published_at_utc) - Date.parse(left.published_at_utc); }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.news-selection.${canvasId}`; }
function readSelectedNews(canvasId: string) { return window.localStorage.getItem(selectionKey(canvasId)) || ""; }
function selectNews(canvasId: string, newsId: string) { window.localStorage.setItem(selectionKey(canvasId), newsId); window.dispatchEvent(new CustomEvent(NEWS_SELECTION_EVENT, { detail: { canvasId, newsId } })); }
function prepareNewsReader(newsId: string) { ensureNewsReaderCanvas(); selectNews(NEWS_READER_CANVAS_ID, newsId); }
function newsPageUrl(newsId: string) { const url = new URL(focusCanvasUrl(NEWS_READER_CANVAS_ID, "news_detail")); url.searchParams.set("news", newsId); return url.toString(); }
function openNewsPage(newsId: string) { prepareNewsReader(newsId); window.open(newsPageUrl(newsId), "quant-news-reader"); }
