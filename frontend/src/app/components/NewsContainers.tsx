import { Bot, Building2, Check, ChevronLeft, ChevronRight, CircleDot, Clock3, ExternalLink, FileCheck2, Flame, Globe2, History, Layers3, Lightbulb, Megaphone, Newspaper, RefreshCw, Search, Snowflake, TrendingUp } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState, type ReactElement } from "react";

import { api, query } from "../../api/client";
import { NEWS_READER_CANVAS_ID, ensureNewsReaderCanvas, focusCanvasUrl } from "../canvasWorkspace";
import { timeRecency, type TimeRecency } from "../timeRecency";
import { FilterOverflowMenu } from "./FilterOverflowMenu";
import { InventoryFilterSelect, inventoryEligibilityOptions, type InventoryFilterOption } from "./InventoryFilterSelect";
import { MarketTime } from "./MarketTime";
import { normalizeSemanticDirection, SemanticDirectionMetric, SentimentSortButton, sortRowsBySentimentScore, type SentimentSortOrder } from "./SemanticDirectionMetric";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations, type TickerPresentation } from "./TickerIdentity";
import { SecurityIdentityCell } from "./TablePresentation";
import { useWallClock } from "./useWallClock";

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
  intelligence_status?: "ready" | "pending" | "unavailable";
  text_preview?: string;
  ticker_link_sample?: string[];
  title: string;
  url_domain?: string;
  news_synthesis_summary?: NewsSynthesisSummary | null;
  news_synthesis?: NewsSynthesisDocument | null;
  ai_state?: NewsAiState | null;
};

type NewsPayload = {
  as_of: string;
  has_more: boolean;
  market_timezone: string;
  next_before: string;
  next_before_id: string;
  query_id: string;
  rows: NewsRow[];
  ticker_options?: string[];
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
    news_synthesis_summary?: NewsSynthesisSummary | null;
    news_synthesis?: NewsSynthesisDocument | null;
    ai_state?: NewsAiState | null;
    render_status?: "rendered" | "title_only" | "unrendered";
    intelligence_status?: "ready" | "pending" | "unavailable";
  };
  tickers: string[];
};

const NEWS_SELECTION_EVENT = "quant-news-selection";
type NewsKindValue = "ai" | "analyst" | "company" | "editorial" | "insights" | "market" | "multi" | "regulatory" | "why_moving";
type NewsOrigin = "analyst" | "automated" | "editorial" | "issuer" | "regulatory" | "third_party" | "unknown";
type NewsScope = "market_wide" | "multi_ticker" | "single_ticker";
type NewsFormat = "ai_generated" | "analyst_action" | "company_announcement" | "earnings_flash" | "editorial_coverage" | "general" | "insights" | "macro_release" | "multi_company_coverage" | "regulatory_filing" | "trading_halt" | "why_moving";
type NewsClassification = { confidence: number; evidence: string[]; format: NewsFormat; is_company_news: boolean; kind: NewsKindValue; origin: NewsOrigin; scope: NewsScope; topics: string[]; version: string };
type NewsSynthesisSummary = {
  communication_purpose: string;
  information_origin: string;
  concepts: string[];
  composite_sentiment: string;
  positive_strength: number;
  negative_strength: number;
  forecast_trigger_eligible: boolean;
  reaction_evaluation_eligible: boolean;
  issuer_history_context_eligible: boolean;
  analyst_evaluation_eligible: boolean;
  issuer_count: number;
  engine_version: string;
  quality_flags: string[];
};
type IssuerAiLabel = { issuer_name: string; ticker: string | null; forecast_relevance_probability: number; positive_implication_probability: number; negative_implication_probability: number; event_tags: string[]; issuer_roles: string[]; time_scope: string; claim_source: string };
type NewsAiState = {
  funnel?: { stage: string; forecast_eligibility: string; eligible_probability: number; threshold: number; release_id: string; updated_at_utc: string } | null;
  review?: { status: string; trigger_mode?: string; requested_by?: string; labels?: { issuers: IssuerAiLabel[] } | null; model?: string; cost_usd?: number; latency_ms?: number; error?: string; updated_at_utc?: string };
  hypotheses?: Array<{ ticker: string; context_as_of_utc: string; prediction: { predictions: Record<string, { upside_probability: number; downside_probability: number; no_action_probability: number; expected_return_pct: number; confidence: number; abstain: boolean }>; regime_compatibility: string; uncertainty: string } }>;
};
type NewsTemperature = TimeRecency;
type AllNewsSettings = { content: string; endDate: string; kind: string; limit: number; lookbackHours: number; rangeMode: "custom" | "preset"; startDate: string; ticker: string };
type EligibilityQuery = { analyst: string; forecast: string; reaction: string; history: string };
const EMPTY_ELIGIBILITY_QUERY: EligibilityQuery = { analyst: "", forecast: "", reaction: "", history: "" };
const NEWS_DIRECTION_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any sentiment" }, { value: "positive", label: "Positive" }, { value: "negative", label: "Negative" }, { value: "neutral", label: "Neutral" }, { value: "mixed", label: "Mixed" }];
const NEWS_WINDOW_OPTIONS: InventoryFilterOption[] = [{ value: "1", label: "1 hour" }, { value: "6", label: "6 hours" }, { value: "24", label: "24 hours" }, { value: "168", label: "7 days" }, { value: "720", label: "30 days" }, { value: "8760", label: "1 year" }, { value: "43800", label: "5 years" }, { value: "custom", label: "Custom dates" }];
const NEWS_ROLE_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any purpose" }, { value: "report", label: "Report" }, { value: "analyze", label: "Analysis" }, { value: "preview", label: "Preview" }, { value: "recap", label: "Recap" }, { value: "explain_move", label: "Move explanation" }];
const NEWS_ORIGIN_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any origin" }, { value: "issuer", label: "Issuer" }, { value: "regulator", label: "Regulator" }, { value: "analyst", label: "Analyst" }, { value: "editorial", label: "Editorial" }, { value: "mixed", label: "Mixed" }, { value: "unknown", label: "Unknown" }];
const NEWS_LIMIT_OPTIONS: InventoryFilterOption[] = [25, 50, 100, 250].map((value) => ({ value: String(value), label: `Top ${value}` }));
const NEWS_LABEL_STATE_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any state" }, { value: "classified", label: "Classified" }, { value: "pending", label: "Pending" }, { value: "quality", label: "Quality issue" }];
const NEWS_ARTICLE_CLASS_LABELS: Record<NewsKindValue, string> = {
  ai: "Automated article",
  analyst: "Analyst article",
  company: "Company article",
  editorial: "Editorial article",
  insights: "Insights article",
  market: "Market event",
  multi: "Multi-company article",
  regulatory: "Regulatory article",
  why_moving: "Why-moving article",
};
type NewsSynthesisEvidence = { source_field: string; start: number; end: number; quote: string };
type NewsSynthesisDocument = {
  contract_version: string;
  concept_registry_version: string;
  envelope: Record<string, { value: string; rule_id: string; evidence: NewsSynthesisEvidence[] }>;
  entities: Array<{ entity_id: string; entity_kind: string; display_name: string; ticker: string; identity_status: string; identity_evidence: string[] }>;
  statements: Array<{ statement_id: string; statement_kind: string; concept_leaf: string; epistemic_status: string; time_relation: string; evidence_spans: NewsSynthesisEvidence[]; typed_facts: Array<Record<string, unknown>> }>;
  participations: Array<{ statement_id: string; entity_id: string; semantic_role: string; discourse_role: string; semantic_sentiment: string; sentiment_strength: number }>;
  issuer_views: Array<{ entity_id: string; composite_sentiment: string; positive_strength: number; negative_strength: number; statement_ids: string[] }>;
  eligibility: Array<{ entity_id: string; product: string; eligible: boolean; reasons: string[]; blocking_flags: string[] }>;
  quality_flags: string[];
};
export const NEWS_ARTICLE_CLASS_OPTIONS: InventoryFilterOption[] = [
  { value: "all", label: "All article classes" },
  ...(["analyst", "company", "editorial", "market", "multi", "regulatory", "why_moving"] as NewsKindValue[]).map((value) => ({ value, label: NEWS_ARTICLE_CLASS_LABELS[value] })),
];
const NEWS_TEXT_OPTIONS: InventoryFilterOption[] = [{ value: "all", label: "All text" }, { value: "full", label: "Full text" }, { value: "title", label: "Title only" }];
type NewsSelection = { newsId: string; publishedAt: string; queryId: string };

