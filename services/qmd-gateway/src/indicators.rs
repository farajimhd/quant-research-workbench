use crate::bars::{BarRow, SharedBarStore, TradeAggregationRules};
use crate::computation_targets::SharedComputationTargets;
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEvent, GenericStructureSnapshot,
    StructureLevelCandidate, StructureTimeframeSnapshot, UnifiedStructureLevel,
};
use crate::metrics::SharedMetrics;
use crate::microstructure_interval::{
    MicrostructureIntervalFeatures, MicrostructureIntervalWindow,
};
use crate::scanner::ScannerPrimitiveRouter;
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, NaiveDate, Timelike, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, RwLock as StdRwLock};
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, sleep, Duration, MissedTickBehavior};

const STRUCTURE_CHECKPOINT_BATCH_LIMIT: usize = 256;

pub const INDICATOR_SCHEMA_VERSION: u16 = 20;
pub const INDICATOR_CALCULATION_REVISION: &str = "qmd-indicators-v22";
const MICROSTRUCTURE_AGGREGATE_TIMEFRAMES: [&str; 7] = ["1s", "5s", "10s", "30s", "1m", "5m", "1h"];
const INDICATOR_STATE_RECLAIM_INTERVAL_SECONDS: u64 = 30;
const RETAINED_100MS_HISTORY_ROWS: usize = 128;
const RETAINED_1S_HISTORY_ROWS: usize = 300;
const RETAINED_OTHER_HISTORY_ROWS: usize = 256;
const PREMARKET_SESSION_START_SECONDS: u32 = 4 * 60 * 60;

#[derive(Clone, Debug, Serialize)]
pub struct IndicatorSnapshot {
    pub ticker: String,
    pub tick: Option<TickIndicatorRow>,
    pub timeframe: String,
    pub current: Option<IndicatorRow>,
    pub history: Vec<IndicatorRow>,
}

#[derive(Clone, Debug, Serialize)]
pub struct IndicatorScannerSnapshot {
    pub as_of: DateTime<Utc>,
    pub timeframe: String,
    pub total_symbols: usize,
    pub row_count: usize,
    pub rows: Vec<IndicatorRow>,
}

