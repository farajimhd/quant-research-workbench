import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowUpDown, Check, ChevronDown, ChevronLeft, Columns3, FileCheck2, Flame, ListFilter, Plus, Search, Star, Trash2, X } from "lucide-react";
import { forwardRef, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api, apiCached, invalidateApiCache } from "../../api/client";
import { CONFIGURATION_SESSION_CHANGED_EVENT, readConfigurationSession } from "../configurationSession";
import { timeRecency } from "../timeRecency";
import { InventoryFilterSelect } from "./InventoryFilterSelect";
import { MarketTime } from "./MarketTime";
import { filterRowsByConditions, TableActiveFilterBar, TableColumnFilterControl, type TableFilterColumn, type TableFilterCondition, type TableFilterMatchMode } from "./TableColumnFilters";
import { useTickerPresentations } from "./TickerIdentity";
import { CategoryBadge, PresentedValue, SecurityIdentityCell, presentationForColumn, tableCellClass, type PresentationValueType } from "./TablePresentation";
import { useWallClock } from "./useWallClock";

export type ScreenerRow = Record<string, unknown>;
export type ScannerSnapshotMeta = {
  complete_universe?: boolean;
  enrichment_scope?: "core" | "full";
  enrichment_status?: "partial" | "ready";
  field_coverage?: Record<string, number>;
  lookback_minutes?: number;
  materialized?: boolean;
  qmd_derived_error?: string;
  qmd_derived_status?: "building" | "error" | "ready";
  qmd_indicator_row_count?: number;
  qmd_signal_event_count?: number;
  refresh_status?: "building" | "error" | "ready";
  row_count?: number;
  snapshot_at_utc?: string;
  status?: "building" | "error" | "ready" | "refreshing";
};
export type ScannerTimeframe = "100ms" | "1s" | "5s" | "10s" | "30s" | "1m" | "5m" | "15m" | "30m" | "1h" | "1d";
export type TechnicalMetric = "change_pct" | "dollar_volume" | "high" | "low" | "quote_count" | "range_pct" | "relative_volume" | "trade_count" | "volume" | "vwap" | "vwap_distance_pct";
export type ScannerSessionAnchor = "extended_session" | "regular_session";
export type ScannerVwapSource = "hlc3" | "trade_price";
export type ScannerCustomColumn = {
  anchor?: ScannerSessionAnchor;
  key: string;
  lookbackSessions?: number;
  metric: TechnicalMetric;
  source?: ScannerVwapSource;
  timeframe?: ScannerTimeframe;
};
type TechnicalListSettings = { columns: string[]; customColumns: ScannerCustomColumn[] };
export type MarketScannerSettings = TechnicalListSettings & { limit: number; preset: string };
export type SignalStreamSettings = TechnicalListSettings & { limit: number; signalStreamHiddenIds: string[]; signalStreamId: string; signalStreamIds: string[] };
export type WatchUniverseSettings = TechnicalListSettings & { limit: number; watchlistId: string; watchlistIds: string[]; universeId?: string };
type DiscoveryScannerColumn = { column_id: string; name: string; source_id: string };
type DiscoveryCapability = { enabled?: boolean; execution_scope?: string; scanner_columns?: DiscoveryScannerColumn[]; system_required?: boolean };
type DiscoveryColumn = { column_id: string; description?: string; name: string; presentation_value_type?: PresentationValueType; provenance?: string; semantic_type?: string; source_id?: string; source_kind?: "data_definition" | "rule_set" | string; unit?: string; value_type?: string };
type DiscoveryWatchlist = { availability?: string; columns?: string[]; description?: string; enabled?: boolean; name: string; origin?: string; watchlist_id: string };
type DiscoverySignalStream = { column_aggregations?: Record<string, string>; column_intervals?: Record<string, unknown>; column_labels?: Record<string, string>; columns?: string[]; description?: string; enabled?: boolean; maximum_events?: number; name: string; origin?: string; refresh_interval_ms?: number; signal_stream_id: string; source_id?: string; source_scan_id?: string; source_type?: "core_scan" | "watchlist" | "news_events" };
type SignalStreamRuntimeResponse = { as_of: string; last_sequence?: number; new_occurrences?: ScreenerRow[]; occurrence_count: number; occurrences: ScreenerRow[]; recovery?: { active?: boolean; recovered_count?: number; recovery_through?: string; status?: "complete" | "coverage_incomplete" | "not_started" | "recovering" | "retryable_error" | "source_native" }; session?: { active?: boolean; end_at?: string; retention?: string; session_date?: string; session_key?: string; start_at?: string; timezone?: string }; signal_streams?: Array<{ candidate_count?: number; configured?: boolean; enabled?: boolean; recovery_kind?: "coverage_unavailable" | "qmd_history_timeline" | "source_native"; recovery_status?: "complete" | "coverage_incomplete" | "not_started" | "recovering" | "retryable_error" | "source_native"; signal_stream_id: string; source_id?: string; source_type?: string; status?: string }>; status: string };
export type WatchUniverseDefinition = {
  description?: string;
  enabled?: boolean;
  name: string;
  scanner_view_id?: string;
  source: "configured_symbols" | "scanner_view" | "watchlist" | string;
  symbols?: string[];
  universe_id: string;
};
export type StrategyActivitySettings = { eventType: string; limit: number; runId: string; strategyId: string; ticker: string };
type StrategyActivityResponse = { as_of: string; complete: boolean; rows: ScreenerRow[]; source: string };
type WatchUniverseCatalogResponse = {
  market_discovery?: { column_catalog?: DiscoveryColumn[]; core_scan?: { calculations?: DiscoveryCapability[]; columns?: string[]; name?: string; scan_id?: string }; signal_streams?: DiscoverySignalStream[]; watchlists?: DiscoveryWatchlist[] };
  run_plans?: { plans?: Array<{ name?: string; run_plan_id: string; universe_id: string }>; universes?: WatchUniverseDefinition[] };
};
type ConfigurationSessionSnapshot = { market_discovery?: WatchUniverseCatalogResponse["market_discovery"] };
export type WatchlistRuntimeResponse = {
  as_of?: string;
  error?: string;
  status?: "awaiting_first_resolution" | "degraded" | "ready" | string;
  target_errors?: Array<{ error?: string; watchlist_id?: string }>;
  watchlists?: Array<{ member_count?: number; members?: ScreenerRow[]; status?: string; watchlist_id: string }>;
};

type FieldKind = "derived" | "estimated" | "raw";
type FieldDefinition = {
  description: string;
  format: "date" | "integer" | "money" | "multiple" | "number" | "percent" | "percentPlain" | "score" | "text";
  group: string;
  key: string;
  kind: FieldKind;
  label: string;
  presentationValueType?: PresentationValueType;
  metric?: TechnicalMetric;
  anchor?: ScannerSessionAnchor;
  lookbackSessions?: number;
  scope?: "interval" | "relative-volume" | "session";
  source?: ScannerVwapSource;
  timeframe?: ScannerTimeframe;
  timeframes?: ScannerTimeframe[];
};

type MarketListViewState = {
  columnFilters: TableFilterCondition[];
  filterMatchMode: TableFilterMatchMode;
  filterPanelOpen: boolean;
  query: string;
  sort: { column: string; direction: "asc" | "desc" };
};

const MARKET_LIST_VIEW_STATE = new Map<string, MarketListViewState>();

export const SCANNER_TIMEFRAMES: ScannerTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d"];
const DEFAULT_SCANNER_TECHNICAL_TIMEFRAME: ScannerTimeframe = "15m";
const TECHNICAL_METRICS: Array<Omit<FieldDefinition, "key" | "timeframe"> & { metric: TechnicalMetric }> = [
  intervalTechnicalMetric("change_pct", "Price change", "percent", "Open-to-last return inside the selected exchange-session interval."),
  intervalTechnicalMetric("volume", "Volume", "integer", "Eligible executed share volume inside the selected interval."),
  intervalTechnicalMetric("dollar_volume", "Dollar volume", "money", "Exact sum of eligible trade price multiplied by trade size inside the selected interval."),
  intervalTechnicalMetric("trade_count", "Trades", "integer", "Eligible trade-print count inside the selected interval."),
  intervalTechnicalMetric("quote_count", "Quotes", "integer", "Consolidated quote-event count inside the selected interval."),
  technicalMetric("vwap", "VWAP", "money", "Anchored cumulative source value weighted by eligible share volume. HLC3 is the standard default; exact trade price is also available.", "session"),
  technicalMetric("vwap_distance_pct", "Price vs VWAP", "percent", "Latest eligible trade relative to the same anchored, source-configured VWAP.", "session"),
  technicalMetric("relative_volume", "Relative volume", "multiple", "Cumulative extended-session volume pace divided by the prior 20 completed extended-session average pace.", "relative-volume"),
  intervalTechnicalMetric("range_pct", "Range", "percentPlain", "High-to-low price range inside the selected interval."),
  intervalTechnicalMetric("high", "High", "money", "Highest eligible trade inside the selected interval."),
  intervalTechnicalMetric("low", "Low", "money", "Lowest eligible trade inside the selected interval."),
];