// Product-wide contract: hot is neon red (<= 4h), cold is neon blue (<= 24h),
// and old is neutral gray. Never substitute success/danger/info semantic colors.

export function AllNewsContainer({ asOf, live = false, onSettingsChange, settings }: { asOf: string; live?: boolean; onSettingsChange: (patch: Partial<AllNewsSettings>) => void; settings: AllNewsSettings }) {
  const [search, setSearch] = useState("");
  const [committedSearch, setCommittedSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const [sentimentSort, setSentimentSort] = useState<SentimentSortOrder>("none");
  const [role, setRole] = useState(""); const [origin, setOrigin] = useState(""); const [direction, setDirection] = useState(""); const [eligibilityFilters, setEligibilityFilters] = useState<EligibilityQuery>(EMPTY_ELIGIBILITY_QUERY); const [labelState, setLabelState] = useState("");
  const wallClockMs = useWallClock();
  const customReady = settings.rangeMode === "custom" && settings.startDate && settings.endDate;
  const state = useNewsQuery({ asOf, content: settings.content, direction, eligibilityFilters, endDate: customReady ? settings.endDate : "", hours: settings.lookbackHours, kind: settings.kind, labelState, limit: settings.limit, live, origin, refreshKey, role, search: committedSearch, startDate: customReady ? settings.startDate : "", ticker: settings.ticker });
  const presentations = useTickerPresentations(state.rows.flatMap((row) => row.ticker_link_sample ?? []), { includeMarketState: live, includeRecency: live });
  const displayRows = useMemo(
    () => sentimentSort === "none"
      ? [...state.rows].sort(compareNewsRecency)
      : sortRowsBySentimentScore(state.rows, (row) => synthesisScore(row.news_synthesis_summary), sentimentSort),
    [sentimentSort, state.rows],
  );
  const tickerOptions = useMemo<InventoryFilterOption[]>(() => {
    const values = new Set(state.tickerOptions);
    if (settings.ticker) values.add(settings.ticker);
    return [{ value: "", label: "Any ticker" }, ...[...values].sort().map((value) => ({ value, label: value }))];
  }, [settings.ticker, state.tickerOptions]);
  const commitSearch = (value: string) => {
    setCommittedSearch(value.trim());
    if (settings.ticker) onSettingsChange({ ticker: "" });
  };
  const hasRefinements = Boolean(direction || role || origin || settings.ticker || eligibilityFilters.analyst || eligibilityFilters.forecast || eligibilityFilters.reaction || eligibilityFilters.history || labelState || settings.kind !== "all" || settings.content !== "all");
  const activeFilterCount = [direction, settings.rangeMode === "custom" || settings.lookbackHours !== 168, role, origin, settings.limit !== 100, settings.ticker, eligibilityFilters.analyst, eligibilityFilters.forecast, eligibilityFilters.reaction, eligibilityFilters.history, labelState, settings.kind !== "all", settings.content !== "all"].filter(Boolean).length;
  const clearRefinements = () => {
    setDirection("");
    setRole("");
    setOrigin("");
    setEligibilityFilters(EMPTY_ELIGIBILITY_QUERY);
    setLabelState("");
    onSettingsChange({ content: "all", kind: "all", ticker: "" });
  };

  return <section className="news-all" aria-label="All news">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); commitSearch(search); }}>
      <div className="news-query-primary">
        <label className="news-search"><Search size={13} /><input aria-label="Search all news" onChange={(event) => { const next = event.target.value; setSearch(next); if (!next.trim() && committedSearch) commitSearch(""); }} onKeyDown={(event) => { if (event.key === "Enter") { event.preventDefault(); commitSearch(event.currentTarget.value); } }} placeholder="Search source ID, ticker, headline…" value={search} /></label>
        <button className="button secondary compact news-search-submit" type="submit">Search</button>
        <span className="news-query-visible-core"><InventoryFilterSelect ariaLabel="Semantic sentiment" onChange={setDirection} options={NEWS_DIRECTION_OPTIONS} value={direction} /></span>
        <span className="news-query-visible-core"><InventoryFilterSelect ariaLabel="News time window" defaultValue={168} onChange={(value) => value === "custom" ? onSettingsChange({ rangeMode: "custom" }) : onSettingsChange({ lookbackHours: Number(value), rangeMode: "preset" })} options={NEWS_WINDOW_OPTIONS} value={settings.rangeMode === "custom" ? "custom" : settings.lookbackHours} /></span>
        <span className="news-query-visible-wide"><InventoryFilterSelect ariaLabel="Communication purpose" onChange={setRole} options={NEWS_ROLE_OPTIONS} value={role} /></span>
        <span className="news-query-visible-wide"><InventoryFilterSelect ariaLabel="Source origin" onChange={setOrigin} options={NEWS_ORIGIN_OPTIONS} value={origin} /></span>
        {settings.rangeMode === "custom" ? <span className="news-query-date-controls news-query-visible-wide"><NewsDateRangeFilters onSettingsChange={onSettingsChange} settings={settings} /></span> : null}
        <InventoryFilterSelect ariaLabel="Ticker" onChange={(value) => onSettingsChange({ ticker: value })} options={tickerOptions} searchable searchPlaceholder="Search tickers…" value={settings.ticker} />
        <FilterOverflowMenu activeCount={activeFilterCount}>
          <div className="filter-overflow-section"><strong>Query filters</strong><div className="filter-overflow-grid">
            <InventoryFilterSelect ariaLabel="Semantic sentiment" onChange={setDirection} options={NEWS_DIRECTION_OPTIONS} value={direction} />
            <InventoryFilterSelect ariaLabel="News time window" defaultValue={168} onChange={(value) => value === "custom" ? onSettingsChange({ rangeMode: "custom" }) : onSettingsChange({ lookbackHours: Number(value), rangeMode: "preset" })} options={NEWS_WINDOW_OPTIONS} value={settings.rangeMode === "custom" ? "custom" : settings.lookbackHours} />
            <InventoryFilterSelect ariaLabel="Communication purpose" onChange={setRole} options={NEWS_ROLE_OPTIONS} value={role} />
            <InventoryFilterSelect ariaLabel="Source origin" onChange={setOrigin} options={NEWS_ORIGIN_OPTIONS} value={origin} />
            <InventoryFilterSelect ariaLabel="News result limit" defaultValue={100} onChange={(value) => onSettingsChange({ limit: Number(value) })} options={NEWS_LIMIT_OPTIONS} value={settings.limit} />
            <InventoryFilterSelect ariaLabel="Ticker" onChange={(value) => onSettingsChange({ ticker: value })} options={tickerOptions} searchable value={settings.ticker} />
          </div>{settings.rangeMode === "custom" ? <div className="filter-overflow-dates"><NewsDateRangeFilters onSettingsChange={onSettingsChange} settings={settings} /></div> : null}</div>
          <div className="filter-overflow-section"><strong>Labels and eligibility</strong><div className="filter-overflow-grid"><EligibilityFilters filters={eligibilityFilters} onChange={setEligibilityFilters} prefix="News" /><InventoryFilterSelect ariaLabel="Label state" onChange={setLabelState} options={NEWS_LABEL_STATE_OPTIONS} value={labelState} /><InventoryFilterSelect ariaLabel="News article class" onChange={(value) => onSettingsChange({ kind: value })} options={NEWS_ARTICLE_CLASS_OPTIONS} value={settings.kind} /><InventoryFilterSelect ariaLabel="News text coverage" onChange={(value) => onSettingsChange({ content: value })} options={NEWS_TEXT_OPTIONS} value={settings.content} /></div></div>
          <div className="filter-overflow-actions">{hasRefinements ? <button className="button secondary compact news-clear-filters" onClick={clearRefinements} type="button">Clear filters</button> : <span />}<button className="button secondary compact" onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCw size={13} /> Refresh</button></div>
        </FilterOverflowMenu>
      </div>
      <NewsStatus inline state={state} />
    </form>
    <div className="news-table-wrap intelligence-feed-scroll">
      <div className="intelligence-feed news-intelligence-feed" role="list">
        <div className="intelligence-feed-header news-intelligence-grid" role="row"><span title="Sorted newest to earliest">Time ↓</span><span>Ticker</span><span>Headline &amp; context</span><span>Purpose</span><span>Origin</span><SentimentSortButton onChange={setSentimentSort} order={sentimentSort} /><span>Forecast</span><span>Reaction</span><span>History</span><span>Analyst</span><span>Text</span></div>
        {displayRows.map((row) => {
          const tone = newsTemperature(row.published_at_utc, wallClockMs);
          const directionValue = normalizeSemanticDirection(row.news_synthesis_summary?.composite_sentiment);
          return <article className="intelligence-feed-row news-intelligence-grid" data-direction={directionValue} key={row.canonical_news_id} role="listitem">
            <div className="intelligence-time-block"><NewsTemperatureTag tone={tone} /><MarketTime className="news-row-time" dateStyle="short" includeDate value={row.published_at_utc} /></div>
            <div className="intelligence-identity-block"><TickerList presentations={presentations} tickers={row.ticker_link_sample} /></div>
            <div className="intelligence-main-block">
              <button className="news-headline-button" onClick={() => openNewsPage(row, state.queryId)} type="button"><strong><MarketNumberText text={row.title || "Untitled story"} /></strong>{newsTeaser(row) ? <small><MarketNumberText text={newsTeaser(row)} /></small> : null}</button>
              <NewsAiReviewControl initialState={row.ai_state} newsId={row.canonical_news_id} publishedAt={row.published_at_utc} compact />
            </div>
            <div className="news-label-value"><SynthesisClass summary={row.news_synthesis_summary} /></div>
            <div className="news-label-value">{readableLabel(row.news_synthesis_summary?.information_origin || row.news_origin || "Unknown")}</div>
            <div className="intelligence-sentiment-cell"><SynthesisDirection compact summary={row.news_synthesis_summary} /></div>
            <EligibilityCell active={row.news_synthesis_summary?.forecast_trigger_eligible} />
            <EligibilityCell active={row.news_synthesis_summary?.reaction_evaluation_eligible} />
            <EligibilityCell active={row.news_synthesis_summary?.issuer_history_context_eligible} />
            <EligibilityCell active={row.news_synthesis_summary?.analyst_evaluation_eligible} />
            <div className="intelligence-utility-cell"><NewsTextState row={row} /></div>
          </article>;
        })}
      </div>
      {!state.loading && !state.rows.length ? <NewsEmpty label="No news matches this query." /> : null}
    </div>
  </section>;
}

