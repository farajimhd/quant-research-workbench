use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalEventSource, SourceRevision};
use crate::watchlist_timeline::{
    plan_evaluation_clock, validate_plan, ExternalFeatureRevisionEvidence, HistoricalWatchlistPlan,
    HistoricalWatchlistPlanValidation, HistoricalWatchlistTimelineBatchRequest,
    HistoricalWatchlistTimelineRequest, WatchlistCandidate, WatchlistCandidateDeltaFrame,
    WatchlistEvaluationWindow, WatchlistTimelineChunk, WatchlistTimelineReducer,
};
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use qmd_core::bars::{BarRow, SharedBarStore, TradeAggregationRules};
use qmd_core::event::MarketEvent;
use qmd_core::indicators::{
    BarIndicatorCalculator, IndicatorRow, MarketStructureReferenceLevels,
    MicrostructureSampleAggregate,
};
use qmd_core::market_signal::{MarketSignalEngine, MarketSignalEvent};
use qmd_core::microstructure_interval::MicrostructureIntervalWindow;
use qmd_core::state::{SharedMarketState, SymbolSnapshot};
use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet, HashMap, VecDeque};
use std::sync::Arc;
use tokio::sync::{Mutex, OnceCell};

pub const HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION: &str = "canvas_historical_qmd_snapshot_v9";
const SIGNAL_EVENT_LIMIT: usize = 20_000;
const SCANNER_TIMEFRAMES: [&str; 12] = [
    "100ms", "1s", "10s", "30s", "1m", "5m", "15m", "30m", "1h", "2h", "4h", "1d",
];
const SCANNER_INDICATOR_TIMEFRAME: &str = "100ms";
const WATCHLIST_BATCH_MAX_EVALUATIONS: u64 = 5_000_000;
const WATCHLIST_BATCH_MAX_MEMBERSHIP_SLOTS: u64 = 10_000_000;
const WATCHLIST_BATCH_MAX_FOCUSED_SLOTS: u64 = 1_000_000_000;
const ALIGNED_VOLUME_SESSION_COUNT: usize = 20;
const ALIGNED_VOLUME_BUCKET_SECONDS: usize = 10;
const ALIGNED_VOLUME_BUCKET_COUNT: usize = 16 * 60 * 60 / ALIGNED_VOLUME_BUCKET_SECONDS;
const ALIGNED_VOLUME_MAX_TICKERS: usize = 2_000;

