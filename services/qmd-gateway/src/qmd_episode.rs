use chrono::{DateTime, Duration, Utc};
use serde::Serialize;
use std::collections::HashMap;

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
                exhaustion_confidence: 0.18,
                exhaustion_dwell_ms: 15_000,
                max_duration_ms: 120_000,
                invalidation_buffer_fraction: 0.0015,
                confidence_alpha: 0.55,
                confidence_decay_alpha: 0.06,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 2_000,
                update_interval_ms: 1_000,
            },
            Self::Tactical => QmdEpisodeConfig {
                start_confidence: 0.45,
                opposite_confidence: 0.50,
                opposite_dwell_ms: 1_500,
                exhaustion_confidence: 0.22,
                exhaustion_dwell_ms: 60_000,
                max_duration_ms: 900_000,
                invalidation_buffer_fraction: 0.0035,
                confidence_alpha: 0.25,
                confidence_decay_alpha: 0.02,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 10_000,
                update_interval_ms: 5_000,
            },
            Self::Context => QmdEpisodeConfig {
                start_confidence: 0.55,
                opposite_confidence: 0.60,
                opposite_dwell_ms: 5_000,
                exhaustion_confidence: 0.28,
                exhaustion_dwell_ms: 300_000,
                max_duration_ms: 3_600_000,
                invalidation_buffer_fraction: 0.0075,
                confidence_alpha: 0.10,
                confidence_decay_alpha: 0.005,
                opposing_structure_confidence: 0.65,
                reentry_cooldown_ms: 30_000,
                update_interval_ms: 15_000,
            },
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct QmdEpisodeConfig {
    start_confidence: f64,
    opposite_confidence: f64,
    opposite_dwell_ms: i64,
    exhaustion_confidence: f64,
    exhaustion_dwell_ms: i64,
    max_duration_ms: i64,
    invalidation_buffer_fraction: f64,
    confidence_alpha: f64,
    confidence_decay_alpha: f64,
    opposing_structure_confidence: f64,
    reentry_cooldown_ms: i64,
    update_interval_ms: i64,
}

#[derive(Clone, Copy, Debug)]
pub struct QmdEpisodeScaleInput {
    pub direction: i8,
    pub confidence: f64,
    pub swing_high: f64,
    pub swing_low: f64,
}

#[derive(Clone, Copy, Debug)]
pub struct QmdEpisodeInput {
    pub occurred_at: DateTime<Utc>,
    pub close: f64,
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
}

#[derive(Clone, Debug, Serialize)]
pub struct QmdEpisodeEvent {
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
    pub resolution: String,
}