#[derive(Clone, Debug, Serialize)]
pub struct TickIndicatorRow {
    pub sym: String,
    pub last_ts: Option<DateTime<Utc>>,
    pub last_price: f64,
    pub last_mid: f64,
    pub spread_bps: f64,
    pub quote_pressure: f64,
    pub trade_rate_10s: f64,
    pub trade_rate_60s: f64,
    pub trade_accel_10s_60s: f64,
    pub quote_rate_10s: f64,
    pub quote_rate_60s: f64,
    pub quote_accel_10s_60s: f64,
    pub rolling_vwap_60s: f64,
    pub tape_imbalance_60s: f64,
    pub buy_pressure_60s: f64,
    pub sell_pressure_60s: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct IndicatorBarFields {
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub dollar_volume: f64,
    pub trade_count: u64,
    pub avg_trade_size: f64,
    pub trade_rate: f64,
    pub volume_rate: f64,
    pub dollar_volume_rate: f64,
    pub price_change: f64,
    pub price_change_pct: f64,
    pub high_low_range_pct: f64,
    pub return_3_bar: f64,
    pub return_5_bar: f64,
    pub price_change_1_bar: Option<f64>,
    pub price_change_3_bar: Option<f64>,
    pub price_change_5_bar: Option<f64>,
    pub price_change_1_bar_pct: Option<f64>,
    pub price_change_3_bar_pct: Option<f64>,
    pub price_change_5_bar_pct: Option<f64>,
    pub price_ratio_1_bar: Option<f64>,
    pub price_ratio_3_bar: Option<f64>,
    pub price_ratio_5_bar: Option<f64>,
    pub volume_change: Option<f64>,
    pub volume_change_pct: Option<f64>,
    pub volume_ratio: Option<f64>,
    pub dollar_volume_change: Option<f64>,
    pub dollar_volume_change_pct: Option<f64>,
    pub dollar_volume_ratio: Option<f64>,
    pub trade_count_change: Option<f64>,
    pub trade_count_change_pct: Option<f64>,
    pub trade_count_ratio: Option<f64>,
    pub trade_rate_change: Option<f64>,
    pub trade_rate_change_pct: Option<f64>,
    pub trade_rate_ratio: Option<f64>,
    pub volume_rate_change: Option<f64>,
    pub volume_rate_change_pct: Option<f64>,
    pub volume_rate_ratio: Option<f64>,
    pub dollar_volume_rate_change: Option<f64>,
    pub dollar_volume_rate_change_pct: Option<f64>,
    pub dollar_volume_rate_ratio: Option<f64>,
    pub avg_trade_size_change: Option<f64>,
    pub avg_trade_size_change_pct: Option<f64>,
    pub avg_trade_size_ratio: Option<f64>,
    pub vwap_change: Option<f64>,
    pub vwap_change_pct: Option<f64>,
    pub vwap_ratio: Option<f64>,
    pub bid_open: f64,
    pub bid_high: f64,
    pub bid_low: f64,
    pub bid_close: f64,
    pub ask_open: f64,
    pub ask_high: f64,
    pub ask_low: f64,
    pub ask_close: f64,
    pub mid_open: f64,
    pub mid_high: f64,
    pub mid_low: f64,
    pub mid_close: f64,
    pub spread_close: f64,
    pub spread_mean: f64,
    pub spread_bps_mean: f64,
    pub spread_bps_close: f64,
    pub quote_count: u64,
    pub quote_rate: f64,
    pub quote_count_change: Option<f64>,
    pub quote_count_change_pct: Option<f64>,
    pub quote_count_ratio: Option<f64>,
    pub quote_rate_change: Option<f64>,
    pub quote_rate_change_pct: Option<f64>,
    pub quote_rate_ratio: Option<f64>,
    pub spread_close_change: Option<f64>,
    pub spread_close_change_pct: Option<f64>,
    pub spread_close_ratio: Option<f64>,
    pub spread_bps_change: Option<f64>,
    pub spread_bps_change_pct: Option<f64>,
    pub spread_bps_ratio: Option<f64>,
    pub buy_sell_volume_delta_change: Option<f64>,
}

impl From<&BarRow> for IndicatorBarFields {
    fn from(bar: &BarRow) -> Self {
        Self {
            open: bar.open,
            high: bar.high,
            low: bar.low,
            dollar_volume: bar.dollar_volume,
            trade_count: bar.trade_count,
            avg_trade_size: bar.avg_trade_size,
            trade_rate: bar.trade_rate,
            volume_rate: bar.volume_rate,
            dollar_volume_rate: bar.dollar_volume_rate,
            price_change: bar.price_change,
            price_change_pct: bar.price_change_pct,
            high_low_range_pct: bar.high_low_range_pct,
            return_3_bar: bar.return_3_bar,
            return_5_bar: bar.return_5_bar,
            price_change_1_bar: bar.price_change_1_bar,
            price_change_3_bar: bar.price_change_3_bar,
            price_change_5_bar: bar.price_change_5_bar,
            price_change_1_bar_pct: bar.price_change_1_bar_pct,
            price_change_3_bar_pct: bar.price_change_3_bar_pct,
            price_change_5_bar_pct: bar.price_change_5_bar_pct,
            price_ratio_1_bar: bar.price_ratio_1_bar,
            price_ratio_3_bar: bar.price_ratio_3_bar,
            price_ratio_5_bar: bar.price_ratio_5_bar,
            volume_change: bar.volume_change,
            volume_change_pct: bar.volume_change_pct,
            volume_ratio: bar.volume_ratio,
            dollar_volume_change: bar.dollar_volume_change,
            dollar_volume_change_pct: bar.dollar_volume_change_pct,
            dollar_volume_ratio: bar.dollar_volume_ratio,
            trade_count_change: bar.trade_count_change,
            trade_count_change_pct: bar.trade_count_change_pct,
            trade_count_ratio: bar.trade_count_ratio,
            trade_rate_change: bar.trade_rate_change,
            trade_rate_change_pct: bar.trade_rate_change_pct,
            trade_rate_ratio: bar.trade_rate_ratio,
            volume_rate_change: bar.volume_rate_change,
            volume_rate_change_pct: bar.volume_rate_change_pct,
            volume_rate_ratio: bar.volume_rate_ratio,
            dollar_volume_rate_change: bar.dollar_volume_rate_change,
            dollar_volume_rate_change_pct: bar.dollar_volume_rate_change_pct,
            dollar_volume_rate_ratio: bar.dollar_volume_rate_ratio,
            avg_trade_size_change: bar.avg_trade_size_change,
            avg_trade_size_change_pct: bar.avg_trade_size_change_pct,
            avg_trade_size_ratio: bar.avg_trade_size_ratio,
            vwap_change: bar.vwap_change,
            vwap_change_pct: bar.vwap_change_pct,
            vwap_ratio: bar.vwap_ratio,
            bid_open: bar.bid_open,
            bid_high: bar.bid_high,
            bid_low: bar.bid_low,
            bid_close: bar.bid_close,
            ask_open: bar.ask_open,
            ask_high: bar.ask_high,
            ask_low: bar.ask_low,
            ask_close: bar.ask_close,
            mid_open: bar.mid_open,
            mid_high: bar.mid_high,
            mid_low: bar.mid_low,
            mid_close: bar.mid_close,
            spread_close: bar.spread_close,
            spread_mean: bar.spread_mean,
            spread_bps_mean: bar.spread_bps_mean,
            spread_bps_close: bar.spread_bps_close,
            quote_count: bar.quote_count,
            quote_rate: bar.quote_rate,
            quote_count_change: bar.quote_count_change,
            quote_count_change_pct: bar.quote_count_change_pct,
            quote_count_ratio: bar.quote_count_ratio,
            quote_rate_change: bar.quote_rate_change,
            quote_rate_change_pct: bar.quote_rate_change_pct,
            quote_rate_ratio: bar.quote_rate_ratio,
            spread_close_change: bar.spread_close_change,
            spread_close_change_pct: bar.spread_close_change_pct,
            spread_close_ratio: bar.spread_close_ratio,
            spread_bps_change: bar.spread_bps_change,
            spread_bps_change_pct: bar.spread_bps_change_pct,
            spread_bps_ratio: bar.spread_bps_ratio,
            buy_sell_volume_delta_change: bar.buy_sell_volume_delta_change,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct IndicatorRow {
    pub schema_version: u16,
    pub session_date: String,
    pub timeframe: String,
    pub sym: String,
    pub bar_start: DateTime<Utc>,
    pub bar_end: DateTime<Utc>,
    pub close: f64,
    pub volume: f64,
    pub vwap: f64,
    #[serde(flatten)]
    pub bar_fields: IndicatorBarFields,
    pub ema_9: f64,
    pub ema_20: f64,
    pub ema_50: f64,
    pub rsi_14: f64,
    pub atr_14: f64,
    pub macd_line: f64,
    pub macd_signal: f64,
    pub macd_histogram: f64,
    pub bollinger_mid_20: f64,
    pub bollinger_upper_20: f64,
    pub bollinger_lower_20: f64,
    pub bollinger_std_20: f64,
    pub close_sma_20: f64,
    pub volume_sma_20: f64,
    pub return_1_bar: f64,
    pub price_vs_ema20_pct: f64,
    pub price_vs_vwap_pct: f64,
    pub trend_score: f64,
    pub microstructure_unified_signal: f64,
    pub microstructure_unified_confidence: f64,
    pub microstructure_unified_action: String,
    pub microstructure_buy_trade_count: u64,
    pub microstructure_sell_trade_count: u64,
    pub microstructure_classified_trade_count: u64,
    pub microstructure_eligible_trade_count: u64,
    pub microstructure_buy_volume: f64,
    pub microstructure_sell_volume: f64,
    pub microstructure_signed_volume_delta: f64,
    pub microstructure_cumulative_signed_volume_delta: f64,
    pub microstructure_anchored_flow_relationship: String,
    pub microstructure_anchored_flow_relationship_score: f64,
    pub microstructure_transaction_imbalance: f64,
    pub microstructure_signed_volume_imbalance: f64,
    pub microstructure_level1_ofi_delta: f64,
    pub microstructure_cumulative_level1_ofi: f64,
    pub microstructure_level1_ofi: f64,
    pub microstructure_queue_imbalance: f64,
    pub microstructure_microprice_lean: f64,
    pub microstructure_midpoint_return_bps: f64,
    pub microstructure_trade_return_bps: f64,
    pub microstructure_aggressor_persistence: f64,
    pub microstructure_arrival_intensity_imbalance: f64,
    pub microstructure_arrival_rate_per_second: f64,
    pub microstructure_resiliency: f64,
    pub microstructure_aggressive_flow_score: f64,
    pub microstructure_displayed_liquidity_score: f64,
    pub microstructure_response_resiliency_score: f64,
    pub microstructure_regime_reliability: f64,
    pub flow_structure_composite_score: f64,
    pub flow_structure_composite_confidence: f64,
    pub flow_structure_composite_bias: String,
    pub flow_structure_composite_reason: String,
    pub liquidity_support_price: f64,
    pub liquidity_support_strength: f64,
    pub liquidity_support_confidence: f64,
    pub liquidity_resistance_price: f64,
    pub liquidity_resistance_strength: f64,
    pub liquidity_resistance_confidence: f64,
    pub liquidity_level_pressure: f64,
    pub market_level_support_score: f64,
    pub market_level_resistance_score: f64,
    pub market_level_bias: f64,
    pub structure_session_high: f64,
    pub structure_session_low: f64,
    pub structure_premarket_high: f64,
    pub structure_premarket_low: f64,
    pub structure_opening_range_high: f64,
    pub structure_opening_range_low: f64,
    pub structure_swing_high: f64,
    pub structure_swing_low: f64,
    pub structure_volume_poc: f64,
    pub structure_nearest_round: f64,
    pub structure_bos_price: f64,
    pub structure_bos_direction: i8,
    pub structure_choch_price: f64,
    pub structure_choch_direction: i8,
    pub structure_luld_upper: f64,
    pub structure_luld_lower: f64,
    pub structure_52_week_high: f64,
    pub structure_52_week_low: f64,
    pub structure_prior_month_high: f64,
    pub structure_prior_month_low: f64,
    pub structure_prior_month_close: f64,
    pub qmd_structure_algorithm_version: u16,
    pub qmd_structure_reference_price: f64,
    pub qmd_structure_direction: i8,
    pub qmd_structure_score: f64,
    pub qmd_structure_agreement: f64,
    pub qmd_structure_strength: f64,
    pub qmd_structure_confidence: f64,
    pub qmd_structure_support_field: f64,
    pub qmd_structure_resistance_field: f64,
    pub qmd_structure_pressure_bias: f64,
    pub qmd_structure_pressure_confidence: f64,
    pub qmd_structure_up_probability: f64,
    pub qmd_structure_support_price: f64,
    pub qmd_structure_support_lower: f64,
    pub qmd_structure_support_upper: f64,
    pub qmd_structure_support_strength: f64,
    pub qmd_structure_support_confidence: f64,
    pub qmd_structure_resistance_price: f64,
    pub qmd_structure_resistance_lower: f64,
    pub qmd_structure_resistance_upper: f64,
    pub qmd_structure_resistance_strength: f64,
    pub qmd_structure_resistance_confidence: f64,
    pub qmd_structure_active_levels: Vec<StructureLevelCandidate>,
    pub qmd_structure_timeframe_states: Vec<StructureTimeframeSnapshot>,
    pub qmd_structure_unified_levels: Vec<UnifiedStructureLevel>,
    pub qmd_structure_developing_high: f64,
    pub qmd_structure_developing_low: f64,
    pub qmd_structure_developing_direction: i8,
    pub qmd_structure_micro_direction: i8,
    pub qmd_structure_micro_threshold: f64,
    pub qmd_structure_micro_swing_high: f64,
    pub qmd_structure_micro_swing_low: f64,
    pub qmd_structure_micro_support_price: f64,
    pub qmd_structure_micro_support_lower: f64,
    pub qmd_structure_micro_support_upper: f64,
    pub qmd_structure_micro_support_strength: f64,
    pub qmd_structure_micro_support_confidence: f64,
    pub qmd_structure_micro_resistance_price: f64,
    pub qmd_structure_micro_resistance_lower: f64,
    pub qmd_structure_micro_resistance_upper: f64,
    pub qmd_structure_micro_resistance_strength: f64,
    pub qmd_structure_micro_resistance_confidence: f64,
    pub qmd_structure_tactical_direction: i8,
    pub qmd_structure_tactical_threshold: f64,
    pub qmd_structure_tactical_swing_high: f64,
    pub qmd_structure_tactical_swing_low: f64,
    pub qmd_structure_tactical_support_price: f64,
    pub qmd_structure_tactical_support_lower: f64,
    pub qmd_structure_tactical_support_upper: f64,
    pub qmd_structure_tactical_support_strength: f64,
    pub qmd_structure_tactical_support_confidence: f64,
    pub qmd_structure_tactical_resistance_price: f64,
    pub qmd_structure_tactical_resistance_lower: f64,
    pub qmd_structure_tactical_resistance_upper: f64,
    pub qmd_structure_tactical_resistance_strength: f64,
    pub qmd_structure_tactical_resistance_confidence: f64,
    pub qmd_structure_context_direction: i8,
    pub qmd_structure_context_threshold: f64,
    pub qmd_structure_context_swing_high: f64,
    pub qmd_structure_context_swing_low: f64,
    pub qmd_structure_context_support_price: f64,
    pub qmd_structure_context_support_lower: f64,
    pub qmd_structure_context_support_upper: f64,
    pub qmd_structure_context_support_strength: f64,
    pub qmd_structure_context_support_confidence: f64,
    pub qmd_structure_context_resistance_price: f64,
    pub qmd_structure_context_resistance_lower: f64,
    pub qmd_structure_context_resistance_upper: f64,
    pub qmd_structure_context_resistance_strength: f64,
    pub qmd_structure_context_resistance_confidence: f64,
    pub qmd_structure_event_id: u64,
    pub qmd_structure_event_pivot_at_ms: i64,
    pub qmd_structure_event_at_ms: i64,
    pub qmd_structure_event_kind: String,
    pub qmd_structure_event_timeframe: String,
    pub qmd_structure_event_direction: i8,
    pub qmd_structure_event_price: f64,
    pub qmd_structure_session_high: f64,
    pub qmd_structure_session_low: f64,
    pub qmd_structure_opening_range_high: f64,
    pub qmd_structure_opening_range_low: f64,
    pub qmd_structure_trade_volume_poc: f64,
    pub qmd_structure_nearest_round: f64,
    pub qmd_structure_luld_upper: f64,
    pub qmd_structure_luld_lower: f64,
    pub qmd_structure_52_week_high: f64,
    pub qmd_structure_52_week_low: f64,
    pub qmd_structure_prior_month_high: f64,
    pub qmd_structure_prior_month_low: f64,
    pub qmd_structure_prior_month_close: f64,
    #[serde(skip_serializing)]
    pub qmd_structure_snapshot: GenericStructureSnapshot,
    #[serde(skip_serializing)]
    pub qmd_structure_events: Vec<GenericStructureEvent>,
    #[serde(skip_serializing)]
    pub microstructure_interval: MicrostructureIntervalFeatures,
}

impl IndicatorRow {
    /// Drop calculator-only state after the public indicator projection has
    /// been finalized. Historical chart structure history is retained by its
    /// dedicated event projection; replay consumes the scalar causal fields.
    /// Unified levels remain because they are the compact, point-in-time chart
    /// projection and must be recoverable at every replay clock.
    pub fn compact_for_historical_cache(mut self) -> Self {
        self.qmd_structure_active_levels.clear();
        self.qmd_structure_timeframe_states.clear();
        for level in &mut self.qmd_structure_unified_levels {
            level.sources.clear();
        }
        self.qmd_structure_snapshot = Default::default();
        self.qmd_structure_events.clear();
        self.microstructure_interval = Default::default();
        self
    }

    pub fn apply_microstructure_interval(&mut self, interval: &MicrostructureIntervalFeatures) {
        self.microstructure_buy_trade_count = interval.buy_trade_count;
        self.microstructure_sell_trade_count = interval.sell_trade_count;
        self.microstructure_classified_trade_count = interval.classified_trade_count;
        self.microstructure_eligible_trade_count = interval.eligible_trade_count;
        self.microstructure_buy_volume = interval.buy_volume;
        self.microstructure_sell_volume = interval.sell_volume;
        self.microstructure_signed_volume_delta = interval.signed_volume_delta;
        self.microstructure_transaction_imbalance = interval.transaction_imbalance;
        self.microstructure_signed_volume_imbalance = interval.signed_volume_imbalance;
        self.microstructure_level1_ofi_delta = interval.level1_ofi_delta;
        self.microstructure_level1_ofi = interval.level1_ofi;
        self.microstructure_queue_imbalance = interval.queue_imbalance;
        self.microstructure_microprice_lean = interval.microprice_lean;
        self.microstructure_midpoint_return_bps = interval.midpoint_return_bps;
        self.microstructure_trade_return_bps = interval.trade_return_bps;
        self.microstructure_aggressor_persistence = interval.aggressor_persistence;
        self.microstructure_arrival_intensity_imbalance = interval.arrival_intensity_imbalance;
        self.microstructure_arrival_rate_per_second = interval.arrival_rate_per_second;
        self.microstructure_resiliency = interval.resiliency;
        self.microstructure_aggressive_flow_score = interval.aggressive_flow_score;
        self.microstructure_displayed_liquidity_score = interval.displayed_liquidity_score;
        self.microstructure_response_resiliency_score = interval.response_resiliency_score;
        self.microstructure_regime_reliability = interval.regime_reliability;
        self.microstructure_unified_signal = interval.unified_signal;
        self.microstructure_unified_confidence = interval.unified_confidence;
        self.microstructure_unified_action = interval.unified_action.to_string();
        self.microstructure_interval = interval.clone();
        self.refresh_flow_structure_composite();
    }

    fn refresh_flow_structure_composite(&mut self) {
        let (score, confidence, bias, reason) = calculate_flow_structure_composite(
            self.microstructure_unified_signal,
            self.microstructure_unified_confidence,
            self.qmd_structure_score,
            self.qmd_structure_pressure_bias,
            self.qmd_structure_pressure_confidence,
            self.qmd_structure_confidence,
            self.qmd_structure_agreement,
        );
        self.flow_structure_composite_score = score;
        self.flow_structure_composite_confidence = confidence;
        self.flow_structure_composite_bias = bias.to_string();
        self.flow_structure_composite_reason = reason.to_string();
    }
}

fn calculate_flow_structure_composite(
    flow: f64,
    flow_confidence_percent: f64,
    structure: f64,
    pressure_bias: f64,
    pressure_confidence: f64,
    structure_confidence: f64,
    structure_agreement: f64,
) -> (f64, f64, &'static str, &'static str) {
    let flow = flow.clamp(-1.0, 1.0);
    let flow_confidence = (flow_confidence_percent / 100.0).clamp(0.0, 1.0);
    let structure = structure.clamp(-1.0, 1.0);
    let pressure_confidence = pressure_confidence.clamp(0.0, 1.0);
    let pressure = (pressure_bias * pressure_confidence).clamp(-1.0, 1.0);
    let context = (0.75 * structure + 0.25 * pressure).clamp(-1.0, 1.0);
    let context_confidence = ((0.65 * structure_confidence.clamp(0.0, 1.0)
        + 0.35 * pressure_confidence)
        * (0.6 + 0.4 * structure_agreement.clamp(0.0, 1.0)))
    .clamp(0.0, 1.0);
    let evidence_weight = flow_confidence + context_confidence;
    let score = if evidence_weight > f64::EPSILON {
        ((flow * flow_confidence + context * context_confidence) / evidence_weight).clamp(-1.0, 1.0)
    } else {
        0.0
    };
    let flow_meaningful = flow.abs() >= 0.15 && flow_confidence >= 0.35;
    let context_meaningful = context.abs() >= 0.05 && context_confidence >= 0.25;
    let aligned = flow_meaningful && context_meaningful && flow.signum() == context.signum();
    let conflicting = flow_meaningful && context_meaningful && flow.signum() != context.signum();
    let agreement_factor = if aligned {
        1.0
    } else if conflicting {
        0.4
    } else {
        0.7
    };
    let confidence =
        ((0.65 * flow_confidence + 0.35 * context_confidence) * agreement_factor).clamp(0.0, 1.0);
    let directional = score.abs() >= 0.15 && confidence >= 0.35;
    let bias = if directional {
        if score > 0.0 {
            "bullish"
        } else {
            "bearish"
        }
    } else {
        "neutral"
    };
    let reason = if aligned {
        if score >= 0.0 {
            "aligned_bullish_evidence"
        } else {
            "aligned_bearish_evidence"
        }
    } else if conflicting {
        "conflicting_flow_structure_evidence"
    } else if flow_meaningful {
        "flow_dominant_evidence"
    } else if context_meaningful {
        "structure_dominant_evidence"
    } else {
        "weak_flow_structure_evidence"
    };
    (
        (score * 10_000.0).round() / 10_000.0,
        (confidence * 100.0).round() / 100.0,
        bias,
        reason,
    )
}

/// Calculate canonical indicators for an ordered batch of bars.
pub fn calculate_bar_indicators(bars: &[BarRow]) -> Vec<IndicatorRow> {
    let mut calculator = BarIndicatorCalculator::new();
    bars.iter().map(|bar| calculator.apply_bar(bar)).collect()
}

/// Stateful canonical bar-indicator calculator shared by live and historical
/// runtimes. Historical replay uses this incrementally so a finalized bar is
/// accompanied by exactly the same causal indicator update as live QMD.
pub struct BarIndicatorCalculator {
    state: BarIndicatorState,
    cumulative_microstructure: MicrostructureCumulativeFlow,
}

#[derive(Clone, Copy, Debug, Default)]
pub struct MarketStructureReferenceLevels {
    pub high_52_week: f64,
    pub low_52_week: f64,
    pub prior_month_high: f64,
    pub prior_month_low: f64,
    pub prior_month_close: f64,
    pub previous_session_close: f64,
}

#[derive(Debug, Deserialize)]
struct MarketStructureReferenceRow {
    sym: String,
    high_52_week: f64,
    low_52_week: f64,
    prior_month_high: f64,
    prior_month_low: f64,
    prior_month_close: f64,
    #[serde(default)]
    previous_session_close: f64,
}

pub async fn load_live_market_structure_references(
    config: &GatewayConfig,
    as_of: DateTime<Utc>,
) -> Result<HashMap<String, MarketStructureReferenceLevels>, String> {
    let sql = market_structure_reference_sql(
        &config.historical_clickhouse_database,
        &config.historical_daily_session_bars_table,
        None,
        as_of,
    )?;
    let url = format!(
        "{}/",
        config.historical_clickhouse_url.trim_end_matches('/')
    );
    let client = Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
        .map_err(|error| format!("daily market-structure client failed: {error}"))?;
    let mut request = client
        .post(url)
        .header("X-ClickHouse-User", &config.historical_clickhouse_user)
        .body(sql);
    let password = config.historical_clickhouse_password();
    if !password.is_empty() {
        request = request.header("X-ClickHouse-Key", password);
    }
    let response = request
        .send()
        .await
        .map_err(|error| format!("daily market-structure query failed: {error}"))?;
    let status = response.status();
    let text = response
        .text()
        .await
        .map_err(|error| format!("daily market-structure response failed: {error}"))?;
    if !status.is_success() {
        return Err(format!(
            "daily market-structure query returned HTTP {status}: {text}"
        ));
    }
    parse_market_structure_reference_rows(&text)
}

pub fn market_structure_reference_sql(
    database: &str,
    table: &str,
    ticker: Option<&str>,
    as_of: DateTime<Utc>,
) -> Result<String, String> {
    for (name, value) in [("database", database), ("table", table)] {
        if value.is_empty()
            || !value
                .chars()
                .all(|character| character.is_ascii_alphanumeric() || character == '_')
        {
            return Err(format!("market-structure {name} is not a valid identifier"));
        }
    }
    let as_of_date = as_of.with_timezone(&New_York).date_naive();
    let daily_bars = daily_session_trade_bars_sql(
        database,
        table,
        ticker,
        as_of_date - chrono::Duration::days(364),
        as_of_date,
        as_of,
    )?;
    Ok(format!(
        r#"SELECT
            sym,
            ifNull(maxIf(high, session_date >= addDays(toDate('{as_of_date}'), -364) AND session_date < toDate('{as_of_date}')), 0) AS high_52_week,
            ifNull(minIf(low, low > 0 AND session_date >= addDays(toDate('{as_of_date}'), -364) AND session_date < toDate('{as_of_date}')), 0) AS low_52_week,
            ifNull(maxIf(high, toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_high,
            ifNull(minIf(low, low > 0 AND toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_low,
            ifNull(argMaxIf(close, bar_end, toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_close,
            ifNull(argMax(close, tuple(session_date, bar_end)), 0) AS previous_session_close
        FROM ({daily_bars})
        GROUP BY sym
        FORMAT JSONEachRow"#,
    ))
}

/// Build fully closed daily trade bars from the three authoritative SIP session rows.
/// Canonical identity joins ticker changes without projecting ambiguous mappings.
pub fn daily_session_trade_bars_sql(
    database: &str,
    table: &str,
    ticker: Option<&str>,
    start_date: NaiveDate,
    end_date: NaiveDate,
    as_of: DateTime<Utc>,
) -> Result<String, String> {
    for (name, value) in [("database", database), ("table", table)] {
        if value.is_empty()
            || !value
                .chars()
                .all(|character| character.is_ascii_alphanumeric() || character == '_')
        {
            return Err(format!("daily-session {name} is not a valid identifier"));
        }
    }
    if start_date >= end_date {
        return Err("daily-session range must have start_date before end_date".to_string());
    }
    let (identity_cte, ticker_filter) = ticker
        .map(|value| {
            let value = value.replace('\'', "''");
            (
                format!(
                    r#"WITH (
                        SELECT count()
                        FROM `{database}`.`{table}` FINAL
                        PREWHERE session_date >= toDate('{start_date}')
                          AND session_date < toDate('{end_date}')
                        WHERE canonical_ticker = '{value}'
                          AND identity_status != 'ambiguous_source_ticker'
                          AND available_at_us <= toUInt64(toUnixTimestamp64Micro(parseDateTime64BestEffort('{as_of}')))
                    ) AS requested_canonical_count"#,
                    as_of = as_of.to_rfc3339(),
                ),
                format!(
                    "AND (canonical_ticker = '{value}' OR (requested_canonical_count = 0 AND source_ticker = '{value}'))"
                ),
            )
        })
        .unwrap_or_default();
    Ok(format!(
        r#"{identity_cte}
        SELECT
            ifNull(canonical_ticker, source_ticker) AS sym,
            argMax(source_ticker, bar_end_us) AS source_sym,
            session_date,
            fromUnixTimestamp64Micro(toInt64(min(bar_start_us)), 'UTC') AS bar_start,
            fromUnixTimestamp64Micro(toInt64(max(bar_end_us)), 'UTC') AS bar_end,
            argMinIf(trade_open, tuple(bar_start_us, source_first_timestamp_us), trade_present = 1) AS open,
            maxIf(trade_high, trade_present = 1) AS high,
            minIf(trade_low, trade_present = 1) AS low,
            argMaxIf(trade_close, tuple(bar_end_us, source_last_timestamp_us), trade_present = 1) AS close,
            sum(trade_size_sum) AS size_sum,
            sum(trade_event_count) AS event_count
        FROM `{database}`.`{table}` FINAL
        PREWHERE session_date >= toDate('{start_date}')
          AND session_date < toDate('{end_date}')
        WHERE adjusted = 0
          AND identity_status != 'ambiguous_source_ticker'
          AND available_at_us <= toUInt64(toUnixTimestamp64Micro(parseDateTime64BestEffort('{as_of}')))
          {ticker_filter}
        GROUP BY sym, session_date
        HAVING uniqExact(session_kind) = 3 AND event_count > 0"#,
        as_of = as_of.to_rfc3339(),
    ))
}

pub fn parse_market_structure_reference_rows(
    text: &str,
) -> Result<HashMap<String, MarketStructureReferenceLevels>, String> {
    text.lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| {
            let row = serde_json::from_str::<MarketStructureReferenceRow>(line)
                .map_err(|error| format!("invalid daily market-structure row: {error}"))?;
            Ok((
                row.sym.to_ascii_uppercase(),
                MarketStructureReferenceLevels {
                    high_52_week: row.high_52_week,
                    low_52_week: row.low_52_week,
                    prior_month_high: row.prior_month_high,
                    prior_month_low: row.prior_month_low,
                    prior_month_close: row.prior_month_close,
                    previous_session_close: row.previous_session_close,
                },
            ))
        })
        .collect()
}

impl BarIndicatorCalculator {
    pub fn new() -> Self {
        Self {
            state: BarIndicatorState::new(),
            cumulative_microstructure: MicrostructureCumulativeFlow::default(),
        }
    }

    pub fn apply_bar(&mut self, bar: &BarRow) -> IndicatorRow {
        self.state.apply_bar(bar)
    }

    pub fn apply_session_vwap_only(&mut self, bar: &BarRow) -> f64 {
        self.state
            .session_vwap
            .update(bar.bar_start, bar.volume, bar.vwap)
    }

    /// Seed only the additive, session-anchored VWAP state before processing a
    /// bounded page of bars. Other indicators intentionally remain page-local.
    pub fn seed_session_vwap(
        &mut self,
        bar_start: DateTime<Utc>,
        cumulative_volume: f64,
        cumulative_trade_notional: f64,
    ) -> Result<(), String> {
        self.state
            .session_vwap
            .seed(bar_start, cumulative_volume, cumulative_trade_notional)
    }

    pub fn set_market_structure_references(&mut self, references: MarketStructureReferenceLevels) {
        self.state.market_structure_references = references;
    }

    /// Apply interval-local microstructure values before the caller finalizes
    /// the row's session-anchored cumulative flow.
    pub fn apply_microstructure_interval(
        &mut self,
        row: &mut IndicatorRow,
        interval: &MicrostructureIntervalFeatures,
    ) {
        row.apply_microstructure_interval(interval);
    }

    /// Advance anchored flow after a caller has populated an aggregated
    /// interval on the row.
    pub fn apply_cumulative_microstructure(&mut self, row: &mut IndicatorRow) {
        self.cumulative_microstructure.apply_to(row);
    }

    /// The event-native QMD structure is attached upstream by the ordered bar
    /// engine. This method now only derives the legacy confluence scalar used
    /// by existing strategy screens from the canonical support/resistance state.
    pub fn apply_market_levels(&mut self, row: &mut IndicatorRow, _bar: &BarRow) {
        row.market_level_support_score =
            row.qmd_structure_support_strength * row.qmd_structure_support_confidence;
        row.market_level_resistance_score =
            row.qmd_structure_resistance_strength * row.qmd_structure_resistance_confidence;
        row.market_level_bias =
            (row.market_level_support_score - row.market_level_resistance_score).clamp(-1.0, 1.0);
        row.liquidity_level_pressure = row.market_level_bias;
    }
}

impl Default for BarIndicatorCalculator {
    fn default() -> Self {
        Self::new()
    }
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct IndicatorKey {
    sym: String,
    timeframe: String,
}

#[derive(Clone)]
pub struct SharedIndicatorStore {
    shards: Arc<Vec<IndicatorShardStore>>,
}

#[derive(Clone, Debug, Default, Serialize)]
pub struct IndicatorStateReclaim {
    pub bar_calculators: usize,
    pub base_indicator_rows: usize,
    pub history_series: usize,
    pub current_rows: usize,
    pub microstructure_aggregates: usize,
    pub microstructure_windows: usize,
    pub tick_states: usize,
}

impl IndicatorStateReclaim {
    pub fn total(&self) -> usize {
        self.bar_calculators
            + self.base_indicator_rows
            + self.history_series
            + self.current_rows
            + self.microstructure_aggregates
            + self.microstructure_windows
            + self.tick_states
    }
}

#[derive(Clone)]
pub struct IndicatorEventRouter {
    bar_sender: mpsc::Sender<BarRow>,
    computation_targets: SharedComputationTargets,
    event_senders: Arc<Vec<mpsc::Sender<MarketEvent>>>,
}

#[derive(Clone)]
struct IndicatorShardStore {
    inner: Arc<Mutex<IndicatorStore>>,
}

struct IndicatorStore {
    bars: HashMap<IndicatorKey, BarIndicatorCalculator>,
    current: HashMap<IndicatorKey, IndicatorRow>,
    history: HashMap<IndicatorKey, VecDeque<IndicatorRow>>,
    history_limits: HashMap<String, usize>,
    history_limit: usize,
    tick_window_seconds: i64,
    ticks: HashMap<String, TickState>,
    microstructure: HashMap<String, MicrostructureIntervalWindow>,
    microstructure_aggregates: HashMap<IndicatorKey, MicrostructureSampleAggregate>,
    last_base_indicators: HashMap<String, IndicatorRow>,
    trade_rules: TradeAggregationRules,
    market_structure_references: Arc<StdRwLock<HashMap<String, MarketStructureReferenceLevels>>>,
}

#[derive(Clone, Debug, Default)]
struct MicrostructureCumulativeFlow {
    anchor_session_date: String,
    level1_ofi: f64,
    signed_volume_delta: f64,
}

impl MicrostructureCumulativeFlow {
    fn apply_to(&mut self, row: &mut IndicatorRow) {
        let anchor_session_date = anchored_market_session_date(row.bar_start);
        let (level1_ofi, signed_volume_delta) = self.update(
            &anchor_session_date,
            row.microstructure_level1_ofi_delta,
            row.microstructure_signed_volume_delta,
        );
        row.microstructure_cumulative_level1_ofi = level1_ofi;
        row.microstructure_cumulative_signed_volume_delta = signed_volume_delta;
        let (relationship, relationship_score) =
            anchored_flow_relationship(level1_ofi, signed_volume_delta);
        row.microstructure_anchored_flow_relationship = relationship.to_string();
        row.microstructure_anchored_flow_relationship_score = relationship_score;
    }

    fn update(
        &mut self,
        anchor_session_date: &str,
        level1_ofi_delta: f64,
        signed_volume_delta: f64,
    ) -> (f64, f64) {
        if self.anchor_session_date != anchor_session_date {
            self.level1_ofi = 0.0;
            self.signed_volume_delta = 0.0;
            self.anchor_session_date = anchor_session_date.to_string();
        }
        self.level1_ofi += level1_ofi_delta;
        self.signed_volume_delta += signed_volume_delta;
        (
            round_indicator_value(self.level1_ofi),
            round_indicator_value(self.signed_volume_delta),
        )
    }
}

fn anchored_market_session_date(bar_start: DateTime<Utc>) -> String {
    market_session_anchor_date(bar_start).to_string()
}

fn anchored_flow_relationship(level1_ofi: f64, signed_volume_delta: f64) -> (&'static str, f64) {
    if level1_ofi > 0.0 && signed_volume_delta > 0.0 {
        ("bullish_confirmation", 1.0)
    } else if level1_ofi < 0.0 && signed_volume_delta < 0.0 {
        ("bearish_confirmation", -1.0)
    } else if level1_ofi > 0.0 && signed_volume_delta < 0.0 {
        ("bullish_absorption", 0.55)
    } else if level1_ofi < 0.0 && signed_volume_delta > 0.0 {
        ("bearish_absorption", -0.55)
    } else {
        ("neutral", 0.0)
    }
}

struct TickState {
    last_ask: f64,
    last_bid: f64,
    last_mid: f64,
    last_price: f64,
    last_ts: Option<DateTime<Utc>>,
    recent_quotes: VecDeque<QuoteSample>,
    recent_trades: VecDeque<TradeSample>,
    spread_bps: f64,
    window_seconds: i64,
}

#[derive(Clone)]
struct TradeSample {
    ts: DateTime<Utc>,
    signed_volume: f64,
    volume: f64,
    notional: f64,
}

#[derive(Clone)]
struct QuoteSample {
    ask_size: f64,
    bid_size: f64,
    ts: DateTime<Utc>,
}

struct BarIndicatorState {
    atr_14: WilderAverage,
    bollinger_20: RollingStats,
    close_sma_20: RollingStats,
    ema_9: EmaState,
    ema_12: EmaState,
    ema_20: EmaState,
    ema_26: EmaState,
    ema_50: EmaState,
    last_close: f64,
    macd_signal_9: EmaState,
    rsi_14: RsiState,
    session_vwap: SessionVwapState,
    volume_sma_20: RollingStats,
    market_structure_references: MarketStructureReferenceLevels,
}

struct SessionVwapState {
    cumulative_trade_notional: f64,
    cumulative_volume: f64,
    anchor: Option<NaiveDate>,
}

struct EmaState {
    period: f64,
    value: Option<f64>,
}

struct RsiState {
    avg_gain: f64,
    avg_loss: f64,
    count: usize,
    period: usize,
    seed_gain_sum: f64,
    seed_loss_sum: f64,
}

struct WilderAverage {
    count: usize,
    period: usize,
    seed_sum: f64,
    value: Option<f64>,
}

struct RollingStats {
    items: VecDeque<f64>,
    sum: f64,
    sum_sq: f64,
    window: usize,
}

impl SharedIndicatorStore {
    pub fn new(
        history_limit: usize,
        history_limits: HashMap<String, usize>,
        tick_window_seconds: i64,
        shard_count: usize,
        trade_rules: TradeAggregationRules,
        market_structure_references: HashMap<String, MarketStructureReferenceLevels>,
    ) -> Self {
        let shard_count = shard_count.max(1);
        let market_structure_references = Arc::new(StdRwLock::new(market_structure_references));
        let shards = (0..shard_count)
            .map(|_| {
                IndicatorShardStore::new(
                    history_limit,
                    history_limits.clone(),
                    tick_window_seconds,
                    trade_rules.clone(),
                    market_structure_references.clone(),
                )
            })
            .collect::<Vec<_>>();
        Self {
            shards: Arc::new(shards),
        }
    }

    pub fn shard_count(&self) -> usize {
        self.shards.len()
    }

    fn shard(&self, index: usize) -> IndicatorShardStore {
        self.shards[index % self.shards.len()].clone()
    }

    pub async fn snapshot(&self, ticker: &str, timeframe: &str, limit: usize) -> IndicatorSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let timeframe = canonical_timeframe(timeframe);
        self.shard_for_ticker(&ticker)
            .snapshot(&ticker, &timeframe, limit)
            .await
    }

    pub async fn scanner_snapshot(
        &self,
        timeframe: &str,
        limit: usize,
    ) -> IndicatorScannerSnapshot {
        let timeframe = canonical_timeframe(timeframe);
        let mut rows = Vec::new();
        for shard in self.shards.iter() {
            let store = shard.inner.lock().await;
            rows.extend(
                store
                    .current
                    .iter()
                    .filter(|(key, _)| key.timeframe == timeframe)
                    .map(|(_, row)| row.clone()),
            );
        }
        rows.sort_by(|left, right| {
            right
                .bar_end
                .cmp(&left.bar_end)
                .then_with(|| left.sym.cmp(&right.sym))
        });
        let total_symbols = rows.len();
        rows.truncate(limit);
        rows.iter_mut().for_each(|row| {
            // Scanner/Watchlist consumers need the scalar structure fields,
            // not the full per-timeframe chart state. Keep that detailed
            // request-scoped payload on the per-ticker indicator endpoint.
            row.qmd_structure_active_levels.clear();
            row.qmd_structure_timeframe_states.clear();
            row.qmd_structure_unified_levels.clear();
        });
        let as_of = rows
            .iter()
            .map(|row| row.bar_end)
            .max()
            .unwrap_or_else(Utc::now);
        IndicatorScannerSnapshot {
            as_of,
            timeframe,
            total_symbols,
            row_count: rows.len(),
            rows,
        }
    }

    /// Seed a newly focused calculation from the already-authoritative core
    /// bar cache. Existing indicator state is never replayed a second time.
    pub async fn warm_from_bars(&self, ticker: &str, timeframe: &str, bars: Vec<BarRow>) -> usize {
        let ticker = ticker.to_ascii_uppercase();
        let timeframe = canonical_timeframe(timeframe);
        let shard = self.shard_for_ticker(&ticker);
        let mut store = shard.inner.lock().await;
        let key = IndicatorKey {
            sym: ticker,
            timeframe,
        };
        if store.current.contains_key(&key) {
            return 0;
        }
        let mut ordered = bars;
        ordered.sort_by_key(|bar| bar.bar_end);
        let count = ordered.len();
        for bar in ordered {
            store.apply_bar(bar);
        }
        count
    }

    pub async fn needs_warm(&self, ticker: &str, timeframe: &str) -> bool {
        let ticker = ticker.to_ascii_uppercase();
        let timeframe = canonical_timeframe(timeframe);
        let shard = self.shard_for_ticker(&ticker);
        let store = shard.inner.lock().await;
        let key = IndicatorKey {
            sym: ticker,
            timeframe,
        };
        !store.current.contains_key(&key)
    }

    /// Apply one ordered canonical event during a bounded retained-window
    /// reconciliation. Callers must serialize a ticker reconciliation and
    /// replay events in canonical event-time order.
    pub async fn apply_reconciliation_event(&self, event: &MarketEvent) {
        self.shard_for_ticker(event.ticker())
            .apply_event(event)
            .await;
    }

    /// Apply one closed bar through the same indicator state machine used by
    /// the live router and return the exact durable row.
    pub async fn apply_reconciliation_bar(&self, bar: BarRow) -> IndicatorRow {
        self.shard_for_ticker(&bar.sym).apply_bar(bar).await
    }

    pub async fn replace_market_structure_references(
        &self,
        references: HashMap<String, MarketStructureReferenceLevels>,
    ) {
        let Some(first_shard) = self.shards.first() else {
            return;
        };
        let shared_references = {
            let store = first_shard.inner.lock().await;
            store.market_structure_references.clone()
        };
        *shared_references
            .write()
            .expect("market-structure reference lock poisoned") = references;
        let references = shared_references
            .read()
            .expect("market-structure reference lock poisoned")
            .clone();
        for shard in self.shards.iter() {
            let mut store = shard.inner.lock().await;
            for (key, calculator) in store.bars.iter_mut() {
                calculator.set_market_structure_references(
                    references.get(&key.sym).copied().unwrap_or_default(),
                );
            }
        }
    }

    /// Reclaim focused state only after checking the current lease set while
    /// each state shard is locked. A concurrent lease activation either becomes
    /// visible before the retain decision or waits for the shard and warms the
    /// newly active state afterwards, so cleanup cannot erase a new activation.
    pub async fn reclaim_unused(
        &self,
        computation_targets: &SharedComputationTargets,
    ) -> IndicatorStateReclaim {
        let mut reclaimed = IndicatorStateReclaim::default();
        for shard in self.shards.iter() {
            let mut store = shard.inner.lock().await;

            let before = store.bars.len();
            store.bars.retain(|key, _| {
                computation_targets.requires_bar_computation(&key.sym, &key.timeframe)
            });
            reclaimed.bar_calculators += before.saturating_sub(store.bars.len());

            let before = store.history.len();
            store.history.retain(|key, _| {
                computation_targets.requires_bar_computation(&key.sym, &key.timeframe)
            });
            reclaimed.history_series += before.saturating_sub(store.history.len());

            let before = store.current.len();
            store.current.retain(|key, _| {
                computation_targets.requires_bar_computation(&key.sym, &key.timeframe)
            });
            reclaimed.current_rows += before.saturating_sub(store.current.len());

            let before = store.microstructure_aggregates.len();
            store.microstructure_aggregates.retain(|key, _| {
                computation_targets.requires_bar_computation(&key.sym, &key.timeframe)
            });
            reclaimed.microstructure_aggregates +=
                before.saturating_sub(store.microstructure_aggregates.len());

            let before = store.last_base_indicators.len();
            store
                .last_base_indicators
                .retain(|ticker, _| computation_targets.requires_bar_computation(ticker, "100ms"));
            reclaimed.base_indicator_rows +=
                before.saturating_sub(store.last_base_indicators.len());

            let before = store.ticks.len();
            store
                .ticks
                .retain(|ticker, _| computation_targets.requires_event_computation(ticker));
            reclaimed.tick_states += before.saturating_sub(store.ticks.len());

            let before = store.microstructure.len();
            store
                .microstructure
                .retain(|ticker, _| computation_targets.requires_event_computation(ticker));
            reclaimed.microstructure_windows += before.saturating_sub(store.microstructure.len());
        }
        reclaimed
    }

    fn shard_for_ticker(&self, ticker: &str) -> IndicatorShardStore {
        self.shard(shard_index(ticker, self.shards.len()))
    }
}

impl IndicatorEventRouter {
    pub fn bar_sender(&self) -> mpsc::Sender<BarRow> {
        self.bar_sender.clone()
    }

    pub fn event_queue_capacity(&self, event: &MarketEvent) -> Option<(usize, usize)> {
        if !self
            .computation_targets
            .requires_event_computation(event.ticker())
        {
            return None;
        }
        let index = shard_index(event.ticker(), self.event_senders.len());
        let sender = &self.event_senders[index];
        Some((sender.capacity(), sender.max_capacity()))
    }

    pub async fn send_event(
        &self,
        event: MarketEvent,
    ) -> Result<(), mpsc::error::SendError<MarketEvent>> {
        if !self
            .computation_targets
            .requires_event_computation(event.ticker())
        {
            return Ok(());
        }
        let index = shard_index(event.ticker(), self.event_senders.len());
        self.event_senders[index].send(event).await
    }

    pub fn try_send_event(
        &self,
        event: MarketEvent,
    ) -> Result<(), mpsc::error::TrySendError<MarketEvent>> {
        if !self
            .computation_targets
            .requires_event_computation(event.ticker())
        {
            return Ok(());
        }
        let index = shard_index(event.ticker(), self.event_senders.len());
        self.event_senders[index].try_send(event)
    }
}

impl IndicatorShardStore {
    fn new(
        history_limit: usize,
        history_limits: HashMap<String, usize>,
        tick_window_seconds: i64,
        trade_rules: TradeAggregationRules,
        market_structure_references: Arc<
            StdRwLock<HashMap<String, MarketStructureReferenceLevels>>,
        >,
    ) -> Self {
        Self {
            inner: Arc::new(Mutex::new(IndicatorStore {
                bars: HashMap::new(),
                current: HashMap::new(),
                history: HashMap::new(),
                history_limits,
                history_limit,
                tick_window_seconds: tick_window_seconds.max(60),
                ticks: HashMap::new(),
                microstructure: HashMap::new(),
                microstructure_aggregates: HashMap::new(),
                last_base_indicators: HashMap::new(),
                trade_rules,
                market_structure_references,
            })),
        }
    }

    async fn apply_bar(&self, bar: BarRow) -> IndicatorRow {
        let mut store = self.inner.lock().await;
        store.apply_bar(bar)
    }

    async fn apply_event(&self, event: &MarketEvent) {
        let mut store = self.inner.lock().await;
        store.apply_event(event);
    }

    async fn snapshot(&self, ticker: &str, timeframe: &str, limit: usize) -> IndicatorSnapshot {
        let key = IndicatorKey {
            sym: ticker.to_string(),
            timeframe: timeframe.to_string(),
        };
        let store = self.inner.lock().await;
        let tick = store.ticks.get(ticker).map(|state| state.snapshot(ticker));
        let current = store.current.get(&key).cloned();
        let history_limit = store.history_limit_for(&timeframe);
        let mut history = store
            .history
            .get(&key)
            .map(|rows| {
                rows.iter()
                    .rev()
                    .take(limit.min(history_limit))
                    .cloned()
                    .collect::<Vec<_>>()
                    .into_iter()
                    .rev()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        history.iter_mut().for_each(|row| {
            row.qmd_structure_active_levels.clear();
            row.qmd_structure_unified_levels.clear();
        });
        IndicatorSnapshot {
            ticker: ticker.to_string(),
            tick,
            timeframe: timeframe.to_string(),
            current,
            history,
        }
    }
}

impl IndicatorStore {
    fn apply_event(&mut self, event: &MarketEvent) {
        let ticker = event.ticker().to_ascii_uppercase();
        let tick_window_seconds = self.tick_window_seconds;
        let tick = self
            .ticks
            .entry(ticker.clone())
            .or_insert_with(|| TickState::new(tick_window_seconds));
        match event {
            MarketEvent::Trade(trade) => tick.apply_trade(trade),
            MarketEvent::Quote(quote) => tick.apply_quote(quote),
        }
        self.microstructure
            .entry(ticker)
            .or_default()
            .apply_event(event);
    }

    fn apply_bar(&mut self, bar: BarRow) -> IndicatorRow {
        let key = IndicatorKey {
            sym: bar.sym.clone(),
            timeframe: bar.timeframe.clone(),
        };
        let references = self
            .market_structure_references
            .read()
            .expect("market-structure reference lock poisoned")
            .get(&bar.sym.to_ascii_uppercase())
            .copied()
            .unwrap_or_default();
        let ticker = bar.sym.to_ascii_uppercase();
        let is_base = bar.timeframe.eq_ignore_ascii_case("100ms");
        let valid_price = indicator_valid_price_bar(&bar);
        let carried = if valid_price {
            None
        } else if is_base {
            self.last_base_indicators.get(&ticker).cloned()
        } else {
            self.current.get(&key).cloned()
        };
        let state = self.bars.entry(key.clone()).or_insert_with(|| {
            let mut calculator = BarIndicatorCalculator::new();
            calculator.set_market_structure_references(references);
            calculator
        });
        let mut row = if let Some(mut carried) = carried {
            carried.session_date = bar.session_date.clone();
            carried.bar_start = bar.bar_start;
            carried.bar_end = bar.bar_end;
            carried.volume = 0.0;
            carried.qmd_structure_events.clear();
            carried
        } else if valid_price {
            state.apply_bar(&bar)
        } else {
            // Before the first eligible trade-price bar, expose a transient
            // zero row without contaminating the canonical rolling state.
            let mut transient = BarIndicatorCalculator::new();
            transient.set_market_structure_references(references);
            transient.apply_bar(&bar)
        };
        if is_base && !valid_price {
            row.vwap = state.apply_session_vwap_only(&bar);
            row.price_vs_vwap_pct = pct_change(row.close, row.vwap);
        }
        if is_base {
            if let Some(window) = self.microstructure.get(&ticker) {
                let interval = window.interval_at(bar.bar_end, &self.trade_rules);
                self.bars
                    .get_mut(&key)
                    .expect("indicator calculator exists")
                    .apply_microstructure_interval(&mut row, &interval);
            }
            for timeframe in MICROSTRUCTURE_AGGREGATE_TIMEFRAMES {
                self.microstructure_aggregates
                    .entry(IndicatorKey {
                        sym: ticker.clone(),
                        timeframe: timeframe.to_string(),
                    })
                    .or_default()
                    .push(&row);
            }
        } else if let Some(aggregate) = self.microstructure_aggregates.get_mut(&key) {
            aggregate.apply_to(&mut row);
            aggregate.reset();
        }
        self.bars
            .get_mut(&key)
            .expect("indicator calculator exists")
            .apply_cumulative_microstructure(&mut row);
        if valid_price {
            self.bars
                .get_mut(&key)
                .expect("indicator calculator exists")
                .apply_market_levels(&mut row, &bar);
        }
        if is_base && row.close.is_finite() && row.close > 0.0 {
            self.last_base_indicators.insert(ticker, row.clone());
        }
        if row.close.is_finite() && row.close > 0.0 {
            let history_limit = self.history_limit_for(&bar.timeframe);
            let history = self
                .history
                .entry(key.clone())
                .or_insert_with(VecDeque::new);
            let mut retained = row.clone();
            retained.qmd_structure_active_levels.clear();
            retained.qmd_structure_timeframe_states.clear();
            retained.qmd_structure_unified_levels.clear();
            retained.qmd_structure_events.clear();
            retained.qmd_structure_snapshot = GenericStructureSnapshot::default();
            retained.microstructure_interval = MicrostructureIntervalFeatures::default();
            history.push_back(retained);
            while history.len() > history_limit {
                history.pop_front();
            }
            self.current.insert(key, row.clone());
        }
        row
    }

    fn history_limit_for(&self, timeframe: &str) -> usize {
        let configured = self
            .history_limits
            .get(&canonical_timeframe(timeframe))
            .copied()
            .unwrap_or(self.history_limit);
        let retained_cap = match canonical_timeframe(timeframe).as_str() {
            "100ms" => RETAINED_100MS_HISTORY_ROWS,
            "1s" => RETAINED_1S_HISTORY_ROWS,
            _ => RETAINED_OTHER_HISTORY_ROWS,
        };
        configured.min(retained_cap)
    }
}

fn indicator_valid_price_bar(bar: &BarRow) -> bool {
    [bar.open, bar.high, bar.low, bar.close]
        .into_iter()
        .all(|value| value.is_finite() && value > 0.0)
        && bar.high >= bar.open.max(bar.close)
        && bar.low <= bar.open.min(bar.close)
        && bar.high >= bar.low
}

/// O(1)-memory sufficient statistics for causal 100 ms evidence buckets.
#[derive(Clone, Debug, Default)]
pub struct MicrostructureSampleAggregate {
    sample_count: u64,
    interval: MicrostructureIntervalFeatures,
    composite_confidence_sum: f64,
    composite_weighted_score_sum: f64,
    composite_sample_count: u64,
    last_session_vwap: Option<f64>,
}

impl MicrostructureSampleAggregate {
    pub fn push(&mut self, row: &IndicatorRow) {
        self.push_interval(&row.microstructure_interval);
        let confidence = row.flow_structure_composite_confidence.clamp(0.0, 1.0);
        self.composite_confidence_sum += confidence;
        self.composite_weighted_score_sum +=
            row.flow_structure_composite_score.clamp(-1.0, 1.0) * confidence;
        self.composite_sample_count += 1;
        if row.vwap.is_finite() && row.vwap > 0.0 {
            self.last_session_vwap = Some(row.vwap);
        }
    }

    pub fn push_interval(&mut self, interval: &MicrostructureIntervalFeatures) {
        self.sample_count += 1;
        self.interval.merge(interval);
    }

    pub fn apply_to(&self, target: &mut IndicatorRow) {
        let mut interval = self.interval.clone();
        let expected_samples = microstructure_expected_samples(&target.timeframe);
        let coverage = if expected_samples == 0 {
            0.0
        } else {
            (self.sample_count as f64 / expected_samples as f64).clamp(0.0, 1.0)
        };
        interval.refresh(coverage);
        target.apply_microstructure_interval(&interval);
        if let Some(session_vwap) = self.last_session_vwap {
            target.vwap = session_vwap;
            target.price_vs_vwap_pct = pct_change(target.close, session_vwap);
        }
        self.apply_composite_summary(target);
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }

    fn apply_composite_summary(&self, target: &mut IndicatorRow) {
        if let Some((score, confidence, bias, reason)) = summarize_canonical_composites(
            self.composite_weighted_score_sum,
            self.composite_confidence_sum,
            self.composite_sample_count,
        ) {
            target.flow_structure_composite_score = score;
            target.flow_structure_composite_confidence = confidence;
            target.flow_structure_composite_bias = bias.to_string();
            target.flow_structure_composite_reason = reason.to_string();
        }
    }
}

fn summarize_canonical_composites(
    weighted_score_sum: f64,
    confidence_sum: f64,
    sample_count: u64,
) -> Option<(f64, f64, &'static str, &'static str)> {
    if sample_count == 0 {
        return None;
    }
    let mean_confidence = (confidence_sum / sample_count as f64).clamp(0.0, 1.0);
    let consensus_score = if confidence_sum > f64::EPSILON {
        (weighted_score_sum / confidence_sum).clamp(-1.0, 1.0)
    } else {
        0.0
    };
    let summary_confidence = (mean_confidence * consensus_score.abs()).clamp(0.0, 1.0);
    let directional = consensus_score.abs() >= 0.15 && summary_confidence >= 0.35;
    let bias = if directional {
        if consensus_score > 0.0 {
            "bullish"
        } else {
            "bearish"
        }
    } else {
        "neutral"
    };
    let reason = if directional {
        "canonical_100ms_consensus"
    } else {
        "canonical_100ms_mixed_or_weak"
    };
    Some((
        (consensus_score * 10_000.0).round() / 10_000.0,
        (summary_confidence * 100.0).round() / 100.0,
        bias,
        reason,
    ))
}

fn microstructure_expected_samples(timeframe: &str) -> u64 {
    match canonical_timeframe(timeframe).as_str() {
        "1s" => 10,
        "5s" => 50,
        "10s" => 100,
        "30s" => 300,
        "1m" => 600,
        "5m" => 3_000,
        "1h" => 36_000,
        _ => 0,
    }
}

impl TickState {
    fn new(window_seconds: i64) -> Self {
        Self {
            last_ask: 0.0,
            last_bid: 0.0,
            last_mid: 0.0,
            last_price: 0.0,
            last_ts: None,
            recent_quotes: VecDeque::new(),
            recent_trades: VecDeque::new(),
            spread_bps: 0.0,
            window_seconds: window_seconds.max(60),
        }
    }

    fn apply_trade(&mut self, trade: &TradeEvent) {
        if trade.price <= 0.0 || trade.size <= 0.0 {
            return;
        }
        let side = self.classify_trade_side(trade.price);
        let signed_volume = if side >= 0 { trade.size } else { -trade.size };
        self.last_price = trade.price;
        self.last_ts = Some(trade.ts.clone());
        self.recent_trades.push_back(TradeSample {
            ts: trade.ts.clone(),
            signed_volume,
            volume: trade.size,
            notional: trade.price * trade.size,
        });
        self.evict_old(trade.ts.clone());
    }

    fn apply_quote(&mut self, quote: &QuoteEvent) {
        if quote.bid_price <= 0.0 || quote.ask_price <= 0.0 {
            return;
        }
        self.last_bid = quote.bid_price;
        self.last_ask = quote.ask_price;
        self.last_mid = (quote.bid_price + quote.ask_price) / 2.0;
        self.last_ts = Some(quote.ts.clone());
        self.spread_bps = safe_div(quote.ask_price - quote.bid_price, self.last_mid) * 10_000.0;
        self.recent_quotes.push_back(QuoteSample {
            ask_size: quote.ask_size as f64,
            bid_size: quote.bid_size as f64,
            ts: quote.ts.clone(),
        });
        self.evict_old(quote.ts.clone());
    }

    fn snapshot(&self, ticker: &str) -> TickIndicatorRow {
        let last_ts = self.last_ts.clone().unwrap_or_else(Utc::now);
        let trade_count_10s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 10)
            .count() as f64;
        let quote_count_10s = self
            .recent_quotes
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 10)
            .count() as f64;
        let trade_count_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .count() as f64;
        let quote_count_60s = self
            .recent_quotes
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .count() as f64;
        let volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .map(|sample| sample.volume)
            .sum::<f64>();
        let signed_volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .map(|sample| sample.signed_volume)
            .sum::<f64>();
        let buy_volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .filter(|sample| sample.signed_volume > 0.0)
            .map(|sample| sample.volume)
            .sum::<f64>();
        let sell_volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .filter(|sample| sample.signed_volume < 0.0)
            .map(|sample| sample.volume)
            .sum::<f64>();
        let notional_60s = self
            .recent_trades
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), last_ts.clone()) <= 60)
            .map(|sample| sample.notional)
            .sum::<f64>();
        let trade_rate_10s = trade_count_10s / 10.0;
        let trade_rate_60s = trade_count_60s / 60.0;
        let quote_rate_10s = quote_count_10s / 10.0;
        let quote_rate_60s = quote_count_60s / 60.0;

        TickIndicatorRow {
            sym: ticker.to_string(),
            last_ts: self.last_ts.clone(),
            last_price: self.last_price,
            last_mid: self.last_mid,
            spread_bps: self.spread_bps,
            quote_pressure: self.quote_pressure(last_ts, 60),
            trade_rate_10s,
            trade_rate_60s,
            trade_accel_10s_60s: trade_rate_10s - trade_rate_60s,
            quote_rate_10s,
            quote_rate_60s,
            quote_accel_10s_60s: quote_rate_10s - quote_rate_60s,
            rolling_vwap_60s: safe_div(notional_60s, volume_60s),
            tape_imbalance_60s: safe_div(signed_volume_60s, volume_60s),
            buy_pressure_60s: safe_div(buy_volume_60s, volume_60s),
            sell_pressure_60s: safe_div(sell_volume_60s, volume_60s),
        }
    }

    fn classify_trade_side(&self, price: f64) -> i8 {
        if self.last_ask > 0.0 && price >= self.last_ask {
            return 1;
        }
        if self.last_bid > 0.0 && price <= self.last_bid {
            return -1;
        }
        if self.last_mid > 0.0 && price >= self.last_mid {
            return 1;
        }
        if self.last_price > 0.0 && price >= self.last_price {
            return 1;
        }
        -1
    }

    fn evict_old(&mut self, now: DateTime<Utc>) {
        while self
            .recent_trades
            .front()
            .map(|sample| seconds_between(sample.ts.clone(), now.clone()) > self.window_seconds)
            .unwrap_or(false)
        {
            self.recent_trades.pop_front();
        }
        while self
            .recent_quotes
            .front()
            .map(|sample| seconds_between(sample.ts.clone(), now.clone()) > self.window_seconds)
            .unwrap_or(false)
        {
            self.recent_quotes.pop_front();
        }
    }

    fn quote_pressure(&self, now: DateTime<Utc>, window_seconds: i64) -> f64 {
        let bid_size = self
            .recent_quotes
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), now.clone()) <= window_seconds)
            .map(|sample| sample.bid_size)
            .sum::<f64>();
        let ask_size = self
            .recent_quotes
            .iter()
            .filter(|sample| seconds_between(sample.ts.clone(), now.clone()) <= window_seconds)
            .map(|sample| sample.ask_size)
            .sum::<f64>();
        safe_div(bid_size - ask_size, bid_size + ask_size)
    }
}

impl BarIndicatorState {
    fn new() -> Self {
        Self {
            atr_14: WilderAverage::new(14),
            bollinger_20: RollingStats::new(20),
            close_sma_20: RollingStats::new(20),
            ema_9: EmaState::new(9),
            ema_12: EmaState::new(12),
            ema_20: EmaState::new(20),
            ema_26: EmaState::new(26),
            ema_50: EmaState::new(50),
            last_close: 0.0,
            macd_signal_9: EmaState::new(9),
            rsi_14: RsiState::new(14),
            session_vwap: SessionVwapState::new(),
            volume_sma_20: RollingStats::new(20),
            market_structure_references: MarketStructureReferenceLevels::default(),
        }
    }

