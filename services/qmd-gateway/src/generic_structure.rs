use crate::bars::TradeUpdateRule;
use crate::event::MarketEvent;
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap, VecDeque};

pub const GENERIC_STRUCTURE_ALGORITHM_VERSION: u16 = 16;
pub const STRUCTURE_HOLD_SCORE_REVISION: &str = "beta22-wilson90-v1";
const HOLD_SCORE_Z: f64 = 1.281_551_565_544_600_4;
const HOLD_RELIABILITY_HALF_LIFE: f64 = 8.0;
pub const STRUCTURE_TIMEFRAMES: [(&str, i64); 10] = [
    ("100ms", 100),
    ("1s", 1_000),
    ("5s", 5_000),
    ("10s", 10_000),
    ("30s", 30_000),
    ("1m", 60_000),
    ("5m", 300_000),
    ("1h", 3_600_000),
    ("1d", 86_400_000),
    ("1w", 604_800_000),
];

const SESSION_ANCHOR_SECONDS: u32 = 4 * 60 * 60;
const SESSION_END_SECONDS: u32 = 20 * 60 * 60;
const REGULAR_OPEN_SECONDS: u32 = 9 * 60 * 60 + 30 * 60;
const OPENING_RANGE_END_SECONDS: u32 = 9 * 60 * 60 + 35 * 60;
const FOOTPRINT_RADIUS_TICKS: i32 = 4;
const MAX_LEVELS: usize = 512;
const MAX_EXPOSED_LEVELS_PER_SIDE: usize = 8;
const MAX_UNIFIED_LEVELS_PER_SIDE: usize = 16;
const MAX_UNIFIED_BOOK_CANDIDATES_PER_SIDE: usize = MAX_UNIFIED_LEVELS_PER_SIDE * 2;
const MAX_UNIFIED_TRACKS: usize = 256;
const MAX_UNIFIED_SOURCES_PER_TRACK: usize = 16;

#[derive(Clone, Debug, Deserialize, PartialEq, Serialize)]
pub struct StructureSplitAdjustment {
    pub execution_date: NaiveDate,
    pub effective_at: DateTime<Utc>,
    pub split_from: f64,
    pub split_to: f64,
    pub source_inserted_at: DateTime<Utc>,
}

