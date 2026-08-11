use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalEventSource, SourceRevision};
use chrono::{DateTime, Utc};
use qmd_core::bars::{BarRow, SharedBarStore};
use qmd_core::event::MarketEvent;
use qmd_core::indicators::{
    BarIndicatorCalculator, IndicatorRow, MarketStructureReferenceLevels,
    MicrostructureSampleAggregate,
};
use qmd_core::market_signal::{MarketSignalEngine, MarketSignalEvent};
use qmd_core::microstructure_interval::MicrostructureIntervalWindow;
use serde::Serialize;
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{Mutex, OnceCell};

pub const HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION: &str = "canvas_historical_qmd_snapshot_v3";
const SIGNAL_EVENT_LIMIT: usize = 20_000;
const SCANNER_TIMEFRAMES: [&str; 5] = ["100ms", "1s", "10s", "30s", "1m"];
const SCANNER_INDICATOR_TIMEFRAME: &str = "100ms";

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalScannerDerivedSnapshot {
    pub active_signals: Vec<MarketSignalEvent>,
    pub as_of: DateTime<Utc>,
    pub engine_version: &'static str,
    pub event_count: u64,
    pub indicators: Vec<IndicatorRow>,
    pub indicator_timeframe: &'static str,
    pub recent_signal_events: Vec<MarketSignalEvent>,
    pub schema_version: &'static str,
    pub source_revision: SourceRevision,
    pub ticker_count: usize,
}

type SnapshotResult = Result<Arc<HistoricalScannerDerivedSnapshot>, String>;

#[derive(Clone)]
pub struct HistoricalScannerDerivedCache {
    config: HistoricalGatewayConfig,
    entries: Arc<Mutex<HashMap<String, Arc<OnceCell<SnapshotResult>>>>>,
    source: HistoricalEventSource,
}

impl HistoricalScannerDerivedCache {
    pub fn new(config: HistoricalGatewayConfig, source: HistoricalEventSource) -> Self {
        Self {
            config,
            entries: Arc::new(Mutex::new(HashMap::new())),
            source,
        }
    }

    pub async fn snapshot(
        &self,
        window: EventWindow,
        as_of: DateTime<Utc>,
    ) -> Result<HistoricalScannerDerivedSnapshot, String> {
        if !window.tickers.is_empty() {
            return Err(
                "historical Scanner derived replay requires the full market window".to_string(),
            );
        }
        if as_of < window.start || as_of > window.end {
            return Err("historical Scanner as_of must fall inside its replay window".to_string());
        }
        let source_revision = self.source.source_revision(&window).await?;
        let key = format!(
            "{}:{}:{}:{}",
            window.start.to_rfc3339(),
            as_of.to_rfc3339(),
            source_revision.token,
            qmd_core::market_signal::MARKET_SIGNAL_ENGINE_VERSION,
        );
        let cell = {
            let mut entries = self.entries.lock().await;
            if entries.len() >= self.config.scanner_cache_max_entries && !entries.contains_key(&key)
            {
                if let Some(stale_key) = entries.keys().next().cloned() {
                    entries.remove(&stale_key);
                }
            }
            entries
                .entry(key)
                .or_insert_with(|| Arc::new(OnceCell::new()))
                .clone()
        };
        let config = self.config.clone();
        let source = self.source.clone();
        let built = cell
            .get_or_init(|| async move {
                build_snapshot(config, source, window, as_of, source_revision).await
            })
            .await;
        built
            .as_ref()
            .map(|snapshot| (**snapshot).clone())
            .map_err(Clone::clone)
    }
}

struct CrossSectionEngine {
    active_signals: HashMap<String, MarketSignalEvent>,
    aggregates: HashMap<String, MicrostructureSampleAggregate>,
    bars: SharedBarStore,
    calculators: HashMap<String, BarIndicatorCalculator>,
    indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    last_base_indicators: HashMap<String, IndicatorRow>,
    latest_indicators: HashMap<String, IndicatorRow>,
    market_signals: MarketSignalEngine,
    microstructure: HashMap<String, MicrostructureIntervalWindow>,
    recent_signal_events: VecDeque<MarketSignalEvent>,
    trade_rules: qmd_core::bars::TradeAggregationRules,
}

#[derive(Default)]
struct ScannerWorkerResult {
    active_signals: Vec<MarketSignalEvent>,
    event_count: u64,
    indicators: Vec<IndicatorRow>,
    recent_signal_events: Vec<MarketSignalEvent>,
}