    fn apply_bar(&mut self, bar: &BarRow) -> IndicatorRow {
        let previous_close = self.last_close;
        let ema_9 = self.ema_9.update(bar.close);
        let ema_20 = self.ema_20.update(bar.close);
        let ema_50 = self.ema_50.update(bar.close);
        let ema_12 = self.ema_12.update(bar.close);
        let ema_26 = self.ema_26.update(bar.close);
        let macd_line = ema_12 - ema_26;
        let macd_signal = self.macd_signal_9.update(macd_line);
        let macd_histogram = macd_line - macd_signal;
        let rsi_14 = if previous_close > 0.0 {
            self.rsi_14.update(bar.close - previous_close)
        } else {
            0.0
        };
        let true_range = if previous_close > 0.0 {
            (bar.high - bar.low)
                .max((bar.high - previous_close).abs())
                .max((bar.low - previous_close).abs())
        } else {
            bar.high - bar.low
        };
        let atr_14 = self.atr_14.update(true_range);
        self.close_sma_20.push(bar.close);
        self.volume_sma_20.push(bar.volume);
        self.bollinger_20.push(bar.close);
        self.last_close = bar.close;
        let session_vwap = self
            .session_vwap
            .update(bar.bar_start, bar.volume, bar.vwap);
        let structure = &bar.qmd_structure;
        let state_for = |timeframe: &str| {
            structure
                .timeframe_states
                .iter()
                .find(|state| state.timeframe == timeframe)
                .cloned()
                .unwrap_or_default()
        };
        let current_timeframe_state = state_for(&bar.timeframe);
        // Temporary compatibility projections for consumers that still deserialize the
        // former three-scale columns. They are sourced from the canonical timeframe
        // states; the level book itself has no micro/tactical/context extraction path.
        let micro_state = state_for("100ms");
        let tactical_state = state_for("10s");
        let context_state = state_for("1m");
        let references = self.market_structure_references;

        IndicatorRow {
            schema_version: INDICATOR_SCHEMA_VERSION,
            session_date: bar.session_date.clone(),
            timeframe: bar.timeframe.clone(),
            sym: bar.sym.clone(),
            bar_start: bar.bar_start.clone(),
            bar_end: bar.bar_end.clone(),
            close: bar.close,
            volume: bar.volume,
            vwap: session_vwap,
            bar_fields: IndicatorBarFields::from(bar),
            ema_9,
            ema_20,
            ema_50,
            rsi_14,
            atr_14,
            macd_line,
            macd_signal,
            macd_histogram,
            bollinger_mid_20: self.bollinger_20.mean(),
            bollinger_upper_20: self.bollinger_20.mean() + 2.0 * self.bollinger_20.stddev(),
            bollinger_lower_20: self.bollinger_20.mean() - 2.0 * self.bollinger_20.stddev(),
            bollinger_std_20: self.bollinger_20.stddev(),
            close_sma_20: self.close_sma_20.mean(),
            volume_sma_20: self.volume_sma_20.mean(),
            return_1_bar: if previous_close > 0.0 {
                pct_change(bar.close, previous_close)
            } else {
                0.0
            },
            price_vs_ema20_pct: pct_change(bar.close, ema_20),
            price_vs_vwap_pct: pct_change(bar.close, session_vwap),
            trend_score: trend_score(bar.close, ema_9, ema_20, ema_50, rsi_14, macd_histogram),
            microstructure_unified_signal: 0.0,
            microstructure_unified_confidence: 0.0,
            microstructure_unified_action: "wait".to_string(),
            microstructure_buy_trade_count: 0,
            microstructure_sell_trade_count: 0,
            microstructure_classified_trade_count: 0,
            microstructure_eligible_trade_count: 0,
            microstructure_buy_volume: 0.0,
            microstructure_sell_volume: 0.0,
            microstructure_signed_volume_delta: 0.0,
            microstructure_cumulative_signed_volume_delta: 0.0,
            microstructure_anchored_flow_relationship: "neutral".to_string(),
            microstructure_anchored_flow_relationship_score: 0.0,
            microstructure_transaction_imbalance: 0.0,
            microstructure_signed_volume_imbalance: 0.0,
            microstructure_level1_ofi_delta: 0.0,
            microstructure_cumulative_level1_ofi: 0.0,
            microstructure_level1_ofi: 0.0,
            microstructure_queue_imbalance: 0.0,
            microstructure_microprice_lean: 0.0,
            microstructure_midpoint_return_bps: 0.0,
            microstructure_trade_return_bps: 0.0,
            microstructure_aggressor_persistence: 0.0,
            microstructure_arrival_intensity_imbalance: 0.0,
            microstructure_arrival_rate_per_second: 0.0,
            microstructure_resiliency: 0.0,
            microstructure_aggressive_flow_score: 0.0,
            microstructure_displayed_liquidity_score: 0.0,
            microstructure_response_resiliency_score: 0.0,
            microstructure_regime_reliability: 0.0,
            flow_structure_composite_score: 0.0,
            flow_structure_composite_confidence: 0.0,
            flow_structure_composite_bias: "neutral".to_string(),
            flow_structure_composite_reason: "weak_flow_structure_evidence".to_string(),
            liquidity_support_price: 0.0,
            liquidity_support_strength: 0.0,
            liquidity_support_confidence: 0.0,
            liquidity_resistance_price: 0.0,
            liquidity_resistance_strength: 0.0,
            liquidity_resistance_confidence: 0.0,
            liquidity_level_pressure: 0.0,
            market_level_support_score: 0.0,
            market_level_resistance_score: 0.0,
            market_level_bias: 0.0,
            structure_session_high: 0.0,
            structure_session_low: 0.0,
            structure_premarket_high: 0.0,
            structure_premarket_low: 0.0,
            structure_opening_range_high: 0.0,
            structure_opening_range_low: 0.0,
            // These compatibility fields are the strategy-facing, timeframe-local
            // confirmed pivots. Leaving them at zero made every configured
            // `indicator.structure.swing_*` rule permanently unavailable even
            // though the canonical generic-structure state was populated.
            structure_swing_high: current_timeframe_state.swing_high,
            structure_swing_low: current_timeframe_state.swing_low,
            structure_volume_poc: 0.0,
            structure_nearest_round: 0.0,
            structure_bos_price: 0.0,
            structure_bos_direction: 0,
            structure_choch_price: 0.0,
            structure_choch_direction: 0,
            structure_luld_upper: 0.0,
            structure_luld_lower: 0.0,
            structure_52_week_high: 0.0,
            structure_52_week_low: 0.0,
            structure_prior_month_high: 0.0,
            structure_prior_month_low: 0.0,
            structure_prior_month_close: 0.0,
            qmd_structure_algorithm_version: structure.algorithm_version,
            qmd_structure_reference_price: structure.reference_price,
            qmd_structure_direction: structure.direction,
            qmd_structure_score: structure.direction as f64
                * structure.strength
                * structure.confidence
                * (0.5 + 0.5 * structure.agreement),
            qmd_structure_agreement: structure.agreement,
            qmd_structure_strength: structure.strength,
            qmd_structure_confidence: structure.confidence,
            qmd_structure_support_field: structure.support_field,
            qmd_structure_resistance_field: structure.resistance_field,
            qmd_structure_pressure_bias: structure.pressure_bias,
            qmd_structure_pressure_confidence: structure.pressure_confidence,
            qmd_structure_up_probability: structure.up_probability,
            qmd_structure_support_price: structure.support.price,
            qmd_structure_support_lower: structure.support.lower,
            qmd_structure_support_upper: structure.support.upper,
            qmd_structure_support_strength: structure.support.strength,
            qmd_structure_support_confidence: structure.support.confidence,
            qmd_structure_resistance_price: structure.resistance.price,
            qmd_structure_resistance_lower: structure.resistance.lower,
            qmd_structure_resistance_upper: structure.resistance.upper,
            qmd_structure_resistance_strength: structure.resistance.strength,
            qmd_structure_resistance_confidence: structure.resistance.confidence,
            qmd_structure_active_levels: structure.active_levels.clone(),
            qmd_structure_timeframe_states: structure.timeframe_states.clone(),
            qmd_structure_unified_levels: structure.unified_levels.clone(),
            qmd_structure_developing_high: structure.developing_high,
            qmd_structure_developing_low: structure.developing_low,
            qmd_structure_developing_direction: structure.developing_direction,
            qmd_structure_micro_direction: micro_state.direction,
            qmd_structure_micro_threshold: 0.0,
            qmd_structure_micro_swing_high: micro_state.swing_high,
            qmd_structure_micro_swing_low: micro_state.swing_low,
            qmd_structure_micro_support_price: micro_state.support.price,
            qmd_structure_micro_support_lower: micro_state.support.lower,
            qmd_structure_micro_support_upper: micro_state.support.upper,
            qmd_structure_micro_support_strength: micro_state.support.strength,
            qmd_structure_micro_support_confidence: micro_state.support.confidence,
            qmd_structure_micro_resistance_price: micro_state.resistance.price,
            qmd_structure_micro_resistance_lower: micro_state.resistance.lower,
            qmd_structure_micro_resistance_upper: micro_state.resistance.upper,
            qmd_structure_micro_resistance_strength: micro_state.resistance.strength,
            qmd_structure_micro_resistance_confidence: micro_state.resistance.confidence,
            qmd_structure_tactical_direction: tactical_state.direction,
            qmd_structure_tactical_threshold: 0.0,
            qmd_structure_tactical_swing_high: tactical_state.swing_high,
            qmd_structure_tactical_swing_low: tactical_state.swing_low,
            qmd_structure_tactical_support_price: tactical_state.support.price,
            qmd_structure_tactical_support_lower: tactical_state.support.lower,
            qmd_structure_tactical_support_upper: tactical_state.support.upper,
            qmd_structure_tactical_support_strength: tactical_state.support.strength,
            qmd_structure_tactical_support_confidence: tactical_state.support.confidence,
            qmd_structure_tactical_resistance_price: tactical_state.resistance.price,
            qmd_structure_tactical_resistance_lower: tactical_state.resistance.lower,
            qmd_structure_tactical_resistance_upper: tactical_state.resistance.upper,
            qmd_structure_tactical_resistance_strength: tactical_state.resistance.strength,
            qmd_structure_tactical_resistance_confidence: tactical_state.resistance.confidence,
            qmd_structure_context_direction: context_state.direction,
            qmd_structure_context_threshold: 0.0,
            qmd_structure_context_swing_high: context_state.swing_high,
            qmd_structure_context_swing_low: context_state.swing_low,
            qmd_structure_context_support_price: context_state.support.price,
            qmd_structure_context_support_lower: context_state.support.lower,
            qmd_structure_context_support_upper: context_state.support.upper,
            qmd_structure_context_support_strength: context_state.support.strength,
            qmd_structure_context_support_confidence: context_state.support.confidence,
            qmd_structure_context_resistance_price: context_state.resistance.price,
            qmd_structure_context_resistance_lower: context_state.resistance.lower,
            qmd_structure_context_resistance_upper: context_state.resistance.upper,
            qmd_structure_context_resistance_strength: context_state.resistance.strength,
            qmd_structure_context_resistance_confidence: context_state.resistance.confidence,
            qmd_structure_event_id: structure.last_event_id,
            qmd_structure_event_pivot_at_ms: structure.last_event_pivot_at_ms,
            qmd_structure_event_at_ms: structure.last_event_at_ms,
            qmd_structure_event_kind: structure.last_event_kind.clone(),
            qmd_structure_event_timeframe: structure.last_event_timeframe.clone(),
            qmd_structure_event_direction: structure.last_event_direction,
            qmd_structure_event_price: structure.last_event_price,
            qmd_structure_session_high: structure.session_high,
            qmd_structure_session_low: structure.session_low,
            qmd_structure_opening_range_high: structure.opening_range_high,
            qmd_structure_opening_range_low: structure.opening_range_low,
            qmd_structure_trade_volume_poc: structure.trade_volume_poc,
            qmd_structure_nearest_round: structure.nearest_round,
            qmd_structure_luld_upper: bar.estimated_luld_upper_price,
            qmd_structure_luld_lower: bar.estimated_luld_lower_price,
            qmd_structure_52_week_high: references.high_52_week,
            qmd_structure_52_week_low: references.low_52_week,
            qmd_structure_prior_month_high: references.prior_month_high,
            qmd_structure_prior_month_low: references.prior_month_low,
            qmd_structure_prior_month_close: references.prior_month_close,
            qmd_structure_snapshot: structure.as_ref().clone(),
            qmd_structure_events: bar.qmd_structure_events.clone(),
            microstructure_interval: MicrostructureIntervalFeatures::default(),
        }
    }
}

impl SessionVwapState {
    fn new() -> Self {
        Self {
            cumulative_trade_notional: 0.0,
            cumulative_volume: 0.0,
            anchor: None,
        }
    }

