//! Causal range and forming-bar checks from the pinned setup-recovery strategy.
//! Full strategy lifecycle and V7 decision parity are not yet implemented.
use crate::market::Bar;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::VecDeque;

pub const SOURCE_PROFILE: &str = "v7-setup-recovery-v9";
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetupSettings {
    pub range_ns: u64,
    pub minimum_bars: usize,
    pub maximum_gap_ns: u64,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Range {
    pub high: f64,
    pub low: f64,
    pub start_ns: u64,
    pub end_ns: u64,
    pub count: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SetupState {
    session: u32,
    bars: VecDeque<Bar>,
    pub prior_range: Option<Range>,
    last_end: u64,
}
impl SetupState {
    pub fn new() -> Self {
        Self {
            session: 0,
            bars: VecDeque::new(),
            prior_range: None,
            last_end: 0,
        }
    }
    pub fn observe(
        &mut self,
        session: u32,
        bar: &Bar,
        settings: &SetupSettings,
        fresh: bool,
    ) -> Result<bool> {
        if settings.range_ns == 0 || settings.minimum_bars == 0 {
            return Err(Error::Invalid("setup parameters missing".into()));
        }
        if bar.start_ns >= bar.end_ns
            || [bar.open, bar.high, bar.low, bar.close]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.)
            || bar.low > bar.open.min(bar.close)
            || bar.high < bar.open.max(bar.close)
        {
            return Err(Error::Invalid("invalid setup candle".into()));
        }
        if self.session != session {
            *self = Self::new();
            self.session = session;
        }
        if !fresh || bar.end_ns <= self.last_end {
            return Ok(false);
        }
        if self.bars.back().is_some_and(|last| {
            bar.start_ns < last.end_ns || bar.start_ns - last.end_ns > settings.maximum_gap_ns
        }) {
            self.bars.clear();
        }
        let cutoff = bar.start_ns.saturating_sub(settings.range_ns);
        while self.bars.front().is_some_and(|b| b.end_ns <= cutoff) {
            self.bars.pop_front();
        }
        self.prior_range = if self.bars.len() >= settings.minimum_bars {
            Some(Range {
                high: self
                    .bars
                    .iter()
                    .map(|b| b.high)
                    .fold(f64::NEG_INFINITY, f64::max),
                low: self
                    .bars
                    .iter()
                    .map(|b| b.low)
                    .fold(f64::INFINITY, f64::min),
                start_ns: self.bars.front().unwrap().start_ns,
                end_ns: self.bars.back().unwrap().end_ns,
                count: self.bars.len(),
            })
        } else {
            None
        };
        self.bars.push_back(bar.clone());
        self.last_end = bar.end_ns;
        Ok(true)
    }
}
impl Default for SetupState {
    fn default() -> Self {
        Self::new()
    }
}
pub fn upper_wick_fraction(bar: &Bar) -> Result<f64> {
    let span = bar.high - bar.low;
    if !span.is_finite()
        || span < 0.
        || bar.high < bar.open.max(bar.close)
        || bar.low > bar.open.min(bar.close)
    {
        return Err(Error::Invalid("invalid candle geometry".into()));
    }
    Ok(if span > 0. {
        (bar.high - bar.open.max(bar.close)) / span
    } else {
        0.
    })
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct ActivityEvidence {
    pub observed_at_ns: u64,
    pub reference_at_ns: Option<u64>,
    pub value_pct: Option<f64>,
    pub minimum_pct: f64,
    pub passed: bool,
    pub reason: String,
}
/// Independent observed-bar history for the source's 300-second range and
/// 60-second progress gates. Short consolidation gaps do not clear this history.
#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct ActivityState {
    session: u32,
    bars: VecDeque<Bar>,
}
impl ActivityState {
    pub fn observe(&mut self, session: u32, bar: &Bar, fresh: bool) -> Result<bool> {
        if bar.start_ns >= bar.end_ns
            || [bar.open, bar.high, bar.low, bar.close]
                .iter()
                .any(|p| !p.is_finite() || *p <= 0.)
            || bar.high < bar.open.max(bar.close)
            || bar.low > bar.open.min(bar.close)
        {
            return Err(Error::Invalid("invalid activity candle".into()));
        }
        if session != self.session {
            self.bars.clear();
            self.session = session;
        }
        if !fresh || self.bars.back().is_some_and(|b| b.end_ns >= bar.end_ns) {
            return Ok(false);
        }
        let cutoff = bar.end_ns.saturating_sub(300_000_000_000);
        while self.bars.front().is_some_and(|b| b.end_ns <= cutoff) {
            self.bars.pop_front();
        }
        if self.bars.len() >= 4096 {
            return Err(Error::Capacity(
                "activity history capacity reached; no truncation".into(),
            ));
        }
        self.bars.push_back(bar.clone());
        Ok(true)
    }
    pub fn range(&self, at_ns: u64, minimum_pct: f64) -> Result<ActivityEvidence> {
        let mut result = self.evidence(at_ns, minimum_pct)?;
        if !result.reason.is_empty() {
            return Ok(result);
        }
        let low = self
            .bars
            .iter()
            .map(|b| b.low)
            .fold(f64::INFINITY, f64::min);
        let high = self
            .bars
            .iter()
            .map(|b| b.high)
            .fold(f64::NEG_INFINITY, f64::max);
        let value = (high / low - 1.) * 100.;
        if !value.is_finite() {
            return Err(Error::Invalid("activity range overflow".into()));
        }
        result.value_pct = Some(value);
        result.reference_at_ns = self.bars.front().map(|b| b.end_ns);
        result.passed = value + 1e-9 >= minimum_pct;
        if !result.passed {
            result.reason = "range_below_minimum".into();
        }
        Ok(result)
    }
    pub fn progress(&self, at_ns: u64, minimum_pct: f64) -> Result<ActivityEvidence> {
        let mut result = self.evidence(at_ns, minimum_pct)?;
        if !result.reason.is_empty() {
            return Ok(result);
        }
        let prior = self
            .bars
            .iter()
            .rev()
            .find(|b| b.end_ns <= at_ns.saturating_sub(60_000_000_000));
        let Some(prior) = prior.filter(|b| at_ns - b.end_ns <= 65_000_000_000) else {
            result.reason = "historical_reference_missing_or_stale".into();
            return Ok(result);
        };
        let value = (self.bars.back().unwrap().close / prior.close - 1.) * 100.;
        if !value.is_finite() {
            return Err(Error::Invalid("activity progress overflow".into()));
        }
        result.value_pct = Some(value);
        result.reference_at_ns = Some(prior.end_ns);
        result.passed = value + 1e-9 >= minimum_pct;
        if !result.passed {
            result.reason = "progress_below_minimum".into();
        }
        Ok(result)
    }
    fn evidence(&self, at_ns: u64, minimum_pct: f64) -> Result<ActivityEvidence> {
        if !minimum_pct.is_finite() || minimum_pct < 0. {
            return Err(Error::Invalid("invalid activity minimum".into()));
        }
        Ok(ActivityEvidence {
            observed_at_ns: at_ns,
            reference_at_ns: None,
            value_pct: None,
            minimum_pct,
            passed: false,
            reason: if self.bars.back().is_none_or(|b| b.end_ns != at_ns) {
                "current_completed_bar_missing".into()
            } else {
                String::new()
            },
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn bar(start: u64, high: f64) -> Bar {
        Bar {
            start_ns: start,
            end_ns: start + 1,
            open: 10.,
            high,
            low: 9.,
            close: 10.,
            volume: 1.,
            notional: 10.,
            trades: 1,
        }
    }
    #[test]
    fn range_excludes_current_candle() {
        let mut s = SetupState::new();
        let settings = SetupSettings {
            range_ns: 30,
            minimum_bars: 2,
            maximum_gap_ns: 0,
        };
        s.observe(1, &bar(1, 11.), &settings, true).unwrap();
        s.observe(1, &bar(2, 12.), &settings, true).unwrap();
        s.observe(1, &bar(3, 99.), &settings, true).unwrap();
        assert_eq!(s.prior_range.as_ref().unwrap().high, 12.);
    }
    #[test]
    fn stale_candle_does_not_advance() {
        let mut s = SetupState::new();
        let settings = SetupSettings {
            range_ns: 30,
            minimum_bars: 2,
            maximum_gap_ns: 0,
        };
        assert!(!s.observe(1, &bar(1, 11.), &settings, false).unwrap());
        assert!(s.bars.is_empty());
    }
    #[test]
    fn activity_history_is_sparse_and_progress_reference_is_bounded() {
        let mut state = ActivityState::default();
        let mut old = bar(0, 11.);
        old.end_ns = 1_000_000_000;
        let mut current = old.clone();
        current.start_ns = 60_000_000_000;
        current.end_ns = 61_000_000_000;
        current.close = 10.5;
        state.observe(1, &old, true).unwrap();
        state.observe(1, &current, true).unwrap();
        assert_eq!(state.bars.len(), 2);
        assert!(state.progress(current.end_ns, 4.).unwrap().passed);
        assert!(state.range(current.end_ns, 1.).unwrap().passed);
        let mut late = current.clone();
        late.start_ns = 67_000_000_000;
        late.end_ns = 68_000_000_000;
        state.observe(1, &late, true).unwrap();
        assert!(!state.progress(late.end_ns, 0.).unwrap().passed);
        assert_eq!(
            state.progress(late.end_ns, 0.).unwrap().reason,
            "historical_reference_missing_or_stale"
        );
    }
    #[test]
    fn activity_cutoff_is_half_open_and_requires_current_bar() {
        let mut state = ActivityState::default();
        let mut first = bar(0, 99.);
        first.end_ns = 1_000_000_000;
        state.observe(1, &first, true).unwrap();
        let mut next = bar(300_000_000_000, 11.);
        next.end_ns = 301_000_000_000;
        state.observe(1, &next, true).unwrap();
        assert_eq!(state.bars.len(), 1);
        assert!(!state.range(next.end_ns + 1, 0.).unwrap().passed);
    }
}