impl StructureSplitAdjustment {
    fn identity(&self) -> (NaiveDate, i64, i64) {
        (
            self.execution_date,
            (self.split_from * 1_000_000_000.0).round() as i64,
            (self.split_to * 1_000_000_000.0).round() as i64,
        )
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureFootprintBin {
    pub offset_ticks: i32,
    pub price: f64,
    pub total_volume: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub neutral_volume: f64,
    pub trade_count: u64,
    pub largest_trade: f64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructurePromotionSnapshot {
    pub timeframe: String,
    pub promoted_at_ms: i64,
    pub score: f64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureLevelSnapshot {
    pub level_id: u64,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub strength: f64,
    pub confidence: f64,
    pub touch_count: u32,
    pub hold_count: u32,
    pub created_at_ms: i64,
    pub last_test_at_ms: i64,
    pub lifecycle: String,
    pub promotions: Vec<StructurePromotionSnapshot>,
    pub footprint: Vec<StructureFootprintBin>,
    pub total_volume: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub neutral_volume: f64,
    pub trade_count: u64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureLevelCandidate {
    pub level_id: u64,
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
    pub lifecycle: String,
    pub promotions: Vec<StructurePromotionSnapshot>,
    /// Extended-hours market session whose cumulative executed volume is
    /// represented by `footprint` (04:00-20:00 America/New_York).
    #[serde(default)]
    pub footprint_session_date: String,
    /// Event time of this cumulative footprint snapshot. Consumers must retain
    /// the newest complete snapshot rather than combining individual fields.
    #[serde(default)]
    pub footprint_as_of_ms: i64,
    pub footprint: Vec<StructureFootprintBin>,
    pub total_volume: f64,
    pub buy_volume: f64,
    pub sell_volume: f64,
    pub neutral_volume: f64,
    pub trade_count: u64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct StructureTimeframeSnapshot {
    pub timeframe: String,
    pub direction: i8,
    pub swing_high: f64,
    pub swing_low: f64,
    pub support: StructureLevelSnapshot,
    pub resistance: StructureLevelSnapshot,
    pub promoted_level_count: usize,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct UnifiedStructureSource {
    pub level_id: u64,
    pub timeframe: String,
    pub side: i8,
    pub price: f64,
    pub pivot_at_ms: i64,
    pub confirmed_at_ms: i64,
    pub total_volume: f64,
    #[serde(default)]
    pub buy_volume: f64,
    #[serde(default)]
    pub sell_volume: f64,
    #[serde(default)]
    pub neutral_volume: f64,
    pub trade_count: u64,
    #[serde(default)]
    pub source_kind: String,
    #[serde(default)]
    pub touch_count: u32,
    #[serde(default)]
    pub hold_count: u32,
    #[serde(default)]
    pub break_count: u32,
    #[serde(default)]
    pub role_flip_count: u32,
    #[serde(default)]
    pub last_test_at_ms: i64,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct UnifiedStructureLevel {
    pub unified_level_id: u64,
    pub side: i8,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub source_count: usize,
    pub independent_pivot_count: usize,
    pub timeframes: Vec<String>,
    pub created_at_ms: i64,
    pub confirmed_at_ms: i64,
    pub total_volume: f64,
    #[serde(default)]
    pub buy_volume: f64,
    #[serde(default)]
    pub sell_volume: f64,
    #[serde(default)]
    pub neutral_volume: f64,
    pub trade_count: u64,
    /// Signed executed-volume pressure in [-1, 1]. Positive values indicate
    /// more buyer-initiated volume around the level; negative values indicate
    /// more seller-initiated volume. It is evidence, not a return forecast.
    #[serde(default)]
    pub pressure_bias: f64,
    /// Beta-smoothed evidence that the level holds in its current role.
    #[serde(default)]
    pub hold_probability: f64,
    /// Beta-smoothed complement of hold probability. This describes observed
    /// level failure frequency and is not a forecast of the next price move.
    #[serde(default)]
    pub break_probability: f64,
    /// Raw observed hold frequency. This remains zero until at least one
    /// hold/accepted-break outcome exists.
    #[serde(default)]
    pub hold_rate: f64,
    /// Number of causal hold/accepted-break outcomes supporting the score.
    #[serde(default)]
    pub hold_observation_count: u32,
    /// Evidence depth in [0, 1], with eight outcomes representing half of the
    /// asymptotic reliability. This is not directional alpha.
    #[serde(default)]
    pub hold_evidence_reliability: f64,
    /// Conservative one-sided 90% Wilson lower bound over the Beta(2, 2)
    /// posterior pseudo-observations. Use this comparable score for ranking;
    /// do not interpret it as an empirically calibrated return probability.
    #[serde(default)]
    pub hold_quality_score: f64,
    /// Exact deterministic scoring contract used for the derived fields.
    #[serde(default)]
    pub hold_score_revision: String,
    #[serde(default)]
    pub touch_count: u32,
    #[serde(default)]
    pub hold_count: u32,
    #[serde(default)]
    pub break_count: u32,
    #[serde(default)]
    pub role_flip_count: u32,
    #[serde(default)]
    pub last_test_at_ms: i64,
    /// Current causal lifecycle of this role episode. Awaiting-retest and
    /// retest-contact levels remain published so a break cannot make an
    /// important range disappear while its role flip is being evaluated.
    #[serde(default = "default_level_lifecycle_label")]
    pub lifecycle: String,
    /// Candidate role after an accepted break. Zero means no pending role;
    /// +1 is support and -1 is resistance.
    #[serde(default)]
    pub pending_side: i8,
    pub sources: Vec<UnifiedStructureSource>,
}

fn default_level_lifecycle_label() -> String {
    "active".to_string()
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct GenericStructureSnapshot {
    pub algorithm_version: u16,
    pub reference_price: f64,
    pub direction: i8,
    pub agreement: f64,
    pub strength: f64,
    pub confidence: f64,
    pub support_field: f64,
    pub resistance_field: f64,
    pub pressure_bias: f64,
    pub pressure_confidence: f64,
    #[serde(default = "default_up_probability")]
    pub up_probability: f64,
    pub support: StructureLevelSnapshot,
    pub resistance: StructureLevelSnapshot,
    #[serde(default)]
    pub active_levels: Vec<StructureLevelCandidate>,
    #[serde(default)]
    pub timeframe_states: Vec<StructureTimeframeSnapshot>,
    #[serde(default)]
    pub unified_levels: Vec<UnifiedStructureLevel>,
    pub developing_high: f64,
    pub developing_low: f64,
    pub developing_direction: i8,
    pub last_event_id: u64,
    pub last_event_pivot_at_ms: i64,
    pub last_event_at_ms: i64,
    pub last_event_kind: String,
    pub last_event_timeframe: String,
    pub last_event_direction: i8,
    pub last_event_price: f64,
    pub session_high: f64,
    pub session_low: f64,
    pub opening_range_high: f64,
    pub opening_range_low: f64,
    pub trade_volume_poc: f64,
    pub nearest_round: f64,
}

fn default_up_probability() -> f64 {
    0.5
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GenericStructureEvent {
    pub algorithm_version: u16,
    pub event_id: u64,
    #[serde(default)]
    pub level_id: u64,
    pub sym: String,
    #[serde(default, alias = "scale")]
    pub timeframe: String,
    pub event_kind: String,
    pub direction: i8,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub strength: f64,
    pub confidence: f64,
    #[serde(default)]
    pub lifecycle: String,
    #[serde(default)]
    pub total_volume: f64,
    #[serde(default)]
    pub buy_volume: f64,
    #[serde(default)]
    pub sell_volume: f64,
    #[serde(default)]
    pub neutral_volume: f64,
    #[serde(default)]
    pub trade_count: u64,
    pub pivot_at: DateTime<Utc>,
    pub confirmed_at: DateTime<Utc>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct FootprintBin {
    total_volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    neutral_volume: f64,
    trade_count: u64,
    largest_trade: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct Promotion {
    timeframe: String,
    promoted_at: DateTime<Utc>,
    score: f64,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
enum LevelLifecycle {
    Active,
    Crossed {
        direction: i8,
        first_crossed_at: DateTime<Utc>,
        beyond_trades: u32,
        beyond_volume: f64,
    },
    AwaitingRetest {
        direction: i8,
        accepted_at: DateTime<Utc>,
    },
    RetestContact {
        direction: i8,
        contacted_at: DateTime<Utc>,
    },
    Retired,
}

impl Default for LevelLifecycle {
    fn default() -> Self {
        Self::Active
    }
}

impl LevelLifecycle {
    fn label(&self) -> &'static str {
        match self {
            Self::Active => "active",
            Self::Crossed { .. } => "crossed",
            Self::AwaitingRetest { .. } => "awaiting_retest",
            Self::RetestContact { .. } => "retest_contact",
            Self::Retired => "retired",
        }
    }

    fn visible(&self) -> bool {
        !matches!(self, Self::Retired)
    }
}

#[derive(Clone, Debug, Deserialize, Serialize)]
struct StructureLevel {
    level_id: u64,
    side: i8,
    price: f64,
    lower: f64,
    upper: f64,
    pivot_at: DateTime<Utc>,
    confirmed_at: DateTime<Utc>,
    last_test_at: DateTime<Utc>,
    touch_count: u32,
    hold_count: u32,
    break_count: u32,
    #[serde(default)]
    accepted_break_count: u32,
    #[serde(default)]
    role_flip_count: u32,
    lifecycle: LevelLifecycle,
    promotions: Vec<Promotion>,
    footprint: BTreeMap<i32, FootprintBin>,
}

impl StructureLevel {
    fn is_active(&self) -> bool {
        matches!(self.lifecycle, LevelLifecycle::Active)
    }

    fn is_unified_projection_visible(&self) -> bool {
        matches!(
            self.lifecycle,
            LevelLifecycle::Active | LevelLifecycle::Crossed { .. }
        )
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimeframeBucket {
    start_ms: i64,
    high: f64,
    high_at: Option<DateTime<Utc>>,
    low: f64,
    low_at: Option<DateTime<Utc>>,
    total_volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    neutral_volume: f64,
    trade_count: u64,
}

impl TimeframeBucket {
    fn new(start_ms: i64, ts: DateTime<Utc>, price: f64, size: f64, aggressor: i8) -> Self {
        let mut bucket = Self {
            start_ms,
            high: price,
            high_at: Some(ts),
            low: price,
            low_at: Some(ts),
            ..Self::default()
        };
        bucket.observe(ts, price, size, aggressor);
        bucket
    }

    fn observe(&mut self, ts: DateTime<Utc>, price: f64, size: f64, aggressor: i8) {
        if price >= self.high {
            self.high = price;
            self.high_at = Some(ts);
        }
        if self.low <= 0.0 || price <= self.low {
            self.low = price;
            self.low_at = Some(ts);
        }
        if size <= 0.0 {
            return;
        }
        self.total_volume += size;
        if aggressor > 0 {
            self.buy_volume += size;
        } else if aggressor < 0 {
            self.sell_volume += size;
        } else {
            self.neutral_volume += size;
        }
        self.trade_count = self.trade_count.saturating_add(1);
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimeframeCrossing {
    direction: i8,
    first_crossed_at: Option<DateTime<Utc>>,
    beyond_trades: u32,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimeframeSwing {
    level_id: u64,
    side: i8,
    price: f64,
    pivot_at: Option<DateTime<Utc>>,
    confirmed_at: Option<DateTime<Utc>>,
    strength: f64,
    confidence: f64,
    total_volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    neutral_volume: f64,
    trade_count: u64,
    broken: bool,
    crossing: Option<TimeframeCrossing>,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimeframeState {
    timeframe: String,
    #[serde(default)]
    horizon_ms: i64,
    #[serde(default)]
    current_bucket: Option<TimeframeBucket>,
    #[serde(default)]
    completed_buckets: VecDeque<TimeframeBucket>,
    #[serde(default)]
    active_high: Option<TimeframeSwing>,
    #[serde(default)]
    active_low: Option<TimeframeSwing>,
    previous_high: f64,
    current_high: f64,
    previous_low: f64,
    current_low: f64,
    direction: i8,
    promoted_level_count: usize,
}

impl TimeframeState {
    fn new(timeframe: &str, horizon_ms: i64) -> Self {
        Self {
            timeframe: timeframe.to_string(),
            horizon_ms,
            ..Self::default()
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct PriceVolumeBin {
    total_volume: f64,
    buy_volume: f64,
    sell_volume: f64,
    neutral_volume: f64,
    trade_count: u64,
    largest_trade: f64,
}

/// A unified level is a persistent market-structure episode, not the result of
/// reclustering only the sources visible in the current snapshot. Its price
/// band and identity stay fixed while evidence is reinforced. A tentative
/// penetration remains part of the episode; only an event-native accepted
/// break closes the current role.
#[derive(Clone, Debug, Deserialize, Serialize)]
struct UnifiedLevelTrack {
    level: UnifiedStructureLevel,
    lifecycle: LevelLifecycle,
    #[serde(default)]
    last_relation: i8,
}

#[derive(Clone, Debug)]
pub struct GenericStructureEngine {
    sym: String,
    last_ts: Option<DateTime<Utc>>,
    replayed_through: Option<DateTime<Utc>>,
    last_arrival_sequence: u64,
    last_reference_price: f64,
    last_trade_price: f64,
    rolling_abs_trade_move: f64,
    rolling_spread: f64,
    rolling_trade_size: f64,
    bid: f64,
    ask: f64,
    leg_direction: i8,
    candidate_high: f64,
    candidate_high_at: Option<DateTime<Utc>>,
    candidate_low: f64,
    candidate_low_at: Option<DateTime<Utc>>,
    levels: Vec<StructureLevel>,
    unified_tracks: Vec<UnifiedLevelTrack>,
    timeframe_states: Vec<TimeframeState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, PriceVolumeBin>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
    applied_split_adjustments: Vec<StructureSplitAdjustment>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GenericStructureCheckpoint {
    pub algorithm_version: u16,
    pub sym: String,
    pub updated_at: Option<DateTime<Utc>>,
    #[serde(default)]
    pub replayed_through: Option<DateTime<Utc>>,
    #[serde(default)]
    pub last_arrival_sequence: u64,
    last_reference_price: f64,
    last_trade_price: f64,
    #[serde(default)]
    rolling_abs_trade_move: f64,
    #[serde(default)]
    rolling_spread: f64,
    #[serde(default)]
    rolling_trade_size: f64,
    bid: f64,
    ask: f64,
    leg_direction: i8,
    candidate_high: f64,
    candidate_high_at: Option<DateTime<Utc>>,
    candidate_low: f64,
    candidate_low_at: Option<DateTime<Utc>>,
    levels: Vec<StructureLevel>,
    #[serde(default)]
    unified_tracks: Vec<UnifiedLevelTrack>,
    timeframe_states: Vec<TimeframeState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, PriceVolumeBin>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
    #[serde(default)]
    pub applied_split_adjustments: Vec<StructureSplitAdjustment>,
}

impl GenericStructureEngine {
    pub fn new(sym: impl Into<String>) -> Self {
        Self {
            sym: sym.into().to_ascii_uppercase(),
            last_ts: None,
            replayed_through: None,
            last_arrival_sequence: 0,
            last_reference_price: 0.0,
            last_trade_price: 0.0,
            rolling_abs_trade_move: 0.0,
            rolling_spread: 0.0,
            rolling_trade_size: 0.0,
            bid: 0.0,
            ask: 0.0,
            leg_direction: 0,
            candidate_high: 0.0,
            candidate_high_at: None,
            candidate_low: 0.0,
            candidate_low_at: None,
            levels: Vec::new(),
            unified_tracks: Vec::new(),
            timeframe_states: STRUCTURE_TIMEFRAMES
                .iter()
                .map(|(timeframe, horizon_ms)| TimeframeState::new(timeframe, *horizon_ms))
                .collect(),
            session_anchor: None,
            session_high: 0.0,
            session_low: 0.0,
            opening_range_high: 0.0,
            opening_range_low: 0.0,
            session_volume_by_price: HashMap::new(),
            trade_volume_poc: 0.0,
            last_event: None,
            applied_split_adjustments: Vec::new(),
        }
    }

    /// Applies a corporate-action boundary to the complete persistent book.
    /// Price coordinates move by `split_from / split_to`; share quantities move
    /// by the inverse factor. The operation is idempotent by split identity.
    pub fn apply_split_adjustment(
        &mut self,
        adjustment: &StructureSplitAdjustment,
    ) -> Result<bool, String> {
        let mut checkpoint = self.checkpoint();
        let changed = checkpoint.apply_split_adjustment(adjustment)?;
        if changed {
            self.seed_checkpoint(&checkpoint);
        }
        Ok(changed)
    }

    pub fn updated_at_ms(&self) -> i64 {
        self.last_ts
            .map(|value| value.timestamp_millis())
            .unwrap_or_default()
    }

    pub fn checkpoint_cursor(&self) -> (i64, u64) {
        (self.updated_at_ms(), self.last_arrival_sequence)
    }

    pub fn apply_event(
        &mut self,
        event: &MarketEvent,
        trade_rule: TradeUpdateRule,
    ) -> (GenericStructureSnapshot, Vec<GenericStructureEvent>) {
        let ts = event.ts();
        let emitted = self.apply_event_without_snapshot(event, trade_rule);
        (self.snapshot(ts), emitted)
    }

    /// Advance the persistent book without materializing its presentation
    /// snapshot. Historical checkpoint and timeline builders consume hundreds
    /// of thousands of prints but need a snapshot only at a checkpoint or bar
    /// boundary; cloning and ranking the complete book for every print made a
    /// cold chart request take minutes and then time out in the frontend.
    pub fn apply_event_without_snapshot(
        &mut self,
        event: &MarketEvent,
        trade_rule: TradeUpdateRule,
    ) -> Vec<GenericStructureEvent> {
        let ts = event.ts();
        let arrival_sequence = event.arrival_sequence();
        if self.last_ts.is_some_and(|previous| {
            ts < previous
                || (ts == previous
                    && arrival_sequence > 0
                    && self.last_arrival_sequence > 0
                    && arrival_sequence <= self.last_arrival_sequence)
        }) {
            return Vec::new();
        }
        self.reset_session_if_needed(ts);
        // A delayed report remains part of the canonical audit sequence, so
        // its cursor must advance. Its execution belongs to an already closed
        // one-second bucket, however, and must never revise the current
        // structural book, session extrema, pivots, volume profile, or gates.
        if event.is_delayed_trade_report() {
            self.last_ts = Some(ts);
            self.replayed_through = Some(
                self.replayed_through
                    .map(|current| current.max(ts))
                    .unwrap_or(ts),
            );
            self.last_arrival_sequence = arrival_sequence;
            return Vec::new();
        }
        let mut emitted = Vec::new();
        match event {
            MarketEvent::Quote(quote)
                if quote.bid_price > 0.0
                    && quote.ask_price > quote.bid_price
                    && quote.bid_price.is_finite()
                    && quote.ask_price.is_finite() =>
            {
                self.bid = quote.bid_price;
                self.ask = quote.ask_price;
                let spread = quote.ask_price - quote.bid_price;
                self.rolling_spread = ewma(self.rolling_spread, spread, 0.08);
                self.last_reference_price = (quote.bid_price + quote.ask_price) / 2.0;
            }
            MarketEvent::Trade(trade)
                if trade_rule.update_last && trade.price > 0.0 && trade.price.is_finite() =>
            {
                let size = if trade_rule.update_volume {
                    trade.size.max(0.0)
                } else {
                    0.0
                };
                let aggressor = self.classify_aggressor(trade.price);
                if self.last_trade_price > 0.0 {
                    self.rolling_abs_trade_move = ewma(
                        self.rolling_abs_trade_move,
                        (trade.price - self.last_trade_price).abs(),
                        0.08,
                    );
                }
                if size > 0.0 {
                    self.rolling_trade_size = ewma(self.rolling_trade_size, size, 0.05);
                }
                self.last_reference_price = trade.price;
                self.observe_trade_reference(ts, trade.price);
                self.update_unified_level_lifecycles(ts, trade.price, size);
                self.observe_trade_volume(trade.price, size, aggressor);
                self.update_level_footprints(trade.price, size, aggressor);
                self.update_level_lifecycles(ts, trade.price, size, &mut emitted);
                self.update_directional_leg(ts, trade.price, &mut emitted);
                self.update_timeframe_structures(ts, trade.price, size, aggressor, &mut emitted);
                // Source membership changes only on structural events. Avoid
                // reclustering the complete persistent book for every raw
                // trade; lifecycle acceptance above still remains event-native.
                if !emitted.is_empty() {
                    self.refresh_unified_level_tracks(ts, trade.price);
                }
                self.last_trade_price = trade.price;
            }
            _ => {}
        }
        self.last_ts = Some(ts);
        self.replayed_through = Some(
            self.replayed_through
                .map(|current| current.max(ts))
                .unwrap_or(ts),
        );
        self.last_arrival_sequence = arrival_sequence;
        if let Some(last) = emitted.last().cloned() {
            self.last_event = Some(last);
        }
        emitted
    }

    fn classify_aggressor(&self, price: f64) -> i8 {
        if self.ask > self.bid && self.bid > 0.0 {
            if price >= self.ask {
                return 1;
            }
            if price <= self.bid {
                return -1;
            }
        }
        if self.last_trade_price > 0.0 {
            if price > self.last_trade_price {
                return 1;
            }
            if price < self.last_trade_price {
                return -1;
            }
        }
        0
    }

    fn update_directional_leg(
        &mut self,
        ts: DateTime<Utc>,
        price: f64,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        let reversal_distance = self.adaptive_reversal_distance(price);
        if self.candidate_high <= 0.0 {
            self.candidate_high = price;
            self.candidate_low = price;
            self.candidate_high_at = Some(ts);
            self.candidate_low_at = Some(ts);
            return;
        }
        if self.leg_direction == 0 {
            if price > self.last_trade_price && self.last_trade_price > 0.0 {
                self.leg_direction = 1;
                self.candidate_high = price;
                self.candidate_high_at = Some(ts);
            } else if price < self.last_trade_price && self.last_trade_price > 0.0 {
                self.leg_direction = -1;
                self.candidate_low = price;
                self.candidate_low_at = Some(ts);
            }
            self.candidate_high = self.candidate_high.max(price);
            self.candidate_low = if self.candidate_low > 0.0 {
                self.candidate_low.min(price)
            } else {
                price
            };
            return;
        }
        if self.leg_direction > 0 {
            if price >= self.candidate_high {
                self.candidate_high = price;
                self.candidate_high_at = Some(ts);
            } else if self.candidate_high - price >= reversal_distance {
                let pivot_price = self.candidate_high;
                let pivot_at = self.candidate_high_at.unwrap_or(ts);
                self.add_or_reinforce_level(-1, pivot_price, pivot_at, ts, emitted);
                self.leg_direction = -1;
                self.candidate_low = price;
                self.candidate_low_at = Some(ts);
                self.candidate_high = price;
                self.candidate_high_at = Some(ts);
            }
        } else if price <= self.candidate_low {
            self.candidate_low = price;
            self.candidate_low_at = Some(ts);
        } else if price - self.candidate_low >= reversal_distance {
            let pivot_price = self.candidate_low;
            let pivot_at = self.candidate_low_at.unwrap_or(ts);
            self.add_or_reinforce_level(1, pivot_price, pivot_at, ts, emitted);
            self.leg_direction = 1;
            self.candidate_high = price;
            self.candidate_high_at = Some(ts);
            self.candidate_low = price;
            self.candidate_low_at = Some(ts);
        }
    }

    fn add_or_reinforce_level(
        &mut self,
        side: i8,
        price: f64,
        pivot_at: DateTime<Utc>,
        confirmed_at: DateTime<Utc>,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        let half_width = self.adaptive_zone_half_width(price);
        if let Some(level) = self.levels.iter_mut().find(|level| {
            level.side == side
                && level.is_active()
                && price >= level.lower - half_width
                && price <= level.upper + half_width
        }) {
            level.touch_count = level.touch_count.saturating_add(1);
            level.hold_count = level.hold_count.saturating_add(1);
            level.last_test_at = confirmed_at;
            emitted.push(level_event(
                &self.sym,
                level,
                "",
                "level_reinforced",
                side,
                confirmed_at,
            ));
            return;
        }
        let level_id = stable_level_id(&self.sym, side, price, pivot_at);
        let level = StructureLevel {
            level_id,
            side,
            price,
            lower: price - half_width,
            upper: price + half_width,
            pivot_at,
            confirmed_at,
            last_test_at: confirmed_at,
            touch_count: 1,
            hold_count: 0,
            break_count: 0,
            accepted_break_count: 0,
            role_flip_count: 0,
            lifecycle: LevelLifecycle::Active,
            promotions: Vec::new(),
            footprint: BTreeMap::new(),
        };
        emitted.push(level_event(
            &self.sym,
            &level,
            "",
            "level_created",
            side,
            confirmed_at,
        ));
        self.levels.push(level);
        self.prune_levels();
    }

    fn adaptive_reversal_distance(&self, price: f64) -> f64 {
        let floor = price_tick(price) * 2.0;
        let geometry = (self.rolling_abs_trade_move * 0.75)
            .max(self.rolling_spread * 0.75)
            .max(floor);
        geometry.min((price * 0.02).max(floor))
    }

    fn adaptive_zone_half_width(&self, price: f64) -> f64 {
        let floor = price_tick(price);
        let geometry = (self.rolling_abs_trade_move * 0.75)
            .max(self.rolling_spread * 0.5)
            .max(floor);
        geometry.min((price * 0.01).max(floor))
    }

    fn update_timeframe_structures(
        &mut self,
        ts: DateTime<Utc>,
        price: f64,
        size: f64,
        aggressor: i8,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        for state in &mut self.timeframe_states {
            emitted.extend(observe_timeframe_structure(
                &self.sym, state, ts, price, size, aggressor,
            ));
        }
    }

    fn update_level_lifecycles(
        &mut self,
        ts: DateTime<Utc>,
        price: f64,
        size: f64,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        let mut pending = Vec::new();
        for level in &mut self.levels {
            // Retest geometry belongs to the fixed level, not to the latest
            // trade. This matters when the two prices straddle a tick regime
            // boundary such as $1.
            let tick = price_tick(level.price);
            let lifecycle = level.lifecycle.clone();
            match lifecycle {
                LevelLifecycle::Active => {
                    let direction = if level.side < 0 && price > level.price {
                        1
                    } else if level.side > 0 && price < level.price {
                        -1
                    } else {
                        0
                    };
                    if direction == 0 {
                        continue;
                    }
                    level.break_count = level.break_count.saturating_add(1);
                    level.last_test_at = ts;
                    level.lifecycle = LevelLifecycle::Crossed {
                        direction,
                        first_crossed_at: ts,
                        beyond_trades: 1,
                        beyond_volume: size,
                    };
                    pending.push((
                        level.level_id,
                        "".to_string(),
                        "level_crossed".to_string(),
                        direction,
                    ));
                }
                LevelLifecycle::Crossed {
                    direction,
                    first_crossed_at,
                    mut beyond_trades,
                    mut beyond_volume,
                } => {
                    let beyond = (direction > 0 && price > level.price)
                        || (direction < 0 && price < level.price);
                    if !beyond {
                        level.lifecycle = LevelLifecycle::Active;
                        level.hold_count = level.hold_count.saturating_add(1);
                        level.last_test_at = ts;
                        pending.push((
                            level.level_id,
                            "".to_string(),
                            "break_rejected".to_string(),
                            -direction,
                        ));
                        continue;
                    }
                    beyond_trades = beyond_trades.saturating_add(1);
                    beyond_volume += size;
                    if beyond_trades >= 2
                        || (ts - first_crossed_at).num_milliseconds().max(0) >= 100
                    {
                        level.accepted_break_count = level.accepted_break_count.saturating_add(1);
                        level.lifecycle = LevelLifecycle::AwaitingRetest {
                            direction,
                            accepted_at: ts,
                        };
                        pending.push((
                            level.level_id,
                            "".to_string(),
                            "break_accepted".to_string(),
                            direction,
                        ));
                    } else {
                        level.lifecycle = LevelLifecycle::Crossed {
                            direction,
                            first_crossed_at,
                            beyond_trades,
                            beyond_volume,
                        };
                    }
                }
                LevelLifecycle::AwaitingRetest {
                    direction,
                    accepted_at,
                } => {
                    if (price - level.price).abs() <= tick {
                        level.lifecycle = LevelLifecycle::RetestContact {
                            direction,
                            contacted_at: ts,
                        };
                        pending.push((
                            level.level_id,
                            "".to_string(),
                            "retest_started".to_string(),
                            direction,
                        ));
                    } else {
                        level.lifecycle = LevelLifecycle::AwaitingRetest {
                            direction,
                            accepted_at,
                        };
                    }
                }
                LevelLifecycle::RetestContact {
                    direction,
                    contacted_at,
                } => {
                    let rejected_in_break_direction = (direction > 0
                        && price >= level.price + tick)
                        || (direction < 0 && price <= level.price - tick);
                    let failed = (direction > 0 && price < level.price - tick)
                        || (direction < 0 && price > level.price + tick);
                    if rejected_in_break_direction {
                        level.side = direction;
                        level.role_flip_count = level.role_flip_count.saturating_add(1);
                        level.lifecycle = LevelLifecycle::Active;
                        level.touch_count = level.touch_count.saturating_add(1);
                        level.hold_count = level.hold_count.saturating_add(1);
                        level.last_test_at = ts;
                        pending.push((
                            level.level_id,
                            "".to_string(),
                            "role_reversal".to_string(),
                            direction,
                        ));
                    } else if failed {
                        level.lifecycle = LevelLifecycle::Active;
                        level.last_test_at = ts;
                        pending.push((
                            level.level_id,
                            "".to_string(),
                            "retest_failed".to_string(),
                            -direction,
                        ));
                    } else {
                        level.lifecycle = LevelLifecycle::RetestContact {
                            direction,
                            contacted_at,
                        };
                    }
                }
                LevelLifecycle::Retired => {}
            }
        }
        for (level_id, timeframe, kind, direction) in pending {
            if let Some(level) = self.levels.iter().find(|level| level.level_id == level_id) {
                emitted.push(level_event(
                    &self.sym, level, &timeframe, &kind, direction, ts,
                ));
            }
        }
    }

    fn update_level_footprints(&mut self, price: f64, size: f64, aggressor: i8) {
        if size <= 0.0 {
            return;
        }
        for level in &mut self.levels {
            if !level.lifecycle.visible() {
                continue;
            }
            let tick = price_tick(level.price);
            let offset = ((price - level.price) / tick).round() as i32;
            if offset.abs() > FOOTPRINT_RADIUS_TICKS {
                continue;
            }
            update_volume_bin(level.footprint.entry(offset).or_default(), size, aggressor);
        }
    }

    fn observe_trade_volume(&mut self, price: f64, size: f64, aggressor: i8) {
        if size <= 0.0 {
            return;
        }
        let key = price_key(price);
        let bin = self.session_volume_by_price.entry(key).or_default();
        update_volume_bin(bin, size, aggressor);
        self.trade_volume_poc = self
            .session_volume_by_price
            .iter()
            .max_by(|left, right| left.1.total_volume.total_cmp(&right.1.total_volume))
            .map(|(key, _)| price_from_key(*key))
            .unwrap_or_default();
    }

    fn observe_trade_reference(&mut self, ts: DateTime<Utc>, reference: f64) {
        let local = ts.with_timezone(&New_York);
        let seconds = local.time().num_seconds_from_midnight();
        if (SESSION_ANCHOR_SECONDS..SESSION_END_SECONDS).contains(&seconds) {
            self.session_high = self.session_high.max(reference);
            self.session_low = positive_min(self.session_low, reference);
        }
        if (REGULAR_OPEN_SECONDS..OPENING_RANGE_END_SECONDS).contains(&seconds) {
            self.opening_range_high = self.opening_range_high.max(reference);
            self.opening_range_low = positive_min(self.opening_range_low, reference);
        }
    }

    fn reset_session_if_needed(&mut self, ts: DateTime<Utc>) {
        let local = ts.with_timezone(&New_York);
        let mut anchor = local.date_naive();
        if local.time().num_seconds_from_midnight() < SESSION_ANCHOR_SECONDS {
            anchor = anchor.pred_opt().unwrap_or(anchor);
        }
        if self.session_anchor == Some(anchor) {
            return;
        }
        self.session_anchor = Some(anchor);
        self.session_high = 0.0;
        self.session_low = 0.0;
        self.opening_range_high = 0.0;
        self.opening_range_low = 0.0;
        self.session_volume_by_price.clear();
        self.trade_volume_poc = 0.0;
    }

    fn prune_levels(&mut self) {
        if self.levels.len() <= MAX_LEVELS {
            return;
        }
        self.levels.sort_by_key(|level| {
            (
                level.lifecycle.visible(),
                level.promotions.len(),
                level.last_test_at,
            )
        });
        let remove = self.levels.len() - MAX_LEVELS;
        self.levels.drain(0..remove);
    }

    fn update_unified_level_lifecycles(&mut self, ts: DateTime<Utc>, price: f64, size: f64) {
        let observed_move = self.rolling_abs_trade_move.max(0.0);
        let observed_spread = self.rolling_spread.max(0.0);
        let observed_trade_size = self.rolling_trade_size.max(0.0);
        for track in &mut self.unified_tracks {
            let lower = track.level.lower;
            let upper = track.level.upper;
            let tick = price_tick(track.level.price);
            let relation = if price < lower {
                -1
            } else if price > upper {
                1
            } else {
                0
            };
            match track.lifecycle.clone() {
                LevelLifecycle::Active => {
                    if relation == 0 && track.last_relation != 0 {
                        track.level.touch_count = track.level.touch_count.saturating_add(1);
                        track.level.last_test_at_ms = ts.timestamp_millis();
                    }
                    let broke_role = (track.level.side > 0 && price < lower - tick * 0.5)
                        || (track.level.side < 0 && price > upper + tick * 0.5);
                    if broke_role {
                        let direction = if track.level.side > 0 { -1 } else { 1 };
                        track.lifecycle = LevelLifecycle::Crossed {
                            direction,
                            first_crossed_at: ts,
                            beyond_trades: 1,
                            beyond_volume: size,
                        };
                    } else {
                        let held = track.last_relation == 0
                            && ((track.level.side > 0 && relation > 0)
                                || (track.level.side < 0 && relation < 0));
                        if held {
                            track.level.hold_count = track.level.hold_count.saturating_add(1);
                            track.level.last_test_at_ms = ts.timestamp_millis();
                        }
                    }
                }
                LevelLifecycle::Crossed {
                    direction,
                    first_crossed_at,
                    mut beyond_trades,
                    mut beyond_volume,
                } => {
                    let beyond = (direction < 0 && price < lower - tick * 0.5)
                        || (direction > 0 && price > upper + tick * 0.5);
                    if !beyond {
                        // A wick, penetration, or immediate retest is a rejected
                        // break. The same level episode remains active.
                        track.lifecycle = LevelLifecycle::Active;
                        track.level.hold_count = track.level.hold_count.saturating_add(1);
                        track.level.last_test_at_ms = ts.timestamp_millis();
                    } else {
                        beyond_trades = beyond_trades.saturating_add(1);
                        beyond_volume += size;
                        let penetration = if direction > 0 {
                            (price - upper).max(0.0)
                        } else {
                            (lower - price).max(0.0)
                        };
                        let band_width = (upper - lower).max(tick);
                        let acceptance_distance = tick
                            .max(band_width * 0.15)
                            .max(observed_move * 2.5)
                            .max(observed_spread * 0.75);
                        let decisive = penetration >= acceptance_distance * 2.0;
                        let required_trades = if decisive { 2 } else { 4 };
                        let required_ms = if decisive { 100 } else { 350 };
                        let required_volume = (observed_trade_size * 2.5).max(size.max(1.0));
                        let elapsed_ms = (ts - first_crossed_at).num_milliseconds().max(0);
                        let accepted = penetration >= acceptance_distance
                            && beyond_trades >= required_trades
                            && (elapsed_ms >= required_ms || beyond_volume >= required_volume);
                        if accepted {
                            track.level.break_count = track.level.break_count.saturating_add(1);
                            track.level.last_test_at_ms = ts.timestamp_millis();
                            track.lifecycle = LevelLifecycle::AwaitingRetest {
                                direction,
                                accepted_at: ts,
                            };
                        } else {
                            track.lifecycle = LevelLifecycle::Crossed {
                                direction,
                                first_crossed_at,
                                beyond_trades,
                                beyond_volume,
                            };
                        }
                    }
                }
                LevelLifecycle::AwaitingRetest {
                    direction,
                    accepted_at,
                } => {
                    if price >= lower - tick && price <= upper + tick {
                        track.lifecycle = LevelLifecycle::RetestContact {
                            direction,
                            contacted_at: ts,
                        };
                    } else {
                        track.lifecycle = LevelLifecycle::AwaitingRetest {
                            direction,
                            accepted_at,
                        };
                    }
                }
                LevelLifecycle::RetestContact {
                    direction,
                    contacted_at,
                } => {
                    let confirmed_flip = (direction > 0 && price > upper + tick)
                        || (direction < 0 && price < lower - tick);
                    let failed_retest = (direction > 0 && price < lower - tick)
                        || (direction < 0 && price > upper + tick);
                    if confirmed_flip {
                        track.level.side = direction;
                        track.level.role_flip_count = track.level.role_flip_count.saturating_add(1);
                        track.level.touch_count = track.level.touch_count.saturating_add(1);
                        track.level.hold_count = track.level.hold_count.saturating_add(1);
                        track.level.confirmed_at_ms = ts.timestamp_millis();
                        track.level.last_test_at_ms = ts.timestamp_millis();
                        track.lifecycle = LevelLifecycle::Active;
                    } else if failed_retest {
                        track.lifecycle = LevelLifecycle::Active;
                        track.level.last_test_at_ms = ts.timestamp_millis();
                    } else {
                        track.lifecycle = LevelLifecycle::RetestContact {
                            direction,
                            contacted_at,
                        };
                    }
                }
                LevelLifecycle::Retired => {}
            }
            track.last_relation = relation;
            track.level.lifecycle = track.lifecycle.label().to_string();
            track.level.pending_side = match track.lifecycle {
                LevelLifecycle::AwaitingRetest { direction, .. }
                | LevelLifecycle::RetestContact { direction, .. } => direction,
                _ => 0,
            };
            refresh_unified_track_evidence(track);
        }
        consolidate_unified_tracks(&mut self.unified_tracks);
    }

    fn refresh_unified_level_tracks(&mut self, ts: DateTime<Utc>, reference: f64) {
        let candidates =
            unified_structure_levels(&self.sym, &self.timeframe_states, &self.levels, reference);
        let tolerance = (price_tick(reference) * 2.0).max(reference * 0.0005);
        for candidate in candidates {
            let role_is_coherent = (candidate.side > 0 && reference >= candidate.lower - tolerance)
                || (candidate.side < 0 && reference <= candidate.upper + tolerance);
            if !role_is_coherent {
                continue;
            }
            let matching = self
                .unified_tracks
                .iter_mut()
                .filter(|track| {
                    track.level.side == candidate.side
                        && !matches!(track.lifecycle, LevelLifecycle::Retired)
                        && candidate.lower <= track.level.upper + tolerance
                        && candidate.upper >= track.level.lower - tolerance
                })
                .min_by(|left, right| {
                    (left.level.price - candidate.price)
                        .abs()
                        .total_cmp(&(right.level.price - candidate.price).abs())
                });
            if let Some(track) = matching {
                merge_unified_candidate(track, candidate);
                continue;
            }
            let mut level = candidate;
            level.unified_level_id = stable_hash(&format!(
                "{}|unified-episode|{}|{}|{}",
                self.sym,
                level.side,
                price_key(level.price),
                ts.timestamp_millis()
            ));
            level.created_at_ms = level.created_at_ms.max(1).min(ts.timestamp_millis());
            level.confirmed_at_ms = ts.timestamp_millis();
            // The unified episode inherits the causal lifecycle evidence that
            // made its event-native source eligible. Later candidate refreshes
            // do not re-add these counters, so this establishes one baseline
            // without double counting.
            level.touch_count = level.touch_count.max(1);
            level.last_test_at_ms = ts.timestamp_millis();
            let last_relation = if reference < level.lower {
                -1
            } else if reference > level.upper {
                1
            } else {
                0
            };
            let mut track = UnifiedLevelTrack {
                level,
                lifecycle: LevelLifecycle::Active,
                last_relation,
            };
            track.level.lifecycle = "active".to_string();
            track.level.pending_side = 0;
            refresh_unified_track_evidence(&mut track);
            self.unified_tracks.push(track);
        }
        consolidate_unified_tracks(&mut self.unified_tracks);
        prune_unified_tracks(&mut self.unified_tracks, reference);
    }

    pub fn snapshot(&self, now: DateTime<Utc>) -> GenericStructureSnapshot {
        let reference = if self.last_trade_price > 0.0 {
            self.last_trade_price
        } else {
            self.last_reference_price
        };
        let active_levels = exposed_active_levels(
            &self.levels,
            &self.session_volume_by_price,
            reference,
            self.session_anchor,
            now,
        );
        let support = active_levels
            .iter()
            .filter(|level| level.side > 0 && level.price < reference)
            .min_by(|left, right| left.distance.total_cmp(&right.distance))
            .map(candidate_to_snapshot)
            .unwrap_or_default();
        let resistance = active_levels
            .iter()
            .filter(|level| level.side < 0 && level.price > reference)
            .min_by(|left, right| left.distance.total_cmp(&right.distance))
            .map(candidate_to_snapshot)
            .unwrap_or_default();
        let timeframe_states = self
            .timeframe_states
            .iter()
            .map(|state| timeframe_snapshot(state, &active_levels))
            .collect::<Vec<_>>();
        let mut unified_levels = self
            .unified_tracks
            .iter()
            .filter(|track| track.lifecycle.visible())
            .map(|track| track.level.clone())
            .collect::<Vec<_>>();
        unified_levels.sort_by(|left, right| {
            right
                .hold_probability
                .total_cmp(&left.hold_probability)
                .then_with(|| {
                    right
                        .independent_pivot_count
                        .cmp(&left.independent_pivot_count)
                })
                .then_with(|| right.role_flip_count.cmp(&left.role_flip_count))
                .then_with(|| right.hold_count.cmp(&left.hold_count))
                .then_with(|| {
                    (left.price - reference)
                        .abs()
                        .total_cmp(&(right.price - reference).abs())
                })
                .then_with(|| left.unified_level_id.cmp(&right.unified_level_id))
        });
        let signed = timeframe_states
            .iter()
            .enumerate()
            .filter(|(_, state)| state.direction != 0)
            .map(|(index, state)| state.direction as f64 * (index + 1) as f64)
            .sum::<f64>();
        let active_weight = timeframe_states
            .iter()
            .enumerate()
            .filter(|(_, state)| state.direction != 0)
            .map(|(index, _)| (index + 1) as f64)
            .sum::<f64>();
        let direction = if signed > 0.0 {
            1
        } else if signed < 0.0 {
            -1
        } else {
            0
        };
        let agreement = if active_weight > 0.0 {
            (signed.abs() / active_weight).clamp(0.0, 1.0)
        } else {
            0.0
        };
        let support_field = level_field(
            active_levels
                .iter()
                .filter(|level| level.side > 0 && level.price < reference),
            reference,
        );
        let resistance_field = level_field(
            active_levels
                .iter()
                .filter(|level| level.side < 0 && level.price > reference),
            reference,
        );
        let field_total = support_field + resistance_field;
        let pressure_bias = if field_total > 0.0 {
            ((support_field - resistance_field) / field_total).clamp(-1.0, 1.0)
        } else {
            0.0
        };
        let pressure_confidence = (field_total / 2.0).clamp(0.0, 1.0);
        let strength = support.strength.max(resistance.strength);
        let confidence = (support.confidence.max(resistance.confidence) * (0.6 + 0.4 * agreement))
            .clamp(0.0, 1.0);
        let last = self.last_event.as_ref();
        GenericStructureSnapshot {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            reference_price: reference,
            direction,
            agreement,
            strength,
            confidence,
            support_field,
            resistance_field,
            pressure_bias,
            pressure_confidence,
            up_probability: (0.5 + 0.5 * pressure_bias * pressure_confidence).clamp(0.0, 1.0),
            support,
            resistance,
            active_levels,
            timeframe_states,
            unified_levels,
            developing_high: self.candidate_high,
            developing_low: self.candidate_low,
            developing_direction: self.leg_direction,
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
            last_event_timeframe: last
                .map(|event| event.timeframe.clone())
                .unwrap_or_default(),
            last_event_direction: last.map(|event| event.direction).unwrap_or_default(),
            last_event_price: last.map(|event| event.price).unwrap_or_default(),
            session_high: self.session_high,
            session_low: self.session_low,
            opening_range_high: self.opening_range_high,
            opening_range_low: self.opening_range_low,
            trade_volume_poc: self.trade_volume_poc,
            nearest_round: nearest_round_price(reference),
        }
    }

    pub fn seed_events(&mut self, events: &[GenericStructureEvent]) {
        for event in events
            .iter()
            .filter(|event| event.algorithm_version == GENERIC_STRUCTURE_ALGORITHM_VERSION)
        {
            self.last_ts = Some(
                self.last_ts
                    .map(|current| current.max(event.confirmed_at))
                    .unwrap_or(event.confirmed_at),
            );
            match event.event_kind.as_str() {
                "level_created" => {
                    if self
                        .levels
                        .iter()
                        .any(|level| level.level_id == event.level_id)
                    {
                        continue;
                    }
                    self.levels.push(StructureLevel {
                        level_id: event.level_id,
                        side: event.direction,
                        price: event.price,
                        lower: event.lower,
                        upper: event.upper,
                        pivot_at: event.pivot_at,
                        confirmed_at: event.confirmed_at,
                        last_test_at: event.confirmed_at,
                        touch_count: 1,
                        hold_count: 0,
                        break_count: 0,
                        accepted_break_count: 0,
                        role_flip_count: 0,
                        lifecycle: LevelLifecycle::Active,
                        promotions: Vec::new(),
                        footprint: BTreeMap::new(),
                    });
                }
                "level_promoted" => {
                    if let Some(state) = self
                        .timeframe_states
                        .iter_mut()
                        .find(|state| state.timeframe == event.timeframe)
                    {
                        seed_timeframe_swing(state, event);
                    }
                }
                "level_reinforced" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.touch_count = level.touch_count.saturating_add(1);
                        level.hold_count = level.hold_count.saturating_add(1);
                        level.last_test_at = event.confirmed_at;
                    }
                }
                "level_crossed" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.break_count = level.break_count.saturating_add(1);
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::Crossed {
                            direction: event.direction,
                            first_crossed_at: event.confirmed_at,
                            beyond_trades: 1,
                            beyond_volume: 0.0,
                        };
                    }
                }
                "break_accepted" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.accepted_break_count = level.accepted_break_count.saturating_add(1);
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::AwaitingRetest {
                            direction: event.direction,
                            accepted_at: event.confirmed_at,
                        };
                    }
                }
                "retest_started" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::RetestContact {
                            direction: event.direction,
                            contacted_at: event.confirmed_at,
                        };
                    }
                }
                "role_reversal" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.side = event.direction;
                        level.role_flip_count = level.role_flip_count.saturating_add(1);
                        level.touch_count = level.touch_count.saturating_add(1);
                        level.hold_count = level.hold_count.saturating_add(1);
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::Active;
                    }
                }
                "break_rejected" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.hold_count = level.hold_count.saturating_add(1);
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::Active;
                    }
                }
                "retest_failed" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.last_test_at = event.confirmed_at;
                        level.lifecycle = LevelLifecycle::Active;
                    }
                }
                "structure_break" | "bos" | "choch" => {
                    if let Some(state) = self
                        .timeframe_states
                        .iter_mut()
                        .find(|state| state.timeframe == event.timeframe)
                    {
                        state.direction = event.direction;
                        let target = if event.direction > 0 {
                            state.active_high.as_mut()
                        } else {
                            state.active_low.as_mut()
                        };
                        if let Some(swing) = target.filter(|swing| swing.level_id == event.level_id)
                        {
                            swing.broken = true;
                            swing.crossing = None;
                        }
                    }
                }
                _ => {}
            }
            self.last_event = Some(event.clone());
        }
        self.prune_levels();
    }

    pub fn seed_snapshot(&mut self, snapshot: &GenericStructureSnapshot) {
        if snapshot.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
            return;
        }
        self.last_reference_price = snapshot.reference_price;
        self.last_trade_price = snapshot.reference_price;
        self.candidate_high = snapshot.developing_high;
        self.candidate_low = snapshot.developing_low;
        self.leg_direction = snapshot.developing_direction;
        self.levels = snapshot
            .active_levels
            .iter()
            .map(candidate_to_level)
            .collect();
        self.unified_tracks = snapshot
            .unified_levels
            .iter()
            .cloned()
            .map(|level| UnifiedLevelTrack {
                last_relation: if snapshot.reference_price < level.lower {
                    -1
                } else if snapshot.reference_price > level.upper {
                    1
                } else {
                    0
                },
                level,
                lifecycle: LevelLifecycle::Active,
            })
            .collect();
        self.timeframe_states = snapshot
            .timeframe_states
            .iter()
            .map(|state| {
                let horizon_ms = STRUCTURE_TIMEFRAMES
                    .iter()
                    .find(|(timeframe, _)| *timeframe == state.timeframe)
                    .map(|(_, horizon_ms)| *horizon_ms)
                    .unwrap_or(100);
                TimeframeState {
                    timeframe: state.timeframe.clone(),
                    horizon_ms,
                    current_high: state.swing_high,
                    current_low: state.swing_low,
                    direction: state.direction,
                    promoted_level_count: state.promoted_level_count,
                    ..TimeframeState::default()
                }
            })
            .collect();
        self.session_high = snapshot.session_high;
        self.session_low = snapshot.session_low;
        self.opening_range_high = snapshot.opening_range_high;
        self.opening_range_low = snapshot.opening_range_low;
        self.trade_volume_poc = snapshot.trade_volume_poc;
    }

    pub fn checkpoint(&self) -> GenericStructureCheckpoint {
        GenericStructureCheckpoint {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            sym: self.sym.clone(),
            updated_at: self.last_ts,
            replayed_through: self.replayed_through,
            last_arrival_sequence: self.last_arrival_sequence,
            last_reference_price: self.last_reference_price,
            last_trade_price: self.last_trade_price,
            rolling_abs_trade_move: self.rolling_abs_trade_move,
            rolling_spread: self.rolling_spread,
            rolling_trade_size: self.rolling_trade_size,
            bid: self.bid,
            ask: self.ask,
            leg_direction: self.leg_direction,
            candidate_high: self.candidate_high,
            candidate_high_at: self.candidate_high_at,
            candidate_low: self.candidate_low,
            candidate_low_at: self.candidate_low_at,
            levels: self.levels.clone(),
            unified_tracks: self.unified_tracks.clone(),
            timeframe_states: self.timeframe_states.clone(),
            session_anchor: self.session_anchor,
            session_high: self.session_high,
            session_low: self.session_low,
            opening_range_high: self.opening_range_high,
            opening_range_low: self.opening_range_low,
            session_volume_by_price: self.session_volume_by_price.clone(),
            trade_volume_poc: self.trade_volume_poc,
            last_event: self.last_event.clone(),
            applied_split_adjustments: self.applied_split_adjustments.clone(),
        }
    }

    pub fn seed_checkpoint(&mut self, checkpoint: &GenericStructureCheckpoint) {
        if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
            return;
        }
        self.sym = checkpoint.sym.clone();
        self.last_ts = checkpoint.updated_at;
        self.replayed_through = checkpoint.replayed_through.or(checkpoint.updated_at);
        self.last_arrival_sequence = checkpoint.last_arrival_sequence;
        self.last_reference_price = checkpoint.last_reference_price;
        self.last_trade_price = checkpoint.last_trade_price;
        self.rolling_abs_trade_move = checkpoint.rolling_abs_trade_move;
        self.rolling_spread = checkpoint.rolling_spread;
        self.rolling_trade_size = checkpoint.rolling_trade_size;
        self.bid = checkpoint.bid;
        self.ask = checkpoint.ask;
        self.leg_direction = checkpoint.leg_direction;
        self.candidate_high = checkpoint.candidate_high;
        self.candidate_high_at = checkpoint.candidate_high_at;
        self.candidate_low = checkpoint.candidate_low;
        self.candidate_low_at = checkpoint.candidate_low_at;
        self.levels = checkpoint.levels.clone();
        self.unified_tracks = checkpoint.unified_tracks.clone();
        for track in &mut self.unified_tracks {
            refresh_unified_hold_evidence(&mut track.level);
        }
        self.timeframe_states = checkpoint.timeframe_states.clone();
        self.session_anchor = checkpoint.session_anchor;
        self.session_high = checkpoint.session_high;
        self.session_low = checkpoint.session_low;
        self.opening_range_high = checkpoint.opening_range_high;
        self.opening_range_low = checkpoint.opening_range_low;
        self.session_volume_by_price = checkpoint.session_volume_by_price.clone();
        self.trade_volume_poc = checkpoint.trade_volume_poc;
        self.last_event = checkpoint.last_event.clone();
        self.applied_split_adjustments = checkpoint.applied_split_adjustments.clone();
    }
}

impl GenericStructureCheckpoint {
    pub fn apply_split_adjustment(
        &mut self,
        adjustment: &StructureSplitAdjustment,
    ) -> Result<bool, String> {
        if !(adjustment.split_from.is_finite()
            && adjustment.split_to.is_finite()
            && adjustment.split_from > 0.0
            && adjustment.split_to > 0.0)
        {
            return Err("Generic Structure split terms must be finite and positive".to_string());
        }
        if adjustment.effective_at.date_naive() < adjustment.execution_date {
            return Err("Generic Structure split effective_at precedes execution_date".to_string());
        }
        if self
            .applied_split_adjustments
            .iter()
            .any(|applied| applied.identity() == adjustment.identity())
        {
            return Ok(false);
        }
        let price_factor = adjustment.split_from / adjustment.split_to;
        let share_factor = adjustment.split_to / adjustment.split_from;
        scale_price(&mut self.last_reference_price, price_factor);
        scale_price(&mut self.last_trade_price, price_factor);
        scale_price(&mut self.rolling_abs_trade_move, price_factor);
        scale_price(&mut self.rolling_spread, price_factor);
        scale_quantity(&mut self.rolling_trade_size, share_factor);
        scale_price(&mut self.bid, price_factor);
        scale_price(&mut self.ask, price_factor);
        scale_price(&mut self.candidate_high, price_factor);
        scale_price(&mut self.candidate_low, price_factor);
        scale_price(&mut self.session_high, price_factor);
        scale_price(&mut self.session_low, price_factor);
        scale_price(&mut self.opening_range_high, price_factor);
        scale_price(&mut self.opening_range_low, price_factor);
        scale_price(&mut self.trade_volume_poc, price_factor);

        for level in &mut self.levels {
            let old_price = level.price;
            let old_tick = price_tick(old_price);
            scale_price(&mut level.price, price_factor);
            scale_price(&mut level.lower, price_factor);
            scale_price(&mut level.upper, price_factor);
            scale_lifecycle_volume(&mut level.lifecycle, share_factor);
            let new_tick = price_tick(level.price);
            let mut footprint = BTreeMap::<i32, FootprintBin>::new();
            for (offset, mut bin) in std::mem::take(&mut level.footprint) {
                let absolute_price = (old_price + offset as f64 * old_tick) * price_factor;
                let new_offset = ((absolute_price - level.price) / new_tick).round() as i32;
                scale_footprint_bin(&mut bin, share_factor);
                merge_footprint_bin(footprint.entry(new_offset).or_default(), &bin);
            }
            level.footprint = footprint;
        }
        for track in &mut self.unified_tracks {
            scale_unified_level(&mut track.level, price_factor, share_factor);
            scale_lifecycle_volume(&mut track.lifecycle, share_factor);
        }
        for state in &mut self.timeframe_states {
            scale_price(&mut state.previous_high, price_factor);
            scale_price(&mut state.current_high, price_factor);
            scale_price(&mut state.previous_low, price_factor);
            scale_price(&mut state.current_low, price_factor);
            if let Some(bucket) = &mut state.current_bucket {
                scale_timeframe_bucket(bucket, price_factor, share_factor);
            }
            for bucket in &mut state.completed_buckets {
                scale_timeframe_bucket(bucket, price_factor, share_factor);
            }
            if let Some(swing) = &mut state.active_high {
                scale_timeframe_swing(swing, price_factor, share_factor);
            }
            if let Some(swing) = &mut state.active_low {
                scale_timeframe_swing(swing, price_factor, share_factor);
            }
        }
        let mut volume_by_price = HashMap::<i64, PriceVolumeBin>::new();
        for (key, mut bin) in std::mem::take(&mut self.session_volume_by_price) {
            scale_price_volume_bin(&mut bin, share_factor);
            let adjusted_key = price_key(price_from_key(key) * price_factor);
            merge_price_volume_bin(volume_by_price.entry(adjusted_key).or_default(), &bin);
        }
        self.session_volume_by_price = volume_by_price;
        if let Some(event) = &mut self.last_event {
            scale_price(&mut event.price, price_factor);
            scale_price(&mut event.lower, price_factor);
            scale_price(&mut event.upper, price_factor);
            scale_quantity(&mut event.total_volume, share_factor);
            scale_quantity(&mut event.buy_volume, share_factor);
            scale_quantity(&mut event.sell_volume, share_factor);
            scale_quantity(&mut event.neutral_volume, share_factor);
        }
        self.applied_split_adjustments.push(adjustment.clone());
        self.applied_split_adjustments
            .sort_by_key(|item| (item.effective_at, item.source_inserted_at));
        Ok(true)
    }
}

fn scale_price(value: &mut f64, factor: f64) {
    if value.is_finite() && *value != 0.0 {
        *value *= factor;
    }
}

fn scale_quantity(value: &mut f64, factor: f64) {
    if value.is_finite() && *value != 0.0 {
        *value *= factor;
    }
}

fn scale_lifecycle_volume(lifecycle: &mut LevelLifecycle, factor: f64) {
    if let LevelLifecycle::Crossed { beyond_volume, .. } = lifecycle {
        scale_quantity(beyond_volume, factor);
    }
}

fn scale_footprint_bin(bin: &mut FootprintBin, factor: f64) {
    scale_quantity(&mut bin.total_volume, factor);
    scale_quantity(&mut bin.buy_volume, factor);
    scale_quantity(&mut bin.sell_volume, factor);
    scale_quantity(&mut bin.neutral_volume, factor);
    scale_quantity(&mut bin.largest_trade, factor);
}

fn merge_footprint_bin(target: &mut FootprintBin, source: &FootprintBin) {
    target.total_volume += source.total_volume;
    target.buy_volume += source.buy_volume;
    target.sell_volume += source.sell_volume;
    target.neutral_volume += source.neutral_volume;
    target.trade_count = target.trade_count.saturating_add(source.trade_count);
    target.largest_trade = target.largest_trade.max(source.largest_trade);
}

fn scale_price_volume_bin(bin: &mut PriceVolumeBin, factor: f64) {
    scale_quantity(&mut bin.total_volume, factor);
    scale_quantity(&mut bin.buy_volume, factor);
    scale_quantity(&mut bin.sell_volume, factor);
    scale_quantity(&mut bin.neutral_volume, factor);
    scale_quantity(&mut bin.largest_trade, factor);
}

fn merge_price_volume_bin(target: &mut PriceVolumeBin, source: &PriceVolumeBin) {
    target.total_volume += source.total_volume;
    target.buy_volume += source.buy_volume;
    target.sell_volume += source.sell_volume;
    target.neutral_volume += source.neutral_volume;
    target.trade_count = target.trade_count.saturating_add(source.trade_count);
    target.largest_trade = target.largest_trade.max(source.largest_trade);
}

fn scale_timeframe_bucket(bucket: &mut TimeframeBucket, price_factor: f64, share_factor: f64) {
    scale_price(&mut bucket.high, price_factor);
    scale_price(&mut bucket.low, price_factor);
    scale_quantity(&mut bucket.total_volume, share_factor);
    scale_quantity(&mut bucket.buy_volume, share_factor);
    scale_quantity(&mut bucket.sell_volume, share_factor);
    scale_quantity(&mut bucket.neutral_volume, share_factor);
}

fn scale_timeframe_swing(swing: &mut TimeframeSwing, price_factor: f64, share_factor: f64) {
    scale_price(&mut swing.price, price_factor);
    scale_quantity(&mut swing.total_volume, share_factor);
    scale_quantity(&mut swing.buy_volume, share_factor);
    scale_quantity(&mut swing.sell_volume, share_factor);
    scale_quantity(&mut swing.neutral_volume, share_factor);
}

fn scale_unified_level(level: &mut UnifiedStructureLevel, price_factor: f64, share_factor: f64) {
    scale_price(&mut level.price, price_factor);
    scale_price(&mut level.lower, price_factor);
    scale_price(&mut level.upper, price_factor);
    scale_quantity(&mut level.total_volume, share_factor);
    scale_quantity(&mut level.buy_volume, share_factor);
    scale_quantity(&mut level.sell_volume, share_factor);
    scale_quantity(&mut level.neutral_volume, share_factor);
    for source in &mut level.sources {
        scale_price(&mut source.price, price_factor);
        scale_quantity(&mut source.total_volume, share_factor);
        scale_quantity(&mut source.buy_volume, share_factor);
        scale_quantity(&mut source.sell_volume, share_factor);
        scale_quantity(&mut source.neutral_volume, share_factor);
    }
}

#[derive(Clone)]
struct UnifiedSwingAtom {
    source: UnifiedStructureSource,
}

fn unified_structure_levels(
    sym: &str,
    states: &[TimeframeState],
    level_book: &[StructureLevel],
    reference: f64,
) -> Vec<UnifiedStructureLevel> {
    if !(reference > 0.0) {
        return Vec::new();
    }
    // The persistent book is the authority and remains fully retained. The
    // wire projection is intentionally bounded: rescoring every footprint in
    // a months-long book for every raw quote/trade makes historical replay
    // quadratic in accumulated levels. Rank with lifecycle metadata first,
    // then perform footprint-aware clustering only for the strongest bounded
    // candidates on each side.
    let role_tolerance = (price_tick(reference) * 2.0).max(reference * 0.0005);
    let mut book_candidates = level_book
        .iter()
        // A first cross is only a tentative breach. Keep projecting the band
        // until the event-native acceptance rules declare the break durable.
        .filter(|level| level.is_unified_projection_visible() && level.price > 0.0)
        .collect::<Vec<_>>();
    book_candidates.sort_by(|left, right| {
        let left_role_coherent = (left.side > 0 && reference >= left.lower - role_tolerance)
            || (left.side < 0 && reference <= left.upper + role_tolerance);
        let right_role_coherent = (right.side > 0 && reference >= right.lower - role_tolerance)
            || (right.side < 0 && reference <= right.upper + role_tolerance);
        left.side
            .cmp(&right.side)
            .then_with(|| right_role_coherent.cmp(&left_role_coherent))
            .then_with(|| {
                right
                    .role_flip_count
                    .min(3)
                    .cmp(&left.role_flip_count.min(3))
            })
            .then_with(|| right.hold_count.min(5).cmp(&left.hold_count.min(5)))
            .then_with(|| {
                right
                    .promotions
                    .len()
                    .min(8)
                    .cmp(&left.promotions.len().min(8))
            })
            .then_with(|| right.touch_count.min(6).cmp(&left.touch_count.min(6)))
            .then_with(|| {
                left.accepted_break_count
                    .min(3)
                    .cmp(&right.accepted_break_count.min(3))
            })
            .then_with(|| {
                (left.price - reference)
                    .abs()
                    .total_cmp(&(right.price - reference).abs())
            })
            .then_with(|| right.last_test_at.cmp(&left.last_test_at))
            .then_with(|| left.level_id.cmp(&right.level_id))
    });
    let mut book_side_counts = HashMap::<i8, usize>::new();
    let mut atoms = book_candidates
        .into_iter()
        .filter(|level| {
            let count = book_side_counts.entry(level.side).or_default();
            if *count >= MAX_UNIFIED_BOOK_CANDIDATES_PER_SIDE {
                false
            } else {
                *count += 1;
                true
            }
        })
        .map(|level| {
            let (total_volume, buy_volume, sell_volume, neutral_volume, trade_count) =
                level.footprint.values().fold(
                    (0.0, 0.0, 0.0, 0.0, 0_u64),
                    |(total, buy, sell, neutral, trades), bin| {
                        (
                            total + bin.total_volume,
                            buy + bin.buy_volume,
                            sell + bin.sell_volume,
                            neutral + bin.neutral_volume,
                            trades.saturating_add(bin.trade_count),
                        )
                    },
                );
            UnifiedSwingAtom {
                source: UnifiedStructureSource {
                    level_id: level.level_id,
                    timeframe: "event-native".to_string(),
                    side: level.side,
                    price: level.price,
                    pivot_at_ms: level.pivot_at.timestamp_millis(),
                    confirmed_at_ms: level.confirmed_at.timestamp_millis(),
                    total_volume,
                    buy_volume,
                    sell_volume,
                    neutral_volume,
                    trade_count,
                    source_kind: "level_book".to_string(),
                    touch_count: level.touch_count,
                    hold_count: level.hold_count,
                    break_count: level.accepted_break_count,
                    role_flip_count: level.role_flip_count,
                    last_test_at_ms: level.last_test_at.timestamp_millis(),
                },
            }
        })
        .collect::<Vec<_>>();
    let timeframe_atoms = states
        .iter()
        .flat_map(|state| {
            [state.active_low.as_ref(), state.active_high.as_ref()]
                .into_iter()
                .flatten()
                .filter(|swing| !swing.broken && swing.price > 0.0)
                .map(|swing| UnifiedSwingAtom {
                    source: UnifiedStructureSource {
                        level_id: swing.level_id,
                        timeframe: state.timeframe.clone(),
                        side: swing.side,
                        price: swing.price,
                        pivot_at_ms: swing
                            .pivot_at
                            .map(|value| value.timestamp_millis())
                            .unwrap_or_default(),
                        confirmed_at_ms: swing
                            .confirmed_at
                            .map(|value| value.timestamp_millis())
                            .unwrap_or_default(),
                        total_volume: swing.total_volume.max(0.0),
                        buy_volume: 0.0,
                        sell_volume: 0.0,
                        neutral_volume: swing.total_volume.max(0.0),
                        trade_count: swing.trade_count,
                        source_kind: "timeframe_swing".to_string(),
                        touch_count: 1,
                        hold_count: 0,
                        break_count: 0,
                        role_flip_count: 0,
                        last_test_at_ms: swing
                            .confirmed_at
                            .map(|value| value.timestamp_millis())
                            .unwrap_or_default(),
                    },
                })
                .collect::<Vec<_>>()
        })
        .collect::<Vec<_>>();
    atoms.sort_by(|left, right| {
        left.source
            .side
            .cmp(&right.source.side)
            .then_with(|| left.source.price.total_cmp(&right.source.price))
            .then_with(|| {
                left.source
                    .confirmed_at_ms
                    .cmp(&right.source.confirmed_at_ms)
            })
            .then_with(|| left.source.level_id.cmp(&right.source.level_id))
    });
    let bandwidth = (price_tick(reference) * 2.0).max(reference * 0.0005);
    let mut clusters: Vec<Vec<UnifiedSwingAtom>> = Vec::new();
    for atom in atoms {
        let joins = clusters.last().is_some_and(|cluster| {
            cluster
                .first()
                .is_some_and(|first| first.source.side == atom.source.side)
                && cluster
                    .iter()
                    .map(|item| item.source.price)
                    .fold(f64::NEG_INFINITY, f64::max)
                    + bandwidth
                    >= atom.source.price
        });
        if joins {
            if let Some(cluster) = clusters.last_mut() {
                cluster.push(atom);
            }
        } else {
            clusters.push(vec![atom]);
        }
    }
    // Timeframe structure is corroborating evidence only. A selected chart
    // interval must never create, remove, or re-identify the unified book.
    // Attach a completed timeframe pivot only to a compatible event-native
    // cluster that already exists.
    for atom in timeframe_atoms {
        let matching = clusters
            .iter_mut()
            .filter(|cluster| {
                cluster
                    .first()
                    .is_some_and(|first| first.source.side == atom.source.side)
            })
            .filter(|cluster| {
                let lower = cluster
                    .iter()
                    .map(|item| item.source.price)
                    .fold(f64::INFINITY, f64::min);
                let upper = cluster
                    .iter()
                    .map(|item| item.source.price)
                    .fold(f64::NEG_INFINITY, f64::max);
                atom.source.price >= lower - bandwidth && atom.source.price <= upper + bandwidth
            })
            .min_by(|left, right| {
                let left_distance = left
                    .iter()
                    .map(|item| (item.source.price - atom.source.price).abs())
                    .fold(f64::INFINITY, f64::min);
                let right_distance = right
                    .iter()
                    .map(|item| (item.source.price - atom.source.price).abs())
                    .fold(f64::INFINITY, f64::min);
                left_distance.total_cmp(&right_distance)
            });
        if let Some(cluster) = matching {
            cluster.push(atom);
        }
    }
    let mut levels = clusters
        .into_iter()
        .map(|cluster| unified_structure_level(sym, cluster, bandwidth))
        .collect::<Vec<_>>();
    levels.retain(is_major_unified_level);
    levels.sort_by(|left, right| {
        right
            .hold_probability
            .total_cmp(&left.hold_probability)
            .then_with(|| {
                right
                    .independent_pivot_count
                    .cmp(&left.independent_pivot_count)
            })
            .then_with(|| right.role_flip_count.cmp(&left.role_flip_count))
            .then_with(|| right.hold_count.cmp(&left.hold_count))
            .then_with(|| {
                (left.price - reference)
                    .abs()
                    .total_cmp(&(right.price - reference).abs())
            })
            .then_with(|| left.unified_level_id.cmp(&right.unified_level_id))
    });
    let mut side_counts = HashMap::<i8, usize>::new();
    levels
        .into_iter()
        .filter(|level| {
            let count = side_counts.entry(level.side).or_default();
            if *count >= MAX_UNIFIED_LEVELS_PER_SIDE {
                false
            } else {
                *count += 1;
                true
            }
        })
        .collect()
}

fn is_major_unified_level(level: &UnifiedStructureLevel) -> bool {
    level
        .sources
        .iter()
        .any(|source| source.source_kind == "timeframe_swing")
        || level.independent_pivot_count >= 2
        || level.hold_count >= 2
        || level.role_flip_count > 0
        || level
            .timeframes
            .iter()
            .any(|timeframe| matches!(timeframe.as_str(), "1m" | "5m" | "1h" | "1d" | "1w"))
}

fn unified_structure_level(
    sym: &str,
    cluster: Vec<UnifiedSwingAtom>,
    bandwidth: f64,
) -> UnifiedStructureLevel {
    let sources = cluster
        .into_iter()
        .map(|item| item.source)
        .collect::<Vec<_>>();
    let mut independent = BTreeMap::<(i64, i64), &UnifiedStructureSource>::new();
    for source in &sources {
        let identity = (price_key(source.price), source.pivot_at_ms);
        independent.entry(identity).or_insert(source);
    }
    let mut independent_book_sources = BTreeMap::<(i64, i64), &UnifiedStructureSource>::new();
    for source in sources
        .iter()
        .filter(|source| source.source_kind == "level_book")
    {
        independent_book_sources
            .entry((price_key(source.price), source.pivot_at_ms))
            .or_insert(source);
    }
    let book_sources = independent_book_sources
        .values()
        .copied()
        .collect::<Vec<_>>();
    let anchor_source = book_sources
        .iter()
        .copied()
        .min_by_key(|source| (source.pivot_at_ms, source.level_id))
        .or_else(|| {
            sources
                .iter()
                .min_by_key(|source| (source.pivot_at_ms, source.level_id))
        });
    let price = anchor_source.map(|source| source.price).unwrap_or_default();
    let touch_count = book_sources
        .iter()
        .map(|source| source.touch_count)
        .max()
        .unwrap_or_default();
    let hold_count = book_sources
        .iter()
        .map(|source| source.hold_count)
        .max()
        .unwrap_or_default();
    let break_count = book_sources
        .iter()
        .map(|source| source.break_count)
        .max()
        .unwrap_or_default();
    let role_flip_count = book_sources
        .iter()
        .map(|source| source.role_flip_count)
        .max()
        .unwrap_or_default();
    let geometry_sources = sources
        .iter()
        .filter(|source| source.source_kind == "level_book")
        .collect::<Vec<_>>();
    let lower_source = geometry_sources
        .iter()
        .map(|source| source.price)
        .fold(f64::INFINITY, f64::min);
    let upper_source = geometry_sources
        .iter()
        .map(|source| source.price)
        .fold(f64::NEG_INFINITY, f64::max);
    let mut timeframes = sources
        .iter()
        .map(|source| source.timeframe.clone())
        .collect::<Vec<_>>();
    timeframes.sort_by_key(|timeframe| {
        STRUCTURE_TIMEFRAMES
            .iter()
            .position(|(candidate, _)| candidate == timeframe)
            .unwrap_or(usize::MAX)
    });
    timeframes.dedup();
    let created_at_ms = sources
        .iter()
        .map(|source| source.pivot_at_ms)
        .filter(|value| *value > 0)
        .min()
        .unwrap_or_default();
    let confirmed_at_ms = sources
        .iter()
        .map(|source| source.confirmed_at_ms)
        .max()
        .unwrap_or_default();
    let side = sources
        .first()
        .map(|source| source.side)
        .unwrap_or_default();
    let lower = lower_source - bandwidth * 0.5;
    let upper = upper_source + bandwidth * 0.5;
    let total_volume = book_sources
        .iter()
        .map(|source| source.total_volume)
        .sum::<f64>();
    let buy_volume = book_sources
        .iter()
        .map(|source| source.buy_volume)
        .sum::<f64>();
    let sell_volume = book_sources
        .iter()
        .map(|source| source.sell_volume)
        .sum::<f64>();
    let neutral_volume = book_sources
        .iter()
        .map(|source| source.neutral_volume)
        .sum::<f64>();
    let trade_count = book_sources
        .iter()
        .map(|source| source.trade_count)
        .sum::<u64>();
    let directional_volume = buy_volume + sell_volume;
    let pressure_bias = if directional_volume > 0.0 {
        ((buy_volume - sell_volume) / directional_volume).clamp(-1.0, 1.0)
    } else {
        0.0
    };
    let last_test_at_ms = sources
        .iter()
        .map(|source| source.last_test_at_ms)
        .max()
        .unwrap_or(confirmed_at_ms);
    // Prefer the persistent event-native book identity. Timeframe swing
    // membership can change as new confirmations join a cluster; allowing one
    // of those sources to become the anchor would make the same long-lived
    // level appear to be a new object.
    let anchor_level_id = anchor_source
        .map(|source| source.level_id)
        .unwrap_or_default();
    let unified_level_id = stable_hash(&format!("{sym}|unified-book|{anchor_level_id}"));
    let mut level = UnifiedStructureLevel {
        unified_level_id,
        side,
        price,
        lower,
        upper,
        source_count: sources.len(),
        independent_pivot_count: independent.len(),
        timeframes,
        created_at_ms,
        confirmed_at_ms,
        total_volume,
        buy_volume,
        sell_volume,
        neutral_volume,
        trade_count,
        pressure_bias,
        hold_probability: 0.0,
        break_probability: 0.0,
        hold_rate: 0.0,
        hold_observation_count: 0,
        hold_evidence_reliability: 0.0,
        hold_quality_score: 0.0,
        hold_score_revision: String::new(),
        touch_count,
        hold_count,
        break_count,
        role_flip_count,
        last_test_at_ms,
        lifecycle: "active".to_string(),
        pending_side: 0,
        sources,
    };
    refresh_unified_hold_evidence(&mut level);
    level
}

fn merge_unified_candidate(track: &mut UnifiedLevelTrack, candidate: UnifiedStructureLevel) {
    // Geometry is deliberately fixed for the role episode. New nearby swings
    // reinforce the level book without rewriting its earlier causal price
    // range or producing a new chart segment.
    track.level.confirmed_at_ms = track.level.confirmed_at_ms.max(candidate.confirmed_at_ms);
    track.level.last_test_at_ms = track.level.last_test_at_ms.max(candidate.last_test_at_ms);
    for timeframe in candidate.timeframes {
        if !track.level.timeframes.contains(&timeframe) {
            track.level.timeframes.push(timeframe);
        }
    }
    track.level.timeframes.sort_by_key(|timeframe| {
        STRUCTURE_TIMEFRAMES
            .iter()
            .position(|(candidate, _)| candidate == timeframe)
            .unwrap_or(usize::MAX)
    });
    for source in candidate.sources {
        let identity = (
            source.level_id,
            source.timeframe.clone(),
            source.source_kind.clone(),
        );
        if let Some(existing) = track.level.sources.iter_mut().find(|existing| {
            (
                existing.level_id,
                existing.timeframe.clone(),
                existing.source_kind.clone(),
            ) == identity
        }) {
            if source.last_test_at_ms >= existing.last_test_at_ms {
                *existing = source;
            }
        } else {
            track.level.sources.push(source);
        }
    }
    track.level.sources.sort_by(|left, right| {
        right
            .role_flip_count
            .cmp(&left.role_flip_count)
            .then_with(|| right.hold_count.cmp(&left.hold_count))
            .then_with(|| right.touch_count.cmp(&left.touch_count))
            .then_with(|| right.last_test_at_ms.cmp(&left.last_test_at_ms))
            .then_with(|| left.level_id.cmp(&right.level_id))
    });
    track.level.sources.truncate(MAX_UNIFIED_SOURCES_PER_TRACK);
    refresh_unified_track_evidence(track);
}

fn refresh_unified_track_evidence(track: &mut UnifiedLevelTrack) {
    let mut independent = BTreeMap::<(i64, i64), &UnifiedStructureSource>::new();
    for source in &track.level.sources {
        independent
            .entry((price_key(source.price), source.pivot_at_ms))
            .or_insert(source);
    }
    track.level.source_count = track.level.sources.len();
    track.level.independent_pivot_count = independent.len();
    if independent.is_empty() {
        return;
    }
    let mut independent_book_sources = BTreeMap::<(i64, i64), &UnifiedStructureSource>::new();
    for source in track
        .level
        .sources
        .iter()
        .filter(|source| source.source_kind == "level_book")
    {
        independent_book_sources
            .entry((price_key(source.price), source.pivot_at_ms))
            .or_insert(source);
    }
    track.level.total_volume = independent_book_sources
        .values()
        .map(|source| source.total_volume)
        .sum();
    track.level.buy_volume = independent_book_sources
        .values()
        .map(|source| source.buy_volume)
        .sum();
    track.level.sell_volume = independent_book_sources
        .values()
        .map(|source| source.sell_volume)
        .sum();
    track.level.neutral_volume = independent_book_sources
        .values()
        .map(|source| source.neutral_volume)
        .sum();
    track.level.trade_count = independent_book_sources
        .values()
        .fold(0_u64, |total, source| {
            total.saturating_add(source.trade_count)
        });
    refresh_unified_hold_evidence(&mut track.level);
    let directional_volume = track.level.buy_volume + track.level.sell_volume;
    track.level.pressure_bias = if directional_volume > 0.0 {
        ((track.level.buy_volume - track.level.sell_volume) / directional_volume).clamp(-1.0, 1.0)
    } else {
        0.0
    };
}

fn refresh_unified_hold_evidence(level: &mut UnifiedStructureLevel) {
    let holds = level.hold_count as f64;
    let breaks = level.break_count as f64;
    let observations = holds + breaks;
    let posterior_successes = holds + 2.0;
    let posterior_total = observations + 4.0;
    let posterior_mean = (posterior_successes / posterior_total).clamp(0.0, 1.0);
    let z2 = HOLD_SCORE_Z * HOLD_SCORE_Z;
    let denominator = 1.0 + z2 / posterior_total;
    let center = posterior_mean + z2 / (2.0 * posterior_total);
    let spread = HOLD_SCORE_Z
        * ((posterior_mean * (1.0 - posterior_mean) / posterior_total
            + z2 / (4.0 * posterior_total * posterior_total))
            .max(0.0))
        .sqrt();
    level.hold_probability = posterior_mean;
    level.break_probability = (1.0 - posterior_mean).clamp(0.0, 1.0);
    level.hold_rate = if observations > 0.0 {
        (holds / observations).clamp(0.0, 1.0)
    } else {
        0.0
    };
    level.hold_observation_count = level.hold_count.saturating_add(level.break_count);
    level.hold_evidence_reliability =
        (observations / (observations + HOLD_RELIABILITY_HALF_LIFE)).clamp(0.0, 1.0);
    level.hold_quality_score = ((center - spread) / denominator).clamp(0.0, 1.0);
    if level.hold_score_revision != STRUCTURE_HOLD_SCORE_REVISION {
        level.hold_score_revision = STRUCTURE_HOLD_SCORE_REVISION.to_string();
    }
}

fn consolidate_unified_tracks(tracks: &mut Vec<UnifiedLevelTrack>) {
    tracks.sort_by_key(|track| (track.level.created_at_ms, track.level.unified_level_id));
    let mut consolidated: Vec<UnifiedLevelTrack> = Vec::with_capacity(tracks.len());
    for track in tracks.drain(..) {
        let tolerance = price_tick(track.level.price) * 2.0;
        let matching = consolidated.iter_mut().find(|existing| {
            (existing.level.side == track.level.side
                || existing.level.pending_side == track.level.side
                || track.level.pending_side == existing.level.side)
                && existing.lifecycle.visible()
                && track.lifecycle.visible()
                && track.level.lower <= existing.level.upper + tolerance
                && track.level.upper >= existing.level.lower - tolerance
        });
        if let Some(existing) = matching {
            // Retain the older episode identity and geometry. Evidence from a
            // duplicate episode strengthens it without rewriting its past.
            let touch_count = track.level.touch_count;
            let hold_count = track.level.hold_count;
            let break_count = track.level.break_count;
            let role_flip_count = track.level.role_flip_count;
            merge_unified_candidate(existing, track.level);
            existing.level.touch_count = existing.level.touch_count.max(touch_count);
            existing.level.hold_count = existing.level.hold_count.max(hold_count);
            existing.level.break_count = existing.level.break_count.max(break_count);
            existing.level.role_flip_count = existing.level.role_flip_count.max(role_flip_count);
            if !matches!(track.lifecycle, LevelLifecycle::Active) {
                existing.lifecycle = track.lifecycle;
                existing.level.lifecycle = existing.lifecycle.label().to_string();
                existing.level.pending_side = match existing.lifecycle {
                    LevelLifecycle::AwaitingRetest { direction, .. }
                    | LevelLifecycle::RetestContact { direction, .. } => direction,
                    _ => 0,
                };
            }
            refresh_unified_track_evidence(existing);
        } else {
            consolidated.push(track);
        }
    }
    *tracks = consolidated;
}

fn prune_unified_tracks(tracks: &mut Vec<UnifiedLevelTrack>, reference: f64) {
    if tracks.len() <= MAX_UNIFIED_TRACKS {
        return;
    }
    tracks.sort_by(|left, right| {
        let left_visible = left.lifecycle.visible();
        let right_visible = right.lifecycle.visible();
        let left_pending = matches!(
            left.lifecycle,
            LevelLifecycle::AwaitingRetest { .. } | LevelLifecycle::RetestContact { .. }
        );
        let right_pending = matches!(
            right.lifecycle,
            LevelLifecycle::AwaitingRetest { .. } | LevelLifecycle::RetestContact { .. }
        );
        left_visible
            .cmp(&right_visible)
            .then_with(|| left_pending.cmp(&right_pending))
            .then_with(|| {
                left.level
                    .hold_probability
                    .total_cmp(&right.level.hold_probability)
            })
            .then_with(|| {
                left.level
                    .independent_pivot_count
                    .cmp(&right.level.independent_pivot_count)
            })
            .then_with(|| left.level.role_flip_count.cmp(&right.level.role_flip_count))
            .then_with(|| {
                (right.level.price - reference)
                    .abs()
                    .total_cmp(&(left.level.price - reference).abs())
            })
            .then_with(|| left.level.last_test_at_ms.cmp(&right.level.last_test_at_ms))
    });
    tracks.drain(0..tracks.len() - MAX_UNIFIED_TRACKS);
}

fn observe_timeframe_structure(
    sym: &str,
    state: &mut TimeframeState,
    ts: DateTime<Utc>,
    price: f64,
    size: f64,
    aggressor: i8,
) -> Vec<GenericStructureEvent> {
    if state.horizon_ms <= 0 {
        state.horizon_ms = STRUCTURE_TIMEFRAMES
            .iter()
            .find(|(timeframe, _)| *timeframe == state.timeframe)
            .map(|(_, horizon_ms)| *horizon_ms)
            .unwrap_or(100);
    }
    let bucket_start = structure_bucket_start_ms(&state.timeframe, state.horizon_ms, ts);
    let mut emitted = Vec::new();
    match state.current_bucket.as_mut() {
        Some(bucket) if bucket.start_ms == bucket_start => {
            bucket.observe(ts, price, size, aggressor);
        }
        Some(_) => {
            let completed = state.current_bucket.take().expect("checked current bucket");
            // A local neighborhood must be local in event time, not merely the
            // last three buckets that happened to contain trades. Reset after
            // a material observation gap so a pre-gap extreme cannot be
            // confirmed minutes later by an unrelated print.
            if bucket_start - completed.start_ms > state.horizon_ms.saturating_mul(3) {
                state.completed_buckets.clear();
            }
            state.completed_buckets.push_back(completed);
            if state.completed_buckets.len() >= 3 {
                let left = state.completed_buckets[0].clone();
                let center = state.completed_buckets[1].clone();
                let right = state.completed_buckets[2].clone();
                let tick = price_tick(center.high.max(center.low));
                let mut swings = Vec::new();
                // The last bar in a flat plateau owns the pivot. This avoids
                // publishing the same traded price once per bucket.
                if center.high >= left.high && center.high > right.high {
                    let prominence_ticks =
                        ((center.high - left.high.max(right.high)) / tick).max(0.0);
                    swings.push((
                        -1,
                        center.high,
                        center.high_at.unwrap_or(ts),
                        local_swing_confidence(prominence_ticks),
                    ));
                }
                if center.low <= left.low && center.low < right.low {
                    let prominence_ticks = ((left.low.min(right.low) - center.low) / tick).max(0.0);
                    swings.push((
                        1,
                        center.low,
                        center.low_at.unwrap_or(ts),
                        local_swing_confidence(prominence_ticks),
                    ));
                }
                swings.sort_by_key(|(_, _, pivot_at, _)| *pivot_at);
                for (side, swing_price, pivot_at, confidence) in swings {
                    emitted.push(install_timeframe_swing(
                        sym,
                        state,
                        side,
                        swing_price,
                        pivot_at,
                        ts,
                        confidence,
                        &center,
                    ));
                }
                state.completed_buckets.pop_front();
            }
            state.current_bucket = Some(TimeframeBucket::new(
                bucket_start,
                ts,
                price,
                size,
                aggressor,
            ));
        }
        None => {
            state.current_bucket = Some(TimeframeBucket::new(
                bucket_start,
                ts,
                price,
                size,
                aggressor,
            ));
        }
    }
    emitted.extend(advance_timeframe_swing_break(
        sym,
        &state.timeframe,
        &mut state.direction,
        &mut state.active_high,
        ts,
        price,
    ));
    emitted.extend(advance_timeframe_swing_break(
        sym,
        &state.timeframe,
        &mut state.direction,
        &mut state.active_low,
        ts,
        price,
    ));
    emitted
}

fn structure_bucket_start_ms(timeframe: &str, horizon_ms: i64, ts: DateTime<Utc>) -> i64 {
    if !matches!(timeframe, "1d" | "1w") {
        return ts.timestamp_millis().div_euclid(horizon_ms) * horizon_ms;
    }
    let local = ts.with_timezone(&New_York);
    let mut session_date = local.date_naive();
    if local.time().num_seconds_from_midnight() < SESSION_ANCHOR_SECONDS {
        session_date = session_date.pred_opt().unwrap_or(session_date);
    }
    if timeframe == "1w" {
        session_date -=
            chrono::Duration::days(session_date.weekday().num_days_from_monday() as i64);
    }
    New_York
        .with_ymd_and_hms(
            session_date.year(),
            session_date.month(),
            session_date.day(),
            4,
            0,
            0,
        )
        .single()
        .map(|value| value.with_timezone(&Utc).timestamp_millis())
        .unwrap_or_else(|| ts.timestamp_millis().div_euclid(horizon_ms) * horizon_ms)
}

fn seed_timeframe_swing(state: &mut TimeframeState, event: &GenericStructureEvent) {
    if state.horizon_ms <= 0 {
        state.horizon_ms = STRUCTURE_TIMEFRAMES
            .iter()
            .find(|(timeframe, _)| *timeframe == state.timeframe)
            .map(|(_, horizon_ms)| *horizon_ms)
            .unwrap_or(100);
    }
    let swing = TimeframeSwing {
        level_id: event.level_id,
        side: event.direction,
        price: event.price,
        pivot_at: Some(event.pivot_at),
        confirmed_at: Some(event.confirmed_at),
        strength: event.strength,
        confidence: event.confidence,
        total_volume: event.total_volume,
        buy_volume: event.buy_volume,
        sell_volume: event.sell_volume,
        neutral_volume: event.neutral_volume,
        trade_count: event.trade_count,
        broken: false,
        crossing: None,
    };
    if event.direction < 0 {
        state.previous_high = state.current_high;
        state.current_high = event.price;
        state.active_high = Some(swing);
    } else {
        state.previous_low = state.current_low;
        state.current_low = event.price;
        state.active_low = Some(swing);
    }
    state.promoted_level_count = state.promoted_level_count.saturating_add(1);
}

fn local_swing_confidence(prominence_ticks: f64) -> f64 {
    (0.55 + 0.075 * prominence_ticks.min(6.0)).clamp(0.0, 1.0)
}

fn install_timeframe_swing(
    sym: &str,
    state: &mut TimeframeState,
    side: i8,
    price: f64,
    pivot_at: DateTime<Utc>,
    confirmed_at: DateTime<Utc>,
    confidence: f64,
    bucket: &TimeframeBucket,
) -> GenericStructureEvent {
    let swing = TimeframeSwing {
        level_id: stable_timeframe_level_id(sym, &state.timeframe, side, price, pivot_at),
        side,
        price,
        pivot_at: Some(pivot_at),
        confirmed_at: Some(confirmed_at),
        strength: confidence,
        confidence,
        total_volume: bucket.total_volume,
        buy_volume: bucket.buy_volume,
        sell_volume: bucket.sell_volume,
        neutral_volume: bucket.neutral_volume,
        trade_count: bucket.trade_count,
        broken: false,
        crossing: None,
    };
    if side < 0 {
        state.previous_high = state.current_high;
        state.current_high = price;
        state.active_high = Some(swing.clone());
    } else {
        state.previous_low = state.current_low;
        state.current_low = price;
        state.active_low = Some(swing.clone());
    }
    state.promoted_level_count = state.promoted_level_count.saturating_add(1);
    timeframe_swing_event(
        sym,
        &state.timeframe,
        &swing,
        "level_promoted",
        side,
        confirmed_at,
    )
}

fn advance_timeframe_swing_break(
    sym: &str,
    timeframe: &str,
    structure_direction: &mut i8,
    swing: &mut Option<TimeframeSwing>,
    ts: DateTime<Utc>,
    price: f64,
) -> Vec<GenericStructureEvent> {
    let Some(swing) = swing.as_mut() else {
        return Vec::new();
    };
    if swing.broken {
        return Vec::new();
    }
    let break_direction = -swing.side;
    let beyond = (break_direction > 0 && price > swing.price)
        || (break_direction < 0 && price < swing.price);
    let mut emitted = Vec::new();
    match swing.crossing.as_mut() {
        None if beyond => {
            swing.crossing = Some(TimeframeCrossing {
                direction: break_direction,
                first_crossed_at: Some(ts),
                beyond_trades: 1,
            });
            emitted.push(timeframe_swing_event(
                sym,
                timeframe,
                swing,
                "structure_crossed",
                break_direction,
                ts,
            ));
        }
        Some(crossing) if beyond => {
            crossing.beyond_trades = crossing.beyond_trades.saturating_add(1);
            let persisted_ms = crossing
                .first_crossed_at
                .map(|value| (ts - value).num_milliseconds().max(0))
                .unwrap_or_default();
            if crossing.beyond_trades >= 2 || persisted_ms >= 100 {
                let kind = if *structure_direction == 0 {
                    "structure_break"
                } else if *structure_direction == break_direction {
                    "bos"
                } else {
                    "choch"
                };
                swing.broken = true;
                swing.crossing = None;
                *structure_direction = break_direction;
                emitted.push(timeframe_swing_event(
                    sym,
                    timeframe,
                    swing,
                    kind,
                    break_direction,
                    ts,
                ));
            }
        }
        Some(_) if !beyond => {
            swing.crossing = None;
        }
        _ => {}
    }
    emitted
}

fn timeframe_swing_event(
    sym: &str,
    timeframe: &str,
    swing: &TimeframeSwing,
    kind: &str,
    direction: i8,
    confirmed_at: DateTime<Utc>,
) -> GenericStructureEvent {
    let pivot_at = swing.pivot_at.unwrap_or(confirmed_at);
    let tick = price_tick(swing.price);
    GenericStructureEvent {
        algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
        event_id: stable_event_id(
            sym,
            swing.level_id,
            timeframe,
            kind,
            direction,
            confirmed_at,
        ),
        level_id: swing.level_id,
        sym: sym.to_string(),
        timeframe: timeframe.to_string(),
        event_kind: kind.to_string(),
        direction,
        price: swing.price,
        lower: swing.price - tick,
        upper: swing.price + tick,
        strength: swing.strength,
        confidence: swing.confidence,
        lifecycle: if swing.broken { "broken" } else { "active" }.to_string(),
        total_volume: swing.total_volume,
        buy_volume: swing.buy_volume,
        sell_volume: swing.sell_volume,
        neutral_volume: swing.neutral_volume,
        trade_count: swing.trade_count,
        pivot_at,
        confirmed_at,
    }
}

fn update_volume_bin<T: VolumeBinMut>(bin: &mut T, size: f64, aggressor: i8) {
    bin.add_total(size);
    if aggressor > 0 {
        bin.add_buy(size);
    } else if aggressor < 0 {
        bin.add_sell(size);
    } else {
        bin.add_neutral(size);
    }
    bin.add_trade(size);
}

trait VolumeBinMut {
    fn add_total(&mut self, size: f64);
    fn add_buy(&mut self, size: f64);
    fn add_sell(&mut self, size: f64);
    fn add_neutral(&mut self, size: f64);
    fn add_trade(&mut self, size: f64);
}

macro_rules! impl_volume_bin {
    ($type:ty) => {
        impl VolumeBinMut for $type {
            fn add_total(&mut self, size: f64) {
                self.total_volume += size;
            }
            fn add_buy(&mut self, size: f64) {
                self.buy_volume += size;
            }
            fn add_sell(&mut self, size: f64) {
                self.sell_volume += size;
            }
            fn add_neutral(&mut self, size: f64) {
                self.neutral_volume += size;
            }
            fn add_trade(&mut self, size: f64) {
                self.trade_count = self.trade_count.saturating_add(1);
                self.largest_trade = self.largest_trade.max(size);
            }
        }
    };
}

impl_volume_bin!(FootprintBin);
impl_volume_bin!(PriceVolumeBin);

fn exposed_active_levels(
    levels: &[StructureLevel],
    session_volume: &HashMap<i64, PriceVolumeBin>,
    reference: f64,
    footprint_session_date: Option<NaiveDate>,
    footprint_as_of: DateTime<Utc>,
) -> Vec<StructureLevelCandidate> {
    let mut supports = levels
        .iter()
        .filter(|level| level.lifecycle.visible() && level.side > 0 && level.price < reference)
        .collect::<Vec<_>>();
    let mut resistances = levels
        .iter()
        .filter(|level| level.lifecycle.visible() && level.side < 0 && level.price > reference)
        .collect::<Vec<_>>();
    supports.sort_by(|left, right| (reference - left.price).total_cmp(&(reference - right.price)));
    resistances
        .sort_by(|left, right| (left.price - reference).total_cmp(&(right.price - reference)));
    supports
        .into_iter()
        .take(MAX_EXPOSED_LEVELS_PER_SIDE)
        .chain(resistances.into_iter().take(MAX_EXPOSED_LEVELS_PER_SIDE))
        .map(|level| {
            level_candidate(
                level,
                session_volume,
                reference,
                footprint_session_date,
                footprint_as_of,
            )
        })
        .collect()
}

fn level_candidate(
    level: &StructureLevel,
    session_volume: &HashMap<i64, PriceVolumeBin>,
    reference: f64,
    footprint_session_date: Option<NaiveDate>,
    footprint_as_of: DateTime<Utc>,
) -> StructureLevelCandidate {
    let (strength, confidence) = level_evidence(level);
    let footprint = footprint_snapshot(level, session_volume);
    let totals = footprint_totals(&footprint);
    StructureLevelCandidate {
        level_id: level.level_id,
        side: level.side,
        price: level.price,
        lower: level.lower,
        upper: level.upper,
        strength,
        confidence,
        evidence_score: strength * confidence,
        distance: (level.price - reference).abs(),
        touch_count: level.touch_count,
        hold_count: level.hold_count,
        created_at_ms: level.confirmed_at.timestamp_millis(),
        last_test_at_ms: level.last_test_at.timestamp_millis(),
        lifecycle: level.lifecycle.label().to_string(),
        promotions: level
            .promotions
            .iter()
            .map(|promotion| StructurePromotionSnapshot {
                timeframe: promotion.timeframe.clone(),
                promoted_at_ms: promotion.promoted_at.timestamp_millis(),
                score: promotion.score,
            })
            .collect(),
        footprint_session_date: footprint_session_date
            .map(|date| date.to_string())
            .unwrap_or_default(),
        footprint_as_of_ms: footprint_as_of.timestamp_millis(),
        footprint,
        total_volume: totals.0,
        buy_volume: totals.1,
        sell_volume: totals.2,
        neutral_volume: totals.3,
        trade_count: totals.4,
    }
}

fn footprint_snapshot(
    level: &StructureLevel,
    session_volume: &HashMap<i64, PriceVolumeBin>,
) -> Vec<StructureFootprintBin> {
    let tick = price_tick(level.price);
    (-FOOTPRINT_RADIUS_TICKS..=FOOTPRINT_RADIUS_TICKS)
        .map(|offset| {
            let price = level.price + offset as f64 * tick;
            let level_bin = level.footprint.get(&offset).cloned().unwrap_or_default();
            let session_bin = session_volume
                .get(&price_key(price))
                .cloned()
                .unwrap_or_default();
            StructureFootprintBin {
                offset_ticks: offset,
                price,
                // The public footprint is the complete session volume at and around
                // the level. Level-local observations still contribute to strength,
                // but exposing only post-creation volume would understate a level
                // that was formed after substantial trading had already occurred.
                total_volume: session_bin.total_volume,
                buy_volume: session_bin.buy_volume,
                sell_volume: session_bin.sell_volume,
                neutral_volume: session_bin.neutral_volume,
                trade_count: session_bin.trade_count,
                largest_trade: level_bin.largest_trade.max(session_bin.largest_trade),
            }
        })
        .collect()
}

fn footprint_totals(bins: &[StructureFootprintBin]) -> (f64, f64, f64, f64, u64) {
    bins.iter().fold(
        (0.0, 0.0, 0.0, 0.0, 0_u64),
        |(total, buy, sell, neutral, count), bin| {
            (
                total + bin.total_volume,
                buy + bin.buy_volume,
                sell + bin.sell_volume,
                neutral + bin.neutral_volume,
                count.saturating_add(bin.trade_count),
            )
        },
    )
}

fn level_evidence(level: &StructureLevel) -> (f64, f64) {
    let trade_count = level
        .footprint
        .values()
        .map(|bin| bin.trade_count)
        .sum::<u64>();
    let strength = (0.18
        + level.touch_count.min(6) as f64 * 0.08
        + level.hold_count.min(5) as f64 * 0.10
        + level.promotions.len().min(8) as f64 * 0.045
        + level.role_flip_count.min(3) as f64 * 0.055
        + (trade_count as f64).ln_1p().min(8.0) * 0.025
        - level.accepted_break_count.min(3) as f64 * 0.08)
        .clamp(0.0, 1.0);
    let confidence = (0.20
        + level.promotions.len().min(8) as f64 * 0.075
        + level.touch_count.min(5) as f64 * 0.04
        + level.hold_count.min(5) as f64 * 0.05
        + level.role_flip_count.min(3) as f64 * 0.04)
        .clamp(0.0, 1.0);
    (strength, confidence)
}

fn candidate_to_snapshot(level: &StructureLevelCandidate) -> StructureLevelSnapshot {
    StructureLevelSnapshot {
        level_id: level.level_id,
        price: level.price,
        lower: level.lower,
        upper: level.upper,
        strength: level.strength,
        confidence: level.confidence,
        touch_count: level.touch_count,
        hold_count: level.hold_count,
        created_at_ms: level.created_at_ms,
        last_test_at_ms: level.last_test_at_ms,
        lifecycle: level.lifecycle.clone(),
        promotions: level.promotions.clone(),
        footprint: level.footprint.clone(),
        total_volume: level.total_volume,
        buy_volume: level.buy_volume,
        sell_volume: level.sell_volume,
        neutral_volume: level.neutral_volume,
        trade_count: level.trade_count,
    }
}

fn candidate_to_level(candidate: &StructureLevelCandidate) -> StructureLevel {
    StructureLevel {
        level_id: candidate.level_id,
        side: candidate.side,
        price: candidate.price,
        lower: candidate.lower,
        upper: candidate.upper,
        pivot_at: DateTime::<Utc>::from_timestamp_millis(candidate.created_at_ms)
            .unwrap_or_else(Utc::now),
        confirmed_at: DateTime::<Utc>::from_timestamp_millis(candidate.created_at_ms)
            .unwrap_or_else(Utc::now),
        last_test_at: DateTime::<Utc>::from_timestamp_millis(candidate.last_test_at_ms)
            .unwrap_or_else(Utc::now),
        touch_count: candidate.touch_count,
        hold_count: candidate.hold_count,
        break_count: 0,
        accepted_break_count: 0,
        role_flip_count: 0,
        lifecycle: LevelLifecycle::Active,
        promotions: candidate
            .promotions
            .iter()
            .filter_map(|promotion| {
                Some(Promotion {
                    timeframe: promotion.timeframe.clone(),
                    promoted_at: DateTime::<Utc>::from_timestamp_millis(promotion.promoted_at_ms)?,
                    score: promotion.score,
                })
            })
            .collect(),
        footprint: candidate
            .footprint
            .iter()
            .map(|bin| {
                (
                    bin.offset_ticks,
                    FootprintBin {
                        total_volume: bin.total_volume,
                        buy_volume: bin.buy_volume,
                        sell_volume: bin.sell_volume,
                        neutral_volume: bin.neutral_volume,
                        trade_count: bin.trade_count,
                        largest_trade: bin.largest_trade,
                    },
                )
            })
            .collect(),
    }
}

fn timeframe_snapshot(
    state: &TimeframeState,
    _levels: &[StructureLevelCandidate],
) -> StructureTimeframeSnapshot {
    let support = state
        .active_low
        .as_ref()
        .filter(|swing| !swing.broken)
        .map(timeframe_swing_snapshot)
        .unwrap_or_default();
    let resistance = state
        .active_high
        .as_ref()
        .filter(|swing| !swing.broken)
        .map(timeframe_swing_snapshot)
        .unwrap_or_default();
    StructureTimeframeSnapshot {
        timeframe: state.timeframe.clone(),
        direction: state.direction,
        swing_high: state.current_high,
        swing_low: state.current_low,
        support,
        resistance,
        promoted_level_count: state.promoted_level_count,
    }
}

fn timeframe_swing_snapshot(swing: &TimeframeSwing) -> StructureLevelSnapshot {
    let tick = price_tick(swing.price);
    StructureLevelSnapshot {
        level_id: swing.level_id,
        price: swing.price,
        lower: swing.price - tick,
        upper: swing.price + tick,
        strength: swing.strength,
        confidence: swing.confidence,
        touch_count: 1,
        hold_count: 0,
        created_at_ms: swing
            .pivot_at
            .map(|value| value.timestamp_millis())
            .unwrap_or_default(),
        last_test_at_ms: swing
            .confirmed_at
            .map(|value| value.timestamp_millis())
            .unwrap_or_default(),
        lifecycle: if swing.broken { "broken" } else { "active" }.to_string(),
        promotions: Vec::new(),
        footprint: Vec::new(),
        total_volume: swing.total_volume,
        buy_volume: swing.buy_volume,
        sell_volume: swing.sell_volume,
        neutral_volume: swing.neutral_volume,
        trade_count: swing.trade_count,
    }
}

fn level_field<'a>(
    levels: impl Iterator<Item = &'a StructureLevelCandidate>,
    reference: f64,
) -> f64 {
    let tick = price_tick(reference);
    levels
        .map(|level| {
            level.evidence_score / (1.0 + level.distance / (tick * 20.0).max(reference * 0.001))
        })
        .sum::<f64>()
        .clamp(0.0, 1.0)
}

fn level_event(
    sym: &str,
    level: &StructureLevel,
    timeframe: &str,
    kind: &str,
    direction: i8,
    confirmed_at: DateTime<Utc>,
) -> GenericStructureEvent {
    let (strength, confidence) = level_evidence(level);
    let totals = level.footprint.values().fold(
        (0.0, 0.0, 0.0, 0.0, 0_u64),
        |(total, buy, sell, neutral, count), bin| {
            (
                total + bin.total_volume,
                buy + bin.buy_volume,
                sell + bin.sell_volume,
                neutral + bin.neutral_volume,
                count.saturating_add(bin.trade_count),
            )
        },
    );
    GenericStructureEvent {
        algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
        event_id: stable_event_id(
            sym,
            level.level_id,
            timeframe,
            kind,
            direction,
            confirmed_at,
        ),
        level_id: level.level_id,
        sym: sym.to_string(),
        timeframe: timeframe.to_string(),
        event_kind: kind.to_string(),
        direction,
        price: level.price,
        lower: level.lower,
        upper: level.upper,
        strength,
        confidence,
        lifecycle: level.lifecycle.label().to_string(),
        total_volume: totals.0,
        buy_volume: totals.1,
        sell_volume: totals.2,
        neutral_volume: totals.3,
        trade_count: totals.4,
        pivot_at: level.pivot_at,
        confirmed_at,
    }
}

fn stable_level_id(sym: &str, side: i8, price: f64, pivot_at: DateTime<Utc>) -> u64 {
    stable_hash(&format!(
        "{sym}|level|{side}|{}|{}",
        price_key(price),
        pivot_at.timestamp_micros()
    ))
}

fn stable_timeframe_level_id(
    sym: &str,
    timeframe: &str,
    side: i8,
    price: f64,
    pivot_at: DateTime<Utc>,
) -> u64 {
    stable_hash(&format!(
        "{sym}|local-swing|{timeframe}|{side}|{}|{}",
        price_key(price),
        pivot_at.timestamp_micros()
    ))
}

fn stable_event_id(
    sym: &str,
    level_id: u64,
    timeframe: &str,
    kind: &str,
    direction: i8,
    ts: DateTime<Utc>,
) -> u64 {
    stable_hash(&format!(
        "{sym}|{level_id}|{timeframe}|{kind}|{direction}|{}",
        ts.timestamp_micros()
    ))
}

fn stable_hash(payload: &str) -> u64 {
    payload
        .as_bytes()
        .iter()
        .fold(1_469_598_103_934_665_603_u64, |hash, byte| {
            (hash ^ u64::from(*byte)).wrapping_mul(1_099_511_628_211)
        })
}

fn price_tick(price: f64) -> f64 {
    if price < 1.0 {
        0.0001
    } else {
        0.01
    }
}

fn ewma(previous: f64, observation: f64, alpha: f64) -> f64 {
    if !observation.is_finite() || observation < 0.0 {
        return previous.max(0.0);
    }
    if previous <= 0.0 || !previous.is_finite() {
        observation
    } else {
        previous * (1.0 - alpha) + observation * alpha
    }
}

fn price_key(price: f64) -> i64 {
    (price * 10_000.0).round() as i64
}

fn price_from_key(key: i64) -> f64 {
    key as f64 / 10_000.0
}

fn positive_min(current: f64, candidate: f64) -> f64 {
    if current > 0.0 {
        current.min(candidate)
    } else {
        candidate
    }
}

fn nearest_round_price(price: f64) -> f64 {
    if price <= 0.0 {
        return 0.0;
    }
    let increment = if price < 1.0 {
        0.05
    } else if price < 10.0 {
        0.25
    } else if price < 100.0 {
        1.0
    } else {
        5.0
    };
    (price / increment).round() * increment
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::event::{QuoteEvent, TradeEvent};
    use chrono::TimeZone;
    use serde_json::json;

    fn trade(ms: i64, price: f64, size: f64, sequence: u64) -> MarketEvent {
        MarketEvent::Trade(TradeEvent {
            conditions: Vec::new(),
            exchange: 1,
            ingest_ts: Utc.timestamp_millis_opt(ms).unwrap(),
            participant_ts: None,
            price,
            raw: json!({"arrival_sequence": sequence}),
            sequence,
            size,
            tape: 3,
            ticker: "TEST".to_string(),
            trade_id: sequence.to_string(),
            trf_id: 0,
            trf_ts: None,
            ts: Utc.timestamp_millis_opt(ms).unwrap(),
        })
    }

    fn quote(ms: i64, bid: f64, ask: f64, sequence: u64) -> MarketEvent {
        MarketEvent::Quote(QuoteEvent {
            ask_exchange: 1,
            ask_price: ask,
            ask_size: 100,
            bid_exchange: 1,
            bid_price: bid,
            bid_size: 100,
            conditions: Vec::new(),
            indicators: Vec::new(),
            ingest_ts: Utc.timestamp_millis_opt(ms).unwrap(),
            raw: json!({"arrival_sequence": sequence}),
            sequence,
            tape: 3,
            ticker: "TEST".to_string(),
            ts: Utc.timestamp_millis_opt(ms).unwrap(),
        })
    }

    fn new_york_ms(year: i32, month: u32, day: u32, hour: u32, minute: u32, second: u32) -> i64 {
        New_York
            .with_ymd_and_hms(year, month, day, hour, minute, second)
            .unwrap()
            .with_timezone(&Utc)
            .timestamp_millis()
    }

    fn swing(
        level_id: u64,
        side: i8,
        price: f64,
        pivot_at_ms: i64,
        confirmed_at_ms: i64,
        broken: bool,
    ) -> TimeframeSwing {
        TimeframeSwing {
            level_id,
            side,
            price,
            pivot_at: Some(Utc.timestamp_millis_opt(pivot_at_ms).unwrap()),
            confirmed_at: Some(Utc.timestamp_millis_opt(confirmed_at_ms).unwrap()),
            strength: 0.8,
            confidence: 0.75,
            total_volume: 1_000.0,
            trade_count: 10,
            broken,
            ..TimeframeSwing::default()
        }
    }

    fn unified_test_level(id: u64, side: i8, lower: f64, upper: f64) -> UnifiedStructureLevel {
        UnifiedStructureLevel {
            unified_level_id: id,
            side,
            price: (lower + upper) * 0.5,
            lower,
            upper,
            source_count: 1,
            independent_pivot_count: 1,
            timeframes: vec!["1s".to_string()],
            created_at_ms: 1_700_000_000_000,
            confirmed_at_ms: 1_700_000_001_000,
            total_volume: 1_000.0,
            buy_volume: 600.0,
            sell_volume: 300.0,
            neutral_volume: 100.0,
            trade_count: 10,
            pressure_bias: 1.0 / 3.0,
            hold_probability: 0.5,
            break_probability: 0.5,
            hold_rate: 0.0,
            hold_observation_count: 0,
            hold_evidence_reliability: 0.0,
            hold_quality_score: 0.0,
            hold_score_revision: String::new(),
            touch_count: 1,
            hold_count: 0,
            break_count: 0,
            role_flip_count: 0,
            last_test_at_ms: 1_700_000_001_000,
            lifecycle: "active".to_string(),
            pending_side: 0,
            sources: Vec::new(),
        }
    }

    fn event_native_level(id: u64, side: i8, price: f64, pivot_at_ms: i64) -> StructureLevel {
        let tick = price_tick(price);
        StructureLevel {
            level_id: id,
            side,
            price,
            lower: price - tick,
            upper: price + tick,
            pivot_at: Utc.timestamp_millis_opt(pivot_at_ms).unwrap(),
            confirmed_at: Utc.timestamp_millis_opt(pivot_at_ms + 100).unwrap(),
            last_test_at: Utc.timestamp_millis_opt(pivot_at_ms + 100).unwrap(),
            touch_count: 1,
            hold_count: 0,
            break_count: 0,
            accepted_break_count: 0,
            role_flip_count: 0,
            lifecycle: LevelLifecycle::Active,
            promotions: Vec::new(),
            footprint: BTreeMap::new(),
        }
    }

    #[test]
    fn unified_episode_merges_new_evidence_without_moving_or_reidentifying_the_zone() {
        let original = unified_test_level(41, 1, 9.99, 10.01);
        let mut track = UnifiedLevelTrack {
            level: original.clone(),
            lifecycle: LevelLifecycle::Active,
            last_relation: 1,
        };
        let mut reinforcement = unified_test_level(99, 1, 9.98, 10.02);
        reinforcement.touch_count = 900;
        reinforcement.hold_count = 800;
        reinforcement.break_count = 700;

        merge_unified_candidate(&mut track, reinforcement);

        assert_eq!(track.level.unified_level_id, original.unified_level_id);
        assert_eq!(track.level.lower, original.lower);
        assert_eq!(track.level.upper, original.upper);
        assert_eq!(track.level.touch_count, original.touch_count);
        assert_eq!(track.level.hold_count, original.hold_count);
        assert_eq!(track.level.break_count, original.break_count);
    }

    #[test]
    fn base_retest_uses_the_levels_tick_regime_across_one_dollar() {
        let mut engine = GenericStructureEngine::new("TEST");
        let at = Utc.timestamp_millis_opt(1_700_000_000_000).unwrap();
        let mut level = event_native_level(7, -1, 0.9999, at.timestamp_millis());
        level.lifecycle = LevelLifecycle::AwaitingRetest {
            direction: 1,
            accepted_at: at,
        };
        engine.levels.push(level);

        engine.update_level_lifecycles(
            at + chrono::Duration::milliseconds(1),
            1.005,
            100.0,
            &mut Vec::new(),
        );

        assert!(matches!(
            engine.levels[0].lifecycle,
            LevelLifecycle::AwaitingRetest { .. }
        ));
    }

    #[test]
    fn unified_episode_inherits_event_native_hold_history_once() {
        let start = 1_700_000_000_000;
        let mut engine = GenericStructureEngine::new("TEST");
        let mut level = event_native_level(77, 1, 10.0, start);
        level.touch_count = 7;
        level.hold_count = 6;
        engine.levels.push(level);

        engine.refresh_unified_level_tracks(Utc.timestamp_millis_opt(start + 1_000).unwrap(), 10.2);

        assert_eq!(engine.unified_tracks.len(), 1);
        assert_eq!(engine.unified_tracks[0].level.touch_count, 7);
        assert_eq!(engine.unified_tracks[0].level.hold_count, 6);
        assert_eq!(engine.unified_tracks[0].level.break_count, 0);
        assert!((engine.unified_tracks[0].level.hold_probability - 0.8).abs() < 1e-12);
        assert_eq!(engine.unified_tracks[0].level.hold_observation_count, 6);
        assert_eq!(engine.unified_tracks[0].level.hold_rate, 1.0);
        assert_eq!(
            engine.unified_tracks[0].level.hold_score_revision,
            STRUCTURE_HOLD_SCORE_REVISION
        );
        assert!(engine.unified_tracks[0].level.hold_quality_score < 0.8);
        assert!(engine.unified_tracks[0].level.hold_quality_score > 0.5);
        let serialized = serde_json::to_value(&engine.unified_tracks[0].level).unwrap();
        for removed in [
            "salience",
            "confidence",
            "reaction_probability",
            "reversal_probability",
        ] {
            assert!(serialized.get(removed).is_none());
        }
    }

    #[test]
    fn unified_episode_survives_retest_and_closes_only_after_accepted_break() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.unified_tracks.push(UnifiedLevelTrack {
            level: unified_test_level(41, 1, 9.99, 10.01),
            lifecycle: LevelLifecycle::Active,
            last_relation: 1,
        });
        let start = Utc.timestamp_millis_opt(1_700_000_010_000).unwrap();

        engine.update_unified_level_lifecycles(start, 10.0, 100.0);
        engine.update_unified_level_lifecycles(
            start + chrono::Duration::milliseconds(1),
            9.98,
            100.0,
        );
        engine.update_unified_level_lifecycles(
            start + chrono::Duration::milliseconds(2),
            10.0,
            100.0,
        );
        assert!(matches!(
            engine.unified_tracks[0].lifecycle,
            LevelLifecycle::Active
        ));
        assert_eq!(engine.unified_tracks[0].level.unified_level_id, 41);
        assert_eq!(engine.unified_tracks[0].level.break_count, 0);

        engine.update_unified_level_lifecycles(
            start + chrono::Duration::milliseconds(3),
            9.97,
            100.0,
        );
        assert!(matches!(
            engine.unified_tracks[0].lifecycle,
            LevelLifecycle::Crossed { .. }
        ));
        engine.update_unified_level_lifecycles(
            start + chrono::Duration::milliseconds(4),
            9.96,
            100.0,
        );
        assert!(matches!(
            engine.unified_tracks[0].lifecycle,
            LevelLifecycle::AwaitingRetest { .. }
        ));
        assert_eq!(engine.unified_tracks[0].level.break_count, 1);
    }

    #[test]
    fn noisy_two_print_penetration_does_not_accept_a_structural_break() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.rolling_abs_trade_move = 0.02;
        engine.rolling_spread = 0.02;
        engine.rolling_trade_size = 100.0;
        engine.unified_tracks.push(UnifiedLevelTrack {
            level: unified_test_level(41, 1, 9.99, 10.01),
            lifecycle: LevelLifecycle::Active,
            last_relation: 1,
        });
        let start = Utc.timestamp_millis_opt(1_700_000_010_000).unwrap();

        engine.update_unified_level_lifecycles(start, 9.98, 10.0);
        engine.update_unified_level_lifecycles(
            start + chrono::Duration::milliseconds(150),
            9.97,
            10.0,
        );

        assert!(matches!(
            engine.unified_tracks[0].lifecycle,
            LevelLifecycle::Crossed { .. }
        ));
        assert_eq!(engine.unified_tracks[0].level.break_count, 0);
    }

    #[test]
    fn accepted_break_stays_published_while_awaiting_retest() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.unified_tracks.push(UnifiedLevelTrack {
            level: unified_test_level(41, 1, 9.99, 10.01),
            lifecycle: LevelLifecycle::AwaitingRetest {
                direction: -1,
                accepted_at: Utc.timestamp_millis_opt(1_700_000_010_000).unwrap(),
            },
            last_relation: -1,
        });
        engine.unified_tracks[0].level.lifecycle = "awaiting_retest".to_string();
        engine.unified_tracks[0].level.pending_side = -1;

        let snapshot = engine.snapshot(Utc.timestamp_millis_opt(1_700_000_011_000).unwrap());

        assert_eq!(snapshot.unified_levels.len(), 1);
        assert_eq!(snapshot.unified_levels[0].lifecycle, "awaiting_retest");
        assert_eq!(snapshot.unified_levels[0].pending_side, -1);
    }

    #[test]
    fn pending_role_episode_absorbs_duplicate_opposite_role_geometry() {
        let accepted_at = Utc.timestamp_millis_opt(1_700_000_010_000).unwrap();
        let mut pending = unified_test_level(41, 1, 9.99, 10.01);
        pending.lifecycle = "awaiting_retest".to_string();
        pending.pending_side = -1;
        let resistance = unified_test_level(42, -1, 9.99, 10.01);
        let mut tracks = vec![
            UnifiedLevelTrack {
                level: pending,
                lifecycle: LevelLifecycle::AwaitingRetest {
                    direction: -1,
                    accepted_at,
                },
                last_relation: -1,
            },
            UnifiedLevelTrack {
                level: resistance,
                lifecycle: LevelLifecycle::Active,
                last_relation: -1,
            },
        ];

        consolidate_unified_tracks(&mut tracks);

        assert_eq!(tracks.len(), 1);
        assert_eq!(tracks[0].level.unified_level_id, 41);
        assert_eq!(tracks[0].level.pending_side, -1);
    }

    #[test]
    fn daily_and_weekly_structure_buckets_follow_the_four_et_session_anchor() {
        let before_anchor = New_York
            .with_ymd_and_hms(2026, 8, 24, 3, 59, 59)
            .single()
            .unwrap()
            .with_timezone(&Utc);
        let at_anchor = New_York
            .with_ymd_and_hms(2026, 8, 24, 4, 0, 0)
            .single()
            .unwrap()
            .with_timezone(&Utc);

        assert_eq!(
            structure_bucket_start_ms("1d", 86_400_000, before_anchor),
            New_York
                .with_ymd_and_hms(2026, 8, 23, 4, 0, 0)
                .single()
                .unwrap()
                .with_timezone(&Utc)
                .timestamp_millis()
        );
        assert_eq!(
            structure_bucket_start_ms("1d", 86_400_000, at_anchor),
            at_anchor.timestamp_millis()
        );
        assert_eq!(
            structure_bucket_start_ms("1w", 604_800_000, at_anchor),
            at_anchor.timestamp_millis()
        );
    }

    #[test]
    fn timeframe_pivots_only_corroborate_an_event_native_level() {
        let pivot_at_ms = 1_700_000_000_000;
        let states = vec![
            TimeframeState {
                timeframe: "1s".to_string(),
                active_high: Some(swing(1, -1, 10.0, pivot_at_ms, pivot_at_ms + 2_000, false)),
                ..TimeframeState::default()
            },
            TimeframeState {
                timeframe: "5s".to_string(),
                active_high: Some(swing(2, -1, 10.0, pivot_at_ms, pivot_at_ms + 10_000, false)),
                ..TimeframeState::default()
            },
        ];
        assert!(unified_structure_levels("TEST", &states, &[], 10.0).is_empty());
        let mut book_level = event_native_level(77, -1, 10.0, pivot_at_ms);
        book_level.footprint.insert(
            0,
            FootprintBin {
                total_volume: 200.0,
                buy_volume: 120.0,
                sell_volume: 80.0,
                neutral_volume: 0.0,
                trade_count: 2,
                largest_trade: 120.0,
            },
        );
        let book = vec![book_level];
        let duplicated = unified_structure_levels("TEST", &states, &book, 10.0);
        let single = unified_structure_levels("TEST", &states[..1], &book, 10.0);

        assert_eq!(duplicated.len(), 1);
        assert_eq!(duplicated[0].source_count, 3);
        assert_eq!(duplicated[0].independent_pivot_count, 1);
        assert_eq!(duplicated[0].timeframes, vec!["1s", "5s", "event-native"]);
        assert_eq!(duplicated[0].confirmed_at_ms, pivot_at_ms + 10_000);
        assert_eq!(duplicated[0].total_volume, single[0].total_volume);
        assert_eq!(duplicated[0].trade_count, single[0].trade_count);
        let mut track = UnifiedLevelTrack {
            level: duplicated[0].clone(),
            lifecycle: LevelLifecycle::Active,
            last_relation: -1,
        };
        refresh_unified_track_evidence(&mut track);
        assert_eq!(track.level.total_volume, 200.0);
        assert_eq!(track.level.buy_volume, 120.0);
        assert_eq!(track.level.sell_volume, 80.0);
        assert_eq!(track.level.trade_count, 2);
    }

    #[test]
    fn role_coherent_levels_receive_candidate_capacity_before_stale_roles() {
        let start = Utc.timestamp_millis_opt(1_700_000_000_000).unwrap();
        let mut book = (0..MAX_UNIFIED_BOOK_CANDIDATES_PER_SIDE as u64)
            .map(|index| {
                let mut level = event_native_level(
                    1_000 + index,
                    -1,
                    8.0 + index as f64 * 0.01,
                    start.timestamp_millis(),
                );
                level.hold_count = 50;
                level.touch_count = 50;
                level
            })
            .collect::<Vec<_>>();
        let mut current = event_native_level(77, -1, 10.5, start.timestamp_millis() + 1_000);
        current.hold_count = 2;
        current.touch_count = 3;
        book.push(current);

        let levels = unified_structure_levels("TEST", &[], &book, 10.0);

        assert!(levels
            .iter()
            .flat_map(|level| &level.sources)
            .any(|source| source.level_id == 77));
    }

    #[test]
    fn unified_levels_cluster_nearby_independent_swings_and_ignore_broken_levels() {
        let start = 1_700_000_000_000;
        let states = vec![
            TimeframeState {
                timeframe: "1s".to_string(),
                active_high: Some(swing(1, -1, 10.000, start, start + 2_000, false)),
                active_low: Some(swing(2, 1, 9.900, start + 500, start + 2_500, false)),
                ..TimeframeState::default()
            },
            TimeframeState {
                timeframe: "5s".to_string(),
                active_high: Some(swing(3, -1, 10.015, start + 1_000, start + 11_000, false)),
                ..TimeframeState::default()
            },
            TimeframeState {
                timeframe: "10s".to_string(),
                active_high: Some(swing(4, -1, 10.200, start + 2_000, start + 22_000, true)),
                ..TimeframeState::default()
            },
        ];
        let book = vec![
            event_native_level(77, -1, 10.0, start),
            event_native_level(78, 1, 9.9, start + 500),
        ];
        let levels = unified_structure_levels("TEST", &states, &book, 10.0);

        assert_eq!(levels.len(), 2);
        let resistance = levels.iter().find(|level| level.side == -1).unwrap();
        assert_eq!(resistance.source_count, 3);
        assert_eq!(resistance.independent_pivot_count, 2);
        assert!(resistance.lower < 10.0);
        assert!(resistance.upper < 10.015);
        assert_eq!(resistance.created_at_ms, start);
        assert_eq!(resistance.confirmed_at_ms, start + 11_000);
        assert!(levels.iter().all(|level| level.price < 10.1));
    }

    #[test]
    fn unified_level_book_preserves_observed_lifecycle_evidence_and_identity_across_role_flip() {
        let start = Utc.timestamp_millis_opt(1_700_000_000_000).unwrap();
        let mut book_level = StructureLevel {
            level_id: 77,
            side: 1,
            price: 10.0,
            lower: 9.99,
            upper: 10.01,
            pivot_at: start,
            confirmed_at: start + chrono::Duration::seconds(1),
            last_test_at: start + chrono::Duration::days(3),
            touch_count: 5,
            hold_count: 4,
            break_count: 2,
            accepted_break_count: 1,
            role_flip_count: 1,
            lifecycle: LevelLifecycle::Active,
            promotions: Vec::new(),
            footprint: BTreeMap::new(),
        };
        let support = unified_structure_levels("TEST", &[], &[book_level.clone()], 10.2);

        assert_eq!(support.len(), 1);
        assert_eq!(support[0].side, 1);
        assert_eq!(support[0].touch_count, 5);
        assert_eq!(support[0].hold_count, 4);
        assert_eq!(support[0].break_count, 1);
        assert_eq!(support[0].role_flip_count, 1);
        assert!(support[0].hold_probability > 0.5);

        let mut crowded_book = (0..100_u64)
            .map(|index| {
                let mut level = book_level.clone();
                level.level_id = 1_000 + index;
                level.price = 8.0 + index as f64 * 0.02;
                level.lower = level.price - 0.005;
                level.upper = level.price + 0.005;
                level.touch_count = 0;
                level.hold_count = 0;
                level.accepted_break_count = 0;
                level.role_flip_count = 0;
                level.last_test_at = start + chrono::Duration::seconds(index as i64);
                level
            })
            .collect::<Vec<_>>();
        crowded_book.push(book_level.clone());
        let crowded = unified_structure_levels("TEST", &[], &crowded_book, 10.2);
        assert!(crowded
            .iter()
            .flat_map(|level| &level.sources)
            .any(|source| source.level_id == 77));
        assert!(
            crowded
                .iter()
                .flat_map(|level| &level.sources)
                .filter(|source| source.source_kind == "level_book")
                .count()
                <= MAX_UNIFIED_BOOK_CANDIDATES_PER_SIDE
        );

        book_level.side = -1;
        let resistance = unified_structure_levels("TEST", &[], &[book_level.clone()], 9.8);
        assert_eq!(resistance.len(), 1);
        assert_eq!(resistance[0].side, -1);
        assert_eq!(resistance[0].unified_level_id, support[0].unified_level_id);

        book_level.lifecycle = LevelLifecycle::Crossed {
            direction: 1,
            first_crossed_at: start + chrono::Duration::days(4),
            beyond_trades: 1,
            beyond_volume: 100.0,
        };
        assert_eq!(
            unified_structure_levels("TEST", &[], &[book_level.clone()], 10.2).len(),
            1
        );

        book_level.lifecycle = LevelLifecycle::AwaitingRetest {
            direction: 1,
            accepted_at: start + chrono::Duration::days(4),
        };
        assert!(unified_structure_levels("TEST", &[], &[book_level], 10.2).is_empty());
    }

    #[test]
    fn extended_session_extrema_use_eligible_trades_not_quote_midpoints() {
        let mut engine = GenericStructureEngine::new("TEST");
        let premarket = new_york_ms(2026, 7, 24, 8, 0, 0);
        engine.apply_event(&quote(premarket, 49.0, 51.0, 1), TradeUpdateRule::regular());
        engine.apply_event(
            &trade(premarket + 1, 100.0, 100.0, 2),
            TradeUpdateRule::regular(),
        );
        engine.apply_event(
            &quote(premarket + 2, 9.0, 11.0, 3),
            TradeUpdateRule::regular(),
        );

        let snapshot = engine.snapshot(Utc::now());
        assert_eq!(snapshot.session_high, 100.0);
        assert_eq!(snapshot.session_low, 100.0);
    }

    #[test]
    fn extended_session_extrema_span_premarket_regular_and_after_hours() {
        let mut engine = GenericStructureEngine::new("TEST");
        for (sequence, (hour, minute, price)) in [
            (4, 0, 100.0),
            (9, 30, 101.0),
            (15, 59, 99.0),
            (19, 59, 102.0),
        ]
        .into_iter()
        .enumerate()
        {
            engine.apply_event(
                &trade(
                    new_york_ms(2026, 7, 24, hour, minute, 0),
                    price,
                    100.0,
                    sequence as u64,
                ),
                TradeUpdateRule::regular(),
            );
        }

        let snapshot = engine.snapshot(Utc::now());
        assert_eq!(snapshot.session_high, 102.0);
        assert_eq!(snapshot.session_low, 99.0);
    }

    #[test]
    fn extended_session_extrema_ignore_trades_outside_four_to_twenty_et() {
        let mut engine = GenericStructureEngine::new("TEST");
        for (sequence, (hour, minute, price)) in [
            (3, 59, 10.0),
            (4, 0, 100.0),
            (19, 59, 101.0),
            (20, 0, 250.0),
        ]
        .into_iter()
        .enumerate()
        {
            engine.apply_event(
                &trade(
                    new_york_ms(2026, 7, 24, hour, minute, 0),
                    price,
                    100.0,
                    sequence as u64,
                ),
                TradeUpdateRule::regular(),
            );
        }

        let snapshot = engine.snapshot(Utc::now());
        assert_eq!(snapshot.session_high, 101.0);
        assert_eq!(snapshot.session_low, 100.0);
    }

    #[test]
    fn extended_session_extrema_reset_at_next_four_et_trade() {
        let mut engine = GenericStructureEngine::new("TEST");
        engine.apply_event(
            &trade(new_york_ms(2026, 7, 24, 19, 59, 0), 101.0, 100.0, 1),
            TradeUpdateRule::regular(),
        );
        engine.apply_event(
            &trade(new_york_ms(2026, 7, 25, 4, 0, 0), 75.0, 100.0, 2),
            TradeUpdateRule::regular(),
        );

        let snapshot = engine.snapshot(Utc::now());
        assert_eq!(snapshot.session_high, 75.0);
        assert_eq!(snapshot.session_low, 75.0);
    }

    #[test]
    fn ticker_level_book_survives_the_next_session_boundary() {
        let mut prior_engine = GenericStructureEngine::new("TEST");
        let mut persisted_events = Vec::new();
        let prior_session = [100.00, 100.04, 100.08, 100.12, 100.08, 100.20, 100.21];
        for (index, price) in prior_session.into_iter().enumerate() {
            let (_, emitted) = prior_engine.apply_event(
                &trade(
                    new_york_ms(2026, 7, 24, 15, 0, 0) + index as i64,
                    price,
                    100.0,
                    index as u64 + 1,
                ),
                TradeUpdateRule::regular(),
            );
            persisted_events.extend(emitted);
        }
        let prior_levels = prior_engine.checkpoint().levels;
        assert!(!prior_levels.is_empty());
        let prior_level_id = prior_levels
            .iter()
            .find(|level| level.break_count > 0)
            .expect("the prior-session fixture must exercise lifecycle reconstruction")
            .level_id;

        let mut engine = GenericStructureEngine::new("TEST");
        engine.seed_events(&persisted_events);
        let seeded_level = engine
            .checkpoint()
            .levels
            .into_iter()
            .find(|level| level.level_id == prior_level_id)
            .unwrap();
        let prior_level = prior_levels
            .iter()
            .find(|level| level.level_id == prior_level_id)
            .unwrap();
        assert_eq!(seeded_level.touch_count, prior_level.touch_count);
        assert_eq!(seeded_level.hold_count, prior_level.hold_count);
        assert_eq!(seeded_level.break_count, prior_level.break_count);
        assert_eq!(
            seeded_level.accepted_break_count,
            prior_level.accepted_break_count
        );
        assert_eq!(seeded_level.role_flip_count, prior_level.role_flip_count);
        assert_eq!(
            seeded_level.lifecycle.label(),
            prior_level.lifecycle.label()
        );
        engine.apply_event(
            &trade(new_york_ms(2026, 7, 25, 4, 0, 0), 100.10, 100.0, 100),
            TradeUpdateRule::regular(),
        );

        let checkpoint = engine.checkpoint();
        assert_eq!(
            checkpoint.session_anchor,
            NaiveDate::from_ymd_opt(2026, 7, 25)
        );
        assert!(checkpoint
            .levels
            .iter()
            .any(|level| level.level_id == prior_level_id));
    }

    #[test]
    fn quote_only_moves_do_not_create_levels() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, midpoint) in [100.0, 101.0, 99.0, 102.0].into_iter().enumerate() {
            engine.apply_event(
                &quote(
                    start + index as i64 * 100,
                    midpoint - 0.01,
                    midpoint + 0.01,
                    index as u64,
                ),
                TradeUpdateRule::regular(),
            );
        }
        assert!(engine.snapshot(Utc::now()).active_levels.is_empty());
    }

