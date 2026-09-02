import {
  Activity,
  Banknote,
  BarChart3,
  CheckCircle2,
  CircleDollarSign,
  ClipboardList,
  Clock3,
  Save,
  ShieldAlert,
  SkipForward,
  TableProperties,
  Target,
  TrendingUp,
  WalletCards,
} from "lucide-react";

import type {
  RealLiveAccountConfig,
  RealLivePortfolioPayload,
  RealLiveSessionBaselineStatus,
  ScannerSnapshot,
} from "./contracts";
import {
  brokerAvailableFunds,
  brokerPnlRows,
  portfolioBalanceRows,
  positionExposure,
  realizedPnlFromTrades,
  type OrderRow,
  type PositionRow,
  type TradeRow,
} from "./portfolio";
import type { DecisionState, LiveClockMode } from "./liveWorkspaceContracts";
import { integer, money, numberValue, percent } from "./liveTradingFormat";
import { signedMetricTone } from "./liveWorkspacePresentation";
import type { TradingSession } from "./time";

export function buildSimulationPortfolioMetrics({
  orders,
  positions,
  startingCash,
  trades,
}: {
  orders: OrderRow[];
  positions: PositionRow[];
  startingCash: number;
  trades: TradeRow[];
}) {
  const realized = realizedPnlFromTrades(trades);
  const unrealized = positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const maxUnrealized = positions.reduce((total, row) => total + (row.max_unrealized_pnl ?? Math.max(0, row.unrealized_pnl)), 0);
  const exposure = positionExposure(positions);
  const cash = Math.max(0, startingCash + realized - positions.reduce((total, row) => total + row.avg_price * row.quantity, 0));
  const stagedOrders = orders.filter((order) => order.status === "STAGED").length;
  const fills = orders.filter((order) => order.status === "FILLED").length;
  const wins = trades.filter((trade) => trade.gross_pnl > 0).length;
  const winRate = trades.length ? wins / trades.length : 0;
  return {
    items: [
      { icon: <Banknote size={14} />, label: "Total P/L", tone: signedMetricTone(realized + unrealized), value: money(realized + unrealized) },
      { icon: <CircleDollarSign size={14} />, label: "Realized P/L", tone: signedMetricTone(realized), value: money(realized) },
      { icon: <Activity size={14} />, label: "Open Unrealized", tone: signedMetricTone(unrealized), value: money(unrealized) },
      { icon: <Activity size={14} />, label: "Peak Unrealized", tone: maxUnrealized > 0 ? "success" : "muted", value: money(maxUnrealized) },
      { icon: <Banknote size={14} />, label: "Cash", tone: cash > startingCash ? "success" : cash < startingCash ? "warning" : "muted", value: money(cash) },
      { icon: <Banknote size={14} />, label: "Equity", tone: signedMetricTone(realized + unrealized), value: money(startingCash + realized + unrealized) },
      { icon: <BarChart3 size={14} />, label: "Exposure", tone: exposure ? "info" : "muted", value: money(exposure) },
      { icon: <WalletCards size={14} />, label: "Open Positions", tone: positions.length ? "info" : "muted", value: integer(positions.length) },
      { icon: <ClipboardList size={14} />, label: "Orders", tone: orders.length ? "info" : "muted", value: integer(orders.length) },
      { icon: <CheckCircle2 size={14} />, label: "Trades", tone: trades.length ? "success" : "muted", value: integer(trades.length) },
      { icon: <Save size={14} />, label: "Staged", tone: stagedOrders ? "warning" : "muted", value: integer(stagedOrders) },
      { icon: <CheckCircle2 size={14} />, label: "Fills", tone: fills ? "success" : "muted", value: integer(fills) },
      { icon: <ShieldAlert size={14} />, label: "Win Rate", tone: trades.length ? signedMetricTone(winRate - 0.5) : "muted", value: percent(winRate) },
    ],
  };
}