const FIELD_CATALOG: FieldDefinition[] = [
  field("logo", "", "Security", "raw", "text", "Ticker presentation logo when a provider asset is available."),
  field("ticker", "Symbol", "Security", "raw", "text", "Canonical point-in-time trading symbol."),
  field("company_name", "Company", "Security", "raw", "text", "Issuer or security display name."),
  field("exchange", "Exchange", "Security", "raw", "text", "Canonical exchange code carried by the tradable-universe record at the workspace clock."),
  field("country", "Country", "Security", "raw", "text", "Canonical issuer domicile when published."),
  field("sector", "Sector", "Security", "raw", "text", "Provider or canonical sector classification."),
  field("last", "Last", "Market state", "raw", "money", "Latest eligible trade price at the workspace clock."),
  field("change_pct", "Change", "Market state", "derived", "percent", "Return over the scanner observation window."),
  field("change_5m_pct", "5 min", "Market state", "derived", "percent", "Return from the first eligible bar in the latest five-minute interval."),
  field("volume", "Volume", "Market state", "raw", "integer", "Eligible executed share volume in the observation window."),
  field("trade_count", "Trades", "Market state", "raw", "integer", "Eligible trade count in the observation window."),
  field("quote_count", "Quotes", "Market state", "raw", "integer", "Consolidated NBBO update count in the observation window."),
  field("dollar_volume", "Dollar volume", "Liquidity", "derived", "money", "Executed share volume multiplied by representative price."),
  field("float_shares", "Tradable shares", "Share supply", "estimated", "integer", "Best available reported or explicitly estimated tradable-share supply."),
  field("shares_outstanding", "Shares outstanding", "Share supply", "raw", "integer", "Latest point-in-time reported share-class or provider outstanding shares."),
  field("short_interest", "Short interest", "Share supply", "raw", "integer", "Latest short-interest shares publicly available at the workspace clock."),
  field("short_crowding_pct", "Short crowding", "Share supply", "derived", "percent", "Reported short interest divided by the best available tradable-share base."),
  field("days_to_cover", "Days to cover", "Share supply", "derived", "number", "Latest reported short interest divided by its aligned average daily volume."),
  field("market_cap", "Market cap", "Fundamentals", "derived", "money", "Latest price multiplied by aligned shares outstanding."),
  field("xbrl_quality_score", "Financial quality", "Financial scores", "derived", "score", "Evidence-weighted operating quality calculated from causal SEC XBRL facts."),
  field("xbrl_quality_label", "Quality regime", "Financial scores", "derived", "text", "Semantic financial-quality label associated with the composite score."),
  field("xbrl_quality_coverage_pct", "Financial evidence", "Financial scores", "derived", "percentPlain", "Share of the XBRL score model supported by comparable reported evidence."),
  field("xbrl_profitability_score", "Profitability", "Financial scores", "derived", "score", "Profitability score from margins and return measures."),
  field("xbrl_growth_score", "Growth", "Financial scores", "derived", "score", "Growth score from comparable revenue and earnings observations."),
  field("xbrl_cash_quality_score", "Cash quality", "Financial scores", "derived", "score", "Cash-quality score from free cash flow and cash conversion."),
  field("xbrl_balance_sheet_score", "Balance sheet", "Financial scores", "derived", "score", "Balance-sheet score from liquidity and leverage measures."),
  field("xbrl_capital_discipline_score", "Capital discipline", "Financial scores", "derived", "score", "Capital-discipline score from dilution, issuance, repurchases, and share-count change."),
  field("financial_trajectory_score", "Financial trajectory", "Financial scores", "derived", "score", "Stock Facts financial-trajectory score using profitability, cash generation, and balance-sheet evidence."),
  field("financial_trajectory_label", "Trajectory regime", "Financial scores", "derived", "text", "Semantic label for the Stock Facts financial trajectory."),
  field("financial_profitability_score", "Trajectory profitability", "Financial scores", "derived", "score", "Profitability subscore used by the Stock Facts trajectory."),
  field("financial_cash_generation_score", "Trajectory cash", "Financial scores", "derived", "score", "Cash-generation subscore used by the Stock Facts trajectory."),
  field("financial_balance_sheet_score", "Trajectory balance sheet", "Financial scores", "derived", "score", "Balance-sheet subscore used by the Stock Facts trajectory."),
  field("share_base_pressure_pct", "Share-base pressure", "Financial scores", "derived", "percent", "Change in shares versus the nearest comparable observation at least 300 days earlier."),
  field("share_base_discipline_score", "Share discipline", "Financial scores", "derived", "score", "Score that rewards stable or contracting share supply and penalizes dilution."),
  field("valuation_pe", "Historical P/E", "Financial scores", "derived", "number", "Current price divided by historical or fiscal diluted earnings per share; not an analyst forward estimate."),
  field("valuation_label", "Valuation regime", "Financial scores", "derived", "text", "Semantic valuation regime based on the historical P/E observation."),
  field("fundamental_latest_filing_at", "Latest financial filing", "Financial scores", "raw", "date", "Latest SEC filing timestamp contributing financial evidence at the scanner clock."),
  field("fundamental_free_cash_flow", "Free cash flow", "Financial ratios & growth", "derived", "money", "Operating cash flow minus capital expenditure."),
  field("fundamental_gross_margin_pct", "Gross margin", "Financial ratios & growth", "derived", "percent", "Gross profit divided by aligned revenue."),
  field("fundamental_operating_margin_pct", "Operating margin", "Financial ratios & growth", "derived", "percent", "Operating income divided by aligned revenue."),
  field("fundamental_net_margin_pct", "Net margin", "Financial ratios & growth", "derived", "percent", "Net income divided by aligned revenue."),
  field("fundamental_free_cash_flow_margin_pct", "FCF margin", "Financial ratios & growth", "derived", "percent", "Free cash flow divided by aligned revenue."),
  field("fundamental_return_on_assets_pct", "Return on assets", "Financial ratios & growth", "derived", "percent", "Comparable net income divided by latest assets."),
  field("fundamental_return_on_equity_pct", "Return on equity", "Financial ratios & growth", "derived", "percent", "Comparable net income divided by latest stockholders' equity."),
  field("fundamental_working_capital", "Working capital", "Financial ratios & growth", "derived", "money", "Current assets minus current liabilities."),
  field("fundamental_current_ratio", "Current ratio", "Financial ratios & growth", "derived", "number", "Current assets divided by current liabilities."),
  field("fundamental_debt_to_equity", "Debt to equity", "Financial ratios & growth", "derived", "number", "Aligned debt divided by stockholders' equity."),
  field("fundamental_net_debt", "Net debt", "Financial ratios & growth", "derived", "money", "Aligned debt minus cash and equivalents."),
  field("fundamental_interest_coverage", "Interest coverage", "Financial ratios & growth", "derived", "number", "Operating income divided by interest expense."),
  field("fundamental_revenue_growth_pct", "Revenue growth", "Financial ratios & growth", "derived", "percent", "Change between latest comparable revenue periods."),
  field("fundamental_earnings_growth_pct", "Earnings growth", "Financial ratios & growth", "derived", "percent", "Change between latest comparable net-income periods."),
  field("fundamental_share_growth_pct", "Share growth", "Financial ratios & growth", "derived", "percent", "Change between latest comparable weighted-average share counts."),
  field("fundamental_dilution_pct", "Dilution", "Financial ratios & growth", "derived", "percent", "Difference between diluted and basic weighted-average shares relative to basic shares."),
  field("fundamental_cash_conversion", "Cash conversion", "Financial ratios & growth", "derived", "number", "Operating cash flow divided by aligned net income."),
  field("fundamental_research_intensity_pct", "R&D intensity", "Financial ratios & growth", "derived", "percent", "Research and development expense divided by aligned revenue."),
  field("fundamental_sga_intensity_pct", "SG&A intensity", "Financial ratios & growth", "derived", "percent", "Selling, general, and administrative expense divided by aligned revenue."),
  ...reportedFundamentalFields(),
  field("live_news_recency", "News recency", "News & SEC", "derived", "text", "Hot, cold, old, or none for company-specific news at the workspace clock."),
  field("live_news_count", "News count", "News & SEC", "derived", "integer", "Recent company-specific article count."),
  field("news_labels", "News", "News & SEC", "derived", "text", "Explainable company-news classifications."),
  field("sec_recency", "SEC recency", "News & SEC", "derived", "text", "Hot, cold, old, or none from filing acceptance time."),
  field("sec_count", "SEC filings", "News & SEC", "derived", "integer", "Recent ticker-linked SEC filing count."),
  field("sec_labels", "SEC", "News & SEC", "derived", "text", "Explainable SEC disclosure categories."),
  field("event_time", "Detected", "Signal event", "raw", "date", "First causal detection time for this event."),
  field("event_type", "Event", "Strategy activity", "raw", "text", "Whether the durable record is a strategy signal, decision, or campaign-state change."),
  field("action", "Action", "Strategy activity", "raw", "text", "The semantic action emitted by the strategy runtime."),
  field("state", "State", "Strategy activity", "raw", "text", "The resulting strategy or campaign state when one was recorded."),
  field("reason", "Reason", "Strategy activity", "raw", "text", "Durable explanation or evidence recorded with the strategy event."),
  field("strategy_id", "Strategy", "Strategy activity", "raw", "text", "Stable Strategy Profile identifier that emitted the event."),
  field("run_id", "Run", "Strategy activity", "raw", "text", "Strategy Run identifier that owns the event."),
  field("signal_type", "Signal", "Signal event", "derived", "text", "Stable event class or strategy-defined signal name."),
  field("signal_domain", "Signal domain", "Signal taxonomy", "raw", "text", "Semantic domain: market, news, SEC, or model."),
  field("signal_producer", "Signal producer", "Signal taxonomy", "raw", "text", "Service or model that produced the signal; QMD is a producer, not a signal domain."),
  field("input_basis", "Input basis", "Signal clock", "raw", "text", "Source state that advances the calculation, such as market events, bars, documents, or model output."),
  field("calculation_window", "Calculation window", "Signal clock", "raw", "text", "Window represented by the signal; this is independent of publication cadence."),
  field("evaluation_mode", "Evaluation mode", "Signal clock", "raw", "text", "Developing, closed-only, or point-in-time evaluation semantics."),
  field("update_trigger", "Update trigger", "Signal clock", "raw", "text", "Event that causes the signal to be evaluated."),
  field("publication_cadence", "Publication cadence", "Signal clock", "raw", "text", "When updated values become visible to consumers."),
  field("signal_state", "State", "Signal event", "raw", "text", "Triggered, updated, resolved, or expired lifecycle state from the signal authority."),
  field("direction", "Direction", "Signal event", "derived", "text", "Bullish, bearish, or neutral direction assigned by the rule owner."),
  field("working_timeframe", "Working interval", "Signal event", "raw", "text", "Market-data interval on which the reusable signal rule was evaluated."),
  field("signal_score", "Score", "Signal event", "derived", "number", "Normalized signed or directional evidence score supplied by the signal authority."),
  field("signal_rank_score", "Rank score", "Signal event", "derived", "score", "Comparable rank supplied by the signal authority for ordering active observations across symbols and methods."),
  field("signal_confidence_pct", "Confidence", "Signal event", "derived", "percentPlain", "Evidence completeness and agreement, not forecast win probability."),
  field("active_signal_count", "Active signals", "Signal event", "derived", "integer", "Count of currently active reusable signals for this ticker."),
  field("action", "Strategy action", "Signal event", "derived", "text", "Enter, exit, hold, or wait interpretation owned by the strategy."),
  field("magnitude", "Magnitude", "Signal event", "derived", "percent", "Observed move or normalized event magnitude."),
  field("source", "Authority", "Signal event", "raw", "text", "Market-derived rule or durable strategy runtime authority."),
  field("evidence", "Evidence", "Signal event", "derived", "text", "Compact explanation of the inputs that triggered the row."),
  field("indicator_timeframe", "Indicator interval", "Indicator taxonomy", "raw", "text", "Closed publication interval for the cross-sectional indicator state."),
  field("indicator_type", "Indicator type", "Indicator taxonomy", "raw", "text", "Technical, QMD, fundamental, reference, or model indicator."),
  field("indicator_producer", "Indicator producer", "Indicator taxonomy", "raw", "text", "Service or model that owns the calculation."),
  field("indicator_input_basis", "Input basis", "Indicator clock", "raw", "text", "Source state that advances the indicator calculation."),
  field("indicator_calculation_window", "Calculation window", "Indicator clock", "raw", "text", "Window represented by the published value."),
  field("indicator_evaluation_mode", "Evaluation mode", "Indicator clock", "raw", "text", "Developing, closed-only, or point-in-time evaluation semantics."),
  field("indicator_update_trigger", "Update trigger", "Indicator clock", "raw", "text", "Event that causes the indicator to be evaluated."),
  field("indicator_publication_cadence", "Publication cadence", "Indicator clock", "raw", "text", "When the indicator becomes visible to consumers."),
  field("flow_structure_composite_score", "Flow-structure composite", "Indicators · Flow & structure", "derived", "score", "Continuous signed agreement between event-native flow and causal structural context."),
  field("flow_structure_composite_confidence_pct", "Composite confidence", "Indicators · Flow & structure", "derived", "percentPlain", "Confidence-weighted evidence quality and agreement for the composite."),
  field("flow_structure_composite_bias", "Composite bias", "Indicators · Flow & structure", "derived", "text", "Bullish, bearish, or neutral indicator bias; this is not an order instruction."),
  field("flow_structure_composite_reason", "Composite evidence", "Indicators · Flow & structure", "derived", "text", "Current relationship between flow and structure, such as aligned, conflicting, or dominated by one evidence family."),
  field("microstructure_unified_signal", "Flow signal", "Indicators · Flow & structure", "derived", "score", "Signed unified microstructure evidence from the event-native indicator engine."),
  field("microstructure_unified_confidence_pct", "Flow confidence", "Indicators · Flow & structure", "derived", "percentPlain", "Evidence quality for the unified microstructure observation."),
  field("microstructure_signed_volume_imbalance", "Volume imbalance", "Indicators · Flow & structure", "derived", "score", "Signed eligible-trade volume imbalance in the current indicator interval."),
  field("microstructure_level1_ofi", "Level 1 OFI", "Indicators · Flow & structure", "derived", "number", "Streaming level-one order-flow imbalance."),
  field("microstructure_queue_imbalance", "Queue imbalance", "Indicators · Flow & structure", "derived", "score", "Displayed bid-versus-ask queue imbalance."),
  field("qmd_structure_score", "Structure score", "Indicators · Flow & structure", "derived", "score", "Signed causal market-structure observation."),
  field("qmd_structure_confidence_pct", "Structure confidence", "Indicators · Flow & structure", "derived", "percentPlain", "Evidence quality for the current structure observation."),
];

const QMD_SCANNER_PRESET = "Core Scan";

const SIGNAL_STREAM_CONTEXT_COLUMNS = [
  "float_category",
  "short_pressure",
  "short_interest",
  "short_interest_pct",
  "days_to_cover",
  "short_volume",
  "short_volume_pct",
  "liquidity_rank",
  "liquidity_score",
];

