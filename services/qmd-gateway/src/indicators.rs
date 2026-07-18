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
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, sleep, Duration};

pub const INDICATOR_SCHEMA_VERSION: u16 = 8;
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
    let local = bar_start.with_timezone(&New_York);
    let mut session_date = local.date_naive();
    if local.num_seconds_from_midnight() < PREMARKET_SESSION_START_SECONDS {
        session_date = session_date.pred_opt().unwrap_or(session_date);
    }
    session_date.to_string()
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
}

struct SessionVwapState {
    cumulative_typical_notional: f64,
    cumulative_volume: f64,
    anchor: Option<SessionVwapAnchor>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct SessionVwapAnchor {
    market_date: NaiveDate,
    phase: SessionVwapPhase,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SessionVwapPhase {
    Premarket,
    Regular,
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
    ) -> Self {
        let shard_count = shard_count.max(1);
        let shards = (0..shard_count)
            .map(|_| {
                IndicatorShardStore::new(
                    history_limit,
                    history_limits.clone(),
                    tick_window_seconds,
                    trade_rules.clone(),
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
        let state = self
            .bars
            .entry(key.clone())
            .or_insert_with(BarIndicatorCalculator::new);
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
        let anchor = session_vwap_anchor(bar_start);
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

fn session_vwap_anchor(bar_start: DateTime<Utc>) -> SessionVwapAnchor {
    let local = bar_start.with_timezone(&New_York);
    let phase = if (local.hour(), local.minute()) < (9, 30) {
        SessionVwapPhase::Premarket
    } else {
        SessionVwapPhase::Regular
    };
    SessionVwapAnchor {
        market_date: local.date_naive(),
        phase,
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
                microstructure_regime_reliability Float64
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
                ADD COLUMN IF NOT EXISTS microstructure_regime_reliability Float64"#,
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
        anchored_flow_relationship, anchored_market_session_date, MicrostructureCumulativeFlow,
        SessionVwapState, WeightedSignalAggregate,
    };
    use chrono::{TimeZone, Utc};

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
    fn session_vwap_resets_at_the_regular_session_open() {
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

        assert!((regular_session - 30.0).abs() < 1e-9);
    }

    #[test]
    fn session_vwap_uses_new_york_time_across_daylight_saving() {
        let mut state = SessionVwapState::new();
        state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 14, 29, 0).unwrap(),
            11.0,
            9.0,
            10.0,
            100.0,
            0.0,
        );
        let winter_regular_session = state.update(
            Utc.with_ymd_and_hms(2026, 1, 14, 14, 30, 0).unwrap(),
            31.0,
            29.0,
            30.0,
            50.0,
            0.0,
        );

        assert!((winter_regular_session - 30.0).abs() < 1e-9);
    }
}
