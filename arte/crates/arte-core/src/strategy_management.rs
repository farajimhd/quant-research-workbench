//! Position-owned exit management. Broker protective stops remain independent.
use crate::market::Bar;
use crate::strategy_targets::{
    eligible, resistance, valid_level, Policy as LevelPolicy, TargetLevel,
};
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventKind {
    HigherLowConfirmed,
    LowerHighConfirmed,
    Rejection,
    FailedBreakout,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub kind: EventKind,
    pub level: TargetLevel,
    pub pivot_at_ns: u64,
    pub local: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Attempt {
    pub level: TargetLevel,
    pub broken: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Rejection {
    pub level: TargetLevel,
    pub at_ns: u64,
    pub upper: f64,
    pub reaction_low: f64,
    pub tolerance: f64,
    pub failed_high: Option<f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FailureEvidence {
    pub level: TargetLevel,
    pub previous_bar: Bar,
    pub exit_bar: Bar,
    pub threshold: f64,
    pub broken: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PendingFailure {
    pub evidence: FailureEvidence,
    pub trigger_at_ns: u64,
    pub trigger_close: f64,
    pub red_closes: usize,
    last_bar_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CandleConfirmation {
    pub trigger_at_ns: u64,
    pub trigger_close: f64,
    pub confirmed_at_ns: u64,
    pub consecutive_red_closes: usize,
    pub bar: Bar,
}
#[derive(Debug, Clone)]
pub enum FailureConfirmation {
    Pending,
    Cancelled,
    Confirmed(CandleConfirmation),
}
impl PendingFailure {
    pub fn new(evidence: FailureEvidence, red_closes: usize) -> Result<Self> {
        valid_level(&evidence.level)?;
        if !valid_bar(&evidence.exit_bar)
            || !evidence.threshold.is_finite()
            || evidence.threshold <= 0.
        {
            return Err(Error::Invalid("invalid pending resistance failure".into()));
        }
        Ok(Self {
            trigger_at_ns: evidence.exit_bar.end_ns,
            trigger_close: evidence.exit_bar.close,
            last_bar_ns: evidence.exit_bar.end_ns,
            red_closes,
            evidence,
        })
    }
    /// Caller removes this object on Cancelled or Confirmed. No broker action is issued here.
    pub fn observe(
        &mut self,
        bar: &Bar,
        previous: Option<&Bar>,
        policy: &LevelPolicy,
    ) -> Result<FailureConfirmation> {
        if !valid_bar(bar)
            || bar.end_ns.checked_sub(bar.start_ns) != Some(1_000_000_000)
            || previous.is_some_and(|p| !valid_bar(p) || p.end_ns > bar.start_ns)
            || bar.end_ns < self.last_bar_ns
        {
            return Err(Error::Invalid("invalid pending failure observation".into()));
        }
        if !eligible(&self.evidence.level, policy) {
            return Ok(FailureConfirmation::Cancelled);
        }
        if bar.end_ns == self.last_bar_ns {
            return Ok(FailureConfirmation::Pending);
        }
        if bar.close >= self.evidence.level.geometry.lower {
            return Ok(FailureConfirmation::Cancelled);
        }
        let count = if bar.start_ns == self.last_bar_ns {
            self.red_closes
        } else {
            0
        };
        self.red_closes = if bar.close < bar.open {
            count.saturating_add(1)
        } else {
            0
        };
        self.last_bar_ns = bar.end_ns;
        if self.red_closes < 2
            || bar.close >= self.evidence.threshold
            || !previous
                .is_some_and(|p| p.end_ns == bar.start_ns && bar.low < p.low && bar.close < p.open)
        {
            return Ok(FailureConfirmation::Pending);
        }
        Ok(FailureConfirmation::Confirmed(CandleConfirmation {
            trigger_at_ns: self.trigger_at_ns,
            trigger_close: self.trigger_close,
            confirmed_at_ns: bar.end_ns,
            consecutive_red_closes: 2,
            bar: bar.clone(),
        }))
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExitReason {
    RedCloseBelowAttemptOpen,
    ProtectiveSwingFailed,
    ConfirmedStructuralReversal,
    ResistanceRejectionFailedRecovery,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    pub confirmed_at_ns: u64,
    pub base_lower: f64,
    pub base_tolerance: f64,
    pub failure_closes: usize,
    pub attempts: Vec<Attempt>,
    pub attempts_at_ns: u64,
    pub rejection: Option<Rejection>,
    pub failed_resistance: Option<FailureEvidence>,
    last_evaluated_ns: u64,
}
#[derive(Debug, Clone)]
pub struct Policy {
    pub tick: f64,
    pub tolerance_atr: f64,
    pub failure_closes: usize,
    pub rejection_offset_bps: f64,
    pub maximum_attempts: usize,
}
pub struct Frame<'a> {
    pub bar: &'a Bar,
    pub previous: Option<&'a Bar>,
    pub levels: &'a [TargetLevel],
    pub events: &'a [Event],
    pub atr: Option<f64>,
    pub bearish_reversal_at_ns: Option<u64>,
    pub level_policy: &'a LevelPolicy,
}
fn valid_bar(b: &Bar) -> bool {
    b.start_ns < b.end_ns
        && [b.open, b.high, b.low, b.close]
            .iter()
            .all(|p| p.is_finite() && *p > 0.)
        && b.high >= b.open.max(b.close)
        && b.low <= b.open.min(b.close)
}
fn touch(bar: &Bar, level: &TargetLevel) -> bool {
    bar.high >= level.geometry.lower && bar.low <= level.geometry.upper
}
impl State {
    pub fn new(confirmed_at_ns: u64, base_lower: f64, base_tolerance: f64) -> Result<Self> {
        if !base_lower.is_finite()
            || base_lower <= 0.
            || !base_tolerance.is_finite()
            || base_tolerance < 0.
        {
            return Err(Error::Invalid("invalid management base".into()));
        }
        Ok(Self {
            confirmed_at_ns,
            base_lower,
            base_tolerance,
            failure_closes: 0,
            attempts: vec![],
            attempts_at_ns: 0,
            rejection: None,
            failed_resistance: None,
            last_evaluated_ns: 0,
        })
    }
    pub fn evaluate(&mut self, frame: &Frame<'_>, policy: &Policy) -> Result<Option<ExitReason>> {
        let bar = frame.bar;
        if !valid_bar(bar)
            || bar.end_ns < self.confirmed_at_ns
            || frame
                .previous
                .is_some_and(|b| !valid_bar(b) || b.end_ns > bar.start_ns)
            || [
                policy.tick,
                policy.tolerance_atr,
                policy.rejection_offset_bps,
            ]
            .iter()
            .any(|v| !v.is_finite() || *v < 0.)
            || policy.tick == 0.
            || policy.failure_closes == 0
            || policy.maximum_attempts == 0
            || policy.rejection_offset_bps >= 10000.
            || frame.atr.is_some_and(|v| !v.is_finite() || v < 0.)
            || frame
                .bearish_reversal_at_ns
                .is_some_and(|at| at > bar.end_ns)
        {
            return Err(Error::Invalid("invalid management frame or policy".into()));
        }
        for level in frame.levels {
            valid_level(level)?;
        }
        for event in frame.events {
            valid_level(&event.level)?;
            if event.level.geometry.confirmed_at_ns > bar.end_ns
                || event.pivot_at_ns > event.level.geometry.confirmed_at_ns
            {
                return Err(Error::Unready("future management event".into()));
            }
        }
        if bar.end_ns < self.last_evaluated_ns {
            return Err(Error::Invalid("management clock cannot rewind".into()));
        }
        if bar.end_ns == self.last_evaluated_ns {
            return Ok(None);
        }
        let mut next = self.clone();
        let reason = next.advance(frame, policy)?;
        next.last_evaluated_ns = bar.end_ns;
        *self = next;
        Ok(reason)
    }
    fn advance(&mut self, frame: &Frame<'_>, policy: &Policy) -> Result<Option<ExitReason>> {
        let bar = frame.bar;
        let contiguous = frame.previous.is_some_and(|p| p.end_ns == bar.start_ns);
        let levels: Vec<_> = frame
            .levels
            .iter()
            .filter(|l| eligible(l, frame.level_policy) && resistance(l, frame.level_policy))
            .collect();
        let mut attempts = if contiguous && self.attempts_at_ns == frame.previous.unwrap().end_ns {
            self.attempts.clone()
        } else {
            vec![]
        };
        if contiguous {
            let previous = frame.previous.unwrap();
            for &level in &levels {
                if level.geometry.confirmed_at_ns <= previous.end_ns
                    && touch(previous, level)
                    && !attempts
                        .iter()
                        .any(|a| a.level.geometry.id == level.geometry.id)
                {
                    attempts.push(Attempt {
                        level: level.clone(),
                        broken: false,
                    });
                }
            }
            for attempt in &mut attempts {
                attempt.broken |= previous.close > attempt.level.geometry.upper;
            }
        }
        let mut ongoing: Vec<_> = attempts
            .iter()
            .filter(|a| touch(bar, &a.level))
            .cloned()
            .collect();
        for attempt in &mut ongoing {
            attempt.broken |= bar.close > attempt.level.geometry.upper;
        }
        for &level in &levels {
            if level.geometry.confirmed_at_ns <= bar.end_ns
                && touch(bar, level)
                && !ongoing
                    .iter()
                    .any(|a| a.level.geometry.id == level.geometry.id)
            {
                ongoing.push(Attempt {
                    level: level.clone(),
                    broken: bar.close > level.geometry.upper,
                });
            }
        }
        if attempts.len() > policy.maximum_attempts || ongoing.len() > policy.maximum_attempts {
            return Err(Error::Capacity(
                "resistance attempt limit exceeded; no truncation".into(),
            ));
        }
        self.attempts = ongoing;
        self.attempts_at_ns = bar.end_ns;
        if let Some(previous) = frame.previous.filter(|_| contiguous) {
            if bar.close < bar.open && bar.close < previous.open && bar.low < previous.low {
                for attempt in attempts {
                    let threshold =
                        attempt.level.geometry.lower * (1. - policy.rejection_offset_bps / 10000.);
                    if eligible(&attempt.level, frame.level_policy) && bar.close < threshold {
                        self.failed_resistance = Some(FailureEvidence {
                            level: attempt.level,
                            previous_bar: previous.clone(),
                            exit_bar: bar.clone(),
                            threshold,
                            broken: attempt.broken,
                        });
                        return Ok(Some(ExitReason::RedCloseBelowAttemptOpen));
                    }
                }
            }
        }
        let tolerance = policy
            .tick
            .max(policy.tolerance_atr * frame.atr.unwrap_or(0.));
        if !tolerance.is_finite() {
            return Err(Error::Invalid("management tolerance overflow".into()));
        }
        for event in frame.events.iter().filter(|e| e.local) {
            if event.kind == EventKind::HigherLowConfirmed
                && event.level.geometry.lower > self.base_lower
                && event.level.geometry.confirmed_at_ns > self.confirmed_at_ns
            {
                self.base_lower = event.level.geometry.lower;
                self.base_tolerance = tolerance;
                self.failure_closes = 0;
            }
        }
        let below = bar.close < self.base_lower - self.base_tolerance;
        self.failure_closes = if below {
            self.failure_closes.saturating_add(1)
        } else {
            0
        };
        if self.failure_closes >= policy.failure_closes {
            return Ok(Some(ExitReason::ProtectiveSwingFailed));
        }
        if below && frame.bearish_reversal_at_ns.is_some() {
            return Ok(Some(ExitReason::ConfirmedStructuralReversal));
        }
        if self
            .rejection
            .as_ref()
            .is_some_and(|r| !eligible(&r.level, frame.level_policy) || bar.close > r.upper)
        {
            self.rejection = None;
        }
        if let Some(rejection) = &mut self.rejection {
            for event in frame.events.iter().filter(|e| e.local) {
                if event.kind == EventKind::LowerHighConfirmed
                    && event.level.geometry.confirmed_at_ns > rejection.at_ns
                    && event.pivot_at_ns > rejection.at_ns
                    && event.level.geometry.price < rejection.upper
                {
                    rejection.failed_high = Some(event.level.geometry.price);
                }
            }
            if rejection.failed_high.is_some()
                && bar.close < rejection.reaction_low - rejection.tolerance
            {
                return Ok(Some(ExitReason::ResistanceRejectionFailedRecovery));
            }
            if rejection.failed_high.is_none() {
                rejection.reaction_low = rejection.reaction_low.min(bar.low);
            }
        } else {
            for event in frame.events {
                let level = &event.level;
                if matches!(event.kind, EventKind::Rejection | EventKind::FailedBreakout)
                    && resistance(level, frame.level_policy)
                    && eligible(level, frame.level_policy)
                    && level.geometry.upper >= bar.close
                    && bar.high >= level.geometry.lower
                {
                    self.rejection = Some(Rejection {
                        level: level.clone(),
                        at_ns: bar.end_ns,
                        upper: level.geometry.upper,
                        reaction_low: bar.low,
                        tolerance,
                        failed_high: None,
                    });
                    break;
                }
            }
        }
        Ok(None)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_encounters::Level;
    use crate::v7_encounters::ActiveRole;
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
    fn policy() -> Policy {
        Policy {
            tick: 0.01,
            tolerance_atr: 1.,
            failure_closes: 2,
            rejection_offset_bps: 0.,
            maximum_attempts: 32,
        }
    }
    fn levels_policy() -> LevelPolicy {
        LevelPolicy {
            distance_fraction: 0.05,
            offset_ticks: 1.,
            stop_buffer_bps: 10.,
            all_origins: true,
            encounter_transitions: true,
        }
    }
    fn level() -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: "r".into(),
                price: 10.,
                lower: 9.9,
                upper: 10.1,
                role: ActiveRole::Resistance,
                confirmed_at_ns: 1,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        }
    }
    #[test]
    fn resistance_failure_precedes_base_failure_and_keeps_evidence() {
        let mut state = State::new(1, 10., 0.01).unwrap();
        let previous = bar(2, 10., 10.05);
        let current = bar(3, 10., 9.7);
        let lp = levels_policy();
        let levels = [level()];
        let mut p = policy();
        p.failure_closes = 1;
        let frame = Frame {
            bar: &current,
            previous: Some(&previous),
            levels: &levels,
            events: &[],
            atr: None,
            bearish_reversal_at_ns: None,
            level_policy: &lp,
        };
        assert_eq!(
            state.evaluate(&frame, &p).unwrap(),
            Some(ExitReason::RedCloseBelowAttemptOpen)
        );
        assert!(state.failed_resistance.is_some());
    }
    #[test]
    fn repeated_evaluation_does_not_double_count_failed_closes() {
        let mut state = State::new(1, 10., 0.01).unwrap();
        let current = bar(2, 9.8, 9.7);
        let lp = levels_policy();
        let frame = Frame {
            bar: &current,
            previous: None,
            levels: &[],
            events: &[],
            atr: None,
            bearish_reversal_at_ns: None,
            level_policy: &lp,
        };
        assert_eq!(state.evaluate(&frame, &policy()).unwrap(), None);
        assert_eq!(state.evaluate(&frame, &policy()).unwrap(), None);
        assert_eq!(state.failure_closes, 1);
        let earlier = bar(1, 9.8, 9.7);
        let earlier_frame = Frame {
            bar: &earlier,
            ..frame
        };
        let before = crate::content_hash(&state).unwrap();
        assert!(state.evaluate(&earlier_frame, &policy()).is_err());
        assert_eq!(before, crate::content_hash(&state).unwrap());
    }
    #[test]
    fn future_local_event_does_not_mutate_state() {
        let mut state = State::new(1, 9., 0.01).unwrap();
        let before = crate::content_hash(&state).unwrap();
        let current = bar(2, 10., 10.1);
        let lp = levels_policy();
        let mut level = level();
        level.geometry.confirmed_at_ns = 10;
        let events = [Event {
            kind: EventKind::HigherLowConfirmed,
            level,
            pivot_at_ns: 2,
            local: true,
        }];
        let frame = Frame {
            bar: &current,
            previous: None,
            levels: &[],
            events: &events,
            atr: None,
            bearish_reversal_at_ns: None,
            level_policy: &lp,
        };
        assert!(state.evaluate(&frame, &policy()).is_err());
        assert_eq!(before, crate::content_hash(&state).unwrap());
    }
    #[test]
    fn departed_resistance_does_not_fail_unrelated_later_candle() {
        let mut state = State::new(1, 8., 0.01).unwrap();
        let lp = levels_policy();
        let levels = [level()];
        let touching = bar(2, 10., 10.05);
        let departed = bar(3, 10.3, 10.4);
        let later = bar(4, 10.3, 9.7);
        let first = Frame {
            bar: &departed,
            previous: Some(&touching),
            levels: &levels,
            events: &[],
            atr: None,
            bearish_reversal_at_ns: None,
            level_policy: &lp,
        };
        assert_eq!(state.evaluate(&first, &policy()).unwrap(), None);
        assert!(state.attempts.is_empty());
        let second = Frame {
            bar: &later,
            previous: Some(&departed),
            levels: &levels,
            events: &[],
            atr: None,
            bearish_reversal_at_ns: None,
            level_policy: &lp,
        };
        assert_eq!(state.evaluate(&second, &policy()).unwrap(), None);
        assert!(state.failed_resistance.is_none());
    }
    #[test]
    fn higher_low_updates_base_before_protective_failure() {
        let mut state = State::new(1, 8., 0.01).unwrap();
        let lp = levels_policy();
        let current = bar(3, 9.8, 9.7);
        let mut support = level();
        support.geometry.confirmed_at_ns = 3;
        let events = [Event {
            kind: EventKind::HigherLowConfirmed,
            level: support,
            pivot_at_ns: 2,
            local: true,
        }];
        let mut p = policy();
        p.failure_closes = 1;
        let frame = Frame {
            bar: &current,
            previous: None,
            levels: &[],
            events: &events,
            atr: Some(0.1),
            bearish_reversal_at_ns: Some(3),
            level_policy: &lp,
        };
        assert_eq!(
            state.evaluate(&frame, &p).unwrap(),
            Some(ExitReason::ProtectiveSwingFailed)
        );
        assert_eq!(state.base_lower, 9.9);
    }
    #[test]
    fn pending_failure_requires_adjacent_red_and_cancels_on_reclaim() {
        let scaled = |t, o, c| {
            let mut b = bar(t, o, c);
            b.start_ns *= 1_000_000_000;
            b.end_ns *= 1_000_000_000;
            b
        };
        let first = scaled(2, 10., 9.7);
        let evidence = FailureEvidence {
            level: level(),
            previous_bar: scaled(1, 10., 10.05),
            exit_bar: first.clone(),
            threshold: 9.9,
            broken: false,
        };
        let pending = PendingFailure::new(evidence, 1).unwrap();
        let mut adjacent = pending.clone();
        assert!(matches!(
            adjacent
                .observe(&scaled(3, 9.7, 9.6), Some(&first), &levels_policy())
                .unwrap(),
            FailureConfirmation::Confirmed(_)
        ));
        let mut gap = pending.clone();
        assert!(matches!(
            gap.observe(&scaled(4, 9.7, 9.6), Some(&first), &levels_policy())
                .unwrap(),
            FailureConfirmation::Pending
        ));
        assert_eq!(gap.red_closes, 1);
        let mut reclaim = pending;
        assert!(matches!(
            reclaim
                .observe(&scaled(3, 9.7, 9.9), Some(&first), &levels_policy())
                .unwrap(),
            FailureConfirmation::Cancelled
        ));
    }
}
