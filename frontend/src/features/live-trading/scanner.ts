import type { BackendTableQuery } from "../../app/components/DataTable";
import { dateInTimeZone } from "../../app/timeZones";
import type { TradingSession } from "./time";

export function normalizeRealLiveScannerRow(row: Record<string, unknown>, session: TradingSession): Record<string, unknown> {
  const ticker = stringField(row, "symbol") || stringField(row, "ticker");
  const lastPrice = numberField(row, "current_open") || numberField(row, "last_price") || numberField(row, "price");
  const bid = numberField(row, "bid");
  const ask = numberField(row, "ask");
  const dayVolume = numberField(row, "last_day_volume_so_far") || numberField(row, "day_volume");
  const tradeCount = numberField(row, "last_transactions") || numberField(row, "trade_count");
  const dayChange = numberField(row, "last_day_current_change_pct") || numberField(row, "day_change_pct");
  const dayNotional = numberField(row, "last_day_dollar_volume_so_far") || numberField(row, "day_notional") || dayVolume * lastPrice;
  const vwap = numberField(row, "last_vwap") || lastPrice;
  const barTimeMarket = stringField(row, "bar_time_market") || dateInTimeZone(session.sessionDate, session.barTime, "America/New_York").toISOString();
  return {
    ...row,
    ticker,
    bar_time_market: barTimeMarket,
    current_open: lastPrice,
    last_close: lastPrice,
    last_open: lastPrice,
    last_high: lastPrice,
    last_low: lastPrice,
    last_return_5: dayChange,
    last_volume: dayVolume,
    last_recent_volume_5: dayVolume,
    last_transactions: tradeCount,
    last_transactions_vs_prior_3: 0,
    last_day_open: lastPrice > 0 && dayChange > -0.99 ? lastPrice / (1 + dayChange) : lastPrice,
    last_day_high_so_far: lastPrice,
    last_day_low_so_far: lastPrice,
    last_day_volume_so_far: dayVolume,
    last_day_dollar_volume_so_far: dayNotional,
    last_vwap: vwap,
    bid,
    ask,
    spread_bps_abs: numberField(row, "spread_bps_abs") || numberField(row, "spread_bps"),
    live_bias: stringField(row, "market_state") || (dayChange > 0 ? "bullish" : dayChange < 0 ? "bearish" : "neutral"),
    live_news_count: 0,
    live_news_items: [],
    live_news_latest_time: "",
    live_news_latest_title: "",
    live_news_recency: "none",
    live_news_recent: false,
    live_setup_group: stringField(row, "signal_type") || stringField(row, "market_state") || "massive-live",
    suggested_entry: ask || lastPrice,
    suggested_stop: bid || lastPrice * 0.97,
  };
}

export function scannerQueryFromConditions(conditions: BackendTableQuery["conditions"]): BackendTableQuery {
  return { conditions, matchMode: "all", sortColumn: "last_return_5", sortDirection: "desc" };
}

export function emptyScannerQuery(): BackendTableQuery {
  return { conditions: [], matchMode: "all", sortDirection: "asc" };
}

export function normalizeLiveScannerQuery(query: BackendTableQuery | null): BackendTableQuery | null {
  if (!query) return null;
  return {
    ...query,
    conditions: (query.conditions ?? []).map((condition) => ({
      ...condition,
      column: condition.column === "last_5m_return" ? "last_return_5" : condition.column,
    })),
    sortColumn: query.sortColumn === "last_5m_return" ? "last_return_5" : query.sortColumn,
  };
}

export function rowMatchesBackendQuery(row: Record<string, unknown>, query: BackendTableQuery | null) {
  const conditions = query?.conditions ?? [];
  if (!conditions.length) return true;
  const results = conditions.map((condition) => rowMatchesBackendCondition(row, condition));
  return (query?.matchMode ?? "all") === "any" ? results.some(Boolean) : results.every(Boolean);
}

