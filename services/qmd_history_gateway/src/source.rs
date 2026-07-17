use crate::config::HistoricalGatewayConfig;
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::bars::TradeAggregationRules;
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

#[derive(Clone, Debug, Serialize)]
pub struct LatestEventCoverage {
    pub coverage_table: String,
    pub event_count: u64,
    pub session_date: Option<String>,
    pub ticker_count: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct SourceRevision {
    pub event_count: u64,
    pub max_build_step: u64,
    pub max_updated_at: String,
    pub token: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalMacroChartRow {
    pub bar_end: DateTime<Utc>,
    pub bar_family: String,
    pub bar_start: DateTime<Utc>,
    pub close: f64,
    pub event_count: u64,
    pub high: f64,
    pub is_closed: bool,
    pub low: f64,
    pub open: f64,
    pub session_date: String,
    pub size_sum: f64,
    pub ticker: String,
    pub timeframe: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalMacroChartSnapshot {
    pub as_of: DateTime<Utc>,
    pub bars: Vec<HistoricalMacroChartRow>,
    pub source: String,
    pub ticker: String,
    pub timeframe: String,
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
    trade_rules: TradeAggregationRules,
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

#[derive(Debug, Deserialize)]
struct LatestEventCoverageRow {
    event_count: u64,
    session_date: String,
    ticker_count: u64,
}

#[derive(Debug, Deserialize)]
struct SourceRevisionRow {
    event_count: u64,
    max_build_step: u64,
    max_updated_at: String,
}

#[derive(Debug, Deserialize)]
struct MacroQueryRow {
    bar_end: String,
    bar_family: String,
    bar_start: String,
    close: f64,
    event_count: u64,
    high: f64,
    low: f64,
    open: f64,
    session_date: String,
    size_sum: f64,
    ticker: String,
    timeframe: String,
}

impl HistoricalEventSource {
    pub async fn initialize(config: HistoricalGatewayConfig) -> Result<Self, String> {
        let references = CompactEventReferences::load_from_clickhouse(
            &config.clickhouse_url,
            &config.clickhouse_user,
            &config.clickhouse_password,
            &config.clickhouse_database,
        )
        .await?;
        let source = Self {
            client: Client::new(),
            config,
            decoder: references.decoder(),
            trade_rules: references.trade_aggregation_rules()?,
        };
        source.health().await?;
        Ok(source)
    }

    pub async fn health(&self) -> Result<(), String> {
        self.query("SELECT 1 FORMAT TSV").await.map(|_| ())
    }

    pub fn market_event(&self, event: &LiveCompactEvent) -> MarketEvent {
        self.decoder.decode(event)
    }

    pub fn trade_aggregation_rules(&self) -> TradeAggregationRules {
        self.trade_rules.clone()
    }

    pub async fn source_revision(&self, window: &EventWindow) -> Result<SourceRevision, String> {
        validate_window(window)?;
        if window.tickers.is_empty() {
            return Err("source revision requires at least one ticker".to_string());
        }
        let tickers = window
            .tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?
            .into_iter()
            .map(|ticker| sql_literal(&ticker))
            .collect::<Vec<_>>()
            .join(",");
        let last_inclusive = window.end - chrono::Duration::microseconds(1);
        let continuity_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
        let sql = format!(
            r#"SELECT
                sum(event_count) AS event_count,
                max(latest_build_step) AS max_build_step,
                toString(max(latest_updated_at)) AS max_updated_at
            FROM (
                SELECT
                    ticker,
                    source_date,
                    argMax(event_count, tuple(build_step, updated_at)) AS event_count,
                    argMax(build_step, tuple(build_step, updated_at)) AS latest_build_step,
                    max(updated_at) AS latest_updated_at
                FROM {continuity_table}
                WHERE source_date >= toDate('{}')
                  AND source_date <= toDate('{}')
                  AND ticker IN ({tickers})
                GROUP BY ticker, source_date
            )
            FORMAT JSONEachRow"#,
            window.start.date_naive(),
            last_inclusive.date_naive(),
        );
        let text = self.query(&sql).await?;
        let row = serde_json::from_str::<SourceRevisionRow>(text.trim())
            .map_err(|error| format!("invalid historical source revision response: {error}"))?;
        Ok(SourceRevision {
            event_count: row.event_count,
            max_build_step: row.max_build_step,
            max_updated_at: row.max_updated_at.clone(),
            token: format!(
                "{}:{}:{}",
                row.max_build_step, row.event_count, row.max_updated_at
            ),
        })
    }

    pub async fn fetch_batch(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(window, cursor, limit, false).await
    }

    pub async fn fetch_latest(
        &self,
        window: &EventWindow,
        limit: usize,
    ) -> Result<Vec<LiveCompactEvent>, String> {
        let (mut events, _) = self.fetch_ordered(window, None, limit, true).await?;
        events.reverse();
        Ok(events)
    }

    async fn fetch_ordered(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
        descending: bool,
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
        let direction = if descending { "DESC" } else { "ASC" };
        let sql = format!(
            "SELECT * FROM ({}) ORDER BY sip_timestamp_us {direction}, ticker {direction}, ordinal {direction} LIMIT {} FORMAT JSONEachRow",
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
        let coverage_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
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
                .map(|year| {
                    format!(
                        "{}.{}{}",
                        self.config.clickhouse_database, self.config.table_prefix, year
                    )
                })
                .collect(),
            start: window.start,
            ticker_count: row.ticker_count,
        })
    }

    pub async fn chart_macro_bars(
        &self,
        window: &EventWindow,
        ticker: &str,
        timeframe: &str,
        as_of: DateTime<Utc>,
    ) -> Result<HistoricalMacroChartSnapshot, String> {
        validate_window(window)?;
        let ticker = normalize_ticker(ticker)?;
        if !matches!(timeframe, "1d" | "1mo") {
            return Err("chart macro timeframe must be 1d or 1mo".to_string());
        }
        let table = format!(
            "{}.{}",
            self.config.clickhouse_database, self.config.macro_bars_table
        );
        let projection = if timeframe == "1mo" {
            r#"SELECT
                toString(month_start) AS session_date,
                '1mo' AS timeframe,
                sym AS ticker,
                bar_family,
                toString(min(source_bar_start)) AS bar_start,
                toString(max(source_bar_end)) AS bar_end,
                argMin(open, source_bar_start) AS open,
                argMax(close, source_bar_end) AS close,
                max(high) AS high,
                min(low) AS low,
                sum(size_sum) AS size_sum,
                sum(event_count) AS event_count
            FROM (
                SELECT toStartOfMonth(toDate(session_date)) AS month_start, sym, bar_family, bar_start AS source_bar_start, bar_end AS source_bar_end, open, close, high, low, size_sum, event_count
                FROM {table} FINAL
                WHERE timeframe = '1d'
                  AND sym = {ticker}
                  AND toDate(session_date) >= toDate('{start}')
                  AND toDate(session_date) < toDate('{end}')
                  AND bar_end <= parseDateTime64BestEffort('{as_of}')
            )
            GROUP BY month_start, sym, bar_family
            ORDER BY bar_start, bar_family
            FORMAT JSONEachRow"#
        } else {
            r#"SELECT
                toString(session_date) AS session_date,
                '1d' AS timeframe,
                sym AS ticker,
                bar_family,
                toString(source_bar_start) AS bar_start,
                toString(source_bar_end) AS bar_end,
                open,
                close,
                high,
                low,
                size_sum,
                event_count
            FROM (
                SELECT session_date, sym, bar_family, bar_start AS source_bar_start, bar_end AS source_bar_end, open, close, high, low, size_sum, event_count
                FROM {table} FINAL
                WHERE timeframe = '1d'
                  AND sym = {ticker}
                  AND toDate(session_date) >= toDate('{start}')
                  AND toDate(session_date) < toDate('{end}')
                  AND bar_end <= parseDateTime64BestEffort('{as_of}')
            )
            ORDER BY bar_start, bar_family
            FORMAT JSONEachRow"#
        };
        let sql = projection
            .replace("{table}", &table)
            .replace("{ticker}", &sql_literal(&ticker))
            .replace("{start}", &window.start.date_naive().to_string())
            .replace("{end}", &window.end.date_naive().to_string())
            .replace("{as_of}", &as_of.to_rfc3339());
        let text = self.query(&sql).await?;
        let bars = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<MacroQueryRow>(line)
                    .map_err(|error| format!("invalid macro bar row: {error}"))?;
                let is_closed = macro_bar_is_closed(&row.session_date, timeframe, as_of)?;
                Ok(HistoricalMacroChartRow {
                    bar_end: parse_clickhouse_datetime(&row.bar_end)?,
                    bar_family: row.bar_family,
                    bar_start: parse_clickhouse_datetime(&row.bar_start)?,
                    close: row.close,
                    event_count: row.event_count,
                    high: row.high,
                    is_closed,
                    low: row.low,
                    open: row.open,
                    session_date: row.session_date,
                    size_sum: row.size_sum,
                    ticker: row.ticker,
                    timeframe: row.timeframe,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(HistoricalMacroChartSnapshot {
            as_of,
            bars,
            source: table,
            ticker,
            timeframe: timeframe.to_string(),
        })
    }

    pub async fn latest_coverage_before(
        &self,
        before: Option<chrono::NaiveDate>,
    ) -> Result<LatestEventCoverage, String> {
        let coverage_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
        let before_filter = before
            .map(|value| format!(" AND source_date < toDate('{value}')"))
            .unwrap_or_default();
        let sql = format!(
            r#"SELECT
                toString(source_date) AS session_date,
                sum(canonical_event_count) AS event_count,
                uniqExact(ticker) AS ticker_count
            FROM (
                SELECT
                    ticker,
                    source_date,
                    argMax(event_count, tuple(build_step, updated_at)) AS canonical_event_count
                FROM {coverage_table}
                GROUP BY ticker, source_date
            )
            WHERE canonical_event_count > 0{before_filter}
            GROUP BY source_date
            ORDER BY source_date DESC
            LIMIT 1
            FORMAT JSONEachRow"#,
            before_filter = before_filter,
        );
        let text = self.query(&sql).await?;
        let row = text
            .lines()
            .find(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str::<LatestEventCoverageRow>(line).map_err(|error| {
                    format!("invalid latest historical coverage response: {error}")
                })
            })
            .transpose()?;
        Ok(LatestEventCoverage {
            coverage_table,
            event_count: row.as_ref().map_or(0, |value| value.event_count),
            session_date: row.as_ref().map(|value| value.session_date.clone()),
            ticker_count: row.map_or(0, |value| value.ticker_count),
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

fn parse_clickhouse_datetime(value: &str) -> Result<DateTime<Utc>, String> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .or_else(|_| {
            NaiveDateTime::parse_from_str(value, "%Y-%m-%d %H:%M:%S%.f")
                .map(|value| value.and_utc())
        })
        .map_err(|error| format!("invalid ClickHouse timestamp {value:?}: {error}"))
}

fn macro_bar_is_closed(
    session_date: &str,
    timeframe: &str,
    as_of: DateTime<Utc>,
) -> Result<bool, String> {
    if timeframe != "1mo" {
        return Ok(true);
    }
    let period = NaiveDate::parse_from_str(session_date, "%Y-%m-%d")
        .map_err(|error| format!("invalid macro session date {session_date:?}: {error}"))?;
    let current = as_of.with_timezone(&New_York).date_naive();
    Ok((period.year(), period.month()) < (current.year(), current.month()))
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
    use super::{macro_bar_is_closed, normalize_ticker, row_to_event, HistoricalRow};
    use chrono::{TimeZone, Utc};
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

    #[test]
    fn current_new_york_month_remains_partial() {
        let july_session = "2026-07-01";
        assert!(!macro_bar_is_closed(
            july_session,
            "1mo",
            Utc.with_ymd_and_hms(2026, 7, 10, 14, 0, 0).unwrap(),
        )
        .unwrap());
        assert!(!macro_bar_is_closed(
            july_session,
            "1mo",
            Utc.with_ymd_and_hms(2026, 8, 1, 0, 30, 0).unwrap(),
        )
        .unwrap());
        assert!(macro_bar_is_closed(
            july_session,
            "1mo",
            Utc.with_ymd_and_hms(2026, 8, 1, 14, 0, 0).unwrap(),
        )
        .unwrap());
    }
}