    fn update(&mut self, bar_start: DateTime<Utc>, volume: f64, interval_vwap: f64) -> f64 {
        let anchor = market_session_anchor_date(bar_start);
        if self.anchor != Some(anchor) {
            self.anchor = Some(anchor);
            self.cumulative_trade_notional = 0.0;
            self.cumulative_volume = 0.0;
        }
        if interval_vwap.is_finite() && interval_vwap > 0.0 && volume.is_finite() && volume > 0.0 {
            // Bar VWAP is exact eligible-trade notional divided by eligible
            // volume. Recombining those two additive primitives keeps the
            // anchored session value invariant under timeframe aggregation.
            self.cumulative_trade_notional += interval_vwap * volume;
            self.cumulative_volume += volume;
        }
        if self.cumulative_volume > 0.0 {
            self.cumulative_trade_notional / self.cumulative_volume
        } else {
            interval_vwap
        }
    }

    fn seed(
        &mut self,
        bar_start: DateTime<Utc>,
        cumulative_volume: f64,
        cumulative_trade_notional: f64,
    ) -> Result<(), String> {
        if !cumulative_volume.is_finite()
            || cumulative_volume < 0.0
            || !cumulative_trade_notional.is_finite()
            || cumulative_trade_notional < 0.0
        {
            return Err("session VWAP seed must contain finite non-negative primitives".into());
        }
        if cumulative_volume == 0.0 && cumulative_trade_notional != 0.0 {
            return Err("session VWAP seed cannot contain notional without volume".into());
        }
        self.anchor = Some(market_session_anchor_date(bar_start));
        self.cumulative_volume = cumulative_volume;
        self.cumulative_trade_notional = cumulative_trade_notional;
        Ok(())
    }
}

fn market_session_anchor_date(bar_start: DateTime<Utc>) -> NaiveDate {
    let local = bar_start.with_timezone(&New_York);
    let session_date = local.date_naive();
    if local.num_seconds_from_midnight() < PREMARKET_SESSION_START_SECONDS {
        session_date.pred_opt().unwrap_or(session_date)
    } else {
        session_date
    }
}

impl EmaState {
    fn new(period: usize) -> Self {
        Self {
            period: period as f64,
            value: None,
        }
    }

