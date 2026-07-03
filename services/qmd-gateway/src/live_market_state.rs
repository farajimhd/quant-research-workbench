use crate::bars::BarRow;
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::metrics::SharedMetrics;
use crate::timefmt::{clickhouse_datetime64, clickhouse_datetime64_opt};
use chrono::{DateTime, Utc};
use reqwest::Client;
use serde::Serialize;
use serde_json::json;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, RwLock};
use tokio::time::{interval, Duration};

pub const LIVE_MARKET_STATE_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Serialize)]
pub struct LiveMarketStateSnapshot {
    pub as_of: DateTime<Utc>,
    pub active_count: usize,
    pub history_count: usize,
    pub active: Vec<LiveSymbolMarketStateEvent>,
    pub recent: Vec<LiveSymbolMarketStateEvent>,
}

#[derive(Clone, Debug, Serialize)]
pub struct TickerLiveMarketStateSnapshot {
    pub as_of: DateTime<Utc>,
    pub ticker: String,
    pub active: Vec<LiveSymbolMarketStateEvent>,
    pub recent: Vec<LiveSymbolMarketStateEvent>,
    pub is_live_tradable: bool,
    pub blocking_reasons: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct LiveSymbolMarketStateEvent {
    pub schema_version: u16,
    pub event_id: String,
    pub ticker: String,
    pub event_type: String,
    pub event_status: String,
    pub event_start_utc: DateTime<Utc>,
    pub event_end_utc: Option<DateTime<Utc>>,
    pub source_event_ts_utc: DateTime<Utc>,
    pub source_event_type: String,
    pub source_conditions: Vec<u16>,
    pub source_indicators: Vec<u16>,
    pub severity: String,
    pub is_live_tradability_blocking: bool,
    pub block_reason: Option<String>,
    pub evidence_json: String,
    pub source_run_id: String,
    pub inserted_at_utc: DateTime<Utc>,
}

#[derive(Clone)]
pub struct SharedLiveMarketStateStore {
    inner: Arc<RwLock<LiveMarketStateStore>>,
}

struct LiveMarketStateStore {
    active: HashMap<StateKey, LiveSymbolMarketStateEvent>,
    history: VecDeque<LiveSymbolMarketStateEvent>,
    history_limit: usize,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct StateKey {
    ticker: String,
    event_type: String,
}

#[derive(Clone)]
pub struct LiveMarketStateRouter {
    sender: mpsc::Sender<LiveMarketStateInput>,
}

#[derive(Clone, Debug)]
pub enum LiveMarketStateInput {
    Event(MarketEvent),
    Bar(BarRow),
}

impl LiveMarketStateRouter {
    pub async fn send_event(
        &self,
        event: MarketEvent,
    ) -> Result<(), mpsc::error::SendError<LiveMarketStateInput>> {
        self.sender.send(LiveMarketStateInput::Event(event)).await
    }

    pub async fn send_bar(
        &self,
        row: BarRow,
    ) -> Result<(), mpsc::error::SendError<LiveMarketStateInput>> {
        self.sender.send(LiveMarketStateInput::Bar(row)).await
    }
}

impl SharedLiveMarketStateStore {
    pub fn new(history_limit: usize) -> Self {
        Self {
            inner: Arc::new(RwLock::new(LiveMarketStateStore {
                active: HashMap::new(),
                history: VecDeque::with_capacity(history_limit.min(10_000)),
                history_limit,
            })),
        }
    }

    async fn apply(&self, event: LiveSymbolMarketStateEvent) {
        let mut store = self.inner.write().await;
        let key = StateKey {
            ticker: event.ticker.clone(),
            event_type: event.event_type.clone(),
        };
        match event.event_status.as_str() {
            "opened" | "updated" => {
                store.active.insert(key, event.clone());
            }
            "closed" => {
                store.active.remove(&key);
            }
            _ => {}
        }
        if event.event_status == "updated" {
            return;
        }
        store.history.push_back(event);
        while store.history.len() > store.history_limit {
            store.history.pop_front();
        }
    }

