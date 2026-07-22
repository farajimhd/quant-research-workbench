import { ArrowDown, ArrowUp, ArrowUpDown, Check, ChevronLeft, Columns3, FileCheck2, Filter, Flame, ListFilter, Plus, Search, Star, Trash2, X } from "lucide-react";
import { useDeferredValue, useMemo, useState, type ReactNode } from "react";

import { MarketTime } from "./MarketTime";
import { TickerLogo, useTickerPresentations } from "./TickerIdentity";

export type ScreenerRow = Record<string, unknown>;
export type ScannerSnapshotMeta = { complete_universe?: boolean; field_coverage?: Record<string, number>; lookback_minutes?: number; materialized?: boolean; row_count?: number; snapshot_at_utc?: string };
export type MarketScannerSettings = { columns: string[]; limit: number; preset: string };
export type SignalStreamSettings = { columns: string[]; limit: number; preset: string };
export type WatchlistSettings = { columns: string[]; limit: number; ownerKind: "strategy" | "user"; ownerName: string; symbols: string[] };

type FieldKind = "derived" | "estimated" | "raw";
type FieldDefinition = {
  description: string;
  format: "date" | "integer" | "money" | "number" | "percent" | "text";
  group: string;
  key: string;
  kind: FieldKind;
  label: string;
};

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
  Fundamentals: ["ticker", "company_name", "exchange", "country", "sector", "market_cap", "shares_outstanding", "float_shares", "short_interest", "short_crowding_pct", "days_to_cover"],
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
    empty="No securities are available at this market clock."
    eyebrow="Market snapshot"
    fieldCoverage={meta?.field_coverage}
    limit={settings.limit}
    lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
    onPresetChange={(preset) => onSettingsChange({ columns: SCANNER_PRESETS[preset] ?? settings.columns, preset })}
    onTickerSelect={onTickerSelect}
    presets={Object.keys(SCANNER_PRESETS)}
    preset={settings.preset}
    rows={normalizedRows}
    subtitle={meta?.complete_universe ? `Full historical universe · ${meta.lookback_minutes ?? 15}-minute causal window · persisted snapshot` : "Scanner universe unavailable or incomplete"}
    title="Scanner"
  />;
}

export function SignalStreamContainer({ asOf, onSettingsChange, onTickerSelect, scannerRows, settings, strategySignals }: { asOf: string; onSettingsChange: (patch: Partial<SignalStreamSettings>) => void; onTickerSelect: (ticker: string) => void; scannerRows: ScreenerRow[]; settings: SignalStreamSettings; strategySignals: ScreenerRow[] }) {
  const events = useMemo(() => buildSignalEvents(normalizeScannerRows(scannerRows), strategySignals, asOf), [asOf, scannerRows, strategySignals]);
  const filtered = useMemo(() => filterSignalPreset(events, settings.preset), [events, settings.preset]);
  return <MarketListSurface
    asOf={asOf}
    columns={withLockedColumns(settings.columns.length ? settings.columns : SIGNAL_PRESETS[settings.preset] ?? SIGNAL_PRESETS.All, LOCKED_MARKET_LIST_COLUMNS)}
    empty="No market or strategy events match this stream."
    eyebrow="Newest first"
    limit={settings.limit}
    lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
    onColumnsChange={(columns) => onSettingsChange({ columns })}
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
      empty="This watchlist has no symbols yet."
      limit={settings.limit}
      lockedColumns={LOCKED_MARKET_LIST_COLUMNS}
      onColumnsChange={(columns) => onSettingsChange({ columns })}
      onTickerSelect={onTickerSelect}
      rowAction={(row) => <button aria-label={`Remove ${row.ticker}`} onClick={() => onSettingsChange({ symbols: settings.symbols.filter((ticker) => ticker !== row.ticker) })} title="Remove from watchlist" type="button"><Trash2 size={13} /></button>}
      rows={rows}
      title={`${owner} watchlist`}
    />
  </section>;
}

