use crate::config::GatewayConfig;
use crate::event::{MarketEvent, QuoteEvent, TradeEvent};
use crate::massive::{MarketEventFanout, fanout_market_event};
use crate::metrics::TimingTarget;
use crate::session::{is_streaming_phase, session_phase};
use chrono::{DateTime, Datelike, Duration as ChronoDuration, NaiveDate, Utc};
use reqwest::Client;
use serde_json::{Value, json};
use std::path::Path as FsPath;
use std::process::Command;
use tokio::time::{Duration, interval};

pub async fn run_startup_maintenance(config: GatewayConfig, fanout: MarketEventFanout) {
    if !config.gap_fill_enabled || !config.qmd_startup_maintenance_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout);
    eprintln!("QMD startup maintenance: checking recent q_live event coverage.");
    if let Err(error) = filler.run_startup_maintenance().await {
        filler.fanout.metrics.inc_gap_fill_failure();
        eprintln!("QMD startup maintenance failed: {error}");
    }
}

pub async fn run_gap_fill_service(config: GatewayConfig, fanout: MarketEventFanout) {
    if !config.gap_fill_enabled {
        return;
    }
    let filler = GapFillService::new(config, fanout);
    let mut timer = interval(Duration::from_millis(filler.config.gap_fill_interval_ms));
    timer.tick().await;
    loop {
        timer.tick().await;
        let mode = if is_streaming_phase(Utc::now()) {
            if !should_run_session_catch_up(filler.config.gap_fill_mode.as_str()) {
                continue;
            }
            "session_catch_up"
        } else {
            if !matches!(
                filler.config.gap_fill_mode.as_str(),
                "auto" | "after_hours" | "repair"
            ) {
                continue;
            }
            "after_hours_repair"
        };
        if let Err(error) = filler.run_once(mode).await {
            filler.fanout.metrics.inc_gap_fill_failure();
            eprintln!("Gap fill cycle failed: {error}");
        }
    }
}

#[derive(Default)]
struct LiveEventAudit {
    duplicate_ticker_ordinal_rows: u64,
    hole_ticker_count: u64,
    out_of_order_ticker_count: u64,
    recent_rows: u64,
    ticker_count: u64,
}

#[derive(Clone)]
struct GapFillService {
    client: Client,
    config: GatewayConfig,
    fanout: MarketEventFanout,
}

impl GapFillService {
    fn new(config: GatewayConfig, fanout: MarketEventFanout) -> Self {
        Self {
            client: Client::new(),
            config,
            fanout,
        }
    }

    async fn run_startup_maintenance(&self) -> Result<(), String> {
        self.initialize_tables().await?;
        let started_at = Utc::now();
        let phase = format!("{:?}", session_phase(started_at));
        let host_role = self.host_role();
        let audit = self.audit_recent_live_events().await?;
        eprintln!(
            "QMD startup q_live audit: rows={} tickers={} duplicate_ordinals={} ordinal_hole_tickers={} out_of_order_tickers={}",
            audit.recent_rows,
            audit.ticker_count,
            audit.duplicate_ticker_ordinal_rows,
            audit.hole_ticker_count,
            audit.out_of_order_ticker_count
        );
        let mut status = if audit.duplicate_ticker_ordinal_rows == 0
            && audit.hole_ticker_count == 0
            && audit.out_of_order_ticker_count == 0
        {
            "ok"
        } else {
            "needs_manual_rebuild"
        };
        let mut rows_written = 0u64;
        let mut message = String::new();
        if status == "ok" {
            rows_written = self
                .run_once("startup_recent_repair")
                .await
                .unwrap_or_else(|error| {
                    message = error;
                    0
                });
            if !message.is_empty() {
                status = "repair_failed";
            }
        } else {
            message = "Recent q_live event table has structural ordinal/order issues. Not rewriting committed ordinals automatically.".to_string();
        }
        self.record_coverage_run(
            started_at,
            "q_live_recent_events",
            status,
            Utc::now() - ChronoDuration::days(self.config.gap_fill_max_lookback_days.max(1)),
            Utc::now(),
            "startup_maintenance",
            rows_written,
            &host_role,
            "",
            &json!({
                "phase": phase,
                "recent_rows": audit.recent_rows,
                "ticker_count": audit.ticker_count,
                "duplicate_ticker_ordinal_rows": audit.duplicate_ticker_ordinal_rows,
                "hole_ticker_count": audit.hole_ticker_count,
                "out_of_order_ticker_count": audit.out_of_order_ticker_count,
                "message": message,
            }),
        )
        .await?;
        Ok(())
    }