function useDiscoveryPresentation() {
  const [configuration, setConfiguration] = useState<WatchUniverseCatalogResponse | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    let baseConfiguration: WatchUniverseCatalogResponse | null = null;
    const applySessionDiscovery = async () => {
      if (!baseConfiguration) return;
      const resolved = overlaySessionDiscovery(baseConfiguration);
      setConfiguration(resolved);
    };
    const handleSessionChange = () => {
      if (readConfigurationSession()) {
        void applySessionDiscovery();
        return;
      }
      invalidateApiCache("/api/market-discovery/configuration/presentation");
      apiCached<WatchUniverseCatalogResponse>("/api/market-discovery/configuration/presentation", { timeoutMs: 10000, ttlMs: 30_000 })
        .then((base) => {
          if (controller.signal.aborted) return;
          baseConfiguration = base;
          void applySessionDiscovery();
        })
        .catch(() => undefined);
    };
    window.addEventListener(CONFIGURATION_SESSION_CHANGED_EVENT, handleSessionChange);
    apiCached<WatchUniverseCatalogResponse>("/api/market-discovery/configuration/presentation", { timeoutMs: 10000, ttlMs: 30_000 })
      .then((base) => {
        if (controller.signal.aborted) return;
        baseConfiguration = base;
        void applySessionDiscovery();
      })
      .catch(() => undefined);
    return () => {
      controller.abort();
      window.removeEventListener(CONFIGURATION_SESSION_CHANGED_EVENT, handleSessionChange);
    };
  }, []);
  const discovery = configuration?.market_discovery;
  const { catalog, coreColumns } = useMemo(() => {
    const calculations = discovery?.core_scan?.calculations ?? [];
    const presentationNames = new Map(calculations
      .flatMap((capability) => capability.scanner_columns ?? [])
      .filter((column) => Boolean(column.column_id && column.name))
      .map((column) => [column.column_id, column.name] as const));
    return {
      catalog: (discovery?.column_catalog ?? []).map((column) => discoveryField({
        ...column,
        name: presentationNames.get(column.column_id) ?? column.name,
      })),
      coreColumns: discovery?.core_scan?.columns?.length
        ? [...new Set(discovery.core_scan.columns)]
        : [...new Set(calculations
          .filter((capability) => capability.execution_scope === "core_scan" && Boolean(capability.enabled || capability.system_required))
          .flatMap((capability) => capability.scanner_columns ?? [])
          .map((column) => column.column_id)
          .filter(Boolean))],
    };
  }, [discovery?.column_catalog, discovery?.core_scan?.calculations, discovery?.core_scan?.columns]);
  return { catalog, configuration, coreColumns, discovery };
}

function overlaySessionDiscovery(base: WatchUniverseCatalogResponse): WatchUniverseCatalogResponse {
  try {
    const session = readConfigurationSession<ConfigurationSessionSnapshot>();
    const draft = session?.market_discovery;
    if (!draft) return base;
    const canonical = base.market_discovery;
    const canonicalStreams = canonical?.signal_streams ?? [];
    const draftStreams = draft.signal_streams ?? [];
    const draftStreamById = new Map(draftStreams.map((row) => [row.signal_stream_id, row]));
    const canonicalStreamIds = new Set(canonicalStreams.map((row) => row.signal_stream_id));
    const reconciledStreams = [
      ...canonicalStreams.map((row) => {
        const saved = draftStreamById.get(row.signal_stream_id);
        if (!saved) return row;
        if (row.origin === "system") {
          return {
            ...row,
            enabled: saved.enabled,
          };
        }
        return { ...row, ...saved };
      }),
      ...draftStreams.filter((row) => row.origin === "user" && !canonicalStreamIds.has(row.signal_stream_id)),
    ];
    const canonicalWatchlists = canonical?.watchlists ?? [];
    const draftWatchlists = draft.watchlists ?? [];
    const draftWatchlistById = new Map(draftWatchlists.map((row) => [row.watchlist_id, row]));
    const canonicalWatchlistIds = new Set(canonicalWatchlists.map((row) => row.watchlist_id));
    const reconciledWatchlists = [
      ...canonicalWatchlists.map((row) => ({ ...row, ...(draftWatchlistById.get(row.watchlist_id) ?? {}) })),
      ...draftWatchlists.filter((row) => row.origin === "user" && !canonicalWatchlistIds.has(row.watchlist_id)),
    ];
    return {
      ...base,
      market_discovery: {
        ...canonical,
        ...draft,
        column_catalog: canonical?.column_catalog ?? [],
        core_scan: {
          ...canonical?.core_scan,
          ...draft.core_scan,
          calculations: canonical?.core_scan?.calculations ?? [],
        },
        signal_streams: reconciledStreams,
        watchlists: reconciledWatchlists,
      },
    };
  } catch {
    return base;
  }
}

function discoveryField(column: DiscoveryColumn): FieldDefinition {
  const unit = String(column.unit ?? "").toLowerCase();
  const valueType = String(column.value_type ?? "").toLowerCase();
  const format: FieldDefinition["format"] = unit === "currency" ? "money"
    : unit === "percent" ? "percent"
      : unit === "shares" || unit === "milliseconds" || unit === "rank" ? "integer"
        : unit === "multiple" ? "multiple"
          : unit === "score" ? "score"
            : unit === "timestamp" ? "date"
              : valueType === "number" ? "number" : "text";
  const provenance = String(column.provenance ?? "raw");
  const kind: FieldKind = provenance === "derived" ? "derived" : provenance === "estimated" ? "estimated" : "raw";
  return { ...field(column.column_id, column.name, readableGroup(column.semantic_type), kind, format, column.description || `QMD-published ${column.name} field.`), presentationValueType: column.presentation_value_type };
}

function readableGroup(value: unknown) {
  const text = String(value ?? "QMD").replaceAll("_", " ");
  return text ? `${text.charAt(0).toUpperCase()}${text.slice(1)}` : "QMD";
}

export function MarketScannerContainer({ asOf, live = false, meta, onSettingsChange, onTickerSelect, rows, settings }: { asOf: string; live?: boolean; meta?: ScannerSnapshotMeta; onSettingsChange: (patch: Partial<MarketScannerSettings>) => void; onTickerSelect: (ticker: string) => void; rows: ScreenerRow[]; settings: MarketScannerSettings }) {
  const normalizedRows = useMemo(() => normalizeScannerRows(rows), [rows]);
  const { catalog, coreColumns } = useDiscoveryPresentation();
  const columns = canonicalDiscoveryColumns([...(coreColumns.length ? coreColumns : ["symbol", "last_price", "change_pct", "volume"]), ...settings.columns]);
  const refreshing = meta?.status === "refreshing";
  const subtitle = meta?.complete_universe
    ? `QMD Core Scan · full eligible universe${refreshing ? " · refreshing projection" : ""}`
    : meta?.status === "building"
      ? "QMD Core Scan is building its first complete eligible-universe snapshot"
      : "QMD Core Scan universe unavailable or incomplete";
  return <MarketListSurface
    asOf={asOf}
    catalog={catalog}
    columns={columns}
    customColumns={settings.customColumns}
    empty="No securities are available at this market clock."
    eyebrow="Market snapshot"
    fieldCoverage={meta?.field_coverage}
    limit={settings.limit}
    liveRecency={live}
    lockedColumns={canonicalDiscoveryColumns(coreColumns.length ? coreColumns : ["symbol"])}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
    onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
    onPresetChange={() => undefined}
    onTickerSelect={onTickerSelect}
    presets={[QMD_SCANNER_PRESET]}
    preset={QMD_SCANNER_PRESET}
    rows={normalizedRows}
    sortColumn="liquidity_rank"
    subtitle={subtitle}
    title="Scanner"
  />;
}

export function migrateMarketScannerSettings(
  settings: MarketScannerSettings,
  storedVersion: number | undefined,
): MarketScannerSettings {
  return { ...settings, columns: Number(storedVersion ?? 0) < 25 ? [] : settings.columns, preset: QMD_SCANNER_PRESET };
}

export function normalizeMarketScannerPreset(value: unknown): string {
  void value;
  return QMD_SCANNER_PRESET;
}

