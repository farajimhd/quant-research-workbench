import { Bot, Building2, Clock3, ExternalLink, Flame, Globe2, Layers3, Lightbulb, Newspaper, RefreshCw, Search, Snowflake, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

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
  news_kind?: "ai" | "analyst" | "company" | "insights" | "market" | "multi";
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
type NewsKindValue = NonNullable<NewsRow["news_kind"]>;
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
      <label><span>Type</span><select aria-label="News type" onChange={(event) => onSettingsChange({ kind: event.target.value })} value={settings.kind}><option value="all">All types</option><option value="company">Company</option><option value="insights">Insights</option><option value="analyst">Analyst</option><option value="multi">Multi</option><option value="ai">AI</option><option value="market">Market</option></select></label>
      <label><span>Text</span><select aria-label="News text coverage" onChange={(event) => onSettingsChange({ content: event.target.value })} value={settings.content}><option value="all">All</option><option value="full">Full text</option><option value="title">Title only</option></select></label>
      <button aria-label="Refresh news" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh" type="button"><RefreshCw size={13} /></button>
    </form>
    <NewsStatus state={state} />
    <div className="news-table-wrap">
      <table className="news-table"><thead><tr><th>Time</th><th>Ticker</th><th>Type</th><th>Headline</th><th>Source</th><th>Text</th></tr></thead><tbody>
        {state.rows.map((row) => <tr key={row.canonical_news_id} tabIndex={0}>
          <td><MarketTime className="news-row-time" dateStyle="short" includeDate value={row.published_at_utc} /></td>
          <td><TickerList presentations={presentations} tickers={row.ticker_link_sample} /></td>
          <td><NewsKind kind={row.news_kind} /></td>
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
  const companyRows = orderedRows.filter((row) => row.news_kind === "company");
  const otherRows = orderedRows.filter((row) => row.news_kind !== "company");
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
    <div><div className="ticker-news-meta"><MarketTime dateStyle="short" includeDate={!sameExchangeDate(row.published_at_utc, asOf)} value={row.published_at_utc} /><em data-tone={tone}>{tone}</em><NewsKind kind={row.news_kind} /><span>{row.url_domain}</span></div><button className="ticker-news-open" onClick={() => openNewsPage(row.canonical_news_id)} type="button"><strong>{row.title}</strong>{showTeaser && row.text_preview ? <p>{row.text_preview}</p> : null}</button></div>
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
  const tags = row.channels.concat(row.provider_tags).slice(0, 12);
  const tone = newsTemperature(row.published_at_utc, Date.parse(asOf));
  const kind = isNewsKind(row.news_kind) ? row.news_kind : "market";
  return <article className="news-reader">
    <header><div className="news-reader-kicker"><NewsTemperatureTag tone={tone} /><MarketTime includeDate value={row.published_at_utc} /><NewsKind kind={kind} /><span>{row.url_domain || "News"}</span></div><h1>{title}</h1><div className="news-reader-byline"><span>{row.author || "Unknown author"}</span><TickerList presentations={presentations} tickers={detailTickers} /></div>{tags.length ? <div className="news-reader-tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}</header>
    {body ? <div className="news-reader-body">{articleParagraphs(body).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`}>{paragraph}</p>)}</div> : <NewsEmpty label="This record contains title metadata but no readable article text." />}
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
function NewsKind({ kind = "market" }: { kind?: NewsRow["news_kind"] }) { const values = { ai: { Icon: Bot, label: "AI" }, analyst: { Icon: TrendingUp, label: "Analyst" }, company: { Icon: Building2, label: "Company" }, insights: { Icon: Lightbulb, label: "Insights" }, market: { Icon: Globe2, label: "Market" }, multi: { Icon: Layers3, label: "Multi" } }; const value = values[kind]; return <span className="news-kind" data-kind={kind}><value.Icon size={11} />{value.label}</span>; }
function NewsTemperatureTag({ tone }: { tone: NewsTemperature }) { const value = newsTemperaturePresentation(tone); return <span className="news-temperature" data-tone={tone}><value.Icon size={12} /><em>{value.label}</em></span>; }
function newsTemperature(publishedAt: string, asOfMs: number): NewsTemperature { const publishedMs = Date.parse(publishedAt); const ageMinutes = Number.isFinite(publishedMs) && Number.isFinite(asOfMs) ? Math.max(0, (asOfMs - publishedMs) / 60_000) : Number.POSITIVE_INFINITY; return ageMinutes <= NEWS_HOT_MINUTES ? "hot" : ageMinutes <= NEWS_COLD_MINUTES ? "cold" : "old"; }
function newsTemperaturePresentation(tone: NewsTemperature) { return tone === "hot" ? { Icon: Flame, label: "Hot" } : tone === "cold" ? { Icon: Snowflake, label: "Cold" } : { Icon: Clock3, label: "Old" }; }
function isNewsKind(value: unknown): value is NewsKindValue { return ["ai", "analyst", "company", "insights", "market", "multi"].includes(String(value)); }
function articleParagraphs(value: string) { const explicit = value.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.match(/[^.!?]+(?:[.!?]+[\]"')]*|$)/g)?.map((item) => item.trim()).filter(Boolean) ?? [value]; const paragraphs: string[] = []; for (let index = 0; index < sentences.length; index += 4) paragraphs.push(sentences.slice(index, index + 4).join(" ")); return paragraphs; }
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
