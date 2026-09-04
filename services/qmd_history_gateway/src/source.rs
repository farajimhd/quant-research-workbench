use crate::config::HistoricalGatewayConfig;
use chrono::{DateTime, Datelike, NaiveDate, NaiveDateTime, TimeZone, Utc, Weekday};
use chrono_tz::America::New_York;
use qmd_core::bars::TradeAggregationRules;
use qmd_core::compact_event::{
    CompactEventDecoder, CompactEventReferences, LiveCompactEvent,
    LIVE_COMPACT_EVENT_SCHEMA_VERSION,
};
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEvent, StructureSplitAdjustment,
    GENERIC_STRUCTURE_ALGORITHM_VERSION,
};
use qmd_core::indicators::{
    daily_session_trade_bars_sql, market_structure_reference_sql,
    parse_market_structure_reference_rows, MarketStructureReferenceLevels,
};
use qmd_core::market_products::parse_resolution_us;
use qmd_core::structure_certification::{
    canonical_json_sha256, checkpoint_certification_value, checkpoint_json_value,
    checkpoint_sha256, validate_checkpoint_certification, StructureCheckpointCertification,
};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap};
use std::str::FromStr;
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::{mpsc, Mutex, OnceCell};

const LATEST_COVERAGE_CACHE_TTL: Duration = Duration::from_secs(30);
const LATEST_COVERAGE_CACHE_MAX_ENTRIES: usize = 64;
const LATEST_COVERAGE_QUERY_MAX_MEMORY_BYTES: u64 = 512 * 1024 * 1024;
const LATEST_COVERAGE_QUERY_MAX_THREADS: u8 = 2;
const LATEST_COVERAGE_QUERY_MAX_SECONDS: u8 = 15;

#[derive(Clone, Debug)]
pub struct EventWindow {
    pub end: DateTime<Utc>,
    pub start: DateTime<Utc>,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StructureTradeCountEstimateRequest {
    pub as_of: DateTime<Utc>,
    pub end_date: NaiveDate,
    pub start_date: NaiveDate,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureTradeCountEstimate {
    pub max_session_trade_events: u64,
    pub session_count: u64,
    pub ticker: String,
    pub total_trade_events: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureTradeCountEstimateResponse {
    pub as_of: DateTime<Utc>,
    pub end_date: NaiveDate,
    pub estimates: Vec<StructureTradeCountEstimate>,
    pub schema_version: u16,
    pub source: String,
    pub start_date: NaiveDate,
}

/// Lightweight campaign-planning estimate sourced from the canonical
/// continuity index. These counts are planning hints only; streamed compact
/// events and their exact cursor remain the checkpoint authority.
#[derive(Clone, Debug, Deserialize)]
pub struct StructureEventCountEstimateRequest {
    pub as_of: DateTime<Utc>,
    pub end_date: NaiveDate,
    pub start_date: NaiveDate,
    pub tickers: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureEventCountEstimate {
    pub max_session_events: u64,
    pub session_count: u64,
    pub ticker: String,
    pub total_events: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureEventCountEstimateResponse {
    pub as_of: DateTime<Utc>,
    pub end_date: NaiveDate,
    pub estimates: Vec<StructureEventCountEstimate>,
    pub schema_version: u16,
    pub source: String,
    pub start_date: NaiveDate,
}

/// Ordered campaign universe. `priority_dollar_volume` is used only to assign
/// scarce worker slots; it never replaces raw events in the level calculation.
#[derive(Clone, Debug, Serialize)]
pub struct StructureCampaignTicker {
    pub currently_active: bool,
    pub priority_dollar_volume: f64,
    pub ticker: String,
}

/// One immutable archive session in a ticker-owned structure campaign.
/// Ordinal bounds are inclusive/exclusive and come from the canonical
/// continuity index, whose identity is pinned into `source_revision`.
#[derive(Clone, Debug)]
pub struct StructureCampaignSession {
    pub event_count: u64,
    pub first_ordinal: u64,
    pub first_sip_timestamp_us: u64,
    pub last_sip_timestamp_us: u64,
    pub next_ordinal: u64,
    pub session_date: NaiveDate,
    pub source_revision: SourceRevision,
}

/// Immutable archive range used to seed a bounded indicator history. These
/// ranges are read from the ingestion-owned continuity index; consumers never
/// discover raw files or scan the archive by date.
#[derive(Clone, Debug, Serialize)]
pub struct IndicatorWarmupOrdinalSession {
    pub event_count: u64,
    pub execution_clock_complete: bool,
    pub execution_clock_revision: String,
    pub first_ordinal: u64,
    pub next_ordinal: u64,
    pub session_date: NaiveDate,
}

#[derive(Clone, Debug)]
pub struct RecentIndicatorTail {
    pub authority_start: DateTime<Utc>,
    pub events: Vec<LiveCompactEvent>,
    pub source_revision: SourceRevision,
}

#[derive(Clone, Debug)]
pub struct StructureCampaignManifest {
    pub authority_start: DateTime<Utc>,
    pub sessions: Vec<StructureCampaignSession>,
    pub split_adjustments: Vec<StructureSplitAdjustment>,
    pub ticker: String,
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

#[derive(Clone, Debug, Deserialize, Serialize)]
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
    pub coverage_status: String,
    pub latest_session_date: Option<String>,
    pub source: String,
    pub split_adjustments: Vec<StructureSplitAdjustment>,
    pub split_adjusted: bool,
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
    latest_coverage_cache: Arc<Mutex<HashMap<Option<NaiveDate>, CachedLatestEventCoverage>>>,
    latest_coverage_query_gate: Arc<Mutex<()>>,
    structure_table_available: Arc<OnceCell<bool>>,
    structure_daily_checkpoint_table_available: Arc<OnceCell<bool>>,
    structure_condition_revision: String,
    trade_rules: TradeAggregationRules,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq)]
pub struct SessionVwapSeed {
    pub cumulative_trade_notional: f64,
    pub cumulative_volume: f64,
    #[serde(default)]
    pub cumulative_execution_trade_notional: f64,
    #[serde(default)]
    pub cumulative_execution_volume: f64,
}

#[derive(Clone, Debug)]
pub struct PersistedStructureCheckpointSeed {
    pub authority_start: DateTime<Utc>,
    pub checkpoint: GenericStructureCheckpoint,
    pub source_plan_hash: String,
    pub source_revision_token: String,
}

#[derive(Clone, Debug)]
struct CachedLatestEventCoverage {
    expires_at: Instant,
    value: LatestEventCoverage,
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
    execution_timestamp_us: u64,
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

#[derive(Debug, Default, Deserialize)]
struct ExecutionClockCoverageRevisionRow {
    expected_ticker_days: u64,
    covered_ticker_days: u64,
    clock_rows: u64,
    delayed_trade_reports: u64,
    max_build_step: u64,
    max_updated_at: String,
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

#[derive(Debug, Deserialize)]
struct PersistedStructureCheckpointRow {
    authority_start: String,
    certification_json: String,
    session_date: String,
    snapshot_json: String,
    source_plan_hash: String,
    source_revision_token: String,
}

fn first_json_difference_path(left: &Value, right: &Value, path: &str) -> Option<String> {
    match (left, right) {
        (Value::Array(left), Value::Array(right)) => {
            if left.len() != right.len() {
                return Some(format!("{path}.length"));
            }
            left.iter()
                .zip(right)
                .enumerate()
                .find_map(|(index, (left, right))| {
                    first_json_difference_path(left, right, &format!("{path}[{index}]"))
                })
        }
        (Value::Object(left), Value::Object(right)) => {
            for key in left.keys().chain(right.keys()) {
                match (left.get(key), right.get(key)) {
                    (Some(left), Some(right)) => {
                        if let Some(difference) =
                            first_json_difference_path(left, right, &format!("{path}.{key}"))
                        {
                            return Some(difference);
                        }
                    }
                    _ => return Some(format!("{path}.{key}")),
                }
            }
            None
        }
        _ if left == right => None,
        _ => Some(path.to_string()),
    }
}

#[derive(Debug, Deserialize)]
struct StockSplitAdjustmentRow {
    execution_date: String,
    split_from: f64,
    split_to: f64,
    source_inserted_at: String,
}

#[derive(Clone, Debug, Deserialize)]
struct StructureCampaignContinuityRow {
    event_count: u64,
    first_sip_timestamp_us: u64,
    last_ordinal: u64,
    last_sip_timestamp_us: u64,
    max_build_step: u64,
    max_updated_at: String,
    next_ordinal: u64,
    source_date: String,
}

#[derive(Clone, Debug, Deserialize)]
struct StructureSplitRevisionRow {
    execution_date: String,
    source_inserted_at: String,
    split_from: f64,
    split_to: f64,
    ticker: String,
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
        let structure_condition_revision = format!(
            "trade-condition-sha256:{:x}",
            Sha256::digest(references.trade_aggregation_revision_material().as_bytes())
        );
        let source = Self {
            client: Client::new(),
            config,
            decoder: references.decoder(),
            latest_coverage_cache: Arc::new(Mutex::new(HashMap::new())),
            latest_coverage_query_gate: Arc::new(Mutex::new(())),
            structure_table_available: Arc::new(OnceCell::new()),
            structure_daily_checkpoint_table_available: Arc::new(OnceCell::new()),
            structure_condition_revision,
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
        let rows = parse_recent_coverage_rows(&text)?;
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
            status: Option<String>,
            start_text: Option<String>,
            end_text: Option<String>,
            error_count: Option<u64>,
        }
        let row = serde_json::from_str::<Row>(line)
            .map_err(|error| format!("invalid focused repair coverage row: {error}"))?;
        if row.status.as_deref() != Some("completed") || row.error_count.unwrap_or(0) > 0 {
            return Ok(None);
        }
        let (Some(start_text), Some(end_text)) =
            (row.start_text.as_deref(), row.end_text.as_deref())
        else {
            return Ok(None);
        };
        let start = parse_clickhouse_datetime(start_text)?;
        let end = parse_clickhouse_datetime(end_text)?;
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

    pub async fn completed_session_dates_between(
        &self,
        start_date: NaiveDate,
        end_date: NaiveDate,
        as_of: DateTime<Utc>,
    ) -> Result<Vec<NaiveDate>, String> {
        if start_date > end_date {
            return Err("completed session range starts after it ends".to_string());
        }
        let sql = completed_session_dates_between_sql(
            &self.config.clickhouse_database,
            start_date,
            end_date,
            as_of,
        );
        #[derive(Deserialize)]
        struct SessionDateRow {
            session_date: String,
        }
        self.query(&sql)
            .await?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<SessionDateRow>(line)
                    .map_err(|error| format!("invalid completed session row: {error}"))?;
                NaiveDate::parse_from_str(&row.session_date, "%Y-%m-%d")
                    .map_err(|error| format!("invalid completed session date: {error}"))
            })
            .collect()
    }

    pub async fn structure_trade_count_estimates(
        &self,
        request: StructureTradeCountEstimateRequest,
    ) -> Result<StructureTradeCountEstimateResponse, String> {
        if request.start_date >= request.end_date {
            return Err(
                "structure trade-count estimate requires start_date before end_date".into(),
            );
        }
        if request.as_of > Utc::now() + chrono::Duration::seconds(1) {
            return Err("structure trade-count estimate as_of cannot be in the future".into());
        }
        if request.end_date
            > request
                .as_of
                .date_naive()
                .succ_opt()
                .unwrap_or(request.end_date)
        {
            return Err("structure trade-count estimate cannot extend beyond as_of".into());
        }
        let mut tickers = request
            .tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?;
        tickers.sort();
        tickers.dedup();
        if tickers.is_empty() || tickers.len() > 25_000 {
            return Err("structure trade-count estimate requires 1..=25000 tickers".into());
        }
        let ticker_filter = tickers
            .iter()
            .map(|ticker| sql_literal(ticker))
            .collect::<Vec<_>>()
            .join(",");
        let start = request
            .start_date
            .and_hms_opt(0, 0, 0)
            .ok_or_else(|| "invalid structure trade-count estimate start".to_string())?
            .and_utc();
        let end = request
            .end_date
            .and_hms_opt(0, 0, 0)
            .ok_or_else(|| "invalid structure trade-count estimate end".to_string())?
            .and_utc();
        let mut session_selects = Vec::new();
        for year in request.start_date.year()
            ..=request
                .end_date
                .pred_opt()
                .unwrap_or(request.end_date)
                .year()
        {
            session_selects.push(format!(
                r#"SELECT
                    upper(source.ticker) AS ticker,
                    source.event_date AS session_date,
                    count() AS session_trade_events
                FROM {}.{}{} AS source
                PREWHERE source.event_date >= toDate({})
                  AND source.event_date < toDate({})
                  AND source.sip_timestamp_us >= {}
                  AND source.sip_timestamp_us < {}
                  AND bitAnd(source.event_meta, 1) = 1
                  AND source.ticker IN ({})
                GROUP BY ticker, session_date"#,
                self.config.clickhouse_database,
                self.config.table_prefix,
                year,
                sql_literal(&request.start_date.to_string()),
                sql_literal(&request.end_date.to_string()),
                start.timestamp_micros(),
                end.timestamp_micros(),
                ticker_filter,
            ));
        }
        let sql = format!(
            r#"SELECT
                ticker,
                sum(session_trade_events) AS total_trade_events,
                max(session_trade_events) AS max_session_trade_events,
                count() AS session_count
            FROM ({session_selects})
            GROUP BY ticker
            ORDER BY ticker
            FORMAT JSONEachRow"#,
            session_selects = session_selects.join(" UNION ALL "),
        );
        #[derive(Deserialize)]
        struct EstimateRow {
            max_session_trade_events: u64,
            session_count: u64,
            ticker: String,
            total_trade_events: u64,
        }
        let text = self.query_bounded(&sql, 120).await?;
        let estimates = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<EstimateRow>(line).map_err(|error| {
                    format!("invalid structure trade-count estimate row: {error}")
                })?;
                Ok(StructureTradeCountEstimate {
                    max_session_trade_events: row.max_session_trade_events,
                    session_count: row.session_count,
                    ticker: row.ticker,
                    total_trade_events: row.total_trade_events,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(StructureTradeCountEstimateResponse {
            as_of: request.as_of,
            end_date: request.end_date,
            estimates,
            schema_version: 1,
            source: format!(
                "{}.{}<year>",
                self.config.clickhouse_database, self.config.table_prefix
            ),
            start_date: request.start_date,
        })
    }

    pub async fn structure_event_count_estimates(
        &self,
        request: StructureEventCountEstimateRequest,
    ) -> Result<StructureEventCountEstimateResponse, String> {
        if request.start_date >= request.end_date {
            return Err(
                "structure event-count estimate requires start_date before end_date".into(),
            );
        }
        if request.as_of > Utc::now() + chrono::Duration::seconds(1) {
            return Err("structure event-count estimate as_of cannot be in the future".into());
        }
        if request.end_date
            > request
                .as_of
                .date_naive()
                .succ_opt()
                .unwrap_or(request.end_date)
        {
            return Err("structure event-count estimate cannot extend beyond as_of".into());
        }
        let mut tickers = request
            .tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?;
        tickers.sort();
        tickers.dedup();
        if tickers.is_empty() || tickers.len() > 25_000 {
            return Err("structure event-count estimate requires 1..=25000 tickers".into());
        }
        let ticker_filter = tickers
            .iter()
            .map(|ticker| sql_literal(ticker))
            .collect::<Vec<_>>()
            .join(",");
        let continuity_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
        let sql = format!(
            r#"SELECT
                ticker,
                sum(session_events) AS total_events,
                max(session_events) AS max_session_events,
                count() AS session_count
            FROM (
                SELECT
                    upper(ticker) AS ticker,
                    source_date,
                    argMax(event_count, tuple(build_step, updated_at)) AS session_events
                FROM {continuity_table}
                PREWHERE source_date >= toDate({start_date})
                  AND source_date < toDate({end_date})
                  AND ticker IN ({ticker_filter})
                GROUP BY ticker, source_date
            )
            GROUP BY ticker
            ORDER BY ticker
            FORMAT JSONEachRow"#,
            start_date = sql_literal(&request.start_date.to_string()),
            end_date = sql_literal(&request.end_date.to_string()),
        );
        #[derive(Deserialize)]
        struct EstimateRow {
            max_session_events: u64,
            session_count: u64,
            ticker: String,
            total_events: u64,
        }
        let text = self.query_bounded(&sql, 30).await?;
        let estimates = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<EstimateRow>(line).map_err(|error| {
                    format!("invalid structure event-count estimate row: {error}")
                })?;
                Ok(StructureEventCountEstimate {
                    max_session_events: row.max_session_events,
                    session_count: row.session_count,
                    ticker: row.ticker,
                    total_events: row.total_events,
                })
            })
            .collect::<Result<Vec<_>, String>>()?;
        Ok(StructureEventCountEstimateResponse {
            as_of: request.as_of,
            end_date: request.end_date,
            estimates,
            schema_version: 2,
            source: continuity_table,
            start_date: request.start_date,
        })
    }

    pub async fn structure_campaign_tickers(
        &self,
        start_date: NaiveDate,
        priority_start_date: NaiveDate,
        priority_end_date: NaiveDate,
        as_of: DateTime<Utc>,
    ) -> Result<Vec<StructureCampaignTicker>, String> {
        if priority_start_date > priority_end_date {
            return Err("structure campaign priority range starts after it ends".to_string());
        }
        if as_of > Utc::now() + chrono::Duration::seconds(1) {
            return Err("structure campaign ticker as_of cannot be in the future".to_string());
        }
        let liquidity_coverage_sql = structure_campaign_liquidity_coverage_sql(
            &self.config.clickhouse_database,
            &self.config.daily_session_bars_table,
            priority_start_date,
            priority_end_date,
            as_of,
        )?;
        let liquidity_rows = self
            .query_bounded(&liquidity_coverage_sql, 60)
            .await?
            .trim()
            .parse::<u64>()
            .map_err(|error| format!("invalid structure liquidity coverage count: {error}"))?;
        let sql = structure_campaign_tickers_sql(
            &self.config.clickhouse_database,
            &self.config.daily_session_bars_table,
            &self.config.recent_database,
            start_date,
            priority_start_date,
            priority_end_date,
            as_of,
            liquidity_rows > 0,
        )?;
        #[derive(Deserialize)]
        struct Row {
            currently_active: u8,
            priority_dollar_volume: f64,
            ticker: String,
        }
        self.query_bounded(&sql, if liquidity_rows > 0 { 60 } else { 900 })
            .await?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<Row>(line)
                    .map_err(|error| format!("invalid structure campaign ticker row: {error}"))?;
                Ok(StructureCampaignTicker {
                    currently_active: row.currently_active != 0,
                    priority_dollar_volume: row.priority_dollar_volume.max(0.0),
                    ticker: normalize_ticker(&row.ticker)?,
                })
            })
            .collect()
    }

    /// Return the point-in-time tradable universe for a historical session.
    /// The reference snapshot is the only universe authority; archive
    /// presence alone does not make a symbol tradable.
    pub async fn tradable_tickers(&self, as_of_date: NaiveDate) -> Result<Vec<String>, String> {
        let sql = tradable_tickers_sql(&self.config.recent_database, as_of_date)?;
        self.query_bounded(&sql, 60)
            .await?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(normalize_ticker)
            .collect()
    }

    /// Resolve newest-first physical archive ranges before a session. The
    /// continuity table is tiny relative to the event archive and lets the
    /// caller request only the ordinal ranges needed to form its warm-up bars.
    pub async fn indicator_warmup_ordinal_sessions(
        &self,
        ticker: &str,
        before_date: NaiveDate,
        max_sessions: usize,
    ) -> Result<Vec<IndicatorWarmupOrdinalSession>, String> {
        let ticker = normalize_ticker(ticker)?;
        let sql = indicator_warmup_ordinal_sessions_sql(
            &self.config.clickhouse_database,
            &self.config.execution_clock_database,
            &self.config.execution_clock_coverage_table,
            &ticker,
            before_date,
            max_sessions.clamp(1, 512),
        )?;
        #[derive(Deserialize)]
        struct Row {
            event_count: u64,
            execution_clock_complete: u8,
            execution_clock_revision: String,
            first_ordinal: u64,
            next_ordinal: u64,
            session_date: String,
        }
        self.query_bounded(&sql, 30)
            .await?
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let row = serde_json::from_str::<Row>(line)
                    .map_err(|error| format!("invalid indicator warm-up ordinal row: {error}"))?;
                let session_date = NaiveDate::parse_from_str(&row.session_date, "%Y-%m-%d")
                    .map_err(|error| format!("invalid indicator warm-up session date: {error}"))?;
                if row.event_count == 0
                    || row.first_ordinal >= row.next_ordinal
                    || row.next_ordinal - row.first_ordinal != row.event_count
                {
                    return Err(format!(
                        "indicator warm-up ordinal range is not closed for {ticker} {session_date}"
                    ));
                }
                Ok(IndicatorWarmupOrdinalSession {
                    event_count: row.event_count,
                    execution_clock_complete: row.execution_clock_complete == 1,
                    execution_clock_revision: row.execution_clock_revision,
                    first_ordinal: row.first_ordinal,
                    next_ordinal: row.next_ordinal,
                    session_date,
                })
            })
            .collect()
    }