    fn update(&mut self, value: f64) -> f64 {
        let next = match self.value {
            Some(previous) => {
                let alpha = 2.0 / (self.period + 1.0);
                alpha * value + (1.0 - alpha) * previous
            }
            None => value,
        };
        self.value = Some(next);
        next
    }
}

impl RsiState {
    fn new(period: usize) -> Self {
        Self {
            avg_gain: 0.0,
            avg_loss: 0.0,
            count: 0,
            period,
            seed_gain_sum: 0.0,
            seed_loss_sum: 0.0,
        }
    }

    fn update(&mut self, change: f64) -> f64 {
        let gain = change.max(0.0);
        let loss = (-change).max(0.0);
        if self.count < self.period {
            self.seed_gain_sum += gain;
            self.seed_loss_sum += loss;
            self.count += 1;
            if self.count == self.period {
                self.avg_gain = self.seed_gain_sum / self.period as f64;
                self.avg_loss = self.seed_loss_sum / self.period as f64;
                return rsi_value(self.avg_gain, self.avg_loss);
            }
            return 0.0;
        }
        self.avg_gain = ((self.avg_gain * (self.period - 1) as f64) + gain) / self.period as f64;
        self.avg_loss = ((self.avg_loss * (self.period - 1) as f64) + loss) / self.period as f64;
        rsi_value(self.avg_gain, self.avg_loss)
    }
}

impl WilderAverage {
    fn new(period: usize) -> Self {
        Self {
            count: 0,
            period,
            seed_sum: 0.0,
            value: None,
        }
    }

    fn update(&mut self, value: f64) -> f64 {
        if self.count < self.period {
            self.seed_sum += value;
            self.count += 1;
            if self.count == self.period {
                let seeded = self.seed_sum / self.period as f64;
                self.value = Some(seeded);
                return seeded;
            }
            return 0.0;
        }
        let previous = self.value.unwrap_or(value);
        let next = ((previous * (self.period - 1) as f64) + value) / self.period as f64;
        self.value = Some(next);
        next
    }
}

impl RollingStats {
    fn new(window: usize) -> Self {
        Self {
            items: VecDeque::new(),
            sum: 0.0,
            sum_sq: 0.0,
            window,
        }
    }

    fn push(&mut self, value: f64) {
        self.items.push_back(value);
        self.sum += value;
        self.sum_sq += value * value;
        while self.items.len() > self.window {
            if let Some(old) = self.items.pop_front() {
                self.sum -= old;
                self.sum_sq -= old * old;
            }
        }
    }

    fn mean(&self) -> f64 {
        safe_div(self.sum, self.items.len() as f64)
    }

