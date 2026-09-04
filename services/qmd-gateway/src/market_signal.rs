use crate::bars::BarRow;
use crate::indicators::IndicatorRow;
use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, VecDeque};

pub const MARKET_SIGNAL_SCHEMA_VERSION: u16 = 3;
pub const MARKET_SIGNAL_ENGINE_VERSION: &str = "qmd-market-signal-v3";
const SIGNAL_VERSION: u16 = 1;
const BASELINE_WARMUP: u64 = 8;

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct MarketSignalEvent {
    pub schema_version: u16,
    #[serde(default = "default_signal_version")]
    pub signal_version: u16,
    pub engine_version: String,
    pub event_id: String,
    pub signal_id: String,
    pub signal_key: String,
    pub producer: String,
    #[serde(default = "market_domain")]
    pub domain: String,
    pub ticker: String,
    pub working_timeframe: String,
    #[serde(default)]
    pub clock: SignalClock,
    pub confirmation_timeframe: Option<String>,
    pub observed_at: DateTime<Utc>,
    pub effective_at: DateTime<Utc>,
    pub state: String,
    pub direction: String,
    pub score: f64,
    #[serde(default)]
    pub rank_score: f64,
    pub confidence: f64,
    pub trigger_reason: String,
    pub resolution_reason: String,
    pub reference_price: f64,
    pub invalidation_price: Option<f64>,
    pub expires_at: Option<DateTime<Utc>>,
    pub evidence: MarketSignalEvidence,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct SignalClock {
    pub input_basis: String,
    pub calculation_window: String,
    pub evaluation_mode: String,
    pub update_trigger: String,
    pub publication_cadence: String,
    pub publication_interval_ms: Option<u64>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
#[serde(default)]
pub struct MarketSignalEvidence {
    pub close: f64,
    pub high: f64,
    pub low: f64,
    pub vwap: f64,
    pub price_change_pct: f64,
    pub return_1_bar: f64,
    pub volume: f64,
    pub volume_rate: f64,
    pub dollar_volume: f64,
    pub dollar_volume_rate: f64,
    pub trade_rate: f64,
    pub quote_rate: f64,
    pub tape_imbalance: f64,
    pub tape_imbalance_accel: f64,
    pub spread_bps: f64,
    pub liquidity_score: f64,
    pub depth_imbalance_proxy: f64,
    pub price_surprise: f64,
    pub activity_surprise: f64,
    pub flow_surprise: f64,
    pub liquidity_surprise: f64,
    pub flow_structure_composite_score: f64,
    pub flow_structure_composite_confidence: f64,
    pub flow_structure_composite_bias: String,
    pub flow_structure_composite_reason: String,
    pub alignment_persistence: f64,
    pub composite_surprise: f64,
    pub estimated_luld_active: bool,
    pub estimated_luld_state: String,
}

#[derive(Clone, Debug)]
struct Candidate {
    key: &'static str,
    direction: &'static str,
    score: f64,
    rank_score: f64,
    confidence: f64,
    reason: String,
    invalidation_price: Option<f64>,
    surprises: SignalSurprises,
}

#[derive(Clone, Debug, Default)]
struct SignalSurprises {
    price: f64,
    activity: f64,
    flow: f64,
    liquidity: f64,
    composite: f64,
    persistence: f64,
    composite_score: f64,
    composite_confidence: f64,
    composite_bias: String,
    composite_reason: String,
}

#[derive(Clone, Debug, Default)]
struct CausalStats {
    count: u64,
    mean: f64,
    variance: f64,
}

impl CausalStats {
    fn update(&mut self, value: f64) {
        if !value.is_finite() {
            return;
        }
        self.count += 1;
        if self.count == 1 {
            self.mean = value;
            return;
        }
        let alpha = 0.10;
        let delta = value - self.mean;
        self.mean += alpha * delta;
        self.variance = (1.0 - alpha) * (self.variance + alpha * delta * delta);
    }

    fn positive_surprise(&self, value: f64) -> f64 {
        if self.count < BASELINE_WARMUP || !value.is_finite() {
            return 0.0;
        }
        let scale = self
            .variance
            .max(0.0)
            .sqrt()
            .max(self.mean.abs() * 0.10)
            .max(1e-9);
        ((value - self.mean) / scale).max(0.0)
    }

    fn negative_surprise(&self, value: f64) -> f64 {
        if self.count < BASELINE_WARMUP || !value.is_finite() {
            return 0.0;
        }
        let scale = self
            .variance
            .max(0.0)
            .sqrt()
            .max(self.mean.abs() * 0.10)
            .max(1e-9);
        ((self.mean - value) / scale).max(0.0)
    }

    fn reliability(&self) -> f64 {
        (self.count as f64 / 30.0).clamp(0.0, 1.0)
    }
}

#[derive(Clone, Debug, Default)]
struct Baselines {
    absolute_return: CausalStats,
    volume_rate: CausalStats,
    dollar_volume_rate: CausalStats,
    trade_rate: CausalStats,
    quote_rate: CausalStats,
    absolute_tape: CausalStats,
    absolute_tape_accel: CausalStats,
    spread_bps: CausalStats,
    liquidity_score: CausalStats,
    absolute_composite: CausalStats,
}

impl Baselines {
    fn update(&mut self, row: &BarRow, indicator: Option<&IndicatorRow>) {
        self.absolute_return.update(row.return_1_bar.abs());
        self.volume_rate.update(row.volume_rate);
        self.dollar_volume_rate.update(row.dollar_volume_rate);
        self.trade_rate.update(row.trade_rate);
        self.quote_rate.update(row.quote_rate);
        self.absolute_tape.update(row.tape_imbalance.abs());
        self.absolute_tape_accel
            .update(row.tape_imbalance_accel.abs());
        if row.spread_bps_close > 0.0 {
            self.spread_bps.update(row.spread_bps_close);
        }
        if row.liquidity_score >= 0.0 {
            self.liquidity_score.update(row.liquidity_score);
        }
        if let Some(indicator) = indicator {
            self.absolute_composite
                .update(indicator.flow_structure_composite_score.abs());
        }
    }

    fn reliability(&self) -> f64 {
        weighted_score(&[
            self.absolute_return.reliability(),
            self.trade_rate.reliability(),
            self.quote_rate.reliability(),
            self.spread_bps.reliability(),
        ])
    }
}

#[derive(Clone, Debug)]
struct SignalSeriesState {
    session_date: String,
    previous: BarRow,
    baselines: Baselines,
    alignment_history: VecDeque<i8>,
}

#[derive(Default)]
pub struct MarketSignalEngine {
    active: HashMap<String, MarketSignalEvent>,
    series: HashMap<String, SignalSeriesState>,
}

impl MarketSignalEngine {
    pub fn update(&mut self, row: &BarRow) -> Vec<MarketSignalEvent> {
        self.update_with_indicator(row, None)
    }

    pub fn update_with_indicator(
        &mut self,
        row: &BarRow,
        indicator: Option<&IndicatorRow>,
    ) -> Vec<MarketSignalEvent> {
        if !row.is_closed || row.close <= 0.0 {
            return Vec::new();
        }

        let series_key = format!(
            "{}:{}",
            row.sym.to_ascii_uppercase(),
            row.timeframe.to_ascii_lowercase()
        );
        let prior = self
            .series
            .get(&series_key)
            .filter(|state| state.session_date == row.session_date)
            .cloned();
        let baselines = prior
            .as_ref()
            .map(|state| state.baselines.clone())
            .unwrap_or_default();
        let mut alignment_history = prior
            .as_ref()
            .map(|state| state.alignment_history.clone())
            .unwrap_or_default();
        let alignment_direction = indicator
            .map(alignment_observation_direction)
            .unwrap_or_default();
        alignment_history.push_back(alignment_direction);
        while alignment_history.len() > 5 {
            alignment_history.pop_front();
        }
        let candidates = evaluate_bar(
            row,
            indicator,
            prior.as_ref().map(|state| &state.previous),
            &baselines,
            &alignment_history,
        );
        let candidate_by_key = candidates
            .iter()
            .map(|candidate| (candidate.key, candidate))
            .collect::<HashMap<_, _>>();
        let mut events = Vec::new();

        for key in signal_keys_for_timeframe(&row.timeframe) {
            let identity = signal_identity(&row.sym, &row.timeframe, key);
            match (
                self.active.get(&identity).cloned(),
                candidate_by_key.get(key),
            ) {
                (None, Some(candidate)) => {
                    let event = build_event(row, candidate, "triggered", "", None);
                    self.active.insert(identity, event.clone());
                    events.push(event);
                }
                (Some(previous), Some(candidate)) if previous.direction != candidate.direction => {
                    let resolved = resolve_event(
                        row,
                        &previous,
                        "direction changed before the prior observation reconfirmed",
                    );
                    events.push(resolved);
                    let triggered = build_event(row, candidate, "triggered", "", None);
                    self.active.insert(identity, triggered.clone());
                    events.push(triggered);
                }
                (Some(previous), Some(candidate))
                    if (previous.rank_score - candidate.rank_score).abs() >= 0.05
                        || (previous.confidence - candidate.confidence).abs() >= 0.05 =>
                {
                    let updated =
                        build_event(row, candidate, "updated", "", Some(&previous.signal_id));
                    self.active.insert(identity, updated.clone());
                    events.push(updated);
                }
                (Some(_), Some(_)) => {}
                (Some(previous), None) => {
                    self.active.remove(&identity);
                    events.push(resolve_event(
                        row,
                        &previous,
                        "normalized trigger conditions no longer hold",
                    ));
                }
                (None, None) => {}
            }
        }

        let mut updated_baselines = baselines;
        updated_baselines.update(row, indicator);
        self.series.insert(
            series_key,
            SignalSeriesState {
                session_date: row.session_date.clone(),
                previous: row.clone(),
                baselines: updated_baselines,
                alignment_history,
            },
        );
        events
    }
}

fn evaluate_bar(
    row: &BarRow,
    indicator: Option<&IndicatorRow>,
    previous: Option<&BarRow>,
    baselines: &Baselines,
    alignment_history: &VecDeque<i8>,
) -> Vec<Candidate> {
    let mut candidates = Vec::new();
    let confidence = evidence_confidence(row, baselines);

    if row.timeframe.eq_ignore_ascii_case("100ms") {
        if let Some(indicator) = indicator {
            let direction_value = alignment_observation_direction(indicator);
            let persistence_count = alignment_history
                .iter()
                .filter(|value| **value == direction_value && direction_value != 0)
                .count();
            let persistence = persistence_count as f64 / 5.0;
            if direction_value != 0 && persistence_count >= 3 {
                let composite_score = indicator.flow_structure_composite_score;
                let composite_confidence = indicator
                    .flow_structure_composite_confidence
                    .clamp(0.0, 1.0);
                let composite_surprise = baselines
                    .absolute_composite
                    .positive_surprise(composite_score.abs());
                let strength = weighted_score(&[
                    composite_score.abs(),
                    z_strength(composite_surprise),
                    composite_confidence,
                    persistence,
                ]);
                candidates.push(candidate_with_rank(
                    "flow_structure_alignment",
                    if direction_value > 0 {
                        "bullish"
                    } else {
                        "bearish"
                    },
                    composite_score.abs(),
                    strength,
                    composite_confidence,
                    format!(
                        "{} persisted in {} of the latest 5 canonical observations",
                        indicator.flow_structure_composite_reason, persistence_count
                    ),
                    Some(directional_invalidation(
                        row,
                        if direction_value > 0 {
                            "bullish"
                        } else {
                            "bearish"
                        },
                    )),
                    SignalSurprises {
                        composite: composite_surprise,
                        persistence,
                        composite_score,
                        composite_confidence,
                        composite_bias: indicator.flow_structure_composite_bias.clone(),
                        composite_reason: indicator.flow_structure_composite_reason.clone(),
                        ..Default::default()
                    },
                ));
            }
        }

        let flow_z = baselines
            .absolute_tape
            .positive_surprise(row.tape_imbalance.abs());
        let flow_accel_z = baselines
            .absolute_tape_accel
            .positive_surprise(row.tape_imbalance_accel.abs());
        let flow_surprise = flow_z.max(flow_accel_z);
        if row.trade_count >= 2
            && row.quote_count >= 1
            && row.tape_imbalance.abs() >= 0.15
            && flow_surprise >= 2.5
        {
            let direction = direction_from_sign(row.tape_imbalance);
            let strength = weighted_score(&[
                z_strength(flow_z),
                z_strength(flow_accel_z),
                row.tape_imbalance.abs(),
            ]);
            candidates.push(candidate(
                "directional_flow_acceleration",
                direction,
                strength,
                confidence,
                format!(
                    "directional flow expanded {:.1} standard deviations above its causal baseline",
                    flow_surprise
                ),
                Some(directional_invalidation(row, direction)),
                SignalSurprises {
                    flow: flow_surprise,
                    ..Default::default()
                },
            ));
        }

        let spread_z = baselines.spread_bps.positive_surprise(row.spread_bps_close);
        let liquidity_down_z = baselines
            .liquidity_score
            .negative_surprise(row.liquidity_score);
        let quote_down_z = baselines.quote_rate.negative_surprise(row.quote_rate);
        let deterioration_z = liquidity_down_z.max(quote_down_z);
        if row.spread_bps_close > 0.0
            && spread_z >= 2.5
            && (deterioration_z >= 1.0 || spread_z >= 4.0)
        {
            let strength = weighted_score(&[z_strength(spread_z), z_strength(deterioration_z)]);
            candidates.push(candidate(
                "liquidity_dislocation",
                "neutral",
                strength,
                confidence,
                format!(
                    "spread widened {:.1} standard deviations while displayed liquidity deteriorated",
                    spread_z
                ),
                None,
                SignalSurprises {
                    liquidity: spread_z.max(deterioration_z),
                    ..Default::default()
                },
            ));
        }

        if let Some(previous) = previous {
            let previous_spread_z = baselines
                .spread_bps
                .positive_surprise(previous.spread_bps_close);
            let spread_reduction = if previous.spread_bps_close > 0.0 {
                1.0 - row.spread_bps_close / previous.spread_bps_close
            } else {
                0.0
            };
            let liquidity_improvement = if previous.liquidity_score > 0.0 {
                row.liquidity_score / previous.liquidity_score - 1.0
            } else {
                0.0
            };
            let quote_improvement = if previous.quote_rate > 0.0 {
                row.quote_rate / previous.quote_rate - 1.0
            } else {
                0.0
            };
            if previous_spread_z >= 2.5
                && spread_reduction >= 0.30
                && (liquidity_improvement >= 0.20
                    || quote_improvement >= 0.20
                    || row.quote_rate_accel > 0.0)
            {
                let direction = if row.tape_imbalance.abs() >= 0.15 {
                    direction_from_sign(row.tape_imbalance)
                } else {
                    "neutral"
                };
                let recovery_strength = weighted_score(&[
                    spread_reduction,
                    liquidity_improvement.max(quote_improvement).max(0.0),
                    z_strength(previous_spread_z),
                ]);
                candidates.push(candidate(
                    "liquidity_recovery",
                    direction,
                    recovery_strength,
                    confidence,
                    format!(
                        "spread contracted {:.0}% after a {:.1}-sigma liquidity dislocation",
                        spread_reduction * 100.0,
                        previous_spread_z
                    ),
                    None,
                    SignalSurprises {
                        liquidity: previous_spread_z,
                        ..Default::default()
                    },
                ));
            }
        }

        let price_z = baselines
            .absolute_return
            .positive_surprise(row.return_1_bar.abs());
        let divergent = (row.tape_imbalance > 0.0 && row.return_1_bar <= 0.0)
            || (row.tape_imbalance < 0.0 && row.return_1_bar >= 0.0);
        if row.trade_count >= 2 && row.tape_imbalance.abs() >= 0.30 && flow_z >= 2.5 && divergent {
            let direction = if row.tape_imbalance > 0.0 {
                "bearish"
            } else {
                "bullish"
            };
            let strength = weighted_score(&[
                z_strength(flow_z),
                row.tape_imbalance.abs(),
                1.0 - z_strength(price_z),
            ]);
            candidates.push(candidate(
                "flow_price_divergence",
                direction,
                strength,
                confidence,
                "aggressive flow expanded but price failed to accept in the same direction"
                    .to_string(),
                Some(directional_invalidation(row, direction)),
                SignalSurprises {
                    price: price_z,
                    flow: flow_z,
                    ..Default::default()
                },
            ));
        }
    }

    if matches_timeframe(&row.timeframe, &["1s", "10s", "30s", "1m"]) {
        let price_z = baselines
            .absolute_return
            .positive_surprise(row.return_1_bar.abs());
        let activity_z = baselines
            .volume_rate
            .positive_surprise(row.volume_rate)
            .max(
                baselines
                    .dollar_volume_rate
                    .positive_surprise(row.dollar_volume_rate),
            )
            .max(baselines.trade_rate.positive_surprise(row.trade_rate));
        if row.return_1_bar.abs() > 0.0 && price_z >= 2.5 && activity_z >= 2.5 {
            let direction = direction_from_sign(row.return_1_bar);
            let strength = weighted_score(&[z_strength(price_z), z_strength(activity_z)]);
            candidates.push(candidate(
                "price_volume_expansion",
                direction,
                strength,
                confidence,
                format!(
                    "price and activity expanded {:.1} and {:.1} standard deviations above causal baselines",
                    price_z, activity_z
                ),
                Some(directional_invalidation(row, direction)),
                SignalSurprises {
                    price: price_z,
                    activity: activity_z,
                    ..Default::default()
                },
            ));
        }
    }

    candidates
}

fn candidate(
    key: &'static str,
    direction: &'static str,
    strength: f64,
    confidence: f64,
    reason: String,
    invalidation_price: Option<f64>,
    surprises: SignalSurprises,
) -> Candidate {
    let calibrated_strength = strength.clamp(0.0, 1.0);
    Candidate {
        key,
        direction,
        score: if direction == "neutral" {
            0.0
        } else {
            calibrated_strength
        },
        rank_score: (calibrated_strength * (0.50 + 0.50 * confidence)).clamp(0.0, 1.0),
        confidence,
        reason,
        invalidation_price,
        surprises,
    }
}

fn candidate_with_rank(
    key: &'static str,
    direction: &'static str,
    score: f64,
    rank_score: f64,
    confidence: f64,
    reason: String,
    invalidation_price: Option<f64>,
    surprises: SignalSurprises,
) -> Candidate {
    Candidate {
        key,
        direction,
        score: score.clamp(0.0, 1.0),
        rank_score: rank_score.clamp(0.0, 1.0),
        confidence: confidence.clamp(0.0, 1.0),
        reason,
        invalidation_price,
        surprises,
    }
}

fn build_event(
    row: &BarRow,
    candidate: &Candidate,
    state: &str,
    resolution_reason: &str,
    signal_id: Option<&str>,
) -> MarketSignalEvent {
    let stable_signal_id = signal_id.map(str::to_string).unwrap_or_else(|| {
        format!(
            "{}:{}:{}:{}",
            row.sym,
            row.timeframe,
            candidate.key,
            row.bar_end.timestamp_millis()
        )
    });
    MarketSignalEvent {
        schema_version: MARKET_SIGNAL_SCHEMA_VERSION,
        signal_version: SIGNAL_VERSION,
        engine_version: MARKET_SIGNAL_ENGINE_VERSION.to_string(),
        event_id: format!(
            "{}:{}:{}:{}:{}",
            row.sym,
            row.timeframe,
            candidate.key,
            row.bar_end.timestamp_millis(),
            state
        ),
        signal_id: stable_signal_id,
        signal_key: candidate.key.to_string(),
        producer: "qmd".to_string(),
        domain: market_domain(),
        ticker: row.sym.clone(),
        working_timeframe: row.timeframe.clone(),
        clock: clock_for(candidate.key, &row.timeframe),
        confirmation_timeframe: None,
        observed_at: row.bar_end,
        effective_at: row.bar_end,
        state: state.to_string(),
        direction: candidate.direction.to_string(),
        score: if candidate.direction == "bearish" {
            -candidate.score
        } else {
            candidate.score
        },
        rank_score: candidate.rank_score,
        confidence: candidate.confidence.clamp(0.0, 1.0),
        trigger_reason: candidate.reason.clone(),
        resolution_reason: resolution_reason.to_string(),
        reference_price: row.close,
        invalidation_price: candidate.invalidation_price,
        expires_at: None,
        evidence: evidence(row, candidate.surprises.clone()),
    }
}

fn clock_for(key: &str, timeframe: &str) -> SignalClock {
    match key {
        "flow_structure_alignment" => SignalClock {
            input_basis: "indicator_derived".to_string(),
            calculation_window: "100ms".to_string(),
            evaluation_mode: "closed_only".to_string(),
            update_trigger: "indicator_update".to_string(),
            publication_cadence: "on_change".to_string(),
            publication_interval_ms: None,
        },
        "directional_flow_acceleration" | "flow_price_divergence" => SignalClock {
            input_basis: "event_native".to_string(),
            calculation_window: "100ms".to_string(),
            evaluation_mode: "closed_only".to_string(),
            update_trigger: "bar_close".to_string(),
            publication_cadence: "interval".to_string(),
            publication_interval_ms: Some(100),
        },
        "liquidity_dislocation" | "liquidity_recovery" => SignalClock {
            input_basis: "event_native".to_string(),
            calculation_window: "100ms".to_string(),
            evaluation_mode: "closed_only".to_string(),
            update_trigger: "bar_close".to_string(),
            publication_cadence: "on_change".to_string(),
            publication_interval_ms: None,
        },
        _ => SignalClock {
            input_basis: "bar_derived".to_string(),
            calculation_window: timeframe.to_string(),
            evaluation_mode: "closed_only".to_string(),
            update_trigger: "bar_close".to_string(),
            publication_cadence: "bar_close".to_string(),
            publication_interval_ms: None,
        },
    }
}

fn market_domain() -> String {
    "market".to_string()
}

fn default_signal_version() -> u16 {
    1
}

fn resolve_event(row: &BarRow, previous: &MarketSignalEvent, reason: &str) -> MarketSignalEvent {
    let mut event = previous.clone();
    event.event_id = format!(
        "{}:{}:{}:{}:resolved",
        row.sym,
        row.timeframe,
        previous.signal_key,
        row.bar_end.timestamp_millis()
    );
    event.observed_at = row.bar_end;
    event.effective_at = row.bar_end;
    event.state = "resolved".to_string();
    event.resolution_reason = reason.to_string();
    event.reference_price = row.close;
    event.evidence = evidence(row, SignalSurprises::default());
    event
}

fn evidence(row: &BarRow, surprises: SignalSurprises) -> MarketSignalEvidence {
    MarketSignalEvidence {
        close: row.close,
        high: row.high,
        low: row.low,
        // Retained only so old serialized signal rows remain readable. New
        // generic signals do not publish or calculate from consolidated VWAP;
        // consumers obtain the sole authority from IndicatorRow.execution_vwap.
        vwap: 0.0,
        price_change_pct: row.price_change_pct,
        return_1_bar: row.return_1_bar,
        volume: row.volume,
        volume_rate: row.volume_rate,
        dollar_volume: row.dollar_volume,
        dollar_volume_rate: row.dollar_volume_rate,
        trade_rate: row.trade_rate,
        quote_rate: row.quote_rate,
        tape_imbalance: row.tape_imbalance,
        tape_imbalance_accel: row.tape_imbalance_accel,
        spread_bps: row.spread_bps_close,
        liquidity_score: row.liquidity_score,
        depth_imbalance_proxy: row.depth_imbalance_proxy,
        price_surprise: surprises.price,
        activity_surprise: surprises.activity,
        flow_surprise: surprises.flow,
        liquidity_surprise: surprises.liquidity,
        flow_structure_composite_score: surprises.composite_score,
        flow_structure_composite_confidence: surprises.composite_confidence,
        flow_structure_composite_bias: surprises.composite_bias,
        flow_structure_composite_reason: surprises.composite_reason,
        alignment_persistence: surprises.persistence,
        composite_surprise: surprises.composite,
        estimated_luld_active: row.estimated_luld_active,
        estimated_luld_state: row.estimated_luld_state.clone(),
    }
}

fn evidence_confidence(row: &BarRow, baselines: &Baselines) -> f64 {
    weighted_score(&[
        if row.trade_count > 0 { 1.0 } else { 0.0 },
        if row.quote_count > 0 { 1.0 } else { 0.0 },
        if row.spread_bps_close > 0.0 { 1.0 } else { 0.0 },
        baselines.reliability(),
    ])
}

fn signal_keys_for_timeframe(timeframe: &str) -> &'static [&'static str] {
    match timeframe.to_ascii_lowercase().as_str() {
        "100ms" => &[
            "flow_structure_alignment",
            "directional_flow_acceleration",
            "liquidity_dislocation",
            "liquidity_recovery",
            "flow_price_divergence",
        ],
        "1s" | "10s" | "30s" | "1m" => &["price_volume_expansion", "vwap_transition"],
        _ => &[],
    }
}

fn alignment_observation_direction(indicator: &IndicatorRow) -> i8 {
    let meaningful = indicator.flow_structure_composite_score.abs() >= 0.15
        && indicator.flow_structure_composite_confidence >= 0.35;
    let aligned = indicator
        .flow_structure_composite_reason
        .starts_with("aligned_");
    if meaningful && aligned {
        if indicator.flow_structure_composite_bias == "bullish" {
            1
        } else if indicator.flow_structure_composite_bias == "bearish" {
            -1
        } else {
            0
        }
    } else {
        0
    }
}

fn signal_identity(ticker: &str, timeframe: &str, key: &str) -> String {
    format!(
        "{}:{}:{}",
        ticker.to_ascii_uppercase(),
        timeframe.to_ascii_lowercase(),
        key
    )
}

fn direction_from_sign(value: f64) -> &'static str {
    if value >= 0.0 {
        "bullish"
    } else {
        "bearish"
    }
}