    /// Read a certified recent-event tail directly from q_live. q_live is
    /// physically ordered by ticker and SIP time rather than ticker ordinal,
    /// so its ingestion coverage ledger—not a fabricated ordinal index—is the
    /// restart and completeness authority.
    pub async fn recent_indicator_tail(
        &self,
        ticker: &str,
        before: DateTime<Utc>,
        event_limit: usize,
    ) -> Result<Option<RecentIndicatorTail>, String> {
        let ticker = normalize_ticker(ticker)?;
        let coverage_window = EventWindow {
            start: before - chrono::Duration::days(14),
            end: before,
            tickers: vec![ticker.clone()],
        };
        let coverage_sql = recent_coverage_sql(
            &format!(
                "{}.{}",
                self.config.recent_database, self.config.recent_event_coverage_table
            ),
            &coverage_window,
        );
        let coverage_text = self.query_bounded(&coverage_sql, 30).await?;
        let rows = parse_recent_coverage_rows(&coverage_text)?;
        let intervals = materialize_confirmed_recent_coverage(&rows);
        let Some(interval) = intervals
            .into_iter()
            .filter(|interval| interval.end <= before)
            .max_by_key(|interval| interval.end)
        else {
            return Ok(None);
        };
        // A prior-session tail may end at 20:00 ET before the closed overnight
        // interval. Anything older than 16 hours is not a valid immediate seed.
        if before - interval.end > chrono::Duration::hours(16) {
            return Ok(None);
        }
        let ticker_filter = format!(" AND source.ticker IN ({})", sql_literal(&ticker));
        let select = event_select(
            &format!(
                "{}.{}",
                self.config.recent_database, self.config.recent_event_table
            ),
            true,
            None,
            interval.start,
            interval.end,
            &format!("{ticker_filter} AND bitAnd(source.event_meta, 1) = toUInt8(1)"),
            None,
        );
        let limit = event_limit.clamp(1, self.config.max_events_per_request.min(250_000));
        let sql = format!(
            "SELECT * FROM ({select}) ORDER BY sip_timestamp_us DESC, ticker DESC, ordinal DESC LIMIT {limit} FORMAT JSONEachRow"
        );
        let text = self.query_bounded(&sql, 60).await?;
        let mut events = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str::<HistoricalRow>(line)
                    .map(row_to_event)
                    .map_err(|error| format!("invalid recent indicator event: {error}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        events.sort_by_key(|event| {
            (
                event.sip_timestamp_us,
                event.ticker.clone(),
                event.arrival_sequence,
            )
        });
        let mut hasher = Sha256::new();
        for row in &rows {
            hasher.update(row.coverage_id.as_bytes());
            hasher.update(row.status.as_bytes());
            hasher.update(row.start.timestamp_micros().to_le_bytes());
            hasher.update(row.end.timestamp_micros().to_le_bytes());
        }
        let coverage_token = format!("q-live-coverage-sha256:{:x}", hasher.finalize());
        Ok(Some(RecentIndicatorTail {
            authority_start: interval.start,
            source_revision: SourceRevision {
                complete_for_history: true,
                event_count: events.len() as u64,
                live_continuation_sequence: events.last().map(|event| event.arrival_sequence),
                max_build_step: 0,
                max_updated_at: interval.end.to_rfc3339(),
                request_complete: true,
                source_plan_hash: coverage_token.clone(),
                source_tiers: vec!["recent".to_string()],
                token: coverage_token,
            },
            events,
        }))
    }

    /// Stream a bounded trade-only archive range in causal ordinal order.
    /// Quote rows are deliberately excluded because EMA/MACD warm-up consumes
    /// only canonical trade closes; entry-time VWAP is seeded separately from
    /// the current session and still consumes the complete event stream.
    pub fn stream_indicator_ordinal_range(
        &self,
        session_date: NaiveDate,
        ticker: &str,
        first_ordinal: u64,
        next_ordinal: u64,
        batch_size: usize,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        let ticker = normalize_ticker(ticker)?;
        if first_ordinal >= next_ordinal {
            return Err("indicator warm-up ordinal range must be non-empty".to_string());
        }
        let batch_size = batch_size.clamp(1, 100_000);
        let (sender, receiver) = mpsc::channel(2);
        let source = self.clone();
        tokio::spawn(async move {
            let select = ordinal_event_select_filtered(
                &format!(
                    "{}.{}{}",
                    source.config.clickhouse_database,
                    source.config.table_prefix,
                    session_date.year()
                ),
                Some(&format!(
                    "{}.{}",
                    source.config.execution_clock_database, source.config.execution_clock_table
                )),
                &ticker,
                first_ordinal,
                next_ordinal,
                Some(1),
            );
            let sql = format!("SELECT * FROM ({select}) ORDER BY ordinal FORMAT TabSeparated");
            if let Err(error) = source
                .stream_query_rows(sql, batch_size, sender.clone())
                .await
            {
                let _ = sender.send(Err(error)).await;
            }
        });
        Ok(receiver)
    }

