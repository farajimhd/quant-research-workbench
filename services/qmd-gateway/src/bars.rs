use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::metrics::SharedMetrics;
use crate::scanner::ScannerPrimitiveRouter;
use crate::timefmt::{clickhouse_datetime64, clickhouse_datetime64_opt};
use chrono::{DateTime, TimeZone, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, Duration};

pub const BAR_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct BarSnapshot {
    pub current: Option<BarRow>,
    pub history: Vec<BarRow>,
    pub ticker: String,
    pub timeframe: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct BarRow {
    /// Schema version for durable bar rows and replay compatibility.
    pub schema_version: u16,
    /// UTC calendar date from `bar_start`; used for ClickHouse partitioning.
    pub session_date: String,
    /// Canonical bar length label, for example `1s`, `10s`, `30s`, `1m`, `5m`, or `1h`.
    pub timeframe: String,
    /// Uppercase Massive ticker symbol.
    pub sym: String,
    /// Bar bucket start, aligned by flooring event timestamp to the timeframe boundary.
    pub bar_start: DateTime<Utc>,
    /// Bar bucket end, calculated as `bar_start + timeframe_seconds`.
    pub bar_end: DateTime<Utc>,
    /// True once the timeframe has elapsed and the bar has been emitted for persistence.
    pub is_closed: bool,
    /// Timestamp of the first quote or trade observed inside this bar.
    pub first_event_ts: Option<DateTime<Utc>>,
    /// Timestamp of the latest quote or trade observed inside this bar.
    pub last_event_ts: Option<DateTime<Utc>>,
    /// First valid trade price observed in the bar.
    pub open: f64,
    /// Highest valid trade price observed in the bar.
    pub high: f64,
    /// Lowest valid trade price observed in the bar.
    pub low: f64,
    /// Latest valid trade price observed in the bar.
    pub close: f64,
    /// Sum of trade sizes in shares.
    pub volume: f64,
    /// Sum of `trade_price * trade_size`.
    pub dollar_volume: f64,
    /// Count of valid trade events.
    pub trade_count: u64,
    /// Volume-weighted average trade price, `dollar_volume / volume`.
    pub vwap: f64,
    /// Average shares per trade, `volume / trade_count`.
    pub avg_trade_size: f64,
    /// Median of a bounded sample of trade sizes; approximate for very active bars.
    pub median_trade_size: f64,
    /// Largest trade size observed in shares.
    pub max_trade_size: f64,
    /// Count of trades with `size >= 10_000` or notional value `>= 100_000`.
    pub large_trade_count: u64,
    /// Sum of sizes for large trades.
    pub large_trade_volume: f64,
    /// Sum of notional dollars for large trades.
    pub large_trade_notional: f64,
    /// Trades per second, `trade_count / timeframe_seconds`.
    pub trade_rate: f64,
    /// Shares per second, `volume / timeframe_seconds`.
    pub volume_rate: f64,
    /// Notional dollars per second, `dollar_volume / timeframe_seconds`.
    pub dollar_volume_rate: f64,
    /// Trade close minus trade open.
    pub price_change: f64,
    /// Percent change from trade open to close, `(close - open) / open * 100`.
    pub price_change_pct: f64,
    /// Trade high minus trade low.
    pub high_low_range: f64,
    /// Trade range as percent of open, `(high - low) / open * 100`.
    pub high_low_range_pct: f64,
    /// First valid quote bid price observed in the bar.
    pub bid_open: f64,
    /// Highest valid quote bid price observed in the bar.
    pub bid_high: f64,
    /// Lowest valid quote bid price observed in the bar.
    pub bid_low: f64,
    /// Latest valid quote bid price observed in the bar.
    pub bid_close: f64,
    /// First valid quote ask price observed in the bar.
    pub ask_open: f64,
    /// Highest valid quote ask price observed in the bar.
    pub ask_high: f64,
    /// Lowest valid quote ask price observed in the bar.
    pub ask_low: f64,
    /// Latest valid quote ask price observed in the bar.
    pub ask_close: f64,
    /// First valid quote midpoint, `(bid + ask) / 2`.
    pub mid_open: f64,
    /// Highest valid quote midpoint.
    pub mid_high: f64,
    /// Lowest valid quote midpoint.
    pub mid_low: f64,
    /// Latest valid quote midpoint.
    pub mid_close: f64,
    /// First valid quoted spread, `ask - bid`.
    pub spread_open: f64,
    /// Highest valid quoted spread.
    pub spread_high: f64,
    /// Lowest valid quoted spread.
    pub spread_low: f64,
    /// Latest valid quoted spread.
    pub spread_close: f64,
    /// Average quoted spread, `sum(ask - bid) / quote_count`.
    pub spread_mean: f64,
    /// Average quoted spread in basis points, `mean((ask - bid) / mid * 10_000)`.
    pub spread_bps_mean: f64,
    /// Closing spread in basis points, `spread_close / mid_close * 10_000`.
    pub spread_bps_close: f64,
    /// Average displayed bid size from quote events.
    pub quoted_bid_size_mean: f64,
    /// Average displayed ask size from quote events.
    pub quoted_ask_size_mean: f64,
    /// Count of valid quote events.
    pub quote_count: u64,
    /// Quotes per second, `quote_count / timeframe_seconds`.
    pub quote_rate: f64,
    /// Quote-to-trade update intensity, `quote_count / max(trade_count, 1)`.
    pub quote_update_intensity: f64,
    /// Count of quotes where `bid >= ask`.
    pub locked_crossed_quote_count: u64,
    /// Count of trades classified as buyer-initiated by quote test.
    pub buy_trade_count: u64,
    /// Count of trades classified as seller-initiated by quote test.
    pub sell_trade_count: u64,
    /// Shares from buyer-initiated trades.
    pub buy_volume: f64,
    /// Shares from seller-initiated trades.
    pub sell_volume: f64,
    /// Notional dollars from buyer-initiated trades.
    pub buy_dollar_volume: f64,
    /// Notional dollars from seller-initiated trades.
    pub sell_dollar_volume: f64,
    /// Signed volume imbalance, `(buy_volume - sell_volume) / volume`.
    pub tape_imbalance: f64,
    /// Buyer-initiated share ratio, `buy_volume / volume`.
    pub aggressive_buy_ratio: f64,
    /// Seller-initiated share ratio, `sell_volume / volume`.
    pub aggressive_sell_ratio: f64,
    /// Signed share delta, `buy_volume - sell_volume`.
    pub buy_sell_volume_delta: f64,
    /// Current bar cumulative delta; same as `buy_sell_volume_delta` until session-level carry is added.
    pub cumulative_delta: f64,
    /// Mean effective spread proxy, `mean(2 * abs(trade_price - last_mid) / last_mid * 10_000)`.
    pub effective_spread_mean: f64,
    /// Realized spread placeholder using `effective_spread_mean` until delayed post-trade matching is added.
    pub realized_spread_proxy: f64,
    /// Short-horizon impact proxy currently set to close-vs-VWAP percent distance.
    pub price_impact_1s: f64,
    /// Longer-horizon impact proxy currently set to close-vs-VWAP percent distance.
    pub price_impact_5s: f64,
    /// Slippage proxy in basis points, `max(effective_spread_mean, spread_bps_close)`.
    pub slippage_proxy_bps: f64,
    /// Displayed depth imbalance proxy, `(mean_bid_size - mean_ask_size) / (mean_bid_size + mean_ask_size)`.
    pub depth_imbalance_proxy: f64,
    /// Liquidity score proxy, `dollar_volume / max(spread_bps_mean, 1)`.
    pub liquidity_score: f64,
    /// Spread per notional liquidity proxy, `spread_bps_mean / dollar_volume`.
    pub spread_volume_ratio: f64,
    /// Percent return versus the previous closed bar in the same ticker/timeframe.
    pub return_1_bar: f64,
    /// Percent return versus the third previous closed bar in the same ticker/timeframe.
    pub return_3_bar: f64,
    /// Percent return versus the fifth previous closed bar in the same ticker/timeframe.
    pub return_5_bar: f64,
    /// Current volume minus previous closed bar volume.
    pub volume_accel: f64,
    /// Current trade count minus previous closed bar trade count.
    pub trade_count_accel: f64,
    /// Current dollar volume minus previous closed bar dollar volume.
    pub dollar_volume_accel: f64,
    /// Current quote rate minus previous closed bar quote rate.
    pub quote_rate_accel: f64,
    /// Current tape imbalance minus previous closed bar tape imbalance.
    pub tape_imbalance_accel: f64,
    /// Percent distance from trade close to VWAP, `(close - vwap) / vwap * 100`.
    pub vwap_distance_pct: f64,
    /// Percent distance from quote mid close to VWAP, `(mid_close - vwap) / vwap * 100`.
    pub mid_vwap_distance_pct: f64,
    /// Square root of mean squared sequential trade returns inside the bar.
    pub realized_volatility: f64,
    /// Micro-price volatility placeholder using midpoint volatility until NBBO-weighted micro-price is added.
    pub micro_price_volatility: f64,
    /// Square root of mean squared sequential quote-midpoint returns inside the bar.
    pub mid_price_volatility: f64,
    /// Mean absolute sequential trade return inside the bar.
    pub mean_abs_trade_return: f64,
    /// Count of sign changes in sequential trade returns.
    pub direction_change_count: u64,
    /// Noise proxy, accumulated absolute trade return scaled by close and divided by high-low range.
    pub chop_score: f64,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct BarKey {
    sym: String,
    timeframe: String,
}

#[derive(Clone, Debug)]
struct BarFrame {
    label: String,
    seconds: i64,
}

#[derive(Clone)]
pub struct SharedBarStore {
    shards: Arc<Vec<BarShardStore>>,
}

#[derive(Clone)]
pub struct BarEventRouter {
    senders: Arc<Vec<mpsc::Sender<MarketEvent>>>,
}

#[derive(Clone)]
pub struct BarShardStore {
    inner: Arc<Mutex<BarStore>>,
}

struct BarStore {
    frames: Vec<BarFrame>,
    history_limit: usize,
    open: HashMap<BarKey, MutableBar>,
    closed: HashMap<BarKey, VecDeque<BarRow>>,
}

struct MutableBar {
    timeframe: String,
    sym: String,
    bar_start: DateTime<Utc>,
    bar_end: DateTime<Utc>,
    seconds: f64,
    first_event_ts: Option<DateTime<Utc>>,
    last_event_ts: Option<DateTime<Utc>>,
    open: f64,
    high: f64,
    low: f64,
    close: f64,
    volume: f64,
    dollar_volume: f64,
    trade_count: u64,
    trade_size_sample: Vec<f64>,
    trade_sample_cursor: usize,
    max_trade_size: f64,
    large_trade_count: u64,
    large_trade_volume: f64,
    large_trade_notional: f64,
    last_trade_price: f64,
    prev_trade_return_sign: i8,
    trade_return_sum_sq: f64,
    trade_abs_return_sum: f64,
    trade_return_count: u64,
    direction_change_count: u64,
    bid_open: f64,
    bid_high: f64,
    bid_low: f64,
    bid_close: f64,
    ask_open: f64,
    ask_high: f64,
    ask_low: f64,
    ask_close: f64,
    mid_open: f64,
    mid_high: f64,
    mid_low: f64,
    mid_close: f64,
    spread_open: f64,
    spread_high: f64,
    spread_low: f64,
    spread_close: f64,
    spread_sum: f64,
    spread_bps_sum: f64,
    bid_size_sum: f64,
    ask_size_sum: f64,
    quote_count: u64,
    locked_crossed_quote_count: u64,
    last_bid: f64,
    last_ask: f64,
    last_mid: f64,
    last_mid_for_return: f64,
    mid_return_sum_sq: f64,
    mid_return_count: u64,
    buy_trade_count: u64,
    sell_trade_count: u64,
    buy_volume: f64,
    sell_volume: f64,
    buy_dollar_volume: f64,
    sell_dollar_volume: f64,
    effective_spread_sum: f64,
}

impl SharedBarStore {
    pub fn new(timeframes: Vec<String>, history_limit: usize, shard_count: usize) -> Self {
        let frames = timeframes
            .into_iter()
            .filter_map(|label| parse_timeframe(&label))
            .collect::<Vec<_>>();
        let shard_count = shard_count.max(1);
        let shards = (0..shard_count)
            .map(|_| BarShardStore::new(frames.clone(), history_limit))
            .collect::<Vec<_>>();
        Self {
            shards: Arc::new(shards),
        }
    }

    pub fn shard_count(&self) -> usize {
        self.shards.len()
    }

    pub fn shard(&self, index: usize) -> BarShardStore {
        self.shards[index % self.shards.len()].clone()
    }

    pub async fn snapshot(&self, ticker: &str, timeframe: &str, limit: usize) -> BarSnapshot {
        let ticker = ticker.to_ascii_uppercase();
        let timeframe = canonical_timeframe(timeframe);
        self.shard_for_ticker(&ticker)
            .snapshot(&ticker, &timeframe, limit)
            .await
    }

    fn shard_for_ticker(&self, ticker: &str) -> BarShardStore {
        self.shard(shard_index(ticker, self.shards.len()))
    }
}

impl BarEventRouter {
    pub async fn send(
        &self,
        event: MarketEvent,
    ) -> Result<(), mpsc::error::SendError<MarketEvent>> {
        let index = shard_index(event.ticker(), self.senders.len());
        self.senders[index].send(event).await
    }
}

impl BarShardStore {
    fn new(frames: Vec<BarFrame>, history_limit: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(BarStore {
                frames,
                history_limit,
                open: HashMap::new(),
                closed: HashMap::new(),
            })),
        }
    }

    pub async fn apply_event(&self, event: &MarketEvent) -> Vec<BarRow> {
        let mut store = self.inner.lock().await;
        store.apply_event(event)
    }

    pub async fn finalize_due(&self, now: DateTime<Utc>) -> Vec<BarRow> {
        let mut store = self.inner.lock().await;
        store.finalize_due(now)
    }

    async fn snapshot(&self, ticker: &str, timeframe: &str, limit: usize) -> BarSnapshot {
        let key = BarKey {
            sym: ticker.to_string(),
            timeframe: timeframe.to_string(),
        };
        let store = self.inner.lock().await;
        let current = store.open.get(&key).map(|bar| store.freeze_bar(bar, false));
        let history = store
            .closed
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
        BarSnapshot {
            current,
            history,
            ticker: ticker.to_string(),
            timeframe: timeframe.to_string(),
        }
    }
}