fn directional_invalidation(row: &BarRow, direction: &str) -> f64 {
    if direction == "bullish" {
        row.low
    } else {
        row.high
    }
}

fn matches_timeframe(value: &str, allowed: &[&str]) -> bool {
    allowed
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(value))
}

fn z_strength(value: f64) -> f64 {
    (value / 6.0).clamp(0.0, 1.0)
}

fn weighted_score(values: &[f64]) -> f64 {
    if values.is_empty() {
        return 0.0;
    }
    values
        .iter()
        .copied()
        .map(|value| value.clamp(0.0, 1.0))
        .sum::<f64>()
        / values.len() as f64
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    fn warm_up(engine: &mut MarketSignalEngine, timeframe: &str) -> BarRow {
        let mut bar = crate::scanner::tests::base_bar();
        bar.timeframe = timeframe.to_string();
        bar.return_1_bar = 0.01;
        bar.price_change_pct = 0.01;
        bar.volume = 100.0;
        bar.volume_rate = 100.0;
        bar.dollar_volume = 1_000.0;
        bar.dollar_volume_rate = 1_000.0;
        bar.trade_count = 10;
        bar.trade_rate = 10.0;
        bar.quote_count = 10;
        bar.quote_rate = 10.0;
        bar.tape_imbalance = 0.05;
        bar.tape_imbalance_accel = 0.01;
        bar.spread_bps_close = 5.0;
        bar.liquidity_score = 10_000.0;
        bar.close = 10.01;
        bar.vwap = 10.0;
        let step = if timeframe == "100ms" {
            Duration::milliseconds(100)
        } else {
            Duration::seconds(1)
        };
        for _ in 0..12 {
            assert!(engine.update(&bar).is_empty());
            bar.bar_start = bar.bar_end;
            bar.bar_end += step;
        }
        bar
    }

    #[test]
    fn event_native_flow_emits_rankable_100ms_signal() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = warm_up(&mut engine, "100ms");
        bar.trade_count = 4;
        bar.quote_count = 4;
        bar.tape_imbalance = 0.80;
        bar.tape_imbalance_accel = 0.70;
        let event = engine
            .update(&bar)
            .into_iter()
            .find(|event| event.signal_key == "directional_flow_acceleration")
            .expect("flow acceleration should emit");
        assert_eq!(event.schema_version, 3);
        assert_eq!(event.signal_version, 1);
        assert_eq!(event.clock.input_basis, "event_native");
        assert_eq!(event.clock.publication_interval_ms, Some(100));
        assert!(event.rank_score > 0.0);
        assert!(event.evidence.flow_surprise >= 2.5);
    }

    #[test]
    fn flow_structure_alignment_requires_three_of_five_and_preserves_composite_score() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = warm_up(&mut engine, "100ms");
        let mut emitted = Vec::new();
        for _ in 0..3 {
            let mut indicator =
                crate::indicators::calculate_bar_indicators(&[bar.clone()]).remove(0);
            indicator.microstructure_unified_signal = 0.72;
            indicator.microstructure_unified_confidence = 84.0;
            indicator.qmd_structure_score = 0.55;
            indicator.qmd_structure_confidence = 0.80;
            indicator.qmd_structure_agreement = 0.85;
            indicator.qmd_structure_pressure_bias = 0.35;
            indicator.qmd_structure_pressure_confidence = 0.75;
            indicator.flow_structure_composite_score = 0.64;
            indicator.flow_structure_composite_confidence = 0.79;
            indicator.flow_structure_composite_bias = "bullish".to_string();
            indicator.flow_structure_composite_reason = "aligned_bullish_evidence".to_string();
            emitted = engine.update_with_indicator(&bar, Some(&indicator));
            bar.bar_start = bar.bar_end;
            bar.bar_end += Duration::milliseconds(100);
        }
        let event = emitted
            .iter()
            .find(|event| event.signal_key == "flow_structure_alignment")
            .expect("third aligned observation should trigger");
        assert_eq!(event.direction, "bullish");
        assert_eq!(event.score, 0.64);
        assert_eq!(event.clock.input_basis, "indicator_derived");
        assert_eq!(event.clock.update_trigger, "indicator_update");
        assert_eq!(event.evidence.alignment_persistence, 0.6);
        assert_eq!(event.evidence.flow_structure_composite_confidence, 0.79);
        assert!(event.rank_score > 0.0);
    }

    #[test]
    fn price_and_volume_expansion_includes_one_second_bars() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = warm_up(&mut engine, "1s");
        bar.return_1_bar = 1.0;
        bar.price_change_pct = 1.0;
        bar.volume_rate = 5_000.0;
        bar.dollar_volume_rate = 50_000.0;
        bar.trade_rate = 100.0;
        let event = engine
            .update(&bar)
            .into_iter()
            .find(|event| event.signal_key == "price_volume_expansion")
            .expect("price and volume expansion should emit");
        assert_eq!(event.working_timeframe, "1s");
        assert_eq!(event.direction, "bullish");
        assert!(event.score > 0.0);
        assert!(event.rank_score > 0.0);
        assert_eq!(event.evidence.vwap, 0.0);
        assert!(engine
            .update(&bar)
            .into_iter()
            .all(|event| event.signal_key != "vwap_transition"));
    }

    #[test]
    fn liquidity_dislocation_and_recovery_emit_distinct_lifecycles() {
        let mut engine = MarketSignalEngine::default();
        let mut shock = warm_up(&mut engine, "100ms");
        shock.spread_bps_close = 50.0;
        shock.liquidity_score = 1_000.0;
        shock.quote_rate = 1.0;
        let dislocation = engine
            .update(&shock)
            .into_iter()
            .find(|event| event.signal_key == "liquidity_dislocation")
            .expect("liquidity shock should emit");
        assert_eq!(dislocation.direction, "neutral");
        assert_eq!(dislocation.score, 0.0);
        assert!(dislocation.rank_score > 0.0);

        let mut recovered = shock.clone();
        recovered.bar_start = shock.bar_end;
        recovered.bar_end += Duration::milliseconds(100);
        recovered.spread_bps_close = 5.0;
        recovered.liquidity_score = 12_000.0;
        recovered.quote_rate = 20.0;
        recovered.quote_rate_accel = 19.0;
        let recovery = engine
            .update(&recovered)
            .into_iter()
            .find(|event| event.signal_key == "liquidity_recovery")
            .expect("liquidity recovery should emit");
        assert!(recovery.rank_score > 0.0);
        assert_eq!(recovery.clock.publication_cadence, "on_change");
    }

    #[test]
    fn flow_price_divergence_reports_absorption_direction() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = warm_up(&mut engine, "100ms");
        bar.trade_count = 4;
        bar.quote_count = 4;
        bar.tape_imbalance = 0.80;
        bar.tape_imbalance_accel = 0.70;
        bar.return_1_bar = -0.01;
        let divergence = engine
            .update(&bar)
            .into_iter()
            .find(|event| event.signal_key == "flow_price_divergence")
            .expect("buy flow without price acceptance should emit");
        assert_eq!(divergence.direction, "bearish");
        assert!(divergence.score < 0.0);
        assert!(divergence.evidence.flow_surprise >= 2.5);
    }

    #[test]
    fn lifecycle_keeps_signal_identity_on_resolution() {
        let mut engine = MarketSignalEngine::default();
        let mut bar = warm_up(&mut engine, "100ms");
        bar.trade_count = 4;
        bar.quote_count = 4;
        bar.tape_imbalance = 0.80;
        bar.tape_imbalance_accel = 0.70;
        let triggered = engine
            .update(&bar)
            .into_iter()
            .find(|event| event.signal_key == "directional_flow_acceleration")
            .unwrap();

        bar.bar_start = bar.bar_end;
        bar.bar_end += Duration::milliseconds(100);
        bar.tape_imbalance = 0.02;
        bar.tape_imbalance_accel = 0.01;
        let resolved = engine
            .update(&bar)
            .into_iter()
            .find(|event| {
                event.signal_key == "directional_flow_acceleration" && event.state == "resolved"
            })
            .unwrap();
        assert_eq!(resolved.signal_id, triggered.signal_id);
        assert_ne!(resolved.event_id, triggered.event_id);
    }
}