impl ScannerWorkerResult {
    fn extend(&mut self, snapshot: HistoricalScannerDerivedSnapshot) {
        self.active_signals.extend(snapshot.active_signals);
        self.indicators.extend(snapshot.indicators);
        self.recent_signal_events
            .extend(snapshot.recent_signal_events);
        if self.recent_signal_events.len() > SIGNAL_EVENT_LIMIT * 2 {
            sort_and_bound_signal_events(&mut self.recent_signal_events);
        }
    }
}

impl CrossSectionEngine {
    fn new(
        source: &HistoricalEventSource,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    ) -> Self {
        let trade_rules = source.trade_aggregation_rules();
        Self {
            active_signals: HashMap::new(),
            aggregates: HashMap::new(),
            bars: SharedBarStore::new(
                SCANNER_TIMEFRAMES
                    .iter()
                    .map(|value| (*value).to_string())
                    .collect(),
                2,
                1,
                trade_rules.clone(),
            ),
            calculators: HashMap::new(),
            indicator_references,
            last_base_indicators: HashMap::new(),
            latest_indicators: HashMap::new(),
            market_signals: MarketSignalEngine::default(),
            microstructure: HashMap::new(),
            recent_signal_events: VecDeque::with_capacity(SIGNAL_EVENT_LIMIT),
            trade_rules,
        }
    }

    async fn apply_event(&mut self, event: MarketEvent) -> Result<(), String> {
        let ticker = event.ticker().to_ascii_uppercase();
        self.microstructure
            .entry(ticker)
            .or_default()
            .apply_event(&event);
        for bar in self.bars.apply_event(&event).await {
            self.apply_bar(bar)?;
        }
        Ok(())
    }

    async fn finalize(&mut self, as_of: DateTime<Utc>) -> Result<(), String> {
        for bar in self.bars.finalize_due(as_of).await {
            self.apply_bar(bar)?;
        }
        Ok(())
    }

    fn apply_bar(&mut self, bar: BarRow) -> Result<(), String> {
        let ticker = bar.sym.to_ascii_uppercase();
        let timeframe = bar.timeframe.to_ascii_lowercase();
        let calculator_key = format!("{ticker}:{timeframe}");
        let references = self
            .indicator_references
            .get(&ticker)
            .copied()
            .unwrap_or_default();
        let calculator = self.calculators.entry(calculator_key).or_insert_with(|| {
            let mut calculator = BarIndicatorCalculator::new();
            calculator.set_market_structure_references(references);
            calculator
        });
        if timeframe == "100ms" {
            let mut indicator = if valid_price_bar(&bar) {
                calculator.apply_bar(&bar)
            } else if let Some(previous) = self.last_base_indicators.get(&ticker) {
                let mut carried = previous.clone();
                carried.session_date = bar.session_date.clone();
                carried.bar_start = bar.bar_start;
                carried.bar_end = bar.bar_end;
                carried.volume = 0.0;
                carried.qmd_structure_events.clear();
                carried
            } else {
                return Ok(());
            };
            let interval = self
                .microstructure
                .get(&ticker)
                .map(|window| window.interval_at(bar.bar_end, &self.trade_rules))
                .unwrap_or_default();
            calculator.apply_microstructure_interval(&mut indicator, &interval);
            calculator.apply_cumulative_microstructure(&mut indicator);
            if valid_price_bar(&bar) {
                calculator.apply_market_levels(&mut indicator, &bar);
            }
            self.last_base_indicators
                .insert(ticker.clone(), indicator.clone());
            let mut published = indicator.clone();
            published.qmd_structure_active_levels.clear();
            self.latest_indicators.insert(ticker.clone(), published);
            for target in SCANNER_TIMEFRAMES.iter().skip(1) {
                self.aggregates
                    .entry(format!("{ticker}:{target}"))
                    .or_default()
                    .push(&indicator);
            }
            let events = self
                .market_signals
                .update_with_indicator(&bar, Some(&indicator));
            self.apply_signal_events(events);
            return Ok(());
        }
        if !valid_price_bar(&bar) {
            return Ok(());
        }
        let mut indicator = calculator.apply_bar(&bar);
        if let Some(aggregate) = self.aggregates.get_mut(&format!("{ticker}:{timeframe}")) {
            aggregate.apply_to(&mut indicator);
            aggregate.reset();
        }
        calculator.apply_cumulative_microstructure(&mut indicator);
        calculator.apply_market_levels(&mut indicator, &bar);
        let events = self
            .market_signals
            .update_with_indicator(&bar, Some(&indicator));
        self.apply_signal_events(events);
        Ok(())
    }

