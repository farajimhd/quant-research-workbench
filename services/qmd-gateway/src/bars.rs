use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEngine, GenericStructureEvent,
    GenericStructureSnapshot,
};
use crate::live_market_state::LiveMarketStateRouter;
use crate::market_products::FamilyBarRow;
use crate::metrics::SharedMetrics;
use crate::scanner::ScannerPrimitiveRouter;
use chrono::{DateTime, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{mpsc, Mutex};
use tokio::time::{interval, Duration};

pub const BAR_SCHEMA_VERSION: u16 = 2;
const ESTIMATED_LULD_WINDOW_SECONDS: i64 = 300;
const ESTIMATED_LULD_NEAR_BAND_PCT: f64 = 1.0;
const FORM_T_EXTENDED_HOURS_CONDITION: u16 = 12;
const REGULAR_SESSION_START_SECONDS: u32 = 9 * 60 * 60 + 30 * 60;
const REGULAR_SESSION_END_SECONDS: u32 = 16 * 60 * 60;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct TradeUpdateRule {
    pub update_high_low: bool,
    pub update_last: bool,
    pub update_volume: bool,
}

impl TradeUpdateRule {
    pub const fn regular() -> Self {
        Self {
            update_high_low: true,
            update_last: true,
            update_volume: true,
        }
    }

    const fn excluded() -> Self {
        Self {
            update_high_low: false,
            update_last: false,
            update_volume: false,
        }
    }
}

#[derive(Clone, Debug)]
pub struct TradeAggregationRules {
    by_condition: Arc<HashMap<u16, TradeUpdateRule>>,
}

impl TradeAggregationRules {
    pub fn new(rules: impl IntoIterator<Item = (u16, TradeUpdateRule)>) -> Result<Self, String> {
        let by_condition = rules.into_iter().collect::<HashMap<_, _>>();
        if by_condition.get(&0).copied() != Some(TradeUpdateRule::regular()) {
            return Err("trade condition 0 must be the canonical regular-sale update rule".into());
        }
        Ok(Self {
            by_condition: Arc::new(by_condition),
        })
    }

    pub(crate) fn resolve(&self, conditions: &[u16], timestamp: DateTime<Utc>) -> TradeUpdateRule {
        if conditions.is_empty() {
            return TradeUpdateRule::regular();
        }
        let local_seconds = timestamp
            .with_timezone(&New_York)
            .time()
            .num_seconds_from_midnight();
        let extended_hours =
            !(REGULAR_SESSION_START_SECONDS..REGULAR_SESSION_END_SECONDS).contains(&local_seconds);
        // Massive includes Form T in extended-hours custom bars only when every
        // additional non-regular condition is itself fully price-eligible. A
        // partial rule such as Prior Reference Price must not leak a Form T
        // print into the bar high/low while remaining ineligible for open/close.
        let form_t_price_eligible = extended_hours
            && conditions.contains(&FORM_T_EXTENDED_HOURS_CONDITION)
            && conditions.iter().all(|condition| {
                if *condition == FORM_T_EXTENDED_HOURS_CONDITION || *condition == 0 {
                    return true;
                }
                self.by_condition
                    .get(condition)
                    .is_some_and(|rule| rule.update_high_low && rule.update_last)
            });
        conditions
            .iter()
            .fold(TradeUpdateRule::regular(), |current, condition| {
                if *condition == FORM_T_EXTENDED_HOURS_CONDITION && form_t_price_eligible {
                    return current;
                }
                let Some(rule) = self.by_condition.get(condition) else {
                    return TradeUpdateRule::excluded();
                };
                TradeUpdateRule {
                    update_high_low: current.update_high_low && rule.update_high_low,
                    update_last: current.update_last && rule.update_last,
                    update_volume: current.update_volume && rule.update_volume,
                }
            })
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct BarSnapshot {
    pub current: Option<BarRow>,
    pub history: Vec<BarRow>,
    pub ticker: String,
    pub timeframe: String,
}

impl BarSnapshot {
    pub fn price_bars(mut self) -> Self {
        self.history.retain(valid_price_bar);
        self.current = self.current.filter(valid_price_bar);
        self
    }

    pub fn reconcile_family_authority(&mut self, family_rows: &[FamilyBarRow]) {
        let trade_rows = family_rows
            .iter()
            .filter(|row| row.bar_family == "trade")
            .map(|row| (row.bar_start.timestamp_micros(), row))
            .collect::<HashMap<_, _>>();
        for bar in self.history.iter_mut().chain(self.current.iter_mut()) {
            let Some(row) = trade_rows.get(&bar.bar_start.timestamp_micros()) else {
                continue;
            };
            bar.open = f64::from(row.open);
            bar.high = f64::from(row.high);
            bar.low = f64::from(row.low);
            bar.close = f64::from(row.close);
            bar.volume = row.size_sum;
            bar.trade_count = row.event_count;
            bar.avg_trade_size = if row.event_count > 0 {
                row.size_sum / row.event_count as f64
            } else {
                0.0
            };
        }
    }
}

fn valid_price_bar(bar: &BarRow) -> bool {
    [bar.open, bar.high, bar.low, bar.close]
        .into_iter()
        .all(|value| value.is_finite() && value > 0.0)
        && bar.high >= bar.open.max(bar.close)
        && bar.low <= bar.open.min(bar.close)
        && bar.high >= bar.low
}

#[derive(Clone, Debug, Serialize)]
pub struct BarRow {
    /// Schema version for durable bar rows and replay compatibility.
    pub schema_version: u16,
    /// UTC calendar date from `bar_start`; used for ClickHouse partitioning.
    pub session_date: String,
    /// Canonical bar length label, for example `100ms`, `1s`, `5s`, `10s`, `30s`, `1m`, `5m`, or `1h`.
    pub timeframe: String,
    /// Uppercase Massive ticker symbol.
    pub sym: String,
    /// Bar bucket start, aligned by flooring event timestamp to the timeframe boundary.
    pub bar_start: DateTime<Utc>,
    /// Bar bucket end, calculated as `bar_start + timeframe duration`.
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
    /// True when the bar is inside the regular 9:30-16:00 ET session where LULD bands apply.
    pub estimated_luld_active: bool,
    /// Local proxy for the SIP LULD reference price. Uses the simple average of valid trade prices in the prior five minutes.
    pub estimated_luld_reference_price: f64,
    /// Estimated lower LULD band from the local reference price and Tier 2/default parameter rules.
    pub estimated_luld_lower_price: f64,
    /// Estimated upper LULD band from the local reference price and Tier 2/default parameter rules.
    pub estimated_luld_upper_price: f64,
    /// Effective percent parameter used for the local estimate. For sub-$0.75 names this reflects the absolute $0.15 cap when tighter than 75%.
    pub estimated_luld_parameter_pct: f64,
    /// Percent distance from current bar price to estimated upper band. Lower values mean closer to limit up.
    pub estimated_luld_distance_to_upper_pct: f64,
    /// Percent distance from current bar price to estimated lower band. Lower values mean closer to limit down.
    pub estimated_luld_distance_to_lower_pct: f64,
    /// Compact state for scanner/UI: `inactive`, `unknown`, `inside`, `near_upper`, `near_lower`, `above_upper`, or `below_lower`.
    pub estimated_luld_state: String,
    /// Canonical event-native, multi-scale QMD structure sampled causally at this bar's last event.
    pub qmd_structure: GenericStructureSnapshot,
    /// Exact structural changes confirmed inside this bar. Persisted by the canonical 100 ms indicator lane.
    #[serde(skip_serializing)]
    pub qmd_structure_events: Vec<GenericStructureEvent>,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct BarKey {
    sym: String,
    timeframe: String,
}

#[derive(Clone, Debug)]
struct BarFrame {
    label: String,
    duration_millis: i64,
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
    structure_event_frame_label: String,
    history_limit: usize,
    trade_rules: TradeAggregationRules,
    luld: HashMap<String, EstimatedLuldState>,
    structure: HashMap<String, GenericStructureEngine>,
    open: HashMap<BarKey, MutableBar>,
    closed: HashMap<BarKey, VecDeque<BarRow>>,
}

#[derive(Clone, Debug)]
struct EstimatedLuldSnapshot {
    active: bool,
    distance_to_lower_pct: f64,
    distance_to_upper_pct: f64,
    lower_price: f64,
    parameter_pct: f64,
    reference_price: f64,
    state: String,
    upper_price: f64,
}

#[derive(Clone, Debug, Default)]
struct EstimatedLuldState {
    price_sum: f64,
    prices: VecDeque<(DateTime<Utc>, f64)>,
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
    estimated_luld: EstimatedLuldSnapshot,
    qmd_structure: GenericStructureSnapshot,
    qmd_structure_events: Vec<GenericStructureEvent>,
}

impl SharedBarStore {
    pub fn new(
        timeframes: Vec<String>,
        history_limit: usize,
        shard_count: usize,
        trade_rules: TradeAggregationRules,
    ) -> Self {
        let frames = timeframes
            .into_iter()
            .filter_map(|label| parse_timeframe(&label))
            .collect::<Vec<_>>();
        let shard_count = shard_count.max(1);
        let shards = (0..shard_count)
            .map(|_| BarShardStore::new(frames.clone(), history_limit, trade_rules.clone()))
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

    pub async fn seed_structure_events(&self, events: Vec<GenericStructureEvent>) {
        let mut by_shard =
            vec![HashMap::<String, Vec<GenericStructureEvent>>::new(); self.shards.len()];
        for event in events {
            let sym = event.sym.to_ascii_uppercase();
            let index = shard_index(&sym, self.shards.len());
            by_shard[index].entry(sym).or_default().push(event);
        }
        for (index, symbols) in by_shard.into_iter().enumerate() {
            if symbols.is_empty() {
                continue;
            }
            self.shards[index].seed_structure_events(symbols).await;
        }
    }

    pub async fn seed_structure_snapshots(
        &self,
        snapshots: Vec<(String, GenericStructureSnapshot)>,
    ) {
        let mut by_shard =
            vec![HashMap::<String, GenericStructureSnapshot>::new(); self.shards.len()];
        for (sym, snapshot) in snapshots {
            let sym = sym.to_ascii_uppercase();
            let index = shard_index(&sym, self.shards.len());
            by_shard[index].insert(sym, snapshot);
        }
        for (index, symbols) in by_shard.into_iter().enumerate() {
            if symbols.is_empty() {
                continue;
            }
            self.shards[index].seed_structure_snapshots(symbols).await;
        }
    }

    pub async fn seed_structure_checkpoints(
        &self,
        checkpoints: Vec<(String, GenericStructureCheckpoint)>,
    ) {
        let mut by_shard = vec![HashMap::new(); self.shards.len()];
        for (sym, checkpoint) in checkpoints {
            let index = shard_index(&sym, self.shards.len());
            by_shard[index].insert(sym, checkpoint);
        }
        for (index, symbols) in by_shard.into_iter().enumerate() {
            self.shards[index].seed_structure_checkpoints(symbols).await;
        }
    }

    pub async fn structure_checkpoints_since(
        &self,
        watermarks: &HashMap<String, i64>,
    ) -> Vec<(String, GenericStructureCheckpoint)> {
        let mut checkpoints = Vec::new();
        for shard in self.shards.iter() {
            let store = shard.inner.lock().await;
            checkpoints.extend(store.structure.iter().filter_map(|(sym, engine)| {
                let updated_at_ms = engine.updated_at_ms();
                if updated_at_ms <= watermarks.get(sym).copied().unwrap_or_default() {
                    return None;
                }
                Some((sym.clone(), engine.checkpoint()))
            }));
        }
        checkpoints.sort_by(|left, right| left.0.cmp(&right.0));
        checkpoints
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
    fn new(
        frames: Vec<BarFrame>,
        history_limit: usize,
        trade_rules: TradeAggregationRules,
    ) -> Self {
        let structure_event_frame_label = frames
            .iter()
            .min_by_key(|frame| frame.duration_millis)
            .map(|frame| frame.label.clone())
            .unwrap_or_default();
        Self {
            inner: Arc::new(Mutex::new(BarStore {
                frames,
                structure_event_frame_label,
                history_limit,
                trade_rules,
                luld: HashMap::new(),
                structure: HashMap::new(),
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

    async fn seed_structure_events(&self, symbols: HashMap<String, Vec<GenericStructureEvent>>) {
        let mut store = self.inner.lock().await;
        for (sym, events) in symbols {
            store
                .structure
                .entry(sym.clone())
                .or_insert_with(|| GenericStructureEngine::new(&sym))
                .seed_events(&events);
        }
    }

    async fn seed_structure_snapshots(&self, symbols: HashMap<String, GenericStructureSnapshot>) {
        let mut store = self.inner.lock().await;
        for (sym, snapshot) in symbols {
            store
                .structure
                .entry(sym.clone())
                .or_insert_with(|| GenericStructureEngine::new(&sym))
                .seed_snapshot(&snapshot);
        }
    }

    async fn seed_structure_checkpoints(
        &self,
        symbols: HashMap<String, GenericStructureCheckpoint>,
    ) {
        let mut store = self.inner.lock().await;
        for (sym, checkpoint) in symbols {
            store
                .structure
                .entry(sym.clone())
                .or_insert_with(|| GenericStructureEngine::new(&sym))
                .seed_checkpoint(&checkpoint);
        }
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
        let sym = event.ticker().to_ascii_uppercase();
        let trade_rule = match event {
            MarketEvent::Trade(trade) => self.trade_rules.resolve(&trade.conditions, trade.ts),
            MarketEvent::Quote(_) => TradeUpdateRule::excluded(),
        };
        if let MarketEvent::Trade(trade) = event {
            if trade_rule.update_last {
                self.luld
                    .entry(sym.clone())
                    .or_default()
                    .observe_trade(trade.ts, trade.price);
            }
        }
        let (structure_snapshot, structure_events) = self
            .structure
            .entry(sym.clone())
            .or_insert_with(|| GenericStructureEngine::new(&sym))
            .apply_event(event, trade_rule);
        for frame in self.frames.clone() {
            let start = aligned_start(event.ts(), frame.duration_millis);
            let end = start + chrono::Duration::milliseconds(frame.duration_millis);
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
                MutableBar::new(
                    frame.label.clone(),
                    sym.clone(),
                    start,
                    end,
                    frame.duration_millis as f64 / 1_000.0,
                )
            });
            match event {
                MarketEvent::Trade(trade) => bar.apply_trade(trade, trade_rule),
                MarketEvent::Quote(quote) => bar.apply_quote(quote),
            }
            if let Some(luld) = self.luld.get_mut(&sym) {
                bar.estimated_luld = luld.snapshot(event.ts(), bar.luld_price());
            }
            bar.qmd_structure = structure_snapshot.clone();
            if bar.timeframe == self.structure_event_frame_label {
                bar.qmd_structure_events
                    .extend(structure_events.iter().cloned());
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
            estimated_luld_active: bar.estimated_luld.active,
            estimated_luld_reference_price: bar.estimated_luld.reference_price,
            estimated_luld_lower_price: bar.estimated_luld.lower_price,
            estimated_luld_upper_price: bar.estimated_luld.upper_price,
            estimated_luld_parameter_pct: bar.estimated_luld.parameter_pct,
            estimated_luld_distance_to_upper_pct: bar.estimated_luld.distance_to_upper_pct,
            estimated_luld_distance_to_lower_pct: bar.estimated_luld.distance_to_lower_pct,
            estimated_luld_state: bar.estimated_luld.state.clone(),
            qmd_structure: bar.qmd_structure.clone(),
            qmd_structure_events: bar.qmd_structure_events.clone(),
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

impl EstimatedLuldSnapshot {
    fn unknown() -> Self {
        Self {
            active: false,
            distance_to_lower_pct: 0.0,
            distance_to_upper_pct: 0.0,
            lower_price: 0.0,
            parameter_pct: 0.0,
            reference_price: 0.0,
            state: "unknown".to_string(),
            upper_price: 0.0,
        }
    }
}

impl EstimatedLuldState {
    fn observe_trade(&mut self, ts: DateTime<Utc>, price: f64) {
        if price <= 0.0 || !price.is_finite() {
            return;
        }
        self.prune(ts);
        self.prices.push_back((ts, price));
        self.price_sum += price;
    }

    fn snapshot(&mut self, ts: DateTime<Utc>, current_price: f64) -> EstimatedLuldSnapshot {
        self.prune(ts);
        let active = is_luld_regular_session(ts);
        if !active {
            return EstimatedLuldSnapshot {
                state: "inactive".to_string(),
                ..EstimatedLuldSnapshot::unknown()
            };
        }
        if self.prices.is_empty() || current_price <= 0.0 {
            return EstimatedLuldSnapshot::unknown();
        }
        let reference_price = self.price_sum / self.prices.len() as f64;
        if reference_price <= 0.0 || !reference_price.is_finite() {
            return EstimatedLuldSnapshot::unknown();
        }
        let parameter_pct = estimated_luld_parameter_pct(reference_price, ts);
        let band_width = reference_price * parameter_pct / 100.0;
        let lower_price = (reference_price - band_width).max(0.0);
        let upper_price = reference_price + band_width;
        let distance_to_upper_pct = safe_div(upper_price - current_price, current_price) * 100.0;
        let distance_to_lower_pct = safe_div(current_price - lower_price, current_price) * 100.0;
        let state = if current_price >= upper_price {
            "above_upper"
        } else if lower_price > 0.0 && current_price <= lower_price {
            "below_lower"
        } else if distance_to_upper_pct <= ESTIMATED_LULD_NEAR_BAND_PCT {
            "near_upper"
        } else if distance_to_lower_pct <= ESTIMATED_LULD_NEAR_BAND_PCT {
            "near_lower"
        } else {
            "inside"
        };
        EstimatedLuldSnapshot {
            active,
            distance_to_lower_pct,
            distance_to_upper_pct,
            lower_price,
            parameter_pct,
            reference_price,
            state: state.to_string(),
            upper_price,
        }
    }

    fn prune(&mut self, now: DateTime<Utc>) {
        let cutoff = now - chrono::Duration::seconds(ESTIMATED_LULD_WINDOW_SECONDS);
        while let Some((ts, price)) = self.prices.front().copied() {
            if ts >= cutoff {
                break;
            }
            self.price_sum -= price;
            self.prices.pop_front();
        }
        if self.prices.is_empty() {
            self.price_sum = 0.0;
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
            estimated_luld: EstimatedLuldSnapshot::unknown(),
            qmd_structure: GenericStructureSnapshot::default(),
            qmd_structure_events: Vec::new(),
        }
    }

    fn apply_trade(&mut self, trade: &TradeEvent, rule: TradeUpdateRule) {
        self.observe_event_time(trade.ts);
        if trade.price <= 0.0 || trade.size <= 0.0 {
            return;
        }
        if rule.update_last {
            if self.open == 0.0 {
                self.open = trade.price;
            }
            self.close = trade.price;
            self.observe_trade_return(trade.price);
        }
        if rule.update_high_low {
            if self.high == 0.0 {
                self.high = trade.price;
                self.low = trade.price;
            } else {
                self.high = self.high.max(trade.price);
                self.low = positive_min(self.low, trade.price);
            }
        }
        if !rule.update_volume {
            return;
        }
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

        if rule.update_last {
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

    fn luld_price(&self) -> f64 {
        if self.mid_close > 0.0 {
            self.mid_close
        } else {
            self.close
        }
    }
}

pub fn spawn_bar_engines(
    bars: SharedBarStore,
    channel_capacity: usize,
    indicator_sender: Option<mpsc::Sender<BarRow>>,
    scanner_sender: Option<ScannerPrimitiveRouter>,
    live_market_state_sender: Option<LiveMarketStateRouter>,
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
            live_market_state_sender.clone(),
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
    live_market_state_sender: Option<LiveMarketStateRouter>,
    metrics: SharedMetrics,
) {
    let mut heartbeat = interval(Duration::from_millis(250));
    loop {
        tokio::select! {
            event = receiver.recv() => {
                match event {
                    Some(event) => {
                        let finalized = shard.apply_event(&event).await;
                        send_finalized_bars(shard_id, indicator_sender.as_ref(), scanner_sender.as_ref(), live_market_state_sender.as_ref(), &metrics, finalized).await;
                    }
                    None => {
                        let finalized = shard.finalize_due(Utc::now()).await;
                        send_finalized_bars(shard_id, indicator_sender.as_ref(), scanner_sender.as_ref(), live_market_state_sender.as_ref(), &metrics, finalized).await;
                        return;
                    }
                }
            }
            _ = heartbeat.tick() => {
                let finalized = shard.finalize_due(Utc::now()).await;
                send_finalized_bars(shard_id, indicator_sender.as_ref(), scanner_sender.as_ref(), live_market_state_sender.as_ref(), &metrics, finalized).await;
            }
        }
    }
}

async fn send_finalized_bars(
    shard_id: usize,
    indicator_sender: Option<&mpsc::Sender<BarRow>>,
    scanner_sender: Option<&ScannerPrimitiveRouter>,
    live_market_state_sender: Option<&LiveMarketStateRouter>,
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
        if let Some(sender) = live_market_state_sender {
            if sender.send_bar(row.clone()).await.is_err() {
                eprintln!("Live market state receiver closed; shard {shard_id} could not route one finalized bar.");
            }
        }
    }
}
fn parse_timeframe(label: &str) -> Option<BarFrame> {
    let label = canonical_timeframe(label);
    let duration_millis = match label.as_str() {
        "100ms" => 100,
        "1s" => 1_000,
        "5s" => 5_000,
        "10s" => 10_000,
        "30s" => 30_000,
        "1m" => 60_000,
        "5m" => 300_000,
        "1h" => 3_600_000,
        _ => return None,
    };
    Some(BarFrame {
        label,
        duration_millis,
    })
}

pub fn is_supported_timeframe(value: &str) -> bool {
    parse_timeframe(value).is_some()
}

fn canonical_timeframe(value: &str) -> String {
    value.trim().to_ascii_lowercase()
}

fn aligned_start(ts: DateTime<Utc>, duration_millis: i64) -> DateTime<Utc> {
    let millis = ts.timestamp_millis();
    let start_millis = millis.div_euclid(duration_millis) * duration_millis;
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

fn estimated_luld_parameter_pct(reference_price: f64, ts: DateTime<Utc>) -> f64 {
    let mut parameter_pct = if reference_price > 3.0 {
        10.0
    } else if reference_price >= 0.75 {
        20.0
    } else {
        safe_div(0.15, reference_price).min(0.75) * 100.0
    };
    if reference_price <= 3.0 && is_luld_close_band_doubling_window(ts) {
        parameter_pct *= 2.0;
    }
    parameter_pct
}

fn is_luld_regular_session(ts: DateTime<Utc>) -> bool {
    let local = ts.with_timezone(&New_York);
    let seconds = local.time().num_seconds_from_midnight();
    seconds >= 9 * 3_600 + 30 * 60 && seconds < 16 * 3_600
}

fn is_luld_close_band_doubling_window(ts: DateTime<Utc>) -> bool {
    let local = ts.with_timezone(&New_York);
    let seconds = local.time().num_seconds_from_midnight();
    seconds >= 15 * 3_600 + 35 * 60 && seconds < 16 * 3_600
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
    use super::*;
    use chrono::TimeZone;

    fn trade(ts: DateTime<Utc>, price: f64, size: f64, conditions: Vec<u16>) -> TradeEvent {
        TradeEvent {
            conditions,
            exchange: 11,
            ingest_ts: ts,
            participant_ts: None,
            price,
            raw: serde_json::Value::Null,
            sequence: 1,
            size,
            tape: 3,
            ticker: "AAPL".into(),
            trade_id: "test".into(),
            trf_id: 0,
            trf_ts: None,
            ts,
        }
    }

    fn quote(ts: DateTime<Utc>, bid: f64, ask: f64, sequence: u64) -> QuoteEvent {
        QuoteEvent {
            ask_exchange: 12,
            ask_price: ask,
            ask_size: 100,
            bid_exchange: 11,
            bid_price: bid,
            bid_size: 100,
            conditions: Vec::new(),
            indicators: Vec::new(),
            ingest_ts: ts,
            raw: serde_json::Value::Null,
            sequence,
            tape: 3,
            ticker: "AAPL".into(),
            ts,
        }
    }

    #[tokio::test]
    async fn event_native_structure_matches_at_aligned_timeframe_endpoints() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let bars = SharedBarStore::new(
            vec!["100ms".into(), "1s".into(), "5s".into()],
            100,
            1,
            rules,
        );
        let shard = bars.shard(0);
        let start = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        for (index, (offset, mid)) in [(0, 100.00), (200, 100.03), (400, 100.06), (900, 100.02)]
            .into_iter()
            .enumerate()
        {
            let ts = start + chrono::Duration::milliseconds(offset);
            shard
                .apply_event(&MarketEvent::Quote(quote(
                    ts,
                    mid - 0.005,
                    mid + 0.005,
                    index as u64,
                )))
                .await;
        }
        shard
            .finalize_due(start + chrono::Duration::seconds(6))
            .await;
        let fine = bars.snapshot("AAPL", "100ms", 20).await;
        let one_second = bars.snapshot("AAPL", "1s", 20).await;
        let five_second = bars.snapshot("AAPL", "5s", 20).await;
        let fine_state = &fine.history.last().unwrap().qmd_structure;
        let one_state = &one_second.history.last().unwrap().qmd_structure;
        let five_state = &five_second.history.last().unwrap().qmd_structure;
        assert_eq!(fine_state.reference_price, one_state.reference_price);
        assert_eq!(one_state.reference_price, five_state.reference_price);
        assert_eq!(
            fine_state
                .timeframe_states
                .iter()
                .map(|state| (state.timeframe.as_str(), state.direction))
                .collect::<Vec<_>>(),
            one_state
                .timeframe_states
                .iter()
                .map(|state| (state.timeframe.as_str(), state.direction))
                .collect::<Vec<_>>()
        );
        assert_eq!(
            one_state
                .timeframe_states
                .iter()
                .map(|state| (state.timeframe.as_str(), state.direction))
                .collect::<Vec<_>>(),
            five_state
                .timeframe_states
                .iter()
                .map(|state| (state.timeframe.as_str(), state.direction))
                .collect::<Vec<_>>()
        );
        assert_eq!(fine_state.last_event_id, five_state.last_event_id);
        let checkpoints = bars.structure_checkpoints_since(&HashMap::new()).await;
        assert_eq!(checkpoints.len(), 1);
        let mut watermarks = HashMap::new();
        watermarks.insert(
            "AAPL".to_string(),
            checkpoints[0].1.updated_at.unwrap().timestamp_millis(),
        );
        assert!(bars
            .structure_checkpoints_since(&watermarks)
            .await
            .is_empty());
    }

    #[test]
    fn supports_fixed_subsecond_and_five_second_boundaries() {
        assert!(is_supported_timeframe("100ms"));
        assert!(is_supported_timeframe("5s"));
        let second = Utc.with_ymd_and_hms(2026, 7, 10, 13, 30, 7).unwrap();
        let timestamp = second + chrono::Duration::milliseconds(987);
        assert_eq!(
            aligned_start(timestamp, 100),
            second + chrono::Duration::milliseconds(900)
        );
        assert_eq!(
            aligned_start(timestamp, 5_000),
            Utc.with_ymd_and_hms(2026, 7, 10, 13, 30, 5).unwrap()
        );
    }

    #[test]
    fn volume_only_conditions_do_not_change_ohlc() {
        let rules = TradeAggregationRules::new([
            (0, TradeUpdateRule::regular()),
            (
                2,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
            (
                12,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
            (
                37,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
        ])
        .unwrap();
        let start = Utc.with_ymd_and_hms(2026, 7, 10, 21, 20, 0).unwrap();
        let mut bar = MutableBar::new(
            "1m".into(),
            "AAPL".into(),
            start,
            start + chrono::Duration::minutes(1),
            60.0,
        );

        bar.apply_trade(
            &trade(start, 315.10, 100.0, vec![0]),
            rules.resolve(&[0], start),
        );
        bar.apply_trade(
            &trade(
                start + chrono::Duration::seconds(1),
                331.7827,
                27.0,
                vec![12, 37],
            ),
            rules.resolve(&[12, 37], start + chrono::Duration::seconds(1)),
        );
        bar.apply_trade(
            &trade(start + chrono::Duration::seconds(2), 315.16, 50.0, vec![0]),
            rules.resolve(&[0], start + chrono::Duration::seconds(2)),
        );

        assert_eq!(bar.open, 315.10);
        assert_eq!(bar.high, 315.16);
        assert_eq!(bar.low, 315.10);
        assert_eq!(bar.close, 315.16);
        assert_eq!(bar.volume, 177.0);
        assert_eq!(bar.trade_count, 3);
    }

    #[test]
    fn form_t_prices_extended_hours_but_not_regular_session() {
        let rules = TradeAggregationRules::new([
            (0, TradeUpdateRule::regular()),
            (
                12,
                TradeUpdateRule {
                    update_high_low: false,
                    update_last: false,
                    update_volume: true,
                },
            ),
            (
                22,
                TradeUpdateRule {
                    update_high_low: true,
                    update_last: false,
                    update_volume: true,
                },
            ),
            (41, TradeUpdateRule::regular()),
        ])
        .unwrap();
        let extended = Utc.with_ymd_and_hms(2026, 7, 10, 21, 20, 0).unwrap();
        let regular = Utc.with_ymd_and_hms(2026, 7, 10, 15, 20, 0).unwrap();

        assert_eq!(rules.resolve(&[12], extended), TradeUpdateRule::regular());
        assert_eq!(
            rules.resolve(&[12, 22], extended),
            TradeUpdateRule {
                update_high_low: false,
                update_last: false,
                update_volume: true,
            }
        );
        assert_eq!(
            rules.resolve(&[12, 41], extended),
            TradeUpdateRule::regular()
        );
        assert_eq!(
            rules.resolve(&[12], regular),
            TradeUpdateRule {
                update_high_low: false,
                update_last: false,
                update_volume: true,
            }
        );
    }

    #[test]
    fn estimated_luld_uses_rolling_five_minute_reference() {
        let mut state = EstimatedLuldState::default();
        let old = Utc.with_ymd_and_hms(2026, 6, 22, 14, 29, 59).unwrap();
        let first = Utc.with_ymd_and_hms(2026, 6, 22, 14, 30, 0).unwrap();
        let second = Utc.with_ymd_and_hms(2026, 6, 22, 14, 31, 0).unwrap();
        let now = Utc.with_ymd_and_hms(2026, 6, 22, 14, 35, 0).unwrap();

        state.observe_trade(old, 8.0);
        state.observe_trade(first, 10.0);
        state.observe_trade(second, 12.0);

        let snapshot = state.snapshot(now, 13.05);
        assert!(snapshot.active);
        assert_eq!(snapshot.reference_price, 11.0);
        assert_eq!(snapshot.parameter_pct, 10.0);
        assert_eq!(snapshot.lower_price, 9.9);
        assert_eq!(snapshot.upper_price, 12.1);
        assert_eq!(snapshot.state, "above_upper");
    }

    #[test]
    fn estimated_luld_marks_near_upper_inside_regular_session() {
        let mut state = EstimatedLuldState::default();
        let now = Utc.with_ymd_and_hms(2026, 6, 22, 15, 0, 0).unwrap();
        state.observe_trade(now, 10.0);

        let snapshot = state.snapshot(now, 10.95);
        assert!(snapshot.active);
        assert_eq!(snapshot.reference_price, 10.0);
        assert_eq!(snapshot.upper_price, 11.0);
        assert_eq!(snapshot.state, "near_upper");
    }

    #[test]
    fn estimated_luld_is_inactive_outside_regular_session() {
        let mut state = EstimatedLuldState::default();
        let premarket = Utc.with_ymd_and_hms(2026, 6, 22, 12, 0, 0).unwrap();
        state.observe_trade(premarket, 10.0);

        let snapshot = state.snapshot(premarket, 10.0);
        assert!(!snapshot.active);
        assert_eq!(snapshot.state, "inactive");
        assert_eq!(snapshot.reference_price, 0.0);
    }
}
