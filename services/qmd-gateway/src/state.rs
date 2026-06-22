use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use chrono::{DateTime, Utc};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
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
    recent_trades: VecDeque<TradeEvent>,
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
pub struct ScannerSnapshot {
    pub as_of: DateTime<Utc>,
    pub row_count: usize,
    pub rows: Vec<SymbolSnapshot>,
    pub total_symbols: usize,
}

impl SharedMarketState {
    pub fn new() -> Self {
        Self {
            inner: Arc::new(RwLock::new(MarketState::default())),
        }
    }

    pub async fn apply_event(&self, event: &MarketEvent) {
        let mut state = self.inner.write().await;
        state.events_received += 1;
        match event {
            MarketEvent::Trade(trade) => {
                state.trades_received += 1;
                let symbol = state
                    .symbols
                    .entry(trade.ticker.clone())
                    .or_insert_with(SymbolState::new);
                symbol.apply_trade(trade.clone());
            }
            MarketEvent::Quote(quote) => {
                state.quotes_received += 1;
                let symbol = state
                    .symbols
                    .entry(quote.ticker.clone())
                    .or_insert_with(SymbolState::new);
                symbol.apply_quote(quote.clone());
            }
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
        let mut rows: Vec<_> = state
            .symbols
            .iter()
            .map(|(ticker, symbol)| symbol.snapshot(ticker))
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
            as_of: Utc::now(),
            row_count: rows.len(),
            rows,
            total_symbols,
        }
    }

    pub async fn ticker_snapshot(&self, ticker: &str) -> Option<SymbolSnapshot> {
        let state = self.inner.read().await;
        let normalized = ticker.to_ascii_uppercase();
        state
            .symbols
            .get(&normalized)
            .map(|symbol| symbol.snapshot(&normalized))
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
            recent_trades: VecDeque::with_capacity(1_000),
        }
    }

    fn apply_trade(&mut self, trade: TradeEvent) {
        self.last_event_ts = Some(trade.ts);
        self.last_price = trade.price;
        self.day_volume += trade.size.max(0.0);
        self.day_dollar_volume += trade.size.max(0.0) * trade.price.max(0.0);
        self.day_trade_count += 1;
        self.recent_trades.push_back(trade.clone());
        while self.recent_trades.len() > 1_000 {
            self.recent_trades.pop_front();
        }
        self.last_trade = Some(trade);
    }

    fn apply_quote(&mut self, quote: QuoteEvent) {
        self.last_event_ts = Some(quote.ts);
        self.last_quote = Some(quote);
    }

    fn snapshot(&self, ticker: &str) -> SymbolSnapshot {
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
            trade_rate_10s: self.trade_rate(10),
            trade_rate_60s: self.trade_rate(60),
        }
    }

    fn trade_rate(&self, seconds: i64) -> f64 {
        let Some(latest) = self.last_trade.as_ref().map(|trade| trade.ts) else {
            return 0.0;
        };
        let cutoff = latest - chrono::Duration::seconds(seconds);
        let count = self
            .recent_trades
            .iter()
            .filter(|trade| trade.ts >= cutoff)
            .count();
        count as f64 / seconds.max(1) as f64
    }
}