    fn apply_signal_events(&mut self, events: Vec<MarketSignalEvent>) {
        for event in events {
            let key = format!(
                "{}:{}:{}",
                event.ticker.to_ascii_uppercase(),
                event.working_timeframe.to_ascii_lowercase(),
                event.signal_key,
            );
            if matches!(event.state.as_str(), "resolved" | "expired") {
                self.active_signals.remove(&key);
            } else {
                self.active_signals.insert(key, event.clone());
            }
            self.recent_signal_events.push_back(event);
            while self.recent_signal_events.len() > SIGNAL_EVENT_LIMIT {
                self.recent_signal_events.pop_front();
            }
        }
    }

    fn into_snapshot(
        self,
        as_of: DateTime<Utc>,
        event_count: u64,
        source_revision: SourceRevision,
    ) -> HistoricalScannerDerivedSnapshot {
        let mut indicators = self.latest_indicators.into_values().collect::<Vec<_>>();
        indicators.sort_by(|left, right| left.sym.cmp(&right.sym));
        let ticker_count = indicators.len();
        let mut active_signals = self.active_signals.into_values().collect::<Vec<_>>();
        active_signals.sort_by(|left, right| {
            right
                .rank_score
                .partial_cmp(&left.rank_score)
                .unwrap_or(std::cmp::Ordering::Equal)
                .then_with(|| {
                    right
                        .confidence
                        .partial_cmp(&left.confidence)
                        .unwrap_or(std::cmp::Ordering::Equal)
                })
                .then_with(|| left.signal_id.cmp(&right.signal_id))
        });
        let mut recent_signal_events = self.recent_signal_events.into_iter().collect::<Vec<_>>();
        recent_signal_events.reverse();
        HistoricalScannerDerivedSnapshot {
            active_signals,
            as_of,
            engine_version: qmd_core::market_signal::MARKET_SIGNAL_ENGINE_VERSION,
            event_count,
            indicators,
            indicator_timeframe: SCANNER_INDICATOR_TIMEFRAME,
            recent_signal_events,
            schema_version: HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION,
            source_revision,
            ticker_count,
        }
    }
}

async fn build_snapshot(
    config: HistoricalGatewayConfig,
    source: HistoricalEventSource,
    mut window: EventWindow,
    as_of: DateTime<Utc>,
    source_revision: SourceRevision,
) -> SnapshotResult {
    window.end = as_of;
    let references = source
        .market_structure_reference_levels_all(window.start)
        .await
        .unwrap_or_else(|error| {
            eprintln!("QMD historical Scanner daily references unavailable: {error}");
            HashMap::new()
        });
    let worker_count = config.scanner_shard_count;
    let mut reference_partitions = vec![HashMap::new(); worker_count];
    for (ticker, levels) in references {
        reference_partitions[scanner_shard_index(&ticker, worker_count)].insert(ticker, levels);
    }
    let mut senders = Vec::with_capacity(worker_count);
    let mut workers = Vec::with_capacity(worker_count);
    for mut worker_references in reference_partitions {
        let (sender, mut receiver) = tokio::sync::mpsc::channel::<Vec<MarketEvent>>(2);
        let worker_source = source.clone();
        workers.push(tokio::spawn(async move {
            let mut current_ticker = String::new();
            let mut engine: Option<CrossSectionEngine> = None;
            let mut result = ScannerWorkerResult::default();
            while let Some(events) = receiver.recv().await {
                for event in events {
                    let ticker = event.ticker().to_string();
                    if ticker != current_ticker {
                        if let Some(mut completed) = engine.take() {
                            completed.finalize(as_of).await?;
                            result.extend(completed.into_snapshot(
                                as_of,
                                0,
                                empty_source_revision(),
                            ));
                        }
                        let mut ticker_references = HashMap::new();
                        if let Some(levels) = worker_references.remove(&ticker) {
                            ticker_references.insert(ticker.clone(), levels);
                        }
                        engine = Some(CrossSectionEngine::new(&worker_source, ticker_references));
                        current_ticker = ticker;
                    }
                    result.event_count = result.event_count.saturating_add(1);
                    if let Some(active) = engine.as_mut() {
                        active.apply_event(event).await?;
                    }
                }
            }
            if let Some(mut completed) = engine {
                completed.finalize(as_of).await?;
                result.extend(completed.into_snapshot(as_of, 0, empty_source_revision()));
            }
            sort_and_bound_signal_events(&mut result.recent_signal_events);
            Ok::<ScannerWorkerResult, String>(result)
        }));
        senders.push(sender);
    }
    let mut event_count = 0_u64;
    let mut batches = source.stream_ordered(
        window,
        config.batch_size.max(100_000),
        source_revision.live_continuation_sequence,
    )?;
    while let Some(events) = batches.recv().await {
        let events = events?;
        event_count = event_count.saturating_add(events.len() as u64);
        if event_count > config.scanner_max_events_per_snapshot as u64 {
            return Err(format!(
                "historical Scanner derived replay exceeded event_limit={}",
                config.scanner_max_events_per_snapshot
            ));
        }
        let mut partitions = vec![Vec::new(); worker_count];
        for compact in events {
            let index = scanner_shard_index(&compact.ticker, worker_count);
            partitions[index].push(source.market_event(&compact));
        }
        for (sender, partition) in senders.iter().zip(partitions) {
            if !partition.is_empty() {
                sender
                    .send(partition)
                    .await
                    .map_err(|_| "historical Scanner replay worker stopped early".to_string())?;
            }
        }
    }
    drop(senders);
    let mut results = Vec::with_capacity(worker_count);
    for worker in workers {
        results.push(
            worker
                .await
                .map_err(|error| format!("historical Scanner replay worker panicked: {error}"))??,
        );
    }
    Ok(Arc::new(merge_worker_results(
        results,
        as_of,
        event_count,
        source_revision,
    )))
}

