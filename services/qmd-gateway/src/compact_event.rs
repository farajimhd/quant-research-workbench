use crate::bars::{TradeAggregationRules, TradeUpdateRule};
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::intraday_bars::IntradayBarRouter;
use crate::market_products::MarketProductEventRouter;
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, Mutex, RwLock};
use tokio::time::{interval, sleep, Duration, Instant};

pub const LIVE_COMPACT_EVENT_SCHEMA_VERSION: u16 = 4;
pub const QUOTE_EVENT_TYPE: u8 = 0;
pub const TRADE_EVENT_TYPE: u8 = 1;
const CONDITION_TOKEN_SLOTS: usize = 5;
const MAX_PRECISE_PRICE: f64 = 429_496.7295;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct LiveCompactEvent {
    pub arrival_sequence: u64,
    pub condition_token_1: u8,
    pub condition_token_2: u8,
    pub condition_token_3: u8,
    pub condition_token_4: u8,
    pub condition_token_5: u8,
    pub event_date: String,
    pub event_meta: u8,
    pub exchange_primary: u8,
    pub exchange_secondary: u8,
    pub ingest_ts: DateTime<Utc>,
    pub issue_flags: u16,
    pub price_primary_int: u32,
    pub price_secondary_int: u32,
    pub schema_version: u16,
    pub sip_timestamp_us: u64,
    pub size_primary: f32,
    pub size_secondary: f32,
    pub source_sequence: u64,
    pub ticker: String,
}

impl LiveCompactEvent {
    pub fn event_type(&self) -> u8 {
        self.event_meta & 0x01
    }

    pub fn correlation_id(&self) -> String {
        let readable = format!("source:{}:{}", self.ticker, self.event_date);
        if readable.len() <= 128
            && readable
                .chars()
                .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '_' | ':' | '-'))
        {
            readable
        } else {
            format!(
                "source:{:016x}",
                causal_hash(&[self.ticker.as_bytes(), self.event_date.as_bytes()])
            )
        }
    }

    pub fn causation_id(&self) -> String {
        let sip = self.sip_timestamp_us.to_le_bytes();
        let source_sequence = self.source_sequence.to_le_bytes();
        let primary_price = self.price_primary_int.to_le_bytes();
        let secondary_price = self.price_secondary_int.to_le_bytes();
        let primary_size = self.size_primary.to_bits().to_le_bytes();
        let secondary_size = self.size_secondary.to_bits().to_le_bytes();
        let event_meta = [self.event_meta];
        let primary_exchange = [self.exchange_primary];
        let secondary_exchange = [self.exchange_secondary];
        format!(
            "event:{:016x}",
            causal_hash(&[
                self.ticker.as_bytes(),
                &sip,
                &source_sequence,
                &event_meta,
                &primary_price,
                &secondary_price,
                &primary_size,
                &secondary_size,
                &primary_exchange,
                &secondary_exchange,
            ])
        )
    }

    fn with_condition_tokens(mut self, tokens: [u8; CONDITION_TOKEN_SLOTS]) -> Self {
        self.condition_token_1 = tokens[0];
        self.condition_token_2 = tokens[1];
        self.condition_token_3 = tokens[2];
        self.condition_token_4 = tokens[3];
        self.condition_token_5 = tokens[4];
        self
    }
}

#[derive(Clone, Default)]
pub struct CompactEventDecoder {
    quote_conditions: HashMap<u8, u16>,
    quote_indicators: HashMap<u8, u16>,
    tapes: HashMap<u8, u8>,
    trade_conditions: HashMap<u8, u16>,
}

impl CompactEventDecoder {
    pub fn new(
        quote_conditions: impl IntoIterator<Item = (u8, u16)>,
        trade_conditions: impl IntoIterator<Item = (u8, u16)>,
        quote_indicators: impl IntoIterator<Item = (u8, u16)>,
        tapes: impl IntoIterator<Item = (u8, u8)>,
    ) -> Self {
        Self {
            quote_conditions: quote_conditions.into_iter().collect(),
            quote_indicators: quote_indicators.into_iter().collect(),
            tapes: tapes.into_iter().collect(),
            trade_conditions: trade_conditions.into_iter().collect(),
        }
    }

    pub fn decode(&self, event: &LiveCompactEvent) -> MarketEvent {
        let primary_scale = if event.event_meta & 0x02 != 0 {
            10_000.0
        } else {
            100.0
        };
        let secondary_scale = if event.event_meta & 0x04 != 0 {
            10_000.0
        } else {
            100.0
        };
        let tokens = [
            event.condition_token_1,
            event.condition_token_2,
            event.condition_token_3,
            event.condition_token_4,
            event.condition_token_5,
        ];
        let encoded_tape = (event.event_meta >> 3) & 0x07;
        let tape = self
            .tapes
            .get(&encoded_tape)
            .copied()
            .unwrap_or(encoded_tape + 1);
        let raw = json!({
            "schema_version": event.schema_version,
            "arrival_sequence": event.arrival_sequence,
            "event_meta": event.event_meta,
            "issue_flags": event.issue_flags,
            "sip_timestamp_us": event.sip_timestamp_us,
            "correlation_id": event.correlation_id(),
            "causation_id": event.causation_id(),
        });
        if event.event_type() == TRADE_EVENT_TYPE {
            let conditions = tokens
                .into_iter()
                .filter_map(|token| self.trade_conditions.get(&token).copied())
                .collect();
            MarketEvent::Trade(TradeEvent {
                conditions,
                exchange: u16::from(event.exchange_primary),
                ingest_ts: event.ingest_ts,
                participant_ts: None,
                price: f64::from(event.price_primary_int) / primary_scale,
                raw,
                sequence: event.source_sequence,
                size: f64::from(event.size_primary),
                tape,
                ticker: event.ticker.clone(),
                trade_id: format!("compact-{}", event.arrival_sequence),
                trf_id: 0,
                trf_ts: None,
                ts: Utc
                    .timestamp_micros(event.sip_timestamp_us as i64)
                    .single()
                    .unwrap_or(event.ingest_ts),
            })
        } else {
            let conditions = tokens[..4]
                .iter()
                .filter_map(|token| self.quote_conditions.get(token).copied())
                .collect();
            let indicators = self
                .quote_indicators
                .get(&tokens[4])
                .copied()
                .into_iter()
                .collect();
            MarketEvent::Quote(QuoteEvent {
                ask_exchange: u16::from(event.exchange_primary),
                ask_price: f64::from(event.price_primary_int) / primary_scale,
                ask_size: event.size_primary.max(0.0).round().min(u32::MAX as f32) as u32,
                bid_exchange: u16::from(event.exchange_secondary),
                bid_price: f64::from(event.price_secondary_int) / secondary_scale,
                bid_size: event.size_secondary.max(0.0).round().min(u32::MAX as f32) as u32,
                conditions,
                indicators,
                ingest_ts: event.ingest_ts,
                raw,
                sequence: event.source_sequence,
                tape,
                ticker: event.ticker.clone(),
                ts: Utc
                    .timestamp_micros(event.sip_timestamp_us as i64)
                    .single()
                    .unwrap_or(event.ingest_ts),
            })
        }
    }
}

