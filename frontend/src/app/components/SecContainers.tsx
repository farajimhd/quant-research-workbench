import { ArrowDown, ArrowUp, ArrowUpDown, BookOpen, Bot, Check, CircleDashed, Clock3, ExternalLink, FileCheck2, FileText, Flame, Minus, RefreshCw, Search, Snowflake, Sparkles, Target, TriangleAlert, X } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { api, query } from "../../api/client";
import { openTickerChartsQuotes } from "../tickerNavigation";
import { SEC_READER_CANVAS_ID, ensureSecReaderCanvas, focusCanvasUrl } from "../canvasWorkspace";
import { FilterOverflowMenu } from "./FilterOverflowMenu";
import { InventoryFilterSelect, inventoryEligibilityOptions, type InventoryFilterOption } from "./InventoryFilterSelect";
import { MarketTime } from "./MarketTime";
import { Modal } from "./Modal";
import { normalizeSemanticDirection, SemanticDirectionMetric, SentimentSortButton, sortRowsBySentimentScore, type SentimentSortOrder } from "./SemanticDirectionMetric";
import { TickerIdentity, TickerIdentityWithChange, useTickerPresentations, type TickerPresentation } from "./TickerIdentity";
import { PresentedValue, SecurityIdentityCell, tableCellClass, type PresentationValueType } from "./TablePresentation";
import { useWallClock } from "./useWallClock";
import { SecIntelligenceIcon, secIconKindFor, secIconKindLabel, type SecIconKind } from "./SecIntelligenceIcon";

export type SecSettings = { content: string; endDate: string; label: string; limit: number; lookbackHours: number; rangeMode: "custom" | "preset"; startDate: string; ticker: string };
type SecEligibilityQuery = { forecast: string; reaction: string; history: string; prior: string; followup: string };
type SecColumnFilters = { direction: string; impact: string; labelState: string; origin: string; role: string; scope: string; eligibility: SecEligibilityQuery };
const EMPTY_SEC_FILTERS: SecColumnFilters = { direction: "", impact: "", labelState: "", origin: "", role: "", scope: "", eligibility: { forecast: "", reaction: "", history: "", prior: "", followup: "" } };
type SecLabel = { id: string; label: string };
type SecScopedLabel = { content_role: string; event_concepts: string[]; evidence_scope: string; episode_followup_eligible: boolean; forecast_trigger_eligible: boolean; issuer_history_context_eligible: boolean; issuer_relationship?: string; issuer_role: string; prior_primary_context_eligible: boolean; quality_flags?: string[]; reaction_evaluation_eligible: boolean; semantic_direction: string; semantic_evidence_text: string; semantic_score?: number | null; source_origin: string; ticker: string; time_orientation?: string };
type SecScopedSummary = { classified?: boolean; content_role: string; episode_followup_eligible: boolean; event_concepts: string[]; forecast_trigger_eligible: boolean; issuer_count?: number; issuer_history_context_eligible: boolean; label_count?: number; labeling_version?: string; prior_primary_context_eligible: boolean; quality_flags?: string[]; reaction_evaluation_eligible: boolean; semantic_direction: string; semantic_score?: number | null; source_origin: string };
type SecEvidence = { document_id: string; end: number; evidence_id: string; quote: string; start: number };
type SecDisclosure = { concept: string; disclosure_id: string; economic_direction: string; epistemic_status: string; evidence: SecEvidence[]; time_relation: string; title: string };
type SecTransition = { comparability: string; concept_family: string; current_period_end: string; current_value?: number | null; economic_direction: string; materiality: string; percent_change?: number | null; prior_period_end: string; prior_value?: number | null; transition_id: string; unit_code: string };
type SecEligibility = { blocking_flags: string[]; eligible: boolean; policy_id: string; product: string; reasons: string[] };
type SecSynthesis = { contract_version: string; eligibility: SecEligibility[]; fundamental_transitions: SecTransition[]; narrative_disclosures: SecDisclosure[]; quality_flags: string[]; reconciliation: Array<{ concept_family: string; reconciliation_id: string; state: string }>; synthesis: { composite_sentiment: string; headline: string; highlights: string[]; mixed_or_contextual: string[]; readable_summary: string; risks: string[] } };
type SecReview = { error?: string; forecast_relevance_probability?: number; fundamental_direction?: string; materiality_probability?: number; model?: string; result?: { abstain: boolean; abstention_reasons: string[]; event_tags: string[]; evidence_ids: string[]; forecast_relevance_probability: number; fundamental_direction: string; guidance_change: string; materiality_probability: number; negative_implication_probability: number; positive_implication_probability: number; risk_change: string; summary: string }; status: string };
type SecRow = { accession_number: string; accepted_at_utc: string; accepted_at_source?: string; acceptance_datetime_raw?: string; affected_security_scope?: string; cik: string; company_name: string; disclosure_title?: string; document_rows?: number; event_time_quality?: "date_only" | "exact"; filing_date?: string; filing_detail_url?: string; filing_label: string; filing_label_text: string; filing_size?: number; form_type: string; impact_label?: string; impact_rationale?: string; impact_score?: number; items: string[]; label_evidence: string[]; primary_document?: string; primary_document_url?: string; report_date?: string; scoped_labels?: SecScopedLabel[]; scoped_summary?: SecScopedSummary | null; sec_review?: SecReview; sec_synthesis?: SecSynthesis | null; taxonomy_version?: string; text_chars?: number; text_rows?: number; text_status?: string; tickers: string[]; xbrl_rows?: number };
type SecRowWire = Omit<SecRow, "items" | "label_evidence" | "tickers"> & { items?: unknown; label_evidence?: unknown; tickers?: unknown };
type SecPayload = { as_of: string; has_more: boolean; intelligence_status?: "ready" | "unavailable"; labels: SecLabel[]; next_before: string; next_before_accession: string; query_id: string; rows: SecRow[]; window_start: string };
type SecPayloadWire = Omit<SecPayload, "rows"> & { rows: SecRowWire[] };
type SecDocument = { byte_size?: number; content_format?: string; content_sha256?: string; description?: string; document_id: string; document_name: string; document_role: string; document_type: string; document_url?: string; extraction_error?: string; extraction_status?: string; file_extension?: string; has_normalized_text?: number; latest_source_revision_at?: string; mime_type?: string; normalizer_version?: string; payload_char_count?: number; sequence_number?: number; source_archive_member?: string; source_revision_kind?: string };
type SecText = { content_format?: string; document_id: string; extraction_method?: string; extracted_at_utc?: string; file_extension?: string; latest_source_revision_at?: string; mime_type?: string; normalizer_version?: string; quality_flags?: string[]; source_archive_member?: string; source_revision_kind?: string; text_byte_count?: number; text_char_count: number; text_kind: string };
type SecTextPage = SecText & { has_more: boolean; limit: number; next_offset: number; offset: number; text: string; view: TextView };
type SecEntity = { entity_cik: string; entity_name?: string; entity_role: string; latest_source_revision_at?: string; source_revision_kind?: string };
type SecFact = { fiscal_period?: string; fiscal_year?: number; period_end_date?: string; tag: string; unit_code: string; value: string };
type SecDetail = { documents: SecDocument[]; entities: SecEntity[]; errors: Record<string, string>; facts: SecFact[]; facts_has_more: boolean; facts_next_offset: number; facts_total: number; filing: SecRow; identity: { exchange_code?: string; sic_description?: string; ticker?: string; tickers?: string[] }; intelligence_status?: "ready" | "unavailable"; originals: SecText[]; status: string; texts: SecText[] };
type SecDetailWire = Omit<SecDetail, "filing"> & { filing: SecRowWire };
type SecFactsPage = { has_more: boolean; next_offset: number; row_count: number; rows: SecFact[] };
type Temperature = "cold" | "dated" | "hot" | "old";
type TextView = "rendered" | "original";
type SecSelection = { acceptedAt: string; key: string; queryId: string };
export type TickerSecPopoverAnchor = { bottom: number; left: number; right: number; top: number };
const SEC_SELECTION_EVENT = "quant-sec-selection";
const INITIAL_LABELS: SecLabel[] = [
  ["current_event", "Current event"], ["periodic_fundamentals", "Periodic fundamentals"], ["offering", "Offering"], ["corporate_transaction", "Corporate transaction"], ["ownership_activism", "Ownership activism"], ["insider_ownership", "Insider ownership"], ["governance", "Governance"], ["ownership", "Ownership"], ["fund_product_disclosure", "Fund product disclosure"], ["fund_dataset", "Fund dataset"], ["structured_finance", "Structured finance"], ["administrative", "Administrative"], ["other_disclosure", "Other disclosure"],
].map(([id, label]) => ({ id, label }));
const SEC_DIRECTION_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any sentiment" }, { value: "positive", label: "Positive" }, { value: "negative", label: "Negative" }, { value: "neutral", label: "Neutral" }, { value: "mixed", label: "Mixed" }];
const SEC_WINDOW_OPTIONS: InventoryFilterOption[] = [{ value: "24", label: "24 hours" }, { value: "72", label: "3 days" }, { value: "168", label: "7 days" }, { value: "720", label: "30 days" }, { value: "8760", label: "1 year" }, { value: "43800", label: "5 years" }, { value: "custom", label: "Custom dates" }];
const SEC_ROLE_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any role" }, { value: "primary_event", label: "Primary event" }, { value: "regulatory_event", label: "Regulatory event" }, { value: "ownership_transaction", label: "Ownership transaction" }, { value: "editorial_analysis", label: "Editorial analysis" }, { value: "automated_summary", label: "Automated summary" }];
const SEC_ORIGIN_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any origin" }, { value: "regulatory_primary", label: "Regulatory primary" }];
const SEC_LIMIT_OPTIONS: InventoryFilterOption[] = [25, 50, 100, 200].map((value) => ({ value: String(value), label: `Top ${value}` }));
const SEC_IMPACT_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any score" }, ...[5, 4, 3, 2, 1].map((score) => ({ value: String(score), label: `${score}/5` }))];
const SEC_LABEL_STATE_OPTIONS: InventoryFilterOption[] = [{ value: "", label: "Any state" }, { value: "classified", label: "Classified" }, { value: "pending", label: "Pending" }, { value: "quality", label: "Quality issue" }];
const SEC_CONTENT_OPTIONS: InventoryFilterOption[] = [{ value: "all", label: "All content" }, { value: "readable", label: "Readable text" }, { value: "xbrl", label: "XBRL facts" }];

