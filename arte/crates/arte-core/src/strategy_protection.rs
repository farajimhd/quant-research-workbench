//! V7 swing trailing and target advancement. Proposals never update broker state.
use crate::market::Bar;
use crate::strategy_lifecycle::{Phase, PositionSetup};
use crate::strategy_targets::{
    eligible, initial_swing, stop_below, valid_level, CausalLevels, Policy as LevelPolicy, Swing,
    SwingSelection, Target, TargetLevel,
};
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub tick: f64,
    pub price_only: bool,
    pub minimum_progress_r: f64,
    pub requires_current_gain: bool,
    pub current_gain_requires_bid: bool,
    pub requires_breakout: bool,
    pub activation_r: f64,
    pub maximum_pending_targets: usize,
    pub levels: LevelPolicy,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum ActiveTarget {
    Structure(Box<Target>),
    Official { price: f64 },
}
impl ActiveTarget {
    pub fn price(&self) -> f64 {
        match self {
            Self::Structure(t) => t.price,
            Self::Official { price } => *price,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StopProposal {
    pub price: f64,
    pub swing: Swing,
    pub at_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetProposal {
    pub target: ActiveTarget,
    pub triggering_breakout: Option<TargetLevel>,
    pub at_ns: u64,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct State {
    pub desired_stop: Option<StopProposal>,
    pub best_close: Option<f64>,
    pending_targets: Vec<TargetLevel>,
    last_bar_ns: u64,
}
pub struct Frame<'a> {
    pub bar: &'a Bar,
    pub contiguous: bool,
    pub bid: f64,
    pub ask: f64,
    pub setup: &'a PositionSetup,
    pub swings: &'a [Swing],
    pub levels: &'a [TargetLevel],
    pub detector_at_ns: Option<u64>,
    pub detector_fingerprint: &'a str,
    pub active_stop: f64,
    pub active_target: &'a ActiveTarget,
    pub regular: bool,
    pub official_target: Option<f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evaluation {
    pub stop: Option<StopProposal>,
    pub target: Option<TargetProposal>,
    pub best_close: f64,
    pub current_gain_ready: bool,
    pub progress_ready: bool,
}
fn positive(v: f64) -> bool {
    v.is_finite() && v > 0.
}
impl State {
    pub fn evaluate(&mut self, f: &Frame<'_>, p: &Policy) -> Result<Evaluation> {
        let b = f.bar;
        if b.end_ns.checked_sub(b.start_ns) != Some(1_000_000_000)
            || ![
                b.open,
                b.close,
                b.high,
                b.low,
                f.bid,
                f.ask,
                f.active_stop,
                f.active_target.price(),
                p.tick,
            ]
            .into_iter()
            .all(positive)
            || b.high < b.open.max(b.close)
            || b.low > b.open.min(b.close)
            || f.bid > f.ask
            || b.end_ns < self.last_bar_ns
            || b.end_ns < f.setup.confirmed_at_ns
            || f.detector_at_ns.is_some_and(|v| v > b.end_ns)
            || f.official_target.is_some_and(|v| !positive(v))
            || [p.minimum_progress_r, p.activation_r]
                .into_iter()
                .any(|v| !v.is_finite() || v < 0.)
            || p.maximum_pending_targets == 0
        {
            return Err(Error::Invalid(
                "invalid protection observation or policy".into(),
            ));
        }
        let mut setup = f.setup.clone();
        if !positive(setup.best_close) {
            return Err(Error::Invalid("invalid best close".into()));
        }
        setup.best_close = setup
            .best_close
            .max(self.best_close.unwrap_or(b.close))
            .max(b.close);
        let current_gain_ready = !p.requires_current_gain
            || setup.initial_fill_price.is_some_and(|v| {
                positive(v)
                    && b.close >= v - 1e-9
                    && (!p.current_gain_requires_bid || f.bid >= v - 1e-9)
            });
        let progress_ready =
            p.minimum_progress_r == 0. || setup.risk_progress_ready(p.minimum_progress_r);
        let mut output = Evaluation {
            stop: None,
            target: None,
            best_close: setup.best_close,
            current_gain_ready,
            progress_ready,
        };
        if b.end_ns == self.last_bar_ns {
            return Ok(output);
        }
        let mut next = self.clone();
        next.best_close = Some(setup.best_close);
        let bid = if p.price_only { b.close } else { f.bid };
        let ask = if p.price_only { b.close } else { f.ask };
        if f.detector_at_ns == Some(b.end_ns)
            && !f.detector_fingerprint.is_empty()
            && progress_ready
            && current_gain_ready
            && (!p.requires_breakout
                || setup.phase == Phase::PostBreakout
                || setup.risk_progress_ready(p.activation_r))
        {
            if let Some(swing) = initial_swing(
                f.swings,
                b.close.min(bid),
                b.end_ns,
                &SwingSelection {
                    closest: true,
                    price_only: p.price_only,
                    confirmed_after_ns: setup.confirmed_at_ns,
                    ..Default::default()
                },
            )? {
                let price = stop_below(swing.lower, &p.levels, p.tick)?;
                if price
                    > f.active_stop
                        .max(next.desired_stop.as_ref().map_or(0., |s| s.price))
                {
                    next.desired_stop = Some(StopProposal {
                        price,
                        swing: swing.clone(),
                        at_ns: b.end_ns,
                    });
                }
            }
        }
        if current_gain_ready {
            output.stop = next
                .desired_stop
                .as_ref()
                .filter(|s| f.active_stop < s.price && s.price < bid)
                .cloned();
            if let Some(s) = &mut output.stop {
                s.at_ns = b.end_ns;
            }
        }
        if !f.contiguous || f.regular {
            next.pending_targets.clear();
        }
        if !f.regular && f.contiguous {
            if let ActiveTarget::Structure(t) = f.active_target {
                let trigger = &t.trigger;
                valid_level(trigger)?;
                if trigger.geometry.confirmed_at_ns > b.end_ns {
                    return Err(Error::Invalid("future active target trigger".into()));
                }
                if (eligible(trigger, &p.levels) || trigger.synthetic)
                    && b.close > trigger.geometry.upper
                {
                    if let Some(existing) = next
                        .pending_targets
                        .iter_mut()
                        .find(|v| v.geometry.id == trigger.geometry.id)
                    {
                        *existing = trigger.clone();
                    } else {
                        if next.pending_targets.len() >= p.maximum_pending_targets {
                            return Err(Error::Capacity(
                                "target confirmation limit; no truncation".into(),
                            ));
                        }
                        next.pending_targets.push(trigger.clone());
                    }
                }
            }
        }
        next.pending_targets.retain(|r| b.close > r.geometry.upper);
        if f.regular {
            if let Some(price) = f
                .official_target
                .filter(|v| *v != f.active_target.price() && *v > b.close.max(ask))
            {
                output.target = Some(TargetProposal {
                    target: ActiveTarget::Official { price },
                    triggering_breakout: None,
                    at_ns: b.end_ns,
                });
            }
        } else if b.close >= b.open {
            let view = CausalLevels::new(f.levels, b.end_ns)?;
            if matches!(f.active_target, ActiveTarget::Official { .. }) {
                if let Some(target) =
                    view.available(b.close.max(ask), 0., None, None, &p.levels, p.tick)?
                {
                    if target.price != f.active_target.price() && target.price > b.close.max(ask) {
                        output.target = Some(TargetProposal {
                            target: ActiveTarget::Structure(Box::new(target)),
                            triggering_breakout: None,
                            at_ns: b.end_ns,
                        });
                    }
                }
            } else {
                next.pending_targets
                    .sort_by(|a, b| a.geometry.upper.total_cmp(&b.geometry.upper));
                for trigger in next.pending_targets.drain(..) {
                    if let Some(target) = view.available(
                        b.close.max(ask),
                        f.active_target.price(),
                        Some(trigger.geometry.upper),
                        Some(b.close),
                        &p.levels,
                        p.tick,
                    )? {
                        if target.price > f.active_target.price()
                            && target.price > b.close.max(ask)
                            && output
                                .target
                                .as_ref()
                                .is_none_or(|v| target.price >= v.target.price())
                        {
                            output.target = Some(TargetProposal {
                                target: ActiveTarget::Structure(Box::new(target)),
                                triggering_breakout: Some(trigger),
                                at_ns: b.end_ns,
                            });
                        }
                    }
                }
            }
        }
        next.last_bar_ns = b.end_ns;
        *self = next;
        Ok(output)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    const S: u64 = 1_000_000_000;
    fn scenario(
        edit: impl FnOnce(&mut Frame<'_>, &mut Policy),
        state: &mut State,
    ) -> Result<Evaluation> {
        let bar = Bar {
            start_ns: 10 * S,
            end_ns: 11 * S,
            open: 11.,
            close: 11.1,
            high: 11.2,
            low: 10.9,
            volume: 1.,
            notional: 11.1,
            trades: 1,
        };
        let setup = PositionSetup {
            confirmed_at_ns: S,
            phase: Phase::PostBreakout,
            breakout_threshold: 10.5,
            breakout_at_ns: Some(5 * S),
            entry_bar: bar.clone(),
            initial_fill_price: Some(10.),
            initial_risk: Some(0.5),
            best_close: 11.,
            entry_failure_recovery: None,
        };
        let swing = Swing {
            id: "fresh".into(),
            lower: 10.7,
            price: 10.8,
            upper: 10.9,
            pivot_at_ns: 8 * S,
            confirmed_at_ns: 9 * S,
            support: true,
            active: true,
        };
        let swings = [swing];
        let target = ActiveTarget::Official { price: 12. };
        let mut f = Frame {
            bar: &bar,
            contiguous: true,
            bid: 11.09,
            ask: 11.11,
            setup: &setup,
            swings: &swings,
            levels: &[],
            detector_at_ns: Some(11 * S),
            detector_fingerprint: "verified",
            active_stop: 9.5,
            active_target: &target,
            regular: true,
            official_target: Some(11.9),
        };
        let mut p = Policy {
            tick: 0.01,
            price_only: true,
            minimum_progress_r: 1.,
            requires_current_gain: true,
            current_gain_requires_bid: true,
            requires_breakout: true,
            activation_r: 2.,
            maximum_pending_targets: 16,
            levels: LevelPolicy {
                distance_fraction: 0.1,
                offset_ticks: 1.,
                stop_buffer_bps: 10.,
                all_origins: true,
                encounter_transitions: true,
            },
        };
        edit(&mut f, &mut p);
        state.evaluate(&f, &p)
    }
    #[test]
    fn proposals_leave_broker_protection_unchanged() {
        let mut state = State::default();
        let output = scenario(|_, _| {}, &mut state).unwrap();
        assert!(output.stop.unwrap().price > 10.6);
        assert_eq!(output.target.unwrap().target.price(), 11.9);
        assert!(state.desired_stop.is_some());
        // Same broker snapshot and same bar must not emit duplicate actions.
        let repeat = scenario(|_, _| {}, &mut state).unwrap();
        assert!(repeat.stop.is_none() && repeat.target.is_none());
    }
    #[test]
    fn real_bid_current_gain_guard_survives_price_only_mode() {
        let output = scenario(
            |f, _| {
                f.bid = 9.9;
            },
            &mut State::default(),
        )
        .unwrap();
        assert!(!output.current_gain_ready);
        assert!(output.stop.is_none());
    }
    #[test]
    fn stale_detector_cannot_select_new_swing() {
        let output = scenario(
            |f, _| {
                f.detector_at_ns = Some(10 * S);
            },
            &mut State::default(),
        )
        .unwrap();
        assert!(output.stop.is_none());
        assert!(output.target.is_some());
    }
    #[test]
    fn missing_official_target_never_uses_synthetic_regular_target() {
        let output = scenario(
            |f, _| {
                f.official_target = None;
            },
            &mut State::default(),
        )
        .unwrap();
        assert!(output.target.is_none());
        let extended = scenario(
            |f, _| {
                f.regular = false;
            },
            &mut State::default(),
        )
        .unwrap();
        assert!(matches!(
            extended.target.unwrap().target,
            ActiveTarget::Structure(_)
        ));
    }
    #[test]
    fn red_target_break_waits_for_non_red_confirmation() {
        scenario(
            |f, p| {
                let mut selected = CausalLevels::new(&[], f.bar.end_ns)
                    .unwrap()
                    .available(10., 0., None, None, &p.levels, p.tick)
                    .unwrap()
                    .unwrap();
                selected.price = 11.5;
                selected.trigger.geometry.price = 10.8;
                selected.trigger.geometry.lower = 10.7;
                selected.trigger.geometry.upper = 10.9;
                let active = ActiveTarget::Structure(Box::new(selected));
                let mut red = f.bar.clone();
                red.open = 11.2;
                let frame = Frame {
                    bar: &red,
                    active_target: &active,
                    regular: false,
                    ..*f
                };
                let mut state = State::default();
                assert!(state.evaluate(&frame, p).unwrap().target.is_none());
                assert_eq!(state.pending_targets.len(), 1);
                let mut green = red.clone();
                green.start_ns += S;
                green.end_ns += S;
                green.open = 11.;
                let frame = Frame {
                    bar: &green,
                    detector_at_ns: Some(green.end_ns),
                    ..frame
                };
                let output = state.evaluate(&frame, p).unwrap();
                assert!(output.target.unwrap().target.price() > 11.5);
                assert!(state.pending_targets.is_empty());
                assert_eq!(active.price(), 11.5);
            },
            &mut State::default(),
        )
        .unwrap();
    }
}