export function SignalStreamContainer({ asOf, live, onSettingsChange, onTickerSelect, runId, settings }: { asOf: string; live: boolean; onSettingsChange: (patch: Partial<SignalStreamSettings>) => void; onTickerSelect: (ticker: string) => void; runId?: string; settings: SignalStreamSettings }) {
  const { catalog, discovery } = useDiscoveryPresentation();
  const [addingStream, setAddingStream] = useState(false);
  const streams = (discovery?.signal_streams ?? []).filter((row) => row.enabled !== false);
  const hiddenIds = new Set(settings.signalStreamHiddenIds);
  const configuredIds = Array.from(new Set(settings.signalStreamIds)).filter((id) => !hiddenIds.has(id) && streams.some((row) => row.signal_stream_id === id));
  const visibleIds = [...configuredIds, ...streams.map((row) => row.signal_stream_id).filter((id) => !hiddenIds.has(id) && !configuredIds.includes(id))];
  const visibleStreams = visibleIds.map((id) => streams.find((row) => row.signal_stream_id === id)).filter((row): row is DiscoverySignalStream => Boolean(row));
  const stream = visibleStreams.find((row) => row.signal_stream_id === settings.signalStreamId) ?? visibleStreams[0];
  const availableStreams = streams.filter((row) => hiddenIds.has(row.signal_stream_id));
  const [runtimeCache, setRuntimeCache] = useState<Record<string, SignalStreamRuntimeResponse>>({});
  const lastSequence = useRef<Record<string, number>>({});
  const sessionKey = useRef<Record<string, string>>({});
  const [runtimeUnavailableKeys, setRuntimeUnavailableKeys] = useState<Set<string>>(() => new Set());
  const runtimeScopeKey = stream ? `${live ? "live" : `${runId ?? ""}:${asOf}`}|${stream.signal_stream_id}` : "";
  const runtime = runtimeScopeKey ? runtimeCache[runtimeScopeKey] ?? null : null;
  const runtimeUnavailable = runtimeScopeKey ? runtimeUnavailableKeys.has(runtimeScopeKey) : false;
  useEffect(() => {
    if (!stream || !runtimeScopeKey) return undefined;
    let active = true;
    let controller: AbortController | null = null;
    const load = () => {
      if (controller) return;
      controller = new AbortController();
      const query = new URLSearchParams({
        limit: String(Math.min(settings.limit, stream.maximum_events ?? settings.limit)),
        signal_stream_id: stream.signal_stream_id,
      });
      const previousSequence = lastSequence.current[runtimeScopeKey] ?? 0;
      if (live && previousSequence > 0) query.set("after_sequence", String(previousSequence));
      if (!live) {
        query.set("as_of", asOf);
        if (runId) query.set("run_id", runId);
      }
      api<SignalStreamRuntimeResponse>(`/api/market-discovery/signal-stream/runtime?${query}`, { signal: controller.signal, timeoutMs: 10000 })
        .then((payload) => { if (active) {
          setRuntimeCache((current) => {
            const currentRuntime = current[runtimeScopeKey];
            const nextSessionKey = String(payload.session?.session_key ?? payload.session?.session_date ?? "");
            const incremental = live && previousSequence > 0 && payload.occurrences.length === 0 && (!sessionKey.current[runtimeScopeKey] || sessionKey.current[runtimeScopeKey] === nextSessionKey);
            sessionKey.current[runtimeScopeKey] = nextSessionKey;
            lastSequence.current[runtimeScopeKey] = Math.max(previousSequence, Number(payload.last_sequence ?? 0));
            if (!incremental || !currentRuntime) return { ...current, [runtimeScopeKey]: payload };
            const additions = payload.new_occurrences ?? [];
            const seen = new Set(additions.map((row) => String(row["event_id"] ?? row["signal_id"] ?? "")));
            return { ...current, [runtimeScopeKey]: { ...payload, occurrences: [...additions, ...currentRuntime.occurrences.filter((row) => !seen.has(String(row["event_id"] ?? row["signal_id"] ?? "")))] } };
          });
          setRuntimeUnavailableKeys((current) => {
            if (!current.has(runtimeScopeKey)) return current;
            const next = new Set(current);
            next.delete(runtimeScopeKey);
            return next;
          });
        } })
        .catch((error) => { if (active && (error as Error).name !== "AbortError") setRuntimeUnavailableKeys((current) => new Set(current).add(runtimeScopeKey)); })
        .finally(() => { controller = null; });
    };
    load();
    const timer = live ? window.setInterval(load, Math.max(100, stream.refresh_interval_ms ?? 5000)) : null;
    return () => { active = false; controller?.abort(); if (timer !== null) window.clearInterval(timer); };
  }, [live, runId, runtimeScopeKey, settings.limit, stream?.maximum_events, stream?.refresh_interval_ms, stream?.signal_stream_id, live ? "" : asOf]);
  const rows = useMemo(() => {
    const normalized: ScreenerRow[] = normalizeScannerRows(runtime?.occurrences ?? []);
    return normalized.filter((row) => String(row["signal_stream_id"] ?? "") === String(stream?.signal_stream_id ?? "")).sort((left, right) => String(right["event_time"] ?? "").localeCompare(String(left["event_time"] ?? "")));
  }, [runtime?.occurrences, stream?.signal_stream_id]);
  const runtimeDefinition = runtime?.signal_streams?.find((row) => row.signal_stream_id === stream?.signal_stream_id);
  const sourceType = stream?.source_type ?? "core_scan";
  const sourceId = stream?.source_id ?? stream?.source_scan_id ?? discovery?.core_scan?.scan_id ?? "";
  const recoveryStatus = runtimeDefinition?.recovery_status;
  const recoveryActive = recoveryStatus === "recovering";
  const recoveryPending = recoveryActive || recoveryStatus === "coverage_incomplete" || recoveryStatus === "retryable_error";
  const sourceLabel = sourceType === "watchlist"
    ? discovery?.watchlists?.find((row) => row.watchlist_id === sourceId)?.name ?? sourceId
    : sourceType === "news_events"
      ? "News Synthesis V1 issuer events"
    : `${discovery?.core_scan?.name ?? "Core Scan"} · all eligible tickers`;
  const emptyMessage = !stream
    ? "No configured Signal Stream is available."
    : runtimeUnavailable && runtime === null
      ? "Signal Stream data is temporarily unavailable."
    : recoveryPending && rows.length === 0
      ? recoveryActive
        ? "Earlier session signals are catching up in the background. New live signals remain available."
        : "Earlier session signals are waiting for complete historical source coverage. New live signals remain available."
    : runtime?.session?.active === false
      ? "Signal Stream is cleared outside the 04:00–20:00 ET trading-day window."
      : runtimeDefinition?.configured === false
        ? "Select at least one Signal Rule in Market Discovery before this stream can emit."
        : runtimeDefinition?.status === "source_unavailable"
          ? `The configured source ${sourceLabel} is not available to the live discovery runtime.`
          : runtimeDefinition?.candidate_count === 0
            ? `${sourceLabel} currently contains no eligible tickers.`
        : `No ticker from ${sourceLabel} has transitioned into this signal state since 04:00 ET.`;
  const displayAsOf = runtime?.as_of ?? asOf;
  const streamCatalog = catalog.map((definition) => ({
    ...definition,
    label: stream?.column_labels?.[definition.key] ?? definition.label,
  }));
  const streamColumns = canonicalDiscoveryColumns([...(stream?.columns ?? []), ...SIGNAL_STREAM_CONTEXT_COLUMNS]);
  const columns = canonicalDiscoveryColumns(["event_time", "symbol", ...streamColumns, ...settings.columns]);
  const selectStream = (signalStreamId: string) => onSettingsChange({ columns: [], signalStreamId });
  const addStream = (signalStreamId: string) => {
    if (!signalStreamId) return;
    onSettingsChange({
      columns: [],
      signalStreamHiddenIds: settings.signalStreamHiddenIds.filter((id) => id !== signalStreamId),
      signalStreamId,
      signalStreamIds: Array.from(new Set([...settings.signalStreamIds, signalStreamId])),
    });
    setAddingStream(false);
  };
  const removeStream = (signalStreamId: string) => {
    const nextIds = visibleIds.filter((id) => id !== signalStreamId);
    onSettingsChange({
      columns: [],
      signalStreamHiddenIds: Array.from(new Set([...settings.signalStreamHiddenIds, signalStreamId])),
      signalStreamId: signalStreamId === settings.signalStreamId ? nextIds[0] ?? "" : settings.signalStreamId,
      signalStreamIds: nextIds,
    });
  };
  return <section className="market-list-surface watchlist-surface signal-stream-surface" aria-label={`${stream?.name ?? "Signal Stream"} signal stream`}>
    <header className="market-list-heading"><div><span className="market-list-eyebrow"><Flame size={12} /> Today’s immutable occurrences</span><h3>{stream?.name ?? "No Signal Stream open"}</h3><p>{stream ? recoveryPending ? `${rows.length} cached · earlier session catch-up ${recoveryActive ? "in progress" : "waiting for complete source coverage"} · through ` : `${rows.length} captured since 04:00 ET · newest first · through ` : "Create or add a configured Signal Stream"}{stream ? <MarketTime value={displayAsOf} /> : null}</p></div><span className="market-list-owner strategy">Market Discovery</span></header>
    <nav aria-label="Signal Streams" className="watchlist-tabs" role="tablist">
      {visibleStreams.map((row) => { const selected = row.signal_stream_id === stream?.signal_stream_id; return <span className={selected ? "active" : undefined} key={row.signal_stream_id}><button aria-selected={selected} onClick={() => selectStream(row.signal_stream_id)} role="tab" title={row.description || row.name} type="button">{row.name}</button><button aria-label={`Remove ${row.name} tab`} className="watchlist-tab-remove" onClick={() => removeStream(row.signal_stream_id)} type="button"><X size={10} /></button></span>; })}
      <button aria-expanded={addingStream} aria-label="Add Signal Stream tab" className="watchlist-tab-add" disabled={!availableStreams.length} onClick={() => setAddingStream((open) => !open)} role="tab" type="button"><Plus size={12} /><span>Add</span></button>
    </nav>
    {addingStream ? <div className="watchlist-tab-lookup"><InventoryFilterSelect ariaLabel="Signal Stream to add" className="watchlist-add-lookup" onChange={addStream} options={availableStreams.map((row) => ({ description: row.description, label: row.name, value: row.signal_stream_id }))} searchable showAllOnOpen value="" /><button onClick={() => { window.location.hash = "market-discovery-configuration"; }} type="button">Configure Signal Stream <ArrowRight size={13} /></button></div> : null}
    <div className="watch-universe-context"><div><span>Source</span><strong>{sourceLabel} · 04:00–20:00 ET</strong></div><button onClick={() => { window.location.hash = "market-discovery-configuration"; }} type="button">Configure in Market Discovery <ArrowRight size={13} /></button></div>
    <MarketListTable key={runtimeScopeKey || "signal-stream"} catalog={streamCatalog} chronological columns={columns} customColumns={settings.customColumns} empty={emptyMessage} limit={Math.min(settings.limit, stream?.maximum_events ?? settings.limit)} liveRecency={live} lockedColumns={canonicalDiscoveryColumns(["event_time", "symbol", ...streamColumns])} onColumnsChange={(columns) => onSettingsChange({ columns })} onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })} onTickerSelect={onTickerSelect} recencyRail rows={rows} title={stream?.name ?? "Signal Stream"} viewStateKey={`signal-stream:${stream?.signal_stream_id ?? "none"}`} />
  </section>;
}

export function WatchUniverseContainer({ asOf, live = false, onSettingsChange, onTickerSelect, runtime, scannerRows, settings }: { asOf: string; live?: boolean; onSettingsChange: (update: Partial<WatchUniverseSettings> | ((current: WatchUniverseSettings) => Partial<WatchUniverseSettings>)) => void; onTickerSelect: (ticker: string) => void; runtime: WatchlistRuntimeResponse | null; scannerRows: ScreenerRow[]; settings: WatchUniverseSettings }) {
  const { catalog: fieldCatalog, configuration, discovery } = useDiscoveryPresentation();
  const [addingWatchlist, setAddingWatchlist] = useState(false);
  const watchlists = discovery?.watchlists ?? [];
  const universes = configuration?.run_plans?.universes ?? [];
  const runPlans = configuration?.run_plans?.plans ?? [];
  const sourceRows = useMemo(() => normalizeScannerRows(scannerRows), [scannerRows]);
  const rowByTicker = useMemo(() => new Map(sourceRows.map((row) => [String(row.ticker), row])), [sourceRows]);
  const selectableWatchlists = watchlists.filter((row) => row.enabled && row.availability !== "integration_pending");
  const configuredWatchlistIds = Array.from(new Set(settings.watchlistIds)).filter((id) => selectableWatchlists.some((row) => row.watchlist_id === id));
  const visibleWatchlists = configuredWatchlistIds.map((id) => selectableWatchlists.find((row) => row.watchlist_id === id)).filter((row): row is DiscoveryWatchlist => Boolean(row));
  const watchlist = visibleWatchlists.find((row) => row.watchlist_id === settings.watchlistId) ?? visibleWatchlists[0];
  const availableWatchlists = watchlists.filter((row) => !visibleWatchlists.some((visible) => visible.watchlist_id === row.watchlist_id));
  const runtimeWatchlist = runtime?.watchlists?.find((row) => row.watchlist_id === watchlist?.watchlist_id);
  const runtimeMembers = runtimeWatchlist?.members ?? [];
  const runtimeReady = runtimeWatchlist !== undefined && ["ready", "degraded"].includes(runtimeWatchlist.status ?? runtime?.status ?? "");
  const resolvedSymbols = runtimeMembers.map((row) => String(row.ticker ?? row.symbol ?? "").trim().toUpperCase()).filter(Boolean);
  const runtimeMemberByTicker = new Map(runtimeMembers.map((row) => [String(row.ticker ?? "").trim().toUpperCase(), row]));
  const rows: ScreenerRow[] = resolvedSymbols.map((ticker) => ({
    ...(runtimeMemberByTicker.get(ticker) ?? {}),
    ...(rowByTicker.get(ticker) ?? {}),
    ticker,
  }));
  const linkedUniverseIds = universes.filter((row) => row.source === "watchlist" && row.scanner_view_id === watchlist?.watchlist_id).map((row) => row.universe_id);
  const linkedPlans = runPlans.filter((plan) => linkedUniverseIds.includes(plan.universe_id));
  const resolved = !watchlist || runtimeReady;
  const resolutionClock = runtime?.as_of ?? asOf;
  const resolving = ["awaiting_first_resolution", "building", "partial", "refreshing"].includes(runtimeWatchlist?.status ?? runtime?.status ?? "");
  const runtimeError = runtime?.status === "error" ? runtime.error || "The scanner did not return a Watchlist membership projection." : "";
  const unresolvedDetail = runtimeError
    || (runtime === null
      ? "This scanner snapshot did not include QMD Watchlist membership."
      : resolving
        ? "Waiting for the complete causal scanner snapshot used by this Watchlist."
        : runtime.status !== "ready" && runtime.status !== "degraded"
          ? `Membership projection is ${String(runtime.status || "unavailable").replaceAll("_", " ")}.`
          : `QMD Watchlist ${watchlist?.watchlist_id || "not selected"} has no membership snapshot.`);
  const columns = canonicalDiscoveryColumns([...(watchlist?.columns ?? ["symbol"]), ...settings.columns]);
  const selectWatchlist = (watchlistId: string) => onSettingsChange((current) => ({ columns: [], watchlistId, watchlistIds: current.watchlistIds }));
  const addWatchlist = (watchlistId: string) => {
    if (!watchlistId || !selectableWatchlists.some((row) => row.watchlist_id === watchlistId)) return;
    onSettingsChange((current) => ({ columns: [], watchlistId, watchlistIds: Array.from(new Set([...current.watchlistIds, watchlistId])) }));
    setAddingWatchlist(false);
  };
  const removeWatchlist = (watchlistId: string) => {
    onSettingsChange((current) => {
      const nextIds = current.watchlistIds.filter((id) => id !== watchlistId);
      const nextActiveId = watchlistId === current.watchlistId ? nextIds[0] ?? "" : current.watchlistId;
      return { columns: [], watchlistId: nextActiveId, watchlistIds: nextIds };
    });
  };
  return <section className="market-list-surface watchlist-surface" aria-label={`${watchlist?.name ?? "Watchlist"} watchlist`}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><Star size={12} /> QMD Watchlist</span><h3>{watchlist?.name ?? "No Watchlist open"}</h3><p>{watchlist ? resolved ? `${rows.length} eligible securities` : "Dynamic membership awaits its causal resolver" : "Add a configured QMD Watchlist to this container"}{watchlist ? <> · state at <MarketTime value={resolutionClock} /></> : null}</p></div>
      <span className="market-list-owner strategy">QMD</span>
    </header>
    <nav aria-label="QMD Watchlists" className="watchlist-tabs" role="tablist">
      {visibleWatchlists.map((row) => {
        const selected = row.watchlist_id === watchlist?.watchlist_id;
        return <span className={selected ? "active" : undefined} key={row.watchlist_id}><button aria-selected={selected} onClick={() => selectWatchlist(row.watchlist_id)} role="tab" title={row.description || row.name} type="button">{row.name}</button><button aria-label={`Remove ${row.name} tab`} className="watchlist-tab-remove" onClick={() => removeWatchlist(row.watchlist_id)} title={`Remove ${row.name} from this container`} type="button"><X size={10} /></button></span>;
      })}
      <button aria-expanded={addingWatchlist} aria-label="Add Watchlist tab" className="watchlist-tab-add" disabled={!availableWatchlists.length} onClick={() => setAddingWatchlist((open) => !open)} role="tab" title={availableWatchlists.length ? "Add a QMD Watchlist to this container" : "All configured Watchlists are already open"} type="button"><Plus size={12} /><span>Add</span></button>
    </nav>
    {addingWatchlist ? <div className="watchlist-tab-lookup"><InventoryFilterSelect ariaLabel="Watchlist to add" className="watchlist-add-lookup" onChange={addWatchlist} options={[{ description: "Select another configured QMD Watchlist for a persistent tab in this container.", label: "Choose a Watchlist", value: "" }, ...availableWatchlists.map((row) => ({ description: selectableWatchlists.some((candidate) => candidate.watchlist_id === row.watchlist_id) ? row.description : `${row.description || row.name} Unavailable until its Market Discovery integration is enabled.`, disabled: !selectableWatchlists.some((candidate) => candidate.watchlist_id === row.watchlist_id), label: row.name, value: row.watchlist_id }))]} searchable={availableWatchlists.length > 7} searchPlaceholder="Find a Watchlist…" showAllOnOpen value="" /><button onClick={() => { window.location.hash = "market-discovery-configuration"; }} type="button">Create or configure Watchlists <ArrowRight size={13} /></button></div> : null}
    <div className="watch-universe-context">
      <div><span>Description</span><strong>{watchlist?.description || "No Watchlist selected"}</strong></div>
      <div><span>Used by</span><strong>{linkedPlans.map((plan) => plan.name || plan.run_plan_id).join(", ") || "No Run Plan"}</strong></div>
      <button onClick={() => { window.location.hash = "market-discovery-configuration"; }} type="button">Configure in Market Discovery <ArrowRight size={13} /></button>
    </div>
    {watchlist && !resolved ? <div className="watch-universe-warning" data-error={!resolving ? "true" : "false"} role="status">{resolving ? <span className="loading-spinner" aria-hidden="true" /> : null}<span><strong>{resolving ? "Resolving membership" : "Membership unavailable"}</strong><small>{unresolvedDetail}</small></span></div> : null}
    {watchlist && resolved && runtime?.status === "degraded" ? <div className="watch-universe-warning" data-error="true" role="status"><span><strong>Membership resolved with publication warnings</strong><small>{runtime.target_errors?.[0]?.error || "The member list is available, but a downstream QMD computation target could not be updated."}</small></span></div> : null}
    <MarketListTable
      key={watchlist?.watchlist_id ?? "watchlist"}
      catalog={fieldCatalog}
      columns={columns}
      customColumns={settings.customColumns}
      empty={!watchlist ? "No Watchlist tabs are open. Use Add to choose one." : resolved ? "This QMD Watchlist currently has no members." : "No resolved membership is available."}
      limit={settings.limit}
      liveRecency={live}
      lockedColumns={canonicalDiscoveryColumns(watchlist?.columns ?? ["symbol"])}
      mergeCompanyWithIdentity
      onColumnsChange={(columns) => onSettingsChange({ columns })}
      onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
      onTickerSelect={onTickerSelect}
      rows={rows}
      title={`${watchlist?.name ?? "Watchlist"} watchlist`}
      viewStateKey={`watchlist:${watchlist?.watchlist_id ?? "none"}`}
    />
  </section>;
}

