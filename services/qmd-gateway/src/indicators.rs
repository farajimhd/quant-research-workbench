use crate::bars::BarRow;
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, Duration};

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
    pub quote_rate_10s: f64,
    pub quote_rate_60s: f64,
    pub rolling_vwap_60s: f64,
    pub tape_imbalance_60s: f64,
    pub buy_pressure_60s: f64,
    pub sell_pressure_60s: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct IndicatorRow {
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
    bars: HashMap<IndicatorKey, BarIndicatorState>,
    history: HashMap<IndicatorKey, VecDeque<IndicatorRow>>,
    history_limit: usize,
    ticks: HashMap<String, TickState>,
}

#[derive(Default)]
struct TickState {
    last_ask: f64,
    last_bid: f64,
    last_mid: f64,
    last_price: f64,
    last_ts: Option<DateTime<Utc>>,
    recent_quotes: VecDeque<QuoteSample>,
    recent_trades: VecDeque<TradeSample>,
    spread_bps: f64,
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
    volume_sma_20: RollingStats,
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
    pub fn new(history_limit: usize, shard_count: usize) -> Self {
        let shard_count = shard_count.max(1);
        let shards = (0..shard_count)
            .map(|_| IndicatorShardStore::new(history_limit))
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

    pub fn try_send_event(&self, event: MarketEvent) -> Result<(), mpsc::error::TrySendError<MarketEvent>> {
        let index = shard_index(event.ticker(), self.event_senders.len());
        self.event_senders[index].try_send(event)
    }
}

impl IndicatorShardStore {
    fn new(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(IndicatorStore {
                bars: HashMap::new(),
                history: HashMap::new(),
                history_limit,
                ticks: HashMap::new(),
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
        let current = store.history.get(&key).and_then(|rows| rows.back()).cloned();
        let history = store
            .history
            .get(&key)
            .map(|rows| {
                rows.iter()
                    .rev()
                    .take(limit.min(store.history_limit))
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
        let tick = self.ticks.entry(ticker).or_default();
        match event {
            MarketEvent::Trade(trade) => tick.apply_trade(trade),
            MarketEvent::Quote(quote) => tick.apply_quote(quote),
        }
    }

    fn apply_bar(&mut self, bar: BarRow) -> IndicatorRow {
        let key = IndicatorKey {
            sym: bar.sym.clone(),
            timeframe: bar.timeframe.clone(),
        };
        let state = self
            .bars
            .entry(key.clone())
            .or_insert_with(BarIndicatorState::new);
        let row = state.apply_bar(&bar);
        let history = self.history.entry(key).or_insert_with(VecDeque::new);
        history.push_back(row.clone());
        while history.len() > self.history_limit {
            history.pop_front();
        }
        row
    }
}

impl TickState {
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
        let volume_60s = self.recent_trades.iter().map(|sample| sample.volume).sum::<f64>();
        let signed_volume_60s = self
            .recent_trades
            .iter()
            .map(|sample| sample.signed_volume)
            .sum::<f64>();
        let buy_volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| sample.signed_volume > 0.0)
            .map(|sample| sample.volume)
            .sum::<f64>();
        let sell_volume_60s = self
            .recent_trades
            .iter()
            .filter(|sample| sample.signed_volume < 0.0)
            .map(|sample| sample.volume)
            .sum::<f64>();
        let notional_60s = self.recent_trades.iter().map(|sample| sample.notional).sum::<f64>();

        TickIndicatorRow {
            sym: ticker.to_string(),
            last_ts: self.last_ts.clone(),
            last_price: self.last_price,
            last_mid: self.last_mid,
            spread_bps: self.spread_bps,
            quote_pressure: self.quote_pressure(),
            trade_rate_10s: trade_count_10s / 10.0,
            trade_rate_60s: self.recent_trades.len() as f64 / 60.0,
            quote_rate_10s: quote_count_10s / 10.0,
            quote_rate_60s: self.recent_quotes.len() as f64 / 60.0,
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
            .map(|sample| seconds_between(sample.ts.clone(), now.clone()) > 60)
            .unwrap_or(false)
        {
            self.recent_trades.pop_front();
        }
        while self
            .recent_quotes
            .front()
            .map(|sample| seconds_between(sample.ts.clone(), now.clone()) > 60)
            .unwrap_or(false)
        {
            self.recent_quotes.pop_front();
        }
    }

    fn quote_pressure(&self) -> f64 {
        let bid_size = self.recent_quotes.iter().map(|sample| sample.bid_size).sum::<f64>();
        let ask_size = self.recent_quotes.iter().map(|sample| sample.ask_size).sum::<f64>();
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

        IndicatorRow {
            session_date: bar.session_date.clone(),
            timeframe: bar.timeframe.clone(),
            sym: bar.sym.clone(),
            bar_start: bar.bar_start.clone(),
            bar_end: bar.bar_end.clone(),
            close: bar.close,
            volume: bar.volume,
            vwap: bar.vwap,
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
            price_vs_vwap_pct: pct_change(bar.close, bar.vwap),
            trend_score: trend_score(bar.close, ema_9, ema_20, ema_50, rsi_14, macd_histogram),
        }
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
    tokio::spawn(route_indicator_bars(
        bar_receiver,
        Arc::new(bar_senders),
    ));
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
        if shard_senders[index].try_send(row).is_err() {
            eprintln!("Indicator bar shard queue is full; dropped one finalized bar.");
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
                        if writer_sender.try_send(row).is_err() {
                            eprintln!("Indicator writer queue is full; shard {shard_id} dropped one indicator row.");
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
}

impl IndicatorClickHouseWriter {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        self.execute(&format!("CREATE DATABASE IF NOT EXISTS `{}`", self.config.clickhouse_database), false)
            .await?;
        self.execute(
            r#"
            CREATE TABLE IF NOT EXISTS live_market_indicators
            (
                session_date Date,
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
                trend_score Float64
            )
            ENGINE = ReplacingMergeTree
            PARTITION BY session_date
            ORDER BY (session_date, timeframe, sym, bar_start)
            "#,
            true,
        )
        .await?;
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<IndicatorRow>) {
        if let Err(error) = self.initialize().await {
            eprintln!("ClickHouse indicator initialization failed: {error}");
        }
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                row = receiver.recv() => {
                    match row {
                        Some(row) => batch.push(row),
                        None => {
                            self.flush(&mut batch).await;
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
        }
    }

    async fn flush(&self, batch: &mut Vec<IndicatorRow>) {
        if batch.is_empty() {
            return;
        }
        let rows = std::mem::take(batch);
        if let Err(error) = self.insert_indicators(&rows).await {
            eprintln!("ClickHouse indicator insert failed: {error}");
        }
    }

    async fn insert_indicators(&self, rows: &[IndicatorRow]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| serde_json::to_string(&indicator_insert_row(row)).unwrap_or_else(|_| "{}".to_string()))
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body("INSERT INTO live_market_indicators FORMAT JSONEachRow", body)
            .await
    }

    async fn execute(&self, sql: &str, use_database: bool) -> Result<(), String> {
        self.query(sql, use_database).await.map(|_| ())
    }

    async fn query_with_body(&self, sql: &str, body: String) -> Result<(), String> {
        self.query(&format!("{sql}\n{body}"), true).await.map(|_| ())
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
        "timeframe": &row.timeframe,
        "sym": &row.sym,
        "bar_start": row.bar_start.to_rfc3339(),
        "bar_end": row.bar_end.to_rfc3339(),
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
    })
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

fn trend_score(close: f64, ema_9: f64, ema_20: f64, ema_50: f64, rsi_14: f64, macd_histogram: f64) -> f64 {
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