fn causal_hash(parts: &[&[u8]]) -> u64 {
    let mut hash = 0xcbf29ce484222325_u64;
    for part in parts {
        for byte in *part {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
        hash ^= 0xff;
        hash = hash.wrapping_mul(0x100000001b3);
    }
    hash
}

#[derive(Clone)]
pub struct SharedCompactEventStore {
    capacity_per_ticker: usize,
    inner: Arc<RwLock<HashMap<String, TickerCompactEvents>>>,
}

#[derive(Clone, Debug, Default)]
struct TickerCompactEvents {
    events: VecDeque<LiveCompactEvent>,
    evicted_through_arrival_sequence: u64,
    evicted_through_sip_timestamp_us: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct CompactEventPage {
    pub buffer_end_arrival_sequence: u64,
    pub buffer_start_arrival_sequence: u64,
    pub cursor_expired: bool,
    pub delivery_order: &'static str,
    pub events: Vec<LiveCompactEvent>,
    pub has_more: bool,
    pub next_after_arrival_sequence: u64,
    pub requested_after_arrival_sequence: u64,
    pub schema_version: u16,
    pub ticker: String,
    pub truncated_before: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct CompactEventMarketPage {
    pub buffer_end_arrival_sequence: u64,
    pub buffer_start_arrival_sequence: u64,
    pub cursor_expired: bool,
    pub delivery_order: &'static str,
    pub end_sip_timestamp_us: u64,
    pub events: Vec<LiveCompactEvent>,
    pub has_more: bool,
    pub next_after_arrival_sequence: u64,
    pub requested_after_arrival_sequence: u64,
    pub schema_version: u16,
    pub start_sip_timestamp_us: u64,
    pub ticker_count: usize,
    pub through_arrival_sequence: u64,
    pub truncated_before: bool,
}

impl SharedCompactEventStore {
    pub fn new(capacity_per_ticker: usize) -> Self {
        Self {
            capacity_per_ticker,
            inner: Arc::new(RwLock::new(HashMap::new())),
        }
    }

    pub async fn push(&self, event: LiveCompactEvent) {
        if self.capacity_per_ticker == 0 {
            return;
        }
        let mut guard = self.inner.write().await;
        let state = guard.entry(event.ticker.clone()).or_default();
        state.events.push_back(event);
        while state.events.len() > self.capacity_per_ticker {
            if let Some(evicted) = state.events.pop_front() {
                state.evicted_through_arrival_sequence = state
                    .evicted_through_arrival_sequence
                    .max(evicted.arrival_sequence);
                state.evicted_through_sip_timestamp_us = state
                    .evicted_through_sip_timestamp_us
                    .max(evicted.sip_timestamp_us);
            }
        }
    }

    pub async fn latest_sorted(&self, ticker: &str, limit: usize) -> Vec<LiveCompactEvent> {
        let guard = self.inner.read().await;
        let Some(state) = guard.get(&ticker.to_ascii_uppercase()) else {
            return Vec::new();
        };
        let mut out = state.events.iter().cloned().collect::<Vec<_>>();
        out.sort_by_key(EventSortKey::from_event);
        if out.len() > limit {
            out.split_off(out.len() - limit)
        } else {
            out
        }
    }

    pub async fn page_after(
        &self,
        ticker: &str,
        after_arrival_sequence: u64,
        limit: usize,
    ) -> CompactEventPage {
        let normalized_ticker = ticker.trim().to_ascii_uppercase();
        let guard = self.inner.read().await;
        let state = guard.get(&normalized_ticker);
        let buffer_start = state
            .and_then(|value| value.events.front())
            .map(|event| event.arrival_sequence)
            .unwrap_or(0);
        let buffer_end = state
            .and_then(|value| value.events.back())
            .map(|event| event.arrival_sequence)
            .unwrap_or(0);
        let evicted_through = state
            .map(|value| value.evicted_through_arrival_sequence)
            .unwrap_or(0);
        let mut available = state
            .map(|value| {
                value
                    .events
                    .iter()
                    .filter(|event| event.arrival_sequence > after_arrival_sequence)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        available.sort_by_key(|event| event.arrival_sequence);
        let bounded_limit = limit.max(1).min(self.capacity_per_ticker.max(1));
        let has_more = available.len() > bounded_limit;
        available.truncate(bounded_limit);
        let next_after = available
            .last()
            .map(|event| event.arrival_sequence)
            .unwrap_or(after_arrival_sequence);
        CompactEventPage {
            buffer_end_arrival_sequence: buffer_end,
            buffer_start_arrival_sequence: buffer_start,
            cursor_expired: after_arrival_sequence > 0 && after_arrival_sequence < evicted_through,
            delivery_order: "arrival_sequence_ascending",
            events: available,
            has_more,
            next_after_arrival_sequence: next_after,
            requested_after_arrival_sequence: after_arrival_sequence,
            schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
            ticker: normalized_ticker,
            truncated_before: evicted_through > 0,
        }
    }

    pub async fn latest_page(&self, ticker: &str, limit: usize) -> CompactEventPage {
        let normalized_ticker = ticker.trim().to_ascii_uppercase();
        let guard = self.inner.read().await;
        let state = guard.get(&normalized_ticker);
        let buffer_start = state
            .and_then(|value| value.events.front())
            .map(|event| event.arrival_sequence)
            .unwrap_or(0);
        let buffer_end = state
            .and_then(|value| value.events.back())
            .map(|event| event.arrival_sequence)
            .unwrap_or(0);
        let bounded_limit = limit.max(1).min(self.capacity_per_ticker.max(1));
        let retained_count = state.map(|value| value.events.len()).unwrap_or(0);
        let mut events = state
            .map(|value| {
                value
                    .events
                    .iter()
                    .rev()
                    .take(bounded_limit)
                    .cloned()
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        events.reverse();
        let next_after = events
            .last()
            .map(|event| event.arrival_sequence)
            .unwrap_or(0);
        CompactEventPage {
            buffer_end_arrival_sequence: buffer_end,
            buffer_start_arrival_sequence: buffer_start,
            cursor_expired: false,
            delivery_order: "arrival_sequence_ascending",
            events,
            has_more: false,
            next_after_arrival_sequence: next_after,
            requested_after_arrival_sequence: 0,
            schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
            ticker: normalized_ticker,
            truncated_before: state
                .map(|value| value.evicted_through_arrival_sequence > 0)
                .unwrap_or(false)
                || retained_count > bounded_limit,
        }
    }

    pub async fn tickers(&self) -> Vec<String> {
        let guard = self.inner.read().await;
        let mut out = guard.keys().cloned().collect::<Vec<_>>();
        out.sort();
        out
    }

    pub async fn market_page_after(
        &self,
        after_arrival_sequence: u64,
        start_sip_timestamp_us: u64,
        end_sip_timestamp_us: u64,
        tickers: &[String],
        limit: usize,
        through_arrival_sequence: Option<u64>,
    ) -> CompactEventMarketPage {
        let requested = tickers
            .iter()
            .map(|ticker| ticker.trim().to_ascii_uppercase())
            .filter(|ticker| !ticker.is_empty())
            .collect::<std::collections::HashSet<_>>();
        let guard = self.inner.read().await;
        let selected = guard
            .iter()
            .filter(|(ticker, _)| requested.is_empty() || requested.contains(*ticker))
            .collect::<Vec<_>>();
        let buffer_start = selected
            .iter()
            .filter_map(|(_, state)| state.events.front())
            .map(|event| event.arrival_sequence)
            .min()
            .unwrap_or(0);
        let buffer_end = selected
            .iter()
            .filter_map(|(_, state)| state.events.back())
            .map(|event| event.arrival_sequence)
            .max()
            .unwrap_or(0);
        let window_end_arrival_sequence = selected
            .iter()
            .flat_map(|(_, state)| state.events.iter())
            .filter(|event| {
                event.sip_timestamp_us >= start_sip_timestamp_us
                    && event.sip_timestamp_us < end_sip_timestamp_us
            })
            .map(|event| event.arrival_sequence)
            .max()
            .unwrap_or(0);
        let through_arrival_sequence =
            through_arrival_sequence.unwrap_or(window_end_arrival_sequence);
        let cursor_expired = selected.iter().any(|(_, state)| {
            state.evicted_through_sip_timestamp_us >= start_sip_timestamp_us
                && (after_arrival_sequence == 0
                    || after_arrival_sequence < state.evicted_through_arrival_sequence)
        });
        let mut available = selected
            .iter()
            .flat_map(|(_, state)| state.events.iter())
            .filter(|event| {
                event.arrival_sequence > after_arrival_sequence
                    && event.arrival_sequence <= through_arrival_sequence
                    && event.sip_timestamp_us >= start_sip_timestamp_us
                    && event.sip_timestamp_us < end_sip_timestamp_us
            })
            .cloned()
            .collect::<Vec<_>>();
        available.sort_by_key(|event| event.arrival_sequence);
        let bounded_limit = limit.max(1).min(100_000);
        let has_more = available.len() > bounded_limit;
        available.truncate(bounded_limit);
        let next_after = available
            .last()
            .map(|event| event.arrival_sequence)
            .unwrap_or(after_arrival_sequence);
        CompactEventMarketPage {
            buffer_end_arrival_sequence: buffer_end,
            buffer_start_arrival_sequence: buffer_start,
            cursor_expired,
            delivery_order: "arrival_sequence_ascending",
            end_sip_timestamp_us,
            events: available,
            has_more,
            next_after_arrival_sequence: next_after,
            requested_after_arrival_sequence: after_arrival_sequence,
            schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
            start_sip_timestamp_us,
            ticker_count: selected.len(),
            through_arrival_sequence,
            truncated_before: cursor_expired,
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, Hash, Ord, PartialEq, PartialOrd)]
struct EventSortKey {
    sip_timestamp_us: u64,
    source_sequence: u64,
    event_type: u8,
    arrival_sequence: u64,
}

impl EventSortKey {
    fn from_event(event: &LiveCompactEvent) -> Self {
        Self {
            sip_timestamp_us: event.sip_timestamp_us,
            source_sequence: event.source_sequence,
            event_type: event.event_type(),
            arrival_sequence: event.arrival_sequence,
        }
    }
}

#[derive(Default)]
struct TickerReorderBuffer {
    events: BTreeMap<EventSortKey, LiveCompactEvent>,
    max_seen_sip_timestamp_us: u64,
}

impl TickerReorderBuffer {
    fn insert(&mut self, event: LiveCompactEvent) -> bool {
        let late = self.max_seen_sip_timestamp_us > event.sip_timestamp_us;
        self.max_seen_sip_timestamp_us = self.max_seen_sip_timestamp_us.max(event.sip_timestamp_us);
        self.events.insert(EventSortKey::from_event(&event), event);
        late
    }

    fn drain_ready(
        &mut self,
        reorder_lag_us: u64,
        force_limit: usize,
    ) -> (Vec<LiveCompactEvent>, bool) {
        let watermark = self
            .max_seen_sip_timestamp_us
            .saturating_sub(reorder_lag_us);
        let force_flush = force_limit > 0 && self.events.len() > force_limit;
        let mut ready = Vec::new();
        loop {
            let Some(key) = self.events.keys().next().copied() else {
                break;
            };
            if !force_flush && key.sip_timestamp_us > watermark {
                break;
            }
            if force_flush && self.events.len().saturating_sub(ready.len()) <= force_limit / 2 {
                break;
            }
            if let Some(event) = self.events.remove(&key) {
                ready.push(event);
            }
        }
        (ready, force_flush)
    }

    fn drain_all(&mut self) -> Vec<LiveCompactEvent> {
        std::mem::take(&mut self.events).into_values().collect()
    }
}

#[derive(Clone)]
pub struct CompactEventReferences {
    quote_conditions: HashMap<i16, u8>,
    trade_conditions: HashMap<i16, u8>,
    trade_updates: HashMap<i16, TradeUpdateRule>,
    quote_indicators: HashMap<i16, u8>,
    tapes: HashMap<u8, u8>,
}

impl CompactEventReferences {
    pub async fn load(config: &GatewayConfig) -> Result<Self, String> {
        Self::load_from_clickhouse(
            &config.historical_clickhouse_url,
            &config.historical_clickhouse_user,
            &config.historical_clickhouse_password(),
            &config.historical_clickhouse_database,
        )
        .await
    }

    pub async fn load_from_clickhouse(
        base_url: &str,
        user: &str,
        password: &str,
        database: &str,
    ) -> Result<Self, String> {
        let client = Client::new();
        let database = database.replace('`', "");
        let token_sql = format!(
            "SELECT source_family, modifier_int, min(token_id) FROM {database}.event_condition_token_reference WHERE is_join_canonical = 1 GROUP BY source_family, modifier_int ORDER BY min(token_id) FORMAT TSV"
        );
        let token_rows =
            clickhouse_query(&client, base_url, user, password, None, &token_sql).await?;
        let mut quote_conditions = HashMap::new();
        let mut trade_conditions = HashMap::new();
        let mut quote_indicators = HashMap::new();
        for row in token_rows.lines() {
            let parts = row.split('\t').collect::<Vec<_>>();
            if parts.len() != 3 {
                continue;
            }
            let family = parts[0];
            let modifier = parts[1].parse::<i16>().map_err(|error| error.to_string())?;
            let token = parts[2].parse::<u16>().map_err(|error| error.to_string())?;
            if token > u8::MAX as u16 {
                return Err(format!(
                    "condition token {token} exceeds the UInt8 event contract"
                ));
            }
            let token = token as u8;
            match family {
                "quote_conditions" => {
                    quote_conditions.insert(modifier, token);
                }
                "trade_conditions" => {
                    trade_conditions.insert(modifier, token);
                }
                "unknown" | "trade_corrections_nyse" => {}
                _ => {
                    quote_indicators
                        .entry(modifier)
                        .and_modify(|current: &mut u8| *current = (*current).min(token))
                        .or_insert(token);
                }
            }
        }
        if quote_conditions.is_empty() || trade_conditions.is_empty() || quote_indicators.is_empty()
        {
            return Err("event_condition_token_reference is missing canonical quote, trade, or indicator rows".to_string());
        }

        let trade_update_sql = format!(
            "SELECT modifier_int, argMin(update_high_low, token_id), argMin(update_last, token_id), argMin(update_volume, token_id) FROM {database}.event_condition_token_reference WHERE source_family = 'trade_conditions' AND is_join_canonical = 1 GROUP BY modifier_int ORDER BY modifier_int FORMAT TSV"
        );
        let trade_update_rows =
            clickhouse_query(&client, base_url, user, password, None, &trade_update_sql).await?;
        let mut trade_updates = HashMap::new();
        for row in trade_update_rows.lines() {
            let parts = row.split('\t').collect::<Vec<_>>();
            if parts.len() != 4 {
                continue;
            }
            let modifier = parts[0].parse::<i16>().map_err(|error| error.to_string())?;
            trade_updates.insert(
                modifier,
                TradeUpdateRule {
                    update_high_low: parts[1] == "1",
                    update_last: parts[2] == "1",
                    update_volume: parts[3] == "1",
                },
            );
        }
        if trade_updates.len() != trade_conditions.len() {
            return Err(format!(
                "trade update rules disagree with canonical trade conditions: rules={} conditions={}",
                trade_updates.len(),
                trade_conditions.len()
            ));
        }

        let tape_sql = format!(
            "SELECT raw_id, dense_id FROM {database}.ref_stock_tapes WHERE raw_id IS NOT NULL AND dense_id_kind = 'actual' ORDER BY raw_id FORMAT TSV"
        );
        let tape_rows =
            clickhouse_query(&client, base_url, user, password, None, &tape_sql).await?;
        let mut tapes = HashMap::new();
        for row in tape_rows.lines() {
            let parts = row.split('\t').collect::<Vec<_>>();
            if parts.len() != 2 {
                continue;
            }
            let raw = parts[0].parse::<u8>().map_err(|error| error.to_string())?;
            let dense = parts[1].parse::<u8>().map_err(|error| error.to_string())?;
            let encoded = dense.checked_sub(1).ok_or_else(|| {
                format!("ref_stock_tapes raw_id={raw} has invalid dense_id={dense}")
            })?;
            tapes.insert(raw, encoded);
        }
        for (raw, expected) in [(1u8, 0u8), (2, 1), (3, 2)] {
            if tapes.get(&raw).copied() != Some(expected) {
                return Err(format!(
                    "ref_stock_tapes disagrees with download_update_events: raw tape {raw} must encode as {expected}"
                ));
            }
        }
        Ok(Self {
            quote_conditions,
            trade_conditions,
            trade_updates,
            quote_indicators,
            tapes,
        })
    }

    pub fn decoder(&self) -> CompactEventDecoder {
        CompactEventDecoder::new(
            self.quote_conditions
                .iter()
                .filter_map(|(modifier, token)| {
                    u16::try_from(*modifier)
                        .ok()
                        .map(|modifier| (*token, modifier))
                }),
            self.trade_conditions
                .iter()
                .filter_map(|(modifier, token)| {
                    u16::try_from(*modifier)
                        .ok()
                        .map(|modifier| (*token, modifier))
                }),
            self.quote_indicators
                .iter()
                .filter_map(|(modifier, token)| {
                    u16::try_from(*modifier)
                        .ok()
                        .map(|modifier| (*token, modifier))
                }),
            self.tapes.iter().map(|(raw, encoded)| (*encoded, *raw)),
        )
    }

    pub fn trade_aggregation_rules(&self) -> Result<TradeAggregationRules, String> {
        TradeAggregationRules::new(self.trade_updates.iter().filter_map(|(modifier, rule)| {
            u16::try_from(*modifier)
                .ok()
                .map(|modifier| (modifier, *rule))
        }))
    }

    fn quote_condition_id(&self, value: u16) -> u8 {
        self.quote_conditions
            .get(&(value as i16))
            .copied()
            .unwrap_or(0)
    }

    fn trade_condition_id(&self, value: u16) -> u8 {
        self.trade_conditions
            .get(&(value as i16))
            .copied()
            .unwrap_or(0)
    }

    fn quote_indicator_id(&self, value: u16) -> u8 {
        self.quote_indicators
            .get(&(value as i16))
            .copied()
            .unwrap_or(0)
    }

    fn tape_id(&self, value: u8) -> u8 {
        self.tapes.get(&value).copied().unwrap_or(0)
    }
}

#[derive(Clone, Debug)]
struct CompactEventIssue {
    issue_kind: &'static str,
    condition_codes: Vec<u16>,
    indicator_codes: Vec<u16>,
    selected_tokens: [u8; CONDITION_TOKEN_SLOTS],
    raw_tape: u8,
}

struct CompactConversion {
    event: LiveCompactEvent,
    issue: Option<CompactEventIssue>,
}

#[derive(Clone, Debug)]
struct CoverageWindow {
    end: DateTime<Utc>,
    rows_written: u64,
    start: DateTime<Utc>,
}

fn compact_coverage_groups(
    rows: &[LiveCompactEvent],
) -> BTreeMap<(String, String), CoverageWindow> {
    let mut grouped = BTreeMap::new();
    for row in rows {
        let Some(timestamp) = sip_us_to_datetime(row.sip_timestamp_us) else {
            continue;
        };
        let key = (
            row.event_date.clone(),
            timestamp.with_timezone(&New_York).date_naive().to_string(),
        );
        let window = grouped.entry(key).or_insert_with(|| CoverageWindow {
            end: timestamp,
            rows_written: 0,
            start: timestamp,
        });
        window.start = window.start.min(timestamp);
        window.end = window.end.max(timestamp);
        window.rows_written = window.rows_written.saturating_add(1);
    }
    grouped
}

#[derive(Clone)]
pub struct CompactEventClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    event_sender: broadcast::Sender<LiveCompactEvent>,
    live_store: SharedCompactEventStore,
    metrics: SharedMetrics,
    product_router: MarketProductEventRouter,
    references: CompactEventReferences,
    decoder: CompactEventDecoder,
    intraday_bar_router: IntradayBarRouter,
    coverage_windows: Arc<Mutex<HashMap<(String, String), CoverageWindow>>>,
}

struct CompactPersistWork {
    events: Vec<LiveCompactEvent>,
    issues: Vec<(LiveCompactEvent, CompactEventIssue)>,
}

#[derive(Clone)]
struct CompactPersistPending {
    main_events: Arc<AtomicU64>,
    main_issues: Arc<AtomicU64>,
    worker_events: Arc<Vec<AtomicU64>>,
    worker_issues: Arc<Vec<AtomicU64>>,
    metrics: SharedMetrics,
}

impl CompactPersistPending {
    fn new(worker_count: usize, metrics: SharedMetrics) -> Self {
        Self {
            main_events: Arc::new(AtomicU64::new(0)),
            main_issues: Arc::new(AtomicU64::new(0)),
            worker_events: Arc::new((0..worker_count).map(|_| AtomicU64::new(0)).collect()),
            worker_issues: Arc::new((0..worker_count).map(|_| AtomicU64::new(0)).collect()),
            metrics,
        }
    }

    fn set_main(&self, events: u64, issues: u64) {
        self.main_events.store(events, Ordering::Relaxed);
        self.main_issues.store(issues, Ordering::Relaxed);
        self.publish();
    }

    fn set_worker(&self, worker_id: usize, events: u64, issues: u64) {
        self.worker_events[worker_id].store(events, Ordering::Relaxed);
        self.worker_issues[worker_id].store(issues, Ordering::Relaxed);
        self.publish();
    }

    fn publish(&self) {
        let events = self
            .worker_events
            .iter()
            .map(|value| value.load(Ordering::Relaxed))
            .fold(
                self.main_events.load(Ordering::Relaxed),
                u64::saturating_add,
            );
        let issues = self
            .worker_issues
            .iter()
            .map(|value| value.load(Ordering::Relaxed))
            .fold(
                self.main_issues.load(Ordering::Relaxed),
                u64::saturating_add,
            );
        self.metrics.set_lane_pending("compact_events", events);
        self.metrics.set_lane_pending("compact_audit", issues);
    }
}

impl CompactEventClickHouseWriter {
    pub fn new(
        config: GatewayConfig,
        references: CompactEventReferences,
        event_sender: broadcast::Sender<LiveCompactEvent>,
        live_store: SharedCompactEventStore,
        metrics: SharedMetrics,
        intraday_bar_router: IntradayBarRouter,
        product_router: MarketProductEventRouter,
    ) -> Self {
        let decoder = references.decoder();
        Self {
            client: Client::new(),
            config,
            event_sender,
            live_store,
            metrics,
            product_router,
            references,
            decoder,
            intraday_bar_router,
            coverage_windows: Arc::new(Mutex::new(HashMap::new())),
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        if !self.config.persist_compact_events {
            return Ok(());
        }
        self.execute(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.ensure_compact_event_table().await?;
        self.execute("DROP TABLE IF EXISTS live_event_ordinal_continuity", true)
            .await?;
        self.execute(&self.create_issue_table_sql(), true).await?;
        self.execute("ALTER TABLE qmd_compact_event_issue_v1 ADD COLUMN IF NOT EXISTS raw_tape UInt8 AFTER arrival_sequence", true).await?;
        self.execute(&self.create_live_coverage_table_sql(), true)
            .await
    }

    pub async fn run(
        self,
        live_receiver: mpsc::Receiver<MarketEvent>,
        repair_receiver: mpsc::Receiver<MarketEvent>,
    ) {
        let (priority_sender, priority_receiver) = mpsc::channel(1);
        let priority_metrics = self.metrics.clone();
        let priority_task = tokio::spawn(merge_compact_inputs_live_first(
            live_receiver,
            repair_receiver,
            priority_sender,
            priority_metrics,
        ));
        self.run_merged(priority_receiver).await;
        priority_task.abort();
    }

    async fn run_merged(self, mut receiver: mpsc::Receiver<MarketEvent>) {
        const PERSIST_WORKER_COUNT: usize = 2;
        let (persist_sender, persist_receiver) = mpsc::channel::<CompactPersistWork>(4);
        let persist_receiver = Arc::new(Mutex::new(persist_receiver));
        let persist_pending =
            CompactPersistPending::new(PERSIST_WORKER_COUNT, self.metrics.clone());
        let mut persist_handles = Vec::with_capacity(PERSIST_WORKER_COUNT);
        for worker_id in 0..PERSIST_WORKER_COUNT {
            persist_handles.push(tokio::spawn(self.clone().run_persist_worker(
                worker_id,
                persist_receiver.clone(),
                persist_pending.clone(),
            )));
        }
        let mut batch = Vec::with_capacity(self.config.compact_event_max_clickhouse_batch);
        let mut issue_batch = Vec::new();
        let mut issues_seen = 0u64;
        let mut arrival_sequence = self
            .latest_arrival_sequence()
            .await
            .unwrap_or_else(|error| {
                eprintln!(
                    "Compact event arrival sequence bootstrap failed; starting from zero: {error}"
                );
                0
            });
        let mut reorder_buffers: HashMap<String, TickerReorderBuffer> = HashMap::new();
        let mut reorder_pending_count = 0u64;
        let reorder_lag_us = self
            .config
            .compact_event_reorder_lag_ms
            .saturating_mul(1_000);
        let mut last_force_flush = Instant::now();
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                event = receiver.recv() => {
                    match event {
                        Some(event) => match compact_event_from_market_event(&event, &self.references) {
                            Ok(mut conversion) => {
                                arrival_sequence = arrival_sequence.saturating_add(1);
                                conversion.event.arrival_sequence = arrival_sequence;
                                if let Some(issue) = conversion.issue.take() {
                                    issues_seen = issues_seen.saturating_add(1);
                                    if issues_seen == 1 || issues_seen % 10_000 == 0 {
                                        eprintln!(
                                            "Compact event warning summary: seen={} latest_kind={} latest_ticker={} latest_sip_timestamp_us={} latest_source_sequence={}",
                                            issues_seen,
                                            issue.issue_kind,
                                            conversion.event.ticker,
                                            conversion.event.sip_timestamp_us,
                                            conversion.event.source_sequence,
                                        );
                                    }
                                    issue_batch.push((conversion.event.clone(), issue));
                                }
                                if self.event_sender.send(conversion.event.clone()).is_err() {
                                    self.metrics.inc_compact_event_broadcast_dropped();
                                }
                                self.live_store.push(conversion.event.clone()).await;
                                let canonical_event = self.decoder.decode(&conversion.event);
                                if self.product_router.send(canonical_event).await.is_err() {
                                    eprintln!("Market-product receiver closed; could not route one compact event.");
                                }
                                if self.intraday_bar_router.send(conversion.event.clone()).await.is_err() {
                                    self.metrics.inc_intraday_bar_event_dropped();
                                    eprintln!("Canonical intraday bar receiver closed; could not route one compact event.");
                                }
                                self.metrics.inc_compact_events_emitted(1);
                                if self.config.persist_compact_events {
                                    let ticker = conversion.event.ticker.clone();
                                    let buffer = reorder_buffers.entry(ticker.clone()).or_default();
                                    if buffer.insert(conversion.event) {
                                        self.metrics.inc_compact_event_reorder_late_arrival();
                                    }
                                    reorder_pending_count = reorder_pending_count.saturating_add(1);
                                    self.metrics.inc_compact_events_reorder_buffered(1);
                                    self.metrics.set_compact_events_reorder_pending(reorder_pending_count);
                                    self.drain_reorder_buffer(
                                        &mut reorder_buffers,
                                        &ticker,
                                        &mut batch,
                                        &mut reorder_pending_count,
                                        reorder_lag_us,
                                        false,
                                    );
                                    if batch.len() >= self.config.compact_event_max_clickhouse_batch {
                                        self.submit_persist_work(&persist_sender, &mut batch, &mut issue_batch).await;
                                    }
                                }
                            }
                            Err(reason) => record_compact_event_rejection(&self.metrics, reason),
                        },
                        None => {
                            self.drain_reorder_buffers(
                                &mut reorder_buffers,
                                &mut batch,
                                &mut reorder_pending_count,
                                reorder_lag_us,
                                true,
                            );
                            self.submit_persist_work(&persist_sender, &mut batch, &mut issue_batch).await;
                            drop(persist_sender);
                            for handle in persist_handles {
                                let _ = handle.await;
                            }
                            return;
                        }
                    }
                }
                _ = flush_interval.tick() => {
                    let force = last_force_flush.elapsed() >= Duration::from_millis(self.config.compact_event_reorder_force_flush_ms);
                    if force {
                        last_force_flush = Instant::now();
                    }
                    self.drain_reorder_buffers(
                        &mut reorder_buffers,
                        &mut batch,
                        &mut reorder_pending_count,
                        reorder_lag_us,
                        force,
                    );
                    self.submit_persist_work(&persist_sender, &mut batch, &mut issue_batch).await;
                }
            }
            persist_pending.set_main(
                reorder_pending_count
                    .saturating_add(batch.len() as u64)
                    .saturating_add(receiver.len() as u64),
                issue_batch.len() as u64,
            );
        }
    }

    async fn submit_persist_work(
        &self,
        sender: &mpsc::Sender<CompactPersistWork>,
        batch: &mut Vec<LiveCompactEvent>,
        issue_batch: &mut Vec<(LiveCompactEvent, CompactEventIssue)>,
    ) {
        if batch.is_empty() && issue_batch.is_empty() {
            return;
        }
        let work = CompactPersistWork {
            events: std::mem::replace(
                batch,
                Vec::with_capacity(self.config.compact_event_max_clickhouse_batch),
            ),
            issues: std::mem::take(issue_batch),
        };
        if let Err(error) = sender.send(work).await {
            eprintln!(
                "Compact persistence workers closed; persisting the rejected durable batch inline."
            );
            let mut work = error.0;
            while !work.events.is_empty() || !work.issues.is_empty() {
                self.flush_persisted(&mut work.events).await;
                self.flush_issues(&mut work.issues).await;
                if !work.events.is_empty() || !work.issues.is_empty() {
                    sleep(Duration::from_millis(250)).await;
                }
            }
        }
    }

    async fn run_persist_worker(
        self,
        worker_id: usize,
        receiver: Arc<Mutex<mpsc::Receiver<CompactPersistWork>>>,
        pending: CompactPersistPending,
    ) {
        loop {
            let Some(mut work) = receiver.lock().await.recv().await else {
                pending.set_worker(worker_id, 0, 0);
                return;
            };
            pending.set_worker(
                worker_id,
                work.events.len() as u64,
                work.issues.len() as u64,
            );
            while !work.events.is_empty() || !work.issues.is_empty() {
                self.flush_persisted(&mut work.events).await;
                self.flush_issues(&mut work.issues).await;
                if !work.events.is_empty() || !work.issues.is_empty() {
                    sleep(Duration::from_millis(250)).await;
                }
            }
            pending.set_worker(worker_id, 0, 0);
        }
    }

    fn drain_reorder_buffers(
        &self,
        reorder_buffers: &mut HashMap<String, TickerReorderBuffer>,
        batch: &mut Vec<LiveCompactEvent>,
        reorder_pending_count: &mut u64,
        reorder_lag_us: u64,
        force: bool,
    ) {
        for buffer in reorder_buffers.values_mut() {
            let (ready, forced) = if force {
                (buffer.drain_all(), false)
            } else {
                buffer.drain_ready(
                    reorder_lag_us,
                    self.config.compact_event_reorder_max_events_per_ticker,
                )
            };
            if forced {
                self.metrics.inc_compact_event_reorder_forced_flush();
            }
            *reorder_pending_count = reorder_pending_count.saturating_sub(ready.len() as u64);
            self.metrics
                .inc_compact_events_reorder_flushed(ready.len() as u64);
            batch.extend(ready);
        }
        self.metrics
            .set_compact_events_reorder_pending(*reorder_pending_count);
    }

    fn drain_reorder_buffer(
        &self,
        reorder_buffers: &mut HashMap<String, TickerReorderBuffer>,
        ticker: &str,
        batch: &mut Vec<LiveCompactEvent>,
        reorder_pending_count: &mut u64,
        reorder_lag_us: u64,
        force: bool,
    ) {
        let Some(buffer) = reorder_buffers.get_mut(ticker) else {
            return;
        };
        let (ready, forced) = if force {
            (buffer.drain_all(), false)
        } else {
            buffer.drain_ready(
                reorder_lag_us,
                self.config.compact_event_reorder_max_events_per_ticker,
            )
        };
        if forced {
            self.metrics.inc_compact_event_reorder_forced_flush();
        }
        *reorder_pending_count = reorder_pending_count.saturating_sub(ready.len() as u64);
        self.metrics
            .inc_compact_events_reorder_flushed(ready.len() as u64);
        batch.extend(ready);
        self.metrics
            .set_compact_events_reorder_pending(*reorder_pending_count);
    }

    async fn flush_persisted(&self, batch: &mut Vec<LiveCompactEvent>) {
        if batch.is_empty() || !self.config.persist_compact_events {
            return;
        }
        batch.sort_by(|left, right| {
            left.ticker
                .cmp(&right.ticker)
                .then_with(|| EventSortKey::from_event(left).cmp(&EventSortKey::from_event(right)))
        });
        match self.insert_events(batch).await {
            Ok(()) => {
                let count = batch.len() as u64;
                self.metrics.inc_compact_events_persisted(count);
                let coverage_result = self
                    .record_live_event_coverage("compact_persisted", batch, "", 0)
                    .await;
                batch.clear();
                self.metrics.record_lane_success(
                    "compact_events",
                    count,
                    "Committed normalized compact events to q_live.events.",
                );
                match coverage_result {
                    Ok(()) => self.metrics.record_lane_success(
                        "coverage_ledger",
                        1,
                        "Recorded compact-event coverage confirmation.",
                    ),
                    Err(error) => {
                        self.metrics.record_lane_failure("coverage_ledger", &error);
                        eprintln!("ClickHouse qmd live coverage update failed: {error}");
                    }
                }
            }
            Err(error) => {
                match self
                    .record_live_event_coverage("failed", batch, &error, 1)
                    .await
                {
                    Ok(()) => self.metrics.record_lane_success(
                        "coverage_ledger",
                        1,
                        "Recorded the compact persistence failure for coverage recovery.",
                    ),
                    Err(coverage_error) => self
                        .metrics
                        .record_lane_failure("coverage_ledger", &coverage_error),
                }
                self.metrics.record_lane_failure("compact_events", &error);
                eprintln!("ClickHouse compact event insert failed: {error}");
            }
        }
    }

    async fn insert_events(&self, rows: &[LiveCompactEvent]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|event| {
                json!({
                    "event_date": event.event_date,
                    "schema_version": event.schema_version,
                    "ingest_ts": clickhouse_datetime64(&event.ingest_ts),
                    "arrival_sequence": event.arrival_sequence,
                    "ticker": event.ticker,
                    "event_meta": event.event_meta,
                    "sip_timestamp_us": event.sip_timestamp_us,
                    "price_primary_int": event.price_primary_int,
                    "price_secondary_int": event.price_secondary_int,
                    "size_primary": event.size_primary,
                    "size_secondary": event.size_secondary,
                    "exchange_primary": event.exchange_primary,
                    "exchange_secondary": event.exchange_secondary,
                    "condition_token_1": event.condition_token_1,
                    "condition_token_2": event.condition_token_2,
                    "condition_token_3": event.condition_token_3,
                    "condition_token_4": event.condition_token_4,
                    "condition_token_5": event.condition_token_5,
                    "source_sequence": event.source_sequence,
                    "issue_flags": event.issue_flags,
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow",
                self.config.compact_event_table
            ),
            body,
        )
        .await
    }

    async fn latest_arrival_sequence(&self) -> Result<u64, String> {
        if !self.config.persist_compact_events {
            return Ok(0);
        }
        let row = self
            .query(
                &format!(
                    "SELECT max(arrival_sequence) FROM {} FORMAT TSV",
                    self.config.compact_event_table
                ),
                true,
            )
            .await?;
        Ok(row.trim().parse::<u64>().unwrap_or(0))
    }

    async fn ensure_compact_event_table(&self) -> Result<(), String> {
        self.execute(&self.create_table_sql(), true).await?;
        let actual = self
            .query(
                &format!(
                    "SELECT name, type FROM system.columns WHERE database = currentDatabase() AND table = '{}' ORDER BY position FORMAT TabSeparatedRaw",
                    escape_sql_string(&self.config.compact_event_table)
                ),
                true,
            )
            .await?;
        let expected = [
            ("event_date", "Date"),
            ("schema_version", "UInt16"),
            ("ingest_ts", "DateTime64(3, 'UTC')"),
            ("arrival_sequence", "UInt64"),
            ("ticker", "LowCardinality(String)"),
            ("event_meta", "UInt8"),
            ("sip_timestamp_us", "UInt64"),
            ("price_primary_int", "UInt32"),
            ("price_secondary_int", "UInt32"),
            ("size_primary", "Float32"),
            ("size_secondary", "Float32"),
            ("exchange_primary", "UInt8"),
            ("exchange_secondary", "UInt8"),
            ("condition_token_1", "UInt8"),
            ("condition_token_2", "UInt8"),
            ("condition_token_3", "UInt8"),
            ("condition_token_4", "UInt8"),
            ("condition_token_5", "UInt8"),
            ("source_sequence", "UInt64"),
            ("issue_flags", "UInt16"),
        ];
        let columns = actual
            .lines()
            .filter_map(|row| row.split_once('\t'))
            .collect::<HashMap<_, _>>();
        let mismatches = expected
            .iter()
            .filter(|(name, ty)| columns.get(name).copied() != Some(*ty))
            .map(|(name, ty)| format!("{name}:{ty}"))
            .collect::<Vec<_>>();
        if columns.contains_key("ordinal") || !mismatches.is_empty() {
            return Err(format!(
                "{}.{} is not the singular ordinal-free live event schema; use a validated cutover before starting QMD (mismatches={mismatches:?})",
                self.config.clickhouse_database, self.config.compact_event_table
            ));
        }
        Ok(())
    }

    fn create_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                event_date Date,
                schema_version UInt16,
                ingest_ts DateTime64(3, 'UTC'),
                arrival_sequence UInt64 CODEC(T64, ZSTD(1)),
                ticker LowCardinality(String),
                event_meta UInt8,
                sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
                price_primary_int UInt32 CODEC(T64, ZSTD(1)),
                price_secondary_int UInt32 CODEC(T64, ZSTD(1)),
                size_primary Float32 CODEC(ZSTD(1)),
                size_secondary Float32 CODEC(ZSTD(1)),
                exchange_primary UInt8,
                exchange_secondary UInt8,
                condition_token_1 UInt8,
                condition_token_2 UInt8,
                condition_token_3 UInt8,
                condition_token_4 UInt8,
                condition_token_5 UInt8,
                source_sequence UInt64 CODEC(T64, ZSTD(1)),
                issue_flags UInt16
            )
            ENGINE = ReplacingMergeTree(ingest_ts)
            PARTITION BY event_date
            ORDER BY
            (
                ticker, sip_timestamp_us, source_sequence, bitAnd(event_meta, 1),
                event_meta, price_primary_int, price_secondary_int,
                size_primary, size_secondary, exchange_primary, exchange_secondary,
                condition_token_1, condition_token_2, condition_token_3,
                condition_token_4, condition_token_5
            )
            {settings}
            "#,
            table = self.config.compact_event_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    fn create_issue_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS qmd_compact_event_issue_v1
            (
                observed_at_utc DateTime64(3, 'UTC'),
                event_date Date,
                ticker LowCardinality(String),
                event_type UInt8,
                sip_timestamp_us UInt64,
                source_sequence UInt64,
                arrival_sequence UInt64,
                raw_tape UInt8,
                issue_kind LowCardinality(String),
                condition_count UInt16,
                indicator_count UInt16,
                condition_codes Array(UInt16),
                indicator_codes Array(UInt16),
                selected_tokens Array(UInt8),
                source LowCardinality(String),
                schema_version UInt16
            )
            ENGINE = MergeTree
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (event_date, ticker, sip_timestamp_us, source_sequence, event_type)
            {settings}
            "#,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    async fn flush_issues(&self, rows: &mut Vec<(LiveCompactEvent, CompactEventIssue)>) {
        if rows.is_empty() {
            return;
        }
        if !self.config.persist_compact_events {
            rows.clear();
            return;
        }
        let observed_at = clickhouse_datetime64(&Utc::now());
        let body = rows
            .iter()
            .map(|(event, issue)| {
                json!({
                    "observed_at_utc": observed_at,
                    "event_date": event.event_date,
                    "ticker": event.ticker,
                    "event_type": event.event_type(),
                    "sip_timestamp_us": event.sip_timestamp_us,
                    "source_sequence": event.source_sequence,
                    "arrival_sequence": event.arrival_sequence,
                    "raw_tape": issue.raw_tape,
                    "issue_kind": issue.issue_kind,
                    "condition_count": issue.condition_codes.len(),
                    "indicator_count": issue.indicator_codes.len(),
                    "condition_codes": issue.condition_codes,
                    "indicator_codes": issue.indicator_codes,
                    "selected_tokens": issue.selected_tokens,
                    "source": "qmd_normalized_event",
                    "schema_version": LIVE_COMPACT_EVENT_SCHEMA_VERSION,
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        if let Err(error) = self
            .query_with_body(
                "INSERT INTO qmd_compact_event_issue_v1 FORMAT JSONEachRow",
                body,
            )
            .await
        {
            self.metrics.record_lane_failure("compact_audit", &error);
            eprintln!("Compact event issue audit insert failed: {error}");
            return;
        }
        let count = rows.len() as u64;
        rows.clear();
        self.metrics.record_lane_success(
            "compact_audit",
            count,
            "Committed compact-event warning audit rows.",
        );
    }

    fn create_live_coverage_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                coverage_kind LowCardinality(String),
                coverage_id String,
                source LowCardinality(String),
                status LowCardinality(String),
                coverage_start_utc DateTime64(3, 'UTC'),
                coverage_end_utc DateTime64(3, 'UTC'),
                rows_written UInt64,
                event_rows UInt64,
                bar_rows UInt64,
                error_count UInt64,
                started_at_utc DateTime64(3, 'UTC'),
                updated_at_utc DateTime64(3, 'UTC'),
                completed_at_utc Nullable(DateTime64(3, 'UTC')),
                metadata_json String
            )
            ENGINE = ReplacingMergeTree(updated_at_utc)
            PARTITION BY toYYYYMM(coverage_start_utc)
            ORDER BY (coverage_kind, coverage_id)
            {settings}
            "#,
            table = self.config.qmd_live_event_coverage_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    async fn record_live_event_coverage(
        &self,
        status: &str,
        rows: &[LiveCompactEvent],
        error: &str,
        error_count: u64,
    ) -> Result<(), String> {
        if rows.is_empty() {
            return Ok(());
        }
        let now = Utc::now();
        let grouped = compact_coverage_groups(rows);
        let mut windows = self.coverage_windows.lock().await;
        let mut coverage_rows = Vec::with_capacity(grouped.len());
        for ((partition, session_date), batch) in grouped {
            let window = windows
                .entry((partition.clone(), session_date.clone()))
                .or_insert_with(|| CoverageWindow {
                    end: batch.end,
                    rows_written: 0,
                    start: batch.start,
                });
            window.start = window.start.min(batch.start);
            window.end = window.end.max(batch.end);
            if status == "compact_persisted" {
                window.rows_written = window.rows_written.saturating_add(batch.rows_written);
            }
            coverage_rows.push(json!({
                "coverage_kind": "q_live_events",
                "coverage_id": format!("compact_{}::{partition}::{session_date}", self.config.qmd_run_id),
                "source": "qmd_compact_event_writer",
                "status": status,
                "coverage_start_utc": clickhouse_datetime64(&window.start),
                "coverage_end_utc": clickhouse_datetime64(&window.end),
                "rows_written": window.rows_written,
                "event_rows": window.rows_written,
                "bar_rows": 0,
                "error_count": error_count,
                "started_at_utc": clickhouse_datetime64(&window.start),
                "updated_at_utc": clickhouse_datetime64(&now),
                "completed_at_utc": if status == "failed" { Some(clickhouse_datetime64(&now)) } else { None },
                "metadata_json": json!({
                    "run_id": self.config.qmd_run_id,
                    "event_partition": partition,
                    "market_session_date": session_date,
                    "error": error,
                }).to_string(),
            }));
        }
        drop(windows);
        let body = coverage_rows
            .into_iter()
            .map(|row| row.to_string())
            .collect::<Vec<_>>()
            .join("\n");
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{body}",
                self.config.qmd_live_event_coverage_table
            ),
            true,
        )
        .await
        .map(|_| ())
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
        clickhouse_query(
            &self.client,
            &self.config.clickhouse_url,
            &self.config.clickhouse_user,
            &self.config.clickhouse_password(),
            use_database.then_some(self.config.clickhouse_database.as_str()),
            body,
        )
        .await
    }
}

async fn merge_compact_inputs_live_first(
    mut live_receiver: mpsc::Receiver<MarketEvent>,
    mut repair_receiver: mpsc::Receiver<MarketEvent>,
    priority_sender: mpsc::Sender<MarketEvent>,
    metrics: SharedMetrics,
) {
    let mut live_closed = false;
    let mut repair_closed = false;
    while !live_closed || !repair_closed {
        let event = tokio::select! {
            biased;
            event = live_receiver.recv(), if !live_closed => {
                match event {
                    Some(event) => Some(event),
                    None => {
                        live_closed = true;
                        None
                    }
                }
            }
            event = repair_receiver.recv(), if !repair_closed => {
                match event {
                    Some(event) => Some(event),
                    None => {
                        repair_closed = true;
                        None
                    }
                }
            }
        };
        metrics.set_compact_live_events_pending(live_receiver.len() as u64);
        metrics.set_compact_repair_events_pending(repair_receiver.len() as u64);
        if let Some(event) = event {
            if priority_sender.send(event).await.is_err() {
                return;
            }
        }
    }
}

fn compact_event_from_market_event(
    event: &MarketEvent,
    references: &CompactEventReferences,
) -> Result<CompactConversion, CompactEventRejectReason> {
    match event {
        MarketEvent::Quote(quote) => compact_quote_event(quote, references),
        MarketEvent::Trade(trade) => compact_trade_event(trade, references),
    }
}

#[derive(Clone, Copy, Debug)]
pub enum CompactEventRejectReason {
    EmptyTicker,
    ZeroSequence,
    ZeroTimestamp,
}

fn record_compact_event_rejection(metrics: &SharedMetrics, reason: CompactEventRejectReason) {
    match reason {
        CompactEventRejectReason::EmptyTicker => metrics.inc_compact_event_rejected_empty_ticker(),
        CompactEventRejectReason::ZeroSequence => {
            metrics.inc_compact_event_rejected_zero_sequence()
        }
        CompactEventRejectReason::ZeroTimestamp => {
            metrics.inc_compact_event_rejected_zero_timestamp()
        }
    }
}

fn compact_quote_event(
    quote: &QuoteEvent,
    references: &CompactEventReferences,
) -> Result<CompactConversion, CompactEventRejectReason> {
    validate_structure(&quote.ticker, quote.sequence, quote.ts)?;
    let (ask_int, ask_scale, ask_valid) = encoded_price(quote.ask_price);
    let (bid_int, bid_scale, bid_valid) = encoded_price(quote.bid_price);
    let quote_valid = ask_valid
        && bid_valid
        && decoded_price(bid_int, bid_scale) <= decoded_price(ask_int, ask_scale);
    let (ask_int, bid_int, ask_scale, bid_scale) = if quote_valid {
        (ask_int, bid_int, ask_scale, bid_scale)
    } else {
        (0, 0, 0, 0)
    };
    let tokens = pack_quote_condition_tokens(&quote.conditions, &quote.indicators, references);
    let issue = condition_issue(
        &quote.conditions,
        &quote.indicators,
        quote.tape,
        tokens,
        references,
        true,
    );
    let event = LiveCompactEvent {
        arrival_sequence: 0,
        condition_token_1: 0,
        condition_token_2: 0,
        condition_token_3: 0,
        condition_token_4: 0,
        condition_token_5: 0,
        event_date: quote.ts.date_naive().to_string(),
        event_meta: event_meta(
            QUOTE_EVENT_TYPE,
            ask_scale,
            bid_scale,
            references.tape_id(quote.tape),
        ),
        exchange_primary: encode_u8(quote.ask_exchange),
        exchange_secondary: encode_u8(quote.bid_exchange),
        ingest_ts: quote.ingest_ts,
        issue_flags: 0,
        price_primary_int: ask_int,
        price_secondary_int: bid_int,
        schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        sip_timestamp_us: timestamp_us(quote.ts),
        size_primary: if quote.ask_size > 0 {
            quote.ask_size as f32
        } else {
            0.0
        },
        size_secondary: if quote.bid_size > 0 {
            quote.bid_size as f32
        } else {
            0.0
        },
        source_sequence: quote.sequence,
        ticker: quote.ticker.clone(),
    }
    .with_condition_tokens(tokens);
    Ok(CompactConversion { event, issue })
}

fn compact_trade_event(
    trade: &TradeEvent,
    references: &CompactEventReferences,
) -> Result<CompactConversion, CompactEventRejectReason> {
    validate_structure(&trade.ticker, trade.sequence, trade.ts)?;
    let (price_int, price_scale, valid) = encoded_price(trade.price);
    let (price_int, price_scale) = if valid {
        (price_int, price_scale)
    } else {
        (0, 0)
    };
    let tokens = pack_trade_condition_tokens(&trade.conditions, references);
    let issue = condition_issue(
        &trade.conditions,
        &[],
        trade.tape,
        tokens,
        references,
        false,
    );
    let event = LiveCompactEvent {
        arrival_sequence: 0,
        condition_token_1: 0,
        condition_token_2: 0,
        condition_token_3: 0,
        condition_token_4: 0,
        condition_token_5: 0,
        event_date: trade.ts.date_naive().to_string(),
        event_meta: event_meta(
            TRADE_EVENT_TYPE,
            price_scale,
            0,
            references.tape_id(trade.tape),
        ),
        exchange_primary: encode_u8(trade.exchange),
        exchange_secondary: 0,
        ingest_ts: trade.ingest_ts,
        issue_flags: 0,
        price_primary_int: price_int,
        price_secondary_int: 0,
        schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        sip_timestamp_us: timestamp_us(trade.ts),
        size_primary: if trade.size > 0.0 && trade.size.is_finite() {
            trade.size as f32
        } else {
            0.0
        },
        size_secondary: 0.0,
        source_sequence: trade.sequence,
        ticker: trade.ticker.clone(),
    }
    .with_condition_tokens(tokens);
    Ok(CompactConversion { event, issue })
}

fn validate_structure(
    ticker: &str,
    sequence: u64,
    ts: DateTime<Utc>,
) -> Result<(), CompactEventRejectReason> {
    if ticker.is_empty() {
        return Err(CompactEventRejectReason::EmptyTicker);
    }
    if sequence == 0 {
        return Err(CompactEventRejectReason::ZeroSequence);
    }
    if timestamp_us(ts) == 0 {
        return Err(CompactEventRejectReason::ZeroTimestamp);
    }
    Ok(())
}

fn pack_quote_condition_tokens(
    conditions: &[u16],
    indicators: &[u16],
    references: &CompactEventReferences,
) -> [u8; CONDITION_TOKEN_SLOTS] {
    let mut tokens = [0u8; CONDITION_TOKEN_SLOTS];
    for slot in 0..4 {
        if let Some(value) = conditions.get(slot) {
            tokens[slot] = references.quote_condition_id(*value);
        }
    }
    if let Some(value) = indicators.first() {
        tokens[4] = references.quote_indicator_id(*value);
    }
    tokens
}

fn pack_trade_condition_tokens(
    conditions: &[u16],
    references: &CompactEventReferences,
) -> [u8; CONDITION_TOKEN_SLOTS] {
    let mut tokens = [0u8; CONDITION_TOKEN_SLOTS];
    for slot in 0..CONDITION_TOKEN_SLOTS {
        if let Some(value) = conditions.get(slot) {
            tokens[slot] = references.trade_condition_id(*value);
        }
    }
    tokens
}

fn condition_issue(
    conditions: &[u16],
    indicators: &[u16],
    raw_tape: u8,
    tokens: [u8; CONDITION_TOKEN_SLOTS],
    references: &CompactEventReferences,
    quote: bool,
) -> Option<CompactEventIssue> {
    let overflow = if quote {
        conditions.len() > 4 || indicators.len() > 1
    } else {
        conditions.len() > CONDITION_TOKEN_SLOTS
    };
    let unknown = if quote {
        conditions
            .iter()
            .take(4)
            .any(|value| references.quote_condition_id(*value) == 0)
            || indicators
                .iter()
                .take(1)
                .any(|value| references.quote_indicator_id(*value) == 0)
    } else {
        conditions
            .iter()
            .take(CONDITION_TOKEN_SLOTS)
            .any(|value| references.trade_condition_id(*value) == 0)
    };
    let unknown_tape = !references.tapes.contains_key(&raw_tape);
    if !overflow && !unknown && !unknown_tape {
        return None;
    }
    Some(CompactEventIssue {
        issue_kind: if overflow {
            "condition_token_overflow"
        } else if unknown {
            "unknown_condition_token"
        } else {
            "unknown_tape_reference"
        },
        condition_codes: conditions.to_vec(),
        indicator_codes: indicators.to_vec(),
        selected_tokens: tokens,
        raw_tape,
    })
}

fn event_meta(event_type: u8, primary_scale: u8, secondary_scale: u8, tape: u8) -> u8 {
    (event_type & 0x01)
        | ((primary_scale & 0x01) << 1)
        | ((secondary_scale & 0x01) << 2)
        | ((tape & 0x07) << 3)
}

fn encoded_price(price: f64) -> (u32, u8, bool) {
    if !price.is_finite() || price <= 0.0 {
        return (0, 0, false);
    }
    let cents = (price * 100.0).round_ties_even();
    let sub_cent = ((price * 100.0) - cents).abs() > 0.000_000_1;
    if price > MAX_PRECISE_PRICE && sub_cent {
        return (0, 0, false);
    }
    let scale = u8::from(price < 1.0 || (sub_cent && price <= MAX_PRECISE_PRICE));
    let multiplier = if scale == 1 { 10_000.0 } else { 100.0 };
    let encoded = (price * multiplier).round_ties_even();
    if !(0.0..=u32::MAX as f64).contains(&encoded) || encoded == 0.0 {
        return (0, 0, false);
    }
    (encoded as u32, scale, true)
}

fn decoded_price(value: u32, scale: u8) -> f64 {
    value as f64 / if scale == 1 { 10_000.0 } else { 100.0 }
}

fn encode_u8(value: u16) -> u8 {
    if value <= u8::MAX as u16 {
        value as u8
    } else {
        0
    }
}

fn timestamp_us(ts: DateTime<Utc>) -> u64 {
    ts.timestamp_micros().max(0) as u64
}

fn sip_us_to_datetime(us: u64) -> Option<DateTime<Utc>> {
    let seconds = (us / 1_000_000) as i64;
    let nanos = ((us % 1_000_000) * 1_000) as u32;
    Utc.timestamp_opt(seconds, nanos).single()
}

fn merge_tree_settings(storage_policy: &str) -> String {
    if storage_policy.trim().is_empty() {
        "SETTINGS index_granularity = 8192".to_string()
    } else {
        format!(
            "SETTINGS index_granularity = 8192, storage_policy = '{}'",
            storage_policy.trim().replace('\'', "\\'")
        )
    }
}

async fn clickhouse_query(
    client: &Client,
    base_url: &str,
    user: &str,
    password: &str,
    database: Option<&str>,
    body: &str,
) -> Result<String, String> {
    let url = match database {
        Some(database) => format!(
            "{}/?database={}",
            base_url.trim_end_matches('/'),
            urlencoding::encode(database)
        ),
        None => format!("{}/", base_url.trim_end_matches('/')),
    };
    let mut request = client
        .post(url)
        .header("Content-Type", "text/plain; charset=utf-8")
        .header("X-ClickHouse-User", user)
        .body(body.to_string());
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

fn escape_sql_string(value: &str) -> String {
    value.replace('\\', "\\\\").replace('\'', "\\'")
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn references() -> CompactEventReferences {
        CompactEventReferences {
            quote_conditions: [(12, 11), (16, 12)].into_iter().collect(),
            trade_conditions: [(2, 21), (5, 22)].into_iter().collect(),
            trade_updates: [
                (0, TradeUpdateRule::regular()),
                (2, TradeUpdateRule::regular()),
                (5, TradeUpdateRule::regular()),
            ]
            .into_iter()
            .collect(),
            quote_indicators: [(7, 31)].into_iter().collect(),
            tapes: [(1, 0), (2, 1), (3, 2)].into_iter().collect(),
        }
    }

    fn compact_quote_at(timestamp: DateTime<Utc>, sequence: u64) -> LiveCompactEvent {
        compact_quote_event(
            &QuoteEvent {
                ask_exchange: 11,
                ask_price: 10.1,
                ask_size: 10,
                bid_exchange: 12,
                bid_price: 10.0,
                bid_size: 20,
                conditions: vec![],
                indicators: vec![],
                ingest_ts: timestamp,
                raw: serde_json::Value::Null,
                sequence,
                tape: 1,
                ticker: "TEST".to_string(),
                ts: timestamp,
            },
            &references(),
        )
        .unwrap()
        .event
    }

    fn market_quote(sequence: u64) -> MarketEvent {
        let timestamp = Utc.with_ymd_and_hms(2026, 8, 11, 18, 0, 0).unwrap();
        MarketEvent::Quote(QuoteEvent {
            ask_exchange: 11,
            ask_price: 10.1,
            ask_size: 10,
            bid_exchange: 12,
            bid_price: 10.0,
            bid_size: 20,
            conditions: vec![],
            indicators: vec![],
            ingest_ts: timestamp,
            raw: serde_json::Value::Null,
            sequence,
            tape: 1,
            ticker: "TEST".to_string(),
            ts: timestamp,
        })
    }

    #[tokio::test]
    async fn compact_input_merger_drains_live_before_ready_repair() {
        let (live_sender, live_receiver) = mpsc::channel(2);
        let (repair_sender, repair_receiver) = mpsc::channel(2);
        let (priority_sender, mut priority_receiver) = mpsc::channel(1);
        repair_sender.send(market_quote(1)).await.unwrap();
        live_sender.send(market_quote(2)).await.unwrap();
        drop(live_sender);
        drop(repair_sender);

        let task = tokio::spawn(merge_compact_inputs_live_first(
            live_receiver,
            repair_receiver,
            priority_sender,
            SharedMetrics::new(),
        ));
        let first = priority_receiver.recv().await.unwrap();
        let second = priority_receiver.recv().await.unwrap();
        assert_eq!(
            match first {
                MarketEvent::Quote(row) => row.sequence,
                MarketEvent::Trade(row) => row.sequence,
            },
            2
        );
        assert_eq!(
            match second {
                MarketEvent::Quote(row) => row.sequence,
                MarketEvent::Trade(row) => row.sequence,
            },
            1
        );
        task.await.unwrap();
    }

    #[test]
    fn coverage_groups_preserve_utc_partition_and_new_york_session() {
        let evening = Utc.with_ymd_and_hms(2026, 1, 3, 0, 30, 0).unwrap();
        let regular = Utc.with_ymd_and_hms(2026, 1, 3, 15, 30, 0).unwrap();
        let rows = [compact_quote_at(evening, 1), compact_quote_at(regular, 2)];

        let groups = compact_coverage_groups(&rows);

        assert_eq!(groups.len(), 2);
        assert!(groups.contains_key(&("2026-01-03".into(), "2026-01-02".into())));
        assert!(groups.contains_key(&("2026-01-03".into(), "2026-01-03".into())));
        assert_eq!(groups.values().map(|row| row.rows_written).sum::<u64>(), 2);
    }

    #[test]
    fn quote_sanitization_preserves_conditions() {
        let refs = references();
        let quote = QuoteEvent {
            ask_exchange: 11,
            ask_price: 9.0,
            ask_size: 0,
            bid_exchange: 12,
            bid_price: 10.0,
            bid_size: 20,
            conditions: vec![12, 16],
            indicators: vec![7],
            ingest_ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
            raw: serde_json::Value::Null,
            sequence: 44,
            tape: 3,
            ticker: "TEST".to_string(),
            ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
        };
        let converted = compact_quote_event(&quote, &refs).unwrap().event;
        assert_eq!(converted.price_primary_int, 0);
        assert_eq!(converted.price_secondary_int, 0);
        assert_eq!(converted.size_primary, 0.0);
        assert_eq!(converted.size_secondary, 20.0);
        assert_eq!(converted.condition_token_1, 11);
        assert_eq!(converted.condition_token_2, 12);
        assert_eq!(converted.condition_token_5, 31);
        assert_eq!((converted.event_meta >> 3) & 0x07, 2);
        match refs.decoder().decode(&converted) {
            MarketEvent::Quote(decoded) => {
                assert_eq!(decoded.conditions, vec![12, 16]);
                assert_eq!(decoded.indicators, vec![7]);
                assert_eq!(decoded.tape, 3);
                assert_eq!(decoded.raw["correlation_id"], converted.correlation_id());
                assert_eq!(decoded.raw["causation_id"], converted.causation_id());
            }
            MarketEvent::Trade(_) => panic!("expected quote"),
        }
    }

    #[test]
    fn source_lineage_is_bounded_and_stable_across_storage_ordinals() {
        let timestamp = Utc.timestamp_millis_opt(1_700_000_000_000).unwrap();
        let first = compact_quote_at(timestamp, 41);
        let mut historical = first.clone();
        historical.arrival_sequence = 9_999;

        assert_eq!(first.correlation_id(), historical.correlation_id());
        assert_eq!(first.causation_id(), historical.causation_id());
        assert!(first.correlation_id().starts_with("source:TEST:"));
        assert!(first.causation_id().starts_with("event:"));
        assert!(first.correlation_id().len() <= 128);
        assert!(first.causation_id().len() <= 128);
    }

    #[test]
    fn condition_overflow_is_audited_without_rejecting_event() {
        let mut refs = references();
        for code in 1..=6 {
            refs.trade_conditions.insert(code, code as u8);
        }
        let trade = TradeEvent {
            conditions: vec![1, 2, 3, 4, 5, 6],
            exchange: 4,
            ingest_ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
            participant_ts: None,
            price: 10.0,
            raw: serde_json::Value::Null,
            sequence: 9,
            size: 100.0,
            tape: 1,
            ticker: "TEST".to_string(),
            trade_id: "1".to_string(),
            trf_id: 0,
            trf_ts: None,
            ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
        };
        let converted = compact_trade_event(&trade, &refs).unwrap();
        assert_eq!(
            converted.issue.unwrap().issue_kind,
            "condition_token_overflow"
        );
        assert_eq!(converted.event.condition_token_5, 5);
    }

    #[test]
    fn price_encoding_uses_historical_ties_to_even_rounding() {
        assert_eq!(encoded_price(0.00025), (2, 1, true));
    }

    #[tokio::test]
    async fn bounded_page_reports_exact_cursor_eviction_and_forward_progress() {
        let store = SharedCompactEventStore::new(2);
        for arrival_sequence in [10, 20, 30] {
            let trade = TradeEvent {
                conditions: vec![],
                exchange: 4,
                ingest_ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
                participant_ts: None,
                price: 10.0,
                raw: serde_json::Value::Null,
                sequence: arrival_sequence,
                size: 100.0,
                tape: 1,
                ticker: "TEST".to_string(),
                trade_id: arrival_sequence.to_string(),
                trf_id: 0,
                trf_ts: None,
                ts: Utc
                    .timestamp_millis_opt(1_700_000_000_000 + arrival_sequence as i64)
                    .unwrap(),
            };
            let mut event = compact_trade_event(&trade, &references()).unwrap().event;
            event.arrival_sequence = arrival_sequence;
            store.push(event).await;
        }

        let expired = store.page_after("test", 5, 10).await;
        assert!(expired.cursor_expired);
        assert_eq!(expired.buffer_start_arrival_sequence, 20);
        assert_eq!(expired.buffer_end_arrival_sequence, 30);
        assert_eq!(
            expired
                .events
                .iter()
                .map(|event| event.arrival_sequence)
                .collect::<Vec<_>>(),
            vec![20, 30]
        );
        assert_eq!(expired.next_after_arrival_sequence, 30);

        let continued = store.page_after("TEST", 20, 1).await;
        assert!(!continued.cursor_expired);
        assert!(!continued.has_more);
        assert_eq!(continued.events[0].arrival_sequence, 30);
        assert_eq!(continued.delivery_order, "arrival_sequence_ascending");

        let latest = store.latest_page("TEST", 1).await;
        assert_eq!(latest.events[0].arrival_sequence, 30);
        assert!(latest.truncated_before);
        assert!(!latest.has_more);
    }

    #[tokio::test]
    async fn market_page_is_bounded_filterable_and_reports_evicted_window_rows() {
        let store = SharedCompactEventStore::new(2);
        for (ticker, arrival_sequence, offset_ms) in [
            ("AAPL", 10, 10),
            ("MSFT", 20, 20),
            ("AAPL", 30, 30),
            ("AAPL", 40, 40),
        ] {
            let trade = TradeEvent {
                conditions: vec![],
                exchange: 4,
                ingest_ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
                participant_ts: None,
                price: 10.0,
                raw: serde_json::Value::Null,
                sequence: arrival_sequence,
                size: 100.0,
                tape: 1,
                ticker: ticker.to_string(),
                trade_id: arrival_sequence.to_string(),
                trf_id: 0,
                trf_ts: None,
                ts: Utc
                    .timestamp_millis_opt(1_700_000_000_000 + offset_ms)
                    .unwrap(),
            };
            let mut event = compact_trade_event(&trade, &references()).unwrap().event;
            event.arrival_sequence = arrival_sequence;
            store.push(event).await;
        }

        let start_us = 1_700_000_000_000_000;
        let page = store
            .market_page_after(0, start_us, start_us + 100_000, &[], 2, None)
            .await;
        assert!(page.cursor_expired);
        assert!(page.has_more);
        assert_eq!(page.ticker_count, 2);
        assert_eq!(
            page.events
                .iter()
                .map(|event| event.arrival_sequence)
                .collect::<Vec<_>>(),
            vec![20, 30]
        );

        let late_trade = TradeEvent {
            conditions: vec![],
            exchange: 4,
            ingest_ts: Utc.timestamp_millis_opt(1_700_000_000_000).unwrap(),
            participant_ts: None,
            price: 10.0,
            raw: serde_json::Value::Null,
            sequence: 50,
            size: 100.0,
            tape: 1,
            ticker: "AAPL".to_string(),
            trade_id: "50".to_string(),
            trf_id: 0,
            trf_ts: None,
            ts: Utc.timestamp_millis_opt(1_700_000_000_050).unwrap(),
        };
        let mut late_event = compact_trade_event(&late_trade, &references())
            .unwrap()
            .event;
        late_event.arrival_sequence = 50;
        store.push(late_event).await;

        let continued = store
            .market_page_after(
                page.next_after_arrival_sequence,
                start_us,
                start_us + 100_000,
                &["AAPL".to_string()],
                2,
                Some(page.through_arrival_sequence),
            )
            .await;
        assert!(!continued.cursor_expired);
        assert_eq!(continued.ticker_count, 1);
        assert_eq!(continued.events[0].arrival_sequence, 40);
        assert_eq!(continued.through_arrival_sequence, 40);
        assert!(!continued.has_more);
    }
}