    /// Pin every piece of archive authority needed by one ticker before its
    /// worker starts. The daily loop performs no continuity or split queries.
    pub async fn structure_campaign_manifest(
        &self,
        ticker: &str,
        authority_start: DateTime<Utc>,
        session_dates: &[NaiveDate],
    ) -> Result<StructureCampaignManifest, String> {
        let ticker = normalize_ticker(ticker)?;
        let Some(final_session) = session_dates.last().copied() else {
            return Err("structure campaign manifest requires at least one session".to_string());
        };
        if session_dates.windows(2).any(|pair| pair[0] >= pair[1]) {
            return Err("structure campaign sessions must be strictly increasing".to_string());
        }
        let final_end = archive_session_end_utc(final_session)?;
        let archive = self.latest_coverage_before(None).await?;
        let archive_end = archive
            .session_date
            .as_deref()
            .map(|value| {
                NaiveDate::parse_from_str(value, "%Y-%m-%d")
                    .map_err(|error| format!("invalid archive coverage date {value:?}: {error}"))
                    .and_then(archive_session_end_utc)
            })
            .transpose()?
            .ok_or_else(|| "structure campaign archive coverage is unavailable".to_string())?;
        if archive_end < final_end {
            return Err(format!(
                "structure campaign requires archive coverage through {final_session}, latest is {}",
                archive.session_date.as_deref().unwrap_or("unavailable")
            ));
        }

        let continuity_sql = structure_campaign_continuity_sql(
            &self.config.clickhouse_database,
            &ticker,
            authority_start.with_timezone(&New_York).date_naive(),
            final_session,
        );
        let continuity_text = self.query_bounded(&continuity_sql, 60).await?;
        let mut continuity = BTreeMap::new();
        for line in continuity_text
            .lines()
            .filter(|line| !line.trim().is_empty())
        {
            let row = serde_json::from_str::<StructureCampaignContinuityRow>(line)
                .map_err(|error| format!("invalid structure continuity row: {error}"))?;
            let date = NaiveDate::parse_from_str(&row.source_date, "%Y-%m-%d")
                .map_err(|error| format!("invalid structure continuity date: {error}"))?;
            if row.event_count > 0 {
                let expected_next = row
                    .last_ordinal
                    .checked_add(1)
                    .ok_or_else(|| "structure continuity ordinal overflow".to_string())?;
                if row.next_ordinal != expected_next || row.next_ordinal < row.event_count {
                    return Err(format!(
                        "structure continuity is not a closed ordinal range for {ticker} {date}"
                    ));
                }
            }
            continuity.insert(date, row);
        }

        let split_sql =
            structure_split_revision_sql(&sql_literal(&ticker), authority_start, final_end);
        let split_text = self.query_bounded(&split_sql, 60).await?;
        let mut split_rows = Vec::new();
        let mut split_adjustments = Vec::new();
        for line in split_text.lines().filter(|line| !line.trim().is_empty()) {
            let row = serde_json::from_str::<StructureSplitRevisionRow>(line)
                .map_err(|error| format!("invalid structure split row: {error}"))?;
            let execution_date = NaiveDate::parse_from_str(&row.execution_date, "%Y-%m-%d")
                .map_err(|error| format!("invalid stock split execution_date: {error}"))?;
            let effective_at = New_York
                .with_ymd_and_hms(
                    execution_date.year(),
                    execution_date.month(),
                    execution_date.day(),
                    4,
                    0,
                    0,
                )
                .single()
                .ok_or_else(|| "invalid New York stock split boundary".to_string())?
                .with_timezone(&Utc);
            let source_inserted_at = DateTime::parse_from_rfc3339(&row.source_inserted_at)
                .map_err(|error| format!("invalid stock split inserted_at: {error}"))?
                .with_timezone(&Utc);
            split_adjustments.push(StructureSplitAdjustment {
                execution_date,
                effective_at,
                split_from: row.split_from,
                split_to: row.split_to,
                source_inserted_at,
            });
            split_rows.push(row);
        }

        let mut cumulative_event_count = 0_u64;
        let mut cumulative_max_build_step = 0_u64;
        let mut cumulative_max_updated_at = String::new();
        let mut sessions = Vec::with_capacity(session_dates.len());
        for session_date in session_dates.iter().copied() {
            let authority_end = archive_session_end_utc(session_date)?;
            let window = EventWindow {
                start: authority_start,
                end: authority_end,
                tickers: vec![ticker.clone()],
            };
            let plan = build_source_plan(
                &window,
                vec![ticker.clone()],
                archive.session_date.clone(),
                Some(archive_end),
                Vec::new(),
                &self.config,
            );
            if !plan.complete_for_history
                || plan
                    .segments
                    .iter()
                    .any(|segment| !segment.queryable_by_history)
            {
                return Err(format!(
                    "structure campaign source plan is incomplete for {ticker} {session_date}"
                ));
            }
            let row = continuity.get(&session_date);
            if let Some(row) = row {
                cumulative_event_count = cumulative_event_count.saturating_add(row.event_count);
                cumulative_max_build_step = cumulative_max_build_step.max(row.max_build_step);
                cumulative_max_updated_at =
                    cumulative_max_updated_at.max(row.max_updated_at.clone());
            }
            let split_revision_token = split_revision_token(split_rows.iter().filter(|row| {
                NaiveDate::parse_from_str(&row.execution_date, "%Y-%m-%d")
                    .is_ok_and(|date| date <= session_date)
            }))?;
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
            let source_revision = SourceRevision {
                complete_for_history: true,
                event_count: cumulative_event_count,
                live_continuation_sequence: None,
                max_build_step: cumulative_max_build_step,
                max_updated_at: cumulative_max_updated_at.clone(),
                request_complete: true,
                source_plan_hash: plan.plan_hash.clone(),
                source_tiers: plan
                    .segments
                    .iter()
                    .map(|segment| format!("{:?}", segment.tier).to_ascii_lowercase())
                    .collect(),
                token: format!(
                    "{}:{}:0:{}:{}:{}:{}",
                    cumulative_max_build_step,
                    cumulative_event_count,
                    cumulative_max_updated_at,
                    plan_token,
                    split_revision_token,
                    format!(
                        "structure-input-v1:archive-sip-condition:{}",
                        self.structure_condition_revision
                    ),
                ),
            };
            let (event_count, first_ordinal, next_ordinal, first_sip, last_sip) = row
                .map(|row| {
                    (
                        row.event_count,
                        row.next_ordinal.saturating_sub(row.event_count),
                        row.next_ordinal,
                        row.first_sip_timestamp_us,
                        row.last_sip_timestamp_us,
                    )
                })
                .unwrap_or_default();
            sessions.push(StructureCampaignSession {
                event_count,
                first_ordinal,
                first_sip_timestamp_us: first_sip,
                last_sip_timestamp_us: last_sip,
                next_ordinal,
                session_date,
                source_revision,
            });
        }
        Ok(StructureCampaignManifest {
            authority_start,
            sessions,
            split_adjustments,
            ticker,
        })
    }

    /// Stream one archive session by its physical `(ticker, ordinal)` key.
    /// Requests are split into bounded ordinal ranges while the response body
    /// remains incrementally decoded into bounded event batches.
    pub fn stream_structure_ordinal_session(
        &self,
        session: &StructureCampaignSession,
        ticker: &str,
        batch_size: usize,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        let ticker = normalize_ticker(ticker)?;
        let batch_size = batch_size.clamp(1, 100_000);
        let (sender, receiver) = mpsc::channel(2);
        let source = self.clone();
        let session = session.clone();
        tokio::spawn(async move {
            let result = async {
                if session.event_count == 0 {
                    return Ok(());
                }
                const ORDINALS_PER_QUERY: u64 = 1_000_000;
                let mut first = session.first_ordinal;
                while first < session.next_ordinal {
                    if sender.is_closed() {
                        return Ok(());
                    }
                    let next = first
                        .saturating_add(ORDINALS_PER_QUERY)
                        .min(session.next_ordinal);
                    let select = ordinal_event_select(
                        &format!(
                            "{}.{}{}",
                            source.config.clickhouse_database,
                            source.config.table_prefix,
                            session.session_date.year()
                        ),
                        &ticker,
                        first,
                        next,
                    );
                    let sql =
                        format!("SELECT * FROM ({select}) ORDER BY ordinal FORMAT TabSeparated");
                    source
                        .stream_query_rows(sql, batch_size, sender.clone())
                        .await?;
                    first = next;
                }
                Ok(())
            }
            .await;
            if let Err(error) = result {
                let _ = sender.send(Err(error)).await;
            }
        });
        Ok(receiver)
    }

    /// Stream exactly one bounded ordinal range. Campaign workers use this
    /// primitive so the HTTP response is fully drained before CPU-heavy level
    /// calculation begins, preventing ClickHouse socket backpressure.
    pub fn stream_structure_ordinal_range(
        &self,
        session_date: NaiveDate,
        ticker: &str,
        first_ordinal: u64,
        next_ordinal: u64,
        batch_size: usize,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        let ticker = normalize_ticker(ticker)?;
        if first_ordinal >= next_ordinal {
            return Err("structure ordinal range must be non-empty".to_string());
        }
        let batch_size = batch_size.clamp(1, 100_000);
        let (sender, receiver) = mpsc::channel(2);
        let source = self.clone();
        tokio::spawn(async move {
            let select = ordinal_event_select(
                &format!(
                    "{}.{}{}",
                    source.config.clickhouse_database,
                    source.config.table_prefix,
                    session_date.year()
                ),
                &ticker,
                first_ordinal,
                next_ordinal,
            );
            let sql = format!("SELECT * FROM ({select}) ORDER BY ordinal FORMAT TabSeparated");
            if let Err(error) = source
                .stream_query_rows(sql, batch_size, sender.clone())
                .await
            {
                let _ = sender.send(Err(error)).await;
            }
        });
        Ok(receiver)
    }

    async fn archive_execution_clock_revision(
        &self,
        window: &EventWindow,
        plan: &MarketSourcePlan,
    ) -> Result<String, String> {
        let archive_bounds = plan
            .segments
            .iter()
            .filter(|segment| matches!(segment.tier, MarketSourceTier::Archive))
            .fold(None, |bounds, segment| match bounds {
                None => Some((segment.start, segment.end)),
                Some((start, end)) => Some((start.min(segment.start), end.max(segment.end))),
            });
        let Some((archive_start, archive_end)) = archive_bounds else {
            return Ok("execution-clock:not-applicable".to_string());
        };
        let ticker_predicate = if window.tickers.is_empty() {
            String::new()
        } else {
            let values = window
                .tickers
                .iter()
                .map(|ticker| normalize_ticker(ticker))
                .collect::<Result<Vec<_>, _>>()?
                .into_iter()
                .map(|ticker| sql_literal(&ticker))
                .collect::<Vec<_>>()
                .join(",");
            format!(" AND source.ticker IN ({values})")
        };
        let start_date = archive_start.with_timezone(&New_York).date_naive();
        let end_date = (archive_end - chrono::Duration::microseconds(1))
            .with_timezone(&New_York)
            .date_naive();
        let sql = format!(
            r#"SELECT
                count() AS expected_ticker_days,
                countIf(c.event_count = h.event_count AND h.trade_count = h.clock_count AND position(h.source_filter_key, '|execution_clock_v1|') > 0 AND endsWith(h.source_filter_key, '|delayed_audit_v1')) AS covered_ticker_days,
                sum(ifNull(h.clock_count, 0)) AS clock_rows,
                sum(ifNull(h.delayed_trade_report_count, 0)) AS delayed_trade_reports,
                max(ifNull(h.build_step, 0)) AS max_build_step,
                toString(max(ifNull(h.latest_clock_updated_at, toDateTime64(0, 3, 'UTC')))) AS max_updated_at
            FROM
            (
                SELECT
                    source.ticker,
                    source.source_date,
                    argMax(source.event_count, tuple(source.build_step, source.updated_at)) AS event_count
                FROM {}.events_ordinal_continuity AS source
                WHERE source.source_date >= toDate('{}')
                  AND source.source_date <= toDate('{}'){}
                GROUP BY source.ticker, source.source_date
            ) AS c
            LEFT JOIN
            (
                SELECT
                    source.ticker,
                    source.source_date,
                    argMax(source.event_count, source.updated_at) AS event_count,
                    argMax(source.trade_count, source.updated_at) AS trade_count,
                    argMax(source.clock_count, source.updated_at) AS clock_count,
                    argMax(source.delayed_trade_report_count, source.updated_at) AS delayed_trade_report_count,
                    argMax(source.build_step, source.updated_at) AS build_step,
                    argMax(source.source_filter_key, source.updated_at) AS source_filter_key,
                    max(source.updated_at) AS latest_clock_updated_at
                FROM {}.{} AS source
                WHERE source.source_date >= toDate('{}')
                  AND source.source_date <= toDate('{}'){}
                GROUP BY source.ticker, source.source_date
            ) AS h ON h.ticker = c.ticker AND h.source_date = c.source_date
            FORMAT JSONEachRow"#,
            self.config.clickhouse_database,
            start_date,
            end_date,
            ticker_predicate,
            self.config.execution_clock_database,
            self.config.execution_clock_coverage_table,
            start_date,
            end_date,
            ticker_predicate,
        );
        let text = self.query(&sql).await.map_err(|error| {
            format!(
                "historical execution-clock authority is unavailable; populate {}.{} before building execution-aware bars or indicators: {error}",
                self.config.execution_clock_database, self.config.execution_clock_coverage_table
            )
        })?;
        let row = serde_json::from_str::<ExecutionClockCoverageRevisionRow>(text.trim())
            .map_err(|error| format!("invalid execution-clock coverage response: {error}"))?;
        if row.expected_ticker_days != row.covered_ticker_days {
            return Err(format!(
                "historical execution-clock coverage incomplete: covered {}/{} ticker-days for {} through {}; delayed reports cannot safely update execution-aware bars or indicators",
                row.covered_ticker_days, row.expected_ticker_days, start_date, end_date
            ));
        }
        Ok(format!(
            "execution-clock-v1:{}:{}:{}:{}:{}",
            row.covered_ticker_days,
            row.clock_rows,
            row.delayed_trade_reports,
            row.max_build_step,
            row.max_updated_at
        ))
    }

    pub async fn source_revision(&self, window: &EventWindow) -> Result<SourceRevision, String> {
        self.source_revision_for_policy(window, true).await
    }

    /// Revision authority for the structural level book. Completed archive
    /// rows intentionally use SIP availability order plus canonical condition
    /// eligibility and do not require a reconstructed participant clock.
    /// Recent q_live rows retain their native participant clock, matching the
    /// live continuation policy.
    pub async fn structure_source_revision(
        &self,
        window: &EventWindow,
    ) -> Result<SourceRevision, String> {
        self.source_revision_for_policy(window, false).await
    }

