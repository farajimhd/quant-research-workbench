import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ArrowUpDown, Check, ChevronDown, ChevronLeft, Columns3, FileCheck2, Filter, Flame, ListFilter, Plus, Search, Star, Trash2, X } from "lucide-react";
import { forwardRef, useDeferredValue, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import { MarketTime } from "./MarketTime";
import { TickerLogo, useTickerPresentations } from "./TickerIdentity";

export type ScreenerRow = Record<string, unknown>;
export type ScannerSnapshotMeta = { complete_universe?: boolean; field_coverage?: Record<string, number>; lookback_minutes?: number; materialized?: boolean; row_count?: number; snapshot_at_utc?: string };
export type ScannerTimeframe = "100ms" | "1s" | "5s" | "10s" | "30s" | "1m" | "5m" | "15m" | "30m" | "1h" | "1d";
export type TechnicalMetric = "change_pct" | "dollar_volume" | "high" | "low" | "quote_count" | "range_pct" | "relative_volume" | "trade_count" | "volume" | "vwap" | "vwap_distance_pct";
export type ScannerCustomColumn = { key: string; metric: TechnicalMetric; timeframe: ScannerTimeframe };
type TechnicalListSettings = { columns: string[]; customColumns: ScannerCustomColumn[] };
export type MarketScannerSettings = TechnicalListSettings & { limit: number; preset: string };
export type SignalStreamSettings = TechnicalListSettings & { limit: number; preset: string };
export type WatchlistSettings = TechnicalListSettings & { limit: number; ownerKind: "strategy" | "user"; ownerName: string; symbols: string[] };

type FieldKind = "derived" | "estimated" | "raw";
type FieldDefinition = {
  description: string;
  format: "date" | "integer" | "money" | "multiple" | "number" | "percent" | "percentPlain" | "score" | "text";
  group: string;
  key: string;
  kind: FieldKind;
  label: string;
  metric?: TechnicalMetric;
  timeframe?: ScannerTimeframe;
  timeframes?: ScannerTimeframe[];
};

export const SCANNER_TIMEFRAMES: ScannerTimeframe[] = ["100ms", "1s", "5s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "1d"];
const DEFAULT_SCANNER_TECHNICAL_TIMEFRAME: ScannerTimeframe = "15m";
const TECHNICAL_METRICS: Array<Omit<FieldDefinition, "key" | "timeframe"> & { metric: TechnicalMetric }> = [
  technicalMetric("change_pct", "Price change", "percent", "Open-to-last return inside the selected exchange-session interval."),
  technicalMetric("volume", "Volume", "integer", "Eligible executed share volume inside the selected interval."),
  technicalMetric("dollar_volume", "Dollar volume", "money", "Exact sum of eligible trade price multiplied by trade size inside the selected interval."),
  technicalMetric("trade_count", "Trades", "integer", "Eligible trade-print count inside the selected interval."),
  technicalMetric("quote_count", "Quotes", "integer", "Consolidated quote-event count inside the selected interval."),
  technicalMetric("vwap", "VWAP", "money", "Trade-price volume-weighted average inside the selected interval."),
  technicalMetric("vwap_distance_pct", "Price vs VWAP", "percent", "Latest eligible trade relative to the selected interval VWAP."),
  technicalMetric("relative_volume", "Relative volume", "multiple", "Interval volume pace divided by the prior 20 completed extended-session average pace.", ["1m", "5m", "15m", "30m", "1h", "1d"]),
  technicalMetric("range_pct", "Range", "percentPlain", "High-to-low price range inside the selected interval."),
  technicalMetric("high", "High", "money", "Highest eligible trade inside the selected interval."),
  technicalMetric("low", "Low", "money", "Lowest eligible trade inside the selected interval."),
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
  field("signal_type", "Signal", "Signal event", "derived", "text", "Stable event class or strategy-defined signal name."),
  field("direction", "Direction", "Signal event", "derived", "text", "Bullish, bearish, or neutral direction assigned by the rule owner."),
  field("magnitude", "Magnitude", "Signal event", "derived", "percent", "Observed move or normalized event magnitude."),
  field("source", "Authority", "Signal event", "raw", "text", "Market-derived rule or durable strategy runtime authority."),
  field("evidence", "Evidence", "Signal event", "derived", "text", "Compact explanation of the inputs that triggered the row."),
];

const SCANNER_PRESETS: Record<string, string[]> = {
  Overview: ["ticker", "last", "change_pct", "change_5m_pct", "volume", "trade_count", "news_labels", "sec_labels"],
  Momentum: ["ticker", "last", "change_5m_pct", "change_pct", "dollar_volume", "trade_count", "quote_count"],
  Intelligence: ["ticker", "last", "change_pct", "live_news_count", "sec_count", "news_labels", "sec_labels"],
  Fundamentals: ["ticker", "xbrl_quality_score", "financial_trajectory_score", "xbrl_profitability_score", "xbrl_growth_score", "xbrl_cash_quality_score", "xbrl_balance_sheet_score", "xbrl_capital_discipline_score", "fundamental_revenue_growth_pct", "fundamental_operating_margin_pct", "valuation_pe"],
};
const LOCKED_MARKET_LIST_COLUMNS = ["logo", "ticker", "news_labels", "sec_labels"];
const SIGNAL_PRESETS: Record<string, string[]> = {
  All: ["ticker", "event_time", "signal_type", "direction", "magnitude", "last", "source", "evidence", "news_labels", "sec_labels"],
  "Price moves": ["ticker", "event_time", "signal_type", "magnitude", "last", "change_5m_pct", "source", "news_labels", "sec_labels"],
  Activity: ["ticker", "event_time", "signal_type", "direction", "trade_count", "quote_count", "source", "evidence", "news_labels", "sec_labels"],
  Intelligence: ["ticker", "event_time", "signal_type", "direction", "source", "evidence", "news_labels", "sec_labels"],
  Strategy: ["ticker", "event_time", "signal_type", "direction", "last", "source", "evidence", "news_labels", "sec_labels"],
};
const WATCHLIST_DEFAULT_COLUMNS = ["ticker", "last", "change_pct", "change_5m_pct", "volume", "news_labels", "sec_labels"];

export function MarketScannerContainer({ asOf, meta, onSettingsChange, onTickerSelect, rows, settings }: { asOf: string; meta?: ScannerSnapshotMeta; onSettingsChange: (patch: Partial<MarketScannerSettings>) => void; onTickerSelect: (ticker: string) => void; rows: ScreenerRow[]; settings: MarketScannerSettings }) {
  const normalizedRows = useMemo(() => normalizeScannerRows(rows), [rows]);
  return <MarketListSurface
    asOf={asOf}
    columns={withLockedColumns(settings.columns.length ? settings.columns : SCANNER_PRESETS[settings.preset] ?? SCANNER_PRESETS.Overview, LOCKED_MARKET_LIST_COLUMNS)}
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
    preset={settings.preset}
    rows={normalizedRows}
    subtitle={meta?.complete_universe ? `Full historical universe · ${meta.lookback_minutes ?? 15}-minute discovery window · cached interval analytics` : "Scanner universe unavailable or incomplete"}
    title="Scanner"
  />;
}

export function SignalStreamContainer({ asOf, onSettingsChange, onTickerSelect, scannerRows, settings, strategySignals }: { asOf: string; onSettingsChange: (patch: Partial<SignalStreamSettings>) => void; onTickerSelect: (ticker: string) => void; scannerRows: ScreenerRow[]; settings: SignalStreamSettings; strategySignals: ScreenerRow[] }) {
  const events = useMemo(() => buildSignalEvents(normalizeScannerRows(scannerRows), strategySignals, asOf), [asOf, scannerRows, strategySignals]);
  const filtered = useMemo(() => filterSignalPreset(events, settings.preset), [events, settings.preset]);
  return <MarketListSurface
    asOf={asOf}
    columns={withLockedColumns(settings.columns.length ? settings.columns : SIGNAL_PRESETS[settings.preset] ?? SIGNAL_PRESETS.All, LOCKED_MARKET_LIST_COLUMNS)}
    customColumns={settings.customColumns}
    empty="No market or strategy events match this stream."
    eyebrow="Newest first"
    limit={settings.limit}
    lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
    onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
    onPresetChange={(preset) => onSettingsChange({ columns: SIGNAL_PRESETS[preset] ?? settings.columns, preset })}
    onTickerSelect={onTickerSelect}
    presets={Object.keys(SIGNAL_PRESETS)}
    preset={settings.preset}
    rows={filtered}
    subtitle="Reproducible market events and durable strategy signals"
    title="Signal stream"
  />;
}

export function WatchlistContainer({ asOf, onSettingsChange, onTickerSelect, scannerRows, settings }: { asOf: string; onSettingsChange: (patch: Partial<WatchlistSettings>) => void; onTickerSelect: (ticker: string) => void; scannerRows: ScreenerRow[]; settings: WatchlistSettings }) {
  const [draft, setDraft] = useState("");
  const sourceRows = useMemo(() => normalizeScannerRows(scannerRows), [scannerRows]);
  const rowByTicker = useMemo(() => new Map(sourceRows.map((row) => [String(row.ticker), row])), [sourceRows]);
  const rows: ScreenerRow[] = settings.symbols.map((ticker) => rowByTicker.get(ticker) ?? { ticker });
  function addTicker() {
    const ticker = draft.trim().toUpperCase();
    if (!/^[A-Z][A-Z0-9.\-]{0,9}$/.test(ticker)) return;
    onSettingsChange({ symbols: [...new Set([...settings.symbols, ticker])] });
    setDraft("");
  }
  const owner = settings.ownerName.trim() || (settings.ownerKind === "strategy" ? "Strategy runtime" : "You");
  return <section className="market-list-surface watchlist-surface" aria-label={`${owner} watchlist`}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><Star size={12} /> {settings.ownerKind} owned</span><h3>{owner}</h3><p>{settings.symbols.length} tracked securities · latest state at <MarketTime value={asOf} /></p></div>
      <span className={`market-list-owner ${settings.ownerKind}`}>{settings.ownerKind}</span>
    </header>
    <div className="watchlist-compose">
      <label><Search size={14} /><input aria-label="Add watchlist symbol" onChange={(event) => setDraft(event.target.value.toUpperCase())} onKeyDown={(event) => { if (event.key === "Enter") addTicker(); }} placeholder="Add ticker" value={draft} /></label>
      <button onClick={addTicker} type="button"><Plus size={14} /> Add</button>
    </div>
    <MarketListTable
      columns={withLockedColumns(settings.columns.length ? settings.columns : WATCHLIST_DEFAULT_COLUMNS, LOCKED_MARKET_LIST_COLUMNS)}
      customColumns={settings.customColumns}
      empty="This watchlist has no symbols yet."
      limit={settings.limit}
      lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
      onColumnsChange={(columns) => onSettingsChange({ columns })}
      onCustomColumnsChange={(customColumns) => onSettingsChange({ customColumns })}
      onTickerSelect={onTickerSelect}
      rowAction={(row) => <button aria-label={`Remove ${row.ticker}`} onClick={() => onSettingsChange({ symbols: settings.symbols.filter((ticker) => ticker !== row.ticker) })} title="Remove from watchlist" type="button"><Trash2 size={13} /></button>}
      rows={rows}
      title={`${owner} watchlist`}
    />
  </section>;
}

function MarketListSurface({
  asOf,
  columns,
  customColumns,
  empty,
  eyebrow,
  fieldCoverage,
  limit,
  lockedColumns = [],
  onColumnsChange,
  onCustomColumnsChange,
  onPresetChange,
  onTickerSelect,
  preset,
  presets,
  rows,
  subtitle,
  title,
}: {
  asOf: string;
  columns: string[];
  customColumns: ScannerCustomColumn[];
  empty: string;
  eyebrow: string;
  fieldCoverage?: Record<string, number>;
  limit: number;
  lockedColumns?: string[];
  onColumnsChange: (columns: string[]) => void;
  onCustomColumnsChange: (columns: ScannerCustomColumn[]) => void;
  onPresetChange: (preset: string) => void;
  onTickerSelect: (ticker: string) => void;
  preset: string;
  presets: string[];
  rows: ScreenerRow[];
  subtitle: string;
  title: string;
}) {
  return <section className="market-list-surface" aria-label={title}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><ListFilter size={12} /> {eyebrow}</span><h3>{title}</h3><p>{subtitle} · <MarketTime value={asOf} /></p></div>
      <strong>{formatCompact(rows.length)} rows</strong>
    </header>
    <nav className="market-list-presets" aria-label={`${title} views`}>{presets.map((item) => <button aria-pressed={preset === item} className={preset === item ? "active" : undefined} key={item} onClick={() => onPresetChange(item)} type="button">{item}</button>)}</nav>
    <MarketListTable columns={columns} customColumns={customColumns} empty={empty} fieldCoverage={fieldCoverage} limit={limit} lockedColumns={lockedColumns} onColumnsChange={onColumnsChange} onCustomColumnsChange={onCustomColumnsChange} onTickerSelect={onTickerSelect} rows={rows} title={title} />
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
  title: string;
}) {
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [filterMode, setFilterMode] = useState("all");
  const [headerMenuColumn, setHeaderMenuColumn] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" }>({ column: title === "Signal stream" ? "event_time" : "change_pct", direction: "desc" });
  const headerMenuRef = useRef<HTMLDivElement | null>(null);
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
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
  function addTechnicalColumn(metric: TechnicalMetric, selectedTimeframe = DEFAULT_SCANNER_TECHNICAL_TIMEFRAME) {
    selectedTimeframe = metricTimeframe(metric, selectedTimeframe);
    const key = technicalColumnKey(metric, selectedTimeframe);
    if (!customColumns.some((item) => item.key === key)) onCustomColumnsChange([...customColumns, { key, metric, timeframe: selectedTimeframe }]);
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
  return <div className="market-list-table-shell">
    <div className="market-list-toolbar">
      <label className="market-list-search"><Search size={14} /><input aria-label={`Search ${title}`} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols and values" value={query} /></label>
      <label className="market-list-filter"><Filter size={13} /><select aria-label={`Filter ${title}`} onChange={(event) => setFilterMode(event.target.value)} value={filterMode}><option value="all">All rows</option><option value="advancing">Advancing</option><option value="declining">Declining</option><option value="news_hot">Hot news</option><option value="news_cold">Cold news</option><option value="sec_hot">Hot SEC</option><option value="sec_cold">Cold SEC</option>{labelFilters.news.length ? <optgroup label="News labels">{labelFilters.news.map((labelValue) => <option key={`news:${labelValue}`} value={`news_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}{labelFilters.sec.length ? <optgroup label="SEC labels">{labelFilters.sec.map((labelValue) => <option key={`sec:${labelValue}`} value={`sec_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}</select></label>
      <span>{visibleRows.length} of {rows.length}</span>
      <button aria-expanded={columnPickerOpen} className="market-list-columns-button" onClick={() => setColumnPickerOpen((open) => !open)} type="button"><Columns3 size={14} /> Columns <b>{columns.length}</b></button>
    </div>
    <div className="market-list-table-scroll"><table className="market-list-table"><thead><tr>{columns.map((column) => { const definition = catalogField(column, customColumns); const sorted = sort.column === column; const className = columnClass(column); const menuOpen = headerMenuColumn === column; return column === "logo" ? <th aria-label="Ticker logo" className={className} key={column} /> : <th aria-sort={sorted ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} className={className} data-menu-open={menuOpen ? "true" : undefined} key={column}><button aria-expanded={menuOpen} onClick={() => setHeaderMenuColumn((current) => current === column ? null : column)} title={`Configure ${definition.label}`} type="button"><span>{definition.label}<small data-kind={definition.kind}>{definition.timeframe ? timeframeLabel(definition.timeframe) : definition.kind}</small></span>{sorted ? sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : <ChevronDown size={12} />}</button>{menuOpen ? <ColumnHeaderMenu column={column} definition={definition} locked={lockedColumns.includes(column)} onMove={(target) => moveColumn(column, target)} onRemove={() => removeColumn(column)} onSort={(direction) => changeSort(column, direction)} onTimeframeChange={(value) => changeTechnicalTimeframe(column, value)} ref={headerMenuRef} /> : null}</th>; })}{rowAction ? <th aria-label="Row actions" /> : null}</tr></thead><tbody>{visibleRows.length ? visibleRows.map((row, index) => { const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase(); const selectable = Boolean(ticker && onTickerSelect); const select = () => { if (selectable) onTickerSelect?.(ticker); }; return <tr aria-label={selectable ? `Open ${ticker} chart` : undefined} data-selectable={selectable ? "true" : undefined} key={`${ticker || "row"}:${row.event_time ?? index}:${index}`} onClick={(event) => { if (!(event.target as HTMLElement).closest("button, input, select, a")) select(); }} onKeyDown={(event) => { if (selectable && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(); } }} tabIndex={selectable ? 0 : undefined}>{columns.map((column) => <td className={`${toneClass(row[column], column, customColumns)} ${columnClass(column)}`.trim()} key={column}>{renderMarketCell(row, column, presentations, customColumns)}</td>)}{rowAction ? <td className="market-list-row-action">{rowAction(row)}</td> : null}</tr>; }) : <tr><td className="market-list-empty" colSpan={columns.length + (rowAction ? 1 : 0)}>{empty}</td></tr>}</tbody></table></div>
    {columnPickerOpen ? <ColumnPicker columns={columns} customColumns={customColumns} fieldCoverage={fieldCoverage} lockedColumns={lockedColumns} onAddTechnical={addTechnicalColumn} onChange={onColumnsChange} onClose={() => setColumnPickerOpen(false)} /> : null}
  </div>;
}

const ColumnHeaderMenu = forwardRef<HTMLDivElement, {
  column: string;
  definition: FieldDefinition;
  locked: boolean;
  onMove: (target: "left" | "right" | "start" | "end") => void;
  onRemove: () => void;
  onSort: (direction: "asc" | "desc") => void;
  onTimeframeChange: (timeframe: ScannerTimeframe) => void;
}>(function ColumnHeaderMenu({ column, definition, locked, onMove, onRemove, onSort, onTimeframeChange }, ref) {
  return <div aria-label={`${definition.label} column tools`} className="market-column-header-menu" ref={ref}>
    <header><div><strong>{definition.label}</strong><span>{definition.description}</span></div>{definition.timeframe ? <label><span>Interval</span><select aria-label={`${definition.label} interval`} onChange={(event) => onTimeframeChange(event.target.value as ScannerTimeframe)} value={definition.timeframe}>{(definition.timeframes ?? SCANNER_TIMEFRAMES).map((value) => <option key={value} value={value}>{timeframeLabel(value)}</option>)}</select></label> : null}</header>
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
  onAddTechnical: (metric: TechnicalMetric, timeframe?: ScannerTimeframe) => void;
  onChange: (columns: string[]) => void;
  onClose: () => void;
}) {
  const customDefinitions = customColumns.map(customField);
  const groups = [...new Set([...FIELD_CATALOG.map((item) => item.group), "Technicals", ...(customDefinitions.length ? ["Custom"] : [])])];
  const [group, setGroup] = useState(groups[0]);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const availableDefinitions = [...FIELD_CATALOG, ...TECHNICAL_METRICS.map((item) => ({ ...item, group: "Technicals", key: `template:${item.metric}`, timeframe: metricTimeframe(item.metric, DEFAULT_SCANNER_TECHNICAL_TIMEFRAME) } as FieldDefinition)), ...customDefinitions];
  const matches = availableDefinitions.filter((item) => (!deferredQuery || `${item.label} ${item.key} ${item.description}`.toLowerCase().includes(deferredQuery)) && (deferredQuery || item.group === group));
  function toggle(key: string) { if (lockedColumns.includes(key)) return; onChange(columns.includes(key) ? columns.filter((column) => column !== key) : [...columns, key]); }
  return <aside aria-label="Add scanner columns" className="market-column-picker">
    <header><div><strong>Columns</strong><span>{columns.length} selected</span></div><button aria-label="Close columns" onClick={onClose} type="button"><X size={15} /></button></header>
    <label><Search size={14} /><input autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Search every available field" value={query} /></label>
    <div className="market-column-picker-body">
      {!deferredQuery ? <nav>{groups.map((item) => <button className={group === item ? "active" : undefined} key={item} onClick={() => setGroup(item)} type="button"><span>{item}</span><b>{availableDefinitions.filter((fieldItem) => fieldItem.group === item).length}</b></button>)}</nav> : null}
      <section className={deferredQuery ? "search-results" : undefined}><button className="market-column-back" onClick={() => { setQuery(""); setGroup(groups[0]); }} type="button"><ChevronLeft size={14} /> {deferredQuery ? "All groups" : group}</button>{matches.map((item) => { const template = item.key.startsWith("template:"); const templateTimeframe = item.timeframe ?? DEFAULT_SCANNER_TECHNICAL_TIMEFRAME; const selectedKey = template && item.metric ? technicalColumnKey(item.metric, templateTimeframe) : item.key; const locked = lockedColumns.includes(selectedKey); const coverage = fieldCoverage?.[selectedKey]; const selected = columns.includes(selectedKey); return <button aria-disabled={locked} className={`${selected ? "selected" : ""}${locked ? " locked" : ""}`.trim()} key={item.key} onClick={() => template && item.metric ? selected ? toggle(selectedKey) : onAddTechnical(item.metric, templateTimeframe) : toggle(item.key)} type="button"><i>{selected ? <Check size={12} /> : null}</i><span><strong>{item.label}{item.timeframe ? <small className="market-column-inline-timeframe">{timeframeLabel(item.timeframe)}</small> : null}</strong><small>{item.description}</small></span><em data-kind={item.kind}>{locked ? "pinned" : coverage !== undefined ? `${coverage}%` : item.kind}</em></button>; })}</section>
    </div>
  </aside>;
}

function buildSignalEvents(rows: ScreenerRow[], strategySignals: ScreenerRow[], asOf: string) {
  const derived = rows.flatMap((row) => {
    const ticker = String(row.ticker ?? "");
    const fiveMinute = numberValue(row.change_5m_pct);
    const windowChange = numberValue(row.change_pct);
    const events: ScreenerRow[] = [];
    const add = (signalType: string, direction: string, magnitude: number, evidence: string) => events.push({ ...row, direction, event_time: asOf, evidence, magnitude, signal_type: signalType, source: "Market rule" });
    if (Math.abs(fiveMinute) >= 10) add(fiveMinute > 0 ? "10% pop · 5m" : "10% drop · 5m", fiveMinute > 0 ? "bullish" : "bearish", fiveMinute, `Five-minute return crossed ${fiveMinute > 0 ? "+" : "−"}10%.`);
    else if (Math.abs(fiveMinute) >= 5) add(fiveMinute > 0 ? "5% pop · 5m" : "5% drop · 5m", fiveMinute > 0 ? "bullish" : "bearish", fiveMinute, `Five-minute return crossed ${fiveMinute > 0 ? "+" : "−"}5%.`);
    if (Math.abs(windowChange) >= 5 && Math.sign(windowChange) === Math.sign(fiveMinute)) add("Momentum continuation", windowChange > 0 ? "bullish" : "bearish", windowChange, "Five-minute and observation-window returns agree.");
    if (numberValue(row.trade_count) >= 500) add("Trade activity burst", "neutral", 0, `${formatCompact(numberValue(row.trade_count))} eligible prints in the observation window.`);
    if (numberValue(row.quote_count) >= 1000) add("Quote activity burst", "neutral", 0, `${formatCompact(numberValue(row.quote_count))} NBBO updates in the observation window.`);
    if (String(row.live_news_recency).toLowerCase() === "hot") add("Hot ticker news", "neutral", 0, "Ticker-linked news published within the hot window.");
    if (String(row.sec_recency).toLowerCase() === "hot") add("Hot SEC disclosure", "neutral", 0, "Ticker-linked filing accepted within the hot window.");
    return events.map((event) => ({ ...event, ticker }));
  });
  const strategy: ScreenerRow[] = strategySignals.map((row) => ({
    direction: String(row.direction ?? "neutral").toLowerCase(),
    event_time: row.time ?? row.event_time ?? asOf,
    evidence: row.detail ?? row.reason ?? "Strategy runtime emitted this durable signal.",
    last: row.value,
    magnitude: row.magnitude ?? 0,
    signal_type: row.signal ?? row.signal_type ?? "Strategy signal",
    source: "Strategy runtime",
    ticker: String(row.symbol ?? row.ticker ?? "").toUpperCase(),
  }));
  const combined: ScreenerRow[] = [...derived, ...strategy];
  return combined.sort((left, right) => String(right.event_time).localeCompare(String(left.event_time)));
}

function filterSignalPreset(rows: ScreenerRow[], preset: string) {
  if (preset === "All") return rows;
  if (preset === "Price moves") return rows.filter((row) => /pop|drop|momentum|gap/i.test(String(row.signal_type)));
  if (preset === "Activity") return rows.filter((row) => /activity/i.test(String(row.signal_type)));
  if (preset === "Intelligence") return rows.filter((row) => /news|SEC/i.test(String(row.signal_type)));
  if (preset === "Strategy") return rows.filter((row) => row.source === "Strategy runtime");
  return rows;
}

function normalizeScannerRows(rows: ScreenerRow[]) {
  return rows.map((row) => {
    const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase();
    const last = numberValue(row.last ?? row.snapshot_last_price ?? row.close);
    const volume = numberValue(row.volume);
    return { ...row, dollar_volume: row.dollar_volume ?? (last > 0 && volume > 0 ? last * volume : undefined), ticker };
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
function technicalMetric(metric: TechnicalMetric, labelValue: string, format: FieldDefinition["format"], description: string, timeframes = SCANNER_TIMEFRAMES) {
  return { description, format, group: "Technicals", kind: "derived" as const, label: labelValue, metric, timeframes };
}
function technicalColumnKey(metric: TechnicalMetric, timeframe: ScannerTimeframe) { return `technical__${metric}__${timeframe}`; }
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
    timeframe: column.timeframe,
    timeframes: definition?.timeframes,
  };
}
function metricTimeframe(metric: TechnicalMetric, requested: ScannerTimeframe) {
  const supported = TECHNICAL_METRICS.find((item) => item.metric === metric)?.timeframes ?? SCANNER_TIMEFRAMES;
  return supported.includes(requested) ? requested : supported[0];
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