    fn stddev(&self) -> f64 {
        if self.items.len() < 2 {
            return 0.0;
        }
        let mean = self.mean();
        let variance = safe_div(self.sum_sq, self.items.len() as f64) - mean * mean;
        variance.max(0.0).sqrt()
    }
}

pub fn spawn_indicator_engines(
    indicators: SharedIndicatorStore,
    computation_targets: SharedComputationTargets,
    event_channel_capacity: usize,
    bar_channel_capacity: usize,
    writer_sender: mpsc::Sender<IndicatorRow>,
    scanner_sender: ScannerPrimitiveRouter,
    metrics: SharedMetrics,
) -> IndicatorEventRouter {
    let shard_count = indicators.shard_count();
    let per_shard_event_capacity = (event_channel_capacity / shard_count).max(1);
    let per_shard_bar_capacity = (bar_channel_capacity / shard_count).max(1);
    let mut event_senders = Vec::with_capacity(shard_count);
    let mut bar_senders = Vec::with_capacity(shard_count);
    for shard_id in 0..shard_count {
        let (event_sender, event_receiver) = mpsc::channel::<MarketEvent>(per_shard_event_capacity);
        let (bar_sender, bar_receiver) = mpsc::channel::<BarRow>(per_shard_bar_capacity);
        event_senders.push(event_sender);
        bar_senders.push(bar_sender);
        tokio::spawn(run_indicator_engine(
            shard_id,
            indicators.shard(shard_id),
            computation_targets.clone(),
            event_receiver,
            bar_receiver,
            writer_sender.clone(),
            scanner_sender.clone(),
            metrics.clone(),
        ));
    }
    let (bar_sender, bar_receiver) = mpsc::channel::<BarRow>(bar_channel_capacity.max(1));
    tokio::spawn(route_indicator_bars(
        bar_receiver,
        Arc::new(bar_senders),
        computation_targets.clone(),
    ));
    tokio::spawn(reclaim_unused_indicator_state(
        indicators.clone(),
        computation_targets.clone(),
    ));
    IndicatorEventRouter {
        bar_sender,
        computation_targets,
        event_senders: Arc::new(event_senders),
    }
}

async fn reclaim_unused_indicator_state(
    indicators: SharedIndicatorStore,
    computation_targets: SharedComputationTargets,
) {
    let mut timer = interval(Duration::from_secs(
        INDICATOR_STATE_RECLAIM_INTERVAL_SECONDS,
    ));
    timer.set_missed_tick_behavior(MissedTickBehavior::Skip);
    // The first tick is immediate; all stores are initially empty.
    loop {
        timer.tick().await;
        let reclaimed = indicators.reclaim_unused(&computation_targets).await;
        if reclaimed.total() > 0 {
            eprintln!(
                "Reclaimed {} inactive focused indicator state entries.",
                reclaimed.total()
            );
        }
    }
}

async fn route_indicator_bars(
    mut receiver: mpsc::Receiver<BarRow>,
    shard_senders: Arc<Vec<mpsc::Sender<BarRow>>>,
    computation_targets: SharedComputationTargets,
) {
    while let Some(row) = receiver.recv().await {
        if !computation_targets.requires_bar_computation(&row.sym, &row.timeframe) {
            continue;
        }
        let index = shard_index(&row.sym, shard_senders.len());
        if shard_senders[index].send(row).await.is_err() {
            eprintln!("Indicator bar shard receiver closed; could not route one finalized bar.");
        }
    }
}

async fn run_indicator_engine(
    shard_id: usize,
    shard: IndicatorShardStore,
    computation_targets: SharedComputationTargets,
    mut event_receiver: mpsc::Receiver<MarketEvent>,
    mut bar_receiver: mpsc::Receiver<BarRow>,
    writer_sender: mpsc::Sender<IndicatorRow>,
    scanner_sender: ScannerPrimitiveRouter,
    metrics: SharedMetrics,
) {
    loop {
        tokio::select! {
            event = event_receiver.recv() => {
                match event {
                    Some(event) => {
                        if computation_targets.requires_event_computation(event.ticker()) {
                            shard.apply_event(&event).await;
                        }
                    }
                    None => return,
                }
            }
            bar = bar_receiver.recv() => {
                match bar {
                    Some(bar) => {
                        if !computation_targets.requires_bar_computation(&bar.sym, &bar.timeframe) {
                            continue;
                        }
                        let source_bar = bar.clone();
                        let row = shard.apply_bar(bar).await;
                        if !row.close.is_finite() || row.close <= 0.0 {
                            continue;
                        }
                        if scanner_sender
                            .send_observation(source_bar, row.clone())
                            .await
                            .is_err()
                        {
                            metrics.inc_bar_scanner_dropped();
                            eprintln!("Scanner receiver closed; shard {shard_id} could not route one indicator observation.");
                        }
                        if writer_sender.send(row).await.is_err() {
                            eprintln!("Indicator writer receiver closed; shard {shard_id} could not persist one indicator row.");
                        }
                    }
                    None => return,
                }
            }
        }
    }
}

#[derive(Clone)]
pub struct IndicatorClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
}

impl IndicatorClickHouseWriter {
    pub fn new(config: GatewayConfig, metrics: SharedMetrics) -> Self {
        Self {
            client: Client::new(),
            config,
            metrics,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        if self.config.indicator_table.is_empty()
            || !self
                .config
                .indicator_table
                .chars()
                .all(|character| character.is_ascii_alphanumeric() || character == '_')
        {
            return Err("QMD_INDICATOR_TABLE must be a ClickHouse identifier".to_string());
        }
        self.execute(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.execute(
            &format!(
                r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                session_date Date,
                schema_version UInt16,
                timeframe LowCardinality(String),
                sym LowCardinality(String),
                bar_start DateTime64(3, 'UTC'),
                bar_end DateTime64(3, 'UTC'),
                close Float64,
                volume Float64,
                vwap Float64,
                ema_9 Float64,
                ema_20 Float64,
                ema_50 Float64,
                rsi_14 Float64,
                atr_14 Float64,
                macd_line Float64,
                macd_signal Float64,
                macd_histogram Float64,
                bollinger_mid_20 Float64,
                bollinger_upper_20 Float64,
                bollinger_lower_20 Float64,
                bollinger_std_20 Float64,
                close_sma_20 Float64,
                volume_sma_20 Float64,
                return_1_bar Float64,
                price_vs_ema20_pct Float64,
                price_vs_vwap_pct Float64,
                trend_score Float64,
                microstructure_unified_signal Float64,
                microstructure_unified_confidence Float64,
                microstructure_unified_action LowCardinality(String),
                microstructure_buy_trade_count UInt64,
                microstructure_sell_trade_count UInt64,
                microstructure_classified_trade_count UInt64,
                microstructure_eligible_trade_count UInt64,
                microstructure_buy_volume Float64,
                microstructure_sell_volume Float64,
                microstructure_signed_volume_delta Float64,
                microstructure_cumulative_signed_volume_delta Float64,
                microstructure_anchored_flow_relationship LowCardinality(String),
                microstructure_anchored_flow_relationship_score Float64,
                microstructure_transaction_imbalance Float64,
                microstructure_signed_volume_imbalance Float64,
                microstructure_level1_ofi_delta Float64,
                microstructure_cumulative_level1_ofi Float64,
                microstructure_level1_ofi Float64,
                microstructure_queue_imbalance Float64,
                microstructure_microprice_lean Float64,
                microstructure_midpoint_return_bps Float64,
                microstructure_trade_return_bps Float64,
                microstructure_aggressor_persistence Float64,
                microstructure_arrival_intensity_imbalance Float64,
                microstructure_arrival_rate_per_second Float64,
                microstructure_resiliency Float64,
                microstructure_aggressive_flow_score Float64,
                microstructure_displayed_liquidity_score Float64,
                microstructure_response_resiliency_score Float64,
                microstructure_regime_reliability Float64,
                flow_structure_composite_score Float64,
                flow_structure_composite_confidence Float64,
                flow_structure_composite_bias LowCardinality(String),
                flow_structure_composite_reason LowCardinality(String),
                liquidity_support_price Float64,
                liquidity_support_strength Float64,
                liquidity_support_confidence Float64,
                liquidity_resistance_price Float64,
                liquidity_resistance_strength Float64,
                liquidity_resistance_confidence Float64,
                liquidity_level_pressure Float64,
                market_level_support_score Float64,
                market_level_resistance_score Float64,
                market_level_bias Float64,
                structure_session_high Float64,
                structure_session_low Float64,
                structure_premarket_high Float64,
                structure_premarket_low Float64,
                structure_opening_range_high Float64,
                structure_opening_range_low Float64,
                structure_swing_high Float64,
                structure_swing_low Float64,
                structure_volume_poc Float64,
                structure_nearest_round Float64,
                structure_bos_price Float64,
                structure_bos_direction Int8,
                structure_choch_price Float64,
                structure_choch_direction Int8,
                structure_luld_upper Float64,
                structure_luld_lower Float64,
                structure_52_week_high Float64,
                structure_52_week_low Float64,
                structure_prior_month_high Float64,
                structure_prior_month_low Float64,
                structure_prior_month_close Float64,
                calculation_revision LowCardinality(String),
                source_revision String,
                complete UInt8,
                updated_at_utc DateTime64(3, 'UTC')
            )
            ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY session_date
            ORDER BY (session_date, timeframe, sym, bar_start)
            "#,
                table = self.config.indicator_table
            ),
            true,
        )
        .await?;
        self.execute(
            &format!(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
                self.config.indicator_table
            ),
            true,
        )
        .await?;
        self.execute(
            &format!(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS calculation_revision LowCardinality(String), ADD COLUMN IF NOT EXISTS source_revision String, ADD COLUMN IF NOT EXISTS complete UInt8, ADD COLUMN IF NOT EXISTS updated_at_utc DateTime64(3, 'UTC') DEFAULT now64(3)",
                self.config.indicator_table
            ),
            true,
        )
        .await?;
        self.execute(
            &r#"ALTER TABLE live_market_indicators
                ADD COLUMN IF NOT EXISTS microstructure_unified_signal Float64,
                ADD COLUMN IF NOT EXISTS microstructure_unified_confidence Float64,
                ADD COLUMN IF NOT EXISTS microstructure_unified_action LowCardinality(String),
                ADD COLUMN IF NOT EXISTS microstructure_buy_trade_count UInt64,
                ADD COLUMN IF NOT EXISTS microstructure_sell_trade_count UInt64,
                ADD COLUMN IF NOT EXISTS microstructure_classified_trade_count UInt64,
                ADD COLUMN IF NOT EXISTS microstructure_eligible_trade_count UInt64,
                ADD COLUMN IF NOT EXISTS microstructure_buy_volume Float64,
                ADD COLUMN IF NOT EXISTS microstructure_sell_volume Float64,
                ADD COLUMN IF NOT EXISTS microstructure_signed_volume_delta Float64,
                ADD COLUMN IF NOT EXISTS microstructure_cumulative_signed_volume_delta Float64,
                ADD COLUMN IF NOT EXISTS microstructure_anchored_flow_relationship LowCardinality(String),
                ADD COLUMN IF NOT EXISTS microstructure_anchored_flow_relationship_score Float64,
                ADD COLUMN IF NOT EXISTS microstructure_transaction_imbalance Float64,
                ADD COLUMN IF NOT EXISTS microstructure_signed_volume_imbalance Float64,
                ADD COLUMN IF NOT EXISTS microstructure_level1_ofi_delta Float64,
                ADD COLUMN IF NOT EXISTS microstructure_cumulative_level1_ofi Float64,
                ADD COLUMN IF NOT EXISTS microstructure_level1_ofi Float64,
                ADD COLUMN IF NOT EXISTS microstructure_queue_imbalance Float64,
                ADD COLUMN IF NOT EXISTS microstructure_microprice_lean Float64,
                ADD COLUMN IF NOT EXISTS microstructure_midpoint_return_bps Float64,
                ADD COLUMN IF NOT EXISTS microstructure_trade_return_bps Float64,
                ADD COLUMN IF NOT EXISTS microstructure_aggressor_persistence Float64,
                ADD COLUMN IF NOT EXISTS microstructure_arrival_intensity_imbalance Float64,
                ADD COLUMN IF NOT EXISTS microstructure_arrival_rate_per_second Float64,
                ADD COLUMN IF NOT EXISTS microstructure_resiliency Float64,
                ADD COLUMN IF NOT EXISTS microstructure_aggressive_flow_score Float64,
                ADD COLUMN IF NOT EXISTS microstructure_displayed_liquidity_score Float64,
                ADD COLUMN IF NOT EXISTS microstructure_response_resiliency_score Float64,
                ADD COLUMN IF NOT EXISTS microstructure_regime_reliability Float64,
                ADD COLUMN IF NOT EXISTS flow_structure_composite_score Float64,
                ADD COLUMN IF NOT EXISTS flow_structure_composite_confidence Float64,
                ADD COLUMN IF NOT EXISTS flow_structure_composite_bias LowCardinality(String),
                ADD COLUMN IF NOT EXISTS flow_structure_composite_reason LowCardinality(String),
                ADD COLUMN IF NOT EXISTS liquidity_support_price Float64,
                ADD COLUMN IF NOT EXISTS liquidity_support_strength Float64,
                ADD COLUMN IF NOT EXISTS liquidity_support_confidence Float64,
                ADD COLUMN IF NOT EXISTS liquidity_resistance_price Float64,
                ADD COLUMN IF NOT EXISTS liquidity_resistance_strength Float64,
                ADD COLUMN IF NOT EXISTS liquidity_resistance_confidence Float64,
                ADD COLUMN IF NOT EXISTS liquidity_level_pressure Float64,
                ADD COLUMN IF NOT EXISTS market_level_support_score Float64,
                ADD COLUMN IF NOT EXISTS market_level_resistance_score Float64,
                ADD COLUMN IF NOT EXISTS market_level_bias Float64,
                ADD COLUMN IF NOT EXISTS structure_session_high Float64,
                ADD COLUMN IF NOT EXISTS structure_session_low Float64,
                ADD COLUMN IF NOT EXISTS structure_premarket_high Float64,
                ADD COLUMN IF NOT EXISTS structure_premarket_low Float64,
                ADD COLUMN IF NOT EXISTS structure_opening_range_high Float64,
                ADD COLUMN IF NOT EXISTS structure_opening_range_low Float64,
                ADD COLUMN IF NOT EXISTS structure_swing_high Float64,
                ADD COLUMN IF NOT EXISTS structure_swing_low Float64,
                ADD COLUMN IF NOT EXISTS structure_volume_poc Float64,
                ADD COLUMN IF NOT EXISTS structure_nearest_round Float64,
                ADD COLUMN IF NOT EXISTS structure_bos_price Float64,
                ADD COLUMN IF NOT EXISTS structure_bos_direction Int8,
                ADD COLUMN IF NOT EXISTS structure_choch_price Float64,
                ADD COLUMN IF NOT EXISTS structure_choch_direction Int8,
                ADD COLUMN IF NOT EXISTS structure_luld_upper Float64,
                ADD COLUMN IF NOT EXISTS structure_luld_lower Float64,
                ADD COLUMN IF NOT EXISTS structure_52_week_high Float64,
                ADD COLUMN IF NOT EXISTS structure_52_week_low Float64,
                ADD COLUMN IF NOT EXISTS structure_prior_month_high Float64,
                ADD COLUMN IF NOT EXISTS structure_prior_month_low Float64,
                ADD COLUMN IF NOT EXISTS structure_prior_month_close Float64"#.replace("live_market_indicators", &self.config.indicator_table),
            true,
        )
        .await?;
        self.execute(
            &r#"ALTER TABLE live_market_indicators
                ADD COLUMN IF NOT EXISTS qmd_structure_algorithm_version UInt16,
                ADD COLUMN IF NOT EXISTS qmd_structure_reference_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_score Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_agreement Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_field Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_field Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_pressure_bias Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_pressure_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_up_probability Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_support_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_resistance_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_threshold Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_swing_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_swing_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_support_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_support_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_support_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_support_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_support_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_resistance_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_resistance_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_resistance_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_resistance_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_micro_resistance_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_threshold Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_swing_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_swing_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_support_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_support_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_support_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_support_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_support_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_resistance_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_resistance_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_resistance_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_resistance_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_tactical_resistance_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_threshold Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_swing_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_swing_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_support_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_support_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_support_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_support_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_support_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_resistance_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_resistance_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_resistance_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_resistance_strength Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_context_resistance_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_id UInt64,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_pivot_at_ms Int64,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_at_ms Int64,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_kind LowCardinality(String),
                ADD COLUMN IF NOT EXISTS qmd_structure_event_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_session_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_session_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_opening_range_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_opening_range_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_trade_volume_poc Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_nearest_round Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_luld_upper Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_luld_lower Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_52_week_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_52_week_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_prior_month_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_prior_month_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_prior_month_close Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_developing_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_developing_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_developing_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_timeframe LowCardinality(String)"#
                .replace("live_market_indicators", &self.config.indicator_table),
            true,
        )
        .await?;
        self.execute(
            r#"CREATE TABLE IF NOT EXISTS qmd_structure_events_v2
            (
                event_date Date,
                algorithm_version UInt16,
                event_id UInt64,
                level_id UInt64,
                sym LowCardinality(String),
                timeframe LowCardinality(String),
                event_kind LowCardinality(String),
                direction Int8,
                price Float64,
                lower Float64,
                upper Float64,
                strength Float64,
                confidence Float64,
                lifecycle LowCardinality(String),
                total_volume Float64,
                buy_volume Float64,
                sell_volume Float64,
                neutral_volume Float64,
                trade_count UInt64,
                pivot_at DateTime64(6, 'UTC'),
                confirmed_at DateTime64(6, 'UTC')
            )
            ENGINE = ReplacingMergeTree
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (sym, confirmed_at, timeframe, event_kind, event_id)"#,
            true,
        )
        .await?;
        self.execute(
            r#"CREATE TABLE IF NOT EXISTS qmd_structure_state_v2
            (
                algorithm_version UInt16,
                sym LowCardinality(String),
                updated_at DateTime64(3, 'UTC'),
                snapshot_json String
            )
            ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY sym"#,
            true,
        )
        .await?;
        self.execute(
            r#"CREATE TABLE IF NOT EXISTS qmd_structure_focus_registry_v1
            (
                sym LowCardinality(String),
                next_advance_at_ms Int64,
                state LowCardinality(String) DEFAULT 'active',
                error_code LowCardinality(String) DEFAULT '',
                retry_action LowCardinality(String) DEFAULT '',
                error_detail String DEFAULT '',
                updated_at DateTime64(3, 'UTC')
            )
            ENGINE = ReplacingMergeTree(updated_at)
            ORDER BY sym"#,
            true,
        )
        .await?;
        self.execute(
            r#"ALTER TABLE qmd_structure_focus_registry_v1
                ADD COLUMN IF NOT EXISTS state LowCardinality(String) DEFAULT 'active',
                ADD COLUMN IF NOT EXISTS error_code LowCardinality(String) DEFAULT '',
                ADD COLUMN IF NOT EXISTS retry_action LowCardinality(String) DEFAULT '',
                ADD COLUMN IF NOT EXISTS error_detail String DEFAULT ''"#,
            true,
        )
        .await?;
        Ok(())
    }

    pub async fn persist_reconciled_rows(&self, rows: &[IndicatorRow]) -> Result<(), String> {
        if rows.is_empty() || !self.config.persist_indicators {
            return Ok(());
        }
        self.insert_indicators(rows).await
    }

    pub async fn load_structure_checkpoints(
        &self,
    ) -> Result<Vec<(String, GenericStructureCheckpoint)>, String> {
        let sql = format!(
            r#"SELECT sym, argMax(snapshot_json, updated_at) AS snapshot_json
            FROM qmd_structure_state_v2
            WHERE algorithm_version = {}
            GROUP BY sym
            FORMAT JSONEachRow"#,
            crate::generic_structure::GENERIC_STRUCTURE_ALGORITHM_VERSION
        );
        let text = self.query(&sql, true).await?;
        text.lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let value = serde_json::from_str::<serde_json::Value>(line)
                    .map_err(|error| format!("invalid QMD structure state row: {error}"))?;
                let sym = value
                    .get("sym")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default()
                    .to_ascii_uppercase();
                let checkpoint_json = value
                    .get("snapshot_json")
                    .and_then(serde_json::Value::as_str)
                    .unwrap_or_default();
                if sym.is_empty() || checkpoint_json.is_empty() {
                    return Err("QMD structure state row omitted symbol or checkpoint".to_string());
                }
                let checkpoint = serde_json::from_str::<GenericStructureCheckpoint>(
                    checkpoint_json,
                )
                .map_err(|error| format!("invalid QMD structure checkpoint for {sym}: {error}"))?;
                Ok((sym, checkpoint))
            })
            .collect()
    }