export function AllSecContainer({ asOf, live = false, onSettingsChange, settings }: { asOf: string; live?: boolean; onSettingsChange: (patch: Partial<SecSettings>) => void; settings: SecSettings }) {
  const [search, setSearch] = useState(""); const [committed, setCommitted] = useState(""); const [refreshKey, setRefreshKey] = useState(0);
  const [sentimentSort, setSentimentSort] = useState<SentimentSortOrder>("none");
  const [filters, setFilters] = useState<SecColumnFilters>(EMPTY_SEC_FILTERS);
  const wallClockMs = useWallClock();
  const state = useSecQuery({ asOf, filters, refreshKey, search: committed, settings });
  const presentations = useTickerPresentations(state.rows.flatMap((row) => row.tickers ?? []), { includeMarketState: live, includeRecency: live });
  const labels = state.labels.length ? state.labels : INITIAL_LABELS;
  const displayRows = useMemo(() => sortRowsBySentimentScore(state.rows, (row) => synthesisSortScore(row.sec_synthesis), sentimentSort), [sentimentSort, state.rows]);
  const hasRefinements = Boolean(filters.direction || filters.impact || filters.labelState || filters.origin || filters.role || filters.scope || filters.eligibility.forecast || filters.eligibility.reaction || filters.eligibility.history || filters.eligibility.prior || filters.eligibility.followup || settings.ticker || settings.label || settings.content !== "all");
  const activeFilterCount = [filters.direction, settings.rangeMode === "custom" || settings.lookbackHours !== 168, filters.role, filters.origin, settings.limit !== 100, settings.ticker, settings.label, filters.impact, filters.scope, filters.eligibility.forecast, filters.eligibility.reaction, filters.eligibility.history, filters.eligibility.prior, filters.eligibility.followup, filters.labelState, settings.content !== "all"].filter(Boolean).length;
  const clearRefinements = () => { setFilters(EMPTY_SEC_FILTERS); onSettingsChange({ content: "all", label: "", ticker: "" }); };
  return <section className="news-all sec-all" aria-label="All SEC filings">
    <form className="news-query-bar" onSubmit={(event) => { event.preventDefault(); setCommitted(search.trim()); }}>
      <div className="news-query-primary">
        <label className="news-search"><Search size={13} /><input aria-label="Search all SEC filings" onChange={(event) => setSearch(event.target.value)} placeholder="Search ticker, filing…" value={search} /></label>
        <button className="button secondary compact news-search-submit" type="submit">Search</button>
        <span className="news-query-visible-core"><InventoryFilterSelect ariaLabel="SEC semantic direction" onChange={(value) => setFilters({ ...filters, direction: value })} options={SEC_DIRECTION_OPTIONS} value={filters.direction} /></span>
        <span className="news-query-visible-core"><InventoryFilterSelect ariaLabel="SEC time window" defaultValue={168} onChange={(value) => value === "custom" ? onSettingsChange({ rangeMode: "custom" }) : onSettingsChange({ lookbackHours: Number(value), rangeMode: "preset" })} options={SEC_WINDOW_OPTIONS} value={settings.rangeMode === "custom" ? "custom" : settings.lookbackHours} /></span>
        <span className="news-query-visible-wide"><InventoryFilterSelect ariaLabel="SEC content role" onChange={(value) => setFilters({ ...filters, role: value })} options={SEC_ROLE_OPTIONS} value={filters.role} /></span>
        <span className="news-query-visible-wide"><InventoryFilterSelect ariaLabel="SEC source origin" onChange={(value) => setFilters({ ...filters, origin: value })} options={SEC_ORIGIN_OPTIONS} value={filters.origin} /></span>
        {settings.rangeMode === "custom" ? <span className="news-query-date-controls news-query-visible-wide"><SecDateRangeFilters onSettingsChange={onSettingsChange} settings={settings} /></span> : null}
        <label><span>Ticker</span><input aria-label="Filter SEC by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any ticker" value={settings.ticker} /></label>
        <FilterOverflowMenu activeCount={activeFilterCount}>
          <div className="filter-overflow-section"><strong>Query filters</strong><div className="filter-overflow-grid">
            <InventoryFilterSelect ariaLabel="SEC semantic direction" onChange={(value) => setFilters({ ...filters, direction: value })} options={SEC_DIRECTION_OPTIONS} value={filters.direction} />
            <InventoryFilterSelect ariaLabel="SEC time window" defaultValue={168} onChange={(value) => value === "custom" ? onSettingsChange({ rangeMode: "custom" }) : onSettingsChange({ lookbackHours: Number(value), rangeMode: "preset" })} options={SEC_WINDOW_OPTIONS} value={settings.rangeMode === "custom" ? "custom" : settings.lookbackHours} />
            <InventoryFilterSelect ariaLabel="SEC content role" onChange={(value) => setFilters({ ...filters, role: value })} options={SEC_ROLE_OPTIONS} value={filters.role} />
            <InventoryFilterSelect ariaLabel="SEC source origin" onChange={(value) => setFilters({ ...filters, origin: value })} options={SEC_ORIGIN_OPTIONS} value={filters.origin} />
            <InventoryFilterSelect ariaLabel="SEC result limit" defaultValue={100} onChange={(value) => onSettingsChange({ limit: Number(value) })} options={SEC_LIMIT_OPTIONS} value={settings.limit} />
            <label><span>Ticker</span><input aria-label="Filter SEC by ticker" maxLength={16} onChange={(event) => onSettingsChange({ ticker: event.target.value.toUpperCase() })} placeholder="Any ticker" value={settings.ticker} /></label>
          </div>{settings.rangeMode === "custom" ? <div className="filter-overflow-dates"><SecDateRangeFilters onSettingsChange={onSettingsChange} settings={settings} /></div> : null}</div>
          <div className="filter-overflow-section"><strong>Classification and eligibility</strong><div className="filter-overflow-grid"><InventoryFilterSelect ariaLabel="SEC filing label" onChange={(value) => onSettingsChange({ label: value })} options={[{ value: "", label: "All labels" }, ...labels.map((label) => ({ value: label.id, label: label.label }))]} value={settings.label} /><InventoryFilterSelect ariaLabel="SEC impact score" onChange={(value) => setFilters({ ...filters, impact: value })} options={SEC_IMPACT_OPTIONS} value={filters.impact} /><label><span>Security scope</span><input aria-label="SEC security scope" onChange={(event) => setFilters({ ...filters, scope: event.target.value })} placeholder="Any scope" value={filters.scope} /></label><SecEligibilityFilters filters={filters.eligibility} onChange={(eligibility) => setFilters({ ...filters, eligibility })} /><InventoryFilterSelect ariaLabel="SEC label state" onChange={(value) => setFilters({ ...filters, labelState: value })} options={SEC_LABEL_STATE_OPTIONS} value={filters.labelState} /><InventoryFilterSelect ariaLabel="SEC filing content" onChange={(value) => onSettingsChange({ content: value })} options={SEC_CONTENT_OPTIONS} value={settings.content} /></div></div>
          <div className="filter-overflow-actions">{hasRefinements ? <button className="button secondary compact" onClick={clearRefinements} type="button">Clear filters</button> : <SecGuideButton compact />}<button className="button secondary compact" onClick={() => setRefreshKey((value) => value + 1)} type="button"><RefreshCw size={13} /> Refresh</button></div>
        </FilterOverflowMenu>
      </div>
      <SecStatus inline state={state} />
    </form>
    <div className="news-table-wrap intelligence-feed-scroll"><div className="intelligence-feed sec-intelligence-feed" role="list"><div className="intelligence-feed-header sec-intelligence-grid" role="row"><span>Accepted</span><span>Ticker</span><span>Filing / disclosure</span><span>Category</span><span>Impact</span><span>Security scope</span><span>Role</span><span>Origin</span><SentimentSortButton onChange={setSentimentSort} order={sentimentSort} /><span>Forecast</span><span>Reaction</span><span>History</span><span>Prior context</span><span>Follow-up</span><span>Content</span></div>{displayRows.map((row) => {
      const directionValue = normalizeSemanticDirection(row.scoped_summary?.semantic_direction);
      return <article className="intelligence-feed-row sec-intelligence-grid" data-direction={directionValue} key={`${row.cik}-${row.accession_number}`} role="listitem">
        <div className="intelligence-time-block"><TemperatureTag tone={temperature(row, live ? wallClockMs : Date.parse(state.asOf || asOf))} /><SecFilingTime row={row} /></div>
        <div className="intelligence-identity-block"><TickerList presentations={presentations} tickers={row.tickers} /><small>{row.tickers.length ? row.company_name : `CIK ${row.cik}`}</small></div>
        <div className="intelligence-main-block">
          <button className="news-headline-button" onClick={() => openSecPage(row, state.queryId, live)} type="button"><strong>{row.disclosure_title || `${row.form_type} filing`}</strong><small>{row.form_type} · {row.company_name}</small></button>
          <div className="intelligence-support-line"><span>{row.sec_synthesis?.synthesis.readable_summary || (row.items.length ? `Items ${row.items.join(" · ")}` : row.accession_number)}</span></div>
        </div>
        <div className="sec-category-cell"><SecDeterministicClass row={row} /></div>
        <div className="sec-impact-cell">{row.impact_score ? <><strong>{row.impact_score}/5</strong><span>{row.impact_label || "Classified impact"}</span></> : <span>Not classified</span>}</div>
        <div className="sec-scope-cell">{humanize(row.affected_security_scope || "Not specified")}</div>
        <div className="sec-label-value">{humanize(row.sec_synthesis?.narrative_disclosures[0]?.concept || row.scoped_summary?.content_role || "Pending")}</div>
        <div className="sec-label-value">{row.sec_synthesis ? "Regulatory primary" : humanize(row.scoped_summary?.source_origin || "Pending")}</div>
        <div className="intelligence-sentiment-cell"><SecSynthesisDirection compact synthesis={row.sec_synthesis} /></div>
        <SecEligibilityCell active={secProductEligible(row.sec_synthesis, "forecast_trigger")} />
        <SecEligibilityCell active={secProductEligible(row.sec_synthesis, "reaction_study")} />
        <SecEligibilityCell active={secProductEligible(row.sec_synthesis, "issuer_history")} />
        <SecEligibilityCell active={row.scoped_summary?.prior_primary_context_eligible} />
        <SecEligibilityCell active={row.scoped_summary?.episode_followup_eligible} />
        <div className="intelligence-utility-cell"><ContentState row={row} /></div>
      </article>;
    })}</div>{!state.loading && !state.rows.length ? <SecEmpty label="No SEC filings match this query." /> : null}</div>
    {state.hasMore ? <button className="news-load-more" disabled={state.loadingMore} onClick={state.loadMore} type="button">{state.loadingMore ? <><span className="loading-spinner" aria-hidden="true" />Loading…</> : "Load older filings"}</button> : null}
  </section>;
}

