use crate::config::HistoricalGatewayConfig;
use crate::source::{
    EventWindow, HistoricalCursor, HistoricalEventSource, SessionVwapSeed, SourceRevision,
};
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use qmd_core::bars::{BarRow, BarSnapshot, SharedBarStore, BAR_SCHEMA_VERSION};
use qmd_core::compact_event::LiveCompactEvent;
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{GenericStructureEvent, StructureLevelCandidate};
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
use serde::Serialize;
use std::collections::{BTreeMap, HashMap, VecDeque};
use std::mem::size_of;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use std::sync::Mutex as StdMutex;
use tokio::sync::{broadcast, mpsc, Mutex, Notify, Semaphore};

pub const HISTORICAL_ENGINE_VERSION: &str = "qmd-derived-v33";
pub const HISTORICAL_CALCULATION_REVISION: &str = "qmd-derived-v34";
pub const HISTORICAL_CORPORATE_ACTION_REVISION: &str = "raw-unadjusted-v1";
const MAX_ENCOUNTERED_STRUCTURE_LEVELS: usize = 4_000;

#[derive(Clone, Debug, Eq, PartialEq)]
enum CacheProfile {
    Derived(String),
    Products,
}

impl CacheProfile {
    fn key(&self) -> String {
        match self {
            Self::Derived(timeframe) => format!("derived:{timeframe}"),
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

#[derive(Clone, Debug, Serialize)]
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
    pub vwap: Option<f64>,
    pub estimated_luld_active: bool,
    pub estimated_luld_reference_price: f64,
    pub estimated_luld_lower_price: f64,
    pub estimated_luld_upper_price: f64,
    pub estimated_luld_distance_to_upper_pct: f64,
    pub estimated_luld_distance_to_lower_pct: f64,
    pub estimated_luld_state: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct ChartSnapshot {
    pub as_of: DateTime<Utc>,
    pub bars: Vec<ChartBarRow>,
    pub cache: CacheEvidence,
    pub has_more: bool,
    pub indicators: Vec<IndicatorRow>,
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
    build_permits: Arc<Semaphore>,
    fetch_permits: Arc<Semaphore>,
}

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
    structure_events: Vec<GenericStructureEvent>,
    frames: Vec<DerivedUpdate>,
    products: Option<MarketProductEngine>,
}

#[derive(Default)]
struct CacheStats {
    builds: AtomicU64,
    evictions: AtomicU64,
    hits: AtomicU64,
    misses: AtomicU64,
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
            build_permits: Arc::new(Semaphore::new(max_concurrent_builds)),
            fetch_permits: Arc::new(Semaphore::new(max_concurrent_fetches)),
        }
    }

