use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalEventSource, SourceRevision};
use crate::watchlist_timeline::{
    validate_plan, ExternalFeatureRevisionEvidence, HistoricalWatchlistTimelineRequest,
    WatchlistCandidate, WatchlistCandidateDeltaFrame, WatchlistTimelineChunk,
    WatchlistTimelineReducer,
};
use chrono::{DateTime, NaiveDate, Utc};
use chrono_tz::America::New_York;
use qmd_core::bars::{BarRow, SharedBarStore, TradeAggregationRules};
use qmd_core::event::MarketEvent;
use qmd_core::indicators::{
    BarIndicatorCalculator, IndicatorRow, MarketStructureReferenceLevels,
    MicrostructureSampleAggregate,
};
use qmd_core::market_signal::{MarketSignalEngine, MarketSignalEvent};
use qmd_core::microstructure_interval::MicrostructureIntervalWindow;
use qmd_core::state::SharedMarketState;
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
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

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalWatchlistTimelineMaterialization {
    pub calculation_revision: &'static str,
    pub cadence_ms: u64,
    pub chunks: Vec<WatchlistTimelineChunk>,
    pub engine_version: &'static str,
    pub evaluation_count: u64,
    pub event_count: u64,
    pub external_feature_revisions: Vec<ExternalFeatureRevisionEvidence>,
    pub materialization_id: String,
    pub plan_hash: String,
    pub schema_version: u16,
    pub source_revision: SourceRevision,
    pub transition_count: usize,
    pub watchlist_id: String,
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
    changed_indicator_tickers: BTreeSet<String>,
    indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    last_base_indicators: HashMap<String, IndicatorRow>,
    latest_indicators: HashMap<String, IndicatorRow>,
    market_signals: MarketSignalEngine,
    market_state: SharedMarketState,
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
        Self::new_with_trade_rules(source.trade_aggregation_rules(), indicator_references)
    }

    fn new_with_trade_rules(
        trade_rules: TradeAggregationRules,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    ) -> Self {
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
            changed_indicator_tickers: BTreeSet::new(),
            indicator_references,
            last_base_indicators: HashMap::new(),
            latest_indicators: HashMap::new(),
            market_signals: MarketSignalEngine::default(),
            market_state: SharedMarketState::new(),
            microstructure: HashMap::new(),
            recent_signal_events: VecDeque::with_capacity(SIGNAL_EVENT_LIMIT),
            trade_rules,
        }
    }

    async fn apply_event(&mut self, event: MarketEvent) -> Result<(), String> {
        let ticker = event.ticker().to_ascii_uppercase();
        self.market_state.apply_event(&event).await;
        self.microstructure
            .entry(ticker)
            .or_default()
            .apply_event(&event);
        for bar in self.bars.apply_event(&event).await {
            self.apply_bar(bar)?;
        }
        Ok(())
    }

    async fn watchlist_candidate(
        &self,
        ticker: &str,
        as_of: DateTime<Utc>,
        sources: &BTreeSet<String>,
    ) -> Result<Option<WatchlistCandidate>, String> {
        let ticker = ticker.to_ascii_uppercase();
        let Some(market) = self.market_state.ticker_snapshot_at(&ticker, as_of).await else {
            return Ok(None);
        };
        let indicator = self.latest_indicators.get(&ticker);
        let references = self
            .indicator_references
            .get(&ticker)
            .copied()
            .unwrap_or_default();
        let mut values = BTreeMap::new();
        for source in sources {
            let value = match source.as_str() {
                "market.last_price" if market.last_price.is_finite() && market.last_price > 0.0 => {
                    Some(market.last_price)
                }
                "market.volume" if market.day_volume.is_finite() => Some(market.day_volume),
                "liquidity-rank" => {
                    Some(market.day_dollar_volume / 1_000_000.0 + market.trade_rate_10s * 100.0)
                }
                "indicator.vwap.value" => indicator
                    .map(|row| row.vwap)
                    .filter(|value| value.is_finite() && *value > 0.0),
                "market.change_pct"
                    if market.last_price.is_finite()
                        && market.last_price > 0.0
                        && references.previous_session_close.is_finite()
                        && references.previous_session_close > 0.0 =>
                {
                    Some((market.last_price / references.previous_session_close - 1.0) * 100.0)
                }
                "market.relative_volume" => {
                    return Err(
                        "historical Watchlist aligned relative-volume baseline is unavailable"
                            .to_string(),
                    );
                }
                _ => None,
            };
            if let Some(value) = value {
                values.insert(source.clone(), Value::from(value));
            }
        }
        Ok(Some(WatchlistCandidate { ticker, values }))
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
            self.changed_indicator_tickers.insert(ticker.clone());
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

    fn take_changed_indicator_tickers(&mut self) -> BTreeSet<String> {
        std::mem::take(&mut self.changed_indicator_tickers)
    }

    fn replace_indicator_references(
        &mut self,
        references: HashMap<String, MarketStructureReferenceLevels>,
    ) {
        self.indicator_references = references;
        for (key, calculator) in &mut self.calculators {
            let ticker = key.split(':').next().unwrap_or_default();
            calculator.set_market_structure_references(
                self.indicator_references
                    .get(ticker)
                    .copied()
                    .unwrap_or_default(),
            );
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
    for worker_references in reference_partitions {
        let (sender, mut receiver) = tokio::sync::mpsc::channel::<Vec<MarketEvent>>(2);
        let worker_source = source.clone();
        workers.push(tokio::spawn(async move {
            // The source is globally event-time ordered, so a ticker can recur
            // after many other symbols. One multi-symbol engine per shard keeps
            // every ticker's bar, indicator, microstructure and signal state
            // alive across those interleavings.
            let mut engine = CrossSectionEngine::new(&worker_source, worker_references);
            let mut result = ScannerWorkerResult::default();
            while let Some(events) = receiver.recv().await {
                for event in events {
                    result.event_count = result.event_count.saturating_add(1);
                    engine.apply_event(event).await?;
                }
            }
            engine.finalize(as_of).await?;
            result.extend(engine.into_snapshot(as_of, 0, empty_source_revision()));
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

#[derive(Clone)]
struct ParsedExternalInterval {
    end: Option<DateTime<Utc>>,
    start: DateTime<Utc>,
    value: Value,
}

type ExternalIntervalIndex = BTreeMap<String, BTreeMap<String, Vec<ParsedExternalInterval>>>;

pub async fn materialize_watchlist_timeline(
    config: HistoricalGatewayConfig,
    source: HistoricalEventSource,
    request: HistoricalWatchlistTimelineRequest,
) -> Result<HistoricalWatchlistTimelineMaterialization, String> {
    let validation = validate_plan(&request.plan)?;
    let start = request
        .plan
        .start
        .parse::<DateTime<Utc>>()
        .map_err(|error| format!("invalid historical Watchlist start: {error}"))?;
    let end = request
        .plan
        .end
        .parse::<DateTime<Utc>>()
        .map_err(|error| format!("invalid historical Watchlist end: {error}"))?;
    let (external, boundaries) = prepare_external_features(&request, start, end)?;
    let window = EventWindow {
        start,
        end,
        tickers: Vec::new(),
    };
    let source_revision = source.source_revision(&window).await?;
    if !source_revision.complete_for_history || !source_revision.request_complete {
        return Err(
            "historical Watchlist timeline requires a complete pinned market-event window"
                .to_string(),
        );
    }
    let references = source
        .market_structure_reference_levels_all(start)
        .await
        .map_err(|error| format!("historical Watchlist daily references unavailable: {error}"))?;
    let mut engine = CrossSectionEngine::new(&source, references);
    let qmd_sources = request.plan.qmd_sources.iter().cloned().collect();
    let mut reducer = Some(WatchlistTimelineReducer::new(&request.plan, None)?);
    let mut chunks = Vec::new();
    let mut dirty = external.keys().cloned().collect::<BTreeSet<_>>();
    let mut known_tickers = BTreeSet::new();
    let mut recent_until = HashMap::<String, DateTime<Utc>>::new();
    let mut boundary_index = 0_usize;
    let mut evaluation_index = 0_u64;
    let mut event_count = 0_u64;
    let mut reference_session = session_date(start);
    let mut batches = source.stream_ordered(
        window,
        config.batch_size.max(100_000),
        source_revision.live_continuation_sequence,
    )?;
    while let Some(events) = batches.recv().await {
        for compact in events? {
            let event = source.market_event(&compact);
            while evaluation_index < validation.evaluation_count
                && evaluation_clock(&request.plan, start, evaluation_index)? <= event.ts()
            {
                let clock = evaluation_clock(&request.plan, start, evaluation_index)?;
                advance_external_boundaries(&boundaries, &mut boundary_index, clock, &mut dirty);
                mark_rate_updates(clock, &mut recent_until, &mut dirty);
                let session = session_date(clock);
                if session != reference_session {
                    let references = source
                        .market_structure_reference_levels_all(clock)
                        .await
                        .map_err(|error| {
                            format!("historical Watchlist daily references unavailable: {error}")
                        })?;
                    engine.replace_indicator_references(references);
                    dirty.extend(known_tickers.iter().cloned());
                    reference_session = session;
                }
                apply_timeline_clock(
                    &mut engine,
                    reducer
                        .as_mut()
                        .ok_or_else(|| "historical Watchlist reducer is unavailable".to_string())?,
                    &external,
                    &qmd_sources,
                    clock,
                    &mut dirty,
                )
                .await?;
                evaluation_index += 1;
                finish_reducer_chunk_if_due(
                    &request.plan,
                    validation.evaluation_count,
                    evaluation_index,
                    &mut reducer,
                    &mut chunks,
                )?;
            }
            let ticker = event.ticker().to_ascii_uppercase();
            known_tickers.insert(ticker.clone());
            dirty.insert(ticker.clone());
            recent_until.insert(ticker, event.ts() + chrono::Duration::seconds(61));
            engine.apply_event(event).await?;
            event_count = event_count.saturating_add(1);
            if event_count > config.scanner_max_events_per_snapshot as u64 {
                return Err(format!(
                    "historical Watchlist replay exceeded event_limit={}",
                    config.scanner_max_events_per_snapshot
                ));
            }
        }
    }
    while evaluation_index < validation.evaluation_count {
        let clock = evaluation_clock(&request.plan, start, evaluation_index)?;
        advance_external_boundaries(&boundaries, &mut boundary_index, clock, &mut dirty);
        mark_rate_updates(clock, &mut recent_until, &mut dirty);
        let session = session_date(clock);
        if session != reference_session {
            let references = source
                .market_structure_reference_levels_all(clock)
                .await
                .map_err(|error| {
                    format!("historical Watchlist daily references unavailable: {error}")
                })?;
            engine.replace_indicator_references(references);
            dirty.extend(known_tickers.iter().cloned());
            reference_session = session;
        }
        apply_timeline_clock(
            &mut engine,
            reducer
                .as_mut()
                .ok_or_else(|| "historical Watchlist reducer is unavailable".to_string())?,
            &external,
            &qmd_sources,
            clock,
            &mut dirty,
        )
        .await?;
        evaluation_index += 1;
        finish_reducer_chunk_if_due(
            &request.plan,
            validation.evaluation_count,
            evaluation_index,
            &mut reducer,
            &mut chunks,
        )?;
    }
    let transition_count = chunks
        .iter()
        .map(|chunk| chunk.transitions.len())
        .sum::<usize>();
    let mut external_revisions = request.external_feature_revisions;
    external_revisions.sort_by(|left, right| left.field_id.cmp(&right.field_id));
    let materialization_id = materialization_id(
        &request.plan.plan_hash,
        &source_revision,
        &external_revisions,
    )?;
    Ok(HistoricalWatchlistTimelineMaterialization {
        calculation_revision: HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION,
        cadence_ms: request.plan.cadence_ms,
        chunks,
        engine_version: qmd_core::market_signal::MARKET_SIGNAL_ENGINE_VERSION,
        evaluation_count: validation.evaluation_count,
        event_count,
        external_feature_revisions: external_revisions,
        materialization_id,
        plan_hash: request.plan.plan_hash,
        schema_version: 1,
        source_revision,
        transition_count,
        watchlist_id: request.plan.watchlist_id,
    })
}

fn prepare_external_features(
    request: &HistoricalWatchlistTimelineRequest,
    plan_start: DateTime<Utc>,
    plan_end: DateTime<Utc>,
) -> Result<(ExternalIntervalIndex, Vec<(DateTime<Utc>, String)>), String> {
    let contracts = request
        .plan
        .external_features
        .iter()
        .map(|feature| (feature.field_id.as_str(), feature))
        .collect::<BTreeMap<_, _>>();
    let revisions = request
        .external_feature_revisions
        .iter()
        .map(|revision| (revision.field_id.as_str(), revision))
        .collect::<BTreeMap<_, _>>();
    if revisions.len() != request.external_feature_revisions.len()
        || revisions.len() != contracts.len()
    {
        return Err(
            "historical Watchlist external feature revisions must exactly match plan contracts"
                .to_string(),
        );
    }
    for (field_id, contract) in &contracts {
        let revision = revisions.get(field_id).ok_or_else(|| {
            format!("historical Watchlist external revision is missing for {field_id}")
        })?;
        if !revision.complete
            || revision.query_plan_id != contract.query_plan_id
            || revision.schema_version != contract.schema_version
            || revision.source_revision.trim().is_empty()
        {
            return Err(format!(
                "historical Watchlist external revision is incomplete or mismatched for {field_id}"
            ));
        }
    }
    let mut index = ExternalIntervalIndex::new();
    let mut boundaries = Vec::new();
    for interval in &request.external_feature_intervals {
        if !contracts.contains_key(interval.field_id.as_str()) {
            return Err(format!(
                "historical Watchlist external interval uses undeclared field_id={}",
                interval.field_id
            ));
        }
        let ticker = interval.ticker.trim().to_ascii_uppercase();
        if ticker.is_empty()
            || interval.value.is_null()
            || interval.value.is_array()
            || interval.value.is_object()
        {
            return Err("historical Watchlist external interval is invalid".to_string());
        }
        let start = interval
            .start
            .parse::<DateTime<Utc>>()
            .map_err(|error| format!("invalid external interval start: {error}"))?;
        let end = interval
            .end
            .as_ref()
            .map(|value| {
                value
                    .parse::<DateTime<Utc>>()
                    .map_err(|error| format!("invalid external interval end: {error}"))
            })
            .transpose()?;
        if end.is_some_and(|value| value <= start) {
            return Err("historical Watchlist external interval end must follow start".to_string());
        }
        if end.is_some_and(|value| value <= plan_start) || start >= plan_end {
            continue;
        }
        let values = index
            .entry(ticker.clone())
            .or_default()
            .entry(interval.field_id.clone())
            .or_default();
        values.push(ParsedExternalInterval {
            end,
            start,
            value: interval.value.clone(),
        });
        boundaries.push((start.max(plan_start), ticker.clone()));
        if let Some(end) = end.filter(|value| *value < plan_end) {
            boundaries.push((end.max(plan_start), ticker));
        }
    }
    for (ticker, fields) in &mut index {
        for (field_id, values) in fields {
            values.sort_by_key(|interval| interval.start);
            for pair in values.windows(2) {
                if pair[0].end.is_none_or(|end| end > pair[1].start) {
                    return Err(format!(
                        "historical Watchlist external intervals overlap for {ticker}:{field_id}"
                    ));
                }
            }
        }
    }
    boundaries.sort();
    Ok((index, boundaries))
}

async fn apply_timeline_clock(
    engine: &mut CrossSectionEngine,
    reducer: &mut WatchlistTimelineReducer<'_>,
    external: &ExternalIntervalIndex,
    qmd_sources: &BTreeSet<String>,
    clock: DateTime<Utc>,
    dirty: &mut BTreeSet<String>,
) -> Result<(), String> {
    engine.finalize(clock).await?;
    dirty.extend(engine.take_changed_indicator_tickers());
    let tickers = std::mem::take(dirty);
    let mut upserts = Vec::new();
    let mut removals = Vec::new();
    for ticker in tickers {
        match engine
            .watchlist_candidate(&ticker, clock, qmd_sources)
            .await?
        {
            Some(mut candidate) => {
                if let Some(fields) = external.get(&ticker) {
                    for (field_id, intervals) in fields {
                        if let Some(value) = external_value_at(intervals, clock) {
                            candidate.values.insert(field_id.clone(), value.clone());
                        }
                    }
                }
                upserts.push(candidate);
            }
            None => removals.push(ticker),
        }
    }
    reducer.apply_delta(&WatchlistCandidateDeltaFrame {
        effective_at: clock.to_rfc3339(),
        removals,
        upserts,
    })?;
    Ok(())
}

fn external_value_at(intervals: &[ParsedExternalInterval], clock: DateTime<Utc>) -> Option<&Value> {
    intervals
        .iter()
        .rev()
        .find(|interval| interval.start <= clock && interval.end.is_none_or(|end| clock < end))
        .map(|interval| &interval.value)
}

fn advance_external_boundaries(
    boundaries: &[(DateTime<Utc>, String)],
    index: &mut usize,
    clock: DateTime<Utc>,
    dirty: &mut BTreeSet<String>,
) {
    while *index < boundaries.len() && boundaries[*index].0 <= clock {
        dirty.insert(boundaries[*index].1.clone());
        *index += 1;
    }
}

fn mark_rate_updates(
    clock: DateTime<Utc>,
    recent_until: &mut HashMap<String, DateTime<Utc>>,
    dirty: &mut BTreeSet<String>,
) {
    let expired = recent_until
        .iter()
        .filter(|(_, until)| **until < clock)
        .map(|(ticker, _)| ticker.clone())
        .collect::<Vec<_>>();
    dirty.extend(recent_until.keys().cloned());
    for ticker in expired {
        dirty.insert(ticker.clone());
        recent_until.remove(&ticker);
    }
}

fn evaluation_clock(
    plan: &crate::watchlist_timeline::HistoricalWatchlistPlan,
    start: DateTime<Utc>,
    index: u64,
) -> Result<DateTime<Utc>, String> {
    Ok(start
        + chrono::Duration::milliseconds(
            i64::try_from(index.saturating_mul(plan.cadence_ms))
                .map_err(|_| "historical Watchlist cadence clock overflowed".to_string())?,
        ))
}

fn finish_reducer_chunk_if_due<'a>(
    plan: &'a crate::watchlist_timeline::HistoricalWatchlistPlan,
    evaluation_count: u64,
    evaluation_index: u64,
    reducer: &mut Option<WatchlistTimelineReducer<'a>>,
    chunks: &mut Vec<WatchlistTimelineChunk>,
) -> Result<(), String> {
    if evaluation_index % plan.max_evaluations_per_chunk != 0
        && evaluation_index != evaluation_count
    {
        return Ok(());
    }
    let completed = reducer
        .take()
        .ok_or_else(|| "historical Watchlist reducer is unavailable".to_string())?
        .finish()?;
    let state = completed.next_state.clone();
    chunks.push(completed);
    if evaluation_index < evaluation_count {
        *reducer = Some(WatchlistTimelineReducer::new(plan, Some(state))?);
    }
    Ok(())
}

fn session_date(clock: DateTime<Utc>) -> NaiveDate {
    let local = clock.with_timezone(&New_York);
    if local.time() < chrono::NaiveTime::from_hms_opt(4, 0, 0).unwrap() {
        local.date_naive().pred_opt().unwrap_or(local.date_naive())
    } else {
        local.date_naive()
    }
}

fn materialization_id(
    plan_hash: &str,
    source_revision: &SourceRevision,
    external_revisions: &[ExternalFeatureRevisionEvidence],
) -> Result<String, String> {
    let payload = serde_json::json!({
        "external_feature_revisions": external_revisions,
        "plan_hash": plan_hash,
        "source_revision": source_revision,
    });
    let encoded = serde_json::to_vec(&payload)
        .map_err(|error| format!("historical Watchlist identity encoding failed: {error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
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

#[cfg(test)]
mod tests {
    use super::{empty_source_revision, CrossSectionEngine};
    use chrono::{TimeZone, Utc};
    use qmd_core::bars::{TradeAggregationRules, TradeUpdateRule};
    use qmd_core::event::{MarketEvent, TradeEvent};
    use qmd_core::indicators::MarketStructureReferenceLevels;
    use serde_json::json;
    use std::collections::{BTreeSet, HashMap};

    fn trade(ticker: &str, millis: i64, price: f64) -> MarketEvent {
        let ts = Utc.timestamp_millis_opt(millis).single().unwrap();
        MarketEvent::Trade(TradeEvent {
            conditions: vec![0],
            exchange: 1,
            ingest_ts: ts,
            participant_ts: None,
            price,
            raw: json!({}),
            sequence: millis as u64,
            size: 100.0,
            tape: 1,
            ticker: ticker.to_string(),
            trade_id: format!("{ticker}-{millis}"),
            trf_id: 0,
            trf_ts: None,
            ts,
        })
    }

    #[tokio::test]
    async fn interleaved_tickers_retain_independent_indicator_state() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let mut engine = CrossSectionEngine::new_with_trade_rules(
            rules,
            HashMap::from([(
                "AAPL".to_string(),
                MarketStructureReferenceLevels {
                    previous_session_close: 200.0,
                    ..Default::default()
                },
            )]),
        );
        let start = Utc
            .with_ymd_and_hms(2026, 8, 7, 13, 30, 0)
            .single()
            .unwrap();
        engine
            .apply_event(trade("AAPL", start.timestamp_millis(), 200.0))
            .await
            .unwrap();
        engine
            .apply_event(trade("MSFT", start.timestamp_millis() + 10, 500.0))
            .await
            .unwrap();
        engine
            .apply_event(trade("AAPL", start.timestamp_millis() + 20, 201.0))
            .await
            .unwrap();
        engine
            .finalize(start + chrono::Duration::seconds(1))
            .await
            .unwrap();

        let candidate = engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(1),
                &BTreeSet::from([
                    "indicator.vwap.value".to_string(),
                    "liquidity-rank".to_string(),
                    "market.change_pct".to_string(),
                    "market.last_price".to_string(),
                    "market.volume".to_string(),
                ]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["market.last_price"], json!(201.0));
        assert_eq!(candidate.values["market.volume"], json!(200.0));
        assert!((candidate.values["market.change_pct"].as_f64().unwrap() - 0.5).abs() < 1e-9);
        assert!(candidate.values["liquidity-rank"].as_f64().unwrap() > 0.0);
        assert!(candidate.values["indicator.vwap.value"].as_f64().unwrap() > 0.0);
        let snapshot = engine.into_snapshot(
            start + chrono::Duration::seconds(1),
            3,
            empty_source_revision(),
        );
        assert_eq!(snapshot.ticker_count, 2);
        assert_eq!(
            snapshot
                .indicators
                .iter()
                .map(|row| row.sym.as_str())
                .collect::<Vec<_>>(),
            vec!["AAPL", "MSFT"]
        );
    }
}
