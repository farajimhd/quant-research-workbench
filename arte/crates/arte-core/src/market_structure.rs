//! End-stamped one-second market/V7 bridge, version 1. Not a trading-ready token.
use crate::{
    market::{Series, Update},
    v7_extraction::Candle,
    v7_seed::{HistoricalSeed, SplitAdjustment},
    v7_stream::{Level, Stream, StreamPolicy},
    Error, Result,
};
const SECOND: u64 = 1_000_000_000;
pub struct Config {
    pub instrument: u64,
    pub session: u32,
    pub start_second: u64,
    pub end_second: u64,
    pub macd_periods: (u32, u32, u32),
    pub maximum_bars: usize,
    pub structure: StreamPolicy,
}
pub struct Runtime {
    market: Series,
    structure: Stream,
    start_ns: u64,
    end_ns: u64,
    observed_at_ns: u64,
    failed: Option<String>,
}
impl Runtime {
    pub fn new(seed: &HistoricalSeed, config: Config, split: &SplitAdjustment) -> Result<Self> {
        let start_ns = config
            .start_second
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Invalid("session clock overflow".into()))?;
        let end_ns = config
            .end_second
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Invalid("session clock overflow".into()))?;
        let structure = Stream::new(
            seed,
            config.instrument,
            config.session,
            config.start_second,
            config.end_second,
            config.structure,
            split,
        )?;
        let (fast, slow, signal) = config.macd_periods;
        Ok(Self {
            market: Series::new(SECOND, fast, slow, signal, config.maximum_bars)?,
            structure,
            start_ns,
            end_ns,
            observed_at_ns: start_ns,
            failed: None,
        })
    }
    fn available(&self) -> Result<()> {
        if self.failed.is_some() {
            return Err(Error::Unready(
                "market/structure bridge requires recovery".into(),
            ));
        }
        Ok(())
    }
    fn clock(&self, event_ns: u64, observed_at_ns: u64) -> Result<()> {
        self.available()?;
        if event_ns < self.start_ns
            || event_ns > self.end_ns
            || observed_at_ns < event_ns
            || observed_at_ns < self.observed_at_ns
        {
            return Err(Error::Invalid(
                "market/structure session or observation clock".into(),
            ));
        }
        Ok(())
    }
    fn update_structure(&mut self, previous: usize, observed_at_ns: u64) -> Result<()> {
        if self.market.completed().len() > previous {
            let bar = &self.market.completed().last().unwrap().bar;
            self.structure.update(
                Candle {
                    t: bar.end_ns / SECOND,
                    open: bar.open,
                    high: bar.high,
                    low: bar.low,
                    close: bar.close,
                    volume: bar.volume,
                },
                observed_at_ns / SECOND,
            )?;
        }
        self.observed_at_ns = observed_at_ns;
        Ok(())
    }
    pub fn trade(
        &mut self,
        sip_ns: u64,
        observed_at_ns: u64,
        price: f64,
        size: f64,
        eligible: bool,
    ) -> Result<Update> {
        self.clock(sip_ns, observed_at_ns)?;
        if sip_ns == self.end_ns {
            return Err(Error::Invalid("trade outside half-open session".into()));
        }
        let previous = self.market.completed().len();
        let result = (|| {
            let update = self.market.trade(sip_ns, price, size, eligible)?;
            if matches!(update, Update::Late) {
                return Err(Error::Unready("late event requires ordered repair".into()));
            }
            self.update_structure(previous, observed_at_ns)?;
            Ok(update)
        })();
        if let Err(error) = &result {
            self.failed = Some(error.to_string());
        }
        result
    }
    /// Watermark must already be established by the ordering/coverage authority.
    pub fn advance(&mut self, watermark_ns: u64, observed_at_ns: u64) -> Result<()> {
        self.clock(watermark_ns, observed_at_ns)?;
        let previous = self.market.completed().len();
        let result = self
            .market
            .advance(watermark_ns)
            .and_then(|()| self.update_structure(previous, observed_at_ns));
        if let Err(error) = &result {
            self.failed = Some(error.to_string());
        }
        result
    }
    pub fn levels(&self) -> Result<impl Iterator<Item = &Level>> {
        self.available()?;
        self.structure.qualified_levels()
    }
    pub fn market(&self) -> Result<&Series> {
        self.available()?;
        Ok(&self.market)
    }
    pub fn failure(&self) -> Option<&str> {
        self.failed.as_deref()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::v7_seed::{build, input_hash, SeedPolicy, SourceCertificate};
    fn runtime(maximum_bars: usize) -> Runtime {
        let bars: Vec<_> = (100..118)
            .map(|t| Candle {
                t,
                open: 10.,
                high: 11.,
                low: 9.,
                close: 10.,
                volume: 1.,
            })
            .collect();
        let source = SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "history".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        };
        let seed = build(
            &bars,
            &[],
            source,
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121,
        )
        .unwrap();
        Runtime::new(
            &seed,
            Config {
                instrument: 1,
                session: 20260915,
                start_second: 200,
                end_second: 300,
                macd_periods: (2, 3, 2),
                maximum_bars,
                structure: StreamPolicy {
                    input_generation: "live".into(),
                    ..StreamPolicy::default()
                },
            },
            &SplitAdjustment::default(),
        )
        .unwrap()
    }
    #[test]
    fn completed_seconds_advance_v7_and_failure_hides_all_projections() {
        let mut runtime = runtime(1);
        runtime
            .trade(200 * SECOND + 1, 200 * SECOND + 1, 10., 1., true)
            .unwrap();
        assert_eq!(runtime.structure.bars_processed, 0);
        runtime.advance(201 * SECOND, 201 * SECOND).unwrap();
        assert_eq!(runtime.structure.bars_processed, 1);
        assert_eq!(runtime.structure.as_of, 201);
        runtime
            .trade(201 * SECOND, 201 * SECOND, 11., 1., true)
            .unwrap();
        assert!(runtime.advance(202 * SECOND, 202 * SECOND).is_err());
        assert!(runtime.failure().is_some());
        assert!(runtime.levels().is_err());
        assert!(runtime.market().is_err());
    }
}