    pub async fn snapshot(&self, limit: usize) -> LiveMarketStateSnapshot {
        let store = self.inner.read().await;
        let mut active = store.active.values().cloned().collect::<Vec<_>>();
        active.sort_by(|left, right| {
            left.ticker
                .cmp(&right.ticker)
                .then(left.event_type.cmp(&right.event_type))
        });
        active.truncate(limit);
        let recent = store
            .history
            .iter()
            .rev()
            .take(limit)
            .cloned()
            .collect::<Vec<_>>();
        LiveMarketStateSnapshot {
            as_of: Utc::now(),
            active_count: store.active.len(),
            history_count: store.history.len(),
            active,
            recent,
        }
    }

    pub async fn ticker_snapshot(
        &self,
        ticker: &str,
        limit: usize,
    ) -> TickerLiveMarketStateSnapshot {
        let normalized = ticker.to_ascii_uppercase();
        let store = self.inner.read().await;
        let mut active = store
            .active
            .values()
            .filter(|event| event.ticker == normalized)
            .cloned()
            .collect::<Vec<_>>();
        active.sort_by(|left, right| left.event_type.cmp(&right.event_type));
        let recent = store
            .history
            .iter()
            .rev()
            .filter(|event| event.ticker == normalized)
            .take(limit)
            .cloned()
            .collect::<Vec<_>>();
        let blocking_reasons = active
            .iter()
            .filter(|event| event.is_live_tradability_blocking)
            .filter_map(|event| event.block_reason.clone())
            .collect::<Vec<_>>();
        TickerLiveMarketStateSnapshot {
            as_of: Utc::now(),
            ticker: normalized,
            active,
            recent,
            is_live_tradable: blocking_reasons.is_empty(),
            blocking_reasons,
        }
    }
}

pub fn spawn_live_market_state_service(
    config: GatewayConfig,
    store: SharedLiveMarketStateStore,
    metrics: SharedMetrics,
    event_sender: broadcast::Sender<LiveSymbolMarketStateEvent>,
) -> LiveMarketStateRouter {
    let (sender, receiver) =
        mpsc::channel::<LiveMarketStateInput>(config.live_market_state_channel_capacity.max(1));
    tokio::spawn(run_live_market_state_service(
        config,
        store,
        metrics,
        event_sender,
        receiver,
    ));
    LiveMarketStateRouter { sender }
}

async fn run_live_market_state_service(
    config: GatewayConfig,
    store: SharedLiveMarketStateStore,
    metrics: SharedMetrics,
    event_sender: broadcast::Sender<LiveSymbolMarketStateEvent>,
    mut receiver: mpsc::Receiver<LiveMarketStateInput>,
) {
    let writer = LiveMarketStateClickHouseWriter::new(config.clone());
    if let Err(error) = writer.initialize().await {
        eprintln!("qmd live-market-state ClickHouse preflight failed: {error}");
        return;
    }
    let mut active = HashMap::<StateKey, LiveSymbolMarketStateEvent>::new();
    let mut batch = Vec::<LiveSymbolMarketStateEvent>::new();
    let mut flush_interval = interval(Duration::from_millis(config.flush_interval_ms));
    loop {
        tokio::select! {
            input = receiver.recv() => {
                match input {
                    Some(input) => {
                        let transitions = evaluate_input(&config, &mut active, input);
                        for transition in transitions {
                            store.apply(transition.clone()).await;
                            if transition.event_status == "updated" {
                                continue;
                            }
                            if event_sender.send(transition.clone()).is_err() {
                                metrics.inc_live_market_state_broadcast_dropped();
                            }
                            batch.push(transition);
                            metrics.inc_live_market_state_emitted(1);
                        }
                        if batch.len() >= config.max_clickhouse_batch {
                            flush_live_market_state_batch(&writer, &metrics, &mut batch).await;
                        }
                    }
                    None => {
                        flush_live_market_state_batch(&writer, &metrics, &mut batch).await;
                        return;
                    }
                }
            }
            _ = flush_interval.tick() => {
                flush_live_market_state_batch(&writer, &metrics, &mut batch).await;
            }
        }
    }
}

async fn flush_live_market_state_batch(
    writer: &LiveMarketStateClickHouseWriter,
    metrics: &SharedMetrics,
    batch: &mut Vec<LiveSymbolMarketStateEvent>,
) {
    if batch.is_empty() {
        return;
    }
    match writer.insert(batch).await {
        Ok(()) => {
            metrics.inc_live_market_state_persisted(batch.len() as u64);
            batch.clear();
        }
        Err(error) => {
            metrics.inc_live_market_state_persist_failed();
            eprintln!("ClickHouse live-market-state insert failed: {error}");
        }
    }
}

fn evaluate_input(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    input: LiveMarketStateInput,
) -> Vec<LiveSymbolMarketStateEvent> {
    match input {
        LiveMarketStateInput::Event(event) => evaluate_market_event(config, active, event),
        LiveMarketStateInput::Bar(row) => evaluate_bar_row(config, active, row),
    }
}

fn evaluate_market_event(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    event: MarketEvent,
) -> Vec<LiveSymbolMarketStateEvent> {
    match event {
        MarketEvent::Trade(trade) => {
            let conditions = trade.conditions.clone();
            let ticker = trade.ticker.clone();
            let ts = trade.ts;
            let mut out = Vec::new();
            if intersects(&conditions, &config.live_market_state_trade_halt_conditions) {
                out.extend(open_or_update(
                    config,
                    active,
                    EventSpec::blocking("condition_halt", "critical", "trade_condition_halt"),
                    Evidence::new(ticker.clone(), "trade", ts, conditions.clone(), Vec::new())
                        .with_json(json!({"trade_id": trade.trade_id, "price": trade.price, "size": trade.size})),
                ));
            }
            if intersects(
                &conditions,
                &config.live_market_state_trade_resume_conditions,
            ) {
                out.extend(close_state(
                    config,
                    active,
                    &ticker,
                    "condition_halt",
                    Evidence::new(ticker.clone(), "trade", ts, conditions, Vec::new())
                        .with_json(json!({"trade_id": trade.trade_id, "price": trade.price, "size": trade.size})),
                ));
            }
            out
        }
        MarketEvent::Quote(quote) => {
            let conditions = quote.conditions.clone();
            let indicators = quote.indicators.clone();
            let ticker = quote.ticker.clone();
            let ts = quote.ts;
            let mut out = Vec::new();
            if intersects(&conditions, &config.live_market_state_quote_halt_conditions) {
                out.extend(open_or_update(
                    config,
                    active,
                    EventSpec::blocking("condition_halt", "critical", "quote_condition_halt"),
                    Evidence::new(
                        ticker.clone(),
                        "quote",
                        ts,
                        conditions.clone(),
                        indicators.clone(),
                    )
                    .with_json(json!({"bid": quote.bid_price, "ask": quote.ask_price})),
                ));
            }
            if intersects(
                &conditions,
                &config.live_market_state_quote_resume_conditions,
            ) {
                out.extend(close_state(
                    config,
                    active,
                    &ticker,
                    "condition_halt",
                    Evidence::new(ticker.clone(), "quote", ts, conditions, indicators)
                        .with_json(json!({"bid": quote.bid_price, "ask": quote.ask_price})),
                ));
            }
            out
        }
    }
}

fn evaluate_bar_row(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    row: BarRow,
) -> Vec<LiveSymbolMarketStateEvent> {
    if row.timeframe != "1s" {
        return Vec::new();
    }
    let mut out = Vec::new();
    let ts = row.last_event_ts.unwrap_or(row.bar_end);
    let luld_event_type = estimated_luld_event_type(&row.estimated_luld_state);
    let luld_specials = [
        "estimated_luld_near_upper",
        "estimated_luld_near_lower",
        "estimated_luld_breach_upper",
        "estimated_luld_breach_lower",
    ];
    if let Some(event_type) = luld_event_type {
        out.extend(open_or_update(
            config,
            active,
            EventSpec {
                event_type,
                severity: if event_type.contains("breach") {
                    "critical"
                } else {
                    "warning"
                },
                blocking: event_type.contains("breach"),
                block_reason: if event_type.contains("breach") {
                    Some(event_type)
                } else {
                    None
                },
            },
            Evidence::new(row.sym.clone(), "bar", ts, Vec::new(), Vec::new()).with_json(json!({
                "timeframe": row.timeframe,
                "bar_start": clickhouse_datetime64(&row.bar_start),
                "bar_end": clickhouse_datetime64(&row.bar_end),
                "estimated_luld_state": row.estimated_luld_state,
                "estimated_luld_distance_to_upper_pct": row.estimated_luld_distance_to_upper_pct,
                "estimated_luld_distance_to_lower_pct": row.estimated_luld_distance_to_lower_pct,
                "estimated_luld_lower_price": row.estimated_luld_lower_price,
                "estimated_luld_upper_price": row.estimated_luld_upper_price,
            })),
        ));
        close_other_specials(
            config,
            active,
            &mut out,
            &row.sym,
            event_type,
            &luld_specials,
            ts,
        );
    } else {
        for event_type in luld_specials {
            out.extend(close_state(
                config,
                active,
                &row.sym,
                event_type,
                Evidence::new(row.sym.clone(), "bar", ts, Vec::new(), Vec::new()).with_json(
                    json!({
                        "timeframe": row.timeframe,
                        "estimated_luld_state": row.estimated_luld_state,
                    }),
                ),
            ));
        }
    }

    if row.locked_crossed_quote_count > 0 {
        out.extend(open_or_update(
            config,
            active,
            EventSpec::blocking("locked_crossed_quote", "warning", "locked_crossed_quote"),
            Evidence::new(row.sym.clone(), "bar", ts, Vec::new(), Vec::new()).with_json(json!({
                "timeframe": row.timeframe,
                "locked_crossed_quote_count": row.locked_crossed_quote_count,
                "quote_count": row.quote_count,
            })),
        ));
    } else {
        out.extend(close_state(
            config,
            active,
            &row.sym,
            "locked_crossed_quote",
            Evidence::new(row.sym.clone(), "bar", ts, Vec::new(), Vec::new()).with_json(json!({
                "timeframe": row.timeframe,
                "locked_crossed_quote_count": row.locked_crossed_quote_count,
            })),
        ));
    }
    out
}

fn estimated_luld_event_type(state: &str) -> Option<&'static str> {
    match state {
        "near_upper" => Some("estimated_luld_near_upper"),
        "near_lower" => Some("estimated_luld_near_lower"),
        "above_upper" => Some("estimated_luld_breach_upper"),
        "below_lower" => Some("estimated_luld_breach_lower"),
        _ => None,
    }
}

fn close_other_specials(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    out: &mut Vec<LiveSymbolMarketStateEvent>,
    ticker: &str,
    keep_event_type: &str,
    candidates: &[&str],
    ts: DateTime<Utc>,
) {
    for event_type in candidates {
        if *event_type != keep_event_type {
            out.extend(close_state(
                config,
                active,
                ticker,
                event_type,
                Evidence::new(ticker.to_string(), "bar", ts, Vec::new(), Vec::new()),
            ));
        }
    }
}

fn open_or_update(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    spec: EventSpec<'_>,
    evidence: Evidence,
) -> Vec<LiveSymbolMarketStateEvent> {
    let key = StateKey {
        ticker: evidence.ticker.clone(),
        event_type: spec.event_type.to_string(),
    };
    let now = Utc::now();
    let already_active = active.contains_key(&key);
    let status = if already_active { "updated" } else { "opened" };
    let event_start_utc = active
        .get(&key)
        .map(|event| event.event_start_utc)
        .unwrap_or(evidence.source_event_ts_utc);
    let row = LiveSymbolMarketStateEvent {
        schema_version: LIVE_MARKET_STATE_SCHEMA_VERSION,
        event_id: event_id(
            config,
            &evidence.ticker,
            spec.event_type,
            status,
            evidence.source_event_ts_utc,
        ),
        ticker: evidence.ticker,
        event_type: spec.event_type.to_string(),
        event_status: status.to_string(),
        event_start_utc,
        event_end_utc: None,
        source_event_ts_utc: evidence.source_event_ts_utc,
        source_event_type: evidence.source_event_type,
        source_conditions: evidence.source_conditions,
        source_indicators: evidence.source_indicators,
        severity: spec.severity.to_string(),
        is_live_tradability_blocking: spec.blocking,
        block_reason: spec.block_reason.map(ToString::to_string),
        evidence_json: evidence.evidence_json.to_string(),
        source_run_id: config.qmd_run_id.clone(),
        inserted_at_utc: now,
    };
    active.insert(key, row.clone());
    vec![row]
}

fn close_state(
    config: &GatewayConfig,
    active: &mut HashMap<StateKey, LiveSymbolMarketStateEvent>,
    ticker: &str,
    event_type: &str,
    evidence: Evidence,
) -> Vec<LiveSymbolMarketStateEvent> {
    let key = StateKey {
        ticker: ticker.to_string(),
        event_type: event_type.to_string(),
    };
    let Some(previous) = active.remove(&key) else {
        return Vec::new();
    };
    let now = Utc::now();
    vec![LiveSymbolMarketStateEvent {
        schema_version: LIVE_MARKET_STATE_SCHEMA_VERSION,
        event_id: event_id(
            config,
            ticker,
            event_type,
            "closed",
            evidence.source_event_ts_utc,
        ),
        ticker: ticker.to_string(),
        event_type: event_type.to_string(),
        event_status: "closed".to_string(),
        event_start_utc: previous.event_start_utc,
        event_end_utc: Some(evidence.source_event_ts_utc),
        source_event_ts_utc: evidence.source_event_ts_utc,
        source_event_type: evidence.source_event_type,
        source_conditions: evidence.source_conditions,
        source_indicators: evidence.source_indicators,
        severity: previous.severity,
        is_live_tradability_blocking: previous.is_live_tradability_blocking,
        block_reason: previous.block_reason,
        evidence_json: evidence.evidence_json.to_string(),
        source_run_id: config.qmd_run_id.clone(),
        inserted_at_utc: now,
    }]
}

fn intersects(values: &[u16], targets: &[u16]) -> bool {
    if values.is_empty() || targets.is_empty() {
        return false;
    }
    let targets = targets.iter().copied().collect::<HashSet<_>>();
    values.iter().any(|value| targets.contains(value))
}

fn event_id(
    config: &GatewayConfig,
    ticker: &str,
    event_type: &str,
    event_status: &str,
    ts: DateTime<Utc>,
) -> String {
    format!(
        "{}:{}:{}:{}:{}",
        config.qmd_run_id,
        ticker,
        event_type,
        event_status,
        ts.timestamp_micros()
    )
}

struct EventSpec<'a> {
    event_type: &'a str,
    severity: &'a str,
    blocking: bool,
    block_reason: Option<&'a str>,
}