export function StrategyActivityContainer({ asOf, onSettingsChange, onTickerSelect, settings }: { asOf: string; onSettingsChange: (patch: Partial<StrategyActivitySettings>) => void; onTickerSelect: (ticker: string) => void; settings: StrategyActivitySettings }) {
  const [payload, setPayload] = useState<StrategyActivityResponse | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<StrategyActivityResponse>(`/api/trading/strategy-activity?as_of=${encodeURIComponent(asOf)}&limit=5000`, { signal: controller.signal, timeoutMs: 10000 })
      .then((response) => { setPayload(response); setError(""); })
      .catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [asOf]);
  const rows = useMemo(() => (payload?.rows ?? []).filter((row) =>
    (!settings.strategyId || String(row.strategy_id) === settings.strategyId)
    && (!settings.runId || String(row.run_id) === settings.runId)
    && (!settings.ticker || String(row.ticker) === settings.ticker)
    && (!settings.eventType || String(row.event_type) === settings.eventType)
  ), [payload, settings]);
  const strategies = useMemo(() => uniqueValues(payload?.rows ?? [], "strategy_id"), [payload]);
  const runs = useMemo(() => uniqueValues(payload?.rows ?? [], "run_id"), [payload]);
  const tickers = useMemo(() => uniqueValues(payload?.rows ?? [], "ticker"), [payload]);
  return <section className="market-list-surface strategy-activity-surface" aria-label="Strategy activity">
    <header className="market-list-heading"><div><span className="market-list-eyebrow"><FileCheck2 size={12} /> Durable runtime history</span><h3>Strategy Activity</h3><p>{rows.length} persisted events at or before <MarketTime value={asOf} /></p></div><span className="market-list-owner strategy">Trading Journal</span></header>
    <div className="strategy-activity-filters">
      <ActivityFilter label="Strategy" onChange={(strategyId) => onSettingsChange({ strategyId })} options={strategies} value={settings.strategyId} />
      <ActivityFilter label="Run" onChange={(runId) => onSettingsChange({ runId })} options={runs} value={settings.runId} />
      <ActivityFilter label="Ticker" onChange={(ticker) => onSettingsChange({ ticker })} options={tickers} value={settings.ticker} />
      <ActivityFilter label="Event" onChange={(eventType) => onSettingsChange({ eventType })} options={["signal", "decision", "campaign_state"]} value={settings.eventType} />
    </div>
    {error ? <div className="canvas-inline-error">Strategy activity unavailable: {error}</div> : <MarketListTable chronological columns={["event_time", "ticker", "event_type", "action", "state", "reason", "strategy_id", "run_id"]} customColumns={[]} empty="No persisted strategy events match these filters." limit={settings.limit} lockedColumns={[]} onColumnsChange={() => undefined} onCustomColumnsChange={() => undefined} onTickerSelect={onTickerSelect} rows={rows} title="Strategy activity" />}
  </section>;
}

function ActivityFilter({ label, onChange, options, value }: { label: string; onChange: (value: string) => void; options: string[]; value: string }) {
  return <label><span>{label}</span><select onChange={(event) => onChange(event.target.value)} value={value}><option value="">All</option>{options.map((option) => <option key={option} value={option}>{option.replaceAll("_", " ")}</option>)}</select></label>;
}

function uniqueValues(rows: ScreenerRow[], key: string) {
  return [...new Set(rows.map((row) => String(row[key] ?? "")).filter(Boolean))].sort();
}

function MarketListSurface({
  asOf,
  catalog = FIELD_CATALOG,
  columns,
  customColumns,
  empty,
  eyebrow,
  fieldCoverage,
  guide,
  limit,
  liveRecency = false,
  lockedColumns = [],
  onColumnsChange,
  onCustomColumnsChange,
  onPresetChange,
  onTickerSelect,
  preset,
  presets,
  rows,
  sortColumn,
  subtitle,
  title,
}: {
  asOf: string;
  catalog?: FieldDefinition[];
  columns: string[];
  customColumns: ScannerCustomColumn[];
  empty: string;
  eyebrow: string;
  fieldCoverage?: Record<string, number>;
  guide?: ReactNode;
  limit: number;
  liveRecency?: boolean;
  lockedColumns?: string[];
  onColumnsChange: (columns: string[]) => void;
  onCustomColumnsChange: (columns: ScannerCustomColumn[]) => void;
  onPresetChange: (preset: string) => void;
  onTickerSelect: (ticker: string) => void;
  preset: string;
  presets: string[];
  rows: ScreenerRow[];
  sortColumn?: string;
  subtitle: string;
  title: string;
}) {
  return <section className="market-list-surface" aria-label={title}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><ListFilter size={12} /> {eyebrow}</span><h3>{title}</h3><p>{subtitle} · <MarketTime value={asOf} /></p></div>
      <strong>{formatCompact(rows.length)} rows</strong>
    </header>
    {guide}
    {presets.length > 1 ? <nav className="market-list-presets" aria-label={`${title} views`}>{presets.map((item) => <button aria-pressed={preset === item} className={preset === item ? "active" : undefined} key={item} onClick={() => onPresetChange(item)} type="button">{item}</button>)}</nav> : null}
    <MarketListTable catalog={catalog} columns={columns} customColumns={customColumns} empty={empty} fieldCoverage={fieldCoverage} limit={limit} liveRecency={liveRecency} lockedColumns={lockedColumns} onColumnsChange={onColumnsChange} onCustomColumnsChange={onCustomColumnsChange} onTickerSelect={onTickerSelect} rows={rows} sortColumn={sortColumn} title={title} />
  </section>;
}