function rowMatchesBackendCondition(row: Record<string, unknown>, condition: BackendTableQuery["conditions"][number]) {
  const column = condition.column === "last_5m_return" ? "last_return_5" : condition.column;
  const value = row[column];
  const operator = condition.operator ?? "contains";
  if (operator === "is_null") return isBlankLiveValue(value);
  if (operator === "is_not_null") return !isBlankLiveValue(value);
  if (isBlankLiveValue(value)) return false;
  if (operator === "contains" || operator === "starts_with" || operator === "ends_with") {
    const left = String(value).toLowerCase();
    const right = String(condition.value ?? "").toLowerCase();
    if (!right) return false;
    if (operator === "contains") return left.includes(right);
    if (operator === "starts_with") return left.startsWith(right);
    return left.endsWith(right);
  }
  const leftNumber = Number(value);
  const rightNumber = Number(condition.value);
  if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
    if (operator === "eq") return leftNumber === rightNumber;
    if (operator === "ne") return leftNumber !== rightNumber;
    if (operator === "gt") return leftNumber > rightNumber;
    if (operator === "gte") return leftNumber >= rightNumber;
    if (operator === "lt") return leftNumber < rightNumber;
    if (operator === "lte") return leftNumber <= rightNumber;
    if (operator === "between") {
      const secondaryNumber = Number(condition.valueSecondary);
      if (!Number.isFinite(secondaryNumber)) return false;
      return leftNumber >= Math.min(rightNumber, secondaryNumber) && leftNumber <= Math.max(rightNumber, secondaryNumber);
    }
  }
  const leftText = String(value);
  const rightText = String(condition.value ?? "");
  if (operator === "eq") return leftText === rightText;
  if (operator === "ne") return leftText !== rightText;
  return false;
}

export function buildMarketStateRows(rows: Record<string, unknown>[]): Record<string, unknown>[] {
  const marketRows = rows.map(buildMarketStateRow);
  const transactionValues = sortedPositiveValues(marketRows.map((row) => numberField(row, "last_transactions")));
  const dollarVolumeValues = sortedPositiveValues(marketRows.map((row) => numberField(row, "last_bar_dollar_volume")));
  return marketRows
    .map((row) => ({
      ...row,
      last_dollar_volume_market_strength: percentileRank(numberField(row, "last_bar_dollar_volume"), dollarVolumeValues),
      last_transactions_market_strength: percentileRank(numberField(row, "last_transactions"), transactionValues),
    }))
    .sort((a, b) => numberField(b, "last_day_volume_so_far") - numberField(a, "last_day_volume_so_far"));
}

export function buildMarketStateRow(row: Record<string, unknown>): Record<string, unknown> {
  const dayOpen = numberField(row, "last_day_open");
  const dayHigh = numberField(row, "last_day_high_so_far");
  const currentOpen = numberField(row, "current_open") || numberField(row, "open");
  const lastClose = numberField(row, "last_close");
  const currentReference = currentOpen || lastClose;
  return {
    ...row,
    last_bar_dollar_volume: currentReference > 0 ? numberField(row, "last_volume") * currentReference : null,
    last_day_current_change_pct: dayOpen > 0 && currentReference > 0 ? (currentReference / dayOpen) - 1 : null,
    last_day_max_change_pct: dayOpen > 0 && dayHigh > 0 ? (dayHigh / dayOpen) - 1 : null,
  };
}

export function enrichLiveCandidate(row: Record<string, unknown>, queryName: string): Record<string, unknown> {
  const currentOpen = numberField(row, "current_open") || numberField(row, "open");
  const lastVwap = numberField(row, "last_vwap");
  const lastClose = numberField(row, "last_close");
  const lastOpen = numberField(row, "last_open");
  const dayHigh = numberField(row, "last_day_high_so_far");
  const lastLow = numberField(row, "last_low");
  const last5mReturn = numberField(row, "last_return_5") || numberField(row, "last_5m_return");
  const transactions = numberField(row, "last_transactions");
  const txRatio = numberField(row, "last_transactions_vs_prior_3");
  const bvd = numberField(row, "last_bearish_volume_divergence_score");
  const aboveVwap = lastVwap > 0 && currentOpen > lastVwap;
  const breakingBody = Boolean(row.current_open_above_last_2_body_high);
  const nearDayHigh = dayHigh > 0 && currentOpen >= dayHigh * 0.995;
  const lastRed = lastClose > 0 && lastOpen > 0 && lastClose < lastOpen;
  const extendedVwap = lastVwap > 0 ? (currentOpen / lastVwap) - 1 : 0;
  const reasons = [
    queryName || "Query match",
    `5m ${formatPercent(last5mReturn)}`,
    `${formatInteger(transactions)} tx`,
    `${formatNumber(txRatio, 1)}x tx`,
    aboveVwap ? `open > VWAP by ${formatPercent(extendedVwap)}` : "",
    breakingBody ? "body break" : "",
    nearDayHigh ? "near day high" : "",
  ].filter(Boolean);
  const risks = [
    !aboveVwap ? "below VWAP" : "",
    lastRed ? "last candle red" : "",
    bvd > 50 ? `BVD ${formatNumber(bvd, 0)}` : "",
    extendedVwap > 0.12 ? `extended ${formatPercent(extendedVwap)} from VWAP` : "",
  ].filter(Boolean);
  return {
    ...buildMarketStateRow(row),
    body_break_open: breakingBody,
    day_high_pressure: nearDayHigh,
    live_bias: risks.length >= 2 ? "Risk" : aboveVwap && !lastRed ? "Ready" : "Watch",
    live_priority: 100 + last5mReturn * 100 + Math.min(25, txRatio) + (aboveVwap ? 10 : 0) + (breakingBody ? 8 : 0) - risks.length * 8,
    live_reasons: reasons.join(" | "),
    live_risks: risks.join(" | "),
    live_setup_group: queryName || "Query Match",
    open_vs_vwap_pct: extendedVwap,
    suggested_entry: currentOpen || lastClose,
    suggested_stop: lastVwap > 0 ? lastVwap * 0.99 : Math.min(lastLow || currentOpen * 0.98, currentOpen * 0.98),
  };
}