export function TickerSecContainer({ asOf, live = false, onSymbolChange, settings, symbol }: { asOf: string; live?: boolean; onSymbolChange?: (symbol: string) => void; settings: { lookbackHours: number }; symbol: string }) {
  const state = useSecQuery({ asOf, filters: EMPTY_SEC_FILTERS, refreshKey: 0, search: "", settings: { content: "all", endDate: "", label: "", limit: 100, lookbackHours: settings.lookbackHours, rangeMode: "preset", startDate: "", ticker: symbol } });
  const presentations = useTickerPresentations([symbol]);
  const wallClockMs = useWallClock();
  const actionable = state.rows.filter((row) => secProductEligible(row.sec_synthesis, "forecast_trigger"));
  const context = state.rows.filter((row) => !secProductEligible(row.sec_synthesis, "forecast_trigger") && secProductEligible(row.sec_synthesis, "issuer_history"));
  const other = state.rows.filter((row) => !actionable.includes(row) && !context.includes(row));
  return <section className="ticker-news ticker-sec" aria-label={`${symbol} SEC filings`}><header><div><TickerIdentityWithChange asOf={state.asOf || asOf} className="ticker-news-symbol" inputAriaLabel="Ticker SEC symbol" logoUrl={presentations[symbol]?.logo_url} onTickerChange={onSymbolChange} ticker={symbol} /><span>Accepted disclosures</span></div><span className="sec-ticker-header-actions"><small>{state.rows.length} filings · through <MarketTime value={state.asOf || asOf} /></small><SecGuideButton compact /></span></header><SecStatus compact state={state} /><div className="ticker-news-feed"><TickerSecSection asOf={state.asOf || asOf} label="Actionable disclosures" live={live} queryId={state.queryId} rows={actionable} /><TickerSecSection asOf={state.asOf || asOf} label="Issuer history & context" live={live} queryId={state.queryId} rows={context} /><TickerSecSection asOf={state.asOf || asOf} label="Administrative & other" live={live} queryId={state.queryId} rows={other} />{!state.loading && !state.rows.length ? <SecEmpty label={`No ${symbol} filings in this window.`} /> : null}</div></section>;
}

export function TickerSecPopover({ anchor, onClose, ticker }: { anchor: TickerSecPopoverAnchor; onClose: () => void; ticker: string }) {
  const [payload, setPayload] = useState<SecPayload | null>(null); const [error, setError] = useState("");
  useEffect(() => { const controller = new AbortController(); api<SecPayloadWire>(`/api/trading/sec${query({ as_of: new Date().toISOString(), limit: 25, lookback_hours: 720, ticker })}`, { signal: controller.signal, timeoutMs: 20000 }).then((value) => setPayload(normalizeSecPayload(value))).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }); return () => controller.abort(); }, [ticker]);
  useEffect(() => { const close = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); }; window.addEventListener("keydown", close); return () => window.removeEventListener("keydown", close); }, [onClose]);
  const width = Math.min(720, Math.max(360, window.innerWidth - 24)); const left = Math.max(12, Math.min(anchor.left, window.innerWidth - width - 12)); const top = Math.min(anchor.bottom + 6, window.innerHeight - 540);
  return createPortal(<aside aria-label={`${ticker} SEC filing timeline`} className="ticker-news-popover sec-ticker-popover" style={{ left, top: Math.max(12, top), width }}>
    <header><div className="ticker-news-popover-heading"><SecIntelligenceIcon count={payload?.rows.length ?? 1} kind={payload?.rows[0] ? secKindForRow(payload.rows[0]) : "other"} recency="cold" synthesized={Boolean(payload?.rows.some((row) => row.sec_synthesis))} /><span><strong>{ticker} SEC intelligence</strong><small>Synthesis, eligibility, evidence, then source filing</small></span></div><div className="ticker-news-popover-header-actions"><button aria-label="Close SEC timeline" onClick={onClose} type="button"><X size={15} /></button></div></header>
    <div className="ticker-news-popover-feed">{error ? <SecEmpty label={error} /> : !payload ? <div className="canvas-preview-loading">Loading SEC timeline…</div> : payload.rows.length ? payload.rows.map((row) => { const synthesis = row.sec_synthesis; const kind = secKindForRow(row); const direction = secPopoverDirection(row); return <article className="ticker-news-popover-story sec-ticker-popover-story" data-direction={direction.value} key={row.accession_number}><button className="ticker-news-popover-story-open" onClick={() => openSecPage(row, payload.query_id, true)} type="button"><div className="ticker-news-popover-story-time"><SecFilingTime row={row} /></div><div className="ticker-news-popover-story-main"><SecPopoverIntelligence direction={direction} kind={kind} row={row} /><strong className="ticker-news-popover-story-title">{synthesis?.synthesis.headline || row.filing_label_text || row.disclosure_title || `${row.form_type} filing`}</strong><p className="ticker-news-popover-story-teaser">{synthesis?.synthesis.readable_summary || "SEC Synthesis is pending. Open the filing to inspect the available source documents and classification."}</p><SecPopoverSupport row={row} /><div className="sec-popover-source"><FileText aria-hidden="true" size={10} /><span>{row.form_type}</span><span>{row.disclosure_title || row.company_name}</span><span>{row.accession_number}</span></div></div><span className="ticker-news-popover-open-cue">Open <ExternalLink size={10} /></span></button></article>; }) : <SecEmpty label={`No recent ${ticker} filings.`} />}</div>
  </aside>, document.body);
}

type SecPopoverDirectionValue = ReturnType<typeof normalizeSemanticDirection> | "contextual" | "uncertain";
type SecPopoverDirection = { source: "AI review" | "SEC Synthesis" | "Direction pending"; value: SecPopoverDirectionValue };

function SecPopoverDirectionPanel({ direction }: { direction: SecPopoverDirection }) {
  const DirectionIcon = direction.value === "positive" ? ArrowUp : direction.value === "negative" ? ArrowDown : direction.value === "mixed" ? ArrowUpDown : direction.value === "neutral" ? Minus : CircleDashed;
  return <section aria-label={`${direction.source}: ${humanize(direction.value)}`} className="sec-popover-direction" data-direction={direction.value}>
    <span className="sec-popover-direction-icon"><DirectionIcon aria-hidden="true" size={16} /></span>
    <span><small>{direction.source}</small><strong>{humanize(direction.value)}</strong></span>
  </section>;
}

function SecPopoverIntelligence({ direction, kind, row }: { direction: SecPopoverDirection; kind: SecIconKind; row: SecRow }) {
  const synthesis = row.sec_synthesis;
  const eligible = synthesis ? secProductEligible(synthesis, "forecast_trigger") : null;
  return <div aria-label="SEC filing intelligence" className="sec-popover-intelligence">
    <SecPopoverDirectionPanel direction={direction} />
    <div className="sec-popover-primary-fields">
      <span className="sec-popover-primary-field" data-field="meaning"><SecIntelligenceIcon count={1} kind={kind} recency="older" synthesized={Boolean(synthesis)} /><span><small>Filing meaning</small><strong>{secIconKindLabel(kind)}</strong></span></span>
      <span className="sec-popover-primary-field" data-field="forecast" data-state={eligible === null ? "pending" : eligible ? "eligible" : "context"}><Target aria-hidden="true" size={14} /><span><small>Forecast use</small><strong>{eligible === null ? "Eligibility pending" : eligible ? "Forecast eligible" : "Context only"}</strong></span></span>
    </div>
  </div>;
}

