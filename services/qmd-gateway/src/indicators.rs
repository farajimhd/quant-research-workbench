use crate::bars::{BarRow, TradeAggregationRules};
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::metrics::SharedMetrics;
use crate::microstructure_forecast::{
    MicrostructureForecastSnapshot, MicrostructureForecastWindow, MicrostructureIntervalFeatures,
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

pub const INDICATOR_SCHEMA_VERSION: u16 = 11;
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
    pub microstructure_fast_signal: f64,
    pub microstructure_fast_confidence: f64,
    pub microstructure_confirm_signal: f64,
    pub microstructure_confirm_confidence: f64,
    pub microstructure_context_signal: f64,
    pub microstructure_context_confidence: f64,
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
    #[serde(skip_serializing)]
    pub microstructure_interval: MicrostructureIntervalFeatures,
}

impl IndicatorRow {
    pub fn apply_microstructure(&mut self, forecast: &MicrostructureForecastSnapshot) {
        let horizon = |events| {
            forecast
                .horizons
                .iter()
                .find(|item| item.horizon_events == events)
        };
        self.microstructure_fast_signal = horizon(25).map(|item| item.score).unwrap_or(0.0);
        self.microstructure_fast_confidence =
            horizon(25).map(|item| item.confidence).unwrap_or(0.0);
        self.microstructure_confirm_signal = horizon(100).map(|item| item.score).unwrap_or(0.0);
        self.microstructure_confirm_confidence =
            horizon(100).map(|item| item.confidence).unwrap_or(0.0);
        self.microstructure_context_signal = horizon(500).map(|item| item.score).unwrap_or(0.0);
        self.microstructure_context_confidence =
            horizon(500).map(|item| item.confidence).unwrap_or(0.0);
        self.apply_microstructure_interval(&forecast.interval);
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
    }
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
    liquidity_levels: LiquidityLevelState,
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
            liquidity_levels: LiquidityLevelState::default(),
        }
    }

    pub fn apply_bar(&mut self, bar: &BarRow) -> IndicatorRow {
        self.state.apply_bar(bar)
    }

    pub fn set_market_structure_references(&mut self, references: MarketStructureReferenceLevels) {
        self.state.market_structure.references = references;
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

    /// Update causal support/resistance candidates after the row has received
    /// its timeframe-native microstructure interval.
    pub fn apply_market_levels(&mut self, row: &mut IndicatorRow, bar: &BarRow) {
        self.liquidity_levels.apply_to(row, bar);
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
    microstructure: HashMap<String, MicrostructureForecastWindow>,
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

#[derive(Clone, Debug, Default)]
struct LiquidityLevelState {
    anchor_session_date: String,
    support: HashMap<i64, LevelEvidence>,
    resistance: HashMap<i64, LevelEvidence>,
}

#[derive(Clone, Debug, Default)]
struct LevelEvidence {
    price: f64,
    score: f64,
    evidence: f64,
    touches: u64,
}

#[derive(Clone, Debug, Default)]
struct MarketStructureState {
    anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    swing_high: f64,
    swing_low: f64,
    recent_bars: VecDeque<(f64, f64)>,
    volume_by_price: HashMap<i64, f64>,
    volume_poc: f64,
    previous_close: f64,
    previous_swing_high: f64,
    previous_swing_low: f64,
    trend_direction: i8,
    bos_price: f64,
    bos_direction: i8,
    choch_price: f64,
    choch_direction: i8,
    references: MarketStructureReferenceLevels,
}

#[derive(Clone, Copy, Debug, Default)]
struct MarketStructureSnapshot {
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    swing_high: f64,
    swing_low: f64,
    volume_poc: f64,
    nearest_round: f64,
    bos_price: f64,
    bos_direction: i8,
    choch_price: f64,
    choch_direction: i8,
    luld_upper: f64,
    luld_lower: f64,
    high_52_week: f64,
    low_52_week: f64,
    prior_month_high: f64,
    prior_month_low: f64,
    prior_month_close: f64,
}

impl LiquidityLevelState {
    fn apply_to(&mut self, row: &mut IndicatorRow, bar: &BarRow) {
        let anchor = anchored_market_session_date(bar.bar_start);
        if self.anchor_session_date != anchor {
            self.anchor_session_date = anchor;
            self.support.clear();
            self.resistance.clear();
        }

        let decay = 0.5_f64.powf(timeframe_seconds(&bar.timeframe) / 900.0);
        decay_levels(&mut self.support, decay);
        decay_levels(&mut self.resistance, decay);

        let range = (bar.high - bar.low).abs().max(price_tick(bar.close));
        let lower_rejection = ((bar.close - bar.low) / range).clamp(0.0, 1.0);
        let upper_rejection = ((bar.high - bar.close) / range).clamp(0.0, 1.0);
        let price_response = (row.microstructure_midpoint_return_bps.abs()
            / (row.microstructure_interval.spread_bps.abs().max(0.25)))
        .clamp(0.0, 1.0);
        let sell_absorption =
            (-row.microstructure_signed_volume_imbalance).max(0.0) * (1.0 - price_response);
        let buy_absorption =
            row.microstructure_signed_volume_imbalance.max(0.0) * (1.0 - price_response);
        let bid_recovery = recovery_ratio(
            row.microstructure_interval.bid_replenishment,
            row.microstructure_interval.bid_depletion,
        );
        let ask_recovery = recovery_ratio(
            row.microstructure_interval.ask_replenishment,
            row.microstructure_interval.ask_depletion,
        );
        let support_raw = 0.30 * row.microstructure_level1_ofi.max(0.0)
            + 0.25 * bid_recovery
            + 0.25 * sell_absorption
            + 0.20 * lower_rejection;
        let resistance_raw = 0.30 * (-row.microstructure_level1_ofi).max(0.0)
            + 0.25 * ask_recovery
            + 0.25 * buy_absorption
            + 0.20 * upper_rejection;
        let reliability = row.microstructure_regime_reliability.clamp(0.0, 1.0);
        let support_price = positive_or(bar.bid_low, bar.low);
        let resistance_price = positive_or(bar.ask_high, bar.high);
        update_level(&mut self.support, support_price, support_raw, reliability);
        update_level(
            &mut self.resistance,
            resistance_price,
            resistance_raw,
            reliability,
        );
        bound_levels(&mut self.support, 128);
        bound_levels(&mut self.resistance, 128);

        if let Some(level) = select_level(&self.support, bar.close, true, row.atr_14) {
            row.liquidity_support_price = level.price;
            row.liquidity_support_strength = normalized_level_strength(level.score);
            row.liquidity_support_confidence = level_confidence(level);
        }
        if let Some(level) = select_level(&self.resistance, bar.close, false, row.atr_14) {
            row.liquidity_resistance_price = level.price;
            row.liquidity_resistance_strength = normalized_level_strength(level.score);
            row.liquidity_resistance_confidence = level_confidence(level);
        }
        row.liquidity_level_pressure = (row.liquidity_support_strength
            * row.liquidity_support_confidence
            - row.liquidity_resistance_strength * row.liquidity_resistance_confidence)
            .clamp(-1.0, 1.0);

        let tolerance = row.atr_14.max(price_tick(bar.close) * 4.0) * 0.20;
        let support_confluence = structure_confluence(row.liquidity_support_price, row, tolerance);
        let resistance_confluence =
            structure_confluence(row.liquidity_resistance_price, row, tolerance);
        row.market_level_support_score =
            (0.65 * row.liquidity_support_strength * row.liquidity_support_confidence
                + 0.35 * support_confluence)
                .clamp(0.0, 1.0);
        row.market_level_resistance_score =
            (0.65 * row.liquidity_resistance_strength * row.liquidity_resistance_confidence
                + 0.35 * resistance_confluence)
                .clamp(0.0, 1.0);
        row.market_level_bias =
            (row.market_level_support_score - row.market_level_resistance_score).clamp(-1.0, 1.0);
    }
}

impl MarketStructureState {
    fn update(&mut self, bar: &BarRow) -> MarketStructureSnapshot {
        let anchor = market_session_anchor_date(bar.bar_start);
        if self.anchor != Some(anchor) {
            let references = self.references;
            *self = Self {
                anchor: Some(anchor),
                references,
                ..Self::default()
            };
        }
        update_high_low(
            &mut self.session_high,
            &mut self.session_low,
            bar.high,
            bar.low,
        );
        let local_seconds = bar
            .bar_start
            .with_timezone(&New_York)
            .time()
            .num_seconds_from_midnight();
        let local_end_seconds = bar
            .bar_end
            .with_timezone(&New_York)
            .time()
            .num_seconds_from_midnight();
        if exact_clock_level_supported(&bar.timeframe, 1800.0)
            && (PREMARKET_SESSION_START_SECONDS..(9 * 3600 + 30 * 60)).contains(&local_seconds)
            && local_end_seconds <= 9 * 3600 + 30 * 60
        {
            update_high_low(
                &mut self.premarket_high,
                &mut self.premarket_low,
                bar.high,
                bar.low,
            );
        }
        if exact_clock_level_supported(&bar.timeframe, 300.0)
            && (9 * 3600 + 30 * 60..9 * 3600 + 35 * 60).contains(&local_seconds)
            && local_end_seconds <= 9 * 3600 + 35 * 60
        {
            update_high_low(
                &mut self.opening_range_high,
                &mut self.opening_range_low,
                bar.high,
                bar.low,
            );
        }

        self.recent_bars.push_back((bar.high, bar.low));
        while self.recent_bars.len() > 5 {
            self.recent_bars.pop_front();
        }
        let mut swing_updated = false;
        if self.recent_bars.len() == 5 {
            let center = self.recent_bars[2];
            if self
                .recent_bars
                .iter()
                .enumerate()
                .all(|(index, value)| index == 2 || center.0 > value.0)
            {
                self.previous_swing_high = self.swing_high;
                self.swing_high = center.0;
                swing_updated = true;
            }
            if self
                .recent_bars
                .iter()
                .enumerate()
                .all(|(index, value)| index == 2 || center.1 < value.1)
            {
                self.previous_swing_low = self.swing_low;
                self.swing_low = center.1;
                swing_updated = true;
            }
        }

        if swing_updated
            && self.previous_swing_high > 0.0
            && self.previous_swing_low > 0.0
            && self.swing_high > 0.0
            && self.swing_low > 0.0
        {
            if self.swing_high > self.previous_swing_high
                && self.swing_low > self.previous_swing_low
            {
                self.trend_direction = 1;
            } else if self.swing_high < self.previous_swing_high
                && self.swing_low < self.previous_swing_low
            {
                self.trend_direction = -1;
            }
        }

        let (bos_direction, choch_direction, next_trend) = classify_structure_break(
            self.previous_close,
            bar.close,
            self.swing_high,
            self.swing_low,
            self.trend_direction,
        );
        if bos_direction != 0 {
            self.bos_direction = bos_direction;
            self.bos_price = if bos_direction > 0 {
                self.swing_high
            } else {
                self.swing_low
            };
        }
        if choch_direction != 0 {
            self.choch_direction = choch_direction;
            self.choch_price = if choch_direction > 0 {
                self.swing_high
            } else {
                self.swing_low
            };
        }
        self.trend_direction = next_trend;
        self.previous_close = bar.close;

        if bar.volume > 0.0 {
            let typical = (bar.high + bar.low + bar.close) / 3.0;
            let key = structure_price_key(typical);
            *self.volume_by_price.entry(key).or_default() += bar.volume;
            self.volume_poc = self
                .volume_by_price
                .iter()
                .max_by(|left, right| left.1.total_cmp(right.1))
                .map(|(key, _)| structure_price_from_key(*key))
                .unwrap_or(0.0);
        }

        MarketStructureSnapshot {
            session_high: self.session_high,
            session_low: self.session_low,
            premarket_high: self.premarket_high,
            premarket_low: self.premarket_low,
            opening_range_high: self.opening_range_high,
            opening_range_low: self.opening_range_low,
            swing_high: self.swing_high,
            swing_low: self.swing_low,
            volume_poc: self.volume_poc,
            nearest_round: nearest_round_price(bar.close),
            bos_price: self.bos_price,
            bos_direction: self.bos_direction,
            choch_price: self.choch_price,
            choch_direction: self.choch_direction,
            luld_upper: bar.estimated_luld_upper_price,
            luld_lower: bar.estimated_luld_lower_price,
            high_52_week: self.references.high_52_week,
            low_52_week: self.references.low_52_week,
            prior_month_high: self.references.prior_month_high,
            prior_month_low: self.references.prior_month_low,
            prior_month_close: self.references.prior_month_close,
        }
    }
}

fn classify_structure_break(
    previous_close: f64,
    close: f64,
    swing_high: f64,
    swing_low: f64,
    trend_direction: i8,
) -> (i8, i8, i8) {
    let crossed_above = swing_high > 0.0
        && previous_close > 0.0
        && previous_close <= swing_high
        && close > swing_high;
    let crossed_below =
        swing_low > 0.0 && previous_close > 0.0 && previous_close >= swing_low && close < swing_low;
    if crossed_above {
        if trend_direction < 0 {
            (0, 1, 1)
        } else {
            (1, 0, 1)
        }
    } else if crossed_below {
        if trend_direction > 0 {
            (0, -1, -1)
        } else {
            (-1, 0, -1)
        }
    } else {
        (0, 0, trend_direction)
    }
}

fn update_high_low(high: &mut f64, low: &mut f64, candidate_high: f64, candidate_low: f64) {
    if candidate_high > 0.0 {
        *high = if *high > 0.0 {
            (*high).max(candidate_high)
        } else {
            candidate_high
        };
    }
    if candidate_low > 0.0 {
        *low = if *low > 0.0 {
            (*low).min(candidate_low)
        } else {
            candidate_low
        };
    }
}

fn timeframe_seconds(timeframe: &str) -> f64 {
    match timeframe.to_ascii_lowercase().as_str() {
        "100ms" => 0.1,
        "1s" => 1.0,
        "5s" => 5.0,
        "10s" => 10.0,
        "30s" => 30.0,
        "1m" => 60.0,
        "5m" => 300.0,
        "1h" => 3600.0,
        _ => 1.0,
    }
}

fn exact_clock_level_supported(timeframe: &str, max_seconds: f64) -> bool {
    timeframe_seconds(timeframe) <= max_seconds
}

fn price_tick(price: f64) -> f64 {
    if price >= 1.0 {
        0.01
    } else {
        0.0001
    }
}

fn price_key(price: f64) -> i64 {
    (price / price_tick(price)).round() as i64
}

fn price_from_key(key: i64, reference: f64) -> f64 {
    key as f64 * price_tick(reference)
}

fn structure_price_key(price: f64) -> i64 {
    (price * 10_000.0).round() as i64
}

fn structure_price_from_key(key: i64) -> f64 {
    key as f64 / 10_000.0
}

fn positive_or(primary: f64, fallback: f64) -> f64 {
    if primary.is_finite() && primary > 0.0 {
        primary
    } else {
        fallback
    }
}

fn recovery_ratio(replenishment: f64, depletion: f64) -> f64 {
    let total = replenishment.max(0.0) + depletion.max(0.0);
    if total > 0.0 {
        (replenishment.max(0.0) / total).clamp(0.0, 1.0)
    } else {
        0.0
    }
}

fn decay_levels(levels: &mut HashMap<i64, LevelEvidence>, decay: f64) {
    levels.values_mut().for_each(|level| {
        level.score *= decay;
        level.evidence *= decay;
    });
    levels.retain(|_, level| level.score > 0.005);
}

fn update_level(
    levels: &mut HashMap<i64, LevelEvidence>,
    price: f64,
    score: f64,
    reliability: f64,
) {
    if !price.is_finite() || price <= 0.0 || !score.is_finite() || score <= 0.0 {
        return;
    }
    let key = price_key(price);
    let entry = levels.entry(key).or_insert_with(|| LevelEvidence {
        price: price_from_key(key, price),
        ..LevelEvidence::default()
    });
    entry.score += score.clamp(0.0, 1.0);
    entry.evidence += reliability;
    entry.touches += 1;
}

fn bound_levels(levels: &mut HashMap<i64, LevelEvidence>, limit: usize) {
    while levels.len() > limit {
        let Some(key) = levels
            .iter()
            .min_by(|left, right| left.1.score.total_cmp(&right.1.score))
            .map(|(key, _)| *key)
        else {
            break;
        };
        levels.remove(&key);
    }
}

fn select_level<'a>(
    levels: &'a HashMap<i64, LevelEvidence>,
    close: f64,
    support: bool,
    atr: f64,
) -> Option<&'a LevelEvidence> {
    let scale = atr.max(price_tick(close) * 8.0);
    levels
        .values()
        .filter(|level| {
            if support {
                level.price <= close + price_tick(close)
            } else {
                level.price >= close - price_tick(close)
            }
        })
        .max_by(|left, right| {
            let left_rank = left.score / (1.0 + (left.price - close).abs() / scale);
            let right_rank = right.score / (1.0 + (right.price - close).abs() / scale);
            left_rank.total_cmp(&right_rank)
        })
}

