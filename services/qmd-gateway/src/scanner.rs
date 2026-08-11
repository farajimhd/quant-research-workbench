use crate::bars::BarRow;
use crate::indicators::IndicatorRow;
use crate::market_signal::{MarketSignalEngine, MarketSignalEvent};
use crate::metrics::SharedMetrics;
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};

pub const SCANNER_PRIMITIVE_SCHEMA_VERSION: u16 = 2;

#[derive(Clone, Debug, Serialize)]
pub struct MarketSignalSnapshot {
    pub as_of: DateTime<Utc>,
    pub last_sequence: u64,
    pub row_count: usize,
    pub rows: Vec<MarketSignalEvent>,
}

#[derive(Clone, Debug, Serialize)]
pub struct MarketSignalDelta {
    pub sequence: u64,
    #[serde(flatten)]
    pub event: MarketSignalEvent,
}

#[derive(Clone, Debug, Serialize)]
pub struct ScannerPrimitiveSnapshot {
    pub as_of: DateTime<Utc>,
    pub row_count: usize,
    pub rows: Vec<ScannerPrimitive>,
}

#[derive(Clone, Debug, Serialize)]
pub struct ScannerPrimitive {
    pub schema_version: u16,
    pub detected_at: DateTime<Utc>,
    pub ticker: String,
    pub timeframe: String,
    pub primitive_key: String,
    pub side_bias: String,
    pub score: f64,
    pub rank_score: f64,
    pub trigger_reason: String,
    pub reject_reason: String,
    pub close: f64,
    pub vwap: f64,
    pub price_change_pct: f64,
    pub volume: f64,
    pub dollar_volume: f64,
    pub trade_rate: f64,
    pub quote_rate: f64,
    pub tape_imbalance: f64,
    pub spread_bps: f64,
    pub liquidity_score: f64,
    pub estimated_luld_active: bool,
    pub estimated_luld_state: String,
    pub estimated_luld_distance_to_upper_pct: f64,
    pub estimated_luld_distance_to_lower_pct: f64,
}

#[derive(Clone)]
pub struct SharedScannerStore {
    inner: Arc<RwLock<ScannerStore>>,
}

struct ScannerStore {
    latest_by_key: HashMap<String, MarketSignalEvent>,
    history: VecDeque<MarketSignalEvent>,
    history_limit: usize,
    publication_sequence: u64,
}

#[derive(Clone)]
pub struct ScannerPrimitiveRouter {
    sender: mpsc::Sender<ScannerObservation>,
}

impl ScannerPrimitiveRouter {
    pub async fn send_observation(
        &self,
        bar: BarRow,
        indicator: IndicatorRow,
    ) -> Result<(), mpsc::error::SendError<ScannerObservation>> {
        self.sender
            .send(ScannerObservation { bar, indicator })
            .await
    }
}

#[derive(Clone, Debug)]
pub struct ScannerObservation {
    pub bar: BarRow,
    pub indicator: IndicatorRow,
}

