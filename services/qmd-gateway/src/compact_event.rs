use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::metrics::SharedMetrics;
use crate::timefmt::clickhouse_datetime64;
use chrono::{DateTime, Datelike, TimeZone, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::fs;
use std::path::Path;
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio::time::{interval, Duration, Instant};

pub const LIVE_COMPACT_EVENT_SCHEMA_VERSION: u16 = 3;
pub const QUOTE_EVENT_TYPE: u8 = 0;
pub const TRADE_EVENT_TYPE: u8 = 1;
const CONDITION_TOKEN_SLOTS: usize = 5;

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

impl LiveCompactEvent {
    fn event_type(&self) -> u8 {
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
            event_type: event.event_type(),
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
    quote_indicators: HashMap<i16, u8>,
}

#[derive(Clone)]
pub struct CompactEventClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    event_sender: broadcast::Sender<LiveCompactEvent>,
    ensured_event_tables: Arc<RwLock<HashSet<String>>>,
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
        Self::from_glossary_payload(&payload)
    }

    fn from_glossary_payload(payload: &Value) -> Result<Self, String> {
        let mut quote_conditions = HashMap::new();
        let mut trade_conditions = HashMap::new();
        let mut quote_indicators: HashMap<i16, u8> = HashMap::new();
        let mut seen_join_keys: HashSet<(String, i16)> = HashSet::new();
        let mut token_id: u16 = 1;
        for kind in [
            "quote_conditions",
            "trade_conditions",
            "trade_corrections_nyse",
            "financial_status",
            "cta_security_status",
            "halt_reason",
            "utp_security_status",
            "nbbo_indicators",
            "held_trade_indicators",
            "misc_indicators",
            "luld_indicators",
        ] {
            let rows = glossary_rows(payload, kind)?;
            for row in rows {
                let Some(modifier) = row.get("modifier_int").and_then(Value::as_i64) else {
                    token_id = token_id.saturating_add(1);
                    continue;
                };
                let modifier = modifier as i16;
                if token_id > u8::MAX as u16 {
                    return Err(format!(
                        "unified compact-event token id overflow: {token_id}"
                    ));
                }
                let token = token_id as u8;
                if seen_join_keys.insert((kind.to_string(), modifier)) {
                    match kind {
                        "quote_conditions" => {
                            quote_conditions.insert(modifier, token);
                        }
                        "trade_conditions" => {
                            trade_conditions.insert(modifier, token);
                        }
                        "trade_corrections_nyse" => {}
                        _ => {
                            quote_indicators
                                .entry(modifier)
                                .and_modify(|current| *current = (*current).min(token))
                                .or_insert(token);
                        }
                    }
                }
                token_id = token_id.saturating_add(1);
            }
        }
        Ok(Self {
            quote_conditions,
            trade_conditions,
            quote_indicators,
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

    fn quote_indicator_id(&self, value: u16) -> u8 {
        self.quote_indicators
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
            ensured_event_tables: Arc::new(RwLock::new(HashSet::new())),
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
        let current_table = self.compact_event_table_for_year(Utc::now().year());
        self.ensure_compact_event_table(&current_table).await?;
        self.execute(&self.create_continuity_table_sql(), true)
            .await?;
        self.execute(&self.create_live_coverage_table_sql(), true)
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
                                Ok(mut compact) => {
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
                                Err(reason) => record_compact_event_rejection(&self.metrics, reason),
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
                state.last_event_type = event.event_type();
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
                self.record_live_event_coverage("compact_persisted", rows, "", 0)
                    .await;
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
            Err(error) => {
                eprintln!("ClickHouse compact event insert failed: {error}");
                self.record_live_event_coverage("failed", rows, &error, rows.len() as u64)
                    .await;
            }
        }
    }

    async fn insert_events(&self, rows: &[LiveCompactEvent]) -> Result<(), String> {
        let mut by_table: BTreeMap<String, Vec<&LiveCompactEvent>> = BTreeMap::new();
        for event in rows {
            by_table
                .entry(self.compact_event_table_for_date(&event.event_date))
                .or_default()
                .push(event);
        }
        for (table, table_rows) in by_table {
            self.ensure_compact_event_table(&table).await?;
            let body = table_rows
                .iter()
                .map(|event| {
                    json!({
                        "event_date": event.event_date,
                        "schema_version": event.schema_version,
                        "ingest_ts": clickhouse_datetime64(&event.ingest_ts),
                        "arrival_sequence": event.arrival_sequence,
                        "ticker": event.ticker,
                        "ordinal": event.ordinal,
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
            self.query_with_body(&format!("INSERT INTO {table} FORMAT JSONEachRow"), body)
                .await?;
        }
        Ok(())
    }

    async fn load_ordinal_state(&self) -> Result<HashMap<String, OrdinalState>, String> {
        if !self.config.persist_compact_events {
            return Ok(HashMap::new());
        }
        let event_tables = self.compact_event_source_tables().await?;
        let mut out = HashMap::new();
        for table in event_tables {
            let event_rows = self
                .query(
                    &format!(
                        "SELECT ticker, max(ordinal), argMax(sip_timestamp_us, ordinal), argMax(source_sequence, ordinal), argMax(bitAnd(event_meta, 1), ordinal) FROM {table} GROUP BY ticker FORMAT TSV",
                    ),
                    true,
                )
                .await
                .unwrap_or_default();
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
                    let replace = out
                        .get(ticker)
                        .and_then(|state: &OrdinalState| state.last_ordinal)
                        .map(|current| value > current)
                        .unwrap_or(true);
                    if replace {
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
        let mut max_value = 0u64;
        for table in self.compact_event_source_tables().await? {
            let row = self
                .query(
                    &format!("SELECT max(arrival_sequence) FROM {table} FORMAT TSV"),
                    true,
                )
                .await
                .unwrap_or_default();
            max_value = max_value.max(row.trim().parse::<u64>().unwrap_or(0));
        }
        Ok(max_value)
    }

    async fn ensure_compact_event_table(&self, table: &str) -> Result<(), String> {
        {
            let guard = self.ensured_event_tables.read().await;
            if guard.contains(table) {
                return Ok(());
            }
        }
        self.execute(&self.create_table_sql_for(table), true)
            .await?;
        self.execute(
            &format!(
                "ALTER TABLE {table} ADD COLUMN IF NOT EXISTS arrival_sequence UInt64 CODEC(T64, ZSTD(1)) AFTER ingest_ts"
            ),
            true,
        )
        .await?;
        let mut guard = self.ensured_event_tables.write().await;
        guard.insert(table.to_string());
        Ok(())
    }

    fn create_table_sql_for(&self, table: &str) -> String {
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
            ENGINE = MergeTree
            PARTITION BY toYYYYMM(event_date)
            ORDER BY (ticker, ordinal)
            {settings}
            "#,
            table = table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
    }

    fn compact_events_use_yearly_tables(&self) -> bool {
        self.config.compact_event_table == "events"
            || self.config.compact_event_table.contains("{year}")
    }

    fn compact_event_table_for_year(&self, year: i32) -> String {
        if self.config.compact_event_table == "events" {
            format!("events_{year}")
        } else if self.config.compact_event_table.contains("{year}") {
            self.config
                .compact_event_table
                .replace("{year}", &year.to_string())
        } else {
            self.config.compact_event_table.clone()
        }
    }

    fn compact_event_table_for_date(&self, event_date: &str) -> String {
        let year = event_date
            .get(0..4)
            .and_then(|value| value.parse::<i32>().ok())
            .unwrap_or_else(|| Utc::now().year());
        self.compact_event_table_for_year(year)
    }

    async fn compact_event_source_tables(&self) -> Result<Vec<String>, String> {
        if !self.compact_events_use_yearly_tables() {
            return Ok(vec![self.config.compact_event_table.clone()]);
        }
        let rows = self
            .query(
                "SELECT name FROM system.tables WHERE database = currentDatabase() AND match(name, '^events_[0-9]{4}$') ORDER BY name FORMAT TSV",
                true,
            )
            .await
            .unwrap_or_default();
        let mut tables = rows
            .lines()
            .map(str::trim)
            .filter(|value| !value.is_empty())
            .map(str::to_string)
            .collect::<Vec<_>>();
        let current = self.compact_event_table_for_year(Utc::now().year());
        if !tables.iter().any(|table| table == &current) {
            tables.push(current);
        }
        Ok(tables)
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
    ) {
        if rows.is_empty() {
            return;
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
        let coverage_start = started_at.min(min_ts);
        let completed_at = if status == "failed" {
            Some(clickhouse_datetime64(&now))
        } else {
            None
        };
        let row = json!({
            "coverage_kind": "q_live_events",
            "coverage_id": format!("compact_{}", self.config.qmd_run_id),
            "source": "qmd_compact_event_writer",
            "status": status,
            "coverage_start_utc": clickhouse_datetime64(&coverage_start),
            "coverage_end_utc": clickhouse_datetime64(&max_ts),
            "rows_written": rows.len() as u64,
            "event_rows": rows.len() as u64,
            "bar_rows": 0u64,
            "error_count": error_count,
            "started_at_utc": clickhouse_datetime64(&started_at),
            "updated_at_utc": clickhouse_datetime64(&now),
            "completed_at_utc": completed_at,
            "metadata_json": json!({
                "run_id": self.config.qmd_run_id,
                "error": error,
                "raw_trade_quote_tables": "not_in_persistence_contract",
                "bars": "coverage_not_complete_until_bar_writer_confirms",
            }).to_string(),
        });
        let result = self
            .query(
                &format!(
                    "INSERT INTO {} FORMAT JSONEachRow\n{}",
                    self.config.qmd_live_event_coverage_table, row
                ),
                true,
            )
            .await;
        if let Err(error) = result {
            eprintln!("ClickHouse qmd live coverage update failed: {error}");
        }
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
) -> Result<LiveCompactEvent, CompactEventRejectReason> {
    match event {
        MarketEvent::Quote(quote) => compact_quote_event(quote, references),
        MarketEvent::Trade(trade) => compact_trade_event(trade, references),
    }
}

#[derive(Clone, Copy, Debug)]
pub enum CompactEventRejectReason {
    EmptyTicker,
    ZeroSequence,
    BadQuotePrice,
    CrossedQuote,
    ZeroQuoteSize,
    BadTradePrice,
    BadTradeSize,
    BadPriceScale,
}

fn record_compact_event_rejection(metrics: &SharedMetrics, reason: CompactEventRejectReason) {
    match reason {
        CompactEventRejectReason::EmptyTicker => metrics.inc_compact_event_rejected_empty_ticker(),
        CompactEventRejectReason::ZeroSequence => {
            metrics.inc_compact_event_rejected_zero_sequence()
        }
        CompactEventRejectReason::BadQuotePrice => {
            metrics.inc_compact_event_rejected_bad_quote_price()
        }
        CompactEventRejectReason::CrossedQuote => {
            metrics.inc_compact_event_rejected_crossed_quote()
        }
        CompactEventRejectReason::ZeroQuoteSize => {
            metrics.inc_compact_event_rejected_zero_quote_size()
        }
        CompactEventRejectReason::BadTradePrice => {
            metrics.inc_compact_event_rejected_bad_trade_price()
        }
        CompactEventRejectReason::BadTradeSize => {
            metrics.inc_compact_event_rejected_bad_trade_size()
        }
        CompactEventRejectReason::BadPriceScale => {
            metrics.inc_compact_event_rejected_bad_price_scale()
        }
    }
}

fn compact_quote_event(
    quote: &QuoteEvent,
    references: &CompactEventReferences,
) -> Result<LiveCompactEvent, CompactEventRejectReason> {
    if quote.ticker.is_empty() {
        return Err(CompactEventRejectReason::EmptyTicker);
    }
    if quote.sequence == 0 {
        return Err(CompactEventRejectReason::ZeroSequence);
    }
    if quote.bid_price <= 0.0 || quote.ask_price <= 0.0 {
        return Err(CompactEventRejectReason::BadQuotePrice);
    }
    if quote.ask_price < quote.bid_price {
        return Err(CompactEventRejectReason::CrossedQuote);
    }
    if quote.bid_size == 0 || quote.ask_size == 0 {
        return Err(CompactEventRejectReason::ZeroQuoteSize);
    }
    let (ask_int, ask_scale) =
        scaled_price(quote.ask_price).ok_or(CompactEventRejectReason::BadPriceScale)?;
    let (bid_int, bid_scale) =
        scaled_price(quote.bid_price).ok_or(CompactEventRejectReason::BadPriceScale)?;
    Ok(LiveCompactEvent {
        arrival_sequence: 0,
        condition_token_1: 0,
        condition_token_2: 0,
        condition_token_3: 0,
        condition_token_4: 0,
        condition_token_5: 0,
        event_date: quote.ts.date_naive().to_string(),
        event_meta: event_meta(QUOTE_EVENT_TYPE, ask_scale, bid_scale, quote.tape),
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
    }
    .with_condition_tokens(pack_quote_condition_tokens(
        &quote.conditions,
        &quote.indicators,
        references,
    )))
}

fn compact_trade_event(
    trade: &TradeEvent,
    references: &CompactEventReferences,
) -> Result<LiveCompactEvent, CompactEventRejectReason> {
    if trade.ticker.is_empty() {
        return Err(CompactEventRejectReason::EmptyTicker);
    }
    if trade.sequence == 0 {
        return Err(CompactEventRejectReason::ZeroSequence);
    }
    if trade.price <= 0.0 {
        return Err(CompactEventRejectReason::BadTradePrice);
    }
    if trade.size <= 0.0 {
        return Err(CompactEventRejectReason::BadTradeSize);
    }
    let (price_int, price_scale) =
        scaled_price(trade.price).ok_or(CompactEventRejectReason::BadPriceScale)?;
    Ok(LiveCompactEvent {
        arrival_sequence: 0,
        condition_token_1: 0,
        condition_token_2: 0,
        condition_token_3: 0,
        condition_token_4: 0,
        condition_token_5: 0,
        event_date: trade.ts.date_naive().to_string(),
        event_meta: event_meta(TRADE_EVENT_TYPE, price_scale, 0, trade.tape),
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
    }
    .with_condition_tokens(pack_trade_condition_tokens(&trade.conditions, references)))
}

fn glossary_rows(payload: &Value, table: &str) -> Result<Vec<Value>, String> {
    let rows = payload
        .get("tables")
        .and_then(|tables| tables.get(table))
        .and_then(|table| table.get("rows"))
        .and_then(Value::as_array)
        .ok_or_else(|| format!("missing {table} rows in Massive condition glossary"))?;
    let mut out = rows.clone();
    out.sort_by(|left, right| {
        let left_key = (
            left.get("source_row").and_then(Value::as_i64).unwrap_or(0),
            left.get("modifier_int")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            left.get("condition").and_then(Value::as_str).unwrap_or(""),
        );
        let right_key = (
            right.get("source_row").and_then(Value::as_i64).unwrap_or(0),
            right
                .get("modifier_int")
                .and_then(Value::as_i64)
                .unwrap_or(0),
            right.get("condition").and_then(Value::as_str).unwrap_or(""),
        );
        left_key.cmp(&right_key)
    });
    Ok(out)
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

fn event_meta(event_type: u8, primary_scale: u8, secondary_scale: u8, tape: u8) -> u8 {
    (event_type & 0x01)
        | ((primary_scale & 0x01) << 1)
        | ((secondary_scale & 0x01) << 2)
        | ((tape & 0x07) << 3)
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
