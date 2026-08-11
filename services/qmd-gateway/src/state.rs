use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use chrono::{DateTime, NaiveDate, Utc};
use chrono_tz::America::New_York;
use serde::Serialize;
use std::collections::{BTreeMap, HashMap};
use std::sync::Arc;
use tokio::sync::RwLock;

#[derive(Clone)]
pub struct SharedMarketState {
    inner: Arc<RwLock<MarketState>>,
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
    pub last_event_ts: Option<DateTime<Utc>>,
    pub last_price: f64,
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
        let row = state
            .symbols
            .get(&ticker)
            .expect("the applied market event creates its symbol state")
            .snapshot(&ticker, as_of);
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
        let state = self.inner.read().await;
        let as_of = Utc::now();
        let mut rows: Vec<_> = state
            .symbols
            .iter()
            .map(|(ticker, symbol)| symbol.snapshot(ticker, as_of))
            .collect();
        rows.sort_by(|left, right| {
            right
                .day_dollar_volume
                .partial_cmp(&left.day_dollar_volume)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let total_symbols = rows.len();
        rows.truncate(limit);
        ScannerSnapshot {
            as_of,
            row_count: rows.len(),
            rows,
            sequence: state.scanner_sequence,
            total_symbols,
        }
    }

    pub async fn ticker_snapshot(&self, ticker: &str) -> Option<SymbolSnapshot> {
        let state = self.inner.read().await;
        let normalized = ticker.to_ascii_uppercase();
        state
            .symbols
            .get(&normalized)
            .map(|symbol| symbol.snapshot(&normalized, Utc::now()))
    }

    pub async fn ticker_state_snapshot(&self, ticker: &str) -> TickerStateSnapshot {
        let state = self.inner.read().await;
        let normalized = ticker.trim().to_ascii_uppercase();
        let as_of = Utc::now();
        let row = state
            .symbols
            .get(&normalized)
            .map(|symbol| symbol.snapshot(&normalized, as_of));
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
            sequence: state.scanner_sequence,
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
        self.last_event_ts = Some(trade.ts);
        self.last_price = trade.price;
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
        self.last_trade = Some(trade);
    }

    fn apply_quote(&mut self, quote: QuoteEvent) {
        if !self.accept_session(quote.ts) {
            return;
        }
        self.last_event_ts = Some(quote.ts);
        self.last_quote = Some(quote);
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
        SymbolSnapshot {
            ask,
            ask_size,
            bid,
            bid_size,
            day_dollar_volume: self.day_dollar_volume,
            day_trade_count: self.day_trade_count,
            day_volume: self.day_volume,
            last_event_ts: self.last_event_ts,
            last_price: self.last_price,
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

#[cfg(test)]
mod tests {
    use super::{SharedMarketState, SymbolState};
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
