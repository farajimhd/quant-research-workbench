use crate::config::HistoricalGatewayConfig;
use crate::source::{
    split_adjustment_factors, EventWindow, HistoricalCursor, HistoricalEventSource,
    PersistedStructureCheckpointSeed, SessionVwapSeed, SourceRevision,
};
use crate::structure_checkpoint::{
    persisted_structure_book_seed, rebuild_trade_structure_checkpoint,
    StructureCheckpointRebuildRequest, STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
};
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use qmd_core::bars::{BarRow, BarSnapshot, SharedBarStore, BAR_SCHEMA_VERSION};
use qmd_core::compact_event::LiveCompactEvent;
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEngine, GenericStructureEvent,
    StructureLevelCandidate, GENERIC_STRUCTURE_ALGORITHM_VERSION, STRUCTURE_HOLD_SCORE_REVISION,
};
use qmd_core::indicators::{
    BarIndicatorCalculator, IndicatorRow, MarketStructureReferenceLevels,
    MicrostructureSampleAggregate, INDICATOR_SCHEMA_VERSION,
};
use qmd_core::market_products::{
    parse_resolution_us, ConditionBarSnapshot, ConditionClassifier, FamilyBarRow,
    FamilyBarSnapshot, MacroBarSnapshot, MarketProductEngine, ProductCacheLimits, ProductState,
    MARKET_PRODUCT_SCHEMA_VERSION,
};
use qmd_core::market_signal::{MarketSignalEngine, MarketSignalEvent};
use qmd_core::microstructure_interval::MicrostructureIntervalWindow;
use qmd_core::structure_certification::checkpoint_sha256;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::fs;
use std::mem::size_of;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use tokio::sync::{broadcast, mpsc, Mutex, Notify, OnceCell, Semaphore};

pub const HISTORICAL_ENGINE_VERSION: &str = "qmd-derived-v35";
pub const HISTORICAL_CALCULATION_REVISION: &str = "qmd-derived-v58";
pub const HISTORICAL_CORPORATE_ACTION_REVISION: &str = "retrospective-split-adjusted-v2";
const MAX_ENCOUNTERED_STRUCTURE_LEVELS: usize = 4_000;
const PREPARED_BAR_CACHE_SCHEMA_VERSION: u16 = 11;
const PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION: u16 = 3;
const INDICATOR_WARMUP_CACHE_SCHEMA_VERSION: u16 = 1;
const INDICATOR_WARMUP_MAX_SESSIONS: usize = 260;
const INDICATOR_WARMUP_ORDINALS_PER_QUERY: u64 = 50_000;
// Prepared structure books have their own algorithm authority. Bar-indicator
// changes (for example MACD or VWAP warm-up fixes) must not invalidate and
// cold-rebuild the complete 180-day level book. v43 is the last legacy shared
// calculation revision whose structure payload already used algorithm v15;
// it is accepted once and migrated to the stable structure-specific identity.
const LEGACY_STRUCTURE_CALCULATION_REVISION: &str = "qmd-derived-v43";
const INDICATOR_EMA_WARMUP_DAYS: i64 = 7;
const INDICATOR_EMA_WARMUP_BARS: usize = 200;

#[derive(Clone, Debug, Eq, PartialEq)]
enum CacheProfile {
    Bars(String),
    Derived(String),
    Structure(String),
    Products,
}

