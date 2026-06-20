use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::metrics::SharedMetrics;
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use std::fs;
use std::path::Path;
use tokio::sync::{broadcast, mpsc};
use tokio::time::{interval, Duration};

pub const LIVE_COMPACT_EVENT_SCHEMA_VERSION: u16 = 1;
pub const QUOTE_EVENT_TYPE: u8 = 0;
pub const TRADE_EVENT_TYPE: u8 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct LiveCompactEvent {
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
pub struct CompactEventReferences {
    quote_conditions: HashMap<i16, u8>,
    trade_conditions: HashMap<i16, u8>,
}

#[derive(Clone)]
pub struct CompactEventClickHouseWriter {
    client: Client,
    config: GatewayConfig,
    event_sender: broadcast::Sender<LiveCompactEvent>,
    metrics: SharedMetrics,
    references: CompactEventReferences,
}

impl CompactEventReferences {
    pub fn load(reference_dir: &str) -> Result<Self, String> {
        let path = Path::new(reference_dir).join("conditions_indicators_glossary.json");
        let text = fs::read_to_string(&path)
            .map_err(|error| format!("could not read Massive condition glossary {}: {error}", path.display()))?;
        let payload: Value = serde_json::from_str(&text)
            .map_err(|error| format!("could not parse Massive condition glossary {}: {error}", path.display()))?;
        Ok(Self {
            quote_conditions: load_condition_table(&payload, "quote_conditions")?,
            trade_conditions: load_condition_table(&payload, "trade_conditions")?,
        })
    }

    fn quote_condition_id(&self, value: u16) -> u8 {
        self.quote_conditions.get(&(value as i16)).copied().unwrap_or(0)
    }

    fn trade_condition_id(&self, value: u16) -> u8 {
        self.trade_conditions.get(&(value as i16)).copied().unwrap_or(0)
    }
}

impl CompactEventClickHouseWriter {
    pub fn new(
        config: GatewayConfig,
        references: CompactEventReferences,
        event_sender: broadcast::Sender<LiveCompactEvent>,
        metrics: SharedMetrics,
    ) -> Self {
        Self {
            client: Client::new(),
            config,
            event_sender,
            metrics,
            references,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        if !self.config.persist_compact_events {
            return Ok(());
        }
        self.execute(
            &format!("CREATE DATABASE IF NOT EXISTS `{}`", self.config.clickhouse_database),
            false,
        )
        .await?;
        self.execute(&self.create_table_sql(), true).await
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<MarketEvent>) {
        let mut batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut ordinals = match self.load_latest_ordinals().await {
            Ok(values) => values,
            Err(error) => {
                eprintln!("Compact event ordinal bootstrap failed; starting from empty state: {error}");
                HashMap::new()
            }
        };
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                event = receiver.recv() => {
                    match event {
                        Some(event) => {
                            match compact_event_from_market_event(&event, &self.references) {
                                Some(mut compact) => {
                                    let next = ordinals.entry(compact.ticker.clone()).or_insert(0);
                                    *next += 1;
                                    compact.ordinal = *next;
                                    if self.event_sender.send(compact.clone()).is_err() {
                                        self.metrics.inc_compact_event_broadcast_dropped();
                                    }
                                    self.metrics.inc_compact_events_emitted(1);
                                    batch.push(compact);
                                    if batch.len() >= self.config.max_clickhouse_batch {
                                        self.flush(&mut batch).await;
                                    }
                                }
                                None => self.metrics.inc_compact_event_rejected(),
                            }
                        }
                        None => {
                            self.flush(&mut batch).await;
                            return;
                        }
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut batch).await;
                }
            }
        }
    }

    async fn flush(&self, rows: &mut Vec<LiveCompactEvent>) {
        if rows.is_empty() {
            return;
        }
        let rows = std::mem::take(rows);
        if !self.config.persist_compact_events {
            return;
        }
        match self.insert_events(&rows).await {
            Ok(()) => self.metrics.inc_compact_events_persisted(rows.len() as u64),
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
                    "ingest_ts": event.ingest_ts.to_rfc3339(),
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
            &format!("INSERT INTO {} FORMAT JSONEachRow", self.config.compact_event_table),
            body,
        )
        .await
    }

    async fn load_latest_ordinals(&self) -> Result<HashMap<String, u64>, String> {
        if !self.config.persist_compact_events {
            return Ok(HashMap::new());
        }
        let rows = self
            .query(
                &format!(
                    "SELECT ticker, max(ordinal) FROM {} GROUP BY ticker FORMAT TSV",
                    self.config.compact_event_table
                ),
                true,
            )
            .await?;
        let mut out = HashMap::new();
        for row in rows.lines() {
            let mut parts = row.split('\t');
            let Some(ticker) = parts.next() else {
                continue;
            };
            let Some(ordinal) = parts.next() else {
                continue;
            };
            if let Ok(value) = ordinal.parse::<u64>() {
                out.insert(ticker.to_string(), value);
            }
        }
        Ok(out)
    }

    fn create_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                event_date Date,
                schema_version UInt16,
                ingest_ts DateTime64(3, 'UTC'),
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

    async fn execute(&self, sql: &str, use_database: bool) -> Result<(), String> {
        self.query(sql, use_database).await.map(|_| ())
    }

    async fn query_with_body(&self, sql: &str, body: String) -> Result<(), String> {
        self.query(&format!("{sql}\n{body}"), true).await.map(|_| ())
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

fn compact_quote_event(quote: &QuoteEvent, references: &CompactEventReferences) -> Option<LiveCompactEvent> {
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

fn compact_trade_event(trade: &TradeEvent, references: &CompactEventReferences) -> Option<LiveCompactEvent> {
    if trade.ticker.is_empty() || trade.sequence == 0 || trade.price <= 0.0 || trade.size <= 0.0 {
        return None;
    }
    let (price_int, price_scale) = scaled_price(trade.price)?;
    Some(LiveCompactEvent {
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