impl BarStore {
    fn apply_event(&mut self, event: &MarketEvent) -> Vec<BarRow> {
        let mut finalized = Vec::new();
        for frame in self.frames.clone() {
            let sym = event.ticker().to_ascii_uppercase();
            let start = aligned_start(event.ts(), frame.seconds);
            let end = start + chrono::Duration::seconds(frame.seconds);
            let key = BarKey {
                sym: sym.clone(),
                timeframe: frame.label.clone(),
            };

            if self
                .closed
                .get(&key)
                .and_then(|history| history.back())
                .map(|bar| bar.bar_start >= start)
                .unwrap_or(false)
            {
                continue;
            }

            if self
                .open
                .get(&key)
                .map(|bar| bar.bar_start > start)
                .unwrap_or(false)
            {
                continue;
            }

            if self
                .open
                .get(&key)
                .map(|bar| bar.bar_start < start)
                .unwrap_or(false)
            {
                if let Some(old_bar) = self.open.remove(&key) {
                    let row = self.finalize_bar(old_bar);
                    self.push_closed(key.clone(), row.clone());
                    finalized.push(row);
                }
            }

            let bar = self.open.entry(key).or_insert_with(|| {
                MutableBar::new(frame.label.clone(), sym, start, end, frame.seconds as f64)
            });
            match event {
                MarketEvent::Trade(trade) => bar.apply_trade(trade),
                MarketEvent::Quote(quote) => bar.apply_quote(quote),
            }
        }
        finalized
    }