impl CacheProfile {
    fn key(&self) -> String {
        match self {
            Self::Bars(timeframe) => format!("bars:{timeframe}"),
            Self::Derived(timeframe) => format!("derived:{timeframe}"),
            Self::Structure(timeframe) => format!("structure:{timeframe}"),
            Self::Products => "products".to_string(),
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct DerivedUpdate {
    pub as_of: DateTime<Utc>,
    pub bar: BarRow,
    pub indicator: IndicatorRow,
    pub sequence: u64,
    #[serde(rename = "type")]
    pub update_type: &'static str,
}

#[derive(Clone, Debug, Serialize)]
pub struct BarUpdate {
    pub bar: BarRow,
    pub sequence: u64,
}

#[derive(Clone, Debug, Serialize)]
pub struct DerivedSnapshot {
    #[serde(flatten)]
    pub bars: BarSnapshot,
    pub cache: CacheEvidence,
    pub indicators: Vec<IndicatorRow>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CacheEvidence {
    pub calculation_revision: &'static str,
    pub corporate_action_revision: &'static str,
    pub engine_version: &'static str,
    pub event_count: u64,
    pub hit: bool,
    pub source_revision: SourceRevision,
}

#[derive(Clone, Debug, Serialize)]
pub struct CacheMetrics {
    pub active_builds: usize,
    pub builds: u64,
    pub estimated_bytes: u64,
    pub entries: usize,
    pub evictions: u64,
    pub hits: u64,
    pub misses: u64,
    pub max_bytes: usize,
    pub prepared_bar_hits: u64,
    pub prepared_bar_misses: u64,
    pub prepared_bar_writes: u64,
    pub requirements: Vec<HistoricalComputationRequirement>,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalComputationRequirement {
    pub schema_version: u16,
    pub requirement_id: String,
    pub scope: String,
    pub product: String,
    pub ticker: String,
    pub timeframe: Option<String>,
    pub parameter_hash: String,
    pub calculation_revision: String,
    pub corporate_action_revision: String,
    pub anchor_start: DateTime<Utc>,
    pub anchor_end: DateTime<Utc>,
    pub source_revision: String,
    pub source_plan_hash: String,
    pub state: String,
    pub event_count: u64,
    pub estimated_bytes: u64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct ChartBarRow {
    pub schema_version: u16,
    pub session_date: String,
    pub timeframe: String,
    pub sym: String,
    pub bar_start: DateTime<Utc>,
    pub bar_end: DateTime<Utc>,
    pub is_closed: bool,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub dollar_volume: Option<f64>,
    pub trade_count: Option<u64>,
    pub spread_bps_close: Option<f64>,
    pub spread_bps_mean: Option<f64>,
    pub vwap: Option<f64>,
    pub estimated_luld_active: bool,
    pub estimated_luld_reference_price: f64,
    pub estimated_luld_lower_price: f64,
    pub estimated_luld_upper_price: f64,
    pub estimated_luld_distance_to_upper_pct: f64,
    pub estimated_luld_distance_to_lower_pct: f64,
    pub estimated_luld_state: String,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct PreparedBarCacheArtifact {
    schema_version: u16,
    key: String,
    event_count: u64,
    bars: Vec<ChartBarRow>,
    bar_indicator_projection: Vec<Value>,
    structure_projection: Vec<Value>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct PreparedStructureSeedCacheArtifact {
    schema_version: u16,
    key: String,
    ticker: String,
    checkpoint: GenericStructureCheckpoint,
}

#[derive(Clone, Debug, Default)]
struct IndicatorPageWarmup {
    ema_closes: Vec<f64>,
    session_vwap_seed: SessionVwapSeed,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct IndicatorWarmupBar {
    pub bar_start: DateTime<Utc>,
    pub close: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct IndicatorWarmupArtifact {
    pub schema_version: u16,
    pub calculation_revision: String,
    pub corporate_action_revision: String,
    pub ticker: String,
    pub timeframe: String,
    pub session_start: DateTime<Utc>,
    pub authority_start: DateTime<Utc>,
    pub required_bars: usize,
    pub bars: Vec<IndicatorWarmupBar>,
    pub fetched_events: u64,
    pub fetched_ordinal_ranges: u64,
    pub source_revision: SourceRevision,
    pub status: String,
    pub cache_hit: bool,
}

#[derive(Clone, Debug, Default)]
struct ExactIndicatorPrefix {
    closes: VecDeque<f64>,
    session_vwap_seed: SessionVwapSeed,
}

fn bar_indicator_projection(
    selected: &[&BarUpdate],
    warmup_closes: &[f64],
    page_start: DateTime<Utc>,
    session_vwap_seed: SessionVwapSeed,
) -> Result<Vec<Value>, String> {
    let mut calculator = BarIndicatorCalculator::new();
    calculator.seed_ema_close_history(warmup_closes.iter().copied());
    calculator.seed_session_vwap(
        page_start,
        session_vwap_seed.cumulative_volume,
        session_vwap_seed.cumulative_trade_notional,
        session_vwap_seed.cumulative_execution_volume,
        session_vwap_seed.cumulative_execution_trade_notional,
    )?;
    selected
        .iter()
        .map(|update| {
            serde_json::to_value(calculator.apply_bar_for_historical_cache(&update.bar))
                .map_err(|error| format!("failed to serialize bar indicator row: {error}"))
        })
        .collect()
}

#[derive(Clone, Debug, Serialize)]
pub struct ChartSnapshot {
    pub as_of: DateTime<Utc>,
    pub bars: Vec<ChartBarRow>,
    pub cache: CacheEvidence,
    pub has_more: bool,
    pub indicators: Vec<IndicatorRow>,
    #[serde(skip)]
    pub indicator_projection: Option<Vec<Value>>,
    pub indicators_available: bool,
    pub market_signal_events: Vec<MarketSignalEvent>,
    pub next_before: Option<DateTime<Utc>>,
    pub structure_events: Vec<GenericStructureEvent>,
    pub structure_level_history: Vec<StructureLevelCandidate>,
    pub ticker: String,
    pub timeframe: String,
}

fn encountered_structure_levels(indicators: &[IndicatorRow]) -> Vec<StructureLevelCandidate> {
    let Some(session_date) = indicators
        .last()
        .map(|indicator| indicator.session_date.as_str())
    else {
        return Vec::new();
    };
    encountered_structure_levels_for_session(
        session_date,
        indicators
            .iter()
            .flat_map(|indicator| indicator.qmd_structure_active_levels.iter().cloned()),
    )
}

fn unified_structure_projection(selected: &[&BarUpdate]) -> Result<Vec<Value>, String> {
    let terminal_index = selected.len().saturating_sub(1);
    let mut previous = BTreeMap::<String, (Value, Value)>::new();
    selected
        .iter()
        .enumerate()
        .map(|(index, update)| {
            let mut levels = serde_json::to_value(&update.bar.qmd_structure.unified_levels)
                .map_err(|error| format!("failed to serialize unified structure levels: {error}"))?;
            if let Some(rows) = levels.as_array_mut() {
                for level in rows {
                    if let Some(object) = level.as_object_mut() {
                        // Source pivots are audit detail, not chart geometry. Keep
                        // the array contract while avoiding repeated nested books
                        // in a presentation-only projection.
                        object.insert("sources".to_string(), json!([]));
                    }
                }
            }
            let current = unified_structure_level_map(&levels);
            let mut row = json!({"bar_start": update.bar.bar_start});
            let object = row
                .as_object_mut()
                .ok_or_else(|| "invalid unified structure projection row".to_string())?;
            if index == 0 || index == terminal_index {
                object.insert("qmd_structure_unified_levels".to_string(), levels);
            } else {
                let upserts = current
                    .iter()
                    .filter_map(|(key, (signature, level))| {
                        (previous.get(key).map(|(value, _)| value) != Some(signature))
                            .then(|| level.clone())
                    })
                    .collect::<Vec<_>>();
                let removed = previous
                    .iter()
                    .filter_map(|(key, (_, level))| {
                        (!current.contains_key(key)).then(|| json!({
                            "unified_level_id": level.get("unified_level_id").cloned().unwrap_or(Value::Null),
                            "side": level.get("side").cloned().unwrap_or(Value::Null),
                        }))
                    })
                    .collect::<Vec<_>>();
                if !upserts.is_empty() || !removed.is_empty() {
                    object.insert(
                        "qmd_structure_unified_level_delta".to_string(),
                        json!({"upserts": upserts, "removed": removed}),
                    );
                }
            }
            previous = current;
            Ok(row)
        })
        .collect()
}

fn unified_structure_level_map(levels: &Value) -> BTreeMap<String, (Value, Value)> {
    levels
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|level| {
            let identity = format!("{}:{}", level.get("unified_level_id")?, level.get("side")?);
            let mut signature = level.clone();
            if let Some(object) = signature.as_object_mut() {
                for key in [
                    "total_volume",
                    "trade_count",
                    "sources",
                    "source_count",
                    "touch_count",
                    "hold_count",
                    "last_test_at_ms",
                ] {
                    object.remove(key);
                }
                for key in ["hold_probability"] {
                    if let Some(number) = object.get(key).and_then(Value::as_f64) {
                        object.insert(key.to_string(), json!((number * 20.0).round() / 20.0));
                    }
                }
            }
            Some((identity, (signature, level.clone())))
        })
        .collect()
}

fn encountered_structure_levels_for_session(
    session_date: &str,
    levels: impl IntoIterator<Item = StructureLevelCandidate>,
) -> Vec<StructureLevelCandidate> {
    bounded_encountered_structure_levels(
        levels
            .into_iter()
            .filter(|level| level.footprint_session_date == session_date),
    )
}

fn bounded_encountered_structure_levels(
    levels: impl IntoIterator<Item = StructureLevelCandidate>,
) -> Vec<StructureLevelCandidate> {
    let mut by_identity = BTreeMap::<(String, i64, i8, u64), StructureLevelCandidate>::new();
    for level in levels {
        by_identity.insert(
            (
                level.footprint_session_date.clone(),
                level.created_at_ms,
                level.side,
                level.price.to_bits(),
            ),
            level,
        );
    }
    let mut levels = by_identity.into_values().collect::<Vec<_>>();
    levels.sort_by_key(|level| level.created_at_ms);
    if levels.len() > MAX_ENCOUNTERED_STRUCTURE_LEVELS {
        levels.drain(..levels.len() - MAX_ENCOUNTERED_STRUCTURE_LEVELS);
    }
    levels
}

#[derive(Clone)]
pub struct HistoricalDerivedCache {
    allocated_bytes: Arc<AtomicU64>,
    config: HistoricalGatewayConfig,
    inner: Arc<Mutex<CacheIndex>>,
    source: HistoricalEventSource,
    stats: Arc<CacheStats>,
    structure_seeds: Arc<Mutex<HashMap<String, Arc<OnceCell<StructureSeedResult>>>>>,
    build_permits: Arc<Semaphore>,
    fetch_permits: Arc<Semaphore>,
}

type StructureSeedResult = Result<Option<GenericStructureCheckpoint>, String>;

pub struct CacheLease {
    pub entry: Arc<CacheEntry>,
    pub hit: bool,
    pub key: String,
    pub source_revision: SourceRevision,
}

pub struct CacheEntry {
    accounted: AtomicBool,
    accounting_lock: StdMutex<()>,
    allocated_bytes: Arc<AtomicU64>,
    complete: AtomicBool,
    frame_bytes: AtomicU64,
    global_max_bytes: u64,
    notify: Notify,
    state: Mutex<EntryState>,
    bar_updates: broadcast::Sender<BarUpdate>,
    updates: broadcast::Sender<DerivedUpdate>,
    estimated_bytes: AtomicU64,
    max_update_bytes: usize,
    max_updates: usize,
    product_bytes: AtomicU64,
    requirement: Option<HistoricalComputationRequirement>,
}

struct CacheIndex {
    entries: HashMap<String, Arc<CacheEntry>>,
    order: VecDeque<String>,
}

#[derive(Default)]
struct EntryState {
    bars_ready: bool,
    complete: bool,
    error: Option<String>,
    events_processed: u64,
    bars: Vec<BarUpdate>,
    market_signal_events: Vec<MarketSignalEvent>,
    structure_projection: Vec<Value>,
    structure_events: Vec<GenericStructureEvent>,
    frames: Vec<DerivedUpdate>,
    products: Option<MarketProductEngine>,
}

struct StructureProjectionBuilder {
    engine: GenericStructureEngine,
    current_bucket_start: Option<DateTime<Utc>>,
    previous: BTreeMap<String, (Value, Value)>,
    rows: Vec<Value>,
}

impl StructureProjectionBuilder {
    fn new(engine: GenericStructureEngine, start: DateTime<Utc>) -> Result<Self, String> {
        let levels = serialized_unified_structure_levels(&engine, start)?;
        let previous = unified_structure_level_map(&levels);
        Ok(Self {
            engine,
            current_bucket_start: None,
            previous,
            rows: vec![json!({
                "bar_start": structure_projection_bar_start(start)?,
                "qmd_structure_unified_levels": levels,
            })],
        })
    }

    fn apply_event(
        &mut self,
        event: &MarketEvent,
        trade_rule: qmd_core::bars::TradeUpdateRule,
    ) -> Result<(), String> {
        let bucket_start = structure_projection_bar_start(event.ts())?;
        if self
            .current_bucket_start
            .is_some_and(|current| current != bucket_start)
        {
            self.flush_transition(self.current_bucket_start.unwrap())?;
        }
        self.engine.apply_event_without_snapshot(event, trade_rule);
        self.current_bucket_start = Some(bucket_start);
        Ok(())
    }

    fn finish(mut self, terminal: DateTime<Utc>) -> Result<Vec<Value>, String> {
        if let Some(bucket_start) = self.current_bucket_start {
            self.flush_transition(bucket_start)?;
        }
        let levels = serialized_unified_structure_levels(&self.engine, terminal)?;
        let terminal_start = structure_projection_bar_start(terminal)?;
        let terminal_row = json!({
            "bar_start": terminal_start,
            "qmd_structure_unified_levels": levels,
        });
        if self.rows.last().and_then(projection_row_start) == Some(terminal_start) {
            self.rows.pop();
        }
        self.rows.push(terminal_row);
        Ok(self.rows)
    }

    fn flush_transition(&mut self, bar_start: DateTime<Utc>) -> Result<(), String> {
        let levels = serialized_unified_structure_levels(&self.engine, bar_start)?;
        let current = unified_structure_level_map(&levels);
        let upserts = current
            .iter()
            .filter_map(|(key, (signature, level))| {
                (self.previous.get(key).map(|(value, _)| value) != Some(signature))
                    .then(|| level.clone())
            })
            .collect::<Vec<_>>();
        let removed = self
            .previous
            .iter()
            .filter_map(|(key, (_, level))| {
                (!current.contains_key(key)).then(|| {
                    json!({
                        "unified_level_id": level
                            .get("unified_level_id")
                            .cloned()
                            .unwrap_or(Value::Null),
                        "side": level.get("side").cloned().unwrap_or(Value::Null),
                    })
                })
            })
            .collect::<Vec<_>>();
        if !upserts.is_empty() || !removed.is_empty() {
            self.rows.push(json!({
                "bar_start": bar_start,
                "qmd_structure_unified_level_delta": {
                    "upserts": upserts,
                    "removed": removed,
                },
            }));
        }
        self.previous = current;
        Ok(())
    }
}

fn serialized_unified_structure_levels(
    engine: &GenericStructureEngine,
    at: DateTime<Utc>,
) -> Result<Value, String> {
    let mut levels = serde_json::to_value(engine.snapshot(at).unified_levels)
        .map_err(|error| format!("failed to serialize unified structure timeline: {error}"))?;
    if let Some(rows) = levels.as_array_mut() {
        for level in rows {
            if let Some(object) = level.as_object_mut() {
                object.insert("sources".to_string(), json!([]));
            }
        }
    }
    Ok(levels)
}

fn structure_projection_bar_start(timestamp: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let micros = timestamp.timestamp_micros().div_euclid(1_000_000) * 1_000_000;
    DateTime::from_timestamp_micros(micros)
        .ok_or_else(|| "unified structure timeline timestamp is out of range".to_string())
}

fn projection_row_start(row: &Value) -> Option<DateTime<Utc>> {
    row.get("bar_start")
        .and_then(Value::as_str)
        .and_then(|value| DateTime::parse_from_rfc3339(value).ok())
        .map(|value| value.with_timezone(&Utc))
}

#[derive(Default)]
struct CacheStats {
    builds: AtomicU64,
    evictions: AtomicU64,
    hits: AtomicU64,
    misses: AtomicU64,
    prepared_bar_hits: AtomicU64,
    prepared_bar_misses: AtomicU64,
    prepared_bar_writes: AtomicU64,
}

enum IndicatorWork {
    Event {
        event: MarketEvent,
        bars: Vec<(Option<u64>, BarRow)>,
    },
    Finalize {
        bars: Vec<(Option<u64>, BarRow)>,
    },
}

impl HistoricalDerivedCache {
    /// Return the checkpoint used to seed the current extended-hours session.
    ///
    /// Chart preparation and causal strategy snapshots must share this seed.
    /// Re-querying the daily checkpoint tables after preparation duplicated the
    /// 180-day level-book warm-up on the first actionable strategy frame.
    pub async fn structure_session_seed(
        &self,
        ticker: &str,
        as_of: DateTime<Utc>,
    ) -> Result<Option<PersistedStructureCheckpointSeed>, String> {
        let session_start = session_anchor(as_of)?;
        let ticker = ticker.trim().to_ascii_uppercase();
        // A certified checkpoint already binds the immutable historical event
        // stream, split lineage, and historical structural input contract. Requiring
        // a fresh revision scan over its entire authority horizon defeated the
        // checkpoint and made every chart rebuild months of history. Only the
        // post-checkpoint advancement is revalidated by the materializer.
        if let Some(seed) = self
            .source
            .persisted_structure_checkpoint_before(&ticker, session_start)
            .await?
        {
            return Ok(Some(seed));
        }
        let authority_start =
            structure_rebuild_start(session_start, self.config.structure_book_lookback_days)?;
        let revision = self
            .source
            .structure_source_revision(&EventWindow {
                start: authority_start,
                end: session_start,
                tickers: vec![ticker.clone()],
            })
            .await?;
        Ok(self
            .structure_seed_checkpoint(&ticker, session_start)
            .await?
            .map(|checkpoint| PersistedStructureCheckpointSeed {
                authority_start,
                checkpoint,
                source_plan_hash: revision.source_plan_hash,
                source_revision_token: revision.token,
            }))
    }

    pub fn new(config: HistoricalGatewayConfig, source: HistoricalEventSource) -> Self {
        let max_concurrent_builds = config.cache_max_concurrent_builds;
        let max_concurrent_fetches = config.cache_max_concurrent_fetches;
        let allocated_bytes = Arc::new(AtomicU64::new(0));
        Self {
            allocated_bytes,
            config,
            inner: Arc::new(Mutex::new(CacheIndex {
                entries: HashMap::new(),
                order: VecDeque::new(),
            })),
            source,
            stats: Arc::new(CacheStats::default()),
            structure_seeds: Arc::new(Mutex::new(HashMap::new())),
            build_permits: Arc::new(Semaphore::new(max_concurrent_builds)),
            fetch_permits: Arc::new(Semaphore::new(max_concurrent_fetches)),
        }
    }

    /// Materialize one durable, revision-bound EMA/MACD seed before a
    /// historical session. The archive is read newest-first by physical
    /// `(ticker, ordinal)` ranges and only enough history to obtain the
    /// requested number of complete non-empty bars is retained.
    pub async fn prepare_indicator_warmup(
        &self,
        ticker: &str,
        timeframe: &str,
        session_start: DateTime<Utc>,
        required_bars: usize,
    ) -> Result<IndicatorWarmupArtifact, String> {
        let ticker = ticker.trim().to_ascii_uppercase();
        if ticker.is_empty() {
            return Err("indicator warm-up requires a ticker".to_string());
        }
        let resolution_us = parse_resolution_us(timeframe)
            .ok_or_else(|| format!("unsupported indicator warm-up timeframe {timeframe}"))?;
        if timeframe != "1s" {
            return Err("indicator warm-up currently requires the canonical 1s timeframe".into());
        }
        let required_bars = required_bars.clamp(1, 10_000);
        let path = indicator_warmup_cache_path(
            &self.config.prepared_bar_cache_root,
            &ticker,
            timeframe,
            session_start,
        );
        if let Some(mut artifact) =
            read_indicator_warmup_cache(&path, &ticker, timeframe, session_start)?
        {
            // A negative artifact is durable audit evidence, not a permanent
            // cache hit: later ingestion may add the missing execution-clock
            // authority. Re-evaluate it cheaply on every campaign resume.
            if artifact.status == "ready" {
                let revision = if artifact.source_revision.source_tiers == ["recent"] {
                    self.source
                        .recent_indicator_tail(&ticker, session_start, 1)
                        .await?
                        .map(|tail| tail.source_revision)
                        .ok_or_else(|| {
                            "persisted recent indicator authority is no longer covered".to_string()
                        })?
                } else {
                    self.source
                        .source_revision(&EventWindow {
                            start: artifact.authority_start,
                            end: session_start,
                            tickers: vec![ticker.clone()],
                        })
                        .await?
                };
                if revision.token == artifact.source_revision.token
                    && artifact.required_bars == required_bars
                    && artifact.calculation_revision == HISTORICAL_CALCULATION_REVISION
                    && artifact.corporate_action_revision == HISTORICAL_CORPORATE_ACTION_REVISION
                {
                    artifact.cache_hit = true;
                    return Ok(artifact);
                }
            }
        }

        let _permit = self
            .fetch_permits
            .clone()
            .acquire_owned()
            .await
            .map_err(|_| "indicator warm-up fetch capacity closed".to_string())?;

        for recent_limit in [10_000, 50_000, 250_000] {
            let Some(recent) = self
                .source
                .recent_indicator_tail(&ticker, session_start, recent_limit)
                .await?
            else {
                break;
            };
            let mut recent_bars = self
                .indicator_warmup_bars(timeframe, session_start, &recent.events)
                .await?;
            // A LIMIT-sized tail can begin inside a one-second bucket. If the
            // query returned fewer rows than requested, it covered the full
            // certified interval and its first bucket is complete.
            if recent.events.len() == recent_limit && !recent_bars.is_empty() {
                recent_bars.remove(0);
            }
            if recent_bars.len() >= required_bars {
                if recent_bars.len() > required_bars {
                    recent_bars.drain(..recent_bars.len() - required_bars);
                }
                let adjustments = self
                    .source
                    .structure_split_adjustments(&ticker, recent.authority_start, session_start)
                    .await?;
                for bar in &mut recent_bars {
                    let (price_factor, _) = split_adjustment_factors(bar.bar_start, &adjustments);
                    bar.close *= price_factor;
                }
                let artifact = IndicatorWarmupArtifact {
                    schema_version: INDICATOR_WARMUP_CACHE_SCHEMA_VERSION,
                    calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
                    corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
                    ticker,
                    timeframe: timeframe.to_string(),
                    session_start,
                    authority_start: recent.authority_start,
                    required_bars,
                    bars: recent_bars,
                    fetched_events: recent.events.len() as u64,
                    fetched_ordinal_ranges: 0,
                    source_revision: recent.source_revision,
                    status: "ready".to_string(),
                    cache_hit: false,
                };
                write_indicator_warmup_cache(&path, &artifact)?;
                return Ok(artifact);
            }
            if recent.events.len() < recent_limit {
                break;
            }
        }
        let session_date = session_start.with_timezone(&New_York).date_naive();
        let sessions = self
            .source
            .indicator_warmup_ordinal_sessions(&ticker, session_date, INDICATOR_WARMUP_MAX_SESSIONS)
            .await?;
        if sessions.is_empty() {
            let revision_key = format!("{ticker}:{session_date}:no-prior-event-history");
            let artifact = IndicatorWarmupArtifact {
                schema_version: INDICATOR_WARMUP_CACHE_SCHEMA_VERSION,
                calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
                corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
                ticker,
                timeframe: timeframe.to_string(),
                session_start,
                authority_start: session_start,
                required_bars,
                bars: Vec::new(),
                fetched_events: 0,
                fetched_ordinal_ranges: 0,
                source_revision: SourceRevision {
                    complete_for_history: false,
                    event_count: 0,
                    live_continuation_sequence: None,
                    max_build_step: 0,
                    max_updated_at: String::new(),
                    request_complete: true,
                    source_plan_hash: stable_hash_hex(&revision_key),
                    source_tiers: vec!["archive-no-prior-history".to_string()],
                    token: format!(
                        "indicator-warmup-no-history:{}",
                        stable_hash_hex(&revision_key)
                    ),
                },
                status: "insufficient_history".to_string(),
                cache_hit: false,
            };
            write_indicator_warmup_cache(&path, &artifact)?;
            return Ok(artifact);
        }
        let mut events = Vec::new();
        let mut fetched_events = 0_u64;
        let mut fetched_ordinal_ranges = 0_u64;
        let mut authority_start = session_start;
        let mut oldest_range_starts_at_session_boundary = false;
        let mut bars = Vec::new();
        'sessions: for session in sessions {
            if !session.execution_clock_complete {
                if !events.is_empty() {
                    bars = self
                        .indicator_warmup_bars(timeframe, session_start, &events)
                        .await?;
                    if bars.len() > required_bars {
                        bars.drain(..bars.len() - required_bars);
                    }
                }
                authority_start = New_York
                    .with_ymd_and_hms(
                        session.session_date.year(),
                        session.session_date.month(),
                        session.session_date.day(),
                        4,
                        0,
                        0,
                    )
                    .single()
                    .ok_or_else(|| "invalid indicator warm-up session boundary".to_string())?
                    .with_timezone(&Utc);
                let revision_key = format!(
                    "{}:{}:{}:{}:{}:{}",
                    ticker,
                    session.session_date,
                    session.first_ordinal,
                    session.next_ordinal,
                    session.event_count,
                    session.execution_clock_revision,
                );
                let artifact = IndicatorWarmupArtifact {
                    schema_version: INDICATOR_WARMUP_CACHE_SCHEMA_VERSION,
                    calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
                    corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
                    ticker,
                    timeframe: timeframe.to_string(),
                    session_start,
                    authority_start,
                    required_bars,
                    bars,
                    fetched_events,
                    fetched_ordinal_ranges,
                    source_revision: SourceRevision {
                        complete_for_history: false,
                        event_count: session.event_count,
                        live_continuation_sequence: None,
                        max_build_step: 0,
                        max_updated_at: session.execution_clock_revision,
                        request_complete: true,
                        source_plan_hash: stable_hash_hex(&revision_key),
                        source_tiers: vec!["archive-incomplete-execution-clock".to_string()],
                        token: format!(
                            "indicator-warmup-incomplete:{}",
                            stable_hash_hex(&revision_key)
                        ),
                    },
                    status: "insufficient_history".to_string(),
                    cache_hit: false,
                };
                write_indicator_warmup_cache(&path, &artifact)?;
                return Ok(artifact);
            }
            let mut next = session.next_ordinal;
            while next > session.first_ordinal {
                let first = next
                    .saturating_sub(INDICATOR_WARMUP_ORDINALS_PER_QUERY)
                    .max(session.first_ordinal);
                let mut receiver = self.source.stream_indicator_ordinal_range(
                    session.session_date,
                    &ticker,
                    first,
                    next,
                    self.config.batch_size.max(25_000),
                )?;
                while let Some(batch) = receiver.recv().await {
                    let batch = batch?;
                    fetched_events = fetched_events.saturating_add(batch.len() as u64);
                    events.extend(batch);
                }
                fetched_ordinal_ranges = fetched_ordinal_ranges.saturating_add(1);
                authority_start = New_York
                    .with_ymd_and_hms(
                        session.session_date.year(),
                        session.session_date.month(),
                        session.session_date.day(),
                        4,
                        0,
                        0,
                    )
                    .single()
                    .ok_or_else(|| "invalid indicator warm-up session boundary".to_string())?
                    .with_timezone(&Utc);
                oldest_range_starts_at_session_boundary = first == session.first_ordinal;

                let candidate_seconds = events
                    .iter()
                    .filter_map(|event| {
                        (event.execution_timestamp_us > 0)
                            .then_some(event.execution_timestamp_us / resolution_us)
                    })
                    .collect::<std::collections::BTreeSet<_>>()
                    .len();
                if candidate_seconds >= required_bars.saturating_add(1) {
                    bars = self
                        .indicator_warmup_bars(timeframe, session_start, &events)
                        .await?;
                    let complete_count = bars.len().saturating_sub(usize::from(
                        !oldest_range_starts_at_session_boundary && !bars.is_empty(),
                    ));
                    if complete_count >= required_bars {
                        break 'sessions;
                    }
                }
                next = first;
            }
        }
        if bars.is_empty() && !events.is_empty() {
            bars = self
                .indicator_warmup_bars(timeframe, session_start, &events)
                .await?;
        }
        if !oldest_range_starts_at_session_boundary && !bars.is_empty() {
            bars.remove(0);
        }
        if bars.len() > required_bars {
            bars.drain(..bars.len() - required_bars);
        }
        let adjustments = self
            .source
            .structure_split_adjustments(&ticker, authority_start, session_start)
            .await?;
        for bar in &mut bars {
            let (price_factor, _) = split_adjustment_factors(bar.bar_start, &adjustments);
            bar.close *= price_factor;
        }
        let source_revision = self
            .source
            .source_revision(&EventWindow {
                start: authority_start,
                end: session_start,
                tickers: vec![ticker.clone()],
            })
            .await?;
        let status = if bars.len() == required_bars {
            "ready"
        } else {
            "insufficient_history"
        };
        let artifact = IndicatorWarmupArtifact {
            schema_version: INDICATOR_WARMUP_CACHE_SCHEMA_VERSION,
            calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
            corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
            ticker,
            timeframe: timeframe.to_string(),
            session_start,
            authority_start,
            required_bars,
            bars,
            fetched_events,
            fetched_ordinal_ranges,
            source_revision,
            status: status.to_string(),
            cache_hit: false,
        };
        write_indicator_warmup_cache(&path, &artifact)?;
        Ok(artifact)
    }

    async fn indicator_warmup_bars(
        &self,
        timeframe: &str,
        session_start: DateTime<Utc>,
        events: &[LiveCompactEvent],
    ) -> Result<Vec<IndicatorWarmupBar>, String> {
        let store = SharedBarStore::new_without_structure(
            vec![timeframe.to_string()],
            self.config.cache_max_bars_per_entry,
            1,
            self.source.trade_aggregation_rules(),
        );
        let shard = store.shard(0);
        let mut ordered = events.iter().collect::<Vec<_>>();
        ordered.sort_by_key(|event| {
            (
                event.sip_timestamp_us,
                event.ticker.as_str(),
                event.arrival_sequence,
            )
        });
        let mut rows = Vec::new();
        for compact in ordered {
            let event = self.source.market_event(compact);
            for bar in shard.apply_event(&event).await {
                if bar.timeframe.eq_ignore_ascii_case(timeframe)
                    && bar.bar_end <= session_start
                    && valid_price_bar(&bar)
                {
                    rows.push(IndicatorWarmupBar {
                        bar_start: bar.bar_start,
                        close: bar.close,
                    });
                }
            }
        }
        for bar in shard.finalize_due(session_start).await {
            if bar.timeframe.eq_ignore_ascii_case(timeframe)
                && bar.bar_end <= session_start
                && valid_price_bar(&bar)
            {
                rows.push(IndicatorWarmupBar {
                    bar_start: bar.bar_start,
                    close: bar.close,
                });
            }
        }
        rows.sort_by_key(|bar| bar.bar_start);
        rows.dedup_by_key(|bar| bar.bar_start);
        Ok(rows)
    }

    async fn indicator_page_warmup(
        &self,
        window: &EventWindow,
        ticker: &str,
        timeframe: &str,
        live_continuation_sequence: Option<u64>,
    ) -> Result<IndicatorPageWarmup, String> {
        // Every bounded page inherits one durable seed at the 04:00 ET session
        // anchor. The seed is built from bounded physical ordinal ranges and
        // pinned to its source revision, so pages and backtests cannot drift
        // according to where their requested window begins.
        let session_start = session_anchor(window.start)?;
        let artifact = self
            .prepare_indicator_warmup(ticker, timeframe, session_start, INDICATOR_EMA_WARMUP_BARS)
            .await?;
        let mut ema_closes = artifact
            .bars
            .into_iter()
            .map(|bar| bar.close)
            .collect::<Vec<_>>();
        if window.start > session_start {
            // Same-session EMA state must be advanced with the identical raw
            // trade authority and bar aggregation used by the requested page.
            // Durable historical bars are a useful pre-session seed, but mixing
            // their independently prepared close series into an in-session
            // historical page can change MACD relative to a calculation that
            // started at 04:00. Rebuild only this bounded in-session prefix.
            let exact_prefix = self
                .exact_indicator_prefix(
                    EventWindow {
                        start: session_start,
                        end: window.start,
                        tickers: vec![ticker.to_ascii_uppercase()],
                    },
                    timeframe,
                    live_continuation_sequence,
                    None,
                )
                .await?;
            ema_closes.extend(exact_prefix.closes);
            return Ok(IndicatorPageWarmup {
                ema_closes,
                session_vwap_seed: exact_prefix.session_vwap_seed,
            });
        }
        Ok(IndicatorPageWarmup {
            ema_closes,
            session_vwap_seed: SessionVwapSeed::default(),
        })
    }

    async fn exact_indicator_prefix(
        &self,
        window: EventWindow,
        timeframe: &str,
        live_continuation_sequence: Option<u64>,
        max_closes: Option<usize>,
    ) -> Result<ExactIndicatorPrefix, String> {
        let bars = SharedBarStore::new_without_structure(
            vec![timeframe.to_string()],
            self.config.cache_max_bars_per_entry,
            1,
            self.source.trade_aggregation_rules(),
        );
        let shard = bars.shard(0);
        let mut receiver = self.source.stream_ordered_filtered(
            window.clone(),
            self.config.batch_size.max(100_000),
            live_continuation_sequence,
            Some(1),
        )?;
        let mut prefix = ExactIndicatorPrefix::default();
        let mut collect_bar = |bar: BarRow| {
            if !bar.timeframe.eq_ignore_ascii_case(timeframe)
                || bar.bar_end > window.end
                || !valid_price_bar(&bar)
            {
                return;
            }
            prefix.closes.push_back(bar.close);
            if let Some(limit) = max_closes {
                if prefix.closes.len() > limit {
                    prefix.closes.pop_front();
                }
            }
            if bar.volume.is_finite()
                && bar.volume > 0.0
                && bar.dollar_volume.is_finite()
                && bar.dollar_volume > 0.0
            {
                prefix.session_vwap_seed.cumulative_volume += bar.volume;
                prefix.session_vwap_seed.cumulative_trade_notional += bar.dollar_volume;
            }
            if bar.nbbo_consistent_volume.is_finite()
                && bar.nbbo_consistent_volume > 0.0
                && bar.nbbo_consistent_dollar_volume.is_finite()
                && bar.nbbo_consistent_dollar_volume > 0.0
            {
                prefix.session_vwap_seed.cumulative_execution_volume += bar.nbbo_consistent_volume;
                prefix.session_vwap_seed.cumulative_execution_trade_notional +=
                    bar.nbbo_consistent_dollar_volume;
            }
        };
        while let Some(batch) = receiver.recv().await {
            for compact in batch? {
                let event = self.source.market_event(&compact);
                for bar in shard.apply_event(&event).await {
                    collect_bar(bar);
                }
            }
        }
        for bar in shard.finalize_due(window.end).await {
            collect_bar(bar);
        }
        Ok(prefix)
    }

    async fn acquire(
        &self,
        window: EventWindow,
        ticker: String,
        profile: CacheProfile,
    ) -> Result<CacheLease, String> {
        let structure_seed = if matches!(&profile, CacheProfile::Structure(_)) {
            self.source
                .persisted_structure_checkpoint_before(&ticker, window.start)
                .await?
        } else {
            None
        };
        let revision_window = revision_window(
            &window,
            &profile,
            self.config.structure_book_lookback_days,
            structure_seed.is_some(),
        )?;
        let source_revision = if matches!(&profile, CacheProfile::Structure(_)) && structure_seed.is_none() {
            self.source
                .structure_source_revision(&revision_window)
                .await?
        } else {
            self.source.source_revision(&revision_window).await?
        };
        let mut key = cache_key(&window, &ticker, &source_revision, &profile);
        if let Some(seed) = structure_seed.as_ref() {
            key.push_str(":structure-seed:");
            key.push_str(&structure_seed_identity(seed)?);
        }
        let mut index = self.inner.lock().await;
        if let Some(entry) = index.entries.get(&key).cloned() {
            touch(&mut index.order, &key);
            self.stats.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(CacheLease {
                entry,
                hit: true,
                key,
                source_revision,
            });
        }

        self.stats.misses.fetch_add(1, Ordering::Relaxed);
        while index.entries.len() >= self.config.cache_max_entries {
            let Some(position) = index.order.iter().position(|candidate| {
                index
                    .entries
                    .get(candidate)
                    .is_some_and(|entry| entry.complete.load(Ordering::Acquire))
            }) else {
                break;
            };
            let Some(oldest) = index.order.remove(position) else {
                break;
            };
            if let Some(entry) = index.entries.remove(&oldest) {
                entry.release_accounting();
                self.stats.evictions.fetch_add(1, Ordering::Relaxed);
            }
        }
        let (bar_updates, _) = broadcast::channel(self.config.cache_update_capacity.max(16));
        let (updates, _) = broadcast::channel(self.config.cache_update_capacity.max(16));
        let requirement =
            historical_requirement(&key, &revision_window, &ticker, &profile, &source_revision);
        let entry = Arc::new(CacheEntry {
            accounted: AtomicBool::new(true),
            accounting_lock: StdMutex::new(()),
            allocated_bytes: self.allocated_bytes.clone(),
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: self.config.cache_max_bytes as u64,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            bar_updates,
            updates,
            estimated_bytes: AtomicU64::new(0),
            // The service-wide atomic reservation below is the authoritative
            // memory ceiling across concurrent products. A second implicit
            // half-budget rejected one legitimate high-volume ticker even
            // when the remaining global capacity was available.
            max_update_bytes: self.config.cache_max_bytes,
            max_updates: self.config.cache_max_updates_per_entry,
            product_bytes: AtomicU64::new(0),
            requirement: Some(requirement),
        });
        index.entries.insert(key.clone(), entry.clone());
        index.order.push_back(key.clone());
        drop(index);

        self.stats.builds.fetch_add(1, Ordering::Relaxed);
        let builder = self.clone();
        let build_entry = entry.clone();
        let build_revision = source_revision.clone();
        tokio::spawn(async move {
            builder
                .build(
                    build_entry,
                    window,
                    ticker,
                    profile,
                    build_revision,
                    structure_seed,
                )
                .await;
        });
        Ok(CacheLease {
            entry,
            hit: false,
            key,
            source_revision,
        })
    }

    pub async fn evict(&self, key: &str) -> bool {
        let mut index = self.inner.lock().await;
        index.order.retain(|candidate| candidate != key);
        let removed = index.entries.remove(key);
        drop(index);
        if let Some(entry) = &removed {
            entry.release_accounting();
        }
        let removed = removed.is_some();
        if removed {
            self.stats.evictions.fetch_add(1, Ordering::Relaxed);
        }
        removed
    }

    pub async fn acquire_derived(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
    ) -> Result<CacheLease, String> {
        self.acquire(window, ticker, CacheProfile::Derived(timeframe))
            .await
    }

    pub async fn snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
        limit: usize,
    ) -> Result<DerivedSnapshot, String> {
        let as_of = window.end;
        let lease = self
            .acquire(
                window,
                ticker.clone(),
                CacheProfile::Derived(timeframe.clone()),
            )
            .await?;
        let (frames, event_count) = lease.entry.wait_complete().await?;
        let matching = frames
            .iter()
            .filter(|frame| frame.bar.timeframe.eq_ignore_ascii_case(&timeframe))
            .collect::<Vec<_>>();
        let take = limit.min(matching.len());
        let selected = &matching[matching.len().saturating_sub(take)..];
        let mut bars = BarSnapshot {
            current: None,
            history: selected.iter().map(|frame| frame.bar.clone()).collect(),
            ticker: ticker.clone(),
            timeframe: timeframe.clone(),
        };
        if let Some(resolution_us) = parse_resolution_us(&timeframe) {
            let mut state = lease.entry.state.lock().await;
            if let Some(products) = state.products.as_mut() {
                let family = products.family_snapshot(
                    &ticker,
                    resolution_us,
                    limit.saturating_mul(3),
                    as_of,
                );
                bars.reconcile_family_authority(&family.rows);
            }
        }
        Ok(DerivedSnapshot {
            bars,
            cache: CacheEvidence {
                calculation_revision: HISTORICAL_CALCULATION_REVISION,
                corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
                engine_version: HISTORICAL_ENGINE_VERSION,
                event_count,
                hit: lease.hit,
                source_revision: lease.source_revision,
            },
            indicators: selected
                .iter()
                .map(|frame| frame.indicator.clone())
                .collect(),
        })
    }

    /// The open bucket is computed only from canonical events available by the
    /// requested cursor. Never slice a precomputed final candle: its high/low
    /// would disclose later trades. This small read is independent of structural
    /// history enrichment and uses the same SIP rules as ordinary chart bars.
    pub async fn forming_chart_bar(
        &self,
        ticker: String,
        timeframe: String,
        as_of: DateTime<Utc>,
    ) -> Result<Option<ChartBarRow>, String> {
        let resolution = parse_resolution_us(&timeframe)
            .filter(|value| *value <= 3_600_000_000)
            .ok_or_else(|| "forming candles require an intraday timeframe up to 1h".to_string())?;
        let resolution = resolution as i64;
        let start = DateTime::from_timestamp_micros(
            as_of.timestamp_micros().div_euclid(resolution) * resolution,
        )
        .ok_or_else(|| "invalid forming candle start".to_string())?;
        let window = EventWindow {
            start,
            end: as_of + Duration::microseconds(1),
            tickers: vec![ticker.clone()],
        };
        let _permit = self
            .fetch_permits
            .acquire()
            .await
            .map_err(|error| error.to_string())?;
        let mut receiver =
            self.source
                .stream_ordered_filtered(window, self.config.batch_size, None, Some(1))?;
        let mut events = Vec::new();
        while let Some(batch) = receiver.recv().await {
            events.extend(batch?);
            if events.len().saturating_mul(size_of::<LiveCompactEvent>())
                > self.config.cache_max_bytes / self.config.cache_max_concurrent_fetches.max(1)
            {
                return Err(
                    "forming candle exceeds the per-request event memory budget".to_string()
                );
            }
        }
        forming_bar_from_events(
            events
                .iter()
                .map(|event| self.source.market_event(event))
                .collect(),
            &ticker,
            &timeframe,
            start,
            as_of,
            self.source.trade_aggregation_rules(),
        )
        .await
    }

    pub async fn chart_snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
        bars_only: bool,
        structure_only: bool,
    ) -> Result<ChartSnapshot, String> {
        let resolution_us = parse_resolution_us(&timeframe)
            .ok_or_else(|| format!("unsupported chart timeframe {timeframe}"))?;
        let profile = if qmd_core::bars::is_supported_timeframe(&timeframe) {
            if bars_only {
                CacheProfile::Bars(timeframe.clone())
            } else if structure_only {
                CacheProfile::Structure(timeframe.clone())
            } else {
                CacheProfile::Derived(timeframe.clone())
            }
        } else {
            CacheProfile::Products
        };
        if matches!(&profile, CacheProfile::Bars(_)) {
            let revision_window = revision_window(
                &window,
                &profile,
                self.config.structure_book_lookback_days,
                false,
            )?;
            let source_revision = self.source.source_revision(&revision_window).await?;
            let key = cache_key(&window, &ticker, &source_revision, &profile);
            if let Some(artifact) = self
                .load_prepared_bar_cache(&key, &ticker, &timeframe)
                .await
            {
                self.stats.prepared_bar_hits.fetch_add(1, Ordering::Relaxed);
                return Ok(prepared_bar_chart_snapshot(
                    &artifact,
                    CacheEvidence {
                        calculation_revision: HISTORICAL_CALCULATION_REVISION,
                        corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
                        engine_version: HISTORICAL_ENGINE_VERSION,
                        event_count: artifact.event_count,
                        hit: true,
                        source_revision,
                    },
                    ticker,
                    timeframe,
                    limit,
                    as_of,
                    before,
                ));
            }
            self.stats
                .prepared_bar_misses
                .fetch_add(1, Ordering::Relaxed);
        }
        let lease = self
            .acquire(window.clone(), ticker.clone(), profile)
            .await?;
        let event_count = if (bars_only || structure_only)
            && qmd_core::bars::is_supported_timeframe(&timeframe)
        {
            lease.entry.wait_bars_ready().await?
        } else {
            lease.entry.wait_ready().await?
        };
        let cache = CacheEvidence {
            calculation_revision: HISTORICAL_CALCULATION_REVISION,
            corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
            engine_version: HISTORICAL_ENGINE_VERSION,
            event_count,
            hit: lease.hit,
            source_revision: lease.source_revision,
        };

        let bars_only_indicator_warmup = if bars_only {
            self.indicator_page_warmup(
                &window,
                &ticker,
                &timeframe,
                cache.source_revision.live_continuation_sequence,
            )
            .await?
        } else {
            IndicatorPageWarmup::default()
        };
        if qmd_core::bars::is_supported_timeframe(&timeframe) {
            let state = lease.entry.state.lock().await;
            if bars_only {
                let all_bars = state
                    .bars
                    .iter()
                    .filter(|update| update.bar.timeframe.eq_ignore_ascii_case(&timeframe))
                    .map(|update| ChartBarRow::from_bar(&update.bar))
                    .collect::<Vec<_>>();
                let all_updates = state
                    .bars
                    .iter()
                    .filter(|update| update.bar.timeframe.eq_ignore_ascii_case(&timeframe))
                    .collect::<Vec<_>>();
                let structure_projection = unified_structure_projection(&all_updates)?;
                let bar_indicator_projection = bar_indicator_projection(
                    &all_updates,
                    &bars_only_indicator_warmup.ema_closes,
                    window.start,
                    bars_only_indicator_warmup.session_vwap_seed,
                )?;
                drop(state);
                let artifact = PreparedBarCacheArtifact {
                    schema_version: PREPARED_BAR_CACHE_SCHEMA_VERSION,
                    key: lease.key.clone(),
                    event_count,
                    bars: all_bars,
                    bar_indicator_projection,
                    structure_projection,
                };
                if self.store_prepared_bar_cache(&artifact).await {
                    self.stats
                        .prepared_bar_writes
                        .fetch_add(1, Ordering::Relaxed);
                }
                return Ok(prepared_bar_chart_snapshot(
                    &artifact, cache, ticker, timeframe, limit, as_of, before,
                ));
            }
            if structure_only {
                let indicator_projection = state
                    .structure_projection
                    .iter()
                    .filter(|row| {
                        projection_row_start(row).is_some_and(|start| {
                            start <= as_of && before.is_none_or(|bound| start < bound)
                        })
                    })
                    .cloned()
                    .collect::<Vec<_>>();
                return Ok(ChartSnapshot {
                    as_of,
                    bars: Vec::new(),
                    cache,
                    has_more: false,
                    indicators: Vec::new(),
                    indicator_projection: Some(indicator_projection),
                    indicators_available: true,
                    market_signal_events: Vec::new(),
                    next_before: None,
                    structure_events: Vec::new(),
                    structure_level_history: Vec::new(),
                    ticker,
                    timeframe,
                });
            }
            let mut selected = state
                .frames
                .iter()
                .rev()
                .filter(|frame| {
                    frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                        && frame.bar.bar_end <= as_of
                        && before.is_none_or(|bound| frame.bar.bar_start < bound)
                })
                .take(limit.saturating_add(1))
                .collect::<Vec<_>>();
            let has_more = selected.len() > limit;
            selected.truncate(limit);
            selected.reverse();
            let bars = selected
                .iter()
                .map(|frame| ChartBarRow::from_bar(&frame.bar))
                .collect::<Vec<_>>();
            let indicators = selected
                .iter()
                .map(|frame| frame.indicator.clone())
                .collect::<Vec<_>>();
            let structure_level_history = encountered_structure_levels(&indicators);
            let mut structure_events = bars
                .first()
                .zip(bars.last())
                .map(|(first, last)| {
                    structure_events_overlapping(
                        &state.structure_events,
                        first.bar_start,
                        last.bar_end,
                        as_of,
                    )
                })
                .unwrap_or_default();
            structure_events.sort_by_key(|event| (event.confirmed_at, event.event_id));
            structure_events.dedup_by_key(|event| event.event_id);
            let next_before = has_more.then(|| bars[0].bar_start);
            let market_signal_events = bars
                .first()
                .zip(bars.last())
                .map(|(first, last)| {
                    state
                        .market_signal_events
                        .iter()
                        .filter(|event| {
                            event.effective_at >= first.bar_start
                                && event.effective_at <= last.bar_end
                                && event.effective_at <= as_of
                        })
                        .cloned()
                        .collect::<Vec<_>>()
                })
                .unwrap_or_default();
            return Ok(ChartSnapshot {
                as_of,
                bars,
                cache,
                has_more,
                indicators,
                indicator_projection: None,
                indicators_available: true,
                market_signal_events,
                next_before,
                structure_events,
                structure_level_history,
                ticker,
                timeframe,
            });
        }

        let mut state = lease.entry.state.lock().await;
        let products = state
            .products
            .as_mut()
            .ok_or_else(|| "historical market products were not built".to_string())?;
        let family = products.trade_price_snapshot_for_before(
            &ticker,
            resolution_us,
            limit.saturating_add(1),
            as_of,
            before,
        );
        let mut trade_rows = family
            .rows
            .into_iter()
            .filter(|row| row.bar_end <= as_of)
            .collect::<Vec<_>>();
        let has_more = trade_rows.len() > limit;
        if has_more {
            let remove = trade_rows.len() - limit;
            trade_rows.drain(..remove);
        }
        let bars = trade_rows
            .iter()
            .map(|row| ChartBarRow::from_family(row, &timeframe))
            .collect::<Vec<_>>();
        let next_before = has_more.then(|| bars[0].bar_start);
        Ok(ChartSnapshot {
            as_of,
            bars,
            cache,
            has_more,
            indicators: Vec::new(),
            indicator_projection: None,
            indicators_available: false,
            market_signal_events: Vec::new(),
            next_before,
            structure_events: Vec::new(),
            structure_level_history: Vec::new(),
            ticker,
            timeframe,
        })
    }

    pub async fn family_snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> Result<FamilyBarSnapshot, String> {
        let lease = self
            .acquire(window, ticker.clone(), CacheProfile::Products)
            .await?;
        lease.entry.wait_complete().await?;
        let mut state = lease.entry.state.lock().await;
        let products = state
            .products
            .as_mut()
            .ok_or_else(|| "historical market products were not built".to_string())?;
        Ok(products.family_snapshot(&ticker, resolution_us, limit, as_of))
    }

    pub async fn condition_snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        resolution_us: u64,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> Result<ConditionBarSnapshot, String> {
        let lease = self
            .acquire(window, ticker.clone(), CacheProfile::Products)
            .await?;
        lease.entry.wait_complete().await?;
        let mut state = lease.entry.state.lock().await;
        let products = state
            .products
            .as_mut()
            .ok_or_else(|| "historical market products were not built".to_string())?;
        Ok(products.condition_snapshot(&ticker, resolution_us, limit, as_of))
    }

    pub async fn macro_snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
        limit: usize,
        as_of: DateTime<Utc>,
    ) -> Result<MacroBarSnapshot, String> {
        let lease = self
            .acquire(window, ticker.clone(), CacheProfile::Products)
            .await?;
        lease.entry.wait_complete().await?;
        let mut state = lease.entry.state.lock().await;
        let products = state
            .products
            .as_mut()
            .ok_or_else(|| "historical market products were not built".to_string())?;
        Ok(products.macro_snapshot(&ticker, &timeframe, limit, as_of))
    }

    pub async fn metrics(&self) -> CacheMetrics {
        let index = self.inner.lock().await;
        let entries = index.entries.values().cloned().collect::<Vec<_>>();
        let entry_count = index.entries.len();
        drop(index);
        let mut requirements = Vec::with_capacity(entries.len());
        for entry in entries {
            let Some(mut requirement) = entry.requirement.clone() else {
                continue;
            };
            let state = entry.state.lock().await;
            requirement.state = if state.error.is_some() {
                "failed"
            } else if state.complete {
                "ready"
            } else if state.bars_ready {
                "bars_ready"
            } else {
                "building"
            }
            .to_string();
            requirement.event_count = state.events_processed;
            requirement.estimated_bytes = entry.estimated_bytes.load(Ordering::Relaxed);
            requirements.push(requirement);
        }
        requirements.sort_by(|left, right| left.requirement_id.cmp(&right.requirement_id));
        CacheMetrics {
            active_builds: self.config.cache_max_concurrent_builds
                - self.build_permits.available_permits(),
            builds: self.stats.builds.load(Ordering::Relaxed),
            estimated_bytes: self.allocated_bytes.load(Ordering::Relaxed),
            entries: entry_count,
            evictions: self.stats.evictions.load(Ordering::Relaxed),
            hits: self.stats.hits.load(Ordering::Relaxed),
            misses: self.stats.misses.load(Ordering::Relaxed),
            max_bytes: self.config.cache_max_bytes,
            prepared_bar_hits: self.stats.prepared_bar_hits.load(Ordering::Relaxed),
            prepared_bar_misses: self.stats.prepared_bar_misses.load(Ordering::Relaxed),
            prepared_bar_writes: self.stats.prepared_bar_writes.load(Ordering::Relaxed),
            requirements,
        }
    }

    async fn load_prepared_bar_cache(
        &self,
        key: &str,
        ticker: &str,
        timeframe: &str,
    ) -> Option<PreparedBarCacheArtifact> {
        let path = prepared_bar_cache_path(&self.config.prepared_bar_cache_root, key);
        let expected_key = key.to_string();
        let expected_ticker = ticker.to_ascii_uppercase();
        let expected_timeframe = timeframe.to_string();
        match tokio::task::spawn_blocking(move || {
            read_prepared_bar_cache(&path, &expected_key, &expected_ticker, &expected_timeframe)
        })
        .await
        {
            Ok(Ok(artifact)) => artifact,
            Ok(Err(error)) => {
                eprintln!("QMD History ignored invalid prepared-bar cache: {error}");
                None
            }
            Err(error) => {
                eprintln!("QMD History prepared-bar cache reader panicked: {error}");
                None
            }
        }
    }

    async fn store_prepared_bar_cache(&self, artifact: &PreparedBarCacheArtifact) -> bool {
        if artifact.bars.is_empty() {
            return false;
        }
        let root = self.config.prepared_bar_cache_root.clone();
        let max_entries = self.config.cache_max_entries;
        let artifact = artifact.clone();
        match tokio::task::spawn_blocking(move || {
            let bytes = serde_json::to_vec(&artifact)
                .map_err(|error| format!("failed to serialize prepared bars: {error}"))?;
            write_prepared_bar_cache(&root, &artifact.key, &bytes, max_entries)
        })
        .await
        {
            Ok(Ok(wrote)) => wrote,
            Ok(Err(error)) => {
                eprintln!("QMD History could not persist prepared bars: {error}");
                false
            }
            Err(error) => {
                eprintln!("QMD History prepared-bar cache writer panicked: {error}");
                false
            }
        }
    }

    async fn build(
        &self,
        entry: Arc<CacheEntry>,
        window: EventWindow,
        ticker: String,
        profile: CacheProfile,
        source_revision: SourceRevision,
        structure_seed: Option<PersistedStructureCheckpointSeed>,
    ) {
        let permit = match self.build_permits.acquire().await {
            Ok(permit) => permit,
            Err(_) => {
                let mut state = entry.state.lock().await;
                state.error = Some("historical build concurrency gate closed".to_string());
                state.complete = true;
                entry.complete.store(true, Ordering::Release);
                entry.notify.notify_waiters();
                return;
            }
        };
        let result = self
            .build_inner(
                entry.clone(),
                window,
                ticker,
                profile,
                source_revision,
                structure_seed,
            )
            .await;
        drop(permit);
        let mut state = entry.state.lock().await;
        match result {
            Ok(events_processed) => {
                state.events_processed = events_processed;
                state.complete = true;
            }
            Err(error) => {
                state.error = Some(error);
                state.complete = true;
            }
        }
        drop(state);
        entry.complete.store(true, Ordering::Release);
        entry.notify.notify_waiters();
        self.enforce_byte_limit().await;
    }

    async fn build_inner(
        &self,
        entry: Arc<CacheEntry>,
        window: EventWindow,
        ticker: String,
        profile: CacheProfile,
        source_revision: SourceRevision,
        structure_seed: Option<PersistedStructureCheckpointSeed>,
    ) -> Result<u64, String> {
        let builds_products = matches!(&profile, CacheProfile::Products);
        let resolutions = self
            .config
            .product_timeframes
            .iter()
            .filter_map(|value| parse_resolution_us(value))
            .collect::<Vec<_>>();
        let requested_timeframe = match &profile {
            CacheProfile::Bars(timeframe)
            | CacheProfile::Derived(timeframe)
            | CacheProfile::Structure(timeframe) => Some(timeframe.clone()),
            CacheProfile::Products => None,
        };
        let structure_only = matches!(&profile, CacheProfile::Structure(_));
        // Only a cold inherited-history rebuild uses the declared SIP
        // approximation. Post-checkpoint chart advancement must use the same
        // execution-aware event source as strategy session advancement.
        let structure_approximation = structure_only && structure_seed.is_none();
        let bars_only = matches!(&profile, CacheProfile::Bars(_));
        let derived_timeframes = match (&profile, &requested_timeframe) {
            (CacheProfile::Bars(_), Some(timeframe)) => vec![timeframe.clone()],
            (CacheProfile::Structure(_), Some(timeframe)) => vec![timeframe.clone()],
            (_, Some(timeframe)) if timeframe.eq_ignore_ascii_case("100ms") => {
                vec![timeframe.clone()]
            }
            (_, Some(timeframe)) => vec!["100ms".to_string(), timeframe.clone()],
            (_, None) => Vec::new(),
        };
        let bars = if bars_only || structure_only {
            // A bars-stage request is the scalar closed-bar authority. Unified
            // Structural Levels are loaded through their checkpoint-backed
            // projection, so advancing the event-native level book here is
            // redundant O(events) work and made active tickers exceed the
            // request deadline before a single cached bar was returned.
            SharedBarStore::new_without_structure(
                derived_timeframes,
                self.config.cache_max_bars_per_entry,
                1,
                self.source.trade_aggregation_rules(),
            )
        } else {
            SharedBarStore::new(
                derived_timeframes,
                self.config.cache_max_bars_per_entry,
                1,
                self.source.trade_aggregation_rules(),
            )
        };
        let indicator_page_warmup = if matches!(&profile, CacheProfile::Derived(_)) {
            self.indicator_page_warmup(
                &window,
                &ticker,
                requested_timeframe.as_deref().unwrap_or("1s"),
                source_revision.live_continuation_sequence,
            )
            .await?
        } else {
            IndicatorPageWarmup::default()
        };
        let mut structure_engine = structure_only.then(|| GenericStructureEngine::new(&ticker));
        if matches!(
            &profile,
            CacheProfile::Derived(_) | CacheProfile::Structure(_)
        ) {
            let checkpoint = match structure_seed {
                Some(seed) => Some(seed.checkpoint),
                None => {
                    self.structure_seed_checkpoint(&ticker, window.start)
                        .await?
                }
            };
            if let Some(checkpoint) = checkpoint {
                if let Some(engine) = structure_engine.as_mut() {
                    engine.seed_checkpoint(&checkpoint);
                } else {
                    bars.seed_structure_checkpoints(vec![(ticker.clone(), checkpoint)])
                        .await;
                }
            }
        }
        let mut structure_projection = structure_engine
            .map(|engine| StructureProjectionBuilder::new(engine, window.start))
            .transpose()?;
        let shard = bars.shard(0);
        let trade_rules = self.source.trade_aggregation_rules();
        let structure_references = if matches!(&profile, CacheProfile::Derived(_)) {
            self.source
                .market_structure_reference_levels(&ticker, window.start)
                .await
                .unwrap_or_else(|error| {
                    eprintln!(
                        "QMD historical daily market-structure references unavailable for {ticker}: {error}"
                    );
                    MarketStructureReferenceLevels::default()
                })
        } else {
            MarketStructureReferenceLevels::default()
        };
        let mut indicator_worker = if matches!(&profile, CacheProfile::Derived(_)) {
            let (sender, mut receiver) = mpsc::channel::<IndicatorWork>(
                self.config.cache_update_capacity.clamp(16, 100_000),
            );
            let worker_entry = entry.clone();
            let worker_rules = trade_rules.clone();
            let worker_structure_references = structure_references;
            let worker_session_vwap_seed = indicator_page_warmup.session_vwap_seed;
            let worker_page_start = window.start;
            let worker_requested_timeframe = requested_timeframe.clone();
            let worker_indicator_ema_warmup_closes = indicator_page_warmup.ema_closes;
            let handle = tokio::spawn(async move {
                let mut calculators = HashMap::<String, BarIndicatorCalculator>::new();
                let mut microstructure = MicrostructureIntervalWindow::default();
                let mut aggregate = MicrostructureSampleAggregate::default();
                let mut market_signal_engine = MarketSignalEngine::default();
                let mut last_base_indicator: Option<IndicatorRow> = None;
                while let Some(work) = receiver.recv().await {
                    let bars = match work {
                        IndicatorWork::Event { event, bars } => {
                            microstructure.apply_event(&event);
                            bars
                        }
                        IndicatorWork::Finalize { bars } => bars,
                    };
                    for (sequence, bar) in bars {
                        if bar.timeframe.eq_ignore_ascii_case("100ms") {
                            let interval = microstructure.interval_at(bar.bar_end, &worker_rules);
                            let calculator =
                                calculators.entry(bar.timeframe.clone()).or_insert_with(|| {
                                    let mut calculator = BarIndicatorCalculator::new();
                                    if worker_requested_timeframe.as_deref().is_some_and(
                                        |timeframe| bar.timeframe.eq_ignore_ascii_case(timeframe),
                                    ) {
                                        calculator.seed_ema_close_history(
                                            worker_indicator_ema_warmup_closes.iter().copied(),
                                        );
                                    }
                                    calculator
                                        .seed_session_vwap(
                                            worker_page_start,
                                            worker_session_vwap_seed.cumulative_volume,
                                            worker_session_vwap_seed.cumulative_trade_notional,
                                            worker_session_vwap_seed.cumulative_execution_volume,
                                            worker_session_vwap_seed
                                                .cumulative_execution_trade_notional,
                                        )
                                        .expect("validated historical session VWAP seed");
                                    calculator.set_market_structure_references(
                                        worker_structure_references,
                                    );
                                    calculator
                                });
                            let mut indicator = if valid_price_bar(&bar) {
                                calculator.apply_bar_for_historical_cache(&bar)
                            } else if let Some(previous) = &last_base_indicator {
                                let mut carried = previous.clone();
                                carried.session_date = bar.session_date.clone();
                                carried.bar_start = bar.bar_start;
                                carried.bar_end = bar.bar_end;
                                carried.volume = 0.0;
                                carried.qmd_structure_events.clear();
                                (carried.vwap, carried.execution_vwap) =
                                    calculator.apply_session_vwaps_only(&bar);
                                carried.price_vs_vwap_pct = if carried.vwap > 0.0 {
                                    (carried.close / carried.vwap - 1.0) * 100.0
                                } else {
                                    0.0
                                };
                                carried.price_vs_execution_vwap_pct =
                                    if carried.execution_vwap > 0.0 {
                                        (carried.close / carried.execution_vwap - 1.0) * 100.0
                                    } else {
                                        0.0
                                    };
                                carried
                            } else {
                                calculator.apply_session_vwaps_only(&bar);
                                continue;
                            };
                            calculator.apply_microstructure_interval(&mut indicator, &interval);
                            calculator.apply_cumulative_microstructure(&mut indicator);
                            if valid_price_bar(&bar) {
                                calculator.apply_market_levels(&mut indicator, &bar);
                            }
                            worker_entry
                                .push_structure_events(&indicator.qmd_structure_events)
                                .await?;
                            for event in
                                market_signal_engine.update_with_indicator(&bar, Some(&indicator))
                            {
                                worker_entry.push_market_signal_event(event).await?;
                            }
                            // The complete unified book is already retained by
                            // the bar-owned delta projection.  Compact before
                            // cloning the carried 100 ms state so ordinary
                            // derived preparation remains linear in bar count.
                            let indicator = indicator.compact_for_historical_cache();
                            last_base_indicator = Some(indicator.clone());
                            aggregate.push(&indicator);
                            if let Some(sequence) = sequence {
                                worker_entry
                                    .push_indicator(sequence, bar, indicator)
                                    .await?;
                            }
                        } else if let Some(sequence) = sequence {
                            let calculator =
                                calculators.entry(bar.timeframe.clone()).or_insert_with(|| {
                                    let mut calculator = BarIndicatorCalculator::new();
                                    if worker_requested_timeframe.as_deref().is_some_and(
                                        |timeframe| bar.timeframe.eq_ignore_ascii_case(timeframe),
                                    ) {
                                        calculator.seed_ema_close_history(
                                            worker_indicator_ema_warmup_closes.iter().copied(),
                                        );
                                    }
                                    calculator
                                        .seed_session_vwap(
                                            worker_page_start,
                                            worker_session_vwap_seed.cumulative_volume,
                                            worker_session_vwap_seed.cumulative_trade_notional,
                                            worker_session_vwap_seed.cumulative_execution_volume,
                                            worker_session_vwap_seed
                                                .cumulative_execution_trade_notional,
                                        )
                                        .expect("validated historical session VWAP seed");
                                    calculator.set_market_structure_references(
                                        worker_structure_references,
                                    );
                                    calculator
                                });
                            let mut indicator = calculator.apply_bar_for_historical_cache(&bar);
                            aggregate.apply_to(&mut indicator);
                            aggregate.reset();
                            calculator.apply_cumulative_microstructure(&mut indicator);
                            calculator.apply_market_levels(&mut indicator, &bar);
                            for event in
                                market_signal_engine.update_with_indicator(&bar, Some(&indicator))
                            {
                                worker_entry.push_market_signal_event(event).await?;
                            }
                            worker_entry
                                .push_indicator(sequence, bar, indicator)
                                .await?;
                        }
                    }
                }
                Ok::<(), String>(())
            });
            Some((sender, handle))
        } else {
            None
        };
        let mut indicator_sender = indicator_worker.as_ref().map(|(sender, _)| sender.clone());
        let mut products = builds_products.then(|| {
            MarketProductEngine::new(
                resolutions,
                ProductCacheLimits {
                    max_bytes: self.config.cache_max_bytes / 2,
                    max_partitions: self.config.cache_max_entries.max(1),
                    max_rows: self.config.product_cache_max_rows_per_entry,
                },
                self.source.trade_aggregation_rules(),
                ConditionClassifier::training_aligned(),
            )
        });
        let mut events_processed = 0_u64;
        // Retrospective chart bars are the one projection that uses exchange
        // execution time. Buffer only this profile so open/close remain ordered;
        // causal derived profiles continue streaming in SIP-availability order.
        let mut chart_events = Vec::<LiveCompactEvent>::new();
        let chunks = split_event_window(&window, self.config.fetch_chunk_hours);
        let per_build_fetches = self
            .config
            .cache_max_concurrent_fetches
            .div_ceil(self.config.cache_max_concurrent_builds)
            .max(1);
        let mut next_chunk = 0usize;
        let mut active = VecDeque::new();
        while next_chunk < chunks.len() && active.len() < per_build_fetches {
            active.push_back(self.spawn_chunk_fetch(
                chunks[next_chunk].clone(),
                source_revision.live_continuation_sequence,
                cache_event_type_filter(&profile),
                structure_approximation,
            ));
            next_chunk += 1;
        }
        while let Some(mut receiver) = active.pop_front() {
            while let Some(batch) = receiver.recv().await {
                let events = batch?;
                let count = events.len();
                if events_processed.saturating_add(count as u64)
                    > self.config.max_events_per_request as u64
                {
                    return Err(format!(
                        "historical derived build exceeded event_limit={}",
                        self.config.max_events_per_request
                    ));
                }
                for compact in &events {
                    if bars_only {
                        chart_events.push(compact.clone());
                        continue;
                    }
                    let event = self.source.market_event(compact);
                    if event.is_delayed_trade_report() {
                        continue;
                    }
                    if let Some(builder) = structure_projection.as_mut() {
                        // Quotes do not create structural levels, but they are
                        // causal inputs to the prevailing NBBO used to classify
                        // trade pressure.  The presentation projection must feed
                        // the same complete ordered event stream to function F as
                        // direct checkpoint materialization; filtering to trades
                        // changes volume attribution, lifecycle transitions, and
                        // derived scores.
                        let conditions = match &event {
                            MarketEvent::Trade(event) => event.conditions.as_slice(),
                            MarketEvent::Quote(event) => event.conditions.as_slice(),
                        };
                        let trade_rule = trade_rules.resolve(conditions, event.ts());
                        builder.apply_event(&event, trade_rule)?;
                        continue;
                    }
                    if let Some(products) = products.as_mut() {
                        products.apply_event(&event, event.ts());
                    }
                    let mut indicator_bars = Vec::new();
                    for bar in shard.apply_event(&event).await {
                        let is_base = bar.timeframe.eq_ignore_ascii_case("100ms");
                        let valid_price = valid_price_bar(&bar);
                        if !valid_price && !is_base {
                            continue;
                        }
                        let sequence = if valid_price
                            && requested_timeframe.as_ref().is_some_and(|timeframe| {
                                bar.timeframe.eq_ignore_ascii_case(timeframe)
                            }) {
                            Some(entry.push_bar(bar.clone()).await?)
                        } else {
                            None
                        };
                        indicator_bars.push((sequence, bar));
                    }
                    if let Some(sender) = indicator_sender.as_mut() {
                        if sender
                            .send(IndicatorWork::Event {
                                event,
                                bars: indicator_bars,
                            })
                            .await
                            .is_err()
                        {
                            drop(indicator_sender.take());
                            if let Some((original_sender, handle)) = indicator_worker.take() {
                                drop(original_sender);
                                return match handle.await {
                                    Ok(Ok(())) => Err(
                                        "historical indicator worker stopped early without an error"
                                            .to_string(),
                                    ),
                                    Ok(Err(error)) => Err(error),
                                    Err(error) => Err(format!(
                                        "historical indicator worker panicked: {error}"
                                    )),
                                };
                            }
                            return Err(
                                "historical indicator worker stopped early without a handle"
                                    .to_string(),
                            );
                        }
                    }
                }
                events_processed += count as u64;
                if let Some(products) = products.as_ref() {
                    entry.set_product_bytes(products.metrics().estimated_bytes)?;
                }
                let mut state = entry.state.lock().await;
                state.events_processed = events_processed;
            }
            if next_chunk < chunks.len() {
                active.push_back(self.spawn_chunk_fetch(
                    chunks[next_chunk].clone(),
                    source_revision.live_continuation_sequence,
                    cache_event_type_filter(&profile),
                    structure_approximation,
                ));
                next_chunk += 1;
            }
        }
        if bars_only {
            chart_events.sort_by_key(|event| {
                (
                    if event.execution_timestamp_us > 0 {
                        event.execution_timestamp_us
                    } else {
                        event.sip_timestamp_us
                    },
                    event.source_sequence,
                    event.event_type(),
                    event.arrival_sequence,
                )
            });
            for compact in &chart_events {
                let event = self.source.market_event(compact).for_execution_time_chart();
                if event.ts() < window.start || event.ts() >= window.end {
                    continue;
                }
                for bar in shard.apply_event(&event).await {
                    if valid_price_bar(&bar)
                        && requested_timeframe
                            .as_ref()
                            .is_some_and(|timeframe| bar.timeframe.eq_ignore_ascii_case(timeframe))
                    {
                        entry.push_bar(bar).await?;
                    }
                }
            }
        }
        if let Some(builder) = structure_projection {
            entry
                .store_structure_projection(builder.finish(window.end)?)
                .await?;
        }
        let mut final_indicator_bars = Vec::new();
        for bar in shard.finalize_due(window.end).await {
            let is_base = bar.timeframe.eq_ignore_ascii_case("100ms");
            let valid_price = valid_price_bar(&bar);
            if !valid_price && !is_base {
                continue;
            }
            let sequence = if valid_price
                && requested_timeframe
                    .as_ref()
                    .is_some_and(|timeframe| bar.timeframe.eq_ignore_ascii_case(timeframe))
            {
                Some(entry.push_bar(bar.clone()).await?)
            } else {
                None
            };
            final_indicator_bars.push((sequence, bar));
        }
        if let Some(sender) = indicator_sender.take() {
            {
                let mut state = entry.state.lock().await;
                state.bars_ready = true;
                state.events_processed = events_processed;
            }
            entry.notify.notify_waiters();
            sender
                .send(IndicatorWork::Finalize {
                    bars: final_indicator_bars,
                })
                .await
                .map_err(|_| {
                    "historical indicator worker stopped before finalization".to_string()
                })?;
            drop(sender);
        }
        if let Some((original_sender, handle)) = indicator_worker {
            drop(original_sender);
            handle
                .await
                .map_err(|error| format!("historical indicator worker panicked: {error}"))??;
        }
        if let Some(products) = products {
            let product_metrics = products.metrics();
            entry.set_product_bytes(product_metrics.estimated_bytes)?;
            if product_metrics.evictions > 0 {
                return Err(format!(
                    "historical canonical product build exceeded its bounded cache: evictions={} rows={} estimated_bytes={}",
                    product_metrics.evictions,
                    product_metrics.family_rows + product_metrics.condition_rows,
                    product_metrics.estimated_bytes,
                ));
            }
            let mut state = entry.state.lock().await;
            state.products = Some(products);
        }
        Ok(events_processed)
    }

    async fn structure_seed_checkpoint(
        &self,
        ticker: &str,
        before: DateTime<Utc>,
    ) -> StructureSeedResult {
        // Prefer the durable certified authority before computing a cold-seed
        // identity. The checkpoint loader validates its causal hash chain and
        // historical structure source contract, while advancement validates only
        // events after its replay cursor. This is the intended checkpoint
        // boundary and avoids a redundant full-horizon source scan.
        if let Some(seed) = self
            .source
            .persisted_structure_checkpoint_before(ticker, before)
            .await?
        {
            return Ok(Some(seed.checkpoint));
        }
        // A missing or algorithm-incompatible persisted checkpoint must
        // rebuild the same complete horizon promised by the ticker level-book
        // contract. A shorter fallback silently deleted older levels exactly
        // when a new algorithm revision invalidated the persisted seed.
        let rebuild_start =
            structure_rebuild_start(before, self.config.structure_book_lookback_days)?;
        let rebuild_as_of = before
            .checked_sub_signed(Duration::microseconds(1))
            .ok_or_else(|| "historical structure warm-start underflow".to_string())?;
        let seed_window = EventWindow {
            start: rebuild_start,
            end: before,
            tickers: vec![ticker.to_string()],
        };
        let seed_revision = self.source.structure_source_revision(&seed_window).await?;
        // Corporate actions are a separate authority from canonical SIP
        // events. A late split correction therefore does not change the event
        // source revision. Include the exact split rows (including their
        // insertion timestamps) in the restart-safe seed identity so a cached
        // pre-adjustment level book can never survive a corrected split table.
        let split_horizon_start = rebuild_start
            .checked_sub_signed(Duration::microseconds(1))
            .ok_or_else(|| "historical structure split horizon underflow".to_string())?;
        let split_adjustments = self
            .source
            .structure_split_adjustments(ticker, split_horizon_start, before)
            .await?;
        let key = structure_seed_cache_key(
            &self.config.structure_checkpoint_set_id,
            ticker,
            rebuild_start,
            before,
            &seed_revision.token,
            &split_adjustments,
        )?;
        let legacy_key = structure_seed_cache_key_for_revision(
            &self.config.structure_checkpoint_set_id,
            ticker,
            rebuild_start,
            before,
            &seed_revision.token,
            &split_adjustments,
            LEGACY_STRUCTURE_CALCULATION_REVISION,
        )?;
        let cell = {
            let mut seeds = self.structure_seeds.lock().await;
            if let Some(existing) = seeds.get(&key) {
                existing.clone()
            } else {
                if seeds.len() >= self.config.cache_max_entries {
                    let removable = seeds
                        .iter()
                        .find(|(_, cell)| cell.initialized())
                        .map(|(key, _)| key.clone());
                    if let Some(removable) = removable {
                        seeds.remove(&removable);
                    }
                }
                let cell = Arc::new(OnceCell::new());
                seeds.insert(key.clone(), cell.clone());
                cell
            }
        };
        let config = self.config.clone();
        let source = self.source.clone();
        let ticker = ticker.to_string();
        let seed_key = key.clone();
        let legacy_seed_key = legacy_key;
        cell.get_or_init(|| async move {
            let prepared_root = config.prepared_bar_cache_root.join("structure-seeds");
            let prepared_path = prepared_structure_seed_cache_path(&prepared_root, &seed_key);
            let expected_key = seed_key.clone();
            let expected_ticker = ticker.clone();
            match tokio::task::spawn_blocking(move || {
                read_prepared_structure_seed_cache(&prepared_path, &expected_key, &expected_ticker)
            })
            .await
            {
                Ok(Ok(Some(artifact))) => return Ok(Some(artifact.checkpoint)),
                Ok(Ok(None)) => {}
                Ok(Err(error)) => {
                    eprintln!("QMD History ignored invalid prepared structure seed: {error}");
                }
                Err(error) => {
                    eprintln!("QMD History prepared structure seed reader panicked: {error}");
                }
            }
            if legacy_seed_key != seed_key {
                let legacy_path =
                    prepared_structure_seed_cache_path(&prepared_root, &legacy_seed_key);
                let expected_legacy_key = legacy_seed_key.clone();
                let expected_ticker = ticker.clone();
                match tokio::task::spawn_blocking(move || {
                    read_prepared_structure_seed_cache(
                        &legacy_path,
                        &expected_legacy_key,
                        &expected_ticker,
                    )
                })
                .await
                {
                    Ok(Ok(Some(legacy))) => {
                        let checkpoint = legacy.checkpoint;
                        let artifact = PreparedStructureSeedCacheArtifact {
                            schema_version: PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION,
                            key: seed_key.clone(),
                            ticker: ticker.to_ascii_uppercase(),
                            checkpoint: checkpoint.clone(),
                        };
                        let migration_root = prepared_root.clone();
                        let max_entries = config.cache_max_entries;
                        match tokio::task::spawn_blocking(move || {
                            let bytes = serde_json::to_vec(&artifact).map_err(|error| {
                                format!("failed to serialize migrated structure seed: {error}")
                            })?;
                            write_prepared_structure_seed_cache(
                                &migration_root,
                                &artifact.key,
                                &bytes,
                                max_entries,
                            )
                        })
                        .await
                        {
                            Ok(Ok(_)) => {}
                            Ok(Err(error)) => eprintln!(
                                "QMD History could not persist migrated structure seed: {error}"
                            ),
                            Err(error) => eprintln!(
                                "QMD History structure seed migration writer panicked: {error}"
                            ),
                        }
                        return Ok(Some(checkpoint));
                    }
                    Ok(Ok(None)) => {}
                    Ok(Err(error)) => {
                        eprintln!("QMD History ignored invalid legacy structure seed: {error}");
                    }
                    Err(error) => {
                        eprintln!("QMD History legacy structure seed reader panicked: {error}");
                    }
                }
            }
            if let Some(seed) = persisted_structure_book_seed(&source, &ticker, before).await? {
                return Ok(Some(seed.checkpoint));
            }
            // The dedicated level-book profile is trade-driven end to end.
            // Use the same filtered authority for its warm-start rebuild so a
            // missing persisted checkpoint cannot replay days of irrelevant
            // quote traffic before the requested chart session can begin.
            match rebuild_trade_structure_checkpoint(
                &config,
                &source,
                StructureCheckpointRebuildRequest {
                    schema_version: STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
                    ticker: ticker.clone(),
                    start: rebuild_start,
                    as_of: rebuild_as_of,
                    expected_source_plan_hash: Some(seed_revision.source_plan_hash),
                    event_limit: Some(config.structure_checkpoint_rebuild_max_events),
                },
            )
            .await
            {
                Ok(rebuilt) => {
                    let checkpoint = rebuilt.checkpoint;
                    let artifact = PreparedStructureSeedCacheArtifact {
                        schema_version: PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION,
                        key: seed_key.clone(),
                        ticker: ticker.to_ascii_uppercase(),
                        checkpoint: checkpoint.clone(),
                    };
                    let prepared_root = prepared_root.clone();
                    let max_entries = config.cache_max_entries;
                    match tokio::task::spawn_blocking(move || {
                        let bytes = serde_json::to_vec(&artifact).map_err(|error| {
                            format!("failed to serialize prepared structure seed: {error}")
                        })?;
                        write_prepared_structure_seed_cache(
                            &prepared_root,
                            &artifact.key,
                            &bytes,
                            max_entries,
                        )
                    })
                    .await
                    {
                        Ok(Ok(_)) => {}
                        Ok(Err(error)) => {
                            eprintln!(
                                "QMD History could not persist prepared structure seed: {error}"
                            );
                        }
                        Err(error) => {
                            eprintln!(
                                "QMD History prepared structure seed writer panicked: {error}"
                            );
                        }
                    }
                    Ok(Some(checkpoint))
                }
                Err(error)
                    if error.starts_with("Generic Structure rebuild found no canonical events") =>
                {
                    Ok(None)
                }
                Err(error) => Err(error),
            }
        })
        .await
        .clone()
    }

    fn spawn_chunk_fetch(
        &self,
        window: EventWindow,
        live_continuation_sequence: Option<u64>,
        event_type_filter: Option<u8>,
        structure_policy: bool,
    ) -> mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>> {
        let (sender, receiver) = mpsc::channel(2);
        let source = self.source.clone();
        let permits = self.fetch_permits.clone();
        // A caller may use a selective event-family query only when that
        // projection's authority does not depend on the omitted family.  The
        // structural profile deliberately passes no filter because its
        // prevailing-NBBO pressure evidence depends on quotes.
        let batch_size = if event_type_filter.is_some() {
            self.config.batch_size.max(100_000)
        } else {
            self.config.batch_size
        };
        tokio::spawn(async move {
            let _permit = match permits.acquire_owned().await {
                Ok(permit) => permit,
                Err(_) => {
                    let _ = sender
                        .send(Err("historical fetch concurrency gate closed".to_string()))
                        .await;
                    return;
                }
            };
            let mut cursor: Option<HistoricalCursor> = None;
            loop {
                let fetched = if structure_policy {
                    source
                        .fetch_structure_batch_at_revision_filtered(
                            &window,
                            cursor.as_ref(),
                            batch_size,
                            live_continuation_sequence,
                            event_type_filter,
                        )
                        .await
                } else {
                    source
                        .fetch_batch_at_revision_filtered(
                            &window,
                            cursor.as_ref(),
                            batch_size,
                            live_continuation_sequence,
                            event_type_filter,
                        )
                        .await
                };
                match fetched {
                    Ok((events, next)) => {
                        let count = events.len();
                        if count > 0 && sender.send(Ok(events)).await.is_err() {
                            return;
                        }
                        if count < batch_size || next.is_none() {
                            return;
                        }
                        cursor = next;
                    }
                    Err(error) => {
                        let _ = sender.send(Err(error)).await;
                        return;
                    }
                }
            }
        });
        receiver
    }

    async fn enforce_byte_limit(&self) {
        let mut index = self.inner.lock().await;
        loop {
            let total = index
                .entries
                .values()
                .map(|entry| entry.estimated_bytes.load(Ordering::Relaxed) as usize)
                .sum::<usize>();
            if total <= self.config.cache_max_bytes {
                break;
            }
            let Some(position) = index.order.iter().position(|candidate| {
                index
                    .entries
                    .get(candidate)
                    .is_some_and(|entry| entry.complete.load(Ordering::Acquire))
            }) else {
                break;
            };
            let Some(oldest) = index.order.remove(position) else {
                break;
            };
            if let Some(entry) = index.entries.remove(&oldest) {
                entry.release_accounting();
                self.stats.evictions.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

fn structure_seed_cache_key(
    checkpoint_set_id: &str,
    ticker: &str,
    rebuild_start: DateTime<Utc>,
    before: DateTime<Utc>,
    source_revision_token: &str,
    split_adjustments: &[qmd_core::generic_structure::StructureSplitAdjustment],
) -> Result<String, String> {
    structure_seed_cache_key_for_revision(
        checkpoint_set_id,
        ticker,
        rebuild_start,
        before,
        source_revision_token,
        split_adjustments,
        &format!(
            "qmd-structure-v{GENERIC_STRUCTURE_ALGORITHM_VERSION}-{STRUCTURE_HOLD_SCORE_REVISION}"
        ),
    )
}

fn structure_seed_cache_key_for_revision(
    checkpoint_set_id: &str,
    ticker: &str,
    rebuild_start: DateTime<Utc>,
    before: DateTime<Utc>,
    source_revision_token: &str,
    split_adjustments: &[qmd_core::generic_structure::StructureSplitAdjustment],
    structure_revision: &str,
) -> Result<String, String> {
    let split_bytes = serde_json::to_vec(split_adjustments)
        .map_err(|error| format!("failed to hash structure split authority: {error}"))?;
    let split_revision = format!("{:x}", Sha256::digest(split_bytes));
    Ok(format!(
        "{}:{}:{}:{}:{}:{}:{}:{}",
        checkpoint_set_id,
        ticker.trim().to_ascii_uppercase(),
        rebuild_start.timestamp_micros(),
        before.timestamp_micros(),
        source_revision_token,
        structure_revision,
        HISTORICAL_CORPORATE_ACTION_REVISION,
        split_revision,
    ))
}

fn structure_events_overlapping(
    events: &[GenericStructureEvent],
    first_bar_start: DateTime<Utc>,
    last_bar_end: DateTime<Utc>,
    as_of: DateTime<Utc>,
) -> Vec<GenericStructureEvent> {
    let terminal_before_window = events
        .iter()
        .filter(|event| {
            chart_structure_event(event)
                && event.confirmed_at < first_bar_start
                && event.confirmed_at <= as_of
                && matches!(
                    event.event_kind.as_str(),
                    "structure_break" | "bos" | "choch"
                )
        })
        .map(|event| event.level_id)
        .collect::<std::collections::HashSet<_>>();
    let mut latest_promotion = HashMap::<(String, i8), &GenericStructureEvent>::new();
    for event in events.iter().filter(|event| {
        chart_structure_event(event)
            && event.event_kind == "level_promoted"
            && event.confirmed_at < first_bar_start
            && event.confirmed_at <= as_of
    }) {
        latest_promotion.insert((event.timeframe.clone(), event.direction), event);
    }
    let mut selected = latest_promotion
        .into_values()
        .filter(|event| !terminal_before_window.contains(&event.level_id))
        .cloned()
        .collect::<Vec<_>>();
    selected.extend(
        events
            .iter()
            .filter(|event| {
                chart_structure_event(event)
                    && event.confirmed_at >= first_bar_start
                    && event.confirmed_at <= last_bar_end
                    && event.confirmed_at <= as_of
            })
            .cloned(),
    );
    selected.sort_by_key(|event| (event.confirmed_at, event.event_id));
    selected.dedup_by_key(|event| event.event_id);
    selected
}

fn chart_structure_event(event: &GenericStructureEvent) -> bool {
    matches!(
        event.event_kind.as_str(),
        "level_promoted" | "structure_crossed" | "structure_break" | "bos" | "choch"
    )
}

impl CacheEntry {
    fn release_accounting(&self) {
        let _guard = self
            .accounting_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if self.accounted.swap(false, Ordering::AcqRel) {
            let bytes = self.estimated_bytes.swap(0, Ordering::AcqRel);
            if bytes > 0 {
                self.allocated_bytes.fetch_sub(bytes, Ordering::AcqRel);
            }
        }
    }

    pub fn subscribe(&self) -> broadcast::Receiver<DerivedUpdate> {
        self.updates.subscribe()
    }

    pub fn subscribe_bars(&self) -> broadcast::Receiver<BarUpdate> {
        self.bar_updates.subscribe()
    }

    pub async fn current(&self) -> (Vec<DerivedUpdate>, bool, Option<String>, u64) {
        let state = self.state.lock().await;
        (
            state.frames.clone(),
            state.complete,
            state.error.clone(),
            state.events_processed,
        )
    }

    pub async fn current_bars(&self) -> (Vec<BarUpdate>, bool, Option<String>, u64) {
        let state = self.state.lock().await;
        (
            state.bars.clone(),
            state.complete,
            state.error.clone(),
            state.events_processed,
        )
    }

    async fn push_bar(&self, bar: BarRow) -> Result<u64, String> {
        let mut state = self.state.lock().await;
        ensure_monotonic_bar_start(
            state.bars.last().map(|update| update.bar.bar_start),
            bar.bar_start,
        )?;
        let update_count = state.bars.len().saturating_add(1);
        let frame_bytes = estimated_frame_bytes(
            update_count,
            state.structure_events.len(),
            state.market_signal_events.len(),
        );
        if state.bars.len() >= self.max_updates || frame_bytes > self.max_update_bytes {
            return Err(format!(
                "historical derived entry exceeded cache limit: updates={} max_updates={} estimated_bytes={} max_update_bytes={}",
                update_count,
                self.max_updates,
                frame_bytes,
                self.max_update_bytes,
            ));
        }
        self.set_estimated_bytes(frame_bytes as u64 + self.product_bytes.load(Ordering::Acquire))?;
        self.frame_bytes
            .store(frame_bytes as u64, Ordering::Release);
        let sequence = state.bars.len() as u64 + 1;
        let update = BarUpdate { bar, sequence };
        state.bars.push(update.clone());
        drop(state);
        let _ = self.bar_updates.send(update);
        Ok(sequence)
    }

    async fn store_structure_projection(&self, projection: Vec<Value>) -> Result<(), String> {
        let projection_bytes = serde_json::to_vec(&projection)
            .map_err(|error| format!("failed to size unified structure timeline: {error}"))?
            .len();
        let mut state = self.state.lock().await;
        let frame_bytes = estimated_frame_bytes(
            state.bars.len(),
            state.structure_events.len(),
            state.market_signal_events.len(),
        )
        .saturating_add(projection_bytes);
        if frame_bytes > self.max_update_bytes {
            return Err(format!(
                "historical unified structure timeline exceeded max_update_bytes={}",
                self.max_update_bytes,
            ));
        }
        self.set_estimated_bytes(frame_bytes as u64 + self.product_bytes.load(Ordering::Acquire))?;
        self.frame_bytes
            .store(frame_bytes as u64, Ordering::Release);
        state.structure_projection = projection;
        Ok(())
    }

    async fn push_indicator(
        &self,
        sequence: u64,
        bar: BarRow,
        indicator: IndicatorRow,
    ) -> Result<(), String> {
        let mut state = self.state.lock().await;
        let expected = state.frames.len() as u64 + 1;
        if sequence != expected {
            return Err(format!(
                "historical indicator sequence gap: expected={expected} received={sequence}"
            ));
        }
        let update = DerivedUpdate {
            as_of: bar.bar_end,
            bar,
            indicator: indicator.compact_for_historical_cache(),
            sequence,
            update_type: "update",
        };
        state.frames.push(update.clone());
        drop(state);
        let _ = self.updates.send(update);
        Ok(())
    }

    async fn push_structure_events(&self, events: &[GenericStructureEvent]) -> Result<(), String> {
        if events.is_empty() {
            return Ok(());
        }
        let mut state = self.state.lock().await;
        let original_len = state.structure_events.len();
        for event in events.iter().filter(|event| {
            matches!(
                event.event_kind.as_str(),
                "level_promoted" | "level_crossed" | "structure_break" | "bos" | "choch"
            )
        }) {
            if state
                .structure_events
                .last()
                .is_some_and(|previous| event.confirmed_at < previous.confirmed_at)
            {
                state.structure_events.truncate(original_len);
                return Err(format!(
                    "historical QMD structure events must be chronological: previous={} next={}",
                    state.structure_events.last().unwrap().confirmed_at,
                    event.confirmed_at,
                ));
            }
            if state
                .structure_events
                .iter()
                .rev()
                .take(events.len().max(16))
                .any(|previous| previous.event_id == event.event_id)
            {
                continue;
            }
            if state.structure_events.len() >= self.max_updates {
                state.structure_events.truncate(original_len);
                return Err(format!(
                    "historical QMD structure event cache exceeded max_updates={}",
                    self.max_updates,
                ));
            }
            state.structure_events.push(event.clone());
        }
        let frame_bytes = estimated_frame_bytes(
            state.bars.len(),
            state.structure_events.len(),
            state.market_signal_events.len(),
        );
        if frame_bytes > self.max_update_bytes {
            state.structure_events.truncate(original_len);
            return Err(format!(
                "historical QMD structure event cache exceeded max_update_bytes={}",
                self.max_update_bytes,
            ));
        }
        if let Err(error) = self
            .set_estimated_bytes(frame_bytes as u64 + self.product_bytes.load(Ordering::Acquire))
        {
            state.structure_events.truncate(original_len);
            return Err(error);
        }
        self.frame_bytes
            .store(frame_bytes as u64, Ordering::Release);
        Ok(())
    }

    async fn push_market_signal_event(&self, event: MarketSignalEvent) -> Result<(), String> {
        let mut state = self.state.lock().await;
        if state
            .market_signal_events
            .last()
            .is_some_and(|previous| event.effective_at < previous.effective_at)
        {
            return Err(format!(
                "historical market signal events must be chronological: previous={} next={}",
                state.market_signal_events.last().unwrap().effective_at,
                event.effective_at,
            ));
        }
        if state.market_signal_events.len() >= self.max_updates {
            return Err(format!(
                "historical market signal event cache exceeded max_updates={}",
                self.max_updates,
            ));
        }
        state.market_signal_events.push(event);
        let frame_bytes = estimated_frame_bytes(
            state.bars.len(),
            state.structure_events.len(),
            state.market_signal_events.len(),
        );
        if frame_bytes > self.max_update_bytes {
            state.market_signal_events.pop();
            return Err(format!(
                "historical market signal event cache exceeded max_update_bytes={}",
                self.max_update_bytes,
            ));
        }
        if let Err(error) = self
            .set_estimated_bytes(frame_bytes as u64 + self.product_bytes.load(Ordering::Acquire))
        {
            state.market_signal_events.pop();
            return Err(error);
        }
        self.frame_bytes
            .store(frame_bytes as u64, Ordering::Release);
        Ok(())
    }

    fn set_product_bytes(&self, bytes: usize) -> Result<(), String> {
        let bytes = bytes as u64;
        self.set_estimated_bytes(self.frame_bytes.load(Ordering::Acquire) + bytes)?;
        self.product_bytes.store(bytes, Ordering::Release);
        Ok(())
    }

    fn set_estimated_bytes(&self, next: u64) -> Result<(), String> {
        // Bar production and indicator/structure production run concurrently
        // for one entry. Serialize the per-entry absolute reservation update;
        // otherwise both workers can observe the same previous value, reserve
        // both deltas globally, and overwrite one another's entry total. That
        // drift pins the service-wide counter at its byte ceiling even after
        // every visible entry has been evicted.
        let _guard = self
            .accounting_lock
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !self.accounted.load(Ordering::Acquire) {
            return Err("historical cache entry was evicted".to_string());
        }
        let previous = self.estimated_bytes.load(Ordering::Acquire);
        if next == previous {
            return Ok(());
        }
        if next < previous {
            self.allocated_bytes
                .fetch_sub(previous - next, Ordering::AcqRel);
            self.estimated_bytes.store(next, Ordering::Release);
            return Ok(());
        }
        let delta = next - previous;
        let mut allocated = self.allocated_bytes.load(Ordering::Acquire);
        loop {
            let Some(candidate) = allocated.checked_add(delta) else {
                return Err("historical cache byte accounting overflowed".to_string());
            };
            if candidate > self.global_max_bytes {
                return Err(format!(
                    "historical cache byte limit exceeded: requested_bytes={} allocated_bytes={} max_bytes={}",
                    delta, allocated, self.global_max_bytes,
                ));
            }
            match self.allocated_bytes.compare_exchange_weak(
                allocated,
                candidate,
                Ordering::AcqRel,
                Ordering::Acquire,
            ) {
                Ok(_) => {
                    self.estimated_bytes.store(next, Ordering::Release);
                    return Ok(());
                }
                Err(current) => allocated = current,
            }
        }
    }

    async fn wait_complete(&self) -> Result<(Vec<DerivedUpdate>, u64), String> {
        let events_processed = self.wait_ready().await?;
        let state = self.state.lock().await;
        Ok((state.frames.clone(), events_processed))
    }

    async fn wait_bars_ready(&self) -> Result<u64, String> {
        loop {
            let notified = self.notify.notified();
            {
                let state = self.state.lock().await;
                if state.bars_ready {
                    return Ok(state.events_processed);
                }
                if state.complete {
                    if let Some(error) = &state.error {
                        return Err(error.clone());
                    }
                    return Ok(state.events_processed);
                }
            }
            notified.await;
        }
    }

    async fn wait_ready(&self) -> Result<u64, String> {
        loop {
            let notified = self.notify.notified();
            {
                let state = self.state.lock().await;
                if state.complete {
                    if let Some(error) = &state.error {
                        return Err(error.clone());
                    }
                    return Ok(state.events_processed);
                }
            }
            notified.await;
        }
    }
}

fn estimated_frame_bytes(
    bar_count: usize,
    structure_event_count: usize,
    market_signal_event_count: usize,
) -> usize {
    bar_count.saturating_mul(size_of::<BarUpdate>() + size_of::<DerivedUpdate>() + 768)
        + structure_event_count.saturating_mul(size_of::<GenericStructureEvent>() + 224)
        + market_signal_event_count.saturating_mul(size_of::<MarketSignalEvent>() + 384)
}

fn ensure_monotonic_bar_start(
    previous: Option<DateTime<Utc>>,
    next: DateTime<Utc>,
) -> Result<(), String> {
    if let Some(previous) = previous {
        if next <= previous {
            return Err(format!(
                "historical chart bars must be strictly chronological: previous={previous} next={next}",
            ));
        }
    }
    Ok(())
}

impl ChartBarRow {
    fn from_bar(bar: &BarRow) -> Self {
        Self {
            schema_version: bar.schema_version,
            session_date: bar.session_date.clone(),
            timeframe: bar.timeframe.clone(),
            sym: bar.sym.clone(),
            bar_start: bar.bar_start,
            bar_end: bar.bar_end,
            is_closed: bar.is_closed,
            open: bar.open,
            high: bar.high,
            low: bar.low,
            close: bar.close,
            volume: bar.volume,
            dollar_volume: Some(bar.dollar_volume),
            trade_count: Some(bar.trade_count),
            spread_bps_close: Some(bar.spread_bps_close),
            spread_bps_mean: Some(bar.spread_bps_mean),
            vwap: Some(bar.vwap),
            estimated_luld_active: bar.estimated_luld_active,
            estimated_luld_reference_price: bar.estimated_luld_reference_price,
            estimated_luld_lower_price: bar.estimated_luld_lower_price,
            estimated_luld_upper_price: bar.estimated_luld_upper_price,
            estimated_luld_distance_to_upper_pct: bar.estimated_luld_distance_to_upper_pct,
            estimated_luld_distance_to_lower_pct: bar.estimated_luld_distance_to_lower_pct,
            estimated_luld_state: bar.estimated_luld_state.clone(),
        }
    }

    fn from_family(bar: &FamilyBarRow, timeframe: &str) -> Self {
        Self {
            schema_version: bar.schema_version,
            session_date: bar.local_date.clone(),
            timeframe: timeframe.to_string(),
            sym: bar.ticker.clone(),
            bar_start: bar.bar_start,
            bar_end: bar.bar_end,
            is_closed: !matches!(bar.state, ProductState::Partial),
            open: f64::from(bar.open),
            high: f64::from(bar.high),
            low: f64::from(bar.low),
            close: f64::from(bar.close),
            volume: bar.size_sum,
            dollar_volume: None,
            trade_count: Some(bar.event_count),
            spread_bps_close: None,
            spread_bps_mean: None,
            vwap: None,
            estimated_luld_active: false,
            estimated_luld_reference_price: 0.0,
            estimated_luld_lower_price: 0.0,
            estimated_luld_upper_price: 0.0,
            estimated_luld_distance_to_upper_pct: 0.0,
            estimated_luld_distance_to_lower_pct: 0.0,
            estimated_luld_state: "unavailable".to_string(),
        }
    }
}

impl Drop for CacheEntry {
    fn drop(&mut self) {
        self.release_accounting();
    }
}

fn split_event_window(window: &EventWindow, chunk_hours: usize) -> Vec<EventWindow> {
    let step = Duration::hours(chunk_hours.max(1) as i64);
    let mut chunks = Vec::new();
    let mut start = window.start;
    while start < window.end {
        let end = (start + step).min(window.end);
        chunks.push(EventWindow {
            start,
            end,
            tickers: window.tickers.clone(),
        });
        start = end;
    }
    chunks
}

fn prepared_bar_chart_snapshot(
    artifact: &PreparedBarCacheArtifact,
    cache: CacheEvidence,
    ticker: String,
    timeframe: String,
    limit: usize,
    as_of: DateTime<Utc>,
    before: Option<DateTime<Utc>>,
) -> ChartSnapshot {
    let mut selected_indices = artifact
        .bars
        .iter()
        .enumerate()
        .rev()
        .filter(|(_, bar)| bar.bar_end <= as_of && before.is_none_or(|bound| bar.bar_start < bound))
        .take(limit.saturating_add(1))
        .map(|(index, _)| index)
        .collect::<Vec<_>>();
    let has_more = selected_indices.len() > limit;
    selected_indices.truncate(limit);
    selected_indices.reverse();
    let selected = selected_indices
        .iter()
        .map(|index| artifact.bars[*index].clone())
        .collect::<Vec<_>>();
    let next_before = has_more.then(|| selected[0].bar_start);
    let indicator_projection = selected_indices
        .first()
        .zip(selected_indices.last())
        .map(|(first, last)| prepared_indicator_projection(artifact, *first, *last))
        .filter(|rows| !rows.is_empty());
    ChartSnapshot {
        as_of,
        bars: selected,
        cache,
        has_more,
        indicators: Vec::new(),
        indicator_projection,
        indicators_available: true,
        market_signal_events: Vec::new(),
        next_before,
        structure_events: Vec::new(),
        structure_level_history: Vec::new(),
        ticker,
        timeframe,
    }
}

fn prepared_indicator_projection(
    artifact: &PreparedBarCacheArtifact,
    first_index: usize,
    last_index: usize,
) -> Vec<Value> {
    if artifact.bar_indicator_projection.len() != artifact.bars.len() {
        return Vec::new();
    }
    let structure = prepared_structure_projection(artifact, first_index, last_index);
    artifact.bar_indicator_projection[first_index..=last_index]
        .iter()
        .cloned()
        .zip(structure)
        .map(|(mut indicator, structure)| {
            if let (Some(target), Some(source)) = (indicator.as_object_mut(), structure.as_object())
            {
                // The compact structure projection is the sole authority for
                // level-book state on chart rows. Older prepared bar payloads
                // carried a full per-bar snapshot in the generic indicator
                // projection; leaving it beside a delta makes the client
                // resurrect that stale, fragmented presentation after an
                // otherwise presentation-only settings update.
                target.remove("qmd_structure_unified_levels");
                target.remove("qmd_structure_unified_level_delta");
                source.iter().for_each(|(key, value)| {
                    target.insert(key.clone(), value.clone());
                });
            }
            indicator
        })
        .collect()
}

fn prepared_structure_projection(
    artifact: &PreparedBarCacheArtifact,
    first_index: usize,
    last_index: usize,
) -> Vec<Value> {
    if artifact.structure_projection.len() != artifact.bars.len() || first_index > last_index {
        return Vec::new();
    }
    let mut active = BTreeMap::<String, Value>::new();
    let mut projected = Vec::with_capacity(last_index - first_index + 1);
    for (index, row) in artifact
        .structure_projection
        .iter()
        .enumerate()
        .take(last_index + 1)
    {
        apply_structure_projection_row(&mut active, row);
        if index < first_index {
            continue;
        }
        if index == first_index || index == last_index {
            projected.push(json!({
                "bar_start": artifact.bars[index].bar_start,
                "qmd_structure_unified_levels": active.values().cloned().collect::<Vec<_>>(),
            }));
        } else {
            projected.push(row.clone());
        }
    }
    projected
}

fn apply_structure_projection_row(active: &mut BTreeMap<String, Value>, row: &Value) {
    let Some(object) = row.as_object() else {
        return;
    };
    if let Some(levels) = object
        .get("qmd_structure_unified_levels")
        .and_then(Value::as_array)
    {
        active.clear();
        for level in levels {
            if let Some(identity) = unified_structure_identity(level) {
                active.insert(identity, level.clone());
            }
        }
        return;
    }
    let Some(delta) = object
        .get("qmd_structure_unified_level_delta")
        .and_then(Value::as_object)
    else {
        return;
    };
    if let Some(removed) = delta.get("removed").and_then(Value::as_array) {
        for level in removed {
            if let Some(identity) = unified_structure_identity(level) {
                active.remove(&identity);
            }
        }
    }
    if let Some(upserts) = delta.get("upserts").and_then(Value::as_array) {
        for level in upserts {
            if let Some(identity) = unified_structure_identity(level) {
                active.insert(identity, level.clone());
            }
        }
    }
}

fn unified_structure_identity(level: &Value) -> Option<String> {
    Some(format!(
        "{}:{}",
        level.get("unified_level_id")?,
        level.get("side")?,
    ))
}

fn prepared_bar_cache_path(root: &Path, key: &str) -> PathBuf {
    root.join(format!(
        "v{PREPARED_BAR_CACHE_SCHEMA_VERSION}-{}.json",
        stable_hash_hex(key)
    ))
}

fn prepared_structure_seed_cache_path(root: &Path, key: &str) -> PathBuf {
    root.join(format!(
        "v{PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION}-{}.json",
        stable_hash_hex(key)
    ))
}

fn indicator_warmup_cache_path(
    root: &Path,
    ticker: &str,
    timeframe: &str,
    session_start: DateTime<Utc>,
) -> PathBuf {
    let session_date = session_start.with_timezone(&New_York).date_naive();
    root.join("indicator-warmups")
        .join(session_date.to_string())
        .join(timeframe)
        .join(format!(
            "v{INDICATOR_WARMUP_CACHE_SCHEMA_VERSION}-{ticker}.json"
        ))
}

fn read_indicator_warmup_cache(
    path: &Path,
    expected_ticker: &str,
    expected_timeframe: &str,
    expected_session_start: DateTime<Utc>,
) -> Result<Option<IndicatorWarmupArtifact>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("failed to read {}: {error}", path.display())),
    };
    let artifact = serde_json::from_slice::<IndicatorWarmupArtifact>(&bytes)
        .map_err(|error| format!("failed to decode {}: {error}", path.display()))?;
    if artifact.schema_version != INDICATOR_WARMUP_CACHE_SCHEMA_VERSION
        || !artifact.ticker.eq_ignore_ascii_case(expected_ticker)
        || !artifact.timeframe.eq_ignore_ascii_case(expected_timeframe)
        || artifact.session_start != expected_session_start
        || artifact
            .bars
            .windows(2)
            .any(|pair| pair[0].bar_start >= pair[1].bar_start)
        || artifact.bars.iter().any(|bar| {
            !bar.close.is_finite() || bar.close <= 0.0 || bar.bar_start >= expected_session_start
        })
    {
        return Err(format!(
            "{} contains an incompatible indicator warm-up",
            path.display()
        ));
    }
    Ok(Some(artifact))
}

fn write_indicator_warmup_cache(
    path: &Path,
    artifact: &IndicatorWarmupArtifact,
) -> Result<(), String> {
    let parent = path
        .parent()
        .ok_or_else(|| format!("indicator warm-up path has no parent: {}", path.display()))?;
    fs::create_dir_all(parent)
        .map_err(|error| format!("failed to create {}: {error}", parent.display()))?;
    let bytes = serde_json::to_vec(artifact)
        .map_err(|error| format!("failed to serialize indicator warm-up: {error}"))?;
    let temporary = parent.join(format!(
        ".{}.{}.tmp",
        path.file_name().unwrap_or_default().to_string_lossy(),
        std::process::id(),
    ));
    fs::write(&temporary, bytes)
        .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
    if path.exists() {
        fs::remove_file(path)
            .map_err(|error| format!("failed to replace {}: {error}", path.display()))?;
    }
    fs::rename(&temporary, path).map_err(|error| {
        let _ = fs::remove_file(&temporary);
        format!(
            "failed to promote {} to {}: {error}",
            temporary.display(),
            path.display()
        )
    })
}

fn read_prepared_structure_seed_cache(
    path: &Path,
    expected_key: &str,
    expected_ticker: &str,
) -> Result<Option<PreparedStructureSeedCacheArtifact>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(format!("failed to read {}: {error}", path.display())),
    };
    let artifact = serde_json::from_slice::<PreparedStructureSeedCacheArtifact>(&bytes)
        .map_err(|error| format!("failed to decode {}: {error}", path.display()))?;
    if artifact.schema_version != PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION {
        return Err(format!(
            "{} has schema {}, expected {}",
            path.display(),
            artifact.schema_version,
            PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION,
        ));
    }
    if artifact.key != expected_key {
        return Err(format!(
            "{} does not match its cache identity",
            path.display()
        ));
    }
    if !artifact.ticker.eq_ignore_ascii_case(expected_ticker)
        || !artifact
            .checkpoint
            .sym
            .eq_ignore_ascii_case(expected_ticker)
        || artifact.checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
    {
        return Err(format!(
            "{} contains an incompatible checkpoint outside {expected_ticker}",
            path.display(),
        ));
    }
    Ok(Some(artifact))
}

fn write_prepared_structure_seed_cache(
    root: &Path,
    key: &str,
    bytes: &[u8],
    max_entries: usize,
) -> Result<bool, String> {
    fs::create_dir_all(root)
        .map_err(|error| format!("failed to create {}: {error}", root.display()))?;
    let path = prepared_structure_seed_cache_path(root, key);
    if path.is_file() {
        return Ok(false);
    }
    let temporary = root.join(format!(
        ".{}.{}.tmp",
        stable_hash_hex(key),
        std::process::id(),
    ));
    fs::write(&temporary, bytes)
        .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
    if let Err(error) = fs::rename(&temporary, &path) {
        let _ = fs::remove_file(&temporary);
        if path.is_file() {
            return Ok(false);
        }
        return Err(format!(
            "failed to promote {} to {}: {error}",
            temporary.display(),
            path.display(),
        ));
    }
    let prefix = format!("v{PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION}-");
    let mut artifacts = fs::read_dir(root)
        .map_err(|error| format!("failed to inspect {}: {error}", root.display()))?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry.file_name().to_string_lossy().starts_with(&prefix)
                && entry
                    .path()
                    .extension()
                    .is_some_and(|extension| extension == "json")
        })
        .collect::<Vec<_>>();
    artifacts.sort_by_key(|entry| {
        entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
    });
    let remove_count = artifacts.len().saturating_sub(max_entries.max(1));
    for entry in artifacts.into_iter().take(remove_count) {
        if entry.path() != path {
            fs::remove_file(entry.path())
                .map_err(|error| format!("failed to prune prepared structure seeds: {error}"))?;
        }
    }
    Ok(true)
}

fn read_prepared_bar_cache(
    path: &Path,
    expected_key: &str,
    expected_ticker: &str,
    expected_timeframe: &str,
) -> Result<Option<PreparedBarCacheArtifact>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => {
            return Err(format!("failed to read {}: {error}", path.display()));
        }
    };
    let artifact = serde_json::from_slice::<PreparedBarCacheArtifact>(&bytes)
        .map_err(|error| format!("failed to decode {}: {error}", path.display()))?;
    if artifact.schema_version != PREPARED_BAR_CACHE_SCHEMA_VERSION {
        return Err(format!(
            "{} has schema {}, expected {}",
            path.display(),
            artifact.schema_version,
            PREPARED_BAR_CACHE_SCHEMA_VERSION,
        ));
    }
    if artifact.key != expected_key {
        return Err(format!(
            "{} does not match its cache identity",
            path.display()
        ));
    }
    let mut previous = None;
    for bar in &artifact.bars {
        if !bar.sym.eq_ignore_ascii_case(expected_ticker)
            || !bar.timeframe.eq_ignore_ascii_case(expected_timeframe)
        {
            return Err(format!(
                "{} contains a row outside {expected_ticker} {expected_timeframe}",
                path.display(),
            ));
        }
        ensure_monotonic_bar_start(previous, bar.bar_start)?;
        if !bar.is_closed || bar.bar_end <= bar.bar_start {
            return Err(format!("{} contains an invalid closed bar", path.display()));
        }
        previous = Some(bar.bar_start);
    }
    Ok(Some(artifact))
}

