import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowUpDown, Check, ChevronDown, ChevronLeft, Columns3, FileCheck2, Filter, Flame, ListFilter, Plus, Search, Star, Trash2, X } from "lucide-react";
import { forwardRef, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { api } from "../../api/client";
import { MarketTime } from "./MarketTime";
import { TickerLogo, useTickerPresentations } from "./TickerIdentity";

export type ScreenerRow = Record<string, unknown>;
export type ScannerSnapshotMeta = {
  complete_universe?: boolean;
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
export type SignalStreamSettings = TechnicalListSettings & { limit: number; preset: string };
export type WatchUniverseSettings = TechnicalListSettings & { limit: number; universeId: string };
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
type WatchUniverseCatalogResponse = { run_plans?: { plans?: Array<{ name?: string; run_plan_id: string; universe_id: string }>; universes?: WatchUniverseDefinition[] } };
type WatchlistRuntimeResponse = {
  as_of?: string;
  status?: "awaiting_first_resolution" | "degraded" | "ready" | string;
  watchlists?: Array<{ member_count?: number; members?: ScreenerRow[]; watchlist_id: string }>;
};
type SignalMethod = { key: string; label: string; signal_version: number; status: string; compute_mode: string; working_timeframes: string[]; confirmation_timeframes: string[]; trigger_rules: string[]; rationale: string; domain?: string; producer?: string; input_basis?: string; evaluation_mode?: string; update_trigger?: string; publication_cadence?: string; publication_interval_ms?: number | null; score_required?: boolean; rank_score_required?: boolean };

type FieldKind = "derived" | "estimated" | "raw";
type FieldDefinition = {
  description: string;
  format: "date" | "integer" | "money" | "multiple" | "number" | "percent" | "percentPlain" | "score" | "text";
  group: string;
  key: string;
  kind: FieldKind;
  label: string;
  metric?: TechnicalMetric;
  anchor?: ScannerSessionAnchor;
  lookbackSessions?: number;
  scope?: "interval" | "relative-volume" | "session";
  source?: ScannerVwapSource;
  timeframe?: ScannerTimeframe;
  timeframes?: ScannerTimeframe[];
};

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

const SCANNER_PRESETS: Record<string, string[]> = {
  Overview: ["ticker", "last", "change_pct", "change_5m_pct", "volume", "trade_count", "news_labels", "sec_labels"],
  Momentum: ["ticker", "last", "change_5m_pct", "change_pct", "dollar_volume", "trade_count", "quote_count"],
  Intelligence: ["ticker", "last", "change_pct", "live_news_count", "sec_count", "news_labels", "sec_labels"],
  Fundamentals: ["ticker", "xbrl_quality_score", "financial_trajectory_score", "xbrl_profitability_score", "xbrl_growth_score", "xbrl_cash_quality_score", "xbrl_balance_sheet_score", "xbrl_capital_discipline_score", "fundamental_revenue_growth_pct", "fundamental_operating_margin_pct", "valuation_pe"],
  Signals: ["ticker", "signal_domain", "signal_producer", "signal_type", "direction", "signal_score", "signal_rank_score", "signal_confidence_pct", "active_signal_count", "working_timeframe", "input_basis", "update_trigger", "evidence"],
  Indicators: ["ticker", "indicator_type", "indicator_producer", "indicator_timeframe", "flow_structure_composite_score", "flow_structure_composite_confidence_pct", "flow_structure_composite_bias", "flow_structure_composite_reason", "microstructure_unified_signal", "microstructure_unified_confidence_pct", "microstructure_signed_volume_imbalance", "microstructure_level1_ofi", "microstructure_queue_imbalance", "qmd_structure_score", "qmd_structure_confidence_pct"],
};
const LEGACY_SCANNER_PRESET_COLUMNS: Record<string, string[]> = {
  Signals: ["ticker", "signal_domain", "signal_type", "direction", "signal_score", "signal_rank_score", "signal_confidence_pct", "active_signal_count", "working_timeframe", "evidence"],
  "QMD indicators": ["ticker", "flow_structure_composite_score", "flow_structure_composite_confidence_pct", "flow_structure_composite_bias", "microstructure_unified_signal", "microstructure_unified_confidence_pct", "microstructure_signed_volume_imbalance", "microstructure_level1_ofi", "microstructure_queue_imbalance", "qmd_structure_score", "qmd_structure_confidence_pct", "indicator_timeframe"],
};
const LOCKED_MARKET_LIST_COLUMNS = ["logo", "ticker", "news_labels", "sec_labels"];
const SIGNAL_PRESETS: Record<string, string[]> = {
  All: ["ticker", "event_time", "signal_domain", "signal_type", "signal_state", "direction", "working_timeframe", "signal_score", "signal_rank_score", "signal_confidence_pct", "last", "source", "evidence", "news_labels", "sec_labels"],
  Market: ["ticker", "event_time", "signal_type", "signal_state", "direction", "working_timeframe", "signal_score", "signal_rank_score", "signal_confidence_pct", "last", "source", "evidence"],
  News: ["ticker", "event_time", "signal_type", "direction", "signal_score", "signal_confidence_pct", "source", "evidence", "news_labels"],
  SEC: ["ticker", "event_time", "signal_type", "direction", "signal_score", "signal_confidence_pct", "source", "evidence", "sec_labels"],
  Strategy: ["ticker", "event_time", "signal_type", "action", "direction", "working_timeframe", "signal_score", "signal_confidence_pct", "last", "source", "evidence"],
};
const WATCHLIST_DEFAULT_COLUMNS = ["ticker", "last", "change_pct", "change_5m_pct", "volume", "news_labels", "sec_labels"];

export function MarketScannerContainer({ asOf, meta, onSettingsChange, onTickerSelect, rows, settings }: { asOf: string; meta?: ScannerSnapshotMeta; onSettingsChange: (patch: Partial<MarketScannerSettings>) => void; onTickerSelect: (ticker: string) => void; rows: ScreenerRow[]; settings: MarketScannerSettings }) {
  const normalizedRows = useMemo(() => normalizeScannerRows(rows), [rows]);
  const preset = normalizeMarketScannerPreset(settings.preset);
  const qmdStatus = meta?.qmd_derived_status;
  const qmdPreset = preset === "Signals" || preset === "Indicators";
  const subtitle = qmdPreset && qmdStatus === "building"
    ? "Building the causal QMD cross-section from canonical events · market rows remain available while replay completes"
    : qmdPreset && qmdStatus === "error"
      ? `QMD cross-section unavailable · ${meta?.qmd_derived_error || "the replay will retry automatically"}`
      : qmdPreset && qmdStatus === "ready"
        ? `${Number(meta?.qmd_indicator_row_count || 0).toLocaleString()} indicator rows · ${Number(meta?.qmd_signal_event_count || 0).toLocaleString()} recent signal events`
        : meta?.complete_universe
          ? `Full historical universe · ${meta.lookback_minutes ?? 15}-minute discovery window · cached interval analytics`
          : "Scanner universe unavailable or incomplete";
  return <MarketListSurface
    asOf={asOf}
    columns={withLockedColumns(settings.columns.length ? settings.columns : SCANNER_PRESETS[preset] ?? SCANNER_PRESETS.Overview, LOCKED_MARKET_LIST_COLUMNS)}
    customColumns={settings.customColumns}
    empty="No securities are available at this market clock."
    eyebrow="Market snapshot"
    fieldCoverage={meta?.field_coverage}
    limit={settings.limit}
    lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
    onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
    onPresetChange={(preset) => onSettingsChange({ columns: SCANNER_PRESETS[preset] ?? settings.columns, preset })}
    onTickerSelect={onTickerSelect}
    presets={Object.keys(SCANNER_PRESETS)}
    preset={preset}
    rows={normalizedRows}
    sortColumn={preset === "Signals" ? "signal_rank_score" : preset === "Indicators" ? "flow_structure_composite_confidence" : "change_pct"}
    subtitle={subtitle}
    title="Scanner"
  />;
}

export function migrateMarketScannerSettings(
  settings: MarketScannerSettings,
  storedVersion: number | undefined,
): MarketScannerSettings {
  const preset = normalizeMarketScannerPreset(settings.preset);
  const legacyColumns = LEGACY_SCANNER_PRESET_COLUMNS[settings.preset];
  const refreshBuiltInView = Number(storedVersion ?? 0) < 21
    && (settings.preset === "QMD indicators"
      || Boolean(legacyColumns && sameColumns(settings.columns, legacyColumns)));
  return {
    ...settings,
    preset,
    columns: refreshBuiltInView ? [...(SCANNER_PRESETS[preset] ?? SCANNER_PRESETS.Overview)] : settings.columns,
  };
}

export function normalizeMarketScannerPreset(value: unknown): string {
  const preset = String(value || "");
  if (preset === "QMD indicators") return "Indicators";
  return Object.hasOwn(SCANNER_PRESETS, preset) ? preset : "Overview";
}

function sameColumns(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((column, index) => column === right[index]);
}

export function SignalStreamContainer({ asOf, onSettingsChange, onTickerSelect, scannerRows, settings, strategySignals }: { asOf: string; onSettingsChange: (patch: Partial<SignalStreamSettings>) => void; onTickerSelect: (ticker: string) => void; scannerRows: ScreenerRow[]; settings: SignalStreamSettings; strategySignals: ScreenerRow[] }) {
  const events = useMemo(() => buildSignalEvents(normalizeScannerRows(scannerRows), strategySignals, asOf), [asOf, scannerRows, strategySignals]);
  const filtered = useMemo(() => filterSignalPreset(events, settings.preset), [events, settings.preset]);
  const [signalMethods, setSignalMethods] = useState<SignalMethod[]>([]);
  useEffect(() => {
    const controller = new AbortController();
    api<{ signal_catalog?: SignalMethod[] }>("/api/real-live-trading/qmd-gateway/catalogs", { signal: controller.signal, timeoutMs: 10000 })
      .then((payload) => setSignalMethods((payload.signal_catalog ?? []).filter((method) => method.status === "implemented")))
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  return <MarketListSurface
    asOf={asOf}
    columns={withLockedColumns(settings.columns.length ? settings.columns : SIGNAL_PRESETS[settings.preset] ?? SIGNAL_PRESETS.All, LOCKED_MARKET_LIST_COLUMNS)}
    customColumns={settings.customColumns}
    empty="No market or strategy events match this stream."
    eyebrow="Newest first"
    guide={signalMethods.length ? <details className="market-signal-methods"><summary>Review {signalMethods.length} implemented market signals</summary><div>{signalMethods.map((method) => <article key={method.key}><strong>{method.label} · v{method.signal_version}</strong><span>{method.domain ?? "market"} · producer: {method.producer ?? "qmd"} · {method.input_basis?.replaceAll("_", " ") ?? method.compute_mode.replaceAll("_", " ")} · {method.working_timeframes.join(", ")} · {method.publication_cadence?.replaceAll("_", " ") ?? "on update"}</span><p>{method.rationale}</p><small>{method.trigger_rules[0] ?? "See the signal catalog for trigger rules."}</small></article>)}</div></details> : null}
    limit={settings.limit}
    lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
    onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
    onPresetChange={(preset) => onSettingsChange({ columns: SIGNAL_PRESETS[preset] ?? settings.columns, preset })}
    onTickerSelect={onTickerSelect}
    presets={Object.keys(SIGNAL_PRESETS)}
    preset={settings.preset}
    rows={filtered}
    subtitle="Market, news, SEC, and model signals remain separate from durable strategy decisions"
    title="Signal stream"
  />;
}

export function WatchUniverseContainer({ asOf, onSettingsChange, onTickerSelect, scannerRows, settings }: { asOf: string; onSettingsChange: (patch: Partial<WatchUniverseSettings>) => void; onTickerSelect: (ticker: string) => void; scannerRows: ScreenerRow[]; settings: WatchUniverseSettings }) {
  const [catalog, setCatalog] = useState<WatchUniverseCatalogResponse | null>(null);
  const [runtime, setRuntime] = useState<WatchlistRuntimeResponse | null>(null);
  const [runtimeError, setRuntimeError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    api<WatchUniverseCatalogResponse>("/api/trading/configuration/base", { signal: controller.signal, timeoutMs: 10000 })
      .then(setCatalog)
      .catch(() => undefined);
    return () => controller.abort();
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try {
        const payload = await api<WatchlistRuntimeResponse>("/api/market-discovery/watchlists/runtime", { signal: controller.signal, timeoutMs: 10000 });
        setRuntime(payload);
        setRuntimeError("");
      } catch (reason) {
        if (!controller.signal.aborted) setRuntimeError(reason instanceof Error ? reason.message : String(reason));
      } finally {
        pending = false;
      }
    };
    void refresh();
    const interval = window.setInterval(() => void refresh(), 5_000);
    return () => { controller.abort(); window.clearInterval(interval); };
  }, []);
  const universes = catalog?.run_plans?.universes ?? [];
  const runPlans = catalog?.run_plans?.plans ?? [];
  const sourceRows = useMemo(() => normalizeScannerRows(scannerRows), [scannerRows]);
  const rowByTicker = useMemo(() => new Map(sourceRows.map((row) => [String(row.ticker), row])), [sourceRows]);
  const universe = universes.find((row) => row.universe_id === settings.universeId) ?? universes[0];
  const symbols = (universe?.symbols ?? []).map((ticker) => ticker.trim().toUpperCase()).filter(Boolean);
  const runtimeWatchlist = runtime?.watchlists?.find((row) => row.watchlist_id === universe?.scanner_view_id);
  const runtimeMembers = runtimeWatchlist?.members ?? [];
  const runtimeReady = runtime?.status === "ready" && runtimeWatchlist !== undefined;
  const resolvedSymbols = universe?.source === "watchlist"
    ? runtimeMembers.map((row) => String(row.ticker ?? "").trim().toUpperCase()).filter(Boolean)
    : symbols;
  const runtimeMemberByTicker = new Map(runtimeMembers.map((row) => [String(row.ticker ?? "").trim().toUpperCase(), row]));
  const rows: ScreenerRow[] = resolvedSymbols.map((ticker) => ({
    ...(runtimeMemberByTicker.get(ticker) ?? {}),
    ...(rowByTicker.get(ticker) ?? {}),
    ticker,
  }));
  const linkedPlans = runPlans.filter((plan) => plan.universe_id === universe?.universe_id);
  const resolved = universe?.source === "configured_symbols" || (universe?.source === "watchlist" && runtimeReady);
  const resolutionClock = universe?.source === "watchlist" ? runtime?.as_of ?? asOf : asOf;
  const unresolvedDetail = universe?.source === "scanner_view"
    ? `Legacy Scanner view ${universe.scanner_view_id || "not selected"} is presentation-only. Convert this universe to a Watchlist or configured symbols.`
    : runtimeError
      ? `Watchlist runtime could not be read: ${runtimeError}`
      : runtime === null
        ? "Loading the current Watchlist membership projection."
        : runtime.status !== "ready"
          ? "Waiting for the first complete causal Watchlist resolution."
          : `Watchlist ${universe?.scanner_view_id || "not selected"} has no runtime snapshot.`;
  return <section className="market-list-surface watchlist-surface" aria-label={`${universe?.name ?? "Watch"} universe`}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><Star size={12} /> Run Plan boundary</span><h3>{universe?.name ?? "No watch universe configured"}</h3><p>{resolved ? `${rows.length} eligible securities` : "Dynamic membership awaits its causal resolver"} · state at <MarketTime value={resolutionClock} /></p></div>
      <span className="market-list-owner strategy">{universe?.source?.replaceAll("_", " ") ?? "unavailable"}</span>
    </header>
    <div className="watch-universe-context">
      <label><span>Watch universe</span><select aria-label="Watch universe" onChange={(event) => onSettingsChange({ universeId: event.target.value })} value={universe?.universe_id ?? ""}>{universes.map((row) => <option key={row.universe_id} value={row.universe_id}>{row.name}</option>)}</select></label>
      <div><span>Used by</span><strong>{linkedPlans.map((plan) => plan.name || plan.run_plan_id).join(", ") || "No Run Plan"}</strong></div>
      <button onClick={() => { window.location.hash = "assignment-configuration"; }} type="button">Configure in Run Plans <ArrowRight size={13} /></button>
    </div>
    {!resolved ? <div className="watch-universe-warning" role="status"><strong>Membership is not runtime-ready</strong><span>{unresolvedDetail}</span></div> : null}
    <MarketListTable
      columns={withLockedColumns(settings.columns.length ? settings.columns : WATCHLIST_DEFAULT_COLUMNS, LOCKED_MARKET_LIST_COLUMNS)}
      customColumns={settings.customColumns}
      empty={resolved ? (universe?.source === "watchlist" ? "This Watchlist currently has no members." : "This watch universe has no configured members.") : "No resolved membership is available."}
      limit={settings.limit}
      lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
      onColumnsChange={(columns) => onSettingsChange({ columns })}
      onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
      onTickerSelect={onTickerSelect}
      rows={rows}
      title={`${universe?.name ?? "Watch"} universe`}
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
    {error ? <div className="canvas-inline-error">Strategy activity unavailable: {error}</div> : <MarketListTable columns={["event_time", "ticker", "event_type", "action", "state", "reason", "strategy_id", "run_id"]} customColumns={[]} empty="No persisted strategy events match these filters." limit={settings.limit} lockedColumns={[]} onColumnsChange={() => undefined} onCustomColumnsChange={() => undefined} onTickerSelect={onTickerSelect} rows={rows} title="Strategy activity" />}
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
  columns,
  customColumns,
  empty,
  eyebrow,
  fieldCoverage,
  guide,
  limit,
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
  columns: string[];
  customColumns: ScannerCustomColumn[];
  empty: string;
  eyebrow: string;
  fieldCoverage?: Record<string, number>;
  guide?: ReactNode;
  limit: number;
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
    <nav className="market-list-presets" aria-label={`${title} views`}>{presets.map((item) => <button aria-pressed={preset === item} className={preset === item ? "active" : undefined} key={item} onClick={() => onPresetChange(item)} type="button">{item}</button>)}</nav>
    <MarketListTable columns={columns} customColumns={customColumns} empty={empty} fieldCoverage={fieldCoverage} limit={limit} lockedColumns={lockedColumns} onColumnsChange={onColumnsChange} onCustomColumnsChange={onCustomColumnsChange} onTickerSelect={onTickerSelect} rows={rows} sortColumn={sortColumn} title={title} />
  </section>;
}

function MarketListTable({
  columns,
  customColumns,
  empty,
  fieldCoverage,
  limit,
  lockedColumns = [],
  onColumnsChange,
  onCustomColumnsChange,
  onTickerSelect,
  rowAction,
  rows,
  sortColumn,
  title,
}: {
  columns: string[];
  customColumns: ScannerCustomColumn[];
  empty: string;
  fieldCoverage?: Record<string, number>;
  limit: number;
  lockedColumns?: string[];
  onColumnsChange: (columns: string[]) => void;
  onCustomColumnsChange: (columns: ScannerCustomColumn[]) => void;
  onTickerSelect?: (ticker: string) => void;
  rowAction?: (row: ScreenerRow) => ReactNode;
  rows: ScreenerRow[];
  sortColumn?: string;
  title: string;
}) {
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [filterMode, setFilterMode] = useState("all");
  const [headerMenuColumn, setHeaderMenuColumn] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" }>({ column: title === "Signal stream" || title === "Strategy activity" ? "event_time" : "change_pct", direction: "desc" });
  const headerMenuRef = useRef<HTMLDivElement | null>(null);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  useEffect(() => {
    if (sortColumn) setSort({ column: sortColumn, direction: "desc" });
  }, [sortColumn]);
  const labelFilters = useMemo(() => ({
    news: collectLabels(rows, "news_labels"),
    sec: collectLabels(rows, "sec_labels"),
  }), [rows]);
  const visibleRows = useMemo(() => rows.filter((row) => {
    if (deferredQuery && !Object.values(row).some((value) => String(value ?? "").toLowerCase().includes(deferredQuery))) return false;
    const change = numberValue(row.change_pct);
    if (filterMode === "advancing" && !(change > 0)) return false;
    if (filterMode === "declining" && !(change < 0)) return false;
    if (filterMode === "news_hot" && String(row.live_news_recency ?? "").toLowerCase() !== "hot") return false;
    if (filterMode === "news_cold" && String(row.live_news_recency ?? "").toLowerCase() !== "cold") return false;
    if (filterMode === "sec_hot" && String(row.sec_recency ?? "").toLowerCase() !== "hot") return false;
    if (filterMode === "sec_cold" && String(row.sec_recency ?? "").toLowerCase() !== "cold") return false;
    if (filterMode.startsWith("news_label:") && !rowLabels(row.news_labels).some((labelValue) => normalizeLabel(labelValue) === filterMode.slice(11))) return false;
    if (filterMode.startsWith("sec_label:") && !rowLabels(row.sec_labels).some((labelValue) => normalizeLabel(labelValue) === filterMode.slice(10))) return false;
    return true;
  }).sort((left, right) => compareValues(left[sort.column], right[sort.column]) * (sort.direction === "asc" ? 1 : -1)).slice(0, limit), [deferredQuery, filterMode, limit, rows, sort]);
  const tickers = visibleRows.filter((row) => !String(row.logo_url ?? "").trim()).map((row) => String(row.ticker ?? row.symbol ?? "")).filter(Boolean);
  const presentations = useTickerPresentations(tickers);
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
    const currentIndex = columns.indexOf(column);
    if (currentIndex < 0 || lockedColumns.includes(column)) return;
    const unlocked = columns.filter((item) => !lockedColumns.includes(item));
    const unlockedIndex = unlocked.indexOf(column);
    const nextIndex = target === "start" ? 0 : target === "end" ? unlocked.length - 1 : Math.max(0, Math.min(unlocked.length - 1, unlockedIndex + (target === "left" ? -1 : 1)));
    unlocked.splice(unlockedIndex, 1);
    unlocked.splice(nextIndex, 0, column);
    onColumnsChange(withLockedColumns(unlocked, lockedColumns));
    setHeaderMenuColumn(null);
  }
  function removeColumn(column: string) {
    if (lockedColumns.includes(column)) return;
    onColumnsChange(columns.filter((item) => item !== column));
    if (isTechnicalKey(column)) onCustomColumnsChange(customColumns.filter((item) => item.key !== column));
    setHeaderMenuColumn(null);
  }
  function addTechnicalColumn(metric: TechnicalMetric) {
    const column = defaultTechnicalColumn(metric);
    const key = column.key;
    if (!customColumns.some((item) => item.key === key)) onCustomColumnsChange([...customColumns, column]);
    if (!columns.includes(key)) onColumnsChange(withLockedColumns([...columns.filter((item) => !lockedColumns.includes(item)), key], lockedColumns));
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
    <div className="market-list-toolbar">
      <label className="market-list-search"><Search size={14} /><input aria-label={`Search ${title}`} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols and values" value={query} /></label>
      <label className="market-list-filter"><Filter size={13} /><select aria-label={`Filter ${title}`} onChange={(event) => setFilterMode(event.target.value)} value={filterMode}><option value="all">All rows</option><option value="advancing">Advancing</option><option value="declining">Declining</option><option value="news_hot">Hot news</option><option value="news_cold">Cold news</option><option value="sec_hot">Hot SEC</option><option value="sec_cold">Cold SEC</option>{labelFilters.news.length ? <optgroup label="News labels">{labelFilters.news.map((labelValue) => <option key={`news:${labelValue}`} value={`news_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}{labelFilters.sec.length ? <optgroup label="SEC labels">{labelFilters.sec.map((labelValue) => <option key={`sec:${labelValue}`} value={`sec_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}</select></label>
      <span>{visibleRows.length} of {rows.length}</span>
      <button aria-expanded={columnPickerOpen} className="market-list-columns-button" onClick={() => setColumnPickerOpen((open) => !open)} type="button"><Columns3 size={14} /> Columns <b>{columns.length}</b></button>
    </div>
    <div className="market-list-table-scroll"><table className="market-list-table"><thead><tr>{columns.map((column) => { const definition = catalogField(column, customColumns); const sorted = sort.column === column; const className = columnClass(column); const menuOpen = headerMenuColumn === column; return column === "logo" ? <th aria-label="Ticker logo" className={className} key={column} /> : <th aria-sort={sorted ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} className={className} data-menu-open={menuOpen ? "true" : undefined} key={column}><button aria-expanded={menuOpen} onClick={() => setHeaderMenuColumn((current) => current === column ? null : column)} title={`Configure ${definition.label}`} type="button"><span>{definition.label}<small data-kind={definition.kind}>{technicalScopeLabel(definition) ?? definition.kind}</small></span>{sorted ? sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : <ChevronDown size={12} />}</button>{menuOpen ? <ColumnHeaderMenu column={column} definition={definition} locked={lockedColumns.includes(column)} onAnchorChange={(value) => changeTechnicalAnchor(column, value)} onMove={(target) => moveColumn(column, target)} onRemove={() => removeColumn(column)} onSort={(direction) => changeSort(column, direction)} onSourceChange={(value) => changeTechnicalSource(column, value)} onTimeframeChange={(value) => changeTechnicalTimeframe(column, value)} ref={headerMenuRef} /> : null}</th>; })}{rowAction ? <th aria-label="Row actions" /> : null}</tr></thead><tbody>{visibleRows.length ? visibleRows.map((row, index) => { const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase(); const selectable = Boolean(ticker && onTickerSelect); const select = () => { if (selectable) onTickerSelect?.(ticker); }; return <tr aria-label={selectable ? `Open ${ticker} Charts & Quotes` : undefined} data-selectable={selectable ? "true" : undefined} key={`${ticker || "row"}:${row.event_time ?? index}:${index}`} onClick={(event) => { if (!(event.target as HTMLElement).closest("button, input, select, a")) select(); }} onKeyDown={(event) => { if (selectable && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(); } }} tabIndex={selectable ? 0 : undefined}>{columns.map((column) => <td className={`${toneClass(row[column], column, customColumns)} ${columnClass(column)}`.trim()} key={column}>{renderMarketCell(row, column, presentations, customColumns)}</td>)}{rowAction ? <td className="market-list-row-action">{rowAction(row)}</td> : null}</tr>; }) : <tr><td className="market-list-empty" colSpan={columns.length + (rowAction ? 1 : 0)}>{empty}</td></tr>}</tbody></table></div>
    {columnPickerOpen ? <ColumnPicker columns={columns} customColumns={customColumns} fieldCoverage={fieldCoverage} lockedColumns={lockedColumns} onAddTechnical={addTechnicalColumn} onChange={onColumnsChange} onClose={() => setColumnPickerOpen(false)} /> : null}
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
  columns,
  customColumns,
  fieldCoverage,
  lockedColumns = [],
  onAddTechnical,
  onChange,
  onClose,
}: {
  columns: string[];
  customColumns: ScannerCustomColumn[];
  fieldCoverage?: Record<string, number>;
  lockedColumns?: string[];
  onAddTechnical: (metric: TechnicalMetric) => void;
  onChange: (columns: string[]) => void;
  onClose: () => void;
}) {
  const customDefinitions = customColumns.map(customField);
  const groups = [...new Set([...FIELD_CATALOG.map((item) => item.group), "Technicals", ...(customDefinitions.length ? ["Custom"] : [])])];
  const [group, setGroup] = useState(groups[0]);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const availableDefinitions = [...FIELD_CATALOG, ...TECHNICAL_METRICS.map((item) => ({ ...item, group: "Technicals", key: `template:${item.metric}` } as FieldDefinition)), ...customDefinitions];
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

function buildSignalEvents(rows: ScreenerRow[], strategySignals: ScreenerRow[], asOf: string) {
  const derived: ScreenerRow[] = rows
    .filter((row) => Boolean(row.signal_id) && Boolean(row.signal_type))
    .map((row) => ({
      ...row,
      direction: String(row.direction ?? row.market_state ?? "neutral").toLowerCase(),
      event_time: row.event_time ?? row.bar_time_market ?? asOf,
      evidence: row.evidence ?? row.live_reasons ?? "QMD emitted this causal market signal.",
      magnitude: row.signal_score ?? row.scanner_score ?? 0,
      signal_domain: row.signal_domain ?? "market",
      signal_producer: row.signal_producer ?? "qmd",
      signal_confidence_pct: numberValue(row.signal_confidence) * 100,
      source: row.source ?? "QMD market signal",
      ticker: String(row.ticker ?? row.symbol ?? "").toUpperCase(),
    }));
  const strategy: ScreenerRow[] = strategySignals.map((row) => ({
    ...row,
    action: row.action ?? "wait",
    direction: String(row.direction ?? "neutral").toLowerCase(),
    event_time: row.time ?? row.event_time ?? asOf,
    evidence: row.detail ?? row.reason ?? "Strategy runtime emitted this durable signal.",
    last: row.value,
    magnitude: row.magnitude ?? 0,
    signal_confidence_pct: numberValue(row.confidence ?? row.signal_confidence) * 100,
    signal_score: row.score ?? row.signal_score ?? row.magnitude ?? 0,
    signal_state: row.signal_state ?? "triggered",
    signal_type: row.signal ?? row.signal_type ?? "Strategy signal",
    source: "Strategy runtime",
    signal_domain: "",
    signal_producer: "strategy_runtime",
    ticker: String(row.symbol ?? row.ticker ?? "").toUpperCase(),
  }));
  const combined: ScreenerRow[] = [...derived, ...strategy];
  return combined.sort((left, right) => String(right.event_time).localeCompare(String(left.event_time)));
}

function filterSignalPreset(rows: ScreenerRow[], preset: string) {
  if (preset === "All") return rows;
  if (preset === "Strategy") return rows.filter((row) => row.source === "Strategy runtime");
  return rows.filter((row) => String(row.signal_domain || "").toLowerCase() === preset.toLowerCase());
}

function normalizeScannerRows(rows: ScreenerRow[]) {
  return rows.map((row) => {
    const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase();
    const last = numberValue(row.last ?? row.snapshot_last_price ?? row.close);
    const volume = numberValue(row.volume);
    return {
      ...row,
      dollar_volume: row.dollar_volume ?? (last > 0 && volume > 0 ? last * volume : undefined),
      microstructure_unified_confidence_pct: numberValue(row.microstructure_unified_confidence) * 100,
      flow_structure_composite_confidence_pct: numberValue(row.flow_structure_composite_confidence) * 100,
      qmd_structure_confidence_pct: numberValue(row.qmd_structure_confidence) * 100,
      signal_confidence_pct: numberValue(row.signal_confidence) * 100,
      ticker,
    };
  });
}

function renderMarketCell(row: ScreenerRow, column: string, presentations: ReturnType<typeof useTickerPresentations>, customColumns: ScannerCustomColumn[]) {
  const value = row[column];
  const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase();
  if (column === "logo") return <TickerLogo logoUrl={String(row.logo_url ?? presentations[ticker]?.logo_url ?? "")} ticker={ticker} />;
  if (column === "ticker") {
    return <span className="market-list-ticker-cell">
      <strong>{ticker}</strong>
      <span className="market-list-ticker-events">
        <TickerEventIcon source="News" value={String(row.live_news_recency ?? "none")} />
        <TickerEventIcon source="SEC" value={String(row.sec_recency ?? "none")} />
      </span>
    </span>;
  }
  if (column === "event_time") return value ? <MarketTime value={String(value)} /> : "—";
  if (["direction", "source"].includes(column)) return value ? <span className={`market-list-badge ${String(value).toLowerCase().replace(/[^a-z]+/g, "-")}`}>{String(value).replaceAll("_", " ")}</span> : "—";
  if (column === "news_labels" || column === "sec_labels") {
    const labels = rowLabels(value);
    return labels.length ? <span className="market-list-label-badges" data-source={column === "news_labels" ? "news" : "sec"} title={labels.join(", ")}>{labels.slice(0, 1).map((labelValue) => <span key={labelValue}>{labelValue}</span>)}{labels.length > 1 ? <span className="market-list-label-overflow">+{labels.length - 1}</span> : null}</span> : <span className="market-list-unavailable">—</span>;
  }
  const definition = catalogField(column, customColumns);
  if (value === null || value === undefined || value === "") return <span className="market-list-unavailable" title={`${definition.label} is not available from the active source at this clock.`}>—</span>;
  if (definition.format === "date") return <MarketTime value={String(value)} />;
  if (definition.format === "percent") return `${numberValue(value) > 0 ? "+" : ""}${numberValue(value).toFixed(Math.abs(numberValue(value)) < 1 ? 2 : 1)}%`;
  if (definition.format === "percentPlain") return `${numberValue(value).toFixed(Math.abs(numberValue(value)) < 1 ? 2 : 1)}%`;
  if (definition.format === "money") return formatMoney(numberValue(value));
  if (definition.format === "integer") return formatCompact(numberValue(value));
  if (definition.format === "multiple") return `${numberValue(value).toFixed(numberValue(value) < 10 ? 2 : 1)}\u00d7`;
  if (definition.format === "number") return numberValue(value).toFixed(2);
  if (definition.format === "score") return numberValue(value).toFixed(0);
  return String(value);
}

function TickerEventIcon({ source, value }: { source: "News" | "SEC"; value: string }) {
  const state = value.toLowerCase();
  if (state !== "hot" && state !== "cold") return null;
  const Icon = source === "News" ? Flame : FileCheck2;
  const label = `${state} ${source.toLowerCase()}`;
  return <span aria-label={label} className="market-list-ticker-event" data-source={source.toLowerCase()} data-state={state} title={label}><Icon aria-hidden="true" fill={source === "News" ? "currentColor" : "none"} size={15} /></span>;
}

function toneClass(value: unknown, column: string, customColumns: ScannerCustomColumn[] = []) {
  const numeric = numberValue(value);
  const definition = catalogField(column, customColumns);
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

function catalogField(key: string, customColumns: ScannerCustomColumn[] = []) {
  const catalog = FIELD_CATALOG.find((item) => item.key === key);
  if (catalog) return catalog;
  const custom = customColumns.find((item) => item.key === key);
  return custom ? customField(custom) : field(key, label(key), "Other", "raw", "text", "Available source field.");
}
function withLockedColumns(columns: string[], lockedColumns: string[]) {
  const leading: string[] = lockedColumns.filter((column) => column === "logo" || column === "ticker");
  const trailing = lockedColumns.filter((column) => !leading.includes(column));
  return [...leading, ...columns.filter((column) => !lockedColumns.includes(column)), ...trailing];
}
function columnClass(column: string) { return column === "logo" ? "market-list-logo-column" : column === "ticker" ? "market-list-symbol-column" : column === "news_labels" || column === "sec_labels" ? "market-list-label-column" : ""; }
function rowLabels(value: unknown) { return [...new Set(String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean))]; }
function collectLabels(rows: ScreenerRow[], column: "news_labels" | "sec_labels") { return [...new Set(rows.flatMap((row) => rowLabels(row[column])))].sort((left, right) => left.localeCompare(right)); }
function normalizeLabel(value: string) { return value.trim().toLowerCase(); }
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
