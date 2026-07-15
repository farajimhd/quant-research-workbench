use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalCursor, HistoricalEventSource, SourceRevision};
use chrono::{DateTime, Duration, Utc};
use qmd_core::bars::{BarRow, BarSnapshot, SharedBarStore, BAR_SCHEMA_VERSION};
use qmd_core::compact_event::LiveCompactEvent;
use qmd_core::indicators::{BarIndicatorCalculator, IndicatorRow, INDICATOR_SCHEMA_VERSION};
use qmd_core::market_products::{
    parse_resolution_us, ConditionBarSnapshot, ConditionClassifier, FamilyBarRow,
    FamilyBarSnapshot, MacroBarSnapshot, MarketProductEngine, ProductCacheLimits, ProductState,
    MARKET_PRODUCT_SCHEMA_VERSION,
};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::mem::size_of;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, mpsc, Mutex, Notify, Semaphore};

pub const HISTORICAL_ENGINE_VERSION: &str = "qmd-derived-v2";

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
pub struct DerivedSnapshot {
    #[serde(flatten)]
    pub bars: BarSnapshot,
    pub cache: CacheEvidence,
    pub indicators: Vec<IndicatorRow>,
}

#[derive(Clone, Debug, Serialize)]
pub struct CacheEvidence {
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
}

#[derive(Clone, Debug, Serialize)]
pub struct ChartSnapshot {
    pub as_of: DateTime<Utc>,
    pub bars: Vec<ChartBarRow>,
    pub cache: CacheEvidence,
    pub has_more: bool,
    pub indicators: Vec<IndicatorRow>,
    pub indicators_available: bool,
    pub next_before: Option<DateTime<Utc>>,
    pub ticker: String,
    pub timeframe: String,
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
    pub source_revision: SourceRevision,
}

pub struct CacheEntry {
    allocated_bytes: Arc<AtomicU64>,
    complete: AtomicBool,
    frame_bytes: AtomicU64,
    global_max_bytes: u64,
    notify: Notify,
    state: Mutex<EntryState>,
    updates: broadcast::Sender<DerivedUpdate>,
    estimated_bytes: AtomicU64,
    max_update_bytes: usize,
    max_updates: usize,
    product_bytes: AtomicU64,
}

struct CacheIndex {
    entries: HashMap<String, Arc<CacheEntry>>,
    order: VecDeque<String>,
}

#[derive(Default)]
struct EntryState {
    complete: bool,
    error: Option<String>,
    events_processed: u64,
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