    pub async fn load_structure_checkpoint(
        &self,
        ticker: &str,
    ) -> Result<Option<GenericStructureCheckpoint>, String> {
        let sym = ticker.trim().to_ascii_uppercase();
        if sym.is_empty()
            || sym.len() > 32
            || !sym
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        {
            return Err("invalid QMD structure checkpoint ticker".to_string());
        }
        let escaped = sym.replace('\'', "''");
        let sql = format!(
            r#"SELECT argMax(snapshot_json, updated_at) AS snapshot_json
            FROM qmd_structure_state_v2
            WHERE algorithm_version = {}
              AND sym = '{}'
            HAVING length(snapshot_json) > 0
            FORMAT JSONEachRow"#,
            crate::generic_structure::GENERIC_STRUCTURE_ALGORITHM_VERSION,
            escaped,
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let value = serde_json::from_str::<serde_json::Value>(line)
            .map_err(|error| format!("invalid QMD structure state row: {error}"))?;
        let checkpoint_json = value
            .get("snapshot_json")
            .and_then(serde_json::Value::as_str)
            .unwrap_or_default();
        if checkpoint_json.is_empty() {
            return Ok(None);
        }
        serde_json::from_str(checkpoint_json)
            .map(Some)
            .map_err(|error| format!("invalid QMD structure checkpoint for {sym}: {error}"))
    }

    pub async fn persist_structure_checkpoint(
        &self,
        checkpoint: &GenericStructureCheckpoint,
    ) -> Result<(), String> {
        self.insert_structure_states(&[(checkpoint.sym.clone(), checkpoint.clone())])
            .await
    }

    pub async fn load_structure_focus_registry(
        &self,
        limit: usize,
    ) -> Result<(Vec<(String, DateTime<Utc>)>, usize), String> {
        let bounded = limit.max(1);
        let sql = format!(
            r#"SELECT
                sym,
                argMax(next_advance_at_ms, updated_at) AS next_advance_at_ms,
                argMax(state, updated_at) AS state
            FROM qmd_structure_focus_registry_v1
            GROUP BY sym
            ORDER BY sym
            LIMIT {}
            FORMAT JSONEachRow"#,
            bounded.saturating_add(1),
        );
        let text = self.query(&sql, true).await?;
        let mut entries = Vec::new();
        let mut blocked = 0_usize;
        let rows = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .collect::<Vec<_>>();
        if rows.len() > bounded {
            return Err(format!(
                "structure focus registry exceeds configured limit {bounded}"
            ));
        }
        for line in rows {
            let value = serde_json::from_str::<serde_json::Value>(line)
                .map_err(|error| format!("invalid structure focus registry row: {error}"))?;
            let sym = value
                .get("sym")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_ascii_uppercase();
            let millis = value
                .get("next_advance_at_ms")
                .and_then(|value| {
                    value
                        .as_i64()
                        .or_else(|| value.as_str().and_then(|raw| raw.parse().ok()))
                })
                .unwrap_or_default();
            let state = value
                .get("state")
                .and_then(serde_json::Value::as_str)
                .unwrap_or("active");
            if state == "blocked" {
                blocked = blocked.saturating_add(1);
                continue;
            }
            let next_due = DateTime::<Utc>::from_timestamp_millis(millis)
                .ok_or_else(|| format!("invalid structure focus registry time for {sym}"))?;
            entries.push((sym, next_due));
        }
        Ok((entries, blocked))
    }

    pub async fn persist_structure_focus_registry(
        &self,
        ticker: &str,
        next_advance_at: DateTime<Utc>,
    ) -> Result<(), String> {
        let body = serde_json::to_string(&json!({
            "sym": ticker.trim().to_ascii_uppercase(),
            "next_advance_at_ms": next_advance_at.timestamp_millis(),
            "state": "active",
            "error_code": "",
            "retry_action": "",
            "error_detail": "",
            "updated_at": clickhouse_datetime64(&Utc::now()),
        }))
        .map_err(|error| format!("failed to serialize structure focus registry: {error}"))?;
        self.query_with_body(
            "INSERT INTO qmd_structure_focus_registry_v1 FORMAT JSONEachRow",
            body,
        )
        .await
    }

    pub async fn persist_structure_focus_blocked(
        &self,
        ticker: &str,
        error_code: &str,
        retry_action: &str,
        error_detail: &str,
    ) -> Result<(), String> {
        let body = serde_json::to_string(&json!({
            "sym": ticker.trim().to_ascii_uppercase(),
            "next_advance_at_ms": Utc::now().timestamp_millis(),
            "state": "blocked",
            "error_code": error_code,
            "retry_action": retry_action,
            "error_detail": error_detail,
            "updated_at": clickhouse_datetime64(&Utc::now()),
        }))
        .map_err(|error| {
            format!("failed to serialize blocked structure focus registry: {error}")
        })?;
        self.query_with_body(
            "INSERT INTO qmd_structure_focus_registry_v1 FORMAT JSONEachRow",
            body,
        )
        .await
    }

    pub async fn load_structure_focus_status(
        &self,
        ticker: &str,
    ) -> Result<Option<(String, String, String)>, String> {
        let sym = ticker.trim().to_ascii_uppercase();
        if sym.is_empty()
            || sym.len() > 32
            || !sym
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        {
            return Err("invalid structure focus registry ticker".to_string());
        }
        let sql = format!(
            r#"SELECT
                argMax(state, updated_at) AS state,
                argMax(error_code, updated_at) AS error_code,
                argMax(retry_action, updated_at) AS retry_action
            FROM qmd_structure_focus_registry_v1
            WHERE sym = '{}'
            HAVING length(state) > 0
            FORMAT JSONEachRow"#,
            sym.replace('\'', "''"),
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let value = serde_json::from_str::<serde_json::Value>(line)
            .map_err(|error| format!("invalid structure focus status row: {error}"))?;
        Ok(Some((
            value
                .get("state")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
            value
                .get("error_code")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
            value
                .get("retry_action")
                .and_then(serde_json::Value::as_str)
                .unwrap_or_default()
                .to_string(),
        )))
    }

    pub async fn run(
        self,
        mut receiver: mpsc::Receiver<IndicatorRow>,
        bars: SharedBarStore,
        mut structure_watermarks: HashMap<String, (i64, u64)>,
    ) {
        if !self.config.persist_indicators && !self.config.persist_structure_events {
            while receiver.recv().await.is_some() {}
            return;
        }
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                row = receiver.recv() => {
                    match row {
                        Some(row) => batch.push(row),
                        None => {
                            loop {
                                let flushed = self.flush(&mut batch, &bars, &mut structure_watermarks).await;
                                if flushed && batch.is_empty() {
                                    return;
                                }
                                sleep(Duration::from_millis(250)).await;
                            }
                        }
                    }
                    if batch.len() >= self.config.max_clickhouse_batch {
                        self.flush(&mut batch, &bars, &mut structure_watermarks).await;
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut batch, &bars, &mut structure_watermarks).await;
                }
            }
            self.metrics
                .set_lane_pending("indicators", (batch.len() + receiver.len()) as u64);
        }
    }

    async fn flush(
        &self,
        batch: &mut Vec<IndicatorRow>,
        bars: &SharedBarStore,
        structure_watermarks: &mut HashMap<String, (i64, u64)>,
    ) -> bool {
        let mut structure_events = batch
            .iter()
            .flat_map(|row| row.qmd_structure_events.iter().cloned())
            .fold(
                HashMap::<u64, GenericStructureEvent>::new(),
                |mut events, event| {
                    events.entry(event.event_id).or_insert(event);
                    events
                },
            )
            .into_values()
            .collect::<Vec<_>>();
        structure_events.sort_by_key(|event| (event.confirmed_at, event.event_id));
        if self.config.persist_indicators && !batch.is_empty() {
            self.metrics
                .set_lane_pending("indicators", batch.len() as u64);
            if let Err(error) = self.insert_indicators(batch).await {
                self.metrics.record_lane_failure("indicators", &error);
                eprintln!("ClickHouse indicator insert failed: {error}");
                return false;
            }
            self.metrics.record_lane_success(
                "indicators",
                batch.len() as u64,
                "Committed closed indicator rows.",
            );
            self.metrics.set_lane_pending("indicators", 0);
        }
        if self.config.persist_structure_events && !structure_events.is_empty() {
            self.metrics
                .set_lane_pending("structure_events", structure_events.len() as u64);
            if let Err(error) = self.insert_structure_events(&structure_events).await {
                self.metrics.record_lane_failure("structure_events", &error);
                eprintln!("ClickHouse QMD structure-event insert failed: {error}");
                return false;
            }
            self.metrics.record_lane_success(
                "structure_events",
                structure_events.len() as u64,
                "Committed canonical QMD structure events.",
            );
            self.metrics.set_lane_pending("structure_events", 0);
        }
        let structure_states = if self.config.persist_structure_events {
            bars.take_structure_checkpoints_since(
                structure_watermarks,
                STRUCTURE_CHECKPOINT_BATCH_LIMIT,
            )
            .await
        } else {
            Vec::new()
        };
        if !structure_states.is_empty() {
            if let Err(error) = self.insert_structure_states(&structure_states).await {
                bars.requeue_structure_checkpoints(
                    structure_states.iter().map(|(sym, _)| sym.clone()),
                )
                .await;
                self.metrics.record_lane_failure("structure_events", &error);
                eprintln!("ClickHouse QMD structure-state insert failed: {error}");
                return false;
            }
            for (sym, checkpoint) in &structure_states {
                if let Some(updated_at) = checkpoint.updated_at {
                    structure_watermarks.insert(
                        sym.clone(),
                        (
                            updated_at.timestamp_millis(),
                            checkpoint.last_arrival_sequence,
                        ),
                    );
                }
            }
        }
        batch.clear();
        true
    }

    async fn insert_indicators(&self, rows: &[IndicatorRow]) -> Result<(), String> {
        let updated_at = clickhouse_datetime64(&Utc::now());
        let body = rows
            .iter()
            .map(|row| {
                let value = durable_indicator_insert_row(
                    row,
                    self.config.qmd_run_id.as_str(),
                    updated_at.as_str(),
                );
                serde_json::to_string(&value).unwrap_or_else(|_| "{}".to_string())
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow",
                self.config.indicator_table
            ),
            body,
        )
        .await
    }

    async fn insert_structure_events(
        &self,
        events: &[GenericStructureEvent],
    ) -> Result<(), String> {
        let body = events
            .iter()
            .map(|event| {
                serde_json::to_string(&json!({
                    "event_date": event.confirmed_at.date_naive().to_string(),
                    "algorithm_version": event.algorithm_version,
                    "event_id": event.event_id,
                    "level_id": event.level_id,
                    "sym": &event.sym,
                    "timeframe": &event.timeframe,
                    "event_kind": &event.event_kind,
                    "direction": event.direction,
                    "price": event.price,
                    "lower": event.lower,
                    "upper": event.upper,
                    "strength": event.strength,
                    "confidence": event.confidence,
                    "lifecycle": &event.lifecycle,
                    "total_volume": event.total_volume,
                    "buy_volume": event.buy_volume,
                    "sell_volume": event.sell_volume,
                    "neutral_volume": event.neutral_volume,
                    "trade_count": event.trade_count,
                    "pivot_at": event.pivot_at.format("%Y-%m-%d %H:%M:%S%.6f").to_string(),
                    "confirmed_at": event.confirmed_at.format("%Y-%m-%d %H:%M:%S%.6f").to_string(),
                }))
                .unwrap_or_else(|_| "{}".to_string())
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            "INSERT INTO qmd_structure_events_v2 FORMAT JSONEachRow",
            body,
        )
        .await
    }

    async fn insert_structure_states(
        &self,
        rows: &[(String, GenericStructureCheckpoint)],
    ) -> Result<(), String> {
        let body = rows
            .iter()
            .filter_map(|(sym, checkpoint)| {
                let updated_at = checkpoint.updated_at.as_ref()?;
                let checkpoint_json =
                    serde_json::to_string(checkpoint).unwrap_or_else(|_| "{}".to_string());
                Some(
                    serde_json::to_string(&json!({
                        "algorithm_version": checkpoint.algorithm_version,
                        "sym": sym,
                        "updated_at": clickhouse_datetime64(updated_at),
                        "snapshot_json": checkpoint_json,
                    }))
                    .unwrap_or_else(|_| "{}".to_string()),
                )
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            "INSERT INTO qmd_structure_state_v2 FORMAT JSONEachRow",
            body,
        )
        .await
    }

    async fn execute(&self, sql: &str, use_database: bool) -> Result<(), String> {
        self.query(sql, use_database).await.map(|_| ())
    }

    async fn query_with_body(&self, sql: &str, body: String) -> Result<(), String> {
        self.query(&format!("{sql}\n{body}"), true)
            .await
            .map(|_| ())
    }

    async fn query(&self, body: &str, use_database: bool) -> Result<String, String> {
        let url = if use_database {
            format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            )
        } else {
            format!("{}/", self.config.clickhouse_url)
        };
        let mut request = self
            .client
            .post(url)
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(body.to_string());
        let password = self.config.clickhouse_password();
        if !password.is_empty() {
            request = request.header("X-ClickHouse-Key", password);
        }
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("ClickHouse HTTP {status}: {text}"));
        }
        Ok(text)
    }
}

fn durable_indicator_insert_row(
    row: &IndicatorRow,
    source_revision: &str,
    updated_at_utc: &str,
) -> serde_json::Value {
    let mut value = indicator_insert_row(row);
    if let Some(object) = value.as_object_mut() {
        object.insert(
            "calculation_revision".to_string(),
            json!(INDICATOR_CALCULATION_REVISION),
        );
        object.insert("source_revision".to_string(), json!(source_revision));
        object.insert("complete".to_string(), json!(1u8));
        object.insert("updated_at_utc".to_string(), json!(updated_at_utc));
    }
    value
}

fn indicator_insert_row(row: &IndicatorRow) -> serde_json::Value {
    let mut value = serde_json::to_value(row).unwrap_or_else(|_| json!({}));
    if let Some(object) = value.as_object_mut() {
        if let Ok(serde_json::Value::Object(bar_fields)) = serde_json::to_value(&row.bar_fields) {
            // These fields are flattened for scanner/rule consumers but remain
            // authoritative in the bar table. Do not duplicate them into the
            // narrower durable indicator table.
            for field in bar_fields.keys() {
                object.remove(field);
            }
        }
        // Active candidates are a bounded streaming/chart state carried by the
        // canonical in-memory snapshot. Durable reconstruction comes from the
        // versioned generic-structure checkpoint and event tables, so the wide
        // per-bar indicator table intentionally does not duplicate this array.
        object.remove("qmd_structure_active_levels");
        object.remove("qmd_structure_timeframe_states");
        object.remove("qmd_structure_unified_levels");
        object.insert(
            "bar_start".to_string(),
            serde_json::Value::String(clickhouse_datetime64(&row.bar_start)),
        );
        object.insert(
            "bar_end".to_string(),
            serde_json::Value::String(clickhouse_datetime64(&row.bar_end)),
        );
    }
    value
}