fn normalized_level_strength(score: f64) -> f64 {
    (1.0 - (-score.max(0.0)).exp()).clamp(0.0, 1.0)
}

fn level_confidence(level: &LevelEvidence) -> f64 {
    if level.touches == 0 {
        return 0.0;
    }
    ((level.touches as f64 / 4.0).sqrt().min(1.0)
        * (level.evidence / level.touches as f64).clamp(0.0, 1.0))
    .clamp(0.0, 1.0)
}

fn structure_confluence(price: f64, row: &IndicatorRow, tolerance: f64) -> f64 {
    if price <= 0.0 {
        return 0.0;
    }
    let levels = [
        row.structure_session_high,
        row.structure_session_low,
        row.structure_premarket_high,
        row.structure_premarket_low,
        row.structure_opening_range_high,
        row.structure_opening_range_low,
        row.structure_swing_high,
        row.structure_swing_low,
        row.structure_volume_poc,
        row.structure_nearest_round,
        row.structure_52_week_high,
        row.structure_52_week_low,
        row.structure_prior_month_high,
        row.structure_prior_month_low,
        row.structure_prior_month_close,
    ];
    (levels
        .iter()
        .filter(|level| **level > 0.0 && (**level - price).abs() <= tolerance)
        .count() as f64
        / 4.0)
        .clamp(0.0, 1.0)
}