export function TickerNewsContainer({ asOf, live = false, onSymbolChange, settings, symbol }: { asOf: string; live?: boolean; onSymbolChange?: (symbol: string) => void; settings: { lookbackHours: number; showTeaser: boolean }; symbol: string }) {
  const state = useNewsQuery({ asOf, content: "all", direction: "", eligibilityFilters: EMPTY_ELIGIBILITY_QUERY, endDate: "", hours: settings.lookbackHours, kind: "all", labelState: "", limit: 100, live, origin: "", refreshKey: 0, role: "", search: "", startDate: "", ticker: symbol });
  const presentations = useTickerPresentations([symbol]);
  const effectiveAsOf = state.asOf || asOf;
  const wallClockMs = useWallClock();
  const orderedRows = [...state.rows].sort(compareNewsRecency);
  const eventRows = orderedRows.filter((row) => row.news_synthesis_summary?.forecast_trigger_eligible);
  const contextRows = orderedRows.filter((row) => !row.news_synthesis_summary?.forecast_trigger_eligible && ["analyst", "editorial", "regulator", "mixed"].includes(row.news_synthesis_summary?.information_origin ?? ""));
  const followupRows = orderedRows.filter((row) => !eventRows.includes(row) && !contextRows.includes(row));
  return <section className="ticker-news" aria-label={`${symbol} news`}>
    <header><div><TickerIdentityWithChange asOf={effectiveAsOf} className="ticker-news-symbol" inputAriaLabel="Ticker news symbol" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} /><span>Recent coverage</span></div><small>{state.rows.length} stories · through <MarketTime value={effectiveAsOf} /></small></header>
    <NewsStatus state={state} compact />
    <div className="ticker-news-feed">
      <TickerNewsSection asOfMs={wallClockMs} emptyLabel="No actionable events in this window." label="Actionable events" queryId={state.queryId} rows={eventRows} showTeaser={settings.showTeaser} />
      <TickerNewsSection asOfMs={wallClockMs} emptyLabel="No analysis or issuer context." label="Analysis & issuer context" queryId={state.queryId} rows={contextRows} showTeaser={settings.showTeaser} />
      <TickerNewsSection asOfMs={wallClockMs} emptyLabel="No follow-ups or market summaries." label="Follow-ups & market summaries" queryId={state.queryId} rows={followupRows} showTeaser={settings.showTeaser} />
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
  const direction = normalizeSemanticDirection(row.news_synthesis_summary?.composite_sentiment);
  return <article data-direction={direction} data-tone={tone}>
    <div aria-label={`${tone} news`} className="ticker-news-marker" title={`${tone} news`}><TemperatureIcon size={14} /></div>
    <div className="ticker-event-time"><MarketTime dateStyle="short" includeDate value={row.published_at_utc} /><em data-tone={tone}>{tone}</em></div>
    <div className="ticker-event-content"><div className="ticker-news-meta"><SynthesisDirection summary={row.news_synthesis_summary} salient /><SynthesisClass summary={row.news_synthesis_summary} /><SynthesisConcepts concepts={row.news_synthesis_summary?.concepts} compact /></div><button className="ticker-news-open" onClick={() => openNewsPage(row, queryId)} type="button"><strong>{row.title}</strong>{showTeaser && newsTeaser(row) ? <p>{newsTeaser(row)}</p> : null}</button></div>
  </article>;
}

