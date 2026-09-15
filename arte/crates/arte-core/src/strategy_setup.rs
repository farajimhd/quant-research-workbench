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
}