export function buildBrokerPortfolioMetrics({
  orders,
  positions,
  snapshot,
  trades,
}: {
  orders: OrderRow[];
  positions: PositionRow[];
  snapshot: RealLivePortfolioPayload | null;
  trades: TradeRow[];
}) {
  const brokerPnl = brokerPnlRows(snapshot);
  const realized = positions.reduce((total, row) => total + (row.realized_pnl ?? 0), 0);
  const unrealized = brokerPnl.length ? brokerPnl.reduce((total, row) => total + numberValue(row, "unrealized_pnl"), 0) : positions.reduce((total, row) => total + row.unrealized_pnl, 0);
  const maxUnrealized = positions.reduce((total, row) => total + (row.max_unrealized_pnl ?? Math.max(0, row.unrealized_pnl)), 0);
  const exposure = positionExposure(positions);
  const balances = portfolioBalanceRows(snapshot);
  const cash = brokerAvailableFunds(snapshot);
  const equity = balances.reduce((total, row) => total + numberValue(row, "net_liquidation"), 0);
  const connection = snapshot?.connection ?? {};
  const stagedOrders = orders.filter((order) => order.status === "STAGED").length;
  const fills = orders.filter((order) => order.status === "FILLED").length;
  const wins = trades.filter((trade) => trade.gross_pnl > 0).length;
  const winRate = trades.length ? wins / trades.length : 0;
  const errors = snapshot?.errors?.length ?? 0;
  return {
    items: [
      { icon: <WalletCards size={14} />, label: "Source", tone: snapshot ? "success" : "muted", value: snapshot?.source?.toUpperCase() || "IBKR" },
      { icon: <Activity size={14} />, label: "Portfolio Conn", tone: connection.portfolio === "blocked" ? "danger" : connection.portfolio ? "success" : "muted", value: connection.portfolio || "waiting" },
      { icon: <ClipboardList size={14} />, label: "Order Conn", tone: connection.iserver === "blocked" ? "danger" : connection.iserver ? "success" : "muted", value: connection.iserver || "waiting" },
      { icon: <Banknote size={14} />, label: "Total P/L", tone: signedMetricTone(realized + unrealized), value: money(realized + unrealized) },
      { icon: <CircleDollarSign size={14} />, label: "Realized P/L", tone: signedMetricTone(realized), value: money(realized) },
      { icon: <Activity size={14} />, label: "Open Unrealized", tone: signedMetricTone(unrealized), value: money(unrealized) },
      { icon: <Activity size={14} />, label: "Peak Unrealized", tone: maxUnrealized > 0 ? "success" : "muted", value: money(maxUnrealized) },
      { icon: <Banknote size={14} />, label: "Available", tone: cash ? "info" : "muted", value: money(cash) },
      { icon: <Banknote size={14} />, label: "Net Liq", tone: equity ? "info" : "muted", value: money(equity) },
      { icon: <BarChart3 size={14} />, label: "Exposure", tone: exposure ? "info" : "muted", value: money(exposure) },
      { icon: <WalletCards size={14} />, label: "Open Positions", tone: positions.length ? "info" : "muted", value: integer(positions.length) },
      { icon: <ClipboardList size={14} />, label: "Orders", tone: orders.length ? "info" : "muted", value: integer(orders.length) },
      { icon: <CheckCircle2 size={14} />, label: "Fills", tone: trades.length ? "success" : "muted", value: integer(trades.length) },
      { icon: <Save size={14} />, label: "Staged", tone: stagedOrders ? "warning" : "muted", value: integer(stagedOrders) },
      { icon: <CheckCircle2 size={14} />, label: "Filled Orders", tone: fills ? "success" : "muted", value: integer(fills) },
      { icon: <ShieldAlert size={14} />, label: "Win Rate", tone: trades.length ? signedMetricTone(winRate - 0.5) : "muted", value: percent(winRate) },
      { icon: <ShieldAlert size={14} />, label: "Broker Errors", tone: errors ? "danger" : "muted", value: integer(errors) },
    ],
  };
}