    fn finalize_due(&mut self, now: DateTime<Utc>) -> Vec<BarRow> {
        let due_keys = self
            .open
            .iter()
            .filter_map(|(key, bar)| {
                if bar.bar_end <= now {
                    Some(key.clone())
                } else {
                    None
                }
            })
            .collect::<Vec<_>>();
        let mut finalized = Vec::with_capacity(due_keys.len());
        for key in due_keys {
            if let Some(bar) = self.open.remove(&key) {
                let row = self.finalize_bar(bar);
                self.push_closed(key, row.clone());
                finalized.push(row);
            }
        }
        finalized
    }

    fn finalize_bar(&self, bar: MutableBar) -> BarRow {
        let mut row = self.freeze_bar(&bar, true);
        let key = BarKey {
            sym: row.sym.clone(),
            timeframe: row.timeframe.clone(),
        };
        if let Some(history) = self.closed.get(&key) {
            let previous = history.back();
            row.return_1_bar = previous
                .map(|item| pct_change(row.close, item.close))
                .unwrap_or_default();
            row.return_3_bar = trailing_return(&row, history, 3);
            row.return_5_bar = trailing_return(&row, history, 5);
            row.volume_accel = previous
                .map(|item| row.volume - item.volume)
                .unwrap_or_default();
            row.trade_count_accel = previous
                .map(|item| row.trade_count as f64 - item.trade_count as f64)
                .unwrap_or_default();
            row.dollar_volume_accel = previous
                .map(|item| row.dollar_volume - item.dollar_volume)
                .unwrap_or_default();
            row.quote_rate_accel = previous
                .map(|item| row.quote_rate - item.quote_rate)
                .unwrap_or_default();
            row.tape_imbalance_accel = previous
                .map(|item| row.tape_imbalance - item.tape_imbalance)
                .unwrap_or_default();
        }
        row
    }