function MarketListSurface({ asOf, columns, empty, eyebrow, fieldCoverage, limit, lockedColumns = [], onColumnsChange, onPresetChange, onTickerSelect, preset, presets, rows, subtitle, title }: { asOf: string; columns: string[]; empty: string; eyebrow: string; fieldCoverage?: Record<string, number>; limit: number; lockedColumns?: string[]; onColumnsChange: (columns: string[]) => void; onPresetChange: (preset: string) => void; onTickerSelect: (ticker: string) => void; preset: string; presets: string[]; rows: ScreenerRow[]; subtitle: string; title: string }) {
  return <section className="market-list-surface" aria-label={title}>
    <header className="market-list-heading">
      <div><span className="market-list-eyebrow"><ListFilter size={12} /> {eyebrow}</span><h3>{title}</h3><p>{subtitle} · <MarketTime value={asOf} /></p></div>
      <strong>{formatCompact(rows.length)} rows</strong>
    </header>
    <nav className="market-list-presets" aria-label={`${title} views`}>{presets.map((item) => <button aria-pressed={preset === item} className={preset === item ? "active" : undefined} key={item} onClick={() => onPresetChange(item)} type="button">{item}</button>)}</nav>
    <MarketListTable columns={columns} empty={empty} fieldCoverage={fieldCoverage} limit={limit} lockedColumns={lockedColumns} onColumnsChange={onColumnsChange} onTickerSelect={onTickerSelect} rows={rows} title={title} />
  </section>;
}

