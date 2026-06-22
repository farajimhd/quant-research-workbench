use crate::bars::BarRow;
use crate::metrics::SharedMetrics;
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};

pub const SCANNER_PRIMITIVE_SCHEMA_VERSION: u16 = 1;

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
}

#[derive(Clone)]
pub struct SharedScannerStore {
    inner: Arc<RwLock<ScannerStore>>,
}

struct ScannerStore {
    latest_by_key: HashMap<String, ScannerPrimitive>,
    history: VecDeque<ScannerPrimitive>,
    history_limit: usize,
}

#[derive(Clone)]
pub struct ScannerPrimitiveRouter {
    sender: mpsc::Sender<BarRow>,
}

impl ScannerPrimitiveRouter {
    pub async fn send_bar(&self, row: BarRow) -> Result<(), mpsc::error::SendError<BarRow>> {
        self.sender.send(row).await
    }
}

impl SharedScannerStore {
    pub fn new(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(ScannerStore {
                latest_by_key: HashMap::new(),
                history: VecDeque::with_capacity(history_limit.min(10_000)),
                history_limit,
            })),
        }
    }

    pub async fn apply(&self, primitive: ScannerPrimitive) {
        let mut store = self.inner.write().await;
        let key = format!(
            "{}:{}:{}",
            primitive.ticker, primitive.timeframe, primitive.primitive_key
        );
        store.latest_by_key.insert(key, primitive.clone());
        store.history.push_back(primitive);
        while store.history.len() > store.history_limit {
            store.history.pop_front();
        }
    }

    pub async fn snapshot(&self, limit: usize) -> ScannerPrimitiveSnapshot {
        let store = self.inner.read().await;
        let mut rows = store.latest_by_key.values().cloned().collect::<Vec<_>>();
        rows.sort_by(|left, right| {
            right
                .score
                .partial_cmp(&left.score)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        rows.truncate(limit);
        ScannerPrimitiveSnapshot {
            as_of: Utc::now(),
            row_count: rows.len(),
            rows,
        }
    }
}

pub fn spawn_scanner_primitive_engine(
    store: SharedScannerStore,
    channel_capacity: usize,
    metrics: SharedMetrics,
    primitive_sender: broadcast::Sender<ScannerPrimitive>,
) -> ScannerPrimitiveRouter {
    let (sender, receiver) = mpsc::channel::<BarRow>(channel_capacity.max(1));
    tokio::spawn(run_scanner_primitive_engine(
        store,
        receiver,
        metrics,
        primitive_sender,
    ));
    ScannerPrimitiveRouter { sender }
}

async fn run_scanner_primitive_engine(
    store: SharedScannerStore,
    mut receiver: mpsc::Receiver<BarRow>,
    metrics: SharedMetrics,
    primitive_sender: broadcast::Sender<ScannerPrimitive>,
) {
    while let Some(row) = receiver.recv().await {
        let primitives = evaluate_bar(&row);
        if primitives.is_empty() {
            continue;
        }
        metrics.inc_scanner_candidates(primitives.len() as u64);
        for primitive in primitives {
            store.apply(primitive.clone()).await;
            let _ = primitive_sender.send(primitive);
        }
    }
}

fn evaluate_bar(row: &BarRow) -> Vec<ScannerPrimitive> {
    if !row.is_closed || row.close <= 0.0 {
        return Vec::new();
    }
    let mut primitives = Vec::new();
    maybe_push(
        &mut primitives,
        row,
        "tape_acceleration",
        row.trade_count_accel > 10.0 && row.tape_imbalance > 0.15 && row.spread_bps_close < 80.0,
        weighted_score(&[
            row.trade_count_accel / 50.0,
            row.tape_imbalance.max(0.0),
            row.price_change_pct.max(0.0) / 5.0,
            row.liquidity_score.log10().max(0.0) / 8.0,
        ]),
        "trade count acceleration with positive tape imbalance",
    );
    maybe_push(
        &mut primitives,
        row,
        "volume_shock",
        row.dollar_volume_accel > 250_000.0 && row.price_change_pct > 0.25,
        weighted_score(&[
            row.dollar_volume_accel / 2_000_000.0,
            row.price_change_pct / 5.0,
            row.trade_rate / 50.0,
        ]),
        "dollar volume expansion with positive price change",
    );
    maybe_push(
        &mut primitives,
        row,
        "liquidity_recovery",
        row.spread_bps_close > 0.0
            && row.spread_bps_close < row.spread_bps_mean
            && row.quote_rate_accel > 0.0
            && row.liquidity_score > 0.0,
        weighted_score(&[
            (row.spread_bps_mean - row.spread_bps_close).max(0.0) / 100.0,
            row.quote_rate_accel / 50.0,
            row.liquidity_score.log10().max(0.0) / 8.0,
        ]),
        "spread tightened while quote activity and liquidity recovered",
    );
    maybe_push(
        &mut primitives,
        row,
        "vwap_reclaim",
        row.vwap_distance_pct > 0.0 && row.mid_vwap_distance_pct > 0.0 && row.tape_imbalance > 0.0,
        weighted_score(&[
            row.vwap_distance_pct / 2.0,
            row.mid_vwap_distance_pct / 2.0,
            row.tape_imbalance.max(0.0),
        ]),
        "trade and quote midpoint reclaimed VWAP with favorable tape",
    );
    maybe_push(
        &mut primitives,
        row,
        "high_momentum_bar",
        row.price_change_pct > 1.0 && row.close >= row.high * 0.995 && row.trade_rate > 0.5,
        weighted_score(&[
            row.price_change_pct / 5.0,
            row.trade_rate / 20.0,
            row.tape_imbalance.max(0.0),
        ]),
        "strong close near bar high with trade activity",
    );
    primitives
}

fn maybe_push(
    primitives: &mut Vec<ScannerPrimitive>,
    row: &BarRow,
    key: &str,
    condition: bool,
    score: f64,
    reason: &str,
) {
    if !condition {
        return;
    }
    primitives.push(ScannerPrimitive {
        schema_version: SCANNER_PRIMITIVE_SCHEMA_VERSION,
        detected_at: Utc::now(),
        ticker: row.sym.clone(),
        timeframe: row.timeframe.clone(),
        primitive_key: key.to_string(),
        side_bias: "long".to_string(),
        score: score.clamp(0.0, 1.0),
        trigger_reason: reason.to_string(),
        reject_reason: String::new(),
        close: row.close,
        vwap: row.vwap,
        price_change_pct: row.price_change_pct,
        volume: row.volume,
        dollar_volume: row.dollar_volume,
        trade_rate: row.trade_rate,
        quote_rate: row.quote_rate,
        tape_imbalance: row.tape_imbalance,
        spread_bps: row.spread_bps_close,
        liquidity_score: row.liquidity_score,
    });
}

fn weighted_score(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values
        .iter()
        .copied()
        .map(|value| value.clamp(0.0, 1.0))
        .sum::<f64>()
        / values.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Utc;

    fn base_bar() -> BarRow {
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
        }
    }

    #[test]
    fn emits_massive_only_primitives_from_bar() {
        let primitives = evaluate_bar(&base_bar());
        assert!(primitives
            .iter()
            .any(|row| row.primitive_key == "tape_acceleration"));
        assert!(primitives
            .iter()
            .all(|row| row.schema_version == SCANNER_PRIMITIVE_SCHEMA_VERSION));
    }
}
