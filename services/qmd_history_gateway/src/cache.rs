use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalCursor, HistoricalEventSource, SourceRevision};
use chrono::{DateTime, Utc};
use qmd_core::bars::{BarRow, BarSnapshot, SharedBarStore, BAR_SCHEMA_VERSION};
use qmd_core::indicators::{BarIndicatorCalculator, IndicatorRow, INDICATOR_SCHEMA_VERSION};
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::Arc;
use tokio::sync::{broadcast, Mutex, Notify};

pub const HISTORICAL_ENGINE_VERSION: &str = "qmd-derived-v1";

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
    pub builds: u64,
    pub entries: usize,
    pub evictions: u64,
    pub hits: u64,
    pub misses: u64,
}

#[derive(Clone)]
pub struct HistoricalDerivedCache {
    config: HistoricalGatewayConfig,
    inner: Arc<Mutex<CacheIndex>>,
    source: HistoricalEventSource,
    stats: Arc<CacheStats>,
}

pub struct CacheLease {
    pub entry: Arc<CacheEntry>,
    pub hit: bool,
    pub source_revision: SourceRevision,
}

pub struct CacheEntry {
    complete: AtomicBool,
    notify: Notify,
    state: Mutex<EntryState>,
    updates: broadcast::Sender<DerivedUpdate>,
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
        Self {
            config,
            inner: Arc::new(Mutex::new(CacheIndex {
                entries: HashMap::new(),
                order: VecDeque::new(),
            })),
            source,
            stats: Arc::new(CacheStats::default()),
        }
    }

    pub async fn acquire(
        &self,
        window: EventWindow,
        ticker: String,
        timeframe: String,
    ) -> Result<CacheLease, String> {
        let source_revision = self.source.source_revision(&window).await?;
        let key = cache_key(&window, &ticker, &timeframe, &source_revision);
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
            complete: AtomicBool::new(false),
            notify: Notify::new(),
            state: Mutex::new(EntryState::default()),
            updates,
        });
        index.entries.insert(key.clone(), entry.clone());
        index.order.push_back(key);
        drop(index);

        self.stats.builds.fetch_add(1, Ordering::Relaxed);
        let builder = self.clone();
        let build_entry = entry.clone();
        tokio::spawn(async move {
            builder.build(build_entry, window, ticker, timeframe).await;
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
        let lease = self
            .acquire(window, ticker.clone(), timeframe.clone())
            .await?;
        let (frames, event_count) = lease.entry.wait_complete().await?;
        let take = limit.min(frames.len());
        let selected = &frames[frames.len().saturating_sub(take)..];
        Ok(DerivedSnapshot {
            bars: BarSnapshot {
                current: None,
                history: selected.iter().map(|frame| frame.bar.clone()).collect(),
                ticker,
                timeframe,
            },
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

    pub async fn metrics(&self) -> CacheMetrics {
        CacheMetrics {
            builds: self.stats.builds.load(Ordering::Relaxed),
            entries: self.inner.lock().await.entries.len(),
            evictions: self.stats.evictions.load(Ordering::Relaxed),
            hits: self.stats.hits.load(Ordering::Relaxed),
            misses: self.stats.misses.load(Ordering::Relaxed),
        }
    }

    async fn build(
        &self,
        entry: Arc<CacheEntry>,
        window: EventWindow,
        ticker: String,
        timeframe: String,
    ) {
        let result = self
            .build_inner(entry.clone(), window, ticker, timeframe)
            .await;
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
    }

    async fn build_inner(
        &self,
        entry: Arc<CacheEntry>,
        window: EventWindow,
        _ticker: String,
        timeframe: String,
    ) -> Result<u64, String> {
        let bars = SharedBarStore::new(
            vec![timeframe],
            self.config.cache_max_bars_per_entry,
            1,
            self.source.trade_aggregation_rules(),
        );
        let shard = bars.shard(0);
        let mut indicators = BarIndicatorCalculator::new();
        let mut cursor: Option<HistoricalCursor> = None;
        let mut events_processed = 0_u64;
        loop {
            let remaining = self
                .config
                .max_events_per_request
                .saturating_sub(events_processed as usize);
            if remaining == 0 {
                return Err(format!(
                    "historical derived build exceeded event_limit={}",
                    self.config.max_events_per_request
                ));
            }
            let request_size = self.config.batch_size.min(remaining).max(1);
            let (events, next) = self
                .source
                .fetch_batch(&window, cursor.as_ref(), request_size)
                .await?;
            let count = events.len();
            for compact in &events {
                let event = self.source.market_event(compact);
                for bar in shard.apply_event(&event).await {
                    if valid_price_bar(&bar) {
                        let indicator = indicators.apply_bar(&bar);
                        entry.push(bar, indicator).await;
                    }
                }
            }
            events_processed += count as u64;
            {
                let mut state = entry.state.lock().await;
                state.events_processed = events_processed;
            }
            if count < request_size || next.is_none() {
                break;
            }
            cursor = next;
        }
        for bar in shard.finalize_due(window.end).await {
            if valid_price_bar(&bar) {
                let indicator = indicators.apply_bar(&bar);
                entry.push(bar, indicator).await;
            }
        }
        Ok(events_processed)
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

    async fn push(&self, bar: BarRow, indicator: IndicatorRow) {
        let mut state = self.state.lock().await;
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
    }

    async fn wait_complete(&self) -> Result<(Vec<DerivedUpdate>, u64), String> {
        loop {
            let notified = self.notify.notified();
            {
                let state = self.state.lock().await;
                if state.complete {
                    if let Some(error) = &state.error {
                        return Err(error.clone());
                    }
                    return Ok((state.frames.clone(), state.events_processed));
                }
            }
            notified.await;
        }
    }
}

fn cache_key(
    window: &EventWindow,
    ticker: &str,
    timeframe: &str,
    revision: &SourceRevision,
) -> String {
    format!(
        "{}:{}:{}:{}:{}:{}:{}:{}",
        ticker.to_ascii_uppercase(),
        timeframe.to_ascii_lowercase(),
        window.start.timestamp_micros(),
        window.end.timestamp_micros(),
        revision.token,
        HISTORICAL_ENGINE_VERSION,
        BAR_SCHEMA_VERSION,
        INDICATOR_SCHEMA_VERSION,
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
    use super::{cache_key, SourceRevision, HISTORICAL_ENGINE_VERSION};
    use crate::source::EventWindow;
    use chrono::{TimeZone, Utc};

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
            cache_key(&window, "AAPL", "1m", &first),
            cache_key(&window, "AAPL", "1m", &second)
        );
        assert!(cache_key(&window, "AAPL", "1m", &first).contains(HISTORICAL_ENGINE_VERSION));
    }
}