fn write_prepared_bar_cache(
    root: &Path,
    key: &str,
    bytes: &[u8],
    max_entries: usize,
) -> Result<bool, String> {
    fs::create_dir_all(root)
        .map_err(|error| format!("failed to create {}: {error}", root.display()))?;
    let path = prepared_bar_cache_path(root, key);
    if path.is_file() {
        return Ok(false);
    }
    let temporary = root.join(format!(
        ".{}.{}.tmp",
        stable_hash_hex(key),
        std::process::id(),
    ));
    fs::write(&temporary, bytes)
        .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
    if let Err(error) = fs::rename(&temporary, &path) {
        let _ = fs::remove_file(&temporary);
        if path.is_file() {
            return Ok(false);
        }
        return Err(format!(
            "failed to promote {} to {}: {error}",
            temporary.display(),
            path.display(),
        ));
    }

    let mut artifacts = fs::read_dir(root)
        .map_err(|error| format!("failed to inspect {}: {error}", root.display()))?
        .filter_map(Result::ok)
        .filter(|entry| {
            entry
                .file_name()
                .to_string_lossy()
                .starts_with(&format!("v{PREPARED_BAR_CACHE_SCHEMA_VERSION}-"))
                && entry
                    .path()
                    .extension()
                    .is_some_and(|extension| extension == "json")
        })
        .collect::<Vec<_>>();
    artifacts.sort_by_key(|entry| {
        entry
            .metadata()
            .and_then(|metadata| metadata.modified())
            .unwrap_or(std::time::SystemTime::UNIX_EPOCH)
    });
    let remove_count = artifacts.len().saturating_sub(max_entries.max(1));
    for entry in artifacts.into_iter().take(remove_count) {
        if entry.path() != path {
            fs::remove_file(entry.path())
                .map_err(|error| format!("failed to prune prepared-bar cache: {error}"))?;
        }
    }
    Ok(true)
}

