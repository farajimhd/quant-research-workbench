use crate::config::GatewayConfig;
use crate::clickhouse::RAW_EVENT_SCHEMA_VERSION;
use crate::metrics::{SharedMetrics, TimingTarget};
use crate::session::{is_streaming_phase, session_phase};
use chrono::{DateTime, Duration as ChronoDuration, NaiveDateTime, Utc};
use reqwest::Client;
use serde_json::{json, Value};
use tokio::time::{interval, Duration};

pub async fn run_gap_fill_service(config: GatewayConfig, metrics: SharedMetrics) {
    if !config.gap_fill_enabled {
        return;
    }
    let filler = GapFillService::new(config, metrics);
    if is_streaming_phase(Utc::now()) && matches!(filler.config.gap_fill_mode.as_str(), "auto" | "session" | "session_catch_up") {
        if let Err(error) = filler.run_once("session_catch_up").await {
            filler.metrics.inc_gap_fill_failure();
            eprintln!("Session catch-up gap fill failed: {error}");
        }
    }
    let mut timer = interval(Duration::from_millis(filler.config.gap_fill_interval_ms));
    loop {
        timer.tick().await;
        if is_streaming_phase(Utc::now()) {
            continue;
        }
        if !matches!(filler.config.gap_fill_mode.as_str(), "auto" | "after_hours" | "repair") {
            continue;
        }
        if let Err(error) = filler.run_once("after_hours_repair").await {
            filler.metrics.inc_gap_fill_failure();
            eprintln!("Gap fill cycle failed: {error}");
        }
    }
}

#[derive(Clone)]
struct GapFillService {
    client: Client,
    config: GatewayConfig,
    metrics: SharedMetrics,
}

impl GapFillService {
    fn new(config: GatewayConfig, metrics: SharedMetrics) -> Self {
        Self {
            client: Client::new(),
            config,
            metrics,
        }
    }

    async fn run_once(&self, mode: &str) -> Result<(), String> {
        let _timing = self.metrics.timing(TimingTarget::GapFillRun);
        self.metrics.inc_gap_fill_run();
        self.initialize_tables().await?;
        let started_at = Utc::now();
        let phase = format!("{:?}", session_phase(started_at));
        if self.config.massive_api_key.is_empty() {
            self.record_run(started_at, mode, &phase, "", "skipped", 0, "MASSIVE_API_KEY is not configured").await?;
            return Ok(());
        }
        let symbols = self.gap_fill_symbols().await?;
        if symbols.is_empty() {
            self.record_run(started_at, mode, &phase, "", "skipped", 0, "No gap-fill symbols were configured or discovered").await?;
            return Ok(());
        }
        for symbol in symbols {
            let trade_rows = self.fill_kind(&symbol, "trades").await.unwrap_or_else(|error| {
                eprintln!("Trade gap fill failed for {symbol}: {error}");
                0
            });
            let quote_rows = self.fill_kind(&symbol, "quotes").await.unwrap_or_else(|error| {
                eprintln!("Quote gap fill failed for {symbol}: {error}");
                0
            });
            let rows = trade_rows + quote_rows;
            self.metrics.inc_gap_fill_rows(rows);
            self.record_run(started_at, mode, &phase, &symbol, "completed", rows, "").await?;
        }
        Ok(())
    }

    async fn fill_kind(&self, symbol: &str, kind: &str) -> Result<u64, String> {
        let table = if kind == "trades" { "live_massive_trades" } else { "live_massive_quotes" };
        let latest = self.latest_ts(table, symbol).await?;
        let now = Utc::now();
        let start = latest.unwrap_or(now - ChronoDuration::minutes(self.config.gap_fill_lookback_minutes));
        if (now - start).num_seconds() < self.config.gap_fill_min_gap_seconds {
            return Ok(0);
        }
        let mut next_url = Some(self.rest_url(symbol, kind, start, now));
        let mut pages = 0usize;
        let mut inserted = 0u64;
        while let Some(url) = next_url.take() {
            if pages >= self.config.gap_fill_max_pages_per_symbol {
                break;
            }
            pages += 1;
            let payload: Value = self.client.get(url).send().await.map_err(|error| error.to_string())?.json().await.map_err(|error| error.to_string())?;
            let rows = payload.get("results").and_then(Value::as_array).cloned().unwrap_or_default();
            if rows.is_empty() {
                break;
            }
            if kind == "trades" {
                inserted += self.insert_rest_trades(symbol, &rows).await? as u64;
            } else {
                inserted += self.insert_rest_quotes(symbol, &rows).await? as u64;
            }
            next_url = payload.get("next_url").and_then(Value::as_str).map(|url| append_api_key(url, &self.config.massive_api_key));
        }
        Ok(inserted)
    }