function SecPopoverSupport({ row }: { row: SecRow }) {
  const synthesis = row.sec_synthesis;
  const concepts = [...new Set(synthesis?.narrative_disclosures.map((item) => item.concept).filter(Boolean) ?? [])];
  const reviewed = Boolean(row.sec_review?.result);
  const transitions = synthesis?.fundamental_transitions.length ?? 0;
  const flags = synthesis?.quality_flags.length ?? 0;
  if (!concepts.length && !reviewed && !transitions && !flags) return null;
  return <div aria-label="Supporting SEC evidence" className="sec-popover-support">
    {concepts.slice(0, 2).map((concept) => <span data-kind="concept" key={concept}>{humanize(concept.split(".").at(-1))}</span>)}
    {concepts.length > 2 ? <span data-kind="concept">+{concepts.length - 2} concepts</span> : null}
    {transitions ? <span data-kind="evidence">{transitions} XBRL {transitions === 1 ? "change" : "changes"}</span> : null}
    {reviewed ? <span data-kind="reviewed"><Sparkles aria-hidden="true" size={10} />AI reviewed</span> : null}
    {flags ? <span data-kind="warning"><TriangleAlert aria-hidden="true" size={10} />{flags} quality {flags === 1 ? "flag" : "flags"}</span> : null}
  </div>;
}

function TickerSecSection({ asOf, label, live, queryId, rows }: { asOf: string; label: string; live: boolean; queryId: string; rows: SecRow[] }) { return <section className="ticker-news-section"><header><strong>{label}</strong><span>{rows.length}</span></header>{rows.map((row) => { const tone = temperature(row, Date.parse(asOf)); const Icon = temperatureIcon(tone); const direction = secSynthesisDirection(row); return <article data-direction={direction} data-tone={tone} key={row.accession_number}><div className="ticker-news-marker"><Icon size={14} /></div><div className="ticker-event-time"><SecFilingTime row={row} /><TemperatureTag tone={tone} /></div><div className="ticker-event-content"><div className="ticker-news-meta"><SecSynthesisDirection synthesis={row.sec_synthesis} salient /><SecScopedRole fallback={row.filing_label_text} summary={row.scoped_summary} /><SecConcepts concepts={row.sec_synthesis?.narrative_disclosures.map((item) => item.concept)} /></div><button className="ticker-news-open" onClick={() => openSecPage(row, queryId, live)} type="button"><strong>{row.form_type} · {row.company_name}</strong><p>{row.sec_synthesis?.synthesis.readable_summary || row.disclosure_title || (row.items ?? []).join(" · ") || row.accession_number}</p></button></div></article>; })}</section>; }

export function SecDetailContainer({ asOf, canvasId, requestedCik, requestedAccession }: { asOf: string; canvasId: string; requestedCik?: string; requestedAccession?: string }) {
  const wallClockMs = useWallClock();
  const storedSelection = readSelectedSec(canvasId); const requestedKey = requestedCik && requestedAccession ? `${requestedCik}/${requestedAccession}` : ""; const initial = requestedKey ? storedSelection.key === requestedKey ? storedSelection : { acceptedAt: "", key: requestedKey, queryId: "" } : storedSelection;
  const [selection, setSelection] = useState<SecSelection>(initial); const key = selection.key; const [detail, setDetail] = useState<SecDetail | null>(null); const [loading, setLoading] = useState(false); const [error, setError] = useState(""); const [documentId, setDocumentId] = useState("");
  const [textView, setTextView] = useState<TextView>("rendered");
  const [textPage, setTextPage] = useState<SecTextPage | null>(null); const [textLoading, setTextLoading] = useState(false); const [textError, setTextError] = useState("");
  const [facts, setFacts] = useState<SecFact[]>([]); const [factsLoading, setFactsLoading] = useState(false);
  useEffect(() => { const listener = (event: Event) => { const value = (event as CustomEvent<SecSelection & { canvasId: string }>).detail; if (value.canvasId === canvasId) setSelection({ acceptedAt: value.acceptedAt, key: value.key, queryId: value.queryId }); }; window.addEventListener(SEC_SELECTION_EVENT, listener); return () => window.removeEventListener(SEC_SELECTION_EVENT, listener); }, [canvasId]);
  useEffect(() => { if (!key) return; const [cik, accession] = key.split("/"); const controller = new AbortController(); setLoading(true); setError(""); setTextPage(null); api<SecDetailWire>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}${query({ accepted_at: selection.acceptedAt || undefined, as_of: asOf, query_id: selection.queryId || undefined })}`, { signal: controller.signal, timeoutMs: 20000 }).then((value) => { const normalized = normalizeSecDetail(value); const preferred = normalized.texts[0]?.document_id ?? normalized.originals[0]?.document_id ?? normalized.documents[0]?.document_id ?? ""; setDetail(normalized); setFacts(normalized.facts); setDocumentId(preferred); setTextView(normalized.texts.some((text) => text.document_id === preferred) ? "rendered" : "original"); }).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => { if (!controller.signal.aborted) setLoading(false); }); return () => controller.abort(); }, [asOf, key, selection.acceptedAt, selection.queryId]);
  const loadTextPage = useCallback((offset: number) => { if (!key || !documentId) return; const [cik, accession] = key.split("/"); setTextLoading(true); setTextError(""); api<SecTextPage>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/text/${encodeURIComponent(documentId)}${query({ as_of: asOf, limit: 32000, offset, view: textView })}`, { timeoutMs: 15000 }).then(setTextPage).catch((reason) => { setTextPage(null); setTextError(reason instanceof Error ? reason.message : String(reason)); }).finally(() => setTextLoading(false)); }, [asOf, documentId, key, textView]);
  useEffect(() => { setTextPage(null); setTextError(""); const collection = textView === "rendered" ? detail?.texts : detail?.originals; if (collection?.some((text) => text.document_id === documentId)) loadTextPage(0); }, [detail?.originals, detail?.texts, documentId, loadTextPage, textView]);
  const loadMoreFacts = useCallback(() => { if (!key || !detail?.facts_has_more) return; const [cik, accession] = key.split("/"); setFactsLoading(true); api<SecFactsPage>(`/api/trading/sec/detail/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/facts${query({ as_of: asOf, limit: 100, offset: facts.length })}`, { timeoutMs: 15000 }).then((page) => { setFacts((current) => [...current, ...page.rows]); setDetail((current) => current ? { ...current, facts_has_more: page.has_more, facts_next_offset: page.next_offset, facts_total: page.row_count } : current); }).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setFactsLoading(false)); }, [asOf, detail?.facts_has_more, facts.length, key]);
  const detailTickers = detail?.identity.tickers ?? detail?.filing.tickers ?? []; const presentations = useTickerPresentations(detailTickers);
  if (!key) return <SecEmpty label="Choose a filing in All SEC or Ticker SEC to read it here." />; if (loading && !detail) return <div className="canvas-preview-loading">Loading filing…</div>; if (error && !detail) return <SecEmpty label={error} />; if (!detail) return null;
  const row = detail.filing; const tickers = detailTickers; const selectedDocument = detail.documents.find((document) => document.document_id === documentId); const renderedText = detail.texts.find((text) => text.document_id === documentId); const originalText = detail.originals.find((text) => text.document_id === documentId); const tone = temperature(row, Date.parse(asOf) || wallClockMs);
  const chooseDocument = (nextDocumentId: string) => { setDocumentId(nextDocumentId); setTextView(detail.texts.some((text) => text.document_id === nextDocumentId) ? "rendered" : "original"); };
  return <article className="news-reader sec-reader"><header className="news-reader-hero"><div className="news-reader-kicker"><TemperatureTag tone={tone} /><SecFilingTime row={row} /><SecLabel label={row.filing_label_text} /><span>{row.form_type}</span>{row.impact_score ? <ImpactBadge label={row.impact_label} score={row.impact_score} /> : null}<SecGuideButton compact /></div><div className="news-reader-title-row"><h1>{row.sec_synthesis?.synthesis.headline || row.disclosure_title || row.company_name}</h1>{row.sec_synthesis ? <div className="news-reader-primary-direction"><SecSynthesisDirection prominent synthesis={row.sec_synthesis} /></div> : null}</div>{row.sec_synthesis?.synthesis.readable_summary ? <p className="sec-reader-company sec-synthesis-summary">{row.sec_synthesis.synthesis.readable_summary}</p> : row.disclosure_title ? <p className="sec-reader-company">{row.company_name}</p> : null}<div className="news-reader-byline"><span>CIK {row.cik} · {row.accession_number}</span><TickerList presentations={presentations} tickers={tickers} /></div>{row.sec_synthesis ? <SecSynthesisOverview synthesis={row.sec_synthesis} /> : <div className="news-label-pending">{detail.intelligence_status === "unavailable" ? "SEC Synthesis temporarily unavailable" : "SEC Synthesis pending"}</div>}{row.items?.length ? <div className="news-reader-tags" aria-label="Filing items">{row.items.map((item) => <span key={item}>Item {item}</span>)}</div> : null}</header>
    <div className="sec-intelligence-cards"><SecSynthesisCard synthesis={row.sec_synthesis} /><SecAiReviewCard cik={row.cik} initial={row.sec_review} accession={row.accession_number} onChange={(review) => setDetail((current) => current ? { ...current, filing: { ...current.filing, sec_review: review } } : current)} /></div>
    {row.sec_synthesis ? <SecSynthesisPanel synthesis={row.sec_synthesis} /> : null}
    <details className="sec-compatibility-intelligence"><summary>Legacy document interpretation</summary><SecIntelligenceDetail presentations={presentations} row={row} status={detail.intelligence_status} /></details>
    {(row.label_evidence?.length || row.impact_rationale) ? <details className="news-classification-evidence sec-classification-evidence"><summary><span><strong>Classification evidence</strong><small>Approved filing taxonomy and impact rationale</small></span></summary><div>{row.label_evidence?.length ? <p>{row.label_evidence.join(" · ")}</p> : null}{row.impact_rationale ? <p>{row.impact_rationale}</p> : null}</div></details> : null}
    <section className="sec-document-inventory"><div className="sec-reader-section-title"><div><strong>Filing documents</strong><small>{detail.documents.length} source documents · {detail.texts.length} rendered · {detail.originals.length} original</small></div></div><div>{detail.documents.map((document) => { const hasRendered = detail.texts.some((text) => text.document_id === document.document_id); const hasOriginal = detail.originals.some((text) => text.document_id === document.document_id); return <div className="sec-document-choice" key={document.document_id}><button data-active={document.document_id === documentId ? "true" : "false"} onClick={() => chooseDocument(document.document_id)} type="button"><FileText size={14} /><span><strong>{document.document_name}</strong><small>{document.document_type} · {document.document_role}</small><em>{hasRendered ? "Rendered" : ""}{hasRendered && hasOriginal ? " · " : ""}{hasOriginal ? "Original" : ""}</em></span></button>{safeUrl(document.document_url) ? <a aria-label={`Open ${document.document_name} on SEC.gov`} href={document.document_url} rel="noreferrer" target="_blank"><ExternalLink size={12} /></a> : null}</div>; })}</div></section>
    {(renderedText || originalText) ? <section className="news-reader-body sec-reader-body"><div className="sec-text-view-header"><div className="sec-text-tabs" role="tablist"><button aria-selected={textView === "rendered"} disabled={!renderedText} onClick={() => setTextView("rendered")} role="tab" type="button">Rendered text<small>{renderedText ? formatCount(renderedText.text_char_count) : "Unavailable"}</small></button><button aria-selected={textView === "original"} disabled={!originalText} onClick={() => setTextView("original")} role="tab" type="button">Original<small>{originalText ? formatCount(originalText.text_char_count) : "Unavailable"}</small></button></div>{textPage ? <span>{formatCount(textPage.offset + 1)}–{formatCount(textPage.next_offset)} of {formatCount(textPage.text_char_count)}</span> : null}</div><p className="sec-text-view-note">{textView === "rendered" ? "Readable extraction, selected by default. Tables and filing structure may be normalized for clarity." : "Canonical source payload retained by the SEC pipeline. Markup and source formatting are shown as stored."}</p>{textLoading ? <div className="canvas-preview-loading">Loading text page…</div> : textError ? <SecEmpty label={textError} /> : textPage ? <>{textView === "rendered" ? paragraphs(textPage.text).map((paragraph, index) => <p key={`${textPage.offset}-${index}-${paragraph.slice(0, 18)}`}>{paragraph}</p>) : <pre className="sec-original-text">{textPage.text}</pre>}<div className="sec-page-controls"><button disabled={textPage.offset === 0 || textLoading} onClick={() => loadTextPage(Math.max(0, textPage.offset - textPage.limit))} type="button">Previous page</button><button disabled={!textPage.has_more || textLoading} onClick={() => loadTextPage(textPage.next_offset)} type="button">Next page</button></div></> : null}</section> : <SecEmpty label="No rendered or original text is available for the selected document." />}
    <SecDetailInformation detail={detail} document={selectedDocument} original={originalText} rendered={renderedText} />
    <section className="sec-facts"><div className="sec-reader-section-title"><div><strong>XBRL facts</strong><small>{formatCount(detail.facts_total)} filing-linked facts · {formatCount(facts.length)} loaded</small></div></div>{facts.length ? <div className="news-table-wrap"><table className="news-table"><thead><tr><th>Concept</th><th>Value</th><th>Period</th></tr></thead><tbody>{facts.map((fact, index) => <tr key={`${fact.tag}-${index}`}><td className={tableCellClass("concept")}>{fact.tag}</td><td className={tableCellClass("value", { presentationValueType: secFactPresentation(fact) })}><PresentedValue column="value" presentation={{ presentationValueType: secFactPresentation(fact) }} value={fact.value} /> <small>{fact.unit_code}</small></td><td className={tableCellClass("period_end_date")}><PresentedValue column="period_end_date" value={fact.fiscal_period || fact.period_end_date} /></td></tr>)}</tbody></table></div> : <SecEmpty label="This filing has no linked XBRL facts." />}{detail.facts_has_more ? <button className="news-load-more" disabled={factsLoading} onClick={loadMoreFacts} type="button">{factsLoading ? <><span className="loading-spinner" aria-hidden="true" />Loading…</> : "Load more facts"}</button> : null}</section>
    <footer>{safeUrl(row.filing_detail_url) ? <a href={row.filing_detail_url} rel="noreferrer" target="_blank">Open filing on SEC.gov <ExternalLink size={12} /></a> : null}</footer></article>;
}