fn revision_window(
    window: &EventWindow,
    profile: &CacheProfile,
    structure_rebuild_days: usize,
    structure_checkpoint_seeded: bool,
) -> Result<EventWindow, String> {
    // Structural books use their checkpoint horizon. Bar and derived profiles
    // calculate EMA/MACD from a bounded persisted-bar warm-up, so their source
    // revision must cover that same history; otherwise a repaired prior bar
    // could leave a stale prepared chart or strategy frame cache authoritative.
    let start = if matches!(profile, CacheProfile::Structure(_)) && structure_checkpoint_seeded {
        window.start
    } else if matches!(profile, CacheProfile::Structure(_)) {
        structure_rebuild_start(window.start, structure_rebuild_days)?
    } else if matches!(profile, CacheProfile::Bars(_) | CacheProfile::Derived(_)) {
        indicator_warmup_start(window.start)?
    } else {
        window.start
    };
    Ok(EventWindow {
        start,
        end: window.end,
        tickers: window.tickers.clone(),
    })
}

fn structure_seed_identity(seed: &PersistedStructureCheckpointSeed) -> Result<String, String> {
    let checkpoint_hash = checkpoint_sha256(&seed.checkpoint)?;
    Ok(stable_hash_hex(&format!(
        "{}:{}:{}:{}:{}",
        seed.authority_start.timestamp_micros(),
        seed.source_plan_hash,
        seed.source_revision_token,
        seed.checkpoint.last_arrival_sequence,
        checkpoint_hash,
    )))
}