fn valid_price_bar(bar: &BarRow) -> bool {
    [bar.open, bar.high, bar.low, bar.close]
        .into_iter()
        .all(|value| value.is_finite() && value > 0.0)
        && bar.high >= bar.open.max(bar.close)
        && bar.low <= bar.open.min(bar.close)
        && bar.high >= bar.low
}

fn merge_worker_results(
    results: Vec<ScannerWorkerResult>,
    as_of: DateTime<Utc>,
    event_count: u64,
    source_revision: SourceRevision,
) -> HistoricalScannerDerivedSnapshot {
    let mut indicators = Vec::new();
    let mut active_signals = Vec::new();
    let mut recent_signal_events = Vec::new();
    for result in results {
        indicators.extend(result.indicators);
        active_signals.extend(result.active_signals);
        recent_signal_events.extend(result.recent_signal_events);
    }
    indicators.sort_by(|left, right| left.sym.cmp(&right.sym));
    active_signals.sort_by(|left, right| {
        right
            .rank_score
            .partial_cmp(&left.rank_score)
            .unwrap_or(std::cmp::Ordering::Equal)
            .then_with(|| left.signal_id.cmp(&right.signal_id))
    });
    sort_and_bound_signal_events(&mut recent_signal_events);
    let ticker_count = indicators.len();
    HistoricalScannerDerivedSnapshot {
        active_signals,
        as_of,
        engine_version: qmd_core::market_signal::MARKET_SIGNAL_ENGINE_VERSION,
        event_count,
        indicators,
        indicator_timeframe: SCANNER_INDICATOR_TIMEFRAME,
        recent_signal_events,
        schema_version: HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION,
        source_revision,
        ticker_count,
    }
}

fn sort_and_bound_signal_events(events: &mut Vec<MarketSignalEvent>) {
    events.sort_by(|left, right| {
        right
            .effective_at
            .cmp(&left.effective_at)
            .then_with(|| right.event_id.cmp(&left.event_id))
    });
    events.truncate(SIGNAL_EVENT_LIMIT);
}

fn empty_source_revision() -> SourceRevision {
    SourceRevision {
        complete_for_history: false,
        event_count: 0,
        live_continuation_sequence: None,
        max_build_step: 0,
        max_updated_at: String::new(),
        request_complete: false,
        source_plan_hash: String::new(),
        source_tiers: Vec::new(),
        token: String::new(),
    }
}

fn scanner_shard_index(ticker: &str, shard_count: usize) -> usize {
    let mut hash = 1_469_598_103_934_665_603_u64;
    for byte in ticker.as_bytes() {
        hash ^= u64::from(*byte);
        hash = hash.wrapping_mul(1_099_511_628_211);
    }
    (hash as usize) % shard_count.max(1)
}
