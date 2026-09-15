//! Position-owned setup phase and recovery rules from the frozen v7_setup source.
use crate::market::Bar;
use crate::strategy_setup::Range;
use crate::strategy_targets::Swing;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Phase {
    Building,
    PostBreakout,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PositionSetup {
    pub confirmed_at_ns: u64,
    pub phase: Phase,
    pub breakout_threshold: f64,
    pub breakout_at_ns: Option<u64>,
    pub entry_bar: Bar,
    pub initial_fill_price: Option<f64>,
    pub initial_risk: Option<f64>,
    pub best_close: f64,
    pub entry_failure_recovery: Option<f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct EntryFailure {
    pub entry_low: f64,
    pub threshold: f64,
    pub close: f64,
    pub confirmed_at_ns: u64,
    pub elapsed_ns: u64,
    pub reclaim_threshold: f64,
}
fn valid_bar(bar: &Bar) -> Result<()> {
    if bar.start_ns >= bar.end_ns
        || [bar.open, bar.high, bar.low, bar.close]
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.)
        || bar.low > bar.open.min(bar.close)
        || bar.high < bar.open.max(bar.close)
    {
        return Err(Error::Invalid("invalid lifecycle bar".into()));
    }
    Ok(())
}
impl PositionSetup {
    pub fn advance_phase(
        &mut self,
        bar: &Bar,
        fresh: bool,
        minimum_progress_r: f64,
    ) -> Result<bool> {
        valid_bar(bar)?;
        if !minimum_progress_r.is_finite()
            || minimum_progress_r < 0.
            || !self.breakout_threshold.is_finite()
            || self.breakout_threshold <= 0.
        {
            return Err(Error::Invalid("invalid setup breakout policy".into()));
        }
        if !fresh || self.phase == Phase::PostBreakout {
            return Ok(false);
        }
        if minimum_progress_r > 0. {
            let Some((fill, risk)) = self
                .initial_fill_price
                .zip(self.initial_risk)
                .filter(|(f, r)| f.is_finite() && r.is_finite() && *f > 0. && *r > 0.)
            else {
                return Ok(false);
            };
            if bar.close < fill + minimum_progress_r * risk - 1e-9 {
                return Ok(false);
            }
        }
        if bar.end_ns > self.confirmed_at_ns
            && bar.close >= bar.open
            && bar.close > self.breakout_threshold
        {
            self.phase = Phase::PostBreakout;
            self.breakout_at_ns = Some(bar.end_ns);
            return Ok(true);
        }
        Ok(false)
    }
    pub fn risk_progress_ready(&self, multiple: f64) -> bool {
        multiple.is_finite()
            && multiple > 0.
            && self
                .initial_fill_price
                .zip(self.initial_risk)
                .is_some_and(|(fill, risk)| {
                    fill.is_finite()
                        && risk.is_finite()
                        && fill > 0.
                        && risk > 0.
                        && self.best_close >= fill + multiple * risk - 1e-9
                })
    }
    pub fn entry_failure(
        &self,
        bar: &Bar,
        fresh: bool,
        contiguous: bool,
        window_ns: u64,
        buffer_ticks: f64,
        tick: f64,
    ) -> Result<Option<EntryFailure>> {
        valid_bar(bar)?;
        valid_bar(&self.entry_bar)?;
        if !buffer_ticks.is_finite() || buffer_ticks < 0. || !tick.is_finite() || tick <= 0. {
            return Err(Error::Invalid("invalid setup failure buffer".into()));
        }
        if window_ns == 0
            || !fresh
            || !contiguous
            || self.phase != Phase::Building
            || bar.end_ns <= self.confirmed_at_ns
        {
            return Ok(None);
        }
        let elapsed = bar.end_ns - self.confirmed_at_ns;
        let buffer = tick * buffer_ticks;
        let threshold = self.entry_bar.low - buffer;
        if !threshold.is_finite() {
            return Err(Error::Invalid("failure threshold overflow".into()));
        }
        // Source compares decimal prices rounded to nine places.
        if elapsed <= window_ns
            && bar.close < bar.open
            && (bar.close * 1e9).round_ties_even() <= (threshold * 1e9).round_ties_even()
        {
            return Ok(Some(EntryFailure {
                entry_low: self.entry_bar.low,
                threshold,
                close: bar.close,
                confirmed_at_ns: bar.end_ns,
                elapsed_ns: elapsed,
                reclaim_threshold: self.entry_bar.open.max(self.entry_bar.close) + buffer,
            }));
        }
        Ok(None)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Held {
    pub setup: PositionSetup,
    pub stop: f64,
    pub body_high: f64,
    pub stop_above_initial_fill: Option<bool>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Exit {
    pub at_ns: u64,
    pub held: Held,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct RecoveryState {
    last_observed_at_ns: u64,
    pub held: Option<Held>,
    pub last_exit: Option<Exit>,
    pub retired_swings: BTreeMap<String, u64>,
}
#[derive(Debug, Clone, Default)]
pub struct RecoveryPolicy {
    pub stop_gain_guard: bool,
    pub tight_base: bool,
    pub unprotected_reentry: bool,
    pub entry_reclaim: bool,
    pub regular_session_start_ns: u64,
    pub regular_full_range: bool,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RecoveryDecision {
    Building,
    BreachedSetupSwing,
    WaitingForNewSupportAfterExit,
    WaitingForFailedSetupReclaim,
    WaitingForPostMoveRecoveryOrHigherBase,
}
impl RecoveryState {
    /// Call after authoritative fill/position reconciliation, never on an intent.
    pub fn observe_position(
        &mut self,
        now_ns: u64,
        position: Option<(&PositionSetup, f64, f64)>,
        preserve_peak: bool,
        stop_gain_guard: bool,
    ) -> Result<()> {
        if now_ns < self.last_observed_at_ns {
            return Err(Error::Invalid(
                "position recovery observations cannot rewind".into(),
            ));
        }
        if let Some((setup, stop, body_high)) = position {
            if !stop.is_finite()
                || stop <= 0.
                || !body_high.is_finite()
                || body_high < 0.
                || now_ns < setup.confirmed_at_ns
            {
                return Err(Error::Invalid(
                    "invalid filled lifecycle observation".into(),
                ));
            }
            let old = self
                .held
                .as_ref()
                .filter(|h| h.setup.confirmed_at_ns == setup.confirmed_at_ns);
            let peak = if preserve_peak {
                old.map_or(body_high, |h| h.body_high.max(body_high))
            } else {
                body_high
            };
            let protected = if stop_gain_guard {
                Some(
                    old.is_some_and(|h| h.stop_above_initial_fill == Some(true))
                        || setup
                            .initial_fill_price
                            .is_some_and(|fill| fill > 0. && stop > fill + 1e-9),
                )
            } else {
                None
            };
            self.held = Some(Held {
                setup: setup.clone(),
                stop,
                body_high: peak,
                stop_above_initial_fill: protected,
            });
        } else if let Some(held) = self.held.take() {
            self.last_exit = Some(Exit {
                at_ns: now_ns,
                held,
            });
        }
        self.last_observed_at_ns = now_ns;
        Ok(())
    }
    pub fn retire_breached(&mut self, swings: &[Swing], bar: &Bar, fresh: bool) -> Result<()> {
        valid_bar(bar)?;
        if !fresh {
            return Ok(());
        }
        for swing in swings {
            if swing.support && swing.confirmed_at_ns <= bar.end_ns && bar.low < swing.lower - 1e-9
            {
                self.retired_swings.insert(swing.id.clone(), bar.end_ns);
            }
        }
        Ok(())
    }
    pub fn permission(
        &self,
        swing: &Swing,
        bar: &Bar,
        range: Option<&Range>,
        policy: &RecoveryPolicy,
    ) -> Result<RecoveryDecision> {
        valid_bar(bar)?;
        if swing.pivot_at_ns > swing.confirmed_at_ns
            || swing.confirmed_at_ns > bar.end_ns
            || [swing.lower, swing.price, swing.upper]
                .iter()
                .any(|v| !v.is_finite())
            || swing.lower <= 0.
            || swing.lower > swing.price
            || swing.price > swing.upper
        {
            return Err(Error::Invalid(
                "recovery support is invalid or future".into(),
            ));
        }
        let Some(previous) = &self.last_exit else {
            return Ok(RecoveryDecision::Building);
        };
        let held = &previous.held;
        if self.retired_swings.contains_key(&swing.id) {
            return Ok(RecoveryDecision::BreachedSetupSwing);
        }
        if swing.pivot_at_ns <= previous.at_ns || swing.confirmed_at_ns <= previous.at_ns {
            return Ok(RecoveryDecision::WaitingForNewSupportAfterExit);
        }
        if held
            .setup
            .entry_failure_recovery
            .is_some_and(|price| bar.close <= price)
        {
            return Ok(RecoveryDecision::WaitingForFailedSetupReclaim);
        }
        let full = range.is_some_and(|r| {
            r.start_ns >= policy.regular_session_start_ns
                && r.start_ns < r.end_ns
                && r.end_ns <= bar.end_ns
        });
        if (!policy.regular_full_range || full)
            && policy.regular_session_start_ns > 0
            && previous.at_ns < policy.regular_session_start_ns
            && policy.regular_session_start_ns <= swing.pivot_at_ns
        {
            return Ok(RecoveryDecision::Building);
        }
        if policy.unprotected_reentry
            && policy.stop_gain_guard
            && held
                .setup
                .initial_fill_price
                .is_some_and(|p| p > 0. && (!policy.entry_reclaim || bar.close > p))
            && held.stop_above_initial_fill == Some(false)
        {
            return Ok(RecoveryDecision::Building);
        }
        if held.setup.phase != Phase::PostBreakout
            && !(policy.stop_gain_guard && held.stop_above_initial_fill == Some(true))
        {
            return Ok(RecoveryDecision::Building);
        }
        if swing.lower > held.stop || (policy.stop_gain_guard && policy.tight_base) {
            return Ok(RecoveryDecision::Building);
        }
        if bar.close > held.setup.breakout_threshold.max(held.body_high) {
            return Ok(RecoveryDecision::Building);
        }
        Ok(RecoveryDecision::WaitingForPostMoveRecoveryOrHigherBase)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn bar(start: u64, open: f64, close: f64) -> Bar {
        Bar {
            start_ns: start,
            end_ns: start + 1,
            open,
            close,
            high: open.max(close) + 0.01,
            low: open.min(close) - 0.01,
            volume: 1.,
            notional: close,
            trades: 1,
        }
    }
    fn setup() -> PositionSetup {
        PositionSetup {
            confirmed_at_ns: 2,
            phase: Phase::Building,
            breakout_threshold: 10.5,
            breakout_at_ns: None,
            entry_bar: bar(1, 10., 10.1),
            initial_fill_price: Some(10.1),
            initial_risk: Some(0.1),
            best_close: 10.6,
            entry_failure_recovery: None,
        }
    }
    #[test]
    fn phase_requires_own_later_green_bar_and_fill_progress() {
        let mut position = setup();
        assert!(!position.advance_phase(&bar(1, 10., 11.), true, 1.).unwrap());
        assert!(!position
            .advance_phase(&bar(2, 11., 10.8), true, 1.)
            .unwrap());
        assert!(position
            .advance_phase(&bar(3, 10.6, 10.8), true, 1.)
            .unwrap());
        assert_eq!(position.phase, Phase::PostBreakout);
    }
    #[test]
    fn early_failure_requires_contiguous_fresh_completed_evidence() {
        let position = setup();
        let failure = bar(2, 10., 9.9);
        assert!(position
            .entry_failure(&failure, true, false, 10, 1., 0.01)
            .unwrap()
            .is_none());
        assert!(position
            .entry_failure(&failure, false, true, 10, 1., 0.01)
            .unwrap()
            .is_none());
        assert!(position
            .entry_failure(&failure, true, true, 10, 1., 0.01)
            .unwrap()
            .is_some());
    }
    #[test]
    fn recovery_keeps_mature_move_strict_until_new_base_or_reclaim() {
        let mut position = setup();
        position.phase = Phase::PostBreakout;
        let mut state = RecoveryState::default();
        state
            .observe_position(3, Some((&position, 10.2, 11.)), true, true)
            .unwrap();
        state.observe_position(4, None, true, true).unwrap();
        let mut swing = Swing {
            id: "s".into(),
            lower: 9.8,
            price: 9.9,
            upper: 10.,
            pivot_at_ns: 5,
            confirmed_at_ns: 6,
            support: true,
            active: true,
        };
        assert_eq!(
            state
                .permission(&swing, &bar(6, 10., 10.1), None, &RecoveryPolicy::default())
                .unwrap(),
            RecoveryDecision::WaitingForPostMoveRecoveryOrHigherBase
        );
        swing.lower = 10.3;
        swing.price = 10.4;
        swing.upper = 10.5;
        assert_eq!(
            state
                .permission(
                    &swing,
                    &bar(6, 10.4, 10.5),
                    None,
                    &RecoveryPolicy::default()
                )
                .unwrap(),
            RecoveryDecision::Building
        );
        assert_eq!(
            state.last_exit.as_ref().unwrap().held.setup.phase,
            Phase::PostBreakout
        );
    }
    #[test]
    fn reconciled_position_history_cannot_rewind() {
        let mut state = RecoveryState::default();
        let position = setup();
        state
            .observe_position(4, Some((&position, 10., 10.5)), true, true)
            .unwrap();
        assert!(state.observe_position(3, None, true, true).is_err());
        assert!(state.held.is_some());
    }
}