    fn freeze_bar(&self, bar: &MutableBar, is_closed: bool) -> BarRow {
        let spread_mean = safe_div(bar.spread_sum, bar.quote_count as f64);
        let spread_bps_mean = safe_div(bar.spread_bps_sum, bar.quote_count as f64);
        let spread_bps_close = safe_div(bar.spread_close, bar.mid_close) * 10_000.0;
        let effective_spread_mean = safe_div(bar.effective_spread_sum, bar.trade_count as f64);
        let avg_trade_size = safe_div(bar.volume, bar.trade_count as f64);
        let median_trade_size = sample_median(&bar.trade_size_sample);
        let tape_imbalance = safe_div(bar.buy_volume - bar.sell_volume, bar.volume);
        let buy_sell_volume_delta = bar.buy_volume - bar.sell_volume;
        let realized_volatility =
            safe_div(bar.trade_return_sum_sq, bar.trade_return_count as f64).sqrt();
        let mid_price_volatility =
            safe_div(bar.mid_return_sum_sq, bar.mid_return_count as f64).sqrt();
        let micro_price_volatility = mid_price_volatility;
        let mean_abs_trade_return =
            safe_div(bar.trade_abs_return_sum, bar.trade_return_count as f64);
        let high_low_range = if bar.high > 0.0 && bar.low > 0.0 {
            bar.high - bar.low
        } else {
            0.0
        };
        let price_change = if bar.open > 0.0 {
            bar.close - bar.open
        } else {
            0.0
        };
        let vwap = safe_div(bar.dollar_volume, bar.volume);
        let vwap_distance_pct = pct_change(bar.close, vwap);
        let mid_vwap_distance_pct = pct_change(bar.mid_close, vwap);
        let depth_imbalance_proxy = safe_div(
            bar.bid_size_sum - bar.ask_size_sum,
            bar.bid_size_sum + bar.ask_size_sum,
        );
        let liquidity_score = safe_div(bar.dollar_volume, spread_bps_mean.max(1.0));
        let spread_volume_ratio = safe_div(spread_bps_mean, bar.dollar_volume);
        let chop_score = if high_low_range > 0.0 {
            safe_div(
                bar.trade_abs_return_sum * bar.close.max(1.0),
                high_low_range,
            )
        } else {
            0.0
        };

        BarRow {
            schema_version: BAR_SCHEMA_VERSION,
            session_date: bar.bar_start.date_naive().to_string(),
            timeframe: bar.timeframe.clone(),
            sym: bar.sym.clone(),
            bar_start: bar.bar_start,
            bar_end: bar.bar_end,
            is_closed,
            first_event_ts: bar.first_event_ts.clone(),
            last_event_ts: bar.last_event_ts.clone(),
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            dollar_volume: bar.dollar_volume,
            trade_count: bar.trade_count,
            vwap,
            avg_trade_size,
            median_trade_size,
            max_trade_size: bar.max_trade_size,
            large_trade_count: bar.large_trade_count,
            large_trade_volume: bar.large_trade_volume,
            large_trade_notional: bar.large_trade_notional,
            trade_rate: safe_div(bar.trade_count as f64, bar.seconds),
            volume_rate: safe_div(bar.volume, bar.seconds),
            dollar_volume_rate: safe_div(bar.dollar_volume, bar.seconds),
            price_change,
            price_change_pct: pct_change(bar.close, bar.open),
            high_low_range,
            high_low_range_pct: safe_div(high_low_range, bar.open) * 100.0,
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
            spread_open: bar.spread_open,
            spread_high: bar.spread_high,
            spread_low: bar.spread_low,
            spread_close: bar.spread_close,
            spread_mean,
            spread_bps_mean,
            spread_bps_close,
            quoted_bid_size_mean: safe_div(bar.bid_size_sum, bar.quote_count as f64),
            quoted_ask_size_mean: safe_div(bar.ask_size_sum, bar.quote_count as f64),
            quote_count: bar.quote_count,
            quote_rate: safe_div(bar.quote_count as f64, bar.seconds),
            quote_update_intensity: safe_div(bar.quote_count as f64, bar.trade_count.max(1) as f64),
            locked_crossed_quote_count: bar.locked_crossed_quote_count,
            buy_trade_count: bar.buy_trade_count,
            sell_trade_count: bar.sell_trade_count,
            buy_volume: bar.buy_volume,
            sell_volume: bar.sell_volume,
            buy_dollar_volume: bar.buy_dollar_volume,
            sell_dollar_volume: bar.sell_dollar_volume,
            tape_imbalance,
            aggressive_buy_ratio: safe_div(bar.buy_volume, bar.volume),
            aggressive_sell_ratio: safe_div(bar.sell_volume, bar.volume),
            buy_sell_volume_delta,
            cumulative_delta: buy_sell_volume_delta,
            effective_spread_mean,
            realized_spread_proxy: effective_spread_mean,
            price_impact_1s: vwap_distance_pct,
            price_impact_5s: vwap_distance_pct,
            slippage_proxy_bps: effective_spread_mean.max(spread_bps_close),
            depth_imbalance_proxy,
            liquidity_score,
            spread_volume_ratio,
            return_1_bar: 0.0,
            return_3_bar: 0.0,
            return_5_bar: 0.0,
            volume_accel: 0.0,
            trade_count_accel: 0.0,
            dollar_volume_accel: 0.0,
            quote_rate_accel: 0.0,
            tape_imbalance_accel: 0.0,
            vwap_distance_pct,
            mid_vwap_distance_pct,
            realized_volatility,
            micro_price_volatility,
            mid_price_volatility,
            mean_abs_trade_return,
            direction_change_count: bar.direction_change_count,
            chop_score,
        }
    }