function MarketListTable({ columns, empty, fieldCoverage, limit, lockedColumns = [], onColumnsChange, onTickerSelect, rowAction, rows, title }: { columns: string[]; empty: string; fieldCoverage?: Record<string, number>; limit: number; lockedColumns?: string[]; onColumnsChange: (columns: string[]) => void; onTickerSelect?: (ticker: string) => void; rowAction?: (row: ScreenerRow) => ReactNode; rows: ScreenerRow[]; title: string }) {
  const [columnPickerOpen, setColumnPickerOpen] = useState(false);
  const [filterMode, setFilterMode] = useState("all");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ column: string; direction: "asc" | "desc" }>({ column: title === "Signal stream" ? "event_time" : "change_pct", direction: "desc" });
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
  function changeSort(column: string) {
    setSort((current) => current.column === column ? { column, direction: current.direction === "asc" ? "desc" : "asc" } : { column, direction: "desc" });
  }
  return <div className="market-list-table-shell">
    <div className="market-list-toolbar">
      <label className="market-list-search"><Search size={14} /><input aria-label={`Search ${title}`} onChange={(event) => setQuery(event.target.value)} placeholder="Search symbols and values" value={query} /></label>
      <label className="market-list-filter"><Filter size={13} /><select aria-label={`Filter ${title}`} onChange={(event) => setFilterMode(event.target.value)} value={filterMode}><option value="all">All rows</option><option value="advancing">Advancing</option><option value="declining">Declining</option><option value="news_hot">Hot news</option><option value="news_cold">Cold news</option><option value="sec_hot">Hot SEC</option><option value="sec_cold">Cold SEC</option>{labelFilters.news.length ? <optgroup label="News labels">{labelFilters.news.map((labelValue) => <option key={`news:${labelValue}`} value={`news_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}{labelFilters.sec.length ? <optgroup label="SEC labels">{labelFilters.sec.map((labelValue) => <option key={`sec:${labelValue}`} value={`sec_label:${normalizeLabel(labelValue)}`}>{labelValue}</option>)}</optgroup> : null}</select></label>
      <span>{visibleRows.length} of {rows.length}</span>
      <button aria-expanded={columnPickerOpen} className="market-list-columns-button" onClick={() => setColumnPickerOpen((open) => !open)} type="button"><Columns3 size={14} /> Columns <b>{columns.length}</b></button>
    </div>
    <div className="market-list-table-scroll"><table className="market-list-table"><thead><tr>{columns.map((column) => { const definition = catalogField(column); const sorted = sort.column === column; const className = columnClass(column); return column === "logo" ? <th aria-label="Ticker logo" className={className} key={column} /> : <th aria-sort={sorted ? (sort.direction === "asc" ? "ascending" : "descending") : "none"} className={className} key={column}><button onClick={() => changeSort(column)} title={definition.description} type="button"><span>{definition.label}<small data-kind={definition.kind}>{definition.kind}</small></span>{sorted ? sort.direction === "asc" ? <ArrowUp size={12} /> : <ArrowDown size={12} /> : <ArrowUpDown size={12} />}</button></th>; })}{rowAction ? <th aria-label="Row actions" /> : null}</tr></thead><tbody>{visibleRows.length ? visibleRows.map((row, index) => { const ticker = String(row.ticker ?? row.symbol ?? "").trim().toUpperCase(); const selectable = Boolean(ticker && onTickerSelect); const select = () => { if (selectable) onTickerSelect?.(ticker); }; return <tr aria-label={selectable ? `Open ${ticker} chart` : undefined} data-selectable={selectable ? "true" : undefined} key={`${ticker || "row"}:${row.event_time ?? index}:${index}`} onClick={(event) => { if (!(event.target as HTMLElement).closest("button, input, select, a")) select(); }} onKeyDown={(event) => { if (selectable && (event.key === "Enter" || event.key === " ")) { event.preventDefault(); select(); } }} tabIndex={selectable ? 0 : undefined}>{columns.map((column) => <td className={`${toneClass(row[column], column)} ${columnClass(column)}`.trim()} key={column}>{renderMarketCell(row, column, presentations)}</td>)}{rowAction ? <td className="market-list-row-action">{rowAction(row)}</td> : null}</tr>; }) : <tr><td className="market-list-empty" colSpan={columns.length + (rowAction ? 1 : 0)}>{empty}</td></tr>}</tbody></table></div>
    {columnPickerOpen ? <ColumnPicker columns={columns} fieldCoverage={fieldCoverage} lockedColumns={lockedColumns} onChange={onColumnsChange} onClose={() => setColumnPickerOpen(false)} /> : null}
  </div>;
}

function ColumnPicker({ columns, fieldCoverage, lockedColumns = [], onChange, onClose }: { columns: string[]; fieldCoverage?: Record<string, number>; lockedColumns?: string[]; onChange: (columns: string[]) => void; onClose: () => void }) {
  const groups = [...new Set(FIELD_CATALOG.map((item) => item.group))];
  const [group, setGroup] = useState(groups[0]);
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const matches = FIELD_CATALOG.filter((item) => (!deferredQuery || `${item.label} ${item.key} ${item.description}`.toLowerCase().includes(deferredQuery)) && (deferredQuery || item.group === group));
  function toggle(key: string) { if (lockedColumns.includes(key)) return; onChange(columns.includes(key) ? columns.filter((column) => column !== key) : [...columns, key]); }
  return <aside aria-label="Add scanner columns" className="market-column-picker">
    <header><div><strong>Columns</strong><span>{columns.length} selected</span></div><button aria-label="Close columns" onClick={onClose} type="button"><X size={15} /></button></header>
    <label><Search size={14} /><input autoFocus onChange={(event) => setQuery(event.target.value)} placeholder="Search every available field" value={query} /></label>
    <div className="market-column-picker-body">
      {!deferredQuery ? <nav>{groups.map((item) => <button className={group === item ? "active" : undefined} key={item} onClick={() => setGroup(item)} type="button"><span>{item}</span><b>{FIELD_CATALOG.filter((fieldItem) => fieldItem.group === item).length}</b></button>)}</nav> : null}
      <section className={deferredQuery ? "search-results" : undefined}><button className="market-column-back" onClick={() => { setQuery(""); setGroup(groups[0]); }} type="button"><ChevronLeft size={14} /> {deferredQuery ? "All groups" : group}</button>{matches.map((item) => { const locked = lockedColumns.includes(item.key); const coverage = fieldCoverage?.[item.key]; return <button aria-disabled={locked} className={`${columns.includes(item.key) ? "selected" : ""}${locked ? " locked" : ""}`.trim()} key={item.key} onClick={() => toggle(item.key)} type="button"><i>{columns.includes(item.key) ? <Check size={12} /> : null}</i><span><strong>{item.label}</strong><small>{item.description}</small></span><em data-kind={item.kind}>{locked ? "pinned" : coverage !== undefined ? `${coverage}%` : item.kind}</em></button>; })}</section>
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

function renderMarketCell(row: ScreenerRow, column: string, presentations: ReturnType<typeof useTickerPresentations>) {
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
  const definition = catalogField(column);
  if (value === null || value === undefined || value === "") return <span className="market-list-unavailable" title={`${definition.label} is not available from the active source at this clock.`}>—</span>;
  if (definition.format === "percent") return `${numberValue(value) > 0 ? "+" : ""}${numberValue(value).toFixed(Math.abs(numberValue(value)) < 1 ? 2 : 1)}%`;
  if (definition.format === "money") return formatMoney(numberValue(value));
  if (definition.format === "integer") return formatCompact(numberValue(value));
  if (definition.format === "number") return numberValue(value).toFixed(2);
  return String(value);
}

function TickerEventIcon({ source, value }: { source: "News" | "SEC"; value: string }) {
  const state = value.toLowerCase();
  if (state !== "hot" && state !== "cold") return null;
  const Icon = source === "News" ? Flame : FileCheck2;
  const label = `${state} ${source.toLowerCase()}`;
  return <span aria-label={label} className="market-list-ticker-event" data-source={source.toLowerCase()} data-state={state} title={label}><Icon aria-hidden="true" size={12} /></span>;
}

function toneClass(value: unknown, column: string) {
  const numeric = numberValue(value);
  if (["change_pct", "change_5m_pct", "gap_pct", "magnitude", "qmd_signal"].includes(column)) return numeric > 0 ? "positive" : numeric < 0 ? "negative" : "neutral";
  const text = String(value ?? "").toLowerCase();
  if (text === "bullish") return "positive";
  if (text === "bearish") return "negative";
  return "";
}

function catalogField(key: string) { return FIELD_CATALOG.find((item) => item.key === key) ?? field(key, label(key), "Other", "raw", "text", "Available source field."); }
function withLockedColumns(columns: string[], lockedColumns: string[]) {
  const leading: string[] = lockedColumns.filter((column) => column === "logo" || column === "ticker");
  const trailing = lockedColumns.filter((column) => !leading.includes(column));
  return [...leading, ...columns.filter((column) => !lockedColumns.includes(column)), ...trailing];
}
function columnClass(column: string) { return column === "logo" ? "market-list-logo-column" : column === "news_labels" || column === "sec_labels" ? "market-list-label-column" : ""; }
function rowLabels(value: unknown) { return [...new Set(String(value ?? "").split(",").map((item) => item.trim()).filter(Boolean))]; }
function collectLabels(rows: ScreenerRow[], column: "news_labels" | "sec_labels") { return [...new Set(rows.flatMap((row) => rowLabels(row[column])))].sort((left, right) => left.localeCompare(right)); }
function normalizeLabel(value: string) { return value.trim().toLowerCase(); }
function field(key: string, labelValue: string, group: string, kind: FieldKind, format: FieldDefinition["format"], description: string): FieldDefinition { return { description, format, group, key, kind, label: labelValue }; }
function label(value: string) { return value.replaceAll("_", " ").replace(/\b\w/g, (character) => character.toUpperCase()); }
function numberValue(value: unknown) { const numeric = Number(value); return Number.isFinite(numeric) ? numeric : 0; }
function compareValues(left: unknown, right: unknown) { const leftNumber = Number(left); const rightNumber = Number(right); if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) return leftNumber - rightNumber; return String(left ?? "").localeCompare(String(right ?? ""), undefined, { numeric: true }); }
function formatCompact(value: number) { return new Intl.NumberFormat("en-US", { maximumFractionDigits: 1, notation: Math.abs(value) >= 1000 ? "compact" : "standard" }).format(value); }
function formatMoney(value: number) { if (!Number.isFinite(value)) return "—"; const compact = Math.abs(value) >= 100_000; return new Intl.NumberFormat("en-US", { currency: "USD", maximumFractionDigits: compact ? 1 : value < 10 ? 4 : 2, notation: compact ? "compact" : "standard", style: "currency" }).format(value); }