fn cache_event_type_filter(_profile: &CacheProfile) -> Option<u8> {
    // Bars, derived products, and structural projections all require quotes.
    // Execution VWAP and structural pressure are defined from eligible trades
    // inside the causal prevailing NBBO, so a trade-only source optimization
    // changes both authorities rather than merely reducing transport volume.
    None
}

fn indicator_warmup_start(timestamp: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let lookback = timestamp
        .checked_sub_signed(Duration::days(INDICATOR_EMA_WARMUP_DAYS))
        .ok_or_else(|| "historical indicator warm-up underflow".to_string())?;
    session_anchor(lookback)
}

fn structure_rebuild_start(
    timestamp: DateTime<Utc>,
    rebuild_days: usize,
) -> Result<DateTime<Utc>, String> {
    let lookback = timestamp
        .checked_sub_signed(Duration::days(rebuild_days as i64))
        .ok_or_else(|| "historical structure rebuild lookback underflow".to_string())?;
    session_anchor(lookback)
}

fn session_anchor(timestamp: DateTime<Utc>) -> Result<DateTime<Utc>, String> {
    let local = timestamp.with_timezone(&New_York);
    let mut date = local.date_naive();
    if local.hour() < 4 {
        date = date
            .pred_opt()
            .ok_or_else(|| "historical session anchor underflow".to_string())?;
    }
    New_York
        .with_ymd_and_hms(date.year(), date.month(), date.day(), 4, 0, 0)
        .single()
        .map(|value| value.with_timezone(&Utc))
        .ok_or_else(|| format!("invalid America/New_York session anchor for {date}"))
}