function MarketListTable({
  catalog = FIELD_CATALOG,
  chronological = false,
  columns,
  customColumns,
  empty,
  fieldCoverage,
  limit,
  liveRecency = false,
  lockedColumns = [],
  mergeCompanyWithIdentity = true,
  onColumnsChange,
  onCustomColumnsChange,
  onTickerSelect,
  recencyRail = false,
  rowAction,
  rows,
  sortColumn,
  title,
  viewStateKey,
}: {
  catalog?: FieldDefinition[];
  chronological?: boolean;
  columns: string[];
  customColumns: ScannerCustomColumn[];
  empty: string;
  fieldCoverage?: Record<string, number>;
  limit: number;
  liveRecency?: boolean;
  lockedColumns?: string[];
  mergeCompanyWithIdentity?: boolean;
  onColumnsChange: (columns: string[]) => void;
  onCustomColumnsChange: (columns: ScannerCustomColumn[]) => void;
  onTickerSelect?: (ticker: string) => void;
  recencyRail?: boolean;
  rowAction?: (row: ScreenerRow) => ReactNode;
  rows: ScreenerRow[];
  sortColumn?: string;
  title: string;
  viewStateKey?: string;
}) {
  const resolvedViewStateKey = viewStateKey ?? `market-list:${title}`;
  const cachedViewState = MARKET_LIST_VIEW_STATE.get(resolvedViewStateKey);
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [columnFilters, setColumnFilters] = useState<TableFilterCondition[]>(() => cachedViewState?.columnFilters ?? []);
  const [filterMatchMode, setFilterMatchMode] = useState<TableFilterMatchMode>(() => cachedViewState?.filterMatchMode ?? "all");
  const [filterPanelOpen, setFilterPanelOpen] = useState(() => cachedViewState?.filterPanelOpen ?? false);
  const [headerMenuColumn, setHeaderMenuColumn] = useState<string | null>(null);
  const [query, setQuery] = useState(() => cachedViewState?.query ?? "");
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" }>(() => cachedViewState?.sort ?? { column: chronological ? "event_time" : "change_pct", direction: "desc" });
  const wallClockMs = useWallClock();
  const headerMenuRef = useRef<HTMLDivElement | null>(null);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const identityColumn = columns.includes("symbol") ? "symbol" : "ticker";
  const effectiveLockedColumns = useMemo(() => [...new Set([...(chronological ? ["event_time"] : []), identityColumn, ...lockedColumns.filter((column) => column !== "logo" && column !== "company_name")])], [chronological, identityColumn, lockedColumns]);
  const selectedColumns = useMemo(() => withLockedColumns(columns, effectiveLockedColumns), [columns, effectiveLockedColumns]);
  const companyInIdentity = mergeCompanyWithIdentity;
  const tableColumns = useMemo(() => selectedColumns.filter((column) => column !== "logo" && !(companyInIdentity && column === "company_name")), [companyInIdentity, selectedColumns]);
  const filterColumns = useMemo<TableFilterColumn[]>(() => tableColumns.map((column) => tableFilterColumn(catalogField(column, customColumns, catalog))), [catalog, customColumns, tableColumns]);
  const filterColumnKey = filterColumns.map((column) => column.key).join("\u0000");
  useEffect(() => {
    MARKET_LIST_VIEW_STATE.set(resolvedViewStateKey, { columnFilters, filterMatchMode, filterPanelOpen, query, sort });
  }, [columnFilters, filterMatchMode, filterPanelOpen, query, resolvedViewStateKey, sort]);
  useEffect(() => {
    const visible = new Set(filterColumns.map((column) => column.key));
    setColumnFilters((current) => current.some((condition) => !visible.has(condition.column)) ? current.filter((condition) => visible.has(condition.column)) : current);
  }, [filterColumnKey]);
  useEffect(() => {
    if (sortColumn) setSort({ column: sortColumn, direction: sortColumn === "liquidity_rank" ? "asc" : "desc" });
  }, [sortColumn]);
  const visibleRows = useMemo(() => filterRowsByConditions(rows, columnFilters, filterColumns, filterMatchMode).filter((row) => {
    if (deferredQuery && !Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(deferredQuery))) return false;
    return true;
  }).sort((left, right) => compareValues(left[sort.column], right[sort.column]) * (sort.direction === "asc" ? 1 : -1)).slice(0, limit), [columnFilters, deferredQuery, filterColumns, filterMatchMode, limit, rows, sort]);
  const tickers = visibleRows
    .filter((row) => liveRecency || !String(row.logo_url ?? "").trim() || (companyInIdentity && !String(row.company_name ?? "").trim()))
    .map((row) => String(row.ticker ?? row.symbol ?? ""))
    .filter(Boolean);
  const presentations = useTickerPresentations(tickers, { includeMarketState: liveRecency, includeRecency: liveRecency });
  useEffect(() => {
    if (!headerMenuColumn) return;
    const dismiss = (event: PointerEvent) => {
      if (headerMenuRef.current?.contains(event.target as Node)) return;
      setHeaderMenuColumn(null);
    };
    document.addEventListener("pointerdown", dismiss, true);
    return () => document.removeEventListener("pointerdown", dismiss, true);
  }, [headerMenuColumn]);
  function changeSort(column: string, direction?: "asc" | "desc") {
    setSort((current) => ({ column, direction: direction ?? (current.column === column && current.direction === "desc" ? "asc" : "desc") }));
    setHeaderMenuColumn(null);
  }
  function moveColumn(column: string, target: "left" | "right" | "start" | "end") {
    const currentIndex = tableColumns.indexOf(column);
    if (currentIndex < 0 || effectiveLockedColumns.includes(column)) return;
    const unlocked = tableColumns.filter((item) => !effectiveLockedColumns.includes(item));
    const unlockedIndex = unlocked.indexOf(column);
    const nextIndex = target === "start" ? 0 : target === "end" ? unlocked.length - 1 : Math.max(0, Math.min(unlocked.length - 1, unlockedIndex + (target === "left" ? -1 : 1)));
    unlocked.splice(unlockedIndex, 1);
    unlocked.splice(nextIndex, 0, column);
    const nextColumns = withLockedColumns(unlocked, effectiveLockedColumns);
    const identityIndex = nextColumns.indexOf(identityColumn);
    if (companyInIdentity && identityIndex >= 0) nextColumns.splice(identityIndex + 1, 0, "company_name");
    onColumnsChange(nextColumns);
    setHeaderMenuColumn(null);
  }
  function removeColumn(column: string) {
    if (effectiveLockedColumns.includes(column)) return;
    onColumnsChange(selectedColumns.filter((item) => item !== column));
    if (isTechnicalKey(column)) onCustomColumnsChange(customColumns.filter((item) => item.key !== column));
    setHeaderMenuColumn(null);
  }
  function addTechnicalColumn(metric: TechnicalMetric) {
    const column = defaultTechnicalColumn(metric);
    const key = column.key;
    if (!customColumns.some((item) => item.key === key)) onCustomColumnsChange([...customColumns, column]);
    if (!selectedColumns.includes(key)) onColumnsChange(withLockedColumns([...selectedColumns.filter((item) => !effectiveLockedColumns.includes(item)), key], effectiveLockedColumns));
  }
  function changeTechnicalTimeframe(column: string, nextTimeframe: ScannerTimeframe) {
    const existing = customColumns.find((item) => item.key === column);
    if (!existing) return;
    const key = technicalColumnKey(existing.metric, nextTimeframe);
    const nextColumns = columns.map((item) => item === column ? key : item).filter((item, index, values) => values.indexOf(item) === index);
    const nextCustom = customColumns.filter((item) => item.key !== column && item.key !== key);
    onCustomColumnsChange([...nextCustom, { key, metric: existing.metric, timeframe: nextTimeframe }]);
    onColumnsChange(nextColumns);
    setHeaderMenuColumn(key);
  }
  function changeTechnicalAnchor(column: string, nextAnchor: ScannerSessionAnchor) {
    const existing = customColumns.find((item) => item.key === column);
    if (!existing || !["vwap", "vwap_distance_pct"].includes(existing.metric)) return;
    const source = existing.source ?? "hlc3";
    const key = technicalColumnKey(existing.metric, nextAnchor, source);
    const nextColumns = columns.map((item) => item === column ? key : item).filter((item, index, values) => values.indexOf(item) === index);
    const nextCustom = customColumns.filter((item) => item.key !== column && item.key !== key);
    onCustomColumnsChange([...nextCustom, { anchor: nextAnchor, key, metric: existing.metric, source }]);
    onColumnsChange(nextColumns);
    setHeaderMenuColumn(key);
  }
  function changeTechnicalSource(column: string, nextSource: ScannerVwapSource) {
    const existing = customColumns.find((item) => item.key === column);
    if (!existing || !["vwap", "vwap_distance_pct"].includes(existing.metric)) return;
    const anchor = existing.anchor ?? "extended_session";
    const key = technicalColumnKey(existing.metric, anchor, nextSource);
    const nextColumns = columns.map((item) => item === column ? key : item).filter((item, index, values) => values.indexOf(item) === index);
    const nextCustom = customColumns.filter((item) => item.key !== column && item.key !== key);
    onCustomColumnsChange([...nextCustom, { anchor, key, metric: existing.metric, source: nextSource }]);
    onColumnsChange(nextColumns);
    setHeaderMenuColumn(key);
  }
  return <div className="market-list-table-shell">
    <div className="market-list-toolbar-stack"><div className="market-list-toolbar">
      <label className="market-list-search"><Search size={14} /><input aria-label={`Search ${title}`} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols and values" value={query} /></label>
      <TableColumnFilterControl columns={filterColumns} conditions={columnFilters} matchMode={filterMatchMode} onChange={setColumnFilters} onMatchModeChange={setFilterMatchMode} onOpenChange={setFilterPanelOpen} open={filterPanelOpen} rows={rows} title={title} />
      <span>{visibleRows.length} of {rows.length}</span>
      <button aria-expanded={columnPickerOpen} className="market-list-columns-button" onClick={() => setColumnPickerOpen((open) => !open)} type="button"><Columns3 size={14} /> Columns <b>{selectedColumns.length}</b></button>
    </div><TableActiveFilterBar columns={filterColumns} conditions={columnFilters} onChange={setColumnFilters} /></div>
    <div className="market-list-table-scroll"><table className={`market-list-table${companyInIdentity ? " with-company-identity" : ""}`}><thead><tr>{tableColumns.map((column) => { const definition = catalogField(column, customColumns, catalog); const sorted = sort.column === column; const className = columnClass(column, definition); const menuOpen = headerMenuColumn === column; return column === "logo" ? <th aria-label="Ticker logo" className={className} key={column} /> : <th aria-sort={sorted ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} className={className} data-menu-open={menuOpen ? "true" : undefined} key={column}><button aria-expanded={menuOpen} aria-label={`Configure ${definition.label} column`} onClick={() => setHeaderMenuColumn((current) => current === column ? null : column)} title={`Configure ${definition.label}`} type="button"><span>{definition.label}</span>{sorted ? sort.direction === "asc" ? <ArrowUp size={13} /> : <ArrowDown size={13} /> : <ChevronDown size={13} />}</button>{menuOpen ? <ColumnHeaderMenu column={column} definition={definition} locked={effectiveLockedColumns.includes(column)} onAnchorChange={(value) => changeTechnicalAnchor(column, value)} onMove={(target) => moveColumn(column, target)} onRemove={() => removeColumn(column)} onSort={(direction) => changeSort(column, direction)} onSourceChange={(value) => changeTechnicalSource(column, value)} onTimeframeChange={(value) => changeTechnicalTimeframe(column, value)} ref={headerMenuRef} /> : null}</th>; })}{rowAction ? <th aria-label="Row actions" /> : null}</tr></thead><tbody>{visibleRows.length ? visibleRows.map((row, index) => { const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase(); const selectable = Boolean(ticker && onTickerSelect); const select = () => { if (selectable) onTickerSelect?.(ticker); }; return <tr aria-label={selectable ? `Open ${ticker} Charts & Quotes` : undefined} data-recency={recencyRail ? eventRecency(row.event_time, wallClockMs) : undefined} data-selectable={selectable ? "true" : undefined} key={`${ticker || "row"}:${row.event_time ?? index}:${index}`} onClick={(event) => { if (!(event.target as HTMLElement).closest("button, input, select, a")) select(); }} onKeyDown={(event) => { if (selectable && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(); } }} tabIndex={selectable ? 0 : undefined}>{tableColumns.map((column) => { const definition = catalogField(column, customColumns, catalog); return <td className={`${toneClass(row[column], column, customColumns, catalog)} ${columnClass(column, definition)}`.trim()} key={column}>{renderMarketCell(row, column, presentations, customColumns, catalog, companyInIdentity)}</td>; })}{rowAction ? <td className="market-list-row-action">{rowAction(row)}</td> : null}</tr>; }) : <tr><td className="market-list-empty" colSpan={tableColumns.length + (rowAction ? 1 : 0)}>{empty}</td></tr>}</tbody></table></div>
    {columnPickerOpen ? <ColumnPicker catalog={catalog} columns={selectedColumns} customColumns={customColumns} fieldCoverage={fieldCoverage} lockedColumns={effectiveLockedColumns} onAddTechnical={addTechnicalColumn} onChange={onColumnsChange} onClose={() => setColumnPickerOpen(false)} /> : null}
  </div>;
}

