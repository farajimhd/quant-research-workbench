use crate::config::HistoricalGatewayConfig;
use chrono::{DateTime, Datelike, TimeZone, Utc};
use qmd_core::compact_event::{
    CompactEventDecoder, CompactEventReferences, LiveCompactEvent,
    LIVE_COMPACT_EVENT_SCHEMA_VERSION,
};
use qmd_core::event::MarketEvent;
use reqwest::Client;
use serde::{Deserialize, Serialize};

#[derive(Clone, Debug)]
pub struct EventWindow {
    pub end: DateTime<Utc>,
    pub start: DateTime<Utc>,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct EventCoverage {
    pub coverage_table: String,
    pub end: DateTime<Utc>,
    pub event_count: u64,
    pub first_sip_timestamp_us: u64,
    pub last_sip_timestamp_us: u64,
    pub source_tables: Vec<String>,
    pub start: DateTime<Utc>,
    pub ticker_count: u64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct HistoricalCursor {
    pub ordinal: u64,
    pub sip_timestamp_us: u64,
    pub ticker: String,
}

#[derive(Clone)]
pub struct HistoricalEventSource {
    client: Client,
    config: HistoricalGatewayConfig,
    decoder: CompactEventDecoder,
}

#[derive(Debug, Deserialize)]
struct HistoricalRow {
    condition_token_1: u8,
    condition_token_2: u8,
    condition_token_3: u8,
    condition_token_4: u8,
    condition_token_5: u8,
    event_date: String,
    event_meta: u8,
    exchange_primary: u8,
    exchange_secondary: u8,
    ordinal: u64,
    price_primary_int: u32,
    price_secondary_int: u32,
    sip_timestamp_us: u64,
    size_primary: f32,
    size_secondary: f32,
    ticker: String,
}

#[derive(Debug, Deserialize)]
struct EventCoverageRow {
    event_count: u64,
    first_sip_timestamp_us: u64,
    last_sip_timestamp_us: u64,
    ticker_count: u64,
}

impl HistoricalEventSource {
    pub async fn initialize(config: HistoricalGatewayConfig) -> Result<Self, String> {
        let mut source = Self {
            client: Client::new(),
            config,
            decoder: CompactEventDecoder::default(),
        };
        source.health().await?;
        source.decoder = CompactEventReferences::load_from_clickhouse(
            &source.config.clickhouse_url,
            &source.config.clickhouse_user,
            &source.config.clickhouse_password,
            &source.config.clickhouse_database,
        )
        .await?
        .decoder();
        Ok(source)
    }

    pub async fn health(&self) -> Result<(), String> {
        self.query("SELECT 1 FORMAT TSV").await.map(|_| ())
    }

    pub fn market_event(&self, event: &LiveCompactEvent) -> MarketEvent {
        self.decoder.decode(event)
    }

    pub async fn fetch_batch(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        validate_window(window)?;
        let limit = limit.clamp(1, 100_000);
        let ticker_filter = if window.tickers.is_empty() {
            String::new()
        } else {
            let tickers = window
                .tickers
                .iter()
                .map(|ticker| normalize_ticker(ticker))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .map(|ticker| sql_literal(&ticker))
                .collect::<Vec<_>>()
                .join(",");
            format!(" AND ticker IN ({tickers})")
        };
        let cursor_filter = cursor
            .filter(|cursor| cursor.sip_timestamp_us > 0)
            .map(|cursor| {
                format!(
                    " AND tuple(sip_timestamp_us, ticker, ordinal) > tuple({}, {}, {})",
                    cursor.sip_timestamp_us,
                    sql_literal(&cursor.ticker),
                    cursor.ordinal
                )
            })
            .unwrap_or_default();
        let start_us = window.start.timestamp_micros();
        let end_us = window.end.timestamp_micros();
        let last_inclusive = window.end - chrono::Duration::microseconds(1);
        let selects = (window.start.year()..=last_inclusive.year())
            .map(|year| {
                format!(
                    r#"SELECT
                        ticker, ordinal, event_meta, sip_timestamp_us, price_primary_int,
                        price_secondary_int, size_primary, size_secondary, exchange_primary,
                        exchange_secondary, condition_token_1, condition_token_2,
                        condition_token_3, condition_token_4, condition_token_5,
                        toString(event_date) AS event_date
                    FROM {}.{}{}
                    PREWHERE sip_timestamp_us >= {} AND sip_timestamp_us < {}
                    WHERE 1{}{}"#,
                    self.config.clickhouse_database,
                    self.config.table_prefix,
                    year,
                    start_us,
                    end_us,
                    ticker_filter,
                    cursor_filter
                )
            })
            .collect::<Vec<_>>();
        let sql = format!(
            "SELECT * FROM ({}) ORDER BY sip_timestamp_us, ticker, ordinal LIMIT {} FORMAT JSONEachRow",
            selects.join(" UNION ALL "),
            limit
        );
        let text = self.query(&sql).await?;
        let rows = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str::<HistoricalRow>(line).map_err(|error| error.to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        let events = rows.into_iter().map(row_to_event).collect::<Vec<_>>();
        let next_cursor = events.last().map(|event| HistoricalCursor {
            ordinal: event.arrival_sequence,
            sip_timestamp_us: event.sip_timestamp_us,
            ticker: event.ticker.clone(),
        });
        Ok((events, next_cursor))
    }

    pub async fn coverage(&self, window: &EventWindow) -> Result<EventCoverage, String> {
        validate_window(window)?;
        let last_inclusive = window.end - chrono::Duration::microseconds(1);
        let years = (window.start.year()..=last_inclusive.year()).collect::<Vec<_>>();
        let coverage_table = format!("{}.events_ordinal_continuity", self.config.clickhouse_database);
        let sql = format!(
            r#"SELECT
                sum(event_count) AS event_count,
                uniqExact(ticker) AS ticker_count,
                if(event_count = 0, 0, min(first_sip_timestamp_us)) AS first_sip_timestamp_us,
                if(event_count = 0, 0, max(last_sip_timestamp_us)) AS last_sip_timestamp_us
            FROM (
                SELECT
                    ticker,
                    source_date,
                    argMax(event_count, tuple(build_step, updated_at)) AS event_count,
                    argMax(first_sip_timestamp_us, tuple(build_step, updated_at)) AS first_sip_timestamp_us,
                    argMax(last_sip_timestamp_us, tuple(build_step, updated_at)) AS last_sip_timestamp_us
                FROM {coverage_table}
                WHERE source_date >= toDate('{}') AND source_date <= toDate('{}')
                GROUP BY ticker, source_date
            )
            FORMAT JSONEachRow"#,
            window.start.date_naive(),
            last_inclusive.date_naive(),
        );
        let text = self.query(&sql).await?;
        let row = serde_json::from_str::<EventCoverageRow>(text.trim())
            .map_err(|error| format!("invalid historical coverage response: {error}"))?;
        Ok(EventCoverage {
            coverage_table,
            end: window.end,
            event_count: row.event_count,
            first_sip_timestamp_us: row.first_sip_timestamp_us,
            last_sip_timestamp_us: row.last_sip_timestamp_us,
            source_tables: years
                .iter()
                .map(|year| format!("{}.{}{}", self.config.clickhouse_database, self.config.table_prefix, year))
                .collect(),
            start: window.start,
            ticker_count: row.ticker_count,
        })
    }

    async fn query(&self, sql: &str) -> Result<String, String> {
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
            .body(sql.to_string());
        if !self.config.clickhouse_password.is_empty() {
            request = request.header("X-ClickHouse-Key", &self.config.clickhouse_password);
        }
        let response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("ClickHouse HTTP {status}: {}", text.trim()));
        }
        Ok(text)
    }
}

fn row_to_event(row: HistoricalRow) -> LiveCompactEvent {
    let ingest_ts = Utc
        .timestamp_micros(row.sip_timestamp_us as i64)
        .single()
        .unwrap_or_else(Utc::now);
    LiveCompactEvent {
        arrival_sequence: row.ordinal,
        condition_token_1: row.condition_token_1,
        condition_token_2: row.condition_token_2,
        condition_token_3: row.condition_token_3,
        condition_token_4: row.condition_token_4,
        condition_token_5: row.condition_token_5,
        event_date: row.event_date,
        event_meta: row.event_meta,
        exchange_primary: row.exchange_primary,
        exchange_secondary: row.exchange_secondary,
        ingest_ts,
        issue_flags: 0,
        price_primary_int: row.price_primary_int,
        price_secondary_int: row.price_secondary_int,
        schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        sip_timestamp_us: row.sip_timestamp_us,
        size_primary: row.size_primary,
        size_secondary: row.size_secondary,
        source_sequence: row.ordinal,
        ticker: row.ticker,
    }
}

fn validate_window(window: &EventWindow) -> Result<(), String> {
    if window.end <= window.start {
        return Err("end must be later than start".to_string());
    }
    Ok(())
}

fn normalize_ticker(value: &str) -> Result<String, String> {
    let ticker = value.trim().to_ascii_uppercase();
    if ticker.is_empty()
        || !ticker
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-'))
    {
        return Err(format!("invalid ticker: {value}"));
    }
    Ok(ticker)
}

fn sql_literal(value: &str) -> String {
    format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'"))
}

#[cfg(test)]
mod tests {
    use super::{normalize_ticker, row_to_event, HistoricalRow};
    use qmd_core::compact_event::{CompactEventDecoder, LIVE_COMPACT_EVENT_SCHEMA_VERSION};
    use qmd_core::event::MarketEvent;