function useSecQuery({ asOf, filters, refreshKey, search, settings }: { asOf: string; filters: SecColumnFilters; refreshKey: number; search: string; settings: SecSettings }) {
  const [payload, setPayload] = useState<SecPayload | null>(null); const [rows, setRows] = useState<SecRow[]>([]); const [loading, setLoading] = useState(true); const [loadingMore, setLoadingMore] = useState(false); const [error, setError] = useState(""); const queryIdRef = useRef("");
  const load = useCallback(async (before = "", beforeAccession = "", pageAsOf = "") => {
    const customReady = settings.rangeMode === "custom" && settings.startDate && settings.endDate;
    const response = await api<SecPayloadWire>(`/api/trading/sec${query({ as_of: pageAsOf || asOf, before: before || undefined, before_accession: beforeAccession || undefined, content: settings.content, direction: filters.direction || undefined, end_date: customReady ? settings.endDate : undefined, followup_eligible: filters.eligibility.followup || undefined, forecast_eligible: filters.eligibility.forecast || undefined, history_eligible: filters.eligibility.history || undefined, impact: filters.impact || undefined, label: settings.label || undefined, label_state: filters.labelState || undefined, limit: settings.limit, lookback_hours: settings.lookbackHours, origin: filters.origin || undefined, prior_context_eligible: filters.eligibility.prior || undefined, query_id: before ? queryIdRef.current : undefined, reaction_eligible: filters.eligibility.reaction || undefined, role: filters.role || undefined, search: search || undefined, security_scope: filters.scope || undefined, start_date: customReady ? settings.startDate : undefined, ticker: settings.ticker || undefined })}`, { timeoutMs: 30000 });
    const next = normalizeSecPayload(response); queryIdRef.current = next.query_id; setPayload(next); setRows((current) => before ? [...current, ...next.rows.filter((row) => !current.some((item) => item.accession_number === row.accession_number))] : next.rows); setError("");
  }, [asOf, filters.direction, filters.eligibility.followup, filters.eligibility.forecast, filters.eligibility.history, filters.eligibility.prior, filters.eligibility.reaction, filters.impact, filters.labelState, filters.origin, filters.role, filters.scope, search, settings.content, settings.endDate, settings.label, settings.limit, settings.lookbackHours, settings.rangeMode, settings.startDate, settings.ticker]);
  useEffect(() => { setLoading(true); load().catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoading(false)); }, [load, refreshKey]);
  const loadMore = useCallback(() => { if (!payload?.next_before) return; setLoadingMore(true); load(payload.next_before, payload.next_before_accession, payload.as_of).catch((reason) => setError(reason instanceof Error ? reason.message : String(reason))).finally(() => setLoadingMore(false)); }, [load, payload]);
  return { asOf: payload?.as_of ?? asOf, error, hasMore: Boolean(payload?.has_more), labels: payload?.labels ?? [], loadMore, loading, loadingMore, queryId: payload?.query_id ?? "", rows, windowStart: payload?.window_start ?? "" };
}

function normalizeSecPayload(value: SecPayloadWire): SecPayload {
  return { ...value, rows: Array.isArray(value.rows) ? value.rows.map(normalizeSecRow) : [] };
}

