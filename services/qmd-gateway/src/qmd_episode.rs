use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use std::collections::HashMap;

pub const QMD_EPISODE_ALGORITHM_VERSION: u16 = 2;

#[derive(Clone, Copy, Debug, Eq, Hash, PartialEq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum QmdEpisodePreset {
    Micro,
    Tactical,
    Context,
}

impl QmdEpisodePreset {
    pub const ALL: [Self; 3] = [Self::Micro, Self::Tactical, Self::Context];

    pub const fn as_str(self) -> &'static str {
        match self {
            Self::Micro => "micro",
            Self::Tactical => "tactical",
            Self::Context => "context",
        }
    }

    fn config(self) -> QmdEpisodeConfig {
        match self {
            Self::Micro => QmdEpisodeConfig {
                start_confidence: 0.35,
                opposite_confidence: 0.40,
                opposite_dwell_ms: 500,
                invalidation_buffer_fraction: 0.0015,
                confidence_alpha: 0.55,
                confidence_decay_alpha: 0.06,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 2_000,
                update_interval_ms: 1_000,
                setup_max_duration_ms: 30_000,
                minimum_favorable_thresholds: 2.0,
                macd_interval_ms: 1_000,
            },
            Self::Tactical => QmdEpisodeConfig {
                start_confidence: 0.45,
                opposite_confidence: 0.50,
                opposite_dwell_ms: 1_500,
                invalidation_buffer_fraction: 0.0035,
                confidence_alpha: 0.25,
                confidence_decay_alpha: 0.02,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 10_000,
                update_interval_ms: 5_000,
                setup_max_duration_ms: 180_000,
                minimum_favorable_thresholds: 1.75,
                macd_interval_ms: 5_000,
            },
            Self::Context => QmdEpisodeConfig {
                start_confidence: 0.55,
                opposite_confidence: 0.60,
                opposite_dwell_ms: 5_000,
                invalidation_buffer_fraction: 0.0075,
                confidence_alpha: 0.10,
                confidence_decay_alpha: 0.005,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 30_000,
                update_interval_ms: 15_000,
                setup_max_duration_ms: 900_000,
                minimum_favorable_thresholds: 1.5,
                macd_interval_ms: 15_000,
            },
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct QmdEpisodeConfig {
    start_confidence: f64,
    opposite_confidence: f64,
    opposite_dwell_ms: i64,
    invalidation_buffer_fraction: f64,
    confidence_alpha: f64,
    confidence_decay_alpha: f64,
    opposing_structure_confidence: f64,
    reentry_cooldown_ms: i64,
    update_interval_ms: i64,
    setup_max_duration_ms: i64,
    minimum_favorable_thresholds: f64,
    macd_interval_ms: i64,
}

#[derive(Clone, Copy, Debug)]
pub struct QmdEpisodeScaleInput {
    pub direction: i8,
    pub confidence: f64,
    pub threshold: f64,
    pub swing_high: f64,
    pub swing_low: f64,
    pub structure_break_direction: i8,
    pub structure_break_confidence: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct QmdEpisodeInput {
    pub occurred_at: DateTime<Utc>,
    pub close: f64,
    pub high: f64,
    pub low: f64,
    pub decision_direction: i8,
    pub decision_confidence: f64,
    pub micro: QmdEpisodeScaleInput,
    pub tactical: QmdEpisodeScaleInput,
    pub context: QmdEpisodeScaleInput,
}

impl QmdEpisodeInput {
    fn scale(self, preset: QmdEpisodePreset) -> QmdEpisodeScaleInput {
        match preset {
            QmdEpisodePreset::Micro => self.micro,
            QmdEpisodePreset::Tactical => self.tactical,
            QmdEpisodePreset::Context => self.context,
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdEpisodeState {
    pub preset: QmdEpisodePreset,
    pub episode_id: u64,
    pub started_at: DateTime<Utc>,
    pub last_updated_at: DateTime<Utc>,
    pub direction: i8,
    pub confidence: f64,
    pub entry_price: f64,
    pub rail_price: f64,
    pub invalidation_price: f64,
    pub best_price: f64,
    pub last_progress_at: DateTime<Utc>,
    pub current_price: f64,
    pub macd_line: f64,
    pub macd_signal: f64,
    pub macd_converging: bool,
    #[serde(skip_serializing)]
    trailing_swing_price: f64,
    #[serde(skip_serializing)]
    favorable_swing_price: f64,
    #[serde(skip_serializing)]
    last_scale_swing_high: f64,
    #[serde(skip_serializing)]
    last_scale_swing_low: f64,
    #[serde(skip_serializing)]
    failed_swing_price: f64,
    #[serde(skip_serializing)]
    failed_swing_confirmed: bool,
    #[serde(skip_serializing)]
    resolution_reference_price: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdEpisodeEvent {
    pub algorithm_version: u16,
    pub sym: String,
    pub preset: QmdEpisodePreset,
    pub episode_id: u64,
    pub event_type: String,
    pub occurred_at: DateTime<Utc>,
    pub started_at: DateTime<Utc>,
    pub direction: i8,
    pub confidence: f64,
    pub entry_price: f64,
    pub rail_price: f64,
    pub invalidation_price: f64,
    pub best_price: f64,
    pub maximum_favorable_move_pct: f64,
    pub event_price: f64,
    pub reference_price: f64,
    pub macd_line: f64,
    pub macd_signal: f64,
    pub macd_converging: bool,
    pub resolution: String,
}

#[derive(Clone, Debug, Default)]
struct PresetState {
    active: Option<QmdEpisodeState>,
    setup: Option<QmdEpisodeSetup>,
    last_emitted_bucket: i16,
    last_emitted_at: Option<DateTime<Utc>>,
    cooldown_until: Option<DateTime<Utc>>,
    opposite_since: Option<DateTime<Utc>>,
    rearm_direction: i8,
    rearm_neutral_since: Option<DateTime<Utc>>,
    last_close: Option<f64>,
    macd: PresetMacdState,
}

#[derive(Clone, Debug)]
struct QmdEpisodeSetup {
    armed_at: DateTime<Utc>,
    direction: i8,
    breakout_price: f64,
    invalidation_price: f64,
    breakout_was_unbroken: bool,
    confidence: f64,
}

#[derive(Clone, Copy, Debug, Default)]
struct MacdReading {
    line: f64,
    signal: f64,
    ready: bool,
    converging: bool,
}

#[derive(Clone, Debug, Default)]
struct PresetMacdState {
    bucket: Option<i64>,
    bucket_close: f64,
    fast: Option<f64>,
    slow: Option<f64>,
    signal: Option<f64>,
    samples: usize,
    previous_diff: Option<f64>,
    reading: MacdReading,
}

impl PresetMacdState {
    fn update(&mut self, occurred_at: DateTime<Utc>, close: f64, interval_ms: i64) -> MacdReading {
        if !close.is_finite() || close <= 0.0 || interval_ms <= 0 {
            return self.reading;
        }
        let bucket = occurred_at.timestamp_millis().div_euclid(interval_ms);
        if self.bucket.is_none() {
            self.bucket = Some(bucket);
            self.bucket_close = close;
            return self.reading;
        }
        if self.bucket == Some(bucket) {
            self.bucket_close = close;
            return self.reading;
        }
        self.finalize_bucket();
        self.bucket = Some(bucket);
        self.bucket_close = close;
        self.reading
    }

    fn finalize_bucket(&mut self) {
        let close = self.bucket_close;
        self.fast = Some(ema_next(self.fast, close, 12));
        self.slow = Some(ema_next(self.slow, close, 26));
        let line = self.fast.unwrap_or(close) - self.slow.unwrap_or(close);
        self.signal = Some(ema_next(self.signal, line, 9));
        let signal = self.signal.unwrap_or(line);
        let diff = line - signal;
        self.samples = self.samples.saturating_add(1);
        let converging = self
            .previous_diff
            .is_some_and(|previous| diff.abs() < previous.abs());
        self.previous_diff = Some(diff);
        self.reading = MacdReading {
            line,
            signal,
            ready: self.samples >= 26,
            converging,
        };
    }
}

fn ema_next(previous: Option<f64>, value: f64, period: u32) -> f64 {
    let alpha = 2.0 / (period as f64 + 1.0);
    previous.map_or(value, |prior| alpha * value + (1.0 - alpha) * prior)
}

#[derive(Clone, Debug, Default)]
pub struct QmdEpisodeEngine {
    next_episode_id: u64,
    presets: HashMap<QmdEpisodePreset, PresetState>,
}

impl QmdEpisodeEngine {
    pub fn update(&mut self, sym: &str, input: QmdEpisodeInput) -> Vec<QmdEpisodeEvent> {
        let mut events = Vec::with_capacity(4);
        for preset in QmdEpisodePreset::ALL {
            self.update_preset(sym, preset, input, &mut events);
        }
        events
    }

    pub fn states(&self) -> Vec<QmdEpisodeState> {
        QmdEpisodePreset::ALL
            .into_iter()
            .filter_map(|preset| {
                self.presets
                    .get(&preset)
                    .and_then(|state| state.active.clone())
            })
            .collect()
    }

    fn update_preset(
        &mut self,
        sym: &str,
        preset: QmdEpisodePreset,
        input: QmdEpisodeInput,
        events: &mut Vec<QmdEpisodeEvent>,
    ) {
        let config = preset.config();
        let scale = input.scale(preset);
        let observed_high = if input.high.is_finite() && input.high > 0.0 {
            input.high.max(input.close)
        } else {
            input.close
        };
        let observed_low = if input.low.is_finite() && input.low > 0.0 {
            input.low.min(input.close)
        } else {
            input.close
        };
        let threshold = if scale.threshold.is_finite() && scale.threshold > 0.0 {
            scale.threshold
        } else {
            (input.close.abs() * config.invalidation_buffer_fraction).max(0.0001)
        };
        let state = self.presets.entry(preset).or_default();
        let macd = state
            .macd
            .update(input.occurred_at, input.close, config.macd_interval_ms);
        let prior_close = state.last_close;
        let mut ending: Option<(&str, f64)> = None;
        let mut ended_direction = 0;
        let qualified_direction = if input.decision_direction != 0
            && input.decision_confidence >= config.start_confidence
        {
            input.decision_direction.signum()
        } else {
            0
        };
        if state.rearm_direction != 0 {
            if qualified_direction == -state.rearm_direction {
                state.rearm_direction = 0;
                state.rearm_neutral_since = None;
            } else if qualified_direction == 0 {
                let since = state.rearm_neutral_since.get_or_insert(input.occurred_at);
                if input.occurred_at - *since >= Duration::milliseconds(config.reentry_cooldown_ms)
                {
                    state.rearm_direction = 0;
                    state.rearm_neutral_since = None;
                }
            } else {
                state.rearm_neutral_since = None;
            }
        }

        if let Some(active) = state.active.as_mut() {
            let opposite_decision = input.decision_direction == -active.direction
                && input.decision_confidence >= config.opposite_confidence;
            let opposing_structure = scale.structure_break_direction == -active.direction
                && scale.structure_break_confidence >= config.opposing_structure_confidence;
            let invalidated = (active.direction > 0 && input.close <= active.invalidation_price)
                || (active.direction < 0 && input.close >= active.invalidation_price);
            let favorable_price = if active.direction > 0 {
                observed_high
            } else {
                observed_low
            };
            let favorable_progress = if active.direction > 0 {
                favorable_price > active.best_price
            } else {
                favorable_price < active.best_price
            };
            if favorable_progress {
                active.best_price = favorable_price;
                active.last_progress_at = input.occurred_at;
            }
            active.current_price = input.close;
            active.macd_line = macd.line;
            active.macd_signal = macd.signal;
            active.macd_converging = macd.converging;
            let minimum_favorable_move = threshold * config.minimum_favorable_thresholds;
            let has_meaningful_progress = if active.direction > 0 {
                active.best_price >= active.rail_price + minimum_favorable_move
            } else {
                active.best_price <= active.rail_price - minimum_favorable_move
            };
            let swing_change_buffer = (threshold * 0.10).max(input.close.abs() * 0.00002);
            if materially_changed(
                scale.swing_high,
                active.last_scale_swing_high,
                swing_change_buffer,
            ) {
                active.last_scale_swing_high = scale.swing_high;
                if active.direction > 0 {
                    if active.favorable_swing_price <= 0.0
                        || scale.swing_high > active.favorable_swing_price + swing_change_buffer
                    {
                        active.favorable_swing_price = scale.swing_high;
                        active.failed_swing_confirmed = false;
                        active.failed_swing_price = 0.0;
                    } else if has_meaningful_progress
                        && scale.swing_high < active.favorable_swing_price - swing_change_buffer
                    {
                        active.failed_swing_confirmed = true;
                        active.failed_swing_price = scale.swing_high;
                    }
                }
            }
            if materially_changed(
                scale.swing_low,
                active.last_scale_swing_low,
                swing_change_buffer,
            ) {
                active.last_scale_swing_low = scale.swing_low;
                if active.direction < 0 {
                    if active.favorable_swing_price <= 0.0
                        || scale.swing_low < active.favorable_swing_price - swing_change_buffer
                    {
                        active.favorable_swing_price = scale.swing_low;
                        active.failed_swing_confirmed = false;
                        active.failed_swing_price = 0.0;
                    } else if has_meaningful_progress
                        && scale.swing_low > active.favorable_swing_price + swing_change_buffer
                    {
                        active.failed_swing_confirmed = true;
                        active.failed_swing_price = scale.swing_low;
                    }
                }
            }
            if active.direction > 0 {
                let candidate = scale.swing_low;
                if has_meaningful_progress
                    && candidate.is_finite()
                    && candidate > active.rail_price
                    && candidate < active.best_price - threshold * 0.5
                    && candidate > active.trailing_swing_price
                {
                    active.trailing_swing_price = candidate;
                }
            } else {
                let candidate = scale.swing_high;
                if has_meaningful_progress
                    && candidate.is_finite()
                    && candidate > active.best_price + threshold * 0.5
                    && candidate < active.rail_price
                    && (active.trailing_swing_price <= 0.0
                        || candidate < active.trailing_swing_price)
                {
                    active.trailing_swing_price = candidate;
                }
            }
            let exhaustion_buffer = threshold * 0.15;
            let protected_swing_broken = has_meaningful_progress
                && if active.direction > 0 {
                    active.trailing_swing_price > active.rail_price
                        && input.close < active.trailing_swing_price - exhaustion_buffer
                } else {
                    active.trailing_swing_price > 0.0
                        && active.trailing_swing_price < active.rail_price
                        && input.close > active.trailing_swing_price + exhaustion_buffer
                };
            let macd_opposes = macd.ready
                && if active.direction > 0 {
                    macd.line < macd.signal
                } else {
                    macd.line > macd.signal
                };
            let failed_swing_with_macd = active.failed_swing_confirmed && macd_opposes;
            if opposite_decision {
                let since = state.opposite_since.get_or_insert(input.occurred_at);
                if input.occurred_at - *since >= Duration::milliseconds(config.opposite_dwell_ms) {
                    ending = Some(("opposite_qmd_decision", input.close));
                }
            } else if invalidated {
                ending = Some(("structural_invalidation", active.invalidation_price));
            } else if protected_swing_broken && macd_opposes {
                ending = Some((
                    "protected_swing_break_macd_confirmation",
                    active.trailing_swing_price,
                ));
            } else if opposing_structure {
                ending = Some(("structure_reversal", input.close));
            } else if failed_swing_with_macd {
                ending = Some((
                    if active.direction > 0 {
                        "lower_high_macd_confirmation"
                    } else {
                        "higher_low_macd_confirmation"
                    },
                    active.failed_swing_price,
                ));
            } else {
                state.opposite_since = None;
                active.confidence = if input.decision_direction == active.direction {
                    (config.confidence_alpha * input.decision_confidence
                        + (1.0 - config.confidence_alpha) * active.confidence)
                        .clamp(0.0, 1.0)
                } else {
                    ((1.0 - config.confidence_decay_alpha) * active.confidence).clamp(0.0, 1.0)
                };
                active.last_updated_at = input.occurred_at;
                let bucket = confidence_bucket(active.confidence);
                let update_due = state.last_emitted_at.is_none_or(|last| {
                    input.occurred_at - last >= Duration::milliseconds(config.update_interval_ms)
                });
                if ending.is_none() && bucket != state.last_emitted_bucket && update_due {
                    state.last_emitted_bucket = bucket;
                    state.last_emitted_at = Some(input.occurred_at);
                    events.push(event_from_state(sym, active, "update", ""));
                }
            }
        }

        if let Some((resolution, reference_price)) = ending {
            if let Some(mut active) = state.active.take() {
                active.last_updated_at = input.occurred_at;
                active.current_price = input.close;
                active.macd_line = macd.line;
                active.macd_signal = macd.signal;
                active.macd_converging = macd.converging;
                active.resolution_reference_price = reference_price;
                ended_direction = active.direction;
                events.push(event_from_state(sym, &active, "end", resolution));
            }
            state.setup = None;
            state.opposite_since = None;
            state.last_emitted_bucket = -1;
            state.last_emitted_at = None;
            state.rearm_direction = ended_direction;
            state.rearm_neutral_since = None;
            state.cooldown_until =
                Some(input.occurred_at + Duration::milliseconds(config.reentry_cooldown_ms));
        }

        let cooled_down = state
            .cooldown_until
            .is_none_or(|until| input.occurred_at >= until);
        if state.active.is_none() {
            let setup_expired = state.setup.as_ref().is_some_and(|setup| {
                input.occurred_at - setup.armed_at
                    >= Duration::milliseconds(config.setup_max_duration_ms)
            });
            let setup_opposed = state.setup.as_ref().is_some_and(|setup| {
                qualified_direction != 0 && qualified_direction == -setup.direction
            });
            if setup_expired || setup_opposed {
                state.setup = None;
            }

            if let Some(setup) = state.setup.as_mut() {
                if qualified_direction == setup.direction {
                    setup.confidence = setup.confidence.max(input.decision_confidence);
                    if setup.direction > 0 && input.close <= setup.breakout_price {
                        setup.breakout_was_unbroken = true;
                    } else if setup.direction < 0 && input.close >= setup.breakout_price {
                        setup.breakout_was_unbroken = true;
                    }
                }
            }

            let breakout = state.setup.as_ref().is_some_and(|setup| {
                let buffer = (threshold * 0.10).max(input.close.abs() * 0.00002);
                setup.breakout_was_unbroken
                    && if setup.direction > 0 {
                        input.close > setup.breakout_price + buffer
                    } else {
                        input.close < setup.breakout_price - buffer
                    }
            });
            let entry_conflicts_with_structure = state.setup.as_ref().is_some_and(|setup| {
                scale.structure_break_direction == -setup.direction
                    && scale.structure_break_confidence >= config.opposing_structure_confidence
            });
            if breakout && !entry_conflicts_with_structure {
                let setup = state
                    .setup
                    .take()
                    .expect("breakout requires an armed setup");
                self.next_episode_id = self.next_episode_id.saturating_add(1);
                let fallback = if setup.direction > 0 {
                    input.close * (1.0 - config.invalidation_buffer_fraction)
                } else {
                    input.close * (1.0 + config.invalidation_buffer_fraction)
                };
                let structural_is_valid = setup.invalidation_price.is_finite()
                    && setup.invalidation_price > 0.0
                    && ((setup.direction > 0 && setup.invalidation_price < input.close)
                        || (setup.direction < 0 && setup.invalidation_price > input.close));
                let active = QmdEpisodeState {
                    preset,
                    episode_id: self.next_episode_id,
                    started_at: input.occurred_at,
                    last_updated_at: input.occurred_at,
                    direction: setup.direction,
                    confidence: setup.confidence.clamp(0.0, 1.0),
                    entry_price: input.close,
                    rail_price: setup.breakout_price,
                    invalidation_price: if structural_is_valid {
                        setup.invalidation_price
                    } else {
                        fallback
                    },
                    best_price: if setup.direction > 0 {
                        observed_high
                    } else {
                        observed_low
                    },
                    last_progress_at: input.occurred_at,
                    current_price: input.close,
                    macd_line: macd.line,
                    macd_signal: macd.signal,
                    macd_converging: macd.converging,
                    trailing_swing_price: 0.0,
                    favorable_swing_price: if setup.direction > 0 {
                        scale.swing_high
                    } else {
                        scale.swing_low
                    },
                    last_scale_swing_high: scale.swing_high,
                    last_scale_swing_low: scale.swing_low,
                    failed_swing_price: 0.0,
                    failed_swing_confirmed: false,
                    resolution_reference_price: setup.breakout_price,
                };
                state.last_emitted_bucket = confidence_bucket(active.confidence);
                state.last_emitted_at = Some(input.occurred_at);
                events.push(event_from_state(sym, &active, "start", "swing_breakout"));
                state.active = Some(active);
            }
        }

        let may_arm = state.active.is_none()
            && state.setup.is_none()
            && cooled_down
            && qualified_direction != 0
            && qualified_direction != state.rearm_direction
            && input.close.is_finite()
            && input.close > 0.0;
        if may_arm {
            let breakout_price = if qualified_direction > 0 {
                scale.swing_high
            } else {
                scale.swing_low
            };
            let invalidation_price = if qualified_direction > 0 {
                scale.swing_low
            } else {
                scale.swing_high
            };
            if breakout_price.is_finite() && breakout_price > 0.0 {
                state.setup = Some(QmdEpisodeSetup {
                    armed_at: input.occurred_at,
                    direction: qualified_direction,
                    breakout_price,
                    invalidation_price,
                    breakout_was_unbroken: if qualified_direction > 0 {
                        prior_close.is_some_and(|price| price <= breakout_price)
                            || input.close <= breakout_price
                    } else {
                        prior_close.is_some_and(|price| price >= breakout_price)
                            || input.close >= breakout_price
                    },
                    confidence: input.decision_confidence.clamp(0.0, 1.0),
                });
            }
        }
        if input.close.is_finite() && input.close > 0.0 {
            state.last_close = Some(input.close);
        }
    }
}

fn confidence_bucket(confidence: f64) -> i16 {
    (confidence.clamp(0.0, 1.0) * 20.0).floor() as i16
}

fn event_from_state(
    sym: &str,
    state: &QmdEpisodeState,
    event_type: &str,
    resolution: &str,
) -> QmdEpisodeEvent {
    QmdEpisodeEvent {
        algorithm_version: QMD_EPISODE_ALGORITHM_VERSION,
        sym: sym.to_string(),
        preset: state.preset,
        episode_id: state.episode_id,
        event_type: event_type.to_string(),
        occurred_at: state.last_updated_at,
        started_at: state.started_at,
        direction: state.direction,
        confidence: state.confidence,
        entry_price: state.entry_price,
        rail_price: state.rail_price,
        invalidation_price: state.invalidation_price,
        best_price: state.best_price,
        maximum_favorable_move_pct: if state.entry_price > 0.0 {
            state.direction as f64 * (state.best_price - state.entry_price) / state.entry_price
                * 100.0
        } else {
            0.0
        },
        event_price: state.current_price,
        reference_price: state.resolution_reference_price,
        macd_line: state.macd_line,
        macd_signal: state.macd_signal,
        macd_converging: state.macd_converging,
        resolution: resolution.to_string(),
    }
}

fn materially_changed(value: f64, previous: f64, tolerance: f64) -> bool {
    value.is_finite()
        && value > 0.0
        && (!previous.is_finite()
            || previous <= 0.0
            || (value - previous).abs() > tolerance.max(0.000_001))
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn input(ms: i64, direction: i8, confidence: f64) -> QmdEpisodeInput {
        let scale = QmdEpisodeScaleInput {
            direction: 0,
            confidence: 0.0,
            threshold: 0.5,
            swing_high: 102.0,
            swing_low: 98.0,
            structure_break_direction: 0,
            structure_break_confidence: 0.0,
        };
        QmdEpisodeInput {
            occurred_at: Utc.timestamp_millis_opt(ms).unwrap(),
            close: 100.0,
            high: 100.0,
            low: 100.0,
            decision_direction: direction,
            decision_confidence: confidence,
            micro: scale,
            tactical: scale,
            context: scale,
        }
    }

    fn breakout(ms: i64, direction: i8, confidence: f64) -> QmdEpisodeInput {
        let mut value = input(ms, direction, confidence);
        value.close = if direction > 0 { 103.0 } else { 97.0 };
        value.high = value.close;
        value.low = value.close;
        value
    }

    #[test]
    fn presets_arm_on_decision_and_start_together_only_at_the_swing_break() {
        let mut engine = QmdEpisodeEngine::default();
        assert!(engine.update("TEST", input(1_000, 1, 0.50)).is_empty());
        let events = engine.update("TEST", breakout(1_100, 1, 0.50));
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].preset, QmdEpisodePreset::Micro);
        assert_eq!(events[1].preset, QmdEpisodePreset::Tactical);
        assert!(engine
            .states()
            .iter()
            .all(|state| state.started_at.timestamp_millis() == 1_100));
        assert!(events
            .iter()
            .all(|event| event.resolution == "swing_breakout"));
    }

    #[test]
    fn neutral_qmd_and_stalled_progress_do_not_end_a_directional_regime() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.40));
        engine.update("TEST", breakout(1_100, 1, 0.40));
        assert!(engine
            .update("TEST", input(1_500, 0, 0.0))
            .iter()
            .all(|event| event.event_type != "end"));
        for timestamp in (2_000..180_000).step_by(500) {
            assert!(engine
                .update("TEST", input(timestamp, 0, 0.0))
                .iter()
                .all(|event| event.event_type != "end"));
        }
        assert!(engine
            .states()
            .iter()
            .any(|state| state.preset == QmdEpisodePreset::Micro));
    }

    #[test]
    fn opposite_signal_ends_then_requires_its_own_swing_break() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        engine.update("TEST", breakout(1_050, 1, 0.60));
        assert!(engine
            .update("TEST", input(1_100, -1, 0.70))
            .iter()
            .all(|event| event.event_type != "end"));
        let events = engine.update("TEST", input(1_600, -1, 0.70));
        assert!(events.iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "opposite_qmd_decision"
        }));
        assert!(events.iter().all(|event| event.event_type != "start"));
        assert!(engine
            .update("TEST", input(3_700, -1, 0.70))
            .iter()
            .all(|event| event.event_type != "start"));
        assert!(engine
            .update("TEST", breakout(3_800, -1, 0.70))
            .iter()
            .any(|event| event.preset == QmdEpisodePreset::Micro
                && event.event_type == "start"
                && event.direction == -1));
    }

    #[test]
    fn an_ended_episode_does_not_restart_from_the_same_persistent_signal() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        engine.update("TEST", breakout(1_050, 1, 0.60));
        let mut invalidated = input(1_100, 1, 0.60);
        invalidated.close = 97.0;
        invalidated.high = 97.0;
        invalidated.low = 97.0;
        assert!(engine
            .update("TEST", invalidated)
            .iter()
            .any(|event| event.event_type == "end"));
        for timestamp in (2_500..6_000).step_by(500) {
            assert!(engine
                .update("TEST", input(timestamp, 1, 0.60))
                .iter()
                .all(|event| event.event_type != "start"));
        }
        engine.update("TEST", input(6_000, 0, 0.0));
        engine.update("TEST", input(8_100, 0, 0.0));
        assert!(engine.update("TEST", input(8_200, 1, 0.60)).is_empty());
        assert!(engine
            .update("TEST", breakout(8_300, 1, 0.60))
            .iter()
            .any(|event| event.preset == QmdEpisodePreset::Micro && event.event_type == "start"));
    }

    #[test]
    fn confirmed_opposing_structure_ends_the_regime_without_a_time_delay() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        engine.update("TEST", breakout(1_050, 1, 0.60));
        let mut opposed = input(1_100, 1, 0.60);
        opposed.micro.structure_break_direction = -1;
        opposed.micro.structure_break_confidence = 0.90;
        assert!(engine.update("TEST", opposed).iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "structure_reversal"
        }));
    }

    #[test]
    fn a_break_of_the_protected_higher_low_requires_bearish_macd_confirmation() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        engine.update("TEST", breakout(1_100, 1, 0.60));
        let mut new_high = input(2_000, 1, 0.60);
        new_high.close = 105.0;
        new_high.high = 105.0;
        new_high.low = 104.8;
        engine.update("TEST", new_high);
        let mut higher_low = input(2_050, 0, 0.0);
        higher_low.close = 104.0;
        higher_low.high = 104.1;
        higher_low.low = 104.0;
        higher_low.micro.swing_low = 103.0;
        engine.update("TEST", higher_low);
        let mut exhausted = input(4_200, 0, 0.0);
        exhausted.close = 102.8;
        exhausted.high = 102.9;
        exhausted.low = 102.8;
        exhausted.micro.swing_high = 105.0;
        exhausted.micro.swing_low = 103.0;
        assert!(engine
            .update("TEST", exhausted)
            .iter()
            .all(|event| event.event_type != "end"));

        let micro = engine
            .presets
            .get_mut(&QmdEpisodePreset::Micro)
            .expect("micro preset exists");
        micro.macd.bucket = Some(4);
        micro.macd.bucket_close = 102.5;
        micro.macd.reading = MacdReading {
            line: -0.10,
            signal: -0.02,
            ready: true,
            converging: false,
        };
        exhausted.occurred_at = Utc.timestamp_millis_opt(4_000).unwrap();
        assert!(engine.update("TEST", exhausted).iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "protected_swing_break_macd_confirmation"
        }));
    }

    #[test]
    fn a_confirmed_lower_high_with_bearish_macd_ends_the_long_regime() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        engine.update("TEST", breakout(1_100, 1, 0.60));

        let mut higher_high = input(2_000, 0, 0.0);
        higher_high.close = 105.0;
        higher_high.high = 105.0;
        higher_high.low = 104.5;
        higher_high.micro.swing_high = 105.0;
        engine.update("TEST", higher_high);

        let micro = engine
            .presets
            .get_mut(&QmdEpisodePreset::Micro)
            .expect("micro preset exists");
        micro.macd.bucket = Some(3);
        micro.macd.bucket_close = 103.8;
        micro.macd.reading = MacdReading {
            line: -0.10,
            signal: -0.02,
            ready: true,
            converging: false,
        };
        let mut lower_high = input(3_000, 0, 0.0);
        lower_high.close = 103.8;
        lower_high.high = 104.0;
        lower_high.low = 103.5;
        lower_high.micro.swing_high = 104.0;
        assert!(engine.update("TEST", lower_high).iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "lower_high_macd_confirmation"
                && (event.reference_price - 104.0).abs() < f64::EPSILON
        }));
    }

    #[test]
    fn quote_only_zero_range_cannot_create_a_phantom_short_gain() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, -1, 0.60));
        engine.update("TEST", breakout(1_100, -1, 0.60));
        let mut quote_only = input(1_200, -1, 0.60);
        quote_only.close = 97.5;
        quote_only.high = 0.0;
        quote_only.low = 0.0;
        engine.update("TEST", quote_only);
        let state = engine
            .states()
            .into_iter()
            .find(|state| state.preset == QmdEpisodePreset::Micro)
            .expect("micro episode should remain active");
        assert_eq!(state.best_price, 97.0);
    }
}
