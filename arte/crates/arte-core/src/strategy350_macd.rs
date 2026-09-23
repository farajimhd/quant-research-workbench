//! Strategy 350's four forming-MACD purchase predicates. Completed bars own
//! the EMA state; trade previews never compound into subsequent bars.
use crate::{
    content_hash, event_order::Scope, events::Decimal, execution_interval::ExecutionInterval,
    market::Macd, Error, Result,
};
use serde::{Deserialize, Serialize};

const SECOND: u64 = 1_000_000_000;
pub const TIMEFRAMES_NS: [u64; 4] = [SECOND, 5 * SECOND, 10 * SECOND, 30 * SECOND];
pub const VERSION: &str = "arte.strategy-350-forming-macd.v1";

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub source_algorithm_hash: String,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        self.execution_interval.validate()?;
        if self.execution_interval != ExecutionInterval::Events
            || self.source_algorithm_hash.len() != 64
            || !self
                .source_algorithm_hash
                .bytes()
                .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
        {
            return Err(Error::Invalid("Strategy 350 MACD configuration".into()));
        }
        content_hash(&(VERSION, self, TIMEFRAMES_NS, (12, 26, 9)))
    }
}

#[derive(Clone)]
struct Frame {
    interval_ns: u64,
    last_end_ns: Option<u64>,
    macd: Macd,
}
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Preview {
    pub timeframe_ns: u64,
    pub completed_end_ns: u64,
    pub line: f64,
    pub signal: f64,
}
#[derive(Debug, Clone, PartialEq)]
pub struct Outcome {
    pub event_time_ns: u64,
    pub evaluated_at_ns: u64,
    pub previews: [Option<Preview>; 4],
    pub bullish: bool,
}
pub struct State {
    scope: Scope,
    session_start_ns: u64,
    session_end_ns: u64,
    config_hash: String,
    frames: [Frame; 4],
}
impl State {
    pub fn new(
        scope: Scope,
        session_start_ns: u64,
        session_end_ns: u64,
        config: &Config,
    ) -> Result<Self> {
        let config_hash = config.hash()?;
        if scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || session_start_ns == 0
            || session_start_ns >= session_end_ns
        {
            return Err(Error::Invalid("Strategy 350 MACD session".into()));
        }
        let frames = TIMEFRAMES_NS.map(|interval_ns| {
            Ok(Frame {
                interval_ns,
                last_end_ns: None,
                macd: Macd::new(12, 26, 9)?,
            })
        });
        Ok(Self {
            scope,
            session_start_ns,
            session_end_ns,
            config_hash,
            frames: frames
                .into_iter()
                .collect::<Result<Vec<_>>>()?
                .try_into()
                .map_err(|_| Error::Conflict("Strategy 350 MACD frame count".into()))?,
        })
    }
    pub fn scope(&self) -> Scope {
        self.scope
    }
    pub fn config_hash(&self) -> &str {
        &self.config_hash
    }
    pub fn observe_completed(
        &mut self,
        timeframe_ns: u64,
        end_ns: u64,
        close: Decimal,
    ) -> Result<()> {
        let frame = self
            .frames
            .iter_mut()
            .find(|frame| frame.interval_ns == timeframe_ns)
            .ok_or_else(|| Error::Invalid("Strategy 350 MACD timeframe".into()))?;
        if !close.positive()
            || end_ns <= self.session_start_ns
            || end_ns > self.session_end_ns
            || !end_ns.is_multiple_of(timeframe_ns)
            || frame.last_end_ns.is_some_and(|prior| end_ns <= prior)
        {
            return Err(Error::Conflict("Strategy 350 MACD completed bar".into()));
        }
        frame.macd.update(close.to_f64())?;
        frame.last_end_ns = Some(end_ns);
        Ok(())
    }
    /// A source event can be evaluated only after its modeled/live availability.
    /// Stale or missing completed frames produce a false gate, not a fabricated
    /// bullish value. Source feed latency is audited by a separate authority.
    pub fn preview_trade(
        &self,
        event_time_ns: u64,
        evaluated_at_ns: u64,
        price: Decimal,
    ) -> Result<Outcome> {
        if event_time_ns < self.session_start_ns
            || event_time_ns >= self.session_end_ns
            || evaluated_at_ns < event_time_ns
            || !price.positive()
        {
            return Err(Error::Invalid(
                "Strategy 350 MACD trade clock or price".into(),
            ));
        }
        let mut previews = [None; 4];
        for (index, frame) in self.frames.iter().enumerate() {
            let Some(end) = frame.last_end_ns else {
                continue;
            };
            if end > event_time_ns || event_time_ns - end > frame.interval_ns {
                continue;
            }
            let (line, signal, _) = frame.macd.preview(price.to_f64())?;
            previews[index] = Some(Preview {
                timeframe_ns: frame.interval_ns,
                completed_end_ns: end,
                line,
                signal,
            });
        }
        Ok(Outcome {
            event_time_ns,
            evaluated_at_ns,
            bullish: previews
                .iter()
                .all(|preview| preview.is_some_and(|value| value.line > value.signal)),
            previews,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn d(atoms: i64) -> Decimal {
        Decimal { atoms, scale: 0 }
    }
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            source_algorithm_hash: "a".repeat(64),
        }
    }
    fn state() -> State {
        State::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            30 * SECOND,
            120 * SECOND,
            &config(),
        )
        .unwrap()
    }
    #[test]
    fn four_completed_timeframes_are_required_and_trade_previews_do_not_compound() {
        let mut state = state();
        let price = d(100);
        assert!(
            !state
                .preview_trade(60 * SECOND, 60 * SECOND, price)
                .unwrap()
                .bullish
        );
        for timeframe in TIMEFRAMES_NS {
            state
                .observe_completed(timeframe, 60 * SECOND, price)
                .unwrap();
        }
        let first = state
            .preview_trade(60 * SECOND + 1, 60 * SECOND + 2, d(110))
            .unwrap();
        assert!(first.bullish);
        assert_eq!(
            first,
            state
                .preview_trade(60 * SECOND + 1, 60 * SECOND + 2, d(110))
                .unwrap()
        );
        assert!(
            !state
                .preview_trade(62 * SECOND + 1, 62 * SECOND + 2, d(110))
                .unwrap()
                .bullish
        );
        assert!(state.observe_completed(SECOND, 60 * SECOND, price).is_err());
    }
    #[test]
    fn configuration_and_clocks_fail_closed() {
        let mut wrong = config();
        wrong.execution_interval = ExecutionInterval::Fixed(100_000_000);
        assert!(wrong.hash().is_err());
        let mut state = state();
        assert!(state
            .observe_completed(2 * SECOND, 60 * SECOND, Decimal { atoms: 1, scale: 0 })
            .is_err());
        assert!(state
            .preview_trade(60 * SECOND, 60 * SECOND - 1, Decimal { atoms: 1, scale: 0 })
            .is_err());
    }
    #[test]
    fn preview_matches_inspected_successor_algebra_without_mutating_completed_ema() {
        let mut macd = Macd::new(12, 26, 9).unwrap();
        let mut base = (0., 0., 0.);
        let mut prior_line = 0.;
        for close in [100., 101., 99., 104., 107., 103., 108., 110.] {
            prior_line = base.0;
            base = macd.update(close).unwrap();
        }
        let close = 110.;
        let preview_price = 113.;
        let fast_alpha = 2. / 13.;
        let slow_alpha = 2. / 27.;
        let previous_slow =
            close - (base.0 - (1. - fast_alpha) * prior_line) / (fast_alpha - slow_alpha);
        let current_slow = slow_alpha * close + (1. - slow_alpha) * previous_slow;
        let line = fast_alpha * preview_price + (1. - fast_alpha) * (current_slow + base.0)
            - (slow_alpha * preview_price + (1. - slow_alpha) * current_slow);
        let signal = 0.2 * line + 0.8 * base.1;
        let preview = macd.preview(preview_price).unwrap();
        assert!((preview.0 - line).abs() < 1e-10);
        assert!((preview.1 - signal).abs() < 1e-10);
        assert_eq!(preview, macd.preview(preview_price).unwrap());
    }
}