fn nearest_round_price(price: f64) -> f64 {
    let interval = if price >= 100.0 {
        1.0
    } else if price >= 10.0 {
        0.5
    } else if price >= 1.0 {
        0.10
    } else {
        0.01
    };
    (price / interval).round() * interval
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
    market_structure: MarketStructureState,
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
        let history = store
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

/// O(1)-memory sufficient statistics for causal 100 ms forecast samples.
#[derive(Clone, Debug, Default)]
pub struct MicrostructureSampleAggregate {
    sample_count: u64,
    fast: WeightedSignalAggregate,
    confirm: WeightedSignalAggregate,
    context: WeightedSignalAggregate,
    interval: MicrostructureIntervalFeatures,
}

#[derive(Clone, Debug, Default)]
struct WeightedSignalAggregate {
    count: u64,
    sum_confidence: f64,
    sum_weighted_signal: f64,
    sum_weighted_signal_sq: f64,
}

impl WeightedSignalAggregate {
    fn push(&mut self, signal: f64, confidence: f64) {
        if !signal.is_finite() || !confidence.is_finite() || confidence <= 0.0 {
            return;
        }
        self.count += 1;
        self.sum_confidence += confidence;
        self.sum_weighted_signal += signal * confidence;
        self.sum_weighted_signal_sq += signal * signal * confidence;
    }

    fn value(&self) -> (f64, f64) {
        if self.count == 0 || self.sum_confidence <= f64::EPSILON {
            return (0.0, 0.0);
        }
        let signal = (self.sum_weighted_signal / self.sum_confidence).clamp(-1.0, 1.0);
        let variance =
            (self.sum_weighted_signal_sq / self.sum_confidence - signal * signal).max(0.0);
        let agreement = 1.0 - variance.sqrt().clamp(0.0, 1.0);
        let mean_confidence = self.sum_confidence / self.count as f64;
        (signal, (mean_confidence * agreement).clamp(0.0, 100.0))
    }
}

impl MicrostructureSampleAggregate {
    pub fn push(&mut self, row: &IndicatorRow) {
        self.fast.push(
            row.microstructure_fast_signal,
            row.microstructure_fast_confidence,
        );
        self.confirm.push(
            row.microstructure_confirm_signal,
            row.microstructure_confirm_confidence,
        );
        self.context.push(
            row.microstructure_context_signal,
            row.microstructure_context_confidence,
        );
        self.push_interval(&row.microstructure_interval);
    }

    pub fn push_interval(&mut self, interval: &MicrostructureIntervalFeatures) {
        self.sample_count += 1;
        self.interval.merge(interval);
    }

    pub fn apply_to(&self, target: &mut IndicatorRow) {
        (
            target.microstructure_fast_signal,
            target.microstructure_fast_confidence,
        ) = self.fast.value();
        (
            target.microstructure_confirm_signal,
            target.microstructure_confirm_confidence,
        ) = self.confirm.value();
        (
            target.microstructure_context_signal,
            target.microstructure_context_confidence,
        ) = self.context.value();
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
            market_structure: MarketStructureState::default(),
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
        let structure = self.market_structure.update(bar);

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
            microstructure_fast_signal: 0.0,
            microstructure_fast_confidence: 0.0,
            microstructure_confirm_signal: 0.0,
            microstructure_confirm_confidence: 0.0,
            microstructure_context_signal: 0.0,
            microstructure_context_confidence: 0.0,
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
            structure_session_high: structure.session_high,
            structure_session_low: structure.session_low,
            structure_premarket_high: structure.premarket_high,
            structure_premarket_low: structure.premarket_low,
            structure_opening_range_high: structure.opening_range_high,
            structure_opening_range_low: structure.opening_range_low,
            structure_swing_high: structure.swing_high,
            structure_swing_low: structure.swing_low,
            structure_volume_poc: structure.volume_poc,
            structure_nearest_round: structure.nearest_round,
            structure_bos_price: structure.bos_price,
            structure_bos_direction: structure.bos_direction,
            structure_choch_price: structure.choch_price,
            structure_choch_direction: structure.choch_direction,
            structure_luld_upper: structure.luld_upper,
            structure_luld_lower: structure.luld_lower,
            structure_52_week_high: structure.high_52_week,
            structure_52_week_low: structure.low_52_week,
            structure_prior_month_high: structure.prior_month_high,
            structure_prior_month_low: structure.prior_month_low,
            structure_prior_month_close: structure.prior_month_close,
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
                microstructure_fast_signal Float64,
                microstructure_fast_confidence Float64,
                microstructure_confirm_signal Float64,
                microstructure_confirm_confidence Float64,
                microstructure_context_signal Float64,
                microstructure_context_confidence Float64,
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
                ADD COLUMN IF NOT EXISTS microstructure_fast_signal Float64,
                ADD COLUMN IF NOT EXISTS microstructure_fast_confidence Float64,
                ADD COLUMN IF NOT EXISTS microstructure_confirm_signal Float64,
                ADD COLUMN IF NOT EXISTS microstructure_confirm_confidence Float64,
                ADD COLUMN IF NOT EXISTS microstructure_context_signal Float64,
                ADD COLUMN IF NOT EXISTS microstructure_context_confidence Float64,
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
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<IndicatorRow>) {
        if !self.config.persist_indicators {
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
                            while !batch.is_empty() {
                                self.flush(&mut batch).await;
                                if !batch.is_empty() {
                                    sleep(Duration::from_millis(250)).await;
                                }
                            }
                            return;
                        }
                    }
                    if batch.len() >= self.config.max_clickhouse_batch {
                        self.flush(&mut batch).await;
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut batch).await;
                }
            }
            self.metrics
                .set_lane_pending("indicators", (batch.len() + receiver.len()) as u64);
        }
    }

    async fn flush(&self, batch: &mut Vec<IndicatorRow>) {
        if batch.is_empty() {
            return;
        }
        self.metrics
            .set_lane_pending("indicators", batch.len() as u64);
        if let Err(error) = self.insert_indicators(batch).await {
            self.metrics.record_lane_failure("indicators", &error);
            eprintln!("ClickHouse indicator insert failed: {error}");
        } else {
            let count = batch.len() as u64;
            batch.clear();
            self.metrics.record_lane_success(
                "indicators",
                count,
                "Committed closed indicator rows.",
            );
            self.metrics.set_lane_pending("indicators", 0);
        }
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
    json!({
        "session_date": &row.session_date,
        "schema_version": row.schema_version,
        "timeframe": &row.timeframe,
        "sym": &row.sym,
        "bar_start": clickhouse_datetime64(&row.bar_start),
        "bar_end": clickhouse_datetime64(&row.bar_end),
        "close": row.close,
        "volume": row.volume,
        "vwap": row.vwap,
        "ema_9": row.ema_9,
        "ema_20": row.ema_20,
        "ema_50": row.ema_50,
        "rsi_14": row.rsi_14,
        "atr_14": row.atr_14,
        "macd_line": row.macd_line,
        "macd_signal": row.macd_signal,
        "macd_histogram": row.macd_histogram,
        "bollinger_mid_20": row.bollinger_mid_20,
        "bollinger_upper_20": row.bollinger_upper_20,
        "bollinger_lower_20": row.bollinger_lower_20,
        "bollinger_std_20": row.bollinger_std_20,
        "close_sma_20": row.close_sma_20,
        "volume_sma_20": row.volume_sma_20,
        "return_1_bar": row.return_1_bar,
        "price_vs_ema20_pct": row.price_vs_ema20_pct,
        "price_vs_vwap_pct": row.price_vs_vwap_pct,
        "trend_score": row.trend_score,
        "microstructure_fast_signal": row.microstructure_fast_signal,
        "microstructure_fast_confidence": row.microstructure_fast_confidence,
        "microstructure_confirm_signal": row.microstructure_confirm_signal,
        "microstructure_confirm_confidence": row.microstructure_confirm_confidence,
        "microstructure_context_signal": row.microstructure_context_signal,
        "microstructure_context_confidence": row.microstructure_context_confidence,
        "microstructure_unified_signal": row.microstructure_unified_signal,
        "microstructure_unified_confidence": row.microstructure_unified_confidence,
        "microstructure_unified_action": &row.microstructure_unified_action,
        "microstructure_buy_trade_count": row.microstructure_buy_trade_count,
        "microstructure_sell_trade_count": row.microstructure_sell_trade_count,
        "microstructure_classified_trade_count": row.microstructure_classified_trade_count,
        "microstructure_eligible_trade_count": row.microstructure_eligible_trade_count,
        "microstructure_buy_volume": row.microstructure_buy_volume,
        "microstructure_sell_volume": row.microstructure_sell_volume,
        "microstructure_signed_volume_delta": row.microstructure_signed_volume_delta,
        "microstructure_cumulative_signed_volume_delta": row.microstructure_cumulative_signed_volume_delta,
        "microstructure_anchored_flow_relationship": &row.microstructure_anchored_flow_relationship,
        "microstructure_anchored_flow_relationship_score": row.microstructure_anchored_flow_relationship_score,
        "microstructure_transaction_imbalance": row.microstructure_transaction_imbalance,
        "microstructure_signed_volume_imbalance": row.microstructure_signed_volume_imbalance,
        "microstructure_level1_ofi_delta": row.microstructure_level1_ofi_delta,
        "microstructure_cumulative_level1_ofi": row.microstructure_cumulative_level1_ofi,
        "microstructure_level1_ofi": row.microstructure_level1_ofi,
        "microstructure_queue_imbalance": row.microstructure_queue_imbalance,
        "microstructure_microprice_lean": row.microstructure_microprice_lean,
        "microstructure_midpoint_return_bps": row.microstructure_midpoint_return_bps,
        "microstructure_trade_return_bps": row.microstructure_trade_return_bps,
        "microstructure_aggressor_persistence": row.microstructure_aggressor_persistence,
        "microstructure_arrival_intensity_imbalance": row.microstructure_arrival_intensity_imbalance,
        "microstructure_arrival_rate_per_second": row.microstructure_arrival_rate_per_second,
        "microstructure_resiliency": row.microstructure_resiliency,
        "microstructure_aggressive_flow_score": row.microstructure_aggressive_flow_score,
        "microstructure_displayed_liquidity_score": row.microstructure_displayed_liquidity_score,
        "microstructure_response_resiliency_score": row.microstructure_response_resiliency_score,
        "microstructure_regime_reliability": row.microstructure_regime_reliability,
        "liquidity_support_price": row.liquidity_support_price,
        "liquidity_support_strength": row.liquidity_support_strength,
        "liquidity_support_confidence": row.liquidity_support_confidence,
        "liquidity_resistance_price": row.liquidity_resistance_price,
        "liquidity_resistance_strength": row.liquidity_resistance_strength,
        "liquidity_resistance_confidence": row.liquidity_resistance_confidence,
        "liquidity_level_pressure": row.liquidity_level_pressure,
        "market_level_support_score": row.market_level_support_score,
        "market_level_resistance_score": row.market_level_resistance_score,
        "market_level_bias": row.market_level_bias,
        "structure_session_high": row.structure_session_high,
        "structure_session_low": row.structure_session_low,
        "structure_premarket_high": row.structure_premarket_high,
        "structure_premarket_low": row.structure_premarket_low,
        "structure_opening_range_high": row.structure_opening_range_high,
        "structure_opening_range_low": row.structure_opening_range_low,
        "structure_swing_high": row.structure_swing_high,
        "structure_swing_low": row.structure_swing_low,
        "structure_volume_poc": row.structure_volume_poc,
        "structure_nearest_round": row.structure_nearest_round,
        "structure_bos_price": row.structure_bos_price,
        "structure_bos_direction": row.structure_bos_direction,
        "structure_choch_price": row.structure_choch_price,
        "structure_choch_direction": row.structure_choch_direction,
        "structure_luld_upper": row.structure_luld_upper,
        "structure_luld_lower": row.structure_luld_lower,
        "structure_52_week_high": row.structure_52_week_high,
        "structure_52_week_low": row.structure_52_week_low,
        "structure_prior_month_high": row.structure_prior_month_high,
        "structure_prior_month_low": row.structure_prior_month_low,
        "structure_prior_month_close": row.structure_prior_month_close,
    })
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
        anchored_flow_relationship, anchored_market_session_date, classify_structure_break,
        decay_levels, exact_clock_level_supported, level_confidence,
        market_structure_reference_sql, normalized_level_strength,
        parse_market_structure_reference_rows, select_level, update_level, LevelEvidence,
        MicrostructureCumulativeFlow, SessionVwapState, WeightedSignalAggregate,
    };
    use chrono::{TimeZone, Utc};
    use std::collections::HashMap;

    #[test]
    fn exact_clock_levels_reject_bars_that_straddle_the_window() {
        assert!(exact_clock_level_supported("30s", 1800.0));
        assert!(!exact_clock_level_supported("1h", 1800.0));
        assert!(exact_clock_level_supported("5m", 300.0));
        assert!(!exact_clock_level_supported("1h", 300.0));
    }

    #[test]
    fn structure_break_distinguishes_continuation_from_character_change() {
        assert_eq!(
            classify_structure_break(99.5, 100.5, 100.0, 98.0, 1),
            (1, 0, 1)
        );
        assert_eq!(
            classify_structure_break(98.5, 97.5, 100.0, 98.0, 1),
            (0, -1, -1)
        );
        assert_eq!(
            classify_structure_break(99.5, 100.5, 100.0, 98.0, -1),
            (0, 1, 1)
        );
        assert_eq!(
            classify_structure_break(98.5, 97.5, 100.0, 98.0, -1),
            (-1, 0, -1)
        );
        assert_eq!(
            classify_structure_break(99.0, 99.5, 100.0, 98.0, 1),
            (0, 0, 1)
        );
    }

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
    fn liquidity_evidence_accumulates_by_price_and_decays_on_elapsed_time() {
        let mut levels = HashMap::new();
        update_level(&mut levels, 100.004, 0.5, 0.8);
        update_level(&mut levels, 100.003, 0.5, 0.8);
        let level = levels.values().next().expect("one tick-binned level");
        assert_eq!(level.touches, 2);
        assert!((level.score - 1.0).abs() < 1e-9);
        assert!(level_confidence(level) > 0.5);
        decay_levels(&mut levels, 0.5);
        assert!((levels.values().next().unwrap().score - 0.5).abs() < 1e-9);
    }

    #[test]
    fn liquidity_selection_prefers_relevant_nearby_side() {
        let mut levels = HashMap::from([
            (
                9_900,
                LevelEvidence {
                    price: 99.0,
                    score: 4.0,
                    evidence: 3.0,
                    touches: 4,
                },
            ),
            (
                9_990,
                LevelEvidence {
                    price: 99.9,
                    score: 2.0,
                    evidence: 2.0,
                    touches: 3,
                },
            ),
            (
                10_010,
                LevelEvidence {
                    price: 100.1,
                    score: 3.0,
                    evidence: 2.0,
                    touches: 3,
                },
            ),
        ]);
        let support = select_level(&levels, 100.0, true, 0.5).unwrap();
        let resistance = select_level(&levels, 100.0, false, 0.5).unwrap();
        assert_eq!(support.price, 99.9);
        assert_eq!(resistance.price, 100.1);
        assert!(normalized_level_strength(support.score) > 0.8);
        levels.clear();
        assert!(select_level(&levels, 100.0, true, 0.5).is_none());
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
    fn streaming_microstructure_aggregate_penalizes_conflicting_samples() {
        let mut aligned = WeightedSignalAggregate::default();
        aligned.push(0.5, 80.0);
        aligned.push(0.5, 80.0);
        let (aligned_signal, aligned_confidence) = aligned.value();
        assert!((aligned_signal - 0.5).abs() < 1e-9);
        assert!((aligned_confidence - 80.0).abs() < 1e-9);

        let mut conflicting = WeightedSignalAggregate::default();
        conflicting.push(1.0, 80.0);
        conflicting.push(-1.0, 80.0);
        let (conflicting_signal, conflicting_confidence) = conflicting.value();
        assert!(conflicting_signal.abs() < 1e-9);
        assert!(conflicting_confidence < aligned_confidence);
    }

    #[test]
    fn cumulative_microstructure_flow_adds_raw_deltas_and_resets_by_session() {
        let mut state = MicrostructureCumulativeFlow::default();
        assert_eq!(state.update("2026-07-14", 120.0, -40.0), (120.0, -40.0));
        assert_eq!(state.update("2026-07-14", -20.0, 90.0), (100.0, 50.0));
        assert_eq!(state.update("2026-07-15", -35.0, -10.0), (-35.0, -10.0));
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