export function buildSimulationGlobalLiveMetrics({
  decisions,
  lastActionTime,
  liveClockMode,
  preloadProgress,
  scannerRows,
  secondsPerMinute,
  session,
  snapshot,
}: {
  decisions: Record<string, DecisionState>;
  lastActionTime: string;
  liveClockMode: LiveClockMode;
  preloadProgress?: number;
  scannerRows: Record<string, unknown>[];
  secondsPerMinute: string;
  session: TradingSession;
  snapshot: ScannerSnapshot | null;
}) {
  const decisionsCount = Object.keys(decisions).length;
  const resolvedPreloadProgress = preloadProgress ?? (liveClockMode === "loading_data" ? 0.45 : 0);
  const modeValue = (
    <span className="live-mode-value">
      <span>{formatLiveMode(liveClockMode)}</span>
      {liveClockMode === "loading_data" ? (
        <span className="live-mode-progress" aria-label="Loading data">
          <span style={{ width: `${Math.max(8, Math.round(resolvedPreloadProgress * 100))}%` }} />
        </span>
      ) : null}
    </span>
  );
  return {
    items: [
      { icon: <Clock3 size={14} />, label: "Date", tone: "info", value: session.sessionDate || "-" },
      { icon: <Clock3 size={14} />, label: "Clock", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" ? "warning" : "muted", value: `${session.barTime} ET` },
      { icon: <Activity size={14} />, label: "Mode", tone: liveClockMode === "running" ? "success" : liveClockMode === "seeking" || liveClockMode === "loading_data" ? "warning" : "muted", value: modeValue },
      { icon: <TableProperties size={14} />, label: "Raw Scanner Rows", tone: snapshot?.row_count ? "info" : "muted", value: integer(snapshot?.row_count ?? 0) },
      { icon: <TrendingUp size={14} />, label: "Signals", tone: scannerRows.length ? "success" : "muted", value: integer(scannerRows.length) },
      { icon: <Target size={14} />, label: "Decisions", tone: decisionsCount ? "info" : "muted", value: integer(decisionsCount) },
      { icon: <SkipForward size={14} />, label: "Replay Pace", tone: "info", value: `${Math.max(1, Number(secondsPerMinute) || 10)}s / 1m` },
      { icon: <CheckCircle2 size={14} />, label: "Last Signal", tone: lastActionTime ? "success" : "muted", value: lastActionTime || "-" },
    ],
  };
}

export function buildBrokerGlobalLiveMetrics({
  decisions,
  exchangeClock,
  lastActionTime,
  liveClockMode,
  localClock,
  scannerRows,
  selectedAccounts,
  session,
  sessionBaseline,
  snapshot,
}: {
  decisions: Record<string, DecisionState>;
  exchangeClock: string;
  lastActionTime: string;
  liveClockMode: LiveClockMode;
  localClock: string;
  scannerRows: Record<string, unknown>[];
  selectedAccounts: RealLiveAccountConfig[];
  session: TradingSession;
  sessionBaseline: RealLiveSessionBaselineStatus;
  snapshot: ScannerSnapshot | null;
}) {
  const decisionsCount = Object.keys(decisions).length;
  const accountLabel = selectedAccounts.length > 1 ? `${selectedAccounts.length} mirrored` : selectedAccounts[0]?.label || "Paper";
  const accountTone = selectedAccounts.some((account) => account.trading_mode !== "paper") ? "warning" : "info";
  const baselineStatus = sessionBaseline.status || "not_started";
  const baselineTone = baselineStatus === "written" || baselineStatus === "written_with_errors" ? "success" : baselineStatus === "pending" ? "warning" : baselineStatus === "failed" ? "danger" : "muted";
  const baselineValue = baselineStatus === "written" || baselineStatus === "written_with_errors"
    ? `${integer(sessionBaseline.scanner_rows_written ?? sessionBaseline.scanner_row_count ?? 0)} rows`
    : baselineStatus;
  return {
    items: [
      { icon: <Banknote size={14} />, label: "Accounts", tone: accountTone, value: accountLabel },
      { icon: <Clock3 size={14} />, label: "Exchange", tone: "info", value: exchangeClock || `${session.barTime} ET` },
      { icon: <Clock3 size={14} />, label: "Local", tone: "info", value: localClock || "-" },
      { icon: <Activity size={14} />, label: "Mode", tone: liveClockMode === "running" ? "success" : liveClockMode === "loading_data" ? "warning" : "muted", value: <span className="live-mode-value"><span>{formatLiveMode(liveClockMode)}</span></span> },
      { icon: <TableProperties size={14} />, label: "Scanner Rows", tone: snapshot?.row_count ? "info" : "muted", value: integer(snapshot?.row_count ?? 0) },
      { icon: <TrendingUp size={14} />, label: "Signals", tone: scannerRows.length ? "success" : "muted", value: integer(scannerRows.length) },
      { icon: <Save size={14} />, label: "Baseline", tone: baselineTone, value: baselineValue },
      { icon: <Target size={14} />, label: "Decisions", tone: decisionsCount ? "info" : "muted", value: integer(decisionsCount) },
      { icon: <CheckCircle2 size={14} />, label: "Last Refresh", tone: lastActionTime ? "success" : "muted", value: lastActionTime || "-" },
    ],
  };
}

export function formatLiveMode(mode: LiveClockMode) {
  return mode === "loading_data" ? "loading data" : mode;
}
