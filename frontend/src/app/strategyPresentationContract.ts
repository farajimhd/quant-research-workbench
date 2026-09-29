/** Shared chart language. Strategies publish semantic evidence, never colors,
 * label sentences, or layout. Backtest and live adapters use this same map.
 * Unknown causes stay unlabeled; a price move is not proof of a stop/target hit.
 */
export type StrategyLabelTone =
  | "exitLong" | "exitPriceLong" | "exitPriceShort" | "exitShort"
  | "label" | "long" | "pnlLoss" | "pnlWin" | "price"
  | "priceLong" | "priceShort" | "reason" | "separator"
  | "short" | "size";

export type StrategyLabelPart = { text: string; tone?: StrategyLabelTone };
export type StrategyChartAction =
  | { kind: "entry"; side: "LONG" | "SHORT"; quantity?: number; price?: number }
  | { kind: "exit"; side: "LONG" | "SHORT"; reason?: string; quantity?: number; price?: number; pnl?: number }
  | { kind: "entry_fill"; side: "LONG" | "SHORT"; quantity?: number; requestedQuantity?: number; price?: number; partial?: boolean }
  | { kind: "exit_fill"; side: "LONG" | "SHORT"; reason?: string; quantity?: number; requestedQuantity?: number; price?: number; pnl?: number; partial?: boolean }
  | { kind: "stop_change"; price: number }
  | { kind: "target_change"; price: number };

const EXIT_REASONS: Readonly<Record<string, string>> = {
  protective_stop: "Stop hit", trailing_stop: "Trail hit",
  profit_target: "Target hit", session_flatten: "Session end",
  manual_exit: "Manual exit", macd_episode_ended: "MACD ended",
  three_resistance_step_stop: "3R stop hit",
  supported_swing_low_stop: "Swing stop hit",
  recent_reentry_resistance_stop: "Re-entry stop hit",
  below_vwap_support_stop: "VWAP stop hit",
  one_percent_entry_stop: "1% stop hit",
  five_percent_entry_stop: "5% stop hit",
  luld_buffer_reached: "LULD buffer",
  red_close_below_attempt_open: "Failed retest",
  protective_swing_failed: "Swing low failed",
  confirmed_structural_reversal: "Structure reversed",
  resistance_rejection_failed_recovery: "Rejection failed",
  exit_pending: "Exit pending",
};

export function strategyExitReason(code: string | undefined): string {
  if (!code) return "";
  const key = code.trim().toLowerCase();
  if (!key) return "";
  if (["exit", "exit_issued", "strategy_one_exit", "unknown"].includes(key)) return "";
  const target = /^momentum_target_(\d+)x$/.exec(key);
  if (target) return `Target ${target[1]}×`;
  return EXIT_REASONS[key] ?? (key === "stop_hit" ? "Stop hit" : key === "target_hit" ? "Target hit" : key.replaceAll("_", " "));
}

function priceText(value: number): string {
  return value.toFixed(4).replace(/0+$/, "").replace(/\.$/, "");
}

/** Compact, reusable output. The action marker/rail supplies direction; labels
 * retain only distinct evidence. There is deliberately no "issued" or "@".
 */
export function strategyActionLabel(action: StrategyChartAction): StrategyLabelPart[] {
  if (action.kind === "stop_change" || action.kind === "target_change") {
    return [{ text: action.kind === "stop_change" ? "SL" : "TP", tone: "reason" },
      { text: priceText(action.price), tone: "price" }];
  }
  const parts: StrategyLabelPart[] = [];
  if (action.kind === "entry") {
    parts.push({ text: action.side === "SHORT" ? "Short" : "Long", tone: action.side === "SHORT" ? "short" : "long" });
  } else if (action.kind === "exit" || action.kind === "exit_fill") {
    const reason = strategyExitReason(action.reason);
    if (reason) parts.push({ text: reason, tone: "reason" });
  }
  if ((action.kind === "entry_fill" || action.kind === "exit_fill") && action.partial) {
    parts.push({ text: "Partial", tone: "reason" });
  }
  if (action.quantity !== undefined && Number.isFinite(action.quantity) && action.quantity > 0) {
    const filled = Math.trunc(action.quantity).toLocaleString("en-US");
    const requested = (action.kind === "entry_fill" || action.kind === "exit_fill")
      && action.partial && action.requestedQuantity !== undefined
      && Number.isFinite(action.requestedQuantity) && action.requestedQuantity > action.quantity
      ? `/${Math.trunc(action.requestedQuantity).toLocaleString("en-US")}` : "";
    parts.push({ text: filled + requested, tone: "size" });
  }
  if (action.price !== undefined && Number.isFinite(action.price) && action.price > 0) {
    parts.push({ text: priceText(action.price), tone: action.kind === "entry" || action.kind === "entry_fill"
      ? action.side === "SHORT" ? "priceShort" : "priceLong"
      : action.side === "SHORT" ? "exitPriceShort" : "exitPriceLong" });
  }
  if ((action.kind === "exit" || action.kind === "exit_fill") && action.pnl !== undefined && Number.isFinite(action.pnl)) {
    parts.push({ text: new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", signDisplay: "always", maximumFractionDigits: 2 }).format(action.pnl), tone: action.pnl >= 0 ? "pnlWin" : "pnlLoss" });
  }
  return parts;
}