    pub async fn acquire(&self, window: EventWindow, ticker: String) -> Result<CacheLease, String> {
        let source_revision = self.source.source_revision(&window).await?;
        let key = cache_key(&window, &ticker, &source_revision);
        let mut index = self.inner.lock().await;
        if let Some(entry) = index.entries.get(&key).cloned() {
            touch(&mut index.order, &key);
            self.stats.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(CacheLease {
                entry,
                hit: true,
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
            if index.entries.remove(&oldest).is_some() {
                self.stats.evictions.fetch_add(1, Ordering::Relaxed);
            }
        }
        let (updates, _) = broadcast::channel(self.config.cache_update_capacity.max(16));
        let entry = Arc::new(CacheEntry {
            allocated_bytes: self.allocated_bytes.clone(),
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: self.config.cache_max_bytes as u64,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: self.config.cache_max_bytes / 2,
            max_updates: self.config.cache_max_updates_per_entry,
            product_bytes: AtomicU64::new(0),
        });
        index.entries.insert(key.clone(), entry.clone());
        index.order.push_back(key);
        drop(index);

        self.stats.builds.fetch_add(1, Ordering::Relaxed);
        let builder = self.clone();
        let build_entry = entry.clone();
        tokio::spawn(async move {
            builder.build(build_entry, window, ticker).await;
        });
        Ok(CacheLease {
            entry,
            hit: false,
            source_revision,
        })
    }

    pub async fn snapshot(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
        limit: usize,
    ) -> Result<DerivedSnapshot, String> {
        let as_of = window.end;
        let lease = self.acquire(window, ticker.clone()).await?;
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
    ) -> Result<ChartSnapshot, String> {
        let resolution_us = parse_resolution_us(&timeframe)
            .ok_or_else(|| format!("unsupported chart timeframe {timeframe}"))?;
        let lease = self.acquire(window, ticker.clone()).await?;
        let event_count = lease.entry.wait_ready().await?;
        let cache = CacheEvidence {
            engine_version: HISTORICAL_ENGINE_VERSION,
            event_count,
            hit: lease.hit,
            source_revision: lease.source_revision,
        };

        if qmd_core::bars::is_supported_timeframe(&timeframe) {
            let state = lease.entry.state.lock().await;
            let matching = state
                .frames
                .iter()
                .filter(|frame| {
                    frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                        && frame.bar.bar_end <= as_of
                        && before.is_none_or(|bound| frame.bar.bar_start < bound)
                })
                .collect::<Vec<_>>();
            let has_more = matching.len() > limit;
            let selected = &matching[matching.len().saturating_sub(limit)..];
            let bars = selected
                .iter()
                .map(|frame| ChartBarRow::from_bar(&frame.bar))
                .collect::<Vec<_>>();
            let indicators = selected
                .iter()
                .map(|frame| frame.indicator.clone())
                .collect::<Vec<_>>();
            let next_before = has_more.then(|| bars[0].bar_start);
            return Ok(ChartSnapshot {
                as_of,
                bars,
                cache,
                has_more,
                indicators,
                indicators_available: true,
                next_before,
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
            next_before,
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
        let lease = self.acquire(window, ticker.clone()).await?;
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
        let lease = self.acquire(window, ticker.clone()).await?;
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
        let lease = self.acquire(window, ticker.clone()).await?;
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
        CacheMetrics {
            active_builds: self.config.cache_max_concurrent_builds
                - self.build_permits.available_permits(),
            builds: self.stats.builds.load(Ordering::Relaxed),
            estimated_bytes: self.allocated_bytes.load(Ordering::Relaxed),
            entries: index.entries.len(),
            evictions: self.stats.evictions.load(Ordering::Relaxed),
            hits: self.stats.hits.load(Ordering::Relaxed),
            misses: self.stats.misses.load(Ordering::Relaxed),
            max_bytes: self.config.cache_max_bytes,
        }
    }

    async fn build(&self, entry: Arc<CacheEntry>, window: EventWindow, ticker: String) {
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
        let result = self.build_inner(entry.clone(), window, ticker).await;
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
        _ticker: String,
    ) -> Result<u64, String> {
        let resolutions = self
            .config
            .product_timeframes
            .iter()
            .filter_map(|value| parse_resolution_us(value))
            .collect::<Vec<_>>();
        let bars = SharedBarStore::new(
            self.config.product_timeframes.clone(),
            self.config.cache_max_bars_per_entry,
            1,
            self.source.trade_aggregation_rules(),
        );
        let shard = bars.shard(0);
        let mut indicators = HashMap::<String, BarIndicatorCalculator>::new();
        let mut products = MarketProductEngine::new(
            resolutions,
            ProductCacheLimits {
                max_bytes: self.config.cache_max_bytes / 2,
                max_partitions: self.config.cache_max_entries.max(1),
                max_rows: self.config.product_cache_max_rows_per_entry,
            },
            self.source.trade_aggregation_rules(),
            ConditionClassifier::training_aligned(),
        );
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
            active.push_back(self.spawn_chunk_fetch(chunks[next_chunk].clone()));
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
                    products.apply_event(&event, event.ts());
                    for bar in shard.apply_event(&event).await {
                        if valid_price_bar(&bar) {
                            let indicator = indicators
                                .entry(bar.timeframe.clone())
                                .or_insert_with(BarIndicatorCalculator::new)
                                .apply_bar(&bar);
                            entry.push(bar, indicator).await?;
                        }
                    }
                }
                events_processed += count as u64;
                entry.set_product_bytes(products.metrics().estimated_bytes)?;
                let mut state = entry.state.lock().await;
                state.events_processed = events_processed;
            }
            if next_chunk < chunks.len() {
                active.push_back(self.spawn_chunk_fetch(chunks[next_chunk].clone()));
                next_chunk += 1;
            }
        }
        for bar in shard.finalize_due(window.end).await {
            if valid_price_bar(&bar) {
                let indicator = indicators
                    .entry(bar.timeframe.clone())
                    .or_insert_with(BarIndicatorCalculator::new)
                    .apply_bar(&bar);
                entry.push(bar, indicator).await?;
            }
        }
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
        Ok(events_processed)
    }

    fn spawn_chunk_fetch(
        &self,
        window: EventWindow,
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
                    .fetch_batch(&window, cursor.as_ref(), batch_size)
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
            if index.entries.remove(&oldest).is_some() {
                self.stats.evictions.fetch_add(1, Ordering::Relaxed);
            }
        }
    }
}

impl CacheEntry {
    pub fn subscribe(&self) -> broadcast::Receiver<DerivedUpdate> {
        self.updates.subscribe()
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

    async fn push(&self, bar: BarRow, indicator: IndicatorRow) -> Result<(), String> {
        let mut state = self.state.lock().await;
        let frame_bytes = state
            .frames
            .len()
            .saturating_add(1)
            .saturating_mul(size_of::<DerivedUpdate>() + 512);
        if state.frames.len() >= self.max_updates || frame_bytes > self.max_update_bytes {
            return Err(format!(
                "historical derived entry exceeded cache limit: updates={} max_updates={} estimated_bytes={} max_update_bytes={}",
                state.frames.len() + 1,
                self.max_updates,
                frame_bytes,
                self.max_update_bytes,
            ));
        }
        self.set_estimated_bytes(frame_bytes as u64 + self.product_bytes.load(Ordering::Acquire))?;
        self.frame_bytes
            .store(frame_bytes as u64, Ordering::Release);
        let update = DerivedUpdate {
            as_of: bar.bar_end,
            bar,
            indicator,
            sequence: state.frames.len() as u64 + 1,
            update_type: "update",
        };
        state.frames.push(update.clone());
        drop(state);
        let _ = self.updates.send(update);
        Ok(())
    }

    fn set_product_bytes(&self, bytes: usize) -> Result<(), String> {
        let bytes = bytes as u64;
        self.set_estimated_bytes(self.frame_bytes.load(Ordering::Acquire) + bytes)?;
        self.product_bytes.store(bytes, Ordering::Release);
        Ok(())
    }

    fn set_estimated_bytes(&self, next: u64) -> Result<(), String> {
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
        }
    }
}

impl Drop for CacheEntry {
    fn drop(&mut self) {
        let bytes = self.estimated_bytes.load(Ordering::Acquire);
        if bytes > 0 {
            self.allocated_bytes.fetch_sub(bytes, Ordering::AcqRel);
        }
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

fn cache_key(window: &EventWindow, ticker: &str, revision: &SourceRevision) -> String {
    format!(
        "{}:{}:{}:{}:{}:{}:{}:{}",
        ticker.to_ascii_uppercase(),
        window.start.timestamp_micros(),
        window.end.timestamp_micros(),
        revision.token,
        HISTORICAL_ENGINE_VERSION,
        BAR_SCHEMA_VERSION,
        INDICATOR_SCHEMA_VERSION,
        MARKET_PRODUCT_SCHEMA_VERSION,
    )
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
        cache_key, split_event_window, CacheEntry, EntryState, SourceRevision,
        HISTORICAL_ENGINE_VERSION,
    };
    use crate::source::EventWindow;
    use chrono::{TimeZone, Utc};
    use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
    use std::sync::Arc;
    use tokio::sync::{broadcast, Mutex, Notify};

    #[test]
    fn cache_key_changes_with_source_revision_and_engine_contract() {
        let window = EventWindow {
            start: Utc.with_ymd_and_hms(2026, 7, 10, 8, 0, 0).unwrap(),
            end: Utc.with_ymd_and_hms(2026, 7, 11, 0, 0, 0).unwrap(),
            tickers: vec!["AAPL".to_string()],
        };
        let first = SourceRevision {
            event_count: 10,
            max_build_step: 1,
            max_updated_at: "2026-07-10 01:00:00".to_string(),
            token: "1:10:2026-07-10 01:00:00".to_string(),
        };
        let second = SourceRevision {
            token: "2:10:2026-07-10 02:00:00".to_string(),
            ..first.clone()
        };
        assert_ne!(
            cache_key(&window, "AAPL", &first),
            cache_key(&window, "AAPL", &second)
        );
        assert!(cache_key(&window, "AAPL", &first).contains(HISTORICAL_ENGINE_VERSION));
    }

    #[test]
    fn cache_entry_reservations_enforce_the_service_byte_ceiling() {
        let allocated = Arc::new(AtomicU64::new(0));
        let (updates, _) = broadcast::channel(16);
        let entry = CacheEntry {
            allocated_bytes: allocated.clone(),
            complete: AtomicBool::new(false),
            frame_bytes: AtomicU64::new(0),
            global_max_bytes: 1_000,
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            updates,
            estimated_bytes: AtomicU64::new(0),
            max_update_bytes: 1_000,
            max_updates: 10,
            product_bytes: AtomicU64::new(0),
        };
        assert!(entry.set_estimated_bytes(900).is_ok());
        assert!(entry.set_estimated_bytes(1_001).is_err());
        assert_eq!(allocated.load(Ordering::Acquire), 900);
        drop(entry);
        assert_eq!(allocated.load(Ordering::Acquire), 0);
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
}
