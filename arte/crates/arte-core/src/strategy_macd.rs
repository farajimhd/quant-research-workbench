//! Forming 5s MACD and episode semantics from frozen historical_hod.py.
//! Completed 5s samples alone change the EMA base. This is not warmup certification.
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
const SECOND: u64 = 1_000_000_000;
const FIVE: u64 = 5 * SECOND;
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Kind {
    Completed,
    Forming,
    Unavailable,
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Reading {
    pub at_ns: u64,
    pub base_at_ns: Option<u64>,
    pub line: Option<f64>,
    pub signal: Option<f64>,
    pub kind: Kind,
    pub episode_at_ns: Option<u64>,
    pub episode_started: bool,
    /// Only a valid, non-bullish completed 5s sample sets this flag.
    pub completed_reversal: bool,
    pub prior_episode_high: Option<f64>,
    pub episode_high: Option<f64>,
}
impl Reading {
    pub fn positive(&self) -> bool {
        self.line
            .zip(self.signal)
            .is_some_and(|(line, signal)| line > signal)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Base {
    at_ns: u64,
    line: Option<f64>,
    signal: Option<f64>,
    slow: Option<f64>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    forming_enabled: bool,
    base: Option<Base>,
    latest: Option<Reading>,
    episode_at_ns: Option<u64>,
    episode_high: Option<f64>,
    last_at_ns: u64,
    last_one: Option<(u64, String)>,
    last_five: Option<(u64, String)>,
    configuration_hash: Option<String>,
}
impl State {
    pub fn new(forming_enabled: bool) -> Self {
        Self {
            forming_enabled,
            base: None,
            latest: None,
            episode_at_ns: None,
            episode_high: None,
            last_at_ns: 0,
            last_one: None,
            last_five: None,
            configuration_hash: None,
        }
    }
    pub fn reading(&self) -> Option<&Reading> {
        self.latest.as_ref()
    }
    fn clock(&self, at_ns: u64, five: bool, hash: &str) -> Result<bool> {
        let previous = if five {
            &self.last_five
        } else {
            &self.last_one
        };
        if let Some((at, previous_hash)) = previous {
            if *at == at_ns {
                return if previous_hash == hash {
                    Ok(false)
                } else {
                    Err(Error::Conflict("MACD boundary changed".into()))
                };
            }
        }
        if at_ns == 0
            || !at_ns.is_multiple_of(if five { FIVE } else { SECOND })
            || at_ns < self.last_at_ns
            || (five && self.last_one.as_ref().is_some_and(|(at, _)| *at == at_ns))
        {
            return Err(Error::Invalid("MACD boundary order or alignment".into()));
        }
        Ok(true)
    }
    fn publish(
        &mut self,
        mut reading: Reading,
        completed: bool,
        body_high: Option<f64>,
    ) -> Reading {
        if reading.positive() && self.episode_at_ns.is_none() {
            self.episode_at_ns = Some(reading.at_ns);
            self.episode_high = None;
            reading.episode_started = true;
        } else if completed
            && reading.line.is_some()
            && reading.signal.is_some()
            && !reading.positive()
        {
            self.episode_at_ns = None;
        }
        reading.prior_episode_high = self.episode_high;
        if self.episode_at_ns.is_some() {
            if let Some(high) = body_high {
                self.episode_high = Some(self.episode_high.map_or(high, |old| old.max(high)));
            }
        }
        reading.episode_at_ns = self.episode_at_ns;
        reading.episode_high = self.episode_high;
        self.latest = Some(reading.clone());
        reading
    }
    pub fn completed_five(
        &mut self,
        at_ns: u64,
        close: f64,
        line: Option<f64>,
        signal: Option<f64>,
    ) -> Result<Option<Reading>> {
        if !close.is_finite()
            || close <= 0.
            || line.is_some_and(|v| !v.is_finite())
            || signal.is_some_and(|v| !v.is_finite())
        {
            return Err(Error::Invalid("invalid completed MACD sample".into()));
        }
        let hash = content_hash(&(at_ns, close, line, signal))?;
        if !self.clock(at_ns, true, &hash)? {
            return Ok(None);
        }
        let slow = self
            .base
            .as_ref()
            .filter(|base| at_ns.checked_sub(base.at_ns) == Some(FIVE))
            .and_then(|base| base.line.zip(line).zip(signal))
            .map(|((prior_line, line), _)| {
                let fast_alpha = 2. / 13.;
                let slow_alpha = 2. / 27.;
                let previous_slow =
                    close - (line - (1. - fast_alpha) * prior_line) / (fast_alpha - slow_alpha);
                slow_alpha * close + (1. - slow_alpha) * previous_slow
            });
        if slow.is_some_and(|v| !v.is_finite()) {
            return Err(Error::Invalid("inferred MACD base overflow".into()));
        }
        self.base = Some(Base {
            at_ns,
            line,
            signal,
            slow,
        });
        self.last_five = Some((at_ns, hash));
        self.last_at_ns = at_ns;
        let reading = Reading {
            at_ns,
            base_at_ns: Some(at_ns),
            line,
            signal,
            kind: Kind::Completed,
            completed_reversal: line
                .zip(signal)
                .is_some_and(|(line, signal)| line <= signal),
            episode_at_ns: None,
            episode_started: false,
            prior_episode_high: None,
            episode_high: None,
        };
        Ok(Some(self.publish(reading, true, None)))
    }
    pub fn completed_one(&mut self, at_ns: u64, open: f64, close: f64) -> Result<Option<Reading>> {
        if [open, close].iter().any(|v| !v.is_finite() || *v <= 0.) {
            return Err(Error::Invalid("invalid MACD preview candle".into()));
        }
        let hash = content_hash(&(at_ns, open, close))?;
        if !self.clock(at_ns, false, &hash)? {
            return Ok(None);
        }
        let base_at_ns = self.base.as_ref().map(|base| base.at_ns);
        let (line, signal, kind, reading_at_ns) = if !self.forming_enabled {
            self.base
                .as_ref()
                .map_or((None, None, Kind::Unavailable, at_ns), |base| {
                    (base.line, base.signal, Kind::Completed, base.at_ns)
                })
        } else if let Some(base) = &self.base {
            let age = at_ns
                .checked_sub(base.at_ns)
                .ok_or_else(|| Error::Conflict("MACD base lies after preview".into()))?;
            if age == 0 {
                (base.line, base.signal, Kind::Completed, at_ns)
            } else if age <= FIVE {
                match base.slow.zip(base.line).zip(base.signal) {
                    Some(((slow, line), signal)) => {
                        let fast = 2. / 13. * close + 11. / 13. * (slow + line);
                        let slow = 2. / 27. * close + 25. / 27. * slow;
                        let line = fast - slow;
                        let signal = 0.2 * line + 0.8 * signal;
                        (Some(line), Some(signal), Kind::Forming, at_ns)
                    }
                    None => (None, None, Kind::Unavailable, at_ns),
                }
            } else {
                (None, None, Kind::Unavailable, at_ns)
            }
        } else {
            (None, None, Kind::Unavailable, at_ns)
        };
        if line.is_some_and(|v| !v.is_finite()) || signal.is_some_and(|v| !v.is_finite()) {
            return Err(Error::Invalid("forming MACD overflow".into()));
        }
        self.last_one = Some((at_ns, hash));
        self.last_at_ns = at_ns;
        let reading = Reading {
            at_ns: reading_at_ns,
            base_at_ns,
            line,
            signal,
            kind,
            completed_reversal: false,
            episode_at_ns: None,
            episode_started: false,
            prior_episode_high: None,
            episode_high: None,
        };
        Ok(Some(self.publish(reading, false, Some(open.max(close)))))
    }
    /// Bind the shared scheduler to the exact 12/26/9 five-second source. All
    /// completed 5s boundaries must be consumed before same-time 1s boundaries.
    pub fn observe_boundary(
        &mut self,
        boundary: &crate::market_structure::scheduler::Boundary<'_>,
        market: &crate::market_structure::Runtime,
    ) -> Result<Option<Reading>> {
        use crate::market_structure::scheduler::Kind as BoundaryKind;
        let BoundaryKind::Completed {
            interval_ns,
            bar,
            available_at_ns,
        } = &boundary.kind
        else {
            return Ok(None);
        };
        if ![SECOND, FIVE].contains(interval_ns) {
            return Ok(None);
        }
        let five = market.timeframe(FIVE)?;
        if !five.macd_periods_match(12, 26, 9)
            || self
                .configuration_hash
                .as_ref()
                .is_some_and(|hash| hash != market.configuration_hash())
            || market.timeframe(*interval_ns)?.completed().last() != Some(*bar)
            || bar.bar.end_ns > *available_at_ns
            || *available_at_ns > boundary.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "MACD scheduler configuration or source boundary".into(),
            ));
        }
        if *interval_ns == SECOND
            && five.completed().last().map(|bar| bar.bar.end_ns)
                != self.base.as_ref().map(|base| base.at_ns)
        {
            return Err(Error::Unready(
                "completed five-second MACD boundary was not consumed".into(),
            ));
        }
        let reading = if *interval_ns == FIVE {
            self.completed_five(
                bar.bar.end_ns,
                bar.bar.close,
                Some(bar.macd.0),
                Some(bar.macd.1),
            )?
        } else {
            self.completed_one(bar.bar.end_ns, bar.bar.open, bar.bar.close)?
        };
        self.configuration_hash = Some(market.configuration_hash().into());
        Ok(reading)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn previews_match_one_ema_step_and_never_compound_the_completed_base() {
        let mut macd = crate::market::Macd::new(12, 26, 9).unwrap();
        let mut state = State::new(true);
        let first = macd.update(10.).unwrap();
        state
            .completed_five(FIVE, 10., Some(first.0), Some(first.1))
            .unwrap();
        assert_eq!(
            state
                .completed_one(6 * SECOND, 10., 11.)
                .unwrap()
                .unwrap()
                .kind,
            Kind::Unavailable
        );
        let second = macd.update(11.).unwrap();
        state
            .completed_five(2 * FIVE, 11., Some(second.0), Some(second.1))
            .unwrap();
        let base_hash = content_hash(&state.base).unwrap();
        for (at, close) in [(11, 12.), (12, 13.), (13, 9.)] {
            let actual = state
                .completed_one(at * SECOND, close, close)
                .unwrap()
                .unwrap();
            let expected = macd.preview(close).unwrap();
            assert_eq!(actual.kind, Kind::Forming);
            assert!((actual.line.unwrap() - expected.0).abs() < 1e-12);
            assert!((actual.signal.unwrap() - expected.1).abs() < 1e-12);
            assert_eq!(content_hash(&state.base).unwrap(), base_hash);
        }
        let mut restored: State =
            serde_json::from_str(&serde_json::to_string(&state).unwrap()).unwrap();
        let next = macd.update(14.).unwrap();
        assert_eq!(
            state
                .completed_five(3 * FIVE, 14., Some(next.0), Some(next.1))
                .unwrap(),
            restored
                .completed_five(3 * FIVE, 14., Some(next.0), Some(next.1))
                .unwrap()
        );
    }
    #[test]
    fn forming_reversal_blocks_positive_reading_without_ending_the_episode() {
        let mut state = State::new(true);
        state
            .completed_five(10 * SECOND, 10., Some(0.), Some(0.))
            .unwrap();
        let start = state
            .completed_five(15 * SECOND, 20., Some(1.), Some(0.2))
            .unwrap()
            .unwrap();
        assert!(start.episode_started);
        let shared = state.completed_one(15 * SECOND, 19., 20.).unwrap().unwrap();
        assert_eq!(shared.kind, Kind::Completed);
        assert_eq!(shared.line, Some(1.));
        assert_eq!(shared.episode_high, Some(20.));
        let forming = state
            .completed_one(16 * SECOND, 0.01, 0.01)
            .unwrap()
            .unwrap();
        assert!(!forming.positive());
        assert!(!forming.completed_reversal);
        assert_eq!(forming.episode_at_ns, Some(15 * SECOND));
        assert_eq!(forming.prior_episode_high, Some(20.));
        let closed = state
            .completed_five(20 * SECOND, 1., Some(-1.), Some(0.))
            .unwrap()
            .unwrap();
        assert!(closed.completed_reversal);
        assert!(closed.episode_at_ns.is_none());
    }
    #[test]
    fn gap_unknown_data_and_disabled_preview_never_become_fresh_positive_evidence() {
        let mut state = State::new(true);
        state
            .completed_five(10 * SECOND, 10., Some(1.), Some(0.))
            .unwrap();
        state
            .completed_five(20 * SECOND, 11., Some(2.), Some(0.5))
            .unwrap();
        let gap = state.completed_one(21 * SECOND, 11., 12.).unwrap().unwrap();
        assert_eq!(gap.kind, Kind::Unavailable);
        assert!(!gap.positive());
        state.completed_five(25 * SECOND, 11., None, None).unwrap();
        assert_eq!(
            state
                .completed_one(25 * SECOND, 11., 11.)
                .unwrap()
                .unwrap()
                .line,
            None
        );
        let mut disabled = State::new(false);
        disabled
            .completed_five(10 * SECOND, 10., Some(1.), Some(0.))
            .unwrap();
        let retained = disabled
            .completed_one(18 * SECOND, 100., 100.)
            .unwrap()
            .unwrap();
        assert_eq!(retained.at_ns, 10 * SECOND);
        assert_eq!(retained.line, Some(1.));
        assert!(!retained.completed_reversal);
    }
    #[test]
    fn retries_are_idempotent_and_bad_input_does_not_change_state() {
        let mut state = State::new(true);
        state
            .completed_five(10 * SECOND, 10., Some(1.), Some(0.))
            .unwrap();
        state.completed_one(10 * SECOND, 9., 10.).unwrap();
        let hash = content_hash(&state).unwrap();
        assert!(state
            .completed_five(10 * SECOND, 10., Some(1.), Some(0.))
            .unwrap()
            .is_none());
        assert!(state.completed_one(10 * SECOND, 9., 10.).unwrap().is_none());
        assert!(state.completed_one(10 * SECOND, 9., 11.).is_err());
        assert!(state
            .completed_five(9 * SECOND, 10., Some(1.), Some(0.))
            .is_err());
        assert!(state.completed_one(11 * SECOND, 10., f64::NAN).is_err());
        assert_eq!(content_hash(&state).unwrap(), hash);
        let mut wrong_order = State::new(true);
        wrong_order.completed_one(10 * SECOND, 9., 10.).unwrap();
        assert!(wrong_order
            .completed_five(10 * SECOND, 10., Some(1.), Some(0.))
            .is_err());
    }
}
