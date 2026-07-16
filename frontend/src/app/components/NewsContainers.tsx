import { ExternalLink, Flame, Newspaper, RefreshCw, Search, Sparkles } from "lucide-react";
import { useCallback, useEffect, useMemo, useState } from "react";

import { api, query } from "../../api/client";
import { focusCanvasUrl } from "../canvasWorkspace";

type NewsRow = {
  article_url?: string;
  author?: string;
  canonical_news_id: string;
  channels?: string[];
  full_text_chars?: number;
  has_external_text?: boolean;
  has_pdf?: boolean;
  is_title_only?: boolean;
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
  source: string;
  window_start: string;
};

type NewsDetailPayload = {
  canonical_news_id: string;
  row: Record<string, unknown>;
  ticker_rows: Record<string, unknown>[];
};

const NEWS_SELECTION_EVENT = "quant-news-selection";

export function AllNewsContainer({ asOf, canvasId, onSettingsChange, settings }: { asOf: string; canvasId: string; onSettingsChange: (patch: Partial<{ content: string; lookbackHours: number; ticker: string }>) => void; settings: { content: string; lookbackHours: number; ticker: string } }) {
  const [search, setSearch] = useState("");
  const [committedSearch, setCommittedSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const state = useNewsQuery({ asOf, content: settings.content, hours: settings.lookbackHours, refreshKey, search: committedSearch, ticker: settings.ticker });

  return <section className="news-all" aria-label="All news">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); setCommittedSearch(search.trim()); }}>
      <label className="news-search"><Search size={13} /><input aria-label="Search all news" onChange={(event) => setSearch(event.target.value)} placeholder="Search headlines, text, author or source" value={search} /></label>
      <button className="button secondary compact news-search-submit" type="submit">Search</button>
      <label><span>Window</span><select aria-label="News time window" onChange={(event) => onSettingsChange({ lookbackHours: Number(event.target.value) })} value={settings.lookbackHours}><option value={1}>1 hour</option><option value={6}>6 hours</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option></select></label>
      <label><span>Ticker</span><input aria-label="Filter by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any" value={settings.ticker} /></label>
      <label><span>Text</span><select aria-label="News text coverage" onChange={(event) => onSettingsChange({ content: event.target.value })} value={settings.content}><option value="all">All</option><option value="full">Full text</option><option value="title">Title only</option></select></label>
      <button aria-label="Refresh news" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh" type="button"><RefreshCw size={13} /></button>
    </form>
    <NewsStatus state={state} />
    <div className="news-table-wrap">
      <table className="news-table"><thead><tr><th>Time</th><th>Ticker</th><th>Headline</th><th>Source</th><th>Text</th></tr></thead><tbody>
        {state.rows.map((row) => <tr key={row.canonical_news_id} onDoubleClick={() => openNewsPage(canvasId, row.canonical_news_id)} onClick={() => selectNews(canvasId, row.canonical_news_id)} tabIndex={0}>
          <td><time dateTime={row.published_at_utc}>{formatNewsTime(row.published_at_utc)}</time></td>
          <td><TickerList tickers={row.ticker_link_sample} /></td>
          <td><button className="news-headline-button" onClick={() => selectNews(canvasId, row.canonical_news_id)} type="button"><strong>{row.title || "Untitled story"}</strong>{row.text_preview ? <small>{row.text_preview}</small> : null}</button></td>
          <td>{row.url_domain || "—"}</td><td><NewsTextState row={row} /></td>
        </tr>)}
      </tbody></table>
      {!state.loading && !state.rows.length ? <NewsEmpty label="No news matches this query." /> : null}
    </div>
    {state.hasMore ? <button className="news-load-more" disabled={state.loadingMore} onClick={state.loadMore} type="button">{state.loadingMore ? "Loading…" : "Load older news"}</button> : null}
  </section>;
}