#[derive(Clone, Debug, Default)]
struct PresetState {
    active: Option<QmdEpisodeState>,
    low_confidence_since: Option<DateTime<Utc>>,
    last_emitted_bucket: i16,
    last_emitted_at: Option<DateTime<Utc>>,
    cooldown_until: Option<DateTime<Utc>>,
    opposite_since: Option<DateTime<Utc>>,
    opposing_structure_since: Option<DateTime<Utc>>,
    rearm_direction: i8,
    rearm_neutral_since: Option<DateTime<Utc>>,
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
        let state = self.presets.entry(preset).or_default();
        let mut ending = None;
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
            let elapsed_ms = (input.occurred_at - active.started_at).num_milliseconds();
            let opposite_decision = input.decision_direction == -active.direction
                && input.decision_confidence >= config.opposite_confidence;
            let opposing_structure = scale.direction == -active.direction
                && scale.confidence >= config.opposing_structure_confidence;
            let invalidated = (active.direction > 0 && input.close <= active.invalidation_price)
                || (active.direction < 0 && input.close >= active.invalidation_price);
            if opposite_decision {
                let since = state.opposite_since.get_or_insert(input.occurred_at);
                if input.occurred_at - *since >= Duration::milliseconds(config.opposite_dwell_ms) {
                    ending = Some("opposite_signal");
                }
            } else if invalidated {
                ending = Some("invalidation");
            } else if elapsed_ms >= config.max_duration_ms {
                ending = Some("maximum_duration");
            } else {
                state.opposite_since = None;
                let favorable_progress = if active.direction > 0 {
                    input.close > active.best_price
                } else {
                    input.close < active.best_price
                };
                if favorable_progress {
                    active.best_price = input.close;
                    active.last_progress_at = input.occurred_at;
                }
                active.confidence = if input.decision_direction == active.direction {
                    (config.confidence_alpha * input.decision_confidence
                        + (1.0 - config.confidence_alpha) * active.confidence)
                        .clamp(0.0, 1.0)
                } else {
                    ((1.0 - config.confidence_decay_alpha) * active.confidence).clamp(0.0, 1.0)
                };
                active.last_updated_at = input.occurred_at;
                let progress_stale = input.occurred_at - active.last_progress_at
                    >= Duration::milliseconds(config.exhaustion_dwell_ms);
                if active.confidence <= config.exhaustion_confidence {
                    state.low_confidence_since.get_or_insert(input.occurred_at);
                } else {
                    state.low_confidence_since = None;
                }
                if opposing_structure {
                    state
                        .opposing_structure_since
                        .get_or_insert(input.occurred_at);
                } else {
                    state.opposing_structure_since = None;
                }
                let weak_for_dwell = state.low_confidence_since.is_some_and(|since| {
                    input.occurred_at - since >= Duration::milliseconds(config.exhaustion_dwell_ms)
                });
                let structure_opposed_for_dwell =
                    state.opposing_structure_since.is_some_and(|since| {
                        input.occurred_at - since
                            >= Duration::milliseconds(config.exhaustion_dwell_ms)
                    });
                if progress_stale && structure_opposed_for_dwell {
                    ending = Some("opposing_structure");
                } else if progress_stale && weak_for_dwell {
                    ending = Some("evidence_exhausted");
                }
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

        if let Some(resolution) = ending {
            if let Some(mut active) = state.active.take() {
                active.last_updated_at = input.occurred_at;
                ended_direction = active.direction;
                events.push(event_from_state(sym, &active, "end", resolution));
            }
            state.low_confidence_since = None;
            state.opposite_since = None;
            state.opposing_structure_since = None;
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
        let immediate_flip = ended_direction != 0 && input.decision_direction == -ended_direction;
        let may_start = state.active.is_none()
            && (cooled_down || immediate_flip)
            && qualified_direction != 0
            && qualified_direction != state.rearm_direction
            && input.close.is_finite()
            && input.close > 0.0;
        if may_start {
            self.next_episode_id = self.next_episode_id.saturating_add(1);
            let direction = input.decision_direction.signum();
            let structural_invalidation = if direction > 0 {
                scale.swing_low
            } else {
                scale.swing_high
            };
            let structural_is_valid = structural_invalidation.is_finite()
                && structural_invalidation > 0.0
                && ((direction > 0 && structural_invalidation < input.close)
                    || (direction < 0 && structural_invalidation > input.close));
            let fallback = if direction > 0 {
                input.close * (1.0 - config.invalidation_buffer_fraction)
            } else {
                input.close * (1.0 + config.invalidation_buffer_fraction)
            };
            let active = QmdEpisodeState {
                preset,
                episode_id: self.next_episode_id,
                started_at: input.occurred_at,
                last_updated_at: input.occurred_at,
                direction,
                confidence: input.decision_confidence.clamp(0.0, 1.0),
                entry_price: input.close,
                rail_price: input.close,
                invalidation_price: if structural_is_valid {
                    structural_invalidation
                } else {
                    fallback
                },
                best_price: input.close,
                last_progress_at: input.occurred_at,
            };
            state.last_emitted_bucket = confidence_bucket(active.confidence);
            state.last_emitted_at = Some(input.occurred_at);
            events.push(event_from_state(sym, &active, "start", ""));
            state.active = Some(active);
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
        resolution: resolution.to_string(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::TimeZone;

    fn input(ms: i64, direction: i8, confidence: f64) -> QmdEpisodeInput {
        let scale = QmdEpisodeScaleInput {
            direction: 0,
            confidence: 0.0,
            swing_high: 102.0,
            swing_low: 98.0,
        };
        QmdEpisodeInput {
            occurred_at: Utc.timestamp_millis_opt(ms).unwrap(),
            close: 100.0,
            decision_direction: direction,
            decision_confidence: confidence,
            micro: scale,
            tactical: scale,
            context: scale,
        }
    }

    #[test]
    fn presets_start_independently_at_the_same_canonical_timestamp() {
        let mut engine = QmdEpisodeEngine::default();
        let events = engine.update("TEST", input(1_000, 1, 0.50));
        assert_eq!(events.len(), 2);
        assert_eq!(events[0].preset, QmdEpisodePreset::Micro);
        assert_eq!(events[1].preset, QmdEpisodePreset::Tactical);
        assert!(engine
            .states()
            .iter()
            .all(|state| state.started_at.timestamp_millis() == 1_000));
    }

    #[test]
    fn micro_exhaustion_requires_causal_dwell() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.40));
        assert!(engine
            .update("TEST", input(1_500, 0, 0.0))
            .iter()
            .all(|event| event.event_type != "end"));
        let mut ended = false;
        for timestamp in (2_000..40_000).step_by(500) {
            ended |= engine
                .update("TEST", input(timestamp, 0, 0.0))
                .iter()
                .any(|event| {
                    event.preset == QmdEpisodePreset::Micro
                        && event.event_type == "end"
                        && event.resolution == "evidence_exhausted"
                });
        }
        assert!(ended);
    }

    #[test]
    fn opposite_signal_ends_and_flips_without_waiting_for_a_chart_bar() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        assert!(engine
            .update("TEST", input(1_100, -1, 0.70))
            .iter()
            .all(|event| event.event_type != "end"));
        let events = engine.update("TEST", input(1_600, -1, 0.70));
        assert!(events.iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "opposite_signal"
        }));
        assert!(events.iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "start"
                && event.direction == -1
        }));
    }

    #[test]
    fn an_ended_episode_does_not_restart_from_the_same_persistent_signal() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        let mut invalidated = input(1_100, 1, 0.60);
        invalidated.close = 97.0;
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
        assert!(engine
            .update("TEST", input(8_200, 1, 0.60))
            .iter()
            .any(|event| {
                event.preset == QmdEpisodePreset::Micro && event.event_type == "start"
            }));
    }

    #[test]
    fn opposing_structure_requires_persistence_and_stalled_price() {
        let mut engine = QmdEpisodeEngine::default();
        engine.update("TEST", input(1_000, 1, 0.60));
        let mut opposed = input(1_100, 1, 0.60);
        opposed.micro.direction = -1;
        opposed.micro.confidence = 0.90;
        assert!(engine
            .update("TEST", opposed)
            .iter()
            .all(|event| event.event_type != "end"));
        opposed.occurred_at = Utc.timestamp_millis_opt(15_900).unwrap();
        assert!(engine
            .update("TEST", opposed)
            .iter()
            .all(|event| event.event_type != "end"));
        opposed.occurred_at = Utc.timestamp_millis_opt(16_200).unwrap();
        assert!(engine.update("TEST", opposed).iter().any(|event| {
            event.preset == QmdEpisodePreset::Micro
                && event.event_type == "end"
                && event.resolution == "opposing_structure"
        }));
    }
}
