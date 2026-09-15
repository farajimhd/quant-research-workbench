//! End-stamped one-second market/V7 bridge, version 1. Not a trading-ready token.
use crate::{
    market::{Series, Update},
    v7_extraction::Candle,
    v7_seed::{HistoricalSeed, SplitAdjustment},
    v7_stream::{Level, Stream, StreamPolicy},
    Error, Result,
};
const SECOND: u64 = 1_000_000_000;
const RECOVERY_VERSION: &str = "market-structure-recovery-v1";
const MAX_RECOVERY_BYTES: usize = 64 * 1024 * 1024;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
#[derive(Serialize)]
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
    configuration_hash: String,
}
/// Bytes must be persisted with their hash in an independently verified manifest.
/// This snapshot is streaming recovery, never a historical next-session seed.
pub struct Checkpoint {
    pub bytes: Vec<u8>,
    pub hash: String,
}
#[derive(Serialize, Deserialize)]
struct Recovery {
    version: String,
    configuration_hash: String,
    market: Series,
    structure: crate::v7_stream::Recovery,
    start_ns: u64,
    end_ns: u64,
    observed_at_ns: u64,
}
impl Runtime {
    pub fn new(seed: &HistoricalSeed, config: Config, split: &SplitAdjustment) -> Result<Self> {
        let configuration_hash =
            crate::content_hash(&(RECOVERY_VERSION, &config, split.factor, &split.evidence))?;
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
            configuration_hash,
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
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn checkpoint(&self) -> Result<Checkpoint> {
        self.available()?;
        let recovery = Recovery {
            version: RECOVERY_VERSION.into(),
            configuration_hash: self.configuration_hash.clone(),
            market: self.market.clone(),
            structure: self.structure.checkpoint()?,
            start_ns: self.start_ns,
            end_ns: self.end_ns,
            observed_at_ns: self.observed_at_ns,
        };
        let bytes =
            serde_json::to_vec(&recovery).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > MAX_RECOVERY_BYTES {
            return Err(Error::Capacity(
                "combined market recovery byte budget".into(),
            ));
        }
        let hash = format!("{:x}", Sha256::digest(&bytes));
        Ok(Checkpoint { bytes, hash })
    }
    /// expected_hash must come from trusted durable publication, not from the same
    /// untrusted payload. This detects corruption; it is not a signature or fencing.
    pub fn restore(
        bytes: &[u8],
        expected_hash: &str,
        seed_hash: &str,
        configuration_hash: &str,
    ) -> Result<Self> {
        if bytes.len() > MAX_RECOVERY_BYTES
            || format!("{:x}", Sha256::digest(bytes)) != expected_hash
        {
            return Err(Error::Invalid(
                "combined recovery size or content hash".into(),
            ));
        }
        let recovery: Recovery =
            serde_json::from_slice(bytes).map_err(|e| Error::Serialization(e.to_string()))?;
        if recovery.version != RECOVERY_VERSION || recovery.configuration_hash != configuration_hash
        {
            return Err(Error::Conflict(
                "combined recovery version or configuration".into(),
            ));
        }
        let structure = Stream::restore(recovery.structure, seed_hash)?;
        if structure.start.checked_mul(SECOND) != Some(recovery.start_ns)
            || structure.end.checked_mul(SECOND) != Some(recovery.end_ns)
            || recovery.observed_at_ns < recovery.start_ns
            || structure.as_of > recovery.observed_at_ns / SECOND
            || structure.bars_processed != recovery.market.completed().len()
            || recovery
                .market
                .completed()
                .last()
                .is_some_and(|bar| bar.bar.end_ns / SECOND != structure.as_of)
        {
            return Err(Error::Conflict(
                "combined recovery component clocks differ".into(),
            ));
        }
        Ok(Self {
            market: recovery.market,
            structure,
            start_ns: recovery.start_ns,
            end_ns: recovery.end_ns,
            observed_at_ns: recovery.observed_at_ns,
            failed: None,
            configuration_hash: recovery.configuration_hash,
        })
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
        assert!(runtime.checkpoint().is_err());
    }
    #[test]
    fn combined_recovery_continues_exactly_and_binds_seed_and_configuration() {
        let mut original = runtime(10);
        original
            .trade(200 * SECOND + 1, 200 * SECOND + 1, 10., 1., true)
            .unwrap();
        original.advance(201 * SECOND, 201 * SECOND).unwrap();
        original
            .trade(201 * SECOND + 1, 201 * SECOND + 1, 11., 2., true)
            .unwrap();
        let checkpoint = original.checkpoint().unwrap();
        let seed = original.structure.seed_hash.clone();
        let config = original.configuration_hash().to_owned();
        let mut restored =
            Runtime::restore(&checkpoint.bytes, &checkpoint.hash, &seed, &config).unwrap();
        assert!(Runtime::restore(&checkpoint.bytes, "wrong", &seed, &config).is_err());
        assert!(Runtime::restore(&checkpoint.bytes, &checkpoint.hash, "wrong", &config).is_err());
        assert!(Runtime::restore(&checkpoint.bytes, &checkpoint.hash, &seed, "wrong").is_err());
        for second in 202..207 {
            for state in [&mut original, &mut restored] {
                state
                    .trade(
                        second * SECOND,
                        second * SECOND,
                        10. + (second % 2) as f64,
                        1.,
                        true,
                    )
                    .unwrap();
            }
            assert_eq!(
                original.checkpoint().unwrap().hash,
                restored.checkpoint().unwrap().hash
            );
        }
    }
}