export function TickerNewsContainer({ asOf, canvasId, settings, symbol }: { asOf: string; canvasId: string; settings: { lookbackHours: number; showTeaser: boolean }; symbol: string }) {
  const state = useNewsQuery({ asOf, content: "all", hours: settings.lookbackHours, refreshKey: 0, search: "", ticker: symbol });
  const asOfMs = Date.parse(asOf);
  return <section className="ticker-news" aria-label={`${symbol} news`}>
    <header><div><strong>{symbol}</strong><span>Recent coverage</span></div><small>{state.rows.length} stories · through {formatNewsTime(asOf)}</small></header>
    <NewsStatus state={state} compact />
    <div className="ticker-news-feed">
      {state.rows.map((row) => {
        const ageMinutes = Math.max(0, (asOfMs - Date.parse(row.published_at_utc)) / 60000);
        const tone = ageMinutes <= 15 ? "hot" : ageMinutes <= 120 ? "recent" : "normal";
        return <article data-tone={tone} key={row.canonical_news_id}>
          <div className="ticker-news-marker">{tone === "hot" ? <Flame size={13} /> : tone === "recent" ? <Sparkles size={13} /> : <Newspaper size={13} />}</div>
          <div><div className="ticker-news-meta"><time>{formatNewsTime(row.published_at_utc)}</time>{tone !== "normal" ? <em>{tone}</em> : null}<span>{row.url_domain}</span></div><strong>{row.title}</strong>{settings.showTeaser && row.text_preview ? <p>{row.text_preview}</p> : null}<a href={newsPageUrl(canvasId, row.canonical_news_id)} onClick={() => selectNews(canvasId, row.canonical_news_id)} rel="noreferrer" target="_blank">Details <ExternalLink size={11} /></a></div>
        </article>;
      })}
      {!state.loading && !state.rows.length ? <NewsEmpty label={`No ${symbol} news in the last ${settings.lookbackHours} hours.`} /> : null}
    </div>
  </section>;
}