function normalizeSecDetail(value: SecDetailWire): SecDetail {
  return { ...value, documents: value.documents ?? [], entities: value.entities ?? [], facts: value.facts ?? [], filing: normalizeSecRow(value.filing), originals: value.originals ?? [], texts: value.texts ?? [] };
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
function SecDirection({ compact = false, prominent = false, salient = false, summary }: { compact?: boolean; prominent?: boolean; salient?: boolean; summary?: SecScopedSummary | null }) { return <SemanticDirectionMetric compact={compact} direction={summary?.semantic_direction} prominent={prominent || salient} score={summary?.semantic_score} />; }
function SecSynthesisDirection({ compact = false, prominent = false, salient = false, synthesis }: { compact?: boolean; prominent?: boolean; salient?: boolean; synthesis?: SecSynthesis | null }) { const direction = synthesis?.synthesis.composite_sentiment; return <SemanticDirectionMetric compact={compact} direction={direction} prominent={prominent || salient} score={synthesisSortScore(synthesis)} />; }
function SecScopedRole({ fallback, summary }: { fallback: string; summary?: SecScopedSummary | null }) { return <span className="news-scoped-class" data-state={summary?.forecast_trigger_eligible ? "event" : summary ? "context" : "pending"}>{humanize(summary?.content_role || fallback || "Unclassified")}</span>; }
function SecConcepts({ concepts = [] }: { concepts?: string[] }) { if (!concepts.length) return <span className="news-scoped-concepts">—</span>; return <span className="news-scoped-concepts"><span>{humanize(concepts[0].split(".").at(-1))}</span>{concepts.length > 1 ? <span>+{concepts.length - 1}</span> : null}</span>; }
function SecEligibilityCell({ active }: { active?: boolean }) { return <span aria-label={active ? "Eligible" : "Not eligible"} className="eligibility-column-value" data-active={active ? "true" : "false"} title={active ? "Eligible" : "Not eligible"}>{active ? <Check aria-hidden="true" size={12} strokeWidth={2.4} /> : "—"}</span>; }
function SecDateRangeFilters({ onSettingsChange, settings }: { onSettingsChange: (patch: Partial<SecSettings>) => void; settings: SecSettings }) { return <><label><span>From</span><input aria-label="SEC range start date" onChange={(event) => onSettingsChange({ startDate: event.target.value })} type="date" value={settings.startDate} /></label><label><span>Through</span><input aria-label="SEC range end date" onChange={(event) => onSettingsChange({ endDate: event.target.value })} type="date" value={settings.endDate} /></label></>; }
function SecEligibilityFilters({ filters, onChange }: { filters: SecEligibilityQuery; onChange: (next: SecEligibilityQuery) => void }) { const fields: { key: keyof SecEligibilityQuery; label: string }[] = [{ key: "forecast", label: "Forecast" }, { key: "reaction", label: "Reaction" }, { key: "history", label: "History" }, { key: "prior", label: "Prior context" }, { key: "followup", label: "Follow-up" }]; return <>{fields.map(({ key, label }) => <InventoryFilterSelect ariaLabel={`SEC ${label} eligibility`} key={key} onChange={(value) => onChange({ ...filters, [key]: value })} options={inventoryEligibilityOptions(label)} value={filters[key]} />)}</>; }
function SecStatus({ compact, inline, state }: { compact?: boolean; inline?: boolean; state: ReturnType<typeof useSecQuery> }) { return <div className="news-status" data-compact={compact ? "true" : "false"} data-inline={inline ? "true" : "false"}>{state.loading ? <span><i className="loading-spinner" aria-hidden="true" />Querying filings…</span> : state.error ? <strong>{state.error}</strong> : inline ? <span title={`${state.rows.length} loaded and displayed`}>{state.rows.length} / {state.rows.length}</span> : <><span>{state.rows.length} returned</span>{!compact && state.windowStart ? <span className="news-window-start"><span>Since</span><MarketTime dateStyle="short" includeDate layout="inline" value={state.windowStart} /></span> : null}<span className="news-source-label">Point-in-time</span></>}</div>; }
function SecEmpty({ label }: { label: string }) { return <div className="news-empty"><BookOpen size={18} /><span>{label}</span></div>; }
function SecLabel({ label }: { label: string }) { return <span className="sec-label"><FileCheck2 size={11} />{label}</span>; }
function SecDeterministicClass({ row }: { row: SecRow }) { return <span className="sec-deterministic-class" title={row.taxonomy_version ? `Approved taxonomy ${row.taxonomy_version}` : "Approved SEC form taxonomy"}><FileCheck2 size={13} /><strong>{row.filing_label_text || "Other disclosure"}</strong></span>; }
function ImpactBadge({ label, score }: { label?: string; score: number }) { const tone = score >= 4 ? "high" : score >= 2 ? "medium" : "low"; return <span className="sec-impact-badge" data-impact={tone}>Impact {score}/5{label ? ` · ${label}` : ""}</span>; }
function SecFilingTime({ row }: { row: SecRow }) { if (row.event_time_quality === "date_only") return <span className="sec-date-only" title="The SEC source published a filing date but no acceptance time."><strong>{formatDateOnly(row.accepted_at_utc)}</strong><small>Time unresolved</small></span>; return <MarketTime className="news-row-time" dateStyle="short" includeDate value={row.accepted_at_utc} />; }
function ContentState({ row }: { row: SecRow }) { return <span className="sec-content-state"><b>{row.text_rows ? "Text" : "Metadata"}</b>{row.xbrl_rows ? <b>XBRL</b> : null}<small>{row.document_rows ?? 0} docs</small></span>; }
function TickerList({ presentations, tickers = [] }: { presentations: ReturnType<typeof useTickerPresentations>; tickers?: string[] }) { return <span className="news-tickers">{tickers.slice(0, 3).map((ticker) => <SecurityIdentityCell companyName={presentations[ticker]?.issuer_name} country={presentations[ticker]?.country} halted={presentations[ticker]?.market_is_halted ?? presentations[ticker]?.trading_status} key={ticker} logoUrl={presentations[ticker]?.logo_url} newsRecency={presentations[ticker]?.live_news_recency} onTickerSelect={openTickerChartsQuotes} secCount={presentations[ticker]?.sec_count} secLabels={presentations[ticker]?.sec_labels} secRecency={presentations[ticker]?.sec_recency} secReviewDirection={presentations[ticker]?.sec_review_fundamental_direction} secReviewStatus={presentations[ticker]?.sec_review_status} secSynthesisCount={presentations[ticker]?.sec_synthesis_count} secSynthesisDirection={presentations[ticker]?.sec_synthesis_direction} ticker={ticker} />)}{tickers.length > 3 ? <b>+{tickers.length - 3}</b> : !tickers.length ? "—" : null}</span>; }
function secFactPresentation(fact: SecFact): PresentationValueType { if (!Number.isFinite(Number(fact.value))) return "text"; const unit = fact.unit_code.toUpperCase(); if (unit === "USD") return "money"; if (unit.includes("SHARE")) return "quantity"; if (unit === "PURE") return "ratio"; return "ratio"; }
function TemperatureTag({ tone }: { tone: Temperature }) { const Icon = temperatureIcon(tone); const label = tone === "dated" ? "Date only" : tone[0].toUpperCase() + tone.slice(1); return <span className="news-temperature" data-tone={tone}><Icon size={tone === "hot" ? 16 : 15} strokeWidth={tone === "hot" ? 1.5 : tone === "cold" ? 1.8 : 1.7} /><em>{label}</em></span>; }

function SecGuideButton({ compact }: { compact?: boolean }) {
  const [open, setOpen] = useState(false);
  return <><button className="sec-guide-button" data-compact={compact ? "true" : "false"} onClick={() => setOpen(true)} type="button"><BookOpen size={12} /> Guide</button>{open ? <Modal className="sec-guide-modal" onClose={() => setOpen(false)} title="How to read SEC filings"><div className="sec-guide-content"><section><strong>Disclosure label</strong><p>The label describes what the filing is about. It comes from the approved SEC form taxonomy; forms without an approved mapping remain explicitly labeled Other disclosure. It is independent of recency and market direction.</p></section><section><strong>Hot, cold, and old</strong><p><b data-tone="hot">Hot</b> means accepted within four hours of the workspace clock. <b data-tone="cold">Cold</b> means four to 24 hours. <b data-tone="old">Old</b> means more than 24 hours. <b>Date only</b> is used when the SEC source did not publish an acceptance time, so the UI does not invent precise recency.</p></section><section><strong>Impact score</strong><p>Impact 1–5 estimates disclosure magnitude and security scope. It is not a bullish or bearish forecast. Read the classification rationale before using it as a filter.</p></section><section><strong>Document views</strong><p>Rendered text is the readable normalized extraction and opens by default. Original shows the canonical source payload retained by the SEC pipeline, including markup. XBRL facts are structured filing values and remain a separate section.</p></section></div></Modal> : null}</>;
}

function SecDetailOverview({ row, summary }: { row: SecRow; summary: SecScopedSummary }) {
  return <section className="detail-intelligence-overview" aria-label="Filing interpretation summary">
    <div className="detail-direction-focus"><span>Text direction</span><SecDirection prominent summary={summary} /></div>
    <SecDetailDatum label="Disclosure role" value={humanize(summary.content_role || row.filing_label_text)} />
    <SecDetailDatum label="Source origin" value={humanize(summary.source_origin)} />
    <SecDetailDatum label="Primary event" value={humanize(summary.event_concepts[0]?.split(".").at(-1) || row.filing_label_text)} />
    <SecDetailDatum label="Operational use" value={secEligibilityText(summary)} />
  </section>;
}

function SecSynthesisOverview({ synthesis }: { synthesis: SecSynthesis }) {
  const comparable = synthesis.fundamental_transitions.filter((item) => item.comparability === "comparable").length;
  const forecast = synthesis.eligibility.find((item) => item.product === "forecast_trigger");
  return <section className="detail-intelligence-overview" aria-label="SEC Synthesis summary"><div className="detail-direction-focus"><span>Economic implication</span><SecSynthesisDirection prominent synthesis={synthesis} /></div><SecDetailDatum label="Forecast label" value={forecast?.eligible ? "Eligible" : `Not eligible${forecast?.blocking_flags.length ? ` · ${forecast.blocking_flags.map(humanize).join(" · ")}` : ""}`} /><SecDetailDatum label="Narrative disclosures" value={String(synthesis.narrative_disclosures.length)} /><SecDetailDatum label="XBRL transitions" value={`${comparable} comparable · ${synthesis.fundamental_transitions.length} total`} /><SecDetailDatum label="Reconciliation" value={synthesis.reconciliation.some((item) => item.state === "contradiction") ? "Conflicts flagged" : "No conflict detected"} /></section>;
}

function SecSynthesisCard({ synthesis }: { synthesis?: SecSynthesis | null }) {
  const forecast = synthesis?.eligibility.find((item) => item.product === "forecast_trigger");
  return <section className="news-intelligence-card news-synthesis-card" data-state={synthesis ? "ready" : "pending"}><header><span>SEC synthesis</span><b data-tone="view">Deterministic</b></header>{synthesis ? <><div className="news-card-primary"><strong>{humanize(synthesis.narrative_disclosures[0]?.concept || "Filing context")}</strong><span data-tone={normalizeSemanticDirection(synthesis.synthesis.composite_sentiment)}>{humanize(synthesis.synthesis.composite_sentiment)}</span></div><div className="news-card-metrics"><span data-tone={forecast?.eligible ? "positive" : "neutral"}>Forecast {forecast?.eligible ? "eligible" : "not eligible"}</span><span>{synthesis.narrative_disclosures.length} disclosures</span><span>{synthesis.fundamental_transitions.length} transitions</span></div></> : <span className="news-card-empty">Pending</span>}</section>;
}

function SecAiReviewCard({ accession, cik, initial, onChange }: { accession: string; cik: string; initial?: SecReview; onChange: (review: SecReview) => void }) {
  const [review, setReview] = useState<SecReview>(initial ?? { status: "not_reviewed" }); const [error, setError] = useState("");
  useEffect(() => setReview(initial ?? { status: "not_reviewed" }), [initial]);
  const pending = ["queued", "reviewing"].includes(review.status);
  useEffect(() => { if (!pending) return; const poll = window.setInterval(() => { api<{ review?: SecReview }>(`/api/trading/sec/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/ai-review`, { timeoutMs: 10000 }).then((value) => { const next = value.review ?? { status: "not_reviewed" }; setReview(next); onChange(next); }).catch(() => undefined); }, 2200); return () => window.clearInterval(poll); }, [accession, cik, onChange, pending]);
  const applyReview = (next: SecReview) => { setReview(next); onChange(next); };
  const readCanonicalReview = async () => { const value = await api<{ review?: SecReview }>(`/api/trading/sec/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/ai-review`, { timeoutMs: 10000 }); return value.review ?? { status: "not_reviewed" }; };
  const requestReview = async () => {
    setError(""); const queued = { ...review, status: "queued" }; applyReview(queued);
    try {
      await api(`/api/trading/sec/${encodeURIComponent(cik)}/${encodeURIComponent(accession)}/ai-review`, { method: "POST", body: JSON.stringify({ requested_by: "frontend-operator" }), timeoutMs: 35000 });
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : String(reason);
      const status = Number((reason as { status?: number })?.status || 0);
      const ambiguous = !status || /timed out|network|aborted|fetch/i.test(message);
      if (ambiguous) {
        setError("Review acknowledgement was delayed; checking canonical status…");
        for (let attempt = 0; attempt < 5; attempt += 1) {
          await new Promise((resolve) => window.setTimeout(resolve, 1500));
          const canonical = await readCanonicalReview().catch(() => null);
          if (canonical && canonical.status !== "not_reviewed") { setError(""); applyReview(canonical); return; }
        }
      }
      const detail = ambiguous ? "The review request could not be confirmed. Retry is safe because review admission is deduplicated." : message;
      setError(detail); applyReview({ ...review, status: "failed", error: detail });
    }
  };
  const result = review.result;
  return <section className="news-intelligence-card news-ai-card" data-state={review.status}><header><span>AI review</span><b data-tone={result ? "candidate" : pending ? "pending" : "view"}>Manual remote</b></header>{result ? <><div className="news-card-primary"><strong>{humanize(result.fundamental_direction)}</strong><em>{Math.round(result.forecast_relevance_probability * 100)}% relevant</em><span data-tone={normalizeSemanticDirection(result.fundamental_direction)}>{Math.round(result.materiality_probability * 100)}% material</span></div><div className="news-card-metrics"><span>Positive {Math.round(result.positive_implication_probability * 100)}%</span><span>Negative {Math.round(result.negative_implication_probability * 100)}%</span><span>{humanize(result.risk_change)} risk</span></div></> : <button className="news-card-action" disabled={pending} onClick={() => void requestReview()} type="button"><Bot size={12} />{pending ? "Reviewing filing" : review.status === "failed" ? "Retry SEC review" : "Review SEC filing"}</button>}{error || review.error ? <small className="news-ai-review-error">{error || review.error}</small> : null}</section>;
}

function SecSynthesisPanel({ synthesis }: { synthesis: SecSynthesis }) {
  return <details className="news-detail-contract sec-synthesis-panel" open><summary><span><strong>SEC synthesis</strong><small>Narrative evidence, fundamental transitions, and cross-source reconciliation</small></span><em>{synthesis.contract_version}</em></summary><div className="news-detail-contract-body"><section><header>Material narrative disclosures</header>{synthesis.narrative_disclosures.length ? synthesis.narrative_disclosures.map((item) => <article className="sec-synthesis-disclosure" data-direction={normalizeSemanticDirection(item.economic_direction)} key={item.disclosure_id}><header><strong>{humanize(item.concept)}</strong><span>{humanize(item.economic_direction)} · {humanize(item.epistemic_status)} · {humanize(item.time_relation)}</span></header><p>{item.title}</p>{item.evidence.map((evidence) => <details key={evidence.evidence_id}><summary>Exact filing evidence</summary><blockquote>{evidence.quote}</blockquote><small>{evidence.document_id} · characters {evidence.start}–{evidence.end}</small></details>)}</article>) : <SecEmpty label="No material narrative disclosure was detected." />}</section><section><header>Fundamental transitions</header>{synthesis.fundamental_transitions.length ? <div className="sec-transition-grid">{synthesis.fundamental_transitions.map((item) => <article data-direction={normalizeSemanticDirection(item.economic_direction)} key={item.transition_id}><header><strong>{humanize(item.concept_family)}</strong><span>{humanize(item.materiality)}</span></header><b>{item.percent_change == null ? "Unresolved" : `${item.percent_change > 0 ? "+" : ""}${item.percent_change.toFixed(1)}%`}</b><p>{item.prior_period_end || "No prior"} → {item.current_period_end || "Current"} · {item.unit_code}</p><small>{humanize(item.comparability)}</small></article>)}</div> : <SecEmpty label="No filing-linked XBRL transitions are available." />}</section><section><header>Reconciliation</header><div className="sec-reconciliation-grid">{synthesis.reconciliation.map((item) => <span data-state={item.state} key={item.reconciliation_id}><strong>{humanize(item.concept_family)}</strong><small>{humanize(item.state)}</small></span>)}</div></section></div></details>;
}

function SecDetailDatum({ label, value }: { label: string; value: string }) { return <span className="detail-intelligence-datum"><small>{label}</small><strong>{value}</strong></span>; }

function SecIntelligenceDetail({ presentations, row, status }: { presentations: Record<string, TickerPresentation>; row: SecRow; status?: "ready" | "unavailable" }) {
  const labels = row.scoped_labels ?? [];
  if (!labels.length) return <div className="news-label-pending">{status === "unavailable" ? "Filing intelligence temporarily unavailable" : "Filing intelligence pending"}</div>;
  const ordered = [...labels].sort((left, right) => secInterpretationPriority(right) - secInterpretationPriority(left));
  const primary = ordered.slice(0, 6);
  const additional = ordered.slice(primary.length);
  return <section className="news-reader-intelligence sec-reader-intelligence" aria-label="Filing intelligence">
    <header><div><strong>Issuer interpretations</strong><small>Material directions first; supporting filing passages remain available below.</small></div><span>{labels.length} {labels.length === 1 ? "issuer view" : "issuer views"}</span></header>
    {primary.map((label, index) => <SecScopedLabelPanel key={`${label.ticker}-primary-${index}`} label={label} presentations={presentations} row={row} />)}
    {additional.length ? <details className="detail-additional-interpretations"><summary>Show {additional.length} additional context {additional.length === 1 ? "interpretation" : "interpretations"}</summary><div>{additional.map((label, index) => <SecScopedLabelPanel key={`${label.ticker}-additional-${index}`} label={label} presentations={presentations} row={row} />)}</div></details> : null}
  </section>;
}

function SecScopedLabelPanel({ label, presentations, row }: { label: SecScopedLabel; presentations: Record<string, TickerPresentation>; row: SecRow }) {
  const summary: SecScopedSummary = { content_role: label.content_role, episode_followup_eligible: label.episode_followup_eligible, event_concepts: label.event_concepts, forecast_trigger_eligible: label.forecast_trigger_eligible, issuer_history_context_eligible: label.issuer_history_context_eligible, prior_primary_context_eligible: label.prior_primary_context_eligible, reaction_evaluation_eligible: label.reaction_evaluation_eligible, semantic_direction: label.semantic_direction, semantic_score: label.semantic_score, source_origin: label.source_origin };
  return <article className="news-scoped-label">
    <header><div className="news-scoped-label-identity">{label.ticker ? <TickerIdentity logoUrl={presentations[label.ticker]?.logo_url} ticker={label.ticker} /> : <strong>Filing-wide</strong>}<SecScopedRole fallback={row.filing_label_text} summary={summary} /></div><SecDirection prominent summary={summary} /></header>
    <div className="news-scoped-label-facts"><SecDetailDatum label="Issuer relationship" value={humanize(label.issuer_relationship || label.issuer_role)} /><SecDetailDatum label="Evidence scope" value={humanize(label.evidence_scope)} /><SecDetailDatum label="Origin" value={humanize(label.source_origin)} /><SecDetailDatum label="Timing" value={humanize(label.time_orientation)} /></div>
    {label.event_concepts.length ? <div className="detail-concept-row"><small>Event concepts</small><SecConcepts concepts={label.event_concepts} /></div> : null}
    {label.semantic_evidence_text ? <details><summary>Read direction evidence</summary><p>{label.semantic_evidence_text}</p></details> : null}
    {label.quality_flags?.length ? <div className="detail-quality-flags">{label.quality_flags.map((flag) => <span key={flag}>{humanize(flag)}</span>)}</div> : null}
    <footer><span className="detail-eligibility-text">{secEligibilityText(summary)}</span></footer>
  </article>;
}

function secInterpretationPriority(label: SecScopedLabel): number {
  const directional = ["positive", "negative", "mixed", "upside", "downside"].includes(label.semantic_direction?.toLowerCase());
  return (directional ? 8 : 0)
    + (label.forecast_trigger_eligible ? 4 : 0)
    + (label.reaction_evaluation_eligible ? 2 : 0)
    + (label.event_concepts.length ? 1 : 0)
    + Math.abs(label.semantic_score ?? 0);
}

function secEligibilityText(summary: Pick<SecScopedSummary, "forecast_trigger_eligible" | "issuer_history_context_eligible" | "reaction_evaluation_eligible">): string { const uses = [summary.forecast_trigger_eligible ? "Forecast" : "", summary.reaction_evaluation_eligible ? "Reaction study" : "", summary.issuer_history_context_eligible ? "Issuer history" : ""].filter(Boolean); return uses.length ? uses.join(" · ") : "Context only"; }

function SecDetailInformation({ detail, document, original, rendered }: { detail: SecDetail; document?: SecDocument; original?: SecText; rendered?: SecText }) {
  const row = detail.filing;
  const flags = rendered?.quality_flags ?? [];
  return <section className="sec-detail-information" aria-label="Filing and document information"><details open><summary><span><strong>Filing information</strong><small>Dates, entities, classification, and source coverage</small></span></summary><div className="sec-information-grid"><InfoItem label="Filed" value={row.filing_date || "Not reported"} /><InfoItem label="Report period" value={row.report_date || "Not reported"} /><InfoItem label="Acceptance source" value={humanize(row.accepted_at_source)} /><InfoItem label="Filing size" value={formatBytes(row.filing_size)} /><InfoItem label="Primary document" value={row.primary_document || "Not reported"} /><InfoItem label="Taxonomy" value={row.taxonomy_version || "Fallback classification"} /></div>{detail.entities.length ? <div className="sec-entity-list">{detail.entities.map((entity) => <article key={`${entity.entity_role}-${entity.entity_cik}`}><span>{humanize(entity.entity_role)}</span><strong>{entity.entity_name || `CIK ${entity.entity_cik}`}</strong><small>{entity.entity_name ? `CIK ${entity.entity_cik}` : "Name not published"}</small></article>)}</div> : null}</details><details><summary><span><strong>Selected document information</strong><small>Format, extraction quality, revision, and provenance</small></span></summary>{document ? <><div className="sec-information-grid"><InfoItem label="Role" value={humanize(document.document_role)} /><InfoItem label="Type / format" value={[document.document_type, document.content_format].filter(Boolean).join(" · ")} /><InfoItem label="MIME type" value={document.mime_type || original?.mime_type || "Not reported"} /><InfoItem label="Source size" value={formatBytes(document.byte_size ?? original?.text_byte_count)} /><InfoItem label="Extraction" value={document.extraction_status || "Not reported"} /><InfoItem label="Renderer" value={rendered?.extraction_method || "No rendered view"} /><InfoItem label="Source revision" value={humanize(document.source_revision_kind || original?.source_revision_kind)} /><InfoItem label="Revision time" value={document.latest_source_revision_at || original?.latest_source_revision_at || "Not reported"} /><InfoItem label="Archive member" value={document.source_archive_member || original?.source_archive_member || "Not reported"} /><InfoItem label="Content hash" value={shortHash(document.content_sha256)} /></div>{flags.length ? <div className="sec-quality-flags">{flags.map((flag) => <span key={flag}>{humanize(flag)}</span>)}</div> : null}{document.extraction_error ? <p className="sec-extraction-error">{document.extraction_error}</p> : null}</> : <SecEmpty label="Choose a document to inspect its metadata." />}</details></section>;
}

function InfoItem({ label, value }: { label: string; value?: string }) { return <div><span>{label}</span><strong title={value}>{value || "Not reported"}</strong></div>; }
function secProductEligible(synthesis: SecSynthesis | null | undefined, product: string) { return Boolean(synthesis?.eligibility.some((item) => item.product === product && item.eligible)); }
function secKindForRow(row: SecRow): SecIconKind { return secIconKindFor(row.filing_label, row.filing_label_text, row.form_type, row.sec_synthesis?.narrative_disclosures.map((item) => item.concept)); }
function secSynthesisDirection(row: SecRow) { return normalizeSemanticDirection(row.sec_synthesis?.synthesis.composite_sentiment || row.scoped_summary?.semantic_direction); }
function secPopoverDirection(row: SecRow): SecPopoverDirection {
  const reviewDirection = row.sec_review?.result?.fundamental_direction || row.sec_review?.fundamental_direction;
  const reviewComplete = ["complete", "completed"].includes(String(row.sec_review?.status || "").toLowerCase());
  if (reviewComplete && reviewDirection) return { source: "AI review", value: secPopoverDirectionValue(reviewDirection) };
  if (row.sec_synthesis) return { source: "SEC Synthesis", value: secSynthesisDirection(row) };
  return { source: "Direction pending", value: "pending" };
}
function secPopoverDirectionValue(value: string): SecPopoverDirectionValue {
  const normalized = value.trim().toLowerCase();
  if (normalized === "contextual" || normalized === "uncertain") return normalized;
  return normalizeSemanticDirection(normalized);
}
function synthesisSortScore(synthesis?: SecSynthesis | null) { const direction = normalizeSemanticDirection(synthesis?.synthesis.composite_sentiment); return direction === "positive" ? 1 : direction === "negative" ? -1 : direction === "mixed" ? 0.5 : 0; }
function temperature(row: SecRow, queryTime: number): Temperature { if (row.event_time_quality === "date_only") return "dated"; const effectiveQueryTime = Number.isFinite(queryTime) ? queryTime : Date.now(); const age = Math.max(0, (effectiveQueryTime - Date.parse(row.accepted_at_utc)) / 60000); return age <= 240 ? "hot" : age <= 1440 ? "cold" : "old"; }
function temperatureIcon(value: Temperature) { return value === "hot" ? Flame : value === "cold" ? Snowflake : Clock3; }
function formatDateOnly(value: string) { const parsed = new Date(value); return Number.isNaN(parsed.getTime()) ? "Date unavailable" : new Intl.DateTimeFormat(undefined, { dateStyle: "medium", timeZone: "America/New_York" }).format(parsed); }
function selectionKey(canvasId: string) { return `quant-research-workbench.canvas.sec-selection.${canvasId}`; }
function parseSecSelection(value: string): SecSelection { try { const parsed = JSON.parse(value) as Partial<SecSelection>; if (parsed.key) return { acceptedAt: parsed.acceptedAt ?? "", key: parsed.key, queryId: parsed.queryId ?? "" }; } catch { if (value) return { acceptedAt: "", key: value, queryId: "" }; } return { acceptedAt: "", key: "", queryId: "" }; }
function readSelectedSec(canvasId: string) { return parseSecSelection(window.localStorage.getItem(selectionKey(canvasId)) || ""); }
function selectSec(canvasId: string, selection: SecSelection) { window.localStorage.setItem(selectionKey(canvasId), JSON.stringify(selection)); window.dispatchEvent(new CustomEvent(SEC_SELECTION_EVENT, { detail: { canvasId, ...selection } })); }
function openSecPage(row: SecRow, queryId: string, live = false) { ensureSecReaderCanvas(); const selection = { acceptedAt: row.accepted_at_utc, key: `${row.cik}/${row.accession_number}`, queryId }; selectSec(SEC_READER_CANVAS_ID, selection); const url = new URL(focusCanvasUrl(SEC_READER_CANVAS_ID, "sec_detail", "draft", live ? "live" : undefined)); url.searchParams.set("sec_cik", row.cik); url.searchParams.set("sec_accession", row.accession_number); window.open(url.toString(), "quant-sec-reader"); }
function safeUrl(value?: string) { if (!value) return false; try { return ["http:", "https:"].includes(new URL(value).protocol); } catch { return false; } }
function paragraphs(value: string) { const explicit = value.split(/\n{2,}/).map((part) => part.trim()).filter(Boolean); if (explicit.length > 1) return explicit; const sentences = value.split(/(?<=[.!?])\s+(?=[A-Z0-9])/).filter(Boolean); const result: string[] = []; for (let index = 0; index < sentences.length; index += 4) result.push(sentences.slice(index, index + 4).join(" ")); return result.length ? result : [value]; }
function formatCount(value: number) { return new Intl.NumberFormat("en-US", { notation: "compact" }).format(value || 0); }
function formatBytes(value?: number) { if (!value) return "Not reported"; const units = ["B", "KB", "MB", "GB"]; let amount = value; let unit = 0; while (amount >= 1024 && unit < units.length - 1) { amount /= 1024; unit += 1; } return `${amount >= 10 || unit === 0 ? amount.toFixed(0) : amount.toFixed(1)} ${units[unit]}`; }
function humanize(value?: string) { if (!value) return "Not reported"; return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase()); }
function shortHash(value?: string) { return value ? `${value.slice(0, 10)}…${value.slice(-6)}` : "Not reported"; }
