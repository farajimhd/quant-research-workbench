use crate::bars::{BarRow, SharedBarStore, TradeAggregationRules};
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEvent, GenericStructureSnapshot,
    StructureLevelCandidate,
};
use crate::metrics::SharedMetrics;
use crate::microstructure_interval::{
    MicrostructureIntervalFeatures, MicrostructureIntervalWindow,
};
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, NaiveDate, Timelike, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, RwLock as StdRwLock};
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, sleep, Duration};

pub const INDICATOR_SCHEMA_VERSION: u16 = 16;
const MICROSTRUCTURE_AGGREGATE_TIMEFRAMES: [&str; 7] = ["1s", "5s", "10s", "30s", "1m", "5m", "1h"];
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
    pub qmd_decision_signal: f64,
    pub qmd_decision_confidence: f64,
    pub qmd_decision_action: String,
    pub qmd_decision_reason: String,
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
    pub qmd_structure_event_scale: String,
    pub qmd_structure_event_direction: i8,
    pub qmd_structure_event_price: f64,
    pub qmd_structure_session_high: f64,
    pub qmd_structure_session_low: f64,
    pub qmd_structure_premarket_high: f64,
    pub qmd_structure_premarket_low: f64,
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
        self.refresh_qmd_decision();
    }

    fn refresh_qmd_decision(&mut self) {
        let (signal, confidence, action, reason) = calculate_qmd_decision(
            self.microstructure_unified_signal,
            self.microstructure_unified_confidence,
            self.qmd_structure_score,
            self.qmd_structure_pressure_bias,
            self.qmd_structure_pressure_confidence,
            self.qmd_structure_confidence,
            self.qmd_structure_agreement,
        );
        self.qmd_decision_signal = signal;
        self.qmd_decision_confidence = confidence;
        self.qmd_decision_action = action.to_string();
        self.qmd_decision_reason = reason.to_string();
    }
}

