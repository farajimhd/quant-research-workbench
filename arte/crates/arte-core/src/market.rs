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
        bar.volume += size;
        bar.notional += price * size;
        bar.trades += 1;
        self.last_event_ns = Some(sip_ns);
        Ok(Update::Applied(complete))
    }
    /// Caller supplies a causal watermark, not an arbitrary wall-clock completion.
    pub fn advance(&mut self, watermark_ns: u64) -> Option<Bar> {
        if self
            .current
            .as_ref()
            .is_some_and(|b| b.end_ns <= watermark_ns)
        {
            let b = self.current.take();
            self.closed_through = b.as_ref().unwrap().end_ns;
            b
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
}