fn cache_key(
    window: &EventWindow,
    ticker: &str,
    revision: &SourceRevision,
    profile: &CacheProfile,
) -> String {
    format!(
        "{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}",
        ticker.to_ascii_uppercase(),
        window.start.timestamp_micros(),
        window.end.timestamp_micros(),
        revision.token,
        HISTORICAL_ENGINE_VERSION,
        HISTORICAL_CALCULATION_REVISION,
        HISTORICAL_CORPORATE_ACTION_REVISION,
        BAR_SCHEMA_VERSION,
        INDICATOR_SCHEMA_VERSION,
        MARKET_PRODUCT_SCHEMA_VERSION,
        profile.key(),
    )
}

fn historical_requirement(
    key: &str,
    window: &EventWindow,
    ticker: &str,
    profile: &CacheProfile,
    revision: &SourceRevision,
) -> HistoricalComputationRequirement {
    let parameter_contract = format!(
        "{}|calculation:{}|corporate_action:{}|bar:{}|indicator:{}|product:{}|{}",
        HISTORICAL_ENGINE_VERSION,
        HISTORICAL_CALCULATION_REVISION,
        HISTORICAL_CORPORATE_ACTION_REVISION,
        BAR_SCHEMA_VERSION,
        INDICATOR_SCHEMA_VERSION,
        MARKET_PRODUCT_SCHEMA_VERSION,
        profile.key(),
    );
    HistoricalComputationRequirement {
        schema_version: 1,
        requirement_id: stable_hash_hex(key),
        scope: "offline".to_string(),
        product: profile.key(),
        ticker: ticker.to_ascii_uppercase(),
        timeframe: match profile {
            CacheProfile::Bars(timeframe)
            | CacheProfile::Derived(timeframe)
            | CacheProfile::Structure(timeframe) => Some(timeframe.clone()),
            CacheProfile::Products => None,
        },
        parameter_hash: stable_hash_hex(&parameter_contract),
        calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
        corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
        anchor_start: window.start,
        anchor_end: window.end,
        source_revision: revision.token.clone(),
        source_plan_hash: revision.source_plan_hash.clone(),
        state: "queued".to_string(),
        event_count: 0,
        estimated_bytes: 0,
    }
}