fn watchlist_derived_timeframes(qmd_sources: &BTreeSet<String>) -> BTreeSet<String> {
    let mut timeframes = BTreeSet::new();
    for source in qmd_sources {
        if let Some((_, dimension)) = source.split_once("@@") {
            let (source_id, _) = source.split_once("@@").unwrap_or((source, ""));
            let timeframe = dimension.split_once("##").map_or(dimension, |row| row.0);
            if source_id == "volume_rate_ratio" && timeframe.eq_ignore_ascii_case("1s") {
                continue;
            }
            if !timeframe.is_empty() {
                timeframes.insert(timeframe.to_ascii_lowercase());
            }
        } else if source == "indicator.vwap.value" {
            timeframes.insert(SCANNER_INDICATOR_TIMEFRAME.to_string());
        }
    }
    timeframes
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalScannerDerivedSnapshot {
    pub active_signals: Vec<MarketSignalEvent>,
    pub as_of: DateTime<Utc>,
    pub engine_version: &'static str,
    pub event_count: u64,
    pub indicators: Vec<IndicatorRow>,
    pub indicator_timeframe: &'static str,
    pub market_rows: Vec<HistoricalScannerMarketRow>,
    pub recent_signal_events: Vec<MarketSignalEvent>,
    pub schema_version: &'static str,
    pub source_revision: SourceRevision,
    pub ticker_count: usize,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalScannerMarketRow {
    #[serde(flatten)]
    pub market: SymbolSnapshot,
    pub previous_close: Option<f64>,
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
    pub projection_complete: bool,
    pub projection_mode: &'static str,
    pub projection_tickers: Vec<String>,
    pub relative_volume_revisions: Vec<RelativeVolumeRevisionEvidence>,
    pub schema_version: u16,
    pub source_revision: SourceRevision,
    pub source_tickers: Vec<String>,
    pub transition_count: usize,
    pub watchlist_id: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct RelativeVolumeRevisionEvidence {
    pub baseline_session_count: usize,
    pub end: DateTime<Utc>,
    pub session_date: NaiveDate,
    pub source_revision: SourceRevision,
    pub start: DateTime<Utc>,
    pub ticker_count: usize,
    pub ticker_hash: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct HistoricalWatchlistTimelineBatchMaterialization {
    pub batch_materialization_id: String,
    pub event_count: u64,
    pub materializations: Vec<HistoricalWatchlistTimelineMaterialization>,
    pub schema_version: u16,
    pub source_revision: SourceRevision,
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
    bars: Option<SharedBarStore>,
    calculators: HashMap<String, BarIndicatorCalculator>,
    changed_indicator_tickers: BTreeSet<String>,
    indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    last_base_indicators: HashMap<String, IndicatorRow>,
    latest_indicators: HashMap<String, IndicatorRow>,
    market_signals: MarketSignalEngine,
    market_state: SharedMarketState,
    microstructure: HashMap<String, MicrostructureIntervalWindow>,
    one_second_trade_volumes: HashMap<String, VecDeque<(i64, f64)>>,
    recent_signal_events: VecDeque<MarketSignalEvent>,
    relative_volume_baselines: HashMap<(NaiveDate, String), Arc<Vec<f64>>>,
    relative_volume_evidence: HashMap<(NaiveDate, String), RelativeVolumeRevisionEvidence>,
    trade_rules: qmd_core::bars::TradeAggregationRules,
}

#[derive(Default)]
struct ScannerWorkerResult {
    active_signals: Vec<MarketSignalEvent>,
    event_count: u64,
    indicators: Vec<IndicatorRow>,
    market_rows: Vec<HistoricalScannerMarketRow>,
    recent_signal_events: Vec<MarketSignalEvent>,
}

impl ScannerWorkerResult {
    fn extend(&mut self, snapshot: HistoricalScannerDerivedSnapshot) {
        self.active_signals.extend(snapshot.active_signals);
        self.indicators.extend(snapshot.indicators);
        self.market_rows.extend(snapshot.market_rows);
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
        Self::new_with_profile(trade_rules, indicator_references, true)
    }

    fn new_market_only(
        source: &HistoricalEventSource,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    ) -> Self {
        Self::new_market_only_with_trade_rules(
            source.trade_aggregation_rules(),
            indicator_references,
        )
    }

    fn new_market_only_with_trade_rules(
        trade_rules: TradeAggregationRules,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
    ) -> Self {
        Self::new_with_profile(trade_rules, indicator_references, false)
    }

    fn new_with_profile(
        trade_rules: TradeAggregationRules,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
        derived_enabled: bool,
    ) -> Self {
        let timeframes = derived_enabled
            .then(|| {
                SCANNER_TIMEFRAMES
                    .iter()
                    .map(|value| (*value).to_string())
                    .collect()
            })
            .unwrap_or_default();
        Self::new_with_timeframes(trade_rules, indicator_references, timeframes)
    }

    fn new_with_timeframes(
        trade_rules: TradeAggregationRules,
        indicator_references: HashMap<String, MarketStructureReferenceLevels>,
        timeframes: Vec<String>,
    ) -> Self {
        Self {
            active_signals: HashMap::new(),
            aggregates: HashMap::new(),
            bars: (!timeframes.is_empty()).then(|| {
                SharedBarStore::new_without_structure(timeframes, 2, 1, trade_rules.clone())
            }),
            calculators: HashMap::new(),
            changed_indicator_tickers: BTreeSet::new(),
            indicator_references,
            last_base_indicators: HashMap::new(),
            latest_indicators: HashMap::new(),
            market_signals: MarketSignalEngine::default(),
            market_state: SharedMarketState::new(),
            microstructure: HashMap::new(),
            one_second_trade_volumes: HashMap::new(),
            recent_signal_events: VecDeque::with_capacity(SIGNAL_EVENT_LIMIT),
            relative_volume_baselines: HashMap::new(),
            relative_volume_evidence: HashMap::new(),
            trade_rules,
        }
    }

    async fn apply_event(&mut self, event: MarketEvent) -> Result<(), String> {
        let ticker = event.ticker().to_ascii_uppercase();
        if let MarketEvent::Trade(trade) = &event {
            let rule = self.trade_rules.resolve(&trade.conditions, trade.ts);
            if rule.update_volume && trade.size.is_finite() && trade.size > 0.0 {
                let second = trade.ts.timestamp();
                let buckets = self
                    .one_second_trade_volumes
                    .entry(ticker.clone())
                    .or_default();
                if let Some((latest_second, volume)) = buckets.back_mut() {
                    if *latest_second == second {
                        *volume += trade.size;
                    } else {
                        buckets.push_back((second, trade.size));
                    }
                } else {
                    buckets.push_back((second, trade.size));
                }
                while buckets.len() > 3 {
                    buckets.pop_front();
                }
            }
        }
        self.market_state.apply_event(&event).await;
        let Some(bars) = self.bars.clone() else {
            return Ok(());
        };
        self.microstructure
            .entry(ticker)
            .or_default()
            .apply_event(&event);
        for bar in bars.apply_event(&event).await {
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
        let indicator = self
            .latest_indicators
            .get(&format!("{ticker}:{SCANNER_INDICATOR_TIMEFRAME}"));
        let references = self
            .indicator_references
            .get(&ticker)
            .copied()
            .unwrap_or_default();
        let mut values = BTreeMap::new();
        for source in sources {
            let (source_id, dimension) = source
                .split_once("@@")
                .map_or((source.as_str(), ""), |(source_id, dimension)| {
                    (source_id, dimension)
                });
            let (interval, aggregation) = dimension
                .split_once("##")
                .map_or((dimension, ""), |(interval, aggregation)| {
                    (interval, aggregation)
                });
            let interval_indicator = if interval.is_empty() {
                None
            } else {
                self.latest_indicators
                    .get(&format!("{ticker}:{}", interval.to_ascii_lowercase()))
            };
            let value = if source_id == "volume_rate_ratio"
                && interval.eq_ignore_ascii_case("1s")
                && aggregation.is_empty()
            {
                self.one_second_volume_rate_ratio(&ticker, as_of)
                    .map(Value::from)
            } else if let Some(interval_row) = interval_indicator {
                indicator_source_value(interval_row, source_id, aggregation)
            } else {
                match source_id {
                    "market.last_price"
                        if market.last_price.is_finite() && market.last_price > 0.0 =>
                    {
                        Some(market.last_price)
                    }
                    "market.volume" if market.day_volume.is_finite() => Some(market.day_volume),
                    "market.session_dollar_volume" if market.day_dollar_volume.is_finite() => {
                        Some(market.day_dollar_volume)
                    }
                    "market.trade_rate_10s" if market.trade_rate_10s.is_finite() => {
                        Some(market.trade_rate_10s)
                    }
                    "market.trade_rate_60s" if market.trade_rate_60s.is_finite() => {
                        Some(market.trade_rate_60s)
                    }
                    "market.liquidity_score" if market.liquidity_score.is_finite() => {
                        Some(market.liquidity_score)
                    }
                    "liquidity-rank" | "market.liquidity_rank" if market.liquidity_rank > 0 => {
                        Some(f64::from(market.liquidity_rank))
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
                    "market.change_actual"
                        if market.last_price.is_finite()
                            && market.last_price > 0.0
                            && references.previous_session_close.is_finite()
                            && references.previous_session_close > 0.0 =>
                    {
                        Some(market.last_price - references.previous_session_close)
                    }
                    "market.relative_volume" => {
                        let session = session_date(as_of);
                        let bucket = aligned_volume_bucket(as_of);
                        self.relative_volume_baselines
                            .get(&(session, ticker.clone()))
                            .and_then(|profile| bucket.and_then(|index| profile.get(index)))
                            .copied()
                            .filter(|baseline| baseline.is_finite() && *baseline > 0.0)
                            .map(|baseline| market.day_volume / baseline)
                    }
                    "market.spread_bps"
                        if market.last_price > 0.0
                            && market.ask >= market.bid
                            && market.bid > 0.0 =>
                    {
                        Some((market.ask - market.bid) / market.last_price * 10_000.0)
                    }
                    "quote.bid_price" if market.bid > 0.0 => Some(market.bid),
                    "quote.ask_price" if market.ask > 0.0 => Some(market.ask),
                    _ => None,
                }
                .map(Value::from)
            };
            if let Some(value) = value {
                values.insert(source.clone(), value);
            }
        }
        Ok(Some(WatchlistCandidate { ticker, values }))
    }

    fn one_second_volume_rate_ratio(&self, ticker: &str, as_of: DateTime<Utc>) -> Option<f64> {
        let completed_second = as_of.timestamp().saturating_sub(1);
        let buckets = self.one_second_trade_volumes.get(ticker)?;
        let current = buckets
            .iter()
            .find_map(|(second, volume)| (*second == completed_second).then_some(*volume))?;
        let prior = buckets.iter().find_map(|(second, volume)| {
            (*second == completed_second.saturating_sub(1)).then_some(*volume)
        })?;
        (current.is_finite() && prior.is_finite() && current >= 0.0 && prior > 0.0)
            .then_some(current / prior)
    }

    async fn liquidity_score(&self, ticker: &str, as_of: DateTime<Utc>) -> Option<f64> {
        self.market_state
            .ticker_snapshot_at(ticker, as_of)
            .await
            .map(|market| market.liquidity_score)
            .filter(|value| value.is_finite())
    }

    fn has_relative_volume_baseline(&self, ticker: &str, as_of: DateTime<Utc>) -> bool {
        self.relative_volume_baselines
            .contains_key(&(session_date(as_of), ticker.to_ascii_uppercase()))
    }

    fn install_relative_volume_baselines(
        &mut self,
        as_of: DateTime<Utc>,
        baselines: HashMap<String, Vec<f64>>,
        evidence: &RelativeVolumeRevisionEvidence,
    ) {
        let session = session_date(as_of);
        for (ticker, profile) in baselines {
            let key = (session, ticker.to_ascii_uppercase());
            self.relative_volume_baselines
                .insert(key.clone(), Arc::new(profile));
            self.relative_volume_evidence.insert(key, evidence.clone());
        }
    }

    fn relative_volume_evidence_for(
        &self,
        tickers: &BTreeSet<String>,
        as_of: DateTime<Utc>,
    ) -> Vec<RelativeVolumeRevisionEvidence> {
        let session = session_date(as_of);
        let mut unique = BTreeMap::new();
        for ticker in tickers {
            if let Some(evidence) = self
                .relative_volume_evidence
                .get(&(session, ticker.to_ascii_uppercase()))
            {
                unique
                    .entry((
                        evidence.session_date,
                        evidence.ticker_hash.clone(),
                        evidence.source_revision.token.clone(),
                    ))
                    .or_insert_with(|| evidence.clone());
            }
        }
        unique.into_values().collect()
    }

    async fn finalize(&mut self, as_of: DateTime<Utc>) -> Result<(), String> {
        let Some(bars) = self.bars.clone() else {
            return Ok(());
        };
        for bar in bars.finalize_due(as_of).await {
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
            published.qmd_structure_unified_levels.clear();
            self.latest_indicators
                .insert(format!("{ticker}:{timeframe}"), published);
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
        self.latest_indicators
            .insert(format!("{ticker}:{timeframe}"), indicator.clone());
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

    async fn into_snapshot(
        self,
        as_of: DateTime<Utc>,
        event_count: u64,
        source_revision: SourceRevision,
    ) -> HistoricalScannerDerivedSnapshot {
        let market_rows = self
            .market_state
            .scanner_snapshot_at(as_of, usize::MAX)
            .await
            .rows
            .into_iter()
            .map(|market| HistoricalScannerMarketRow {
                previous_close: self
                    .indicator_references
                    .get(&market.ticker)
                    .map(|levels| levels.previous_session_close)
                    .filter(|value| value.is_finite() && *value > 0.0),
                market,
            })
            .collect::<Vec<_>>();
        let mut indicators = self
            .latest_indicators
            .into_values()
            .filter(|row| row.timeframe == SCANNER_INDICATOR_TIMEFRAME)
            .collect::<Vec<_>>();
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
            market_rows,
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
            result.extend(
                engine
                    .into_snapshot(as_of, 0, empty_source_revision())
                    .await,
            );
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

#[derive(Clone, Copy, Debug, Default, Eq, PartialEq)]
enum RuleEventRequirement {
    #[default]
    Neutral,
    Quote,
    Trade,
    Both,
}

impl RuleEventRequirement {
    fn union(self, other: Self) -> Self {
        match (self, other) {
            (Self::Neutral, value) | (value, Self::Neutral) => value,
            (Self::Quote, Self::Quote) => Self::Quote,
            (Self::Trade, Self::Trade) => Self::Trade,
            _ => Self::Both,
        }
    }

    fn event_type_filter(self) -> Option<u8> {
        match self {
            Self::Quote => Some(0),
            Self::Trade => Some(1),
            Self::Neutral | Self::Both => None,
        }
    }
}

fn combined_rule_event_type_filter<'a>(
    plans: impl Iterator<Item = &'a HistoricalWatchlistPlan>,
) -> Option<u8> {
    plans
        .fold(RuleEventRequirement::Neutral, |required, plan| {
            required.union(rule_event_requirement(plan))
        })
        .event_type_filter()
}

fn rule_event_type_filter(plan: &HistoricalWatchlistPlan) -> Option<u8> {
    rule_event_requirement(plan).event_type_filter()
}

fn rule_event_requirement(plan: &HistoricalWatchlistPlan) -> RuleEventRequirement {
    plan.rule_sets
        .iter()
        .flat_map(|rule| {
            rule.get("conditions")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
        })
        .filter(|condition| {
            condition
                .get("enabled")
                .and_then(Value::as_bool)
                .unwrap_or(true)
        })
        .flat_map(|condition| {
            ["left_source_id", "right_source_id"]
                .into_iter()
                .filter_map(|key| condition.get(key).and_then(Value::as_str))
        })
        .filter(|source_id| !source_id.trim().is_empty())
        .fold(RuleEventRequirement::Neutral, |required, source_id| {
            required.union(source_event_requirement(source_id))
        })
}

fn source_event_requirement(source_id: &str) -> RuleEventRequirement {
    let source = source_id
        .split("@@")
        .next()
        .unwrap_or(source_id)
        .trim()
        .to_ascii_lowercase();
    if source.starts_with("quote.")
        || matches!(
            source.as_str(),
            "market.bid_price"
                | "market.ask_price"
                | "market.spread"
                | "market.spread_bps"
                | "market.microprice"
                | "market.imbalance"
                | "market.quote_rate"
        )
    {
        return RuleEventRequirement::Quote;
    }
    if source.starts_with("bar.")
        || source.starts_with("trade.")
        || source.starts_with("price_change")
        || source.starts_with("volume")
        || source.starts_with("transaction")
        || matches!(
            source.as_str(),
            "market.last_price"
                | "market.volume"
                | "market.session_dollar_volume"
                | "market.trade_rate_10s"
                | "market.trade_rate_60s"
                | "market.liquidity_score"
                | "market.session_volume"
                | "market.session_vwap"
                | "market.session_change"
                | "market.session_change_percent"
        )
    {
        return RuleEventRequirement::Trade;
    }
    if source.starts_with("time.")
        || source.starts_with("calendar.")
        || source.starts_with("reference.")
        || source.starts_with("company.")
        || source.starts_with("security.")
        || matches!(
            source.as_str(),
            "market.session_phase" | "market.market_status" | "market.trading_halted"
        )
    {
        return RuleEventRequirement::Neutral;
    }
    // Unknown computed fields fail open to the complete market-event stream;
    // filtering is only safe when every rule operand has a known event family.
    RuleEventRequirement::Both
}

pub async fn materialize_watchlist_timeline(
    config: HistoricalGatewayConfig,
    source: HistoricalEventSource,
    request: HistoricalWatchlistTimelineRequest,
) -> Result<HistoricalWatchlistTimelineMaterialization, String> {
    if request.plan.qmd_sources.iter().any(|source| {
        matches!(
            source.as_str(),
            "indicator.vwap.value" | "market.relative_volume"
        )
    }) {
        return materialize_watchlist_timelines(
            config,
            source,
            HistoricalWatchlistTimelineBatchRequest {
                requests: vec![request],
            },
        )
        .await?
        .materializations
        .into_iter()
        .next()
        .ok_or_else(|| "historical Watchlist batch returned no materialization".to_string());
    }
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
    let source_tickers = request
        .source_tickers
        .iter()
        .map(|ticker| ticker.trim().to_ascii_uppercase())
        .filter(|ticker| !ticker.is_empty())
        .collect::<BTreeSet<_>>();
    if source_tickers.len() > 5_000 {
        return Err("historical Watchlist source scope exceeds ticker_limit=5000".to_string());
    }
    let window = EventWindow {
        start,
        end,
        tickers: source_tickers.iter().cloned().collect(),
    };
    let source_plan = source.source_plan(&window).await?;
    if !source_plan.complete_for_history {
        return Err(
            "historical Watchlist timeline requires a complete pinned market-event window"
                .to_string(),
        );
    }
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
    let mut batches = source.stream_ordered_filtered(
        window,
        config.batch_size.max(100_000),
        source_revision.live_continuation_sequence,
        rule_event_type_filter(&request.plan),
    )?;
    while let Some(events) = batches.recv().await {
        for compact in events? {
            let event = source.market_event(&compact);
            while evaluation_index < validation.evaluation_count
                && plan_evaluation_clock(&request.plan, evaluation_index)? <= event.ts()
            {
                let clock = plan_evaluation_clock(&request.plan, evaluation_index)?;
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
        let clock = plan_evaluation_clock(&request.plan, evaluation_index)?;
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
    let projection_tickers = request
        .projection_tickers
        .iter()
        .map(|ticker| ticker.trim().to_ascii_uppercase())
        .filter(|ticker| !ticker.is_empty())
        .collect::<BTreeSet<_>>();
    if projection_tickers.len() > 5_000 {
        return Err(
            "historical Watchlist transition projection exceeds ticker_limit=5000".to_string(),
        );
    }
    if !projection_tickers.is_empty() {
        for chunk in &mut chunks {
            chunk.transitions.retain(|transition| {
                transition.event != "rank_changed"
                    && projection_tickers.contains(&transition.ticker.to_ascii_uppercase())
            });
        }
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
        &[],
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
        projection_complete: true,
        projection_mode: if projection_tickers.is_empty() {
            "full"
        } else {
            "membership_transitions"
        },
        projection_tickers: projection_tickers.into_iter().collect(),
        relative_volume_revisions: Vec::new(),
        schema_version: 1,
        source_revision,
        source_tickers: source_tickers.into_iter().collect(),
        transition_count,
        watchlist_id: request.plan.watchlist_id,
    })
}

struct BatchPlanRuntime<'a> {
    boundaries: Vec<(DateTime<Utc>, String)>,
    boundary_index: usize,
    chunks: Vec<WatchlistTimelineChunk>,
    dirty: BTreeSet<String>,
    evaluation_index: u64,
    external: ExternalIntervalIndex,
    focused_seed_limit: usize,
    qmd_sources: BTreeSet<String>,
    reducer: Option<WatchlistTimelineReducer<'a>>,
    relative_volume_revisions: Vec<RelativeVolumeRevisionEvidence>,
    request: &'a HistoricalWatchlistTimelineRequest,
    seed_tickers: BTreeSet<String>,
    validation: HistoricalWatchlistPlanValidation,
}

#[derive(Clone, Debug)]
struct CoreLiquidityKey {
    normalized_score: f64,
    ticker: String,
}

impl PartialEq for CoreLiquidityKey {
    fn eq(&self, other: &Self) -> bool {
        self.normalized_score.to_bits() == other.normalized_score.to_bits()
            && self.ticker == other.ticker
    }
}

impl Eq for CoreLiquidityKey {}

impl PartialOrd for CoreLiquidityKey {
    fn partial_cmp(&self, other: &Self) -> Option<std::cmp::Ordering> {
        Some(self.cmp(other))
    }
}

impl Ord for CoreLiquidityKey {
    fn cmp(&self, other: &Self) -> std::cmp::Ordering {
        self.normalized_score
            .total_cmp(&other.normalized_score)
            .then_with(|| self.ticker.cmp(&other.ticker))
    }
}

#[derive(Default)]
struct CoreLiquidityIndex {
    by_ticker: HashMap<String, f64>,
    ranked: BTreeSet<CoreLiquidityKey>,
}

impl CoreLiquidityIndex {
    fn update(&mut self, ticker: String, score: Option<f64>) {
        if let Some(prior) = self.by_ticker.remove(&ticker) {
            self.ranked.remove(&CoreLiquidityKey {
                normalized_score: -prior,
                ticker: ticker.clone(),
            });
        }
        if let Some(score) = score.filter(|value| value.is_finite()) {
            self.by_ticker.insert(ticker.clone(), score);
            self.ranked.insert(CoreLiquidityKey {
                normalized_score: -score,
                ticker,
            });
        }
    }

    fn top(&self, limit: usize) -> BTreeSet<String> {
        self.ranked
            .iter()
            .take(limit)
            .map(|key| key.ticker.clone())
            .collect()
    }
}

pub async fn materialize_watchlist_timelines(
    config: HistoricalGatewayConfig,
    source: HistoricalEventSource,
    batch: HistoricalWatchlistTimelineBatchRequest,
) -> Result<HistoricalWatchlistTimelineBatchMaterialization, String> {
    if batch.requests.is_empty() || batch.requests.len() > 64 {
        return Err("historical Watchlist batch requires between one and 64 plans".to_string());
    }
    let first = &batch.requests[0].plan;
    let start = first
        .start
        .parse::<DateTime<Utc>>()
        .map_err(|error| format!("invalid historical Watchlist start: {error}"))?;
    let end = first
        .end
        .parse::<DateTime<Utc>>()
        .map_err(|error| format!("invalid historical Watchlist end: {error}"))?;
    let mut watchlist_ids = BTreeSet::new();
    let mut runtimes = Vec::with_capacity(batch.requests.len());
    let mut batch_evaluations = 0_u64;
    let mut batch_membership_slots = 0_u64;
    let mut batch_focused_slots = 0_u64;
    for request in &batch.requests {
        if request.plan.start != first.start || request.plan.end != first.end {
            return Err(
                "historical Watchlist batch plans must share exact replay bounds".to_string(),
            );
        }
        if !watchlist_ids.insert(request.plan.watchlist_id.as_str()) {
            return Err(
                "historical Watchlist batch watchlist_id values must be unique".to_string(),
            );
        }
        let validation = validate_plan(&request.plan)?;
        let focused = request.plan.qmd_sources.iter().any(|source| {
            matches!(
                source.as_str(),
                "indicator.vwap.value" | "market.relative_volume"
            )
        });
        let focused_seed_limit = if focused {
            request
                .plan
                .maximum_size
                .saturating_mul(request.plan.focused_seed_multiplier)
        } else {
            0
        };
        if focused_seed_limit > ALIGNED_VOLUME_MAX_TICKERS {
            return Err(format!(
                "historical Watchlist focused seed exceeds ticker_limit={ALIGNED_VOLUME_MAX_TICKERS}"
            ));
        }
        batch_focused_slots = batch_focused_slots.saturating_add(
            validation
                .evaluation_count
                .saturating_mul(focused_seed_limit as u64),
        );
        if batch_focused_slots > WATCHLIST_BATCH_MAX_FOCUSED_SLOTS {
            return Err(format!(
                "historical Watchlist batch exceeds focused_slot_limit={WATCHLIST_BATCH_MAX_FOCUSED_SLOTS}"
            ));
        }
        batch_evaluations = batch_evaluations.saturating_add(validation.evaluation_count);
        // The reducer carries one bounded membership state per materialization
        // chunk. Total session evaluations do not allocate membership slots at
        // once, so budget the peak chunk working set rather than multiplying by
        // the full replay duration.
        batch_membership_slots = batch_membership_slots.saturating_add(
            request
                .plan
                .max_evaluations_per_chunk
                .saturating_mul(request.plan.maximum_size as u64),
        );
        if batch_evaluations > WATCHLIST_BATCH_MAX_EVALUATIONS
            || batch_membership_slots > WATCHLIST_BATCH_MAX_MEMBERSHIP_SLOTS
        {
            return Err(format!(
                "historical Watchlist batch exceeds evaluation_limit={} or membership_slot_limit={}",
                WATCHLIST_BATCH_MAX_EVALUATIONS, WATCHLIST_BATCH_MAX_MEMBERSHIP_SLOTS
            ));
        }
        let (external, boundaries) = prepare_external_features(request, start, end)?;
        let dirty = external.keys().cloned().collect();
        runtimes.push(BatchPlanRuntime {
            boundaries,
            boundary_index: 0,
            chunks: Vec::new(),
            dirty,
            evaluation_index: 0,
            external,
            focused_seed_limit,
            qmd_sources: request.plan.qmd_sources.iter().cloned().collect(),
            reducer: Some(WatchlistTimelineReducer::new(&request.plan, None)?),
            relative_volume_revisions: Vec::new(),
            request,
            seed_tickers: BTreeSet::new(),
            validation,
        });
    }

    let source_tickers = batch
        .requests
        .iter()
        .flat_map(|request| request.source_tickers.iter())
        .map(|ticker| ticker.trim().to_ascii_uppercase())
        .filter(|ticker| !ticker.is_empty())
        .collect::<BTreeSet<_>>();
    if source_tickers.len() > 5_000 {
        return Err(
            "historical Watchlist batch source scope exceeds ticker_limit=5000".to_string(),
        );
    }
    // Plans scoped to source-native Signal Stream clocks do not depend on
    // market events after their final evaluation window.  Keep the pinned
    // source authority over the complete requested interval, but stop the
    // physical replay at the last causal clock.  Previously a premarket-only
    // strategy still decoded the entire 04:00-20:00 session.
    let replay_end = replay_end_for_evaluation_windows(
        batch
            .requests
            .iter()
            .flat_map(|request| request.plan.evaluation_windows.iter()),
        end,
    )?;
    let authority_window = EventWindow {
        start,
        end: replay_end,
        tickers: source_tickers.iter().cloned().collect(),
    };
    let source_plan = source.source_plan(&authority_window).await?;
    if !source_plan.complete_for_history {
        return Err(
            "historical Watchlist batch requires a complete pinned market-event window".to_string(),
        );
    }
    let source_revision = source.source_revision(&authority_window).await?;
    if !source_revision.complete_for_history || !source_revision.request_complete {
        return Err(
            "historical Watchlist batch requires a complete pinned market-event window".to_string(),
        );
    }
    let references = source
        .market_structure_reference_levels_all(start)
        .await
        .map_err(|error| format!("historical Watchlist daily references unavailable: {error}"))?;
    let shard_count = config.scanner_shard_count.max(1);
    let mut reference_partitions = vec![HashMap::new(); shard_count];
    for (ticker, levels) in references {
        reference_partitions[scanner_shard_index(&ticker, shard_count)].insert(ticker, levels);
    }
    let derived_timeframes = runtimes
        .iter()
        .flat_map(|runtime| watchlist_derived_timeframes(&runtime.qmd_sources))
        .collect::<BTreeSet<_>>();
    let mut engines = reference_partitions
        .into_iter()
        .map(|references| {
            if derived_timeframes.is_empty() {
                CrossSectionEngine::new_market_only(&source, references)
            } else {
                CrossSectionEngine::new_with_timeframes(
                    source.trade_aggregation_rules(),
                    references,
                    derived_timeframes.iter().cloned().collect(),
                )
            }
        })
        .collect::<Vec<_>>();
    let mut known_tickers = BTreeSet::new();
    let mut core_liquidity = CoreLiquidityIndex::default();
    let mut recent_until = HashMap::<String, DateTime<Utc>>::new();
    let mut event_count = 0_u64;
    let mut reference_session = session_date(start);
    let mut batches = source.stream_ordered_filtered(
        EventWindow {
            start,
            end: replay_end,
            tickers: source_tickers.iter().cloned().collect(),
        },
        config.batch_size.max(100_000),
        source_revision.live_continuation_sequence,
        combined_rule_event_type_filter(batch.requests.iter().map(|request| &request.plan)),
    )?;
    while let Some(events) = batches.recv().await {
        for compact in events? {
            let event = source.market_event(&compact);
            while let Some(clock) = next_batch_clock(&runtimes)? {
                if clock > event.ts() {
                    break;
                }
                advance_batch_clock(
                    &config,
                    &source,
                    &mut engines,
                    &mut runtimes,
                    clock,
                    &known_tickers,
                    &mut core_liquidity,
                    &mut recent_until,
                    &mut reference_session,
                )
                .await?;
            }
            let ticker = event.ticker().to_ascii_uppercase();
            known_tickers.insert(ticker.clone());
            recent_until.insert(ticker.clone(), event.ts() + chrono::Duration::seconds(61));
            for runtime in &mut runtimes {
                runtime.dirty.insert(ticker.clone());
            }
            let shard = scanner_shard_index(&ticker, engines.len());
            engines[shard].apply_event(event).await?;
            event_count = event_count.saturating_add(1);
            if event_count > config.scanner_max_events_per_snapshot as u64 {
                return Err(format!(
                    "historical Watchlist batch replay exceeded event_limit={}",
                    config.scanner_max_events_per_snapshot
                ));
            }
        }
    }
    while let Some(clock) = next_batch_clock(&runtimes)? {
        advance_batch_clock(
            &config,
            &source,
            &mut engines,
            &mut runtimes,
            clock,
            &known_tickers,
            &mut core_liquidity,
            &mut recent_until,
            &mut reference_session,
        )
        .await?;
    }

    let mut materializations = Vec::with_capacity(runtimes.len());
    for mut runtime in runtimes {
        let projection_tickers = runtime
            .request
            .projection_tickers
            .iter()
            .map(|ticker| ticker.trim().to_ascii_uppercase())
            .filter(|ticker| !ticker.is_empty())
            .collect::<BTreeSet<_>>();
        if projection_tickers.len() > 5_000 {
            return Err(
                "historical Watchlist transition projection exceeds ticker_limit=5000".to_string(),
            );
        }
        if !projection_tickers.is_empty() {
            for chunk in &mut runtime.chunks {
                chunk.transitions.retain(|transition| {
                    transition.event != "rank_changed"
                        && projection_tickers.contains(&transition.ticker.to_ascii_uppercase())
                });
            }
        }
        let transition_count = runtime
            .chunks
            .iter()
            .map(|chunk| chunk.transitions.len())
            .sum::<usize>();
        let mut external_revisions = runtime.request.external_feature_revisions.clone();
        external_revisions.sort_by(|left, right| left.field_id.cmp(&right.field_id));
        let mut relative_volume_revisions = runtime.relative_volume_revisions;
        relative_volume_revisions.sort_by(|left, right| {
            left.session_date
                .cmp(&right.session_date)
                .then_with(|| left.ticker_hash.cmp(&right.ticker_hash))
                .then_with(|| left.source_revision.token.cmp(&right.source_revision.token))
        });
        let materialization_id = materialization_id(
            &runtime.request.plan.plan_hash,
            &source_revision,
            &external_revisions,
            &relative_volume_revisions,
        )?;
        materializations.push(HistoricalWatchlistTimelineMaterialization {
            calculation_revision: HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION,
            cadence_ms: runtime.request.plan.cadence_ms,
            chunks: runtime.chunks,
            engine_version: qmd_core::market_signal::MARKET_SIGNAL_ENGINE_VERSION,
            evaluation_count: runtime.validation.evaluation_count,
            event_count,
            external_feature_revisions: external_revisions,
            materialization_id,
            plan_hash: runtime.request.plan.plan_hash.clone(),
            projection_complete: true,
            projection_mode: if projection_tickers.is_empty() {
                "full"
            } else {
                "membership_transitions"
            },
            projection_tickers: projection_tickers.into_iter().collect(),
            relative_volume_revisions,
            schema_version: 1,
            source_revision: source_revision.clone(),
            source_tickers: source_tickers.iter().cloned().collect(),
            transition_count,
            watchlist_id: runtime.request.plan.watchlist_id.clone(),
        });
    }
    let batch_materialization_id = batch_materialization_id(&materializations, &source_revision)?;
    Ok(HistoricalWatchlistTimelineBatchMaterialization {
        batch_materialization_id,
        event_count,
        materializations,
        schema_version: 1,
        source_revision,
    })
}

fn replay_end_for_evaluation_windows<'a>(
    windows: impl Iterator<Item = &'a WatchlistEvaluationWindow>,
    requested_end: DateTime<Utc>,
) -> Result<DateTime<Utc>, String> {
    Ok(windows
        .map(|window| {
            window
                .end
                .parse::<DateTime<Utc>>()
                .map_err(|error| format!("invalid historical Watchlist evaluation end: {error}"))
        })
        .collect::<Result<Vec<_>, _>>()?
        .into_iter()
        .max()
        .unwrap_or(requested_end)
        .min(requested_end))
}

fn next_batch_clock(runtimes: &[BatchPlanRuntime<'_>]) -> Result<Option<DateTime<Utc>>, String> {
    let mut next = None;
    for runtime in runtimes {
        if runtime.evaluation_index >= runtime.validation.evaluation_count {
            continue;
        }
        let clock = plan_evaluation_clock(&runtime.request.plan, runtime.evaluation_index)?;
        next = Some(next.map_or(clock, |prior: DateTime<Utc>| prior.min(clock)));
    }
    Ok(next)
}

async fn advance_batch_clock(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    engines: &mut [CrossSectionEngine],
    runtimes: &mut [BatchPlanRuntime<'_>],
    clock: DateTime<Utc>,
    known_tickers: &BTreeSet<String>,
    core_liquidity: &mut CoreLiquidityIndex,
    recent_until: &mut HashMap<String, DateTime<Utc>>,
    reference_session: &mut NaiveDate,
) -> Result<(), String> {
    let mut rate_dirty = recent_until.keys().cloned().collect::<BTreeSet<_>>();
    let expired = recent_until
        .iter()
        .filter(|(_, until)| **until < clock)
        .map(|(ticker, _)| ticker.clone())
        .collect::<Vec<_>>();
    for ticker in expired {
        rate_dirty.insert(ticker.clone());
        recent_until.remove(&ticker);
    }
    let session = session_date(clock);
    if session != *reference_session {
        let references = source
            .market_structure_reference_levels_all(clock)
            .await
            .map_err(|error| {
                format!("historical Watchlist daily references unavailable: {error}")
            })?;
        let mut partitions = vec![HashMap::new(); engines.len()];
        for (ticker, levels) in references {
            partitions[scanner_shard_index(&ticker, engines.len())].insert(ticker, levels);
        }
        for (engine, references) in engines.iter_mut().zip(partitions) {
            engine.replace_indicator_references(references);
        }
        *reference_session = session;
        rate_dirty.extend(known_tickers.iter().cloned());
    }
    for engine in engines.iter_mut() {
        engine.finalize(clock).await?;
        rate_dirty.extend(engine.take_changed_indicator_tickers());
    }
    let mut core_dirty = rate_dirty.clone();
    for runtime in runtimes.iter() {
        core_dirty.extend(runtime.dirty.iter().cloned());
    }
    for ticker in core_dirty {
        let shard = scanner_shard_index(&ticker, engines.len());
        let score = engines[shard].liquidity_score(&ticker, clock).await;
        core_liquidity.update(ticker, score);
    }
    for runtime in runtimes {
        runtime.dirty.extend(rate_dirty.iter().cloned());
        if runtime.evaluation_index >= runtime.validation.evaluation_count
            || plan_evaluation_clock(&runtime.request.plan, runtime.evaluation_index)? != clock
        {
            continue;
        }
        advance_external_boundaries(
            &runtime.boundaries,
            &mut runtime.boundary_index,
            clock,
            &mut runtime.dirty,
        );
        let focused_seed = if runtime.focused_seed_limit > 0 {
            let mut seed = core_liquidity.top(runtime.focused_seed_limit);
            if let Some(reducer) = runtime.reducer.as_ref() {
                seed.extend(reducer.member_tickers().cloned());
            }
            seed.extend(
                runtime
                    .request
                    .plan
                    .manual_inclusions
                    .iter()
                    .map(|ticker| ticker.to_ascii_uppercase()),
            );
            for ticker in &runtime.request.plan.manual_exclusions {
                seed.remove(&ticker.to_ascii_uppercase());
            }
            runtime
                .dirty
                .extend(runtime.seed_tickers.symmetric_difference(&seed).cloned());
            runtime.seed_tickers = seed;
            Some(&runtime.seed_tickers)
        } else {
            None
        };
        if runtime.qmd_sources.contains("market.relative_volume") {
            let mut preload = core_liquidity.top(
                runtime
                    .focused_seed_limit
                    .saturating_mul(2)
                    .min(ALIGNED_VOLUME_MAX_TICKERS),
            );
            preload.extend(runtime.seed_tickers.iter().cloned());
            let missing = preload
                .iter()
                .filter(|ticker| {
                    let shard = scanner_shard_index(ticker, engines.len());
                    !engines[shard].has_relative_volume_baseline(ticker, clock)
                })
                .cloned()
                .collect::<BTreeSet<_>>();
            if !missing.is_empty() {
                let (baselines, revision) =
                    load_aligned_volume_baselines(config, source, &missing, clock).await?;
                let mut partitions = vec![HashMap::new(); engines.len()];
                for (ticker, profile) in baselines {
                    let shard = scanner_shard_index(&ticker, engines.len());
                    partitions[shard].insert(ticker, profile);
                }
                for (engine, baselines) in engines.iter_mut().zip(partitions) {
                    if !baselines.is_empty() {
                        engine.install_relative_volume_baselines(clock, baselines, &revision);
                    }
                }
            }
            let mut seed_by_shard = vec![BTreeSet::new(); engines.len()];
            for ticker in &runtime.seed_tickers {
                seed_by_shard[scanner_shard_index(ticker, engines.len())].insert(ticker.clone());
            }
            for evidence in engines
                .iter()
                .zip(seed_by_shard.iter())
                .flat_map(|(engine, tickers)| engine.relative_volume_evidence_for(tickers, clock))
            {
                if !runtime.relative_volume_revisions.iter().any(|existing| {
                    existing.session_date == evidence.session_date
                        && existing.ticker_hash == evidence.ticker_hash
                        && existing.source_revision.token == evidence.source_revision.token
                }) {
                    runtime.relative_volume_revisions.push(evidence);
                }
            }
        }
        apply_timeline_candidates_sharded(
            engines,
            runtime
                .reducer
                .as_mut()
                .ok_or_else(|| "historical Watchlist batch reducer is unavailable".to_string())?,
            &runtime.external,
            &runtime.qmd_sources,
            focused_seed,
            clock,
            &mut runtime.dirty,
        )
        .await?;
        runtime.evaluation_index += 1;
        finish_reducer_chunk_if_due(
            &runtime.request.plan,
            runtime.validation.evaluation_count,
            runtime.evaluation_index,
            &mut runtime.reducer,
            &mut runtime.chunks,
        )?;
    }
    Ok(())
}

fn batch_materialization_id(
    materializations: &[HistoricalWatchlistTimelineMaterialization],
    source_revision: &SourceRevision,
) -> Result<String, String> {
    let payload = serde_json::json!({
        "materializations": materializations
            .iter()
            .map(|row| (&row.watchlist_id, &row.materialization_id))
            .collect::<Vec<_>>(),
        "source_revision": source_revision,
    });
    let encoded = serde_json::to_vec(&payload)
        .map_err(|error| format!("historical Watchlist batch identity encoding failed: {error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
}

fn aligned_volume_bucket(clock: DateTime<Utc>) -> Option<usize> {
    let local = clock.with_timezone(&New_York);
    let seconds = local.time().num_seconds_from_midnight() as usize;
    let session_start = 4 * 60 * 60;
    let session_end = 20 * 60 * 60;
    (session_start..session_end)
        .contains(&seconds)
        .then_some((seconds - session_start) / ALIGNED_VOLUME_BUCKET_SECONDS)
}

async fn load_aligned_volume_baselines(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    tickers: &BTreeSet<String>,
    as_of: DateTime<Utc>,
) -> Result<(HashMap<String, Vec<f64>>, RelativeVolumeRevisionEvidence), String> {
    if tickers.is_empty() || tickers.len() > ALIGNED_VOLUME_MAX_TICKERS {
        return Err(format!(
            "aligned relative-volume baseline requires 1..={ALIGNED_VOLUME_MAX_TICKERS} tickers"
        ));
    }
    let sessions = source
        .completed_session_dates_before(as_of, ALIGNED_VOLUME_SESSION_COUNT)
        .await?;
    if sessions.len() != ALIGNED_VOLUME_SESSION_COUNT {
        return Err(format!(
            "aligned relative-volume baseline requires {ALIGNED_VOLUME_SESSION_COUNT} completed sessions; found {}",
            sessions.len()
        ));
    }
    let first = sessions[0];
    let last = *sessions.last().expect("completed session list is nonempty");
    let start = New_York
        .with_ymd_and_hms(first.year(), first.month(), first.day(), 4, 0, 0)
        .single()
        .ok_or_else(|| "invalid aligned-volume first session boundary".to_string())?
        .with_timezone(&Utc);
    let end = New_York
        .with_ymd_and_hms(last.year(), last.month(), last.day(), 20, 0, 0)
        .single()
        .ok_or_else(|| "invalid aligned-volume last session boundary".to_string())?
        .with_timezone(&Utc);
    let window = EventWindow {
        start,
        end,
        tickers: tickers.iter().cloned().collect(),
    };
    let source_revision = source.source_revision(&window).await?;
    if !source_revision.complete_for_history || !source_revision.request_complete {
        return Err("aligned relative-volume baseline source window is incomplete".to_string());
    }
    let session_set = sessions.iter().copied().collect::<BTreeSet<_>>();
    let rules = source.trade_aggregation_rules();
    let mut sums = tickers
        .iter()
        .map(|ticker| (ticker.clone(), vec![0.0; ALIGNED_VOLUME_BUCKET_COUNT]))
        .collect::<HashMap<_, _>>();
    let mut current_session = None;
    let mut increments = HashMap::<String, Vec<f64>>::new();
    let mut event_count = 0_u64;
    let event_limit = (config.scanner_max_events_per_snapshot as u64).saturating_mul(4);
    let mut batches = source.stream_ordered(
        window,
        config.batch_size.max(100_000),
        source_revision.live_continuation_sequence,
    )?;
    while let Some(events) = batches.recv().await {
        for compact in events? {
            let event = source.market_event(&compact);
            let event_session = session_date(event.ts());
            if current_session.is_some_and(|session| session != event_session) {
                accumulate_aligned_volume_session(&mut sums, &mut increments);
            }
            current_session = Some(event_session);
            event_count = event_count.saturating_add(1);
            if event_count > event_limit {
                return Err(format!(
                    "aligned relative-volume baseline exceeded event_limit={event_limit}"
                ));
            }
            let MarketEvent::Trade(trade) = event else {
                continue;
            };
            if !session_set.contains(&event_session)
                || !rules.resolve(&trade.conditions, trade.ts).update_volume
                || !trade.size.is_finite()
                || trade.size <= 0.0
            {
                continue;
            }
            let Some(bucket) = aligned_volume_bucket(trade.ts) else {
                continue;
            };
            increments
                .entry(trade.ticker.to_ascii_uppercase())
                .or_insert_with(|| vec![0.0; ALIGNED_VOLUME_BUCKET_COUNT])[bucket] += trade.size;
        }
    }
    accumulate_aligned_volume_session(&mut sums, &mut increments);
    for profile in sums.values_mut() {
        for value in profile {
            *value /= ALIGNED_VOLUME_SESSION_COUNT as f64;
        }
    }
    let ticker_hash = {
        let encoded = tickers.iter().cloned().collect::<Vec<_>>().join("\n");
        format!("sha256:{:x}", Sha256::digest(encoded.as_bytes()))
    };
    Ok((
        sums,
        RelativeVolumeRevisionEvidence {
            baseline_session_count: sessions.len(),
            end,
            session_date: session_date(as_of),
            source_revision,
            start,
            ticker_count: tickers.len(),
            ticker_hash,
        },
    ))
}

fn accumulate_aligned_volume_session(
    sums: &mut HashMap<String, Vec<f64>>,
    increments: &mut HashMap<String, Vec<f64>>,
) {
    for (ticker, profile) in increments.drain() {
        let Some(sum) = sums.get_mut(&ticker) else {
            continue;
        };
        let mut cumulative = 0.0;
        for (index, increment) in profile.into_iter().enumerate() {
            cumulative += increment;
            sum[index] += cumulative;
        }
    }
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
            || revision.query_plan_version != contract.query_plan_version
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
    apply_timeline_candidates(engine, reducer, external, qmd_sources, None, clock, dirty).await
}

async fn apply_timeline_candidates(
    engine: &mut CrossSectionEngine,
    reducer: &mut WatchlistTimelineReducer<'_>,
    external: &ExternalIntervalIndex,
    qmd_sources: &BTreeSet<String>,
    allowed_tickers: Option<&BTreeSet<String>>,
    clock: DateTime<Utc>,
    dirty: &mut BTreeSet<String>,
) -> Result<(), String> {
    let tickers = std::mem::take(dirty);
    let mut upserts = Vec::new();
    let mut removals = Vec::new();
    for ticker in tickers {
        if allowed_tickers.is_some_and(|allowed| !allowed.contains(&ticker)) {
            removals.push(ticker);
            continue;
        }
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

async fn apply_timeline_candidates_sharded(
    engines: &mut [CrossSectionEngine],
    reducer: &mut WatchlistTimelineReducer<'_>,
    external: &ExternalIntervalIndex,
    qmd_sources: &BTreeSet<String>,
    allowed_tickers: Option<&BTreeSet<String>>,
    clock: DateTime<Utc>,
    dirty: &mut BTreeSet<String>,
) -> Result<(), String> {
    let tickers = std::mem::take(dirty);
    let mut upserts = Vec::new();
    let mut removals = Vec::new();
    for ticker in tickers {
        if allowed_tickers.is_some_and(|allowed| !allowed.contains(&ticker)) {
            removals.push(ticker);
            continue;
        }
        let shard = scanner_shard_index(&ticker, engines.len());
        match engines[shard]
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
    let mut completed = reducer
        .take()
        .ok_or_else(|| "historical Watchlist reducer is unavailable".to_string())?
        .finish()?;
    let state = completed.next_state.clone();
    if plan.output_mode == "signal_transitions_only" {
        completed.next_state.candidates.clear();
        if evaluation_index < evaluation_count {
            completed.next_state.members.clear();
        }
    }
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
    relative_volume_revisions: &[RelativeVolumeRevisionEvidence],
) -> Result<String, String> {
    let payload = serde_json::json!({
        "calculation_revision": HISTORICAL_SCANNER_DERIVED_SCHEMA_VERSION,
        "external_feature_revisions": external_revisions,
        "plan_hash": plan_hash,
        "relative_volume_revisions": relative_volume_revisions,
        "source_revision": source_revision,
    });
    let encoded = serde_json::to_vec(&payload)
        .map_err(|error| format!("historical Watchlist identity encoding failed: {error}"))?;
    Ok(format!("sha256:{:x}", Sha256::digest(encoded)))
}

fn indicator_source_value(
    indicator: &IndicatorRow,
    source_id: &str,
    aggregation: &str,
) -> Option<Value> {
    let runtime_field = match (source_id, aggregation) {
        ("quote.bid_price", "first") => "bid_open",
        ("quote.bid_price", "min") => "bid_low",
        ("quote.bid_price", "max") => "bid_high",
        ("quote.ask_price", "first") => "ask_open",
        ("quote.ask_price", "min") => "ask_low",
        ("quote.ask_price", "max") => "ask_high",
        ("indicator.vwap.value", _) => "vwap",
        ("indicator.vwap.execution_value", _) => "execution_vwap",
        ("market.last_price", _) => "close",
        ("market.spread_bps", _) => "spread_bps",
        ("quote.bid_price", "" | "last") => "bid_close",
        ("quote.ask_price", "" | "last") => "ask_close",
        (other, "") => other,
        (_, _) => return None,
    };
    let payload = serde_json::to_value(indicator).ok()?;
    let value = payload.get(runtime_field)?.clone();
    (!value.is_null()).then_some(value)
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
    let mut market_rows = Vec::new();
    let mut active_signals = Vec::new();
    let mut recent_signal_events = Vec::new();
    for result in results {
        indicators.extend(result.indicators);
        market_rows.extend(result.market_rows);
        active_signals.extend(result.active_signals);
        recent_signal_events.extend(result.recent_signal_events);
    }
    indicators.sort_by(|left, right| left.sym.cmp(&right.sym));
    market_rows.sort_by(|left, right| left.market.ticker.cmp(&right.market.ticker));
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
        market_rows,
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
    use super::{
        accumulate_aligned_volume_session, aligned_volume_bucket, empty_source_revision,
        replay_end_for_evaluation_windows, scanner_shard_index, source_event_requirement,
        watchlist_derived_timeframes, CoreLiquidityIndex, CrossSectionEngine,
        RelativeVolumeRevisionEvidence, RuleEventRequirement, ALIGNED_VOLUME_BUCKET_COUNT,
    };
    use crate::watchlist_timeline::WatchlistEvaluationWindow;
    use chrono::{TimeZone, Utc};
    use qmd_core::bars::{TradeAggregationRules, TradeUpdateRule};
    use qmd_core::event::{MarketEvent, TradeEvent};
    use qmd_core::indicators::MarketStructureReferenceLevels;
    use serde_json::json;
    use std::collections::{BTreeSet, HashMap};

    fn trade(ticker: &str, millis: i64, price: f64) -> MarketEvent {
        trade_with_size(ticker, millis, price, 100.0)
    }

    fn trade_with_size(ticker: &str, millis: i64, price: f64, size: f64) -> MarketEvent {
        let ts = Utc.timestamp_millis_opt(millis).single().unwrap();
        MarketEvent::Trade(TradeEvent {
            conditions: vec![0],
            exchange: 1,
            ingest_ts: ts,
            participant_ts: None,
            price,
            raw: json!({}),
            sequence: millis as u64,
            size,
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
        let mut baseline = vec![0.0; ALIGNED_VOLUME_BUCKET_COUNT];
        baseline[aligned_volume_bucket(start + chrono::Duration::seconds(1)).unwrap()] = 100.0;
        engine.install_relative_volume_baselines(
            start + chrono::Duration::seconds(1),
            HashMap::from([("AAPL".to_string(), baseline)]),
            &RelativeVolumeRevisionEvidence {
                baseline_session_count: 20,
                end: start,
                session_date: start.date_naive(),
                source_revision: empty_source_revision(),
                start,
                ticker_count: 1,
                ticker_hash: "sha256:test".to_string(),
            },
        );

        let candidate = engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(1),
                &BTreeSet::from([
                    "indicator.vwap.value".to_string(),
                    "liquidity-rank".to_string(),
                    "market.change_actual".to_string(),
                    "market.change_pct".to_string(),
                    "market.last_price".to_string(),
                    "market.relative_volume".to_string(),
                    "market.volume".to_string(),
                ]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["market.last_price"], json!(201.0));
        assert_eq!(candidate.values["market.volume"], json!(200.0));
        assert_eq!(candidate.values["market.relative_volume"], json!(2.0));
        assert_eq!(candidate.values["market.change_actual"], json!(1.0));
        assert!((candidate.values["market.change_pct"].as_f64().unwrap() - 0.5).abs() < 1e-9);
        assert!(candidate.values["liquidity-rank"].as_f64().unwrap() > 0.0);
        assert!(candidate.values["indicator.vwap.value"].as_f64().unwrap() > 0.0);
        let snapshot = engine
            .into_snapshot(
                start + chrono::Duration::seconds(1),
                3,
                empty_source_revision(),
            )
            .await;
        assert_eq!(snapshot.ticker_count, 2);
        assert_eq!(snapshot.market_rows.len(), 2);
        let aapl_market = snapshot
            .market_rows
            .iter()
            .find(|row| row.market.ticker == "AAPL")
            .unwrap();
        assert_eq!(aapl_market.market.last_price, 201.0);
        assert_eq!(aapl_market.previous_close, Some(200.0));
        assert_eq!(
            snapshot
                .indicators
                .iter()
                .map(|row| row.sym.as_str())
                .collect::<Vec<_>>(),
            vec!["AAPL", "MSFT"]
        );
    }

    #[tokio::test]
    async fn watchlist_candidate_uses_the_requested_bar_interval() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let mut engine = CrossSectionEngine::new_with_trade_rules(rules, HashMap::new());
        let start = Utc
            .with_ymd_and_hms(2026, 8, 7, 13, 30, 0)
            .single()
            .unwrap();
        engine
            .apply_event(trade("AAPL", start.timestamp_millis(), 100.0))
            .await
            .unwrap();
        engine
            .apply_event(trade(
                "AAPL",
                (start + chrono::Duration::minutes(5)).timestamp_millis(),
                100.0,
            ))
            .await
            .unwrap();
        engine
            .apply_event(trade(
                "AAPL",
                (start + chrono::Duration::minutes(10)).timestamp_millis(),
                105.0,
            ))
            .await
            .unwrap();
        engine
            .finalize(start + chrono::Duration::minutes(15))
            .await
            .unwrap();

        let candidate = engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::minutes(15),
                &BTreeSet::from(["price_change_1_bar_pct@@5m".to_string()]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["price_change_1_bar_pct@@5m"], json!(5.0));
    }

    #[test]
    fn interval_watchlist_sources_select_only_required_derived_timeframes() {
        assert_eq!(
            watchlist_derived_timeframes(&BTreeSet::from([
                "volume_rate_ratio@@1s".to_string(),
                "market.last_price@@5s##max".to_string(),
            ])),
            BTreeSet::from(["5s".to_string()]),
        );
        assert_eq!(
            watchlist_derived_timeframes(&BTreeSet::from(["indicator.vwap.value".to_string(),])),
            BTreeSet::from(["100ms".to_string()]),
        );
        assert!(watchlist_derived_timeframes(&BTreeSet::from([
            "market.session_dollar_volume".to_string(),
            "market.trade_rate_10s".to_string(),
        ]))
        .is_empty());
    }

    #[tokio::test]
    async fn one_second_volume_ratio_uses_two_completed_causal_trade_buckets() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let mut engine =
            CrossSectionEngine::new_market_only_with_trade_rules(rules, HashMap::new());
        let start = Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).single().unwrap();
        for event in [
            trade_with_size("AAPL", start.timestamp_millis() + 100, 10.0, 100.0),
            trade_with_size("AAPL", start.timestamp_millis() + 1_100, 10.1, 120.0),
            trade_with_size("AAPL", start.timestamp_millis() + 1_500, 10.2, 80.0),
        ] {
            engine.apply_event(event).await.unwrap();
        }

        let candidate = engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(2),
                &BTreeSet::from(["volume_rate_ratio@@1s".to_string()]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["volume_rate_ratio@@1s"], json!(2.0));
        assert!(engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(3),
                &BTreeSet::from(["volume_rate_ratio@@1s".to_string()]),
            )
            .await
            .unwrap()
            .unwrap()
            .values
            .is_empty());
    }

    #[tokio::test]
    async fn market_only_profile_skips_all_bar_indicator_and_signal_state() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let mut engine =
            CrossSectionEngine::new_market_only_with_trade_rules(rules, HashMap::new());
        let start = Utc
            .with_ymd_and_hms(2026, 8, 7, 13, 30, 0)
            .single()
            .unwrap();

        engine
            .apply_event(trade("AAPL", start.timestamp_millis(), 200.0))
            .await
            .unwrap();
        engine
            .finalize(start + chrono::Duration::seconds(1))
            .await
            .unwrap();

        assert!(engine.bars.is_none());
        assert!(engine.microstructure.is_empty());
        assert!(engine.calculators.is_empty());
        assert!(engine.latest_indicators.is_empty());
        assert!(engine.active_signals.is_empty());
        let candidate = engine
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(1),
                &BTreeSet::from([
                    "liquidity-rank".to_string(),
                    "market.last_price".to_string(),
                    "market.volume".to_string(),
                ]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["market.last_price"], json!(200.0));
        assert_eq!(candidate.values["market.volume"], json!(100.0));
        assert!(candidate.values["liquidity-rank"].as_f64().unwrap() > 0.0);
    }

    #[tokio::test]
    async fn ticker_shards_preserve_interleaved_state_and_stable_ownership() {
        let rules = TradeAggregationRules::new([(0, TradeUpdateRule::regular())]).unwrap();
        let mut engines = (0..3)
            .map(|_| CrossSectionEngine::new_with_trade_rules(rules.clone(), HashMap::new()))
            .collect::<Vec<_>>();
        let start = Utc
            .with_ymd_and_hms(2026, 8, 7, 13, 30, 0)
            .single()
            .unwrap();
        for event in [
            trade("AAPL", start.timestamp_millis(), 200.0),
            trade("MSFT", start.timestamp_millis() + 10, 500.0),
            trade("AAPL", start.timestamp_millis() + 20, 201.0),
        ] {
            let shard = scanner_shard_index(event.ticker(), engines.len());
            engines[shard].apply_event(event).await.unwrap();
        }
        for engine in &mut engines {
            engine
                .finalize(start + chrono::Duration::seconds(1))
                .await
                .unwrap();
        }
        let shard = scanner_shard_index("AAPL", engines.len());
        let candidate = engines[shard]
            .watchlist_candidate(
                "AAPL",
                start + chrono::Duration::seconds(1),
                &BTreeSet::from(["market.volume".to_string()]),
            )
            .await
            .unwrap()
            .unwrap();
        assert_eq!(candidate.values["market.volume"], json!(200.0));
        assert_eq!(shard, scanner_shard_index("AAPL", engines.len()));
    }

    #[test]
    fn aligned_volume_profiles_are_cumulative_and_core_seed_is_bounded() {
        let mut sums =
            HashMap::from([("AAPL".to_string(), vec![0.0; ALIGNED_VOLUME_BUCKET_COUNT])]);
        let mut day = vec![0.0; ALIGNED_VOLUME_BUCKET_COUNT];
        day[0] = 100.0;
        day[2] = 50.0;
        let mut increments = HashMap::from([("AAPL".to_string(), day)]);
        accumulate_aligned_volume_session(&mut sums, &mut increments);
        assert_eq!(&sums["AAPL"][0..4], &[100.0, 100.0, 150.0, 150.0]);

        let mut index = CoreLiquidityIndex::default();
        index.update("LOW".to_string(), Some(1.0));
        index.update("HIGH".to_string(), Some(10.0));
        index.update("MID".to_string(), Some(5.0));
        assert_eq!(
            index.top(2).into_iter().collect::<Vec<_>>(),
            vec!["HIGH".to_string(), "MID".to_string()]
        );
    }

    #[test]
    fn rule_operands_select_only_the_required_market_event_family() {
        assert_eq!(
            source_event_requirement("price_change_1_bar_pct"),
            RuleEventRequirement::Trade
        );
        assert_eq!(
            source_event_requirement("quote.bid_price"),
            RuleEventRequirement::Quote
        );
        assert_eq!(
            source_event_requirement("time.session_phase"),
            RuleEventRequirement::Neutral
        );
        assert_eq!(
            source_event_requirement("custom.unknown_signal"),
            RuleEventRequirement::Both
        );
    }

    #[test]
    fn source_native_evaluation_windows_bound_physical_replay_end() {
        let requested_end = Utc
            .with_ymd_and_hms(2026, 8, 21, 20, 0, 0)
            .single()
            .unwrap();
        let windows = [
            WatchlistEvaluationWindow {
                start: "2026-08-21T08:01:00+00:00".to_string(),
                end: "2026-08-21T08:01:01+00:00".to_string(),
            },
            WatchlistEvaluationWindow {
                start: "2026-08-21T13:29:58+00:00".to_string(),
                end: "2026-08-21T13:29:59+00:00".to_string(),
            },
        ];

        let replay_end = replay_end_for_evaluation_windows(windows.iter(), requested_end).unwrap();

        assert_eq!(
            replay_end,
            Utc.with_ymd_and_hms(2026, 8, 21, 13, 29, 59)
                .single()
                .unwrap()
        );
    }
}