const ColumnHeaderMenu = forwardRef<HTMLDivElement, {
  column: string;
  definition: FieldDefinition;
  locked: boolean;
  onAnchorChange: (anchor: ScannerSessionAnchor) => void;
  onMove: (target: "left" | "right" | "start" | "end") => void;
  onRemove: () => void;
  onSort: (direction: "asc" | "desc") => void;
  onSourceChange: (source: ScannerVwapSource) => void;
  onTimeframeChange: (timeframe: ScannerTimeframe) => void;
}>(function ColumnHeaderMenu({ column, definition, locked, onAnchorChange, onMove, onRemove, onSort, onSourceChange, onTimeframeChange }, ref) {
  return <div aria-label={`${definition.label} column tools`} className="market-column-header-menu" ref={ref}>
    <header>
      <div><strong>{definition.label}</strong><span>{definition.description}</span></div>
      {definition.scope === "interval" && definition.timeframe ? <label><span>Interval</span><select aria-label={`${definition.label} interval`} onChange={(event) => onTimeframeChange(event.target.value as ScannerTimeframe)} value={definition.timeframe}>{(definition.timeframes ?? SCANNER_TIMEFRAMES).map((value) => <option key={value} value={value}>{timeframeLabel(value)}</option>)}</select></label> : null}
      {definition.scope === "session" && definition.anchor ? <label><span>Anchor</span><select aria-label={`${definition.label} anchor`} onChange={(event) => onAnchorChange(event.target.value as ScannerSessionAnchor)} value={definition.anchor}><option value="extended_session">Extended session</option><option value="regular_session">Regular session</option></select></label> : null}
      {definition.scope === "session" && definition.source ? <label><span>Source</span><select aria-label={`${definition.label} source`} onChange={(event) => onSourceChange(event.target.value as ScannerVwapSource)} value={definition.source}><option value="hlc3">HLC3 · 1m source bars</option><option value="trade_price">Exact trade prices</option></select></label> : null}
      {definition.scope === "relative-volume" ? <div className="market-column-formula-note"><span>Anchor</span><strong>Extended session</strong><span>Baseline</span><strong>{definition.lookbackSessions ?? 20} completed sessions</strong></div> : null}
    </header>
    <section><button onClick={() => onSort("asc")} type="button"><ArrowUp size={14} /> Sort ascending</button><button onClick={() => onSort("desc")} type="button"><ArrowDown size={14} /> Sort descending</button></section>
    {!locked ? <><section><button onClick={() => onMove("left")} type="button"><ArrowLeft size={14} /> Move left</button><button onClick={() => onMove("right")} type="button"><ArrowRight size={14} /> Move right</button><button onClick={() => onMove("start")} type="button"><ChevronLeft size={14} /> Move to start</button><button onClick={() => onMove("end")} type="button"><ChevronLeft size={14} className="flip-horizontal" /> Move to end</button></section><section><button className="danger" onClick={onRemove} type="button"><Trash2 size={14} /> Remove column</button></section></> : null}
    <small data-column={column}>Computed causally at the workspace clock.</small>
  </div>;
});

function ColumnPicker({
  catalog = FIELD_CATALOG,
  columns,
  customColumns,
  fieldCoverage,
  lockedColumns = [],
  onAddTechnical,
  onChange,
  onClose,
}: {
  catalog?: FieldDefinition[];
  columns: string[];
  customColumns: ScannerCustomColumn[];
  fieldCoverage?: Record<string, number>;
  lockedColumns?: string[];
  onAddTechnical: (metric: TechnicalMetric) => void;
  onChange: (columns: string[]) => void;
  onClose: () => void;
}) {
  const customDefinitions = customColumns.map(customField);
  const groups = [...new Set([...catalog.map((item) => item.group), "Technicals", ...(customDefinitions.length ? ["Custom"] : [])])];
  const [group, setGroup] = useState(groups[0]);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const availableDefinitions = [...catalog, ...TECHNICAL_METRICS.map((item) => ({ ...item, group: "Technicals", key: `template:${item.metric}` } as FieldDefinition)), ...customDefinitions];
  const matches = availableDefinitions.filter((item) => (!deferredQuery || `${item.label} ${item.key} ${item.description}`.toLowerCase().includes(deferredQuery)) && (deferredQuery || item.group === group));
  function toggle(key: string) { if (lockedColumns.includes(key)) return; onChange(columns.includes(key) ? columns.filter((column) => column !== key) : [...columns, key]); }
  return <aside aria-label="Add scanner columns" className="market-column-picker">
    <header><div><strong>Columns</strong><span>{columns.length} selected</span></div><button aria-label="Close columns" onClick={onClose} type="button"><X size={15} /></button></header>
    <label><Search size={14} /><input autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Search every available field" value={query} /></label>
    <div className="market-column-picker-body">
      {!deferredQuery ? <nav>{groups.map((item) => <button className={group === item ? "active" : undefined} key={item} onClick={() => setGroup(item)} type="button"><span>{item}</span><b>{availableDefinitions.filter((fieldItem) => fieldItem.group === item).length}</b></button>)}</nav> : null}
      <section className={deferredQuery ? "search-results" : undefined}><button className="market-column-back" onClick={() => { setQuery(""); setGroup(groups[0]); }} type="button"><ChevronLeft size={14} /> {deferredQuery ? "All groups" : group}</button>{matches.map((item) => { const template = item.key.startsWith("template:"); const templateColumn = template && item.metric ? defaultTechnicalColumn(item.metric) : null; const selectedKey = templateColumn?.key ?? item.key; const locked = lockedColumns.includes(selectedKey); const coverage = fieldCoverage?.[selectedKey]; const selected = columns.includes(selectedKey); return <button aria-disabled={locked} className={`${selected ? "selected" : ""}${locked ? " locked" : ""}`.trim()} key={item.key} onClick={() => template && item.metric ? selected ? toggle(selectedKey) : onAddTechnical(item.metric) : toggle(item.key)} type="button"><i>{selected ? <Check size={12} /> : null}</i><span><strong>{item.label}{!template && technicalScopeLabel(item) ? <small className="market-column-inline-timeframe">{technicalScopeLabel(item)}</small> : null}</strong><small>{item.description}</small></span><em data-kind={item.kind}>{locked ? "pinned" : coverage !== undefined ? `${coverage}%` : item.kind}</em></button>; })}</section>
    </div>
  </aside>;
}

function normalizeScannerRows(rows: ScreenerRow[]) {
  return rows.map((row) => {
    const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase();
    const lastSource = row.last_price ?? row.last ?? row.snapshot_last_price ?? row.close;
    const volumeSource = row.volume ?? row.day_volume ?? row.last_day_volume_so_far;
    const last = numberValue(lastSource);
    const volume = numberValue(volumeSource);
    return {
      ...row,
      symbol: ticker,
      last_price: lastSource,
      market_event_at: row.market_event_at ?? row.last_event_ts ?? row.bar_end ?? row.bar_time_market,
      spread_bps: row.spread_bps ?? row.spread_bps_abs,
      volume: volumeSource,
      dollar_volume: row.dollar_volume ?? (last > 0 && volume > 0 ? last * volume : undefined),
      microstructure_unified_confidence_pct: numberValue(row.microstructure_unified_confidence) * 100,
      flow_structure_composite_confidence_pct: numberValue(row.flow_structure_composite_confidence) * 100,
      qmd_structure_confidence_pct: numberValue(row.qmd_structure_confidence) * 100,
      signal_confidence_pct: numberValue(row.signal_confidence) * 100,
      ticker,
    };
  });
}

function renderMarketCell(row: ScreenerRow, column: string, presentations: ReturnType<typeof useTickerPresentations>, customColumns: ScannerCustomColumn[], catalog = FIELD_CATALOG, companyInIdentity = false) {
  const value = row[column];
  const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase();
  if (column === "ticker" || column === "symbol") {
    const companyName = companyInIdentity ? String(row.company_name ?? presentations[ticker]?.issuer_name ?? "").trim() : "";
    return <SecurityIdentityCell companyName={companyName} country={String(row.country ?? presentations[ticker]?.country ?? "")} halted={row.market_is_halted ?? row.is_halted ?? row.trading_status ?? presentations[ticker]?.market_is_halted ?? presentations[ticker]?.trading_status} logoUrl={String(row.logo_url ?? presentations[ticker]?.logo_url ?? "")} newsRecency={preferRecentRecency(row.live_news_recency, presentations[ticker]?.live_news_recency)} secRecency={preferRecentRecency(row.sec_recency, presentations[ticker]?.sec_recency)} ticker={ticker} />;
  }
  if (column === "event_time") return value ? <MarketTime includeSeconds value={String(value)} /> : "—";
  const definition = catalogField(column, customColumns, catalog);
  const presentationValueType = definition.presentationValueType ?? presentationForColumn(column).presentationValueType;
  if (presentationValueType === "category" || presentationValueType === "boolean" || ["direction", "source"].includes(column)) return <CategoryBadge column={column} value={value} />;
  if (column === "news_labels" || column === "sec_labels") {
    const labels = rowLabels(value);
    return labels.length ? <span className="market-list-label-badges" data-source={column === "news_labels" ? "news" : "sec"} title={labels.join(", ")}>{labels.slice(0, 1).map((labelValue) => <span key={labelValue}>{labelValue}</span>)}{labels.length > 1 ? <span className="market-list-label-overflow">+{labels.length - 1}</span> : null}</span> : <span className="market-list-unavailable">—</span>;
  }
  if (value === null || value === undefined || value === "") return <span className="market-list-unavailable" title={`${definition.label} is not available from the active source at this clock.`}>—</span>;
  if (definition.presentationValueType === "date") return <PresentedValue column={column} presentation={{ presentationValueType: "date" }} value={value} />;
  if (definition.presentationValueType === "datetime" || definition.format === "date") return <MarketTime includeDate value={String(value)} />;
  const numeric = numberValue(value);
  const semanticTone = toneClass(value, column, customColumns, catalog);
  if (definition.format === "percent") return marketNumber(formatPercent(numeric, true), numeric, definition, semanticTone);
  if (definition.format === "percentPlain") return marketNumber(formatPercent(numeric), numeric, definition, semanticTone);
  if (definition.format === "money") return marketNumber(formatMoney(numeric), numeric, definition, semanticTone);
  if (definition.format === "integer") return marketNumber(formatCompact(numeric), numeric, definition, semanticTone);
  if (definition.format === "multiple") return marketNumber(`${formatDecimal(numeric, Math.abs(numeric) < 10 ? 2 : 1)}\u00d7`, numeric, definition, semanticTone);
  if (definition.format === "number") return marketNumber(formatDecimal(numeric), numeric, definition, semanticTone);
  if (definition.format === "score") return marketNumber(formatDecimal(numeric, 0), numeric, definition, semanticTone);
  return <PresentedValue column={column} presentation={{ presentationValueType: definition.presentationValueType }} value={value} />;
}

function preferRecentRecency(frozenValue: unknown, liveValue: unknown) {
  const frozen = String(frozenValue ?? "").trim().toLowerCase();
  return frozen === "hot" || frozen === "cold" ? frozenValue : liveValue ?? frozenValue;
}

function marketNumber(display: string, value: number, definition: FieldDefinition, tone = "") {
  const exact = new Intl.NumberFormat("en-US", { maximumFractionDigits: 8 }).format(value);
  const resolvedTone = tone || (definition.format === "percent" && value !== 0 ? (value > 0 ? "positive" : "negative") : "neutral");
  return <span className="market-list-number table-number" data-importance={presentationForColumn(definition.key).importance} data-tone={resolvedTone} title={`${definition.label}: ${exact}`}>{display}</span>;
}

function toneClass(value: unknown, column: string, customColumns: ScannerCustomColumn[] = [], catalog = FIELD_CATALOG) {
  const numeric = numberValue(value);
  const definition = catalogField(column, customColumns, catalog);
  if (["change_pct", "change_5m_pct", "gap_pct", "magnitude", "qmd_signal"].includes(column) || ["change_pct", "vwap_distance_pct"].includes(definition.metric ?? "")) return numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral";
  if (definition.metric === "relative_volume") return numeric >= 1.5 ? "positive" : numeric < 0.75 ? "muted" : "neutral";
  if (definition.format === "score") return numeric >= 65 ? "positive" : numeric < 45 ? "negative" : "neutral";
  if (["fundamental_free_cash_flow", "fundamental_gross_margin_pct", "fundamental_operating_margin_pct", "fundamental_net_margin_pct", "fundamental_free_cash_flow_margin_pct", "fundamental_return_on_assets_pct", "fundamental_return_on_equity_pct", "fundamental_working_capital", "fundamental_interest_coverage", "fundamental_revenue_growth_pct", "fundamental_earnings_growth_pct", "fundamental_cash_conversion"].includes(column)) return numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral";
  if (["fundamental_share_growth_pct", "fundamental_dilution_pct", "share_base_pressure_pct", "fundamental_net_debt"].includes(column)) return numeric < 0 ? "positive" : numeric > 0 ? "negative" : "neutral";
  const text = String(value ?? "").toLowerCase();
  if (["xbrl_quality_label", "financial_trajectory_label"].includes(column)) {
    if (["strong", "robust", "improving"].includes(text)) return "positive";
    if (["weak", "deteriorating", "fragile"].includes(text)) return "negative";
    return "neutral";
  }
  if (text === "bullish") return "positive";
  if (text === "bearish") return "negative";
  return "";
}

