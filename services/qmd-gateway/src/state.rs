use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use chrono::{DateTime, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
use tokio::sync::RwLock;

const SCANNER_STALE_AFTER_MS: u64 = 60_000;
const LIQUIDITY_RANK_CACHE_MS: i64 = 1_000;
const LIQUIDITY_MIN_SESSION_DOLLAR_VOLUME: f64 = 500_000.0;
const LIQUIDITY_MIN_TRADE_RATE_10S: f64 = 1.0;
const LIQUIDITY_MAX_SPREAD_BPS: f64 = 50.0;

#[derive(Clone)]
pub struct SharedMarketState {
    inner: Arc<RwLock<MarketState>>,
    liquidity: Arc<RwLock<LiquidityRankCache>>,
}

#[derive(Default)]
struct LiquidityRankCache {
    as_of: Option<DateTime<Utc>>,
    by_ticker: HashMap<String, LiquidityRankValue>,
}

#[derive(Clone, Copy, Debug, Default, Serialize)]
pub struct LiquidityRankValue {
    pub liquidity_rank: u32,
    pub liquidity_score: f64,
}

#[derive(Default)]
struct MarketState {
    events_received: u64,
    quotes_received: u64,
    scanner_sequence: u64,
    trades_received: u64,
    symbols: HashMap<String, SymbolState>,
}

#[derive(Clone, Debug)]
struct SymbolState {
    day_dollar_volume: f64,
    day_trade_count: u64,
    day_volume: f64,
    last_event_ts: Option<DateTime<Utc>>,
    last_price: f64,
    last_quote: Option<QuoteEvent>,
    last_trade: Option<TradeEvent>,
    latest_trade_second: Option<i64>,
    recent_trade_counts: BTreeMap<i64, u64>,
    session_date: Option<NaiveDate>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StatusMetrics {
    pub events_received: u64,
    pub quotes_received: u64,
    pub symbols_seen: usize,
    pub trades_received: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct SymbolSnapshot {
    pub ask: f64,
    pub ask_size: u32,
    pub bid: f64,
    pub bid_size: u32,
    pub day_dollar_volume: f64,
    pub day_trade_count: u64,
    pub day_volume: f64,
    pub degradation_reason: Option<String>,
    pub event_age_ms: Option<u64>,
    pub last_event_ts: Option<DateTime<Utc>>,
    pub last_price: f64,
    pub liquidity_rank: u32,
    pub liquidity_score: f64,
    pub liquidity_eligible: bool,
    pub liquidity_eligibility_reasons: Vec<String>,
    pub quality_flags: Vec<String>,
    pub quality_state: String,
    pub spread: f64,
    pub ticker: String,
    pub trade_rate_10s: f64,
    pub trade_rate_60s: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct TickerStateSnapshot {
    pub age_ms: Option<u64>,
    pub as_of: DateTime<Utc>,
    pub authority: &'static str,
    pub found: bool,
    pub row: Option<SymbolSnapshot>,
    pub schema_version: u16,
    pub sequence: u64,
    pub state: &'static str,
    pub ticker: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ScannerSnapshot {
    pub as_of: DateTime<Utc>,
    pub row_count: usize,
    pub rows: Vec<SymbolSnapshot>,
    pub sequence: u64,
    pub total_symbols: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct ScannerRowDelta {
    pub as_of: DateTime<Utc>,
    pub row: SymbolSnapshot,
    pub sequence: u64,
}

impl SharedMarketState {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(MarketState::default())),
            liquidity: Arc::new(RwLock::new(LiquidityRankCache::default())),
        }
    }

    pub async fn apply_event(&self, event: &MarketEvent) -> ScannerRowDelta {
        let mut state = self.inner.write().await;
        state.events_received += 1;
        state.scanner_sequence = state.scanner_sequence.saturating_add(1);
        let sequence = state.scanner_sequence;
        let ticker = event.ticker().to_ascii_uppercase();
        match event {
            MarketEvent::Trade(trade) => {
                state.trades_received += 1;
                let symbol = state
                    .symbols
                    .entry(ticker.clone())
                    .or_insert_with(SymbolState::new);
                symbol.apply_trade(trade.clone());
            }
            MarketEvent::Quote(quote) => {
                state.quotes_received += 1;
                let symbol = state
                    .symbols
                    .entry(ticker.clone())
                    .or_insert_with(SymbolState::new);
                symbol.apply_quote(quote.clone());
            }
        }
        let as_of = state
            .symbols
            .get(&ticker)
            .and_then(|symbol| symbol.last_event_ts)
            .unwrap_or_else(Utc::now);
        let mut row = state
            .symbols
            .get(&ticker)
            .expect("the applied market event creates its symbol state")
            .snapshot(&ticker, as_of);
        drop(state);
        if let Some(liquidity) = self.liquidity.read().await.by_ticker.get(&ticker).copied() {
            row.liquidity_rank = liquidity.liquidity_rank;
            row.liquidity_score = liquidity.liquidity_score;
        }
        ScannerRowDelta {
            as_of,
            row,
            sequence,
        }
    }

    pub async fn metrics(&self) -> StatusMetrics {
        let state = self.inner.read().await;
        StatusMetrics {
            events_received: state.events_received,
            quotes_received: state.quotes_received,
            symbols_seen: state.symbols.len(),
            trades_received: state.trades_received,
        }
    }

    pub async fn scanner_snapshot(&self, limit: usize) -> ScannerSnapshot {
        self.scanner_snapshot_at(Utc::now(), limit).await
    }

    pub async fn scanner_snapshot_at(&self, as_of: DateTime<Utc>, limit: usize) -> ScannerSnapshot {
        let state = self.inner.read().await;
        let mut rows: Vec<_> = state
            .symbols
            .iter()
            .map(|(ticker, symbol)| symbol.snapshot(ticker, as_of))
            .collect();
        let sequence = state.scanner_sequence;
        drop(state);
        apply_liquidity_ranking(&mut rows);
        rows.sort_by(|left, right| {
            left.liquidity_rank
                .cmp(&right.liquidity_rank)
                .then_with(|| left.ticker.cmp(&right.ticker))
        });
        let total_symbols = rows.len();
        {
            let mut cache = self.liquidity.write().await;
            cache.as_of = Some(as_of);
            cache.by_ticker = rows
                .iter()
                .map(|row| {
                    (
                        row.ticker.clone(),
                        LiquidityRankValue {
                            liquidity_rank: row.liquidity_rank,
                            liquidity_score: row.liquidity_score,
                        },
                    )
                })
                .collect();
        }
        rows.truncate(limit);
        ScannerSnapshot {
            as_of,
            row_count: rows.len(),
            rows,
            sequence,
            total_symbols,
        }
    }

    pub async fn liquidity_rank_at(
        &self,
        ticker: &str,
        as_of: DateTime<Utc>,
    ) -> LiquidityRankValue {
        let normalized = ticker.trim().to_ascii_uppercase();
        let fresh = {
            let cache = self.liquidity.read().await;
            cache.as_of.is_some_and(|cached_at| {
                as_of
                    .signed_duration_since(cached_at)
                    .num_milliseconds()
                    .abs()
                    <= LIQUIDITY_RANK_CACHE_MS
            })
        };
        if !fresh {
            let _ = self.scanner_snapshot_at(as_of, usize::MAX).await;
        }
        self.liquidity
            .read()
            .await
            .by_ticker
            .get(&normalized)
            .copied()
            .unwrap_or_default()
    }

    pub async fn ticker_snapshot(&self, ticker: &str) -> Option<SymbolSnapshot> {
        self.ticker_snapshot_at(ticker, Utc::now()).await
    }

    pub async fn ticker_snapshot_at(
        &self,
        ticker: &str,
        as_of: DateTime<Utc>,
    ) -> Option<SymbolSnapshot> {
        let state = self.inner.read().await;
        let normalized = ticker.to_ascii_uppercase();
        let mut row = state
            .symbols
            .get(&normalized)
            .map(|symbol| symbol.snapshot(&normalized, as_of));
        drop(state);
        if let Some(row) = row.as_mut() {
            let liquidity = self.liquidity_rank_at(&normalized, as_of).await;
            row.liquidity_rank = liquidity.liquidity_rank;
            row.liquidity_score = liquidity.liquidity_score;
        }
        row
    }

    pub async fn ticker_state_snapshot(&self, ticker: &str) -> TickerStateSnapshot {
        let state = self.inner.read().await;
        let normalized = ticker.trim().to_ascii_uppercase();
        let as_of = Utc::now();
        let mut row = state
            .symbols
            .get(&normalized)
            .map(|symbol| symbol.snapshot(&normalized, as_of));
        let sequence = state.scanner_sequence;
        drop(state);
        if let Some(row) = row.as_mut() {
            let liquidity = self.liquidity_rank_at(&normalized, as_of).await;
            row.liquidity_rank = liquidity.liquidity_rank;
            row.liquidity_score = liquidity.liquidity_score;
        }
        let age_ms = row.as_ref().and_then(|snapshot| {
            snapshot.last_event_ts.map(|last_event| {
                as_of
                    .signed_duration_since(last_event)
                    .num_milliseconds()
                    .max(0) as u64
            })
        });
        TickerStateSnapshot {
            age_ms,
            as_of,
            authority: "qmd_gateway_live_memory",
            found: row.is_some(),
            state: if row.is_some() { "ready" } else { "missing" },
            row,
            schema_version: 1,
            sequence,
            ticker: normalized,
        }
    }
}

impl SymbolState {
    fn new() -> Self {
        Self {
            day_dollar_volume: 0.0,
            day_trade_count: 0,
            day_volume: 0.0,
            last_event_ts: None,
            last_price: 0.0,
            last_quote: None,
            last_trade: None,
            latest_trade_second: None,
            recent_trade_counts: BTreeMap::new(),
            session_date: None,
        }
    }

    fn apply_trade(&mut self, trade: TradeEvent) {
        if !self.accept_session(trade.ts) {
            return;
        }
        let advances_current_trade = self
            .last_trade
            .as_ref()
            .map(|last_trade| trade.ts >= last_trade.ts)
            .unwrap_or(true);
        if advances_current_trade {
            self.last_price = trade.price;
            self.last_trade = Some(trade.clone());
        }
        self.last_event_ts = Some(
            self.last_event_ts
                .map(|last_event_ts| last_event_ts.max(trade.ts))
                .unwrap_or(trade.ts),
        );
        self.day_volume += trade.size.max(0.0);
        self.day_dollar_volume += trade.size.max(0.0) * trade.price.max(0.0);
        self.day_trade_count += 1;
        let second = trade.ts.timestamp();
        let watermark = self
            .latest_trade_second
            .map(|current| current.max(second))
            .unwrap_or(second);
        self.latest_trade_second = Some(watermark);
        let cutoff = watermark - 60;
        if second >= cutoff {
            *self.recent_trade_counts.entry(second).or_insert(0) += 1;
        }
        self.recent_trade_counts
            .retain(|timestamp, _| *timestamp >= cutoff);
    }

    fn apply_quote(&mut self, quote: QuoteEvent) {
        if !self.accept_session(quote.ts) {
            return;
        }
        if self
            .last_quote
            .as_ref()
            .map(|last_quote| quote.ts >= last_quote.ts)
            .unwrap_or(true)
        {
            self.last_quote = Some(quote.clone());
        }
        self.last_event_ts = Some(
            self.last_event_ts
                .map(|last_event_ts| last_event_ts.max(quote.ts))
                .unwrap_or(quote.ts),
        );
    }

    fn accept_session(&mut self, ts: DateTime<Utc>) -> bool {
        let local = ts.with_timezone(&New_York);
        let mut session_date = local.date_naive();
        if local.time() < chrono::NaiveTime::from_hms_opt(4, 0, 0).unwrap() {
            session_date = session_date.pred_opt().unwrap_or(session_date);
        }
        match self.session_date {
            Some(current) if session_date < current => return false,
            Some(current) if session_date == current => return true,
            _ => {}
        }
        self.session_date = Some(session_date);
        self.day_dollar_volume = 0.0;
        self.day_trade_count = 0;
        self.day_volume = 0.0;
        self.latest_trade_second = None;
        self.recent_trade_counts.clear();
        true
    }

    fn snapshot(&self, ticker: &str, as_of: DateTime<Utc>) -> SymbolSnapshot {
        let (bid, bid_size, ask, ask_size) = self
            .last_quote
            .as_ref()
            .map(|quote| {
                (
                    quote.bid_price,
                    quote.bid_size,
                    quote.ask_price,
                    quote.ask_size,
                )
            })
            .unwrap_or((0.0, 0, 0.0, 0));
        let event_age_ms = self.last_event_ts.map(|last_event| {
            as_of
                .signed_duration_since(last_event)
                .num_milliseconds()
                .max(0) as u64
        });
        let (quality_state, quality_flags, degradation_reason) = if self.last_event_ts.is_none() {
            (
                "unavailable",
                vec!["missing_market_event"],
                Some("No accepted market event is available."),
            )
        } else if bid > 0.0 && ask > 0.0 && bid > ask {
            (
                "crossed",
                vec!["crossed_nbbo"],
                Some("Best bid exceeds best ask."),
            )
        } else if bid > 0.0 && ask > 0.0 && bid == ask {
            (
                "locked",
                vec!["locked_nbbo"],
                Some("Best bid equals best ask."),
            )
        } else if event_age_ms.unwrap_or_default() > SCANNER_STALE_AFTER_MS {
            (
                "stale",
                vec!["stale_market_event"],
                Some("Latest accepted market event exceeds the QMD freshness threshold."),
            )
        } else {
            ("ready", Vec::new(), None)
        };
        SymbolSnapshot {
            ask,
            ask_size,
            bid,
            bid_size,
            day_dollar_volume: self.day_dollar_volume,
            day_trade_count: self.day_trade_count,
            day_volume: self.day_volume,
            degradation_reason: degradation_reason.map(str::to_string),
            event_age_ms,
            last_event_ts: self.last_event_ts,
            last_price: self.last_price,
            liquidity_rank: 0,
            liquidity_score: 0.0,
            liquidity_eligible: false,
            liquidity_eligibility_reasons: Vec::new(),
            quality_flags: quality_flags.into_iter().map(str::to_string).collect(),
            quality_state: quality_state.to_string(),
            spread: if bid > 0.0 && ask > 0.0 {
                (ask - bid).max(0.0)
            } else {
                0.0
            },
            ticker: ticker.to_string(),
            trade_rate_10s: self.trade_rate(10, as_of),
            trade_rate_60s: self.trade_rate(60, as_of),
        }
    }

    fn trade_rate(&self, seconds: i64, as_of: DateTime<Utc>) -> f64 {
        if self.last_trade.is_none() {
            return 0.0;
        }
        let cutoff = as_of.timestamp() - seconds;
        let count: u64 = self
            .recent_trade_counts
            .iter()
            .filter(|(timestamp, _)| **timestamp >= cutoff)
            .map(|(_, count)| *count)
            .sum();
        count as f64 / seconds.max(1) as f64
    }
}

fn apply_liquidity_ranking(rows: &mut [SymbolSnapshot]) {
    if rows.is_empty() {
        return;
    }
    // Zero-activity rows cannot form the percentile reference population. Including
    // thousands of inactive listings made a few thin prints appear highly liquid.
    let executed = rows.iter().filter(|row| {
        row.day_trade_count > 0 && row.day_dollar_volume > 0.0 && row.day_volume > 0.0
    });
    let dollar_volume = sorted_finite(executed.clone().map(|row| row.day_dollar_volume));
    let trade_rate = sorted_finite(executed.clone().map(|row| row.trade_rate_10s.max(0.0)));
    let quoted_depth = sorted_finite(
        executed.clone()
            .map(|row| f64::from(row.bid_size) + f64::from(row.ask_size)),
    );
    let valid_spreads = sorted_finite(executed.filter_map(|row| {
        (row.last_price > 0.0 && row.bid > 0.0 && row.ask > row.bid)
            .then_some((row.ask - row.bid) / row.last_price * 10_000.0)
    }));

    for row in rows.iter_mut() {
        let has_executed_liquidity =
            row.day_trade_count > 0 && row.day_dollar_volume > 0.0 && row.day_volume > 0.0;
        if !has_executed_liquidity {
            row.liquidity_score = 0.0;
            row.liquidity_eligible = false;
            row.liquidity_eligibility_reasons = vec!["no_executed_session_liquidity".to_string()];
            continue;
        }
        let spread_bps = (row.last_price > 0.0 && row.bid > 0.0 && row.ask > row.bid)
            .then_some((row.ask - row.bid) / row.last_price * 10_000.0);
        let spread_quality = if let Some(spread_bps) = spread_bps {
            descending_percentile(
                spread_bps,
                &valid_spreads,
            )
        } else {
            0.0
        };
        let relative_score = (
            0.45 * ascending_percentile(row.day_dollar_volume.max(0.0), &dollar_volume)
                + 0.30 * ascending_percentile(row.trade_rate_10s.max(0.0), &trade_rate)
                + 0.15 * spread_quality
                + 0.10
                    * ascending_percentile(
                        f64::from(row.bid_size) + f64::from(row.ask_size),
                        &quoted_depth,
                    )
        )
        .clamp(0.0, 100.0);
        let mut reasons = Vec::new();
        if row.quality_state != "ready" {
            reasons.push(format!("market_state_{}", row.quality_state));
        }
        if row.day_dollar_volume < LIQUIDITY_MIN_SESSION_DOLLAR_VOLUME {
            reasons.push("session_dollar_volume_below_500000".to_string());
        }
        if row.trade_rate_10s < LIQUIDITY_MIN_TRADE_RATE_10S {
            reasons.push("trade_rate_10s_below_1".to_string());
        }
        if spread_bps.is_none() {
            reasons.push("executable_nbbo_unavailable".to_string());
        } else if spread_bps.is_some_and(|value| value > LIQUIDITY_MAX_SPREAD_BPS) {
            reasons.push("spread_above_50_bps".to_string());
        }
        row.liquidity_eligible = reasons.is_empty();
        row.liquidity_eligibility_reasons = reasons;
        // Score 50 is an executable-liquidity boundary, not merely a percentile.
        // Eligible rows occupy 50..100; ineligible rows remain strictly below 50.
        row.liquidity_score = round2(if row.liquidity_eligible {
            50.0 + relative_score * 0.5
        } else {
            relative_score * 0.4999
        })
        .clamp(0.0, 100.0);
    }
    rows.sort_by(|left, right| {
        right
            .liquidity_score
            .partial_cmp(&left.liquidity_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| {
                right
                    .day_dollar_volume
                    .partial_cmp(&left.day_dollar_volume)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| {
                right
                    .trade_rate_10s
                    .partial_cmp(&left.trade_rate_10s)
                    .unwrap_or(std::cmp::Ordering::Equal)
            })
            .then_with(|| left.ticker.cmp(&right.ticker))
    });
    for (index, row) in rows.iter_mut().enumerate() {
        row.liquidity_rank = index as u32 + 1;
    }
}

fn sorted_finite(values: impl Iterator<Item = f64>) -> Vec<f64> {
    let mut values = values.filter(|value| value.is_finite()).collect::<Vec<_>>();
    values.sort_by(|left, right| left.partial_cmp(right).unwrap_or(std::cmp::Ordering::Equal));
    values
}

fn ascending_percentile(value: f64, sorted: &[f64]) -> f64 {
    if sorted.is_empty() || !value.is_finite() || value <= 0.0 {
        return 0.0;
    }
    let upper = sorted.partition_point(|candidate| *candidate <= value);
    upper as f64 / sorted.len() as f64 * 100.0
}

fn descending_percentile(value: f64, sorted: &[f64]) -> f64 {
    if sorted.is_empty() || !value.is_finite() {
        return 0.0;
    }
    let lower = sorted.partition_point(|candidate| *candidate < value);
    (sorted.len() - lower) as f64 / sorted.len() as f64 * 100.0
}

fn round2(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::{apply_liquidity_ranking, SharedMarketState, SymbolSnapshot, SymbolState};
    use crate::event::{MarketEvent, TradeEvent};
    use chrono::{DateTime, Utc};
    use serde_json::Value;

    fn trade(ts: &str, price: f64, size: f64) -> TradeEvent {
        let timestamp = ts.parse::<DateTime<Utc>>().unwrap();
        TradeEvent {
            conditions: Vec::new(),
            exchange: 0,
            ingest_ts: timestamp,
            participant_ts: None,
            price,
            raw: Value::Null,
            sequence: 0,
            size,
            tape: 0,
            ticker: "TEST".to_string(),
            trade_id: String::new(),
            trf_id: 0,
            trf_ts: None,
            ts: timestamp,
        }
    }

    fn liquidity_row(
        ticker: &str,
        dollar_volume: f64,
        trade_rate: f64,
        spread: f64,
        depth: u32,
    ) -> SymbolSnapshot {
        let executed = dollar_volume > 0.0;
        SymbolSnapshot {
            ask: 10.0 + spread,
            ask_size: depth,
            bid: 10.0,
            bid_size: depth,
            day_dollar_volume: dollar_volume,
            day_trade_count: u64::from(executed),
            day_volume: if executed {
                (dollar_volume / 10.0).max(1.0)
            } else {
                0.0
            },
            degradation_reason: None,
            event_age_ms: Some(0),
            last_event_ts: None,
            last_price: 10.0,
            liquidity_rank: 0,
            liquidity_score: 0.0,
            liquidity_eligible: false,
            liquidity_eligibility_reasons: Vec::new(),
            quality_flags: Vec::new(),
            quality_state: "ready".to_string(),
            spread,
            ticker: ticker.to_string(),
            trade_rate_10s: trade_rate,
            trade_rate_60s: trade_rate,
        }
    }

    #[test]
    fn liquidity_score_is_bounded_and_rank_one_is_best_market_row() {
        let mut rows = vec![
            liquidity_row("NONE", 0.0, 0.0, 0.0, 0),
            liquidity_row("LOW", 1_000.0, 1.0, 0.20, 10),
            liquidity_row("HIGH", 1_000_000.0, 100.0, 0.01, 1_000),
            liquidity_row("MID", 50_000.0, 10.0, 0.05, 100),
        ];
        apply_liquidity_ranking(&mut rows);

        assert_eq!(rows[0].ticker, "HIGH");
        assert_eq!(rows[0].liquidity_rank, 1);
        assert_eq!(rows[1].liquidity_rank, 2);
        assert_eq!(rows[2].liquidity_rank, 3);
        assert_eq!(rows[3].liquidity_rank, 4);
        assert_eq!(rows[3].liquidity_score, 0.0);
        assert!(rows
            .iter()
            .all(|row| (0.0..=100.0).contains(&row.liquidity_score)));
        assert!(rows[0].liquidity_score > rows[1].liquidity_score);
        assert!(rows[1].liquidity_score > rows[2].liquidity_score);
        assert!(rows[2].liquidity_score > rows[3].liquidity_score);
    }

    #[test]
    fn quote_only_rows_cannot_outrank_executed_liquidity() {
        let mut quote_only = liquidity_row("QUOTE", 0.0, 100.0, 0.001, 50_000);
        quote_only.day_trade_count = 0;
        quote_only.day_volume = 0.0;
        let mut traded = liquidity_row("TRADE", 100.0, 0.1, 0.20, 1);
        traded.day_trade_count = 1;
        traded.day_volume = 10.0;
        let mut rows = vec![quote_only, traded];

        apply_liquidity_ranking(&mut rows);

        assert_eq!(rows[0].ticker, "TRADE");
        assert_eq!(rows[0].liquidity_rank, 1);
        assert_eq!(rows[1].ticker, "QUOTE");
        assert_eq!(rows[1].liquidity_score, 0.0);
    }

    #[test]
    fn thin_relative_leader_cannot_cross_executable_liquidity_boundary() {
        let mut rows = vec![
            liquidity_row("THIN", 25_000.0, 0.1, 0.01, 10_000),
            liquidity_row("ELIGIBLE", 1_000_000.0, 2.0, 0.02, 1_000),
            liquidity_row("NONE", 0.0, 0.0, 0.0, 0),
        ];

        apply_liquidity_ranking(&mut rows);

        let thin = rows.iter().find(|row| row.ticker == "THIN").unwrap();
        let eligible = rows.iter().find(|row| row.ticker == "ELIGIBLE").unwrap();
        assert!(!thin.liquidity_eligible);
        assert!(thin.liquidity_score < 50.0);
        assert!(thin
            .liquidity_eligibility_reasons
            .contains(&"session_dollar_volume_below_500000".to_string()));
        assert!(eligible.liquidity_eligible);
        assert!(eligible.liquidity_score >= 50.0);
        assert_eq!(eligible.liquidity_rank, 1);
    }

    #[test]
    fn resets_counters_at_four_am_new_york_and_rejects_late_old_session_events() {
        let mut state = SymbolState::new();
        state.apply_trade(trade("2026-07-14T07:59:59Z", 10.0, 5.0));
        assert_eq!(state.day_trade_count, 1);
        assert_eq!(state.day_volume, 5.0);

        state.apply_trade(trade("2026-07-14T08:00:00Z", 11.0, 7.0));
        assert_eq!(state.day_trade_count, 1);
        assert_eq!(state.day_volume, 7.0);
        assert_eq!(state.last_price, 11.0);

        state.apply_trade(trade("2026-07-14T07:59:58Z", 9.0, 100.0));
        assert_eq!(state.day_trade_count, 1);
        assert_eq!(state.day_volume, 7.0);
        assert_eq!(state.last_price, 11.0);
    }

    #[test]
    fn trade_rates_use_bounded_per_second_counts_and_decay() {
        let mut state = SymbolState::new();
        for offset in 0..120 {
            let ts = format!("2026-07-14T14:{:02}:{:02}Z", offset / 60, offset % 60);
            state.apply_trade(trade(&ts, 10.0, 1.0));
        }
        state.apply_trade(trade("2026-07-14T14:00:00Z", 9.0, 1.0));
        assert!(state.recent_trade_counts.len() <= 61);
        let active = "2026-07-14T14:01:59Z".parse::<DateTime<Utc>>().unwrap();
        assert!(state.trade_rate(60, active) > 0.0);
        let stale = "2026-07-14T14:03:00Z".parse::<DateTime<Utc>>().unwrap();
        assert_eq!(state.trade_rate(60, stale), 0.0);
    }

    #[test]
    fn scanner_snapshot_publishes_qmd_freshness_state() {
        let mut state = SymbolState::new();
        state.apply_trade(trade("2026-07-14T14:00:00Z", 10.0, 1.0));
        let ready = state.snapshot(
            "TEST",
            "2026-07-14T14:00:30Z".parse::<DateTime<Utc>>().unwrap(),
        );
        assert_eq!(ready.quality_state, "ready");
        assert_eq!(ready.event_age_ms, Some(30_000));

        let stale = state.snapshot(
            "TEST",
            "2026-07-14T14:01:01Z".parse::<DateTime<Utc>>().unwrap(),
        );
        assert_eq!(stale.quality_state, "stale");
        assert_eq!(stale.quality_flags, vec!["stale_market_event"]);
        assert!(stale.degradation_reason.is_some());
    }

    #[test]
    fn late_same_session_repairs_do_not_regress_current_trade_state() {
        let mut state = SymbolState::new();
        state.apply_trade(trade("2026-07-14T14:00:10Z", 11.0, 7.0));
        state.apply_trade(trade("2026-07-14T14:00:00Z", 9.0, 5.0));

        assert_eq!(state.last_price, 11.0);
        assert_eq!(
            state.last_event_ts.unwrap().to_rfc3339(),
            "2026-07-14T14:00:10+00:00"
        );
        assert_eq!(state.day_trade_count, 2);
        assert_eq!(state.day_volume, 12.0);
    }

    #[tokio::test]
    async fn scanner_snapshot_and_row_deltas_share_one_sequence() {
        let state = SharedMarketState::new();
        let first = state
            .apply_event(&MarketEvent::Trade(trade(
                "2026-07-14T14:00:00Z",
                10.0,
                5.0,
            )))
            .await;
        let second = state
            .apply_event(&MarketEvent::Trade(trade(
                "2026-07-14T14:00:01Z",
                11.0,
                7.0,
            )))
            .await;
        let snapshot = state.scanner_snapshot(10).await;

        assert_eq!(first.sequence, 1);
        assert_eq!(second.sequence, 2);
        assert_eq!(snapshot.sequence, 2);
        assert_eq!(snapshot.rows[0].last_price, 11.0);
    }

    #[tokio::test]
    async fn ticker_state_snapshot_is_versioned_and_sequence_aligned() {
        let state = SharedMarketState::new();
        let missing = state.ticker_state_snapshot("missing").await;
        assert!(!missing.found);
        assert_eq!(missing.state, "missing");
        assert_eq!(missing.schema_version, 1);
        assert_eq!(missing.sequence, 0);

        let delta = state
            .apply_event(&MarketEvent::Trade(trade(
                "2026-07-14T14:00:00Z",
                10.0,
                5.0,
            )))
            .await;
        let snapshot = state.ticker_state_snapshot("test").await;

        assert!(snapshot.found);
        assert_eq!(snapshot.authority, "qmd_gateway_live_memory");
        assert_eq!(snapshot.sequence, delta.sequence);
        assert_eq!(snapshot.row.unwrap().last_price, 10.0);
        assert!(snapshot.age_ms.is_some());
    }
}