fn round_indicator_value(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

fn canonical_timeframe(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn seconds_between(older: DateTime<Utc>, newer: DateTime<Utc>) -> i64 {
    newer.signed_duration_since(older).num_seconds()
}

fn rsi_value(avg_gain: f64, avg_loss: f64) -> f64 {
    if avg_loss <= 0.0 {
        return 100.0;
    }
    100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
}

fn trend_score(
    close: f64,
    ema_9: f64,
    ema_20: f64,
    ema_50: f64,
    rsi_14: f64,
    macd_histogram: f64,
) -> f64 {
    let mut score = 0.0;
    if close > ema_20 {
        score += 1.0;
    }
    if ema_9 > ema_20 {
        score += 1.0;
    }
    if ema_20 > ema_50 {
        score += 1.0;
    }
    if rsi_14 >= 50.0 {
        score += 1.0;
    }
    if macd_histogram > 0.0 {
        score += 1.0;
    }
    score / 5.0
}

fn shard_index(ticker: &str, shard_count: usize) -> usize {
    let mut hash = 14_695_981_039_346_656_037_u64;
    for byte in ticker.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(1_099_511_628_211);
    }
    (hash as usize) % shard_count.max(1)
}

fn pct_change(current: f64, previous: f64) -> f64 {
    safe_div(current - previous, previous) * 100.0
}

fn safe_div(numerator: f64, denominator: f64) -> f64 {
    if denominator.abs() < f64::EPSILON || !numerator.is_finite() || !denominator.is_finite() {
        0.0
    } else {
        numerator / denominator
    }
}

#[cfg(test)]
mod tests {
    use super::{
        anchored_flow_relationship, anchored_market_session_date,
        calculate_flow_structure_composite, durable_indicator_insert_row, indicator_insert_row,
        market_structure_reference_sql, parse_market_structure_reference_rows,
        summarize_canonical_composites, BarIndicatorCalculator, IndicatorKey,
        MicrostructureCumulativeFlow, MicrostructureSampleAggregate, SessionVwapState,
        SharedIndicatorStore, TickState, INDICATOR_SCHEMA_VERSION,
    };
    use crate::bars::{TradeAggregationRules, TradeUpdateRule};
    use crate::capability_catalog::ExecutionScope;
    use crate::computation_targets::{ComputationTargetRequest, SharedComputationTargets};
    use crate::microstructure_interval::MicrostructureIntervalWindow;
    use crate::scanner::tests::base_bar;
    use chrono::{TimeZone, Utc};
    use std::collections::{HashMap, VecDeque};

    #[test]
    fn scanner_indicator_row_projects_registered_bar_change_fields() {
        let bar = base_bar();
        let mut calculator = BarIndicatorCalculator::new();
        let row = calculator.apply_bar(&bar);
        let value = serde_json::to_value(row).expect("indicator row should serialize");

        assert_eq!(value["schema_version"], INDICATOR_SCHEMA_VERSION);
        assert_eq!(value["open"], bar.open);
        assert_eq!(value["price_change_1_bar_pct"], 1.0);
        assert_eq!(value["trade_count_change"], 25.0);
        assert_eq!(value["volume_change"], 1_000.0);
        assert_eq!(value["quote_rate_change_pct"], 20.0);
        assert_eq!(value["spread_bps_ratio"], 0.8333);

        let insert = indicator_insert_row(&calculator.apply_bar(&bar));
        assert!(insert.get("price_change_1_bar_pct").is_none());
        assert!(insert.get("trade_count_change").is_none());
        assert!(insert.get("ema_9").is_some());

        let durable = durable_indicator_insert_row(
            &calculator.apply_bar(&bar),
            "run-1",
            "2026-08-23 17:30:12.345",
        );
        assert_eq!(durable["updated_at_utc"], "2026-08-23 17:30:12.345");
        assert_eq!(durable["source_revision"], "run-1");
        assert_eq!(durable["complete"], 1);
    }

    #[test]
    fn historical_cache_compaction_preserves_the_wire_projection() {
        let bar = base_bar();
        let mut calculator = BarIndicatorCalculator::new();
        let row = calculator.apply_bar(&bar);
        let unified_level_count = row.qmd_structure_unified_levels.len();
        let mut wire_before = serde_json::to_value(&row).expect("indicator row should serialize");
        wire_before
            .as_object_mut()
            .unwrap()
            .remove("qmd_structure_active_levels");
        wire_before
            .as_object_mut()
            .unwrap()
            .remove("qmd_structure_timeframe_states");

        let compacted = row.compact_for_historical_cache();
        let mut wire_after =
            serde_json::to_value(&compacted).expect("compacted indicator row should serialize");
        wire_after
            .as_object_mut()
            .unwrap()
            .remove("qmd_structure_active_levels");
        wire_after
            .as_object_mut()
            .unwrap()
            .remove("qmd_structure_timeframe_states");

        assert_eq!(wire_after, wire_before);
        assert!(compacted.qmd_structure_active_levels.is_empty());
        assert!(compacted.qmd_structure_timeframe_states.is_empty());
        assert_eq!(
            compacted.qmd_structure_unified_levels.len(),
            unified_level_count
        );
        assert!(compacted
            .qmd_structure_unified_levels
            .iter()
            .all(|level| level.sources.is_empty()));
        assert!(compacted.qmd_structure_snapshot.active_levels.is_empty());
        assert!(compacted.qmd_structure_snapshot.timeframe_states.is_empty());
        assert!(compacted.qmd_structure_events.is_empty());
        assert_eq!(compacted.microstructure_interval.ofi_numerator, 0.0);
    }

    #[tokio::test]
    async fn price_indicators_carry_across_invalid_buckets_without_zero_pollution() {
        let store = SharedIndicatorStore::new(
            100,
            HashMap::new(),
            300,
            1,
            TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap(),
            HashMap::new(),
        );
        let mut first = base_bar();
        first.sym = "AAPL".to_string();
        let first_row = store.apply_reconciliation_bar(first.clone()).await;

        let mut invalid = first.clone();
        invalid.bar_start = first.bar_end;
        invalid.bar_end = first.bar_end + chrono::Duration::seconds(10);
        invalid.open = 0.0;
        invalid.high = 0.0;
        invalid.low = 0.0;
        invalid.close = 0.0;
        let carried = store.apply_reconciliation_bar(invalid.clone()).await;
        assert_eq!(carried.close, first_row.close);
        assert_eq!(carried.ema_20, first_row.ema_20);

        let mut second = first;
        second.bar_start = invalid.bar_end;
        second.bar_end = invalid.bar_end + chrono::Duration::seconds(10);
        second.open = 11.0;
        second.high = 11.0;
        second.low = 11.0;
        second.close = 11.0;
        let second_row = store.apply_reconciliation_bar(second).await;
        assert!(second_row.ema_20 > first_row.ema_20);
        assert!(second_row.ema_20 < 11.0);
        assert_eq!(store.snapshot("AAPL", "10s", 10).await.history.len(), 3);
    }

    #[tokio::test]
    async fn higher_timeframes_project_canonical_base_vwap_including_volume_only_buckets() {
        let store = SharedIndicatorStore::new(
            100,
            HashMap::new(),
            300,
            1,
            TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap(),
            HashMap::new(),
        );
        let mut first = base_bar();
        first.sym = "AAPL".to_string();
        first.timeframe = "100ms".to_string();
        first.volume = 100.0;
        first.vwap = 10.0;
        store.apply_reconciliation_bar(first.clone()).await;

        let mut volume_only = first.clone();
        volume_only.bar_start = first.bar_end;
        volume_only.bar_end = first.bar_end + chrono::Duration::milliseconds(100);
        volume_only.open = 0.0;
        volume_only.high = 0.0;
        volume_only.low = 0.0;
        volume_only.close = 0.0;
        volume_only.volume = 300.0;
        volume_only.vwap = 20.0;
        let carried = store.apply_reconciliation_bar(volume_only.clone()).await;
        assert!((carried.vwap - 17.5).abs() < 1e-9);

        let mut minute = first;
        minute.timeframe = "1m".to_string();
        minute.bar_end = volume_only.bar_end;
        minute.volume = 400.0;
        minute.vwap = 999.0;
        let projected = store.apply_reconciliation_bar(minute).await;

        assert!((projected.vwap - 17.5).abs() < 1e-9);
        assert!(
            (projected.price_vs_vwap_pct - (projected.close / 17.5 - 1.0) * 100.0).abs() < 1e-9
        );
    }

    #[tokio::test]
    async fn high_frequency_history_is_bounded_with_full_current_authority() {
        let store = SharedIndicatorStore::new(
            6_000,
            HashMap::new(),
            300,
            1,
            TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap(),
            HashMap::new(),
        );
        let mut bar = base_bar();
        bar.sym = "AAPL".to_string();
        bar.timeframe = "100ms".to_string();
        let initial_start = bar.bar_start;
        let mut latest = None;
        for index in 0..200 {
            bar.bar_start = initial_start + chrono::Duration::milliseconds(index * 100);
            bar.bar_end = bar.bar_start + chrono::Duration::milliseconds(100);
            bar.close = 10.0 + index as f64;
            latest = Some(store.apply_reconciliation_bar(bar.clone()).await);
        }

        let snapshot = store.snapshot("AAPL", "100ms", 6_000).await;
        assert_eq!(snapshot.history.len(), 128);
        let current = snapshot.current.expect("current row remains authoritative");
        assert_eq!(current.bar_end, bar.bar_end);
        assert_eq!(current.close, latest.expect("latest indicator row").close);
    }

    #[test]
    fn daily_structure_reference_contract_is_causal_and_parseable() {
        let as_of = Utc.with_ymd_and_hms(2026, 7, 14, 13, 45, 0).unwrap();
        let sql = market_structure_reference_sql(
            "market_sip_compact",
            "daily_session_bars_by_symbol_time_v1",
            Some("AAPL"),
            as_of,
        )
        .unwrap();
        assert!(sql.contains("daily_session_bars_by_symbol_time_v1"));
        assert!(sql.contains("session_date < toDate('2026-07-14')"));
        assert!(sql.contains("canonical_ticker = 'AAPL'"));
        assert!(sql.contains("available_at_us <="));
        assert!(sql.contains("uniqExact(session_kind) = 3"));
        assert!(sql.contains("identity_status != 'ambiguous_source_ticker'"));
        assert!(sql.contains("previous_session_close"));
        let rows = parse_market_structure_reference_rows(
            r#"{"sym":"AAPL","high_52_week":331.78,"low_52_week":181.46,"prior_month_high":324.09,"prior_month_low":246.63,"prior_month_close":289.0,"previous_session_close":301.0}"#,
        )
        .unwrap();
        let aapl = rows.get("AAPL").unwrap();
        assert_eq!(aapl.high_52_week, 331.78);
        assert_eq!(aapl.prior_month_close, 289.0);
        assert_eq!(aapl.previous_session_close, 301.0);
    }

    #[tokio::test]
    async fn focused_state_is_reclaimed_only_after_the_last_current_lease() {
        let indicators = SharedIndicatorStore::new(
            10,
            HashMap::new(),
            60,
            1,
            TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap(),
            HashMap::new(),
        );
        let shard = indicators.shard_for_ticker("AAPL");
        {
            let mut store = shard.inner.lock().await;
            for ticker in ["AAPL", "MSFT"] {
                let key = IndicatorKey {
                    sym: ticker.to_string(),
                    timeframe: "1m".to_string(),
                };
                store
                    .bars
                    .insert(key.clone(), BarIndicatorCalculator::new());
                store.history.insert(key.clone(), VecDeque::new());
                store
                    .microstructure_aggregates
                    .insert(key, MicrostructureSampleAggregate::default());
                store.ticks.insert(ticker.to_string(), TickState::new(60));
                store
                    .microstructure
                    .insert(ticker.to_string(), MicrostructureIntervalWindow::default());
            }
        }

        let targets = SharedComputationTargets::default();
        targets
            .replace(ComputationTargetRequest {
                target_id: "watchlist:aapl".to_string(),
                owner: "test".to_string(),
                scope: ExecutionScope::Watchlist,
                tickers: vec!["AAPL".to_string()],
                capabilities: vec!["opening_range".to_string()],
                timeframes: vec!["1m".to_string()],
                parameter_hash: "test".to_string(),
                anchor: "new_york_session".to_string(),
                source_revision: "advancing_live".to_string(),
                ttl_seconds: None,
                correlation_id: String::new(),
                causation_id: String::new(),
            })
            .unwrap();

        let first = indicators.reclaim_unused(&targets).await;
        assert_eq!(first.bar_calculators, 1);
        assert_eq!(first.history_series, 1);
        assert_eq!(first.microstructure_aggregates, 1);
        assert_eq!(first.tick_states, 2);
        assert_eq!(first.microstructure_windows, 2);
        {
            let store = shard.inner.lock().await;
            assert!(store.bars.contains_key(&IndicatorKey {
                sym: "AAPL".to_string(),
                timeframe: "1m".to_string(),
            }));
            assert!(!store.bars.contains_key(&IndicatorKey {
                sym: "MSFT".to_string(),
                timeframe: "1m".to_string(),
            }));
        }
        assert!(targets.remove("watchlist:aapl"));

        let final_reclaim = indicators.reclaim_unused(&targets).await;
        assert_eq!(final_reclaim.bar_calculators, 1);
        assert_eq!(final_reclaim.history_series, 1);
        assert_eq!(final_reclaim.microstructure_aggregates, 1);
        assert_eq!(final_reclaim.total(), 3);
    }

    #[test]
    fn session_vwap_accumulates_exact_trade_notional() {
        let mut state = SessionVwapState::new();
        let first = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 14, 0, 0).unwrap(),
            100.0,
            10.0,
        );
        let second = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 14, 1, 0).unwrap(),
            300.0,
            20.0,
        );

        assert!((first - 10.0).abs() < 1e-9);
        assert!((second - 17.5).abs() < 1e-9);
    }

    #[test]
    fn session_vwap_is_invariant_under_timeframe_aggregation() {
        let first_start = Utc.with_ymd_and_hms(2026, 7, 14, 14, 0, 0).unwrap();
        let second_start = Utc.with_ymd_and_hms(2026, 7, 14, 14, 1, 0).unwrap();
        let mut fine = SessionVwapState::new();
        fine.update(first_start, 100.0, 10.0);
        let fine_value = fine.update(second_start, 300.0, 20.0);

        let mut coarse = SessionVwapState::new();
        let coarse_value = coarse.update(first_start, 400.0, 17.5);

        assert!((fine_value - coarse_value).abs() < 1e-9);
    }

    #[test]
    fn session_vwap_seed_matches_processing_the_prior_session_bars() {
        let page_start = Utc.with_ymd_and_hms(2026, 7, 14, 18, 0, 0).unwrap();
        let mut complete = SessionVwapState::new();
        complete.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 14, 0, 0).unwrap(),
            100.0,
            10.0,
        );
        let complete_value = complete.update(page_start, 300.0, 20.0);

        let mut paged = SessionVwapState::new();
        paged.seed(page_start, 100.0, 1_000.0).unwrap();
        let paged_value = paged.update(page_start, 300.0, 20.0);

        assert!((complete_value - paged_value).abs() < 1e-9);
    }

    #[test]
    fn cumulative_microstructure_flow_adds_raw_deltas_and_resets_by_session() {
        let mut state = MicrostructureCumulativeFlow::default();
        assert_eq!(state.update("2026-07-14", 120.0, -40.0), (120.0, -40.0));
        assert_eq!(state.update("2026-07-14", -20.0, 90.0), (100.0, 50.0));
        assert_eq!(state.update("2026-07-15", -35.0, -10.0), (-35.0, -10.0));
    }

    #[test]
    fn flow_structure_composite_preserves_continuous_evidence_and_discounts_conflict() {
        let (score, confidence, bias, reason) =
            calculate_flow_structure_composite(0.6, 80.0, 0.45, 0.30, 0.8, 0.75, 0.9);
        assert!(score > 0.4);
        assert!(confidence > 0.7);
        assert_eq!(bias, "bullish");
        assert_eq!(reason, "aligned_bullish_evidence");

        let (score, confidence, bias, reason) =
            calculate_flow_structure_composite(0.6, 80.0, -0.7, -0.4, 0.9, 0.8, 0.8);
        assert!(score.abs() < 0.2);
        assert!(confidence < 0.35);
        assert_eq!(bias, "neutral");
        assert_eq!(reason, "conflicting_flow_structure_evidence");

        let (score, _, bias, reason) =
            calculate_flow_structure_composite(-0.5, 20.0, -0.4, -0.3, 0.8, 0.8, 0.8);
        assert!(score < 0.0);
        assert_eq!(bias, "neutral");
        assert_eq!(reason, "structure_dominant_evidence");
    }

    #[test]
    fn higher_timeframe_composite_is_a_summary_of_canonical_100ms_states() {
        let bullish = summarize_canonical_composites(1.08, 1.8, 3).unwrap();
        assert_eq!(bullish.0, 0.6);
        assert_eq!(bullish.1, 0.36);
        assert_eq!(bullish.2, "bullish");
        assert_eq!(bullish.3, "canonical_100ms_consensus");

        let mixed = summarize_canonical_composites(0.0, 1.8, 3).unwrap();
        assert_eq!(mixed.0, 0.0);
        assert_eq!(mixed.1, 0.0);
        assert_eq!(mixed.2, "neutral");
        assert_eq!(mixed.3, "canonical_100ms_mixed_or_weak");
    }

    #[test]
    fn anchored_flow_session_starts_at_four_new_york_across_dst() {
        assert_eq!(
            anchored_market_session_date(Utc.with_ymd_and_hms(2026, 7, 14, 7, 59, 59).unwrap()),
            "2026-07-13"
        );
        assert_eq!(
            anchored_market_session_date(Utc.with_ymd_and_hms(2026, 7, 14, 8, 0, 0).unwrap()),
            "2026-07-14"
        );
        assert_eq!(
            anchored_market_session_date(Utc.with_ymd_and_hms(2026, 1, 14, 8, 59, 59).unwrap()),
            "2026-01-13"
        );
        assert_eq!(
            anchored_market_session_date(Utc.with_ymd_and_hms(2026, 1, 14, 9, 0, 0).unwrap()),
            "2026-01-14"
        );
        assert_eq!(
            anchored_market_session_date(Utc.with_ymd_and_hms(2026, 7, 14, 13, 30, 0).unwrap()),
            "2026-07-14"
        );
    }

    #[test]
    fn anchored_flow_relationship_distinguishes_confirmation_and_absorption() {
        assert_eq!(
            anchored_flow_relationship(100.0, 50.0),
            ("bullish_confirmation", 1.0)
        );
        assert_eq!(
            anchored_flow_relationship(-100.0, -50.0),
            ("bearish_confirmation", -1.0)
        );
        assert_eq!(
            anchored_flow_relationship(100.0, -50.0),
            ("bullish_absorption", 0.55)
        );
        assert_eq!(
            anchored_flow_relationship(-100.0, 50.0),
            ("bearish_absorption", -0.55)
        );
        assert_eq!(anchored_flow_relationship(0.0, 50.0), ("neutral", 0.0));
    }

    #[test]
    fn session_vwap_continues_through_the_regular_session_open() {
        let mut state = SessionVwapState::new();
        state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 13, 29, 0).unwrap(),
            100.0,
            10.0,
        );
        let regular_session = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 13, 30, 0).unwrap(),
            50.0,
            30.0,
        );

        assert!((regular_session - (2500.0 / 150.0)).abs() < 1e-9);
    }

    #[test]
    fn session_vwap_resets_at_four_new_york_across_daylight_saving() {
        let mut state = SessionVwapState::new();
        state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 8, 59, 0).unwrap(),
            100.0,
            10.0,
        );
        let winter_premarket = state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 9, 0, 0).unwrap(),
            50.0,
            30.0,
        );

        assert!((winter_premarket - 30.0).abs() < 1e-9);
    }
}