    fn push_closed(&mut self, key: BarKey, row: BarRow) {
        let history = self.closed.entry(key).or_insert_with(VecDeque::new);
        history.push_back(row);
        while history.len() > self.history_limit {
            history.pop_front();
        }
    }
}

impl MutableBar {
    fn new(
        timeframe: String,
        sym: String,
        bar_start: DateTime<Utc>,
        bar_end: DateTime<Utc>,
        seconds: f64,
    ) -> Self {
        Self {
            timeframe,
            sym,
            bar_start,
            bar_end,
            seconds,
            first_event_ts: None,
            last_event_ts: None,
            open: 0.0,
            high: 0.0,
            low: 0.0,
            close: 0.0,
            volume: 0.0,
            dollar_volume: 0.0,
            trade_count: 0,
            trade_size_sample: Vec::with_capacity(512),
            trade_sample_cursor: 0,
            max_trade_size: 0.0,
            large_trade_count: 0,
            large_trade_volume: 0.0,
            large_trade_notional: 0.0,
            last_trade_price: 0.0,
            prev_trade_return_sign: 0,
            trade_return_sum_sq: 0.0,
            trade_abs_return_sum: 0.0,
            trade_return_count: 0,
            direction_change_count: 0,
            bid_open: 0.0,
            bid_high: 0.0,
            bid_low: 0.0,
            bid_close: 0.0,
            ask_open: 0.0,
            ask_high: 0.0,
            ask_low: 0.0,
            ask_close: 0.0,
            mid_open: 0.0,
            mid_high: 0.0,
            mid_low: 0.0,
            mid_close: 0.0,
            spread_open: 0.0,
            spread_high: 0.0,
            spread_low: 0.0,
            spread_close: 0.0,
            spread_sum: 0.0,
            spread_bps_sum: 0.0,
            bid_size_sum: 0.0,
            ask_size_sum: 0.0,
            quote_count: 0,
            locked_crossed_quote_count: 0,
            last_bid: 0.0,
            last_ask: 0.0,
            last_mid: 0.0,
            last_mid_for_return: 0.0,
            mid_return_sum_sq: 0.0,
            mid_return_count: 0,
            buy_trade_count: 0,
            sell_trade_count: 0,
            buy_volume: 0.0,
            sell_volume: 0.0,
            buy_dollar_volume: 0.0,
            sell_dollar_volume: 0.0,
            effective_spread_sum: 0.0,
        }
    }

    fn apply_trade(&mut self, trade: &TradeEvent) {
        self.observe_event_time(trade.ts);
        if trade.price <= 0.0 || trade.size <= 0.0 {
            return;
        }
        if self.open == 0.0 {
            self.open = trade.price;
            self.high = trade.price;
            self.low = trade.price;
        } else {
            self.high = self.high.max(trade.price);
            self.low = positive_min(self.low, trade.price);
        }
        self.close = trade.price;
        self.volume += trade.size;
        self.dollar_volume += trade.price * trade.size;
        self.trade_count += 1;
        self.max_trade_size = self.max_trade_size.max(trade.size);
        if trade.size >= 10_000.0 || trade.price * trade.size >= 100_000.0 {
            self.large_trade_count += 1;
            self.large_trade_volume += trade.size;
            self.large_trade_notional += trade.price * trade.size;
        }
        self.push_trade_size_sample(trade.size);
        self.observe_trade_return(trade.price);

        let side = self.classify_trade_side(trade.price);
        if side >= 0 {
            self.buy_trade_count += 1;
            self.buy_volume += trade.size;
            self.buy_dollar_volume += trade.price * trade.size;
        } else {
            self.sell_trade_count += 1;
            self.sell_volume += trade.size;
            self.sell_dollar_volume += trade.price * trade.size;
        }
        if self.last_mid > 0.0 {
            self.effective_spread_sum +=
                safe_div((trade.price - self.last_mid).abs() * 2.0, self.last_mid) * 10_000.0;
        }
    }

    fn apply_quote(&mut self, quote: &QuoteEvent) {
        self.observe_event_time(quote.ts);
        let bid = quote.bid_price;
        let ask = quote.ask_price;
        if bid <= 0.0 || ask <= 0.0 {
            return;
        }
        let mid = (bid + ask) / 2.0;
        let spread = ask - bid;
        self.apply_price_ohlc(bid, "bid");
        self.apply_price_ohlc(ask, "ask");
        self.apply_price_ohlc(mid, "mid");
        self.apply_spread(spread);
        self.quote_count += 1;
        self.bid_size_sum += quote.bid_size as f64;
        self.ask_size_sum += quote.ask_size as f64;
        if bid >= ask {
            self.locked_crossed_quote_count += 1;
        }
        self.spread_sum += spread;
        self.spread_bps_sum += safe_div(spread, mid) * 10_000.0;
        if self.last_mid_for_return > 0.0 && mid > 0.0 {
            let ret = pct_change(mid, self.last_mid_for_return) / 100.0;
            self.mid_return_sum_sq += ret * ret;
            self.mid_return_count += 1;
        }
        self.last_bid = bid;
        self.last_ask = ask;
        self.last_mid = mid;
        self.last_mid_for_return = mid;
    }