fn stable_hash_hex(value: &str) -> String {
    let mut hash = 0xcbf29ce484222325_u64;
    for byte in value.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(0x100000001b3);
    }
    format!("{hash:016x}")
}

fn touch(order: &mut VecDeque<String>, key: &str) {
    if let Some(index) = order.iter().position(|candidate| candidate == key) {
        order.remove(index);
    }
    order.push_back(key.to_string());
}

async fn forming_bar_from_events(
    mut events: Vec<MarketEvent>,
    ticker: &str,
    timeframe: &str,
    start: DateTime<Utc>,
    as_of: DateTime<Utc>,
    rules: qmd_core::bars::TradeAggregationRules,
) -> Result<Option<ChartBarRow>, String> {
    events.retain(|event| {
        event.availability_ts() <= as_of
            && event.execution_ts() >= start
            && event.execution_ts() <= as_of
    });
    events.sort_by_key(|event| event.execution_ts());
    let store = SharedBarStore::new_without_structure(vec![timeframe.to_string()], 2, 1, rules);
    let shard = store.shard(0);
    for event in events {
        shard.apply_event(&event.for_execution_time_chart()).await;
    }
    Ok(store
        .snapshot(ticker, timeframe, 1)
        .await
        .current
        .filter(valid_price_bar)
        .map(|bar| ChartBarRow::from_bar(&bar)))
}

fn valid_price_bar(bar: &BarRow) -> bool {
    [bar.open, bar.high, bar.low, bar.close]
        .into_iter()
        .all(|value| value.is_finite() && value > 0.0)
        && bar.high >= bar.open.max(bar.close)
        && bar.low <= bar.open.min(bar.close)
        && bar.high >= bar.low
}