    #[test]
    fn historical_rows_use_the_live_compact_contract_and_decoder() {
        let compact = row_to_event(HistoricalRow {
            condition_token_1: 3,
            condition_token_2: 0,
            condition_token_3: 0,
            condition_token_4: 0,
            condition_token_5: 0,
            event_date: "2026-07-13".to_string(),
            event_meta: 6,
            exchange_primary: 11,
            exchange_secondary: 12,
            ordinal: 42,
            price_primary_int: 1_001_234,
            price_secondary_int: 1_001_200,
            sip_timestamp_us: 1_752_415_200_000_000,
            size_primary: 20.0,
            size_secondary: 25.0,
            ticker: "AAPL".to_string(),
        });
        assert_eq!(compact.schema_version, LIVE_COMPACT_EVENT_SCHEMA_VERSION);
        assert_eq!(compact.arrival_sequence, 42);
        let decoder =
            CompactEventDecoder::new([(3, 7)], [(4, 8)], [(5, 9)], [(0, 1), (1, 2), (2, 3)]);
        match decoder.decode(&compact) {
            MarketEvent::Quote(quote) => {
                assert!((quote.ask_price - 100.1234).abs() < 0.000001);
                assert!((quote.bid_price - 100.12).abs() < 0.000001);
                assert_eq!(quote.sequence, 42);
                assert_eq!(quote.conditions, vec![7]);
                assert_eq!(quote.tape, 1);
            }
            MarketEvent::Trade(_) => panic!("expected quote"),
        }
    }

    #[test]
    fn ticker_validation_rejects_sql_content() {
        assert_eq!(normalize_ticker("aapl").unwrap(), "AAPL");
        assert!(normalize_ticker("AAPL') OR 1=1").is_err());
    }
}