export function NewsDetailContainer({ canvasId, requestedNewsId }: { canvasId: string; requestedNewsId?: string }) {
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
    return () => window.removeEventListener(NEWS_SELECTION_EVENT, onSelection);
  }, [canvasId]);
  useEffect(() => {
    if (!newsId) { setDetail(null); return; }
    const controller = new AbortController(); setLoading(true); setError("");
    api<NewsDetailPayload>(`/api/trading/news/detail/${encodeURIComponent(newsId)}`, { signal: controller.signal, timeoutMs: 30000 })
      .then(setDetail).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [newsId]);
  if (!newsId) return <NewsEmpty label="Choose a headline in All News or Ticker News to read it here." />;
  if (loading && !detail) return <div className="canvas-preview-loading">Loading article…</div>;
  if (error) return <NewsEmpty label={error} />;
  if (!detail) return null;
  const row = detail.row;
  const title = String(row.title || row.normalized_title || "Untitled story");
  const body = firstText(row.normalized_full_text, row.external_text, row.body_text, row.pdf_text);
  const tickers = detail.ticker_rows.map((item) => String(item.ticker || "")).filter(Boolean);
  const tags = stringList(row.channels).concat(stringList(row.provider_tags)).slice(0, 12);
  return <article className="news-reader">
    <header><div className="news-reader-kicker"><time>{formatNewsDate(String(row.published_at_utc || ""))}</time><span>{String(row.url_domain || "News")}</span></div><h1>{title}</h1><div className="news-reader-byline"><span>{String(row.author || "Unknown author")}</span><TickerList tickers={tickers} /></div></header>
    {body ? <div className="news-reader-body">{articleParagraphs(body).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`}>{paragraph}</p>)}</div> : <NewsEmpty label="This record contains title metadata but no readable article text." />}
    <footer>{tags.length ? <div className="news-reader-tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}{row.article_url ? <a href={String(row.article_url)} rel="noreferrer" target="_blank">Open original source <ExternalLink size={12} /></a> : null}<details><summary>Record provenance</summary><dl><dt>Canonical ID</dt><dd>{detail.canonical_news_id}</dd><dt>Downloaded</dt><dd>{String(row.downloaded_at_utc || "—")}</dd><dt>Text quality</dt><dd>{stringList(row.content_quality_flags).join(", ") || "No flags"}</dd></dl></details></footer>
  </article>;
}

function useNewsQuery({ asOf, content, hours, refreshKey, search, ticker }: { asOf: string; content: string; hours: number; refreshKey: number; search: string; ticker: string }) {
  const [rows, setRows] = useState<NewsRow[]>([]); const [payload, setPayload] = useState<NewsPayload | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false);
  const load = useCallback(async (before = "", beforeId = "", signal?: AbortSignal) => {
    const next = await api<NewsPayload>(`/api/trading/news${query({ as_of: asOf, before: before || undefined, before_id: beforeId || undefined, content, limit: 100, lookback_hours: hours, search: search || undefined, ticker: ticker || undefined })}`, { signal, timeoutMs: 30000 });
    if (signal?.aborted) return;
    setPayload(next); setRows((current) => before ? [...current, ...next.rows.filter((row) => !current.some((item) => item.canonical_news_id === row.canonical_news_id))] : next.rows);
  }, [asOf, content, hours, search, ticker]);
  useEffect(() => { const controller = new AbortController(); setLoading(true); setError(""); load("", "", controller.signal).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load, refreshKey]);
  const loadMore = useCallback(() => { if (!payload?.next_before) return; setLoadingMore(true); load(payload.next_before, payload.next_before_id).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoadingMore(false)); }, [load, payload]);
  return { error, hasMore: Boolean(payload?.has_more), loadMore, loading, loadingMore, rows, source: payload?.source, windowStart: payload?.window_start };
}

function NewsStatus({ compact, state }: { compact?: boolean; state: ReturnType<typeof useNewsQuery> }) { return <div className="news-status" data-compact={compact ? "true" : "false"}>{state.loading ? <span>Querying news…</span> : state.error ? <strong>{state.error}</strong> : <><span>{state.rows.length} returned</span>{!compact ? <span>{state.windowStart ? `Since ${formatNewsDate(state.windowStart)}` : ""}</span> : null}<span>{state.source || "Point-in-time DB"}</span></>}</div>; }
function NewsEmpty({ label }: { label: string }) { return <div className="news-empty"><Newspaper size={18} /><span>{label}</span></div>; }
function TickerList({ tickers = [] }: { tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <b key={ticker}>{ticker}</b>)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function NewsTextState({ row }: { row: NewsRow }) { return <span className="news-text-state" data-state={row.is_title_only ? "title" : "full"}>{row.is_title_only ? "Title" : row.has_pdf ? "PDF" : row.has_external_text ? "Full" : "Body"}</span>; }
function formatNewsTime(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat(undefined, { hour: "2-digit", minute: "2-digit" }).format(date); }
function formatNewsDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? "—" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeStyle: "short" }).format(date); }
function firstText(...values: unknown[]) { for (const value of values) if (typeof value === "string" && value.trim()) return value.trim(); return ""; }
function articleParagraphs(value: string) { const explicit = value.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.match(/[^.!?]+(?:[.!?]+[\]"')]*|$)/g)?.map((item) => item.trim()).filter(Boolean) ?? [value]; const paragraphs: string[] = []; for (let index = 0; index < sentences.length; index += 4) paragraphs.push(sentences.slice(index, index + 4).join(" ")); return paragraphs; }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.map(String).filter(Boolean) : []; }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.news-selection.${canvasId}`; }
function readSelectedNews(canvasId: string) { return window.localStorage.getItem(selectionKey(canvasId)) || ""; }
function selectNews(canvasId: string, newsId: string) { window.localStorage.setItem(selectionKey(canvasId), newsId); window.dispatchEvent(new CustomEvent(NEWS_SELECTION_EVENT, { detail: { canvasId, newsId } })); }
function newsPageUrl(canvasId: string, newsId: string) { const url = new URL(focusCanvasUrl(canvasId, "news_detail")); url.searchParams.set("news", newsId); return url.toString(); }
function openNewsPage(canvasId: string, newsId: string) { selectNews(canvasId, newsId); window.open(newsPageUrl(canvasId, newsId), "_blank", "noopener,noreferrer"); }