    async fn gap_fill_symbols(&self) -> Result<Vec<String>, String> {
        if !self.config.gap_fill_symbols.is_empty() {
            return Ok(self.config.gap_fill_symbols.clone());
        }
        let session_date = Utc::now().date_naive().to_string();
        let sql = format!(
            r#"
            SELECT DISTINCT sym
            FROM
            (
                SELECT sym FROM live_massive_trades WHERE session_date = toDate('{session_date}')
                UNION ALL
                SELECT sym FROM live_massive_quotes WHERE session_date = toDate('{session_date}')
            )
            WHERE sym != ''
            ORDER BY sym
            FORMAT JSONEachRow
            "#
        );
        let text = self.query(&sql, true).await?;
        let mut symbols = Vec::new();
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
            if let Some(symbol) = value.get("sym").and_then(Value::as_str) {
                symbols.push(symbol.to_ascii_uppercase());
            }
        }
        Ok(symbols)
    }

    fn rest_url(&self, symbol: &str, kind: &str, start: DateTime<Utc>, end: DateTime<Utc>) -> String {
        let endpoint = if kind == "trades" { "trades" } else { "quotes" };
        format!(
            "https://api.massive.com/v3/{endpoint}/{symbol}?timestamp.gte={}&timestamp.lt={}&order=asc&sort=timestamp&limit=50000&apiKey={}",
            start.timestamp_nanos_opt().unwrap_or_default(),
            end.timestamp_nanos_opt().unwrap_or_default(),
            urlencoding::encode(&self.config.massive_api_key),
        )
    }

    async fn latest_ts(&self, table: &str, symbol: &str) -> Result<Option<DateTime<Utc>>, String> {
        let sql = format!(
            "SELECT max(ts) AS ts FROM {table} WHERE sym = '{}' FORMAT JSONEachRow",
            symbol.replace('\'', "''")
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        let Some(text) = value.get("ts").and_then(Value::as_str) else {
            return Ok(None);
        };
        if text.is_empty() || text.starts_with("1970-") {
            return Ok(None);
        }
        DateTime::parse_from_rfc3339(text)
            .map(|value| Some(value.with_timezone(&Utc)))
            .or_else(|_| NaiveDateTime::parse_from_str(text, "%Y-%m-%d %H:%M:%S%.f").map(|value| Some(value.and_utc())))
            .map_err(|error| error.to_string())
    }

    async fn insert_rest_trades(&self, symbol: &str, rows: &[Value]) -> Result<usize, String> {
        let body = rows
            .iter()
            .map(|row| {
                let ts = ns_to_rfc3339(row.get("sip_timestamp").and_then(Value::as_i64).unwrap_or_default());
                json!({
                    "session_date": ts.get(0..10).unwrap_or("1970-01-01"),
                    "schema_version": RAW_EVENT_SCHEMA_VERSION,
                    "ts": ts,
                    "participant_ts": optional_ns_to_rfc3339(row.get("participant_timestamp").and_then(Value::as_i64)),
                    "trf_ts": optional_ns_to_rfc3339(row.get("trf_timestamp").and_then(Value::as_i64)),
                    "ingest_ts": Utc::now().to_rfc3339(),
                    "sym": symbol,
                    "trade_id": row.get("id").and_then(Value::as_str).unwrap_or_default(),
                    "seq": row.get("sequence_number").and_then(Value::as_u64).unwrap_or_default(),
                    "exchange": row.get("exchange").and_then(Value::as_u64).unwrap_or_default(),
                    "tape": row.get("tape").and_then(Value::as_u64).unwrap_or_default(),
                    "price": row.get("price").and_then(Value::as_f64).unwrap_or_default(),
                    "size": row.get("size").and_then(Value::as_f64).unwrap_or_default(),
                    "conditions": row.get("conditions").cloned().unwrap_or_else(|| json!([])),
                    "trf_id": row.get("trf_id").and_then(Value::as_u64).unwrap_or_default(),
                    "raw": row.to_string(),
                }).to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query(&format!("INSERT INTO live_massive_trades FORMAT JSONEachRow\n{body}"), true).await?;
        Ok(rows.len())
    }

    async fn insert_rest_quotes(&self, symbol: &str, rows: &[Value]) -> Result<usize, String> {
        let body = rows
            .iter()
            .map(|row| {
                let ts = ns_to_rfc3339(row.get("sip_timestamp").and_then(Value::as_i64).unwrap_or_default());
                json!({
                    "session_date": ts.get(0..10).unwrap_or("1970-01-01"),
                    "schema_version": RAW_EVENT_SCHEMA_VERSION,
                    "ts": ts,
                    "ingest_ts": Utc::now().to_rfc3339(),
                    "sym": symbol,
                    "seq": row.get("sequence_number").and_then(Value::as_u64).unwrap_or_default(),
                    "bid_exchange": row.get("bid_exchange").and_then(Value::as_u64).unwrap_or_default(),
                    "ask_exchange": row.get("ask_exchange").and_then(Value::as_u64).unwrap_or_default(),
                    "bid_price": row.get("bid_price").and_then(Value::as_f64).unwrap_or_default(),
                    "ask_price": row.get("ask_price").and_then(Value::as_f64).unwrap_or_default(),
                    "bid_size": row.get("bid_size").and_then(Value::as_u64).unwrap_or_default(),
                    "ask_size": row.get("ask_size").and_then(Value::as_u64).unwrap_or_default(),
                    "conditions": row.get("conditions").cloned().unwrap_or_else(|| json!([])),
                    "indicators": row.get("indicators").cloned().unwrap_or_else(|| json!([])),
                    "tape": row.get("tape").and_then(Value::as_u64).unwrap_or_default(),
                    "raw": row.to_string(),
                }).to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query(&format!("INSERT INTO live_massive_quotes FORMAT JSONEachRow\n{body}"), true).await?;
        Ok(rows.len())
    }

    async fn initialize_tables(&self) -> Result<(), String> {
        self.query(&format!("CREATE DATABASE IF NOT EXISTS `{}`", self.config.clickhouse_database), false)
            .await?;
        self.query(
            "ALTER TABLE live_massive_trades ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await
        .map(|_| ())
        .unwrap_or(());
        self.query(
            "ALTER TABLE live_massive_quotes ADD COLUMN IF NOT EXISTS schema_version UInt16 AFTER session_date",
            true,
        )
        .await
        .map(|_| ())
        .unwrap_or(());
        self.query(
            r#"
            CREATE TABLE IF NOT EXISTS qmd_gap_fill_runs
            (
                started_at DateTime64(3, 'UTC'),
                mode LowCardinality(String),
                phase LowCardinality(String),
                symbol LowCardinality(String),
                status LowCardinality(String),
                rows_written UInt64,
                message String
            )
            ENGINE = MergeTree
            ORDER BY (started_at, symbol)
            "#,
            true,
        )
        .await
        .map(|_| ())?;
        self.query(
            "ALTER TABLE qmd_gap_fill_runs ADD COLUMN IF NOT EXISTS mode LowCardinality(String) AFTER started_at",
            true,
        )
        .await
        .map(|_| ())
    }

    async fn record_run(&self, started_at: DateTime<Utc>, mode: &str, phase: &str, symbol: &str, status: &str, rows_written: u64, message: &str) -> Result<(), String> {
        let row = json!({
            "started_at": started_at.to_rfc3339(),
            "mode": mode,
            "phase": phase,
            "symbol": symbol,
            "status": status,
            "rows_written": rows_written,
            "message": message,
        });
        self.query(&format!("INSERT INTO qmd_gap_fill_runs FORMAT JSONEachRow\n{}", row), true)
            .await
            .map(|_| ())
    }

    async fn query(&self, body: &str, use_database: bool) -> Result<String, String> {
        let url = if use_database {
            format!("{}/?database={}", self.config.clickhouse_url, urlencoding::encode(&self.config.clickhouse_database))
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

fn append_api_key(url: &str, api_key: &str) -> String {
    if url.contains("apiKey=") {
        url.to_string()
    } else if url.contains('?') {
        format!("{url}&apiKey={}", urlencoding::encode(api_key))
    } else {
        format!("{url}?apiKey={}", urlencoding::encode(api_key))
    }
}

fn ns_to_rfc3339(ns: i64) -> String {
    let seconds = ns.div_euclid(1_000_000_000);
    let nanos = ns.rem_euclid(1_000_000_000) as u32;
    DateTime::<Utc>::from_timestamp(seconds, nanos)
        .unwrap_or_else(|| DateTime::<Utc>::from_timestamp(0, 0).expect("epoch timestamp"))
        .to_rfc3339()
}

fn optional_ns_to_rfc3339(ns: Option<i64>) -> Option<String> {
    ns.filter(|value| *value > 0).map(ns_to_rfc3339)
}
