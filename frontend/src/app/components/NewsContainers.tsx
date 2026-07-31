import { Bot, Building2, CircleDot, Clock3, ExternalLink, FileCheck2, Flame, Globe2, History, Layers3, Lightbulb, Megaphone, Newspaper, RefreshCw, Search, Snowflake, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import { api, query } from "../../api/client";
import { NEWS_READER_CANVAS_ID, ensureNewsReaderCanvas, focusCanvasUrl } from "../canvasWorkspace";
import { MarketTime } from "./MarketTime";
import { normalizeSemanticDirection, SemanticDirectionMetric, SentimentSortButton, sortRowsBySentimentScore, type SentimentSortOrder } from "./SemanticDirectionMetric";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations, type TickerPresentation } from "./TickerIdentity";

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
  render_status?: "rendered" | "title_only" | "unrendered";
  intelligence_status?: "ready" | "unavailable";
  text_preview?: string;
  ticker_link_sample?: string[];
  title: string;
  url_domain?: string;
  scoped_labels?: ScopedNewsLabel[];
  scoped_summary?: ScopedNewsSummary | null;
};

type NewsPayload = {
  as_of: string;
  has_more: boolean;
  next_before: string;
  next_before_id: string;
  query_id: string;
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
    scoped_labels?: ScopedNewsLabel[];
    scoped_summary?: ScopedNewsSummary | null;
    render_status?: "rendered" | "title_only" | "unrendered";
    intelligence_status?: "ready" | "unavailable";
  };
  tickers: string[];
};

const NEWS_SELECTION_EVENT = "quant-news-selection";
type NewsKindValue = "ai" | "analyst" | "company" | "editorial" | "insights" | "market" | "multi" | "regulatory" | "why_moving";
type NewsOrigin = "analyst" | "automated" | "editorial" | "issuer" | "regulatory" | "third_party" | "unknown";
type NewsScope = "market_wide" | "multi_ticker" | "single_ticker";
type NewsFormat = "ai_generated" | "analyst_action" | "company_announcement" | "earnings_flash" | "editorial_coverage" | "general" | "insights" | "macro_release" | "multi_company_coverage" | "regulatory_filing" | "trading_halt" | "why_moving";
type NewsClassification = { confidence: number; evidence: string[]; format: NewsFormat; is_company_news: boolean; kind: NewsKindValue; origin: NewsOrigin; scope: NewsScope; topics: string[]; version: string };
type ScopedNewsLabel = {
  content_role: string;
  event_id: string;
  event_concepts: string[];
  event_tickers: string[];
  evidence_scope: string;
  forecast_trigger_eligible: boolean;
  issuer_history_context_eligible: boolean;
  issuer_role: string;
  labeling_version: string;
  modality: string;
  quality_flags: string[];
  confidence: number;
  source_type: string;
  source_subtype: string;
  issuer_relationship: string;
  scope: string;
  prior_primary_context_eligible: boolean;
  episode_followup_eligible: boolean;
  semantic_direction_basis: string[];
  reaction_evaluation_eligible: boolean;
  semantic_direction: string;
  semantic_evidence_text: string;
  semantic_score?: number | null;
  source_origin: string;
  ticker: string;
  time_orientation: string;
  unit_id: string;
  unit_role: string;
};
type ScopedNewsSummary = {
  content_role: string;
  event_concepts: string[];
  forecast_trigger_eligible: boolean;
  issuer_count: number;
  issuer_history_context_eligible: boolean;
  label_count: number;
  labeling_version: string;
  reaction_evaluation_eligible: boolean;
  semantic_direction: string;
  semantic_score?: number | null;
  source_origin: string;
  quality_flags?: string[];
  classified?: boolean;
};
type NewsTemperature = "cold" | "hot" | "old";
type AllNewsSettings = { content: string; endDate: string; kind: string; limit: number; lookbackHours: number; rangeMode: "custom" | "preset"; startDate: string; ticker: string };
type NewsSelection = { newsId: string; publishedAt: string; queryId: string };

// Product-wide contract: hot is neon red (<= 4h), cold is neon blue (<= 24h),
// and old is neutral gray. Never substitute success/danger/info semantic colors.
const NEWS_HOT_MINUTES = 4 * 60;
const NEWS_COLD_MINUTES = 24 * 60;

