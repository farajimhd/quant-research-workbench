import type { LiveEntryLine } from "../../app/components/ChartPanel";
import type { RealLivePortfolioPayload } from "./contracts";

export type OrderRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  account_type?: string;
  account_keys?: string[];
  avg_fill_price?: number | null;
  broker_order_id?: string;
  client_order_id?: string;
  conid?: string;
  filled_quantity?: number;
  id: string;
  last_fill_price?: number | null;
  limit: number;
  quantity: number;
  remaining_quantity?: number;
  side: "BUY" | "SELL";
  status: string;
  stop: number;
  symbol: string;
  timestamp: string;
  type: string;
};

export type PositionRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  asset_class?: string;
  conid?: string;
  currency?: string;
  market_value?: number;
  realized_pnl?: number | null;
  avg_price: number;
  entry_session_date?: string;
  entry_time?: string;
  mark: number;
  quantity: number;
  stop: number;
  symbol: string;
  unrealized_pnl: number;
  unrealized_pnl_pct: number;
  max_unrealized_pnl: number;
};

export type TradeRow = {
  account_class?: string;
  account_id?: string;
  account_key?: string;
  account_label?: string;
  broker_order_id?: string;
  commission?: number | null;
  conid?: string;
  entry_price: number;
  entry_session_date?: string;
  entry_time?: string;
  execution_id?: string;
  exit_order_id?: string;
  exit_price: number;
  exit_session_date: string;
  exit_time: string;
  gross_pnl: number;
  gross_pnl_pct: number;
  id: string;
  quantity: number;
  side: "LONG";
  symbol: string;
};

export type StageOrderContext = {
  limit: number;
  mark: number;
  quantity: number;
  row: Record<string, unknown> | null;
  side: "BUY" | "SELL";
  status: string;
  stop: number;
  symbol: string;
  type: string;
};

export function normalizeRealLivePosition(row: Record<string, unknown>): PositionRow {
  const symbol = stringField(row, "symbol");
  const quantity = numberField(row, "quantity");
  const avgPrice = numberField(row, "avg_price");
  const mark = numberField(row, "mark_price") || avgPrice;
  const unrealizedPnl = numberField(row, "unrealized_pnl") || (mark - avgPrice) * quantity;
  return {
    account_class: stringField(row, "account_class"),
    account_id: stringField(row, "account_id"),
    account_key: stringField(row, "account_key"),
    account_label: stringField(row, "account_label"),
    asset_class: stringField(row, "asset_class"),
    avg_price: avgPrice,
    conid: stringField(row, "conid"),
    currency: stringField(row, "currency"),
    mark,
    market_value: optionalNumberField(row, "market_value") ?? mark * quantity,
    quantity,
    realized_pnl: optionalNumberField(row, "realized_pnl"),
    stop: 0,
    symbol,
    unrealized_pnl: unrealizedPnl,
    unrealized_pnl_pct: avgPrice > 0 ? unrealizedPnl / (avgPrice * Math.abs(quantity || 1)) : 0,
    max_unrealized_pnl: Math.max(0, unrealizedPnl, numberField(row, "max_unrealized_pnl")),
  };
}

export function normalizeRealLiveOrder(row: Record<string, unknown>): OrderRow {
  const quantity = numberField(row, "quantity");
  const filled = numberField(row, "filled_quantity");
  const brokerOrderId = stringField(row, "broker_order_id");
  return {
    account_class: stringField(row, "account_class"),
    account_id: stringField(row, "account_id"),
    account_key: stringField(row, "account_key"),
    account_label: stringField(row, "account_label"),
    account_type: stringField(row, "account_key"),
    avg_fill_price: optionalNumberField(row, "avg_fill_price"),
    broker_order_id: brokerOrderId,
    client_order_id: stringField(row, "client_order_id"),
    conid: stringField(row, "conid"),
    filled_quantity: filled,
    id: `${stringField(row, "account_key") || "account"}-${brokerOrderId || stringField(row, "client_order_id") || `${stringField(row, "symbol")}-${stringField(row, "submitted_at")}`}`,
    last_fill_price: optionalNumberField(row, "last_fill_price"),
    limit: numberField(row, "limit_price"),
    quantity,
    remaining_quantity: numberField(row, "remaining_quantity") || Math.max(0, quantity - filled),
    side: stringField(row, "side") === "SELL" ? "SELL" : "BUY",
    status: stringField(row, "status") || "UNKNOWN",
    stop: 0,
    symbol: stringField(row, "symbol"),
    timestamp: stringField(row, "submitted_at"),
    type: stringField(row, "order_type"),
  };
}

export function normalizeRealLiveExecution(row: Record<string, unknown>): TradeRow {
  const fillPrice = numberField(row, "fill_price");
  const quantity = numberField(row, "filled_quantity");
  const timestamp = stringField(row, "timestamp");
  const sideText = stringField(row, "side");
  return {
    account_class: stringField(row, "account_class"),
    account_id: stringField(row, "account_id"),
    account_key: stringField(row, "account_key"),
    account_label: stringField(row, "account_label"),
    broker_order_id: stringField(row, "broker_order_id"),
    commission: optionalNumberField(row, "commission"),
    conid: stringField(row, "conid"),
    entry_price: sideText === "BUY" ? fillPrice : 0,
    entry_time: timestamp,
    execution_id: stringField(row, "execution_id"),
    exit_order_id: stringField(row, "broker_order_id"),
    exit_price: sideText === "SELL" ? fillPrice : 0,
    exit_session_date: timestamp.split(" ")[0] || "",
    exit_time: timestamp,
    gross_pnl: numberField(row, "gross_amount"),
    gross_pnl_pct: 0,
    id: `${stringField(row, "account_key") || "account"}-${stringField(row, "execution_id") || stringField(row, "broker_order_id") || `${stringField(row, "symbol")}-${timestamp}`}`,
    quantity,
    side: "LONG",
    symbol: stringField(row, "symbol"),
  };
}

