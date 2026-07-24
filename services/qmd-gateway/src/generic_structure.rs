use crate::bars::TradeUpdateRule;
use crate::event::MarketEvent;
use chrono::{DateTime, NaiveDate, Timelike, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, HashMap};

pub const GENERIC_STRUCTURE_ALGORITHM_VERSION: u16 = 6;
pub const STRUCTURE_TIMEFRAMES: [(&str, i64); 8] = [
    ("100ms", 100),
    ("1s", 1_000),
    ("5s", 5_000),
    ("10s", 10_000),
    ("30s", 30_000),
    ("1m", 60_000),
    ("5m", 300_000),
    ("1h", 3_600_000),
];

const SESSION_ANCHOR_SECONDS: u32 = 4 * 60 * 60;
const REGULAR_OPEN_SECONDS: u32 = 9 * 60 * 60 + 30 * 60;
const OPENING_RANGE_END_SECONDS: u32 = 9 * 60 * 60 + 35 * 60;
const FOOTPRINT_RADIUS_TICKS: i32 = 4;
const MAX_LEVELS: usize = 512;
const MAX_EXPOSED_LEVELS_PER_SIDE: usize = 8;

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
    pub premarket_high: f64,
    pub premarket_low: f64,
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
    lifecycle: LevelLifecycle,
    promotions: Vec<Promotion>,
    footprint: BTreeMap<i32, FootprintBin>,
}

impl StructureLevel {
    fn is_active(&self) -> bool {
        matches!(self.lifecycle, LevelLifecycle::Active)
    }