    #[test]
    fn first_opposing_trade_publishes_exact_trade_extreme() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let prices = [100.00, 100.04, 100.08, 100.12, 100.08];
        let mut events = Vec::new();
        for (index, price) in prices.into_iter().enumerate() {
            let (_, emitted) = engine.apply_event(
                &trade(start + index as i64, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
            events.extend(emitted);
        }
        let created = events
            .iter()
            .find(|event| event.event_kind == "level_created")
            .unwrap();
        assert_eq!(created.price, 100.12);
        assert_eq!(created.direction, -1);
        assert!(created.confirmed_at > created.pivot_at);
    }

    #[test]
    fn timeframe_local_swing_is_causal_and_break_is_immediate() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, (offset, price)) in [
            (0, 100.00),
            (100, 100.10),
            (150, 100.09),
            (200, 100.08),
            (250, 100.05),
        ]
        .into_iter()
        .enumerate()
        {
            engine.apply_event(
                &trade(start + offset, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
        }
        let (_, promoted) = engine.apply_event(
            &trade(start + 300, 100.07, 200.0, 10),
            TradeUpdateRule::regular(),
        );
        let swing = promoted
            .iter()
            .find(|event| {
                event.event_kind == "level_promoted"
                    && event.timeframe == "100ms"
                    && event.direction < 0
            })
            .unwrap();
        assert_eq!(swing.price, 100.10);
        assert_eq!(swing.pivot_at.timestamp_millis(), start + 100);
        assert_eq!(swing.confirmed_at.timestamp_millis(), start + 300);
        let crossing_at = Utc.timestamp_millis_opt(start + 301).unwrap();
        let (_, crossed) = engine.apply_event(
            &trade(start + 301, 100.11, 300.0, 11),
            TradeUpdateRule::regular(),
        );
        assert!(crossed.iter().any(|event| {
            event.event_kind == "structure_crossed"
                && event.timeframe == "100ms"
                && event.confirmed_at == crossing_at
        }));
        assert!(crossed.iter().all(|event| !matches!(
            event.event_kind.as_str(),
            "bos" | "choch" | "structure_break"
        )));
        let (_, accepted) = engine.apply_event(
            &trade(start + 302, 100.12, 200.0, 12),
            TradeUpdateRule::regular(),
        );
        assert!(accepted
            .iter()
            .any(|event| { event.event_kind == "structure_break" && event.timeframe == "100ms" }));
    }

    #[test]
    fn timeframe_swing_uses_exact_trade_extreme_inside_bucket() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let mut emitted = Vec::new();
        for (index, (offset, price)) in [
            (0, 100.00),
            (1_010, 100.08),
            (1_250, 100.16),
            (1_800, 100.11),
            (2_010, 100.07),
            (3_010, 100.09),
        ]
        .into_iter()
        .enumerate()
        {
            let (_, events) = engine.apply_event(
                &trade(start + offset, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
            emitted.extend(events);
        }
        let swing = emitted
            .iter()
            .find(|event| {
                event.event_kind == "level_promoted"
                    && event.timeframe == "1s"
                    && event.direction < 0
            })
            .expect("1s swing high");
        assert_eq!(swing.price, 100.16);
        assert_eq!(swing.pivot_at.timestamp_millis(), start + 1_250);
        assert_eq!(swing.confirmed_at.timestamp_millis(), start + 3_010);
    }

    #[test]
    fn timeframe_swing_neighborhood_resets_after_observation_gap() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let mut emitted = Vec::new();
        for (index, (offset, price)) in [
            (0, 100.00),
            (1_010, 101.00),
            (10_010, 99.00),
            (11_010, 99.20),
        ]
        .into_iter()
        .enumerate()
        {
            let (_, events) = engine.apply_event(
                &trade(start + offset, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
            emitted.extend(events);
        }
        assert!(emitted.iter().all(|event| {
            !(event.event_kind == "level_promoted"
                && event.timeframe == "1s"
                && event.price == 101.00)
        }));
    }

    #[test]
    fn each_timeframe_breaks_only_its_own_active_swing() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let mut emitted = Vec::new();
        for (index, (offset, price)) in [
            (0, 100.00),
            (1_010, 100.10),
            (1_250, 100.20),
            (2_010, 100.05),
            (3_010, 100.08),
            (3_020, 100.21),
            (3_030, 100.22),
        ]
        .into_iter()
        .enumerate()
        {
            let (_, events) = engine.apply_event(
                &trade(start + offset, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
            emitted.extend(events);
        }
        assert!(emitted.iter().any(|event| {
            event.timeframe == "1s"
                && event.event_kind == "structure_break"
                && event.price == 100.20
        }));
        assert!(emitted.iter().all(|event| {
            !(event.timeframe == "5s"
                && matches!(
                    event.event_kind.as_str(),
                    "structure_break" | "bos" | "choch"
                ))
        }));
    }

    #[test]
    fn footprint_tracks_aggressor_volume_around_level() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        engine.apply_event(&quote(start, 99.99, 100.01, 1), TradeUpdateRule::regular());
        for (index, price) in [100.00, 100.05, 100.01, 100.00].into_iter().enumerate() {
            engine.apply_event(
                &trade(start + index as i64 + 1, price, 100.0, index as u64 + 2),
                TradeUpdateRule::regular(),
            );
        }
        let snapshot = engine.snapshot(Utc::now());
        let level = snapshot
            .active_levels
            .iter()
            .find(|level| (level.price - 100.05).abs() < 1e-9)
            .unwrap();
        assert!(level.total_volume > 0.0);
        assert!(level.trade_count > 0);
        assert_eq!(level.footprint.len(), 9);
        assert_eq!(level.footprint_session_date, "2026-07-24");
        assert!(level.footprint_as_of_ms >= start);
    }

    #[test]
    fn checkpoint_round_trip_preserves_level_book() {
        let mut source = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, price) in [100.00, 100.10, 100.00].into_iter().enumerate() {
            source.apply_event(
                &trade(start + index as i64, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
        }
        let serialized = serde_json::to_string(&source.checkpoint()).unwrap();
        let checkpoint = serde_json::from_str::<GenericStructureCheckpoint>(&serialized).unwrap();
        assert_eq!(checkpoint.last_arrival_sequence, 2);
        assert_eq!(checkpoint.replayed_through, checkpoint.updated_at);
        let mut restored = GenericStructureEngine::new("TEST");
        restored.seed_checkpoint(&checkpoint);
        assert_eq!(
            source.snapshot(Utc::now()).active_levels.len(),
            restored.snapshot(Utc::now()).active_levels.len()
        );

        let mut legacy = serde_json::from_str::<serde_json::Value>(&serialized).unwrap();
        legacy
            .as_object_mut()
            .unwrap()
            .remove("last_arrival_sequence");
        let legacy_checkpoint =
            serde_json::from_value::<GenericStructureCheckpoint>(legacy).unwrap();
        assert_eq!(legacy_checkpoint.last_arrival_sequence, 0);

        let mut legacy_level_book = serde_json::from_str::<serde_json::Value>(&serialized).unwrap();
        if let Some(level) = legacy_level_book
            .get_mut("levels")
            .and_then(|levels| levels.as_array_mut())
            .and_then(|levels| levels.first_mut())
            .and_then(|level| level.as_object_mut())
        {
            level.remove("accepted_break_count");
            level.remove("role_flip_count");
        }
        let legacy_checkpoint =
            serde_json::from_value::<GenericStructureCheckpoint>(legacy_level_book).unwrap();
        assert_eq!(legacy_checkpoint.levels[0].accepted_break_count, 0);
        assert_eq!(legacy_checkpoint.levels[0].role_flip_count, 0);

        let mut legacy_without_watermark =
            serde_json::from_str::<serde_json::Value>(&serialized).unwrap();
        legacy_without_watermark
            .as_object_mut()
            .unwrap()
            .remove("replayed_through");
        let legacy_checkpoint =
            serde_json::from_value::<GenericStructureCheckpoint>(legacy_without_watermark).unwrap();
        assert_eq!(legacy_checkpoint.replayed_through, None);
    }

    #[test]
    fn checkpoint_seed_repairs_missing_or_stale_derived_hold_scores() {
        let mut source = GenericStructureEngine::new("TEST");
        let mut level = unified_test_level(41, -1, 9.99, 10.01);
        level.hold_count = 10;
        level.break_count = 1;
        level.hold_probability = 0.01;
        level.hold_quality_score = 0.99;
        source.unified_tracks.push(UnifiedLevelTrack {
            level,
            lifecycle: LevelLifecycle::Active,
            last_relation: -1,
        });
        let mut legacy = serde_json::to_value(source.checkpoint()).unwrap();
        let serialized_level = legacy
            .get_mut("unified_tracks")
            .and_then(|tracks| tracks.as_array_mut())
            .and_then(|tracks| tracks.first_mut())
            .and_then(|track| track.get_mut("level"))
            .and_then(|level| level.as_object_mut())
            .unwrap();
        for field in [
            "hold_rate",
            "hold_observation_count",
            "hold_evidence_reliability",
            "hold_quality_score",
            "hold_score_revision",
        ] {
            serialized_level.remove(field);
        }
        let checkpoint = serde_json::from_value::<GenericStructureCheckpoint>(legacy).unwrap();
        let mut restored = GenericStructureEngine::new("TEST");
        restored.seed_checkpoint(&checkpoint);
        let repaired = &restored.unified_tracks[0].level;

        assert_eq!(repaired.hold_observation_count, 11);
        assert_eq!(repaired.hold_score_revision, STRUCTURE_HOLD_SCORE_REVISION);
        assert!((repaired.hold_probability - 0.8).abs() < 1e-12);
        assert!(repaired.hold_quality_score < repaired.hold_probability);
        assert!(repaired.hold_quality_score > 0.5);
    }

    #[test]
    fn split_adjustment_transforms_prices_and_shares_once_without_changing_identity() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = new_york_ms(2026, 7, 23, 15, 59, 59);
        let pivot_at = Utc.timestamp_millis_opt(start).unwrap();
        let mut level = event_native_level(77, 1, 20.0, start);
        level.footprint.insert(
            1,
            FootprintBin {
                total_volume: 100.0,
                buy_volume: 60.0,
                sell_volume: 40.0,
                neutral_volume: 0.0,
                trade_count: 2,
                largest_trade: 60.0,
            },
        );
        engine.levels.push(level);
        engine.last_reference_price = 20.0;
        engine.last_trade_price = 20.0;
        engine.rolling_abs_trade_move = 0.20;
        engine.rolling_spread = 0.04;
        engine.rolling_trade_size = 50.0;
        engine.trade_volume_poc = 20.0;
        engine.last_ts = Some(pivot_at);
        engine.replayed_through = Some(pivot_at);
        engine.last_arrival_sequence = 9;
        engine.session_volume_by_price.insert(
            price_key(20.0),
            PriceVolumeBin {
                total_volume: 200.0,
                buy_volume: 120.0,
                sell_volume: 80.0,
                neutral_volume: 0.0,
                trade_count: 3,
                largest_trade: 100.0,
            },
        );
        let execution_date = NaiveDate::from_ymd_opt(2026, 7, 24).unwrap();
        let effective_at = New_York
            .from_local_datetime(
                &execution_date.and_time(chrono::NaiveTime::from_hms_opt(4, 0, 0).unwrap()),
            )
            .single()
            .unwrap()
            .with_timezone(&Utc);
        let adjustment = StructureSplitAdjustment {
            execution_date,
            effective_at,
            split_from: 1.0,
            split_to: 10.0,
            source_inserted_at: effective_at,
        };

        assert!(engine.apply_split_adjustment(&adjustment).unwrap());
        assert!(!engine.apply_split_adjustment(&adjustment).unwrap());
        let checkpoint = engine.checkpoint();
        assert_eq!(checkpoint.levels[0].level_id, 77);
        assert!((checkpoint.levels[0].price - 2.0).abs() < 1e-9);
        assert!((checkpoint.last_trade_price - 2.0).abs() < 1e-9);
        assert!((checkpoint.rolling_abs_trade_move - 0.02).abs() < 1e-9);
        assert!((checkpoint.rolling_spread - 0.004).abs() < 1e-9);
        assert!((checkpoint.rolling_trade_size - 500.0).abs() < 1e-9);
        assert_eq!(checkpoint.applied_split_adjustments.len(), 1);
        let footprint = checkpoint.levels[0].footprint.values().next().unwrap();
        assert!((footprint.total_volume - 1_000.0).abs() < 1e-9);
        let session_bin = checkpoint.session_volume_by_price.values().next().unwrap();
        assert!((session_bin.total_volume - 2_000.0).abs() < 1e-9);
        assert_eq!(session_bin.trade_count, 3);
    }

    #[test]
    fn arrival_cursor_rejects_duplicate_replay_at_the_same_timestamp() {
        let mut engine = GenericStructureEngine::new("TEST");
        let timestamp = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let event = trade(timestamp, 100.0, 100.0, 17);
        engine.apply_event(&event, TradeUpdateRule::regular());
        let before = serde_json::to_string(&engine.checkpoint()).unwrap();

        engine.apply_event(&event, TradeUpdateRule::regular());

        assert_eq!(serde_json::to_string(&engine.checkpoint()).unwrap(), before);
        assert_eq!(engine.checkpoint().last_arrival_sequence, 17);
    }

    #[test]
    fn delayed_trade_advances_audit_cursor_without_revising_structure() {
        let mut engine = GenericStructureEngine::new("TEST");
        let fresh_at = new_york_ms(2026, 8, 21, 4, 2, 52);
        let fresh = trade(fresh_at, 3.46, 100.0, 17);
        engine.apply_event(&fresh, TradeUpdateRule::regular());

        let mut delayed = trade(fresh_at + 1_000, 3.70, 70.0, 18);
        let MarketEvent::Trade(delayed_trade) = &mut delayed else {
            unreachable!("trade helper must return a trade")
        };
        delayed_trade.participant_ts = Some(Utc.with_ymd_and_hms(2026, 8, 21, 5, 47, 51).unwrap());
        assert!(delayed.is_delayed_trade_report());

        engine.apply_event(&delayed, TradeUpdateRule::regular());

        let snapshot = engine.snapshot(delayed.ts());
        let checkpoint = engine.checkpoint();
        assert!((snapshot.session_high - 3.46).abs() < 1e-9);
        assert!((snapshot.reference_price - 3.46).abs() < 1e-9);
        assert!((checkpoint.last_trade_price - 3.46).abs() < 1e-9);
        assert_eq!(checkpoint.last_arrival_sequence, 18);
        assert_eq!(checkpoint.updated_at, Some(delayed.ts()));
    }

    #[test]
    fn snapshot_free_advancement_preserves_the_complete_engine_state() {
        let mut ordinary = GenericStructureEngine::new("TEST");
        let mut historical = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        let events = [
            quote(start, 9.99, 10.01, 1),
            trade(start + 100, 10.0, 100.0, 2),
            trade(start + 200, 10.2, 200.0, 3),
            trade(start + 300, 9.9, 150.0, 4),
        ];

        for event in &events {
            ordinary.apply_event(event, TradeUpdateRule::regular());
            historical.apply_event_without_snapshot(event, TradeUpdateRule::regular());
        }

        assert_eq!(
            serde_json::to_value(ordinary.checkpoint()).unwrap(),
            serde_json::to_value(historical.checkpoint()).unwrap(),
        );
        assert_eq!(
            serde_json::to_value(ordinary.snapshot(events.last().unwrap().ts())).unwrap(),
            serde_json::to_value(historical.snapshot(events.last().unwrap().ts())).unwrap(),
        );
    }
}