    async fn run_once(&self, mode: &str) -> Result<u64, String> {
        let _timing = self.fanout.metrics.timing(TimingTarget::GapFillRun);
        self.fanout.metrics.inc_gap_fill_run();
        self.initialize_tables().await?;
        let started_at = Utc::now();
        let phase = format!("{:?}", session_phase(started_at));
        if self.config.massive_api_key.is_empty() {
            self.record_run(
                started_at,
                mode,
                &phase,
                "",
                "skipped",
                0,
                "MASSIVE_API_KEY is not configured",
            )
            .await?;
            return Ok(0);
        }
        let symbols = self.gap_fill_symbols().await?;
        if symbols.is_empty() {
            self.record_run(
                started_at,
                mode,
                &phase,
                "",
                "skipped",
                0,
                "No gap-fill symbols were configured or discovered",
            )
            .await?;
            return Ok(0);
        }
        let mut total_rows = 0u64;
        for symbol in symbols {
            let rows = self.fill_symbol(&symbol).await.unwrap_or_else(|error| {
                eprintln!("Gap fill failed for {symbol}: {error}");
                0
            });
            total_rows += rows;
            self.fanout.metrics.inc_gap_fill_rows(rows);
            self.record_run(started_at, mode, &phase, &symbol, "completed", rows, "")
                .await?;
        }
        if !is_streaming_phase(Utc::now()) && self.config.historical_flatfile_update_enabled {
            self.plan_historical_flatfile_update(started_at, mode)
                .await?;
        }
        Ok(total_rows)
    }

    async fn fill_symbol(&self, symbol: &str) -> Result<u64, String> {
        let now = Utc::now();
        let latest = self.latest_compact_ts(symbol).await?;
        let requested_start =
            latest.unwrap_or(now - ChronoDuration::minutes(self.config.gap_fill_lookback_minutes));
        let max_start = now - ChronoDuration::days(self.config.gap_fill_max_lookback_days.max(1));
        let start = requested_start.max(max_start);
        if (now - start).num_seconds() < self.config.gap_fill_min_gap_seconds {
            return Ok(0);
        }

        let mut events = Vec::new();
        events.extend(self.fetch_events(symbol, "trades", start, now).await?);
        events.extend(self.fetch_events(symbol, "quotes", start, now).await?);
        events.sort_by_key(|event| {
            let tie_breaker = match event {
                MarketEvent::Quote(_) => 0u8,
                MarketEvent::Trade(_) => 1u8,
            };
            (event.ts(), tie_breaker)
        });

        let mut count = 0u64;
        for event in events {
            fanout_market_event(event, &self.fanout).await;
            count += 1;
        }
        Ok(count)
    }

    async fn fetch_events(
        &self,
        symbol: &str,
        kind: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<Vec<MarketEvent>, String> {
        let mut next_url = Some(self.rest_url(symbol, kind, start, end));
        let mut pages = 0usize;
        let mut out = Vec::new();
        while let Some(url) = next_url.take() {
            if pages >= self.config.gap_fill_max_pages_per_symbol {
                break;
            }
            pages += 1;
            let payload: Value = self
                .client
                .get(url)
                .send()
                .await
                .map_err(|error| error.to_string())?
                .json()
                .await
                .map_err(|error| error.to_string())?;
            let rows = payload
                .get("results")
                .and_then(Value::as_array)
                .cloned()
                .unwrap_or_default();
            if rows.is_empty() {
                break;
            }
            for row in rows {
                let event = if kind == "trades" {
                    rest_trade_event(symbol, row)
                } else {
                    rest_quote_event(symbol, row)
                };
                if let Some(event) = event {
                    out.push(event);
                }
            }
            next_url = payload
                .get("next_url")
                .and_then(Value::as_str)
                .map(|url| append_api_key(url, &self.config.massive_api_key));
        }
        Ok(out)
    }

    async fn gap_fill_symbols(&self) -> Result<Vec<String>, String> {
        if !self.config.gap_fill_symbols.is_empty() {
            return Ok(self.config.gap_fill_symbols.clone());
        }
        if !self.config.compact_events_enabled {
            return Ok(Vec::new());
        }
        let session_date = Utc::now().date_naive().to_string();
        let sql = format!(
            r#"
            SELECT DISTINCT ticker
            FROM {table}
            WHERE event_date >= toDate('{session_date}') - 1
              AND ticker != ''
            ORDER BY ticker
            FORMAT JSONEachRow
            "#,
            table = self.config.compact_event_table,
        );
        let text = self.query(&sql, true).await?;
        let mut symbols = Vec::new();
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
            if let Some(symbol) = value.get("ticker").and_then(Value::as_str) {
                symbols.push(symbol.to_ascii_uppercase());
            }
        }
        Ok(symbols)
    }

