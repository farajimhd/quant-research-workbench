use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Bar {
    pub start_ns: u64,
    pub end_ns: u64,
    pub open: f64,
    pub high: f64,
    pub low: f64,
    pub close: f64,
    pub volume: f64,
    pub notional: f64,
    pub trades: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct BarBuilder {
    interval_ns: u64,
    current: Option<Bar>,
    last_event_ns: Option<u64>,
    closed_through: u64,
}
#[derive(Debug, Clone, PartialEq)]
pub enum Update {
    Ineligible,
    Late,
    Applied(Option<Bar>),
}
impl BarBuilder {
    pub fn new(interval_ns: u64) -> Result<Self> {
        if interval_ns == 0 {
            return Err(Error::Invalid("bar interval is zero".into()));
        }
        Ok(Self {
            interval_ns,
            current: None,
            last_event_ns: None,
            closed_through: 0,
        })
    }
    /// Eligibility is supplied by the versioned condition/clock policy, never guessed here.
    pub fn trade(&mut self, sip_ns: u64, price: f64, size: f64, eligible: bool) -> Result<Update> {
        if !price.is_finite() || price <= 0. || !size.is_finite() || size <= 0. {
            return Err(Error::Invalid("invalid trade values".into()));
        }
        if !eligible {
            return Ok(Update::Ineligible);
        }
        if sip_ns < self.closed_through || self.last_event_ns.is_some_and(|at| sip_ns < at) {
            return Ok(Update::Late);
        }
        let start = sip_ns / self.interval_ns * self.interval_ns;
        let end = start
            .checked_add(self.interval_ns)
            .ok_or_else(|| Error::Invalid("bar time overflow".into()))?;
        let prior = self.current.as_ref().filter(|bar| bar.start_ns == start);
        let volume = prior.map_or(0., |bar| bar.volume) + size;
        let notional = prior.map_or(0., |bar| bar.notional) + price * size;
        let trades = prior
            .map_or(0, |bar| bar.trades)
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("bar trade count overflow".into()))?;
        if !volume.is_finite() || !notional.is_finite() {
            return Err(Error::Capacity("bar aggregate overflow".into()));
        }
        let complete = if self.current.as_ref().is_some_and(|b| b.start_ns != start) {
            let old = self.current.take();
            self.closed_through = old.as_ref().unwrap().end_ns;
            old
        } else {
            None
        };
        let bar = self.current.get_or_insert(Bar {
            start_ns: start,
            end_ns: end,
            open: price,
            high: price,
            low: price,
            close: price,
            volume: 0.,
            notional: 0.,
            trades: 0,
        });
        bar.high = bar.high.max(price);
        bar.low = bar.low.min(price);
        bar.close = price;
        bar.volume = volume;
        bar.notional = notional;
        bar.trades = trades;
        self.last_event_ns = Some(sip_ns);
        Ok(Update::Applied(complete))
    }
    /// Caller supplies a causal watermark, not an arbitrary wall-clock completion.
    pub fn advance(&mut self, watermark_ns: u64) -> Option<Bar> {
        // A watermark closes event-time input even during an empty interval or
        // inside a developing bar. It never moves backward.
        self.closed_through = self.closed_through.max(watermark_ns);
        if self
            .current
            .as_ref()
            .is_some_and(|b| b.end_ns <= watermark_ns)
        {
            self.current.take()
        } else {
            None
        }
    }
    pub fn developing(&self) -> Option<&Bar> {
        self.current.as_ref()
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Ema {
    alpha: f64,
    pub value: Option<f64>,
}
impl Ema {
    pub fn new(period: u32) -> Result<Self> {
        if period == 0 {
            return Err(Error::Invalid("EMA period zero".into()));
        }
        Ok(Self {
            alpha: 2. / (period as f64 + 1.),
            value: None,
        })
    }
    pub fn update(&mut self, value: f64) -> Result<f64> {
        if !value.is_finite() {
            return Err(Error::Invalid("nonfinite indicator input".into()));
        }
        let next = self
            .value
            .map_or(value, |last| last + self.alpha * (value - last));
        self.value = Some(next);
        Ok(next)
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Macd {
    fast: Ema,
    slow: Ema,
    signal: Ema,
}
impl Macd {
    pub fn new(fast: u32, slow: u32, signal: u32) -> Result<Self> {
        if fast >= slow {
            return Err(Error::Invalid("MACD fast must be less than slow".into()));
        }
        Ok(Self {
            fast: Ema::new(fast)?,
            slow: Ema::new(slow)?,
            signal: Ema::new(signal)?,
        })
    }
    pub fn update(&mut self, close: f64) -> Result<(f64, f64, f64)> {
        let line = self.fast.update(close)? - self.slow.update(close)?;
        let signal = self.signal.update(line)?;
        Ok((line, signal, line - signal))
    }
    /// Forming-bar preview never advances completed-candle state.
    pub fn preview(&self, close: f64) -> Result<(f64, f64, f64)> {
        self.clone().update(close)
    }
}
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Completed {
    pub bar: Bar,
    pub macd: (f64, f64, f64),
    pub session_vwap: f64,
    pub session_high: f64,
    pub prior_session_high: Option<f64>,
}
/// One instrument/timeframe's retained session series. The same update path serves
/// historical warming and live events; it has no strategy or broker capability.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Series {
    builder: BarBuilder,
    macd: Macd,
    completed: Vec<Completed>,
    maximum_bars: usize,
    completed_volume: f64,
    completed_notional: f64,
    session_high: Option<f64>,
}
impl Series {
    pub fn new(
        interval_ns: u64,
        fast: u32,
        slow: u32,
        signal: u32,
        maximum_bars: usize,
    ) -> Result<Self> {
        if maximum_bars == 0 || maximum_bars > 1_000_000 {
            return Err(Error::Capacity("series bar budget".into()));
        }
        Ok(Self {
            builder: BarBuilder::new(interval_ns)?,
            macd: Macd::new(fast, slow, signal)?,
            completed: vec![],
            maximum_bars,
            completed_volume: 0.,
            completed_notional: 0.,
            session_high: None,
        })
    }
    fn commit(&mut self, builder: BarBuilder, complete: Option<Bar>) -> Result<()> {
        if let Some(bar) = complete {
            if self.completed.len() == self.maximum_bars {
                return Err(Error::Capacity(
                    "session series full; no bars were discarded".into(),
                ));
            }
            let mut macd = self.macd.clone();
            let volume = self.completed_volume + bar.volume;
            let notional = self.completed_notional + bar.notional;
            if !volume.is_finite() || !notional.is_finite() || volume <= 0. || notional <= 0. {
                return Err(Error::Capacity("session market aggregates overflow".into()));
            }
            let session_high = self
                .session_high
                .map_or(bar.high, |high| high.max(bar.high));
            let values = macd.update(bar.close)?;
            if !values.0.is_finite() || !values.1.is_finite() || !values.2.is_finite() {
                return Err(Error::Invalid("nonfinite series indicator".into()));
            }
            self.completed.push(Completed {
                bar,
                macd: values,
                session_vwap: notional / volume,
                session_high,
                prior_session_high: self.session_high,
            });
            self.completed_volume = volume;
            self.completed_notional = notional;
            self.session_high = Some(session_high);
            self.macd = macd;
        }
        self.builder = builder;
        Ok(())
    }
    pub fn trade(&mut self, sip_ns: u64, price: f64, size: f64, eligible: bool) -> Result<Update> {
        let mut builder = self.builder.clone();
        let update = builder.trade(sip_ns, price, size, eligible)?;
        let complete = match &update {
            Update::Applied(bar) => bar.clone(),
            _ => None,
        };
        self.commit(builder, complete)?;
        Ok(update)
    }
    pub fn advance(&mut self, watermark_ns: u64) -> Result<()> {
        let mut builder = self.builder.clone();
        let complete = builder.advance(watermark_ns);
        self.commit(builder, complete)
    }
    pub fn completed(&self) -> &[Completed] {
        &self.completed
    }
    pub fn developing(&self) -> Option<&Bar> {
        self.builder.developing()
    }
    pub fn preview(&self) -> Result<Option<(f64, f64, f64)>> {
        self.developing()
            .map(|bar| self.macd.preview(bar.close))
            .transpose()
    }
    /// Developing-session VWAP, distinct from each completed bar's frozen value.
    pub fn session_vwap(&self) -> Result<Option<f64>> {
        let volume = self.completed_volume + self.developing().map_or(0., |bar| bar.volume);
        let notional = self.completed_notional + self.developing().map_or(0., |bar| bar.notional);
        if !volume.is_finite() || !notional.is_finite() {
            return Err(Error::Capacity(
                "developing session aggregate overflow".into(),
            ));
        }
        Ok((volume > 0.).then(|| notional / volume))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn sparse_bars_and_late_events() {
        let mut b = BarBuilder::new(10).unwrap();
        b.trade(1, 10., 2., true).unwrap();
        assert!(matches!(
            b.trade(31, 12., 1., true).unwrap(),
            Update::Applied(Some(_))
        ));
        assert_eq!(b.trade(2, 99., 1., true).unwrap(), Update::Late);
        assert_eq!(b.developing().unwrap().open, 12.);
    }
    #[test]
    fn checkpoint_continuation() {
        let mut a = BarBuilder::new(10).unwrap();
        a.trade(1, 10., 2., true).unwrap();
        let mut b: BarBuilder = serde_json::from_str(&serde_json::to_string(&a).unwrap()).unwrap();
        assert_eq!(
            a.trade(11, 11., 1., true).unwrap(),
            b.trade(11, 11., 1., true).unwrap()
        );
    }
    #[test]
    fn forming_macd_does_not_mutate() {
        let mut m = Macd::new(12, 26, 9).unwrap();
        m.update(10.).unwrap();
        let before = serde_json::to_string(&m).unwrap();
        m.preview(11.).unwrap();
        assert_eq!(before, serde_json::to_string(&m).unwrap());
    }
    #[test]
    fn watermark_closes_empty_and_developing_intervals_monotonically() {
        let mut builder = BarBuilder::new(10).unwrap();
        assert!(builder.advance(25).is_none());
        assert_eq!(builder.trade(24, 10., 1., true).unwrap(), Update::Late);
        assert_eq!(
            builder.trade(25, 10., 1., true).unwrap(),
            Update::Applied(None)
        );
        assert!(builder.advance(28).is_none());
        assert!(builder.advance(20).is_none());
        assert_eq!(builder.trade(27, 20., 1., true).unwrap(), Update::Late);
        assert_eq!(builder.advance(40).unwrap().close, 10.);
        assert_eq!(builder.trade(39, 20., 1., true).unwrap(), Update::Late);
        assert_eq!(
            builder.trade(40, 20., 1., true).unwrap(),
            Update::Applied(None)
        );
    }
    #[test]
    fn aggregate_overflow_leaves_current_bar_unchanged() {
        let mut builder = BarBuilder::new(10).unwrap();
        builder.trade(1, 10., 1., true).unwrap();
        let before = serde_json::to_string(&builder).unwrap();
        for at in [2, 11] {
            assert!(matches!(
                builder.trade(at, f64::MAX, 2., true),
                Err(Error::Capacity(_))
            ));
            assert_eq!(serde_json::to_string(&builder).unwrap(), before);
        }
    }
    #[test]
    fn retained_series_matches_shared_algorithms_and_rejects_overflow_atomically() {
        let mut series = Series::new(10, 2, 3, 2, 2).unwrap();
        let mut expected = Macd::new(2, 3, 2).unwrap();
        series.trade(1, 10., 1., true).unwrap();
        series.trade(11, 11., 1., true).unwrap();
        assert_eq!(series.completed()[0].macd, expected.update(10.).unwrap());
        series.advance(20).unwrap();
        assert_eq!(series.completed()[1].macd, expected.update(11.).unwrap());
        assert_eq!(series.completed()[0].session_vwap, 10.);
        assert_eq!(series.completed()[0].prior_session_high, None);
        assert_eq!(series.completed()[1].session_vwap, 10.5);
        assert_eq!(series.completed()[1].prior_session_high, Some(10.));
        assert_eq!(series.completed()[1].session_high, 11.);
        series.trade(21, 12., 1., true).unwrap();
        assert_eq!(series.session_vwap().unwrap(), Some(11.));
        assert_eq!(series.completed()[1].session_vwap, 10.5);
        let before = series.completed().to_vec();
        let developing = series.developing().cloned();
        assert!(series.advance(30).is_err());
        assert_eq!(series.completed(), before);
        assert_eq!(series.developing(), developing.as_ref());
        assert!(series.trade(31, 13., 1., true).is_err());
        assert_eq!(series.developing(), developing.as_ref());
        assert_eq!(
            series.preview().unwrap(),
            Some(expected.preview(12.).unwrap())
        );
    }
}