export function NewsDetailContainer({ asOf, canvasId, requestedNewsId }: { asOf: string; canvasId: string; requestedNewsId?: string }) {
  const wallClockMs = useWallClock();
  const [selection, setSelection] = useState<NewsSelection>(() => {
    const stored = readSelectedNews(canvasId);
    if (!requestedNewsId) return stored;
    const params = new URLSearchParams(window.location.search);
    const publishedAt = params.get("news_published_at") || "";
    const queryId = params.get("news_query_id") || "";
    if (publishedAt || queryId) return { newsId: requestedNewsId, publishedAt, queryId };
    return stored.newsId === requestedNewsId ? stored : { newsId: requestedNewsId, publishedAt: "", queryId: "" };
  });
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
  const tone = newsTemperature(row.published_at_utc, wallClockMs);
  const kind = isNewsKind(row.news_kind) ? row.news_kind : classification.kind;
  const synthesisSummary = row.news_synthesis_summary ?? null;
  return <article className="news-reader">
    <header className="news-reader-hero">
      <div className="news-reader-kicker"><NewsTemperatureTag tone={tone} /><MarketTime includeDate value={row.published_at_utc} /><NewsKind classification={{ ...classification, kind }} /><span className="news-reader-source">{row.url_domain || "News"}</span>{row.render_status === "unrendered" ? <span className="news-text-state" data-state="unrendered">Unrendered</span> : null}</div>
      <div className="news-reader-title-row"><h1><MarketNumberText text={title} /></h1>{synthesisSummary ? <div className="news-reader-primary-direction"><SynthesisDirection prominent summary={synthesisSummary} /></div> : null}</div>
      <div className="news-reader-byline"><span>{row.author || "Unknown author"}</span>{detailTickers.length === 1 ? <TickerIdentityWithChange asOf={asOf} logoUrl={presentations[detailTickers[0]]?.logo_url} ticker={detailTickers[0]} /> : <TickerList presentations={presentations} tickers={detailTickers} />}</div>
      {synthesisSummary
        ? <NewsDetailOverview summary={synthesisSummary} />
        : <div className="news-label-pending">{row.intelligence_status === "unavailable" ? "News Synthesis temporarily unavailable" : "News Synthesis pending"}</div>}
      {tags.length ? <div className="news-reader-tags" aria-label="Source tags">{tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
      <NewsAiReviewControl initialState={row.ai_state} newsId={newsId} publishedAt={row.published_at_utc} />
    </header>
    <div className="news-reader-evidence-grid">
      {row.news_synthesis ? <NewsSynthesisPanel document={row.news_synthesis} /> : <NewsClassificationPanel classification={classification} />}
    </div>
    {body ? <div className="news-reader-body">{articleParagraphs(body).map((paragraph, index) => <p key={`${index}-${paragraph.slice(0, 20)}`}><MarketNumberText text={paragraph} /></p>)}</div> : <NewsEmpty label="This record contains title metadata but no readable article text." />}
    <footer>{row.article_url ? <a href={row.article_url} rel="noreferrer" target="_blank">Open original source <ExternalLink size={12} /></a> : null}</footer>
  </article>;
}

function useNewsQuery({ asOf, content, direction, eligibilityFilters, endDate, hours, kind, labelState, limit, live, origin, refreshKey, role, search, startDate, ticker }: { asOf: string; content: string; direction: string; eligibilityFilters: EligibilityQuery; endDate: string; hours: number; kind: string; labelState: string; limit: number; live: boolean; origin: string; refreshKey: number; role: string; search: string; startDate: string; ticker: string }) {
  const [rows, setRows] = useState<NewsRow[]>([]); const [payload, setPayload] = useState<NewsPayload | null>(null); const [error, setError] = useState(""); const [loading, setLoading] = useState(true); const [loadingPage, setLoadingPage] = useState(false);
  const [tickerOptions, setTickerOptions] = useState<string[]>([]);
  const [pageIndex, setPageIndex] = useState(0);
  const pageStartsRef = useRef<Array<{ before: string; beforeId: string }>>([{ before: "", beforeId: "" }]);
  const queryIdRef = useRef("");
  const [liveConnected, setLiveConnected] = useState(false);
  const [liveError, setLiveError] = useState("");
  const latestRevision = useRef<number | null>(null);
  const load = useCallback(async (before = "", beforeId = "", signal?: AbortSignal, pageAsOf = "") => {
    const queryAsOf = pageAsOf || (live ? new Date().toISOString() : asOf);
    const next = await api<NewsPayload>(`/api/trading/news${query({ analyst_eligible: eligibilityFilters.analyst || undefined, as_of: queryAsOf, before: before || undefined, before_id: beforeId || undefined, content, direction: direction || undefined, end_date: endDate || undefined, forecast_eligible: eligibilityFilters.forecast || undefined, history_eligible: eligibilityFilters.history || undefined, kind: kind === "all" ? undefined : kind, label_state: labelState || undefined, limit, lookback_hours: hours, origin: origin || undefined, query_id: before ? queryIdRef.current : undefined, reaction_eligible: eligibilityFilters.reaction || undefined, role: role || undefined, search: search || undefined, start_date: startDate || undefined, ticker: ticker || undefined })}`, { signal, timeoutMs: 30000 });
    if (signal?.aborted) return;
    queryIdRef.current = next.query_id;
    setError("");
    if (next.ticker_options) setTickerOptions(next.ticker_options);
    setPayload(next); setRows(next.rows);
  }, [asOf, content, direction, eligibilityFilters.analyst, eligibilityFilters.forecast, eligibilityFilters.history, eligibilityFilters.reaction, endDate, hours, kind, labelState, limit, live, origin, role, search, startDate, ticker]);
  useEffect(() => { const controller = new AbortController(); pageStartsRef.current = [{ before: "", beforeId: "" }]; setPageIndex(0); setTickerOptions([]); setLoading(true); setError(""); load("", "", controller.signal).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [load, refreshKey]);
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
          pageStartsRef.current = [{ before: "", beforeId: "" }];
          setPageIndex(0);
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
  const goToPage = useCallback((targetIndex: number) => {
    if (!payload || targetIndex < 0) return;
    let start = pageStartsRef.current[targetIndex];
    if (targetIndex === pageIndex + 1) {
      if (!payload.has_more || !payload.next_before) return;
      start = { before: payload.next_before, beforeId: payload.next_before_id };
      pageStartsRef.current[targetIndex] = start;
    }
    if (!start) return;
    setLoadingPage(true); setError("");
    load(start.before, start.beforeId, undefined, payload.as_of)
      .then(() => setPageIndex(targetIndex))
      .catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)))
      .finally(() => setLoadingPage(false));
  }, [load, pageIndex, payload]);
  return { asOf: payload?.as_of, canGoNext: Boolean(payload?.has_more), canGoPrevious: pageIndex > 0, error, goToNextPage: () => goToPage(pageIndex + 1), goToPreviousPage: () => goToPage(pageIndex - 1), hasMore: Boolean(payload?.has_more), live, liveConnected, liveError, loading, loadingPage, marketTimezone: payload?.market_timezone ?? "America/New_York", pageNumber: pageIndex + 1, queryId: payload?.query_id ?? "", rows, tickerOptions, windowStart: payload?.window_start };
}