impl SharedScannerStore {
    pub fn new(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(ScannerStore {
                latest_by_key: HashMap::new(),
                history: VecDeque::with_capacity(history_limit.min(10_000)),
                history_limit,
                publication_sequence: 0,
            })),
        }
    }

    pub async fn apply(&self, signal: MarketSignalEvent) -> MarketSignalDelta {
        let mut store = self.inner.write().await;
        store.publication_sequence = store.publication_sequence.saturating_add(1);
        let sequence = store.publication_sequence;
        let key = format!(
            "{}:{}:{}",
            signal.ticker, signal.working_timeframe, signal.signal_key
        );
        if signal.state == "resolved" || signal.state == "expired" {
            store.latest_by_key.remove(&key);
        } else {
            store.latest_by_key.insert(key, signal.clone());
        }
        store.history.push_back(signal.clone());
        while store.history.len() > store.history_limit {
            store.history.pop_front();
        }
        MarketSignalDelta {
            sequence,
            event: signal,
        }
    }

    pub async fn signal_snapshot(&self, limit: usize) -> MarketSignalSnapshot {
        let store = self.inner.read().await;
        let mut rows = store.latest_by_key.values().cloned().collect::<Vec<_>>();
        rows.sort_by(|left, right| {
            right
                .rank_score
                .partial_cmp(&left.rank_score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| {
                    right
                        .confidence
                        .partial_cmp(&left.confidence)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .then_with(|| right.effective_at.cmp(&left.effective_at))
                .then_with(|| left.signal_id.cmp(&right.signal_id))
        });
        rows.truncate(limit);
        let as_of = rows
            .iter()
            .map(|row| row.effective_at)
            .max()
            .unwrap_or_else(Utc::now);
        MarketSignalSnapshot {
            as_of,
            last_sequence: store.publication_sequence,
            row_count: rows.len(),
            rows,
        }
    }

    pub async fn signal_event_snapshot(&self, limit: usize) -> MarketSignalSnapshot {
        let store = self.inner.read().await;
        let rows = store
            .history
            .iter()
            .rev()
            .take(limit)
            .cloned()
            .collect::<Vec<_>>();
        let as_of = rows
            .iter()
            .map(|row| row.effective_at)
            .max()
            .unwrap_or_else(Utc::now);
        MarketSignalSnapshot {
            as_of,
            last_sequence: store.publication_sequence,
            row_count: rows.len(),
            rows,
        }
    }

    pub async fn snapshot(&self, limit: usize) -> ScannerPrimitiveSnapshot {
        let snapshot = self.signal_snapshot(limit).await;
        ScannerPrimitiveSnapshot {
            as_of: snapshot.as_of,
            row_count: snapshot.row_count,
            rows: snapshot
                .rows
                .into_iter()
                .map(ScannerPrimitive::from)
                .collect(),
        }
    }
}

pub fn spawn_scanner_primitive_engine(
    store: SharedScannerStore,
    channel_capacity: usize,
    metrics: SharedMetrics,
    signal_sender: broadcast::Sender<MarketSignalDelta>,
) -> ScannerPrimitiveRouter {
    let (sender, receiver) = mpsc::channel::<ScannerObservation>(channel_capacity.max(1));
    tokio::spawn(run_scanner_primitive_engine(
        store,
        receiver,
        metrics,
        signal_sender,
    ));
    ScannerPrimitiveRouter { sender }
}

async fn run_scanner_primitive_engine(
    store: SharedScannerStore,
    mut receiver: mpsc::Receiver<ScannerObservation>,
    metrics: SharedMetrics,
    signal_sender: broadcast::Sender<MarketSignalDelta>,
) {
    let mut engine = MarketSignalEngine::default();
    while let Some(observation) = receiver.recv().await {
        let signals = engine.update_with_indicator(&observation.bar, Some(&observation.indicator));
        if signals.is_empty() {
            continue;
        }
        metrics.inc_scanner_candidates(signals.len() as u64);
        for signal in signals {
            let delta = store.apply(signal).await;
            let _ = signal_sender.send(delta);
        }
    }
}

impl From<MarketSignalEvent> for ScannerPrimitive {
    fn from(signal: MarketSignalEvent) -> Self {
        Self {
            schema_version: SCANNER_PRIMITIVE_SCHEMA_VERSION,
            detected_at: signal.effective_at,
            ticker: signal.ticker,
            timeframe: signal.working_timeframe,
            primitive_key: signal.signal_key,
            side_bias: signal.direction,
            score: signal.score,
            rank_score: signal.rank_score,
            trigger_reason: signal.trigger_reason,
            reject_reason: signal.resolution_reason,
            close: signal.evidence.close,
            vwap: signal.evidence.vwap,
            price_change_pct: signal.evidence.price_change_pct,
            volume: signal.evidence.volume,
            dollar_volume: signal.evidence.dollar_volume,
            trade_rate: signal.evidence.trade_rate,
            quote_rate: signal.evidence.quote_rate,
            tape_imbalance: signal.evidence.tape_imbalance,
            spread_bps: signal.evidence.spread_bps,
            liquidity_score: signal.evidence.liquidity_score,
            estimated_luld_active: signal.evidence.estimated_luld_active,
            estimated_luld_state: signal.evidence.estimated_luld_state,
            estimated_luld_distance_to_upper_pct: 0.0,
            estimated_luld_distance_to_lower_pct: 0.0,
        }
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use chrono::Utc;

    pub(crate) fn base_bar() -> BarRow {
        BarRow {
            schema_version: crate::bars::BAR_SCHEMA_VERSION,
            session_date: "2026-06-05".to_string(),
            timeframe: "10s".to_string(),
            sym: "TEST".to_string(),
            bar_start: Utc::now(),
            bar_end: Utc::now(),
            is_closed: true,
            first_event_ts: None,
            last_event_ts: None,
            open: 10.0,
            high: 10.5,
            low: 9.9,
            close: 10.45,
            volume: 10_000.0,
            dollar_volume: 100_000.0,
            trade_count: 100,
            vwap: 10.1,
            avg_trade_size: 100.0,
            median_trade_size: 100.0,
            max_trade_size: 1_000.0,
            large_trade_count: 0,
            large_trade_volume: 0.0,
            large_trade_notional: 0.0,
            trade_rate: 10.0,
            volume_rate: 1_000.0,
            dollar_volume_rate: 10_000.0,
            price_change: 0.45,
            price_change_pct: 4.5,
            high_low_range: 0.6,
            high_low_range_pct: 6.0,
            bid_open: 10.0,
            bid_high: 10.4,
            bid_low: 9.9,
            bid_close: 10.4,
            ask_open: 10.1,
            ask_high: 10.5,
            ask_low: 10.0,
            ask_close: 10.5,
            mid_open: 10.05,
            mid_high: 10.45,
            mid_low: 9.95,
            mid_close: 10.45,
            spread_open: 0.1,
            spread_high: 0.1,
            spread_low: 0.05,
            spread_close: 0.05,
            spread_mean: 0.08,
            spread_bps_mean: 8.0,
            spread_bps_close: 5.0,
            quoted_bid_size_mean: 1_000.0,
            quoted_ask_size_mean: 900.0,
            quote_count: 120,
            quote_rate: 12.0,
            quote_update_intensity: 1.2,
            locked_crossed_quote_count: 0,
            buy_trade_count: 70,
            sell_trade_count: 30,
            buy_volume: 7_000.0,
            sell_volume: 3_000.0,
            buy_dollar_volume: 70_000.0,
            sell_dollar_volume: 30_000.0,
            tape_imbalance: 0.4,
            aggressive_buy_ratio: 0.7,
            aggressive_sell_ratio: 0.3,
            buy_sell_volume_delta: 4_000.0,
            cumulative_delta: 4_000.0,
            effective_spread_mean: 5.0,
            realized_spread_proxy: 5.0,
            price_impact_1s: 3.0,
            price_impact_5s: 3.0,
            slippage_proxy_bps: 5.0,
            depth_imbalance_proxy: 0.05,
            liquidity_score: 12_500.0,
            spread_volume_ratio: 0.0,
            return_1_bar: 1.0,
            return_3_bar: 2.0,
            return_5_bar: 3.0,
            volume_accel: 1_000.0,
            trade_count_accel: 25.0,
            dollar_volume_accel: 300_000.0,
            quote_rate_accel: 3.0,
            tape_imbalance_accel: 0.2,
            vwap_distance_pct: 3.4,
            mid_vwap_distance_pct: 3.4,
            realized_volatility: 0.01,
            micro_price_volatility: 0.01,
            mid_price_volatility: 0.01,
            mean_abs_trade_return: 0.01,
            direction_change_count: 1,
            chop_score: 0.2,
            estimated_luld_active: true,
            estimated_luld_reference_price: 10.0,
            estimated_luld_lower_price: 9.0,
            estimated_luld_upper_price: 11.0,
            estimated_luld_parameter_pct: 10.0,
            estimated_luld_distance_to_upper_pct: 5.26,
            estimated_luld_distance_to_lower_pct: 13.88,
            estimated_luld_state: "inside".to_string(),
            qmd_structure: Default::default(),
            qmd_structure_events: Vec::new(),
        }
    }

    fn signal_template() -> MarketSignalEvent {
        let mut engine = MarketSignalEngine::default();
        let mut bar = base_bar();
        bar.timeframe = "1s".to_string();
        bar.return_1_bar = 0.01;
        bar.price_change_pct = 0.01;
        bar.volume_rate = 100.0;
        bar.dollar_volume_rate = 1_000.0;
        bar.trade_rate = 10.0;
        for _ in 0..12 {
            assert!(engine.update(&bar).is_empty());
            bar.bar_start = bar.bar_end;
            bar.bar_end += chrono::Duration::seconds(1);
        }
        bar.return_1_bar = 1.0;
        bar.price_change_pct = 1.0;
        bar.volume_rate = 5_000.0;
        bar.dollar_volume_rate = 50_000.0;
        bar.trade_rate = 100.0;
        engine
            .update(&bar)
            .into_iter()
            .find(|row| row.signal_key == "price_volume_expansion")
            .expect("expanded bar should emit")
    }

    #[test]
    fn emits_only_rankable_qmd_market_observations() {
        let signal = signal_template();
        assert_eq!(signal.signal_key, "price_volume_expansion");
        assert_eq!(
            signal.schema_version,
            crate::market_signal::MARKET_SIGNAL_SCHEMA_VERSION
        );
        assert!(signal.rank_score > 0.0);
    }

    #[tokio::test]
    async fn active_signals_rank_by_authority_rank_score() {
        let template = signal_template();
        let mut lower_rank = template.clone();
        lower_rank.ticker = "LOW".to_string();
        lower_rank.signal_key = "lower_rank".to_string();
        lower_rank.score = 0.95;
        lower_rank.rank_score = 0.25;
        lower_rank.confidence = 0.99;
        let mut higher_rank = template;
        higher_rank.ticker = "HIGH".to_string();
        higher_rank.signal_key = "higher_rank".to_string();
        higher_rank.score = -0.20;
        higher_rank.rank_score = 0.80;
        higher_rank.confidence = 0.40;

        let store = SharedScannerStore::new(10);
        store.apply(lower_rank).await;
        store.apply(higher_rank).await;
        let snapshot = store.signal_snapshot(10).await;

        assert_eq!(snapshot.rows[0].ticker, "HIGH");
        assert_eq!(snapshot.rows[0].rank_score, 0.80);
    }

    #[tokio::test]
    async fn signal_snapshot_and_deltas_share_one_sequence() {
        let store = SharedScannerStore::new(10);
        let first = store.apply(signal_template()).await;
        let second = store.apply(signal_template()).await;
        let snapshot = store.signal_snapshot(10).await;

        assert_eq!(first.sequence, 1);
        assert_eq!(second.sequence, 2);
        assert_eq!(snapshot.last_sequence, 2);
    }
}
