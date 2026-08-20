use crate::config::HistoricalGatewayConfig;
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime, TimeZone, Utc, Weekday};
use chrono_tz::America::New_York;
use qmd_core::bars::TradeAggregationRules;
use qmd_core::compact_event::{
    CompactEventDecoder, CompactEventReferences, LiveCompactEvent,
    LIVE_COMPACT_EVENT_SCHEMA_VERSION,
};
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{GenericStructureEvent, GENERIC_STRUCTURE_ALGORITHM_VERSION};
use qmd_core::indicators::{
    daily_session_trade_bars_sql, market_structure_reference_sql,
    parse_market_structure_reference_rows, MarketStructureReferenceLevels,
};
use qmd_core::market_products::parse_resolution_us;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::str::FromStr;
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{mpsc, OnceCell};

#[derive(Clone, Debug)]
pub struct EventWindow {
    pub end: DateTime<Utc>,
    pub start: DateTime<Utc>,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct EventCoverage {
    pub complete: bool,
    pub coverage_table: String,
    pub end: DateTime<Utc>,
    pub event_count: u64,
    pub first_sip_timestamp_us: u64,
    pub last_sip_timestamp_us: u64,
    pub source_tables: Vec<String>,
    pub source_plan_hash: String,
    pub start: DateTime<Utc>,
    pub ticker_count: u64,
}

#[derive(Clone, Copy, Debug, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum MarketSourceTier {
    Archive,
    Recent,
    CurrentLive,
    ClosedMarket,
    Gap,
}

#[derive(Clone, Debug, Serialize)]
pub struct MarketSourceSegment {
    pub coverage_state: &'static str,
    pub end: DateTime<Utc>,
    pub queryable_by_history: bool,
    pub source: String,
    pub start: DateTime<Utc>,
    pub tier: MarketSourceTier,
}

#[derive(Clone, Debug, Serialize)]
pub struct MarketSourcePlan {
    pub archive_watermark: Option<String>,
    pub complete_for_history: bool,
    pub end: DateTime<Utc>,
    pub event_schema_version: u16,
    pub ordering: &'static str,
    pub plan_hash: String,
    pub recent_watermark: Option<DateTime<Utc>>,
    pub segments: Vec<MarketSourceSegment>,
    pub start: DateTime<Utc>,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug)]
struct CoverageInterval {
    end: DateTime<Utc>,
    start: DateTime<Utc>,
}