export function buildLiveEntryLine(position: PositionRow | undefined, currentBid: number): LiveEntryLine | null {
  if (!position || !position.quantity || !position.avg_price) return null;
  return {
    color: "#2563eb",
    pnl: (currentBid - position.avg_price) * position.quantity,
    price: position.avg_price,
    quantity: position.quantity,
  };
}

export function upsertPosition(rows: PositionRow[], symbol: string, quantity: number, price: number, stop: number, mark: number, entrySessionDate?: string, entryTime?: string): PositionRow[] {
  const existing = rows.find((row) => row.symbol === symbol);
  const nextQuantity = (existing?.quantity ?? 0) + quantity;
  const avgPrice = existing ? ((existing.avg_price * existing.quantity) + (price * quantity)) / Math.max(1, nextQuantity) : price;
  const unrealizedPnl = (mark - avgPrice) * nextQuantity;
  const row = {
    avg_price: avgPrice,
    entry_session_date: existing?.entry_session_date ?? entrySessionDate,
    entry_time: existing?.entry_time ?? entryTime,
    mark,
    quantity: nextQuantity,
    stop,
    symbol,
    unrealized_pnl: unrealizedPnl,
    unrealized_pnl_pct: avgPrice > 0 ? (mark / avgPrice) - 1 : 0,
    max_unrealized_pnl: Math.max(0, unrealizedPnl, existing?.max_unrealized_pnl ?? 0),
  };
  return [row, ...rows.filter((item) => item.symbol !== symbol)];
}

export function reducePosition(rows: PositionRow[], symbol: string, quantity: number, mark: number): PositionRow[] {
  return rows.flatMap((row) => {
    if (row.symbol !== symbol) return [row];
    const nextQuantity = Math.max(0, row.quantity - quantity);
    if (nextQuantity <= 0) return [];
    return [{
      ...row,
      mark,
      quantity: nextQuantity,
      unrealized_pnl: (mark - row.avg_price) * nextQuantity,
      unrealized_pnl_pct: row.avg_price > 0 ? (mark / row.avg_price) - 1 : 0,
      max_unrealized_pnl: Math.max(row.max_unrealized_pnl ?? 0, (mark - row.avg_price) * nextQuantity),
    }];
  });
}

export function buildClosedTrade(position: PositionRow, quantity: number, exitPrice: number, exitSessionDate: string, exitTime: string, exitOrderId: string): TradeRow {
  const closedQuantity = Math.max(0, Math.min(quantity, position.quantity));
  const grossPnl = (exitPrice - position.avg_price) * closedQuantity;
  return {
    entry_price: position.avg_price,
    entry_session_date: position.entry_session_date,
    entry_time: position.entry_time,
    exit_order_id: exitOrderId,
    exit_price: exitPrice,
    exit_session_date: exitSessionDate,
    exit_time: exitTime,
    gross_pnl: grossPnl,
    gross_pnl_pct: position.avg_price > 0 ? (exitPrice / position.avg_price) - 1 : 0,
    id: `${exitOrderId}-trade`,
    quantity: closedQuantity,
    side: "LONG",
    symbol: position.symbol,
  };
}

export function realizedPnlFromTrades(trades: TradeRow[]) {
  return trades.reduce((total, row) => total + row.gross_pnl, 0);
}

export function positionExposure(positions: PositionRow[]) {
  return positions.reduce((total, row) => total + (row.market_value ?? row.mark * row.quantity), 0);
}

export function buildProfitLossRows(positions: PositionRow[], trades: TradeRow[], snapshot: RealLivePortfolioPayload | null) {
  return [
    ...brokerPnlRows(snapshot),
    ...positions.map((row) => ({
      account: row.account_label,
      avg_price: row.avg_price,
      mark: row.mark,
      max_unrealized_pnl: row.max_unrealized_pnl,
      pnl: row.unrealized_pnl,
      pnl_pct: row.unrealized_pnl_pct,
      quantity: row.quantity,
      status: "OPEN",
      symbol: row.symbol,
    })),
    ...trades.map((row) => ({
      account: row.account_label,
      entry_price: row.entry_price,
      exit_price: row.exit_price,
      pnl: row.gross_pnl,
      pnl_pct: row.gross_pnl_pct,
      quantity: row.quantity,
      status: "CLOSED",
      symbol: row.symbol,
    })),
  ];
}

export function portfolioBalanceRows(snapshot: RealLivePortfolioPayload | null): Record<string, unknown>[] {
  return (snapshot?.balances ?? []).filter((row) => row && typeof row === "object");
}

export function brokerPnlRows(snapshot: RealLivePortfolioPayload | null): Record<string, unknown>[] {
  return (snapshot?.pnl ?? []).filter((row) => row && typeof row === "object").map((row) => ({ ...row, status: "BROKER_PNL" }));
}

export function brokerAvailableFunds(snapshot: RealLivePortfolioPayload | null) {
  const balances = portfolioBalanceRows(snapshot);
  const available = balances.reduce((total, row) => total + numberField(row, "available_funds"), 0);
  if (available > 0) return available;
  return balances.reduce((total, row) => total + numberField(row, "cash"), 0);
}

function stringField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

function numberField(row: Record<string, unknown>, key: string) {
  const value = Number(row[key]);
  return Number.isFinite(value) ? value : 0;
}

function optionalNumberField(row: Record<string, unknown>, key: string) {
  const value = row[key];
  if (value === null || value === undefined || value === "") return null;
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}