impl<'a> EventSpec<'a> {
    fn blocking(event_type: &'a str, severity: &'a str, block_reason: &'a str) -> Self {
        Self {
            event_type,
            severity,
            blocking: true,
            block_reason: Some(block_reason),
        }
    }
}

struct Evidence {
    ticker: String,
    source_event_type: String,
    source_event_ts_utc: DateTime<Utc>,
    source_conditions: Vec<u16>,
    source_indicators: Vec<u16>,
    evidence_json: serde_json::Value,
}

impl Evidence {
    fn new(
        ticker: String,
        source_event_type: impl Into<String>,
        source_event_ts_utc: DateTime<Utc>,
        source_conditions: Vec<u16>,
        source_indicators: Vec<u16>,
    ) -> Self {
        Self {
            ticker,
            source_event_type: source_event_type.into(),
            source_event_ts_utc,
            source_conditions,
            source_indicators,
            evidence_json: json!({}),
        }
    }

    fn with_json(mut self, evidence_json: serde_json::Value) -> Self {
        self.evidence_json = evidence_json;
        self
    }
}

struct LiveMarketStateClickHouseWriter {
    client: Client,
    config: GatewayConfig,
}

impl LiveMarketStateClickHouseWriter {
    fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    async fn initialize(&self) -> Result<(), String> {
        if !self.config.live_market_state_enabled {
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
        self.execute(&self.create_table_sql(), true).await
    }

    async fn insert(&self, rows: &[LiveSymbolMarketStateEvent]) -> Result<(), String> {
        if !self.config.live_market_state_enabled || rows.is_empty() {
            return Ok(());
        }
        let body = rows
            .iter()
            .map(|row| serde_json::to_string(&insert_row(row)).unwrap_or_else(|_| "{}".to_string()))
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow",
                self.config.live_market_state_table
            ),
            body,
        )
        .await
    }

    fn create_table_sql(&self) -> String {
        format!(
            r#"
            CREATE TABLE IF NOT EXISTS {table}
            (
                schema_version UInt16,
                event_id String,
                ticker LowCardinality(String),
                event_type LowCardinality(String),
                event_status LowCardinality(String),
                event_start_utc DateTime64(3, 'UTC'),
                event_end_utc Nullable(DateTime64(3, 'UTC')),
                source_event_ts_utc DateTime64(3, 'UTC'),
                source_event_type LowCardinality(String),
                source_conditions Array(UInt16),
                source_indicators Array(UInt16),
                severity LowCardinality(String),
                is_live_tradability_blocking UInt8,
                block_reason Nullable(String),
                evidence_json String,
                source_run_id String,
                inserted_at_utc DateTime64(3, 'UTC')
            )
            ENGINE = MergeTree
            PARTITION BY toYYYYMM(event_start_utc)
            ORDER BY (ticker, event_type, event_start_utc, event_id)
            {settings}
            "#,
            table = self.config.live_market_state_table,
            settings = merge_tree_settings(&self.config.clickhouse_storage_policy),
        )
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
            self.config.clickhouse_url.clone()
        };
        let mut request = self
            .client
            .post(url)
            .basic_auth(
                self.config.clickhouse_user.clone(),
                Some(self.config.clickhouse_password()),
            )
            .body(body.to_string());
        request = request.header("Content-Type", "text/plain; charset=utf-8");
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("ClickHouse HTTP {status}: {text}"));
        }
        Ok(text)
    }
}

