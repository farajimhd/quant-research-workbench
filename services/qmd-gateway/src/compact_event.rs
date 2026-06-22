use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio::time::{interval, Duration, Instant};

pub const LIVE_COMPACT_EVENT_SCHEMA_VERSION: u16 = 1;
pub const QUOTE_EVENT_TYPE: u8 = 0;
pub const TRADE_EVENT_TYPE: u8 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct LiveCompactEvent {
    pub arrival_sequence: u64,
    pub conditions_packed: u32,
    pub event_date: String,
    pub event_flags: u8,
    pub event_type: u8,
    pub exchange_primary: u8,
    pub exchange_secondary: u8,
    pub ingest_ts: DateTime<Utc>,
    pub issue_flags: u16,
    pub ordinal: u64,
    pub price_primary_int: u32,
    pub price_secondary_int: u32,
    pub schema_version: u16,
    pub sip_timestamp_us: u64,
    pub size_primary: f32,
    pub size_secondary: f32,
    pub source_sequence: u64,
    pub ticker: String,
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
}

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
struct EventSortKey {
    sip_timestamp_us: u64,
    source_sequence: u64,
    event_type: u8,
    arrival_sequence: u64,
}

impl Ord for EventSortKey {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        (
            self.sip_timestamp_us,
            self.source_sequence,
            self.event_type,
            self.arrival_sequence,
        )
            .cmp(&(
                other.sip_timestamp_us,
                other.source_sequence,
                other.event_type,
                other.arrival_sequence,
            ))
    }
}

impl PartialOrd for EventSortKey {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl EventSortKey {
    fn from_event(event: &LiveCompactEvent) -> Self {
        Self {
            sip_timestamp_us: event.sip_timestamp_us,
            source_sequence: event.source_sequence,
            event_type: event.event_type,
            arrival_sequence: event.arrival_sequence,
        }
    }
}

#[derive(Clone, Debug)]
struct OrdinalState {
    next_ordinal: u64,
    last_event_type: u8,
    last_ordinal: Option<u64>,
    last_sip_timestamp_us: u64,
    last_source_sequence: u64,
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

    fn len(&self) -> usize {
        self.events.len()
    }
}

#[derive(Clone)]
pub struct CompactEventReferences {
    quote_conditions: HashMap<i16, u8>,
    trade_conditions: HashMap<i16, u8>,
}

#[derive(Clone)]
pub struct CompactEventClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    event_sender: broadcast::Sender<LiveCompactEvent>,
    live_store: SharedCompactEventStore,
    metrics: SharedMetrics,
    references: CompactEventReferences,
}