    fn observe_event_time(&mut self, ts: DateTime<Utc>) {
        if self.first_event_ts.is_none() {
            self.first_event_ts = Some(ts);
        }
        self.last_event_ts = Some(ts);
    }

    fn apply_price_ohlc(&mut self, price: f64, target: &str) {
        match target {
            "bid" => update_ohlc(
                price,
                &mut self.bid_open,
                &mut self.bid_high,
                &mut self.bid_low,
                &mut self.bid_close,
            ),
            "ask" => update_ohlc(
                price,
                &mut self.ask_open,
                &mut self.ask_high,
                &mut self.ask_low,
                &mut self.ask_close,
            ),
            "mid" => update_ohlc(
                price,
                &mut self.mid_open,
                &mut self.mid_high,
                &mut self.mid_low,
                &mut self.mid_close,
            ),
            _ => {}
        }
    }

    fn apply_spread(&mut self, spread: f64) {
        update_ohlc(
            spread,
            &mut self.spread_open,
            &mut self.spread_high,
            &mut self.spread_low,
            &mut self.spread_close,
        );
    }

    fn push_trade_size_sample(&mut self, size: f64) {
        if self.trade_size_sample.len() < 512 {
            self.trade_size_sample.push(size);
            return;
        }
        let index = self.trade_sample_cursor % self.trade_size_sample.len();
        self.trade_size_sample[index] = size;
        self.trade_sample_cursor += 1;
    }

    fn observe_trade_return(&mut self, price: f64) {
        if self.last_trade_price > 0.0 {
            let ret = pct_change(price, self.last_trade_price) / 100.0;
            self.trade_return_sum_sq += ret * ret;
            self.trade_abs_return_sum += ret.abs();
            self.trade_return_count += 1;
            let sign = if ret > 0.0 {
                1
            } else if ret < 0.0 {
                -1
            } else {
                0
            };
            if sign != 0 && self.prev_trade_return_sign != 0 && sign != self.prev_trade_return_sign
            {
                self.direction_change_count += 1;
            }
            if sign != 0 {
                self.prev_trade_return_sign = sign;
            }
        }
        self.last_trade_price = price;
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
        -1
    }
}

pub fn spawn_bar_engines(
    bars: SharedBarStore,
    channel_capacity: usize,
    indicator_sender: Option<mpsc::Sender<BarRow>>,
    scanner_sender: Option<ScannerPrimitiveRouter>,
    writer_sender: mpsc::Sender<BarRow>,
    metrics: SharedMetrics,
) -> BarEventRouter {
    let shard_count = bars.shard_count();
    let per_shard_capacity = (channel_capacity / shard_count).max(1);
    let mut senders = Vec::with_capacity(shard_count);
    for shard_id in 0..shard_count {
        let (sender, receiver) = mpsc::channel::<MarketEvent>(per_shard_capacity);
        senders.push(sender);
        tokio::spawn(run_bar_engine(
            shard_id,
            bars.shard(shard_id),
            receiver,
            indicator_sender.clone(),
            scanner_sender.clone(),
            writer_sender.clone(),
            metrics.clone(),
        ));
    }
    BarEventRouter {
        senders: Arc::new(senders),
    }
}

async fn run_bar_engine(
    shard_id: usize,
    shard: BarShardStore,
    mut receiver: mpsc::Receiver<MarketEvent>,
    indicator_sender: Option<mpsc::Sender<BarRow>>,
    scanner_sender: Option<ScannerPrimitiveRouter>,
    writer_sender: mpsc::Sender<BarRow>,
    metrics: SharedMetrics,
) {
    let mut heartbeat = interval(Duration::from_millis(250));
    loop {
        tokio::select! {
            event = receiver.recv() => {
                match event {
                    Some(event) => {
                        let finalized = shard.apply_event(&event).await;
                        send_finalized_bars(shard_id, &writer_sender, indicator_sender.as_ref(), scanner_sender.as_ref(), &metrics, finalized).await;
                    }
                    None => {
                        let finalized = shard.finalize_due(Utc::now()).await;
                        send_finalized_bars(shard_id, &writer_sender, indicator_sender.as_ref(), scanner_sender.as_ref(), &metrics, finalized).await;
                        return;
                    }
                }
            }
            _ = heartbeat.tick() => {
                let finalized = shard.finalize_due(Utc::now()).await;
                send_finalized_bars(shard_id, &writer_sender, indicator_sender.as_ref(), scanner_sender.as_ref(), &metrics, finalized).await;
            }
        }
    }
}

async fn send_finalized_bars(
    shard_id: usize,
    writer_sender: &mpsc::Sender<BarRow>,
    indicator_sender: Option<&mpsc::Sender<BarRow>>,
    scanner_sender: Option<&ScannerPrimitiveRouter>,
    metrics: &SharedMetrics,
    rows: Vec<BarRow>,
) {
    metrics.inc_bar_emitted(rows.len() as u64);
    for row in rows {
        if let Some(sender) = indicator_sender {
            if sender.send(row.clone()).await.is_err() {
                metrics.inc_bar_indicator_dropped();
                eprintln!("Indicator bar receiver closed; shard {shard_id} could not route one finalized bar.");
            }
        }
        if let Some(sender) = scanner_sender {
            if sender.send_bar(row.clone()).await.is_err() {
                metrics.inc_bar_scanner_dropped();
                eprintln!("Scanner primitive receiver closed; shard {shard_id} could not route one finalized bar.");
            }
        }
        if writer_sender.send(row).await.is_err() {
            metrics.inc_bar_writer_dropped();
            eprintln!(
                "Bar writer receiver closed; shard {shard_id} could not persist one finalized bar."
            );
        } else {
            metrics.inc_bar_persist_queued();
        }
    }
}