fn insert_row(row: &LiveSymbolMarketStateEvent) -> serde_json::Value {
    json!({
        "schema_version": row.schema_version,
        "event_id": row.event_id,
        "ticker": row.ticker,
        "event_type": row.event_type,
        "event_status": row.event_status,
        "event_start_utc": clickhouse_datetime64(&row.event_start_utc),
        "event_end_utc": clickhouse_datetime64_opt(row.event_end_utc.as_ref()),
        "source_event_ts_utc": clickhouse_datetime64(&row.source_event_ts_utc),
        "source_event_type": row.source_event_type,
        "source_conditions": row.source_conditions,
        "source_indicators": row.source_indicators,
        "severity": row.severity,
        "is_live_tradability_blocking": if row.is_live_tradability_blocking { 1 } else { 0 },
        "block_reason": row.block_reason,
        "evidence_json": row.evidence_json,
        "source_run_id": row.source_run_id,
        "inserted_at_utc": clickhouse_datetime64(&row.inserted_at_utc),
    })
}

fn merge_tree_settings(storage_policy: &str) -> String {
    if storage_policy.trim().is_empty() {
        "SETTINGS index_granularity = 8192".to_string()
    } else {
        format!(
            "SETTINGS index_granularity = 8192, storage_policy = '{}'",
            storage_policy.replace('\'', "''")
        )
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn config() -> GatewayConfig {
        let mut config = GatewayConfig::from_env();
        config.qmd_run_id = "test_run".to_string();
        config.live_market_state_enabled = false;
        config
    }

    #[test]
    fn maps_luld_special_states() {
        assert_eq!(
            estimated_luld_event_type("near_upper"),
            Some("estimated_luld_near_upper")
        );
        assert_eq!(
            estimated_luld_event_type("above_upper"),
            Some("estimated_luld_breach_upper")
        );
        assert_eq!(estimated_luld_event_type("inside"), None);
    }

    #[test]
    fn opens_and_closes_state() {
        let config = config();
        let mut active = HashMap::new();
        let ts = Utc.with_ymd_and_hms(2026, 1, 1, 15, 0, 0).unwrap();
        let opened = open_or_update(
            &config,
            &mut active,
            EventSpec::blocking("condition_halt", "critical", "condition_halt"),
            Evidence::new("AAPL".to_string(), "trade", ts, vec![1], Vec::new()),
        );
        assert_eq!(opened[0].event_status, "opened");
        let updated = open_or_update(
            &config,
            &mut active,
            EventSpec::blocking("condition_halt", "critical", "condition_halt"),
            Evidence::new("AAPL".to_string(), "trade", ts, vec![1], Vec::new()),
        );
        assert_eq!(updated[0].event_status, "updated");
        let closed = close_state(
            &config,
            &mut active,
            "AAPL",
            "condition_halt",
            Evidence::new("AAPL".to_string(), "trade", ts, vec![2], Vec::new()),
        );
        assert_eq!(closed[0].event_status, "closed");
        assert!(active.is_empty());
    }
}
