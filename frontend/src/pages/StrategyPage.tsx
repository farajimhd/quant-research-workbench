import { Activity, Banknote, BarChart3, ChevronDown, ChevronRight, CircleDollarSign, CircleHelp, Database, Gauge, ListChecks, MoreHorizontal, Percent, Play, Shield, SlidersHorizontal, StopCircle, Trash2 } from "lucide-react";
import { useEffect, useMemo, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";

import { api, query } from "../api/client";
import { ChartPanel, type ChartCatalogItem, type ChartPayload, type ChartReference } from "../app/components/ChartPanel";
import { DataTable, type DataTableFilterPreset, type DataTableManualFilterState } from "../app/components/DataTable";
import { Modal } from "../app/components/Modal";
import { PageIntro } from "../app/components/PageIntro";
import { ProgressMeter } from "../app/components/Progress";
import { SemanticBadge, toneForStatus, type SemanticTone } from "../app/components/SemanticBadge";
import { CachedTabPanel, Tabs, useCachedTabState } from "../app/components/Tabs";
import { formatMoney, formatNumber, formatPct } from "../app/format";

type Strategy = {
  name: string;
  display_name: string;
  description: string;
  version_descriptions?: Record<string, string>;
  versions?: string[];
  default_version?: string;
};

type RunRow = {
  run_id: string;
  run_name: string;
  strategy_name: string;
  strategy_version: string;
  status: string;
  created_at: string;
  date_range: string;
  return_pct: number;
  total_pnl: number;
  trade_count: number;
};

type StrategyConfig = {
  strategy_name: string;
  strategy_version: string;
  strategy_description?: string;
  strategy_version_description?: string;
  run_name: string;
  start_date: string;
  end_date: string;
  data_root: string;
  processed_data_root: string;
  output_root: string;
  excluded_symbols_file: string;
  initial_cash: number;
  slippage_bps: number;
  max_entry_participation_rate: number;
  max_entry_trade_multiple: number;
  enable_partial_fills: boolean;
  max_allowable_entry_fill_size: number;
  exit_liquidity_slippage_bps_per_excess_multiple: number;
  fee_model: string;
  fee_tax_rate: number;
  save_symbol_bars: boolean;
  observability_mode: string;
  observability_sessions: number;
  observability_scanner_top_percent: number;
  observability_scanner_min_rows: number;
  observability_scanner_max_rows: number;
  observability_always_trace_trades: boolean;
  strategy_params: Record<string, number | string | boolean>;
};

type StrategyParamValue = number | string | boolean;
type EditTarget = "run" | "strategy";
type NewRunMetricTone = "danger" | "info" | "neutral" | "success" | "warning";
type NewRunMetric = {
  detail: string;
  icon: ReactNode;
  label: string;
  tone?: NewRunMetricTone;
  value: ReactNode;
};
type DataRow = Record<string, unknown>;
type ObservationChartTarget = {
  label: string;
  row?: DataRow;
  source: string;
  symbol: string;
  timestamp: string;
};

type InteractiveDebugStep = {
  execution_rows?: DataRow[];
  fills?: DataRow[];
  filter_groups?: DataRow[];
  message?: string;
  observability_scanner?: DataRow[];
  observability_state?: DataRow[];
  observability_trace?: DataRow[];
  orders?: DataRow[];
  pending_orders?: DataRow[];
  portfolio?: DataRow[];
  positions?: DataRow[];
  raw_scanner_rows?: DataRow[];
  recent_fills_for_next_bar?: DataRow[];
  recent_orders_for_next_bar?: DataRow[];
  rejection_events?: DataRow[];
  signal_events?: DataRow[];
  strategy_requests?: DataRow[];
  strategy_scanner_rows?: DataRow[];
  strategy_watchlist_rows?: DataRow[];
  summary?: Record<string, unknown>;
  timestamp?: string | null;
  trades?: DataRow[];
  type?: string;
};

type InteractiveDebugSession = {
  config: Record<string, unknown>;
  current_session?: string | null;
  current_step_index: number;
  processed_event_bars: number;
  session_id: string;
  sessions: string[];
  status: string;
  step?: InteractiveDebugStep | null;
  strategy?: Record<string, unknown>;
  summary?: Record<string, unknown>;
  total_event_bars: number;
  total_history_steps: number;
};

const OBSERVABILITY_SCANNER_SETUP_FILTER_PRESET: DataTableFilterPreset = {
  filters: scannerBooleanFilters([
    "long_momentum_v5_price_above_vwap",
    "long_momentum_v5_tema_stack_ok",
    "long_momentum_v5_tema_spread_ok",
    "long_momentum_v5_macd_line_positive",
    "long_momentum_v5_macd_hist_ok",
    "long_momentum_v5_above_day_open",
    "long_momentum_v5_near_enough_day_high",
    "long_momentum_v5_volume_vs_avg_so_far_ok",
    "long_momentum_v5_bearish_divergence_ok",
  ]),
  label: "Setup Uptrend",
  title: "Apply the v5 setup filters: VWAP, TEMA, MACD, day-open, day-high position, volume expansion, and bearish divergence guard.",
};
const OBSERVABILITY_SCANNER_EARLY_MOVE_FILTER_PRESET: DataTableFilterPreset = {
  filters: scannerBooleanFilters([
    "long_momentum_v5_distance_from_day_low_ok",
    "long_momentum_v5_distance_above_vwap_ok",
    "long_momentum_v5_open_above_last_close_ok",
    "long_momentum_v5_last_bar_range_ok",
    "long_momentum_v5_day_high_chase_ok",
  ]),
  label: "Early Move",
  title: "Apply the v5 early-move filters: distance from day low, VWAP distance, no jump, bar range, and day-high chase guard.",
};
const OBSERVABILITY_SCANNER_MOMENTUM_FILTER_PRESET: DataTableFilterPreset = {
  filters: {
    last_close: { operator: "between", presetLabel: "between 1 and 10", valueText: "1", valueTextSecondary: "10" },
    current_open_above_last_body_high: { operator: "eq", presetLabel: ">= last high", valueText: "true" },
    last_volume: { operator: "gte", presetLabel: ">= 10,000", valueText: "10000" },
    last_transactions: { operator: "gte", presetLabel: ">= 100", valueText: "100" },
    long_momentum_spread_ok: { operator: "eq", presetLabel: "Is true", valueText: "true" },
    last_tema_open: { operator: "eq", presetLabel: "Is true", valueText: "true" },
    last_macd_line: { operator: "gt", presetLabel: "Positive", valueText: "0" },
    last_macd_hist_z_since_open: { operator: "gte", presetLabel: ">= 0.1", valueText: "0.1" },
  },
  label: "Momentum Filters",
  title: "Apply the original Long Momentum scanner filters to the strategy-time row view.",
};
const OBSERVABILITY_SCANNER_SPREAD_FILTER_PRESET: DataTableFilterPreset = {
  filters: {
    last_spread_bps_abs: { operator: "lte", presetLabel: "<= 100 bps", valueText: "100" },
    last_spread_bps_max: { operator: "lte", presetLabel: "<= 150 bps", valueText: "150" },
    last_quote_valid_ratio: { operator: "gte", presetLabel: ">= 0.8", valueText: "0.8" },
    last_locked_or_crossed_count: { operator: "eq", presetLabel: "Zero", valueText: "0" },
    last_recent_dollar_volume_5: { operator: "gte", presetLabel: ">= $100k", valueText: "100000" },
  },
  label: "Spread Quality",
  title: "Apply bid/ask spread and recent dollar-volume filters to avoid high-cost fills.",
};
const OBSERVABILITY_SCANNER_FILTER_PRESETS = [
  OBSERVABILITY_SCANNER_MOMENTUM_FILTER_PRESET,
  OBSERVABILITY_SCANNER_SETUP_FILTER_PRESET,
  OBSERVABILITY_SCANNER_EARLY_MOVE_FILTER_PRESET,
  OBSERVABILITY_SCANNER_SPREAD_FILTER_PRESET,
];
const DEBUG_BACKTEST_SCANNER_MAX_ROWS = 500;
const BACKTEST_RESULT_TABS = ["Backtest Results", "Observability", "Daily", "Trades", "Orders", "Fills", "Positions", "Watchlist"];

function scannerBooleanFilters(columns: string[]): DataTableFilterPreset["filters"] {
  return Object.fromEntries(columns.map((column) => [column, { operator: "eq", presetLabel: "Is true", valueText: "true" }]));
}

function trueFilter(): DataTableManualFilterState {
  return { operator: "eq", presetLabel: "Is true", valueText: "true" };
}

function hasValueFilter(): DataTableManualFilterState {
  return { operator: "not_null", presetLabel: "Has value", valueText: "" };
}

function gteFilter(value: unknown, label?: string): DataTableManualFilterState {
  const valueText = String(value ?? 0);
  return { operator: "gte", presetLabel: label ?? `>= ${valueText}`, valueText };
}

function gtFilter(value: unknown, label?: string): DataTableManualFilterState {
  const valueText = String(value ?? 0);
  return { operator: "gt", presetLabel: label ?? `> ${valueText}`, valueText };
}

function lteFilter(value: unknown, label?: string): DataTableManualFilterState {
  const valueText = String(value ?? 0);
  return { operator: "lte", presetLabel: label ?? `<= ${valueText}`, valueText };
}

function betweenFilter(minValue: unknown, maxValue: unknown, label?: string): DataTableManualFilterState {
  const valueText = String(minValue ?? 0);
  const valueTextSecondary = String(maxValue ?? 0);
  return { operator: "between", presetLabel: label ?? `${valueText}-${valueTextSecondary}`, valueText, valueTextSecondary };
}

const tabs = ["Backtest", "Runs", "Strategy README"];
const defaultStrategyName = "long_momentum";
const CHART_DISPLAY_ITEMS_NONE = "__none__";
const VOLUME_PROFILE_DISPLAY_ITEM = "feature.volume_profile";

type StrategySelection = {
  strategyName: string;
  version: string;
};

const RUN_PARAMETER_HELP: Record<string, string> = {
  run_name: "A readable label for this backtest run. It is used in saved run folders and run history.",
  strategy_version: "Version of the strategy implementation used for this run. It is saved with the run metadata.",
  output_root: "Folder where backtest outputs, summaries, logs, and tables are saved.",
  start_date: "First market session included in the backtest range.",
  end_date: "Last market session included in the backtest range.",
  data_root: "Raw source data root used by older or fallback data readers.",
  processed_data_root: "Canonical provider-built market data root used by the backtest.",
  excluded_symbols_file: "CSV of tickers that the backtest engine removes before strategy evaluation and rejects at order submission.",
  initial_cash: "Starting portfolio cash used to size positions and calculate return.",
  slippage_bps: "Per-fill slippage in basis points. One basis point is 0.01 percent.",
  max_entry_participation_rate: "Legacy liquidity capacity share used for sell-side partial fills and exit slippage estimates.",
  max_entry_trade_multiple: "Legacy liquidity capacity multiple used for sell-side partial fills and exit slippage estimates.",
  enable_partial_fills: "When enabled, the backtest can partially fill orders instead of assuming every requested share fills.",
  max_allowable_entry_fill_size: "Maximum BUY entry shares the backtest can fill on one candle when partial fills are enabled.",
  exit_liquidity_slippage_bps_per_excess_multiple: "Extra sell-side slippage per multiple above the estimated fill capacity.",
  fee_model: "Commission and regulatory fee model applied at each fill. The default estimates IBKR Canada fixed pricing for US stocks.",
  fee_tax_rate: "Optional tax rate applied to estimated commissions. Leave 0 unless you want to explicitly model GST/PST/HST.",
  save_symbol_bars: "When enabled, the run saves per-symbol bar snapshots for diagnostics.",
  observability_mode: "Controls rich strategy debugging artifacts. Standard captures a bounded profiling window; Off saves only core execution artifacts.",
  observability_sessions: "Number of initial market sessions with rich scanner, trace, and state capture.",
  observability_scanner_top_percent: "Fraction of scanner candidates to keep at each actionable timestamp. Use 0.25 for the top 25 percent.",
  observability_scanner_min_rows: "Minimum scanner rows to keep when scanner capture is active.",
  observability_scanner_max_rows: "Maximum scanner rows to keep at each actionable timestamp.",
  observability_always_trace_trades: "Keep entry/exit intent traces even outside the profiling window."
};

const STRATEGY_PARAMETER_HELP: Record<string, string> = {
  min_price: "Minimum stock price allowed into the premarket candidate universe.",
  max_price: "Maximum stock price allowed into the premarket candidate universe.",
  min_avg_daily_volume: "Minimum average daily volume required before a ticker can be considered.",
  min_atr: "Minimum average true range required so tiny-range symbols are filtered out.",
  relative_volume_daily_share: "Minimum early relative-volume share versus normal daily volume.",
  min_opening_relative_volume: "Minimum opening relative volume required during setup scoring.",
  min_setup_score: "Minimum pre-entry setup score for a ticker to stay in the candidate list.",
  min_live_score: "Minimum live score required before the strategy can place an entry.",
  top_n: "Number of entry-open candidates the live rotation strategy tries to hold.",
  max_gross_exposure_pct: "Maximum portfolio equity deployed across live rotation positions.",
  rank_decay: "How quickly target position size decays from rank one to lower ranks.",
  min_session_dollar_volume: "Minimum session dollar volume required before a symbol can be ranked.",
  min_recent_dollar_volume: "Minimum rolling recent dollar volume required before entry.",
  min_recent_volume: "Minimum rolling recent share volume required before entry.",
  min_recent_transactions: "Minimum rolling recent transaction count required before entry.",
  min_volume: "Minimum current bar share volume required before a Long Momentum entry.",
  min_transactions: "Minimum current bar transaction count required before a Long Momentum entry.",
  min_first_entry_transactions: "Long Momentum v9 1-minute transaction threshold needed to add a ticker to the day momentum watchlist. This is calibrated for 1m bars and should be retuned for other timeframes.",
  min_first_entry_transactions_vs_prior_3: "Long Momentum v9 immediate-entry transaction impulse threshold versus the prior three transactions average. This is calibrated for 1m bars.",
  min_last_5m_return: "Long Momentum v9 provider-built same-session return_5 threshold needed to add a ticker to the day momentum watchlist.",
  max_entry_order_quantity: "Maximum BUY shares Long Momentum v9 is allowed to send to the backtest for a single entry or partial-entry remainder order.",
  max_reentry_bvd_score: "Maximum 1-minute bearish volume divergence score allowed for Long Momentum v9 watchlist reentry. Scores above this block reentry.",
  reentry_vwap_buffer_pct: "Minimum Long Momentum v9 watchlist reentry buffer above VWAP. For example, 2 means last close must be at least 2% above VWAP.",
  max_immediate_entry_candidates_per_bar: "Maximum Long Momentum v9 First Entry candidates the strategy can process on one bar before cash allocation.",
  max_reentry_candidates_per_bar: "Maximum Long Momentum v9 watchlist reentry candidates the strategy can process on one bar after First Entries.",
  watchlist_snapshot_limit: "Maximum Long Momentum v9 day-momentum watchlist rows saved in debug and observability snapshots.",
  min_macd_hist_z_since_open: "Minimum MACD histogram z-score since the open required before a Long Momentum entry.",
  trading_start_minute: "First minute of day where the strategy can evaluate entries. 240 is 04:00 ET.",
  trading_end_minute: "End minute of day, exclusive, where the strategy can evaluate entries. 1200 is 20:00 ET.",
  trigger_1_minute_start: "Earliest minute of day where Long Momentum v4 Trigger 1 can enter.",
  trigger_1_minute_end: "End minute, exclusive, for the first Long Momentum v4 Trigger 1 window.",
  trigger_1_late_minute_start: "Earliest minute of day where the late Long Momentum v4 Trigger 1 window can enter.",
  trigger_1_late_minute_end: "End minute, exclusive, for the late Long Momentum v4 Trigger 1 window.",
  trigger_1_min_break_bps: "Minimum Trigger 1 open break above the body-break threshold, in basis points.",
  entry_minute_start: "Earliest minute of day where Long Momentum v5 can enter.",
  entry_minute_end: "End minute, exclusive, for the first Long Momentum v5 entry window.",
  entry_late_minute_start: "Earliest minute of day where the late Long Momentum v5 entry window can enter.",
  entry_late_minute_end: "End minute, exclusive, for the late Long Momentum v5 entry window.",
  setup_valid_bars: "Number of bars a Long Momentum v5 setup body high remains usable for entry.",
  min_body_break_bps: "Minimum Long Momentum v5 break above the body-break threshold, in basis points.",
  min_tema_spread_pct: "Minimum TEMA9/TEMA20 spread required for Long Momentum v5 trend quality.",
  min_volume_vs_avg_so_far: "Minimum last-bar volume multiple versus average volume so far for Long Momentum v5.",
  min_volume_vs_recent_3: "Minimum last-bar volume multiple versus the last three-bar average for Long Momentum v5.",
  max_entry_below_day_high_pct: "Maximum distance below day high allowed for Long Momentum v5 entries.",
  near_day_high_chase_pct: "Distance from day high treated as a chase zone unless Long Momentum v5 breaks to a fresh high.",
  fresh_day_high_break_bps: "Break above day high, in basis points, that permits a Long Momentum v5 fresh-high entry.",
  max_distance_above_vwap_pct: "Maximum entry distance above VWAP allowed by Long Momentum v5.",
  max_distance_from_day_low_pct: "Maximum entry distance from day low allowed by Long Momentum v5.",
  max_open_above_last_close_pct: "Maximum current-open gap above the previous close allowed by Long Momentum v5.",
  max_last_bar_range_pct: "Maximum previous candle range as a fraction of close allowed by Long Momentum v5.",
  breakeven_activation_r: "Open R multiple where Long Momentum v5 starts protecting breakeven.",
  structural_trail_activation_r: "Open R multiple where Long Momentum v5 starts trailing structural support.",
  vwap_stop_buffer_pct: "Buffer below VWAP used for Long Momentum v5 structural stops.",
  max_spread_below_5: "Maximum observed dollar spread allowed for Long Momentum candidates priced below $5.",
  max_spread_5_to_10: "Maximum observed dollar spread allowed for Long Momentum candidates priced from $5 to $10.",
  sizing_slippage_bps: "Slippage estimate used by Long Momentum sizing so all-cash entries do not exceed cash after fill friction.",
  sizing_fee_per_share: "Per-share fee estimate used by Long Momentum sizing.",
  sizing_min_fee: "Minimum order fee estimate used by Long Momentum sizing.",
  risk_per_trade_pct: "Maximum account equity risked per Long Momentum trade at the initial stop distance.",
  liquidity_window_minutes: "Rolling minute window used for recent liquidity checks.",
  recent_return_lookback_minutes: "Minute lookback used to score recent price momentum.",
  min_recent_return_bps: "Minimum recent return in basis points required before entry.",
  min_momentum_score: "Minimum live momentum score required before entry.",
  require_price_above_vwap: "Require close to be above VWAP before entry.",
  max_vwap_extension_bps: "VWAP distance where overextension penalty begins.",
  rotation_min_hold_minutes: "Minimum hold time before a position can be rotated out.",
  non_progress_score_decay: "Score decay from entry that marks a held position as not progressing.",
  min_progress_r_after_hold: "Minimum open R progress expected after the rotation hold period.",
  initial_risk_pct: "Initial R distance as a fraction of entry price.",
  min_initial_risk_dollars: "Minimum initial R distance in dollars.",
  max_initial_risk_pct: "Maximum initial R distance as a fraction of entry price.",
  max_risk_fraction_of_cash: "Long Momentum v9 maximum cash-slice risk used for position sizing.",
  cash_buffer_dollars: "Cash kept out of Long Momentum sizing so estimated fees and rounding do not overdraw the account.",
  double_bvd_exit_score: "Long Momentum v9 main exit threshold for provider-built double-timeframe bearish volume divergence score.",
  pocket_profit_pct: "Long Momentum v9 pocket threshold. When estimated bid reaches entry price times 1 plus this fraction, v9 sells to pocket profit.",
  pocket_immediate_reentry_enabled: "When true, Long Momentum v9 immediately rebuys after pocketing. When false, it waits for a later candle and normal reentry gates.",
  tema9_exit_buffer_pct: "Long Momentum v9 TEMA exit threshold as a fraction of TEMA9. -0.01 means exit when TEMA20 reaches 99% of TEMA9, before the crossover.",
  vwap_stop_offset_pct: "Long Momentum v9 VWAP reentry protective stop offset, expressed as n percent of VWAP. For example, 3 means stop = VWAP - 3% of VWAP.",
  limit_order_offset_dollars: "Long Momentum v9 liquid-limit execution offset. Buys use current open plus this value; sells use current open minus this value.",
  trailing_activation_r: "Open R multiple required before the trailing stop tightens.",
  trailing_lock_r: "R multiple locked in after trailing activation.",
  trailing_giveback_r: "R multiple allowed to give back from the best price.",
  trend_macd_weight: "Weight applied to cumulative 1-minute MACD pressure in the live scanner score.",
  trend_price_weight: "Weight applied to price change since the opening box in the live scanner score.",
  watchlist_size: "Maximum number of candidate tickers carried from scan into live evaluation.",
  max_active_positions: "Maximum number of simultaneous open positions.",
  replacement_score_buffer: "Score advantage required before a new candidate can replace a weaker active idea.",
  minimum_hold_minutes: "Minimum number of minutes a position must be held before normal exits can apply.",
  opening_box_start_minute: "Minute of day where the opening range measurement starts.",
  opening_box_end_minute: "Minute of day where the opening range measurement ends.",
  entry_cutoff_minute: "Last minute of day where new entries are allowed.",
  exit_minutes_before_close: "Minutes before the session close where positions should be flattened.",
  entry_buffer_pct: "Breakout buffer above the opening range high before an entry is valid.",
  entry_stage_proximity_pct: "Maximum distance from the staged entry level while the candidate remains actionable.",
  stop_box_pullback_fraction: "Opening-range pullback fraction used to place the initial stop.",
  min_risk_pct: "Minimum allowed trade risk as a fraction of entry price.",
  max_risk_pct: "Maximum allowed trade risk as a fraction of entry price.",
  max_capital_per_trade_pct: "Maximum portfolio capital allocated to one trade.",
  cash_reserve_pct: "Cash reserve kept outside new position sizing.",
  min_gap_up_pct: "Minimum gap-up from prior close required for setup eligibility.",
  min_close_location: "Minimum close location within the candle range required for quality scoring.",
  min_body_to_range: "Minimum candle body-to-range ratio required for quality scoring.",
  min_orb_range_atr_fraction: "Minimum opening-range size compared with ATR.",
  max_orb_range_atr_fraction: "Maximum opening-range size compared with ATR.",
  tema_entry_atr_buffer: "TEMA entry buffer expressed relative to ATR.",
  tema_exit_atr_buffer: "TEMA exit buffer expressed relative to ATR.",
  min_opening_volume: "Minimum opening-box share volume required before a ticker can be considered.",
  min_opening_dollar_volume: "Minimum opening-box dollar volume required before a ticker can be considered.",
  opening_volume_score_full: "Opening-box volume level that earns the full volume score.",
  min_box_range_pct: "Minimum opening-box range as a fraction of the opening price.",
  max_box_range_pct: "Maximum opening-box range as a fraction of the opening price.",
  min_box_dollar_range: "Minimum opening-box dollar range required for setup eligibility.",
  max_entry_extension_pct: "Maximum allowed close extension beyond the entry trigger.",
  tema_entry_buffer_pct: "TEMA entry buffer as a fraction of opening-box close.",
  tema_exit_buffer_pct: "TEMA exit buffer as a fraction of opening-box close."
};

const STRATEGY_PARAMETER_GROUPS = [
  {
    title: "Universe & Liquidity",
    description: "Filters that decide which symbols are allowed into the setup scan.",
    keys: [
      "min_price",
      "max_price",
      "min_avg_daily_volume",
      "min_atr",
      "relative_volume_daily_share",
      "min_opening_relative_volume",
      "min_opening_volume",
      "min_opening_dollar_volume",
      "opening_volume_score_full",
      "min_session_dollar_volume",
      "min_recent_dollar_volume",
      "min_recent_volume",
      "min_recent_transactions",
      "min_volume",
      "min_transactions",
      "min_first_entry_transactions",
      "min_first_entry_transactions_vs_prior_3",
      "max_spread_below_5",
      "max_spread_5_to_10",
      "liquidity_window_minutes"
    ]
  },
  {
    title: "Scoring & Capacity",
    description: "Controls candidate ranking, live confirmation, and portfolio breadth.",
    keys: [
      "min_setup_score",
      "min_live_score",
      "min_momentum_score",
      "min_last_5m_return",
      "min_macd_hist_z_since_open",
      "top_n",
      "trend_macd_weight",
      "trend_price_weight",
      "session_return_weight",
      "recent_return_weight",
      "macd_pressure_weight",
      "tema_spread_weight",
      "volume_weight",
      "overextension_penalty_weight",
      "rank_decay",
      "watchlist_size",
      "max_active_positions",
      "replacement_score_buffer",
      "max_immediate_entry_candidates_per_bar",
      "max_reentry_candidates_per_bar",
      "watchlist_snapshot_limit"
    ]
  },
  {
    title: "Session Timing",
    description: "Defines opening range, entry window, hold time, and closing behavior.",
    keys: [
      "opening_box_start_minute",
      "opening_box_end_minute",
      "entry_cutoff_minute",
      "exit_minutes_before_close",
      "minimum_hold_minutes",
      "trigger_1_minute_start",
      "trigger_1_minute_end",
      "trigger_1_late_minute_start",
      "trigger_1_late_minute_end",
      "entry_minute_start",
      "entry_minute_end",
      "entry_late_minute_start",
      "entry_late_minute_end",
      "trading_start_minute",
      "trading_end_minute"
    ]
  },
  {
    title: "Entry & Risk",
    description: "Position sizing, entry buffers, stops, and portfolio capital constraints.",
    keys: [
      "entry_buffer_pct",
      "entry_stage_proximity_pct",
      "max_entry_extension_pct",
      "trigger_1_min_break_bps",
      "setup_valid_bars",
      "min_body_break_bps",
      "stop_box_pullback_fraction",
      "min_risk_pct",
      "max_risk_pct",
      "max_initial_risk_pct",
      "max_risk_fraction_of_cash",
      "max_entry_order_quantity",
      "max_reentry_bvd_score",
      "reentry_vwap_buffer_pct",
      "risk_per_trade_pct",
      "max_capital_per_trade_pct",
      "cash_reserve_pct",
      "cash_buffer_dollars",
      "sizing_slippage_bps",
      "sizing_fee_per_share",
      "sizing_min_fee"
    ]
  },
  {
    title: "Live Rotation",
    description: "Controls live trend rotation, progress expectations, and R-based trailing stops.",
    keys: [
      "rotation_min_hold_minutes",
      "non_progress_score_decay",
      "min_progress_r_after_hold",
      "initial_risk_pct",
      "min_initial_risk_dollars",
      "trailing_activation_r",
      "trailing_lock_r",
      "trailing_giveback_r",
      "breakeven_activation_r",
      "structural_trail_activation_r",
      "vwap_stop_buffer_pct",
      "vwap_stop_offset_pct",
      "pocket_profit_pct",
      "pocket_immediate_reentry_enabled",
      "tema9_exit_buffer_pct",
      "limit_order_offset_dollars",
      "double_bvd_exit_score"
    ]
  },
  {
    title: "Pattern Quality",
    description: "Gap, candle quality, opening-range size, and trend buffer requirements.",
    keys: [
      "min_gap_up_pct",
      "min_close_location",
      "min_tema_spread_pct",
      "min_volume_vs_avg_so_far",
      "min_volume_vs_recent_3",
      "max_entry_below_day_high_pct",
      "near_day_high_chase_pct",
      "fresh_day_high_break_bps",
      "max_distance_above_vwap_pct",
      "max_distance_from_day_low_pct",
      "max_open_above_last_close_pct",
      "max_last_bar_range_pct",
      "min_body_to_range",
      "min_orb_range_atr_fraction",
      "max_orb_range_atr_fraction",
      "min_box_range_pct",
      "max_box_range_pct",
      "min_box_dollar_range",
      "tema_entry_atr_buffer",
      "tema_exit_atr_buffer",
      "tema_entry_buffer_pct",
      "tema_exit_buffer_pct"
    ]
  }
] satisfies Array<{ title: string; description: string; keys: string[] }>;

const IMPORTANT_STRATEGY_PARAMETER_KEYS = [
  "max_active_positions",
  "top_n",
  "max_capital_per_trade_pct",
  "max_gross_exposure_pct",
  "cash_reserve_pct",
  "min_setup_score",
  "min_live_score",
  "min_momentum_score",
  "min_session_dollar_volume",
  "min_recent_dollar_volume",
  "trend_macd_weight",
  "trend_price_weight",
  "entry_cutoff_minute",
  "min_risk_pct",
  "max_risk_pct"
];

export function StrategyPage() {
  const [strategies, setStrategies] = useState<Strategy[]>([]);
  const [draftSelection, setDraftSelection] = useState<StrategySelection | null>(null);
  const [activeSelection, setActiveSelection] = useState<StrategySelection | null>(null);
  const { activeTab, isTabMounted, setActiveTab } = useCachedTabState(tabs[0]);
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [readme, setReadme] = useState("");
  const [config, setConfig] = useState<StrategyConfig | null>(null);
  const [selectedRun, setSelectedRun] = useState<string | null>(null);
  const activeStrategy = activeSelection ? strategies.find((item) => item.name === activeSelection.strategyName) ?? null : null;
  const draftStrategy = draftSelection ? strategies.find((item) => item.name === draftSelection.strategyName) ?? null : null;

  useEffect(() => {
    api<{ strategies: Strategy[] }>("/api/strategies").then((payload) => {
      const nextStrategies = payload.strategies;
      setStrategies(nextStrategies);
      const defaultStrategy = nextStrategies.find((item) => item.name === defaultStrategyName) ?? nextStrategies[0];
      if (!defaultStrategy) return;
      const version = defaultStrategy.default_version ?? defaultStrategy.versions?.[0] ?? "v1";
      setDraftSelection((current) => current ?? { strategyName: defaultStrategy.name, version });
    });
  }, []);

  useEffect(() => {
    if (!activeSelection) return;
    api<StrategyConfig>(`/api/strategies/${activeSelection.strategyName}/default-config${query({ version: activeSelection.version })}`).then((payload) => {
      setConfig({ ...payload, run_name: submittedRunName(payload) });
    });
    api<{ content: string }>(`/api/strategies/${activeSelection.strategyName}/readme${query({ version: activeSelection.version })}`).then((payload) => setReadme(payload.content));
  }, [activeSelection?.strategyName, activeSelection?.version]);

  useEffect(() => {
    if (!config || !activeSelection) return;
    loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version);
  }, [config?.output_root, activeSelection?.strategyName, activeSelection?.version]);

  async function loadRuns(outputRoot: string, strategyName: string, version: string) {
    const payload = await api<{ runs: RunRow[] }>(`/api/backtests/runs${query({ output_root: outputRoot, strategy_name: strategyName, strategy_version: version })}`);
    setRuns(sortRunsNewestFirst(payload.runs));
  }

  function updateDraftStrategy(strategyName: string) {
    const strategy = strategies.find((item) => item.name === strategyName);
    const version = strategy?.default_version ?? strategy?.versions?.[0] ?? "v1";
    setDraftSelection({ strategyName, version });
  }

  function updateDraftVersion(version: string) {
    if (!draftSelection) return;
    setDraftSelection({ ...draftSelection, version });
  }

  function openBacktestWorkspace() {
    if (!draftSelection) return;
    setConfig(null);
    setRuns([]);
    setReadme("");
    setSelectedRun(null);
    setActiveTab(tabs[0]);
    setActiveSelection(draftSelection);
  }

  function openSavedRun(runId: string) {
    setSelectedRun(runId);
    setActiveTab(tabs[0]);
  }

  function changeSelection() {
    if (activeSelection) setDraftSelection(activeSelection);
    setActiveSelection(null);
    setActiveTab(tabs[0]);
    setConfig(null);
    setRuns([]);
    setReadme("");
    setSelectedRun(null);
  }

  if (!activeSelection) {
    return (
      <>
        <PageIntro
          groupLabel="Backtest"
          title="Backtest"
          description="Select the strategy and version before opening the run workspace."
        />
        <BacktestSelectionPanel
          draftSelection={draftSelection}
          onOpen={openBacktestWorkspace}
          onStrategyChange={updateDraftStrategy}
          onVersionChange={updateDraftVersion}
          strategies={strategies}
          strategy={draftStrategy}
        />
      </>
    );
  }

  return (
    <>
      <PageIntro
        groupLabel="Backtest"
        title={`${activeStrategy?.display_name ?? activeSelection.strategyName.replaceAll("_", " ")} ${activeSelection.version}`}
        description={activeStrategy?.description ?? "Opening range momentum research strategy."}
      />
      <Tabs tabs={tabs} active={activeTab} onChange={setActiveTab} />
      <CachedTabPanel active={activeTab === "Backtest"} mounted={isTabMounted("Backtest") && Boolean(config)}>
        {config ? (
        selectedRun ? (
          <SavedRunPanel
            config={config}
            onBack={() => {
              setSelectedRun(null);
              setActiveTab("Runs");
            }}
            onNewBacktest={() => setSelectedRun(null)}
            outputRoot={config.output_root}
            runId={selectedRun}
          />
        ) : (
          <NewRunPanel
            config={config}
            key={config.strategy_version}
            onConfigChange={setConfig}
            onComplete={() => loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version)}
            onChangeStrategy={changeSelection}
            versions={activeStrategy?.versions ?? [activeSelection.version]}
          />
        )
      ) : null}
      </CachedTabPanel>
      <CachedTabPanel active={activeTab === "Runs"} mounted={isTabMounted("Runs") && Boolean(config)}>
        {config ? (
        <RunsPanel
          runs={runs}
          outputRoot={config.output_root}
          onOpen={openSavedRun}
          onDeleted={() => loadRuns(config.output_root, activeSelection.strategyName, activeSelection.version)}
        />
        ) : null}
      </CachedTabPanel>
      <CachedTabPanel active={activeTab === "Strategy README"} mounted={isTabMounted("Strategy README")}>
        <div className="markdown-panel">
          <ReactMarkdown>{readme}</ReactMarkdown>
        </div>
      </CachedTabPanel>
    </>
  );
}

