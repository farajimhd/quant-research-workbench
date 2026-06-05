use crate::bars::BarEventRouter;
use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::indicators::IndicatorEventRouter;
use crate::metrics::SharedMetrics;
use crate::state::SharedMarketState;
use chrono::{DateTime, NaiveDateTime, Utc};
use reqwest::Client;
use serde_json::{json, Value};

pub async fn run_replay_service(
    config: GatewayConfig,
    metrics: SharedMetrics,
    market: SharedMarketState,
    bar_router: BarEventRouter,
    indicator_router: IndicatorEventRouter,
) {
    if !config.replay_enabled {
        return;
    }
    let replay = ReplayService {
        client: Client::new(),
        config,
        metrics,
        market,
        bar_router,
        indicator_router,
    };
    if let Err(error) = replay.run_once().await {
        eprintln!("Replay failed: {error}");
    }
}

struct ReplayService {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
    market: SharedMarketState,
    bar_router: BarEventRouter,
    indicator_router: IndicatorEventRouter,
}

impl ReplayService {
    async fn run_once(&self) -> Result<(), String> {
        let date = if self.config.replay_date.is_empty() {
            Utc::now().date_naive().to_string()
        } else {
            self.config.replay_date.clone()
        };
        let symbol_filter = if self.config.replay_symbols.is_empty() {
            String::new()
        } else {
            let symbols = self
                .config
                .replay_symbols
                .iter()
                .map(|symbol| format!("'{}'", symbol.replace('\'', "''")))
                .collect::<Vec<_>>()
                .join(",");
            format!("AND sym IN ({symbols})")
        };
        let sql = format!(
            r#"
            SELECT *
            FROM
            (
                SELECT
                    'trade' AS kind,
                    ts,
                    ingest_ts,
                    sym,
                    seq,
                    exchange,
                    tape,
                    price,
                    size,
                    conditions,
                    trade_id,
                    trf_id,
                    bid_exchange,
                    ask_exchange,
                    bid_price,
                    ask_price,
                    bid_size,
                    ask_size,
                    indicators
                FROM
                (
                    SELECT
                        ts, ingest_ts, sym, seq, exchange, tape, price, size, conditions, trade_id, trf_id,
                        toUInt16(0) AS bid_exchange, toUInt16(0) AS ask_exchange,
                        toFloat64(0) AS bid_price, toFloat64(0) AS ask_price,
                        toUInt32(0) AS bid_size, toUInt32(0) AS ask_size,
                        CAST([], 'Array(UInt16)') AS indicators
                    FROM live_massive_trades
                    WHERE session_date = toDate('{date}') {symbol_filter}
                )
                UNION ALL
                SELECT
                    'quote' AS kind,
                    ts,
                    ingest_ts,
                    sym,
                    seq,
                    toUInt16(0) AS exchange,
                    tape,
                    toFloat64(0) AS price,
                    toFloat64(0) AS size,
                    conditions,
                    '' AS trade_id,
                    toUInt16(0) AS trf_id,
                    bid_exchange,
                    ask_exchange,
                    bid_price,
                    ask_price,
                    bid_size,
                    ask_size,
                    indicators
                FROM live_massive_quotes
                WHERE session_date = toDate('{date}') {symbol_filter}
            )
            ORDER BY ts, kind
            LIMIT {}
            FORMAT JSONEachRow
            "#,
            self.config.replay_max_rows
        );
        let text = self.query(&sql).await?;
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
            if let Some(event) = row_to_event(&value)? {
                self.metrics.observe_event(
                    match &event {
                        MarketEvent::Trade(_) => "trade",
                        MarketEvent::Quote(_) => "quote",
                    },
                    event.ts(),
                );
                self.market.apply_event(&event).await;
                if self.bar_router.try_send(event.clone()).is_err() {
                    self.metrics.inc_bar_event_dropped();
                }
                if self.indicator_router.try_send_event(event).is_err() {
                    self.metrics.inc_indicator_event_dropped();
                }
            }
        }
        Ok(())
    }

    async fn query(&self, body: &str) -> Result<String, String> {
        let url = format!(
            "{}/?database={}",
            self.config.clickhouse_url,
            urlencoding::encode(&self.config.clickhouse_database)
        );
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

fn row_to_event(row: &Value) -> Result<Option<MarketEvent>, String> {
    let kind = row.get("kind").and_then(Value::as_str).unwrap_or_default();
    let ts = parse_ts(row.get("ts").and_then(Value::as_str).unwrap_or_default())?;
    let ingest_ts = parse_ts(row.get("ingest_ts").and_then(Value::as_str).unwrap_or_default()).unwrap_or_else(|_| Utc::now());
    let ticker = row.get("sym").and_then(Value::as_str).unwrap_or_default().to_ascii_uppercase();
    if ticker.is_empty() {
        return Ok(None);
    }
    match kind {
        "trade" => Ok(Some(MarketEvent::Trade(TradeEvent {
            conditions: u16_array(row.get("conditions").unwrap_or(&Value::Null)),
            exchange: u16_field(row, "exchange"),
            ingest_ts,
            participant_ts: None,
            price: f64_field(row, "price"),
            raw: json!({}),
            sequence: u64_field(row, "seq"),
            size: f64_field(row, "size"),
            tape: u8_field(row, "tape"),
            ticker,
            trade_id: row.get("trade_id").and_then(Value::as_str).unwrap_or_default().to_string(),
            trf_id: u16_field(row, "trf_id"),
            trf_ts: None,
            ts,
        }))),
        "quote" => Ok(Some(MarketEvent::Quote(QuoteEvent {
            ask_exchange: u16_field(row, "ask_exchange"),
            ask_price: f64_field(row, "ask_price"),
            ask_size: u32_field(row, "ask_size"),
            bid_exchange: u16_field(row, "bid_exchange"),
            bid_price: f64_field(row, "bid_price"),
            bid_size: u32_field(row, "bid_size"),
            conditions: u16_array(row.get("conditions").unwrap_or(&Value::Null)),
            indicators: u16_array(row.get("indicators").unwrap_or(&Value::Null)),
            ingest_ts,
            raw: json!({}),
            sequence: u64_field(row, "seq"),
            tape: u8_field(row, "tape"),
            ticker,
            ts,
        }))),
        _ => Ok(None),
    }
}

fn parse_ts(text: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(text)
        .map(|value| value.with_timezone(&Utc))
        .or_else(|_| NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S%.f").map(|value| value.and_utc()))
        .map_err(|error| error.to_string())
}

fn f64_field(row: &Value, key: &str) -> f64 {
    row.get(key).and_then(Value::as_f64).unwrap_or_default()
}

fn u64_field(row: &Value, key: &str) -> u64 {
    row.get(key).and_then(Value::as_u64).unwrap_or_default()
}

fn u32_field(row: &Value, key: &str) -> u32 {
    u64_field(row, key).min(u32::MAX as u64) as u32
}

fn u16_field(row: &Value, key: &str) -> u16 {
    u64_field(row, key).min(u16::MAX as u64) as u16
}

fn u8_field(row: &Value, key: &str) -> u8 {
    u64_field(row, key).min(u8::MAX as u64) as u8
}

fn u16_array(value: &Value) -> Vec<u16> {
    value
        .as_array()
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_u64)
                .map(|item| item.min(u16::MAX as u64) as u16)
                .collect()
        })
        .unwrap_or_default()
}