// Product UI reports user-relevant freshness only. Never render database/table
// names, storage paths, raw service errors, implementation notes, or agent/chat text.
function NewsStatus({ compact, inline, state }: { compact?: boolean; inline?: boolean; state: ReturnType<typeof useNewsQuery> }) { const resultState = `${state.rows.length} rows on page ${state.pageNumber}${state.hasMore ? " · more available" : ""}`; return <div className="news-status" data-compact={compact ? "true" : "false"} data-inline={inline ? "true" : "false"}>{state.loading ? <span><i className="loading-spinner" aria-hidden="true" />Querying news…</span> : state.error ? <strong>{state.error}</strong> : inline ? <div className="news-pagination" title={`${resultState} · custom dates use ${state.marketTimezone}`}><span>{state.rows.length} rows</span><button aria-label="Previous News page" disabled={!state.canGoPrevious || state.loadingPage} onClick={state.goToPreviousPage} type="button"><ChevronLeft size={13} /></button><b>Page {state.pageNumber}</b><button aria-label="Next News page" disabled={!state.canGoNext || state.loadingPage} onClick={state.goToNextPage} type="button"><ChevronRight size={13} /></button></div> : <><span>{resultState}</span>{!compact && state.windowStart ? <span className="news-window-start"><span>Since</span><MarketTime dateStyle="short" includeDate layout="inline" value={state.windowStart} /></span> : null}<span className="news-source-label">{state.live ? state.liveConnected ? "Live updates" : "Reconnecting…" : "Point-in-time"}</span></>}</div>; }
function NewsEmpty({ label }: { label: string }) { return <div className="news-empty"><Newspaper size={18} /><span>{label}</span></div>; }
function TickerList({ presentations, tickers = [] }: { presentations: Record<string, TickerPresentation>; tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <SecurityIdentityCell companyName={presentations[ticker]?.issuer_name} country={presentations[ticker]?.country} halted={presentations[ticker]?.market_is_halted ?? presentations[ticker]?.trading_status} key={ticker} logoUrl={presentations[ticker]?.logo_url} newsRecency={presentations[ticker]?.live_news_recency} secRecency={presentations[ticker]?.sec_recency} ticker={ticker} />)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function NewsTextState({ row }: { row: NewsRow }) { const state = row.render_status === "unrendered" ? "unrendered" : row.is_title_only ? "title" : "full"; return <span className="news-text-state" data-state={state}>{state === "unrendered" ? "Unrendered" : row.is_title_only ? "Title" : row.has_pdf ? "PDF" : row.has_external_text ? "Full" : "Body"}</span>; }
function NewsKind({ classification }: { classification: NewsClassification }) { const values = { ai: { Icon: Bot, label: "AI" }, analyst: { Icon: TrendingUp, label: "Analyst" }, company: { Icon: Building2, label: classification.format === "earnings_flash" ? "Company earnings" : "Company" }, editorial: { Icon: Newspaper, label: "Editorial" }, insights: { Icon: Lightbulb, label: "Insights" }, market: { Icon: Globe2, label: classification.format === "trading_halt" ? "Trading halt" : "Market" }, multi: { Icon: Layers3, label: "Multi-company" }, regulatory: { Icon: FileCheck2, label: "Regulatory" }, why_moving: { Icon: Megaphone, label: "Why moving" } }; const value = values[classification.kind]; return <span className="news-kind" data-kind={classification.kind} title={`${Math.round(classification.confidence * 100)}% classification confidence`}><value.Icon size={11} />{value.label}</span>; }
function SynthesisDirection({ compact = false, prominent = false, salient = false, summary }: { compact?: boolean; prominent?: boolean; salient?: boolean; summary?: NewsSynthesisSummary | null }) { return <SemanticDirectionMetric compact={compact} direction={summary?.composite_sentiment} prominent={prominent || salient} score={synthesisScore(summary)} />; }
function EligibilityCell({ active }: { active?: boolean }) { return <span aria-label={active ? "Eligible" : "Not eligible"} className="eligibility-column-value" data-active={active ? "true" : "false"} title={active ? "Eligible" : "Not eligible"}>{active ? <Check aria-hidden="true" size={12} strokeWidth={2.4} /> : "—"}</span>; }

function NewsAiReviewControl({ compact = false, initialState, newsId, publishedAt }: { compact?: boolean; initialState?: NewsAiState | null; newsId: string; publishedAt: string }) {
  const [state, setState] = useState<NewsAiState | null>(initialState ?? null);
  const [error, setError] = useState("");
  const status = state?.review?.status ?? "not_reviewed";
  const labels = state?.review?.labels?.issuers ?? [];
  const hypotheses = state?.hypotheses ?? [];
  const expectedHypotheses = labels.filter((label) => label.ticker && label.forecast_relevance_probability >= 0.5).length;
  const awaitingHypotheses = status === "complete" && hypotheses.length < expectedHypotheses;
  const pending = ["queued", "labeling", "building_context", "predicting"].includes(status) || awaitingHypotheses;
  const pollCount = useRef(0);
  useEffect(() => { setState(initialState ?? null); }, [initialState]);
  useEffect(() => {
    if (!pending) return;
    pollCount.current = 0;
    const timer = window.setInterval(() => {
      pollCount.current += 1;
      if (pollCount.current > 30) { window.clearInterval(timer); return; }
      api<NewsAiState>(`/api/trading/news/${encodeURIComponent(newsId)}/ai-review`, { timeoutMs: 10000 })
        .then(setState).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason)));
    }, 2000);
    return () => window.clearInterval(timer);
  }, [newsId, pending]);
  const requestReview = async () => {
    setError("");
    setState((current) => ({ ...(current ?? {}), review: { ...(current?.review ?? {}), status: "queued" } }));
    try {
      await api(`/api/trading/news/${encodeURIComponent(newsId)}/ai-review`, {
        method: "POST", body: JSON.stringify({ published_at_utc: publishedAt, requested_by: "frontend-operator" }), timeoutMs: 15000,
      });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
      setState((current) => ({ ...(current ?? {}), review: { ...(current?.review ?? {}), status: "failed" } }));
    }
  };
  const funnel = state?.funnel;
  return <div className={compact ? "news-ai-review compact" : "news-ai-review"} data-status={status}>
    <div className="news-ai-review-summary">
      {funnel ? <span className="news-ai-badge" data-tone={funnel.forecast_eligibility === "eligible" ? "candidate" : "filtered"}>{funnel.forecast_eligibility === "eligible" ? `Funnel candidate ${Math.round(funnel.eligible_probability * 100)}%` : readableLabel(funnel.stage)}</span> : <span className="news-ai-badge" data-tone="pending">Funnel pending</span>}
      {labels.map((label) => <span className="news-ai-badge" data-tone={aiLabelTone(label)} key={`${label.ticker}-${label.issuer_name}`}>{label.ticker || label.issuer_name}: {Math.round(label.forecast_relevance_probability * 100)}% · {readableLabel(aiLanguageSentiment(label))}</span>)}
      {!labels.length ? <button aria-label="Review this news with AI" className="news-ai-review-button" disabled={pending} onClick={requestReview} type="button"><Bot size={13} />{pending ? "Reviewing…" : status === "failed" ? "Retry AI review" : "Review with AI"}</button> : null}
    </div>
    {!compact && labels.length ? <div className="news-ai-review-detail">{labels.map((label) => <div key={`${label.issuer_name}-detail`}><strong>{label.ticker || label.issuer_name}</strong><span>Forecast relevance {Math.round(label.forecast_relevance_probability * 100)}%</span><span>Positive {Math.round(label.positive_implication_probability * 100)}%</span><span>Negative {Math.round(label.negative_implication_probability * 100)}%</span><small>{label.event_tags.map(readableLabel).join(" · ") || "No event tags"}</small></div>)}</div> : null}
    {!compact && hypotheses.length ? <div className="news-ai-hypotheses">{hypotheses.map((row) => <section key={`${row.ticker}-${row.context_as_of_utc}`}><header><strong>{row.ticker} market hypothesis</strong><span>{readableLabel(row.prediction.regime_compatibility)}</span></header><div>{Object.entries(row.prediction.predictions).map(([horizon, prediction]) => <span data-abstain={prediction.abstain} key={horizon}><b>{horizon}</b><em>Up {Math.round(prediction.upside_probability * 100)}%</em><em>Down {Math.round(prediction.downside_probability * 100)}%</em><em>Expected {prediction.expected_return_pct.toFixed(2)}%</em></span>)}</div><small>{row.prediction.uncertainty}</small></section>)}</div> : null}
    {error || state?.review?.error ? <small className="news-ai-review-error">{error || state?.review?.error}</small> : null}
  </div>;
}