    async fn audit_recent_live_events(&self) -> Result<LiveEventAudit, String> {
        if !self.config.compact_events_enabled {
            return Ok(LiveEventAudit::default());
        }
        let lookback_days = self.config.gap_fill_max_lookback_days.max(1);
        let table = &self.config.compact_event_table;
        let sql = format!(
            r#"
            WITH recent AS
            (
                SELECT
                    ticker,
                    ordinal,
                    sip_timestamp_us,
                    source_sequence,
                    event_type,
                    arrival_sequence
                FROM {table}
                WHERE event_date >= today('UTC') - {lookback_days}
                  AND ticker != ''
            ),
            ticker_ranges AS
            (
                SELECT
                    ticker,
                    count() AS rows,
                    min(ordinal) AS min_ordinal,
                    max(ordinal) AS max_ordinal
                FROM recent
                GROUP BY ticker
            ),
            order_checks AS
            (
                SELECT
                    ticker,
                    countIf(
                        has_prev = 1
                        AND tuple(sip_timestamp_us, source_sequence, event_type, arrival_sequence)
                            < tuple(prev_sip_timestamp_us, prev_source_sequence, prev_event_type, prev_arrival_sequence)
                    ) AS order_errors
                FROM
                (
                    SELECT
                        ticker,
                        ordinal,
                        sip_timestamp_us,
                        source_sequence,
                        event_type,
                        arrival_sequence,
                        lagInFrame(sip_timestamp_us, 1, sip_timestamp_us) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_sip_timestamp_us,
                        lagInFrame(source_sequence, 1, source_sequence) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_source_sequence,
                        lagInFrame(event_type, 1, event_type) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_event_type,
                        lagInFrame(arrival_sequence, 1, arrival_sequence) OVER (PARTITION BY ticker ORDER BY ordinal ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS prev_arrival_sequence,
                        row_number() OVER (PARTITION BY ticker ORDER BY ordinal) > 1 AS has_prev
                    FROM recent
                )
                GROUP BY ticker
            )
            SELECT
                (SELECT count() FROM recent) AS recent_rows,
                (SELECT uniqExact(ticker) FROM recent) AS ticker_count,
                (SELECT count() - uniqExact(ticker, ordinal) FROM recent) AS duplicate_ticker_ordinal_rows,
                (SELECT count() FROM ticker_ranges WHERE rows != max_ordinal - min_ordinal + 1) AS hole_ticker_count,
                (SELECT count() FROM order_checks WHERE order_errors > 0) AS out_of_order_ticker_count
            FORMAT JSONEachRow
            "#,
            table = table,
            lookback_days = lookback_days,
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(LiveEventAudit::default());
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        Ok(LiveEventAudit {
            duplicate_ticker_ordinal_rows: value
                .get("duplicate_ticker_ordinal_rows")
                .and_then(json_u64)
                .unwrap_or(0),
            hole_ticker_count: value
                .get("hole_ticker_count")
                .and_then(json_u64)
                .unwrap_or(0),
            out_of_order_ticker_count: value
                .get("out_of_order_ticker_count")
                .and_then(json_u64)
                .unwrap_or(0),
            recent_rows: value.get("recent_rows").and_then(json_u64).unwrap_or(0),
            ticker_count: value.get("ticker_count").and_then(json_u64).unwrap_or(0),
        })
    }

    async fn plan_historical_flatfile_update(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
    ) -> Result<(), String> {
        let Some(target_end) = self.historical_safe_target_date() else {
            return Ok(());
        };
        let latest = self
            .latest_historical_event_date()
            .await
            .unwrap_or_else(|error| {
                eprintln!("Historical flatfile coverage query failed: {error}");
                self.config.historical_known_coverage_end_date.clone()
            });
        if latest >= target_end.to_string() {
            return Ok(());
        }
        let Some(start_date) = next_date(&latest) else {
            return Ok(());
        };
        let command =
            self.historical_update_command(&start_date.to_string(), &target_end.to_string());
        eprintln!("Historical flatfile update needed: {start_date} to {target_end}");
        eprintln!("Run command: {command}");
        let mut status = "planned";
        let host_role = self.host_role();
        if host_role == "workstation" && self.config.historical_flatfile_autorun {
            match spawn_command(&command) {
                Ok(()) => {
                    status = "launched";
                    eprintln!("Historical flatfile update launched asynchronously.");
                }
                Err(error) => {
                    status = "launch_failed";
                    eprintln!("Historical flatfile update launch failed: {error}");
                }
            }
        }
        self.record_coverage_run(
            started_at,
            "historical_flatfile_events",
            status,
            date_start_utc(start_date),
            date_start_utc(target_end) + ChronoDuration::days(1),
            mode,
            0,
            &host_role,
            &command,
            &json!({
                "latest_historical_event_date": latest,
                "target_end_date": target_end.to_string(),
                "autorun": self.config.historical_flatfile_autorun,
            }),
        )
        .await
    }

    fn rest_url(
        &self,
        symbol: &str,
        kind: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> String {
        let endpoint = if kind == "trades" { "trades" } else { "quotes" };
        format!(
            "https://api.massive.com/v3/{endpoint}/{symbol}?timestamp.gte={}&timestamp.lt={}&order=asc&sort=timestamp&limit=50000&apiKey={}",
            start.timestamp_nanos_opt().unwrap_or_default(),
            end.timestamp_nanos_opt().unwrap_or_default(),
            urlencoding::encode(&self.config.massive_api_key),
        )
    }

    async fn latest_compact_ts(&self, symbol: &str) -> Result<Option<DateTime<Utc>>, String> {
        if !self.config.compact_events_enabled {
            return Ok(None);
        }
        let sql = format!(
            "SELECT max(sip_timestamp_us) AS sip_timestamp_us FROM {table} WHERE ticker = '{}' FORMAT JSONEachRow",
            symbol.replace('\'', "''"),
            table = self.config.compact_event_table,
        );
        let text = self.query(&sql, true).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let value: Value = serde_json::from_str(line).map_err(|error| error.to_string())?;
        let Some(us) = value.get("sip_timestamp_us").and_then(json_u64) else {
            return Ok(None);
        };
        if us == 0 {
            return Ok(None);
        }
        Ok(us_to_datetime(us))
    }

    async fn initialize_tables(&self) -> Result<(), String> {
        self.query(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
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
            &format!(
                r#"
                CREATE TABLE IF NOT EXISTS {table}
                (
                    started_at DateTime64(3, 'UTC'),
                    finished_at DateTime64(3, 'UTC'),
                    coverage_kind LowCardinality(String),
                    status LowCardinality(String),
                    start_ts_utc DateTime64(3, 'UTC'),
                    end_ts_utc DateTime64(3, 'UTC'),
                    action LowCardinality(String),
                    rows_written UInt64,
                    host_role LowCardinality(String),
                    command String,
                    summary_json String
                )
                ENGINE = MergeTree
                ORDER BY (coverage_kind, started_at)
                "#,
                table = self.config.qmd_coverage_table
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn record_run(
        &self,
        started_at: DateTime<Utc>,
        mode: &str,
        phase: &str,
        symbol: &str,
        status: &str,
        rows_written: u64,
        message: &str,
    ) -> Result<(), String> {
        let row = json!({
            "started_at": started_at.to_rfc3339(),
            "mode": mode,
            "phase": phase,
            "symbol": symbol,
            "status": status,
            "rows_written": rows_written,
            "message": message,
        });
        self.query(
            &format!("INSERT INTO qmd_gap_fill_runs FORMAT JSONEachRow\n{}", row),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn record_coverage_run(
        &self,
        started_at: DateTime<Utc>,
        coverage_kind: &str,
        status: &str,
        start_ts_utc: DateTime<Utc>,
        end_ts_utc: DateTime<Utc>,
        action: &str,
        rows_written: u64,
        host_role: &str,
        command: &str,
        summary: &Value,
    ) -> Result<(), String> {
        let row = json!({
            "started_at": started_at.to_rfc3339(),
            "finished_at": Utc::now().to_rfc3339(),
            "coverage_kind": coverage_kind,
            "status": status,
            "start_ts_utc": start_ts_utc.to_rfc3339(),
            "end_ts_utc": end_ts_utc.to_rfc3339(),
            "action": action,
            "rows_written": rows_written,
            "host_role": host_role,
            "command": command,
            "summary_json": summary.to_string(),
        });
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{}",
                self.config.qmd_coverage_table, row
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn latest_historical_event_date(&self) -> Result<String, String> {
        let sql = format!(
            "SELECT max(source_date) FROM {}.events_ordinal_continuity FORMAT TSV",
            self.config.historical_clickhouse_database.replace('`', "")
        );
        let value = self.query(&sql, false).await?.trim().to_string();
        if value.is_empty() || value == "0000-00-00" {
            Ok(self.config.historical_known_coverage_end_date.clone())
        } else {
            Ok(value)
        }
    }

    fn historical_update_command(&self, start_date: &str, end_date: &str) -> String {
        format!(
            "python {}\\pipelines\\market_sip\\flatfiles\\download_update_events.py --database {} --start-date {} --end-date {}",
            self.config.historical_pipeline_code_root,
            self.config.historical_clickhouse_database,
            start_date,
            end_date,
        )
    }

    fn historical_safe_target_date(&self) -> Option<NaiveDate> {
        let today = Utc::now().date_naive();
        let lag = self.config.historical_flatfile_safe_lag_days.max(1);
        Some(previous_weekday(today - ChronoDuration::days(lag)))
    }

    fn host_role(&self) -> String {
        if self.config.qmd_host_role != "auto" {
            return self.config.qmd_host_role.clone();
        }
        let computer = std::env::var("COMPUTERNAME")
            .or_else(|_| std::env::var("HOSTNAME"))
            .unwrap_or_default()
            .to_ascii_uppercase();
        let pipeline_root_exists = FsPath::new(&self.config.historical_pipeline_code_root).exists();
        if computer.contains("DESKTOP-SAAI85T")
            || (pipeline_root_exists
                && self
                    .config
                    .historical_pipeline_code_root
                    .to_ascii_lowercase()
                    .starts_with("d:\\tradingml"))
        {
            "workstation".to_string()
        } else {
            "laptop".to_string()
        }
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

fn should_run_session_catch_up(mode: &str) -> bool {
    matches!(mode, "auto" | "session" | "session_catch_up")
}

fn previous_weekday(mut date: NaiveDate) -> NaiveDate {
    while date.weekday().number_from_monday() > 5 {
        date -= ChronoDuration::days(1);
    }
    date
}

fn next_date(value: &str) -> Option<NaiveDate> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .ok()
        .map(|date| date + ChronoDuration::days(1))
}

fn date_start_utc(date: NaiveDate) -> DateTime<Utc> {
    date.and_hms_opt(0, 0, 0)
        .expect("midnight is valid for every NaiveDate")
        .and_utc()
}

fn spawn_command(command: &str) -> Result<(), String> {
    let status = if cfg!(windows) {
        Command::new("cmd")
            .args(["/C", "start", "", "cmd", "/C", command])
            .spawn()
    } else {
        Command::new("sh").arg("-lc").arg(command).spawn()
    };
    status.map(|_| ()).map_err(|error| error.to_string())
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

fn rest_trade_event(symbol: &str, row: Value) -> Option<MarketEvent> {
    Some(MarketEvent::Trade(TradeEvent {
        conditions: u16_array_field(&row, "conditions"),
        exchange: u16_field(&row, "exchange"),
        ingest_ts: Utc::now(),
        participant_ts: optional_ns_field(&row, "participant_timestamp"),
        price: f64_field(&row, "price"),
        raw: row.clone(),
        sequence: u64_field(&row, "sequence_number"),
        size: f64_field(&row, "size"),
        tape: u8_field(&row, "tape"),
        ticker: symbol.to_ascii_uppercase(),
        trade_id: string_or_number_field(&row, "id"),
        trf_id: u16_field(&row, "trf_id"),
        trf_ts: optional_ns_field(&row, "trf_timestamp"),
        ts: optional_ns_field(&row, "sip_timestamp")?,
    }))
}

fn rest_quote_event(symbol: &str, row: Value) -> Option<MarketEvent> {
    Some(MarketEvent::Quote(QuoteEvent {
        ask_exchange: u16_field(&row, "ask_exchange"),
        ask_price: f64_field(&row, "ask_price"),
        ask_size: u32_field(&row, "ask_size"),
        bid_exchange: u16_field(&row, "bid_exchange"),
        bid_price: f64_field(&row, "bid_price"),
        bid_size: u32_field(&row, "bid_size"),
        conditions: u16_array_field(&row, "conditions"),
        indicators: u16_array_field(&row, "indicators"),
        ingest_ts: Utc::now(),
        raw: row.clone(),
        sequence: u64_field(&row, "sequence_number"),
        tape: u8_field(&row, "tape"),
        ticker: symbol.to_ascii_uppercase(),
        ts: optional_ns_field(&row, "sip_timestamp")?,
    }))
}

fn optional_ns_field(item: &Value, key: &str) -> Option<DateTime<Utc>> {
    let ns = item.get(key).and_then(json_i64)?;
    if ns <= 0 {
        return None;
    }
    ns_to_datetime(ns)
}

fn ns_to_datetime(ns: i64) -> Option<DateTime<Utc>> {
    let seconds = ns.div_euclid(1_000_000_000);
    let nanos = ns.rem_euclid(1_000_000_000) as u32;
    DateTime::<Utc>::from_timestamp(seconds, nanos)
}

fn us_to_datetime(us: u64) -> Option<DateTime<Utc>> {
    let seconds = (us / 1_000_000) as i64;
    let micros = (us % 1_000_000) as u32;
    DateTime::<Utc>::from_timestamp(seconds, micros * 1_000)
}

fn string_or_number_field(item: &Value, key: &str) -> String {
    match item.get(key) {
        Some(Value::String(value)) => value.clone(),
        Some(Value::Number(value)) => value.to_string(),
        _ => String::new(),
    }
}

fn f64_field(item: &Value, key: &str) -> f64 {
    item.get(key).and_then(json_f64).unwrap_or_default()
}

fn u64_field(item: &Value, key: &str) -> u64 {
    item.get(key).and_then(json_u64).unwrap_or_default()
}

fn u32_field(item: &Value, key: &str) -> u32 {
    u64_field(item, key).min(u32::MAX as u64) as u32
}

fn u16_field(item: &Value, key: &str) -> u16 {
    u64_field(item, key).min(u16::MAX as u64) as u16
}

fn u8_field(item: &Value, key: &str) -> u8 {
    u64_field(item, key).min(u8::MAX as u64) as u8
}

fn u16_array_field(item: &Value, key: &str) -> Vec<u16> {
    item.get(key)
        .and_then(Value::as_array)
        .map(|values| {
            values
                .iter()
                .filter_map(json_u64)
                .map(|value| value.min(u16::MAX as u64) as u16)
                .collect()
        })
        .unwrap_or_default()
}

fn json_f64(value: &Value) -> Option<f64> {
    match value {
        Value::Number(number) => number.as_f64(),
        Value::String(text) => text.parse::<f64>().ok(),
        _ => None,
    }
}

fn json_i64(value: &Value) -> Option<i64> {
    match value {
        Value::Number(number) => number
            .as_i64()
            .or_else(|| number.as_u64().and_then(|value| i64::try_from(value).ok())),
        Value::String(text) => text.parse::<i64>().ok(),
        _ => None,
    }
}

fn json_u64(value: &Value) -> Option<u64> {
    match value {
        Value::Number(number) => number
            .as_u64()
            .or_else(|| number.as_i64().and_then(|value| u64::try_from(value).ok())),
        Value::String(text) => text.parse::<u64>().ok(),
        _ => None,
    }
}