function BacktestSelectionPanel({
  draftSelection,
  onOpen,
  onStrategyChange,
  onVersionChange,
  strategies,
  strategy
}: {
  draftSelection: StrategySelection | null;
  onOpen: () => void;
  onStrategyChange: (strategyName: string) => void;
  onVersionChange: (version: string) => void;
  strategies: Strategy[];
  strategy: Strategy | null;
}) {
  const versions = strategy?.versions?.length ? strategy.versions : draftSelection?.version ? [draftSelection.version] : [];
  const selectedVersionDescription = draftSelection?.version ? strategy?.version_descriptions?.[draftSelection.version] : "";

  return (
    <section className="panel backtest-selection-panel">
      <div className="backtest-selection-grid">
        <div className="field config-field">
          <label>Strategy</label>
          <select
            disabled={!strategies.length}
            onChange={(event) => onStrategyChange(event.target.value)}
            value={draftSelection?.strategyName ?? ""}
          >
            {strategies.map((item) => (
              <option key={item.name} value={item.name}>
                {item.display_name}
              </option>
            ))}
          </select>
        </div>
        <div className="field config-field">
          <label>Version</label>
          <select
            disabled={!versions.length}
            onChange={(event) => onVersionChange(event.target.value)}
            value={draftSelection?.version ?? ""}
          >
            {versions.map((version) => (
              <option key={version} value={version}>
                {version}
              </option>
            ))}
          </select>
        </div>
      </div>
      <div className="backtest-selection-footer">
        <div>
          <h2>{strategy?.display_name ?? "Strategy"}</h2>
          <p>{strategy?.description ?? "Strategy catalog is loading."}</p>
          {selectedVersionDescription ? <p>{selectedVersionDescription}</p> : null}
        </div>
        <button className="button primary" disabled={!draftSelection} onClick={onOpen} type="button">
          <Play size={15} /> Open Backtest
        </button>
      </div>
    </section>
  );
}

function RunsPanel({
  runs,
  outputRoot,
  onOpen,
  onDeleted
}: {
  runs: RunRow[];
  outputRoot: string;
  onOpen: (runId: string) => void;
  onDeleted: () => void;
}) {
  async function deleteRun(runId: string) {
    await api(`/api/backtests/runs/${runId}${query({ output_root: outputRoot })}`, { method: "DELETE" });
    onDeleted();
  }
  if (!runs.length) return <div className="empty-state panel">No app-created runs exist for this strategy yet.</div>;
  return (
    <div className="progress-column-body">
      {runs.map((run) => (
        <article
          className="run-card clickable"
          key={run.run_id}
          onClick={() => onOpen(run.run_id)}
          onKeyDown={(event) => {
            if (event.key !== "Enter" && event.key !== " ") return;
            event.preventDefault();
            onOpen(run.run_id);
          }}
          role="button"
          tabIndex={0}
          title="Open run"
        >
          <div>
            <div className="run-card-title">{run.run_name}</div>
            <div className="muted">{run.date_range} | {run.created_at}</div>
            <div className="toolbar" style={{ marginBottom: 0 }}>
              <SemanticBadge tone={toneForStatus(run.status)}>{run.status}</SemanticBadge>
              <span className="meta-tag">{run.strategy_version || "v1"}</span>
              <span className="meta-tag">{formatPct(run.return_pct)}</span>
              <span className="meta-tag">{formatMoney(run.total_pnl)}</span>
              <span className="meta-tag">{run.trade_count} trades</span>
            </div>
          </div>
          <div className="toolbar" onKeyDown={(event) => event.stopPropagation()} style={{ margin: 0 }}>
            <button className="button primary" onClick={(event) => { event.stopPropagation(); onOpen(run.run_id); }} type="button">Open</button>
            <button className="icon-button" onClick={(event) => { event.stopPropagation(); deleteRun(run.run_id); }} type="button" title="Delete run"><Trash2 size={15} /></button>
          </div>
        </article>
      ))}
    </div>
  );
}

function sortRunsNewestFirst(runs: RunRow[]) {
  return [...runs].sort((left, right) => {
    const rightTime = runCreatedAtMs(right);
    const leftTime = runCreatedAtMs(left);
    if (rightTime !== leftTime) return rightTime - leftTime;
    return right.run_id.localeCompare(left.run_id);
  });
}

function runCreatedAtMs(run: RunRow) {
  const parsed = Date.parse(run.created_at);
  return Number.isFinite(parsed) ? parsed : 0;
}