export function marketStateTableColumns(snapshotColumns: string[]) {
  const hiddenColumns = new Set(["live_news_count", "live_news_latest_title", "live_news_latest_time"]);
  const importantColumns = [
    "ticker", "live_news_recency", "current_open", "last_volume", "last_day_volume_so_far",
    "last_recent_volume_5", "last_return_5", "last_gap_pct", "last_day_max_change_pct",
    "last_day_current_change_pct", "last_close", "last_transactions", "last_transactions_vs_prior_3",
    "last_day_dollar_volume_so_far", "last_day_open", "last_day_high_so_far", "last_day_low_so_far",
    "last_vwap", "last_bearish_volume_divergence_score", "last_double_timeframe_bearish_volume_divergence_score",
    "spread_bps_abs",
  ];
  return [...importantColumns, ...snapshotColumns.filter((column) => !importantColumns.includes(column) && !hiddenColumns.has(column))];
}

export function latestLiveChartRow(chart: { row: Record<string, unknown>; ticker: string }, marketRows: Record<string, unknown>[], scannerRows: Record<string, unknown>[]) {
  const ticker = chart.ticker.trim().toUpperCase();
  const matchesTicker = (row: Record<string, unknown>) => stringField(row, "ticker").trim().toUpperCase() === ticker;
  return { ...chart.row, ...(scannerRows.find(matchesTicker) ?? {}), ...(marketRows.find(matchesTicker) ?? {}) };
}

export function quoteFromRow(row: Record<string, unknown>, fallbackOpen: number) {
  const last = fallbackOpen || numberField(row, "current_open") || numberField(row, "open") || numberField(row, "last_close");
  const ask = numberField(row, "ask") || last;
  const bid = numberField(row, "bid") || Math.max(0, ask - 0.01);
  return {
    ask,
    bid,
    spread: Math.max(0, ask - bid),
    transactions: numberField(row, "last_transactions"),
    transactionsMarketStrength: numberField(row, "last_transactions_market_strength"),
    volume: numberField(row, "last_volume"),
    volumeMarketStrength: numberField(row, "last_dollar_volume_market_strength"),
  };
}

function isBlankLiveValue(value: unknown) {
  return value === null || value === undefined || value === "" || (typeof value === "number" && !Number.isFinite(value));
}

function sortedPositiveValues(values: number[]) {
  return values.filter((value) => Number.isFinite(value) && value > 0).sort((a, b) => a - b);
}

function percentileRank(value: number, sortedValues: number[]) {
  if (!(value > 0) || !sortedValues.length) return 0;
  let lowerOrEqual = 0;
  for (const candidate of sortedValues) {
    if (candidate <= value) lowerOrEqual += 1;
    else break;
  }
  return lowerOrEqual / sortedValues.length;
}

function stringField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberField(row: Record<string, unknown>, key: string) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : 0;
}

function formatPercent(value: number) {
  return Number.isFinite(value) ? `${(value * 100).toFixed(Math.abs(value) >= 0.1 ? 1 : 2)}%` : "-";
}

function formatInteger(value: number) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString() : "-";
}

function formatNumber(value: number, digits: number) {
  return Number.isFinite(value) ? value.toFixed(digits) : "-";
}
