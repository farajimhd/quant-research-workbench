use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use reqwest::Client;
use serde_json::json;
use tokio::sync::mpsc;
use tokio::time::{interval, Duration};

pub const RAW_EVENT_SCHEMA_VERSION: u16 = 1;

#[derive(Clone)]
pub struct ClickHouseWriter {
    client: Client,
    config: GatewayConfig,
}

impl ClickHouseWriter {
    pub fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    pub async fn initialize(&self) -> Result<(), String> {
        self.execute(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.execute(
            r#"
            CREATE TABLE IF NOT EXISTS live_massive_trades
            (
                session_date Date,
                schema_version UInt16,
                ts DateTime64(3, 'UTC'),
                participant_ts Nullable(DateTime64(3, 'UTC')),
                trf_ts Nullable(DateTime64(3, 'UTC')),
                ingest_ts DateTime64(3, 'UTC'),
                sym LowCardinality(String),
                trade_id String,
                seq UInt64,
                exchange UInt16,
                tape UInt8,
                price Float64,
                size Float64,
                conditions Array(UInt16),
                trf_id UInt16,
                raw String
            )
            ENGINE = MergeTree
            PARTITION BY session_date
            ORDER BY (session_date, sym, ts, seq)
            "#,
            true,
        )
        .await?;
        self.execute(
            r#"
            CREATE TABLE IF NOT EXISTS live_massive_quotes
            (
                session_date Date,
                schema_version UInt16,
                ts DateTime64(3, 'UTC'),
                ingest_ts DateTime64(3, 'UTC'),
                sym LowCardinality(String),
                seq UInt64,
                bid_exchange UInt16,
                ask_exchange UInt16,
                bid_price Float64,
                ask_price Float64,
                bid_size UInt32,
                ask_size UInt32,
                conditions Array(UInt16),
                indicators Array(UInt16),
                tape UInt8,
                raw String
            )
            ENGINE = MergeTree
            PARTITION BY session_date
            ORDER BY (session_date, sym, ts, seq)
            "#,
            true,
        )
        .await?;
        self.execute(
            "ALTER TABLE live_massive_trades ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await?;
        self.execute(
            "ALTER TABLE live_massive_quotes ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await?;
        Ok(())
    }

    pub async fn run(self, mut receiver: mpsc::Receiver<MarketEvent>) {
        let mut quote_batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut trade_batch = Vec::with_capacity(self.config.max_clickhouse_batch);
        let mut flush_interval = interval(Duration::from_millis(self.config.flush_interval_ms));
        loop {
            tokio::select! {
                event = receiver.recv() => {
                    match event {
                        Some(MarketEvent::Trade(trade)) => trade_batch.push(trade),
                        Some(MarketEvent::Quote(quote)) => quote_batch.push(quote),
                        None => {
                            self.flush(&mut trade_batch, &mut quote_batch).await;
                            return;
                        }
                    }
                    if trade_batch.len() >= self.config.max_clickhouse_batch || quote_batch.len() >= self.config.max_clickhouse_batch {
                        self.flush(&mut trade_batch, &mut quote_batch).await;
                    }
                }
                _ = flush_interval.tick() => {
                    self.flush(&mut trade_batch, &mut quote_batch).await;
                }
            }
        }
    }

    async fn flush(&self, trades: &mut Vec<TradeEvent>, quotes: &mut Vec<QuoteEvent>) {
        if !trades.is_empty() {
            if let Err(error) = self.insert_trades(trades).await {
                eprintln!("ClickHouse trade insert failed: {error}");
            } else {
                trades.clear();
            }
        }
        if !quotes.is_empty() {
            if let Err(error) = self.insert_quotes(quotes).await {
                eprintln!("ClickHouse quote insert failed: {error}");
            } else {
                quotes.clear();
            }
        }
    }

    async fn insert_trades(&self, rows: &[TradeEvent]) -> Result<(), String> {
        let body =
            rows.iter()
                .map(|event| {
                    json!({
                    "session_date": event.ts.date_naive().to_string(),
                    "schema_version": RAW_EVENT_SCHEMA_VERSION,
                    "ts": event.ts.to_rfc3339(),
                    "participant_ts": event.participant_ts.as_ref().map(|value| value.to_rfc3339()),
                    "trf_ts": event.trf_ts.as_ref().map(|value| value.to_rfc3339()),
                    "ingest_ts": event.ingest_ts.to_rfc3339(),
                    "sym": &event.ticker,
                    "trade_id": &event.trade_id,
                    "seq": event.sequence,
                    "exchange": event.exchange,
                    "tape": event.tape,
                    "price": event.price,
                    "size": event.size,
                    "conditions": &event.conditions,
                    "trf_id": event.trf_id,
                    "raw": event.raw.to_string(),
                }).to_string()
                })
                .collect::<Vec<_>>()
                .join("\n");
        self.query_with_body("INSERT INTO live_massive_trades FORMAT JSONEachRow", body)
            .await
    }

    async fn insert_quotes(&self, rows: &[QuoteEvent]) -> Result<(), String> {
        let body = rows
            .iter()
            .map(|event| {
                json!({
                    "session_date": event.ts.date_naive().to_string(),
                    "schema_version": RAW_EVENT_SCHEMA_VERSION,
                    "ts": event.ts.to_rfc3339(),
                    "ingest_ts": event.ingest_ts.to_rfc3339(),
                    "sym": &event.ticker,
                    "seq": event.sequence,
                    "bid_exchange": event.bid_exchange,
                    "ask_exchange": event.ask_exchange,
                    "bid_price": event.bid_price,
                    "ask_price": event.ask_price,
                    "bid_size": event.bid_size,
                    "ask_size": event.ask_size,
                    "conditions": &event.conditions,
                    "indicators": &event.indicators,
                    "tape": event.tape,
                    "raw": event.raw.to_string(),
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query_with_body("INSERT INTO live_massive_quotes FORMAT JSONEachRow", body)
            .await
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