export function AllNewsContainer({ asOf, live = false, onSettingsChange, settings }: { asOf: string; live?: boolean; onSettingsChange: (patch: Partial<AllNewsSettings>) => void; settings: AllNewsSettings }) {
  const [search, setSearch] = useState("");
  const [committedSearch, setCommittedSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [sentimentSort, setSentimentSort] = useState<SentimentSortOrder>("none");
  const [role, setRole] = useState(""); const [origin, setOrigin] = useState(""); const [direction, setDirection] = useState(""); const [eligibility, setEligibility] = useState(""); const [labelState, setLabelState] = useState("");
  const customReady = settings.rangeMode === "custom" && settings.startDate && settings.endDate;
  const state = useNewsQuery({ asOf, content: settings.content, direction, eligibility, endDate: customReady ? settings.endDate : "", hours: settings.lookbackHours, kind: settings.kind, labelState, limit: settings.limit, live, origin, refreshKey, role, search: committedSearch, startDate: customReady ? settings.startDate : "", ticker: settings.ticker });
  const presentations = useTickerPresentations(state.rows.flatMap((row) => row.ticker_link_sample ?? []));
  const displayRows = useMemo(() => sortRowsBySentimentScore(state.rows, (row) => row.scoped_summary?.semantic_score, sentimentSort), [sentimentSort, state.rows]);

  return <section className="news-all" aria-label="All news">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); setCommittedSearch(search.trim()); }}>
      <label className="news-search"><Search size={13} /><input aria-label="Search all news" onChange={(event) => setSearch(event.target.value)} placeholder="Search headlines, text, author or source" value={search} /></label>
      <button className="button secondary compact news-search-submit" type="submit">Search</button>
      <label><span>Window</span><select aria-label="News time window" onChange={(event) => event.target.value === "custom" ? onSettingsChange({ rangeMode: "custom" }) : onSettingsChange({ lookbackHours: Number(event.target.value), rangeMode: "preset" })} value={settings.rangeMode === "custom" ? "custom" : settings.lookbackHours}><option value={1}>1 hour</option><option value={6}>6 hours</option><option value={24}>24 hours</option><option value={168}>7 days</option><option value={720}>30 days</option><option value={8760}>1 year</option><option value={43800}>5 years</option><option value="custom">Custom dates</option></select></label>
      {settings.rangeMode === "custom" ? <><label><span>From</span><input aria-label="News range start date" onChange={(event) => onSettingsChange({ startDate: event.target.value })} type="date" value={settings.startDate} /></label><label><span>Through</span><input aria-label="News range end date" onChange={(event) => onSettingsChange({ endDate: event.target.value })} type="date" value={settings.endDate} /></label></> : null}
      <label><span>Top</span><select aria-label="News result limit" onChange={(event) => onSettingsChange({ limit: Number(event.target.value) })} value={settings.limit}><option value={25}>25</option><option value={50}>50</option><option value={100}>100</option><option value={250}>250</option></select></label>
      <label><span>Ticker</span><input aria-label="Filter by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any" value={settings.ticker} /></label>
      <label><span>Role</span><select aria-label="News content role" onChange={(event) => setRole(event.target.value)} value={role}><option value="">All roles</option><option value="primary_event">Primary event</option><option value="analyst_event">Analyst event</option><option value="regulatory_event">Regulatory event</option><option value="editorial_analysis">Editorial analysis</option><option value="market_roundup">Market roundup</option><option value="mover_recap">Mover recap</option><option value="why_moving_followup">Why-moving follow-up</option><option value="automated_market_statistics">Automated summary</option></select></label>
      <label><span>Direction</span><select aria-label="Semantic direction" onChange={(event) => setDirection(event.target.value)} value={direction}><option value="">Any direction</option><option value="positive">Positive</option><option value="negative">Negative</option><option value="neutral">Neutral</option><option value="mixed">Mixed</option></select></label>
      <label><span>Origin</span><select aria-label="Source origin" onChange={(event) => setOrigin(event.target.value)} value={origin}><option value="">Any origin</option><option value="issuer">Issuer</option><option value="analyst">Analyst</option><option value="regulatory">Regulatory</option><option value="editorial">Editorial</option><option value="automated">Automated</option></select></label>
      <label><span>Use</span><select aria-label="News eligibility" onChange={(event) => setEligibility(event.target.value)} value={eligibility}><option value="">Any use</option><option value="forecast">Forecast</option><option value="reaction">Reaction study</option><option value="history">Issuer history</option></select></label>
      <label><span>Labels</span><select aria-label="Label state" onChange={(event) => setLabelState(event.target.value)} value={labelState}><option value="">Any state</option><option value="classified">Classified</option><option value="pending">Pending</option><option value="quality">Quality issue</option></select></label>
      <label><span>Source format</span><select aria-label="Legacy source format" onChange={(event) => onSettingsChange({ kind: event.target.value })} value={settings.kind}><option value="all">All formats</option><option value="company">Company feed</option><option value="regulatory">Regulatory feed</option><option value="analyst">Analyst feed</option><option value="editorial">Editorial feed</option><option value="multi">Multi-company</option><option value="ai">Automated</option></select></label>
      <label><span>Text</span><select aria-label="News text coverage" onChange={(event) => onSettingsChange({ content: event.target.value })} value={settings.content}><option value="all">All</option><option value="full">Full text</option><option value="title">Title only</option></select></label>
      <button aria-label="Refresh news" className="toolbar-button compact" onClick={() => setRefreshKey((value) => value + 1)} title="Refresh" type="button"><RefreshCw size={13} /></button>
    </form>
    <NewsStatus state={state} />
    <div className="news-table-wrap intelligence-feed-scroll">
      <div className="intelligence-feed news-intelligence-feed" role="list">
        <div className="intelligence-feed-header news-intelligence-grid" role="row"><span>Time</span><span>Ticker</span><span>Headline &amp; context</span><SentimentSortButton onChange={setSentimentSort} order={sentimentSort} /><span>Use &amp; text</span></div>
        {displayRows.map((row) => {
          const tone = newsTemperature(row.published_at_utc, Date.parse(state.asOf || asOf));
          const directionValue = normalizeSemanticDirection(row.scoped_summary?.semantic_direction);
          return <article className="intelligence-feed-row news-intelligence-grid" data-direction={directionValue} key={row.canonical_news_id} role="listitem">
            <div className="intelligence-time-block"><NewsTemperatureTag tone={tone} /><MarketTime className="news-row-time" dateStyle="short" includeDate value={row.published_at_utc} /></div>
            <div className="intelligence-identity-block"><TickerList presentations={presentations} tickers={row.ticker_link_sample} /></div>
            <div className="intelligence-main-block">
              <div className="intelligence-meta-line"><ScopedClass summary={row.scoped_summary} /><span className="news-origin">{readableLabel(row.scoped_summary?.source_origin || row.news_origin || "Unknown")}</span><ScopedConcepts concepts={row.scoped_summary?.event_concepts} /></div>
              <button className="news-headline-button" onClick={() => openNewsPage(row, state.queryId)} type="button"><strong>{row.title || "Untitled story"}</strong>{newsTeaser(row) ? <small>{newsTeaser(row)}</small> : null}</button>
              <div className="intelligence-support-line"><span>{row.url_domain || "News"}</span></div>
            </div>
            <div className="intelligence-sentiment-cell"><ScopedDirection summary={row.scoped_summary} /></div>
            <div className="intelligence-utility-cell"><EligibilityMarks summary={row.scoped_summary} /><NewsTextState row={row} /></div>
          </article>;
        })}
      </div>
      {!state.loading && !state.rows.length ? <NewsEmpty label="No news matches this query." /> : null}
    </div>
    {state.hasMore ? <button className="news-load-more" disabled={state.loadingMore} onClick={state.loadMore} type="button">{state.loadingMore ? "Loading…" : "Load older news"}</button> : null}
  </section>;
}