#[derive(Debug, Deserialize)]
struct LiveCompactEventMarketPage {
    cursor_expired: bool,
    events: Vec<LiveCompactEvent>,
    has_more: bool,
    next_after_arrival_sequence: u64,
    through_arrival_sequence: u64,
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
    pub complete_for_history: bool,
    pub event_count: u64,
    pub live_continuation_sequence: Option<u64>,
    pub max_build_step: u64,
    pub max_updated_at: String,
    pub request_complete: bool,
    pub source_plan_hash: String,
    pub source_tiers: Vec<String>,
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

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalIntradayChartRow {
    pub bar_end: DateTime<Utc>,
    pub bar_start: DateTime<Utc>,
    pub close: f64,
    pub event_count: u64,
    pub high: f64,
    pub low: f64,
    pub open: f64,
    pub session_date: String,
    pub size_sum: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalIntradayChartSnapshot {
    pub bars: Vec<HistoricalIntradayChartRow>,
    pub has_more: bool,
    pub next_before: Option<DateTime<Utc>>,
    pub source: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalScannerMarketSnapshot {
    pub as_of: DateTime<Utc>,
    pub event_count: u64,
    pub lookback_minutes: u16,
    pub rows: Vec<HistoricalScannerMarketSnapshotRow>,
    pub source_revision: SourceRevision,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct HistoricalScannerMarketSnapshotRow {
    pub change_5m_pct: f64,
    pub change_pct: f64,
    pub last: f64,
    pub market_event_age_ms: u64,
    pub market_event_at: String,
    pub quote_count: u64,
    pub symbol: String,
    pub trade_count: u64,
    pub volume: f64,
}

#[derive(Clone, Debug, Default, Deserialize, PartialEq, Serialize)]
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
    structure_table_available: Arc<OnceCell<bool>>,
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
    source_sequence: u64,
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

#[derive(Debug, Default, Deserialize)]
struct SourceRevisionRow {
    event_count: u64,
    max_build_step: u64,
    max_updated_at: String,
}

#[derive(Debug, Deserialize)]
struct CoverageIntervalRow {
    coverage_id: String,
    coverage_end_text: String,
    coverage_start_text: String,
    status: String,
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

#[derive(Debug, Deserialize)]
struct IntradayChartQueryRow {
    bar_end: String,
    bar_start: String,
    close: f64,
    event_count: u64,
    high: f64,
    low: f64,
    open: f64,
    session_date: String,
    size_sum: f64,
}

#[derive(Debug, Deserialize)]
struct PersistedStructureEventRow {
    algorithm_version: u16,
    event_id: String,
    level_id: String,
    sym: String,
    timeframe: String,
    event_kind: String,
    direction: i8,
    price: f64,
    lower: f64,
    upper: f64,
    strength: f64,
    confidence: f64,
    lifecycle: String,
    total_volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    neutral_volume: f64,
    trade_count: u64,
    pivot_at_text: String,
    confirmed_at_text: String,
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
            structure_table_available: Arc::new(OnceCell::new()),
            trade_rules: references.trade_aggregation_rules()?,
        };
        source.health().await?;
        Ok(source)
    }

    pub async fn health(&self) -> Result<(), String> {
        self.query("SELECT 1 FORMAT TSV").await.map(|_| ())
    }

    pub async fn source_plan(&self, window: &EventWindow) -> Result<MarketSourcePlan, String> {
        validate_window(window)?;
        let tickers = window
            .tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?;
        let archive = self.latest_coverage_before(None).await?;
        let archive_end = archive
            .session_date
            .as_deref()
            .map(|value| {
                let date = NaiveDate::parse_from_str(value, "%Y-%m-%d")
                    .map_err(|error| format!("invalid archive coverage date {value:?}: {error}"))?;
                archive_session_end_utc(date)
            })
            .transpose()?;
        let mut recent = if archive_end.is_some_and(|end| end >= window.end) {
            Vec::new()
        } else {
            self.recent_coverage_intervals(window).await?
        };
        if window.tickers.len() == 1 {
            if let Some(repaired) = self
                .focused_repair_coverage(&window.tickers[0], window)
                .await?
            {
                recent.push(repaired);
                recent.sort_by_key(|interval| (interval.start, interval.end));
                recent = merge_coverage_intervals(recent);
            }
        }
        Ok(build_source_plan(
            window,
            tickers,
            archive.session_date,
            archive_end,
            recent,
            &self.config,
        ))
    }

    async fn recent_coverage_intervals(
        &self,
        window: &EventWindow,
    ) -> Result<Vec<CoverageInterval>, String> {
        let table = format!(
            "{}.{}",
            self.config.recent_database, self.config.recent_event_coverage_table
        );
        let sql = recent_coverage_sql(&table, window);
        let text = self.query(&sql).await?;
        let rows = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<CoverageIntervalRow>(line)
                    .map_err(|error| format!("invalid recent coverage row: {error}"))?;
                Ok(RecentCoverageRow {
                    coverage_id: row.coverage_id,
                    end: parse_clickhouse_datetime(&row.coverage_end_text)?,
                    start: parse_clickhouse_datetime(&row.coverage_start_text)?,
                    status: row.status,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(materialize_confirmed_recent_coverage(&rows))
    }

    async fn focused_repair_coverage(
        &self,
        ticker: &str,
        window: &EventWindow,
    ) -> Result<Option<CoverageInterval>, String> {
        let symbol = normalize_ticker(ticker)?;
        let table = format!(
            "{}.{}",
            self.config.recent_database, self.config.recent_focused_repair_table
        );
        let sql = format!(
            r#"SELECT
                argMax(status, updated_at_utc) AS status,
                toString(argMax(last_window_start_utc, updated_at_utc)) AS start_text,
                toString(argMax(last_window_end_utc, updated_at_utc)) AS end_text,
                argMax(error_count, updated_at_utc) AS error_count
            FROM {table}
            WHERE symbol = {symbol}
            FORMAT JSONEachRow"#,
            symbol = sql_literal(&symbol),
        );
        let text = self.query(&sql).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        #[derive(Deserialize)]
        struct Row {
            status: String,
            start_text: String,
            end_text: String,
            error_count: u64,
        }
        let row = serde_json::from_str::<Row>(line)
            .map_err(|error| format!("invalid focused repair coverage row: {error}"))?;
        if row.status != "completed" || row.error_count > 0 {
            return Ok(None);
        }
        let start = parse_clickhouse_datetime(&row.start_text)?;
        let end = parse_clickhouse_datetime(&row.end_text)?;
        let start = start.max(window.start);
        let end = end.min(window.end);
        Ok((end > start).then_some(CoverageInterval { start, end }))
    }

    pub fn market_event(&self, event: &LiveCompactEvent) -> MarketEvent {
        self.decoder.decode(event)
    }

    pub fn trade_aggregation_rules(&self) -> TradeAggregationRules {
        self.trade_rules.clone()
    }

    pub async fn scanner_market_snapshot(
        &self,
        window: EventWindow,
        as_of: DateTime<Utc>,
    ) -> Result<HistoricalScannerMarketSnapshot, String> {
        validate_window(&window)?;
        if !window.tickers.is_empty() {
            return Err(
                "historical Scanner market snapshot requires the full market window".into(),
            );
        }
        if as_of < window.start || as_of > window.end {
            return Err("historical Scanner as_of must fall inside its source window".into());
        }
        let mut bounded = window;
        bounded.end = as_of;
        let source_revision = self.source_revision(&bounded).await?;
        if !source_revision.complete_for_history || !source_revision.request_complete {
            return Err("historical Scanner source window is incomplete".into());
        }
        let plan = self.source_plan(&bounded).await?;
        let sql = scanner_market_snapshot_sql(&self.config, &plan, as_of)?;
        let text = self.query_bounded(&sql, 75).await?;
        let rows = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str::<HistoricalScannerMarketSnapshotRow>(line)
                    .map_err(|error| format!("invalid historical Scanner market row: {error}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        let event_count = rows.iter().fold(0_u64, |total, row| {
            total.saturating_add(row.trade_count.saturating_add(row.quote_count))
        });
        let lookback_minutes = (as_of - bounded.start).num_minutes().clamp(1, 390) as u16;
        Ok(HistoricalScannerMarketSnapshot {
            as_of,
            event_count,
            lookback_minutes,
            rows,
            source_revision,
        })
    }

    pub async fn completed_session_dates_before(
        &self,
        as_of: DateTime<Utc>,
        limit: usize,
    ) -> Result<Vec<NaiveDate>, String> {
        let bounded_limit = limit.clamp(1, 64);
        let cutoff_date = as_of.with_timezone(&New_York).date_naive();
        let table = format!(
            "`{}`.`{}`",
            self.config.clickhouse_database, self.config.daily_session_bars_table
        );
        let sql = format!(
            r#"SELECT toString(session_date) AS session_date
            FROM {table} FINAL
            PREWHERE session_date < toDate('{cutoff_date}')
            WHERE available_at_us <= toUInt64({available_at_us})
            GROUP BY session_date
            HAVING uniqExact(session_kind) = 3
               AND max(bar_end_us) <= toUInt64({available_at_us})
               AND countIf(trade_present = 1) > 0
            ORDER BY session_date DESC
            LIMIT {bounded_limit}
            FORMAT JSONEachRow"#,
            available_at_us = as_of.timestamp_micros().max(0),
        );
        #[derive(Deserialize)]
        struct SessionDateRow {
            session_date: String,
        }
        let mut dates = self
            .query(&sql)
            .await?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<SessionDateRow>(line)
                    .map_err(|error| format!("invalid completed session row: {error}"))?;
                NaiveDate::parse_from_str(&row.session_date, "%Y-%m-%d")
                    .map_err(|error| format!("invalid completed session date: {error}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        dates.reverse();
        Ok(dates)
    }

    pub async fn source_revision(&self, window: &EventWindow) -> Result<SourceRevision, String> {
        validate_window(window)?;
        let plan = self.source_plan(window).await?;
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
        let continuity_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
        let archive_bounds = plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::Archive))
            .fold(None, |bounds, segment| match bounds {
                None => Some((segment.start, segment.end)),
                Some((start, end)) => Some((start.min(segment.start), end.max(segment.end))),
            });
        let archive_row = if let Some((archive_start, archive_end)) = archive_bounds {
            let last_inclusive = archive_end - chrono::Duration::microseconds(1);
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
                      {ticker_filter}
                    GROUP BY ticker, source_date
                )
                FORMAT JSONEachRow"#,
                archive_start.date_naive(),
                last_inclusive.date_naive(),
            );
            let text = self.query(&sql).await?;
            serde_json::from_str::<SourceRevisionRow>(text.trim())
                .map_err(|error| format!("invalid historical source revision response: {error}"))?
        } else {
            SourceRevisionRow::default()
        };
        let mut event_count = archive_row.event_count;
        let mut max_build_step = archive_row.max_build_step;
        let mut max_updated_at = archive_row.max_updated_at;
        let mut live_continuation_sequence: Option<u64> = None;
        for segment in plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::Recent))
        {
            let recent_sql = format!(
                r#"SELECT
                    count() AS event_count,
                    max(arrival_sequence) AS max_build_step,
                    toString(max(ingest_ts)) AS max_updated_at
                FROM {}.{} FINAL
                WHERE sip_timestamp_us >= {}
                  AND sip_timestamp_us < {}
                  {}
                FORMAT JSONEachRow"#,
                self.config.recent_database,
                self.config.recent_event_table,
                segment.start.timestamp_micros(),
                segment.end.timestamp_micros(),
                ticker_filter,
            );
            let text = self.query(&recent_sql).await?;
            let row = serde_json::from_str::<SourceRevisionRow>(text.trim())
                .map_err(|error| format!("invalid recent source revision response: {error}"))?;
            event_count = event_count.saturating_add(row.event_count);
            max_build_step = max_build_step.max(row.max_build_step);
            max_updated_at = max_updated_at.max(row.max_updated_at);
        }
        for segment in plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::CurrentLive))
        {
            let live_sequence = self.live_segment_revision(segment, &window.tickers).await?;
            live_continuation_sequence = Some(
                live_continuation_sequence
                    .unwrap_or_default()
                    .max(live_sequence),
            );
            max_build_step = max_build_step.max(live_sequence);
            max_updated_at = max_updated_at.max(segment.end.to_rfc3339());
        }
        let plan_token = plan
            .segments
            .iter()
            .map(|segment| {
                format!(
                    "{:?}:{}:{}",
                    segment.tier,
                    segment.start.timestamp_micros(),
                    segment.end.timestamp_micros()
                )
            })
            .collect::<Vec<_>>()
            .join("|");
        Ok(SourceRevision {
            complete_for_history: plan.complete_for_history,
            event_count,
            live_continuation_sequence,
            max_build_step,
            max_updated_at: max_updated_at.clone(),
            request_complete: !plan
                .segments
                .iter()
                .any(|segment| matches!(segment.tier, MarketSourceTier::Gap)),
            source_plan_hash: plan.plan_hash.clone(),
            source_tiers: plan
                .segments
                .iter()
                .map(|segment| format!("{:?}", segment.tier).to_ascii_lowercase())
                .collect(),
            token: format!(
                "{}:{}:{}:{}:{}",
                max_build_step,
                event_count,
                live_continuation_sequence.unwrap_or_default(),
                max_updated_at,
                plan_token
            ),
        })
    }

    pub async fn fetch_batch(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(window, cursor, limit, false, None).await
    }

    pub async fn fetch_batch_at_revision(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
        live_continuation_sequence: Option<u64>,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(window, cursor, limit, false, live_continuation_sequence)
            .await
    }

    pub async fn fetch_latest(
        &self,
        window: &EventWindow,
        limit: usize,
    ) -> Result<Vec<LiveCompactEvent>, String> {
        let (mut events, _) = self.fetch_ordered(window, None, limit, true, None).await?;
        events.reverse();
        Ok(events)
    }

    pub fn stream_ordered(
        &self,
        window: EventWindow,
        batch_size: usize,
        live_continuation_sequence: Option<u64>,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        self.stream_ordered_filtered(window, batch_size, live_continuation_sequence, None)
    }

    pub fn stream_ordered_filtered(
        &self,
        window: EventWindow,
        batch_size: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        validate_window(&window)?;
        let batch_size = batch_size.clamp(1, 100_000);
        let (sender, receiver) = mpsc::channel(2);
        let source = self.clone();
        tokio::spawn(async move {
            let stream_sender = sender.clone();
            let result = tokio::select! {
                _ = sender.closed() => return,
                result = source.stream_scanner_window(
                    window,
                    batch_size,
                    live_continuation_sequence,
                    event_type_filter,
                    stream_sender,
                ) => result,
            };
            if let Err(error) = result {
                let _ = sender.send(Err(error)).await;
            }
        });
        Ok(receiver)
    }

    async fn stream_scanner_window(
        &self,
        window: EventWindow,
        batch_size: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
        sender: mpsc::Sender<Result<Vec<LiveCompactEvent>, String>>,
    ) -> Result<(), String> {
        let plan = self.source_plan(&window).await?;
        let mut ticker_filter = if window.tickers.is_empty() {
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
        if let Some(event_type) = event_type_filter.filter(|value| *value <= 1) {
            ticker_filter.push_str(&format!(
                " AND bitAnd(source.event_meta, 1) = toUInt8({event_type})"
            ));
        }
        let mut historical_segments = plan
            .segments
            .iter()
            .filter(|segment| segment.queryable_by_history)
            .collect::<Vec<_>>();
        historical_segments.sort_by_key(|segment| segment.start);
        let chunk_duration =
            chrono::Duration::minutes(self.config.scanner_fetch_chunk_minutes.max(1) as i64);
        for segment in historical_segments {
            let mut chunk_start = segment.start;
            while chunk_start < segment.end {
                if sender.is_closed() {
                    return Ok(());
                }
                let mut chunk_end = (chunk_start + chunk_duration).min(segment.end);
                if matches!(segment.tier, MarketSourceTier::Archive) {
                    let next_year = Utc
                        .with_ymd_and_hms(chunk_start.year() + 1, 1, 1, 0, 0, 0)
                        .single()
                        .ok_or_else(|| "invalid archive year boundary".to_string())?;
                    chunk_end = chunk_end.min(next_year);
                }
                let select = match segment.tier {
                    MarketSourceTier::Archive => event_select(
                        &format!(
                            "{}.{}{}",
                            self.config.clickhouse_database,
                            self.config.table_prefix,
                            chunk_start.year()
                        ),
                        false,
                        chunk_start,
                        chunk_end,
                        &ticker_filter,
                        None,
                    ),
                    MarketSourceTier::Recent => event_select(
                        &format!(
                            "{}.{}",
                            self.config.recent_database, self.config.recent_event_table
                        ),
                        true,
                        chunk_start,
                        chunk_end,
                        &ticker_filter,
                        None,
                    ),
                    MarketSourceTier::CurrentLive
                    | MarketSourceTier::ClosedMarket
                    | MarketSourceTier::Gap => {
                        chunk_start = chunk_end;
                        continue;
                    }
                };
                let sql = format!(
                    "SELECT * FROM ({select}) ORDER BY sip_timestamp_us, ticker, ordinal FORMAT TabSeparated"
                );
                self.stream_query_rows(sql, batch_size, sender.clone())
                    .await?;
                chunk_start = chunk_end;
            }
        }
        for segment in plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::CurrentLive))
        {
            let mut events = self
                .fetch_live_segment(segment, &window.tickers, live_continuation_sequence)
                .await?;
            events.sort_by_key(|event| {
                (
                    event.sip_timestamp_us,
                    event.ticker.clone(),
                    event.arrival_sequence,
                )
            });
            for batch in events.chunks(batch_size) {
                sender
                    .send(Ok(batch.to_vec()))
                    .await
                    .map_err(|_| "historical stream consumer closed".to_string())?;
            }
        }
        Ok(())
    }

    async fn stream_query_rows(
        &self,
        sql: String,
        batch_size: usize,
        sender: mpsc::Sender<Result<Vec<LiveCompactEvent>, String>>,
    ) -> Result<(), String> {
        let url = format!(
            "{}/?database={}&enable_http_compression=1&max_query_size=2097152&max_ast_elements=200000&max_expanded_ast_elements=200000",
            self.config.clickhouse_url,
            urlencoding::encode(&self.config.clickhouse_database)
        );
        let mut request = self
            .client
            .post(url)
            .header("Accept-Encoding", "gzip")
            .header("Content-Type", "text/plain; charset=utf-8")
            .header("X-ClickHouse-User", &self.config.clickhouse_user)
            .body(sql);
        if !self.config.clickhouse_password.is_empty() {
            request = request.header("X-ClickHouse-Key", &self.config.clickhouse_password);
        }
        let mut response = request.send().await.map_err(|error| error.to_string())?;
        let status = response.status();
        if !status.is_success() {
            let text = response.text().await.map_err(|error| error.to_string())?;
            return Err(format!("ClickHouse HTTP {status}: {}", text.trim()));
        }
        let mut buffer = Vec::<u8>::with_capacity(256 * 1024);
        let mut batch = Vec::with_capacity(batch_size);
        while let Some(chunk) = response.chunk().await.map_err(|error| error.to_string())? {
            buffer.extend_from_slice(&chunk);
            let mut consumed = 0usize;
            while let Some(relative_end) = buffer[consumed..].iter().position(|byte| *byte == b'\n')
            {
                let line_end = consumed + relative_end;
                if line_end > consumed {
                    let row = parse_historical_tsv_row(&buffer[consumed..line_end])?;
                    batch.push(row_to_event(row));
                    if batch.len() >= batch_size {
                        sender
                            .send(Ok(std::mem::take(&mut batch)))
                            .await
                            .map_err(|_| "historical stream consumer closed".to_string())?;
                        batch = Vec::with_capacity(batch_size);
                    }
                }
                consumed = line_end + 1;
            }
            if consumed > 0 {
                buffer.drain(..consumed);
            }
        }
        if !buffer.iter().all(|byte| byte.is_ascii_whitespace()) {
            let row = parse_historical_tsv_row(&buffer)
                .map_err(|error| format!("invalid final historical stream row: {error}"))?;
            batch.push(row_to_event(row));
        }
        if !batch.is_empty() {
            sender
                .send(Ok(batch))
                .await
                .map_err(|_| "historical stream consumer closed".to_string())?;
        }
        Ok(())
    }

    async fn fetch_ordered(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
        descending: bool,
        live_continuation_sequence: Option<u64>,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        validate_window(window)?;
        let plan = self.source_plan(window).await?;
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
        let mut selects = Vec::new();
        for segment in plan
            .segments
            .iter()
            .filter(|segment| segment.queryable_by_history)
        {
            match segment.tier {
                MarketSourceTier::Archive => {
                    let last_inclusive = segment.end - chrono::Duration::microseconds(1);
                    for year in segment.start.year()..=last_inclusive.year() {
                        selects.push(event_select(
                            &format!(
                                "{}.{}{}",
                                self.config.clickhouse_database, self.config.table_prefix, year
                            ),
                            false,
                            segment.start,
                            segment.end,
                            &ticker_filter,
                            cursor,
                        ));
                    }
                }
                MarketSourceTier::Recent => selects.push(event_select(
                    &format!(
                        "{}.{}",
                        self.config.recent_database, self.config.recent_event_table
                    ),
                    true,
                    segment.start,
                    segment.end,
                    &ticker_filter,
                    cursor,
                )),
                MarketSourceTier::CurrentLive
                | MarketSourceTier::ClosedMarket
                | MarketSourceTier::Gap => {}
            }
        }
        let mut events = if selects.is_empty() {
            Vec::new()
        } else {
            let direction = if descending { "DESC" } else { "ASC" };
            let sql = format!(
                "SELECT * FROM ({}) ORDER BY sip_timestamp_us {direction}, ticker {direction}, ordinal {direction} LIMIT {} FORMAT JSONEachRow",
                selects.join(" UNION ALL "),
                limit
            );
            let text = self.query(&sql).await?;
            text.lines()
                .filter(|line| !line.trim().is_empty())
                .map(|line| {
                    serde_json::from_str::<HistoricalRow>(line).map_err(|error| error.to_string())
                })
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .map(row_to_event)
                .collect::<Vec<_>>()
        };
        for segment in plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::CurrentLive))
        {
            events.extend(
                self.fetch_live_segment(segment, &window.tickers, live_continuation_sequence)
                    .await?,
            );
        }
        if let Some(cursor) = cursor {
            events.retain(|event| event_follows_cursor(event, cursor, descending));
        }
        events.sort_by_key(|event| {
            (
                event.sip_timestamp_us,
                event.ticker.clone(),
                event.arrival_sequence,
            )
        });
        if descending {
            events.reverse();
        }
        events.truncate(limit);
        let next_cursor = events.last().map(|event| HistoricalCursor {
            ordinal: event.arrival_sequence,
            sip_timestamp_us: event.sip_timestamp_us,
            ticker: event.ticker.clone(),
        });
        Ok((events, next_cursor))
    }

    async fn fetch_live_segment(
        &self,
        segment: &MarketSourceSegment,
        tickers: &[String],
        through_arrival_sequence: Option<u64>,
    ) -> Result<Vec<LiveCompactEvent>, String> {
        let url = format!(
            "{}/snapshot/compact-event-market-page",
            self.config.live_gateway_url.trim_end_matches('/')
        );
        let ticker_filter = tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?
            .join(",");
        let mut after_arrival_sequence = 0_u64;
        let mut pinned_through_arrival_sequence = through_arrival_sequence;
        let mut events = Vec::new();
        loop {
            let mut request = self.client.get(&url).query(&[
                ("after_arrival_sequence", after_arrival_sequence.to_string()),
                (
                    "start_sip_timestamp_us",
                    segment.start.timestamp_micros().to_string(),
                ),
                (
                    "end_sip_timestamp_us",
                    segment.end.timestamp_micros().to_string(),
                ),
                ("limit", "100000".to_string()),
            ]);
            if !ticker_filter.is_empty() {
                request = request.query(&[("tickers", ticker_filter.as_str())]);
            }
            if let Some(sequence) = pinned_through_arrival_sequence {
                request = request.query(&[("through_arrival_sequence", sequence.to_string())]);
            }
            let response = request
                .send()
                .await
                .map_err(|error| format!("QMD Live continuation request failed: {error}"))?;
            let status = response.status();
            if !status.is_success() {
                let detail = response.text().await.unwrap_or_default();
                return Err(format!(
                    "QMD Live continuation returned HTTP {status}: {detail}"
                ));
            }
            let page = response
                .json::<LiveCompactEventMarketPage>()
                .await
                .map_err(|error| format!("invalid QMD Live continuation page: {error}"))?;
            if page.cursor_expired {
                return Err(
                    "QMD Live continuation was evicted before the requested source segment; restart after durable coverage advances"
                        .to_string(),
                );
            }
            pinned_through_arrival_sequence.get_or_insert(page.through_arrival_sequence);
            if events.len().saturating_add(page.events.len()) > self.config.max_events_per_request {
                return Err(format!(
                    "QMD Live continuation exceeds QMD_HISTORY_MAX_EVENTS_PER_REQUEST ({})",
                    self.config.max_events_per_request
                ));
            }
            events.extend(page.events);
            if !page.has_more {
                break;
            }
            if page.next_after_arrival_sequence <= after_arrival_sequence {
                return Err("QMD Live continuation cursor made no forward progress".to_string());
            }
            after_arrival_sequence = page.next_after_arrival_sequence;
        }
        Ok(events)
    }

    async fn live_segment_revision(
        &self,
        segment: &MarketSourceSegment,
        tickers: &[String],
    ) -> Result<u64, String> {
        let url = format!(
            "{}/snapshot/compact-event-market-page",
            self.config.live_gateway_url.trim_end_matches('/')
        );
        let ticker_filter = tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?
            .join(",");
        let mut request = self.client.get(&url).query(&[
            ("after_arrival_sequence", "0".to_string()),
            (
                "start_sip_timestamp_us",
                segment.start.timestamp_micros().to_string(),
            ),
            (
                "end_sip_timestamp_us",
                segment.end.timestamp_micros().to_string(),
            ),
            ("limit", "1".to_string()),
        ]);
        if !ticker_filter.is_empty() {
            request = request.query(&[("tickers", ticker_filter.as_str())]);
        }
        let response = request
            .send()
            .await
            .map_err(|error| format!("QMD Live revision request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            let detail = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD Live revision returned HTTP {status}: {detail}"
            ));
        }
        let page = response
            .json::<LiveCompactEventMarketPage>()
            .await
            .map_err(|error| format!("invalid QMD Live revision page: {error}"))?;
        if page.cursor_expired {
            return Err(
                "QMD Live source revision cannot cover the requested segment because retained events were evicted"
                    .to_string(),
            );
        }
        Ok(page.through_arrival_sequence)
    }

    pub async fn coverage(&self, window: &EventWindow) -> Result<EventCoverage, String> {
        validate_window(window)?;
        let plan = self.source_plan(window).await?;
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
        let mut selects = Vec::new();
        let mut source_tables = Vec::new();
        for segment in plan
            .segments
            .iter()
            .filter(|segment| segment.queryable_by_history)
        {
            match segment.tier {
                MarketSourceTier::Archive => {
                    let last_inclusive = segment.end - chrono::Duration::microseconds(1);
                    for year in segment.start.year()..=last_inclusive.year() {
                        let table = format!(
                            "{}.{}{}",
                            self.config.clickhouse_database, self.config.table_prefix, year
                        );
                        source_tables.push(table.clone());
                        selects.push(event_select(
                            &table,
                            false,
                            segment.start,
                            segment.end,
                            &ticker_filter,
                            None,
                        ));
                    }
                }
                MarketSourceTier::Recent => {
                    let table = format!(
                        "{}.{}",
                        self.config.recent_database, self.config.recent_event_table
                    );
                    source_tables.push(table.clone());
                    selects.push(event_select(
                        &table,
                        true,
                        segment.start,
                        segment.end,
                        &ticker_filter,
                        None,
                    ));
                }
                MarketSourceTier::CurrentLive
                | MarketSourceTier::ClosedMarket
                | MarketSourceTier::Gap => {}
            }
        }
        source_tables.sort();
        source_tables.dedup();
        let row = if selects.is_empty() {
            EventCoverageRow {
                event_count: 0,
                first_sip_timestamp_us: 0,
                last_sip_timestamp_us: 0,
                ticker_count: 0,
            }
        } else {
            let sql = format!(
                r#"SELECT
                    count() AS event_count,
                    uniqExact(ticker) AS ticker_count,
                    if(event_count = 0, 0, min(sip_timestamp_us)) AS first_sip_timestamp_us,
                    if(event_count = 0, 0, max(sip_timestamp_us)) AS last_sip_timestamp_us
                FROM ({})
                FORMAT JSONEachRow"#,
                selects.join(" UNION ALL ")
            );
            let text = self.query(&sql).await?;
            serde_json::from_str::<EventCoverageRow>(text.trim())
                .map_err(|error| format!("invalid planned coverage response: {error}"))?
        };
        Ok(EventCoverage {
            complete: plan.complete_for_history,
            coverage_table: format!(
                "{}.events_ordinal_continuity+{}.{}",
                self.config.clickhouse_database,
                self.config.recent_database,
                self.config.recent_event_coverage_table
            ),
            end: window.end,
            event_count: row.event_count,
            first_sip_timestamp_us: row.first_sip_timestamp_us,
            last_sip_timestamp_us: row.last_sip_timestamp_us,
            source_plan_hash: plan.plan_hash,
            source_tables,
            start: window.start,
            ticker_count: row.ticker_count,
        })
    }

    pub async fn persisted_intraday_chart_bars(
        &self,
        window: &EventWindow,
        ticker: &str,
        timeframe: &str,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
    ) -> Result<Option<HistoricalIntradayChartSnapshot>, String> {
        validate_window(window)?;
        let ticker = normalize_ticker(ticker)?;
        let Some(resolution_us) = parse_resolution_us(timeframe) else {
            return Ok(None);
        };
        let start_date = window.start.with_timezone(&New_York).date_naive();
        let end_date = window
            .end
            .checked_sub_signed(chrono::Duration::microseconds(1))
            .unwrap_or(window.end)
            .with_timezone(&New_York)
            .date_naive();
        let start_text = window.start.to_rfc3339();
        let end_text = window.end.to_rfc3339();
        let as_of_text = as_of.to_rfc3339();
        let before_filter = before
            .map(|value| {
                format!(
                    "AND bar_start < parseDateTime64BestEffort({}, 6, 'UTC')",
                    sql_literal(&value.to_rfc3339())
                )
            })
            .unwrap_or_default();
        let requested = limit.saturating_add(1);
        let table = format!(
            "{}.{}",
            self.config.clickhouse_database, self.config.intraday_base_bars_table
        );
        let sql = format!(
            r#"WITH
                fromUnixTimestamp64Micro(
                    toUInt64(toUnixTimestamp(toDateTime(local_date, 'America/New_York'))) * 1000000
                    + bucket_index * label_resolution_us,
                    'UTC'
                ) AS computed_bar_start,
                computed_bar_start + toIntervalMicrosecond(label_resolution_us) AS computed_bar_end
            SELECT
                toString(local_date) AS session_date,
                toString(computed_bar_start) AS bar_start,
                toString(computed_bar_end) AS bar_end,
                open,
                close,
                high,
                low,
                size_sum,
                event_count
            FROM {table}
            PREWHERE local_date >= toDate({start_date})
              AND local_date <= toDate({end_date})
              AND ticker = {ticker}
              AND label_resolution_us = toUInt64({resolution_us})
            WHERE bar_family = 'trade'
              AND computed_bar_start >= parseDateTime64BestEffort({start_text}, 6, 'UTC')
              AND computed_bar_start < parseDateTime64BestEffort({end_text}, 6, 'UTC')
              AND computed_bar_end <= parseDateTime64BestEffort({as_of_text}, 6, 'UTC')
              {before_filter}
            ORDER BY local_date DESC, bucket_index DESC
            LIMIT {requested}
            FORMAT JSONEachRow"#,
            start_date = sql_literal(&start_date.to_string()),
            end_date = sql_literal(&end_date.to_string()),
            ticker = sql_literal(&ticker),
            start_text = sql_literal(&start_text),
            end_text = sql_literal(&end_text),
            as_of_text = sql_literal(&as_of_text),
        );
        let text = match self.query(&sql).await {
            Ok(value) => value,
            Err(error) if missing_table_error(&error) => return Ok(None),
            Err(error) => return Err(error),
        };
        let mut bars = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<IntradayChartQueryRow>(line)
                    .map_err(|error| format!("invalid persisted intraday bar row: {error}"))?;
                Ok(HistoricalIntradayChartRow {
                    bar_end: parse_clickhouse_datetime(&row.bar_end)?,
                    bar_start: parse_clickhouse_datetime(&row.bar_start)?,
                    close: row.close,
                    event_count: row.event_count,
                    high: row.high,
                    low: row.low,
                    open: row.open,
                    session_date: row.session_date,
                    size_sum: row.size_sum,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        if bars.is_empty() {
            return Ok(None);
        }
        let has_more = bars.len() > limit;
        bars.truncate(limit);
        bars.reverse();
        let next_before = has_more.then(|| bars[0].bar_start);
        Ok(Some(HistoricalIntradayChartSnapshot {
            bars,
            has_more,
            next_before,
            source: table,
        }))
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
        if !matches!(timeframe, "1d" | "1w" | "1mo" | "1y") {
            return Err("chart macro timeframe must be 1d, 1w, 1mo, or 1y".to_string());
        }
        let table = format!(
            "{}.{}",
            self.config.clickhouse_database, self.config.daily_session_bars_table
        );
        let daily_bars = daily_session_trade_bars_sql(
            &self.config.clickhouse_database,
            &self.config.daily_session_bars_table,
            Some(&ticker),
            window.start.date_naive(),
            window.end.date_naive(),
            as_of,
        )?;
        let projection = if timeframe != "1d" {
            let period_expression = match timeframe {
                "1w" => "toMonday(session_date)",
                "1mo" => "toStartOfMonth(session_date)",
                "1y" => "toStartOfYear(session_date)",
                _ => unreachable!(),
            };
            format!(
                r#"SELECT
                toString(period_start) AS session_date,
                '{timeframe}' AS timeframe,
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
                SELECT {period_expression} AS period_start, sym, 'trade' AS bar_family, bar_start AS source_bar_start, bar_end AS source_bar_end, open, close, high, low, size_sum, event_count
                FROM ({daily_bars})
            )
            GROUP BY period_start, sym, bar_family
            ORDER BY bar_start, bar_family
            FORMAT JSONEachRow"#
            )
        } else {
            format!(
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
                SELECT session_date, sym, 'trade' AS bar_family, bar_start AS source_bar_start, bar_end AS source_bar_end, open, close, high, low, size_sum, event_count
                FROM ({daily_bars})
            )
            ORDER BY bar_start, bar_family
            FORMAT JSONEachRow"#
            )
        };
        let sql = projection;
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

    pub async fn market_structure_reference_levels(
        &self,
        ticker: &str,
        as_of: DateTime<Utc>,
    ) -> Result<MarketStructureReferenceLevels, String> {
        let ticker = normalize_ticker(ticker)?;
        let sql = market_structure_reference_sql(
            &self.config.clickhouse_database,
            &self.config.daily_session_bars_table,
            Some(&ticker),
            as_of,
        )?;
        let text = self.query(&sql).await?;
        Ok(parse_market_structure_reference_rows(&text)?
            .remove(&ticker)
            .unwrap_or_default())
    }

    pub async fn market_structure_reference_levels_all(
        &self,
        as_of: DateTime<Utc>,
    ) -> Result<std::collections::HashMap<String, MarketStructureReferenceLevels>, String> {
        let sql = market_structure_reference_sql(
            &self.config.clickhouse_database,
            &self.config.daily_session_bars_table,
            None,
            as_of,
        )?;
        parse_market_structure_reference_rows(&self.query(&sql).await?)
    }

    pub async fn persisted_structure_events_before(
        &self,
        ticker: &str,
        before: DateTime<Utc>,
    ) -> Result<Vec<GenericStructureEvent>, String> {
        let ticker = normalize_ticker(ticker)?;
        let table = format!(
            "{}.{}",
            self.config.structure_database, self.config.structure_events_table
        );
        let exists_sql = format!(
            "SELECT count() FROM system.tables WHERE database = {} AND name = {} FORMAT TSV",
            sql_literal(&self.config.structure_database),
            sql_literal(&self.config.structure_events_table)
        );
        let available = *self
            .structure_table_available
            .get_or_try_init(|| async {
                Ok::<bool, String>(self.query(&exists_sql).await?.trim() == "1")
            })
            .await?;
        if !available {
            return Ok(Vec::new());
        }
        let sql = persisted_structure_events_sql(&table, &ticker, before);
        let text = self.query(&sql).await?;
        let mut events = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<PersistedStructureEventRow>(line)
                    .map_err(|error| format!("invalid persisted structure event row: {error}"))?;
                Ok(GenericStructureEvent {
                    algorithm_version: row.algorithm_version,
                    event_id: row.event_id.parse::<u64>().map_err(|error| {
                        format!("invalid persisted structure event id: {error}")
                    })?,
                    level_id: row.level_id.parse::<u64>().map_err(|error| {
                        format!("invalid persisted structure level id: {error}")
                    })?,
                    sym: row.sym,
                    timeframe: row.timeframe,
                    event_kind: row.event_kind,
                    direction: row.direction,
                    price: row.price,
                    lower: row.lower,
                    upper: row.upper,
                    strength: row.strength,
                    confidence: row.confidence,
                    lifecycle: row.lifecycle,
                    total_volume: row.total_volume,
                    buy_volume: row.buy_volume,
                    sell_volume: row.sell_volume,
                    neutral_volume: row.neutral_volume,
                    trade_count: row.trade_count,
                    pivot_at: parse_clickhouse_datetime(&row.pivot_at_text)?,
                    confirmed_at: parse_clickhouse_datetime(&row.confirmed_at_text)?,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        events.sort_by_key(|event| (event.confirmed_at, event.event_id));
        Ok(events)
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
            "{}/?database={}&enable_http_compression=1",
            self.config.clickhouse_url,
            urlencoding::encode(&self.config.clickhouse_database)
        );
        let mut request = self
            .client
            .post(url)
            .header("Accept-Encoding", "gzip")
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

    async fn query_bounded(&self, sql: &str, timeout_seconds: u64) -> Result<String, String> {
        let timeout_seconds = timeout_seconds.clamp(1, 300);
        let url = format!(
            "{}/?database={}&enable_http_compression=1&max_execution_time={}",
            self.config.clickhouse_url,
            urlencoding::encode(&self.config.clickhouse_database),
            timeout_seconds
        );
        let mut request = self
            .client
            .post(url)
            .timeout(Duration::from_secs(timeout_seconds + 5))
            .header("Accept-Encoding", "gzip")
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

fn scanner_market_snapshot_sql(
    config: &HistoricalGatewayConfig,
    plan: &MarketSourcePlan,
    as_of: DateTime<Utc>,
) -> Result<String, String> {
    let mut selects = Vec::new();
    for segment in plan
        .segments
        .iter()
        .filter(|segment| segment.queryable_by_history)
    {
        match segment.tier {
            MarketSourceTier::Archive => {
                let last_inclusive = segment.end - chrono::Duration::microseconds(1);
                for year in segment.start.year()..=last_inclusive.year() {
                    selects.push(scanner_market_select(
                        &format!(
                            "{}.{}{}",
                            config.clickhouse_database, config.table_prefix, year
                        ),
                        false,
                        segment.start,
                        segment.end,
                    ));
                }
            }
            MarketSourceTier::Recent => selects.push(scanner_market_select(
                &format!("{}.{}", config.recent_database, config.recent_event_table),
                true,
                segment.start,
                segment.end,
            )),
            MarketSourceTier::CurrentLive
            | MarketSourceTier::ClosedMarket
            | MarketSourceTier::Gap => {}
        }
    }
    if selects.is_empty() {
        return Err("historical Scanner market snapshot has no queryable source segment".into());
    }
    let five_minute_us = (as_of - chrono::Duration::minutes(5)).timestamp_micros();
    Ok(format!(
        r#"SELECT
            upper(ticker) AS symbol,
            last,
            if(first_price = 0, 0, (last / first_price - 1) * 100) AS change_pct,
            if(first_5m_price = 0, 0, (last / first_5m_price - 1) * 100) AS change_5m_pct,
            toString(fromUnixTimestamp64Micro(toInt64(last_event_ts_us), 'UTC')) AS market_event_at,
            toUInt64(greatest(0, intDiv({as_of_us} - toInt64(last_event_ts_us), 1000))) AS market_event_age_ms,
            volume,
            trade_count,
            quote_count
        FROM
        (
            SELECT
                ticker,
                argMax(price, tuple(sip_timestamp_us, ordinal)) AS last,
                argMin(price, tuple(sip_timestamp_us, ordinal)) AS first_price,
                argMinIf(price, tuple(sip_timestamp_us, ordinal), sip_timestamp_us >= {five_minute_us}) AS first_5m_price,
                max(sip_timestamp_us) AS last_event_ts_us,
                sum(toFloat64(size_primary)) AS volume,
                count() AS trade_count,
                toUInt64(0) AS quote_count
            FROM
            (
                SELECT
                    ticker,
                    ordinal,
                    sip_timestamp_us,
                    size_primary,
                    toFloat64(price_primary_int) / if(bitAnd(event_meta, 2) != 0, 10000., 100.) AS price
                FROM ({source})
            )
            GROUP BY ticker
            HAVING trade_count > 0
        )
        ORDER BY abs(change_5m_pct) DESC, symbol ASC
        LIMIT 20000
        FORMAT JSONEachRow"#,
        as_of_us = as_of.timestamp_micros(),
        source = selects.join(" UNION ALL "),
    ))
}

fn scanner_market_select(
    table: &str,
    recent: bool,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> String {
    let ordinal = if recent {
        "source.arrival_sequence"
    } else {
        "source.ordinal"
    };
    let final_clause = if recent { " FINAL" } else { "" };
    let last_inclusive = end - chrono::Duration::microseconds(1);
    format!(
        r#"SELECT
            source.ticker,
            {ordinal} AS ordinal,
            source.event_meta,
            source.sip_timestamp_us,
            source.price_primary_int,
            source.size_primary
        FROM {table} AS source{final_clause}
        PREWHERE source.event_date >= toDate('{start_date}')
          AND source.event_date <= toDate('{end_date}')
          AND source.sip_timestamp_us >= {start_us}
          AND source.sip_timestamp_us < {end_us}
          AND bitAnd(source.event_meta, 1) = 1
          AND source.price_primary_int > 0
          AND source.size_primary > 0"#,
        start_date = start.date_naive(),
        end_date = last_inclusive.date_naive(),
        start_us = start.timestamp_micros(),
        end_us = end.timestamp_micros(),
    )
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

fn missing_table_error(error: &str) -> bool {
    error.contains("UNKNOWN_TABLE") || error.contains("doesn't exist")
}

fn event_select(
    table: &str,
    recent: bool,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    ticker_filter: &str,
    cursor: Option<&HistoricalCursor>,
) -> String {
    let ordinal = if recent {
        "source.arrival_sequence"
    } else {
        "source.ordinal"
    };
    // Legacy archive compact tables do not persist the vendor source sequence;
    // their deterministic ordinal is the only lossless ordering identity.
    // Recent q_live rows retain the original source sequence.
    let source_sequence = if recent {
        "source.source_sequence"
    } else {
        "source.ordinal"
    };
    let final_clause = if recent { " FINAL" } else { "" };
    let cursor_filter = cursor
        .filter(|value| value.sip_timestamp_us > 0)
        .map(|value| {
            format!(
                " AND tuple(source.sip_timestamp_us, upper(source.ticker), {ordinal}) > tuple({}, {}, {})",
                value.sip_timestamp_us,
                sql_literal(&value.ticker),
                value.ordinal,
            )
        })
        .unwrap_or_default();
    let last_inclusive = end - chrono::Duration::microseconds(1);
    format!(
        r#"SELECT
            upper(source.ticker) AS ticker,
            {ordinal} AS ordinal,
            {source_sequence} AS source_sequence,
            source.event_meta,
            source.sip_timestamp_us,
            source.price_primary_int,
            source.price_secondary_int,
            source.size_primary,
            source.size_secondary,
            source.exchange_primary,
            source.exchange_secondary,
            source.condition_token_1,
            source.condition_token_2,
            source.condition_token_3,
            source.condition_token_4,
            source.condition_token_5,
            toString(source.event_date) AS event_date
        FROM {table} AS source{final_clause}
        PREWHERE source.event_date >= toDate('{start_date}')
          AND source.event_date <= toDate('{end_date}')
          AND source.sip_timestamp_us >= {start_us}
          AND source.sip_timestamp_us < {end_us}
        WHERE 1{ticker_filter}{cursor_filter}"#,
        start_date = start.date_naive(),
        end_date = last_inclusive.date_naive(),
        start_us = start.timestamp_micros(),
        end_us = end.timestamp_micros(),
    )
}

fn archive_session_end_utc(date: NaiveDate) -> Result<DateTime<Utc>, String> {
    New_York
        .with_ymd_and_hms(date.year(), date.month(), date.day(), 20, 0, 0)
        .single()
        .map(|value| value.with_timezone(&Utc))
        .ok_or_else(|| format!("invalid New York archive session boundary for {date}"))
}

fn merge_coverage_intervals(intervals: Vec<CoverageInterval>) -> Vec<CoverageInterval> {
    let mut merged: Vec<CoverageInterval> = Vec::new();
    for interval in intervals {
        if interval.end <= interval.start {
            continue;
        }
        if let Some(previous) = merged.last_mut() {
            if interval.start <= previous.end {
                previous.end = previous.end.max(interval.end);
                continue;
            }
        }
        merged.push(interval);
    }
    merged
}

#[derive(Clone, Debug)]
struct RecentCoverageRow {
    coverage_id: String,
    end: DateTime<Utc>,
    start: DateTime<Utc>,
    status: String,
}

fn coverage_run_id(coverage_id: &str, prefix: &str) -> String {
    coverage_id
        .strip_prefix(prefix)
        .unwrap_or(coverage_id)
        .split("::")
        .next()
        .unwrap_or_default()
        .to_string()
}

fn materialize_confirmed_recent_coverage(rows: &[RecentCoverageRow]) -> Vec<CoverageInterval> {
    let mut direct = Vec::new();
    let mut compact_by_run: BTreeMap<String, Vec<&RecentCoverageRow>> = BTreeMap::new();
    let mut intraday_by_run: BTreeMap<String, Vec<&RecentCoverageRow>> = BTreeMap::new();
    for row in rows {
        match row.status.as_str() {
            "repair_completed" | "coverage_bootstrap" => direct.push(CoverageInterval {
                end: row.end,
                start: row.start,
            }),
            "compact_persisted" => compact_by_run
                .entry(coverage_run_id(&row.coverage_id, "compact_"))
                .or_default()
                .push(row),
            "intraday_bars_persisted" => intraday_by_run
                .entry(coverage_run_id(&row.coverage_id, "intraday_"))
                .or_default()
                .push(row),
            _ => {}
        }
    }
    for (run_id, compact_rows) in compact_by_run {
        let Some(bar_rows) = intraday_by_run.get(&run_id) else {
            continue;
        };
        for compact in compact_rows {
            for bars in bar_rows {
                let start = compact.start.max(bars.start);
                let end = compact.end.min(bars.end);
                if end > start {
                    direct.push(CoverageInterval { end, start });
                }
            }
        }
    }
    direct.sort_by_key(|interval| (interval.start, interval.end));
    merge_coverage_intervals(direct)
}

fn build_source_plan(
    window: &EventWindow,
    tickers: Vec<String>,
    archive_watermark: Option<String>,
    archive_end: Option<DateTime<Utc>>,
    recent: Vec<CoverageInterval>,
    config: &HistoricalGatewayConfig,
) -> MarketSourcePlan {
    let mut segments = Vec::new();
    let mut cursor = window.start;
    if let Some(end) = archive_end.map(|value| value.min(window.end)) {
        if end > cursor {
            segments.push(MarketSourceSegment {
                coverage_state: "covered",
                end,
                queryable_by_history: true,
                source: format!("{}.{}YYYY", config.clickhouse_database, config.table_prefix),
                start: cursor,
                tier: MarketSourceTier::Archive,
            });
            cursor = end;
        }
    }
    for interval in recent {
        let start = interval.start.max(cursor).max(window.start);
        let end = interval.end.min(window.end);
        if end <= start {
            continue;
        }
        if start > cursor {
            append_scheduled_gap_segments(&mut segments, cursor, start);
        }
        segments.push(MarketSourceSegment {
            coverage_state: "covered",
            end,
            queryable_by_history: true,
            source: format!("{}.{}", config.recent_database, config.recent_event_table),
            start,
            tier: MarketSourceTier::Recent,
        });
        cursor = end;
    }
    if cursor < window.end {
        append_live_tail_segments(&mut segments, cursor, window.end, &config.live_gateway_url);
    }
    let recent_watermark = segments
        .iter()
        .filter(|segment| matches!(segment.tier, MarketSourceTier::Recent))
        .map(|segment| segment.end)
        .max();
    let complete_for_history = segments.iter().all(|segment| segment.queryable_by_history);
    let plan_hash = source_plan_hash(window.start, window.end, &tickers, &segments);
    MarketSourcePlan {
        archive_watermark,
        complete_for_history,
        end: window.end,
        event_schema_version: LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        ordering: "sip_timestamp_us,ticker,arrival_sequence",
        plan_hash,
        recent_watermark,
        segments,
        start: window.start,
        tickers,
    }
}

fn append_scheduled_gap_segments(
    segments: &mut Vec<MarketSourceSegment>,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) {
    let mut cursor = start;
    while cursor < end {
        let (closed, boundary) = market_calendar_segment_boundary(cursor);
        let tier = if closed {
            MarketSourceTier::ClosedMarket
        } else {
            MarketSourceTier::Gap
        };
        let segment_end = boundary.min(end);
        let (coverage_state, queryable_by_history, source) = match tier {
            MarketSourceTier::ClosedMarket => (
                "covered_empty",
                true,
                "market_calendar:scheduled_closed".to_string(),
            ),
            MarketSourceTier::Gap => ("uncovered", false, "coverage_gap".to_string()),
            _ => unreachable!("scheduled gap splitter emits only closed or gap tiers"),
        };
        if let Some(previous) = segments.last_mut() {
            if previous.tier == tier && previous.end == cursor && previous.source == source {
                previous.end = segment_end;
                cursor = segment_end;
                continue;
            }
        }
        segments.push(MarketSourceSegment {
            coverage_state,
            end: segment_end,
            queryable_by_history,
            source,
            start: cursor,
            tier,
        });
        cursor = segment_end;
    }
}

fn append_live_tail_segments(
    segments: &mut Vec<MarketSourceSegment>,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    live_gateway_url: &str,
) {
    let mut cursor = start;
    while cursor < end {
        let (closed, boundary) = market_calendar_segment_boundary(cursor);
        let segment_end = boundary.min(end);
        let (coverage_state, queryable_by_history, source, tier) = if closed {
            (
                "covered_empty",
                true,
                "market_calendar:scheduled_closed".to_string(),
                MarketSourceTier::ClosedMarket,
            )
        } else {
            (
                "requires_live_continuation",
                false,
                live_gateway_url.to_string(),
                MarketSourceTier::CurrentLive,
            )
        };
        if let Some(previous) = segments.last_mut() {
            if previous.tier == tier && previous.end == cursor && previous.source == source {
                previous.end = segment_end;
                cursor = segment_end;
                continue;
            }
        }
        segments.push(MarketSourceSegment {
            coverage_state,
            end: segment_end,
            queryable_by_history,
            source,
            start: cursor,
            tier,
        });
        cursor = segment_end;
    }
}

fn market_calendar_segment_boundary(cursor: DateTime<Utc>) -> (bool, DateTime<Utc>) {
    let local = cursor.with_timezone(&New_York);
    let date = local.date_naive();
    let local_boundary = |date: NaiveDate, hour| {
        New_York
            .with_ymd_and_hms(date.year(), date.month(), date.day(), hour, 0, 0)
            .single()
            .expect("New York market boundary must be unique")
    };
    let next_date = date.succ_opt().expect("market calendar date must advance");
    let next_midnight = local_boundary(next_date, 0);
    let session_start = local_boundary(date, 4);
    let session_end = local_boundary(date, 20);
    let (closed, boundary) = if matches!(date.weekday(), Weekday::Sat | Weekday::Sun) {
        (true, next_midnight)
    } else if local < session_start {
        (true, session_start)
    } else if local >= session_end {
        (true, next_midnight)
    } else {
        (false, session_end)
    };
    (closed, boundary.with_timezone(&Utc))
}

fn source_plan_hash(
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    tickers: &[String],
    segments: &[MarketSourceSegment],
) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    let mut update = |value: &str| {
        for byte in value.as_bytes() {
            hash ^= u64::from(*byte);
            hash = hash.wrapping_mul(0x100000001b3);
        }
        hash ^= 0xff;
        hash = hash.wrapping_mul(0x100000001b3);
    };
    update(&start.to_rfc3339());
    update(&end.to_rfc3339());
    for ticker in tickers {
        update(ticker);
    }
    for segment in segments {
        update(&format!("{:?}", segment.tier));
        update(&segment.start.to_rfc3339());
        update(&segment.end.to_rfc3339());
        update(segment.coverage_state);
        update(&segment.source);
    }
    format!("fnv1a64:{hash:016x}")
}

fn macro_bar_is_closed(
    session_date: &str,
    timeframe: &str,
    as_of: DateTime<Utc>,
) -> Result<bool, String> {
    if timeframe == "1d" {
        return Ok(true);
    }
    let period = NaiveDate::parse_from_str(session_date, "%Y-%m-%d")
        .map_err(|error| format!("invalid macro session date {session_date:?}: {error}"))?;
    let current = as_of.with_timezone(&New_York).date_naive();
    match timeframe {
        "1w" => {
            let current_week_start =
                current - chrono::Duration::days(current.weekday().num_days_from_monday() as i64);
            Ok(period < current_week_start)
        }
        "1mo" => Ok((period.year(), period.month()) < (current.year(), current.month())),
        "1y" => Ok(period.year() < current.year()),
        _ => Err(format!("unsupported macro timeframe {timeframe}")),
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
        source_sequence: row.source_sequence,
        ticker: row.ticker,
    }
}

fn parse_historical_tsv_row(bytes: &[u8]) -> Result<HistoricalRow, String> {
    let text = std::str::from_utf8(bytes)
        .map_err(|error| format!("invalid historical stream UTF-8: {error}"))?;
    let fields = text.split('\t').collect::<Vec<_>>();
    if fields.len() != 17 {
        return Err(format!(
            "invalid historical stream column count: expected 17, found {}",
            fields.len()
        ));
    }
    Ok(HistoricalRow {
        ticker: fields[0].to_string(),
        ordinal: parse_tsv_field(&fields, 1, "ordinal")?,
        source_sequence: parse_tsv_field(&fields, 2, "source_sequence")?,
        event_meta: parse_tsv_field(&fields, 3, "event_meta")?,
        sip_timestamp_us: parse_tsv_field(&fields, 4, "sip_timestamp_us")?,
        price_primary_int: parse_tsv_field(&fields, 5, "price_primary_int")?,
        price_secondary_int: parse_tsv_field(&fields, 6, "price_secondary_int")?,
        size_primary: parse_tsv_field(&fields, 7, "size_primary")?,
        size_secondary: parse_tsv_field(&fields, 8, "size_secondary")?,
        exchange_primary: parse_tsv_field(&fields, 9, "exchange_primary")?,
        exchange_secondary: parse_tsv_field(&fields, 10, "exchange_secondary")?,
        condition_token_1: parse_tsv_field(&fields, 11, "condition_token_1")?,
        condition_token_2: parse_tsv_field(&fields, 12, "condition_token_2")?,
        condition_token_3: parse_tsv_field(&fields, 13, "condition_token_3")?,
        condition_token_4: parse_tsv_field(&fields, 14, "condition_token_4")?,
        condition_token_5: parse_tsv_field(&fields, 15, "condition_token_5")?,
        event_date: fields[16].to_string(),
    })
}

fn parse_tsv_field<T>(fields: &[&str], index: usize, name: &str) -> Result<T, String>
where
    T: FromStr,
    T::Err: std::fmt::Display,
{
    fields[index]
        .parse()
        .map_err(|error| format!("invalid historical stream {name}: {error}"))
}

fn event_follows_cursor(
    event: &LiveCompactEvent,
    cursor: &HistoricalCursor,
    descending: bool,
) -> bool {
    let event_key = (
        event.sip_timestamp_us,
        event.ticker.as_str(),
        event.arrival_sequence,
    );
    let cursor_key = (
        cursor.sip_timestamp_us,
        cursor.ticker.as_str(),
        cursor.ordinal,
    );
    if descending {
        event_key < cursor_key
    } else {
        event_key > cursor_key
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

fn recent_coverage_sql(table: &str, window: &EventWindow) -> String {
    format!(
        r#"SELECT
                coverage_id,
                status,
                formatDateTime(coverage_start_utc, '%Y-%m-%dT%H:%i:%s.%fZ', 'UTC') AS coverage_start_text,
                formatDateTime(coverage_end_utc, '%Y-%m-%dT%H:%i:%s.%fZ', 'UTC') AS coverage_end_text
            FROM {table} FINAL
            WHERE coverage_kind = 'q_live_events'
              AND status IN ('repair_completed', 'coverage_bootstrap', 'compact_persisted', 'intraday_bars_persisted')
              AND coverage_end_utc > parseDateTime64BestEffort({start})
              AND coverage_start_utc < parseDateTime64BestEffort({end})
            ORDER BY coverage_start_utc, coverage_end_utc
            FORMAT JSONEachRow"#,
        start = sql_literal(&window.start.to_rfc3339()),
        end = sql_literal(&window.end.to_rfc3339()),
    )
}

fn persisted_structure_events_sql(table: &str, ticker: &str, before: DateTime<Utc>) -> String {
    format!(
        r#"SELECT
                algorithm_version,
                toString(event_id) AS event_id,
                toString(level_id) AS level_id,
                sym,
                timeframe,
                event_kind,
                direction,
                price,
                lower,
                upper,
                strength,
                confidence,
                lifecycle,
                total_volume,
                buy_volume,
                sell_volume,
                neutral_volume,
                trade_count,
                formatDateTime(pivot_at, '%Y-%m-%dT%H:%i:%s.%fZ', 'UTC') AS pivot_at_text,
                formatDateTime(confirmed_at, '%Y-%m-%dT%H:%i:%s.%fZ', 'UTC') AS confirmed_at_text
            FROM {table} FINAL
            WHERE algorithm_version = {version}
              AND sym = {ticker}
              AND confirmed_at < parseDateTime64BestEffort({before})
              AND confirmed_at >= parseDateTime64BestEffort({before}) - INTERVAL 90 DAY
            ORDER BY confirmed_at DESC, event_id DESC
            LIMIT 5000
            FORMAT JSONEachRow"#,
        version = GENERIC_STRUCTURE_ALGORITHM_VERSION,
        ticker = sql_literal(ticker),
        before = sql_literal(&before.to_rfc3339()),
    )
}

#[cfg(test)]
mod tests {
    use super::{
        append_scheduled_gap_segments, archive_session_end_utc, build_source_plan, event_select,
        macro_bar_is_closed, materialize_confirmed_recent_coverage, merge_coverage_intervals,
        normalize_ticker, parse_historical_tsv_row, persisted_structure_events_sql,
        recent_coverage_sql, row_to_event, CoverageInterval, EventWindow, HistoricalRow,
        MarketSourceTier, RecentCoverageRow,
    };
    use crate::config::HistoricalGatewayConfig;
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
            source_sequence: 42,
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
                assert_eq!(quote.raw["correlation_id"], "source:AAPL:2026-07-13");
                assert_eq!(quote.raw["causation_id"], compact.causation_id());
            }
            MarketEvent::Trade(_) => panic!("expected quote"),
        }
    }

    #[test]
    fn ordered_stream_tsv_parser_preserves_the_compact_wire_columns() {
        let row = parse_historical_tsv_row(
            b"AAPL\t42\t9001\t6\t1752415200000000\t1001234\t1001200\t20\t25\t11\t12\t3\t4\t5\t0\t0\t2026-07-13",
        )
        .unwrap();

        assert_eq!(row.ticker, "AAPL");
        assert_eq!(row.ordinal, 42);
        assert_eq!(row.source_sequence, 9001);
        assert_eq!(row.event_meta, 6);
        assert_eq!(row.price_primary_int, 1_001_234);
        assert_eq!(row.size_secondary, 25.0);
        assert_eq!(row.condition_token_3, 5);
        assert_eq!(row.event_date, "2026-07-13");
        assert!(parse_historical_tsv_row(b"AAPL\t42").is_err());
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
        assert!(!macro_bar_is_closed(
            "2026-08-10",
            "1w",
            Utc.with_ymd_and_hms(2026, 8, 12, 14, 0, 0).unwrap(),
        )
        .unwrap());
        assert!(macro_bar_is_closed(
            "2026-08-03",
            "1w",
            Utc.with_ymd_and_hms(2026, 8, 12, 14, 0, 0).unwrap(),
        )
        .unwrap());
        assert!(!macro_bar_is_closed(
            "2026-01-01",
            "1y",
            Utc.with_ymd_and_hms(2026, 8, 12, 14, 0, 0).unwrap(),
        )
        .unwrap());
        assert!(macro_bar_is_closed(
            "2025-01-01",
            "1y",
            Utc.with_ymd_and_hms(2026, 8, 12, 14, 0, 0).unwrap(),
        )
        .unwrap());
    }

    #[test]
    fn recent_coverage_query_does_not_shadow_datetime_predicates_with_text_aliases() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 8, 6, 12, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 8, 11, 18, 20, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let sql = recent_coverage_sql("q_live.qmd_live_event_coverage_v1", &window);
        assert!(sql.contains("AS coverage_start_text"));
        assert!(sql.contains("AS coverage_end_text"));
        assert!(!sql.contains("AS coverage_start_utc"));
        assert!(!sql.contains("AS coverage_end_utc"));
        assert!(sql.contains("AND coverage_end_utc > parseDateTime64BestEffort"));
        assert!(sql.contains("AND coverage_start_utc < parseDateTime64BestEffort"));
    }

    #[test]
    fn persisted_structure_query_does_not_shadow_datetime_predicates_with_text_aliases() {
        let before = Utc.with_ymd_and_hms(2026, 8, 11, 18, 20, 0).unwrap();
        let sql =
            persisted_structure_events_sql("q_derived.generic_structure_events_v1", "AAPL", before);
        assert!(sql.contains("AS pivot_at_text"));
        assert!(sql.contains("AS confirmed_at_text"));
        assert!(!sql.contains("AS pivot_at,"));
        assert!(!sql.contains("AS confirmed_at\n"));
        assert!(sql.contains("AND confirmed_at < parseDateTime64BestEffort"));
        assert!(sql.contains("AND confirmed_at >= parseDateTime64BestEffort"));
    }

    #[test]
    fn source_plan_is_ordered_non_overlapping_and_exposes_live_tail() {
        let start = Utc.with_ymd_and_hms(2026, 7, 1, 0, 0, 0).unwrap();
        let archive_end = Utc.with_ymd_and_hms(2026, 7, 2, 0, 0, 0).unwrap();
        let recent_end = Utc.with_ymd_and_hms(2026, 7, 3, 0, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 7, 4, 0, 0, 0).unwrap();
        let window = EventWindow {
            end,
            start,
            tickers: vec!["AAPL".to_string()],
        };
        let plan = build_source_plan(
            &window,
            window.tickers.clone(),
            Some("2026-07-01".to_string()),
            Some(archive_end),
            vec![CoverageInterval {
                start: archive_end,
                end: recent_end,
            }],
            &HistoricalGatewayConfig::from_env(),
        );
        assert_eq!(plan.segments.len(), 4);
        assert!(matches!(plan.segments[0].tier, MarketSourceTier::Archive));
        assert!(matches!(plan.segments[1].tier, MarketSourceTier::Recent));
        assert!(matches!(
            plan.segments[2].tier,
            MarketSourceTier::ClosedMarket
        ));
        assert!(matches!(
            plan.segments[3].tier,
            MarketSourceTier::CurrentLive
        ));
        assert!(plan.plan_hash.starts_with("fnv1a64:"));
        assert!(!plan.complete_for_history);
        for pair in plan.segments.windows(2) {
            assert_eq!(pair[0].end, pair[1].start);
        }
    }

    #[test]
    fn source_plan_treats_a_scheduled_closed_tail_as_durable_empty_coverage() {
        let start = Utc.with_ymd_and_hms(2026, 8, 7, 23, 59, 50).unwrap();
        let archive_end = Utc.with_ymd_and_hms(2026, 8, 8, 0, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 8, 10, 8, 0, 0).unwrap();
        let window = EventWindow {
            end,
            start,
            tickers: vec!["AAPL".to_string()],
        };

        let plan = build_source_plan(
            &window,
            window.tickers.clone(),
            Some("2026-08-07".to_string()),
            Some(archive_end),
            Vec::new(),
            &HistoricalGatewayConfig::from_env(),
        );

        assert_eq!(plan.segments.len(), 2);
        assert!(matches!(plan.segments[0].tier, MarketSourceTier::Archive));
        assert!(matches!(
            plan.segments[1].tier,
            MarketSourceTier::ClosedMarket
        ));
        assert_eq!(plan.segments[1].start, archive_end);
        assert_eq!(plan.segments[1].end, end);
        assert!(plan.complete_for_history);
    }

    #[test]
    fn scheduled_weekend_between_extended_sessions_is_covered_empty() {
        let start = Utc.with_ymd_and_hms(2026, 8, 8, 0, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 8, 10, 8, 0, 0).unwrap();
        let mut segments = Vec::new();

        append_scheduled_gap_segments(&mut segments, start, end);

        assert!(!segments.is_empty());
        assert_eq!(segments.first().unwrap().start, start);
        assert_eq!(segments.last().unwrap().end, end);
        assert!(segments
            .iter()
            .all(|segment| matches!(segment.tier, MarketSourceTier::ClosedMarket)));
        assert!(segments.iter().all(|segment| {
            segment.coverage_state == "covered_empty" && segment.queryable_by_history
        }));
    }

    #[test]
    fn missing_weekday_session_time_remains_a_fail_closed_gap() {
        let start = Utc.with_ymd_and_hms(2026, 8, 11, 17, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 8, 11, 17, 5, 0).unwrap();
        let mut segments = Vec::new();

        append_scheduled_gap_segments(&mut segments, start, end);

        assert_eq!(segments.len(), 1);
        assert!(matches!(segments[0].tier, MarketSourceTier::Gap));
        assert_eq!(segments[0].coverage_state, "uncovered");
        assert!(!segments[0].queryable_by_history);
    }

    #[test]
    fn scheduled_closed_boundaries_follow_wall_clock_across_dst() {
        let start = Utc.with_ymd_and_hms(2026, 10, 31, 0, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 11, 2, 9, 0, 0).unwrap();
        let mut segments = Vec::new();

        append_scheduled_gap_segments(&mut segments, start, end);

        assert_eq!(segments.first().unwrap().start, start);
        assert_eq!(segments.last().unwrap().end, end);
        assert!(segments
            .iter()
            .all(|segment| matches!(segment.tier, MarketSourceTier::ClosedMarket)));
    }

    #[test]
    fn archive_watermark_ends_at_new_york_extended_session_close_across_dst() {
        assert_eq!(
            archive_session_end_utc(chrono::NaiveDate::from_ymd_opt(2026, 7, 1).unwrap()).unwrap(),
            Utc.with_ymd_and_hms(2026, 7, 2, 0, 0, 0).unwrap(),
        );
        assert_eq!(
            archive_session_end_utc(chrono::NaiveDate::from_ymd_opt(2026, 1, 2).unwrap()).unwrap(),
            Utc.with_ymd_and_hms(2026, 1, 3, 1, 0, 0).unwrap(),
        );
    }

    #[test]
    fn recent_coverage_intervals_merge_before_planning() {
        let first = Utc.with_ymd_and_hms(2026, 7, 2, 0, 0, 0).unwrap();
        let second = Utc.with_ymd_and_hms(2026, 7, 2, 12, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 7, 3, 0, 0, 0).unwrap();
        let merged = merge_coverage_intervals(vec![
            CoverageInterval {
                start: first,
                end: second,
            },
            CoverageInterval { start: second, end },
        ]);
        assert_eq!(merged.len(), 1);
        assert_eq!(merged[0].start, first);
        assert_eq!(merged[0].end, end);
    }

    #[test]
    fn recent_coverage_requires_compact_and_bar_confirmation_for_one_run() {
        let start = Utc.with_ymd_and_hms(2026, 1, 3, 0, 30, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 1, 3, 1, 0, 0).unwrap();
        let compact = RecentCoverageRow {
            coverage_id: "compact_run-1::2026-01-03::2026-01-02".into(),
            end,
            start,
            status: "compact_persisted".into(),
        };
        assert!(materialize_confirmed_recent_coverage(&[compact.clone()]).is_empty());

        let confirmed = materialize_confirmed_recent_coverage(&[
            compact,
            RecentCoverageRow {
                coverage_id: "intraday_run-1::2026-01-02".into(),
                end,
                start,
                status: "intraday_bars_persisted".into(),
            },
        ]);
        assert_eq!(confirmed.len(), 1);
        assert_eq!(confirmed[0].start, start);
        assert_eq!(confirmed[0].end, end);
    }

    #[test]
    fn recent_and_archive_selects_share_one_wire_row_contract() {
        let start = Utc.with_ymd_and_hms(2026, 7, 1, 0, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 7, 2, 0, 0, 0).unwrap();
        let archive = event_select(
            "market_sip_compact.events_2026",
            false,
            start,
            end,
            "",
            None,
        );
        assert!(archive.contains("source.ordinal AS source_sequence"));
        let recent = event_select("q_live.events", true, start, end, "", None);
        assert!(archive.contains("source.ordinal AS ordinal"));
        assert!(recent.contains("source.arrival_sequence AS ordinal"));
        assert!(recent.contains("source.source_sequence AS source_sequence"));
        assert!(recent.contains("FROM q_live.events AS source FINAL"));
    }
}