    async fn acquire(
        &self,
        window: EventWindow,
        ticker: String,
        profile: CacheProfile,
    ) -> Result<CacheLease, String> {
        let revision_window = revision_window(&window, &profile)?;
        let source_revision = self.source.source_revision(&revision_window).await?;
        let key = cache_key(&window, &ticker, &source_revision, &profile);
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
                .build(build_entry, window, ticker, profile, build_revision)
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

    pub async fn chart_snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
        limit: usize,
        as_of: DateTime<Utc>,
        before: Option<DateTime<Utc>>,
        bars_only: bool,
    ) -> Result<ChartSnapshot, String> {
        let resolution_us = parse_resolution_us(&timeframe)
            .ok_or_else(|| format!("unsupported chart timeframe {timeframe}"))?;
        let profile = if qmd_core::bars::is_supported_timeframe(&timeframe) {
            CacheProfile::Derived(timeframe.clone())
        } else {
            CacheProfile::Products
        };
        let lease = self.acquire(window, ticker.clone(), profile).await?;
        let event_count = if bars_only && qmd_core::bars::is_supported_timeframe(&timeframe) {
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

        if qmd_core::bars::is_supported_timeframe(&timeframe) {
            let state = lease.entry.state.lock().await;
            if bars_only {
                let mut selected = state
                    .bars
                    .iter()
                    .rev()
                    .filter(|update| {
                        update.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                            && update.bar.bar_end <= as_of
                            && before.is_none_or(|bound| update.bar.bar_start < bound)
                    })
                    .take(limit.saturating_add(1))
                    .collect::<Vec<_>>();
                let has_more = selected.len() > limit;
                selected.truncate(limit);
                selected.reverse();
                let bars = selected
                    .iter()
                    .map(|update| ChartBarRow::from_bar(&update.bar))
                    .collect::<Vec<_>>();
                let next_before = has_more.then(|| bars[0].bar_start);
                return Ok(ChartSnapshot {
                    as_of,
                    bars,
                    cache,
                    has_more,
                    indicators: Vec::new(),
                    indicators_available: false,
                    market_signal_events: Vec::new(),
                    next_before,
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
            requirements,
        }
    }

    async fn build(
        &self,
        entry: Arc<CacheEntry>,
        window: EventWindow,
        ticker: String,
        profile: CacheProfile,
        source_revision: SourceRevision,
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
            .build_inner(entry.clone(), window, ticker, profile, source_revision)
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
    ) -> Result<u64, String> {
        let builds_products = matches!(&profile, CacheProfile::Products);
        let resolutions = self
            .config
            .product_timeframes
            .iter()
            .filter_map(|value| parse_resolution_us(value))
            .collect::<Vec<_>>();
        let requested_timeframe = match &profile {
            CacheProfile::Derived(timeframe) => Some(timeframe.clone()),
            CacheProfile::Products => None,
        };
        let derived_timeframes = match &requested_timeframe {
            Some(timeframe) if timeframe.eq_ignore_ascii_case("100ms") => vec![timeframe.clone()],
            Some(timeframe) => vec!["100ms".to_string(), timeframe.clone()],
            None => Vec::new(),
        };
        let bars = SharedBarStore::new(
            derived_timeframes,
            self.config.cache_max_bars_per_entry,
            1,
            self.source.trade_aggregation_rules(),
        );
        let session_vwap_seed = if matches!(&profile, CacheProfile::Derived(_)) {
            let seed_start = session_anchor(window.start)?;
            self.source
                .session_vwap_seed(
                    EventWindow {
                        start: seed_start,
                        end: window.start,
                        tickers: window.tickers.clone(),
                    },
                    source_revision.live_continuation_sequence,
                )
                .await?
        } else {
            SessionVwapSeed::default()
        };
        if matches!(&profile, CacheProfile::Derived(_)) {
            let events = self
                .source
                .persisted_structure_events_before(&ticker, window.start)
                .await?;
            bars.seed_structure_events(events).await;
        }
        let shard = bars.shard(0);
        let trade_rules = self.source.trade_aggregation_rules();
        let structure_references = if matches!(profile, CacheProfile::Derived(_)) {
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
        let mut indicator_worker = if matches!(profile, CacheProfile::Derived(_)) {
            let (sender, mut receiver) = mpsc::channel::<IndicatorWork>(
                self.config.cache_update_capacity.clamp(16, 100_000),
            );
            let worker_entry = entry.clone();
            let worker_rules = trade_rules.clone();
            let worker_structure_references = structure_references;
            let worker_session_vwap_seed = session_vwap_seed;
            let worker_page_start = window.start;
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
                                    calculator
                                        .seed_session_vwap(
                                            worker_page_start,
                                            worker_session_vwap_seed.cumulative_volume,
                                            worker_session_vwap_seed.cumulative_trade_notional,
                                        )
                                        .expect("validated historical session VWAP seed");
                                    calculator.set_market_structure_references(
                                        worker_structure_references,
                                    );
                                    calculator
                                });
                            let mut indicator = if valid_price_bar(&bar) {
                                calculator.apply_bar(&bar)
                            } else if let Some(previous) = &last_base_indicator {
                                let mut carried = previous.clone();
                                carried.session_date = bar.session_date.clone();
                                carried.bar_start = bar.bar_start;
                                carried.bar_end = bar.bar_end;
                                carried.volume = 0.0;
                                carried.qmd_structure_events.clear();
                                carried.vwap = calculator.apply_session_vwap_only(&bar);
                                carried.price_vs_vwap_pct = if carried.vwap > 0.0 {
                                    (carried.close / carried.vwap - 1.0) * 100.0
                                } else {
                                    0.0
                                };
                                carried
                            } else {
                                calculator.apply_session_vwap_only(&bar);
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
                            last_base_indicator = Some(indicator.clone());
                            aggregate.push(&indicator);
                            for event in
                                market_signal_engine.update_with_indicator(&bar, Some(&indicator))
                            {
                                worker_entry.push_market_signal_event(event).await?;
                            }
                            if let Some(sequence) = sequence {
                                worker_entry
                                    .push_indicator(sequence, bar, indicator)
                                    .await?;
                            }
                        } else if let Some(sequence) = sequence {
                            let calculator =
                                calculators.entry(bar.timeframe.clone()).or_insert_with(|| {
                                    let mut calculator = BarIndicatorCalculator::new();
                                    calculator
                                        .seed_session_vwap(
                                            worker_page_start,
                                            worker_session_vwap_seed.cumulative_volume,
                                            worker_session_vwap_seed.cumulative_trade_notional,
                                        )
                                        .expect("validated historical session VWAP seed");
                                    calculator.set_market_structure_references(
                                        worker_structure_references,
                                    );
                                    calculator
                                });
                            let mut indicator = calculator.apply_bar(&bar);
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
                    let event = self.source.market_event(compact);
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
                ));
                next_chunk += 1;
            }
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

    fn spawn_chunk_fetch(
        &self,
        window: EventWindow,
        live_continuation_sequence: Option<u64>,
    ) -> mpsc::Receiver<Result<Vec<LiveCompactEvent>, String>> {
        let (sender, receiver) = mpsc::channel(2);
        let source = self.source.clone();
        let permits = self.fetch_permits.clone();
        let batch_size = self.config.batch_size;
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
                match source
                    .fetch_batch_at_revision(
                        &window,
                        cursor.as_ref(),
                        batch_size,
                        live_continuation_sequence,
                    )
                    .await
                {
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

fn revision_window(window: &EventWindow, profile: &CacheProfile) -> Result<EventWindow, String> {
    let start = if matches!(profile, CacheProfile::Derived(_)) {
        session_anchor(window.start)?
    } else {
        window.start
    };
    Ok(EventWindow {
        start,
        end: window.end,
        tickers: window.tickers.clone(),
    })
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
            CacheProfile::Derived(timeframe) => Some(timeframe.clone()),
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
        bounded_encountered_structure_levels, cache_key, encountered_structure_levels_for_session,
        ensure_monotonic_bar_start, historical_requirement, revision_window, session_anchor,
        split_event_window, stable_hash_hex, structure_events_overlapping, CacheEntry,
        CacheProfile, EntryState, SourceRevision, HISTORICAL_CALCULATION_REVISION,
        HISTORICAL_CORPORATE_ACTION_REVISION, HISTORICAL_ENGINE_VERSION,
        MAX_ENCOUNTERED_STRUCTURE_LEVELS,
    };
    use crate::source::EventWindow;
    use chrono::{DateTime, Duration, TimeZone, Utc};
    use qmd_core::generic_structure::{GenericStructureEvent, StructureLevelCandidate};
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::{Arc, Mutex as StdMutex};
    use tokio::sync::{broadcast, Mutex, Notify};

    #[test]
    fn derived_revision_and_seed_anchor_cover_the_full_new_york_session() {
        let page = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 14, 18, 30, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 14, 20, 30, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let expected = Utc.with_ymd_and_hms(2026, 7, 14, 8, 0, 0).unwrap();

        assert_eq!(session_anchor(page.start).unwrap(), expected);
        assert_eq!(
            revision_window(&page, &CacheProfile::Derived("5m".to_string()))
                .unwrap()
                .start,
            expected
        );
        assert_eq!(
            revision_window(&page, &CacheProfile::Products)
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
        let products = cache_key(&window, "AAPL", &revision, &CacheProfile::Products);

        assert_ne!(one_minute, five_minute);
        assert_ne!(one_minute, products);
        assert_ne!(five_minute, products);
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