    fn has_promotion(&self, timeframe: &str) -> bool {
        self.promotions
            .iter()
            .any(|promotion| promotion.timeframe == timeframe)
    }
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct TimeframeState {
    timeframe: String,
    previous_high: f64,
    current_high: f64,
    previous_low: f64,
    current_low: f64,
    direction: i8,
    promoted_level_count: usize,
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

#[derive(Clone, Debug)]
pub struct GenericStructureEngine {
    sym: String,
    last_ts: Option<DateTime<Utc>>,
    last_reference_price: f64,
    last_trade_price: f64,
    bid: f64,
    ask: f64,
    leg_direction: i8,
    candidate_high: f64,
    candidate_high_at: Option<DateTime<Utc>>,
    candidate_low: f64,
    candidate_low_at: Option<DateTime<Utc>>,
    levels: Vec<StructureLevel>,
    timeframe_states: Vec<TimeframeState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, PriceVolumeBin>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
pub struct GenericStructureCheckpoint {
    pub algorithm_version: u16,
    pub sym: String,
    pub updated_at: Option<DateTime<Utc>>,
    last_reference_price: f64,
    last_trade_price: f64,
    bid: f64,
    ask: f64,
    leg_direction: i8,
    candidate_high: f64,
    candidate_high_at: Option<DateTime<Utc>>,
    candidate_low: f64,
    candidate_low_at: Option<DateTime<Utc>>,
    levels: Vec<StructureLevel>,
    timeframe_states: Vec<TimeframeState>,
    session_anchor: Option<NaiveDate>,
    session_high: f64,
    session_low: f64,
    premarket_high: f64,
    premarket_low: f64,
    opening_range_high: f64,
    opening_range_low: f64,
    session_volume_by_price: HashMap<i64, PriceVolumeBin>,
    trade_volume_poc: f64,
    last_event: Option<GenericStructureEvent>,
}

impl GenericStructureEngine {
    pub fn new(sym: impl Into<String>) -> Self {
        Self {
            sym: sym.into().to_ascii_uppercase(),
            last_ts: None,
            last_reference_price: 0.0,
            last_trade_price: 0.0,
            bid: 0.0,
            ask: 0.0,
            leg_direction: 0,
            candidate_high: 0.0,
            candidate_high_at: None,
            candidate_low: 0.0,
            candidate_low_at: None,
            levels: Vec::new(),
            timeframe_states: STRUCTURE_TIMEFRAMES
                .iter()
                .map(|(timeframe, _)| TimeframeState {
                    timeframe: (*timeframe).to_string(),
                    ..TimeframeState::default()
                })
                .collect(),
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

    pub fn updated_at_ms(&self) -> i64 {
        self.last_ts
            .map(|value| value.timestamp_millis())
            .unwrap_or_default()
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
                self.last_reference_price = (quote.bid_price + quote.ask_price) / 2.0;
                self.observe_reference(ts, self.last_reference_price);
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
                self.last_reference_price = trade.price;
                self.observe_reference(ts, trade.price);
                self.observe_trade_volume(trade.price, size, aggressor);
                self.update_level_footprints(trade.price, size, aggressor);
                self.promote_levels(ts, trade.price, &mut emitted);
                self.update_level_lifecycles(ts, trade.price, size, &mut emitted);
                self.update_directional_leg(ts, trade.price, &mut emitted);
                self.last_trade_price = trade.price;
            }
            _ => {}
        }
        self.last_ts = Some(ts);
        if let Some(last) = emitted.last().cloned() {
            self.last_event = Some(last);
        }
        (self.snapshot(ts), emitted)
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
        let tick = price_tick(price);
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
            } else if moved_at_least_one_tick(self.candidate_high - price, tick) {
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
        } else if moved_at_least_one_tick(price - self.candidate_low, tick) {
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
        let tick = price_tick(price);
        if let Some(level) = self.levels.iter_mut().find(|level| {
            level.side == side && level.is_active() && (level.price - price).abs() <= tick * 0.5
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
            lower: price - tick,
            upper: price + tick,
            pivot_at,
            confirmed_at,
            last_test_at: confirmed_at,
            touch_count: 1,
            hold_count: 0,
            break_count: 0,
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

    fn promote_levels(
        &mut self,
        ts: DateTime<Utc>,
        price: f64,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        let tick = price_tick(price);
        let mut promotions = Vec::new();
        for level in &mut self.levels {
            if !level.is_active() {
                continue;
            }
            let departure = if level.side > 0 {
                price - level.price
            } else {
                level.price - price
            };
            if !moved_at_least_one_tick(departure, tick) {
                continue;
            }
            let age_ms = (ts - level.confirmed_at).num_milliseconds().max(0);
            for (timeframe, horizon_ms) in STRUCTURE_TIMEFRAMES {
                if age_ms < horizon_ms || level.has_promotion(timeframe) {
                    continue;
                }
                let survival = (age_ms as f64 / horizon_ms as f64).min(2.0) / 2.0;
                let excursion_ticks = (departure / tick).max(0.0);
                let score = (0.45 + 0.25 * survival + 0.30 * (excursion_ticks / 8.0).min(1.0))
                    .clamp(0.0, 1.0);
                level.promotions.push(Promotion {
                    timeframe: timeframe.to_string(),
                    promoted_at: ts,
                    score,
                });
                promotions.push((level.level_id, timeframe.to_string(), score));
            }
        }
        for (level_id, timeframe, score) in promotions {
            if let Some(level) = self.levels.iter().find(|level| level.level_id == level_id) {
                let mut event = level_event(
                    &self.sym,
                    level,
                    &timeframe,
                    "level_promoted",
                    level.side,
                    ts,
                );
                event.confidence = score;
                emitted.push(event);
            }
            self.update_timeframe_state(level_id, &timeframe);
        }
    }

    fn update_timeframe_state(&mut self, level_id: u64, timeframe: &str) {
        let Some(level) = self.levels.iter().find(|level| level.level_id == level_id) else {
            return;
        };
        let Some(state) = self
            .timeframe_states
            .iter_mut()
            .find(|state| state.timeframe == timeframe)
        else {
            return;
        };
        if level.side < 0 {
            state.previous_high = state.current_high;
            state.current_high = level.price;
        } else {
            state.previous_low = state.current_low;
            state.current_low = level.price;
        }
        state.promoted_level_count = state.promoted_level_count.saturating_add(1);
        if state.previous_high > 0.0
            && state.previous_low > 0.0
            && state.current_high > state.previous_high
            && state.current_low > state.previous_low
        {
            state.direction = 1;
        } else if state.previous_high > 0.0
            && state.previous_low > 0.0
            && state.current_high < state.previous_high
            && state.current_low < state.previous_low
        {
            state.direction = -1;
        }
    }

    fn update_level_lifecycles(
        &mut self,
        ts: DateTime<Utc>,
        price: f64,
        size: f64,
        emitted: &mut Vec<GenericStructureEvent>,
    ) {
        let tick = price_tick(price);
        let mut pending = Vec::new();
        for level in &mut self.levels {
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
                    for promotion in &level.promotions {
                        let prior_direction = self
                            .timeframe_states
                            .iter()
                            .find(|state| state.timeframe == promotion.timeframe)
                            .map(|state| state.direction)
                            .unwrap_or_default();
                        let kind = if prior_direction != 0 && direction != prior_direction {
                            "choch"
                        } else if prior_direction != 0 {
                            "bos"
                        } else {
                            "structure_break"
                        };
                        pending.push((
                            level.level_id,
                            promotion.timeframe.clone(),
                            kind.to_string(),
                            direction,
                        ));
                    }
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
            if matches!(kind.as_str(), "bos" | "choch" | "structure_break") {
                if let Some(state) = self
                    .timeframe_states
                    .iter_mut()
                    .find(|state| state.timeframe == timeframe)
                {
                    state.direction = direction;
                }
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

    fn observe_reference(&mut self, ts: DateTime<Utc>, reference: f64) {
        let local = ts.with_timezone(&New_York);
        let seconds = local.time().num_seconds_from_midnight();
        if seconds >= SESSION_ANCHOR_SECONDS {
            self.session_high = self.session_high.max(reference);
            self.session_low = positive_min(self.session_low, reference);
        }
        if (SESSION_ANCHOR_SECONDS..REGULAR_OPEN_SECONDS).contains(&seconds) {
            self.premarket_high = self.premarket_high.max(reference);
            self.premarket_low = positive_min(self.premarket_low, reference);
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
        self.premarket_high = 0.0;
        self.premarket_low = 0.0;
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

    pub fn snapshot(&self, _now: DateTime<Utc>) -> GenericStructureSnapshot {
        let reference = if self.last_trade_price > 0.0 {
            self.last_trade_price
        } else {
            self.last_reference_price
        };
        let active_levels =
            exposed_active_levels(&self.levels, &self.session_volume_by_price, reference);
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
            premarket_high: self.premarket_high,
            premarket_low: self.premarket_low,
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
                        lifecycle: LevelLifecycle::Active,
                        promotions: Vec::new(),
                        footprint: BTreeMap::new(),
                    });
                }
                "level_promoted" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        if !level.has_promotion(&event.timeframe) {
                            level.promotions.push(Promotion {
                                timeframe: event.timeframe.clone(),
                                promoted_at: event.confirmed_at,
                                score: event.confidence,
                            });
                        }
                    }
                    self.update_timeframe_state(event.level_id, &event.timeframe);
                }
                "level_crossed" | "break_accepted" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.lifecycle = LevelLifecycle::AwaitingRetest {
                            direction: event.direction,
                            accepted_at: event.confirmed_at,
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
                        level.lifecycle = LevelLifecycle::Active;
                    }
                }
                "break_rejected" | "retest_failed" => {
                    if let Some(level) = self
                        .levels
                        .iter_mut()
                        .find(|level| level.level_id == event.level_id)
                    {
                        level.lifecycle = LevelLifecycle::Active;
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
        self.timeframe_states = snapshot
            .timeframe_states
            .iter()
            .map(|state| TimeframeState {
                timeframe: state.timeframe.clone(),
                current_high: state.swing_high,
                current_low: state.swing_low,
                direction: state.direction,
                promoted_level_count: state.promoted_level_count,
                ..TimeframeState::default()
            })
            .collect();
        self.session_high = snapshot.session_high;
        self.session_low = snapshot.session_low;
        self.premarket_high = snapshot.premarket_high;
        self.premarket_low = snapshot.premarket_low;
        self.opening_range_high = snapshot.opening_range_high;
        self.opening_range_low = snapshot.opening_range_low;
        self.trade_volume_poc = snapshot.trade_volume_poc;
    }

    pub fn checkpoint(&self) -> GenericStructureCheckpoint {
        GenericStructureCheckpoint {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            sym: self.sym.clone(),
            updated_at: self.last_ts,
            last_reference_price: self.last_reference_price,
            last_trade_price: self.last_trade_price,
            bid: self.bid,
            ask: self.ask,
            leg_direction: self.leg_direction,
            candidate_high: self.candidate_high,
            candidate_high_at: self.candidate_high_at,
            candidate_low: self.candidate_low,
            candidate_low_at: self.candidate_low_at,
            levels: self.levels.clone(),
            timeframe_states: self.timeframe_states.clone(),
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

    pub fn seed_checkpoint(&mut self, checkpoint: &GenericStructureCheckpoint) {
        if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
            return;
        }
        self.sym = checkpoint.sym.clone();
        self.last_ts = checkpoint.updated_at;
        self.last_reference_price = checkpoint.last_reference_price;
        self.last_trade_price = checkpoint.last_trade_price;
        self.bid = checkpoint.bid;
        self.ask = checkpoint.ask;
        self.leg_direction = checkpoint.leg_direction;
        self.candidate_high = checkpoint.candidate_high;
        self.candidate_high_at = checkpoint.candidate_high_at;
        self.candidate_low = checkpoint.candidate_low;
        self.candidate_low_at = checkpoint.candidate_low_at;
        self.levels = checkpoint.levels.clone();
        self.timeframe_states = checkpoint.timeframe_states.clone();
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
        .map(|level| level_candidate(level, session_volume, reference))
        .collect()
}

fn level_candidate(
    level: &StructureLevel,
    session_volume: &HashMap<i64, PriceVolumeBin>,
    reference: f64,
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
        + (trade_count as f64).ln_1p().min(8.0) * 0.025
        - level.break_count.min(3) as f64 * 0.08)
        .clamp(0.0, 1.0);
    let confidence = (0.20
        + level.promotions.len().min(8) as f64 * 0.075
        + level.touch_count.min(5) as f64 * 0.04
        + level.hold_count.min(5) as f64 * 0.05)
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
    levels: &[StructureLevelCandidate],
) -> StructureTimeframeSnapshot {
    let support = levels
        .iter()
        .filter(|level| {
            level.side > 0
                && level
                    .promotions
                    .iter()
                    .any(|promotion| promotion.timeframe == state.timeframe)
        })
        .min_by(|left, right| left.distance.total_cmp(&right.distance))
        .map(candidate_to_snapshot)
        .unwrap_or_default();
    let resistance = levels
        .iter()
        .filter(|level| {
            level.side < 0
                && level
                    .promotions
                    .iter()
                    .any(|promotion| promotion.timeframe == state.timeframe)
        })
        .min_by(|left, right| left.distance.total_cmp(&right.distance))
        .map(candidate_to_snapshot)
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

fn moved_at_least_one_tick(distance: f64, tick: f64) -> bool {
    distance + tick * 1e-6 >= tick
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
    use serde_json::Value;

    fn trade(ms: i64, price: f64, size: f64, sequence: u64) -> MarketEvent {
        MarketEvent::Trade(TradeEvent {
            conditions: Vec::new(),
            exchange: 1,
            ingest_ts: Utc.timestamp_millis_opt(ms).unwrap(),
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
            raw: Value::Null,
            sequence,
            tape: 3,
            ticker: "TEST".to_string(),
            ts: Utc.timestamp_millis_opt(ms).unwrap(),
        })
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
        let prices = [100.00, 100.04, 100.08, 100.12, 100.11];
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
    fn timeframe_promotion_is_causal_and_break_is_immediate() {
        let mut engine = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, price) in [100.00, 100.10, 100.09].into_iter().enumerate() {
            engine.apply_event(
                &trade(start + index as i64, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
        }
        let (_, promoted) = engine.apply_event(
            &trade(start + 150, 100.05, 200.0, 10),
            TradeUpdateRule::regular(),
        );
        assert!(promoted
            .iter()
            .any(|event| event.event_kind == "level_promoted" && event.timeframe == "100ms"));
        let crossing_at = Utc.timestamp_millis_opt(start + 151).unwrap();
        let (_, crossed) = engine.apply_event(
            &trade(start + 151, 100.11, 300.0, 11),
            TradeUpdateRule::regular(),
        );
        assert!(crossed.iter().any(|event| {
            event.event_kind == "level_crossed" && event.confirmed_at == crossing_at
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
        for (index, price) in [100.00, 100.05, 100.04, 100.03].into_iter().enumerate() {
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
    }

    #[test]
    fn checkpoint_round_trip_preserves_level_book() {
        let mut source = GenericStructureEngine::new("TEST");
        let start = Utc
            .with_ymd_and_hms(2026, 7, 24, 13, 30, 0)
            .unwrap()
            .timestamp_millis();
        for (index, price) in [100.00, 100.10, 100.09].into_iter().enumerate() {
            source.apply_event(
                &trade(start + index as i64, price, 100.0, index as u64),
                TradeUpdateRule::regular(),
            );
        }
        let serialized = serde_json::to_string(&source.checkpoint()).unwrap();
        let checkpoint = serde_json::from_str::<GenericStructureCheckpoint>(&serialized).unwrap();
        let mut restored = GenericStructureEngine::new("TEST");
        restored.seed_checkpoint(&checkpoint);
        assert_eq!(
            source.snapshot(Utc::now()).active_levels.len(),
            restored.snapshot(Utc::now()).active_levels.len()
        );
    }
}