impl CompactEventReferences {
    pub fn load(reference_dir: &str) -> Result<Self, String> {
        let path = Path::new(reference_dir).join("conditions_indicators_glossary.json");
        let text = fs::read_to_string(&path).map_err(|error| {
            format!(
                "could not read Massive condition glossary {}: {error}",
                path.display()
            )
        })?;
        let payload: Value = serde_json::from_str(&text).map_err(|error| {
            format!(
                "could not parse Massive condition glossary {}: {error}",
                path.display()
            )
        })?;
        Ok(Self {
            quote_conditions: load_condition_table(&payload, "quote_conditions")?,
            trade_conditions: load_condition_table(&payload, "trade_conditions")?,
        })
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
}

impl CompactEventClickHouseWriter {
    pub fn new(
        config: GatewayConfig,
        references: CompactEventReferences,
        event_sender: broadcast::Sender<LiveCompactEvent>,
        live_store: SharedCompactEventStore,
        metrics: SharedMetrics,
    ) -> Self {
        Self {
            client: Client::new(),
            config,
            event_sender,
            live_store,
            metrics,
            references,
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
        self.execute(&self.create_table_sql(), true).await?;
        self.execute(&self.create_continuity_table_sql(), true)
            .await?;
        self.execute(
            &format!(
                "ALTER TABLE {} ADD COLUMN IF NOT EXISTS arrival_sequence UInt64 CODEC(T64, ZSTD(1)) AFTER ingest_ts",
                self.config.compact_event_table
            ),
            true,
        )
        .await
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<MarketEvent>) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut ordinal_state = match self.load_ordinal_state().await {
            Ok(values) => values,
            Err(error) => {
                eprintln!(
                    "Compact event ordinal bootstrap failed; starting from empty state: {error}"
                );
                HashMap::new()
            }
        };
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
        let mut dirty_continuity_tickers: HashSet<String> = HashSet::new();
        let mut reorder_pending_count = 0u64;
        let reorder_lag_us = self
            .config
            .compact_event_reorder_lag_ms
            .saturating_mul(1_000);
        let mut last_force_flush = Instant::now();
        let mut last_continuity_flush = Instant::now();
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                event = receiver.recv() => {
                    match event {
                        Some(event) => {
                            match compact_event_from_market_event(&event, &self.references) {
                                Some(mut compact) => {
                                    arrival_sequence = arrival_sequence.saturating_add(1);
                                    compact.arrival_sequence = arrival_sequence;
                                    if self.event_sender.send(compact.clone()).is_err() {
                                        self.metrics.inc_compact_event_broadcast_dropped();
                                    }
                                    self.live_store.push(compact.clone()).await;
                                    self.metrics.inc_compact_events_emitted(1);
                                    if self.config.persist_compact_events {
                                        let ticker = compact.ticker.clone();
                                        let buffer = reorder_buffers.entry(ticker).or_default();
                                        if buffer.insert(compact) {
                                            self.metrics.inc_compact_event_reorder_late_arrival();
                                        }
                                        reorder_pending_count = reorder_pending_count.saturating_add(1);
                                        self.metrics.inc_compact_events_reorder_buffered(1);
                                        self.metrics.set_compact_events_reorder_pending(reorder_pending_count);
                                        self.drain_reorder_buffers(
                                            &mut reorder_buffers,
                                            &mut ordinal_state,
                                            &mut dirty_continuity_tickers,
                                            &mut batch,
                                            &mut reorder_pending_count,
                                            reorder_lag_us,
                                            false,
                                        );
                                        if batch.len() >= self.config.max_clickhouse_batch {
                                            self.flush_persisted(&mut batch, &ordinal_state, &mut dirty_continuity_tickers, &mut last_continuity_flush).await;
                                        }
                                    }
                                }
                                None => self.metrics.inc_compact_event_rejected(),
                            }
                        }
                        None => {
                            self.drain_reorder_buffers(
                                &mut reorder_buffers,
                                &mut ordinal_state,
                                &mut dirty_continuity_tickers,
                                &mut batch,
                                &mut reorder_pending_count,
                                reorder_lag_us,
                                true,
                            );
                            self.flush_persisted(&mut batch, &ordinal_state, &mut dirty_continuity_tickers, &mut last_continuity_flush).await;
                            self.flush_continuity(&ordinal_state, &mut dirty_continuity_tickers).await;
                            return;
                        }
                    }
                }
                _ = flush_interval.tick() => {
                    let force = last_force_flush.elapsed()
                        >= Duration::from_millis(self.config.compact_event_reorder_force_flush_ms);
                    if force {
                        last_force_flush = Instant::now();
                    }
                    self.drain_reorder_buffers(
                        &mut reorder_buffers,
                        &mut ordinal_state,
                        &mut dirty_continuity_tickers,
                        &mut batch,
                        &mut reorder_pending_count,
                        reorder_lag_us,
                        force,
                    );
                    self.flush_persisted(&mut batch, &ordinal_state, &mut dirty_continuity_tickers, &mut last_continuity_flush).await;
                }
            }
        }
    }

    fn drain_reorder_buffers(
        &self,
        reorder_buffers: &mut HashMap<String, TickerReorderBuffer>,
        ordinal_state: &mut HashMap<String, OrdinalState>,
        dirty_continuity_tickers: &mut HashSet<String>,
        batch: &mut Vec<LiveCompactEvent>,
        reorder_pending_count: &mut u64,
        reorder_lag_us: u64,
        force: bool,
    ) {
        if !self.config.persist_compact_events {
            return;
        }
        for (ticker, buffer) in reorder_buffers.iter_mut() {
            let mut ready = if force {
                buffer.drain_all()
            } else {
                let (ready, forced_by_size) = buffer.drain_ready(
                    reorder_lag_us,
                    self.config.compact_event_reorder_max_events_per_ticker,
                );
                if forced_by_size {
                    self.metrics.inc_compact_event_reorder_forced_flush();
                }
                ready
            };
            if ready.is_empty() {
                continue;
            }
            let state = ordinal_state
                .entry(ticker.clone())
                .or_insert_with(|| OrdinalState {
                    next_ordinal: 0,
                    last_event_type: 0,
                    last_ordinal: None,
                    last_sip_timestamp_us: 0,
                    last_source_sequence: 0,
                });
            for event in ready.iter_mut() {
                event.ordinal = state.next_ordinal;
                state.last_ordinal = Some(event.ordinal);
                state.last_sip_timestamp_us = event.sip_timestamp_us;
                state.last_source_sequence = event.source_sequence;
                state.last_event_type = event.event_type;
                state.next_ordinal = state.next_ordinal.saturating_add(1);
            }
            let ready_len = ready.len() as u64;
            self.metrics.inc_compact_events_reorder_flushed(ready_len);
            *reorder_pending_count = reorder_pending_count.saturating_sub(ready_len);
            dirty_continuity_tickers.insert(ticker.clone());
            batch.extend(ready);
        }
        reorder_buffers.retain(|_, buffer| buffer.len() > 0);
        self.metrics
            .set_compact_events_reorder_pending(*reorder_pending_count);
    }

    async fn flush_persisted(
        &self,
        rows: &mut Vec<LiveCompactEvent>,
        ordinal_state: &HashMap<String, OrdinalState>,
        dirty_continuity_tickers: &mut HashSet<String>,
        last_continuity_flush: &mut Instant,
    ) {
        if rows.is_empty() {
            if last_continuity_flush.elapsed()
                >= Duration::from_millis(self.config.flush_interval_ms.saturating_mul(5))
            {
                self.flush_continuity(ordinal_state, dirty_continuity_tickers)
                    .await;
                *last_continuity_flush = Instant::now();
            }
            return;
        }
        if !self.config.persist_compact_events {
            return;
        }
        match self.insert_events(rows).await {
            Ok(()) => {
                self.metrics.inc_compact_events_persisted(rows.len() as u64);
                rows.clear();
                if last_continuity_flush.elapsed()
                    >= Duration::from_millis(self.config.flush_interval_ms.saturating_mul(5))
                {
                    self.flush_continuity(ordinal_state, dirty_continuity_tickers)
                        .await;
                    *last_continuity_flush = Instant::now();
                }
            }
            Err(error) => eprintln!("ClickHouse compact event insert failed: {error}"),
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
                    "ordinal": event.ordinal,
                    "event_type": event.event_type,
                    "sip_timestamp_us": event.sip_timestamp_us,
                    "price_primary_int": event.price_primary_int,
                    "price_secondary_int": event.price_secondary_int,
                    "size_primary": event.size_primary,
                    "size_secondary": event.size_secondary,
                    "exchange_primary": event.exchange_primary,
                    "exchange_secondary": event.exchange_secondary,
                    "event_flags": event.event_flags,
                    "conditions_packed": event.conditions_packed,
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

    async fn load_ordinal_state(&self) -> Result<HashMap<String, OrdinalState>, String> {
        if !self.config.persist_compact_events {
            return Ok(HashMap::new());
        }
        let event_rows = self
            .query(
                &format!(
                    "SELECT ticker, max(ordinal), argMax(sip_timestamp_us, ordinal), argMax(source_sequence, ordinal), argMax(event_type, ordinal) FROM {} GROUP BY ticker FORMAT TSV",
                    self.config.compact_event_table
                ),
                true,
            )
            .await?;
        let mut out = HashMap::new();
        for row in event_rows.lines() {
            let mut parts = row.split('\t');
            let Some(ticker) = parts.next() else {
                continue;
            };
            let Some(last_ordinal) = parts.next() else {
                continue;
            };
            let last_sip_timestamp_us = parts
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let last_source_sequence = parts
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let last_event_type = parts
                .next()
                .and_then(|value| value.parse::<u8>().ok())
                .unwrap_or(0);
            if let Ok(value) = last_ordinal.parse::<u64>() {
                out.insert(
                    ticker.to_string(),
                    OrdinalState {
                        next_ordinal: value.saturating_add(1),
                        last_event_type,
                        last_ordinal: Some(value),
                        last_sip_timestamp_us,
                        last_source_sequence,
                    },
                );
            }
        }
        let continuity_rows = self
            .query(
                &format!(
                    "SELECT ticker, argMax(next_ordinal, updated_at), argMax(last_ordinal, updated_at), argMax(last_sip_timestamp_us, updated_at), argMax(last_source_sequence, updated_at), argMax(last_event_type, updated_at) FROM {} GROUP BY ticker FORMAT TSV",
                    self.config.compact_event_continuity_table
                ),
                true,
            )
            .await
            .unwrap_or_default();
        for row in continuity_rows.lines() {
            let mut parts = row.split('\t');
            let Some(ticker) = parts.next() else {
                continue;
            };
            let continuity_next = parts
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let continuity_last = parts.next().and_then(|value| value.parse::<u64>().ok());
            let continuity_ts = parts
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let continuity_sequence = parts
                .next()
                .and_then(|value| value.parse::<u64>().ok())
                .unwrap_or(0);
            let continuity_type = parts
                .next()
                .and_then(|value| value.parse::<u8>().ok())
                .unwrap_or(0);
            let entry = out.entry(ticker.to_string()).or_insert(OrdinalState {
                next_ordinal: continuity_next,
                last_event_type: continuity_type,
                last_ordinal: continuity_last,
                last_sip_timestamp_us: continuity_ts,
                last_source_sequence: continuity_sequence,
            });
            if continuity_next > entry.next_ordinal {
                eprintln!(
                    "Compact event continuity for {ticker} is ahead of event rows; using event-table ordinal {} instead of continuity {}.",
                    entry.next_ordinal,
                    continuity_next
                );
            }
        }
        Ok(out)
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
                ordinal UInt64 CODEC(T64, ZSTD(1)),
                event_type UInt8,
                sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
                price_primary_int UInt32 CODEC(T64, ZSTD(1)),
                price_secondary_int UInt32 CODEC(T64, ZSTD(1)),
                size_primary Float32 CODEC(ZSTD(1)),
                size_secondary Float32 CODEC(ZSTD(1)),
                exchange_primary UInt8,
                exchange_secondary UInt8,
                event_flags UInt8,
                conditions_packed UInt32 CODEC(T64, ZSTD(1)),
                source_sequence UInt64 CODEC(T64, ZSTD(1)),
                issue_flags UInt16
            )
            ENGINE = MergeTree
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (ticker, ordinal)
            {settings}
            "#,
            table = self.config.compact_event_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    fn create_continuity_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                ticker LowCardinality(String),
                next_ordinal UInt64 CODEC(T64, ZSTD(1)),
                last_ordinal UInt64 CODEC(T64, ZSTD(1)),
                last_sip_timestamp_us UInt64 CODEC(DoubleDelta, ZSTD(1)),
                last_source_sequence UInt64 CODEC(T64, ZSTD(1)),
                last_event_type UInt8,
                updated_at DateTime64(3, 'UTC'),
                schema_version UInt16
            )
            ENGINE = MergeTree
            ORDER BY (ticker, updated_at)
            {settings}
            "#,
            table = self.config.compact_event_continuity_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    async fn flush_continuity(
        &self,
        ordinal_state: &HashMap<String, OrdinalState>,
        dirty_continuity_tickers: &mut HashSet<String>,
    ) {
        if dirty_continuity_tickers.is_empty() || !self.config.persist_compact_events {
            return;
        }
        let updated_at = clickhouse_datetime64(&Utc::now());
        let body = dirty_continuity_tickers
            .iter()
            .filter_map(|ticker| ordinal_state.get(ticker).map(|state| (ticker, state)))
            .filter_map(|(ticker, state)| {
                state.last_ordinal.map(|last_ordinal| {
                    json!({
                        "ticker": ticker,
                        "next_ordinal": state.next_ordinal,
                        "last_ordinal": last_ordinal,
                        "last_sip_timestamp_us": state.last_sip_timestamp_us,
                        "last_source_sequence": state.last_source_sequence,
                        "last_event_type": state.last_event_type,
                        "updated_at": updated_at,
                        "schema_version": LIVE_COMPACT_EVENT_SCHEMA_VERSION,
                    })
                    .to_string()
                })
            })
            .collect::<Vec<_>>()
            .join("\n");
        if body.is_empty() {
            dirty_continuity_tickers.clear();
            return;
        }
        match self
            .query_with_body(
                &format!(
                    "INSERT INTO {} FORMAT JSONEachRow",
                    self.config.compact_event_continuity_table
                ),
                body,
            )
            .await
        {
            Ok(()) => dirty_continuity_tickers.clear(),
            Err(error) => eprintln!("ClickHouse compact event continuity insert failed: {error}"),
        }
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

pub fn compact_event_from_market_event(
    event: &MarketEvent,
    references: &CompactEventReferences,
) -> Option<LiveCompactEvent> {
    match event {
        MarketEvent::Quote(quote) => compact_quote_event(quote, references),
        MarketEvent::Trade(trade) => compact_trade_event(trade, references),
    }
}

fn compact_quote_event(
    quote: &QuoteEvent,
    references: &CompactEventReferences,
) -> Option<LiveCompactEvent> {
    if quote.ticker.is_empty()
        || quote.sequence == 0
        || quote.bid_price <= 0.0
        || quote.ask_price <= 0.0
        || quote.ask_price < quote.bid_price
        || quote.bid_size == 0
        || quote.ask_size == 0
    {
        return None;
    }
    let (ask_int, ask_scale) = scaled_price(quote.ask_price)?;
    let (bid_int, bid_scale) = scaled_price(quote.bid_price)?;
    Some(LiveCompactEvent {
        arrival_sequence: 0,
        conditions_packed: pack_quote_conditions(&quote.conditions, references),
        event_date: quote.ts.date_naive().to_string(),
        event_flags: (ask_scale & 1) | ((bid_scale & 1) << 1) | ((quote.tape & 0x07) << 2),
        event_type: QUOTE_EVENT_TYPE,
        exchange_primary: quote.ask_exchange.min(u8::MAX as u16) as u8,
        exchange_secondary: quote.bid_exchange.min(u8::MAX as u16) as u8,
        ingest_ts: quote.ingest_ts,
        issue_flags: 0,
        ordinal: 0,
        price_primary_int: ask_int,
        price_secondary_int: bid_int,
        schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        sip_timestamp_us: timestamp_us(quote.ts),
        size_primary: quote.ask_size as f32,
        size_secondary: quote.bid_size as f32,
        source_sequence: quote.sequence,
        ticker: quote.ticker.clone(),
    })
}

fn compact_trade_event(
    trade: &TradeEvent,
    references: &CompactEventReferences,
) -> Option<LiveCompactEvent> {
    if trade.ticker.is_empty() || trade.sequence == 0 || trade.price <= 0.0 || trade.size <= 0.0 {
        return None;
    }
    let (price_int, price_scale) = scaled_price(trade.price)?;
    Some(LiveCompactEvent {
        arrival_sequence: 0,
        conditions_packed: pack_trade_conditions(&trade.conditions, references),
        event_date: trade.ts.date_naive().to_string(),
        event_flags: (price_scale & 1) | ((trade.tape & 0x07) << 2),
        event_type: TRADE_EVENT_TYPE,
        exchange_primary: trade.exchange.min(u8::MAX as u16) as u8,
        exchange_secondary: 0,
        ingest_ts: trade.ingest_ts,
        issue_flags: 0,
        ordinal: 0,
        price_primary_int: price_int,
        price_secondary_int: 0,
        schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        sip_timestamp_us: timestamp_us(trade.ts),
        size_primary: trade.size as f32,
        size_secondary: 0.0,
        source_sequence: trade.sequence,
        ticker: trade.ticker.clone(),
    })
}

fn load_condition_table(payload: &Value, table: &str) -> Result<HashMap<i16, u8>, String> {
    let rows = payload
        .get("tables")
        .and_then(|tables| tables.get(table))
        .and_then(|table| table.get("rows"))
        .and_then(Value::as_array)
        .ok_or_else(|| format!("missing {table} rows in Massive condition glossary"))?;
    let mut out = HashMap::new();
    for (index, row) in rows.iter().enumerate() {
        let Some(modifier) = row.get("modifier_int").and_then(Value::as_i64) else {
            continue;
        };
        let dense_id = (index + 1).min(u8::MAX as usize) as u8;
        out.insert(modifier as i16, dense_id);
    }
    Ok(out)
}

fn pack_quote_conditions(conditions: &[u16], references: &CompactEventReferences) -> u32 {
    let mut packed = 0u32;
    for slot in 0..4 {
        let dense_id = conditions
            .get(slot)
            .map(|value| references.quote_condition_id(*value))
            .unwrap_or(0);
        packed |= u32::from(dense_id) << (slot * 8);
    }
    packed
}

fn pack_trade_conditions(conditions: &[u16], references: &CompactEventReferences) -> u32 {
    let mut packed = 0u32;
    for slot in 0..5 {
        let dense_id = conditions
            .get(slot)
            .map(|value| references.trade_condition_id(*value) & 0x3F)
            .unwrap_or(0);
        packed |= u32::from(dense_id) << (slot * 6);
    }
    packed
}

fn scaled_price(price: f64) -> Option<(u32, u8)> {
    if !price.is_finite() || price <= 0.0 {
        return None;
    }
    let cents = (price * 100.0).round();
    let cents_price = cents / 100.0;
    let use_1e4 = price < 1.0 || (price - cents_price).abs() > 0.000_000_5;
    let scale = if use_1e4 { 1 } else { 0 };
    let multiplier = if use_1e4 { 10_000.0 } else { 100.0 };
    let value = (price * multiplier).round();
    if value < 0.0 || value > u32::MAX as f64 {
        return None;
    }
    Some((value as u32, scale))
}

fn timestamp_us(ts: DateTime<Utc>) -> u64 {
    ts.timestamp_micros().max(0) as u64
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