function NewRunPanel({
  config,
  onChangeStrategy,
  onConfigChange,
  onComplete,
  versions
}: {
  config: StrategyConfig;
  onChangeStrategy: () => void;
  onConfigChange: (config: StrategyConfig) => void;
  onComplete: () => void;
  versions: string[];
}) {
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Record<string, unknown> | null>(null);
  const [debugSession, setDebugSession] = useState<InteractiveDebugSession | null>(null);
  const [debugBusy, setDebugBusy] = useState<"next" | "previous" | "until-action" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<EditTarget | null>(null);
  const [draftConfig, setDraftConfig] = useState(config);
  useEffect(() => {
    if (!jobId || !["running", "queued", "canceling"].includes(String(job?.status ?? "running"))) return;
    const timer = window.setInterval(() => {
      api<Record<string, unknown>>(`/api/backtests/jobs/${jobId}${query({ output_root: config.output_root })}`)
        .then((payload) => {
          setJob(payload);
          if (["complete", "cancelled"].includes(String(payload.status))) onComplete();
        })
        .catch((err) => {
          setError(err instanceof Error ? err.message : String(err));
        });
    }, 1000);
    return () => window.clearInterval(timer);
  }, [jobId, job?.status, config.output_root]);

  async function startRun() {
    setError(null);
    setDebugSession(null);
    const runConfig = { ...config, run_name: submittedRunName(config) };
    onConfigChange(runConfig);
    setDraftConfig(runConfig);
    try {
      const payload = await api<Record<string, unknown>>("/api/backtests/jobs", { method: "POST", body: JSON.stringify(runConfig) });
      setJob(payload);
      setJobId(String(payload.job_id));
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function startDebugRun() {
    setError(null);
    const runConfig = {
      ...config,
      observability_always_trace_trades: true,
      observability_mode: "standard",
      observability_scanner_max_rows: Math.max(DEBUG_BACKTEST_SCANNER_MAX_ROWS, config.observability_scanner_max_rows ?? 0),
      observability_scanner_min_rows: Math.max(100, config.observability_scanner_min_rows ?? 0),
      observability_scanner_top_percent: 1,
      observability_sessions: Math.max(999, config.observability_sessions ?? 0),
      run_name: `${submittedRunName(config)}_step_debug`,
      save_symbol_bars: true
    };
    onConfigChange(runConfig);
    setDraftConfig(runConfig);
    try {
      const payload = await api<InteractiveDebugSession>("/api/backtests/debug/sessions", { method: "POST", body: JSON.stringify(runConfig) });
      setJob(null);
      setJobId(null);
      setDebugSession(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  async function moveDebug(direction: "next" | "previous") {
    if (!debugSession?.session_id) return;
    setError(null);
    setDebugBusy(direction);
    try {
      const payload = await api<InteractiveDebugSession>(
        `/api/backtests/debug/sessions/${debugSession.session_id}/${direction}`,
        { method: "POST" }
      );
      setDebugSession(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDebugBusy(null);
    }
  }

  async function runDebugUntilAction() {
    if (!debugSession?.session_id) return;
    setError(null);
    setDebugBusy("until-action");
    try {
      let payload = debugSession;
      for (let index = 0; index < Math.max(1, debugSession.total_event_bars); index += 1) {
        payload = await api<InteractiveDebugSession>(
          `/api/backtests/debug/sessions/${debugSession.session_id}/next`,
          { method: "POST" }
        );
        setDebugSession(payload);
        await waitForDebugProgressPaint();
        if (debugSessionIsComplete(payload) || debugStepHasPrimaryAction(payload.step ?? null)) break;
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setDebugBusy(null);
    }
  }

  async function stopRun() {
    if (!jobId) return;
    setError(null);
    try {
      const payload = await api<Record<string, unknown>>(
        `/api/backtests/jobs/${jobId}/cancel${query({ output_root: config.output_root })}`,
        { method: "POST" }
      );
      setJob(payload);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  function openEditor(target: EditTarget) {
    setDraftConfig(config);
    setEditing(target);
  }

  function applyDraft() {
    onConfigChange(draftConfig);
    setEditing(null);
  }

  const jobStatus = String(job?.status ?? "").toLowerCase();
  const running = ["running", "queued", "canceling"].includes(jobStatus);
  const canStop = ["running", "queued"].includes(jobStatus);

  return (
    <section className={debugSession ? "new-run-page step-debug-page-scroll" : "new-run-page"}>
      <div className="new-run-action-row">
        <button className="button primary" onClick={startRun} type="button" disabled={running}>
          <Play size={15} /> Start Backtest
        </button>
        <button className="button secondary" onClick={startDebugRun} type="button" disabled={running}>
          <ListChecks size={15} /> Start Step Debug
        </button>
        <button className="button danger" onClick={stopRun} type="button" disabled={!canStop}>
          <StopCircle size={15} /> Stop Backtest
        </button>
        <button className="button secondary" onClick={() => openEditor("run")} type="button">
          <Database size={15} /> Update Backtest Parameters
        </button>
        <button className="button secondary" onClick={() => openEditor("strategy")} type="button">
          <SlidersHorizontal size={15} /> Update Strategy Parameters
        </button>
        <button className="button secondary" onClick={onChangeStrategy} type="button">
          <SlidersHorizontal size={15} /> Change Strategy
        </button>
      </div>

      {error ? <div className="error-panel" style={{ marginTop: 12 }}>{error}</div> : null}
      {debugSession ? (
        <InteractiveStepDebugPanel
          busy={debugBusy}
          config={config}
          onNext={() => moveDebug("next")}
          onPrevious={() => moveDebug("previous")}
          onRunUntilAction={runDebugUntilAction}
          session={debugSession}
        />
      ) : (
        <BacktestJobPanel config={config} job={job} outputRoot={config.output_root} />
      )}
      {editing === "run" ? (
        <Modal className="parameter-modal-panel" title="Update Backtest Parameters" onClose={() => setEditing(null)}>
          <BacktestParameterEditor config={draftConfig} onChange={setDraftConfig} versions={versions} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Update Parameters</button>
          </div>
        </Modal>
      ) : null}
      {editing === "strategy" ? (
        <Modal className="parameter-modal-panel" title="Update Strategy Parameters" onClose={() => setEditing(null)}>
          <StrategyParameterEditor config={draftConfig} onChange={setDraftConfig} />
          <div className="modal-actions">
            <button className="button" onClick={() => setEditing(null)} type="button">Cancel</button>
            <button className="button primary" onClick={applyDraft} type="button">Update Parameters</button>
          </div>
        </Modal>
      ) : null}
    </section>
  );
}

function SavedRunPanel({
  config,
  onBack,
  onNewBacktest,
  outputRoot,
  runId
}: {
  config: StrategyConfig;
  onBack: () => void;
  onNewBacktest: () => void;
  outputRoot: string;
  runId: string;
}) {
  return (
    <section className="new-run-page">
      <div className="new-run-action-row">
        <button className="button secondary" onClick={onBack} type="button">
          Back to Runs
        </button>
        <button className="button primary" onClick={onNewBacktest} type="button">
          <Play size={15} /> New Backtest
        </button>
      </div>
      <BacktestJobPanel config={config} job={null} outputRoot={outputRoot} runId={runId} />
    </section>
  );
}

function InteractiveStepDebugPanel({
  busy,
  config,
  onNext,
  onPrevious,
  onRunUntilAction,
  session
}: {
  busy: "next" | "previous" | "until-action" | null;
  config: StrategyConfig;
  onNext: () => void;
  onPrevious: () => void;
  onRunUntilAction: () => void;
  session: InteractiveDebugSession;
}) {
  const step = session.step ?? null;
  const summary = step?.summary ?? session.summary ?? {};
  const complete = session.status === "complete" || step?.type === "complete";
  const canGoPrevious = session.current_step_index > 0;
  const progressDone = Math.max(0, Math.min(session.processed_event_bars, session.total_event_bars));
  const progressTotal = Math.max(session.total_event_bars, 1);
  const progressPct = progressTotal > 0 ? (progressDone / progressTotal) * 100 : 0;
  const parameters = useMemo(() => interactiveDebugParameterRows(session.config, config.strategy_params), [config.strategy_params, session.config]);
  const rawScannerFilterPresets = useMemo(() => interactiveDebugRawFilterPresets(config), [config]);
  const defaultRawFilterPreset = rawScannerFilterPresets?.[0];
  const rawRows = step?.raw_scanner_rows ?? [];
  const strategyWatchlistRows = step?.strategy_watchlist_rows ?? [];
  const actionRows = step?.observability_trace ?? [];
  const counts = debugStepCounts(step);
  const metrics = useMemo(() => buildInteractiveDebugMetrics(config, summary, rawRows.length, strategyWatchlistRows.length, counts), [config, counts, rawRows.length, strategyWatchlistRows.length, summary]);
  const [selectedDebugChart, setSelectedDebugChart] = useState<ObservationChartTarget | null>(null);
  return (
    <section className="panel backtest-results-panel interactive-debug-panel" style={{ marginTop: 16 }}>
      {selectedDebugChart ? (
        <DebugScannerRowChartModal config={config} onClose={() => setSelectedDebugChart(null)} target={selectedDebugChart} />
      ) : null}
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <div>
          <h2 className="backtest-results-title">Interactive Step Debug</h2>
          <p className="muted">
            Session {session.session_id.slice(0, 8)} | {session.current_session || "not started"} | {formatNumber(session.processed_event_bars)} / {formatNumber(session.total_event_bars)} bars
          </p>
        </div>
        <SemanticBadge tone={complete ? "success" : "info"}>{complete ? "complete" : "waiting for next"}</SemanticBadge>
      </div>
      <div className="step-debug-control-panel">
        <ProgressMeter
          ariaLabel="Interactive debug progress"
          done={progressDone}
          elapsed_sec={0}
          label="Debug progress"
          progress={progressPct}
          status={complete ? "complete" : "running"}
          total={progressTotal}
        />
        <div className="step-debug-control-row">
          <button className="button secondary" disabled={!canGoPrevious || busy !== null} onClick={onPrevious} type="button">
            {busy === "previous" ? "Running..." : "Previous"}
          </button>
          <button className="button primary" disabled={complete || busy !== null} onClick={onNext} type="button">
            {busy === "next" ? "Running..." : "Next Bar"}
          </button>
          <button className="button secondary" disabled={complete || busy !== null} onClick={onRunUntilAction} type="button">
            {busy === "until-action" ? "Running..." : "Run Until Action"}
          </button>
          <SemanticBadge tone="neutral">
            Step {formatNumber(Math.max(0, session.current_step_index + 1))} of {formatNumber(session.total_history_steps)}
          </SemanticBadge>
          <SemanticBadge tone="info">{config.strategy_name} {config.strategy_version}</SemanticBadge>
          {step?.timestamp ? <SemanticBadge tone="neutral">{formatObservationTimestamp(step.timestamp)}</SemanticBadge> : null}
        </div>
        <NewRunMetricStrip metrics={metrics} />
        <div className="step-debug-current-card">
          <div>
            <span>Backtest Response</span>
            <DebugStepResponseSummary counts={counts} step={step} />
            <p>
              Pressing Next calls the strategy for exactly one timestamp, then the backtest engine responds with submitted requests, accepted orders, fills, portfolio state, and traces.
            </p>
          </div>
          <DebugStepCountBadges counts={counts} />
        </div>
      </div>

      <div className="step-debug-detail">
        <ObservationEvidenceTable
          description="The exact rows passed to strategy.on_bar as context.updates. Toolbar presets here use only raw/provider columns and do not depend on strategy watchlist or order state."
          defaultFilterPreset={defaultRawFilterPreset}
          filterPresets={rawScannerFilterPresets}
          onOpenChart={setSelectedDebugChart}
          rows={rawRows}
          title="Scanner Raw Input"
        />
        <ObservationEvidenceTable
          description="Grouped boolean filters detected from the strategy scanner rows or raw inputs."
          rows={step?.filter_groups ?? []}
          title="Filter Groups"
        />
        <ObservationEvidenceTable
          description="Strategy-maintained day momentum watchlist state for this bar, including max VWAP, first-entry status, and reentry inputs."
          onOpenChart={setSelectedDebugChart}
          rows={strategyWatchlistRows}
          showWhenEmpty
          title="Momentum Watchlist"
        />
        <ObservationEvidenceTable
          description="Trace rows emitted by the strategy while evaluating this bar."
          presentation="cards"
          rows={actionRows}
          title="Strategy Actions"
        />
        <ObservationEvidenceTable
          description="OrderRequest objects returned by strategy.on_bar before the engine handles them."
          rows={step?.strategy_requests ?? []}
          title="Strategy Submitted To Backtest"
        />
        <ObservationEvidenceTable
          description="Orders created, filled, rejected, cancelled, or left pending by the engine on this bar."
          rows={step?.orders ?? []}
          title="Backtest Orders Response"
        />
        <ObservationEvidenceTable
          description="Fills created by the fill model on this bar."
          rows={step?.fills ?? []}
          title="Backtest Fills Response"
        />
        <ObservationEvidenceTable
          description="Trades closed at this bar."
          rows={step?.trades ?? []}
          title="Trades"
        />
        <ObservationEvidenceTable
          description="Portfolio row after this bar is processed."
          rows={step?.portfolio ?? []}
          title="Portfolio"
        />
        <ObservationEvidenceTable
          description="Open positions after this bar is processed."
          rows={step?.positions ?? []}
          title="Positions"
        />
        <ObservationEvidenceTable
          description="Orders still pending after this bar."
          rows={step?.pending_orders ?? []}
          title="Pending Orders"
        />
        <ObservationEvidenceTable
          description="Orders and fills that will be passed to the strategy on the next bar as recent_orders and recent_fills."
          rows={[...(step?.recent_orders_for_next_bar ?? []), ...(step?.recent_fills_for_next_bar ?? [])]}
          title="Recent Execution For Next Strategy Step"
        />
        <ObservationEvidenceTable
          description="Strategy and engine rejection rows for this bar."
          rows={step?.rejection_events ?? []}
          title="Rejections"
        />
        <ObservationEvidenceTable
          description="State rows emitted by the strategy or engine for this bar."
          rows={step?.observability_state ?? []}
          title="State"
        />
        <ObservationEvidenceTable
          description="The backtest and strategy parameters used when this debug session was created. Change parameters, then press Start Step Debug again to create a new path."
          rows={parameters}
          title="Parameters"
        />
      </div>
    </section>
  );
}

function DebugScannerRowChartModal({
  config,
  onClose,
  target,
}: {
  config: StrategyConfig;
  onClose: () => void;
  target: ObservationChartTarget;
}) {
  const symbol = target.symbol;
  const sessionDate = rowText(target.row ?? {}, "session_date") || observationTargetMarketDate(target);
  const processedRoot = config.processed_data_root;
  const [timeframe, setTimeframe] = useState("1m");
  const [payload, setPayload] = useState<ChartPayload | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  const [customVisibleColumns, setCustomVisibleColumns] = useState(false);
  const [period, setPeriod] = useState({ end: sessionDate, start: sessionDate });
  const reference = useMemo<ChartReference | null>(() => selectedSymbolReference(target), [target]);
  const displayItemsRequest = customVisibleColumns ? (visibleColumns.length ? visibleColumns.join(",") : CHART_DISPLAY_ITEMS_NONE) : undefined;
  const displayItemsRequestKey = displayItemsRequest ?? "";
  const periodBounds = useMemo(() => chartPayloadPeriodBounds(payload, sessionDate), [payload, sessionDate]);

  useEffect(() => {
    setPeriod({ end: sessionDate, start: sessionDate });
    setCustomVisibleColumns(false);
    setVisibleColumns([]);
  }, [sessionDate, symbol]);

  useEffect(() => {
    if (!processedRoot || !sessionDate || !symbol || !timeframe) return;
    let active = true;
    setLoading(true);
    setError("");
    api<ChartPayload>(
      `/api/market-data/chart${query({
        display_items: displayItemsRequest,
        marker_limit: 100,
        processed_root: processedRoot,
        session_date: sessionDate,
        ticker: symbol,
        timeframe,
      })}`
    )
      .then((nextPayload) => {
        if (!active) return;
        setPayload(nextPayload);
        if (!customVisibleColumns) setVisibleColumns(debugChartVisibleColumns(nextPayload));
      })
      .catch((err) => {
        if (!active) return;
        setPayload(null);
        setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [customVisibleColumns, displayItemsRequestKey, processedRoot, sessionDate, symbol, timeframe]);

  function updatePeriod(start: string, end: string) {
    setPeriod(start <= end ? { start, end } : { start: end, end: start });
  }

  const filteredPayload = useMemo(() => {
    if (!payload) return null;
    const candles = payload.candles.filter((candle) => candleInChartPeriod(candle, period.start, period.end));
    const visibleTimes = new Set(candles.map((candle) => Number(candle.time)));
    return {
      ...payload,
      candles,
      markers: payload.markers.filter((marker) => visibleTimes.has(Number(marker.time))),
      overlay_series: payload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => visibleTimes.has(Number(point.time))) })),
      oscillator_series: payload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => visibleTimes.has(Number(point.time))) })),
      price_zones: (payload.price_zones ?? []).filter((zone) => candles.some((candle) => candle.time >= zone.start && candle.time <= zone.end)),
      volume: payload.volume.filter((point) => visibleTimes.has(Number(point.time))),
    };
  }, [payload, period.end, period.start]);

  return (
    <Modal className="chart-context-modal-panel" onClose={onClose} title={`${symbol || "Scanner Row"} Chart`}>
      <ChartPanel
        displayItemOptions={payload?.options?.display_items ?? []}
        emptyMessage={symbol ? `No provider chart data found for ${symbol} on ${sessionDate}.` : "This row does not include a ticker."}
        errorMessage={error}
        featureOptions={payload?.options?.feature_columns ?? []}
        indicatorOptions={payload?.options?.standard_indicators ?? []}
        labelOptions={payload?.options?.supervision_groups ?? []}
        loading={loading}
        normalizeTicker={false}
        onPeriodChange={updatePeriod}
        onTickerChange={() => undefined}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={(nextColumns) => {
          setCustomVisibleColumns(true);
          setVisibleColumns(nextColumns);
        }}
        onVisibleSupervisionGroupsChange={() => undefined}
        payload={filteredPayload}
        periodEnd={period.end || periodBounds.end}
        periodMax={periodBounds.max}
        periodMin={periodBounds.min}
        periodStart={period.start || periodBounds.start}
        reference={reference}
        showIndicatorControls
        showReferenceLine
        showSupervisionControls={false}
        ticker={symbol || "Scanner"}
        tickerInputWidth={112}
        tickerMaxLength={16}
        timeframe={timeframe}
        timeframes={["1m", "5m"]}
        visibleColumns={visibleColumns}
        visibleSupervisionGroups={[]}
      />
    </Modal>
  );
}

function NewRunMetricStrip({ metrics }: { metrics: NewRunMetric[] }) {
  return (
    <div
      aria-label="Backtest metrics"
      className="new-run-metric-strip"
      role="list"
      style={{ gridTemplateColumns: `repeat(${Math.max(metrics.length, 1)}, minmax(106px, 1fr))` }}
    >
      {metrics.map((metric) => (
        <article
          aria-label={`${metric.label}: ${typeof metric.value === "string" ? metric.value : metric.detail}`}
          className="new-run-metric-card"
          data-tone={metric.tone ?? "neutral"}
          key={metric.label}
          role="listitem"
          title={metric.detail}
        >
          <div className="new-run-metric-icon">{metric.icon}</div>
          <span className="new-run-metric-label">{metric.label}</span>
          <strong className="new-run-metric-value">{metric.value}</strong>
        </article>
      ))}
    </div>
  );
}

function BacktestParameterEditor({
  config,
  onChange,
  versions
}: {
  config: StrategyConfig;
  onChange: (config: StrategyConfig) => void;
  versions: string[];
}) {
  return (
    <ParameterEditorShell
      description="Define the run identity, date window, capital model, and artifact locations before submitting the backtest."
      icon={<Database size={18} />}
      meta={[
        { label: "Strategy", value: config.strategy_name.replaceAll("_", " ") },
        { label: "Version", value: versions.includes(config.strategy_version) ? config.strategy_version : config.strategy_version || "v1" },
        { label: "Range", value: `${config.start_date} to ${config.end_date}` }
      ]}
      title="Backtest Run Settings"
    >
      <EditSection
        description="Most runs are changed here: name, date range, starting capital, and synthetic fill cost."
        emphasis="primary"
        title="Important Settings"
      >
        <EditField help={RUN_PARAMETER_HELP.run_name} label="Run name" value={config.run_name} onChange={(value) => onChange({ ...config, run_name: value })} />
        <EditField help={RUN_PARAMETER_HELP.start_date} label="Start" type="date" value={config.start_date} onChange={(value) => onChange({ ...config, start_date: value })} />
        <EditField help={RUN_PARAMETER_HELP.end_date} label="End" type="date" value={config.end_date} onChange={(value) => onChange({ ...config, end_date: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.initial_cash} label="Initial cash" value={config.initial_cash} onChange={(value) => onChange({ ...config, initial_cash: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.slippage_bps} label="Slippage bps" value={config.slippage_bps} onChange={(value) => onChange({ ...config, slippage_bps: value })} />
        <EditBooleanField help={RUN_PARAMETER_HELP.enable_partial_fills} label="Partial fills" value={config.enable_partial_fills ?? true} onChange={(value) => onChange({ ...config, enable_partial_fills: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.max_allowable_entry_fill_size} label="Max entry fill size" value={config.max_allowable_entry_fill_size ?? 3000} onChange={(value) => onChange({ ...config, max_allowable_entry_fill_size: Math.max(0, Math.round(value)) })} />
        <EditNumberField help={RUN_PARAMETER_HELP.max_entry_participation_rate} label="Liquidity volume share" value={config.max_entry_participation_rate ?? 0.05} onChange={(value) => onChange({ ...config, max_entry_participation_rate: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.max_entry_trade_multiple} label="Liquidity trade multiple" value={config.max_entry_trade_multiple ?? 3} onChange={(value) => onChange({ ...config, max_entry_trade_multiple: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.exit_liquidity_slippage_bps_per_excess_multiple} label="Exit liquidity bps" value={config.exit_liquidity_slippage_bps_per_excess_multiple ?? 10} onChange={(value) => onChange({ ...config, exit_liquidity_slippage_bps_per_excess_multiple: value })} />
        <EditReadonlyField help={RUN_PARAMETER_HELP.fee_model} label="Fee model" value={config.fee_model || "ibkr_ca_us_stock_fixed"} />
        <EditNumberField help={RUN_PARAMETER_HELP.fee_tax_rate} label="Fee tax rate" value={config.fee_tax_rate ?? 0} onChange={(value) => onChange({ ...config, fee_tax_rate: value })} />
      </EditSection>
      <EditSection description="Provider-built data is preferred for backtests; output root controls saved run artifacts." title="Data & Artifacts">
        <EditField help={RUN_PARAMETER_HELP.processed_data_root} label="Processed data root" value={config.processed_data_root} onChange={(value) => onChange({ ...config, processed_data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.data_root} label="Raw data root" value={config.data_root} onChange={(value) => onChange({ ...config, data_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.output_root} label="Output root" value={config.output_root} onChange={(value) => onChange({ ...config, output_root: value })} />
        <EditField help={RUN_PARAMETER_HELP.excluded_symbols_file} label="Excluded symbols CSV" value={config.excluded_symbols_file ?? ""} onChange={(value) => onChange({ ...config, excluded_symbols_file: value })} />
        <EditBooleanField help={RUN_PARAMETER_HELP.save_symbol_bars} label="Save symbol bars" value={config.save_symbol_bars} onChange={(value) => onChange({ ...config, save_symbol_bars: value })} />
      </EditSection>
      <EditSection description="Profile the first sessions deeply without turning every long backtest into a huge artifact set." title="Observability">
        <EditField help={RUN_PARAMETER_HELP.observability_mode} label="Mode" value={config.observability_mode || "standard"} onChange={(value) => onChange({ ...config, observability_mode: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.observability_sessions} label="Profile sessions" value={config.observability_sessions ?? 7} onChange={(value) => onChange({ ...config, observability_sessions: Math.max(0, Math.round(value)) })} />
        <EditNumberField help={RUN_PARAMETER_HELP.observability_scanner_top_percent} label="Scanner top fraction" value={config.observability_scanner_top_percent ?? 0.25} onChange={(value) => onChange({ ...config, observability_scanner_top_percent: value })} />
        <EditNumberField help={RUN_PARAMETER_HELP.observability_scanner_min_rows} label="Scanner min rows" value={config.observability_scanner_min_rows ?? 10} onChange={(value) => onChange({ ...config, observability_scanner_min_rows: Math.max(1, Math.round(value)) })} />
        <EditNumberField help={RUN_PARAMETER_HELP.observability_scanner_max_rows} label="Scanner max rows" value={config.observability_scanner_max_rows ?? 100} onChange={(value) => onChange({ ...config, observability_scanner_max_rows: Math.max(1, Math.round(value)) })} />
        <EditBooleanField help={RUN_PARAMETER_HELP.observability_always_trace_trades} label="Always trace trades" value={config.observability_always_trace_trades ?? true} onChange={(value) => onChange({ ...config, observability_always_trace_trades: value })} />
      </EditSection>
      <EditSection description="Strategy context is locked by the workspace selection so saved runs stay traceable." title="Strategy Context">
        <EditReadonlyField
          help={RUN_PARAMETER_HELP.strategy_version}
          label="Strategy version"
          value={versions.includes(config.strategy_version) ? config.strategy_version : config.strategy_version || "v1"}
        />
      </EditSection>
    </ParameterEditorShell>
  );
}

function EditReadonlyField({ help, label, value }: { help: string; label: string; value: string }) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input readOnly value={value} />
    </div>
  );
}

function StrategyParameterEditor({ config, onChange }: { config: StrategyConfig; onChange: (config: StrategyConfig) => void }) {
  const params = config.strategy_params;
  const knownKeys = new Set(STRATEGY_PARAMETER_GROUPS.flatMap((group) => group.keys));
  const importantKeys = IMPORTANT_STRATEGY_PARAMETER_KEYS.filter((key) => key in params);
  const importantKeySet = new Set(importantKeys);
  const remaining = Object.keys(params).filter((key) => !knownKeys.has(key));
  const strategySummary = [config.strategy_description, config.strategy_version_description].filter(Boolean).join(" ");

  function updateParam(key: string, value: StrategyParamValue) {
    onChange({ ...config, strategy_params: { ...params, [key]: value } });
  }

  return (
    <ParameterEditorShell
      description={strategySummary || "Tune the active strategy version. Capacity, scoring, timing, and risk controls are kept at the top because they change results the most."}
      icon={<SlidersHorizontal size={18} />}
      meta={[
        { label: "Strategy", value: config.strategy_name.replaceAll("_", " ") },
        { label: "Version", value: config.strategy_version || "v1" },
        { label: "Parameters", value: formatNumber(Object.keys(params).length) }
      ]}
      title="Strategy Controls"
    >
      {importantKeys.length ? (
        <EditSection
          description="High-impact controls for breadth, capital allocation, score thresholds, entry timing, and risk bounds."
          emphasis="primary"
          title="Important Settings"
        >
          {importantKeys.map((key) => (
            <EditStrategyParamField
              help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
              key={key}
              name={key}
              onChange={(value) => updateParam(key, value)}
              value={params[key]}
            />
          ))}
        </EditSection>
      ) : null}
      {STRATEGY_PARAMETER_GROUPS.map((group) => {
        const keys = group.keys.filter((key) => key in params && !importantKeySet.has(key));
        if (!keys.length) return null;
        return (
          <EditSection description={group.description} key={group.title} title={group.title}>
            {keys.map((key) => (
              <EditStrategyParamField
                help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
                key={key}
                name={key}
                onChange={(value) => updateParam(key, value)}
                value={params[key]}
              />
            ))}
          </EditSection>
        );
      })}
      {remaining.length ? (
        <EditSection description="Additional strategy settings declared by the backend configuration." title="Other Parameters">
          {remaining.map((key) => (
            <EditStrategyParamField
              help={STRATEGY_PARAMETER_HELP[key] ?? `Controls ${formatParamLabel(key)} for this strategy run.`}
              key={key}
              name={key}
              onChange={(value) => updateParam(key, value)}
              value={params[key]}
            />
          ))}
        </EditSection>
      ) : null}
    </ParameterEditorShell>
  );
}

function ParameterEditorShell({
  children,
  description,
  icon,
  meta,
  title
}: {
  children: ReactNode;
  description: string;
  icon: ReactNode;
  meta: Array<{ label: string; value: string }>;
  title: string;
}) {
  return (
    <div className="parameter-editor-shell">
      <div className="parameter-editor-hero">
        <div className="parameter-editor-title-row">
          <span className="parameter-editor-icon">{icon}</span>
          <div>
            <h3>{title}</h3>
            <p>{description}</p>
          </div>
        </div>
        <div className="parameter-editor-summary">
          {meta.map((item) => (
            <div className="parameter-editor-summary-item" key={item.label}>
              <span>{item.label}</span>
              <b>{item.value}</b>
            </div>
          ))}
        </div>
      </div>
      <div className="parameter-edit-stack">{children}</div>
    </div>
  );
}

function EditSection({
  children,
  description,
  emphasis,
  title
}: {
  children: ReactNode;
  description: string;
  emphasis?: "primary";
  title: string;
}) {
  return (
    <section className="parameter-edit-section" data-emphasis={emphasis ?? "standard"}>
      <div className="parameter-edit-heading">
        <h3>{title}</h3>
        <p>{description}</p>
      </div>
      <div className="parameter-edit-grid">{children}</div>
    </section>
  );
}

function EditStrategyParamField({ help, name, onChange, value }: { help: string; name: string; onChange: (value: StrategyParamValue) => void; value: StrategyParamValue }) {
  if (typeof value === "boolean") {
    return <EditBooleanField help={help} label={formatParamLabel(name)} value={value} onChange={onChange} />;
  }
  if (typeof value === "number") {
    return <EditNumberField help={help} label={formatParamLabel(name)} value={value} onChange={onChange} />;
  }
  return <EditField help={help} label={formatParamLabel(name)} value={String(value)} onChange={onChange} />;
}

function EditField({
  help,
  label,
  onChange,
  type = "text",
  value
}: {
  help: string;
  label: string;
  onChange: (value: string) => void;
  type?: string;
  value: string;
}) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input type={type} value={value} onChange={(event) => onChange(event.target.value)} />
    </div>
  );
}

function EditNumberField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: number) => void; value: number }) {
  return (
    <div className="field config-field">
      <FieldLabel help={help} label={label} />
      <input type="number" value={value} onChange={(event) => onChange(Number(event.target.value))} />
    </div>
  );
}

function EditBooleanField({ help, label, onChange, value }: { help: string; label: string; onChange: (value: boolean) => void; value: boolean }) {
  return (
    <div className="config-checkbox-field">
      <div>
        <FieldLabel help={help} label={label} />
        <span>{value ? "Enabled" : "Disabled"}</span>
      </div>
      <input aria-label={label} checked={value} onChange={(event) => onChange(event.target.checked)} type="checkbox" />
    </div>
  );
}

function FieldLabel({ help, label }: { help: string; label: string }) {
  return (
    <span className="parameter-label">
      <span>{label}</span>
      <HelpButton help={help} label={label} />
    </span>
  );
}

function HelpButton({ help, label }: { help: string; label: string }) {
  return (
    <button aria-label={`Help for ${label}`} className="parameter-help-button" data-help={help} type="button">
      <CircleHelp size={13} />
    </button>
  );
}

function formatParamLabel(key: string): string {
  const tokenOverrides: Record<string, string> = {
    atr: "ATR",
    avg: "Avg",
    box: "Box",
    gap: "Gap",
    max: "Max",
    min: "Min",
    orb: "ORB",
    pct: "%",
    per: "Per",
    tema: "TEMA",
    to: "to"
  };
  return key
    .split("_")
    .filter(Boolean)
    .map((part) => tokenOverrides[part.toLowerCase()] ?? part[0].toUpperCase() + part.slice(1))
    .join(" ");
}

function submittedRunName(config: StrategyConfig): string {
  const currentName = config.run_name.trim();
  const base = generatedRunName(config.strategy_name, config.strategy_version);
  if (!currentName || isDefaultRunName(currentName) || isGeneratedRunName(currentName, config.strategy_name, config.strategy_version)) {
    return base;
  }
  return `${base}_${slugRunToken(currentName)}`;
}

function generatedRunName(strategyName: string, strategyVersion: string, date = new Date()): string {
  const timestamp = [
    date.getFullYear(),
    padDatePart(date.getMonth() + 1),
    padDatePart(date.getDate()),
    "_",
    padDatePart(date.getHours()),
    padDatePart(date.getMinutes()),
    padDatePart(date.getSeconds()),
    "_",
    String(date.getMilliseconds()).padStart(3, "0")
  ].join("");
  return `${slugRunToken(strategyName)}_${slugRunToken(strategyVersion)}_${timestamp}`;
}

function isDefaultRunName(value: string): boolean {
  return ["react app run", "untitled run"].includes(value.trim().toLowerCase());
}

function isGeneratedRunName(value: string, strategyName: string, strategyVersion: string): boolean {
  const name = slugRunToken(value);
  const prefix = `${slugRunToken(strategyName)}_${slugRunToken(strategyVersion)}_`;
  return name.startsWith(prefix) && new RegExp(`^${escapeRegExp(prefix)}\\d{8}_\\d{6}_\\d{3}(?:_.+)?$`).test(name);
}

function slugRunToken(value: string): string {
  return value.replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_+|_+$/g, "").toLowerCase() || "run";
}

function padDatePart(value: number): string {
  return String(value).padStart(2, "0");
}

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function parseIsoDate(value: string): { date: Date; month: number; year: number } | null {
  const [yearText, monthText, dayText] = value.split("-");
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  if (![year, month, day].every(Number.isFinite)) return null;
  return { date: new Date(Date.UTC(year, month - 1, day)), month, year };
}

function BacktestJobPanel({
  config,
  job,
  outputRoot,
  runId
}: {
  config: StrategyConfig;
  job: Record<string, unknown> | null;
  outputRoot: string;
  runId?: string;
}) {
  const events = Array.isArray(job?.events) ? (job?.events as Record<string, unknown>[]) : [];
  const result = job?.result && typeof job.result === "object" ? job.result as Record<string, unknown> : null;
  const resultRunDir = String(result?.run_dir ?? "");
  const jobRunDir = String(job?.run_dir ?? "");
  const jobConfig = job?.config && typeof job.config === "object" ? job.config as Record<string, unknown> : null;
  const [detail, setDetail] = useState<RunDetailPayload | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const { activeTab: tab, isTabMounted, setActiveTab: setTab } = useCachedTabState("Backtest Results");
  const [selectedObservationChart, setSelectedObservationChart] = useState<ObservationChartTarget | null>(null);
  const [selectedTrade, setSelectedTrade] = useState<DataRow | null>(null);
  const shouldLoadTables = tab !== "Backtest Results";
  const isLiveRun = !runId && ["running", "queued"].includes(String(job?.status ?? "").toLowerCase());
  const shouldPollLiveDetail = isLiveRun && (!shouldLoadTables || tab === "Trades");
  const metadataRunDir = String(detail?.metadata.run_dir ?? "");
  const latestRunDir = resultRunDir || jobRunDir || metadataRunDir || [...events].reverse().map((event) => String(event.run_dir ?? "")).find(Boolean) || "";
  const latestRunId = runId || (latestRunDir ? latestRunDir.split(/[\\/]/).filter(Boolean).at(-1) ?? "" : "");

  useEffect(() => {
    setDetail(null);
    setDetailError(null);
    setSelectedObservationChart(null);
    setSelectedTrade(null);
    setTab("Backtest Results");
  }, [latestRunId]);

  useEffect(() => {
    if (!latestRunId) {
      setDetail(null);
      setDetailError(null);
      return;
    }
    let canceled = false;
    const loadDetail = () => {
      fetchRunDetail(latestRunId, outputRoot, shouldLoadTables)
        .then((payload) => {
          if (canceled) return;
          setDetail((current) => (shouldLoadTables ? payload : mergeLiveRunDetail(current, payload)));
          setDetailError(null);
        })
        .catch((err) => {
          if (!canceled) setDetailError(err instanceof Error ? err.message : String(err));
        });
    };
    loadDetail();
    if (!shouldPollLiveDetail) {
      return () => {
        canceled = true;
      };
    }
    const timer = window.setInterval(loadDetail, 2500);
    return () => {
      canceled = true;
      window.clearInterval(timer);
    };
  }, [latestRunId, outputRoot, shouldLoadTables, shouldPollLiveDetail, `${job?.status ?? "not-started"}-${events.length}`]);

  const progress = buildBacktestProgress(job, detail, config);
  const activeRunName = String(detail?.metadata.run_name ?? jobConfig?.run_name ?? config.run_name ?? "Backtest Results");
  const metrics = buildLiveBacktestMetrics(job, detail);

  return (
    <section className="panel backtest-results-panel" style={{ marginTop: 16 }}>
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h2 className="backtest-results-title">{activeRunName}</h2>
        <SemanticBadge tone={toneForStatus(progress.status)}>{progress.status}</SemanticBadge>
      </div>
      <Tabs tabs={BACKTEST_RESULT_TABS} active={tab} onChange={setTab} />
      {selectedObservationChart ? (
        <Modal className="trade-chart-modal-panel" title={`${selectedObservationChart.symbol || "Symbol"} Chart`} onClose={() => setSelectedObservationChart(null)}>
          <StrategySymbolChart
            outputRoot={outputRoot}
            runId={latestRunId}
            target={selectedObservationChart}
            trades={detail?.tables.trades.rows ?? []}
          />
        </Modal>
      ) : null}
      <div className="backtest-results-tab-content">
        <CachedTabPanel active={tab === "Backtest Results"} mounted={isTabMounted("Backtest Results")}>
          <>
            <ProgressMeter
              ariaLabel="Backtest progress"
              done={progress.done}
              elapsed_sec={0}
              label={progress.label}
              progress={progress.percent}
              status={progress.meterStatus}
              total={progress.total}
            />
            <NewRunMetricStrip metrics={metrics} />
            {detailError ? <div className="error-panel">{detailError}</div> : null}
            <PnlCandleChart payload={detail?.portfolio_candles} runName={activeRunName} title="Portfolio P/L Candles" />
          </>
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Daily"} mounted={isTabMounted("Daily")}>
          <DataTable rows={detail?.tables.daily.rows ?? []} />
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Observability"} mounted={isTabMounted("Observability")}>
          <ObservabilityPanel detail={detail} events={events} logs={detail?.logs ?? ""} onOpenChart={setSelectedObservationChart} />
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Trades"} mounted={isTabMounted("Trades")}>
          <>
            {selectedTrade ? (
              <Modal className="trade-chart-modal-panel" title={`${tradeSymbol(selectedTrade) || "Trade"} Chart`} onClose={() => setSelectedTrade(null)}>
                <TradeTickerChart
                  outputRoot={outputRoot}
                  runId={latestRunId}
                  selectedTrade={selectedTrade}
                  trades={detail?.tables.trades.rows ?? []}
                />
              </Modal>
            ) : null}
            <div className="trades-table-fill">
              <DataTable
                isRowSelected={(row) => tradeRowKey(row) === tradeRowKey(selectedTrade)}
                onRowClick={setSelectedTrade}
                rows={detail?.tables.trades.rows ?? []}
              />
            </div>
          </>
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Orders"} mounted={isTabMounted("Orders")}>
          <DataTable rows={detail?.tables.orders.rows ?? []} />
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Fills"} mounted={isTabMounted("Fills")}>
          <DataTable rows={detail?.tables.fills.rows ?? []} />
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Positions"} mounted={isTabMounted("Positions")}>
          <DataTable rows={detail?.tables.positions.rows ?? []} />
        </CachedTabPanel>
        <CachedTabPanel active={tab === "Watchlist"} mounted={isTabMounted("Watchlist")}>
          <DataTable
            onRowClick={(row) => openScannerRowChart(row, setSelectedObservationChart)}
            rows={detail?.tables.watchlist?.rows ?? []}
          />
        </CachedTabPanel>
        {job?.error ? <div className="error-panel">{String(job.error)}</div> : null}
      </div>
    </section>
  );
}

function ObservabilityPanel({
  detail,
  events,
  logs,
  onOpenChart
}: {
  detail: RunDetailPayload | null;
  events: Record<string, unknown>[];
  logs: string;
  onOpenChart: (target: ObservationChartTarget) => void;
}) {
  const scannerRows = detail?.tables.observability_scanner?.rows ?? [];
  const traceRows = detail?.tables.observability_trace?.rows ?? [];
  const stateRows = detail?.tables.observability_state?.rows ?? [];
  const rejectedRows = detail?.tables.rejections?.rows ?? [];
  const legacyScannerRows = detail?.tables.scanner?.rows ?? [];
  const scannerContextRows = scannerRows.length ? scannerRows : legacyScannerRows;
  const actions = useMemo(() => buildObservabilityActions(traceRows), [traceRows]);
  const [activeActionFilter, setActiveActionFilter] = useState<ObservationActionFilter>("all");
  const [visibleActionCount, setVisibleActionCount] = useState(100);
  const filteredActions = useMemo(() => actions.filter((action) => observationActionMatchesFilter(action, activeActionFilter)), [actions, activeActionFilter]);
  const visibleActions = filteredActions.slice(0, visibleActionCount);
  const sources = useMemo<ObservationEvidenceSources>(
    () => ({
      fills: detail?.tables.fills.rows ?? [],
      orders: detail?.tables.orders.rows ?? [],
      rejections: rejectedRows,
      scanner: scannerContextRows,
      states: stateRows,
      trades: detail?.tables.trades.rows ?? [],
    }),
    [detail?.tables.fills.rows, detail?.tables.orders.rows, detail?.tables.trades.rows, rejectedRows, scannerContextRows, stateRows]
  );
  const systemRows = events.map((event) => ({
    event: event.event,
    session_date: event.session_date,
    status: event.status,
    run_dir: event.run_dir,
    ...((event.daily_summary as Record<string, unknown>) ?? {})
  }));

  useEffect(() => {
    setVisibleActionCount(100);
  }, [activeActionFilter, actions]);

  return (
    <section className="observability-workspace">
      <div className="observability-summary">
        <ObservabilitySummaryMetric label="Actions" value={actions.length} />
        <ObservabilitySummaryMetric label="Scanner Context" value={scannerContextRows.length} />
        <ObservabilitySummaryMetric label="State Rows" value={stateRows.length} />
        <ObservabilitySummaryMetric label="Skipped" value={rejectedRows.length} />
      </div>
      <div className="observability-filter-bar">
        {OBSERVABILITY_ACTION_FILTERS.map((filter) => (
          <button
            className={activeActionFilter === filter.value ? "observability-filter-chip active" : "observability-filter-chip"}
            key={filter.value}
            onClick={() => setActiveActionFilter(filter.value)}
            type="button"
          >
            <span>{filter.label}</span>
            <strong>{formatNumber(countObservationActionFilter(actions, filter.value))}</strong>
          </button>
        ))}
      </div>
      <div className="observability-scroll-region">
        <div className="observability-action-list">
          {visibleActions.length ? (
            visibleActions.map((action) => <ObservabilityActionCard action={action} key={action.id} onOpenChart={onOpenChart} sources={sources} />)
          ) : (
            <div className="empty-state">No strategy actions were captured. Check observability mode and profile sessions.</div>
          )}
        </div>
        {visibleActionCount < filteredActions.length ? (
          <button className="observability-load-more" onClick={() => setVisibleActionCount((count) => Math.min(filteredActions.length, count + 100))} type="button">
            Show more actions
            <span>{formatNumber(visibleActionCount)} of {formatNumber(filteredActions.length)}</span>
          </button>
        ) : null}
        {systemRows.length || logs ? <ObservabilitySystemPanel logs={logs} rows={systemRows} /> : null}
      </div>
    </section>
  );
}

function ObservabilitySummaryMetric({ label, value }: { label: string; value: number }) {
  return (
    <article className="observability-summary-card">
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </article>
  );
}

function ObservabilityActionCard({ action, onOpenChart, sources }: { action: ObservabilityAction; onOpenChart: (target: ObservationChartTarget) => void; sources: ObservationEvidenceSources }) {
  const [open, setOpen] = useState(false);
  const evidence = useMemo(() => (open ? buildObservationEvidence(action.trace, sources) : emptyObservationEvidence()), [action.trace, open, sources]);
  const evidenceCount = evidence.scannerRows.length + evidence.stateRows.length + evidence.rejectionRows.length + evidence.orderRows.length + evidence.fillRows.length + evidence.tradeRows.length;
  const primaryFields = primaryObservationFields(action);
  const previewFields = observationActionPreviewFields(action);
  const inputFields = prioritizedObservationFields(action.inputFields, "input").slice(0, 8);
  const stateFields = prioritizedObservationFields(action.stateFields, "state").slice(0, 8);
  const chartTarget = observationActionChartTarget(action);
  return (
    <article className={open ? "observability-action-card open" : "observability-action-card"} data-tone={action.tone}>
      <button aria-expanded={open} className="observability-action-header" onClick={() => setOpen((value) => !value)} type="button">
        <span className="observability-card-corner">
          <span>{formatObservationTimestamp(action.timestamp || action.sessionDate) || "No timestamp"}</span>
          <strong>#{formatNumber(action.step)}</strong>
        </span>
        <span className="observability-action-symbol">
          <strong>{action.ticker || "-"}</strong>
        </span>
        <span className="observability-action-toggle" aria-hidden="true">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
        <span className="observability-action-title">
          <strong>{action.title}</strong>
          <ObservationActionPreview action={action} />
        </span>
        <span className="observability-action-tags">
          <ObservationActionPreviewFields fields={previewFields} />
        </span>
        <span className="observability-action-meta">
          <SemanticBadge tone={action.tone}>{action.decision || "observed"}</SemanticBadge>
          <span className="observability-card-count">{open ? `${formatNumber(evidenceCount)} rows` : "Open"}</span>
        </span>
      </button>
      {chartTarget ? (
        <button
          aria-label={`Show ${action.ticker} action on chart`}
          className="observability-action-chart-button"
          onClick={(event) => {
            event.stopPropagation();
            onOpenChart(chartTarget);
          }}
          title="Show action on chart"
          type="button"
        >
          <BarChart3 size={14} />
        </button>
      ) : null}
      {open ? (
        <div className="observability-action-body">
          <ObservationDecisionPanel action={action} fields={primaryFields} />
          <div className="observability-detail-grid">
            {inputFields.length ? <ObservationFieldGroup fields={inputFields} title="Inputs & Thresholds" /> : null}
            <ObservationStateSnapshots rows={evidence.stateRows} />
          </div>
          {stateFields.length ? <ObservationFieldGroup fields={stateFields} title="Strategy State" /> : null}
          <ObservationEvidenceTable
            description="Strategy-level skip rows captured at this exact action time."
            presentation="cards"
            rows={evidence.rejectionRows}
            title="Strategy Skips"
          />
          <ObservationEvidenceTable
            description="Order records created by this exact action."
            presentation="cards"
            rows={evidence.orderRows}
            title="Execution Orders"
          />
          <ObservationEvidenceTable
            description="Fill records produced by the backtest fill model for those orders."
            presentation="cards"
            rows={evidence.fillRows}
            title="Execution Fills"
          />
          <ObservationEvidenceTable
            description="Closed trade records finalized by this exact action."
            presentation="cards"
            rows={evidence.tradeRows}
            title="Closed Trades"
          />
          <ObservationEvidenceTable
            collapsible
            description="Captured scanner candidates for this action timestamp, not only the selected ticker."
            onOpenChart={onOpenChart}
            rows={evidence.scannerRows}
            title="Full Scanner At Action Time"
          />
        </div>
      ) : null}
    </article>
  );
}

function ObservationDecisionPanel({ action, fields }: { action: ObservabilityAction; fields: ObservationFieldValue[] }) {
  return (
    <section className="observability-decision-panel" data-tone={action.tone}>
      <div className="observability-decision-copy">
        <span>Decision</span>
        <strong>{action.decision || "observed"}</strong>
        <p>{action.reason || action.reasonCode || "No reason recorded"}</p>
      </div>
      <div className="observability-decision-facts">
        {fields
          .filter((field) => field.key !== "decision" && field.key !== "reason")
          .map((field) => (
            <ObservationFact key={field.key} label={field.label} value={field.value} />
          ))}
      </div>
    </section>
  );
}

function ObservationActionPreview({ action }: { action: ObservabilityAction }) {
  const reason = action.reason || action.reasonCode || "No reason recorded";
  return (
    <span className="observability-action-preview">
      <span className="observability-action-reason">{reason}</span>
    </span>
  );
}

function ObservationActionPreviewFields({ fields }: { fields: ObservationFieldValue[] }) {
  if (!fields.length) return null;
  return (
    <span className="observability-action-preview-fields">
      {fields.map((field) => (
        <span className="observability-action-preview-chip" key={field.key}>
          <span>{field.label}</span>
          <span className="observability-action-preview-value">{formatObservationValue(field.value, field.label)}</span>
        </span>
      ))}
    </span>
  );
}

function ObservationTagDetailPopover({ field }: { field: ObservationFieldValue }) {
  const segments = observationTagDetailSegments(field);
  return (
    <span className="observability-tag-popover" onClick={(event) => event.stopPropagation()}>
      <span className="observability-tag-popover-header">
        <span>{field.label}</span>
        <small>{formatNumber(segments.length)} fields</small>
      </span>
      <span className="observability-tag-popover-grid">
        {segments.map((segment, index) => (
          <span className="observability-tag-popover-field" key={`${segment.label}:${segment.value}:${index}`}>
            <span>{segment.label}</span>
            <span>{segment.value}</span>
          </span>
        ))}
      </span>
    </span>
  );
}

function ObservationFact({ label, value }: { label: string; value: unknown }) {
  const field = { key: label, label, value };
  return (
    <div className="observability-fact">
      <span>{label}</span>
      {observationFieldIsStructuredTag(field) ? (
        <ObservationStructuredTagValue field={field} />
      ) : (
        <span className="observability-fact-value">{formatObservationValue(value, label)}</span>
      )}
    </div>
  );
}

function ObservationFieldGroup({ fields, title }: { fields: ObservationFieldValue[]; title: string }) {
  const visibleFields = fields.filter((field) => field.value !== undefined && field.value !== null && field.value !== "");
  if (!visibleFields.length) return null;
  return (
    <section className="observability-field-group">
      <div className="observability-section-header">
        <h4>{title}</h4>
        <small>{formatNumber(visibleFields.length)} values</small>
      </div>
      <div className="observability-fact-list">
        {visibleFields.map((field) => (
          <ObservationFact key={field.key} label={field.label} value={field.value} />
        ))}
      </div>
    </section>
  );
}

function ObservationEvidenceTable({
  collapsible = false,
  defaultFilterPreset,
  description,
  filterPresets,
  onOpenChart,
  presentation = "table",
  rows,
  showWhenEmpty = false,
  title,
}: {
  collapsible?: boolean;
  defaultFilterPreset?: DataTableFilterPreset;
  description?: string;
  filterPresets?: DataTableFilterPreset[];
  onOpenChart?: (target: ObservationChartTarget) => void;
  presentation?: "cards" | "table";
  rows: DataRow[];
  showWhenEmpty?: boolean;
  title: string;
}) {
  const [open, setOpen] = useState(false);
  const scannerTable = title === "Full Scanner At Action Time";
  const watchlistTable = title.toLowerCase().includes("watchlist");
  const chartableTable = Boolean(onOpenChart) && (scannerTable || watchlistTable || title === "Scanner Raw Input");
  const displayRows = scannerTable ? sortScannerSnapshotRows(rows) : rows;
  const scannerColumns = scannerTable ? scannerSnapshotColumns(displayRows) : undefined;
  const scannerSort = scannerTable && scannerColumns?.includes("rank") ? { column: "rank", direction: "asc" as const } : undefined;
  const tableFilterPresets = filterPresets ?? (scannerTable ? OBSERVABILITY_SCANNER_FILTER_PRESETS : undefined);
  const emptyMessage = <p className="muted" style={{ margin: 0, padding: "12px 16px" }}>No rows for this step.</p>;
  if (!displayRows.length && !showWhenEmpty) return null;
  if (!collapsible) {
    return (
      <section className="observability-evidence-block">
        <div className="observability-evidence-header static">
          <span className="observability-evidence-title">
            <span>{title}</span>
            {description ? <small>{description}</small> : null}
          </span>
          <small>{formatNumber(displayRows.length)} rows</small>
        </div>
        {presentation === "cards" ? (
          <ObservationEvidenceCards rows={displayRows} title={title} />
        ) : displayRows.length ? (
          <DataTable
            columns={scannerColumns}
            defaultFilterPreset={defaultFilterPreset}
            defaultSort={scannerSort}
            filterPresets={tableFilterPresets}
            onRowClick={chartableTable && onOpenChart ? (row) => openScannerRowChart(row, onOpenChart) : undefined}
            rows={displayRows}
            transposeHelper={scannerTable}
          />
        ) : (
          emptyMessage
        )}
      </section>
    );
  }
  return (
    <section className="observability-evidence-block">
      <button aria-expanded={open} className="observability-evidence-header" onClick={() => setOpen((value) => !value)} type="button">
        <span>{open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}</span>
        <span className="observability-evidence-title">
          <span>{title}</span>
          {description ? <small>{description}</small> : null}
        </span>
        <small>{formatNumber(displayRows.length)} rows</small>
      </button>
      {open && displayRows.length ? (
        <DataTable
          columns={scannerColumns}
          defaultFilterPreset={defaultFilterPreset}
          defaultSort={scannerSort}
          filterPresets={tableFilterPresets}
          onRowClick={chartableTable && onOpenChart ? (row) => openScannerRowChart(row, onOpenChart) : undefined}
          rows={displayRows}
          transposeHelper={scannerTable}
        />
      ) : open ? emptyMessage : null}
    </section>
  );
}

function ObservationEvidenceCards({ rows, title }: { rows: DataRow[]; title: string }) {
  return (
    <div className="observability-evidence-card-list">
      {rows.map((row, index) => (
        <ObservationEvidenceCard index={index} key={`${title}:${evidenceRowIdentity(row, index)}:${index}`} row={row} title={title} />
      ))}
    </div>
  );
}

function ObservationEvidenceCard({ index, row, title }: { index: number; row: DataRow; title: string }) {
  const label = evidenceCardLabel(row, title, index);
  const time = rowText(row, "created_at") || rowText(row, "filled_at") || rowText(row, "entry_time") || rowText(row, "exit_time") || rowText(row, "bar_time_market") || rowText(row, "timestamp");
  const badge = rowText(row, "status") || rowText(row, "state") || rowText(row, "side") || rowText(row, "exit_reason") || rowText(row, "reason") || "";
  const fields = evidenceCardFields(row);
  return (
    <article className="observability-evidence-card">
      <div className="observability-evidence-card-header">
        <span className="observability-evidence-card-title">
          <strong>{label}</strong>
          {time ? <small>{formatObservationTimestamp(time)}</small> : null}
        </span>
        {badge ? <SemanticBadge tone={observabilitySemanticTone(badge, title)}>{badge}</SemanticBadge> : null}
      </div>
      {fields.length ? (
        <div className="observability-evidence-card-fields">
          {fields.map((field) => (
            <ObservationEvidenceCardField field={field} key={field.key} />
          ))}
        </div>
      ) : (
        <div className="empty-state">No row details.</div>
      )}
    </article>
  );
}

function ObservationEvidenceCardField({ field }: { field: ObservationFieldValue }) {
  const semanticTone = observationFieldSemanticTone(field);
  if (observationFieldIsStructuredTag(field)) {
    return (
      <div className="observability-evidence-card-field">
        <span>{field.label}</span>
        <ObservationStructuredTagValue field={field} compact />
      </div>
    );
  }
  return (
    <div className="observability-evidence-card-field">
      <span>{field.label}</span>
      {semanticTone ? (
        <SemanticBadge tone={semanticTone}>{formatObservationValue(field.value, field.label)}</SemanticBadge>
      ) : (
        <span>{formatObservationValue(field.value, field.label)}</span>
      )}
    </div>
  );
}

function ObservationStructuredTagValue({ compact = false, field }: { compact?: boolean; field: ObservationFieldValue }) {
  const [open, setOpen] = useState(false);
  useEffect(() => {
    if (!open) return;
    const closePopover = () => setOpen(false);
    document.addEventListener("click", closePopover);
    return () => document.removeEventListener("click", closePopover);
  }, [open]);
  const toggleDetails = (event: MouseEvent<HTMLSpanElement> | KeyboardEvent<HTMLSpanElement>) => {
    event.preventDefault();
    event.stopPropagation();
    setOpen((current) => !current);
  };
  return (
    <span className={compact ? "observability-structured-tag compact" : "observability-structured-tag"} onClick={(event) => event.stopPropagation()}>
      <span className="observability-structured-tag-text">{formatObservationValue(field.value, field.label)}</span>
      <span
        aria-expanded={open}
        aria-label={`Show ${field.label} details`}
        className="observability-tag-detail-trigger"
        onClick={toggleDetails}
        onKeyDown={(event) => {
          if (event.key === "Enter" || event.key === " ") toggleDetails(event);
          if (event.key === "Escape") setOpen(false);
        }}
        role="button"
        tabIndex={0}
      >
        <MoreHorizontal size={13} />
      </span>
      {open ? <ObservationTagDetailPopover field={field} /> : null}
    </span>
  );
}

function evidenceCardFields(row: DataRow): ObservationFieldValue[] {
  const preferredKeys = [
    "symbol",
    "ticker",
    "side",
    "quantity",
    "qty",
    "status",
    "state",
    "order_type",
    "limit_price",
    "stop_price",
    "fill_price",
    "filled_price",
    "entry_price",
    "exit_price",
    "pnl",
    "fees",
    "commission",
    "order_id",
    "trade_id",
    "fill_id",
    "created_at",
    "filled_at",
    "entry_time",
    "exit_time",
    "exit_reason",
    "reason",
  ];
  const orderedKeys = [
    ...preferredKeys.filter((key) => key in row),
    ...Object.keys(row).filter((key) => !preferredKeys.includes(key)),
  ];
  return orderedKeys
    .filter((key) => key !== "values_json" && key !== "state_json")
    .map((key) => ({ key, label: formatObservationLabel(key), value: normalizeObservationTableValue(row[key]) }))
    .filter((field) => field.value !== undefined && field.value !== null && field.value !== "");
}

function evidenceCardLabel(row: DataRow, title: string, index: number): string {
  const symbol = rowText(row, "symbol") || rowText(row, "ticker");
  const identifier = rowText(row, "trade_id") || rowText(row, "order_id") || rowText(row, "fill_id") || String(index + 1);
  if (symbol) return `${symbol} #${identifier}`;
  return `${title.replace(/^Execution\s+/, "").replace(/^Closed\s+/, "")} #${identifier}`;
}

function evidenceRowIdentity(row: DataRow, index: number): string {
  return rowText(row, "trade_id") || rowText(row, "order_id") || rowText(row, "fill_id") || rowText(row, "created_at") || rowText(row, "filled_at") || rowText(row, "entry_time") || String(index);
}

function ObservationStateSnapshots({ rows }: { rows: DataRow[] }) {
  if (!rows.length) return null;
  return (
    <section className="observability-state-snapshots">
      <div className="observability-section-header">
        <h4>State Snapshots</h4>
        <small>{formatNumber(rows.length)} rows</small>
      </div>
      <div className="observability-state-card-grid">
        {rows.map((row, index) => {
          const state = parseObservationJson(row.state_json);
          const fields = objectToObservationFields(state).slice(0, 8);
          return (
            <article className="observability-state-card" key={`${rowText(row, "timestamp")}:${rowText(row, "ticker")}:${index}`}>
              <div className="observability-state-card-header">
                <span>{rowText(row, "scope") || "state"}</span>
                <small>{formatObservationTimestamp(rowText(row, "timestamp") || rowText(row, "session_date"))}</small>
              </div>
              {fields.length ? (
                <div className="observability-fact-list compact">
                  {fields.map((field) => (
                    <ObservationFact key={field.key} label={field.label} value={field.value} />
                  ))}
                </div>
              ) : (
                <div className="empty-state">No parsed state values.</div>
              )}
            </article>
          );
        })}
      </div>
    </section>
  );
}

function ObservabilitySystemPanel({ logs, rows }: { logs: string; rows: DataRow[] }) {
  const [open, setOpen] = useState(false);
  return (
    <article className={open ? "observability-card open" : "observability-card"}>
      <button className="observability-card-header" onClick={() => setOpen((value) => !value)} type="button">
        <span>
          <strong>Run System Context</strong>
          <small>Progress events and raw logs for infrastructure debugging.</small>
        </span>
        <span className="observability-card-count">{formatNumber(rows.length)} events</span>
      </button>
      {open ? (
        <div className="observability-card-body">
          {rows.length ? <DataTable rows={rows} /> : <div className="empty-state">No progress events were reported.</div>}
          {logs ? <pre className="observability-log">{logs}</pre> : null}
        </div>
      ) : null}
    </article>
  );
}

type ObservationFieldValue = {
  key: string;
  label: string;
  value: unknown;
};

type ObservabilityAction = {
  decision: string;
  eventType: string;
  id: string;
  inputFields: ObservationFieldValue[];
  reason: string;
  reasonCode: string;
  sessionDate: string;
  stage: string;
  stateFields: ObservationFieldValue[];
  step: number;
  subtitle: string;
  ticker: string;
  timestamp: string;
  title: string;
  tone: SemanticTone;
  trace: DataRow;
};

type ObservationEvidenceSources = {
  fills: DataRow[];
  orders: DataRow[];
  rejections: DataRow[];
  scanner: DataRow[];
  states: DataRow[];
  trades: DataRow[];
};

type ObservationEvidenceRows = {
  fillRows: DataRow[];
  orderRows: DataRow[];
  rejectionRows: DataRow[];
  scannerRows: DataRow[];
  stateRows: DataRow[];
  tradeRows: DataRow[];
};

type ObservationActionFilter = "all" | "cancel" | "entry" | "exit" | "order" | "rejected" | "scanner";

const OBSERVABILITY_ACTION_FILTERS: Array<{ label: string; value: ObservationActionFilter }> = [
  { label: "All", value: "all" },
  { label: "Entries", value: "entry" },
  { label: "Exits", value: "exit" },
  { label: "Skipped", value: "rejected" },
  { label: "Scanner", value: "scanner" },
  { label: "Orders", value: "order" },
  { label: "Cancel", value: "cancel" },
];

function buildObservabilityActions(traces: DataRow[]): ObservabilityAction[] {
  return [...traces]
    .sort(compareObservationTraceRows)
    .map((trace, index) => {
      const sessionDate = rowText(trace, "session_date");
      const timestamp = rowText(trace, "timestamp");
      const ticker = normalizedTicker(rowText(trace, "ticker"));
      const stage = rowText(trace, "stage");
      const eventType = rowText(trace, "event_type");
      const decision = rowText(trace, "decision");
      const reasonCode = rowText(trace, "reason_code");
      const reason = normalizeObservationReason(rowText(trace, "reason"), eventType);
      const values = parseObservationJson(trace.values_json);
      const state = parseObservationJson(trace.state_json);
      return {
        decision,
        eventType,
        id: `${timestamp}:${ticker}:${stage}:${eventType}:${index}`,
        inputFields: objectToObservationFields(values),
        reason,
        reasonCode,
        sessionDate,
        stage,
        stateFields: objectToObservationFields(state),
        step: index + 1,
        subtitle: [ticker || "Run", formatObservationLabel(stage), reasonCode || reason].filter(Boolean).join(" | "),
        ticker,
        timestamp,
        title: formatObservationActionTitle(eventType, decision),
        tone: observationDecisionTone(decision, eventType),
        trace,
      };
    });
}

function compareObservationTraceRows(left: DataRow, right: DataRow): number {
  const timeDiff = rowTime(left) - rowTime(right);
  if (timeDiff) return timeDiff;

  const sequenceDiff = finiteObservationNumber(left.sequence) - finiteObservationNumber(right.sequence);
  if (sequenceDiff) return sequenceDiff;

  const stageDiff = observationStageOrder(rowText(left, "stage")) - observationStageOrder(rowText(right, "stage"));
  if (stageDiff) return stageDiff;

  const leftValues = parseObservationJson(left.values_json);
  const rightValues = parseObservationJson(right.values_json);
  const rankDiff = observationRank(left, leftValues) - observationRank(right, rightValues);
  if (rankDiff) return rankDiff;

  return normalizedTicker(rowText(left, "ticker")).localeCompare(normalizedTicker(rowText(right, "ticker")));
}

function finiteObservationNumber(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : Number.POSITIVE_INFINITY;
}

function observationRank(row: DataRow, values: Record<string, unknown>): number {
  return Math.min(
    finiteObservationNumber(row.setup_rank),
    finiteObservationNumber(row.live_rank),
    finiteObservationNumber(row.rank),
    finiteObservationNumber(values.setup_rank),
    finiteObservationNumber(values.live_rank),
    finiteObservationNumber(values.rank)
  );
}

function observationStageOrder(stage: string): number {
  const normalized = stage.toLowerCase();
  if (normalized.includes("setup_scanner")) return 0;
  if (normalized.includes("entry_evaluation")) return 10;
  if (normalized.includes("risk_check")) return 20;
  if (normalized.includes("order_request")) return 30;
  if (normalized.includes("entry_order_management")) return 40;
  if (normalized.includes("exit_evaluation")) return 50;
  if (normalized.includes("day_end")) return 60;
  return 100;
}

function countObservationActionFilter(actions: ObservabilityAction[], filter: ObservationActionFilter): number {
  return actions.reduce((count, action) => count + (observationActionMatchesFilter(action, filter) ? 1 : 0), 0);
}

function observationActionMatchesFilter(action: ObservabilityAction, filter: ObservationActionFilter): boolean {
  if (filter === "all") return true;
  const text = `${action.eventType} ${action.decision} ${action.stage} ${action.reasonCode} ${action.reason}`.toLowerCase();
  if (filter === "entry") return text.includes("entry");
  if (filter === "exit") return text.includes("exit") || text.includes("day_end");
  if (filter === "rejected") return text.includes("reject") || text.includes("skip");
  if (filter === "scanner") return text.includes("scanner") || text.includes("watchlist");
  if (filter === "order") return text.includes("order") || text.includes("submit");
  if (filter === "cancel") return text.includes("cancel");
  return true;
}

function buildObservationEvidence(trace: DataRow, sources: ObservationEvidenceSources): ObservationEvidenceRows {
  return {
    fillRows: relatedSymbolActionRows(sources.fills, trace, ["filled_at", "bar_time_market"]).slice(0, 12),
    orderRows: relatedSymbolActionRows(sources.orders, trace, ["created_at"]).slice(0, 12),
    rejectionRows: relatedSymbolActionRows(sources.rejections, trace, ["timestamp"]).slice(0, 12),
    scannerRows: flattenScannerRows(relatedScannerRows(sources.scanner, trace)),
    stateRows: relatedStateRows(sources.states, trace).slice(0, 12),
    tradeRows: relatedTradeActionRows(sources.trades, trace).slice(0, 12),
  };
}

function emptyObservationEvidence(): ObservationEvidenceRows {
  return {
    fillRows: [],
    orderRows: [],
    rejectionRows: [],
    scannerRows: [],
    stateRows: [],
    tradeRows: [],
  };
}

function interactiveDebugParameterRows(config: Record<string, unknown>, currentStrategyParams: Record<string, StrategyParamValue>): DataRow[] {
  const strategyParams =
    config.strategy_params && typeof config.strategy_params === "object"
      ? config.strategy_params as Record<string, unknown>
      : currentStrategyParams;
  return [
    ...Object.entries(config)
      .filter(([key]) => key !== "strategy_params")
      .map(([key, value]) => ({ group: "Backtest", parameter: key, value: normalizeObservationTableValue(value) })),
    ...Object.entries(strategyParams).map(([key, value]) => ({ group: "Strategy", parameter: key, value: normalizeObservationTableValue(value) })),
  ];
}

function interactiveDebugRawFilterPresets(config: StrategyConfig): DataTableFilterPreset[] | undefined {
  if (config.strategy_name !== "long_momentum") return undefined;
  const params = config.strategy_params ?? {};
  const version = String(config.strategy_version || "").toLowerCase();
  const minPrice = strategyNumberParam(params, "min_price", 1);
  const maxPrice = strategyNumberParam(params, "max_price", 10);
  if (version !== "v9") return interactiveDebugStrategyFilterPresets(config);
  return [
    {
      filters: {
        last_close: betweenFilter(minPrice, maxPrice),
        last_5m_return: gteFilter(strategyNumberParam(params, "min_last_5m_return", 0.05)),
        last_transactions: gteFilter(strategyNumberParam(params, "min_first_entry_transactions", 100)),
      },
      label: "v9 Watchlist Add Raw",
      title: "Apply only the raw/provider inputs for adding a ticker to the v9 watchlist: price range, same-session 5m return, and transactions.",
    },
    {
      filters: {
        last_close: betweenFilter(minPrice, maxPrice),
        minute_of_day: betweenFilter(
          strategyNumberParam(params, "trading_start_minute", 240),
          strategyNumberParam(params, "trading_end_minute", 1200) - 1,
        ),
        last_5m_return: gteFilter(strategyNumberParam(params, "min_last_5m_return", 0.05)),
        last_transactions: gteFilter(strategyNumberParam(params, "min_first_entry_transactions", 100)),
        last_transactions_vs_prior_3: gteFilter(strategyNumberParam(params, "min_first_entry_transactions_vs_prior_3", 20)),
      },
      label: "v9 Immediate Entry Raw",
      title: "Apply the raw/provider inputs that allow v9 to enter on the same bar as watchlist detection.",
    },
    {
      filters: {
        last_close: betweenFilter(minPrice, maxPrice),
        minute_of_day: betweenFilter(
          strategyNumberParam(params, "trading_start_minute", 240),
          strategyNumberParam(params, "trading_end_minute", 1200) - 1,
        ),
        last_vwap: gtFilter(0),
        long_momentum_v9_reentry_vwap_buffer_ok: trueFilter(),
        last_bearish_volume_divergence_score: lteFilter(strategyNumberParam(params, "max_reentry_bvd_score", 80)),
        current_open_above_last_2_body_high: trueFilter(),
      },
      label: "v9 Watchlist Entry Raw",
      title: "Apply the raw/provider inputs available before v9 checks watchlist state, last close is above the VWAP buffer, last_close >= last_open, 1m BVD is not above the threshold, and current_open breaks the highest body of the last two completed bars.",
    },
    {
      filters: {
        last_double_timeframe_bearish_volume_divergence_score: gtFilter(strategyNumberParam(params, "double_bvd_exit_score", 50)),
        last_is_red: trueFilter(),
      },
      label: "v9 Exit Raw",
      title: "Apply the raw/provider 2xBVD exit input threshold and the red-candle confirmation. TEMA and pocket exits are visible in the step filter groups and order tags because they compare multiple fields and position state.",
    },
  ];
}

function interactiveDebugStrategyFilterPresets(config: StrategyConfig): DataTableFilterPreset[] | undefined {
  if (config.strategy_name !== "long_momentum") return undefined;
  const params = config.strategy_params ?? {};
  const version = String(config.strategy_version || "").toLowerCase();
  const minPrice = strategyNumberParam(params, "min_price", 1);
  const maxPrice = strategyNumberParam(params, "max_price", 10);
  const minVolume = strategyNumberParam(params, "min_volume", 10_000);
  const minTransactions = strategyNumberParam(params, "min_transactions", 100);
  const minMacdHistZ = strategyNumberParam(params, "min_macd_hist_z_since_open", 0.1);
  const minRecentDollarVolume = strategyNumberParam(params, "min_recent_dollar_volume_5", 100_000);
  const maxSpreadBpsAbs = strategyNumberParam(params, "max_spread_bps_abs", 100);
  const maxSpreadBpsMax = strategyNumberParam(params, "max_spread_bps_max", 150);
  const minQuoteValidRatio = strategyNumberParam(params, "min_quote_valid_ratio", 0.8);
  const maxLockedOrCrossedCount = strategyNumberParam(params, "max_locked_or_crossed_count", 0);
  if (version === "v9") {
    return [
      {
        filters: {
          last_close: betweenFilter(minPrice, maxPrice),
          long_momentum_v9_last_5m_return: gteFilter(strategyNumberParam(params, "min_last_5m_return", 0.05)),
          last_transactions: gteFilter(strategyNumberParam(params, "min_first_entry_transactions", 100)),
        },
        label: "v9 Watchlist Add",
        title: "Apply the visible v9 watchlist-add inputs: price range, provider-built same-session return threshold, and transactions.",
      },
      {
        filters: {
          last_close: betweenFilter(minPrice, maxPrice),
          long_momentum_v9_watchlist_added_timestamp: hasValueFilter(),
          minute_of_day: betweenFilter(
            strategyNumberParam(params, "trading_start_minute", 240),
            strategyNumberParam(params, "trading_end_minute", 1200) - 1,
          ),
          held_quantity: lteFilter(0),
          long_momentum_v9_pending_symbol_order: { operator: "eq", presetLabel: "Is false", valueText: "false" },
          long_momentum_v9_last_5m_return: gteFilter(strategyNumberParam(params, "min_last_5m_return", 0.05)),
          last_transactions: gteFilter(strategyNumberParam(params, "min_first_entry_transactions", 100)),
          last_transactions_vs_prior_3: gteFilter(strategyNumberParam(params, "min_first_entry_transactions_vs_prior_3", 20)),
        },
        label: "v9 Immediate Entry",
        title: "Apply the v9 same-bar immediate entry gates: watchlist detected, transaction impulse confirmed, and no position or pending order.",
      },
      {
        filters: {
          last_close: betweenFilter(minPrice, maxPrice),
          long_momentum_v9_watchlist_added_timestamp: hasValueFilter(),
          minute_of_day: betweenFilter(
            strategyNumberParam(params, "trading_start_minute", 240),
            strategyNumberParam(params, "trading_end_minute", 1200) - 1,
          ),
          held_quantity: lteFilter(0),
          long_momentum_v9_pending_symbol_order: { operator: "eq", presetLabel: "Is false", valueText: "false" },
          long_momentum_v9_reentry_vwap_buffer_ok: trueFilter(),
          long_momentum_v9_reentry_last_bar_not_red: trueFilter(),
          long_momentum_v9_reentry_bvd_ok: trueFilter(),
          long_momentum_v9_reentry_body_break_ok: trueFilter(),
        },
        label: "v9 Watchlist VWAP Entry",
        title: "Apply the v9 watchlist entry gates: ticker is on watchlist, not held/pending, last close is above the VWAP buffer, the reclaim bar is not red, 1m BVD is not above the threshold, and current_open breaks the highest body of the last two completed bars.",
      },
      {
        filters: {
          long_momentum_v9_entry_open: trueFilter(),
        },
        label: "v9 Final Entry",
        title: "Apply the exact v9 final entry decision.",
      },
    ];
  }
  const filters: DataTableFilterPreset["filters"] = {
    last_close: betweenFilter(minPrice, maxPrice),
    last_volume: gteFilter(minVolume),
    last_transactions: gteFilter(minTransactions),
  };

  if (version === "v1") {
    Object.assign(filters, {
      current_open_above_last_body_high: trueFilter(),
      last_tema_open: trueFilter(),
      last_macd_line: gtFilter(0, "Positive"),
      last_macd_hist_z_since_open: gteFilter(minMacdHistZ),
      long_momentum_spread_ok: trueFilter(),
      long_momentum_entry_open: trueFilter(),
    });
  } else if (["v2", "v3", "v7"].includes(version)) {
    Object.assign(filters, {
      current_open_above_last_body_high: trueFilter(),
      last_tema_open: trueFilter(),
      ...(version === "v2" ? { long_momentum_v2_macd_line_or_vwap_ok: trueFilter() } : { last_macd_line: gtFilter(0, "Positive") }),
      last_macd_hist_z_since_open: gteFilter(minMacdHistZ),
      long_momentum_spread_ok: trueFilter(),
      last_recent_dollar_volume_5: gteFilter(minRecentDollarVolume),
      last_spread_bps_abs: lteFilter(maxSpreadBpsAbs),
      last_spread_bps_max: lteFilter(maxSpreadBpsMax),
      last_quote_valid_ratio: gteFilter(minQuoteValidRatio),
      last_locked_or_crossed_count: lteFilter(maxLockedOrCrossedCount),
      long_momentum_entry_open: trueFilter(),
    });
    if (version === "v3" || version === "v7") {
      filters.last_close_location = gteFilter(strategyNumberParam(params, "min_close_location", 0.55));
    }
  } else if (version === "v4") {
    Object.assign(filters, {
      long_momentum_setup_price_ok: trueFilter(),
      long_momentum_setup_activity_ok: trueFilter(),
      long_momentum_setup_quote_ok: trueFilter(),
      long_momentum_setup_trend_ok: trueFilter(),
      long_momentum_setup_exhaustion_ok: trueFilter(),
      long_momentum_setup_open: trueFilter(),
      long_momentum_v4_entry_open: trueFilter(),
      long_momentum_entry_open: trueFilter(),
    });
  } else if (version === "v5") {
    Object.assign(filters, {
      long_momentum_v5_price_above_vwap: trueFilter(),
      long_momentum_v5_tema_stack_ok: trueFilter(),
      long_momentum_v5_tema_spread_ok: trueFilter(),
      long_momentum_v5_macd_line_positive: trueFilter(),
      long_momentum_v5_macd_hist_ok: trueFilter(),
      long_momentum_v5_above_day_open: trueFilter(),
      long_momentum_v5_near_enough_day_high: trueFilter(),
      long_momentum_v5_volume_vs_avg_so_far_ok: trueFilter(),
      long_momentum_v5_bearish_divergence_ok: trueFilter(),
      long_momentum_v5_distance_from_day_low_ok: trueFilter(),
      long_momentum_v5_distance_above_vwap_ok: trueFilter(),
      long_momentum_v5_open_above_last_close_ok: trueFilter(),
      long_momentum_v5_last_bar_range_ok: trueFilter(),
      long_momentum_v5_day_high_chase_ok: trueFilter(),
      long_momentum_v5_trend_quality_ok: trueFilter(),
      long_momentum_v5_volume_expansion_ok: trueFilter(),
      long_momentum_v5_day_high_position_ok: trueFilter(),
      long_momentum_v5_early_move_ok: trueFilter(),
      long_momentum_v5_setup_open: trueFilter(),
      long_momentum_v5_entry_open: trueFilter(),
      long_momentum_entry_open: trueFilter(),
    });
  } else if (version === "v6") {
    const maxDrawdown = strategyNumberParam(params, "max_oracle_drawdown_before_best", 0.05);
    return [
      {
        filters: {
          oracle_long_enter_signal: trueFilter(),
          oracle_entry_score: gteFilter(strategyNumberParam(params, "min_oracle_entry_score", 0.7)),
          oracle_expected_profit: gteFilter(strategyNumberParam(params, "min_oracle_expected_profit", 0.01)),
          oracle_drawdown_before_best: gteFilter(-Math.abs(maxDrawdown), `>= -${Math.abs(maxDrawdown)}`),
          entry_open: trueFilter(),
        },
        label: "Apply v6 Strategy",
        title: "Apply all v6 oracle entry filters for the active strategy parameters.",
      },
    ];
  } else if (version === "v8") {
    Object.assign(filters, {
      long_momentum_v8_shock_watch_active: trueFilter(),
      long_momentum_v8_shock_confirmation_seen: trueFilter(),
      long_momentum_v8_liquidity_ok: trueFilter(),
      long_momentum_v8_price_acceptance_ok: trueFilter(),
      long_momentum_v8_trend_ok: trueFilter(),
      long_momentum_v8_entry_time_ok: trueFilter(),
      long_momentum_v8_entry_limit_ok: trueFilter(),
      long_momentum_v8_entry_trigger: trueFilter(),
      long_momentum_v8_initial_risk_pct: lteFilter(strategyNumberParam(params, "max_initial_risk_pct", 0.12)),
      long_momentum_v8_score: gteFilter(strategyNumberParam(params, "min_entry_score", 0.65)),
      long_momentum_v8_entry_open: trueFilter(),
      long_momentum_entry_open: trueFilter(),
    });
  } else {
    return undefined;
  }

  return [
    {
      filters,
      label: `Apply ${version} Strategy`,
      title: `Apply all available ${version} strategy filters for the active strategy parameters.`,
    },
  ];
}

function strategyNumberParam(params: Record<string, StrategyParamValue>, key: string, fallback: number): number {
  const value = Number(params[key]);
  return Number.isFinite(value) ? value : fallback;
}

function waitForDebugProgressPaint(): Promise<void> {
  return new Promise((resolve) => {
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => resolve());
    });
  });
}

type DebugStepCounts = {
  actions: number;
  fills: number;
  orders: number;
  pendingOrders: number;
  positions: number;
  rejections: number;
  requests: number;
  trades: number;
};

function debugSessionIsComplete(session: InteractiveDebugSession): boolean {
  return session.status === "complete" || session.step?.type === "complete";
}

function debugStepCounts(step: InteractiveDebugStep | null): DebugStepCounts {
  return {
    actions: step?.observability_trace?.length ?? 0,
    fills: step?.fills?.length ?? 0,
    orders: step?.orders?.length ?? 0,
    pendingOrders: step?.pending_orders?.length ?? 0,
    positions: step?.positions?.length ?? 0,
    rejections: step?.rejection_events?.length ?? 0,
    requests: step?.strategy_requests?.length ?? 0,
    trades: step?.trades?.length ?? 0
  };
}

function debugStepHasPrimaryAction(step: InteractiveDebugStep | null): boolean {
  const counts = debugStepCounts(step);
  return counts.actions > 0 || counts.requests > 0 || counts.orders > 0 || counts.fills > 0;
}

function buildInteractiveDebugMetrics(
  config: StrategyConfig,
  summary: Record<string, unknown>,
  rawScannerRows: number,
  watchlistRows: number,
  counts: DebugStepCounts
): NewRunMetric[] {
  const pnl = finiteNumber(summary.total_pnl);
  const realizedPnl = finiteNumber(summary.realized_pnl);
  const unrealizedPnl = finiteNumber(summary.open_unrealized_pnl);
  const equity = finiteNumber(summary.final_equity ?? config.initial_cash);
  return [
    {
      detail: "Mark-to-market P/L after this debug bar",
      icon: <Banknote size={15} />,
      label: "Total P/L",
      tone: signedTone(pnl),
      value: formatMoney(pnl)
    },
    {
      detail: "Closed-trade realized P/L after this debug bar",
      icon: <CircleDollarSign size={15} />,
      label: "Realized P/L",
      tone: signedTone(realizedPnl),
      value: formatMoney(realizedPnl)
    },
    {
      detail: "Open-position unrealized P/L after this debug bar",
      icon: <Activity size={15} />,
      label: "Unrealized P/L",
      tone: signedTone(unrealizedPnl),
      value: formatMoney(unrealizedPnl)
    },
    {
      detail: "Portfolio equity after this debug bar",
      icon: <Banknote size={15} />,
      label: "Equity",
      tone: signedTone(equity - config.initial_cash),
      value: formatMoney(equity)
    },
    {
      detail: "Open positions after this bar",
      icon: <Activity size={15} />,
      label: "Open Positions",
      tone: countTone(counts.positions),
      value: formatNumber(counts.positions)
    },
    {
      detail: "Orders still pending after this bar",
      icon: <ListChecks size={15} />,
      label: "Pending Orders",
      tone: countTone(counts.pendingOrders),
      value: formatNumber(counts.pendingOrders)
    },
    {
      detail: "Raw provider rows passed into the strategy for this timestamp",
      icon: <Database size={15} />,
      label: "Raw Scanner",
      tone: countTone(rawScannerRows),
      value: formatNumber(rawScannerRows)
    },
    {
      detail: "Strategy-maintained momentum watchlist rows at this timestamp",
      icon: <ListChecks size={15} />,
      label: "Watchlist",
      tone: countTone(watchlistRows),
      value: formatNumber(watchlistRows)
    },
    {
      detail: "Strategy trace actions emitted on this bar",
      icon: <Activity size={15} />,
      label: "Actions",
      tone: countTone(counts.actions),
      value: formatNumber(counts.actions)
    },
    {
      detail: "Order requests submitted by the strategy to the backtest engine",
      icon: <BarChart3 size={15} />,
      label: "Requests",
      tone: countTone(counts.requests),
      value: formatNumber(counts.requests)
    },
    {
      detail: "Backtest orders created or updated on this bar",
      icon: <Shield size={15} />,
      label: "Orders",
      tone: countTone(counts.orders),
      value: formatNumber(counts.orders)
    },
    {
      detail: "Backtest fills produced on this bar",
      icon: <Gauge size={15} />,
      label: "Fills",
      tone: countTone(counts.fills),
      value: formatNumber(counts.fills)
    },
    {
      detail: "Trades closed on this bar",
      icon: <Percent size={15} />,
      label: "Trades",
      tone: countTone(counts.trades),
      value: formatNumber(counts.trades)
    },
    {
      detail: "Strategy or engine rejection rows on this bar",
      icon: <StopCircle size={15} />,
      label: "Rejections",
      tone: counts.rejections > 0 ? "warning" : "neutral",
      value: formatNumber(counts.rejections)
    }
  ];
}

function DebugStepResponseSummary({ counts, step }: { counts: DebugStepCounts; step: InteractiveDebugStep | null }) {
  if (!step) {
    return <strong className="step-debug-response-title">Session ready. Press Next Bar to evaluate the first timestamp.</strong>;
  }
  if (step.type === "complete") {
    return <strong className="step-debug-response-title">Debug backtest is complete.</strong>;
  }
  const noAction = !debugStepHasPrimaryAction(step);
  return (
    <div className="step-debug-response-summary">
      <strong className="step-debug-response-title">{step.timestamp ? formatObservationTimestamp(step.timestamp) : "Current bar"}</strong>
      <span className="step-debug-response-badges" aria-label="Backtest response counts">
        <DebugCountBadge label="Actions" tone={counts.actions > 0 ? "info" : "neutral"} value={counts.actions} />
        <DebugCountBadge label="Requests" tone={counts.requests > 0 ? "warning" : "neutral"} value={counts.requests} />
        <DebugCountBadge label="Orders" tone={counts.orders > 0 ? "info" : "neutral"} value={counts.orders} />
        <DebugCountBadge label="Fills" tone={counts.fills > 0 ? "success" : "neutral"} value={counts.fills} />
        <DebugCountBadge label="Trades" tone={counts.trades > 0 ? "success" : "neutral"} value={counts.trades} />
        <DebugCountBadge label="Rejections" tone={counts.rejections > 0 ? "warning" : "neutral"} value={counts.rejections} />
      </span>
      {noAction ? <span className="step-debug-no-action">No strategy or backtest action on this bar.</span> : null}
    </div>
  );
}

function DebugStepCountBadges({ counts }: { counts: DebugStepCounts }) {
  return (
    <div className="step-debug-count-grid" aria-label="Debug state counts">
      <DebugCountBadge label="Open Positions" tone={counts.positions > 0 ? "info" : "neutral"} value={counts.positions} />
      <DebugCountBadge label="Pending Orders" tone={counts.pendingOrders > 0 ? "warning" : "neutral"} value={counts.pendingOrders} />
      <DebugCountBadge label="Closed Trades" tone={counts.trades > 0 ? "success" : "neutral"} value={counts.trades} />
      <DebugCountBadge label="Rejections" tone={counts.rejections > 0 ? "warning" : "neutral"} value={counts.rejections} />
    </div>
  );
}

function DebugCountBadge({ label, tone, value }: { label: string; tone: SemanticTone; value: number }) {
  return (
    <span className="step-debug-count-badge" data-tone={tone}>
      <span>{label}</span>
      <strong>{formatNumber(value)}</strong>
    </span>
  );
}

function relatedScannerRows(rows: DataRow[], trace: DataRow): DataRow[] {
  const ticker = normalizedTicker(rowText(trace, "ticker"));
  const sessionDate = rowText(trace, "session_date");
  const stage = rowText(trace, "stage");
  const traceTime = rowTime(trace);
  const sessionRows = rows.filter((row) => rowText(row, "session_date") === sessionDate);
  if (!sessionRows.length) return [];
  const preferredStage = stage === "setup_scanner" ? "setup_scanner" : "live_scanner";
  const stagedRows = sessionRows.filter((row) => rowText(row, "stage") === preferredStage);
  const snapshotRows = scannerSnapshotRowsAtTime(stagedRows.length ? stagedRows : sessionRows, traceTime);
  if (snapshotRows.length) return snapshotRows.sort(compareScannerRows);
  if (!ticker) return [];
  return sessionRows
    .filter((row) => normalizedTicker(rowText(row, "ticker")) === ticker && sameObservationStepTime(rowTime(row), traceTime))
    .sort(compareScannerRows);
}

function scannerSnapshotRowsAtTime(rows: DataRow[], traceTime: number): DataRow[] {
  if (!rows.length) return [];
  if (!Number.isFinite(traceTime)) return [];
  return rows.filter((row) => sameObservationStepTime(rowTime(row), traceTime));
}

const SCANNER_IMPORTANT_COLUMNS = [
  "timestamp",
  "ticker",
  "rank",
  "score",
  "score_key",
  "setup_rank",
  "setup_score",
  "momentum_score",
  "entry_open",
  "entry_state",
  "entry_rank",
  "trend_score",
  "macd_pressure_bps",
  "cumulative_macd_pressure_bps",
  "recent_macd_pressure_bps",
  "opening_macd_pressure_bps",
  "session_macd_pressure_bps",
  "session_return_bps",
  "recent_return_bps",
  "last_5m_return",
  "long_momentum_v9_last_5m_return",
  "long_momentum_v9_price_eligible",
  "long_momentum_v9_watchlist_add_open",
  "long_momentum_v9_watchlist_active",
  "long_momentum_v9_watchlist_added_timestamp",
  "long_momentum_v9_watchlist_added_last_close",
  "long_momentum_v9_watchlist_added_last_5m_return",
  "long_momentum_v9_watchlist_added_this_bar",
  "long_momentum_v9_watchlist_entry_ready",
  "long_momentum_v9_watchlist_entry_submitted",
  "long_momentum_v9_watchlist_last_entry_type",
  "long_momentum_v9_watchlist_last_state",
  "long_momentum_v9_watchlist_max_vwap",
  "long_momentum_v9_watchlist_avg_transactions",
  "long_momentum_v9_return_ok",
  "long_momentum_v9_watchlist_add_transactions_ok",
  "long_momentum_v9_immediate_transactions_vs_prior_3_ok",
  "long_momentum_v9_entry_time_ok",
  "long_momentum_v9_pending_symbol_order",
  "long_momentum_v9_no_symbol_position",
  "long_momentum_v9_close_minus_vwap",
  "long_momentum_v9_reentry_vwap_threshold",
  "long_momentum_v9_close_minus_reentry_vwap_threshold",
  "long_momentum_v9_reentry_price_reclaim",
  "long_momentum_v9_reentry_vwap_buffer_ok",
  "long_momentum_v9_reentry_last_bar_not_red",
  "long_momentum_v9_reentry_bvd_ok",
  "long_momentum_v9_reentry_bvd_score",
  "long_momentum_v9_reentry_body_break_ok",
  "long_momentum_v9_reentry_body_break_threshold",
  "long_momentum_v9_double_bvd_exit_red_ok",
  "long_momentum_v9_double_bvd_exit_open",
  "long_momentum_v9_immediate_entry_open",
  "long_momentum_v9_reentry_open",
  "long_momentum_v9_entry_open",
  "long_momentum_v9_reject_reason",
  "last_tema9",
  "last_tema20",
  "current_open_tema9",
  "current_open_tema20",
  "current_open",
  "last_open",
  "second_last_open",
  "second_last_close",
  "last_high",
  "last_low",
  "last_3_candle_low_price",
  "last_close",
  "last_2_body_high",
  "current_open_above_last_2_body_high",
  "current_open_above_last_body_high",
  "current_open_above_last_body_high_actual",
  "last_body_high",
  "last_body_low",
  "last_volume",
  "last_transactions",
  "last_transactions_avg_prior_3",
  "last_transactions_vs_prior_3",
  "last_return_5",
  "last_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence",
  "last_double_timeframe_bearish_volume_divergence_score",
  "last_double_timeframe_bearish_volume_divergence_label",
  "last_triple_bearish_volume_divergence",
  "last_triple_bearish_volume_divergence_score",
  "last_triple_bearish_volume_divergence_label",
  "last_return_1",
  "last_is_red",
  "last_tema_open",
  "last_macd_line",
  "last_macd_signal",
  "last_macd_hist",
  "last_macd_hist_z_since_open",
  "last_spread",
  "last_spread_bps_abs",
  "last_spread_bps_max",
  "last_quote_bid_size",
  "last_quote_ask_size",
  "last_minutes_since_premarket_start",
  "last_ideal_bars_since_premarket_start",
  "last_session_bar_count",
  "tema_spread_bps",
  "vwap_distance_bps",
  "recent_dollar_volume",
  "last_recent_volume_5",
  "last_recent_dollar_volume_5",
  "last_recent_transactions_5",
  "session_dollar_volume",
  "last_avg_trade_size",
  "last_max_fill_qty",
  "last_max_entry_qty",
  "last_max_exit_qty",
  "last_max_fill_qty_quote_ask",
  "last_max_fill_qty_quote_bid",
  "last_max_fill_qty_volume",
  "last_max_fill_qty_volume_last_bar",
  "last_max_fill_qty_volume_3bar",
  "last_max_fill_notional",
  "last_max_entry_notional",
  "last_max_exit_notional",
  "last_max_fill_notional_volume",
  "last_max_fill_notional_volume_last_bar",
  "last_max_fill_notional_volume_3bar",
  "long_momentum_spread_ok",
  "long_momentum_setup_price_ok",
  "long_momentum_setup_activity_ok",
  "long_momentum_setup_quote_ok",
  "long_momentum_setup_trend_ok",
  "long_momentum_setup_exhaustion_ok",
  "long_momentum_setup_open",
  "active_setup_body_high",
  "body_break_threshold",
  "trigger_1_time_ok",
  "trigger_1_threshold",
  "long_momentum_body_break_entry_open",
  "long_momentum_v4_body_break_entry_open",
  "long_momentum_v4_pullback_reclaim_entry_open",
  "long_momentum_v4_entry_open",
  "long_momentum_v5_price_above_vwap",
  "long_momentum_v5_tema_stack_ok",
  "long_momentum_v5_tema_spread_ok",
  "long_momentum_v5_macd_line_positive",
  "long_momentum_v5_macd_hist_ok",
  "long_momentum_v5_above_day_open",
  "long_momentum_v5_near_enough_day_high",
  "long_momentum_v5_volume_vs_avg_so_far_ok",
  "long_momentum_v5_volume_vs_recent_3_ok",
  "long_momentum_v5_bearish_divergence_ok",
  "long_momentum_v5_trend_quality_ok",
  "long_momentum_v5_volume_expansion_ok",
  "long_momentum_v5_distance_from_day_low_ok",
  "long_momentum_v5_distance_above_vwap_ok",
  "long_momentum_v5_open_above_last_close_ok",
  "long_momentum_v5_last_bar_range_ok",
  "long_momentum_v5_close_location_ok",
  "long_momentum_v5_day_high_chase_ok",
  "long_momentum_v5_day_high_position_ok",
  "long_momentum_v5_early_move_ok",
  "long_momentum_v5_setup_open",
  "long_momentum_v5_entry_time_ok",
  "long_momentum_v5_entry_threshold",
  "long_momentum_v5_early_uptrend_entry_open",
  "long_momentum_v5_entry_open",
  "long_momentum_v2_macd_line_or_vwap_ok",
  "long_momentum_v2_entry_open",
  "long_momentum_early_body_break_entry_open",
  "long_momentum_entry_open",
  "price_trend_bps",
  "live_rank",
  "live_score",
  "scanner_status",
  "status",
  "reason_code",
  "reject_reason",
  "reason",
  "price",
  "trigger",
  "stop",
  "box_high",
  "box_mid",
  "box_low",
  "box_close",
  "box_range",
  "box_strength",
  "box_range_pct",
  "box_volume",
  "box_dollar_volume",
  "volume_score",
  "liquidity_score",
  "ideal_range_score",
  "close_location",
  "body_to_range",
  "macd_hist_5m",
  "macd_line_5m",
  "macd_signal_5m",
  "tema9_5m",
  "tema20_5m",
  "breakout_extension_pct",
  "total_candidates",
  "captured_candidates",
  "stage",
  "session_date",
  "session_index",
] as const;

function flattenScannerRows(rows: DataRow[]): DataRow[] {
  return rows.map((row) => {
    const values = parseObservationJson(row.values_json);
    const flattened: DataRow = {};
    for (const key of SCANNER_IMPORTANT_COLUMNS) {
      if (key in row) {
        flattened[key] = row[key];
      } else if (key in values) {
        flattened[key] = normalizeObservationTableValue(values[key]);
      }
    }
    for (const [key, value] of Object.entries(row)) {
      if (key === "values_json" || key in flattened) continue;
      flattened[key] = value;
    }
    for (const [key, value] of Object.entries(values)) {
      const flattenedKey = key in flattened ? `candidate_${key}` : key;
      flattened[flattenedKey] = normalizeObservationTableValue(value);
    }
    return flattened;
  });
}

function scannerSnapshotColumns(rows: DataRow[]): string[] {
  const availableColumns = Array.from(new Set(rows.flatMap((row) => Object.keys(row))));
  const preferredColumns = SCANNER_IMPORTANT_COLUMNS.filter((column) => availableColumns.includes(column));
  const remainingColumns = availableColumns.filter((column) => !preferredColumns.includes(column as typeof SCANNER_IMPORTANT_COLUMNS[number]));
  return [...preferredColumns, ...remainingColumns];
}

function sortScannerSnapshotRows(rows: DataRow[]): DataRow[] {
  return [...rows].sort(compareScannerRows);
}

function normalizeObservationTableValue(value: unknown): unknown {
  if (value === undefined || value === null) return "";
  if (Array.isArray(value) || typeof value === "object") return JSON.stringify(value);
  return value;
}

function relatedStateRows(rows: DataRow[], trace: DataRow): DataRow[] {
  const ticker = normalizedTicker(rowText(trace, "ticker"));
  const sessionDate = rowText(trace, "session_date");
  const traceTime = rowTime(trace);
  return rows
    .filter((row) => {
      if (rowText(row, "session_date") !== sessionDate) return false;
      const rowTicker = normalizedTicker(rowText(row, "ticker"));
      const rowTimeValue = rowTime(row);
      if (!sameObservationStepTime(rowTimeValue, traceTime)) return false;
      return !ticker || !rowTicker || rowTicker === ticker;
    })
    .sort(compareEvidenceRows);
}

function relatedSymbolActionRows(rows: DataRow[], trace: DataRow, timeKeys: string[]): DataRow[] {
  const ticker = normalizedTicker(rowText(trace, "ticker"));
  const sessionDate = rowText(trace, "session_date");
  const traceTime = rowTime(trace);
  if (!ticker) return [];
  return rows
    .filter((row) => {
      if (normalizedTicker(rowText(row, "symbol") || rowText(row, "ticker")) !== ticker) return false;
      if (rowSessionDate(row, timeKeys) !== sessionDate) return false;
      return timeKeys.some((key) => sameObservationStepTime(rowTimeForKey(row, key), traceTime));
    })
    .sort(compareEvidenceRows);
}

function relatedTradeActionRows(rows: DataRow[], trace: DataRow): DataRow[] {
  const ticker = normalizedTicker(rowText(trace, "ticker"));
  const sessionDate = rowText(trace, "session_date");
  const traceTime = rowTime(trace);
  if (!ticker) return [];
  return rows
    .filter((row) => {
      if (normalizedTicker(rowText(row, "symbol") || rowText(row, "ticker")) !== ticker) return false;
      if (rowSessionDate(row, ["exit_time"]) !== sessionDate) return false;
      return sameObservationStepTime(rowTimeForKey(row, "exit_time"), traceTime);
    })
    .sort(compareEvidenceRows);
}

function parseObservationJson(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as Record<string, unknown> : {};
  } catch {
    return {};
  }
}

function objectToObservationFields(value: Record<string, unknown>): ObservationFieldValue[] {
  return Object.entries(value)
    .filter(([, fieldValue]) => fieldValue !== undefined && fieldValue !== null && fieldValue !== "")
    .slice(0, 24)
    .map(([key, fieldValue]) => ({ key, label: formatObservationLabel(key), value: fieldValue }));
}

function primaryObservationFields(action: ObservabilityAction): ObservationFieldValue[] {
  return [
    { key: "decision", label: "Decision", value: action.decision || "observed" },
    { key: "stage", label: "Stage", value: formatObservationLabel(action.stage) || "-" },
    { key: "reason", label: "Reason", value: action.reason || action.reasonCode || "-" },
    { key: "timestamp", label: "Time", value: formatObservationTimestamp(action.timestamp || action.sessionDate) },
  ];
}

function observationActionPreviewFields(action: ObservabilityAction): ObservationFieldValue[] {
  const merged = [...action.inputFields, ...action.stateFields];
  return prioritizedObservationFields(merged, "preview").slice(0, 3);
}

function observationTagDetailSegments(field: ObservationFieldValue): Array<{ label: string; value: string }> {
  const formattedValue = formatObservationValue(field.value, field.label);
  const parts = formattedValue
    .split("|")
    .map((part) => part.trim())
    .filter(Boolean);
  return parts.map((part, index) => {
    const splitIndex = findObservationTagSegmentDivider(part);
    if (splitIndex > 0) {
      return {
        label: formatObservationLabel(part.slice(0, splitIndex).trim()),
        value: part.slice(splitIndex + 1).trim() || "-"
      };
    }
    return {
      label: index === 0 ? "Tag" : `Field ${formatNumber(index)}`,
      value: part
    };
  });
}

function observationFieldIsStructuredTag(field: ObservationFieldValue): boolean {
  const normalized = `${field.key} ${field.label}`.toLowerCase();
  const formattedValue = formatObservationValue(field.value, field.label);
  return normalized.includes("tag") && formattedValue.includes("|");
}

function findObservationTagSegmentDivider(segment: string) {
  const equalsIndex = segment.indexOf("=");
  const colonIndex = segment.indexOf(":");
  if (equalsIndex < 0) return colonIndex;
  if (colonIndex < 0) return equalsIndex;
  return Math.min(equalsIndex, colonIndex);
}

function prioritizedObservationFields(fields: ObservationFieldValue[], group: "input" | "preview" | "state"): ObservationFieldValue[] {
  return [...fields]
    .filter((field) => field.value !== undefined && field.value !== null && field.value !== "")
    .sort((left, right) => observationFieldPriority(left.key, group) - observationFieldPriority(right.key, group));
}

function observationFieldPriority(key: string, group: "input" | "preview" | "state"): number {
  const normalized = key.toLowerCase();
  const priorityGroups = {
    input: [
      ["setup_score", "score", "rank"],
      ["entry_price", "entry_trigger", "trigger", "price", "current_open", "last_close"],
      ["stop_price", "stop"],
      ["range_high", "range_low", "orb", "range"],
      ["last_volume", "volume", "relative_volume", "rvol"],
      ["macd", "momentum"],
    ],
    preview: [
      ["setup_score", "score", "rank"],
      ["entry_price", "exit_price", "fill_price", "price", "current_open", "last_close"],
      ["stop_price", "stop"],
      ["quantity", "qty", "position_size", "position"],
      ["pnl", "fees", "commission"],
      ["range_high", "range_low", "orb", "range"],
    ],
    state: [
      ["position", "quantity", "qty", "shares"],
      ["entry_price", "avg_price", "price"],
      ["stop_price", "stop"],
      ["unrealized", "pnl"],
      ["cash", "equity"],
      ["orders", "fills", "trades"],
    ],
  } satisfies Record<typeof group, string[][]>;
  const matchedGroup = priorityGroups[group].findIndex((terms) => terms.some((term) => normalized.includes(term)));
  return matchedGroup >= 0 ? matchedGroup : 100;
}

function observationFieldSemanticTone(field: ObservationFieldValue): SemanticTone | null {
  const key = field.key.toLowerCase();
  if (!["decision", "exit_reason", "reason", "side", "state", "status"].some((term) => key.includes(term))) return null;
  const value = String(field.value ?? "").trim();
  return value ? observabilitySemanticTone(value, key) : null;
}

function observabilitySemanticTone(value: string, context = ""): SemanticTone {
  const normalized = `${context} ${value}`.toLowerCase();
  if (normalized.includes("reject") || normalized.includes("failed") || normalized.includes("error") || normalized.includes("blocked")) return "danger";
  if (normalized.includes("sell") || normalized.includes("stop") || normalized.includes("loss")) return "danger";
  if (normalized.includes("buy") || normalized.includes("filled") || normalized.includes("complete")) return "success";
  if (normalized.includes("cancel") || normalized.includes("skip") || normalized.includes("partial")) return "warning";
  if (normalized.includes("submit") || normalized.includes("pending") || normalized.includes("open") || normalized.includes("entry")) return "info";
  if (normalized.includes("closed") || normalized.includes("market")) return "muted";
  return "neutral";
}

function observationDecisionTone(decision: string, eventType: string): SemanticTone {
  const normalized = `${decision} ${eventType}`.toLowerCase();
  if (normalized.includes("reject") || normalized.includes("skip") || normalized.includes("blocked")) return "danger";
  if (normalized.includes("cancel")) return "warning";
  if (normalized.includes("submit") || normalized.includes("entry")) return "success";
  if (normalized.includes("exit")) return "info";
  return observabilitySemanticTone(decision, eventType);
}

function formatObservationActionTitle(eventType: string, decision: string): string {
  if (eventType === "candidate_rejected" || eventType === "candidate_skipped") return "Candidate Skipped";
  const label = formatObservationLabel(eventType || decision || "action");
  return label || "Action";
}

function normalizeObservationReason(reason: string, eventType: string): string {
  if (eventType === "candidate_rejected") return reason.replace(/^Candidate rejected by /i, "Candidate skipped by ");
  return reason;
}

function formatObservationLabel(value: string): string {
  return String(value || "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .replace(/\b\w/g, (match) => match.toUpperCase());
}

function formatObservationValue(value: unknown, key: string): string {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    const normalized = key.toLowerCase();
    if (normalized.includes("price") || normalized.includes("pnl") || normalized.includes("cash") || normalized.includes("equity") || normalized.includes("fee") || normalized.includes("stop") || normalized.includes("trigger")) {
      return formatMoney(value);
    }
    return formatNumber(value, Number.isInteger(value) ? 0 : 4);
  }
  if (Array.isArray(value)) return value.join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  if (observationValueLooksLikeTime(String(value), key)) return formatObservationTimestamp(String(value));
  return String(value);
}

function observationValueLooksLikeTime(value: string, key: string): boolean {
  const normalizedKey = key.toLowerCase();
  if (!/(time|timestamp|created_at|filled_at|entry_time|exit_time|bar_time)/.test(normalizedKey)) return false;
  return Number.isFinite(parseObservationTimestampMs(value));
}

function formatObservationTimestamp(value: string): string {
  if (!value) return "";
  const dateOnlyMatch = value.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (dateOnlyMatch) {
    const [, year, month, day] = dateOnlyMatch;
    return new Intl.DateTimeFormat("en-US", {
      day: "numeric",
      month: "short",
      timeZone: "UTC",
      year: "numeric",
    }).format(new Date(Date.UTC(Number(year), Number(month) - 1, Number(day), 12)));
  }
  const parsed = parseObservationTimestampMs(value);
  if (!Number.isFinite(parsed)) return value;
  const formatter = new Intl.DateTimeFormat("en-US", {
    day: "numeric",
    hour: "numeric",
    hour12: true,
    minute: "2-digit",
    month: "short",
    timeZone: "America/New_York",
    year: "numeric",
  });
  return `${formatter.format(new Date(parsed))} ET`;
}

function compareEvidenceRows(left: DataRow, right: DataRow): number {
  const timeDiff = rowTime(right) - rowTime(left);
  if (timeDiff) return timeDiff;
  return Number(left.rank ?? 0) - Number(right.rank ?? 0);
}

function compareScannerRows(left: DataRow, right: DataRow): number {
  const leftRank = Number(left.rank ?? Number.POSITIVE_INFINITY);
  const rightRank = Number(right.rank ?? Number.POSITIVE_INFINITY);
  if (leftRank !== rightRank) return leftRank - rightRank;
  return Number(right.score ?? 0) - Number(left.score ?? 0);
}

function rowTime(row: DataRow): number {
  const value = rowText(row, "timestamp") || rowText(row, "filled_at") || rowText(row, "created_at") || rowText(row, "entry_time") || rowText(row, "exit_time") || rowText(row, "bar_time_market");
  const parsed = parseObservationTimestampMs(value);
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function rowTimeForKey(row: DataRow, key: string): number {
  const parsed = parseObservationTimestampMs(rowText(row, key));
  return Number.isFinite(parsed) ? parsed : Number.NaN;
}

function sameObservationStepTime(left: number, right: number): boolean {
  return Number.isFinite(left) && Number.isFinite(right) && Math.abs(left - right) < 1000;
}

function normalizeTimestampString(value: string): string {
  return value.includes("T") ? value : value.replace(" ", "T");
}

function parseObservationTimestampMs(value: string): number {
  if (!value) return Number.NaN;
  const normalized = normalizeTimestampString(value);
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(normalized)) return Date.parse(normalized);
  const match = normalized.match(/^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?/);
  if (!match) return Date.parse(normalized);
  const [, year, month, day, hour, minute, second = "0"] = match;
  return exchangeLocalTimeToUtcMs(
    Number(year),
    Number(month),
    Number(day),
    Number(hour),
    Number(minute),
    Number(second)
  );
}

function exchangeLocalTimeToUtcMs(year: number, month: number, day: number, hour: number, minute: number, second: number): number {
  const utcGuess = Date.UTC(year, month - 1, day, hour, minute, second);
  const offset = timeZoneOffsetMs(new Date(utcGuess), "America/New_York");
  return utcGuess - offset;
}

function timeZoneOffsetMs(date: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    second: "2-digit",
    timeZone,
    year: "numeric",
  }).formatToParts(date);
  const value = (type: string) => Number(parts.find((part) => part.type === type)?.value ?? 0);
  const zonedAsUtc = Date.UTC(value("year"), value("month") - 1, value("day"), value("hour"), value("minute"), value("second"));
  return zonedAsUtc - date.getTime();
}

function rowSessionDate(row: DataRow, timeKeys: string[]): string {
  const explicit = rowText(row, "session_date");
  if (explicit) return explicit.slice(0, 10);
  for (const key of timeKeys) {
    const value = rowText(row, key);
    if (value.length >= 10) return value.slice(0, 10);
  }
  return "";
}

function rowText(row: DataRow, key: string): string {
  const value = row[key];
  return value === null || value === undefined ? "" : String(value);
}

function normalizedTicker(value: string): string {
  return value.trim().toUpperCase();
}

function buildLiveBacktestMetrics(job: Record<string, unknown> | null, detail: RunDetailPayload | null): NewRunMetric[] {
  const summary = liveSummary(job, detail);
  const totalPnl = finiteNumber(summary.total_pnl);
  const avgDailyPnl = finiteNumber(summary.avg_daily_pnl);
  const avgMonthlyPnl = finiteNumber(summary.avg_monthly_pnl);
  const premarketPnl = finiteNumber(summary.premarket_pnl);
  const marketOpenPnl = finiteNumber(summary.market_open_pnl);
  const afterMarketPnl = finiteNumber(summary.after_market_pnl);
  const returnPct = finiteNumber(summary.return_pct);
  const sharpe = finiteNumber(summary.sharpe_ratio);
  const maxDrawdownPct = finiteNumber(summary.max_drawdown_pct);
  const maxDrawdown = finiteNumber(summary.max_drawdown);
  const tradeCount = finiteNumber(summary.trade_count);
  const winRate = finiteNumber(summary.win_rate);
  const profitFactor = finiteNumber(summary.profit_factor);
  const totalFees = finiteNumber(summary.total_fees);
  const unrealized = finiteNumber(summary.open_unrealized_pnl);
  const maxUnrealizedGain = finiteNumber(summary.max_open_unrealized_pnl);
  const maxUnrealizedLoss = finiteNumber(summary.max_open_unrealized_loss);

  return [
    {
      detail: "Mark-to-market net P/L",
      icon: <Banknote size={15} />,
      label: "P/L",
      tone: signedTone(totalPnl),
      value: formatMoney(totalPnl)
    },
    {
      detail: "Average mark-to-market P/L per completed session",
      icon: <BarChart3 size={15} />,
      label: "Avg Day",
      tone: signedTone(avgDailyPnl),
      value: formatMoney(avgDailyPnl)
    },
    {
      detail: "Average mark-to-market P/L per calendar month in this run",
      icon: <BarChart3 size={15} />,
      label: "Avg Month",
      tone: signedTone(avgMonthlyPnl),
      value: formatMoney(avgMonthlyPnl)
    },
    {
      detail: "Total return on equity",
      icon: <Percent size={15} />,
      label: "Return",
      tone: signedTone(returnPct),
      value: formatPct(returnPct)
    },
    {
      detail: "Annualized from live equity returns",
      icon: <Activity size={15} />,
      label: "Sharpe",
      tone: sharpeTone(sharpe),
      value: formatNumber(sharpe, 2)
    },
    {
      detail: `${formatMoney(maxDrawdown)} peak-to-trough`,
      icon: <Gauge size={15} />,
      label: "Max DD",
      tone: drawdownTone(maxDrawdownPct),
      value: formatPct(maxDrawdownPct)
    },
    {
      detail: sessionPnlDetail(summary, "premarket"),
      icon: <Activity size={15} />,
      label: "Premkt P/L",
      tone: signedTone(premarketPnl),
      value: formatMoney(premarketPnl)
    },
    {
      detail: sessionPnlDetail(summary, "market_open"),
      icon: <Activity size={15} />,
      label: "Market P/L",
      tone: signedTone(marketOpenPnl),
      value: formatMoney(marketOpenPnl)
    },
    {
      detail: sessionPnlDetail(summary, "after_market"),
      icon: <Activity size={15} />,
      label: "After P/L",
      tone: signedTone(afterMarketPnl),
      value: formatMoney(afterMarketPnl)
    },
    {
      detail: "Closed trades",
      icon: <ListChecks size={15} />,
      label: "Trades",
      tone: countTone(tradeCount),
      value: formatNumber(tradeCount)
    },
    {
      detail: "Winning closed trades",
      icon: <Percent size={15} />,
      label: "Win %",
      tone: winRateTone(winRate, tradeCount),
      value: formatPct(winRate)
    },
    {
      detail: "Gross profit / gross loss",
      icon: <Shield size={15} />,
      label: "PF",
      tone: profitFactorTone(profitFactor, tradeCount),
      value: formatNumber(profitFactor, 2)
    },
    {
      detail: "Estimated commissions and fees",
      icon: <Banknote size={15} />,
      label: "Fees",
      tone: "danger",
      value: formatMoney(totalFees)
    },
    {
      detail: "Current unrealized P/L",
      icon: <Banknote size={15} />,
      label: "Open",
      tone: signedTone(unrealized),
      value: formatMoney(unrealized)
    },
    {
      detail: "Worst / best unrealized",
      icon: <Gauge size={15} />,
      label: "Max Unrealized",
      tone: unrealizedRangeTone(maxUnrealizedLoss, maxUnrealizedGain),
      value: <UnrealizedRangeValue gain={maxUnrealizedGain} loss={maxUnrealizedLoss} />
    }
  ];
}

function sessionPnlDetail(summary: Record<string, unknown>, segment: "after_market" | "market_open" | "premarket") {
  const label = segment === "premarket" ? "Premarket" : segment === "market_open" ? "Market hours" : "After-hours";
  return `${label} mark-to-market P/L. Avg/day ${formatMoney(finiteNumber(summary[`${segment}_avg_daily_pnl`]))}; avg/month ${formatMoney(finiteNumber(summary[`${segment}_avg_monthly_pnl`]))}.`;
}

function UnrealizedRangeValue({ gain, loss }: { gain: number; loss: number }) {
  return (
    <span className="new-run-metric-range">
      <span className={loss < 0 ? "new-run-metric-range-loss" : undefined}>{formatCompactMoney(loss)}</span>
      <span className="new-run-metric-range-divider">/</span>
      <span className={gain > 0 ? "new-run-metric-range-gain" : undefined}>{formatSignedCompactMoney(gain)}</span>
    </span>
  );
}

function liveSummary(job: Record<string, unknown> | null, detail: RunDetailPayload | null): Record<string, unknown> {
  const isLiveRun = ["running", "queued", "canceling"].includes(String(job?.status ?? "").toLowerCase());
  const jobSummary = job?.summary && typeof job.summary === "object" ? job.summary as Record<string, unknown> : null;
  if (isLiveRun && jobSummary) return jobSummary;
  if (detail?.summary && Object.keys(detail.summary).length > 0) return detail.summary;
  const metadataSummary = detail?.metadata?.summary;
  if (metadataSummary && typeof metadataSummary === "object") return metadataSummary as Record<string, unknown>;
  if (jobSummary) return jobSummary;
  return {};
}

function signedTone(value: number): NewRunMetricTone {
  if (value > 0) return "success";
  if (value < 0) return "danger";
  return "neutral";
}

function sharpeTone(value: number): NewRunMetricTone {
  if (value >= 1) return "success";
  if (value >= 0.3) return "warning";
  if (value < 0) return "danger";
  return "neutral";
}

function drawdownTone(value: number): NewRunMetricTone {
  if (value >= 0.1) return "danger";
  if (value > 0) return "warning";
  return "neutral";
}

function unrealizedRangeTone(loss: number, gain: number): NewRunMetricTone {
  if (loss < 0 && gain > Math.abs(loss)) return "success";
  if (loss < 0) return "danger";
  if (gain > 0) return "success";
  return "neutral";
}

function formatCompactMoney(value: number): string {
  if (!Number.isFinite(value)) return "-";
  const useCompact = Math.abs(value) >= 1000;
  return value.toLocaleString(undefined, {
    currency: "USD",
    maximumFractionDigits: useCompact ? 1 : 0,
    minimumFractionDigits: 0,
    notation: useCompact ? "compact" : "standard",
    style: "currency"
  });
}

function formatSignedCompactMoney(value: number): string {
  if (value > 0) return `+${formatCompactMoney(value)}`;
  return formatCompactMoney(value);
}

function countTone(value: number): NewRunMetricTone {
  return value > 0 ? "info" : "neutral";
}

function winRateTone(value: number, tradeCount: number): NewRunMetricTone {
  if (tradeCount <= 0) return "neutral";
  if (value >= 0.5) return "success";
  if (value >= 0.4) return "warning";
  return "danger";
}

function profitFactorTone(value: number, tradeCount: number): NewRunMetricTone {
  if (tradeCount <= 0 || value <= 0) return "neutral";
  if (value >= 1.5) return "success";
  if (value >= 1) return "warning";
  return "danger";
}

function buildBacktestProgress(job: Record<string, unknown> | null, detail: RunDetailPayload | null, config: StrategyConfig) {
  const status = String(job?.status ?? detail?.metadata.status ?? "not started").replaceAll("_", " ");
  const normalizedStatus = status.toLowerCase();
  const barDone = finiteNumber(job?.processed_event_bars ?? detail?.metadata.processed_event_bars);
  const barTotal = finiteNumber(job?.total_event_bars ?? detail?.metadata.total_event_bars);
  if (barTotal > 0) {
    const done = Math.min(barTotal, Math.max(0, barDone));
    return {
      currentSession: String(job?.current_session ?? detail?.metadata.latest_session ?? ""),
      done,
      label: "",
      meterStatus: normalizedStatus === "not started" ? "queued" : status,
      percent: normalizedStatus.includes("complete") ? 100 : (done / barTotal) * 100,
      status,
      total: Math.max(1, barTotal),
      unitLabel: `${String(job?.progress_unit ?? detail?.metadata.progress_unit ?? "event")} bars`
    };
  }
  const eventCount = Array.isArray(job?.events) ? (job?.events as unknown[]).filter(Boolean).length : 0;
  const completed = finiteNumber(detail?.metadata.completed_sessions ?? eventCount);
  const totalFromMetadata = finiteNumber(detail?.metadata.total_sessions);
  const total = totalFromMetadata > 0 ? totalFromMetadata : estimateCalendarDays(config.start_date, config.end_date);
  const done = Math.min(total, Math.max(0, completed));
  const percent = normalizedStatus.includes("complete") ? 100 : total > 0 ? (done / total) * 100 : 0;
  return {
    currentSession: String(job?.current_session ?? detail?.metadata.latest_session ?? ""),
    done,
    label: "",
    meterStatus: normalizedStatus === "not started" ? "queued" : status,
    percent,
    status,
    total: Math.max(1, total),
    unitLabel: "sessions"
  };
}

function finiteNumber(value: unknown): number {
  const numeric = Number(value ?? 0);
  return Number.isFinite(numeric) ? numeric : 0;
}

function estimateCalendarDays(start: string, end: string) {
  const startParts = parseIsoDate(start);
  const endParts = parseIsoDate(end);
  if (!startParts || !endParts) return 1;
  return Math.max(1, Math.round((endParts.date.getTime() - startParts.date.getTime()) / 86_400_000) + 1);
}

function fetchRunDetail(runId: string, outputRoot: string, includeTables = true) {
  return api<RunDetailPayload>(
    `/api/backtests/runs/${runId}${query({ include_logs: includeTables, include_tables: includeTables, output_root: outputRoot })}`
  );
}

function mergeLiveRunDetail(current: RunDetailPayload | null, next: RunDetailPayload): RunDetailPayload {
  if (!current) return next;
  return {
    ...next,
    logs: next.logs || current.logs,
    tables: current.tables
  };
}

function PnlCandleChart({ payload, runName, title }: { payload?: PortfolioCandlePayload | null; runName: string; title: string }) {
  const availableTimeframes = useMemo(() => portfolioChartTimeframes(payload), [payload]);
  const defaultTimeframe = availableTimeframes.includes(payload?.default_timeframe ?? "") ? String(payload?.default_timeframe) : availableTimeframes[0] ?? "1h";
  const [timeframe, setTimeframe] = useState(defaultTimeframe);
  const periodBounds = useMemo(() => portfolioChartPeriodBounds(payload, availableTimeframes), [availableTimeframes, payload]);
  const [period, setPeriod] = useState({ end: periodBounds.end, start: periodBounds.start });

  useEffect(() => {
    setTimeframe(defaultTimeframe);
  }, [defaultTimeframe]);

  useEffect(() => {
    setPeriod({ end: periodBounds.end, start: periodBounds.start });
  }, [periodBounds.end, periodBounds.start]);

  const chartPayload = useMemo(
    () => portfolioChartPayload(payload, timeframe, period.start, period.end),
    [payload, period.end, period.start, timeframe]
  );

  function updatePeriod(start: string, end: string) {
    setPeriod(start <= end ? { start, end } : { start: end, end: start });
  }

  return (
    <section className="pnl-candle-chart">
      <div className="toolbar" style={{ justifyContent: "space-between" }}>
        <h2 style={{ margin: 0 }}>{title}</h2>
      </div>
      <ChartPanel
        emptyMessage="No portfolio P/L candles have been written yet."
        featureOptions={[]}
        indicatorOptions={[]}
        normalizeTicker={false}
        onPeriodChange={updatePeriod}
        onTickerChange={() => undefined}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={() => undefined}
        onVisibleSupervisionGroupsChange={() => undefined}
        payload={chartPayload}
        periodEnd={period.end}
        periodMax={periodBounds.max}
        periodMin={periodBounds.min}
        periodStart={period.start}
        showIndicatorControls={false}
        showSupervisionControls={false}
        ticker={runName || "Backtest"}
        tickerInputWidth={180}
        tickerMaxLength={64}
        timeframe={timeframe}
        timeframes={availableTimeframes}
        visibleColumns={[]}
        visibleSupervisionGroups={[]}
      />
    </section>
  );
}

function TradeTickerChart({
  outputRoot,
  runId,
  selectedTrade,
  trades
}: {
  outputRoot: string;
  runId: string;
  selectedTrade: DataRow;
  trades: DataRow[];
}) {
  const target = tradeChartTarget(selectedTrade);
  return <StrategySymbolChart outputRoot={outputRoot} runId={runId} selectedTrade={selectedTrade} target={target} trades={trades} />;
}

function StrategySymbolChart({
  outputRoot,
  runId,
  selectedTrade,
  target,
  trades
}: {
  outputRoot: string;
  runId: string;
  selectedTrade?: DataRow;
  target: ObservationChartTarget;
  trades: DataRow[];
}) {
  const symbol = target.symbol;
  const selectedKey = selectedTrade ? tradeRowKey(selectedTrade) : "";
  const [payload, setPayload] = useState<RunSymbolChartPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [timeframe, setTimeframe] = useState("1m");
  const selectedDay = useMemo(() => observationTargetMarketDate(target) || tradeMarketDate(selectedTrade), [selectedTrade, target]);
  const sameSymbolTrades = useMemo(() => {
    const payloadTrades = payload?.trades?.length ? payload.trades : trades;
    return payloadTrades.filter((trade) => tradeSymbol(trade) === symbol && (!selectedDay || tradeMarketDate(trade) === selectedDay));
  }, [payload?.trades, selectedDay, symbol, trades]);
  const availableTimeframes = useMemo(() => symbolChartTimeframes(payload), [payload]);
  const chartPayload = useMemo(() => symbolTradeChartPayload(payload, sameSymbolTrades, selectedKey, timeframe), [payload, sameSymbolTrades, selectedKey, timeframe]);
  const defaultVisibleColumns = useMemo(() => strategyVisibleColumns(chartPayload, payload), [chartPayload, payload]);
  const [visibleColumns, setVisibleColumns] = useState<string[]>([]);
  const [customVisibleColumns, setCustomVisibleColumns] = useState(false);
  const reference = useMemo(() => selectedSymbolReference(target, selectedTrade), [selectedTrade, target]);
  const periodBounds = useMemo(() => symbolChartPeriodBounds(payload, timeframe, selectedDay), [payload, selectedDay, timeframe]);
  const [period, setPeriod] = useState({ end: periodBounds.end, start: periodBounds.start });
  const indicatorOptions = chartPayload?.options?.standard_indicators ?? [];
  const featureOptions = chartPayload?.options?.feature_columns ?? [];
  const displayItemOptions = chartPayload?.options?.display_items ?? [];
  const catalogColumns = payload?.catalog_columns ?? [];
  const selectionContext = `${symbol}|${timeframe}`;
  const [visibleSelectionContext, setVisibleSelectionContext] = useState("");
  const displayItemsRequest = customVisibleColumns ? (visibleColumns.length ? visibleColumns.join(",") : CHART_DISPLAY_ITEMS_NONE) : undefined;
  const displayItemsRequestKey = displayItemsRequest ?? "";

  useEffect(() => {
    const next = payload?.default_timeframe || availableTimeframes[0] || "1m";
    setTimeframe((current) => (availableTimeframes.includes(current) ? current : next));
  }, [availableTimeframes, payload?.default_timeframe]);

  useEffect(() => {
    if (!defaultVisibleColumns.length) return;
    if (visibleSelectionContext !== selectionContext) {
      setVisibleColumns(defaultVisibleColumns);
      setCustomVisibleColumns(false);
      setVisibleSelectionContext(selectionContext);
    } else if (!visibleColumns.length && !customVisibleColumns) {
      setVisibleColumns(defaultVisibleColumns);
    }
  }, [customVisibleColumns, defaultVisibleColumns.join("|"), selectionContext, visibleColumns.length, visibleSelectionContext]);

  useEffect(() => {
    setPeriod({ end: periodBounds.end, start: periodBounds.start });
  }, [periodBounds.end, periodBounds.start]);

  useEffect(() => {
    if (!runId || !symbol) {
      setPayload(null);
      setError(null);
      return;
    }
    let canceled = false;
    setLoading(true);
    api<RunSymbolChartPayload>(`/api/backtests/runs/${runId}/symbols/${encodeURIComponent(symbol)}/chart${query({ display_items: displayItemsRequest, output_root: outputRoot, timeframe })}`)
      .then((nextPayload) => {
        if (canceled) return;
        setPayload(nextPayload);
        setError(null);
      })
      .catch((err) => {
        if (!canceled) setError(err instanceof Error ? err.message : String(err));
      })
      .finally(() => {
        if (!canceled) setLoading(false);
      });
    return () => {
      canceled = true;
    };
  }, [displayItemsRequestKey, outputRoot, runId, symbol, timeframe]);

  function updatePeriod(start: string, end: string) {
    setPeriod(start <= end ? { start, end } : { start: end, end: start });
  }

  const filteredPayload = useMemo(() => {
    if (!chartPayload) return null;
    const candles = chartPayload.candles.filter((candle) => candleInChartPeriod(candle, period.start, period.end));
    const visibleTimes = new Set(candles.map((candle) => candle.time));
    return {
      ...chartPayload,
      candles,
      markers: chartPayload.markers.filter((marker) => visibleTimes.has(Number(marker.time))),
      overlay_series: chartPayload.overlay_series.map((series) => ({ ...series, data: series.data.filter((point) => visibleTimes.has(Number(point.time))) })),
      oscillator_series: chartPayload.oscillator_series.map((series) => ({ ...series, data: series.data.filter((point) => visibleTimes.has(Number(point.time))) })),
      price_zones: (chartPayload.price_zones ?? []).filter((zone) => candles.some((candle) => candle.time >= zone.start && candle.time <= zone.end)),
      volume: chartPayload.volume.filter((point) => visibleTimes.has(Number(point.time)))
    };
  }, [chartPayload, period.end, period.start]);

  return (
    <section className="trade-chart-modal-body">
      <div className="trade-chart-summary">
        <span className="trade-chart-subtitle">
          {selectedTrade
            ? `Showing ${sameSymbolTrades.length} trade${sameSymbolTrades.length === 1 ? "" : "s"} for this ticker. The selected trade is centered by its entry/exit midpoint.`
            : `${target.source} at ${target.timestamp || "the selected time"}. The gray line marks the exact event time.`}
        </span>
        {selectedTrade ? (
          <SemanticBadge tone={Number(selectedTrade.pnl ?? 0) >= 0 ? "success" : "danger"}>
            {formatMoney(Number(selectedTrade.pnl ?? 0))}
          </SemanticBadge>
        ) : (
          <SemanticBadge tone="neutral">{target.label}</SemanticBadge>
        )}
      </div>
      <ChartPanel
        catalogColumns={catalogColumns}
        displayItemOptions={displayItemOptions}
        emptyMessage={symbol ? `No saved symbol bars found for ${symbol}. Enable Save symbol bars before running the backtest.` : "Select a trade with a symbol to load the chart."}
        errorMessage={error ?? undefined}
        featureOptions={featureOptions}
        indicatorOptions={indicatorOptions}
        loading={loading}
        normalizeTicker={false}
        onPeriodChange={updatePeriod}
        onTickerChange={() => undefined}
        onTimeframeChange={setTimeframe}
        onVisibleColumnsChange={(nextColumns) => {
          setCustomVisibleColumns(true);
          setVisibleColumns(nextColumns);
        }}
        onVisibleSupervisionGroupsChange={() => undefined}
        payload={filteredPayload}
        periodEnd={period.end}
        periodMax={periodBounds.max}
        periodMin={periodBounds.min}
        periodStart={period.start}
        reference={reference}
        showReferenceLine={!selectedTrade}
        showIndicatorControls={true}
        showSupervisionControls={false}
        ticker={symbol || "Trade"}
        tickerInputWidth={112}
        tickerMaxLength={16}
        timeframe={timeframe}
        timeframes={availableTimeframes}
        visibleColumns={visibleColumns}
        visibleSupervisionGroups={[]}
      />
    </section>
  );
}

function portfolioChartTimeframes(payload?: PortfolioCandlePayload | null) {
  const allowed = ["30m", "1h", "2h", "4h", "1d"];
  const provided = payload?.timeframes?.length ? payload.timeframes.map(String) : allowed;
  const filtered = allowed.filter((timeframe) => provided.includes(timeframe));
  return filtered.length ? filtered : ["30m"];
}

function portfolioChartPeriodBounds(payload: PortfolioCandlePayload | null | undefined, timeframes: string[]) {
  const timestamps = timeframes.flatMap((timeframe) => (payload?.candles?.[timeframe] ?? []).map((candle) => Number(candle.time)).filter(Number.isFinite));
  if (!timestamps.length) return { end: "", max: "", min: "", start: "" };
  const dates = timestamps.map(dateStringFromTimestamp).filter(Boolean).sort();
  const min = dates[0] ?? "";
  const max = dates[dates.length - 1] ?? min;
  return { end: max, max, min, start: min };
}

function portfolioChartPayload(payload: PortfolioCandlePayload | null | undefined, timeframe: string, periodStart: string, periodEnd: string): ChartPayload | null {
  const sourceCandles = (payload?.candles?.[timeframe] ?? [])
    .filter((candle) => Number.isFinite(candle.time))
    .filter((candle) => candleInPeriod(candle, periodStart, periodEnd));
  const candles = sourceCandles.map((candle) => ({
    close: Number(candle.close ?? 0),
    high: Number(candle.high ?? 0),
    low: Number(candle.low ?? 0),
    open: Number(candle.open ?? 0),
    time: Number(candle.time)
  }));
  if (!candles.length) return null;
  return {
    candles,
    markers: [],
    oscillator_series: [],
    overlay_series: [],
    regions: [],
    volume: []
  };
}

function symbolTradeChartPayload(payload: RunSymbolChartPayload | null | undefined, trades: DataRow[], selectedKey: string, timeframe: string): ChartPayload | null {
  const source = symbolTimeframePayload(payload, timeframe);
  const candles = (source?.candles ?? [])
    .filter((candle) => Number.isFinite(candle.time))
    .map((candle) => ({
      close: Number(candle.close ?? 0),
      high: Number(candle.high ?? 0),
      low: Number(candle.low ?? 0),
      open: Number(candle.open ?? 0),
      time: Number(candle.time)
    }));
  if (!candles.length) return null;
  const candleTimes = new Set(candles.map((candle) => candle.time));
  return {
    candles,
    markers: source?.markers ?? [],
    options: source?.options ?? payload?.options,
    oscillator_series: source?.oscillator_series ?? [],
    overlay_series: source?.overlay_series ?? [],
    price_zones: source?.price_zones ?? [],
    regions: source?.regions ?? [],
    trade_annotations: tradeAnnotations(trades, selectedKey),
    volume: (source?.volume ?? []).filter((point) => candleTimes.has(Number(point.time)))
  };
}

function tradeAnnotations(trades: DataRow[], selectedKey: string): NonNullable<ChartPayload["trade_annotations"]> {
  return trades.flatMap((trade, index) => {
    const key = tradeRowKey(trade);
    const selected = key === selectedKey;
    const entryTime = tradeTimestampSeconds(trade.entry_bar_time ?? trade.entry_time);
    const exitTime = tradeTimestampSeconds(trade.exit_bar_time ?? trade.exit_time);
    const entryPrice = numericTradeValue(trade.entry_price);
    const exitPrice = numericTradeValue(trade.exit_price);
    if (entryTime === null || exitTime === null || entryPrice === null || exitPrice === null) return [];
    const quantity = numericTradeValue(trade.quantity);
    const pnl = Number(trade.pnl ?? 0);
    const color = pnl >= 0 ? "#16a34a" : "#dc2626";
    return [{
      color,
      entryLabel: tradeEntryLabel(trade, quantity, entryPrice),
      entryLabelParts: tradeEntryLabelParts(trade, quantity, entryPrice),
      entryLabelSide: "left",
      entryPrice,
      entryTime,
      exitLabel: tradeExitLabel(trade, exitPrice, pnl),
      exitLabelParts: tradeExitLabelParts(trade, exitPrice, pnl),
      exitLabelSide: "right",
      exitPrice,
      exitTime,
      fills: tradeFillAnnotations(trade),
      id: `${key}:trade:${index}`,
      pnl,
      selected,
      stopPrice: numericTradeValue(trade.stop_price ?? trade.entry_stop) ?? undefined,
      triggerPrice: numericTradeValue(trade.entry_trigger) ?? undefined
    }];
  });
}

function tradeFillAnnotations(trade: DataRow) {
  const entryFills = arrayValue(trade.entry_fills).map((fill) => tradeFillAnnotation(fill, "BUY" as const)).filter((fill) => fill !== null);
  const exitFills = arrayValue(trade.exit_fills).map((fill) => tradeFillAnnotation(fill, "SELL" as const)).filter((fill) => fill !== null);
  return [...entryFills, ...exitFills];
}

function tradeFillAnnotation(fill: DataRow, side: "BUY" | "SELL") {
  const time = tradeTimestampSeconds(fill.bar_time_market ?? fill.filled_at);
  const price = numericTradeValue(fill.fill_price);
  if (time === null || price === null) return null;
  const quantity = numericTradeValue(fill.quantity);
  const partial = String(fill.tag ?? "").includes("PARTIAL");
  const label = `${partial ? "Partial" : side === "BUY" ? "Entry" : "Exit"} ${quantity ? formatNumber(quantity) : ""}@${formatMoney(price)}`.replace(/\s+/g, " ").trim();
  return {
    label,
    labelParts: [
      { text: partial ? "Partial" : side === "BUY" ? "Entry" : "Exit", tone: "reason" as const },
      { text: " ", tone: "separator" as const },
      ...(quantity ? [{ text: formatNumber(quantity), tone: "size" as const }] : []),
      { text: "@", tone: "label" as const },
      { text: formatMoney(price), tone: "price" as const }
    ],
    price,
    quantity: quantity ?? undefined,
    side,
    time
  };
}

function arrayValue(value: unknown): DataRow[] {
  return Array.isArray(value) ? value.filter((item): item is DataRow => item !== null && typeof item === "object") : [];
}

function symbolTimeframePayload(payload: RunSymbolChartPayload | null | undefined, timeframe: string): RunSymbolChartTimeframePayload | null {
  if (!payload) return null;
  return payload.timeframe_payloads?.[timeframe] ?? (timeframe === payload.default_timeframe ? payload : null);
}

function symbolChartTimeframes(payload: RunSymbolChartPayload | null | undefined) {
  const configured = payload?.presentation?.timeframes?.map(String).filter(Boolean) ?? [];
  const provided = payload?.timeframes?.map(String).filter(Boolean) ?? [];
  if (configured.length) return configured;
  return provided.length ? provided : ["1m"];
}

function strategyVisibleColumns(chartPayload: ChartPayload | null, payload: RunSymbolChartPayload | null | undefined) {
  const volumeProfileAvailable = Boolean(chartPayload?.volume?.length) && Boolean(chartPayload?.options?.display_items?.some((item) => item.id === VOLUME_PROFILE_DISPLAY_ITEM));
  const available = [
    ...(chartPayload?.overlay_series ?? []).map((series) => String(series.displayItemId ?? series.column ?? "")),
    ...(chartPayload?.oscillator_series ?? []).map((series) => String(series.displayItemId ?? series.column ?? "")),
    ...(chartPayload?.markers ?? []).map((marker) => String(marker.displayItemId ?? "")),
    ...(chartPayload?.price_zones ?? []).map((zone) => String(zone.displayItemId ?? "")),
    ...(volumeProfileAvailable ? [VOLUME_PROFILE_DISPLAY_ITEM] : []),
  ].filter(Boolean);
  const configured = payload?.presentation?.default_visible?.map(String).filter((column) => available.includes(column)) ?? [];
  return configured.length ? configured : Array.from(new Set(available));
}

function debugChartVisibleColumns(payload: ChartPayload | null | undefined) {
  const volumeProfileAvailable = Boolean(payload?.volume?.length) && Boolean(payload?.options?.display_items?.some((item) => item.id === VOLUME_PROFILE_DISPLAY_ITEM));
  return Array.from(new Set([
    ...(payload?.overlay_series ?? []).map((series) => String(series.displayItemId ?? series.column ?? "")),
    ...(payload?.oscillator_series ?? []).map((series) => String(series.displayItemId ?? series.column ?? "")),
    ...(payload?.markers ?? []).map((marker) => String(marker.displayItemId ?? "")),
    ...(payload?.price_zones ?? []).map((zone) => String(zone.displayItemId ?? "")),
    ...(volumeProfileAvailable ? [VOLUME_PROFILE_DISPLAY_ITEM] : []),
  ].filter(Boolean)));
}

function tradeEntryLabel(trade: DataRow, quantity: number | null, entryPrice: number) {
  const reason = String(trade.entry_reason ?? trade.reason ?? "Entry").trim();
  const size = quantity ? `${formatNumber(quantity)}` : "";
  return `${reason} ${size}@${formatMoney(entryPrice)}`.replace(/\s+/g, " ").trim();
}

function tradeEntryLabelParts(trade: DataRow, quantity: number | null, entryPrice: number) {
  const reason = String(trade.entry_reason ?? trade.reason ?? "Entry").trim();
  return [
    { text: reason, tone: "reason" as const },
    { text: " ", tone: "separator" as const },
    ...(quantity ? [{ text: formatNumber(quantity), tone: "size" as const }] : []),
    { text: "@", tone: "label" as const },
    { text: formatMoney(entryPrice), tone: "price" as const }
  ];
}

function tradeExitLabel(trade: DataRow, exitPrice: number, pnl: number) {
  const reason = String(trade.exit_reason ?? "Exit").trim();
  return `${reason}@${formatMoney(exitPrice)}, P/L=${formatMoney(pnl)}`;
}

function tradeExitLabelParts(trade: DataRow, exitPrice: number, pnl: number) {
  const reason = String(trade.exit_reason ?? "Exit").trim();
  return [
    { text: reason, tone: "reason" as const },
    { text: "@", tone: "label" as const },
    { text: formatMoney(exitPrice), tone: "price" as const },
    { text: ", P/L=", tone: "label" as const },
    { text: formatMoney(pnl), tone: pnl >= 0 ? "pnlWin" as const : "pnlLoss" as const }
  ];
}

function selectedTradeReference(trade: DataRow) {
  const entryTime = tradeTimestampSeconds(trade.entry_bar_time ?? trade.entry_time);
  const exitTime = tradeTimestampSeconds(trade.exit_bar_time ?? trade.exit_time);
  const time = entryTime !== null && exitTime !== null ? Math.round(((entryTime + exitTime) / 2) / 60) * 60 : entryTime ?? exitTime;
  return time === null ? null : { endTime: exitTime ?? undefined, label: "Selected trade", startTime: entryTime ?? undefined, time };
}

function selectedSymbolReference(target: ObservationChartTarget, selectedTrade?: DataRow) {
  if (selectedTrade) return selectedTradeReference(selectedTrade);
  const time = tradeTimestampSeconds(target.timestamp);
  return time === null ? null : { label: target.label || target.source || "Selected event", time };
}

function tradeChartTarget(trade: DataRow): ObservationChartTarget {
  const symbol = tradeSymbol(trade);
  const timestamp = String(trade.entry_time ?? trade.exit_time ?? "");
  return {
    label: "Selected trade",
    row: trade,
    source: "Trade",
    symbol,
    timestamp
  };
}

function observationActionChartTarget(action: ObservabilityAction): ObservationChartTarget | null {
  if (!action.ticker || !action.timestamp) return null;
  return {
    label: action.title || "Action",
    row: action.trace,
    source: "Action",
    symbol: action.ticker,
    timestamp: action.timestamp
  };
}

function scannerRowChartTarget(row: DataRow): ObservationChartTarget | null {
  const symbol = normalizedTicker(rowText(row, "ticker") || rowText(row, "candidate_ticker") || rowText(row, "symbol"));
  const timestamp = rowText(row, "timestamp") || rowText(row, "candidate_timestamp") || rowText(row, "bar_time_market");
  if (!symbol || !timestamp) return null;
  const rank = rowText(row, "rank") || rowText(row, "live_rank") || rowText(row, "setup_rank");
  const scoreKey = rowText(row, "score_key") || "score";
  return {
    label: rank ? `Scanner rank ${rank}` : "Scanner row",
    row,
    source: `Scanner ${scoreKey}`,
    symbol,
    timestamp
  };
}

function openScannerRowChart(row: DataRow, onOpenChart: (target: ObservationChartTarget) => void) {
  const target = scannerRowChartTarget(row);
  if (target) onOpenChart(target);
}

function tradeSymbol(trade: DataRow | null | undefined) {
  return String(trade?.symbol ?? trade?.ticker ?? "").trim().toUpperCase();
}

function tradeMarketDate(trade: DataRow | null | undefined) {
  const entry = tradeTimestampSeconds(trade?.entry_time);
  if (entry !== null) return dateStringFromTimestamp(entry);
  const exit = tradeTimestampSeconds(trade?.exit_time);
  return exit === null ? "" : dateStringFromTimestamp(exit);
}

function numericTradeValue(value: unknown): number | null {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function tradeRowKey(trade: DataRow | null | undefined) {
  if (!trade) return "";
  return [
    tradeSymbol(trade),
    trade.entry_time ?? "",
    trade.exit_time ?? "",
    trade.quantity ?? "",
    trade.entry_price ?? "",
    trade.exit_price ?? ""
  ].map(String).join("|");
}

function tradeTimestampSeconds(value: unknown): number | null {
  if (typeof value === "number" && Number.isFinite(value)) {
    return Math.abs(value) > 100_000_000_000 ? Math.round(value / 1000) : Math.round(value);
  }
  if (typeof value !== "string" || !value.trim()) return null;
  const text = value.trim();
  if (/[zZ]$|[+-]\d{2}:?\d{2}$/.test(text)) {
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? Math.round(parsed / 1000) : null;
  }
  const match = text.match(/^(\d{4})-(\d{2})-(\d{2})(?:[T\s](\d{2}):(\d{2})(?::(\d{2}))?)?/);
  if (!match) {
    const parsed = Date.parse(text);
    return Number.isFinite(parsed) ? Math.round(parsed / 1000) : null;
  }
  const [, year, month, day, hour = "00", minute = "00", second = "00"] = match;
  return zonedTimestampSeconds(
    Number(year),
    Number(month),
    Number(day),
    Number(hour),
    Number(minute),
    Number(second),
    "America/New_York"
  );
}

function zonedTimestampSeconds(year: number, month: number, day: number, hour: number, minute: number, second: number, timeZone: string) {
  const utcGuess = Date.UTC(year, month - 1, day, hour, minute, second);
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    hour: "2-digit",
    hour12: false,
    minute: "2-digit",
    month: "2-digit",
    second: "2-digit",
    timeZone,
    year: "numeric"
  }).formatToParts(new Date(utcGuess));
  const part = (type: string) => Number(parts.find((item) => item.type === type)?.value ?? 0);
  const zonedAsUtc = Date.UTC(part("year"), part("month") - 1, part("day"), part("hour"), part("minute"), part("second"));
  const offset = zonedAsUtc - utcGuess;
  return Math.round((utcGuess - offset) / 1000);
}

function observationTargetMarketDate(target: ObservationChartTarget | null | undefined) {
  const timestamp = tradeTimestampSeconds(target?.timestamp);
  return timestamp === null ? "" : dateStringFromTimestamp(timestamp);
}

function symbolChartPeriodBounds(payload: RunSymbolChartPayload | null | undefined, timeframe: string, selectedDay = "") {
  const source = symbolTimeframePayload(payload, timeframe);
  const timestamps = (source?.candles ?? []).map((candle) => Number(candle.time)).filter(Number.isFinite);
  if (!timestamps.length) return { end: "", max: "", min: "", start: "" };
  const dates = timestamps.map(dateStringFromTimestamp).filter(Boolean).sort();
  const min = dates[0] ?? "";
  const max = dates[dates.length - 1] ?? min;
  const selectedInRange = selectedDay && selectedDay >= min && selectedDay <= max;
  return { end: selectedInRange ? selectedDay : max, max, min, start: selectedInRange ? selectedDay : min };
}

function chartPayloadPeriodBounds(payload: ChartPayload | null | undefined, selectedDay = "") {
  const timestamps = (payload?.candles ?? []).map((candle) => Number(candle.time)).filter(Number.isFinite);
  if (!timestamps.length) return { end: selectedDay, max: selectedDay, min: selectedDay, start: selectedDay };
  const dates = timestamps.map(dateStringFromTimestamp).filter(Boolean).sort();
  const min = dates[0] ?? selectedDay;
  const max = dates[dates.length - 1] ?? min;
  const selectedInRange = selectedDay && selectedDay >= min && selectedDay <= max;
  return { end: selectedInRange ? selectedDay : max, max, min, start: selectedInRange ? selectedDay : min };
}

function candleInChartPeriod(candle: { time: number }, periodStart: string, periodEnd: string) {
  if (!periodStart || !periodEnd) return true;
  const date = dateStringFromTimestamp(Number(candle.time));
  return Boolean(date && date >= periodStart && date <= periodEnd);
}

function candleInPeriod(candle: PortfolioCandle, periodStart: string, periodEnd: string) {
  if (!periodStart || !periodEnd) return true;
  const date = dateStringFromTimestamp(Number(candle.time));
  return Boolean(date && date >= periodStart && date <= periodEnd);
}

function dateStringFromTimestamp(timestamp: number) {
  if (!Number.isFinite(timestamp)) return "";
  const parts = new Intl.DateTimeFormat("en-US", {
    day: "2-digit",
    month: "2-digit",
    timeZone: "America/New_York",
    year: "numeric"
  }).formatToParts(new Date(timestamp * 1000));
  const part = (type: string) => parts.find((item) => item.type === type)?.value ?? "";
  const year = part("year");
  const month = part("month");
  const day = part("day");
  return year && month && day ? `${year}-${month}-${day}` : "";
}

type RunDetailPayload = {
  metadata: Record<string, unknown>;
  summary: Record<string, unknown>;
  tables: Record<string, { columns: string[]; rows: Record<string, unknown>[] }>;
  portfolio_candles?: PortfolioCandlePayload;
  logs: string;
};

type PortfolioCandle = {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  equity_open?: number;
  equity_high?: number;
  equity_low?: number;
  equity_close?: number;
  open_unrealized_open?: number;
  open_unrealized_high?: number;
  open_unrealized_low?: number;
  open_unrealized_close?: number;
  realized_pnl_open?: number;
  realized_pnl_high?: number;
  realized_pnl_low?: number;
  realized_pnl_close?: number;
  drawdown_open?: number;
  drawdown_high?: number;
  drawdown_low?: number;
  drawdown_close?: number;
  drawdown_pct_close?: number;
  gross_exposure?: number;
};

type PortfolioCandlePayload = {
  timeframes: string[];
  default_timeframe: string;
  candles: Record<string, PortfolioCandle[]>;
};

type RunSymbolChartTimeframePayload = {
  candles: Array<{
    time: number;
    open: number;
    high: number;
    low: number;
    close: number;
  }>;
  markers: ChartPayload["markers"];
  volume: ChartPayload["volume"];
  overlay_series: ChartPayload["overlay_series"];
  oscillator_series: ChartPayload["oscillator_series"];
  options?: ChartPayload["options"];
  price_zones?: ChartPayload["price_zones"];
  regions?: ChartPayload["regions"];
};

type RunSymbolChartPresentation = {
  default_timeframe?: string;
  default_visible?: string[];
  timeframes?: string[];
};

type RunSymbolChartPayload = RunSymbolChartTimeframePayload & {
  catalog_columns?: ChartCatalogItem[];
  symbol: string;
  timeframes: string[];
  default_timeframe: string;
  timeframe_payloads?: Record<string, RunSymbolChartTimeframePayload>;
  presentation?: RunSymbolChartPresentation;
  trades?: DataRow[];
};