export function TickerNewsContainer({ asOf, live = false, onSymbolChange, settings, symbol }: { asOf: string; live?: boolean; onSymbolChange?: (symbol: string) => void; settings: { lookbackHours: number; showTeaser: boolean }; symbol: string }) {
  const state = useNewsQuery({ asOf, content: "all", direction: "", eligibility: "", endDate: "", hours: settings.lookbackHours, kind: "all", labelState: "", limit: 100, live, origin: "", refreshKey: 0, role: "", search: "", startDate: "", ticker: symbol });
  const presentations = useTickerPresentations([symbol]);
  const effectiveAsOf = state.asOf || asOf;
  const asOfMs = Date.parse(effectiveAsOf);
  const orderedRows = [...state.rows].sort(compareNewsRecency);
  const eventRows = orderedRows.filter((row) => row.scoped_summary?.forecast_trigger_eligible);
  const contextRows = orderedRows.filter((row) => !row.scoped_summary?.forecast_trigger_eligible && ["analyst_event", "editorial_analysis", "regulatory_event"].includes(row.scoped_summary?.content_role ?? ""));
  const followupRows = orderedRows.filter((row) => !eventRows.includes(row) && !contextRows.includes(row));
  return <section className="ticker-news" aria-label={`${symbol} news`}>
    <header><div><TickerIdentityWithChange asOf={effectiveAsOf} className="ticker-news-symbol" inputAriaLabel="Ticker news symbol" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} /><span>Recent coverage</span></div><small>{state.rows.length} stories · through <MarketTime value={effectiveAsOf} /></small></header>
    <NewsStatus state={state} compact />
    <div className="ticker-news-feed">
      <TickerNewsSection asOfMs={asOfMs} emptyLabel="No actionable events in this window." label="Actionable events" queryId={state.queryId} rows={eventRows} showTeaser={settings.showTeaser} />
      <TickerNewsSection asOfMs={asOfMs} emptyLabel="No analysis or issuer context." label="Analysis & issuer context" queryId={state.queryId} rows={contextRows} showTeaser={settings.showTeaser} />
      <TickerNewsSection asOfMs={asOfMs} emptyLabel="No follow-ups or market summaries." label="Follow-ups & market summaries" queryId={state.queryId} rows={followupRows} showTeaser={settings.showTeaser} />
      {!state.loading && !state.rows.length ? <NewsEmpty label={`No ${symbol} news in the last ${settings.lookbackHours} hours.`} /> : null}
    </div>
  </section>;
}

function TickerNewsSection({ asOfMs, emptyLabel, label, queryId, rows, showTeaser }: { asOfMs: number; emptyLabel: string; label: string; queryId: string; rows: NewsRow[]; showTeaser: boolean }) {
  return <section className="ticker-news-section" aria-label={label}>
    <header><strong>{label}</strong><span>{rows.length}</span></header>
    {rows.map((row) => <TickerNewsStory asOfMs={asOfMs} key={row.canonical_news_id} queryId={queryId} row={row} showTeaser={showTeaser} />)}
    {!rows.length ? <small className="ticker-news-section-empty">{emptyLabel}</small> : null}
  </section>;
}