#[derive(Clone)]
pub struct BarClickHouseWriter {
    client: Client,
    config: GatewayConfig,
}

impl BarClickHouseWriter {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
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
            CREATE TABLE IF NOT EXISTS live_market_bars
            (
                session_date Date,
                schema_version UInt16,
                timeframe LowCardinality(String),
                sym LowCardinality(String),
                bar_start DateTime64(3, 'UTC'),
                bar_end DateTime64(3, 'UTC'),
                is_closed UInt8,
                first_event_ts Nullable(DateTime64(3, 'UTC')),
                last_event_ts Nullable(DateTime64(3, 'UTC')),
                open Float64,
                high Float64,
                low Float64,
                close Float64,
                volume Float64,
                dollar_volume Float64,
                trade_count UInt64,
                vwap Float64,
                avg_trade_size Float64,
                median_trade_size Float64,
                max_trade_size Float64,
                large_trade_count UInt64,
                large_trade_volume Float64,
                large_trade_notional Float64,
                trade_rate Float64,
                volume_rate Float64,
                dollar_volume_rate Float64,
                price_change Float64,
                price_change_pct Float64,
                high_low_range Float64,
                high_low_range_pct Float64,
                bid_open Float64,
                bid_high Float64,
                bid_low Float64,
                bid_close Float64,
                ask_open Float64,
                ask_high Float64,
                ask_low Float64,
                ask_close Float64,
                mid_open Float64,
                mid_high Float64,
                mid_low Float64,
                mid_close Float64,
                spread_open Float64,
                spread_high Float64,
                spread_low Float64,
                spread_close Float64,
                spread_mean Float64,
                spread_bps_mean Float64,
                spread_bps_close Float64,
                quoted_bid_size_mean Float64,
                quoted_ask_size_mean Float64,
                quote_count UInt64,
                quote_rate Float64,
                quote_update_intensity Float64,
                locked_crossed_quote_count UInt64,
                buy_trade_count UInt64,
                sell_trade_count UInt64,
                buy_volume Float64,
                sell_volume Float64,
                buy_dollar_volume Float64,
                sell_dollar_volume Float64,
                tape_imbalance Float64,
                aggressive_buy_ratio Float64,
                aggressive_sell_ratio Float64,
                buy_sell_volume_delta Float64,
                cumulative_delta Float64,
                effective_spread_mean Float64,
                realized_spread_proxy Float64,
                price_impact_1s Float64,
                price_impact_5s Float64,
                slippage_proxy_bps Float64,
                depth_imbalance_proxy Float64,
                liquidity_score Float64,
                spread_volume_ratio Float64,
                return_1_bar Float64,
                return_3_bar Float64,
                return_5_bar Float64,
                volume_accel Float64,
                trade_count_accel Float64,
                dollar_volume_accel Float64,
                quote_rate_accel Float64,
                tape_imbalance_accel Float64,
                vwap_distance_pct Float64,
                mid_vwap_distance_pct Float64,
                realized_volatility Float64,
                micro_price_volatility Float64,
                mid_price_volatility Float64,
                mean_abs_trade_return Float64,
                direction_change_count UInt64,
                chop_score Float64
            )
            ENGINE = ReplacingMergeTree
            PARTITION BY session_date
            ORDER BY (session_date, timeframe, sym, bar_start)
            "#,
            true,
        )
        .await?;
        self.execute(
            "ALTER TABLE live_market_bars ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await?;
        self.execute(
            "ALTER TABLE live_market_bars ADD COLUMN IF NOT EXISTS large_trade_notional Float64 AFTER large_trade_volume",
            true,
        )
        .await?;
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<BarRow>) {
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

    async fn flush(&self, batch: &mut Vec<BarRow>) {
        if batch.is_empty() {
            return;
        }
        if let Err(error) = self.insert_bars(batch).await {
            eprintln!("ClickHouse bar insert failed: {error}");
        } else {
            batch.clear();
        }
    }

    async fn insert_bars(&self, rows: &[BarRow]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|row| {
                serde_json::to_string(&bar_insert_row(row)).unwrap_or_else(|_| "{}".to_string())
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body("INSERT INTO live_market_bars FORMAT JSONEachRow", body)
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

fn bar_insert_row(row: &BarRow) -> serde_json::Value {
    json!({
        "session_date": &row.session_date,
        "schema_version": row.schema_version,
        "timeframe": &row.timeframe,
        "sym": &row.sym,
        "bar_start": clickhouse_datetime64(&row.bar_start),
        "bar_end": clickhouse_datetime64(&row.bar_end),
        "is_closed": row.is_closed as u8,
        "first_event_ts": clickhouse_datetime64_opt(row.first_event_ts.as_ref()),
        "last_event_ts": clickhouse_datetime64_opt(row.last_event_ts.as_ref()),
        "open": row.open,
        "high": row.high,
        "low": row.low,
        "close": row.close,
        "volume": row.volume,
        "dollar_volume": row.dollar_volume,
        "trade_count": row.trade_count,
        "vwap": row.vwap,
        "avg_trade_size": row.avg_trade_size,
        "median_trade_size": row.median_trade_size,
        "max_trade_size": row.max_trade_size,
        "large_trade_count": row.large_trade_count,
        "large_trade_volume": row.large_trade_volume,
        "large_trade_notional": row.large_trade_notional,
        "trade_rate": row.trade_rate,
        "volume_rate": row.volume_rate,
        "dollar_volume_rate": row.dollar_volume_rate,
        "price_change": row.price_change,
        "price_change_pct": row.price_change_pct,
        "high_low_range": row.high_low_range,
        "high_low_range_pct": row.high_low_range_pct,
        "bid_open": row.bid_open,
        "bid_high": row.bid_high,
        "bid_low": row.bid_low,
        "bid_close": row.bid_close,
        "ask_open": row.ask_open,
        "ask_high": row.ask_high,
        "ask_low": row.ask_low,
        "ask_close": row.ask_close,
        "mid_open": row.mid_open,
        "mid_high": row.mid_high,
        "mid_low": row.mid_low,
        "mid_close": row.mid_close,
        "spread_open": row.spread_open,
        "spread_high": row.spread_high,
        "spread_low": row.spread_low,
        "spread_close": row.spread_close,
        "spread_mean": row.spread_mean,
        "spread_bps_mean": row.spread_bps_mean,
        "spread_bps_close": row.spread_bps_close,
        "quoted_bid_size_mean": row.quoted_bid_size_mean,
        "quoted_ask_size_mean": row.quoted_ask_size_mean,
        "quote_count": row.quote_count,
        "quote_rate": row.quote_rate,
        "quote_update_intensity": row.quote_update_intensity,
        "locked_crossed_quote_count": row.locked_crossed_quote_count,
        "buy_trade_count": row.buy_trade_count,
        "sell_trade_count": row.sell_trade_count,
        "buy_volume": row.buy_volume,
        "sell_volume": row.sell_volume,
        "buy_dollar_volume": row.buy_dollar_volume,
        "sell_dollar_volume": row.sell_dollar_volume,
        "tape_imbalance": row.tape_imbalance,
        "aggressive_buy_ratio": row.aggressive_buy_ratio,
        "aggressive_sell_ratio": row.aggressive_sell_ratio,
        "buy_sell_volume_delta": row.buy_sell_volume_delta,
        "cumulative_delta": row.cumulative_delta,
        "effective_spread_mean": row.effective_spread_mean,
        "realized_spread_proxy": row.realized_spread_proxy,
        "price_impact_1s": row.price_impact_1s,
        "price_impact_5s": row.price_impact_5s,
        "slippage_proxy_bps": row.slippage_proxy_bps,
        "depth_imbalance_proxy": row.depth_imbalance_proxy,
        "liquidity_score": row.liquidity_score,
        "spread_volume_ratio": row.spread_volume_ratio,
        "return_1_bar": row.return_1_bar,
        "return_3_bar": row.return_3_bar,
        "return_5_bar": row.return_5_bar,
        "volume_accel": row.volume_accel,
        "trade_count_accel": row.trade_count_accel,
        "dollar_volume_accel": row.dollar_volume_accel,
        "quote_rate_accel": row.quote_rate_accel,
        "tape_imbalance_accel": row.tape_imbalance_accel,
        "vwap_distance_pct": row.vwap_distance_pct,
        "mid_vwap_distance_pct": row.mid_vwap_distance_pct,
        "realized_volatility": row.realized_volatility,
        "micro_price_volatility": row.micro_price_volatility,
        "mid_price_volatility": row.mid_price_volatility,
        "mean_abs_trade_return": row.mean_abs_trade_return,
        "direction_change_count": row.direction_change_count,
        "chop_score": row.chop_score,
    })
}

fn parse_timeframe(label: &str) -> Option<BarFrame> {
    let label = canonical_timeframe(label);
    let seconds = match label.as_str() {
        "1s" => 1,
        "10s" => 10,
        "30s" => 30,
        "1m" => 60,
        "5m" => 300,
        "1h" => 3_600,
        _ => return None,
    };
    Some(BarFrame { label, seconds })
}

fn canonical_timeframe(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn aligned_start(ts: DateTime<Utc>, seconds: i64) -> DateTime<Utc> {
    let millis = ts.timestamp_millis();
    let bucket_millis = seconds * 1_000;
    let start_millis = millis.div_euclid(bucket_millis) * bucket_millis;
    Utc.timestamp_millis_opt(start_millis)
        .single()
        .unwrap_or(ts)
}

fn shard_index(ticker: &str, shard_count: usize) -> usize {
    let mut hash = 14_695_981_039_346_656_037_u64;
    for byte in ticker.as_bytes() {
        hash ^= *byte as u64;
        hash = hash.wrapping_mul(1_099_511_628_211);
    }
    (hash as usize) % shard_count.max(1)
}

fn update_ohlc(price: f64, open: &mut f64, high: &mut f64, low: &mut f64, close: &mut f64) {
    if price <= 0.0 {
        return;
    }
    if *open == 0.0 {
        *open = price;
        *high = price;
        *low = price;
    } else {
        *high = (*high).max(price);
        *low = positive_min(*low, price);
    }
    *close = price;
}

fn positive_min(current: f64, candidate: f64) -> f64 {
    if current <= 0.0 {
        candidate
    } else {
        current.min(candidate)
    }
}

fn sample_median(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    let mut sorted = values.to_vec();
    sorted.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    let mid = sorted.len() / 2;
    if sorted.len() % 2 == 0 {
        (sorted[mid - 1] + sorted[mid]) / 2.0
    } else {
        sorted[mid]
    }
}

fn trailing_return(row: &BarRow, history: &VecDeque<BarRow>, bars_back: usize) -> f64 {
    if history.len() < bars_back {
        return 0.0;
    }
    let index = history.len() - bars_back;
    history
        .get(index)
        .map(|previous| pct_change(row.close, previous.close))
        .unwrap_or_default()
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