fn calculate_qmd_decision(
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
    let pressure = (pressure_bias * pressure_confidence.clamp(0.0, 1.0)).clamp(-1.0, 1.0);
    let context = (0.75 * structure + 0.25 * pressure).clamp(-1.0, 1.0);
    let flow_directional = flow.abs() >= 0.15 && flow_confidence >= 0.35;
    let context_conflict =
        flow_directional && context.abs() >= 0.12 && flow.signum() != context.signum();
    let signal = if flow_directional && !context_conflict {
        (0.78 * flow + 0.22 * context).clamp(-1.0, 1.0)
    } else {
        0.0
    };
    let structure_quality =
        (0.5 * structure_confidence + 0.5 * structure_agreement).clamp(0.0, 1.0);
    let confidence = if signal == 0.0 {
        flow_confidence * if context_conflict { 0.45 } else { 0.65 }
    } else {
        flow_confidence * (0.75 + 0.25 * structure_quality)
    }
    .clamp(0.0, 1.0);
    let (action, reason) = if !flow_directional {
        ("wait", "insufficient_microstructure_evidence")
    } else if context_conflict {
        ("wait", "structure_flow_conflict")
    } else if signal > 0.0 {
        (
            "buy",
            if context.abs() >= 0.05 {
                "aligned_buy_evidence"
            } else {
                "buy_flow_neutral_structure"
            },
        )
    } else {
        (
            "sell",
            if context.abs() >= 0.05 {
                "aligned_sell_evidence"
            } else {
                "sell_flow_neutral_structure"
            },
        )
    };
    (
        (signal * 10_000.0).round() / 10_000.0,
        (confidence * 100.0).round() / 100.0,
        action,
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
}

#[derive(Debug, Deserialize)]
struct MarketStructureReferenceRow {
    sym: String,
    high_52_week: f64,
    low_52_week: f64,
    prior_month_high: f64,
    prior_month_low: f64,
    prior_month_close: f64,
}

pub async fn load_live_market_structure_references(
    config: &GatewayConfig,
    as_of: DateTime<Utc>,
) -> Result<HashMap<String, MarketStructureReferenceLevels>, String> {
    let sql = market_structure_reference_sql(
        &config.historical_clickhouse_database,
        "macro_bars_by_time_symbol",
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
    let ticker_filter = ticker
        .map(|value| format!("AND sym = '{}'", value.replace('\'', "''")))
        .unwrap_or_default();
    let as_of_date = as_of.with_timezone(&New_York).date_naive();
    Ok(format!(
        r#"SELECT
            sym,
            ifNull(maxIf(high, session_date >= addDays(toDate('{as_of_date}'), -364) AND session_date < toDate('{as_of_date}')), 0) AS high_52_week,
            ifNull(minIf(low, low > 0 AND session_date >= addDays(toDate('{as_of_date}'), -364) AND session_date < toDate('{as_of_date}')), 0) AS low_52_week,
            ifNull(maxIf(high, toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_high,
            ifNull(minIf(low, low > 0 AND toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_low,
            ifNull(argMaxIf(close, bar_end, toStartOfMonth(session_date) = addMonths(toStartOfMonth(toDate('{as_of_date}')), -1)), 0) AS prior_month_close
        FROM `{database}`.`{table}` FINAL
        WHERE timeframe = '1d'
          AND bar_family = 'trade'
          AND session_date >= addDays(toDate('{as_of_date}'), -364)
          AND session_date < toDate('{as_of_date}')
          AND bar_end <= parseDateTime64BestEffort('{as_of}')
          {ticker_filter}
        GROUP BY sym
        FORMAT JSONEachRow"#,
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

#[derive(Clone)]
pub struct IndicatorEventRouter {
    bar_sender: mpsc::Sender<BarRow>,
    event_senders: Arc<Vec<mpsc::Sender<MarketEvent>>>,
}

#[derive(Clone)]
struct IndicatorShardStore {
    inner: Arc<Mutex<IndicatorStore>>,
}

struct IndicatorStore {
    bars: HashMap<IndicatorKey, BarIndicatorCalculator>,
    history: HashMap<IndicatorKey, VecDeque<IndicatorRow>>,
    history_limits: HashMap<String, usize>,
    history_limit: usize,
    tick_window_seconds: i64,
    ticks: HashMap<String, TickState>,
    microstructure: HashMap<String, MicrostructureIntervalWindow>,
    microstructure_aggregates: HashMap<IndicatorKey, MicrostructureSampleAggregate>,
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
    cumulative_typical_notional: f64,
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

    fn shard_for_ticker(&self, ticker: &str) -> IndicatorShardStore {
        self.shard(shard_index(ticker, self.shards.len()))
    }
}

impl IndicatorEventRouter {
    pub fn bar_sender(&self) -> mpsc::Sender<BarRow> {
        self.bar_sender.clone()
    }

    pub async fn send_event(
        &self,
        event: MarketEvent,
    ) -> Result<(), mpsc::error::SendError<MarketEvent>> {
        let index = shard_index(event.ticker(), self.event_senders.len());
        self.event_senders[index].send(event).await
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
                history: HashMap::new(),
                history_limits,
                history_limit,
                tick_window_seconds: tick_window_seconds.max(60),
                ticks: HashMap::new(),
                microstructure: HashMap::new(),
                microstructure_aggregates: HashMap::new(),
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
        let current = store
            .history
            .get(&key)
            .and_then(|rows| rows.back())
            .cloned();
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
        history
            .iter_mut()
            .for_each(|row| row.qmd_structure_active_levels.clear());
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
        let state = self.bars.entry(key.clone()).or_insert_with(|| {
            let mut calculator = BarIndicatorCalculator::new();
            calculator.set_market_structure_references(references);
            calculator
        });
        let mut row = state.apply_bar(&bar);
        let ticker = bar.sym.to_ascii_uppercase();
        if bar.timeframe.eq_ignore_ascii_case("100ms") {
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
        self.bars
            .get_mut(&key)
            .expect("indicator calculator exists")
            .apply_market_levels(&mut row, &bar);
        let history_limit = self.history_limit_for(&bar.timeframe);
        let history = self.history.entry(key).or_insert_with(VecDeque::new);
        history.push_back(row.clone());
        while history.len() > history_limit {
            history.pop_front();
        }
        row
    }

    fn history_limit_for(&self, timeframe: &str) -> usize {
        self.history_limits
            .get(&canonical_timeframe(timeframe))
            .copied()
            .unwrap_or(self.history_limit)
    }
}

/// O(1)-memory sufficient statistics for causal 100 ms evidence buckets.
#[derive(Clone, Debug, Default)]
pub struct MicrostructureSampleAggregate {
    sample_count: u64,
    interval: MicrostructureIntervalFeatures,
}

impl MicrostructureSampleAggregate {
    pub fn push(&mut self, row: &IndicatorRow) {
        self.push_interval(&row.microstructure_interval);
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
    }

    pub fn reset(&mut self) {
        *self = Self::default();
    }
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
        let session_vwap = self.session_vwap.update(
            bar.bar_start,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.vwap,
        );
        let structure = &bar.qmd_structure;
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
            qmd_decision_signal: 0.0,
            qmd_decision_confidence: 0.0,
            qmd_decision_action: "wait".to_string(),
            qmd_decision_reason: "insufficient_microstructure_evidence".to_string(),
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
            structure_swing_high: 0.0,
            structure_swing_low: 0.0,
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
            qmd_structure_micro_direction: structure.micro.direction,
            qmd_structure_micro_threshold: structure.micro.threshold,
            qmd_structure_micro_swing_high: structure.micro.swing_high,
            qmd_structure_micro_swing_low: structure.micro.swing_low,
            qmd_structure_micro_support_price: structure.micro.support.price,
            qmd_structure_micro_support_lower: structure.micro.support.lower,
            qmd_structure_micro_support_upper: structure.micro.support.upper,
            qmd_structure_micro_support_strength: structure.micro.support.strength,
            qmd_structure_micro_support_confidence: structure.micro.support.confidence,
            qmd_structure_micro_resistance_price: structure.micro.resistance.price,
            qmd_structure_micro_resistance_lower: structure.micro.resistance.lower,
            qmd_structure_micro_resistance_upper: structure.micro.resistance.upper,
            qmd_structure_micro_resistance_strength: structure.micro.resistance.strength,
            qmd_structure_micro_resistance_confidence: structure.micro.resistance.confidence,
            qmd_structure_tactical_direction: structure.tactical.direction,
            qmd_structure_tactical_threshold: structure.tactical.threshold,
            qmd_structure_tactical_swing_high: structure.tactical.swing_high,
            qmd_structure_tactical_swing_low: structure.tactical.swing_low,
            qmd_structure_tactical_support_price: structure.tactical.support.price,
            qmd_structure_tactical_support_lower: structure.tactical.support.lower,
            qmd_structure_tactical_support_upper: structure.tactical.support.upper,
            qmd_structure_tactical_support_strength: structure.tactical.support.strength,
            qmd_structure_tactical_support_confidence: structure.tactical.support.confidence,
            qmd_structure_tactical_resistance_price: structure.tactical.resistance.price,
            qmd_structure_tactical_resistance_lower: structure.tactical.resistance.lower,
            qmd_structure_tactical_resistance_upper: structure.tactical.resistance.upper,
            qmd_structure_tactical_resistance_strength: structure.tactical.resistance.strength,
            qmd_structure_tactical_resistance_confidence: structure.tactical.resistance.confidence,
            qmd_structure_context_direction: structure.context.direction,
            qmd_structure_context_threshold: structure.context.threshold,
            qmd_structure_context_swing_high: structure.context.swing_high,
            qmd_structure_context_swing_low: structure.context.swing_low,
            qmd_structure_context_support_price: structure.context.support.price,
            qmd_structure_context_support_lower: structure.context.support.lower,
            qmd_structure_context_support_upper: structure.context.support.upper,
            qmd_structure_context_support_strength: structure.context.support.strength,
            qmd_structure_context_support_confidence: structure.context.support.confidence,
            qmd_structure_context_resistance_price: structure.context.resistance.price,
            qmd_structure_context_resistance_lower: structure.context.resistance.lower,
            qmd_structure_context_resistance_upper: structure.context.resistance.upper,
            qmd_structure_context_resistance_strength: structure.context.resistance.strength,
            qmd_structure_context_resistance_confidence: structure.context.resistance.confidence,
            qmd_structure_event_id: structure.last_event_id,
            qmd_structure_event_pivot_at_ms: structure.last_event_pivot_at_ms,
            qmd_structure_event_at_ms: structure.last_event_at_ms,
            qmd_structure_event_kind: structure.last_event_kind.clone(),
            qmd_structure_event_scale: structure.last_event_scale.clone(),
            qmd_structure_event_direction: structure.last_event_direction,
            qmd_structure_event_price: structure.last_event_price,
            qmd_structure_session_high: structure.session_high,
            qmd_structure_session_low: structure.session_low,
            qmd_structure_premarket_high: structure.premarket_high,
            qmd_structure_premarket_low: structure.premarket_low,
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
            qmd_structure_snapshot: structure.clone(),
            qmd_structure_events: bar.qmd_structure_events.clone(),
            microstructure_interval: MicrostructureIntervalFeatures::default(),
        }
    }
}

impl SessionVwapState {
    fn new() -> Self {
        Self {
            cumulative_typical_notional: 0.0,
            cumulative_volume: 0.0,
            anchor: None,
        }
    }

    fn update(
        &mut self,
        bar_start: DateTime<Utc>,
        high: f64,
        low: f64,
        close: f64,
        volume: f64,
        fallback: f64,
    ) -> f64 {
        let anchor = market_session_anchor_date(bar_start);
        if self.anchor != Some(anchor) {
            self.anchor = Some(anchor);
            self.cumulative_typical_notional = 0.0;
            self.cumulative_volume = 0.0;
        }
        let typical_price = (high + low + close) / 3.0;
        if typical_price.is_finite() && volume.is_finite() && volume > 0.0 {
            self.cumulative_typical_notional += typical_price * volume;
            self.cumulative_volume += volume;
        }
        if self.cumulative_volume > 0.0 {
            self.cumulative_typical_notional / self.cumulative_volume
        } else {
            fallback
        }
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
    event_channel_capacity: usize,
    bar_channel_capacity: usize,
    writer_sender: mpsc::Sender<IndicatorRow>,
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
            event_receiver,
            bar_receiver,
            writer_sender.clone(),
        ));
    }
    let (bar_sender, bar_receiver) = mpsc::channel::<BarRow>(bar_channel_capacity.max(1));
    tokio::spawn(route_indicator_bars(bar_receiver, Arc::new(bar_senders)));
    IndicatorEventRouter {
        bar_sender,
        event_senders: Arc::new(event_senders),
    }
}

async fn route_indicator_bars(
    mut receiver: mpsc::Receiver<BarRow>,
    shard_senders: Arc<Vec<mpsc::Sender<BarRow>>>,
) {
    while let Some(row) = receiver.recv().await {
        let index = shard_index(&row.sym, shard_senders.len());
        if shard_senders[index].send(row).await.is_err() {
            eprintln!("Indicator bar shard receiver closed; could not route one finalized bar.");
        }
    }
}

async fn run_indicator_engine(
    shard_id: usize,
    shard: IndicatorShardStore,
    mut event_receiver: mpsc::Receiver<MarketEvent>,
    mut bar_receiver: mpsc::Receiver<BarRow>,
    writer_sender: mpsc::Sender<IndicatorRow>,
) {
    loop {
        tokio::select! {
            event = event_receiver.recv() => {
                match event {
                    Some(event) => shard.apply_event(&event).await,
                    None => return,
                }
            }
            bar = bar_receiver.recv() => {
                match bar {
                    Some(bar) => {
                        let row = shard.apply_bar(bar).await;
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
        self.execute(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.execute(
            r#"
            CREATE TABLE IF NOT EXISTS live_market_indicators
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
                qmd_decision_signal Float64,
                qmd_decision_confidence Float64,
                qmd_decision_action LowCardinality(String),
                qmd_decision_reason LowCardinality(String),
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
                structure_prior_month_close Float64
            )
            ENGINE = ReplacingMergeTree
            PARTITION BY session_date
            ORDER BY (session_date, timeframe, sym, bar_start)
            "#,
            true,
        )
        .await?;
        self.execute(
            "ALTER TABLE live_market_indicators ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await?;
        self.execute(
            r#"ALTER TABLE live_market_indicators
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
                ADD COLUMN IF NOT EXISTS qmd_decision_signal Float64,
                ADD COLUMN IF NOT EXISTS qmd_decision_confidence Float64,
                ADD COLUMN IF NOT EXISTS qmd_decision_action LowCardinality(String),
                ADD COLUMN IF NOT EXISTS qmd_decision_reason LowCardinality(String),
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
                ADD COLUMN IF NOT EXISTS structure_prior_month_close Float64"#,
            true,
        )
        .await?;
        self.execute(
            r#"ALTER TABLE live_market_indicators
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
                ADD COLUMN IF NOT EXISTS qmd_structure_event_scale LowCardinality(String),
                ADD COLUMN IF NOT EXISTS qmd_structure_event_direction Int8,
                ADD COLUMN IF NOT EXISTS qmd_structure_event_price Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_session_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_session_low Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_premarket_high Float64,
                ADD COLUMN IF NOT EXISTS qmd_structure_premarket_low Float64,
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
                ADD COLUMN IF NOT EXISTS qmd_structure_prior_month_close Float64"#,
            true,
        )
        .await?;
        self.execute(
            r#"CREATE TABLE IF NOT EXISTS qmd_structure_events_v1
            (
                event_date Date,
                algorithm_version UInt16,
                event_id UInt64,
                sym LowCardinality(String),
                scale LowCardinality(String),
                event_kind LowCardinality(String),
                direction Int8,
                price Float64,
                lower Float64,
                upper Float64,
                strength Float64,
                confidence Float64,
                pivot_at DateTime64(6, 'UTC'),
                confirmed_at DateTime64(6, 'UTC')
            )
            ENGINE = ReplacingMergeTree
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (sym, confirmed_at, scale, event_kind, event_id)"#,
            true,
        )
        .await?;
        self.execute(
            r#"CREATE TABLE IF NOT EXISTS qmd_structure_state_v1
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
        Ok(())
    }

    pub async fn load_structure_checkpoints(
        &self,
    ) -> Result<Vec<(String, GenericStructureCheckpoint)>, String> {
        let sql = format!(
            r#"SELECT sym, argMax(snapshot_json, updated_at) AS snapshot_json
            FROM qmd_structure_state_v1
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

    pub async fn run(
        self,
        mut receiver: mpsc::Receiver<IndicatorRow>,
        bars: SharedBarStore,
        mut structure_watermarks: HashMap<String, i64>,
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
        structure_watermarks: &mut HashMap<String, i64>,
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
        let structure_states = if self.config.persist_structure_events {
            bars.structure_checkpoints_since(structure_watermarks).await
        } else {
            Vec::new()
        };
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
        if self.config.persist_structure_events && !structure_states.is_empty() {
            if let Err(error) = self.insert_structure_states(&structure_states).await {
                self.metrics.record_lane_failure("structure_events", &error);
                eprintln!("ClickHouse QMD structure-state insert failed: {error}");
                return false;
            }
            for (sym, checkpoint) in &structure_states {
                if let Some(updated_at) = checkpoint.updated_at {
                    structure_watermarks.insert(sym.clone(), updated_at.timestamp_millis());
                }
            }
        }
        batch.clear();
        true
    }

    async fn insert_indicators(&self, rows: &[IndicatorRow]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| {
                serde_json::to_string(&indicator_insert_row(row))
                    .unwrap_or_else(|_| "{}".to_string())
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            "INSERT INTO live_market_indicators FORMAT JSONEachRow",
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
                    "sym": &event.sym,
                    "scale": &event.scale,
                    "event_kind": &event.event_kind,
                    "direction": event.direction,
                    "price": event.price,
                    "lower": event.lower,
                    "upper": event.upper,
                    "strength": event.strength,
                    "confidence": event.confidence,
                    "pivot_at": event.pivot_at.format("%Y-%m-%d %H:%M:%S%.6f").to_string(),
                    "confirmed_at": event.confirmed_at.format("%Y-%m-%d %H:%M:%S%.6f").to_string(),
                }))
                .unwrap_or_else(|_| "{}".to_string())
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            "INSERT INTO qmd_structure_events_v1 FORMAT JSONEachRow",
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
            "INSERT INTO qmd_structure_state_v1 FORMAT JSONEachRow",
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

fn indicator_insert_row(row: &IndicatorRow) -> serde_json::Value {
    let mut value = serde_json::to_value(row).unwrap_or_else(|_| json!({}));
    if let Some(object) = value.as_object_mut() {
        // Active candidates are a bounded streaming/chart state carried by the
        // canonical in-memory snapshot. Durable reconstruction comes from the
        // versioned generic-structure checkpoint and event tables, so the wide
        // per-bar indicator table intentionally does not duplicate this array.
        object.remove("qmd_structure_active_levels");
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
        anchored_flow_relationship, anchored_market_session_date, calculate_qmd_decision,
        market_structure_reference_sql, parse_market_structure_reference_rows,
        MicrostructureCumulativeFlow, SessionVwapState,
    };
    use chrono::{TimeZone, Utc};

    #[test]
    fn daily_structure_reference_contract_is_causal_and_parseable() {
        let as_of = Utc.with_ymd_and_hms(2026, 7, 14, 13, 45, 0).unwrap();
        let sql = market_structure_reference_sql(
            "market_sip_compact",
            "macro_bars_by_time_symbol",
            Some("AAPL"),
            as_of,
        )
        .unwrap();
        assert!(sql.contains("addDays(toDate('2026-07-14'), -364)"));
        assert!(sql.contains("session_date < toDate('2026-07-14')"));
        assert!(sql.contains("AND sym = 'AAPL'"));
        let rows = parse_market_structure_reference_rows(
            r#"{"sym":"AAPL","high_52_week":331.78,"low_52_week":181.46,"prior_month_high":324.09,"prior_month_low":246.63,"prior_month_close":289.0}"#,
        )
        .unwrap();
        let aapl = rows.get("AAPL").unwrap();
        assert_eq!(aapl.high_52_week, 331.78);
        assert_eq!(aapl.prior_month_close, 289.0);
    }

    #[test]
    fn session_vwap_accumulates_hlc3_weighted_by_volume() {
        let mut state = SessionVwapState::new();
        let first = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 14, 0, 0).unwrap(),
            11.0,
            9.0,
            10.0,
            100.0,
            0.0,
        );
        let second = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 14, 1, 0).unwrap(),
            22.0,
            18.0,
            20.0,
            300.0,
            0.0,
        );

        assert!((first - 10.0).abs() < 1e-9);
        assert!((second - 17.5).abs() < 1e-9);
    }

    #[test]
    fn cumulative_microstructure_flow_adds_raw_deltas_and_resets_by_session() {
        let mut state = MicrostructureCumulativeFlow::default();
        assert_eq!(state.update("2026-07-14", 120.0, -40.0), (120.0, -40.0));
        assert_eq!(state.update("2026-07-14", -20.0, 90.0), (100.0, 50.0));
        assert_eq!(state.update("2026-07-15", -35.0, -10.0), (-35.0, -10.0));
    }

    #[test]
    fn qmd_decision_emits_direction_only_for_reliable_non_conflicting_flow() {
        let (signal, confidence, action, reason) =
            calculate_qmd_decision(0.6, 80.0, 0.45, 0.30, 0.8, 0.75, 0.9);
        assert!(signal > 0.5);
        assert!(confidence > 0.7);
        assert_eq!(action, "buy");
        assert_eq!(reason, "aligned_buy_evidence");

        let (signal, _, action, reason) =
            calculate_qmd_decision(0.6, 80.0, -0.7, -0.4, 0.9, 0.8, 0.8);
        assert_eq!(signal, 0.0);
        assert_eq!(action, "wait");
        assert_eq!(reason, "structure_flow_conflict");

        let (signal, _, action, reason) =
            calculate_qmd_decision(-0.5, 20.0, -0.4, -0.3, 0.8, 0.8, 0.8);
        assert_eq!(signal, 0.0);
        assert_eq!(action, "wait");
        assert_eq!(reason, "insufficient_microstructure_evidence");
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
            11.0,
            9.0,
            10.0,
            100.0,
            0.0,
        );
        let regular_session = state.update(
            Utc.with_ymd_and_hms(2026, 7, 14, 13, 30, 0).unwrap(),
            31.0,
            29.0,
            30.0,
            50.0,
            0.0,
        );

        assert!((regular_session - (2500.0 / 150.0)).abs() < 1e-9);
    }

    #[test]
    fn session_vwap_resets_at_four_new_york_across_daylight_saving() {
        let mut state = SessionVwapState::new();
        state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 8, 59, 0).unwrap(),
            11.0,
            9.0,
            10.0,
            100.0,
            0.0,
        );
        let winter_premarket = state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 9, 0, 0).unwrap(),
            31.0,
            29.0,
            30.0,
            50.0,
            0.0,
        );

        assert!((winter_premarket - 30.0).abs() < 1e-9);
    }
}