    async fn source_revision_for_policy(
        &self,
        window: &EventWindow,
        require_archive_execution_clock: bool,
    ) -> Result<SourceRevision, String> {
        validate_window(window)?;
        let plan = self.source_plan(window).await?;
        let input_policy_revision = if require_archive_execution_clock {
            self.archive_execution_clock_revision(window, &plan).await?
        } else {
            format!(
                "structure-input-v1:archive-sip-condition:recent-participant-aware:{}",
                self.structure_condition_revision
            )
        };
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
                archive_start.with_timezone(&New_York).date_naive(),
                last_inclusive.with_timezone(&New_York).date_naive(),
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
        // A persisted structure checkpoint is not compatible merely because
        // compact events are unchanged. Corporate-action corrections alter
        // every surviving price and quantity coordinate. Include the
        // latest canonical split authority in the same revision token so
        // resume fails closed and rebuilds instead of applying corrected terms
        // on top of a checkpoint produced with superseded terms.
        let split_revision_token = self
            .structure_split_revision_token(&window.tickers, window.start, window.end)
            .await?;
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
                "{}:{}:{}:{}:{}:{}:{}",
                max_build_step,
                event_count,
                live_continuation_sequence.unwrap_or_default(),
                max_updated_at,
                plan_token,
                split_revision_token,
                input_policy_revision,
            ),
        })
    }

    async fn structure_split_revision_token(
        &self,
        tickers: &[String],
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<String, String> {
        if tickers.is_empty() {
            // Full-market consumers do not restore per-ticker structural
            // checkpoints. Avoid turning their ordinary revision probes into
            // an unbounded corporate-action universe query.
            return Ok("split-unscoped".to_string());
        }
        let mut normalized = tickers
            .iter()
            .map(|ticker| normalize_ticker(ticker))
            .collect::<Result<Vec<_>, _>>()?;
        normalized.sort();
        normalized.dedup();
        let ticker_filter = normalized
            .iter()
            .map(|ticker| sql_literal(ticker))
            .collect::<Vec<_>>()
            .join(",");
        let sql = structure_split_revision_sql(&ticker_filter, start, end);
        let text = self.query(&sql).await?;
        let rows = text
            .lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                serde_json::from_str::<StructureSplitRevisionRow>(line)
                    .map_err(|error| format!("invalid structure split revision row: {error}"))
            })
            .collect::<Result<Vec<_>, _>>()?;
        split_revision_token(rows.iter())
    }

    pub async fn fetch_batch(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(window, cursor, limit, false, None, None, true)
            .await
    }

    pub async fn fetch_batch_at_revision_filtered(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(
            window,
            cursor,
            limit,
            false,
            live_continuation_sequence,
            event_type_filter,
            true,
        )
        .await
    }

    pub async fn fetch_structure_batch_at_revision_filtered(
        &self,
        window: &EventWindow,
        cursor: Option<&HistoricalCursor>,
        limit: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        self.fetch_ordered(
            window,
            cursor,
            limit,
            false,
            live_continuation_sequence,
            event_type_filter,
            false,
        )
        .await
    }

    pub async fn fetch_latest(
        &self,
        window: &EventWindow,
        limit: usize,
    ) -> Result<Vec<LiveCompactEvent>, String> {
        let (mut events, _) = self
            .fetch_ordered(window, None, limit, true, None, None, true)
            .await?;
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
        self.stream_ordered_filtered_chunked(
            window,
            batch_size,
            live_continuation_sequence,
            event_type_filter,
            self.config.scanner_fetch_chunk_minutes.max(1),
            true,
        )
    }

    pub fn stream_structure_ordered_filtered(
        &self,
        window: EventWindow,
        batch_size: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
        estimated_event_count: u64,
    ) -> Result<mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>>, String> {
        // Structural reconstruction is single-ticker and the ClickHouse body
        // is consumed incrementally into bounded decoded batches. Size source
        // windows from the already pinned revision count so sparse symbols do
        // not pay thousands of empty four-hour query round trips, while dense
        // symbols retain bounded streaming responses. Adjacent chunks preserve
        // the same causal ordering and checkpoint result.
        let chunk_minutes = adaptive_structure_chunk_minutes(
            &window,
            estimated_event_count,
            self.config.structure_fetch_chunk_minutes.max(1),
        );
        self.stream_ordered_filtered_chunked(
            window,
            batch_size,
            live_continuation_sequence,
            event_type_filter,
            chunk_minutes,
            false,
        )
    }

    fn stream_ordered_filtered_chunked(
        &self,
        window: EventWindow,
        batch_size: usize,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
        chunk_minutes: usize,
        require_archive_execution_clock: bool,
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
                    chunk_minutes,
                    require_archive_execution_clock,
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
        chunk_minutes: usize,
        require_archive_execution_clock: bool,
        sender: mpsc::Sender<Result<Vec<LiveCompactEvent>, String>>,
    ) -> Result<(), String> {
        let plan = self.source_plan(&window).await?;
        if require_archive_execution_clock {
            self.archive_execution_clock_revision(&window, &plan)
                .await?;
        }
        let mut ticker_filter = ticker_filter(&window.tickers)?;
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
        let chunk_duration = chrono::Duration::minutes(chunk_minutes.max(1) as i64);
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
                        require_archive_execution_clock
                            .then_some(format!(
                                "{}.{}",
                                self.config.execution_clock_database,
                                self.config.execution_clock_table
                            ))
                            .as_deref(),
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
                        None,
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
            "{}/?database={}&enable_http_compression=1&send_timeout=900&receive_timeout=900&max_query_size=2097152&max_ast_elements=200000&max_expanded_ast_elements=200000",
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
        let mut response = {
            let mut attempt = 0_u8;
            loop {
                attempt = attempt.saturating_add(1);
                let retry = request
                    .try_clone()
                    .ok_or_else(|| "historical ClickHouse request is not cloneable".to_string())?;
                match retry.send().await {
                    Ok(response) => break response,
                    Err(_error) if attempt < 3 => {
                        // No response body exists yet, so retrying cannot
                        // duplicate streamed events. Once body streaming has
                        // begun, errors below still fail closed.
                        tokio::time::sleep(std::time::Duration::from_millis(
                            250_u64.saturating_mul(1_u64 << (attempt - 1)),
                        ))
                        .await;
                    }
                    Err(error) => {
                        return Err(format!(
                            "ClickHouse request failed before response after {attempt} attempts: {error}"
                        ));
                    }
                }
            }
        };
        let status = response.status();
        if !status.is_success() {
            let text = response.text().await.map_err(|error| error.to_string())?;
            return Err(format!("ClickHouse HTTP {status}: {}", text.trim()));
        }
        let mut buffer = Vec::<u8>::with_capacity(256 * 1024);
        let mut batch = Vec::with_capacity(batch_size);
        while let Some(chunk) = response
            .chunk()
            .await
            .map_err(|error| format!("ClickHouse response body stream failed: {error:#}"))?
        {
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
        event_type_filter: Option<u8>,
        require_archive_execution_clock: bool,
    ) -> Result<(Vec<LiveCompactEvent>, Option<HistoricalCursor>), String> {
        validate_window(window)?;
        let plan = self.source_plan(window).await?;
        if require_archive_execution_clock {
            self.archive_execution_clock_revision(window, &plan).await?;
        }
        let limit = limit.clamp(1, 100_000);
        let mut ticker_filter = ticker_filter(&window.tickers)?;
        if let Some(event_type) = event_type_filter.filter(|value| *value <= 1) {
            ticker_filter.push_str(&format!(
                " AND bitAnd(source.event_meta, 1) = toUInt8({event_type})"
            ));
        }
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
                            require_archive_execution_clock
                                .then_some(format!(
                                    "{}.{}",
                                    self.config.execution_clock_database,
                                    self.config.execution_clock_table
                                ))
                                .as_deref(),
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
                    None,
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
        if let Some(event_type) = event_type_filter.filter(|value| *value <= 1) {
            events.retain(|event| event.event_meta & 1 == event_type);
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
        self.archive_execution_clock_revision(window, &plan).await?;
        let ticker_filter = ticker_filter(&window.tickers)?;
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
                            Some(&format!(
                                "{}.{}",
                                self.config.execution_clock_database,
                                self.config.execution_clock_table
                            )),
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
                        None,
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
        allow_live_fallback: bool,
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
        let mut persisted_source = table.clone();
        let mut fallback_has_more = false;
        if bars.is_empty() && !allow_live_fallback {
            return Ok(None);
        }
        if bars.is_empty() {
            let url = format!(
                "{}/snapshot/intraday-bar-history/{}",
                self.config.live_gateway_url.trim_end_matches('/'),
                urlencoding::encode(&ticker)
            );
            let response = self
                .client
                .get(url)
                .query(&[
                    ("timeframe", timeframe.to_string()),
                    ("start_date", start_date.to_string()),
                    ("end_date", end_date.to_string()),
                    (
                        "before_event_timestamp_us",
                        as_of.timestamp_micros().max(0).to_string(),
                    ),
                    ("limit", limit.clamp(1, 50_000).to_string()),
                ])
                .send()
                .await
                .map_err(|error| format!("QMD Live persisted-bar request failed: {error}"))?;
            let status = response.status();
            if !status.is_success() {
                let detail = response.text().await.unwrap_or_default();
                return Err(format!(
                    "QMD Live persisted-bar request returned HTTP {status}: {detail}"
                ));
            }
            let payload = response
                .json::<Value>()
                .await
                .map_err(|error| format!("invalid QMD Live persisted-bar response: {error}"))?;
            persisted_source = payload
                .get("source")
                .and_then(Value::as_str)
                .unwrap_or("qmd_live_intraday_bars")
                .to_string();
            fallback_has_more = payload
                .get("has_more")
                .and_then(Value::as_bool)
                .unwrap_or(false);
            bars = payload
                .get("bars")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(|row| {
                    let bar_start = row
                        .get("bar_start")
                        .and_then(Value::as_str)
                        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())?
                        .with_timezone(&Utc);
                    let bar_end = row
                        .get("bar_end")
                        .and_then(Value::as_str)
                        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())?
                        .with_timezone(&Utc);
                    if bar_start < window.start
                        || bar_start >= window.end
                        || bar_end > as_of
                        || before.is_some_and(|bound| bar_start >= bound)
                    {
                        return None;
                    }
                    Some(HistoricalIntradayChartRow {
                        bar_end,
                        bar_start,
                        close: row.get("close")?.as_f64()?,
                        event_count: row.get("trade_count")?.as_u64()?,
                        high: row.get("high")?.as_f64()?,
                        low: row.get("low")?.as_f64()?,
                        open: row.get("open")?.as_f64()?,
                        session_date: row.get("session_date")?.as_str()?.to_string(),
                        size_sum: row.get("volume")?.as_f64()?,
                    })
                })
                .collect();
            if bars.is_empty() {
                return Ok(None);
            }
        }
        let has_more = fallback_has_more || bars.len() > limit;
        bars.truncate(limit);
        bars.reverse();
        let adjustments = self
            .structure_split_adjustments(
                &ticker,
                window.start - chrono::Duration::milliseconds(1),
                as_of,
            )
            .await?;
        adjust_intraday_chart_bars_for_splits(&mut bars, &adjustments);
        let next_before = has_more.then(|| bars[0].bar_start);
        Ok(Some(HistoricalIntradayChartSnapshot {
            bars,
            has_more,
            next_before,
            source: persisted_source,
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
        let mut bars = parse_macro_chart_rows(&text, timeframe, as_of)?;
        let mut source = table;
        let mut has_large_archive_to_recent_gap = false;
        if timeframe == "1d" {
            let archive_latest = bars.last().map(|bar| bar.bar_start.date_naive());
            let recent_table = format!(
                "{}.{}",
                self.config.recent_database, self.config.recent_intraday_family_bars_table
            );
            let recent_sql = recent_daily_trade_bars_sql(
                &recent_table,
                &ticker,
                window.start.date_naive(),
                window.end.date_naive(),
                as_of,
            );
            let recent_text = self.query(&recent_sql).await?;
            let recent_bars = parse_macro_chart_rows(&recent_text, timeframe, as_of)?;
            let recent_earliest_after_archive = archive_latest.and_then(|archive| {
                recent_bars
                    .iter()
                    .map(|bar| bar.bar_start.date_naive())
                    .find(|date| *date > archive)
            });
            has_large_archive_to_recent_gap = archive_latest
                .zip(recent_earliest_after_archive)
                .is_some_and(|(archive, recent)| recent - archive > chrono::Duration::days(7));
            if !recent_bars.is_empty() {
                merge_daily_chart_bars(&mut bars, recent_bars);
                source = format!("{source}+{recent_table}");
            }
        }
        let adjustments = self
            .structure_split_adjustments(
                &ticker,
                window.start - chrono::Duration::milliseconds(1),
                as_of,
            )
            .await?;
        adjust_macro_chart_bars_for_splits(&mut bars, &adjustments);
        let latest_session_date = bars.last().map(|bar| bar.session_date.clone());
        let freshness_floor =
            as_of.with_timezone(&New_York).date_naive() - chrono::Duration::days(7);
        let coverage_status = bars
            .last()
            .map(|bar| bar.bar_end.with_timezone(&New_York).date_naive())
            .map(|date| {
                if date >= freshness_floor && has_large_archive_to_recent_gap {
                    "partial"
                } else if date >= freshness_floor {
                    "ready"
                } else {
                    "stale"
                }
            })
            .unwrap_or("unavailable")
            .to_string();
        Ok(HistoricalMacroChartSnapshot {
            as_of,
            bars,
            coverage_status,
            latest_session_date,
            source,
            split_adjustments: adjustments,
            split_adjusted: true,
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
            return Err(format!(
                "required persisted Generic Structure ticker-book table {table} is unavailable"
            ));
        }
        let max_events = self.config.structure_book_max_seed_events;
        let sql = persisted_structure_events_sql(
            &table,
            &ticker,
            before,
            self.config.structure_book_lookback_days,
            max_events.saturating_add(1),
        );
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
        if events.len() > max_events {
            return Err(format!(
                "persisted Generic Structure ticker book for {ticker} exceeded the configured complete-seed limit of {max_events} events; refusing to build a silently truncated prior-session book"
            ));
        }
        events.sort_by_key(|event| (event.confirmed_at, event.event_id));
        Ok(events)
    }

    pub async fn persisted_structure_checkpoint_before(
        &self,
        ticker: &str,
        before: DateTime<Utc>,
    ) -> Result<Option<PersistedStructureCheckpointSeed>, String> {
        let ticker = normalize_ticker(ticker)?;
        let table = format!(
            "{}.{}",
            self.config.structure_database, self.config.structure_daily_checkpoint_table
        );
        let exists_sql = format!(
            "SELECT count() FROM system.tables WHERE database = {} AND name = {} FORMAT TSV",
            sql_literal(&self.config.structure_database),
            sql_literal(&self.config.structure_daily_checkpoint_table)
        );
        let available = *self
            .structure_daily_checkpoint_table_available
            .get_or_try_init(|| async {
                Ok::<bool, String>(self.query(&exists_sql).await?.trim() == "1")
            })
            .await?;
        if !available {
            return Ok(None);
        }
        let sql = format!(
            r#"SELECT
                formatDateTime(authority_start, '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS authority_start,
                toString(session_date) AS session_date,
                source_plan_hash,
                source_revision_token,
                snapshot_json,
                certification_json
            FROM {table} FINAL
            WHERE checkpoint_set_id = {checkpoint_set_id}
              AND sym = {ticker}
              AND algorithm_version = {algorithm_version}
              AND checkpoint_at < parseDateTime64BestEffort({before}, 6, 'UTC')
              AND source_complete = 1
            ORDER BY session_date DESC, built_at DESC
            LIMIT 1
            FORMAT JSONEachRow"#,
            ticker = sql_literal(&ticker),
            checkpoint_set_id = sql_literal(&self.config.structure_checkpoint_set_id),
            algorithm_version = GENERIC_STRUCTURE_ALGORITHM_VERSION,
            before = sql_literal(&before.to_rfc3339()),
        );
        let text = self.query(&sql).await?;
        let Some(line) = text.lines().find(|line| !line.trim().is_empty()) else {
            return Ok(None);
        };
        let row = serde_json::from_str::<PersistedStructureCheckpointRow>(line)
            .map_err(|error| format!("invalid persisted structure checkpoint row: {error}"))?;
        if !source_revision_uses_historical_structure_policy_v1(&row.source_revision_token) {
            return Err(format!(
                "persisted structure checkpoint for {ticker} does not use the required historical SIP-plus-condition structure contract; rebuild or recover it into the configured checkpoint set before using it for charts or strategies"
            ));
        }
        let authority_start = DateTime::parse_from_rfc3339(&row.authority_start)
            .map_err(|error| format!("invalid structure checkpoint authority start: {error}"))?
            .with_timezone(&Utc);
        let stored_checkpoint_value = serde_json::from_str::<Value>(&row.snapshot_json)
            .map_err(|error| format!("invalid persisted structure checkpoint JSON: {error}"))?;
        let mut checkpoint = serde_json::from_value::<GenericStructureCheckpoint>(
            stored_checkpoint_value.clone(),
        )
        .map_err(|error| format!("invalid persisted structure checkpoint payload: {error}"))?;
        let stored_certification_value = checkpoint_certification_value(&stored_checkpoint_value);
        let decoded_checkpoint_value = checkpoint_json_value(&checkpoint)?;
        let decoded_certification_value = checkpoint_certification_value(&decoded_checkpoint_value);
        if let Some(path) = first_json_difference_path(
            &stored_certification_value,
            &decoded_certification_value,
            "$",
        ) {
            return Err(format!(
                "persisted structure checkpoint causal state is not schema-stable at {path}"
            ));
        }
        let stored_checkpoint_sha256 = canonical_json_sha256(&stored_certification_value)?;
        let decoded_checkpoint_sha256 = checkpoint_sha256(&checkpoint)?;
        if stored_checkpoint_sha256 != decoded_checkpoint_sha256 {
            return Err(format!(
                "persisted structure checkpoint is not canonically stable: stored={stored_checkpoint_sha256}, decoded={decoded_checkpoint_sha256}"
            ));
        }
        let session_date = NaiveDate::parse_from_str(&row.session_date, "%Y-%m-%d")
            .map_err(|error| format!("invalid persisted structure checkpoint date: {error}"))?;
        let certification =
            serde_json::from_str::<StructureCheckpointCertification>(&row.certification_json)
                .map_err(|error| {
                    format!("persisted structure checkpoint lacks valid certification: {error}")
                })?;
        validate_checkpoint_certification(
            &certification,
            &checkpoint,
            session_date,
            authority_start,
            &row.source_plan_hash,
            &row.source_revision_token,
        )?;
        if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
            || checkpoint.sym.to_ascii_uppercase() != ticker
            || checkpoint.last_arrival_sequence == 0
            || row.source_plan_hash.trim().is_empty()
            || row.source_revision_token.trim().is_empty()
        {
            return Err("persisted structure checkpoint identity is invalid".to_string());
        }
        // Split rows are an independently corrected authority. Reapply the
        // complete checkpoint horizon on every checkout so a split inserted
        // or corrected after the daily checkpoint was built still adjusts all
        // surviving historical levels. The checkpoint records applied split
        // identities, making this idempotent for already-correct checkpoints.
        let split_start = authority_start
            .checked_sub_signed(chrono::Duration::microseconds(1))
            .ok_or_else(|| "structure checkpoint split horizon underflow".to_string())?;
        for adjustment in self
            .structure_split_adjustments(&ticker, split_start, before)
            .await?
        {
            checkpoint.apply_split_adjustment(&adjustment)?;
        }
        Ok(Some(PersistedStructureCheckpointSeed {
            authority_start,
            checkpoint,
            source_plan_hash: row.source_plan_hash,
            source_revision_token: row.source_revision_token,
        }))
    }

    pub async fn structure_split_adjustments(
        &self,
        ticker: &str,
        after: DateTime<Utc>,
        through: DateTime<Utc>,
    ) -> Result<Vec<StructureSplitAdjustment>, String> {
        let ticker = normalize_ticker(ticker)?;
        if through < after {
            return Err("split adjustment range ends before it starts".to_string());
        }
        let sql = structure_split_adjustments_sql(&ticker, after, through);
        let text = self.query(&sql).await?;
        let mut adjustments = Vec::new();
        for line in text.lines().filter(|line| !line.trim().is_empty()) {
            let row = serde_json::from_str::<StockSplitAdjustmentRow>(line)
                .map_err(|error| format!("invalid stock split adjustment row: {error}"))?;
            let execution_date = NaiveDate::parse_from_str(&row.execution_date, "%Y-%m-%d")
                .map_err(|error| format!("invalid stock split execution_date: {error}"))?;
            let effective_at = New_York
                .with_ymd_and_hms(
                    execution_date.year(),
                    execution_date.month(),
                    execution_date.day(),
                    4,
                    0,
                    0,
                )
                .single()
                .ok_or_else(|| "invalid New York stock split boundary".to_string())?
                .with_timezone(&Utc);
            if effective_at < after || effective_at > through {
                continue;
            }
            let source_inserted_at = DateTime::parse_from_rfc3339(&row.source_inserted_at)
                .map_err(|error| format!("invalid stock split inserted_at: {error}"))?
                .with_timezone(&Utc);
            adjustments.push(StructureSplitAdjustment {
                execution_date,
                effective_at,
                split_from: row.split_from,
                split_to: row.split_to,
                source_inserted_at,
            });
        }
        Ok(adjustments)
    }

    pub async fn latest_coverage_before(
        &self,
        before: Option<chrono::NaiveDate>,
    ) -> Result<LatestEventCoverage, String> {
        if let Some(cached) = self.cached_latest_coverage(before).await {
            return Ok(cached);
        }

        // Every source-plan and status request needs this watermark. Serialize
        // cold lookups so a slow or unhealthy ClickHouse cannot turn client
        // retries into many copies of the same server-side aggregation.
        let _query_guard = self.latest_coverage_query_gate.lock().await;
        if let Some(cached) = self.cached_latest_coverage(before).await {
            return Ok(cached);
        }

        let coverage_table = format!(
            "{}.events_ordinal_continuity",
            self.config.clickhouse_database
        );
        let target_sql = latest_coverage_target_date_sql(&coverage_table, before);
        let target_text = self.query(&target_sql).await?;
        let target_date = target_text
            .trim()
            .split_whitespace()
            .next()
            .filter(|value| !value.is_empty() && *value != "\\N")
            .map(|value| {
                NaiveDate::parse_from_str(value, "%Y-%m-%d").map_err(|error| {
                    format!("invalid latest historical coverage date {value:?}: {error}")
                })
            })
            .transpose()?;
        let row = if let Some(target_date) = target_date {
            let summary_sql = latest_coverage_summary_sql(&coverage_table, target_date);
            let text = self.query(&summary_sql).await?;
            text.lines()
                .find(|line| !line.trim().is_empty())
                .map(|line| {
                    serde_json::from_str::<LatestEventCoverageRow>(line).map_err(|error| {
                        format!("invalid latest historical coverage response: {error}")
                    })
                })
                .transpose()?
        } else {
            None
        };
        let value = LatestEventCoverage {
            coverage_table,
            event_count: row.as_ref().map_or(0, |value| value.event_count),
            session_date: row.as_ref().map(|value| value.session_date.clone()),
            ticker_count: row.map_or(0, |value| value.ticker_count),
        };
        self.store_latest_coverage(before, value.clone()).await;
        Ok(value)
    }

    async fn cached_latest_coverage(
        &self,
        before: Option<NaiveDate>,
    ) -> Option<LatestEventCoverage> {
        let now = Instant::now();
        let mut cache = self.latest_coverage_cache.lock().await;
        cache.retain(|_, entry| entry.expires_at > now);
        if let Some(entry) = cache.get(&before) {
            return Some(entry.value.clone());
        }
        // Readiness warms the unrestricted archive watermark. When that
        // watermark is already earlier than a bounded request, it is also the
        // exact latest session before that bound, so avoid repeating the same
        // expensive ClickHouse aggregation for Replay preflight.
        let bounded_before = before?;
        cache
            .get(&None)
            .filter(|entry| coverage_precedes(&entry.value, bounded_before))
            .map(|entry| entry.value.clone())
    }

    async fn store_latest_coverage(&self, before: Option<NaiveDate>, value: LatestEventCoverage) {
        let now = Instant::now();
        let mut cache = self.latest_coverage_cache.lock().await;
        cache.retain(|_, entry| entry.expires_at > now);
        if cache.len() >= LATEST_COVERAGE_CACHE_MAX_ENTRIES && !cache.contains_key(&before) {
            if let Some(oldest_key) = cache
                .iter()
                .min_by_key(|(_, entry)| entry.expires_at)
                .map(|(key, _)| *key)
            {
                cache.remove(&oldest_key);
            }
        }
        cache.insert(
            before,
            CachedLatestEventCoverage {
                expires_at: now + LATEST_COVERAGE_CACHE_TTL,
                value,
            },
        );
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

fn source_revision_uses_historical_structure_policy_v1(token: &str) -> bool {
    token.contains(":structure-input-v1:archive-sip-condition:")
}

fn adaptive_structure_chunk_minutes(
    window: &EventWindow,
    estimated_event_count: u64,
    minimum_chunk_minutes: usize,
) -> usize {
    const TARGET_EVENTS_PER_QUERY: u64 = 50_000;
    const MAX_CHUNK_MINUTES: usize = 7 * 24 * 60;

    let duration_seconds = (window.end - window.start).num_seconds().max(1) as u64;
    let duration_minutes = duration_seconds.div_ceil(60).max(1) as usize;
    if estimated_event_count == 0 {
        return duration_minutes.min(MAX_CHUNK_MINUTES);
    }
    let estimated_minutes = duration_minutes
        .saturating_mul(TARGET_EVENTS_PER_QUERY as usize)
        .div_ceil(estimated_event_count.min(usize::MAX as u64) as usize);
    estimated_minutes
        .max(minimum_chunk_minutes.max(1))
        .min(duration_minutes)
        .min(MAX_CHUNK_MINUTES)
}

pub(crate) fn split_adjustment_factors(
    observed_at: DateTime<Utc>,
    adjustments: &[StructureSplitAdjustment],
) -> (f64, f64) {
    adjustments
        .iter()
        .filter(|adjustment| observed_at < adjustment.effective_at)
        .fold((1.0, 1.0), |(price_factor, share_factor), adjustment| {
            (
                price_factor * adjustment.split_from / adjustment.split_to,
                share_factor * adjustment.split_to / adjustment.split_from,
            )
        })
}

fn adjust_intraday_chart_bars_for_splits(
    bars: &mut [HistoricalIntradayChartRow],
    adjustments: &[StructureSplitAdjustment],
) {
    for bar in bars {
        let (price_factor, share_factor) = split_adjustment_factors(bar.bar_start, adjustments);
        bar.open *= price_factor;
        bar.high *= price_factor;
        bar.low *= price_factor;
        bar.close *= price_factor;
        bar.size_sum *= share_factor;
    }
}

fn adjust_macro_chart_bars_for_splits(
    bars: &mut [HistoricalMacroChartRow],
    adjustments: &[StructureSplitAdjustment],
) {
    for bar in bars {
        let (price_factor, share_factor) = split_adjustment_factors(bar.bar_start, adjustments);
        bar.open *= price_factor;
        bar.high *= price_factor;
        bar.low *= price_factor;
        bar.close *= price_factor;
        bar.size_sum *= share_factor;
    }
}

fn coverage_precedes(value: &LatestEventCoverage, before: NaiveDate) -> bool {
    value
        .session_date
        .as_deref()
        .and_then(|session_date| NaiveDate::parse_from_str(session_date, "%Y-%m-%d").ok())
        .is_some_and(|session_date| session_date < before)
}

fn latest_coverage_target_date_sql(table: &str, before: Option<NaiveDate>) -> String {
    let before_filter = before
        .map(|value| format!(" WHERE source_date < toDate('{value}')"))
        .unwrap_or_default();
    format!(
        "SELECT ifNull(toString(maxOrNull(source_date)), '') \
         FROM {table}{before_filter} \
         SETTINGS max_threads = {LATEST_COVERAGE_QUERY_MAX_THREADS}, \
             max_memory_usage = {LATEST_COVERAGE_QUERY_MAX_MEMORY_BYTES}, \
             max_execution_time = {LATEST_COVERAGE_QUERY_MAX_SECONDS} \
         FORMAT TSV"
    )
}

fn latest_coverage_summary_sql(table: &str, source_date: NaiveDate) -> String {
    format!(
        r#"SELECT
            toString(toDate('{source_date}')) AS session_date,
            sum(canonical_event_count) AS event_count,
            uniqExact(ticker) AS ticker_count
        FROM (
            SELECT
                ticker,
                argMax(event_count, tuple(build_step, updated_at)) AS canonical_event_count
            FROM {table}
            WHERE source_date = toDate('{source_date}')
            GROUP BY ticker
        )
        WHERE canonical_event_count > 0
        SETTINGS max_threads = {LATEST_COVERAGE_QUERY_MAX_THREADS},
            max_memory_usage = {LATEST_COVERAGE_QUERY_MAX_MEMORY_BYTES},
            max_execution_time = {LATEST_COVERAGE_QUERY_MAX_SECONDS}
        FORMAT JSONEachRow"#
    )
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
    execution_clock_table: Option<&str>,
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
    // The immutable archive stays unchanged. Its exact participant clock is
    // restored from a separately certified sidecar keyed by ticker/ordinal.
    let execution_timestamp = if recent {
        "source.execution_timestamp_us"
    } else if execution_clock_table.is_some() {
        "if(bitAnd(source.event_meta, 1) = 1, execution_clock.execution_timestamp_us, toUInt64(0))"
    } else {
        "toUInt64(0)"
    };
    let final_clause = if recent { " FINAL" } else { "" };
    let last_inclusive = end - chrono::Duration::microseconds(1);
    let clock_ticker_filter = ticker_filter
        .split(" AND bitAnd(")
        .next()
        .unwrap_or_default()
        .replace("source.ticker", "ticker");
    let execution_clock_join = execution_clock_table
        .map(|clock| {
            format!(
                " LEFT JOIN (SELECT ticker, ordinal, execution_timestamp_us FROM {clock} FINAL WHERE source_date >= toDate('{}') AND source_date <= toDate('{}'){}) AS execution_clock ON execution_clock.ticker = source.ticker AND execution_clock.ordinal = source.ordinal",
                start.with_timezone(&New_York).date_naive(),
                last_inclusive.with_timezone(&New_York).date_naive(),
                clock_ticker_filter,
            )
        })
        .unwrap_or_default();
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
    format!(
        r#"SELECT
            upper(source.ticker) AS ticker,
            {ordinal} AS ordinal,
            {source_sequence} AS source_sequence,
            source.event_meta,
            {execution_timestamp} AS execution_timestamp_us,
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
        FROM {table} AS source{final_clause}{execution_clock_join}
        PREWHERE source.event_date >= toDate('{start_date}')
          AND source.event_date <= toDate('{end_date}')
          AND source.sip_timestamp_us >= {start_us}
          AND source.sip_timestamp_us < {end_us}{ticker_filter}
        WHERE 1{cursor_filter}"#,
        start_date = start.date_naive(),
        end_date = last_inclusive.date_naive(),
        start_us = start.timestamp_micros(),
        end_us = end.timestamp_micros(),
    )
}

fn ordinal_event_select(table: &str, ticker: &str, first: u64, next: u64) -> String {
    ordinal_event_select_filtered(table, None, ticker, first, next, None)
}

fn ordinal_event_select_filtered(
    table: &str,
    execution_clock_table: Option<&str>,
    ticker: &str,
    first: u64,
    next: u64,
    event_type: Option<u8>,
) -> String {
    let event_filter = event_type
        .filter(|value| *value <= 1)
        .map(|value| format!(" AND bitAnd(source.event_meta, 1) = toUInt8({value})"))
        .unwrap_or_default();
    let execution_timestamp = execution_clock_table
        .map(|_| {
            "if(bitAnd(source.event_meta, 1) = 1, execution_clock.execution_timestamp_us, toUInt64(0))"
        })
        .unwrap_or("toUInt64(0)");
    let execution_clock_join = execution_clock_table
        .map(|table| {
            format!(
                r#"LEFT JOIN
        (
            SELECT ticker, ordinal, execution_timestamp_us
            FROM {table} FINAL
            WHERE ticker = {ticker}
              AND ordinal >= {first}
              AND ordinal < {next}
        ) AS execution_clock
          ON execution_clock.ticker = source.ticker AND execution_clock.ordinal = source.ordinal"#,
                ticker = sql_literal(ticker),
            )
        })
        .unwrap_or_default();
    format!(
        r#"SELECT
            upper(source.ticker) AS ticker,
            source.ordinal AS ordinal,
            source.ordinal AS source_sequence,
            source.event_meta,
            {execution_timestamp} AS execution_timestamp_us,
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
        FROM {table} AS source
        {execution_clock_join}
        PREWHERE source.ticker = {ticker}
          AND source.ordinal >= {first}
          AND source.ordinal < {next}{event_filter}"#,
        ticker = sql_literal(ticker),
    )
}

fn tradable_tickers_sql(reference_database: &str, as_of_date: NaiveDate) -> Result<String, String> {
    if !reference_database
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    {
        return Err("tradable universe database is not a valid identifier".to_string());
    }
    Ok(format!(
        r#"SELECT upper(ticker) AS ticker
           FROM `{reference_database}`.`feature_tradable_universe_v1` FINAL
           WHERE universe_date = (
               SELECT max(universe_date)
               FROM `{reference_database}`.`feature_tradable_universe_v1` FINAL
               WHERE universe_date <= toDate('{as_of_date}')
           )
             AND is_tradable = 1
             AND match(upper(ticker), '^[A-Z0-9._-]{{1,32}}$')
           GROUP BY ticker
           ORDER BY ticker
           FORMAT TSV"#,
    ))
}

fn indicator_warmup_ordinal_sessions_sql(
    archive_database: &str,
    execution_clock_database: &str,
    execution_clock_table: &str,
    ticker: &str,
    before_date: NaiveDate,
    max_sessions: usize,
) -> Result<String, String> {
    if [
        archive_database,
        execution_clock_database,
        execution_clock_table,
    ]
    .iter()
    .any(|identifier| {
        !identifier
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
    }) {
        return Err("indicator warm-up archive database is not a valid identifier".to_string());
    }
    Ok(format!(
        r#"SELECT
               toString(continuity.source_date) AS session_date,
               continuity.event_count AS event_count,
               toUInt8(
                   continuity.event_count = ifNull(clock.event_count, 0)
                   AND ifNull(clock.trade_count, 0) = ifNull(clock.clock_count, 0)
                   AND position(ifNull(clock.source_filter_key, ''), '|execution_clock_v1|') > 0
                   AND endsWith(ifNull(clock.source_filter_key, ''), '|delayed_audit_v1')
               ) AS execution_clock_complete,
               concat(
                   'execution-clock-v1:',
                   toString(ifNull(clock.clock_count, 0)), ':',
                   toString(ifNull(clock.build_step, 0)), ':',
                   ifNull(clock.max_updated_at, '')
               ) AS execution_clock_revision,
               continuity.next_ordinal - continuity.event_count AS first_ordinal,
               continuity.next_ordinal AS next_ordinal
           FROM
           (
               SELECT
                   source.source_date AS source_date,
                   argMax(source.event_count, tuple(source.build_step, source.updated_at)) AS event_count,
                   argMax(source.next_ordinal, tuple(source.build_step, source.updated_at)) AS next_ordinal
               FROM `{archive_database}`.`events_ordinal_continuity` AS source
               PREWHERE source.ticker = {ticker}
                 AND source.source_date < toDate('{before_date}')
               GROUP BY source.source_date
               HAVING event_count > 0
               ORDER BY source_date DESC
               LIMIT {max_sessions}
           ) AS continuity
           LEFT JOIN
           (
               SELECT
                   source.source_date AS source_date,
                   argMax(source.event_count, source.updated_at) AS event_count,
                   argMax(source.trade_count, source.updated_at) AS trade_count,
                   argMax(source.clock_count, source.updated_at) AS clock_count,
                   argMax(source.build_step, source.updated_at) AS build_step,
                   argMax(source.source_filter_key, source.updated_at) AS source_filter_key,
                   toString(max(source.updated_at)) AS max_updated_at
               FROM `{execution_clock_database}`.`{execution_clock_table}` AS source
               PREWHERE source.ticker = {ticker}
                 AND source.source_date < toDate('{before_date}')
               GROUP BY source.source_date
           ) AS clock ON clock.source_date = continuity.source_date
           ORDER BY continuity.source_date DESC
           LIMIT {max_sessions}
           FORMAT JSONEachRow"#,
        ticker = sql_literal(ticker),
        execution_clock_database = execution_clock_database,
        execution_clock_table = execution_clock_table,
    ))
}

fn ticker_filter(tickers: &[String]) -> Result<String, String> {
    if tickers.is_empty() {
        return Ok(String::new());
    }
    let values = tickers
        .iter()
        .map(|ticker| normalize_ticker(ticker))
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .map(|ticker| sql_literal(&ticker))
        .collect::<Vec<_>>()
        .join(",");
    Ok(format!(" AND source.ticker IN ({values})"))
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

fn parse_recent_coverage_rows(text: &str) -> Result<Vec<RecentCoverageRow>, String> {
    text.lines()
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
        .collect()
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

fn recent_daily_trade_bars_sql(
    table: &str,
    ticker: &str,
    start_date: NaiveDate,
    end_date: NaiveDate,
    as_of: DateTime<Utc>,
) -> String {
    format!(
        r#"SELECT
            toString(local_date) AS session_date,
            '1d' AS timeframe,
            ticker,
            'trade' AS bar_family,
            formatDateTime(
                toDateTime(local_date, 'America/New_York') + INTERVAL 4 HOUR,
                '%Y-%m-%dT%H:%i:%sZ',
                'UTC'
            ) AS bar_start,
            formatDateTime(
                toDateTime(local_date, 'America/New_York') + INTERVAL 20 HOUR,
                '%Y-%m-%dT%H:%i:%sZ',
                'UTC'
            ) AS bar_end,
            argMin(source_open, tuple(bucket_index, updated_at_utc)) AS open,
            argMax(source_close, tuple(bucket_index, updated_at_utc)) AS close,
            max(source_high) AS high,
            min(source_low) AS low,
            sum(size_sum) AS size_sum,
            sum(event_count) AS event_count
        FROM (
            SELECT
                ticker,
                local_date,
                bucket_index,
                updated_at_utc,
                open AS source_open,
                high AS source_high,
                low AS source_low,
                close AS source_close,
                size_sum,
                event_count
            FROM {table} FINAL
            PREWHERE ticker = {ticker}
              AND local_date >= toDate('{start_date}')
              AND local_date < toDate('{end_date}')
            WHERE label_resolution_us = 3600000000
              AND bar_family = 'trade'
              AND calculation_revision = 'qmd-family-bars-v3'
              AND complete = 1
              AND updated_at_utc <= parseDateTime64BestEffort({as_of})
              AND bar_start_session_us >= 14400000000
              AND bar_start_session_us < 72000000000
              AND open > 0
              AND high > 0
              AND low > 0
              AND close > 0
        )
        GROUP BY ticker, local_date
        HAVING event_count > 0
        ORDER BY local_date
        FORMAT JSONEachRow"#,
        ticker = sql_literal(ticker),
        as_of = sql_literal(&as_of.to_rfc3339()),
    )
}

fn parse_macro_chart_rows(
    text: &str,
    timeframe: &str,
    as_of: DateTime<Utc>,
) -> Result<Vec<HistoricalMacroChartRow>, String> {
    text.lines()
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
        .collect()
}

fn merge_daily_chart_bars(
    archive_bars: &mut Vec<HistoricalMacroChartRow>,
    recent_bars: Vec<HistoricalMacroChartRow>,
) {
    let mut merged = BTreeMap::new();
    for bar in recent_bars {
        merged.insert(bar.session_date.clone(), bar);
    }
    // Finalized archive rows remain authoritative whenever both tiers cover a session.
    for bar in archive_bars.drain(..) {
        merged.insert(bar.session_date.clone(), bar);
    }
    *archive_bars = merged.into_values().collect();
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
    LiveCompactEvent::from_persisted_fields(
        row.ordinal,
        row.condition_token_1,
        row.condition_token_2,
        row.condition_token_3,
        row.condition_token_4,
        row.condition_token_5,
        row.event_date,
        row.event_meta,
        row.execution_timestamp_us,
        row.exchange_primary,
        row.exchange_secondary,
        ingest_ts,
        0,
        row.price_primary_int,
        row.price_secondary_int,
        LIVE_COMPACT_EVENT_SCHEMA_VERSION,
        row.sip_timestamp_us,
        row.size_primary,
        row.size_secondary,
        row.source_sequence,
        row.ticker,
    )
}

fn parse_historical_tsv_row(bytes: &[u8]) -> Result<HistoricalRow, String> {
    let text = std::str::from_utf8(bytes)
        .map_err(|error| format!("invalid historical stream UTF-8: {error}"))?;
    let fields = text.split('\t').collect::<Vec<_>>();
    if fields.len() != 18 {
        return Err(format!(
            "invalid historical stream column count: expected 18, found {}",
            fields.len()
        ));
    }
    Ok(HistoricalRow {
        ticker: fields[0].to_string(),
        ordinal: parse_tsv_field(&fields, 1, "ordinal")?,
        source_sequence: parse_tsv_field(&fields, 2, "source_sequence")?,
        event_meta: parse_tsv_field(&fields, 3, "event_meta")?,
        execution_timestamp_us: parse_tsv_field(&fields, 4, "execution_timestamp_us")?,
        sip_timestamp_us: parse_tsv_field(&fields, 5, "sip_timestamp_us")?,
        price_primary_int: parse_tsv_field(&fields, 6, "price_primary_int")?,
        price_secondary_int: parse_tsv_field(&fields, 7, "price_secondary_int")?,
        size_primary: parse_tsv_field(&fields, 8, "size_primary")?,
        size_secondary: parse_tsv_field(&fields, 9, "size_secondary")?,
        exchange_primary: parse_tsv_field(&fields, 10, "exchange_primary")?,
        exchange_secondary: parse_tsv_field(&fields, 11, "exchange_secondary")?,
        condition_token_1: parse_tsv_field(&fields, 12, "condition_token_1")?,
        condition_token_2: parse_tsv_field(&fields, 13, "condition_token_2")?,
        condition_token_3: parse_tsv_field(&fields, 14, "condition_token_3")?,
        condition_token_4: parse_tsv_field(&fields, 15, "condition_token_4")?,
        condition_token_5: parse_tsv_field(&fields, 16, "condition_token_5")?,
        event_date: fields[17].to_string(),
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

fn structure_campaign_tickers_sql(
    archive_database: &str,
    daily_table: &str,
    reference_database: &str,
    start_date: NaiveDate,
    priority_start_date: NaiveDate,
    priority_end_date: NaiveDate,
    as_of: DateTime<Utc>,
    use_daily_liquidity: bool,
) -> Result<String, String> {
    for (name, value) in [
        ("archive database", archive_database),
        ("daily table", daily_table),
        ("reference database", reference_database),
    ] {
        if value.is_empty()
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return Err(format!(
                "structure campaign {name} is not a valid identifier"
            ));
        }
    }
    let priority_end_exclusive = priority_end_date
        .succ_opt()
        .ok_or_else(|| "structure campaign priority end date overflow".to_string())?;
    let liquidity_source = if use_daily_liquidity {
        format!(
            r#"SELECT
                upper(if(empty(ifNull(canonical_ticker, '')), source_ticker, canonical_ticker)) AS ticker,
                sumIf(trade_close * trade_size_sum, trade_present = 1) AS priority_dollar_volume
            FROM `{archive_database}`.`{daily_table}` FINAL
            PREWHERE session_date >= toDate('{priority_start_date}')
              AND session_date < toDate('{priority_end_exclusive}')
            WHERE adjusted = 0
              AND identity_status != 'ambiguous_source_ticker'
              AND available_at_us <= toUInt64({available_at_us})
            GROUP BY ticker"#,
            available_at_us = as_of.timestamp_micros().max(0),
        )
    } else {
        structure_campaign_raw_liquidity_sql(
            archive_database,
            priority_start_date,
            priority_end_date,
        )?
    };
    Ok(format!(
        r#"SELECT
            universe.ticker AS ticker,
            max(universe.currently_active) AS currently_active,
            ifNull(max(liquidity.priority_dollar_volume), 0.0) AS priority_dollar_volume
        FROM
        (
            SELECT upper(ticker) AS ticker, toUInt8(1) AS currently_active
            FROM `{reference_database}`.`feature_tradable_universe_v1` FINAL
            WHERE universe_date = (
                SELECT max(universe_date)
                FROM `{reference_database}`.`feature_tradable_universe_v1` FINAL
                WHERE universe_date <= toDate('{as_of_date}')
            )
              AND is_tradable = 1

            UNION ALL

            SELECT upper(ticker) AS ticker, toUInt8(0) AS currently_active
            FROM `{archive_database}`.`events_ordinal_continuity`
            PREWHERE source_date = toDate('{start_date}')
            GROUP BY ticker
            HAVING argMax(event_count, tuple(build_step, updated_at)) > 0
        ) AS universe
        LEFT JOIN
        (
            {liquidity_source}
        ) AS liquidity USING ticker
        GROUP BY universe.ticker
        HAVING match(universe.ticker, '^[A-Z0-9._-]{{1,32}}$')
        ORDER BY currently_active DESC, priority_dollar_volume DESC, ticker
        FORMAT JSONEachRow"#,
        as_of_date = as_of.date_naive(),
    ))
}

fn structure_campaign_raw_liquidity_sql(
    archive_database: &str,
    start_date: NaiveDate,
    end_date: NaiveDate,
) -> Result<String, String> {
    let mut selects = Vec::new();
    for year in start_date.year()..=end_date.year() {
        let left = if year == start_date.year() {
            start_date
        } else {
            NaiveDate::from_ymd_opt(year, 1, 1)
                .ok_or_else(|| "invalid liquidity year boundary".to_string())?
        };
        let right = if year == end_date.year() {
            end_date
        } else {
            NaiveDate::from_ymd_opt(year, 12, 31)
                .ok_or_else(|| "invalid liquidity year boundary".to_string())?
        };
        selects.push(format!(
            r#"SELECT ticker, event_meta, price_primary_int, size_primary
               FROM `{archive_database}`.`events_{year}`
               PREWHERE event_date >= toDate('{left}')
                 AND event_date <= toDate('{right}')
                 AND bitAnd(event_meta, 1) = 1"#
        ));
    }
    Ok(format!(
        r#"SELECT
            upper(ticker) AS ticker,
            sum(
                (toFloat64(price_primary_int) /
                    if(bitAnd(event_meta, 2) != 0, 10000.0, 100.0)) *
                toFloat64(size_primary)
            ) AS priority_dollar_volume
        FROM ({})
        GROUP BY ticker"#,
        selects.join(" UNION ALL ")
    ))
}

fn structure_campaign_liquidity_coverage_sql(
    archive_database: &str,
    daily_table: &str,
    start_date: NaiveDate,
    end_date: NaiveDate,
    as_of: DateTime<Utc>,
) -> Result<String, String> {
    for (name, value) in [
        ("archive database", archive_database),
        ("daily table", daily_table),
    ] {
        if value.is_empty()
            || !value
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return Err(format!(
                "structure campaign {name} is not a valid identifier"
            ));
        }
    }
    let end_exclusive = end_date
        .succ_opt()
        .ok_or_else(|| "structure campaign liquidity end date overflow".to_string())?;
    Ok(format!(
        "SELECT count() FROM `{archive_database}`.`{daily_table}` FINAL \
         PREWHERE session_date >= toDate('{start_date}') \
           AND session_date < toDate('{end_exclusive}') \
         WHERE adjusted = 0 \
           AND identity_status != 'ambiguous_source_ticker' \
           AND available_at_us <= toUInt64({available_at_us}) \
         FORMAT TSV",
        available_at_us = as_of.timestamp_micros().max(0),
    ))
}

fn structure_campaign_continuity_sql(
    archive_database: &str,
    ticker: &str,
    start_date: NaiveDate,
    end_date: NaiveDate,
) -> String {
    format!(
        r#"SELECT
            toString(source_date) AS source_date,
            argMax(event_count, tuple(build_step, updated_at)) AS event_count,
            argMax(next_ordinal, tuple(build_step, updated_at)) AS next_ordinal,
            argMax(last_ordinal, tuple(build_step, updated_at)) AS last_ordinal,
            argMax(first_sip_timestamp_us, tuple(build_step, updated_at)) AS first_sip_timestamp_us,
            argMax(last_sip_timestamp_us, tuple(build_step, updated_at)) AS last_sip_timestamp_us,
            argMax(build_step, tuple(build_step, updated_at)) AS max_build_step,
            toString(max(updated_at)) AS max_updated_at
        FROM `{archive_database}`.`events_ordinal_continuity` AS source
        PREWHERE source.ticker = {ticker}
          AND source.source_date >= toDate({start_date})
          AND source.source_date <= toDate({end_date})
        GROUP BY source.source_date
        ORDER BY source.source_date
        FORMAT JSONEachRow"#,
        ticker = sql_literal(ticker),
        start_date = sql_literal(&start_date.to_string()),
        end_date = sql_literal(&end_date.to_string()),
    )
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

fn persisted_structure_events_sql(
    table: &str,
    ticker: &str,
    before: DateTime<Utc>,
    lookback_days: usize,
    event_limit: usize,
) -> String {
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
              AND event_date >= toDate(parseDateTime64BestEffort({before}) - INTERVAL {lookback_days} DAY)
              AND confirmed_at >= parseDateTime64BestEffort({before}) - INTERVAL {lookback_days} DAY
            ORDER BY confirmed_at ASC, event_id ASC
            LIMIT {event_limit}
            FORMAT JSONEachRow"#,
        version = GENERIC_STRUCTURE_ALGORITHM_VERSION,
        ticker = sql_literal(ticker),
        before = sql_literal(&before.to_rfc3339()),
        lookback_days = lookback_days,
        event_limit = event_limit,
    )
}

fn completed_session_dates_between_sql(
    database: &str,
    start_date: NaiveDate,
    end_date: NaiveDate,
    as_of: DateTime<Utc>,
) -> String {
    let table = format!("`{database}`.`events_ordinal_continuity`");
    format!(
        r#"SELECT toString(source_date) AS session_date
            FROM (
                SELECT
                    source.ticker AS ticker,
                    source.source_date AS source_date,
                    argMax(source.event_count, tuple(source.build_step, source.updated_at)) AS event_count
                FROM {table} AS source
                PREWHERE source.source_date >= toDate('{start_date}')
                  AND source.source_date <= toDate('{end_date}')
                WHERE source.updated_at <= parseDateTime64BestEffort('{as_of}')
                GROUP BY source.ticker, source.source_date
            )
            GROUP BY source_date
            HAVING sum(event_count) > 0
            ORDER BY source_date
            FORMAT JSONEachRow"#,
        as_of = as_of.to_rfc3339(),
    )
}

fn structure_split_adjustments_sql(
    ticker: &str,
    after: DateTime<Utc>,
    through: DateTime<Utc>,
) -> String {
    let start_date = after.with_timezone(&New_York).date_naive();
    let end_date = through.with_timezone(&New_York).date_naive();
    format!(
        r#"SELECT
            toString(source.execution_date) AS execution_date,
            argMax(source.split_from, source.inserted_at) AS split_from,
            argMax(source.split_to, source.inserted_at) AS split_to,
            formatDateTime(max(source.inserted_at), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS source_inserted_at
        FROM q_live.market_stock_split_v1 AS source FINAL
        WHERE upper(source.provider_ticker) = {ticker}
          AND toDate(source.execution_date) >= toDate({start_date})
          AND toDate(source.execution_date) <= toDate({end_date})
          AND source.split_from > 0
          AND source.split_to > 0
        GROUP BY source.execution_date
        ORDER BY source.execution_date
        FORMAT JSONEachRow"#,
        ticker = sql_literal(ticker),
        start_date = sql_literal(&start_date.to_string()),
        end_date = sql_literal(&end_date.to_string()),
    )
}

fn structure_split_revision_sql(
    ticker_filter: &str,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> String {
    let start_date = start.with_timezone(&New_York).date_naive();
    let end_date = end.with_timezone(&New_York).date_naive();
    format!(
        r#"SELECT
            upper(source.provider_ticker) AS ticker,
            toString(source.execution_date) AS execution_date,
            argMax(source.split_from, source.inserted_at) AS split_from,
            argMax(source.split_to, source.inserted_at) AS split_to,
            formatDateTime(max(source.inserted_at), '%Y-%m-%dT%H:%i:%S.%fZ', 'UTC') AS source_inserted_at
        FROM q_live.market_stock_split_v1 AS source FINAL
        WHERE upper(source.provider_ticker) IN ({ticker_filter})
          AND toDate(source.execution_date) >= toDate({start_date})
          AND toDate(source.execution_date) <= toDate({end_date})
          AND source.split_from > 0
          AND source.split_to > 0
        GROUP BY ticker, source.execution_date
        ORDER BY ticker, source.execution_date
        FORMAT JSONEachRow"#,
        start_date = sql_literal(&start_date.to_string()),
        end_date = sql_literal(&end_date.to_string()),
    )
}

fn split_revision_token<'a>(
    rows: impl Iterator<Item = &'a StructureSplitRevisionRow>,
) -> Result<String, String> {
    let mut identities = rows
        .map(|row| {
            let inserted_at = DateTime::parse_from_rfc3339(&row.source_inserted_at)
                .map_err(|error| format!("invalid stock split inserted_at: {error}"))?;
            Ok(format!(
                "{}|{}|{:016x}|{:016x}|{}",
                row.ticker.to_ascii_uppercase(),
                row.execution_date,
                row.split_from.to_bits(),
                row.split_to.to_bits(),
                inserted_at.timestamp_micros(),
            ))
        })
        .collect::<Result<Vec<_>, String>>()?;
    identities.sort();
    let mut hasher = Sha256::new();
    for identity in identities {
        hasher.update(identity.as_bytes());
        hasher.update([b'\n']);
    }
    Ok(format!("split-sha256:{:x}", hasher.finalize()))
}

#[cfg(test)]
mod tests {
    use super::{
        adaptive_structure_chunk_minutes, append_scheduled_gap_segments, archive_session_end_utc,
        build_source_plan, completed_session_dates_between_sql, coverage_precedes, event_select,
        first_json_difference_path, indicator_warmup_ordinal_sessions_sql,
        latest_coverage_summary_sql, latest_coverage_target_date_sql, macro_bar_is_closed,
        materialize_confirmed_recent_coverage, merge_coverage_intervals, merge_daily_chart_bars,
        normalize_ticker, ordinal_event_select, parse_historical_tsv_row,
        persisted_structure_events_sql, recent_coverage_sql, recent_daily_trade_bars_sql,
        row_to_event, source_revision_uses_historical_structure_policy_v1,
        split_adjustment_factors, structure_campaign_continuity_sql,
        structure_campaign_tickers_sql, structure_split_adjustments_sql,
        structure_split_revision_sql, ticker_filter, CoverageInterval, EventWindow,
        HistoricalMacroChartRow, HistoricalRow, LatestEventCoverage, MarketSourceTier,
        RecentCoverageRow,
    };
    use crate::config::HistoricalGatewayConfig;
    use chrono::{NaiveDate, TimeZone, Utc};
    use chrono_tz::America::New_York;
    use qmd_core::compact_event::{CompactEventDecoder, LIVE_COMPACT_EVENT_SCHEMA_VERSION};
    use qmd_core::event::MarketEvent;
    use qmd_core::generic_structure::StructureSplitAdjustment;

    #[test]
    fn persisted_structure_source_revision_requires_historical_structure_contract() {
        assert!(source_revision_uses_historical_structure_policy_v1(
            "1:2:0:updated:Archive:1:2:split-sha256:abc:structure-input-v1:archive-sip-condition:trade-condition-sha256:abc"
        ));
        assert!(!source_revision_uses_historical_structure_policy_v1(
            "1:2:0:updated:Archive:1:2:split-sha256:abc:execution-clock-v1:5:99:7:updated"
        ));
        assert!(!source_revision_uses_historical_structure_policy_v1(
            "1:2:0:updated:Archive:1:2:split-sha256:abc"
        ));
        assert!(!source_revision_uses_historical_structure_policy_v1(
            "execution-clock:not-applicable"
        ));
    }

    #[test]
    fn event_fetch_ticker_filter_targets_the_raw_sort_key() {
        let filter = ticker_filter(&["aapl".to_string()]).unwrap();
        assert_eq!(filter, " AND source.ticker IN ('AAPL')");
        let sql = event_select(
            "q_live.events",
            true,
            None,
            Utc.with_ymd_and_hms(2026, 8, 25, 8, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2026, 8, 25, 9, 0, 0).unwrap(),
            &filter,
            None,
        );
        assert!(sql.contains("AND source.ticker IN ('AAPL')\n        WHERE 1"));
        assert!(sql.contains("source.execution_timestamp_us AS execution_timestamp_us"));
        assert!(!sql.contains("WHERE 1 AND source.ticker IN ('AAPL')"));
    }

    #[test]
    fn indicator_warmup_uses_reverse_ticker_ordinal_ranges() {
        let sql = indicator_warmup_ordinal_sessions_sql(
            "market_sip_compact",
            "q_live",
            "historical_event_execution_clock_coverage_v1",
            "SUGP",
            NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
            260,
        )
        .unwrap();
        assert!(sql.contains("events_ordinal_continuity"));
        assert!(sql.contains("PREWHERE source.ticker = 'SUGP'"));
        assert!(sql.contains("source.source_date < toDate('2026-08-21')"));
        assert!(sql.contains("ORDER BY source_date DESC"));
        assert!(sql.contains("LIMIT 260"));
    }

    #[test]
    fn archive_event_fetch_joins_the_versioned_execution_clock_sidecar() {
        let sql = event_select(
            "market_sip_compact.events_2025",
            false,
            Some("q_live.historical_event_execution_clock_v1"),
            Utc.with_ymd_and_hms(2025, 1, 2, 9, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2025, 1, 2, 10, 0, 0).unwrap(),
            " AND source.ticker IN ('AAPL')",
            None,
        );

        assert!(sql.contains("execution_clock.execution_timestamp_us"));
        assert!(sql.contains("FROM q_live.historical_event_execution_clock_v1 FINAL"));
        assert!(sql.contains("AND ticker IN ('AAPL')"));
        assert!(!sql.contains("source.execution_timestamp_us"));
    }

    #[test]
    fn historical_structure_event_fetch_does_not_reference_execution_clock() {
        let sql = event_select(
            "market_sip_compact.events_2026",
            false,
            None,
            Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2026, 8, 21, 9, 0, 0).unwrap(),
            " AND source.ticker IN ('SUGP')",
            None,
        );

        assert!(sql.contains("toUInt64(0) AS execution_timestamp_us"));
        assert!(!sql.contains("execution_clock."));
        assert!(!sql.contains("historical_event_execution_clock_v1"));
    }

    #[test]
    fn archive_execution_clock_subquery_does_not_capture_event_kind_predicates() {
        let sql = event_select(
            "market_sip_compact.events_2026",
            false,
            Some("q_live.historical_event_execution_clock_v1"),
            Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2026, 8, 21, 8, 1, 0).unwrap(),
            " AND source.ticker IN ('SUGP') AND bitAnd(source.event_meta, 1) = toUInt8(1)",
            None,
        );

        let clock_subquery = sql
            .split("LEFT JOIN (")
            .nth(1)
            .unwrap()
            .split(") AS execution_clock")
            .next()
            .unwrap();
        assert!(clock_subquery.contains("ticker IN ('SUGP')"));
        assert!(!clock_subquery.contains("source.event_meta"));
    }

    #[test]
    fn campaign_universe_prioritizes_current_tradable_symbols_by_bounded_liquidity() {
        let sql = structure_campaign_tickers_sql(
            "market_sip_compact",
            "daily_session_bars_by_symbol_time_v1",
            "q_live",
            NaiveDate::from_ymd_opt(2026, 1, 2).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 1).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
            Utc.with_ymd_and_hms(2026, 9, 1, 0, 0, 0).unwrap(),
            true,
        )
        .unwrap();

        assert!(sql.contains("feature_tradable_universe_v1"));
        assert!(sql.contains("is_tradable = 1"));
        assert!(!sql.contains("currency_code = 'USD'"));
        assert!(sql.contains("HAVING match(universe.ticker"));
        assert!(sql.contains("events_ordinal_continuity"));
        assert!(sql.contains("session_date >= toDate('2026-08-01')"));
        assert!(sql.contains("session_date < toDate('2026-09-01')"));
        assert!(sql.contains("ORDER BY currently_active DESC, priority_dollar_volume DESC, ticker"));

        let raw_sql = structure_campaign_tickers_sql(
            "market_sip_compact",
            "daily_session_bars_by_symbol_time_v1",
            "q_live",
            NaiveDate::from_ymd_opt(2026, 1, 2).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 1).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
            Utc.with_ymd_and_hms(2026, 9, 1, 0, 0, 0).unwrap(),
            false,
        )
        .unwrap();
        assert!(raw_sql.contains("events_2026"));
        assert!(raw_sql.contains("bitAnd(event_meta, 1) = 1"));
        assert!(raw_sql.contains("toFloat64(price_primary_int)"));
    }

    #[test]
    fn completed_session_query_uses_qualified_continuity_columns() {
        let sql = completed_session_dates_between_sql(
            "market_sip_compact",
            NaiveDate::from_ymd_opt(2026, 8, 20).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
            Utc.with_ymd_and_hms(2026, 8, 22, 0, 0, 0).unwrap(),
        );

        assert!(sql.contains("`market_sip_compact`.`events_ordinal_continuity` AS source"));
        assert!(sql.contains("source.source_date >= toDate('2026-08-20')"));
        assert!(sql.contains("source.source_date <= toDate('2026-08-21')"));
        assert!(sql.contains("source.updated_at <= parseDateTime64BestEffort"));
        assert!(sql.contains("GROUP BY source.ticker, source.source_date"));
    }

    #[test]
    fn structure_revision_uses_latest_split_terms_for_the_execution_horizon() {
        let sql = structure_split_revision_sql(
            "'AAPL','SUGP'",
            Utc.with_ymd_and_hms(2026, 2, 20, 9, 0, 0).unwrap(),
            Utc.with_ymd_and_hms(2026, 8, 22, 0, 0, 0).unwrap(),
        );
        assert!(sql.contains("upper(source.provider_ticker) IN ('AAPL','SUGP')"));
        assert!(sql.contains("argMax(source.split_from, source.inserted_at)"));
        assert!(!sql.contains("source.inserted_at <= parseDateTime64BestEffort"));
        assert!(sql.contains("ORDER BY ticker, source.execution_date"));
    }

    #[test]
    fn campaign_event_fetch_uses_the_physical_ticker_ordinal_key() {
        let sql = ordinal_event_select("market_sip_compact.events_2026", "SUGP", 10, 20);
        assert!(sql.contains("source.ticker = 'SUGP'"));
        assert!(sql.contains("source.ordinal >= 10"));
        assert!(sql.contains("source.ordinal < 20"));
        assert!(!sql.contains("source.sip_timestamp_us >="));
        assert!(!sql.contains("source.event_date >="));
        assert!(sql.contains("toUInt64(0) AS execution_timestamp_us"));
        assert!(!sql.contains("historical_event_execution_clock_v1"));
        assert!(!sql.contains("source.execution_timestamp_us"));
    }

    #[test]
    fn campaign_continuity_plan_pins_exact_daily_ordinal_bounds() {
        let sql = structure_campaign_continuity_sql(
            "market_sip_compact",
            "SUGP",
            NaiveDate::from_ymd_opt(2026, 8, 20).unwrap(),
            NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
        );
        assert!(sql.contains("PREWHERE source.ticker = 'SUGP'"));
        assert!(sql.contains("source.source_date >= toDate('2026-08-20')"));
        assert!(!sql.contains("PREWHERE ticker ="));
        assert!(sql.contains("argMax(next_ordinal"));
        assert!(sql.contains("argMax(last_ordinal"));
        assert!(sql.contains("GROUP BY source.source_date"));
    }

    #[test]
    fn sparse_structure_rebuilds_use_long_bounded_source_windows() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 2, 20, 0, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 8, 20, 0, 0, 0).unwrap(),
            tickers: vec!["SUGP".to_string()],
        };

        assert_eq!(
            adaptive_structure_chunk_minutes(&window, 50_000, 240),
            10_080
        );
        let million_event_chunk = adaptive_structure_chunk_minutes(&window, 1_000_000, 240);
        assert_eq!(million_event_chunk, 10_080);
    }

    #[test]
    fn dense_structure_rebuilds_keep_streaming_windows_bounded() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 2, 20, 0, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 8, 20, 0, 0, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };

        let dense_chunk = adaptive_structure_chunk_minutes(&window, 100_000_000, 240);
        assert_eq!(dense_chunk, 240);
    }

    #[test]
    fn latest_coverage_queries_bound_memory_and_aggregate_only_the_target_session() {
        let before = chrono::NaiveDate::from_ymd_opt(2026, 8, 21).unwrap();
        let target = latest_coverage_target_date_sql(
            "market_sip_compact.events_ordinal_continuity",
            Some(before),
        );
        assert!(target.contains("maxOrNull(source_date)"));
        assert!(target.contains("source_date < toDate('2026-08-21')"));
        assert!(target.contains("max_threads = 2"));
        assert!(target.contains("max_memory_usage = 536870912"));
        assert!(!target.contains("GROUP BY ticker"));

        let summary =
            latest_coverage_summary_sql("market_sip_compact.events_ordinal_continuity", before);
        assert!(summary.contains("source_date = toDate('2026-08-21')"));
        assert!(summary.contains("GROUP BY ticker"));
        assert!(!summary.contains("GROUP BY ticker, source_date"));
        assert!(summary.contains("max_execution_time = 15"));
    }

    #[test]
    fn unrestricted_coverage_can_satisfy_a_later_bounded_preflight() {
        let coverage = LatestEventCoverage {
            coverage_table: "market.events".to_string(),
            event_count: 42,
            session_date: Some("2026-08-21".to_string()),
            ticker_count: 3,
        };

        assert!(coverage_precedes(
            &coverage,
            NaiveDate::from_ymd_opt(2026, 8, 22).unwrap(),
        ));
        assert!(!coverage_precedes(
            &coverage,
            NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
        ));
    }

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
            execution_timestamp_us: 0,
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
                assert_eq!(quote.raw["execution_timestamp_us"], 0);
                assert_eq!(quote.raw["correlation_id"], "source:AAPL:2026-07-13");
                assert_eq!(quote.raw["causation_id"], compact.causation_id());
            }
            MarketEvent::Trade(_) => panic!("expected quote"),
        }
    }

    #[test]
    fn ordered_stream_tsv_parser_preserves_the_compact_wire_columns() {
        let row = parse_historical_tsv_row(
            b"AAPL\t42\t9001\t6\t0\t1752415200000000\t1001234\t1001200\t20\t25\t11\t12\t3\t4\t5\t0\t0\t2026-07-13",
        )
        .unwrap();

        assert_eq!(row.ticker, "AAPL");
        assert_eq!(row.ordinal, 42);
        assert_eq!(row.source_sequence, 9001);
        assert_eq!(row.event_meta, 6);
        assert_eq!(row.execution_timestamp_us, 0);
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
    fn recent_daily_bars_are_causal_complete_trade_aggregates() {
        let sql = recent_daily_trade_bars_sql(
            "q_live.intraday_family_bars_v3",
            "SUGP",
            NaiveDate::from_ymd_opt(2026, 8, 1).unwrap(),
            NaiveDate::from_ymd_opt(2026, 9, 3).unwrap(),
            Utc.with_ymd_and_hms(2026, 9, 2, 18, 0, 0).unwrap(),
        );

        assert!(sql.contains("FROM q_live.intraday_family_bars_v3 FINAL"));
        assert!(sql.contains("ticker = 'SUGP'"));
        assert!(sql.contains("label_resolution_us = 3600000000"));
        assert!(sql.contains("calculation_revision = 'qmd-family-bars-v3'"));
        assert!(sql.contains("complete = 1"));
        assert!(sql.contains("updated_at_utc <= parseDateTime64BestEffort"));
        assert!(sql.contains("AND close > 0"));
    }

    #[test]
    fn finalized_daily_archive_rows_override_recent_overlap() {
        let make_bar = |session_date: &str, close: f64| HistoricalMacroChartRow {
            bar_end: Utc.with_ymd_and_hms(2026, 8, 18, 0, 0, 0).unwrap(),
            bar_family: "trade".to_string(),
            bar_start: Utc.with_ymd_and_hms(2026, 8, 18, 0, 0, 0).unwrap(),
            close,
            event_count: 1,
            high: close,
            is_closed: true,
            low: close,
            open: close,
            session_date: session_date.to_string(),
            size_sum: 1.0,
            ticker: "SUGP".to_string(),
            timeframe: "1d".to_string(),
        };
        let mut archive = vec![make_bar("2026-08-18", 2.25)];

        merge_daily_chart_bars(
            &mut archive,
            vec![make_bar("2026-08-18", 2.10), make_bar("2026-08-20", 3.50)],
        );

        assert_eq!(archive.len(), 2);
        assert_eq!(archive[0].session_date, "2026-08-18");
        assert_eq!(archive[0].close, 2.25);
        assert_eq!(archive[1].session_date, "2026-08-20");
        assert_eq!(archive[1].close, 3.50);
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
        let sql = persisted_structure_events_sql(
            "q_derived.generic_structure_events_v1",
            "AAPL",
            before,
            180,
            2_000_001,
        );
        assert!(sql.contains("AS pivot_at_text"));
        assert!(sql.contains("AS confirmed_at_text"));
        assert!(!sql.contains("AS pivot_at,"));
        assert!(!sql.contains("AS confirmed_at\n"));
        assert!(sql.contains("AND confirmed_at < parseDateTime64BestEffort"));
        assert!(sql.contains("AND confirmed_at >= parseDateTime64BestEffort"));
        assert!(sql.contains("AND event_date >= toDate("));
        assert!(sql.contains("INTERVAL 180 DAY"));
        assert!(sql.contains("ORDER BY confirmed_at ASC, event_id ASC"));
        assert!(sql.contains("LIMIT 2000001"));
        assert!(!sql.contains("ORDER BY confirmed_at DESC"));
    }

    #[test]
    fn checkpoint_schema_drift_reports_the_first_causal_path() {
        let stored = serde_json::json!({
            "algorithm_version": 16,
            "levels": [{"price": 3.45, "touch_count": 2}],
        });
        let decoded = serde_json::json!({
            "algorithm_version": 16,
            "levels": [{"price": 3.45, "touch_count": 0}],
        });

        assert_eq!(
            first_json_difference_path(&stored, &decoded, "$"),
            Some("$.levels[0].touch_count".to_string()),
        );
    }

    #[test]
    fn checkpoint_schema_drift_ignores_recomputable_hold_projections() {
        let stored = serde_json::json!({
            "algorithm_version": 16,
            "levels": [{
                "price": 3.45,
                "touch_count": 2,
                "hold_quality_score": 0.61,
                "hold_score_revision": "old"
            }],
        });
        let decoded = serde_json::json!({
            "algorithm_version": 16,
            "levels": [{
                "price": 3.45,
                "touch_count": 2,
                "hold_quality_score": 0.74,
                "hold_score_revision": "current"
            }],
        });
        let stored = qmd_core::structure_certification::checkpoint_certification_value(&stored);
        let decoded = qmd_core::structure_certification::checkpoint_certification_value(&decoded);

        assert_eq!(first_json_difference_path(&stored, &decoded, "$"), None);
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
            Some("q_live.historical_event_execution_clock_v1"),
            start,
            end,
            "",
            None,
        );
        assert!(archive.contains("source.ordinal AS source_sequence"));
        let recent = event_select("q_live.events", true, None, start, end, "", None);
        assert!(archive.contains("source.ordinal AS ordinal"));
        assert!(recent.contains("source.arrival_sequence AS ordinal"));
        assert!(recent.contains("source.source_sequence AS source_sequence"));
        assert!(recent.contains("FROM q_live.events AS source FINAL"));
    }

    #[test]
    fn retrospective_split_factors_normalize_price_and_share_units() {
        let effective_at = New_York
            .with_ymd_and_hms(2026, 8, 6, 4, 0, 0)
            .single()
            .unwrap()
            .with_timezone(&Utc);
        let adjustments = vec![StructureSplitAdjustment {
            execution_date: chrono::NaiveDate::from_ymd_opt(2026, 8, 6).unwrap(),
            effective_at,
            split_from: 5.0,
            split_to: 1.0,
            source_inserted_at: effective_at,
        }];

        assert_eq!(
            split_adjustment_factors(
                effective_at - chrono::Duration::milliseconds(1),
                &adjustments
            ),
            (5.0, 0.2)
        );
        assert_eq!(
            split_adjustment_factors(effective_at, &adjustments),
            (1.0, 1.0)
        );
    }

    #[test]
    fn split_adjustment_query_uses_latest_canonical_terms_for_the_execution_horizon() {
        let after = Utc.with_ymd_and_hms(2026, 8, 1, 8, 0, 0).unwrap();
        let through = Utc.with_ymd_and_hms(2026, 8, 6, 14, 15, 0).unwrap();
        let sql = structure_split_adjustments_sql("SUGP", after, through);

        assert!(sql.contains("upper(source.provider_ticker) = 'SUGP'"));
        assert!(!sql.contains("source.inserted_at <= parseDateTime64BestEffort"));
        assert!(sql.contains("argMax(source.split_from, source.inserted_at)"));
        assert!(sql.contains("argMax(source.split_to, source.inserted_at)"));
    }
}