function TickerNewsStory({ asOfMs, queryId, row, showTeaser }: { asOfMs: number; queryId: string; row: NewsRow; showTeaser: boolean }) {
  const tone = newsTemperature(row.published_at_utc, asOfMs);
  const TemperatureIcon = newsTemperaturePresentation(tone).Icon;
  const direction = normalizeSemanticDirection(row.scoped_summary?.semantic_direction);
  return <article data-direction={direction} data-tone={tone}>
    <div aria-label={`${tone} news`} className="ticker-news-marker" title={`${tone} news`}><TemperatureIcon size={14} /></div>
    <div className="ticker-event-time"><MarketTime dateStyle="short" includeDate value={row.published_at_utc} /><em data-tone={tone}>{tone}</em></div>
    <div className="ticker-event-content"><div className="ticker-news-meta"><ScopedDirection summary={row.scoped_summary} salient /><ScopedClass summary={row.scoped_summary} /><ScopedConcepts concepts={row.scoped_summary?.event_concepts} compact /></div><button className="ticker-news-open" onClick={() => openNewsPage(row, queryId)} type="button"><strong>{row.title}</strong>{showTeaser && newsTeaser(row) ? <p>{newsTeaser(row)}</p> : null}</button></div>
  </article>;
}

export function NewsDetailContainer({ asOf, canvasId, requestedNewsId }: { asOf: string; canvasId: string; requestedNewsId?: string }) {
  const [selection, setSelection] = useState<NewsSelection>(() => { const stored = readSelectedNews(canvasId); return requestedNewsId ? stored.newsId === requestedNewsId ? stored : { newsId: requestedNewsId, publishedAt: "", queryId: "" } : stored; });
  const newsId = selection.newsId;
  const [detail, setDetail] = useState<NewsDetailPayload | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const onSelection = (event: Event) => {
      const selected = (event as CustomEvent<NewsSelection & { canvasId: string }>).detail;
      if (selected.canvasId === canvasId) setSelection({ newsId: selected.newsId, publishedAt: selected.publishedAt, queryId: selected.queryId });
    };
    window.addEventListener(NEWS_SELECTION_EVENT, onSelection);
    const onStorage = (event: StorageEvent) => {
      if (event.key === selectionKey(canvasId) && event.newValue) setSelection(parseNewsSelection(event.newValue));
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
    api<NewsDetailPayload>(`/api/trading/news/detail/${encodeURIComponent(newsId)}${query({ published_at: selection.publishedAt || undefined, query_id: selection.queryId || undefined })}`, { signal: controller.signal, timeoutMs: 30000 })
      .then(setDetail).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [newsId, selection.publishedAt, selection.queryId]);
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
  const scopedLabels = row.scoped_labels ?? [];
  const scopedSummary = row.scoped_summary ?? null;
  return <article className="news-reader">
    <header className="news-reader-hero">
      <div className="news-reader-kicker"><NewsTemperatureTag tone={tone} /><MarketTime includeDate value={row.published_at_utc} /><NewsKind classification={{ ...classification, kind }} /><span>{row.url_domain || "News"}</span>{row.render_status === "unrendered" ? <span className="news-text-state" data-state="unrendered">Unrendered</span> : null}</div>
      <h1><MarketNumberText text={title} /></h1>
      <div className="news-reader-byline"><span>{row.author || "Unknown author"}</span>{detailTickers.length === 1 ? <TickerIdentityWithChange asOf={asOf} logoUrl={presentations[detailTickers[0]]?.logo_url} ticker={detailTickers[0]} /> : <TickerList presentations={presentations} tickers={detailTickers} />}</div>
      {scopedSummary
        ? <NewsDetailOverview summary={scopedSummary} />
        : <div className="news-label-pending">{row.intelligence_status === "unavailable" ? "Deterministic labels temporarily unavailable" : "Deterministic classification pending"}</div>}
      <NewsClassificationPanel classification={classification} summary={scopedSummary} />
      {tags.length ? <div className="news-reader-tags" aria-label="Source tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
    </header>
    {scopedLabels.length ? <section className="news-reader-intelligence" aria-label="Issuer-specific interpretation"><header><div><strong>Issuer interpretations</strong><small>Direction and evidence are evaluated for each affected issuer.</small></div><span>{scopedLabels.length} {scopedLabels.length === 1 ? "issuer view" : "issuer views"}</span></header>{scopedLabels.map((label) => <ScopedLabelPanel key={`${label.unit_id}-${label.ticker}`} label={label} presentations={presentations} />)}</section> : null}
    {body ? <div className="news-reader-body">{articleParagraphs(body).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`}><MarketNumberText text={paragraph} /></p>)}</div> : <NewsEmpty label="This record contains title metadata but no readable article text." />}
    <footer>{row.article_url ? <a href={row.article_url} rel="noreferrer" target="_blank">Open original source <ExternalLink size={12} /></a> : null}</footer>
  </article>;
}

function useNewsQuery({ asOf, content, direction, eligibility, endDate, hours, kind, labelState, limit, live, origin, refreshKey, role, search, startDate, ticker }: { asOf: string; content: string; direction: string; eligibility: string; endDate: string; hours: number; kind: string; labelState: string; limit: number; live: boolean; origin: string; refreshKey: number; role: string; search: string; startDate: string; ticker: string }) {
  const [rows, setRows] = useState<NewsRow[]>([]); const [payload, setPayload] = useState<NewsPayload | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false);
  const queryIdRef = useRef("");
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveError, setLiveError] = useState("");
  const latestRevision = useRef<number | null>(null);
  const load = useCallback(async (before = "", beforeId = "", signal?: AbortSignal, pageAsOf = "") => {
    const queryAsOf = pageAsOf || (live ? new Date().toISOString() : asOf);
    const next = await api<NewsPayload>(`/api/trading/news${query({ as_of: queryAsOf, before: before || undefined, before_id: beforeId || undefined, content, direction: direction || undefined, eligibility: eligibility || undefined, end_date: endDate || undefined, kind: kind === "all" ? undefined : kind, label_state: labelState || undefined, limit, lookback_hours: hours, origin: origin || undefined, query_id: before ? queryIdRef.current : undefined, role: role || undefined, search: search || undefined, start_date: startDate || undefined, ticker: ticker || undefined })}`, { signal, timeoutMs: 30000 });
    if (signal?.aborted) return;
    queryIdRef.current = next.query_id;
    setError("");
    setPayload(next); setRows((current) => before ? [...current, ...next.rows.filter((row) => !current.some((item) => item.canonical_news_id === row.canonical_news_id))] : next.rows);
  }, [asOf, content, direction, eligibility, endDate, hours, kind, labelState, limit, live, origin, role, search, startDate, ticker]);
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
  return { asOf: payload?.as_of, error, hasMore: Boolean(payload?.has_more), live, liveConnected, liveError, loadMore, loading, loadingMore, queryId: payload?.query_id ?? "", rows, windowStart: payload?.window_start };
}

// Product UI reports user-relevant freshness only. Never render database/table
// names, storage paths, raw service errors, implementation notes, or agent/chat text.
function NewsStatus({ compact, state }: { compact?: boolean; state: ReturnType<typeof useNewsQuery> }) { return <div className="news-status" data-compact={compact ? "true" : "false"}>{state.loading ? <span>Querying news…</span> : state.error ? <strong>{state.error}</strong> : <><span>{state.rows.length} returned</span>{!compact && state.windowStart ? <span className="news-window-start"><span>Since</span><MarketTime dateStyle="short" includeDate layout="inline" value={state.windowStart} /></span> : null}<span className="news-source-label">{state.live ? state.liveConnected ? "Live updates" : "Reconnecting…" : "Point-in-time"}</span></>}</div>; }
function NewsEmpty({ label }: { label: string }) { return <div className="news-empty"><Newspaper size={18} /><span>{label}</span></div>; }
function TickerList({ presentations, tickers = [] }: { presentations: Record<string, TickerPresentation>; tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <b key={ticker}><TickerIdentity logoUrl={presentations[ticker]?.logo_url} ticker={ticker} /></b>)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function NewsTextState({ row }: { row: NewsRow }) { const state = row.render_status === "unrendered" ? "unrendered" : row.is_title_only ? "title" : "full"; return <span className="news-text-state" data-state={state}>{state === "unrendered" ? "Unrendered" : row.is_title_only ? "Title" : row.has_pdf ? "PDF" : row.has_external_text ? "Full" : "Body"}</span>; }
function NewsKind({ classification }: { classification: NewsClassification }) { const values = { ai: { Icon: Bot, label: "AI" }, analyst: { Icon: TrendingUp, label: "Analyst" }, company: { Icon: Building2, label: classification.format === "earnings_flash" ? "Company earnings" : "Company" }, editorial: { Icon: Newspaper, label: "Editorial" }, insights: { Icon: Lightbulb, label: "Insights" }, market: { Icon: Globe2, label: classification.format === "trading_halt" ? "Trading halt" : "Market" }, multi: { Icon: Layers3, label: "Multi-company" }, regulatory: { Icon: FileCheck2, label: "Regulatory" }, why_moving: { Icon: Megaphone, label: "Why moving" } }; const value = values[classification.kind]; return <span className="news-kind" data-kind={classification.kind} title={`${Math.round(classification.confidence * 100)}% classification confidence`}><value.Icon size={11} />{value.label}</span>; }
function ScopedDirection({ prominent = false, salient = false, summary }: { prominent?: boolean; salient?: boolean; summary?: ScopedNewsSummary | null }) { return <SemanticDirectionMetric direction={summary?.semantic_direction} prominent={prominent || salient} score={summary?.semantic_score} />; }
function EligibilityMarks({ summary }: { summary?: ScopedNewsSummary | null }) { if (!summary) return <span className="news-eligibility" data-state="pending">Pending</span>; return <span className="news-eligibility"><b data-active={summary.forecast_trigger_eligible} title="Forecast trigger">F</b><b data-active={summary.reaction_evaluation_eligible} title="Reaction study">R</b><b data-active={summary.issuer_history_context_eligible} title="Issuer history">H</b></span>; }
function ScopedClass({ summary }: { summary?: ScopedNewsSummary | null }) { if (!summary) return <span className="news-scoped-class" data-state="pending">Unclassified</span>; return <span className="news-scoped-class" data-state={summary.forecast_trigger_eligible ? "event" : "context"} title={summary.forecast_trigger_eligible ? "Eligible primary event evidence" : "Supporting context or follow-up"}>{summary.forecast_trigger_eligible ? <CircleDot size={10} /> : <History size={10} />}{readableLabel(summary.content_role || summary.source_origin || "context")}</span>; }
function ScopedConcepts({ compact = false, concepts = [] }: { compact?: boolean; concepts?: string[] }) { const readable = concepts.map(shortConcept).filter(Boolean); const visible = readable.slice(0, compact ? 1 : 3); if (!visible.length) return null; return <span className="news-scoped-concepts">{visible.map((concept) => <span key={concept}>{concept}</span>)}{readable.length > visible.length ? <span>+{readable.length - visible.length}</span> : null}</span>; }
function NewsDetailOverview({ summary }: { summary: ScopedNewsSummary }) {
  return <section className="detail-intelligence-overview" aria-label="News interpretation summary">
    <div className="detail-direction-focus"><span>Text direction</span><ScopedDirection prominent summary={summary} /></div>
    <DetailDatum label="Content role" value={readableLabel(summary.content_role || "unclassified")} />
    <DetailDatum label="Source origin" value={readableLabel(summary.source_origin || "unknown")} />
    <DetailDatum label="Primary event" value={shortConcept(summary.event_concepts[0] || "Not identified")} />
    <DetailDatum label="Operational use" value={eligibilityText(summary)} />
  </section>;
}
function DetailDatum({ label, value }: { label: string; value: string }) { return <span className="detail-intelligence-datum"><small>{label}</small><strong>{value}</strong></span>; }
function NewsClassificationPanel({ classification, summary }: { classification: NewsClassification; summary: ScopedNewsSummary | null }) {
  return <details className="news-detail-contract" open>
    <summary><span><strong>Loaded classification</strong><small>Article rules and issuer-label summary</small></span><em>{classification.version}</em></summary>
    <div className="news-detail-contract-body">
      <section aria-label="Article classification fields">
        <header>Article class</header>
        <div className="news-detail-contract-grid"><DetailDatum label="Kind" value={readableLabel(classification.kind)} /><DetailDatum label="Format" value={readableLabel(classification.format)} /><DetailDatum label="Origin" value={readableLabel(classification.origin)} /><DetailDatum label="Scope" value={readableLabel(classification.scope)} /><DetailDatum label="Company news" value={yesNo(classification.is_company_news)} /><DetailDatum label="Confidence" value={percentConfidence(classification.confidence)} /><DetailDatum label="Rule version" value={classification.version || "Not reported"} /></div>
        <DetailList label="Topics" values={classification.topics} />
        <DetailList label="Classification evidence" values={classification.evidence} />
      </section>
      {summary ? <section aria-label="Issuer label summary fields">
        <header>Issuer-label summary</header>
        <div className="news-detail-contract-grid"><DetailDatum label="Classified" value={yesNo(summary.classified !== false)} /><DetailDatum label="Labels" value={String(summary.label_count)} /><DetailDatum label="Issuers" value={String(summary.issuer_count)} /><DetailDatum label="Label version" value={summary.labeling_version || "Not reported"} /><DetailDatum label="Forecast trigger" value={eligibilityValue(summary.forecast_trigger_eligible)} /><DetailDatum label="Reaction study" value={eligibilityValue(summary.reaction_evaluation_eligible)} /><DetailDatum label="Issuer history" value={eligibilityValue(summary.issuer_history_context_eligible)} /></div>
        <DetailList label="Event concepts" values={summary.event_concepts.map(shortConcept)} />
        <DetailList label="Quality flags" values={(summary.quality_flags ?? []).map(readableStructuredLabel)} emptyLabel="None" />
      </section> : null}
    </div>
  </details>;
}
function ScopedLabelPanel({ label, presentations }: { label: ScopedNewsLabel; presentations: Record<string, TickerPresentation> }) {
  const summary: ScopedNewsSummary = { content_role: label.content_role, event_concepts: label.event_concepts, forecast_trigger_eligible: label.forecast_trigger_eligible, issuer_count: label.ticker ? 1 : 0, issuer_history_context_eligible: label.issuer_history_context_eligible, label_count: 1, labeling_version: label.labeling_version, reaction_evaluation_eligible: label.reaction_evaluation_eligible, semantic_direction: label.semantic_direction, semantic_score: label.semantic_score, source_origin: label.source_origin };
  const confidence = Number.isFinite(label.confidence) ? `${Math.round(label.confidence * 100)}%` : "Not reported";
  return <article className="news-scoped-label">
    <header><div className="news-scoped-label-identity">{label.ticker ? <TickerIdentity logoUrl={presentations[label.ticker]?.logo_url} ticker={label.ticker} /> : <strong>Document-wide</strong>}<ScopedClass summary={summary} /></div><ScopedDirection prominent summary={summary} /></header>
    <div className="news-scoped-label-facts"><DetailDatum label="Content role" value={readableLabel(label.content_role || "not specified")} /><DetailDatum label="Unit role" value={readableLabel(label.unit_role || "not specified")} /><DetailDatum label="Issuer role" value={readableLabel(label.issuer_role || "not specified")} /><DetailDatum label="Issuer relationship" value={readableLabel(label.issuer_relationship || "not specified")} /><DetailDatum label="Evidence scope" value={readableLabel(label.evidence_scope || "document")} /><DetailDatum label="Origin" value={readableLabel(label.source_origin || "unknown")} /><DetailDatum label="Source type" value={readableLabel(label.source_type || "not specified")} /><DetailDatum label="Source subtype" value={readableLabel(label.source_subtype || "not specified")} /><DetailDatum label="Timing" value={readableLabel(label.time_orientation || "not specified")} /><DetailDatum label="Modality" value={readableLabel(label.modality || "not specified")} /><DetailDatum label="Scope" value={readableLabel(label.scope || "not specified")} /><DetailDatum label="Confidence" value={confidence} /></div>
    <DetailList label="Event concepts" values={label.event_concepts.map(shortConcept)} emptyLabel="None" />
    <DetailList label="Direction basis" values={label.semantic_direction_basis.map(readableStructuredLabel)} emptyLabel="Text evidence" />
    <DetailList label="Event tickers" values={label.event_tickers} emptyLabel="None" />
    <div className="news-label-identifiers"><DetailDatum label="Unit ID" value={label.unit_id || "Not reported"} /><DetailDatum label="Event ID" value={label.event_id || "Not reported"} /><DetailDatum label="Label version" value={label.labeling_version || "Not reported"} /></div>
    <details><summary>Read direction evidence</summary><p>{label.semantic_evidence_text ? <MarketNumberText text={label.semantic_evidence_text} /> : "Not reported"}</p></details>
    <DetailList label="Quality flags" values={label.quality_flags.map(readableStructuredLabel)} emptyLabel="None" />
    <footer><EligibilityState active={label.forecast_trigger_eligible} label="Forecast trigger" /><EligibilityState active={label.reaction_evaluation_eligible} label="Reaction study" /><EligibilityState active={label.issuer_history_context_eligible} label="Issuer history" /><EligibilityState active={label.prior_primary_context_eligible} label="Prior primary context" /><EligibilityState active={label.episode_followup_eligible} label="Episode follow-up" /></footer>
  </article>;
}
function DetailList({ emptyLabel = "Not reported", label, values }: { emptyLabel?: string; label: string; values: string[] }) { const visible = values.filter(Boolean); return <div className="detail-list-row"><small>{label}</small><span>{visible.length ? visible.map((value) => <b key={value}>{value}</b>) : <em>{emptyLabel}</em>}</span></div>; }
function EligibilityState({ active, label }: { active: boolean; label: string }) { return <span data-active={active} title={`${label}: ${active ? "eligible" : "not eligible"}`}>{label}: {active ? "Eligible" : "Not eligible"}</span>; }
function eligibilityText(summary: Pick<ScopedNewsSummary, "forecast_trigger_eligible" | "issuer_history_context_eligible" | "reaction_evaluation_eligible">): string { const uses = [summary.forecast_trigger_eligible ? "Forecast" : "", summary.reaction_evaluation_eligible ? "Reaction study" : "", summary.issuer_history_context_eligible ? "Issuer history" : ""].filter(Boolean); return uses.length ? uses.join(" · ") : "Context only"; }
function MarketNumberText({ text }: { text: string }) { const matches = Array.from(text.matchAll(MARKET_NUMBER_PATTERN)); if (!matches.length) return text; const parts: Array<string | ReactElement> = []; let cursor = 0; matches.forEach((match, index) => { const start = match.index; if (start > cursor) parts.push(text.slice(cursor, start)); const value = match[0]; const kind = /%|percent|basis|bps/i.test(value) ? "rate" : "price"; parts.push(<span className="market-number" data-market-number={kind} key={`${start}-${index}`}>{value}</span>); cursor = start + value.length; }); if (cursor < text.length) parts.push(text.slice(cursor)); return <>{parts}</>; }
function NewsTemperatureTag({ tone }: { tone: NewsTemperature }) { const value = newsTemperaturePresentation(tone); return <span className="news-temperature" data-tone={tone}><value.Icon size={12} /><em>{value.label}</em></span>; }
function newsTemperature(publishedAt: string, asOfMs: number): NewsTemperature { const publishedMs = Date.parse(publishedAt); const ageMinutes = Number.isFinite(publishedMs) && Number.isFinite(asOfMs) ? Math.max(0, (asOfMs - publishedMs) / 60_000) : Number.POSITIVE_INFINITY; return ageMinutes <= NEWS_HOT_MINUTES ? "hot" : ageMinutes <= NEWS_COLD_MINUTES ? "cold" : "old"; }
function newsTemperaturePresentation(tone: NewsTemperature) { return tone === "hot" ? { Icon: Flame, label: "Hot" } : tone === "cold" ? { Icon: Snowflake, label: "Cold" } : { Icon: Clock3, label: "Old" }; }
function isNewsKind(value: unknown): value is NewsKindValue { return ["ai", "analyst", "company", "editorial", "insights", "market", "multi", "regulatory", "why_moving"].includes(String(value)); }
function classificationFromRow(row: NewsRow): NewsClassification { if (row.classification) return row.classification; const kind = isNewsKind(row.news_kind) ? row.news_kind : "market"; return { confidence: row.classification_confidence ?? 0.65, evidence: row.classification_evidence ?? [], format: row.news_format ?? "general", is_company_news: row.is_company_news ?? (kind === "company" || kind === "regulatory"), kind, origin: row.news_origin ?? "unknown", scope: row.news_scope ?? ((row.ticker_link_sample?.length ?? 0) === 1 ? "single_ticker" : (row.ticker_link_sample?.length ?? 0) > 1 ? "multi_ticker" : "market_wide"), topics: row.news_topics ?? [], version: "news_rules_v1" }; }
function readableLabel(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function readableStructuredLabel(value: string) { return readableLabel(value.replace(/[.:]+/g, " ")); }
function percentConfidence(value: number) { return Number.isFinite(value) ? `${Math.round(value * 100)}%` : "Not reported"; }
function yesNo(value: boolean) { return value ? "Yes" : "No"; }
function eligibilityValue(value: boolean) { return value ? "Eligible" : "Not eligible"; }
function shortConcept(value: string) { const leaf = value.split(".").at(-1) ?? value; return readableLabel(leaf); }
const MARKET_NUMBER_PATTERN = /(?:[+\-−]\s*)?(?:[$€£¥]\s*\d[\d,]*(?:\.\d+)?(?:\s+(?:thousand|million|billion|trillion)\b|[KMBT]\b)?|(?:USD|CAD|EUR|GBP|JPY|CNY|HKD|AUD)\s*\$?\s*\d[\d,]*(?:\.\d+)?(?:\s+(?:thousand|million|billion|trillion)\b|[KMBT]\b)?|\d[\d,]*(?:\.\d+)?\s*(?:USD|CAD|EUR|GBP|JPY|CNY|HKD|AUD)\b|\d[\d,]*(?:\.\d+)?\s*(?:%|percent(?:age points?)?|basis points?|bps)\b)/gi;
function articleParagraphs(value: string) { const explicit = value.split(/\n{2,}/).map((item) => item.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.split(/(?<=[.!?])\s+(?=["“‘']?[A-Z0-9])/).map((item) => item.trim()).filter(Boolean); const paragraphs: string[] = []; for (let index = 0; index < sentences.length; index += 4) paragraphs.push(sentences.slice(index, index + 4).join(" ")); return paragraphs.length ? paragraphs : [value]; }
function stringList(value: unknown): string[] { return Array.isArray(value) ? value.map(String).filter(Boolean) : []; }
function compareNewsRecency(left: NewsRow, right: NewsRow) { return Date.parse(right.published_at_utc) - Date.parse(left.published_at_utc); }
function newsTeaser(row: NewsRow) { const value = (row.text_preview ?? "").replace(/\s+/g, " ").trim(); if (!value) return ""; const titlePrefix = `Title: ${row.title}`; const withoutTitle = value.toLowerCase().startsWith(titlePrefix.toLowerCase()) ? value.slice(titlePrefix.length).trim() : value; return withoutTitle.replace(/^Teaser:\s*/i, "").replace(/^Body:\s*/i, "").trim(); }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.news-selection.${canvasId}`; }
function parseNewsSelection(value: string): NewsSelection { try { const parsed = JSON.parse(value) as Partial<NewsSelection>; if (parsed.newsId) return { newsId: parsed.newsId, publishedAt: parsed.publishedAt ?? "", queryId: parsed.queryId ?? "" }; } catch { if (value) return { newsId: value, publishedAt: "", queryId: "" }; } return { newsId: "", publishedAt: "", queryId: "" }; }
function readSelectedNews(canvasId: string) { return parseNewsSelection(window.localStorage.getItem(selectionKey(canvasId)) || ""); }
function selectNews(canvasId: string, selection: NewsSelection) { window.localStorage.setItem(selectionKey(canvasId), JSON.stringify(selection)); window.dispatchEvent(new CustomEvent(NEWS_SELECTION_EVENT, { detail: { canvasId, ...selection } })); }
function prepareNewsReader(selection: NewsSelection) { ensureNewsReaderCanvas(); selectNews(NEWS_READER_CANVAS_ID, selection); }
function newsPageUrl(selection: NewsSelection) { const url = new URL(focusCanvasUrl(NEWS_READER_CANVAS_ID, "news_detail")); url.searchParams.set("news", selection.newsId); return url.toString(); }
function openNewsPage(row: NewsRow, queryId: string) { const selection = { newsId: row.canonical_news_id, publishedAt: row.published_at_utc, queryId }; prepareNewsReader(selection); window.open(newsPageUrl(selection), "quant-news-reader"); }
