//! Position-independent overhead-level encounter logic from frozen v7_encounters.
//! Nanosecond clocks are explicit; quote-only updates never consume opening evidence.
use crate::market::Bar;
use crate::strategy_setup::upper_wick_fraction;
use crate::v7_encounters::ActiveRole;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
const SECOND: u64 = 1_000_000_000;
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Settings {
    pub breakout_buffer_ticks: f64,
    pub breakout_buffer_bps: f64,
    pub rejection_break_offset_bps: f64,
    pub topping_tail_fraction: f64,
    pub maximum_encounters: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Level {
    pub id: String,
    pub price: f64,
    pub lower: f64,
    pub upper: f64,
    pub role: ActiveRole,
    pub confirmed_at_ns: u64,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Status {
    Approaching,
    Warning,
    Failed,
    Broken,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExitReason {
    ToppingRejectionNextOpen,
    BufferedResistanceFailure,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Warning {
    pub open: f64,
    pub end_ns: u64,
    pub fraction: f64,
    pub opening_seen: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Encounter {
    pub level: Level,
    pub threshold: f64,
    pub failure_threshold: f64,
    pub status: Status,
    pub red_closes: usize,
    pub warning: Option<Warning>,
    pub failed_at_ns: Option<u64>,
    pub recovered_at_ns: Option<u64>,
    pub touch_at_ns: Option<u64>,
}
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct State {
    session: u32,
    at_ns: u64,
    pub levels: BTreeMap<String, Encounter>,
    pub exit_reason: Option<ExitReason>,
}
fn validate_bar(bar: &Bar) -> Result<()> {
    if bar.start_ns >= bar.end_ns
        || [bar.open, bar.high, bar.low, bar.close]
            .iter()
            .any(|v| !v.is_finite() || *v <= 0.)
        || bar.low > bar.open.min(bar.close)
        || bar.high < bar.open.max(bar.close)
    {
        return Err(Error::Invalid("invalid completed encounter bar".into()));
    }
    Ok(())
}
fn validate(settings: &Settings, tick: f64) -> Result<()> {
    if !tick.is_finite()
        || tick <= 0.
        || settings.maximum_encounters == 0
        || [
            settings.breakout_buffer_ticks,
            settings.breakout_buffer_bps,
            settings.rejection_break_offset_bps,
            settings.topping_tail_fraction,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v < 0.)
        || settings.rejection_break_offset_bps >= 10_000.
        || settings.topping_tail_fraction > 1.
    {
        return Err(Error::Invalid("invalid encounter settings or tick".into()));
    }
    Ok(())
}
impl State {
    fn session(&mut self, session: u32) {
        if self.session != session {
            *self = Self {
                session,
                ..Self::default()
            };
        }
    }
    pub fn blocked(&self) -> bool {
        self.levels
            .values()
            .any(|e| matches!(e.status, Status::Warning | Status::Failed))
    }
    /// Call only for a non-completed-bar update. `trade_changed` must identify a
    /// last-trade change, not a quote or generic market-data notification.
    pub fn market_update(
        &mut self,
        session: u32,
        now_ns: u64,
        price: f64,
        trade_changed: bool,
    ) -> Result<Option<ExitReason>> {
        if !price.is_finite() || price <= 0. {
            return Err(Error::Invalid("invalid encounter last-trade price".into()));
        }
        self.session(session);
        let mut reason = None;
        if trade_changed {
            for encounter in self.levels.values_mut() {
                let Some(warning) = &mut encounter.warning else {
                    continue;
                };
                if warning.opening_seen
                    || now_ns < warning.end_ns
                    || now_ns - warning.end_ns >= SECOND
                {
                    continue;
                }
                warning.opening_seen = true;
                if price < warning.open && price < encounter.failure_threshold {
                    encounter.status = Status::Failed;
                    encounter.failed_at_ns = Some(now_ns);
                    reason = Some(ExitReason::ToppingRejectionNextOpen);
                }
            }
        }
        self.exit_reason = reason;
        Ok(reason)
    }
    pub fn completed_bar(
        &mut self,
        session: u32,
        bar: &Bar,
        previous: Option<&Bar>,
        prior_levels: &[Level],
        settings: &Settings,
        tick: f64,
    ) -> Result<Option<ExitReason>> {
        validate(settings, tick)?;
        validate_bar(bar)?;
        if let Some(previous) = previous {
            validate_bar(previous)?;
            if previous.end_ns > bar.start_ns {
                return Err(Error::Invalid(
                    "prior candle overlaps current candle".into(),
                ));
            }
        }
        for level in prior_levels {
            if level.id.is_empty()
                || [level.price, level.lower, level.upper]
                    .iter()
                    .any(|p| !p.is_finite() || *p <= 0.)
                || level.lower > level.price
                || level.price > level.upper
            {
                return Err(Error::Invalid("invalid causal level geometry".into()));
            }
        }
        self.session(session);
        if self.at_ns >= bar.end_ns {
            return Ok(None);
        }
        let contiguous = previous.is_some_and(|p| p.end_ns == bar.start_ns);
        // Preflight candidate additions before any persistent mutation.
        let mut additions = BTreeMap::new();
        for level in prior_levels {
            if !matches!(level.role, ActiveRole::Resistance | ActiveRole::Transition)
                || level.confirmed_at_ns > bar.start_ns
            {
                continue;
            }
            let boundary = level.price
                + (tick * settings.breakout_buffer_ticks)
                    .max(level.price * settings.breakout_buffer_bps / 10000.);
            let failure_threshold =
                level.lower * (1. - settings.rejection_break_offset_bps / 10000.);
            if !boundary.is_finite() || !failure_threshold.is_finite() {
                return Err(Error::Invalid("encounter threshold overflow".into()));
            }
            if !self.levels.contains_key(&level.id)
                && contiguous
                && previous.unwrap().close <= boundary
                && bar.high >= level.lower
                && (bar.low <= level.upper || bar.close > boundary)
            {
                additions.entry(level.id.clone()).or_insert(Encounter {
                    level: level.clone(),
                    threshold: boundary,
                    failure_threshold,
                    status: Status::Approaching,
                    red_closes: 0,
                    warning: None,
                    failed_at_ns: None,
                    recovered_at_ns: None,
                    touch_at_ns: None,
                });
            }
        }
        if self.levels.len() + additions.len() > settings.maximum_encounters {
            return Err(Error::Capacity(
                "strategy encounter capacity reached; no truncation".into(),
            ));
        }
        self.at_ns = bar.end_ns;
        let recovery = self
            .levels
            .values()
            .filter(|e| matches!(e.status, Status::Warning | Status::Failed))
            .map(|e| e.threshold)
            .fold(0., f64::max);
        if bar.close >= bar.open && bar.close > recovery {
            for encounter in self
                .levels
                .values_mut()
                .filter(|e| matches!(e.status, Status::Warning | Status::Failed))
            {
                encounter.status = Status::Broken;
                encounter.recovered_at_ns = Some(bar.end_ns);
                encounter.red_closes = 0;
                encounter.warning = None;
            }
        }
        self.levels.extend(additions);
        let fraction = upper_wick_fraction(bar)?;
        let topping = fraction > settings.topping_tail_fraction
            || (fraction - settings.topping_tail_fraction).abs()
                <= 1e-12_f64.max(1e-9 * fraction.abs().max(settings.topping_tail_fraction.abs()));
        let mut reason = None;
        for encounter in self.levels.values_mut() {
            let touching = bar.high >= encounter.level.lower && bar.low <= encounter.level.upper;
            if !matches!(encounter.status, Status::Warning | Status::Failed)
                && bar.close >= bar.open
                && bar.close > encounter.threshold
            {
                encounter.status = Status::Broken;
            }
            if touching && topping && bar.close <= encounter.threshold {
                if encounter.status != Status::Failed {
                    encounter.status = Status::Warning;
                }
                encounter.warning = Some(Warning {
                    open: bar.open,
                    end_ns: bar.end_ns,
                    fraction,
                    opening_seen: false,
                });
            }
            encounter.red_closes = if bar.close < bar.open {
                if contiguous {
                    encounter.red_closes.saturating_add(1)
                } else {
                    1
                }
            } else {
                0
            };
            let recent_touch = encounter
                .touch_at_ns
                .is_some_and(|at| at >= bar.start_ns.saturating_sub(SECOND));
            if contiguous
                && (touching
                    || recent_touch
                    || matches!(encounter.status, Status::Warning | Status::Failed))
                && encounter.red_closes >= 2
                && bar.low < previous.unwrap().low
                && bar.close < previous.unwrap().open
                && bar.close < encounter.failure_threshold
            {
                encounter.status = Status::Failed;
                encounter.failed_at_ns = Some(bar.end_ns);
                reason = Some(ExitReason::BufferedResistanceFailure);
            }
            if touching {
                encounter.touch_at_ns = Some(bar.end_ns);
            }
        }
        self.exit_reason = reason;
        Ok(reason)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn bar(start: u64, open: f64, high: f64, low: f64, close: f64) -> Bar {
        Bar {
            start_ns: start * SECOND,
            end_ns: (start + 1) * SECOND,
            open,
            high,
            low,
            close,
            volume: 1.,
            notional: close,
            trades: 1,
        }
    }
    fn settings() -> Settings {
        Settings {
            breakout_buffer_ticks: 1.,
            breakout_buffer_bps: 0.,
            rejection_break_offset_bps: 10.,
            topping_tail_fraction: 0.5,
            maximum_encounters: 10,
        }
    }
    fn level() -> Level {
        Level {
            id: "resistance".into(),
            price: 10.,
            lower: 9.9,
            upper: 10.1,
            role: ActiveRole::Resistance,
            confirmed_at_ns: SECOND,
        }
    }
    #[test]
    fn quotes_do_not_consume_next_open_failure() {
        let mut state = State::default();
        let previous = bar(1, 9.8, 9.9, 9.7, 9.8);
        let topping = bar(2, 10., 10.2, 9.8, 9.9);
        state
            .completed_bar(1, &topping, Some(&previous), &[level()], &settings(), 0.01)
            .unwrap();
        assert!(state.blocked());
        assert_eq!(
            state.market_update(1, 3 * SECOND, 9.7, false).unwrap(),
            None
        );
        assert_eq!(
            state.market_update(1, 3 * SECOND + 1, 9.7, true).unwrap(),
            Some(ExitReason::ToppingRejectionNextOpen)
        );
        assert_eq!(
            state.market_update(1, 3 * SECOND + 2, 9.6, true).unwrap(),
            None
        );
    }
    #[test]
    fn future_confirmed_levels_are_not_used() {
        let mut state = State::default();
        let mut future = level();
        future.confirmed_at_ns = 3 * SECOND;
        state
            .completed_bar(
                1,
                &bar(2, 10., 10.2, 9.8, 9.9),
                Some(&bar(1, 9.8, 9.9, 9.7, 9.8)),
                &[future],
                &settings(),
                0.01,
            )
            .unwrap();
        assert!(state.levels.is_empty());
    }
    #[test]
    fn warning_group_recovery_and_session_reset() {
        let mut state = State::default();
        let a = bar(1, 9.8, 9.9, 9.7, 9.8);
        let b = bar(2, 10., 10.2, 9.8, 9.9);
        state
            .completed_bar(1, &b, Some(&a), &[level()], &settings(), 0.01)
            .unwrap();
        state
            .completed_bar(
                1,
                &bar(3, 10., 10.4, 10., 10.4),
                Some(&b),
                &[],
                &settings(),
                0.01,
            )
            .unwrap();
        assert!(!state.blocked());
        state.market_update(2, 10 * SECOND, 10., false).unwrap();
        assert!(state.levels.is_empty());
    }
}