function aiLanguageSentiment(label: IssuerAiLabel) { return label.positive_implication_probability >= 0.5 && label.negative_implication_probability >= 0.5 ? "mixed" : label.positive_implication_probability >= 0.5 ? "positive" : label.negative_implication_probability >= 0.5 ? "negative" : "neutral"; }
function aiLabelTone(label: IssuerAiLabel) { return label.forecast_relevance_probability < 0.5 ? "filtered" : aiLanguageSentiment(label); }
function NewsDateRangeFilters({ onSettingsChange, settings }: { onSettingsChange: (patch: Partial<AllNewsSettings>) => void; settings: AllNewsSettings }) { return <><label title="Exchange date in America/New_York"><span>From (ET)</span><input aria-label="News range start date in exchange time" onChange={(event) => onSettingsChange({ startDate: event.target.value })} type="date" value={settings.startDate} /></label><label title="Exchange date in America/New_York"><span>Through (ET)</span><input aria-label="News range end date in exchange time" onChange={(event) => onSettingsChange({ endDate: event.target.value })} type="date" value={settings.endDate} /></label></>; }
function EligibilityFilters({ filters, onChange, prefix }: { filters: EligibilityQuery; onChange: (next: EligibilityQuery) => void; prefix: string }) { const fields: { key: keyof EligibilityQuery; label: string }[] = [{ key: "forecast", label: "Forecast" }, { key: "reaction", label: "Reaction" }, { key: "history", label: "History" }, { key: "analyst", label: "Analyst" }]; return <>{fields.map(({ key, label }) => <InventoryFilterSelect ariaLabel={`${prefix} ${label} eligibility`} key={key} onChange={(value) => onChange({ ...filters, [key]: value })} options={inventoryEligibilityOptions(label)} value={filters[key]} />)}</>; }
function SynthesisClass({ summary }: { summary?: NewsSynthesisSummary | null }) { if (!summary) return <span className="news-scoped-class" data-state="pending">Pending</span>; return <span className="news-scoped-class" data-state={summary.forecast_trigger_eligible ? "event" : "context"} title={summary.forecast_trigger_eligible ? "V1 forecast-trigger eligible" : "V1 context"}>{summary.forecast_trigger_eligible ? <CircleDot size={10} /> : <History size={10} />}{readableLabel(summary.communication_purpose || summary.information_origin || "context")}</span>; }
function SynthesisConcepts({ compact = false, concepts = [] }: { compact?: boolean; concepts?: string[] }) { const readable = concepts.map(shortConcept).filter(Boolean); const visible = readable.slice(0, compact ? 1 : 3); if (!visible.length) return null; return <span className="news-scoped-concepts">{visible.map((concept) => <span key={concept}>{concept}</span>)}{readable.length > visible.length ? <span>+{readable.length - visible.length}</span> : null}</span>; }
function NewsDetailOverview({ summary }: { summary: NewsSynthesisSummary }) {
  return <section className="detail-intelligence-overview" aria-label="News interpretation summary">
    <div className="detail-direction-focus"><span>Text sentiment</span><SynthesisDirection prominent summary={summary} /></div>
    <DetailDatum label="Purpose" value={readableLabel(summary.communication_purpose || "unclassified")} />
    <DetailDatum label="Source origin" value={readableLabel(summary.information_origin || "unknown")} />
    <DetailDatum label="Primary event" value={shortConcept(summary.concepts[0] || "Not identified")} />
    <DetailDatum label="Operational use" value={eligibilityText(summary)} />
  </section>;
}
function DetailDatum({ label, value }: { label: string; value: string }) { return <span className="detail-intelligence-datum"><small>{label}</small><strong>{value}</strong></span>; }
function NewsClassificationPanel({ classification }: { classification: NewsClassification }) {
  return <details className="news-detail-contract" open>
    <summary><span><strong>Loaded classification</strong><small>Article rules and issuer-label summary</small></span><em>{classification.version}</em></summary>
    <div className="news-detail-contract-body">
      <section aria-label="Article classification fields">
        <header>Article class</header>
        <div className="news-detail-contract-grid"><DetailDatum label="Kind" value={readableLabel(classification.kind)} /><DetailDatum label="Format" value={readableLabel(classification.format)} /><DetailDatum label="Origin" value={readableLabel(classification.origin)} /><DetailDatum label="Scope" value={readableLabel(classification.scope)} /><DetailDatum label="Company news" value={yesNo(classification.is_company_news)} /><DetailDatum label="Confidence" value={percentConfidence(classification.confidence)} /><DetailDatum label="Rule version" value={classification.version || "Not reported"} /></div>
        <DetailList label="Topics" values={classification.topics} />
        <DetailList label="Classification evidence" values={classification.evidence} />
      </section>
    </div>
  </details>;
}
function NewsSynthesisPanel({ document }: { document: NewsSynthesisDocument }) {
  const envelope = document.envelope;
  const entityById = new Map(document.entities.map((entity) => [entity.entity_id, entity]));
  const statementById = new Map(document.statements.map((statement) => [statement.statement_id, statement]));
  const eligibilityByEntity = new Map<string, Map<string, boolean>>();
  document.eligibility.forEach((row) => { const products = eligibilityByEntity.get(row.entity_id) ?? new Map<string, boolean>(); products.set(row.product, row.eligible); eligibilityByEntity.set(row.entity_id, products); });
  return <details className="news-detail-contract" open>
    <summary><span><strong>News synthesis</strong><small>Evidence-preserving document and issuer interpretation</small></span><em>{document.contract_version}</em></summary>
    <div className="news-detail-contract-body">
      <section aria-label="News synthesis envelope">
        <header>Document envelope</header>
        <div className="news-detail-contract-grid">
          <DetailDatum label="Structure" value={readableLabel(envelope.document_structure?.value || "unknown")} />
          <DetailDatum label="Purpose" value={readableLabel(envelope.communication_purpose?.value || "unknown")} />
          <DetailDatum label="Origin" value={readableLabel(envelope.information_origin?.value || "unknown")} />
          <DetailDatum label="Production" value={readableLabel(envelope.production_method?.value || "unknown")} />
          <DetailDatum label="Text" value={readableLabel(envelope.text_availability?.value || "unknown")} />
          <DetailDatum label="Concept registry" value={document.concept_registry_version} />
        </div>
        <DetailList label="Quality flags" values={document.quality_flags.map(readableStructuredLabel)} emptyLabel="None" />
      </section>
      <section aria-label="News synthesis issuer views">
        <header>Issuer views</header>
        {document.issuer_views.map((view) => {
          const entity = entityById.get(view.entity_id);
          const statements = view.statement_ids.map((id) => statementById.get(id)).filter(Boolean) as NewsSynthesisDocument["statements"];
          const products = eligibilityByEntity.get(view.entity_id);
          return <article className="news-scoped-label" key={view.entity_id}>
            <header><strong>{entity?.ticker || entity?.display_name || "Unresolved issuer"}</strong><span className="news-scoped-class" data-state="event">{readableLabel(view.composite_sentiment)}</span></header>
            <div className="news-scoped-label-facts"><DetailDatum label="Identity" value={readableLabel(entity?.identity_status || "unknown")} /><DetailDatum label="Positive strength" value={String(view.positive_strength)} /><DetailDatum label="Negative strength" value={String(view.negative_strength)} /><DetailDatum label="Statements" value={String(statements.length)} /></div>
            <DetailList label="Concepts" values={Array.from(new Set(statements.map((statement) => shortConcept(statement.concept_leaf))))} emptyLabel="None" />
            {statements.map((statement) => <details key={statement.statement_id}><summary>{shortConcept(statement.concept_leaf)} · {readableLabel(statement.statement_kind)} · {readableLabel(statement.time_relation)}</summary><p>{statement.evidence_spans.map((span) => span.quote).join(" ")}</p><DetailList label="Facts" values={statement.typed_facts.map((fact) => String(fact.raw || fact.fact_type || "fact"))} emptyLabel="None" /></details>)}
            <footer><EligibilityState active={products?.get("forecast_trigger") ?? false} label="Forecast trigger" /><EligibilityState active={products?.get("reaction_study") ?? false} label="Reaction study" /><EligibilityState active={products?.get("issuer_history") ?? false} label="Issuer history" /><EligibilityState active={products?.get("analyst_evaluation") ?? false} label="Analyst evaluation" /></footer>
          </article>;
        })}
      </section>
    </div>
  </details>;
}
function DetailList({ emptyLabel = "Not reported", label, values }: { emptyLabel?: string; label: string; values: string[] }) { const visible = values.filter(Boolean); return <div className="detail-list-row"><small>{label}</small><span>{visible.length ? visible.map((value) => <b key={value}>{value}</b>) : <em>{emptyLabel}</em>}</span></div>; }
function EligibilityState({ active, label }: { active: boolean; label: string }) { return <span data-active={active} title={`${label}: ${active ? "eligible" : "not eligible"}`}>{label}: {active ? "Eligible" : "Not eligible"}</span>; }
function eligibilityText(summary: Pick<NewsSynthesisSummary, "analyst_evaluation_eligible" | "forecast_trigger_eligible" | "issuer_history_context_eligible" | "reaction_evaluation_eligible">): string { const uses = [summary.forecast_trigger_eligible ? "Forecast" : "", summary.reaction_evaluation_eligible ? "Reaction study" : "", summary.issuer_history_context_eligible ? "Issuer history" : "", summary.analyst_evaluation_eligible ? "Analyst evaluation" : ""].filter(Boolean); return uses.length ? uses.join(" · ") : "Context only"; }
function synthesisScore(summary?: NewsSynthesisSummary | null): number | null { return summary ? summary.positive_strength - summary.negative_strength : null; }
function MarketNumberText({ text }: { text: string }) { const matches = Array.from(text.matchAll(MARKET_NUMBER_PATTERN)); if (!matches.length) return text; const parts: Array<string | ReactElement> = []; let cursor = 0; matches.forEach((match, index) => { const start = match.index; if (start > cursor) parts.push(text.slice(cursor, start)); const value = match[0]; const kind = /%|percent|basis|bps/i.test(value) ? "rate" : "price"; parts.push(<span className="market-number" data-market-number={kind} key={`${start}-${index}`}>{value}</span>); cursor = start + value.length; }); if (cursor < text.length) parts.push(text.slice(cursor)); return <>{parts}</>; }
function NewsTemperatureTag({ tone }: { tone: NewsTemperature }) { const value = newsTemperaturePresentation(tone); return <span className="news-temperature" data-tone={tone}><value.Icon size={tone === "hot" ? 16 : 15} strokeWidth={tone === "hot" ? 1.5 : tone === "cold" ? 1.8 : 1.7} /><em>{value.label}</em></span>; }
function newsTemperature(publishedAt: string, asOfMs: number): NewsTemperature { return timeRecency(publishedAt, asOfMs); }
function newsTemperaturePresentation(tone: NewsTemperature) { return tone === "hot" ? { Icon: Flame, label: "Hot" } : tone === "cold" ? { Icon: Snowflake, label: "Cold" } : { Icon: Clock3, label: "Old" }; }
function isNewsKind(value: unknown): value is NewsKindValue { return ["ai", "analyst", "company", "editorial", "insights", "market", "multi", "regulatory", "why_moving"].includes(String(value)); }
function classificationFromRow(row: NewsRow): NewsClassification { if (row.classification) return row.classification; const kind = isNewsKind(row.news_kind) ? row.news_kind : "market"; return { confidence: row.classification_confidence ?? 0, evidence: row.classification_evidence ?? ["news_synthesis_pending"], format: row.news_format ?? "general", is_company_news: row.is_company_news ?? false, kind, origin: row.news_origin ?? "unknown", scope: row.news_scope ?? ((row.ticker_link_sample?.length ?? 0) === 1 ? "single_ticker" : (row.ticker_link_sample?.length ?? 0) > 1 ? "multi_ticker" : "market_wide"), topics: row.news_topics ?? [], version: row.news_synthesis_summary?.engine_version ?? "pending" }; }
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
function newsTeaser(row: NewsRow) { const value = (row.text_preview ?? "").replace(/\s+/g, " ").trim(); if (!value) return ""; const titlePrefix = `Title: ${row.title}`; const withoutTitle = value.toLowerCase().startsWith(titlePrefix.toLowerCase()) ? value.slice(titlePrefix.length).trim() : value; return withoutTitle.replace(/^Teaser:\s*/i, "").replace(/^Body:\s*/i, "").replace(/\s+Source\s+\[[^\]]+\].*$/i, "").trim(); }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.news-selection.${canvasId}`; }
function parseNewsSelection(value: string): NewsSelection { try { const parsed = JSON.parse(value) as Partial<NewsSelection>; if (parsed.newsId) return { newsId: parsed.newsId, publishedAt: parsed.publishedAt ?? "", queryId: parsed.queryId ?? "" }; } catch { if (value) return { newsId: value, publishedAt: "", queryId: "" }; } return { newsId: "", publishedAt: "", queryId: "" }; }
function readSelectedNews(canvasId: string) { return parseNewsSelection(window.localStorage.getItem(selectionKey(canvasId)) || ""); }
function selectNews(canvasId: string, selection: NewsSelection) { window.localStorage.setItem(selectionKey(canvasId), JSON.stringify(selection)); window.dispatchEvent(new CustomEvent(NEWS_SELECTION_EVENT, { detail: { canvasId, ...selection } })); }
function prepareNewsReader(selection: NewsSelection) { ensureNewsReaderCanvas(); selectNews(NEWS_READER_CANVAS_ID, selection); }
function newsPageUrl(selection: NewsSelection) { const url = new URL(focusCanvasUrl(NEWS_READER_CANVAS_ID, "news_detail", "draft")); url.searchParams.set("news", selection.newsId); if (selection.publishedAt) url.searchParams.set("news_published_at", selection.publishedAt); if (selection.queryId) url.searchParams.set("news_query_id", selection.queryId); return url.toString(); }
function openNewsPage(row: NewsRow, queryId: string) { const selection = { newsId: row.canonical_news_id, publishedAt: row.published_at_utc, queryId }; prepareNewsReader(selection); window.open(newsPageUrl(selection), "quant-news-reader"); }