function catalogField(key: string, customColumns: ScannerCustomColumn[] = [], catalog = FIELD_CATALOG) {
  const definition = catalog.find((item) => item.key === key) ?? FIELD_CATALOG.find((item) => item.key === key);
  if (definition) return definition;
  const custom = customColumns.find((item) => item.key === key);
  return custom ? customField(custom) : field(key, label(key), "Other", "raw", "text", "Available source field.");
}
function canonicalDiscoveryColumns(columns: string[]) {
  return [...new Set(columns.map((column) => column === "ticker" ? "symbol" : column === "last" ? "last_price" : column))];
}
function tableFilterColumn(definition: FieldDefinition): TableFilterColumn {
  const presentation = definition.presentationValueType ?? presentationForColumn(definition.key).presentationValueType;
  const numeric = ["integer", "money", "multiple", "number", "percent", "percentPlain", "score"].includes(definition.format)
    || ["basis_points", "integer", "money", "percent", "price", "quantity", "ratio", "score"].includes(presentation);
  const temporal = definition.format === "date" || ["date", "datetime", "time"].includes(presentation);
  return {
    description: definition.description,
    key: definition.key,
    kind: numeric ? "number" : temporal ? "datetime" : presentation === "boolean" ? "boolean" : presentation === "category" ? "category" : "text",
    label: definition.label,
    temporalUnit: presentation === "date" ? "date" : "datetime",
  };
}

function eventRecency(value: unknown, asOfMs: number) {
  const timestamp = String(value ?? "");
  return Number.isFinite(Date.parse(timestamp)) ? timeRecency(timestamp, asOfMs) : "unknown";
}
function withLockedColumns(columns: string[], lockedColumns: string[]) {
  return [...lockedColumns, ...columns.filter((column) => !lockedColumns.includes(column))];
}
function columnClass(column: string, definition = catalogField(column)) {
  const identityClass = column === "logo" ? "market-list-logo-column" : column === "ticker" || column === "symbol" ? "market-list-symbol-column" : column === "news_labels" || column === "sec_labels" ? "market-list-label-column" : "";
  const numericClass = ["integer", "money", "multiple", "number", "percent", "percentPlain", "score"].includes(definition.format) ? "market-list-numeric-column" : "";
  const timeClass = definition.format === "date" || ["date", "datetime", "time"].includes(definition.presentationValueType ?? "") ? "market-list-time-column" : "";
  return `${identityClass} ${numericClass} ${timeClass} ${tableCellClass(column, { presentationValueType: definition.presentationValueType })}`.trim();
}
function rowLabels(value: unknown) { return [...new Set(String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean))]; }
function field(key: string, labelValue: string, group: string, kind: FieldKind, format: FieldDefinition["format"], description: string): FieldDefinition { return { description, format, group, key, kind, label: labelValue }; }
function technicalMetric(metric: TechnicalMetric, labelValue: string, format: FieldDefinition["format"], description: string, scope: FieldDefinition["scope"]) {
  return { description, format, group: "Technicals", kind: "derived" as const, label: labelValue, metric, scope };
}
function intervalTechnicalMetric(metric: TechnicalMetric, labelValue: string, format: FieldDefinition["format"], description: string, timeframes = SCANNER_TIMEFRAMES) {
  return { ...technicalMetric(metric, labelValue, format, description, "interval"), timeframes };
}
function technicalColumnKey(metric: TechnicalMetric, parameter: ScannerSessionAnchor | ScannerTimeframe, source?: ScannerVwapSource) { return `technical__${metric}__${parameter}${source ? `__${source}` : ""}`; }
function isTechnicalKey(key: string) { return key.startsWith("technical__"); }
function customField(column: ScannerCustomColumn): FieldDefinition {
  const definition = TECHNICAL_METRICS.find((item) => item.metric === column.metric);
  return {
    description: definition?.description ?? "Causal technical scanner field.",
    format: definition?.format ?? "number",
    group: "Custom",
    key: column.key,
    kind: "derived",
    label: definition?.label ?? label(column.metric),
    metric: column.metric,
    anchor: column.anchor,
    lookbackSessions: column.lookbackSessions,
    scope: definition?.scope,
    source: column.source,
    timeframe: column.timeframe,
    timeframes: definition?.timeframes,
  };
}
function metricTimeframe(metric: TechnicalMetric, requested: ScannerTimeframe) {
  const supported = TECHNICAL_METRICS.find((item) => item.metric === metric)?.timeframes ?? SCANNER_TIMEFRAMES;
  return supported.includes(requested) ? requested : supported[0];
}
function defaultTechnicalColumn(metric: TechnicalMetric): ScannerCustomColumn {
  const definition = TECHNICAL_METRICS.find((item) => item.metric === metric);
  if (definition?.scope === "session") {
    const anchor: ScannerSessionAnchor = "extended_session";
    const source: ScannerVwapSource = "hlc3";
    return { anchor, key: technicalColumnKey(metric, anchor, source), metric, source };
  }
  if (definition?.scope === "relative-volume") {
    return { anchor: "extended_session", key: technicalColumnKey(metric, "extended_session"), lookbackSessions: 20, metric };
  }
  const timeframe = metricTimeframe(metric, DEFAULT_SCANNER_TECHNICAL_TIMEFRAME);
  return { key: technicalColumnKey(metric, timeframe), metric, timeframe };
}
function technicalScopeLabel(definition: FieldDefinition) {
  if (definition.scope === "interval" && definition.timeframe) return timeframeLabel(definition.timeframe);
  if (definition.scope === "session" && definition.anchor) return `${definition.anchor === "regular_session" ? "RTH" : "XH"} · ${definition.source === "trade_price" ? "trades" : "HLC3"}`;
  if (definition.scope === "relative-volume") return `${definition.lookbackSessions ?? 20}D session`;
  return null;
}
function timeframeLabel(value: ScannerTimeframe) {
  return value === "1d" ? "1 day" : value === "1h" ? "1 hour" : value.endsWith("m") ? `${value.slice(0, -1)} min` : value;
}
function reportedFundamentalFields(): FieldDefinition[] {
  const definitions: Array<[string, string, FieldDefinition["format"], string]> = [
    ["fundamental_revenue", "Revenue", "money", "Latest comparable SEC-reported revenue."],
    ["fundamental_gross_profit", "Gross profit", "money", "Latest comparable SEC-reported gross profit."],
    ["fundamental_operating_income", "Operating income", "money", "Latest comparable SEC-reported operating income."],
    ["fundamental_net_income", "Net income", "money", "Latest comparable SEC-reported net income."],
    ["fundamental_diluted_eps", "Diluted EPS", "number", "Latest comparable SEC-reported diluted earnings per share."],
    ["fundamental_operating_cash_flow", "Operating cash flow", "money", "Latest comparable SEC-reported cash flow from operations."],
    ["fundamental_capital_expenditure", "Capital expenditure", "money", "Latest comparable SEC-reported capital expenditure."],
    ["fundamental_cash", "Cash", "money", "Latest SEC-reported cash and cash equivalents."],
    ["fundamental_current_assets", "Current assets", "money", "Latest SEC-reported current assets."],
    ["fundamental_current_liabilities", "Current liabilities", "money", "Latest SEC-reported current liabilities."],
    ["fundamental_accounts_receivable", "Accounts receivable", "money", "Latest SEC-reported accounts receivable."],
    ["fundamental_accounts_payable", "Accounts payable", "money", "Latest SEC-reported accounts payable."],
    ["fundamental_inventory", "Inventory", "money", "Latest SEC-reported inventory."],
    ["fundamental_assets", "Total assets", "money", "Latest SEC-reported total assets."],
    ["fundamental_liabilities", "Total liabilities", "money", "Latest SEC-reported total liabilities."],
    ["fundamental_stockholders_equity", "Stockholders' equity", "money", "Latest SEC-reported stockholders' equity."],
    ["fundamental_long_term_debt", "Long-term debt", "money", "Latest SEC-reported long-term debt."],
    ["fundamental_current_debt", "Current debt", "money", "Latest SEC-reported current debt."],
    ["fundamental_research_development", "R&D expense", "money", "Latest comparable SEC-reported research and development expense."],
    ["fundamental_sga_expense", "SG&A expense", "money", "Latest comparable SEC-reported selling, general, and administrative expense."],
    ["fundamental_stock_based_compensation", "Stock compensation", "money", "Latest comparable SEC-reported stock-based compensation."],
    ["fundamental_interest_expense", "Interest expense", "money", "Latest comparable SEC-reported interest expense."],
    ["fundamental_income_tax_expense", "Income tax expense", "money", "Latest comparable SEC-reported income tax expense."],
    ["fundamental_effective_tax_rate_pct", "Effective tax rate", "number", "Latest SEC-reported effective tax-rate value; inspect the filing unit before cross-issuer comparison."],
    ["fundamental_goodwill", "Goodwill", "money", "Latest SEC-reported goodwill."],
    ["fundamental_intangible_assets", "Intangible assets", "money", "Latest SEC-reported intangible assets."],
    ["fundamental_deferred_revenue", "Deferred revenue", "money", "Latest SEC-reported deferred revenue."],
    ["fundamental_debt_issued", "Debt issued", "money", "Latest comparable SEC-reported debt issuance."],
    ["fundamental_debt_repaid", "Debt repaid", "money", "Latest comparable SEC-reported debt repayment."],
    ["fundamental_common_stock_issuance", "Common stock issued", "money", "Latest comparable SEC-reported proceeds from common-stock issuance."],
    ["fundamental_common_shares_outstanding", "Common shares", "integer", "Latest SEC-reported common shares outstanding."],
    ["fundamental_weighted_average_basic_shares", "Basic weighted shares", "integer", "Latest comparable SEC-reported weighted-average basic shares."],
    ["fundamental_weighted_average_diluted_shares", "Diluted weighted shares", "integer", "Latest comparable SEC-reported weighted-average diluted shares."],
    ["fundamental_sec_public_float_value", "SEC public float", "money", "Latest SEC-reported public-float value; this is a dollar value, not a share count."],
    ["fundamental_dividends_per_share", "Dividends per share", "number", "Latest comparable SEC-reported dividends per share."],
    ["fundamental_share_repurchases", "Share repurchases", "money", "Latest comparable SEC-reported share-repurchase value."],
    ["fundamental_repurchased_shares", "Repurchased shares", "integer", "Latest comparable SEC-reported number of repurchased shares."],
  ];
  return definitions.map(([key, labelValue, format, description]) => field(key, labelValue, "Reported fundamentals", "raw", format, description));
}
function label(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()); }
function numberValue(value: unknown) { const numeric = Number(value); return Number.isFinite(numeric) ? numeric : 0; }
function compareValues(left: unknown, right: unknown) { const leftNumber = Number(left); const rightNumber = Number(right); if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber; return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true }); }
function formatCompact(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: Math.abs(value) >= 1000 ? "compact" : "standard" }).format(value); }
function formatMoney(value: number) { if (!Number.isFinite(value)) return "—"; const compact = Math.abs(value) >= 100_000; return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: compact ? 1 : value < 10 ? 4 : 2, notation: compact ? "compact" : "standard", style: "currency" }).format(value); }
function formatDecimal(value: number, maximumFractionDigits?: number) {
  const digits = maximumFractionDigits ?? (Math.abs(value) < 0.01 ? 4 : Math.abs(value) < 1 ? 3 : Math.abs(value) < 100 ? 2 : 1);
  return new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);
}
function formatPercent(value: number, signed = false) {
  const digits = Math.abs(value) < 1 ? 2 : 1;
  return `${signed && value > 0 ? "+" : ""}${new Intl.NumberFormat("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(value)}%`;
}