#[cfg(test)]
mod tests {
    use super::{
        apply_structure_projection_row, bounded_encountered_structure_levels,
        cache_event_type_filter, cache_key, encountered_structure_levels_for_session,
        ensure_monotonic_bar_start, historical_requirement, indicator_warmup_cache_path,
        prepared_bar_cache_path, prepared_indicator_projection, prepared_structure_seed_cache_path,
        read_indicator_warmup_cache, read_prepared_bar_cache, read_prepared_structure_seed_cache,
        revision_window, session_anchor, split_event_window, stable_hash_hex,
        structure_events_overlapping, structure_seed_cache_key,
        structure_seed_cache_key_for_revision, write_indicator_warmup_cache,
        write_prepared_bar_cache, write_prepared_structure_seed_cache, CacheEntry, CacheProfile,
        ChartBarRow, EntryState, IndicatorWarmupArtifact, IndicatorWarmupBar,
        PreparedBarCacheArtifact, PreparedStructureSeedCacheArtifact, SourceRevision,
        StructureProjectionBuilder, GENERIC_STRUCTURE_ALGORITHM_VERSION,
        HISTORICAL_CALCULATION_REVISION, HISTORICAL_CORPORATE_ACTION_REVISION,
        HISTORICAL_ENGINE_VERSION, LEGACY_STRUCTURE_CALCULATION_REVISION,
        MAX_ENCOUNTERED_STRUCTURE_LEVELS, PREPARED_BAR_CACHE_SCHEMA_VERSION,
        PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION, STRUCTURE_HOLD_SCORE_REVISION,
    };
    use crate::source::EventWindow;
    use chrono::{DateTime, Duration, NaiveDate, TimeZone, Utc};
    use qmd_core::generic_structure::{
        GenericStructureEngine, GenericStructureEvent, StructureLevelCandidate,
        StructureSplitAdjustment,
    };
    use serde_json::json;
    use std::collections::BTreeMap;
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex as StdMutex};
    use tokio::sync::{broadcast, Mutex, Notify};

    #[tokio::test]
    async fn forming_candle_excludes_future_reports_and_preserves_open_bucket() {
        use qmd_core::event::{MarketEvent, TradeEvent};
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 11, 21, 31).unwrap();
        let trade = |availability_ms: i64, execution_ms: i64, price: f64| {
            MarketEvent::Trade(TradeEvent {
                conditions: vec![],
                exchange: 1,
                ingest_ts: start + Duration::milliseconds(availability_ms),
                participant_ts: Some(start + Duration::milliseconds(execution_ms)),
                price,
                raw: json!({}),
                sequence: availability_ms as u64,
                size: 100.0,
                tape: 1,
                ticker: "JUNS".to_string(),
                trade_id: availability_ms.to_string(),
                trf_id: 0,
                trf_ts: None,
                ts: start + Duration::milliseconds(availability_ms),
            })
        };
        let events = vec![
            trade(100, 100, 7.2),
            trade(200, 200, 7.3),
            trade(800, 250, 9.0),
            trade(250, -100, 1.0),
        ];
        let rules = qmd_core::bars::TradeAggregationRules::new([(
            0,
            qmd_core::bars::TradeUpdateRule::regular(),
        )])
        .unwrap();
        let early = super::forming_bar_from_events(
            events.clone(),
            "JUNS",
            "1s",
            start,
            start + Duration::milliseconds(300),
            rules.clone(),
        )
        .await
        .unwrap()
        .unwrap();
        assert!(!early.is_closed);
        assert_eq!(
            (early.open, early.high, early.low, early.close, early.volume),
            (7.2, 7.3, 7.2, 7.3, 200.0)
        );
        assert_eq!(early.bar_end, start + Duration::seconds(1));
        let later = super::forming_bar_from_events(
            events,
            "JUNS",
            "1s",
            start,
            start + Duration::milliseconds(900),
            rules,
        )
        .await
        .unwrap()
        .unwrap();
        assert_eq!((later.high, later.close, later.volume), (9.0, 9.0, 300.0));
    }

    #[test]
    fn all_causal_cache_profiles_fetch_quotes_required_by_their_authorities() {
        assert_eq!(
            cache_event_type_filter(&CacheProfile::Bars("1s".to_string())),
            None
        );
        assert_eq!(
            cache_event_type_filter(&CacheProfile::Derived("1s".to_string())),
            None
        );
        assert_eq!(
            cache_event_type_filter(&CacheProfile::Structure("1s".to_string())),
            None,
        );
    }

    #[test]
    fn prepared_structure_seed_identity_changes_with_split_authority() {
        let rebuild_start = Utc.with_ymd_and_hms(2026, 2, 22, 9, 0, 0).unwrap();
        let before = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let adjustment = StructureSplitAdjustment {
            execution_date: NaiveDate::from_ymd_opt(2026, 8, 1).unwrap(),
            effective_at: Utc.with_ymd_and_hms(2026, 8, 1, 8, 0, 0).unwrap(),
            split_from: 1.0,
            split_to: 2.0,
            source_inserted_at: Utc.with_ymd_and_hms(2026, 8, 1, 9, 0, 0).unwrap(),
        };
        let original = structure_seed_cache_key(
            "certified-set-v1",
            "SUGP",
            rebuild_start,
            before,
            "event-revision",
            std::slice::from_ref(&adjustment),
        )
        .unwrap();
        let mut corrected = adjustment.clone();
        corrected.source_inserted_at += Duration::hours(1);
        let corrected = structure_seed_cache_key(
            "certified-set-v1",
            "SUGP",
            rebuild_start,
            before,
            "event-revision",
            &[corrected],
        )
        .unwrap();
        let no_split = structure_seed_cache_key(
            "certified-set-v1",
            "SUGP",
            rebuild_start,
            before,
            "event-revision",
            &[],
        )
        .unwrap();
        let legacy = structure_seed_cache_key_for_revision(
            "certified-set-v1",
            "SUGP",
            rebuild_start,
            before,
            "event-revision",
            std::slice::from_ref(&adjustment),
            LEGACY_STRUCTURE_CALCULATION_REVISION,
        )
        .unwrap();

        assert_ne!(original, corrected);
        assert_ne!(original, no_split);
        assert_ne!(original, legacy);
        assert_ne!(
            original,
            structure_seed_cache_key(
                "another-certified-set",
                "SUGP",
                rebuild_start,
                before,
                "event-revision",
                std::slice::from_ref(&adjustment),
            )
            .unwrap()
        );
        assert!(original.contains(&format!(
            "qmd-structure-v{GENERIC_STRUCTURE_ALGORITHM_VERSION}-{STRUCTURE_HOLD_SCORE_REVISION}"
        )));
        assert!(legacy.contains(LEGACY_STRUCTURE_CALCULATION_REVISION));
    }

    #[test]
    fn indicator_warmup_artifact_is_persistent_and_identity_checked() {
        let root = std::env::temp_dir().join(format!(
            "qmd-indicator-warmup-test-{}-{}",
            std::process::id(),
            Utc::now().timestamp_nanos_opt().unwrap_or_default()
        ));
        let session_start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let path = indicator_warmup_cache_path(&root, "SUGP", "1s", session_start);
        let artifact = IndicatorWarmupArtifact {
            schema_version: 1,
            calculation_revision: HISTORICAL_CALCULATION_REVISION.to_string(),
            corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION.to_string(),
            ticker: "SUGP".to_string(),
            timeframe: "1s".to_string(),
            session_start,
            authority_start: session_start - Duration::days(1),
            required_bars: 2,
            bars: vec![
                IndicatorWarmupBar {
                    bar_start: session_start - Duration::seconds(2),
                    close: 3.40,
                },
                IndicatorWarmupBar {
                    bar_start: session_start - Duration::seconds(1),
                    close: 3.41,
                },
            ],
            fetched_events: 20,
            fetched_ordinal_ranges: 1,
            source_revision: SourceRevision {
                complete_for_history: true,
                event_count: 20,
                live_continuation_sequence: None,
                max_build_step: 1,
                max_updated_at: "2026-08-21T00:00:00Z".to_string(),
                request_complete: true,
                source_plan_hash: "test-plan".to_string(),
                source_tiers: vec!["archive".to_string()],
                token: "test-revision".to_string(),
            },
            status: "ready".to_string(),
            cache_hit: false,
        };
        write_indicator_warmup_cache(&path, &artifact).unwrap();
        let restored = read_indicator_warmup_cache(&path, "SUGP", "1s", session_start)
            .unwrap()
            .unwrap();
        assert_eq!(restored.bars.len(), 2);
        assert_eq!(restored.source_revision.token, "test-revision");
        assert!(read_indicator_warmup_cache(&path, "JUNS", "1s", session_start).is_err());
        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn structure_timeline_has_one_initial_and_one_terminal_authority() {
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let rows = StructureProjectionBuilder::new(GenericStructureEngine::new("SUGP"), start)
            .unwrap()
            .finish(start + Duration::seconds(2))
            .unwrap();

        assert_eq!(rows.len(), 2);
        assert!(rows.iter().all(|row| {
            row.get("qmd_structure_unified_levels").is_some()
                && row.get("qmd_structure_unified_level_delta").is_none()
        }));
    }

    #[test]
    fn indicator_and_structure_revisions_cover_their_causal_warm_starts() {
        let page = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 14, 18, 30, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 14, 20, 30, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let expected = Utc.with_ymd_and_hms(2026, 7, 14, 8, 0, 0).unwrap();

        assert_eq!(session_anchor(page.start).unwrap(), expected);
        assert_eq!(
            revision_window(&page, &CacheProfile::Derived("5m".to_string()), 7, false)
                .unwrap()
                .start,
            Utc.with_ymd_and_hms(2026, 7, 7, 8, 0, 0).unwrap()
        );
        assert_eq!(
            revision_window(&page, &CacheProfile::Structure("5m".to_string()), 7, false)
                .unwrap()
                .start,
            Utc.with_ymd_and_hms(2026, 7, 7, 8, 0, 0).unwrap()
        );
        assert_eq!(
            revision_window(&page, &CacheProfile::Products, 7, false)
                .unwrap()
                .start,
            page.start
        );
        assert_eq!(
            revision_window(&page, &CacheProfile::Bars("1s".to_string()), 7, false)
                .unwrap()
                .start,
            Utc.with_ymd_and_hms(2026, 7, 7, 8, 0, 0).unwrap()
        );
        assert_eq!(
            revision_window(&page, &CacheProfile::Structure("5m".to_string()), 7, true)
                .unwrap()
                .start,
            page.start
        );
    }

    #[test]
    fn cache_key_changes_with_source_revision_and_engine_contract() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 10, 8, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 11, 0, 0, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let first = SourceRevision {
            complete_for_history: true,
            event_count: 10,
            live_continuation_sequence: None,
            max_build_step: 1,
            max_updated_at: "2026-07-10 01:00:00".to_string(),
            request_complete: true,
            source_plan_hash: "plan-1".to_string(),
            source_tiers: vec!["archive".to_string()],
            token: "1:10:2026-07-10 01:00:00".to_string(),
        };
        let second = SourceRevision {
            token: "2:10:2026-07-10 02:00:00".to_string(),
            ..first.clone()
        };
        assert_ne!(
            cache_key(
                &window,
                "AAPL",
                &first,
                &CacheProfile::Derived("1m".to_string())
            ),
            cache_key(
                &window,
                "AAPL",
                &second,
                &CacheProfile::Derived("1m".to_string())
            )
        );
        assert!(cache_key(
            &window,
            "AAPL",
            &first,
            &CacheProfile::Derived("1m".to_string())
        )
        .contains(HISTORICAL_ENGINE_VERSION));
    }

    #[test]
    fn offline_requirement_includes_anchor_parameters_and_source_revision() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 17, 13, 30, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 17, 20, 0, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let revision = SourceRevision {
            complete_for_history: true,
            event_count: 10,
            live_continuation_sequence: None,
            max_build_step: 1,
            max_updated_at: "2026-07-18T00:00:00Z".to_string(),
            request_complete: true,
            source_plan_hash: "plan-17".to_string(),
            source_tiers: vec!["archive".to_string()],
            token: "revision-17".to_string(),
        };
        let requirement = historical_requirement(
            "exact-cache-key",
            &window,
            "aapl",
            &CacheProfile::Derived("1m".to_string()),
            &revision,
        );

        assert_eq!(requirement.scope, "offline");
        assert_eq!(requirement.ticker, "AAPL");
        assert_eq!(requirement.timeframe.as_deref(), Some("1m"));
        assert_eq!(requirement.anchor_start, window.start);
        assert_eq!(requirement.anchor_end, window.end);
        assert_eq!(requirement.source_revision, "revision-17");
        assert_eq!(requirement.source_plan_hash, "plan-17");
        assert_eq!(
            requirement.calculation_revision,
            HISTORICAL_CALCULATION_REVISION
        );
        assert_eq!(
            requirement.corporate_action_revision,
            HISTORICAL_CORPORATE_ACTION_REVISION
        );
        assert_eq!(
            requirement.requirement_id,
            stable_hash_hex("exact-cache-key")
        );
        assert_eq!(stable_hash_hex("abc"), "e71fa2190541574b");
    }

    #[test]
    fn cache_key_separates_derived_timeframes_from_product_builds() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 10, 8, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 10, 13, 45, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let revision = SourceRevision {
            complete_for_history: true,
            event_count: 10,
            live_continuation_sequence: None,
            max_build_step: 1,
            max_updated_at: "2026-07-10 13:45:00".to_string(),
            request_complete: true,
            source_plan_hash: "plan-1".to_string(),
            source_tiers: vec!["archive".to_string()],
            token: "1:10:2026-07-10 13:45:00".to_string(),
        };
        let one_minute = cache_key(
            &window,
            "AAPL",
            &revision,
            &CacheProfile::Derived("1m".to_string()),
        );
        let five_minute = cache_key(
            &window,
            "AAPL",
            &revision,
            &CacheProfile::Derived("5m".to_string()),
        );
        let one_minute_bars = cache_key(
            &window,
            "AAPL",
            &revision,
            &CacheProfile::Bars("1m".to_string()),
        );
        let products = cache_key(&window, "AAPL", &revision, &CacheProfile::Products);

        assert_ne!(one_minute, five_minute);
        assert_ne!(one_minute, one_minute_bars);
        assert_ne!(one_minute, products);
        assert_ne!(five_minute, products);
    }

    #[test]
    fn prepared_bar_cache_is_restart_safe_and_identity_checked() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "qmd-history-prepared-bars-{}-{nonce}",
            std::process::id()
        ));
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let artifact = PreparedBarCacheArtifact {
            schema_version: PREPARED_BAR_CACHE_SCHEMA_VERSION,
            key: "revisioned-bars-key".to_string(),
            event_count: 17,
            bars: vec![ChartBarRow {
                schema_version: 1,
                session_date: "2026-08-21".to_string(),
                timeframe: "1s".to_string(),
                sym: "SUGP".to_string(),
                bar_start: start,
                bar_end: start + Duration::seconds(1),
                is_closed: true,
                open: 3.5,
                high: 3.6,
                low: 3.5,
                close: 3.6,
                volume: 100.0,
                dollar_volume: Some(355.0),
                trade_count: Some(5),
                spread_bps_close: Some(28.0),
                spread_bps_mean: Some(30.0),
                vwap: Some(3.55),
                estimated_luld_active: false,
                estimated_luld_reference_price: 0.0,
                estimated_luld_lower_price: 0.0,
                estimated_luld_upper_price: 0.0,
                estimated_luld_distance_to_upper_pct: 0.0,
                estimated_luld_distance_to_lower_pct: 0.0,
                estimated_luld_state: "unavailable".to_string(),
            }],
            bar_indicator_projection: vec![json!({
                "bar_start": start,
                "vwap": 3.55,
            })],
            structure_projection: vec![json!({
                "bar_start": start,
                "qmd_structure_unified_levels": [],
            })],
        };
        let bytes = serde_json::to_vec(&artifact).unwrap();

        assert!(write_prepared_bar_cache(&root, &artifact.key, &bytes, 4).unwrap());
        let path = prepared_bar_cache_path(&root, &artifact.key);
        let restored = read_prepared_bar_cache(&path, &artifact.key, "SUGP", "1s")
            .unwrap()
            .unwrap();
        assert_eq!(restored.event_count, 17);
        assert_eq!(restored.bars.len(), 1);
        assert!(read_prepared_bar_cache(&path, "wrong-key", "SUGP", "1s").is_err());

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn prepared_structure_seed_is_restart_safe_and_identity_checked() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::SystemTime::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "qmd-history-prepared-structure-{}-{nonce}",
            std::process::id()
        ));
        let artifact = PreparedStructureSeedCacheArtifact {
            schema_version: PREPARED_STRUCTURE_SEED_CACHE_SCHEMA_VERSION,
            key: "revisioned-structure-key".to_string(),
            ticker: "SUGP".to_string(),
            checkpoint: GenericStructureEngine::new("SUGP").checkpoint(),
        };
        let bytes = serde_json::to_vec(&artifact).unwrap();

        assert!(write_prepared_structure_seed_cache(&root, &artifact.key, &bytes, 4).unwrap());
        let path = prepared_structure_seed_cache_path(&root, &artifact.key);
        let restored = read_prepared_structure_seed_cache(&path, &artifact.key, "SUGP")
            .unwrap()
            .unwrap();
        assert_eq!(restored.checkpoint.sym, "SUGP");
        assert!(read_prepared_structure_seed_cache(&path, "wrong-key", "SUGP").is_err());
        assert!(read_prepared_structure_seed_cache(&path, &artifact.key, "NOK").is_err());

        std::fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn prepared_structure_projection_rehydrates_full_state_and_deltas() {
        let first = json!({
            "bar_start": "2026-08-21T08:00:00Z",
            "qmd_structure_unified_levels": [{
                "unified_level_id": 1,
                "side": 1,
                "price": 3.5,
            }],
        });
        let transition = json!({
            "bar_start": "2026-08-21T08:00:01Z",
            "qmd_structure_unified_level_delta": {
                "removed": [{"unified_level_id": 1, "side": 1}],
                "upserts": [{"unified_level_id": 2, "side": -1, "price": 3.8}],
            },
        });
        let mut active = BTreeMap::new();

        apply_structure_projection_row(&mut active, &first);
        assert_eq!(active.len(), 1);
        assert_eq!(active.values().next().unwrap()["price"], json!(3.5));

        apply_structure_projection_row(&mut active, &transition);
        assert_eq!(active.len(), 1);
        assert_eq!(active.values().next().unwrap()["price"], json!(3.8));
    }

    #[test]
    fn prepared_indicator_projection_replaces_legacy_per_bar_structure_atomically() {
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let bar = |offset: i64| ChartBarRow {
            schema_version: 1,
            session_date: "2026-08-21".to_string(),
            timeframe: "1s".to_string(),
            sym: "SUGP".to_string(),
            bar_start: start + Duration::seconds(offset),
            bar_end: start + Duration::seconds(offset + 1),
            is_closed: true,
            open: 3.5,
            high: 3.6,
            low: 3.5,
            close: 3.6,
            volume: 100.0,
            dollar_volume: Some(355.0),
            trade_count: Some(5),
            spread_bps_close: Some(28.0),
            spread_bps_mean: Some(30.0),
            vwap: Some(3.55),
            estimated_luld_active: false,
            estimated_luld_reference_price: 0.0,
            estimated_luld_lower_price: 0.0,
            estimated_luld_upper_price: 0.0,
            estimated_luld_distance_to_upper_pct: 0.0,
            estimated_luld_distance_to_lower_pct: 0.0,
            estimated_luld_state: "unavailable".to_string(),
        };
        let legacy = |offset: i64| {
            json!({
                "bar_start": start + Duration::seconds(offset),
                "vwap": 3.55,
                "qmd_structure_unified_levels": [{"unified_level_id": 999, "side": 1}],
                "qmd_structure_unified_level_delta": {
                    "upserts": [{"unified_level_id": 998, "side": 1}],
                    "removed": [],
                },
            })
        };
        let artifact = PreparedBarCacheArtifact {
            schema_version: PREPARED_BAR_CACHE_SCHEMA_VERSION,
            key: "atomic-structure-projection".to_string(),
            event_count: 2,
            bars: vec![bar(0), bar(1)],
            bar_indicator_projection: vec![legacy(0), legacy(1)],
            structure_projection: vec![
                json!({
                    "bar_start": start,
                    "qmd_structure_unified_levels": [{"unified_level_id": 1, "side": 1}],
                }),
                json!({
                    "bar_start": start + Duration::seconds(1),
                    "qmd_structure_unified_level_delta": {
                        "upserts": [{"unified_level_id": 2, "side": -1}],
                        "removed": [{"unified_level_id": 1, "side": 1}],
                    },
                }),
            ],
        };

        let projected = prepared_indicator_projection(&artifact, 0, 1);

        assert_eq!(
            projected[0]["qmd_structure_unified_levels"][0]["unified_level_id"],
            1
        );
        assert!(projected[0]
            .get("qmd_structure_unified_level_delta")
            .is_none());
        assert_eq!(
            projected[1]["qmd_structure_unified_levels"][0]["unified_level_id"],
            2
        );
        assert!(projected[1]
            .get("qmd_structure_unified_level_delta")
            .is_none());
    }

    #[test]
    fn derived_revision_window_covers_ema_warmup_without_structural_horizon() {
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap();
        let end = Utc.with_ymd_and_hms(2026, 8, 21, 13, 30, 0).unwrap();
        let window = EventWindow {
            start,
            end,
            tickers: vec!["SUGP".to_string()],
        };

        let derived = revision_window(
            &window,
            &CacheProfile::Derived("1s".to_string()),
            180,
            false,
        )
        .unwrap();
        let structure = revision_window(
            &window,
            &CacheProfile::Structure("1s".to_string()),
            180,
            false,
        )
        .unwrap();

        assert_eq!(
            derived.start,
            Utc.with_ymd_and_hms(2026, 8, 14, 8, 0, 0).unwrap()
        );
        assert_eq!(derived.end, end);
        assert_eq!(
            structure.start,
            Utc.with_ymd_and_hms(2026, 2, 21, 9, 0, 0).unwrap()
        );
        assert_eq!(structure.end, end);
    }

    #[test]
    fn encountered_structure_history_keeps_latest_exact_identity_and_bounds_oldest() {
        let level =
            |created_at_ms: i64, side: i8, price: f64, total_volume: f64| StructureLevelCandidate {
                created_at_ms,
                side,
                price,
                footprint_session_date: "2026-07-24".to_string(),
                total_volume,
                ..StructureLevelCandidate::default()
            };
        let mut candidates = (0..=MAX_ENCOUNTERED_STRUCTURE_LEVELS)
            .map(|index| level(index as i64, 1, 100.0 + index as f64 * 0.01, index as f64))
            .collect::<Vec<_>>();
        candidates.push(level(
            MAX_ENCOUNTERED_STRUCTURE_LEVELS as i64,
            1,
            100.0 + MAX_ENCOUNTERED_STRUCTURE_LEVELS as f64 * 0.01,
            9_999.0,
        ));

        let history = bounded_encountered_structure_levels(candidates);

        assert_eq!(history.len(), MAX_ENCOUNTERED_STRUCTURE_LEVELS);
        assert_eq!(history.first().unwrap().created_at_ms, 1);
        assert_eq!(history.last().unwrap().total_volume, 9_999.0);
    }

    #[test]
    fn encountered_structure_history_does_not_merge_sessions() {
        let level = |session: &str, as_of_ms: i64| StructureLevelCandidate {
            created_at_ms: 100,
            side: 1,
            price: 45.0,
            footprint_session_date: session.to_string(),
            footprint_as_of_ms: as_of_ms,
            total_volume: as_of_ms as f64,
            ..StructureLevelCandidate::default()
        };

        let history = bounded_encountered_structure_levels([
            level("2026-07-16", 1_000),
            level("2026-07-17", 2_000),
        ]);

        assert_eq!(history.len(), 2);
        assert_eq!(history[0].footprint_session_date, "2026-07-16");
        assert_eq!(history[1].footprint_session_date, "2026-07-17");
    }

    #[test]
    fn encountered_structure_history_excludes_prior_session_levels() {
        let level = |session: &str, price: f64| StructureLevelCandidate {
            created_at_ms: 100,
            side: 1,
            price,
            footprint_session_date: session.to_string(),
            footprint_as_of_ms: 2_000,
            total_volume: 1_000.0,
            ..StructureLevelCandidate::default()
        };

        let history = encountered_structure_levels_for_session(
            "2026-07-17",
            [level("2026-07-16", 35.0), level("2026-07-17", 45.0)],
        );

        assert_eq!(history.len(), 1);
        assert_eq!(history[0].footprint_session_date, "2026-07-17");
        assert_eq!(history[0].price, 45.0);
    }

    #[test]
    fn cache_entry_reservations_enforce_the_service_byte_ceiling() {
        let allocated = Arc::new(AtomicU64::new(0));
        let (updates, _) = broadcast::channel(16);
        let (bar_updates, _) = broadcast::channel(16);
        let entry = CacheEntry {
            accounted: AtomicBool::new(true),
            accounting_lock: StdMutex::new(()),
            allocated_bytes: allocated.clone(),
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: 1_000,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            bar_updates,
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: 1_000,
            max_updates: 10,
            product_bytes: AtomicU64::new(0),
            requirement: None,
        };
        assert!(entry.set_estimated_bytes(900).is_ok());
        assert!(entry.set_estimated_bytes(1_001).is_err());
        assert_eq!(allocated.load(Ordering::Acquire), 900);
        entry.release_accounting();
        entry.release_accounting();
        assert_eq!(allocated.load(Ordering::Acquire), 0);
        assert!(entry.set_estimated_bytes(1).is_err());
        drop(entry);
        assert_eq!(allocated.load(Ordering::Acquire), 0);
    }

    #[test]
    fn concurrent_entry_updates_do_not_drift_global_byte_accounting() {
        let allocated = Arc::new(AtomicU64::new(0));
        let (updates, _) = broadcast::channel(16);
        let (bar_updates, _) = broadcast::channel(16);
        let entry = Arc::new(CacheEntry {
            accounted: AtomicBool::new(true),
            accounting_lock: StdMutex::new(()),
            allocated_bytes: allocated.clone(),
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: 1_000_000,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            bar_updates,
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: 1_000_000,
            max_updates: 10,
            product_bytes: AtomicU64::new(0),
            requirement: None,
        });
        let barrier = Arc::new(std::sync::Barrier::new(8));
        let workers = (0..8)
            .map(|worker| {
                let entry = entry.clone();
                let barrier = barrier.clone();
                std::thread::spawn(move || {
                    barrier.wait();
                    for offset in 1..=2_000_u64 {
                        entry.set_estimated_bytes(worker * 2_000 + offset).unwrap();
                    }
                })
            })
            .collect::<Vec<_>>();
        for worker in workers {
            worker.join().unwrap();
        }

        assert_eq!(
            allocated.load(Ordering::Acquire),
            entry.estimated_bytes.load(Ordering::Acquire),
        );
        entry.release_accounting();
        assert_eq!(allocated.load(Ordering::Acquire), 0);
    }

    #[tokio::test]
    async fn chart_bar_waiter_releases_before_indicator_completion() {
        let allocated = Arc::new(AtomicU64::new(0));
        let (updates, _) = broadcast::channel(16);
        let (bar_updates, _) = broadcast::channel(16);
        let entry = Arc::new(CacheEntry {
            accounted: AtomicBool::new(true),
            accounting_lock: StdMutex::new(()),
            allocated_bytes: allocated,
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: 100_000,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            bar_updates,
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: 100_000,
            max_updates: 100,
            product_bytes: AtomicU64::new(0),
            requirement: None,
        });
        let waiter = {
            let entry = entry.clone();
            tokio::spawn(async move { entry.wait_bars_ready().await })
        };
        tokio::task::yield_now().await;
        assert!(!waiter.is_finished());

        {
            let mut state = entry.state.lock().await;
            state.bars_ready = true;
            state.events_processed = 42;
        }
        entry.notify.notify_waiters();

        assert_eq!(waiter.await.unwrap().unwrap(), 42);
        assert!(!entry.complete.load(Ordering::Acquire));
    }

    #[tokio::test]
    async fn structure_events_are_retained_independently_across_timeframes() {
        let allocated = Arc::new(AtomicU64::new(0));
        let (updates, _) = broadcast::channel(16);
        let (bar_updates, _) = broadcast::channel(16);
        let entry = CacheEntry {
            accounted: AtomicBool::new(true),
            accounting_lock: StdMutex::new(()),
            allocated_bytes: allocated,
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: 100_000,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            bar_updates,
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: 100_000,
            max_updates: 100,
            product_bytes: AtomicU64::new(0),
            requirement: None,
        };
        let confirmed_at = Utc.with_ymd_and_hms(2026, 7, 17, 13, 30, 0).unwrap();
        let event = |event_id: u64, timeframe: &str| GenericStructureEvent {
            algorithm_version: 2,
            event_id,
            level_id: 11,
            sym: "VEEE".to_string(),
            timeframe: timeframe.to_string(),
            event_kind: "level_promoted".to_string(),
            direction: 1,
            price: 42.0,
            lower: 42.0,
            upper: 42.0,
            strength: 0.7,
            confidence: 0.8,
            lifecycle: "active".to_string(),
            total_volume: 500.0,
            buy_volume: 400.0,
            sell_volume: 100.0,
            neutral_volume: 0.0,
            trade_count: 5,
            pivot_at: confirmed_at - chrono::Duration::seconds(1),
            confirmed_at,
        };

        entry
            .push_structure_events(&[event(1, "100ms"), event(2, "1s")])
            .await
            .unwrap();
        entry
            .push_structure_events(&[event(1, "100ms")])
            .await
            .unwrap();

        let state = entry.state.lock().await;
        assert_eq!(state.structure_events.len(), 2);
        assert_eq!(state.structure_events[0].timeframe, "100ms");
        assert_eq!(state.structure_events[1].timeframe, "1s");
    }

    #[test]
    fn chart_window_carries_only_active_pre_window_swings_per_timeframe() {
        let window_start = Utc.with_ymd_and_hms(2026, 7, 17, 13, 40, 0).unwrap();
        let window_end = window_start + chrono::Duration::minutes(5);
        let event = |event_id: u64,
                     level_id: u64,
                     timeframe: &str,
                     event_kind: &str,
                     direction: i8,
                     confirmed_at: DateTime<Utc>| GenericStructureEvent {
            algorithm_version: 2,
            event_id,
            level_id,
            sym: "VEEE".to_string(),
            timeframe: timeframe.to_string(),
            event_kind: event_kind.to_string(),
            direction,
            price: 42.0,
            lower: 42.0,
            upper: 42.0,
            strength: 0.7,
            confidence: 0.8,
            lifecycle: "active".to_string(),
            total_volume: 500.0,
            buy_volume: 400.0,
            sell_volume: 100.0,
            neutral_volume: 0.0,
            trade_count: 5,
            pivot_at: confirmed_at - chrono::Duration::seconds(1),
            confirmed_at,
        };
        let events = vec![
            event(
                1,
                10,
                "5m",
                "level_promoted",
                -1,
                window_start - Duration::minutes(12),
            ),
            event(
                2,
                11,
                "5m",
                "level_promoted",
                -1,
                window_start - Duration::minutes(6),
            ),
            event(3, 11, "5m", "bos", 1, window_start - Duration::minutes(2)),
            event(
                4,
                12,
                "5m",
                "level_promoted",
                1,
                window_start - Duration::minutes(4),
            ),
            event(
                5,
                13,
                "1h",
                "level_promoted",
                -1,
                window_start - Duration::minutes(30),
            ),
            event(
                6,
                14,
                "1s",
                "level_promoted",
                -1,
                window_start + Duration::seconds(5),
            ),
        ];

        let selected = structure_events_overlapping(&events, window_start, window_end, window_end);
        let ids = selected
            .iter()
            .map(|event| event.event_id)
            .collect::<Vec<_>>();

        assert_eq!(ids, vec![5, 4, 6]);
    }

    #[test]
    fn source_windows_split_into_ordered_non_overlapping_chunks() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 10, 0, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 12, 6, 0, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let chunks = split_event_window(&window, 24);
        assert_eq!(chunks.len(), 3);
        assert_eq!(chunks.first().unwrap().start, window.start);
        assert_eq!(chunks.last().unwrap().end, window.end);
        for pair in chunks.windows(2) {
            assert_eq!(pair[0].end, pair[1].start);
        }
    }

    #[test]
    fn chart_cache_rejects_duplicate_and_descending_bar_times() {
        let first = Utc.with_ymd_and_hms(2026, 7, 14, 13, 45, 0).unwrap();
        let next = first + chrono::Duration::milliseconds(100);
        assert!(ensure_monotonic_bar_start(Some(first), next).is_ok());
        assert!(ensure_monotonic_bar_start(Some(first), first).is_err());
        assert!(ensure_monotonic_bar_start(Some(next), first).is_err());
    }
}
