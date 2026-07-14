use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::intraday_bars::IntradayBarRouter;
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, TimeZone, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio::time::{interval, sleep, Duration, Instant};

pub const LIVE_COMPACT_EVENT_SCHEMA_VERSION: u16 = 4;
pub const QUOTE_EVENT_TYPE: u8 = 0;
pub const TRADE_EVENT_TYPE: u8 = 1;
const CONDITION_TOKEN_SLOTS: usize = 5;
const MAX_PRECISE_PRICE: f64 = 429_496.7295;

#[derive(Clone, Debug, Serialize)]
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

#[derive(Clone)]
pub struct SharedCompactEventStore {
    capacity_per_ticker: usize,
    inner: Arc<RwLock<HashMap<String, VecDeque<LiveCompactEvent>>>>,
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
        let events = guard.entry(event.ticker.clone()).or_default();
        events.push_back(event);
        while events.len() > self.capacity_per_ticker {
            events.pop_front();
        }
    }

    pub async fn latest_sorted(&self, ticker: &str, limit: usize) -> Vec<LiveCompactEvent> {
        let guard = self.inner.read().await;
        let Some(events) = guard.get(&ticker.to_ascii_uppercase()) else {
            return Vec::new();
        };
        let mut out = events.iter().cloned().collect::<Vec<_>>();
        out.sort_by_key(EventSortKey::from_event);
        if out.len() > limit {
            out.split_off(out.len() - limit)
        } else {
            out
        }
    }

    pub async fn tickers(&self) -> Vec<String> {
        let guard = self.inner.read().await;
        let mut out = guard.keys().cloned().collect::<Vec<_>>();
        out.sort();
        out
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

#[derive(Clone)]
pub struct CompactEventClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    event_sender: broadcast::Sender<LiveCompactEvent>,
    live_store: SharedCompactEventStore,
    metrics: SharedMetrics,
    references: CompactEventReferences,
    intraday_bar_router: IntradayBarRouter,
}

impl CompactEventClickHouseWriter {
    pub fn new(
        config: GatewayConfig,
        references: CompactEventReferences,
        event_sender: broadcast::Sender<LiveCompactEvent>,
        live_store: SharedCompactEventStore,
        metrics: SharedMetrics,
        intraday_bar_router: IntradayBarRouter,
    ) -> Self {
        Self {
            client: Client::new(),
            config,
            event_sender,
            live_store,
            metrics,
            references,
            intraday_bar_router,
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

    pub async fn run(self, mut receiver: mpsc::Receiver<MarketEvent>) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut issue_batch = Vec::new();
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
                                    eprintln!(
                                        "Compact event warning: kind={} ticker={} sip_timestamp_us={} source_sequence={} conditions={} indicators={}",
                                        issue.issue_kind,
                                        conversion.event.ticker,
                                        conversion.event.sip_timestamp_us,
                                        conversion.event.source_sequence,
                                        issue.condition_codes.len(),
                                        issue.indicator_codes.len(),
                                    );
                                    issue_batch.push((conversion.event.clone(), issue));
                                }
                                if self.event_sender.send(conversion.event.clone()).is_err() {
                                    self.metrics.inc_compact_event_broadcast_dropped();
                                }
                                self.live_store.push(conversion.event.clone()).await;
                                if self.intraday_bar_router.send(conversion.event.clone()).await.is_err() {
                                    self.metrics.inc_intraday_bar_event_dropped();
                                    eprintln!("Canonical intraday bar receiver closed; could not route one compact event.");
                                }
                                self.metrics.inc_compact_events_emitted(1);
                                if self.config.persist_compact_events {
                                    let buffer = reorder_buffers.entry(conversion.event.ticker.clone()).or_default();
                                    if buffer.insert(conversion.event) {
                                        self.metrics.inc_compact_event_reorder_late_arrival();
                                    }
                                    reorder_pending_count = reorder_pending_count.saturating_add(1);
                                    self.metrics.inc_compact_events_reorder_buffered(1);
                                    self.metrics.set_compact_events_reorder_pending(reorder_pending_count);
                                    self.drain_reorder_buffers(
                                        &mut reorder_buffers,
                                        &mut batch,
                                        &mut reorder_pending_count,
                                        reorder_lag_us,
                                        false,
                                    );
                                    if batch.len() >= self.config.max_clickhouse_batch {
                                        self.flush_persisted(&mut batch).await;
                                        self.flush_issues(&mut issue_batch).await;
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
                            while !batch.is_empty() || !issue_batch.is_empty() {
                                self.flush_persisted(&mut batch).await;
                                self.flush_issues(&mut issue_batch).await;
                                if !batch.is_empty() || !issue_batch.is_empty() {
                                    sleep(Duration::from_millis(250)).await;
                                }
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
                    self.flush_persisted(&mut batch).await;
                    self.flush_issues(&mut issue_batch).await;
                }
            }
            self.metrics.set_lane_pending(
                "compact_events",
                reorder_pending_count
                    .saturating_add(batch.len() as u64)
                    .saturating_add(receiver.len() as u64),
            );
            self.metrics
                .set_lane_pending("compact_audit", issue_batch.len() as u64);
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

    async fn flush_persisted(&self, batch: &mut Vec<LiveCompactEvent>) {
        if batch.is_empty() || !self.config.persist_compact_events {
            return;
        }
        self.metrics
            .set_lane_pending("compact_events", batch.len() as u64);
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
                self.metrics.set_lane_pending("compact_events", 0);
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
        self.metrics
            .set_lane_pending("compact_audit", rows.len() as u64);
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
        self.metrics.set_lane_pending("compact_audit", 0);
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
        let min_ts = rows
            .iter()
            .filter_map(|row| sip_us_to_datetime(row.sip_timestamp_us))
            .min()
            .unwrap_or(now);
        let max_ts = rows
            .iter()
            .filter_map(|row| sip_us_to_datetime(row.sip_timestamp_us))
            .max()
            .unwrap_or(now);
        let started_at = self
            .config
            .qmd_run_started_at()
            .unwrap_or_else(|| min_ts.min(now));
        let row = json!({
            "coverage_kind": "q_live_events",
            "coverage_id": format!("compact_{}", self.config.qmd_run_id),
            "source": "qmd_compact_event_writer",
            "status": status,
            "coverage_start_utc": clickhouse_datetime64(&started_at.min(min_ts)),
            "coverage_end_utc": clickhouse_datetime64(&max_ts),
            "rows_written": rows.len(),
            "event_rows": rows.len(),
            "bar_rows": 0,
            "error_count": error_count,
            "started_at_utc": clickhouse_datetime64(&started_at),
            "updated_at_utc": clickhouse_datetime64(&now),
            "completed_at_utc": if status == "failed" { Some(clickhouse_datetime64(&now)) } else { None },
            "metadata_json": json!({"run_id": self.config.qmd_run_id, "error": error}).to_string(),
        });
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{row}",
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
            quote_indicators: [(7, 31)].into_iter().collect(),
            tapes: [(1, 0), (2, 1), (3, 2)].into_iter().collect(),
        }
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
            }
            MarketEvent::Trade(_) => panic!("expected quote"),
        }
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
}
