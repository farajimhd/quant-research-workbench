use crate::bars::TradeUpdateRule;
use crate::event::MarketEvent;
use chrono::{DateTime, NaiveDate, Timelike, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::{HashMap, HashSet};

pub const GENERIC_STRUCTURE_ALGORITHM_VERSION: u16 = 4;
const SESSION_ANCHOR_SECONDS: u32 = 4 * 60 * 60;
const REGULAR_OPEN_SECONDS: u32 = 9 * 60 * 60 + 30 * 60;
const OPENING_RANGE_END_SECONDS: u32 = 9 * 60 * 60 + 35 * 60;
const MOVE_HALF_LIFE_SECONDS: f64 = 30.0;
const SPREAD_HALF_LIFE_SECONDS: f64 = 15.0;
const MAX_ZONES_PER_SIDE: usize = 48;
const MAX_EXPOSED_LEVELS_PER_SIDE: usize = 8;
const MAX_PRESSURE_CLUSTERS_PER_SIDE: usize = 12;

#[derive(Clone, Copy, Debug)]
struct ScaleConfig {
    name: &'static str,
    threshold_multiplier: f64,
    acceptance_events: u32,
    acceptance_millis: i64,
    retention_half_life_seconds: f64,
    weight: f64,
}

const SCALE_CONFIGS: [ScaleConfig; 3] = [
    ScaleConfig {
        name: "micro",
        threshold_multiplier: 1.0,
        acceptance_events: 2,
        acceptance_millis: 100,
        retention_half_life_seconds: 30.0 * 60.0,
        weight: 0.20,
    },
    ScaleConfig {
        name: "tactical",
        threshold_multiplier: 3.0,
        acceptance_events: 3,
        acceptance_millis: 300,
        retention_half_life_seconds: 5.0 * 24.0 * 60.0 * 60.0,
        weight: 0.35,
    },
    ScaleConfig {
        name: "context",
        threshold_multiplier: 8.0,
        acceptance_events: 5,
        acceptance_millis: 1_000,
        retention_half_life_seconds: 45.0 * 24.0 * 60.0 * 60.0,
        weight: 0.45,
    },
];

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureLevelSnapshot {
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub strength: f64,
    pub confidence: f64,
    pub touch_count: u32,
    pub hold_count: u32,
    pub created_at_ms: i64,
    pub last_test_at_ms: i64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureLevelCandidate {
    pub scale: String,
    pub side: i8,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub strength: f64,
    pub confidence: f64,
    pub evidence_score: f64,
    pub distance: f64,
    pub touch_count: u32,
    pub hold_count: u32,
    pub created_at_ms: i64,
    pub last_test_at_ms: i64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureScaleSnapshot {
    pub direction: i8,
    pub threshold: f64,
    pub swing_high: f64,
    pub swing_low: f64,
    pub support: StructureLevelSnapshot,
    pub resistance: StructureLevelSnapshot,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct GenericStructureSnapshot {
    pub algorithm_version: u16,
    pub reference_price: f64,
    pub direction: i8,
    pub agreement: f64,
    pub strength: f64,
    pub confidence: f64,
    #[serde(default)]
    pub support_field: f64,
    #[serde(default)]
    pub resistance_field: f64,
    #[serde(default)]
    pub pressure_bias: f64,
    #[serde(default)]
    pub pressure_confidence: f64,
    #[serde(default = "default_up_probability")]
    pub up_probability: f64,
    pub support: StructureLevelSnapshot,
    pub resistance: StructureLevelSnapshot,
    #[serde(default)]
    pub active_levels: Vec<StructureLevelCandidate>,
    pub micro: StructureScaleSnapshot,
    pub tactical: StructureScaleSnapshot,
    pub context: StructureScaleSnapshot,
    pub last_event_id: u64,
    pub last_event_pivot_at_ms: i64,
    pub last_event_at_ms: i64,
    pub last_event_kind: String,
    pub last_event_scale: String,
    pub last_event_direction: i8,
    pub last_event_price: f64,
    pub session_high: f64,
    pub session_low: f64,
    pub premarket_high: f64,
    pub premarket_low: f64,
    pub opening_range_high: f64,
    pub opening_range_low: f64,
    pub trade_volume_poc: f64,
    pub nearest_round: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct StructuralPressureField {
    support: f64,
    resistance: f64,
    bias: f64,
    confidence: f64,
    up_probability: f64,
}

#[derive(Clone, Debug)]
struct PressureCluster {
    lower: f64,
    upper: f64,
    distance: f64,
    evidence: f64,
    scales: HashSet<&'static str>,
}

#[derive(Clone, Copy, Debug)]
struct PressureEvidence {
    scale: &'static str,
    side: i8,
    lower: f64,
    upper: f64,
    distance: f64,
    evidence: f64,
}

fn default_up_probability() -> f64 {
    0.5
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GenericStructureEvent {
    pub algorithm_version: u16,
    pub event_id: u64,
    pub sym: String,
    pub scale: String,
    pub event_kind: String,
    pub direction: i8,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub strength: f64,
    pub confidence: f64,
    pub pivot_at: DateTime<Utc>,
    pub confirmed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Pivot {
    price: f64,
    pivot_at: DateTime<Utc>,
    confirmed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Zone {
    side: i8,
    price: f64,
    lower: f64,
    upper: f64,
    created_at: DateTime<Utc>,
    last_test_at: DateTime<Utc>,
    touch_count: u32,
    hold_count: u32,
    break_count: u32,
    trade_confirmations: u32,
    seeded_confidence: f64,
    seeded_strength: f64,
    in_contact: bool,
    lifecycle: ZoneLifecycle,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
enum ZoneLifecycle {
    #[default]
    Active,
    BreachCandidate(BoundaryAcceptance),
    AwaitingRetest,
    RetestContact,
    RejectionCandidate(BoundaryAcceptance),
    Retired,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct BoundaryAcceptance {
    first_observed_at: DateTime<Utc>,
    beyond_events: u32,
    trade_confirmed: bool,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct BreakCandidate {
    direction: i8,
    level: f64,
    first_crossed_at: DateTime<Utc>,
    beyond_events: u32,
    trade_confirmed: bool,
}

impl Zone {
    fn is_active(&self) -> bool {
        matches!(
            self.lifecycle,
            ZoneLifecycle::Active | ZoneLifecycle::BreachCandidate(_)
        )
    }

    fn is_pending_reversal(&self) -> bool {
        matches!(
            self.lifecycle,
            ZoneLifecycle::AwaitingRetest
                | ZoneLifecycle::RetestContact
                | ZoneLifecycle::RejectionCandidate(_)
        )
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct ScaleState {
    leg_direction: i8,
    trend_direction: i8,
    candidate_high: f64,
    candidate_high_at: Option<DateTime<Utc>>,
    candidate_low: f64,
    candidate_low_at: Option<DateTime<Utc>>,
    previous_high: Option<Pivot>,
    previous_low: Option<Pivot>,
    swing_high: Option<Pivot>,
    swing_low: Option<Pivot>,
    support_zones: Vec<Zone>,
    resistance_zones: Vec<Zone>,
    break_candidate: Option<BreakCandidate>,
    last_broken_high_event: u64,
    last_broken_low_event: u64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimedEwma {
    value: f64,
    last_ts: Option<DateTime<Utc>>,
}

impl TimedEwma {
    fn update(&mut self, ts: DateTime<Utc>, observation: f64, half_life_seconds: f64) {
        if !observation.is_finite() || observation < 0.0 {
            return;
        }
        let Some(previous_ts) = self.last_ts else {
            self.value = observation;
            self.last_ts = Some(ts);
            return;
        };
        let elapsed = (ts - previous_ts)
            .num_microseconds()
            .unwrap_or_default()
            .max(0) as f64
            / 1_000_000.0;
        let alpha = if elapsed > 0.0 {
            1.0 - 0.5_f64.powf(elapsed / half_life_seconds.max(0.001))
        } else {
            0.0
        };
        self.value += alpha * (observation - self.value);
        self.last_ts = Some(ts);
    }
}

#[derive(Clone, Debug)]
pub struct GenericStructureEngine {
    sym: String,
    last_ts: Option<DateTime<Utc>>,
    last_reference_price: f64,
    last_structure_price: f64,
    bid: f64,
    ask: f64,
    spread_ewma: TimedEwma,
    move_ewma: TimedEwma,
    scales: Vec<ScaleState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, f64>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GenericStructureCheckpoint {
    pub algorithm_version: u16,
    pub sym: String,
    pub updated_at: Option<DateTime<Utc>>,
    last_reference_price: f64,
    #[serde(default)]
    last_structure_price: f64,
    bid: f64,
    ask: f64,
    spread_ewma: TimedEwma,
    move_ewma: TimedEwma,
    scales: Vec<ScaleState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, f64>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
}

impl GenericStructureEngine {
    pub fn new(sym: impl Into<String>) -> Self {
        Self {
            sym: sym.into().to_ascii_uppercase(),
            last_ts: None,
            last_reference_price: 0.0,
            last_structure_price: 0.0,
            bid: 0.0,
            ask: 0.0,
            spread_ewma: TimedEwma::default(),
            move_ewma: TimedEwma::default(),
            scales: vec![ScaleState::default(); SCALE_CONFIGS.len()],
            session_anchor: None,
            session_high: 0.0,
            session_low: 0.0,
            premarket_high: 0.0,
            premarket_low: 0.0,
            opening_range_high: 0.0,
            opening_range_low: 0.0,
            session_volume_by_price: HashMap::new(),
            trade_volume_poc: 0.0,
            last_event: None,
        }
    }

    pub fn apply_event(
        &mut self,
        event: &MarketEvent,
        trade_rule: TradeUpdateRule,
    ) -> (GenericStructureSnapshot, Vec<GenericStructureEvent>) {
        let ts = event.ts();
        if self.last_ts.is_some_and(|previous| ts < previous) {
            return (self.snapshot(ts), Vec::new());
        }
        self.reset_session_if_needed(ts);
        let mut eligible_trade_price = 0.0;
        let mut eligible_trade_size = 0.0;
        let reference = match event {
            MarketEvent::Quote(quote)
                if quote.bid_price > 0.0
                    && quote.ask_price > quote.bid_price
                    && quote.bid_price.is_finite()
                    && quote.ask_price.is_finite() =>
            {
                self.bid = quote.bid_price;
                self.ask = quote.ask_price;
                self.spread_ewma.update(
                    ts,
                    quote.ask_price - quote.bid_price,
                    SPREAD_HALF_LIFE_SECONDS,
                );
                (quote.bid_price + quote.ask_price) / 2.0
            }
            MarketEvent::Trade(trade) if trade_rule.update_last && trade.price > 0.0 => {
                eligible_trade_price = trade.price;
                eligible_trade_size = if trade_rule.update_volume {
                    trade.size.max(0.0)
                } else {
                    0.0
                };
                self.observe_trade(ts, trade.price, eligible_trade_size);
                if self.bid > 0.0 && self.ask > self.bid {
                    (self.bid + self.ask) / 2.0
                } else {
                    trade.price
                }
            }
            _ => self.last_reference_price,
        };
        if !(reference > 0.0 && reference.is_finite()) {
            self.last_ts = Some(ts);
            return (self.snapshot(ts), Vec::new());
        }
        if eligible_trade_price > 0.0 && self.last_structure_price > 0.0 {
            self.move_ewma.update(
                ts,
                (eligible_trade_price - self.last_structure_price).abs(),
                MOVE_HALF_LIFE_SECONDS,
            );
        }
        if eligible_trade_price > 0.0 {
            self.last_structure_price = eligible_trade_price;
        }
        self.last_reference_price = reference;
        self.last_ts = Some(ts);
        let base_threshold = self.base_threshold(reference);
        let mut emitted = Vec::new();
        for (index, config) in SCALE_CONFIGS.iter().copied().enumerate() {
            let threshold = base_threshold * config.threshold_multiplier;
            let state = &mut self.scales[index];
            update_zones(
                state,
                config,
                reference,
                eligible_trade_price,
                eligible_trade_size,
                threshold,
                ts,
                &mut emitted,
                &self.sym,
            );
            // Price structure must match the traded-price candles on which it
            // is audited. Quotes continue to update liquidity zones and the
            // NBBO reference, but cannot manufacture a swing or structure
            // break that never traded.
            if eligible_trade_price > 0.0 {
                update_directional_change(
                    state,
                    config,
                    eligible_trade_price,
                    threshold,
                    ts,
                    &mut emitted,
                    &self.sym,
                );
                update_structure_break(
                    state,
                    config,
                    eligible_trade_price,
                    eligible_trade_price,
                    eligible_trade_size,
                    threshold,
                    ts,
                    &mut emitted,
                    &self.sym,
                );
            }
        }
        if let Some(last) = emitted.last().cloned() {
            self.last_event = Some(last);
        }
        (self.snapshot(ts), emitted)
    }

    pub fn seed_events(&mut self, events: &[GenericStructureEvent]) {
        let mut ordered = events
            .iter()
            .filter(|event| {
                event.algorithm_version == GENERIC_STRUCTURE_ALGORITHM_VERSION
                    && event.sym.eq_ignore_ascii_case(&self.sym)
            })
            .cloned()
            .collect::<Vec<_>>();
        ordered.sort_by_key(|event| (event.confirmed_at, event.event_id));
        let mut seen = HashSet::new();
        for event in ordered {
            if !seen.insert(event.event_id) {
                continue;
            }
            let Some(scale_index) = SCALE_CONFIGS
                .iter()
                .position(|config| config.name == event.scale)
            else {
                continue;
            };
            seed_scale_event(&mut self.scales[scale_index], &event);
            if self.last_event.as_ref().is_none_or(|current| {
                (event.confirmed_at, event.event_id) > (current.confirmed_at, current.event_id)
            }) {
                self.last_event = Some(event);
            }
        }
    }

    pub fn checkpoint(&self) -> GenericStructureCheckpoint {
        GenericStructureCheckpoint {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            sym: self.sym.clone(),
            updated_at: self.last_ts,
            last_reference_price: self.last_reference_price,
            last_structure_price: self.last_structure_price,
            bid: self.bid,
            ask: self.ask,
            spread_ewma: self.spread_ewma.clone(),
            move_ewma: self.move_ewma.clone(),
            scales: self.scales.clone(),
            session_anchor: self.session_anchor,
            session_high: self.session_high,
            session_low: self.session_low,
            premarket_high: self.premarket_high,
            premarket_low: self.premarket_low,
            opening_range_high: self.opening_range_high,
            opening_range_low: self.opening_range_low,
            session_volume_by_price: self.session_volume_by_price.clone(),
            trade_volume_poc: self.trade_volume_poc,
            last_event: self.last_event.clone(),
        }
    }

    pub fn updated_at_ms(&self) -> i64 {
        self.last_ts
            .map(|timestamp| timestamp.timestamp_millis())
            .unwrap_or_default()
    }

    pub fn seed_checkpoint(&mut self, checkpoint: &GenericStructureCheckpoint) {
        if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
            || !checkpoint.sym.eq_ignore_ascii_case(&self.sym)
        {
            return;
        }
        self.last_ts = checkpoint.updated_at;
        self.last_reference_price = checkpoint.last_reference_price;
        self.last_structure_price = checkpoint.last_structure_price;
        self.bid = checkpoint.bid;
        self.ask = checkpoint.ask;
        self.spread_ewma = checkpoint.spread_ewma.clone();
        self.move_ewma = checkpoint.move_ewma.clone();
        self.scales = checkpoint.scales.clone();
        self.session_anchor = checkpoint.session_anchor;
        self.session_high = checkpoint.session_high;
        self.session_low = checkpoint.session_low;
        self.premarket_high = checkpoint.premarket_high;
        self.premarket_low = checkpoint.premarket_low;
        self.opening_range_high = checkpoint.opening_range_high;
        self.opening_range_low = checkpoint.opening_range_low;
        self.session_volume_by_price = checkpoint.session_volume_by_price.clone();
        self.trade_volume_poc = checkpoint.trade_volume_poc;
        self.last_event = checkpoint.last_event.clone();
    }

    pub fn seed_snapshot(&mut self, snapshot: &GenericStructureSnapshot) {
        if snapshot.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
            return;
        }
        self.last_reference_price = snapshot.reference_price;
        self.last_structure_price = snapshot.reference_price;
        self.session_high = snapshot.session_high;
        self.session_low = snapshot.session_low;
        self.premarket_high = snapshot.premarket_high;
        self.premarket_low = snapshot.premarket_low;
        self.opening_range_high = snapshot.opening_range_high;
        self.opening_range_low = snapshot.opening_range_low;
        self.trade_volume_poc = snapshot.trade_volume_poc;
        for (state, scale) in
            self.scales
                .iter_mut()
                .zip([&snapshot.micro, &snapshot.tactical, &snapshot.context])
        {
            seed_scale_snapshot(state, scale);
        }
        if snapshot.last_event_id > 0 && snapshot.last_event_at_ms > 0 {
            if let Some(confirmed_at) =
                DateTime::<Utc>::from_timestamp_millis(snapshot.last_event_at_ms)
            {
                self.session_anchor = Some(market_session_anchor_date(confirmed_at));
                self.last_event = Some(GenericStructureEvent {
                    algorithm_version: snapshot.algorithm_version,
                    event_id: snapshot.last_event_id,
                    sym: self.sym.clone(),
                    scale: snapshot.last_event_scale.clone(),
                    event_kind: snapshot.last_event_kind.clone(),
                    direction: snapshot.last_event_direction,
                    price: snapshot.last_event_price,
                    lower: snapshot.last_event_price,
                    upper: snapshot.last_event_price,
                    strength: snapshot.strength,
                    confidence: snapshot.confidence,
                    pivot_at: DateTime::<Utc>::from_timestamp_millis(
                        snapshot.last_event_pivot_at_ms,
                    )
                    .unwrap_or(confirmed_at),
                    confirmed_at,
                });
            }
        }
    }

    fn reset_session_if_needed(&mut self, ts: DateTime<Utc>) {
        let anchor = market_session_anchor_date(ts);
        if self.session_anchor == Some(anchor) {
            return;
        }
        self.session_anchor = Some(anchor);
        self.session_high = 0.0;
        self.session_low = 0.0;
        self.premarket_high = 0.0;
        self.premarket_low = 0.0;
        self.opening_range_high = 0.0;
        self.opening_range_low = 0.0;
        self.session_volume_by_price.clear();
        self.trade_volume_poc = 0.0;
    }

    fn observe_trade(&mut self, ts: DateTime<Utc>, price: f64, size: f64) {
        update_high_low(&mut self.session_high, &mut self.session_low, price);
        let seconds = ts
            .with_timezone(&New_York)
            .time()
            .num_seconds_from_midnight();
        if (SESSION_ANCHOR_SECONDS..REGULAR_OPEN_SECONDS).contains(&seconds) {
            update_high_low(&mut self.premarket_high, &mut self.premarket_low, price);
        }
        if (REGULAR_OPEN_SECONDS..OPENING_RANGE_END_SECONDS).contains(&seconds) {
            update_high_low(
                &mut self.opening_range_high,
                &mut self.opening_range_low,
                price,
            );
        }
        if size > 0.0 {
            let key = price_key(price);
            *self.session_volume_by_price.entry(key).or_default() += size;
            self.trade_volume_poc = self
                .session_volume_by_price
                .iter()
                .max_by(|left, right| left.1.total_cmp(right.1))
                .map(|(key, _)| price_from_key(*key))
                .unwrap_or_default();
        }
    }

    fn base_threshold(&self, reference: f64) -> f64 {
        let tick = price_tick(reference);
        let floor = (2.0 * tick).max(reference * 0.00005);
        let adaptive = (self.spread_ewma.value * 1.25).max(self.move_ewma.value * 1.5);
        // Sparse prints, locked/crossed transitions, and transient wide quotes
        // must not inflate a micro reversal into a multi-percent move. The cap
        // remains price-relative, and Tactical/Context retain their 3x/8x
        // separation from the same robust base.
        let robust_cap = (reference * 0.0025).max(4.0 * tick);
        floor.max(adaptive.min(robust_cap))
    }

    pub fn snapshot(&self, now: DateTime<Utc>) -> GenericStructureSnapshot {
        let base_threshold = self.base_threshold(self.last_reference_price);
        let snapshots = SCALE_CONFIGS
            .iter()
            .enumerate()
            .map(|(index, config)| {
                scale_snapshot(
                    &self.scales[index],
                    *config,
                    self.last_reference_price,
                    base_threshold,
                    now,
                )
            })
            .collect::<Vec<_>>();
        let signed = snapshots
            .iter()
            .zip(SCALE_CONFIGS)
            .map(|(snapshot, config)| snapshot.direction as f64 * config.weight)
            .sum::<f64>();
        let active_weight = snapshots
            .iter()
            .zip(SCALE_CONFIGS)
            .filter(|(snapshot, _)| snapshot.direction != 0)
            .map(|(_, config)| config.weight)
            .sum::<f64>();
        let direction = if signed > 0.15 {
            1
        } else if signed < -0.15 {
            -1
        } else {
            0
        };
        let agreement = if active_weight > 0.0 {
            (signed.abs() / active_weight).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let strength = snapshots
            .iter()
            .zip(SCALE_CONFIGS)
            .map(|(snapshot, config)| {
                snapshot.support.strength.max(snapshot.resistance.strength) * config.weight
            })
            .sum::<f64>()
            .clamp(0.0, 1.0);
        let confidence = (snapshots
            .iter()
            .zip(SCALE_CONFIGS)
            .map(|(snapshot, config)| {
                snapshot
                    .support
                    .confidence
                    .max(snapshot.resistance.confidence)
                    * config.weight
            })
            .sum::<f64>()
            * (0.55 + 0.45 * agreement))
            .clamp(0.0, 1.0);
        let support = select_unified_level(&snapshots, true, self.last_reference_price);
        let resistance = select_unified_level(&snapshots, false, self.last_reference_price);
        let active_levels = exposed_active_levels(&self.scales, self.last_reference_price, now);
        let pressure =
            structural_pressure_field(&self.scales, self.last_reference_price, base_threshold, now);
        let last = self.last_event.as_ref();
        GenericStructureSnapshot {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            reference_price: self.last_reference_price,
            direction,
            agreement,
            strength,
            confidence,
            support_field: pressure.support,
            resistance_field: pressure.resistance,
            pressure_bias: pressure.bias,
            pressure_confidence: pressure.confidence,
            up_probability: pressure.up_probability,
            support,
            resistance,
            active_levels,
            micro: snapshots.first().cloned().unwrap_or_default(),
            tactical: snapshots.get(1).cloned().unwrap_or_default(),
            context: snapshots.get(2).cloned().unwrap_or_default(),
            last_event_id: last.map(|event| event.event_id).unwrap_or_default(),
            last_event_pivot_at_ms: last
                .map(|event| event.pivot_at.timestamp_millis())
                .unwrap_or_default(),
            last_event_at_ms: last
                .map(|event| event.confirmed_at.timestamp_millis())
                .unwrap_or_default(),
            last_event_kind: last
                .map(|event| event.event_kind.clone())
                .unwrap_or_default(),
            last_event_scale: last.map(|event| event.scale.clone()).unwrap_or_default(),
            last_event_direction: last.map(|event| event.direction).unwrap_or_default(),
            last_event_price: last.map(|event| event.price).unwrap_or_default(),
            session_high: self.session_high,
            session_low: self.session_low,
            premarket_high: self.premarket_high,
            premarket_low: self.premarket_low,
            opening_range_high: self.opening_range_high,
            opening_range_low: self.opening_range_low,
            trade_volume_poc: self.trade_volume_poc,
            nearest_round: nearest_round_price(self.last_reference_price),
        }
    }
}

fn structural_pressure_field(
    states: &[ScaleState],
    reference: f64,
    base_threshold: f64,
    now: DateTime<Utc>,
) -> StructuralPressureField {
    if reference <= 0.0 || !reference.is_finite() || base_threshold <= 0.0 {
        return StructuralPressureField {
            up_probability: 0.5,
            ..StructuralPressureField::default()
        };
    }

    let evidence = states
        .iter()
        .zip(SCALE_CONFIGS)
        .flat_map(|(state, config)| {
            state
                .support_zones
                .iter()
                .chain(state.resistance_zones.iter())
                .filter(|zone| zone.is_active() && zone_is_on_expected_side(zone, reference))
                .filter_map(move |zone| {
                    let snapshot = zone_snapshot(zone, config, now);
                    let distance = zone_distance(zone, reference);
                    let normalized_distance =
                        distance / (base_threshold * config.threshold_multiplier).max(f64::EPSILON);
                    let proximity = (-normalized_distance / 6.0).exp();
                    let scale_reliability = 0.75 + 0.25 * (config.weight / 0.45);
                    let score =
                        snapshot.strength * snapshot.confidence * scale_reliability * proximity;
                    (score > 0.0 && score.is_finite()).then_some(PressureEvidence {
                        scale: config.name,
                        side: zone.side,
                        lower: zone.lower,
                        upper: zone.upper,
                        distance,
                        evidence: score.clamp(0.0, 1.0),
                    })
                })
        })
        .collect::<Vec<_>>();

    let support = pressure_side_field(&evidence, 1, base_threshold);
    let resistance = pressure_side_field(&evidence, -1, base_threshold);
    let total = support + resistance;
    let bias = ((support - resistance) / (total + 0.20)).clamp(-1.0, 1.0);
    let coverage = (1.0 - (1.0 - support) * (1.0 - resistance)).clamp(0.0, 1.0);
    let separation = if total > f64::EPSILON {
        ((support - resistance).abs() / total).clamp(0.0, 1.0)
    } else {
        0.0
    };
    let confidence = (coverage * separation).clamp(0.0, 1.0);
    let up_probability = (0.5 + 0.5 * bias * confidence).clamp(0.0, 1.0);

    StructuralPressureField {
        support,
        resistance,
        bias,
        confidence,
        up_probability,
    }
}

fn pressure_side_field(evidence: &[PressureEvidence], side: i8, base_threshold: f64) -> f64 {
    let merge_gap = base_threshold * 0.75;
    let mut side_evidence = evidence
        .iter()
        .copied()
        .filter(|candidate| candidate.side == side)
        .collect::<Vec<_>>();
    side_evidence.sort_by(|left, right| {
        left.lower
            .total_cmp(&right.lower)
            .then_with(|| left.upper.total_cmp(&right.upper))
            .then_with(|| right.evidence.total_cmp(&left.evidence))
    });

    let mut clusters: Vec<PressureCluster> = Vec::new();
    for candidate in side_evidence {
        if let Some(cluster) = clusters.iter_mut().find(|cluster| {
            candidate.lower <= cluster.upper + merge_gap
                && candidate.upper >= cluster.lower - merge_gap
        }) {
            let independence = if cluster.scales.contains(candidate.scale) {
                0.35
            } else {
                0.65
            };
            cluster.evidence = 1.0
                - (1.0 - cluster.evidence)
                    * (1.0 - candidate.evidence * independence).clamp(0.0, 1.0);
            cluster.lower = cluster.lower.min(candidate.lower);
            cluster.upper = cluster.upper.max(candidate.upper);
            cluster.distance = cluster.distance.min(candidate.distance);
            cluster.scales.insert(candidate.scale);
        } else {
            clusters.push(PressureCluster {
                lower: candidate.lower,
                upper: candidate.upper,
                distance: candidate.distance,
                evidence: candidate.evidence,
                scales: HashSet::from([candidate.scale]),
            });
        }
    }

    clusters.sort_by(|left, right| {
        left.distance
            .total_cmp(&right.distance)
            .then_with(|| right.evidence.total_cmp(&left.evidence))
    });
    1.0 - clusters
        .iter()
        .take(MAX_PRESSURE_CLUSTERS_PER_SIDE)
        .fold(1.0, |remaining, cluster| {
            remaining * (1.0 - cluster.evidence).clamp(0.0, 1.0)
        })
}

fn exposed_active_levels(
    states: &[ScaleState],
    reference: f64,
    now: DateTime<Utc>,
) -> Vec<StructureLevelCandidate> {
    let mut candidates = states
        .iter()
        .zip(SCALE_CONFIGS)
        .flat_map(|(state, config)| {
            state
                .support_zones
                .iter()
                .chain(state.resistance_zones.iter())
                .filter(|zone| zone.is_active())
                .filter(move |zone| zone_is_on_expected_side(zone, reference))
                .map(move |zone| {
                    let snapshot = zone_snapshot(zone, config, now);
                    let distance = zone_distance(zone, reference);
                    StructureLevelCandidate {
                        scale: config.name.to_string(),
                        side: zone.side,
                        price: snapshot.price,
                        lower: snapshot.lower,
                        upper: snapshot.upper,
                        strength: snapshot.strength,
                        confidence: snapshot.confidence,
                        evidence_score: snapshot.strength * snapshot.confidence * config.weight,
                        distance,
                        touch_count: snapshot.touch_count,
                        hold_count: snapshot.hold_count,
                        created_at_ms: snapshot.created_at_ms,
                        last_test_at_ms: snapshot.last_test_at_ms,
                    }
                })
        })
        .filter(|level| level.price > 0.0 && level.price.is_finite())
        .collect::<Vec<_>>();
    candidates.sort_by(|left, right| {
        left.side
            .cmp(&right.side)
            .then_with(|| left.distance.total_cmp(&right.distance))
            .then_with(|| right.evidence_score.total_cmp(&left.evidence_score))
    });

    let mut exposed = Vec::new();
    for side in [1_i8, -1_i8] {
        let side_candidates = candidates
            .iter()
            .filter(|candidate| candidate.side == side)
            .collect::<Vec<_>>();
        exposed.extend(
            side_candidates
                .iter()
                .take(MAX_EXPOSED_LEVELS_PER_SIDE)
                .map(|candidate| (*candidate).clone()),
        );
        if let Some(strongest) = side_candidates
            .iter()
            .max_by(|left, right| left.evidence_score.total_cmp(&right.evidence_score))
        {
            if !exposed.iter().any(|candidate| {
                candidate.side == side
                    && candidate.scale == strongest.scale
                    && (candidate.price - strongest.price).abs() <= price_tick(strongest.price)
            }) {
                exposed.push((**strongest).clone());
            }
        }
    }
    exposed
}

fn seed_scale_snapshot(state: &mut ScaleState, snapshot: &StructureScaleSnapshot) {
    state.trend_direction = snapshot.direction;
    let fallback_ms = snapshot
        .support
        .created_at_ms
        .max(snapshot.resistance.created_at_ms);
    let fallback =
        DateTime::<Utc>::from_timestamp_millis(fallback_ms).unwrap_or(DateTime::<Utc>::UNIX_EPOCH);
    if snapshot.swing_high > 0.0 {
        state.swing_high = Some(Pivot {
            price: snapshot.swing_high,
            pivot_at: fallback,
            confirmed_at: fallback,
        });
    }
    if snapshot.swing_low > 0.0 {
        state.swing_low = Some(Pivot {
            price: snapshot.swing_low,
            pivot_at: fallback,
            confirmed_at: fallback,
        });
    }
    if snapshot.support.price > 0.0 {
        state
            .support_zones
            .push(zone_from_snapshot(1, &snapshot.support));
    }
    if snapshot.resistance.price > 0.0 {
        state
            .resistance_zones
            .push(zone_from_snapshot(-1, &snapshot.resistance));
    }
}

fn zone_from_snapshot(side: i8, snapshot: &StructureLevelSnapshot) -> Zone {
    let created_at = DateTime::<Utc>::from_timestamp_millis(snapshot.created_at_ms)
        .unwrap_or(DateTime::<Utc>::UNIX_EPOCH);
    let last_test_at =
        DateTime::<Utc>::from_timestamp_millis(snapshot.last_test_at_ms).unwrap_or(created_at);
    Zone {
        side,
        price: snapshot.price,
        lower: snapshot.lower,
        upper: snapshot.upper,
        created_at,
        last_test_at,
        touch_count: snapshot.touch_count.max(1),
        hold_count: snapshot.hold_count,
        break_count: 0,
        trade_confirmations: 0,
        seeded_confidence: snapshot.confidence.clamp(0.0, 1.0),
        seeded_strength: snapshot.strength.clamp(0.0, 1.0),
        in_contact: false,
        lifecycle: ZoneLifecycle::Active,
    }
}

fn seed_scale_event(state: &mut ScaleState, event: &GenericStructureEvent) {
    let pivot = Pivot {
        price: event.price,
        pivot_at: event.pivot_at,
        confirmed_at: event.confirmed_at,
    };
    match event.event_kind.as_str() {
        "pivot_high" => {
            state.previous_high = state.swing_high.replace(pivot);
            seed_zone(&mut state.resistance_zones, -1, event, true);
            update_trend(state);
        }
        "pivot_low" => {
            state.previous_low = state.swing_low.replace(pivot);
            seed_zone(&mut state.support_zones, 1, event, true);
            update_trend(state);
        }
        "touch" | "hold" => {
            let zones = if event.direction > 0 {
                &mut state.support_zones
            } else {
                &mut state.resistance_zones
            };
            let zone = seed_zone(zones, event.direction, event, true);
            zone.touch_count = zone.touch_count.max(1);
            if event.event_kind == "hold" {
                zone.hold_count = zone.hold_count.saturating_add(1);
            }
        }
        "bos" | "choch" => {
            state.trend_direction = event.direction;
            let zones = if event.direction > 0 {
                &mut state.resistance_zones
            } else {
                &mut state.support_zones
            };
            let zone = seed_zone(zones, -event.direction, event, false);
            zone.lifecycle = ZoneLifecycle::AwaitingRetest;
            zone.break_count = zone.break_count.saturating_add(1);
            zone.trade_confirmations = zone.trade_confirmations.saturating_add(1);
            let fingerprint = stable_event_id(
                &event.sym,
                &event.scale,
                "break_source",
                event.direction,
                event.price,
                event.pivot_at,
            );
            if event.direction > 0 {
                state.last_broken_high_event = fingerprint;
            } else {
                state.last_broken_low_event = fingerprint;
            }
        }
        "level_break" => {
            let original_side = -event.direction;
            let zones = if original_side > 0 {
                &mut state.support_zones
            } else {
                &mut state.resistance_zones
            };
            let zone = seed_zone(zones, original_side, event, false);
            zone.lifecycle = ZoneLifecycle::AwaitingRetest;
            zone.break_count = zone.break_count.saturating_add(1);
            zone.trade_confirmations = zone.trade_confirmations.saturating_add(1);
        }
        "role_reversal" => {
            let original_zones = if event.direction > 0 {
                &mut state.resistance_zones
            } else {
                &mut state.support_zones
            };
            retire_matching_zone(original_zones, event.price, event.lower, event.upper);
            let zones = if event.direction > 0 {
                &mut state.support_zones
            } else {
                &mut state.resistance_zones
            };
            let zone = seed_zone(zones, event.direction, event, true);
            zone.lifecycle = ZoneLifecycle::Active;
            zone.touch_count = zone.touch_count.max(1);
            zone.hold_count = zone.hold_count.max(1);
            zone.trade_confirmations = zone.trade_confirmations.saturating_add(1);
        }
        _ => {}
    }
}

fn retire_matching_zone(zones: &mut [Zone], price: f64, lower: f64, upper: f64) {
    let tolerance = (upper - lower).abs().max(price_tick(price) * 2.0);
    if let Some(zone) = zones
        .iter_mut()
        .filter(|zone| !matches!(zone.lifecycle, ZoneLifecycle::Retired))
        .min_by(|left, right| {
            (left.price - price)
                .abs()
                .total_cmp(&(right.price - price).abs())
        })
        .filter(|zone| (zone.price - price).abs() <= tolerance)
    {
        zone.lifecycle = ZoneLifecycle::Retired;
        zone.in_contact = false;
    }
}

fn seed_zone<'a>(
    zones: &'a mut Vec<Zone>,
    side: i8,
    event: &GenericStructureEvent,
    active: bool,
) -> &'a mut Zone {
    let tolerance = (event.upper - event.lower)
        .abs()
        .max(price_tick(event.price) * 2.0);
    if let Some(index) = zones
        .iter()
        .enumerate()
        .filter(|(_, zone)| zone.side == side)
        .min_by(|(_, left), (_, right)| {
            (left.price - event.price)
                .abs()
                .total_cmp(&(right.price - event.price).abs())
        })
        .filter(|(_, zone)| (zone.price - event.price).abs() <= tolerance)
        .map(|(index, _)| index)
    {
        let zone = &mut zones[index];
        zone.lower = zone.lower.min(event.lower);
        zone.upper = zone.upper.max(event.upper);
        zone.last_test_at = zone.last_test_at.max(event.confirmed_at);
        zone.seeded_strength = zone.seeded_strength.max(event.strength);
        zone.seeded_confidence = zone.seeded_confidence.max(event.confidence);
        if active {
            zone.lifecycle = ZoneLifecycle::Active;
        }
        return zone;
    }
    zones.push(Zone {
        side,
        price: event.price,
        lower: event.lower,
        upper: event.upper,
        created_at: event.confirmed_at,
        last_test_at: event.confirmed_at,
        touch_count: 1,
        hold_count: 0,
        break_count: 0,
        trade_confirmations: 0,
        seeded_confidence: event.confidence.clamp(0.0, 1.0),
        seeded_strength: event.strength.clamp(0.0, 1.0),
        in_contact: false,
        lifecycle: if active {
            ZoneLifecycle::Active
        } else {
            ZoneLifecycle::Retired
        },
    });
    let index = zones.len() - 1;
    &mut zones[index]
}

fn update_directional_change(
    state: &mut ScaleState,
    config: ScaleConfig,
    price: f64,
    threshold: f64,
    ts: DateTime<Utc>,
    emitted: &mut Vec<GenericStructureEvent>,
    sym: &str,
) {
    if state.candidate_high <= 0.0 {
        state.candidate_high = price;
        state.candidate_low = price;
        state.candidate_high_at = Some(ts);
        state.candidate_low_at = Some(ts);
        return;
    }
    if price > state.candidate_high {
        state.candidate_high = price;
        state.candidate_high_at = Some(ts);
    }
    if price < state.candidate_low {
        state.candidate_low = price;
        state.candidate_low_at = Some(ts);
    }
    if state.leg_direction == 0 {
        if price >= state.candidate_low + threshold {
            state.leg_direction = 1;
            state.candidate_high = price;
            state.candidate_high_at = Some(ts);
        } else if price <= state.candidate_high - threshold {
            state.leg_direction = -1;
            state.candidate_low = price;
            state.candidate_low_at = Some(ts);
        }
        return;
    }
    if state.leg_direction > 0 && price <= state.candidate_high - threshold {
        let pivot = Pivot {
            price: state.candidate_high,
            pivot_at: state.candidate_high_at.unwrap_or(ts),
            confirmed_at: ts,
        };
        state.previous_high = state.swing_high.replace(pivot.clone());
        update_trend(state);
        let zone = add_or_merge_zone(&mut state.resistance_zones, -1, &pivot, threshold);
        emitted.push(structure_event(
            sym,
            config.name,
            "pivot_high",
            -1,
            &pivot,
            &zone,
        ));
        state.leg_direction = -1;
        state.candidate_low = price;
        state.candidate_low_at = Some(ts);
        state.candidate_high = price;
        state.candidate_high_at = Some(ts);
    } else if state.leg_direction < 0 && price >= state.candidate_low + threshold {
        let pivot = Pivot {
            price: state.candidate_low,
            pivot_at: state.candidate_low_at.unwrap_or(ts),
            confirmed_at: ts,
        };
        state.previous_low = state.swing_low.replace(pivot.clone());
        update_trend(state);
        let zone = add_or_merge_zone(&mut state.support_zones, 1, &pivot, threshold);
        emitted.push(structure_event(
            sym,
            config.name,
            "pivot_low",
            1,
            &pivot,
            &zone,
        ));
        state.leg_direction = 1;
        state.candidate_high = price;
        state.candidate_high_at = Some(ts);
        state.candidate_low = price;
        state.candidate_low_at = Some(ts);
    }
}

fn update_trend(state: &mut ScaleState) {
    let highs = state
        .previous_high
        .as_ref()
        .zip(state.swing_high.as_ref())
        .map(|(previous, current)| current.price.total_cmp(&previous.price));
    let lows = state
        .previous_low
        .as_ref()
        .zip(state.swing_low.as_ref())
        .map(|(previous, current)| current.price.total_cmp(&previous.price));
    if highs.is_some_and(|order| order.is_gt()) && lows.is_some_and(|order| order.is_gt()) {
        state.trend_direction = 1;
    } else if highs.is_some_and(|order| order.is_lt()) && lows.is_some_and(|order| order.is_lt()) {
        state.trend_direction = -1;
    }
}

#[allow(clippy::too_many_arguments)]
fn update_structure_break(
    state: &mut ScaleState,
    config: ScaleConfig,
    reference: f64,
    trade_price: f64,
    trade_size: f64,
    threshold: f64,
    ts: DateTime<Utc>,
    emitted: &mut Vec<GenericStructureEvent>,
    sym: &str,
) {
    let high = state
        .swing_high
        .as_ref()
        .map(|pivot| pivot.price)
        .unwrap_or_default();
    let low = state
        .swing_low
        .as_ref()
        .map(|pivot| pivot.price)
        .unwrap_or_default();
    let buffer = threshold * 0.10;
    let crossing = if high > 0.0 && reference > high + buffer {
        Some((1, high))
    } else if low > 0.0 && reference < low - buffer {
        Some((-1, low))
    } else {
        None
    };
    let Some((direction, level)) = crossing else {
        state.break_candidate = None;
        return;
    };
    let candidate = state.break_candidate.get_or_insert_with(|| BreakCandidate {
        direction,
        level,
        first_crossed_at: ts,
        beyond_events: 0,
        trade_confirmed: false,
    });
    if candidate.direction != direction || (candidate.level - level).abs() > buffer.max(1e-9) {
        *candidate = BreakCandidate {
            direction,
            level,
            first_crossed_at: ts,
            beyond_events: 0,
            trade_confirmed: false,
        };
    }
    candidate.beyond_events = candidate.beyond_events.saturating_add(1);
    if trade_size > 0.0
        && ((direction > 0 && trade_price > level) || (direction < 0 && trade_price < level))
    {
        candidate.trade_confirmed = true;
    }
    let elapsed = (ts - candidate.first_crossed_at).num_milliseconds().max(0);
    let accepted = candidate.trade_confirmed
        && (candidate.beyond_events >= config.acceptance_events
            || elapsed >= config.acceptance_millis);
    if !accepted {
        return;
    }
    let pivot = if direction > 0 {
        state.swing_high.clone()
    } else {
        state.swing_low.clone()
    };
    let Some(pivot) = pivot else {
        return;
    };
    let fingerprint = stable_event_id(
        sym,
        config.name,
        "break_source",
        direction,
        level,
        pivot.pivot_at,
    );
    let already_broken = if direction > 0 {
        state.last_broken_high_event == fingerprint
    } else {
        state.last_broken_low_event == fingerprint
    };
    if already_broken {
        state.break_candidate = None;
        return;
    }
    let kind = if state.trend_direction != 0 && direction != state.trend_direction {
        "choch"
    } else {
        "bos"
    };
    state.trend_direction = direction;
    let zones = if direction > 0 {
        &mut state.resistance_zones
    } else {
        &mut state.support_zones
    };
    let mut broken_zone = Zone {
        side: -direction,
        price: level,
        lower: level - threshold * 0.25,
        upper: level + threshold * 0.25,
        created_at: pivot.confirmed_at,
        last_test_at: ts,
        touch_count: 1,
        hold_count: 0,
        break_count: 1,
        trade_confirmations: 1,
        seeded_confidence: 0.0,
        seeded_strength: 0.0,
        in_contact: false,
        lifecycle: ZoneLifecycle::AwaitingRetest,
    };
    if let Some(zone) = zones
        .iter_mut()
        .filter(|zone| !matches!(zone.lifecycle, ZoneLifecycle::Retired))
        .min_by(|left, right| {
            (left.price - level)
                .abs()
                .total_cmp(&(right.price - level).abs())
        })
        .filter(|zone| (zone.price - level).abs() <= threshold)
    {
        if zone.is_active() {
            zone.break_count = zone.break_count.saturating_add(1);
            zone.trade_confirmations = zone.trade_confirmations.saturating_add(1);
            zone.lifecycle = ZoneLifecycle::AwaitingRetest;
            zone.in_contact = false;
        }
        zone.last_test_at = ts;
        broken_zone = zone.clone();
    }
    let mut event = structure_event(sym, config.name, kind, direction, &pivot, &broken_zone);
    event.confirmed_at = ts;
    event.event_id = stable_event_id(sym, config.name, kind, direction, level, ts);
    event.price = level;
    emitted.push(event);
    if direction > 0 {
        state.last_broken_high_event = fingerprint;
    } else {
        state.last_broken_low_event = fingerprint;
    }
    state.break_candidate = None;
}

#[allow(clippy::too_many_arguments)]
fn update_zones(
    state: &mut ScaleState,
    config: ScaleConfig,
    reference: f64,
    trade_price: f64,
    trade_size: f64,
    threshold: f64,
    ts: DateTime<Utc>,
    emitted: &mut Vec<GenericStructureEvent>,
    sym: &str,
) {
    let mut new_resistance = update_zone_collection(
        &mut state.support_zones,
        config,
        reference,
        trade_price,
        trade_size,
        threshold,
        ts,
        emitted,
        sym,
    );
    let mut new_support = update_zone_collection(
        &mut state.resistance_zones,
        config,
        reference,
        trade_price,
        trade_size,
        threshold,
        ts,
        emitted,
        sym,
    );
    for zone in new_resistance.drain(..) {
        insert_role_reversed_zone(&mut state.resistance_zones, zone, threshold);
    }
    for zone in new_support.drain(..) {
        insert_role_reversed_zone(&mut state.support_zones, zone, threshold);
    }
    prune_zones(&mut state.support_zones);
    prune_zones(&mut state.resistance_zones);
}

#[allow(clippy::too_many_arguments)]
fn update_zone_collection(
    zones: &mut [Zone],
    config: ScaleConfig,
    reference: f64,
    trade_price: f64,
    trade_size: f64,
    threshold: f64,
    ts: DateTime<Utc>,
    emitted: &mut Vec<GenericStructureEvent>,
    sym: &str,
) -> Vec<Zone> {
    let mut reversed = Vec::new();
    let contact_buffer = threshold * 0.10;
    for zone in zones {
        let inside =
            reference >= zone.lower - contact_buffer && reference <= zone.upper + contact_buffer;
        let broken_side = zone_broken_side(zone, reference, contact_buffer);
        let reclaimed_side = zone_reclaimed_side(zone, reference, threshold);
        match zone.lifecycle.clone() {
            ZoneLifecycle::Retired => {}
            ZoneLifecycle::Active => {
                if broken_side {
                    zone.in_contact = false;
                    zone.lifecycle = ZoneLifecycle::BreachCandidate(new_acceptance(
                        zone,
                        trade_price,
                        trade_size,
                        ts,
                    ));
                } else {
                    update_active_contact(zone, config, reference, threshold, ts, emitted, sym);
                }
            }
            ZoneLifecycle::BreachCandidate(mut candidate) => {
                if !broken_side {
                    zone.lifecycle = ZoneLifecycle::Active;
                    update_active_contact(zone, config, reference, threshold, ts, emitted, sym);
                    continue;
                }
                advance_acceptance(&mut candidate, zone, trade_price, trade_size);
                if acceptance_confirmed(&candidate, config, ts) {
                    zone.lifecycle = ZoneLifecycle::AwaitingRetest;
                    zone.in_contact = false;
                    zone.break_count = zone.break_count.saturating_add(1);
                    zone.trade_confirmations = zone.trade_confirmations.saturating_add(1);
                    zone.last_test_at = ts;
                    let pivot = zone_pivot(zone, ts);
                    emitted.push(structure_event(
                        sym,
                        config.name,
                        "level_break",
                        -zone.side,
                        &pivot,
                        zone,
                    ));
                } else {
                    zone.lifecycle = ZoneLifecycle::BreachCandidate(candidate);
                }
            }
            ZoneLifecycle::AwaitingRetest => {
                if reclaimed_side {
                    zone.lifecycle = ZoneLifecycle::Retired;
                } else if inside {
                    zone.lifecycle = ZoneLifecycle::RetestContact;
                    zone.last_test_at = ts;
                }
            }
            ZoneLifecycle::RetestContact => {
                if reclaimed_side {
                    zone.lifecycle = ZoneLifecycle::Retired;
                } else if broken_side {
                    zone.lifecycle = ZoneLifecycle::RejectionCandidate(new_acceptance(
                        zone,
                        trade_price,
                        trade_size,
                        ts,
                    ));
                }
            }
            ZoneLifecycle::RejectionCandidate(mut candidate) => {
                if reclaimed_side {
                    zone.lifecycle = ZoneLifecycle::Retired;
                } else if !broken_side {
                    zone.lifecycle = ZoneLifecycle::RetestContact;
                } else {
                    advance_acceptance(&mut candidate, zone, trade_price, trade_size);
                    if acceptance_confirmed(&candidate, config, ts) {
                        let reversed_zone = role_reversed_zone(zone, ts);
                        let pivot = zone_pivot(&reversed_zone, ts);
                        emitted.push(structure_event(
                            sym,
                            config.name,
                            "role_reversal",
                            reversed_zone.side,
                            &pivot,
                            &reversed_zone,
                        ));
                        reversed.push(reversed_zone);
                        zone.lifecycle = ZoneLifecycle::Retired;
                        zone.last_test_at = ts;
                    } else {
                        zone.lifecycle = ZoneLifecycle::RejectionCandidate(candidate);
                    }
                }
            }
        }
    }
    reversed
}

fn update_active_contact(
    zone: &mut Zone,
    config: ScaleConfig,
    reference: f64,
    threshold: f64,
    ts: DateTime<Utc>,
    emitted: &mut Vec<GenericStructureEvent>,
    sym: &str,
) {
    let inside =
        reference >= zone.lower - threshold * 0.10 && reference <= zone.upper + threshold * 0.10;
    if inside && !zone.in_contact {
        zone.in_contact = true;
        zone.touch_count = zone.touch_count.saturating_add(1);
        zone.last_test_at = ts;
        let pivot = zone_pivot(zone, ts);
        emitted.push(structure_event(
            sym,
            config.name,
            "touch",
            zone.side,
            &pivot,
            zone,
        ));
    } else if zone.in_contact {
        let held = (zone.side > 0 && reference >= zone.upper + threshold)
            || (zone.side < 0 && reference <= zone.lower - threshold);
        if held {
            zone.in_contact = false;
            zone.hold_count = zone.hold_count.saturating_add(1);
            zone.last_test_at = ts;
            let pivot = zone_pivot(zone, ts);
            emitted.push(structure_event(
                sym,
                config.name,
                "hold",
                zone.side,
                &pivot,
                zone,
            ));
        }
    }
}

fn new_acceptance(
    zone: &Zone,
    trade_price: f64,
    trade_size: f64,
    ts: DateTime<Utc>,
) -> BoundaryAcceptance {
    BoundaryAcceptance {
        first_observed_at: ts,
        beyond_events: 1,
        trade_confirmed: qualifying_boundary_trade(zone, trade_price, trade_size),
    }
}

fn advance_acceptance(
    candidate: &mut BoundaryAcceptance,
    zone: &Zone,
    trade_price: f64,
    trade_size: f64,
) {
    candidate.beyond_events = candidate.beyond_events.saturating_add(1);
    candidate.trade_confirmed |= qualifying_boundary_trade(zone, trade_price, trade_size);
}

fn acceptance_confirmed(
    candidate: &BoundaryAcceptance,
    config: ScaleConfig,
    ts: DateTime<Utc>,
) -> bool {
    let elapsed = (ts - candidate.first_observed_at).num_milliseconds().max(0);
    candidate.trade_confirmed
        && (candidate.beyond_events >= config.acceptance_events
            || elapsed >= config.acceptance_millis)
}

fn qualifying_boundary_trade(zone: &Zone, trade_price: f64, trade_size: f64) -> bool {
    trade_size > 0.0
        && if zone.side > 0 {
            trade_price < zone.lower
        } else {
            trade_price > zone.upper
        }
}

fn zone_broken_side(zone: &Zone, reference: f64, buffer: f64) -> bool {
    if zone.side > 0 {
        reference < zone.lower - buffer
    } else {
        reference > zone.upper + buffer
    }
}

fn zone_reclaimed_side(zone: &Zone, reference: f64, threshold: f64) -> bool {
    if zone.side > 0 {
        reference > zone.upper + threshold
    } else {
        reference < zone.lower - threshold
    }
}

fn zone_pivot(zone: &Zone, confirmed_at: DateTime<Utc>) -> Pivot {
    Pivot {
        price: zone.price,
        pivot_at: zone.created_at,
        confirmed_at,
    }
}

fn role_reversed_zone(zone: &Zone, ts: DateTime<Utc>) -> Zone {
    Zone {
        side: -zone.side,
        price: zone.price,
        lower: zone.lower,
        upper: zone.upper,
        created_at: ts,
        last_test_at: ts,
        touch_count: 1,
        hold_count: 1,
        break_count: 0,
        trade_confirmations: 1,
        seeded_confidence: 0.0,
        seeded_strength: 0.0,
        in_contact: false,
        lifecycle: ZoneLifecycle::Active,
    }
}

fn insert_role_reversed_zone(zones: &mut Vec<Zone>, reversed: Zone, threshold: f64) {
    if let Some(existing) = zones
        .iter_mut()
        .filter(|zone| zone.is_active())
        .min_by(|left, right| {
            (left.price - reversed.price)
                .abs()
                .total_cmp(&(right.price - reversed.price).abs())
        })
        .filter(|zone| (zone.price - reversed.price).abs() <= threshold * 0.75)
    {
        let existing_weight = existing.touch_count.max(1) as f64;
        existing.price =
            (existing.price * existing_weight + reversed.price) / (existing_weight + 1.0);
        existing.lower = existing.lower.min(reversed.lower);
        existing.upper = existing.upper.max(reversed.upper);
        existing.touch_count = existing.touch_count.saturating_add(reversed.touch_count);
        existing.hold_count = existing.hold_count.saturating_add(reversed.hold_count);
        existing.trade_confirmations = existing
            .trade_confirmations
            .saturating_add(reversed.trade_confirmations);
        existing.last_test_at = existing.last_test_at.max(reversed.last_test_at);
        return;
    }
    zones.push(reversed);
}

fn prune_zones(zones: &mut Vec<Zone>) {
    while zones.len() > MAX_ZONES_PER_SIDE {
        let remove_index = zones
            .iter()
            .enumerate()
            .min_by_key(|(_, zone)| (zone_retention_rank(zone), zone.last_test_at))
            .map(|(index, _)| index)
            .unwrap_or_default();
        zones.remove(remove_index);
    }
}

fn zone_retention_rank(zone: &Zone) -> u8 {
    if matches!(zone.lifecycle, ZoneLifecycle::Retired) {
        0
    } else if zone.is_pending_reversal() {
        1
    } else {
        2
    }
}

fn add_or_merge_zone(zones: &mut Vec<Zone>, side: i8, pivot: &Pivot, threshold: f64) -> Zone {
    let half_width = (threshold * 0.30).max(price_tick(pivot.price));
    if let Some(zone) = zones
        .iter_mut()
        .filter(|zone| zone.is_active())
        .min_by(|left, right| {
            (left.price - pivot.price)
                .abs()
                .total_cmp(&(right.price - pivot.price).abs())
        })
        .filter(|zone| (zone.price - pivot.price).abs() <= threshold * 0.75)
    {
        let prior_weight = zone.touch_count.max(1) as f64;
        zone.price = (zone.price * prior_weight + pivot.price) / (prior_weight + 1.0);
        zone.lower = zone.lower.min(pivot.price - half_width);
        zone.upper = zone.upper.max(pivot.price + half_width);
        zone.touch_count = zone.touch_count.saturating_add(1);
        zone.last_test_at = pivot.confirmed_at;
        return zone.clone();
    }
    let zone = Zone {
        side,
        price: pivot.price,
        lower: pivot.price - half_width,
        upper: pivot.price + half_width,
        created_at: pivot.confirmed_at,
        last_test_at: pivot.confirmed_at,
        touch_count: 1,
        hold_count: 0,
        break_count: 0,
        trade_confirmations: 0,
        seeded_confidence: 0.0,
        seeded_strength: 0.0,
        in_contact: false,
        lifecycle: ZoneLifecycle::Active,
    };
    zones.push(zone.clone());
    prune_zones(zones);
    zone
}

fn scale_snapshot(
    state: &ScaleState,
    config: ScaleConfig,
    reference: f64,
    base_threshold: f64,
    now: DateTime<Utc>,
) -> StructureScaleSnapshot {
    let threshold = base_threshold * config.threshold_multiplier;
    StructureScaleSnapshot {
        direction: state.trend_direction,
        threshold,
        swing_high: state
            .swing_high
            .as_ref()
            .map(|pivot| pivot.price)
            .unwrap_or_default(),
        swing_low: state
            .swing_low
            .as_ref()
            .map(|pivot| pivot.price)
            .unwrap_or_default(),
        support: select_level(
            &state.support_zones,
            true,
            reference,
            threshold,
            config,
            now,
        ),
        resistance: select_level(
            &state.resistance_zones,
            false,
            reference,
            threshold,
            config,
            now,
        ),
    }
}

fn select_level(
    zones: &[Zone],
    support: bool,
    reference: f64,
    threshold: f64,
    config: ScaleConfig,
    now: DateTime<Utc>,
) -> StructureLevelSnapshot {
    zones
        .iter()
        .filter(|zone| zone.is_active())
        .filter(|zone| (zone.side > 0) == support)
        .filter(|zone| zone_is_on_expected_side(zone, reference))
        .map(|zone| {
            let snapshot = zone_snapshot(zone, config, now);
            let distance = zone_distance(zone, reference) / threshold.max(price_tick(reference));
            let score = snapshot.strength * snapshot.confidence / (1.0 + 0.12 * distance);
            (score, snapshot)
        })
        .max_by(|left, right| left.0.total_cmp(&right.0))
        .map(|(_, snapshot)| snapshot)
        .unwrap_or_default()
}

fn zone_is_on_expected_side(zone: &Zone, reference: f64) -> bool {
    if zone.side > 0 {
        zone.upper < reference
    } else {
        zone.lower > reference
    }
}

fn zone_distance(zone: &Zone, reference: f64) -> f64 {
    if zone.side > 0 {
        (reference - zone.upper).max(0.0)
    } else {
        (zone.lower - reference).max(0.0)
    }
}

fn zone_snapshot(zone: &Zone, config: ScaleConfig, now: DateTime<Utc>) -> StructureLevelSnapshot {
    let age_seconds = (now - zone.last_test_at).num_seconds().max(0) as f64;
    let decay = 0.5_f64.powf(age_seconds / config.retention_half_life_seconds.max(1.0));
    let evidence = 0.18
        + zone.touch_count.min(6) as f64 * 0.10
        + zone.hold_count.min(4) as f64 * 0.13
        + zone.trade_confirmations.min(3) as f64 * 0.08
        - zone.break_count.min(3) as f64 * 0.20;
    let strength = (evidence * decay)
        .max(zone.seeded_strength * decay)
        .clamp(0.0, 1.0);
    let confidence = (((zone.touch_count as f64 + 1.5 * zone.hold_count as f64) / 7.0)
        .sqrt()
        .clamp(0.0, 1.0)
        .max(zone.seeded_confidence)
        * (0.65 + 0.35 * decay))
        .clamp(0.0, 1.0);
    StructureLevelSnapshot {
        price: zone.price,
        lower: zone.lower,
        upper: zone.upper,
        strength,
        confidence,
        touch_count: zone.touch_count,
        hold_count: zone.hold_count,
        created_at_ms: zone.created_at.timestamp_millis(),
        last_test_at_ms: zone.last_test_at.timestamp_millis(),
    }
}

fn select_unified_level(
    snapshots: &[StructureScaleSnapshot],
    support: bool,
    reference: f64,
) -> StructureLevelSnapshot {
    snapshots
        .iter()
        .zip(SCALE_CONFIGS)
        .map(|(snapshot, config)| {
            let level = if support {
                &snapshot.support
            } else {
                &snapshot.resistance
            };
            let distance = if support {
                (reference - level.upper).max(0.0)
            } else {
                (level.lower - reference).max(0.0)
            } / snapshot.threshold.max(price_tick(reference));
            let score = level.strength * level.confidence * config.weight / (1.0 + 0.08 * distance);
            (score, level)
        })
        .filter(|(_, level)| {
            level.price > 0.0
                && if support {
                    level.upper < reference
                } else {
                    level.lower > reference
                }
        })
        .max_by(|left, right| left.0.total_cmp(&right.0))
        .map(|(_, level)| level.clone())
        .unwrap_or_default()
}

fn structure_event(
    sym: &str,
    scale: &str,
    kind: &str,
    direction: i8,
    pivot: &Pivot,
    zone: &Zone,
) -> GenericStructureEvent {
    let (strength, confidence) = event_evidence(zone);
    GenericStructureEvent {
        algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
        event_id: stable_event_id(sym, scale, kind, direction, pivot.price, pivot.confirmed_at),
        sym: sym.to_string(),
        scale: scale.to_string(),
        event_kind: kind.to_string(),
        direction,
        price: pivot.price,
        lower: zone.lower,
        upper: zone.upper,
        strength,
        confidence,
        pivot_at: pivot.pivot_at,
        confirmed_at: pivot.confirmed_at,
    }
}

fn event_evidence(zone: &Zone) -> (f64, f64) {
    let strength = (0.18
        + zone.touch_count.min(6) as f64 * 0.10
        + zone.hold_count.min(4) as f64 * 0.13
        + zone.trade_confirmations.min(3) as f64 * 0.08
        - zone.break_count.min(3) as f64 * 0.20)
        .clamp(0.0, 1.0);
    let confidence = ((zone.touch_count as f64 + 1.5 * zone.hold_count as f64) / 7.0)
        .sqrt()
        .clamp(0.0, 1.0);
    (strength, confidence)
}

fn stable_event_id(
    sym: &str,
    scale: &str,
    kind: &str,
    direction: i8,
    price: f64,
    ts: DateTime<Utc>,
) -> u64 {
    let payload = format!(
        "{sym}|{scale}|{kind}|{direction}|{}|{}",
        price_key(price),
        ts.timestamp_micros()
    );
    payload
        .as_bytes()
        .iter()
        .fold(0xcbf29ce484222325_u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(0x100000001b3)
        })
}

fn update_high_low(high: &mut f64, low: &mut f64, price: f64) {
    *high = if *high > 0.0 {
        (*high).max(price)
    } else {
        price
    };
    *low = if *low > 0.0 { (*low).min(price) } else { price };
}

fn market_session_anchor_date(ts: DateTime<Utc>) -> NaiveDate {
    let local = ts.with_timezone(&New_York);
    if local.time().num_seconds_from_midnight() < SESSION_ANCHOR_SECONDS {
        local.date_naive().pred_opt().unwrap_or(local.date_naive())
    } else {
        local.date_naive()
    }
}

fn price_tick(price: f64) -> f64 {
    if price >= 1.0 {
        0.01
    } else {
        0.0001
    }
}

fn price_key(price: f64) -> i64 {
    (price * 10_000.0).round() as i64
}

fn price_from_key(key: i64) -> f64 {
    key as f64 / 10_000.0
}

fn nearest_round_price(price: f64) -> f64 {
    if price <= 0.0 {
        return 0.0;
    }
    let interval = if price >= 100.0 {
        1.0
    } else if price >= 10.0 {
        0.5
    } else if price >= 1.0 {
        0.10
    } else {
        0.01
    };
    (price / interval).round() * interval
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::{QuoteEvent, TradeEvent};
    use chrono::TimeZone;
    use serde_json::Value;

    fn quote(ts_ms: i64, bid: f64, ask: f64, sequence: u64) -> MarketEvent {
        MarketEvent::Quote(QuoteEvent {
            ask_exchange: 1,
            ask_price: ask,
            ask_size: 100,
            bid_exchange: 1,
            bid_price: bid,
            bid_size: 100,
            conditions: Vec::new(),
            indicators: Vec::new(),
            ingest_ts: Utc.timestamp_millis_opt(ts_ms).unwrap(),
            raw: Value::Null,
            sequence,
            tape: 3,
            ticker: "TEST".to_string(),
            ts: Utc.timestamp_millis_opt(ts_ms).unwrap(),
        })
    }

    fn trade(ts_ms: i64, price: f64, size: f64, sequence: u64) -> MarketEvent {
        MarketEvent::Trade(TradeEvent {
            conditions: Vec::new(),
            exchange: 1,
            ingest_ts: Utc.timestamp_millis_opt(ts_ms).unwrap(),
            participant_ts: None,
            price,
            raw: Value::Null,
            sequence,
            size,
            tape: 3,
            ticker: "TEST".to_string(),
            trade_id: sequence.to_string(),
            trf_id: 0,
            trf_ts: None,
            ts: Utc.timestamp_millis_opt(ts_ms).unwrap(),
        })
    }

    fn zone_at(side: i8, price: f64, now: DateTime<Utc>) -> Zone {
        Zone {
            side,
            price,
            lower: price - 0.10,
            upper: price + 0.10,
            created_at: now,
            last_test_at: now,
            touch_count: 1,
            hold_count: 0,
            break_count: 0,
            trade_confirmations: 0,
            seeded_confidence: 0.0,
            seeded_strength: 0.0,
            in_contact: false,
            lifecycle: ZoneLifecycle::Active,
        }
    }

    fn evidenced_zone(side: i8, price: f64, now: DateTime<Utc>) -> Zone {
        Zone {
            touch_count: 6,
            hold_count: 4,
            trade_confirmations: 3,
            ..zone_at(side, price, now)
        }
    }

    #[test]
    fn structural_pressure_is_directional_and_shrinks_unbalanced_evidence() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let support_states = vec![
            ScaleState {
                support_zones: vec![evidenced_zone(1, 99.8, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let bullish = structural_pressure_field(&support_states, 100.0, 0.05, now);
        assert!(bullish.support > bullish.resistance);
        assert!(bullish.bias > 0.0);
        assert!(bullish.confidence > 0.0);
        assert!(bullish.up_probability > 0.5);

        let resistance_states = vec![
            ScaleState {
                resistance_zones: vec![evidenced_zone(-1, 100.2, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let bearish = structural_pressure_field(&resistance_states, 100.0, 0.05, now);
        assert!(bearish.resistance > bearish.support);
        assert!(bearish.bias < 0.0);
        assert!(bearish.up_probability < 0.5);
    }

    #[test]
    fn structural_pressure_treats_balanced_fields_as_uncertain() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let states = vec![
            ScaleState {
                support_zones: vec![evidenced_zone(1, 99.8, now)],
                resistance_zones: vec![evidenced_zone(-1, 100.2, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let pressure = structural_pressure_field(&states, 100.0, 0.05, now);
        assert!(pressure.support > 0.0 && pressure.resistance > 0.0);
        assert!(pressure.bias.abs() < 0.05);
        assert!(pressure.confidence < 0.05);
        assert!((pressure.up_probability - 0.5).abs() < 0.01);
    }

    #[test]
    fn structural_pressure_distance_decay_and_cluster_discount_prevent_double_counting() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let near = vec![
            ScaleState {
                support_zones: vec![evidenced_zone(1, 99.8, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let far = vec![
            ScaleState {
                support_zones: vec![evidenced_zone(1, 98.0, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let duplicate = vec![
            ScaleState {
                support_zones: vec![evidenced_zone(1, 99.80, now), evidenced_zone(1, 99.82, now)],
                ..ScaleState::default()
            },
            ScaleState::default(),
            ScaleState::default(),
        ];
        let near_score = structural_pressure_field(&near, 100.0, 0.05, now).support;
        let far_score = structural_pressure_field(&far, 100.0, 0.05, now).support;
        let duplicate_score = structural_pressure_field(&duplicate, 100.0, 0.05, now).support;
        assert!(near_score > far_score);
        assert!(duplicate_score > near_score);
        assert!(duplicate_score < near_score + near_score * 0.5);
    }

    #[test]
    fn active_snapshot_never_labels_crossed_or_in_play_zones_as_support_or_resistance() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let mut engine = GenericStructureEngine::new("TEST");
        engine.last_reference_price = 100.0;
        engine.spread_ewma.value = 0.01;
        engine.scales[0].support_zones.push(zone_at(1, 100.20, now));
        engine.scales[0].support_zones.push(zone_at(1, 100.00, now));
        engine.scales[0]
            .resistance_zones
            .push(zone_at(-1, 99.80, now));
        engine.scales[0]
            .resistance_zones
            .push(zone_at(-1, 100.00, now));

        let snapshot = engine.snapshot(now);
        assert_eq!(snapshot.micro.support.price, 0.0);
        assert_eq!(snapshot.micro.resistance.price, 0.0);
        assert!(snapshot.active_levels.is_empty());
    }

    #[test]
    fn broken_support_only_becomes_resistance_after_confirmed_retest_rejection() {
        let start = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let mut state = ScaleState {
            support_zones: vec![zone_at(1, 100.0, start)],
            ..ScaleState::default()
        };
        let config = SCALE_CONFIGS[0];
        let mut emitted = Vec::new();

        update_zones(
            &mut state,
            config,
            99.70,
            0.0,
            0.0,
            0.10,
            start + chrono::Duration::milliseconds(10),
            &mut emitted,
            "TEST",
        );
        update_zones(
            &mut state,
            config,
            99.70,
            99.70,
            100.0,
            0.10,
            start + chrono::Duration::milliseconds(120),
            &mut emitted,
            "TEST",
        );
        assert!(matches!(
            state.support_zones[0].lifecycle,
            ZoneLifecycle::AwaitingRetest
        ));
        assert!(state.resistance_zones.is_empty());
        assert_eq!(
            emitted
                .iter()
                .filter(|event| event.event_kind == "level_break")
                .count(),
            1
        );

        update_zones(
            &mut state,
            config,
            100.0,
            0.0,
            0.0,
            0.10,
            start + chrono::Duration::milliseconds(200),
            &mut emitted,
            "TEST",
        );
        update_zones(
            &mut state,
            config,
            99.70,
            0.0,
            0.0,
            0.10,
            start + chrono::Duration::milliseconds(220),
            &mut emitted,
            "TEST",
        );
        assert!(state.resistance_zones.is_empty());

        update_zones(
            &mut state,
            config,
            99.70,
            99.70,
            100.0,
            0.10,
            start + chrono::Duration::milliseconds(340),
            &mut emitted,
            "TEST",
        );
        assert!(matches!(
            state.support_zones[0].lifecycle,
            ZoneLifecycle::Retired
        ));
        assert_eq!(state.resistance_zones.len(), 1);
        assert_eq!(state.resistance_zones[0].side, -1);
        assert!(state.resistance_zones[0].is_active());
        assert_eq!(
            emitted
                .iter()
                .filter(|event| event.event_kind == "role_reversal")
                .count(),
            1
        );

        let snapshot = scale_snapshot(&state, config, 99.70, 0.10, start);
        assert_eq!(snapshot.support.price, 0.0);
        assert_eq!(snapshot.resistance.price, 100.0);
    }

    #[test]
    fn durable_level_break_and_role_reversal_events_restore_the_flipped_side() {
        let start = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let event = |kind: &str, direction: i8, millis: i64, event_id: u64| GenericStructureEvent {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            event_id,
            sym: "TEST".to_string(),
            scale: "micro".to_string(),
            event_kind: kind.to_string(),
            direction,
            price: 100.0,
            lower: 99.90,
            upper: 100.10,
            strength: 0.50,
            confidence: 0.50,
            pivot_at: start,
            confirmed_at: start + chrono::Duration::milliseconds(millis),
        };
        let mut engine = GenericStructureEngine::new("TEST");
        engine.last_reference_price = 99.70;
        engine.seed_events(&[
            event("pivot_low", 1, 0, 1),
            event("level_break", -1, 100, 2),
            event("role_reversal", -1, 300, 3),
        ]);

        let snapshot = engine.snapshot(start + chrono::Duration::milliseconds(400));
        assert_eq!(snapshot.micro.support.price, 0.0);
        assert_eq!(snapshot.micro.resistance.price, 100.0);
        assert!(matches!(
            engine.scales[0].support_zones[0].lifecycle,
            ZoneLifecycle::Retired
        ));
        assert!(snapshot
            .active_levels
            .iter()
            .all(|level| level.side < 0 && level.lower > snapshot.reference_price));
    }

    #[test]
    fn pivot_is_only_published_after_a_causal_reversal() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 20, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        engine.apply_event(
            &quote(start, 99.995, 100.005, 0),
            TradeUpdateRule::regular(),
        );
        for (index, price) in [100.00, 100.02, 100.04, 100.06].into_iter().enumerate() {
            let event = trade(
                start + index as i64 * 100 + 10,
                price,
                100.0,
                index as u64 + 1,
            );
            let (_, emitted) = engine.apply_event(&event, TradeUpdateRule::regular());
            assert!(emitted.iter().all(|item| item.event_kind != "pivot_high"));
        }
        let event = trade(start + 500, 99.98, 100.0, 10);
        let (_, emitted) = engine.apply_event(&event, TradeUpdateRule::regular());
        assert!(emitted.iter().any(|item| item.event_kind == "pivot_high"));
        assert!(emitted
            .iter()
            .all(|item| item.confirmed_at >= item.pivot_at));
    }

    #[test]
    fn quote_only_moves_cannot_create_price_swings() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 20, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, mid) in [100.0, 101.0, 99.0, 102.0].into_iter().enumerate() {
            let (_, emitted) = engine.apply_event(
                &quote(
                    start + index as i64 * 100,
                    mid - 0.01,
                    mid + 0.01,
                    index as u64,
                ),
                TradeUpdateRule::regular(),
            );
            assert!(emitted
                .iter()
                .all(|event| !event.event_kind.starts_with("pivot_")));
        }
        assert_eq!(
            engine
                .snapshot(Utc.timestamp_millis_opt(start + 500).unwrap())
                .micro
                .swing_high,
            0.0
        );
    }

    #[test]
    fn adaptive_micro_threshold_is_robust_to_transient_wide_quotes() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.spread_ewma.value = 5.0;
        engine.move_ewma.value = 5.0;
        let threshold = engine.base_threshold(44.0);
        assert!((threshold - 0.11).abs() < 1e-9);
    }

    #[test]
    fn session_volume_poc_uses_eligible_trade_prices() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 20, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        engine.apply_event(&quote(start, 99.99, 100.01, 1), TradeUpdateRule::regular());
        engine.apply_event(
            &trade(start + 10, 100.00, 100.0, 2),
            TradeUpdateRule::regular(),
        );
        let (snapshot, _) = engine.apply_event(
            &trade(start + 20, 100.01, 500.0, 3),
            TradeUpdateRule::regular(),
        );
        assert_eq!(snapshot.trade_volume_poc, 100.01);
    }

    #[test]
    fn out_of_order_event_cannot_rewrite_structure() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 20, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        engine.apply_event(
            &quote(start + 100, 99.99, 100.01, 2),
            TradeUpdateRule::regular(),
        );
        let before = engine.snapshot(Utc.timestamp_millis_opt(start + 100).unwrap());
        let (after, emitted) =
            engine.apply_event(&quote(start, 90.0, 90.02, 1), TradeUpdateRule::regular());
        assert!(emitted.is_empty());
        assert_eq!(after.reference_price, before.reference_price);
    }

    #[test]
    fn accepted_break_emits_once_for_the_same_causal_pivot() {
        let start = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let pivot = Pivot {
            price: 100.0,
            pivot_at: start,
            confirmed_at: start + chrono::Duration::milliseconds(10),
        };
        let mut state = ScaleState {
            trend_direction: 1,
            swing_high: Some(pivot.clone()),
            resistance_zones: vec![Zone {
                side: -1,
                price: 100.0,
                lower: 99.99,
                upper: 100.01,
                created_at: pivot.confirmed_at,
                last_test_at: pivot.confirmed_at,
                touch_count: 2,
                hold_count: 1,
                break_count: 0,
                trade_confirmations: 0,
                seeded_confidence: 0.0,
                seeded_strength: 0.0,
                in_contact: false,
                lifecycle: ZoneLifecycle::Active,
            }],
            ..ScaleState::default()
        };
        let mut emitted = Vec::new();
        for millis in [20, 80, 160, 260] {
            update_structure_break(
                &mut state,
                SCALE_CONFIGS[0],
                100.05,
                100.05,
                25.0,
                0.02,
                start + chrono::Duration::milliseconds(millis),
                &mut emitted,
                "TEST",
            );
        }
        assert_eq!(
            emitted
                .iter()
                .filter(|event| event.event_kind == "bos")
                .count(),
            1
        );
        assert!(emitted[0].confirmed_at >= emitted[0].pivot_at);
    }

    #[test]
    fn opposing_scale_directions_report_low_not_false_high_agreement() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.last_reference_price = 100.0;
        engine.scales[1].trend_direction = 1;
        engine.scales[2].trend_direction = -1;
        let snapshot = engine.snapshot(Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap());
        assert_eq!(snapshot.direction, 0);
        assert!(snapshot.agreement < 0.2);
    }

    #[test]
    fn active_level_payload_keeps_nearest_candidates_and_distant_strongest() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let mut engine = GenericStructureEngine::new("TEST");
        engine.last_reference_price = 100.0;
        engine.spread_ewma.value = 0.01;
        for index in 0..10 {
            engine.scales[0].support_zones.push(Zone {
                side: 1,
                price: 99.9 - index as f64 * 0.1,
                lower: 99.89 - index as f64 * 0.1,
                upper: 99.91 - index as f64 * 0.1,
                created_at: now,
                last_test_at: now,
                touch_count: if index == 9 { 6 } else { 1 },
                hold_count: if index == 9 { 4 } else { 0 },
                break_count: 0,
                trade_confirmations: if index == 9 { 3 } else { 0 },
                seeded_confidence: 0.0,
                seeded_strength: 0.0,
                in_contact: false,
                lifecycle: ZoneLifecycle::Active,
            });
        }
        let snapshot = engine.snapshot(now);
        let supports = snapshot
            .active_levels
            .iter()
            .filter(|level| level.side > 0)
            .collect::<Vec<_>>();
        assert_eq!(supports.len(), MAX_EXPOSED_LEVELS_PER_SIDE + 1);
        assert!(supports
            .iter()
            .any(|level| (level.price - 99.9).abs() < 1e-9));
        assert!(supports
            .iter()
            .any(|level| (level.price - 99.0).abs() < 1e-9));
    }

    #[test]
    fn compact_snapshot_restores_strategy_visible_structure() {
        let now = Utc.with_ymd_and_hms(2026, 7, 20, 13, 30, 0).unwrap();
        let mut source = GenericStructureEngine::new("TEST");
        source.last_reference_price = 100.0;
        source.scales[2].trend_direction = 1;
        source.scales[2].support_zones.push(Zone {
            side: 1,
            price: 99.5,
            lower: 99.45,
            upper: 99.55,
            created_at: now - chrono::Duration::days(1),
            last_test_at: now - chrono::Duration::hours(1),
            touch_count: 3,
            hold_count: 2,
            break_count: 0,
            trade_confirmations: 1,
            seeded_confidence: 0.0,
            seeded_strength: 0.0,
            in_contact: false,
            lifecycle: ZoneLifecycle::Active,
        });
        let checkpoint = source.snapshot(now);
        let mut restored = GenericStructureEngine::new("TEST");
        restored.seed_snapshot(&checkpoint);
        let snapshot = restored.snapshot(now);
        assert_eq!(snapshot.context.direction, 1);
        assert_eq!(snapshot.context.support.price, 99.5);
        assert!(snapshot.context.support.strength > 0.0);
        assert!(snapshot.context.support.confidence > 0.0);
    }

    #[test]
    fn compact_checkpoint_restores_exact_continuation_state() {
        let start = Utc
            .with_ymd_and_hms(2026, 7, 20, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let mut source = GenericStructureEngine::new("TEST");
        source.apply_event(&quote(start, 99.99, 100.01, 1), TradeUpdateRule::regular());
        source.apply_event(
            &trade(start + 10, 100.00, 1_000.0, 2),
            TradeUpdateRule::regular(),
        );
        source.apply_event(
            &trade(start + 20, 100.01, 900.0, 3),
            TradeUpdateRule::regular(),
        );
        let serialized = serde_json::to_string(&source.checkpoint()).unwrap();
        let checkpoint = serde_json::from_str::<GenericStructureCheckpoint>(&serialized).unwrap();
        let mut restored = GenericStructureEngine::new("TEST");
        restored.seed_checkpoint(&checkpoint);

        let next = trade(start + 30, 100.02, 100.0, 4);
        let (expected, expected_events) = source.apply_event(&next, TradeUpdateRule::regular());
        let (actual, actual_events) = restored.apply_event(&next, TradeUpdateRule::regular());

        assert_eq!(actual.trade_volume_poc, 100.00);
        assert_eq!(actual.trade_volume_poc, expected.trade_volume_poc);
        assert_eq!(actual.reference_price, expected.reference_price);
        assert_eq!(actual.micro.threshold, expected.micro.threshold);
        assert_eq!(actual.last_event_id, expected.last_event_id);
        assert_eq!(actual_events.len(), expected_events.len());
    }
}
