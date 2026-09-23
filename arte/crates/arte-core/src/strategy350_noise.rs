//! Strategy 350 causal adaptive-distance evidence from completed one-second bars.
//! Shared by live and historical projection. This is not a bracket/order authority.
use crate::{content_hash, execution_interval::ExecutionInterval, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeSet, VecDeque};

const SECOND: u64 = 1_000_000_000;
const BPS: i128 = 10_000;
pub const VERSION: &str = "arte.strategy-350-noise.v1";

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub price_scale: u8,
    pub short_multiplier_bps: u32,
    pub session_multiplier_bps: u32,
    pub maximum_entry_fraction_bps: u32,
    pub percentile_bps: u32,
    pub minimum_session_samples: usize,
    pub maximum_session_samples: usize,
    pub source_algorithm_hash: String,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        self.execution_interval.validate()?;
        if self.execution_interval != ExecutionInterval::Fixed(SECOND)
            || !(1..=9).contains(&self.price_scale)
            || self.short_multiplier_bps == 0
            || self.session_multiplier_bps == 0
            || self.maximum_entry_fraction_bps == 0
            || self.maximum_entry_fraction_bps > 10_000
            || self.percentile_bps == 0
            || self.percentile_bps > 10_000
            || self.minimum_session_samples == 0
            || self.maximum_session_samples < self.minimum_session_samples
            || self.maximum_session_samples > 60_000
            || self.source_algorithm_hash.len() != 64
            || !self
                .source_algorithm_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("Strategy 350 noise configuration".into()));
        }
        content_hash(&(VERSION, self))
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct CompletedBar {
    pub end_ns: u64,
    pub high_atoms: i64,
    pub low_atoms: i64,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Distance {
    pub observed_at_ns: u64,
    pub short_range_atoms: i64,
    pub session_p90_atoms: i64,
    pub session_samples: usize,
    /// Required distance in 1/10,000 price atoms. Compare this integer against
    /// the cap before any tick rounding; no floating-point boundary drift.
    pub required_scaled: i128,
    pub maximum_scaled: i128,
    pub within_cap: bool,
}
pub struct State {
    config: Config,
    configuration_hash: String,
    session_start_ns: u64,
    session_end_ns: u64,
    bars: VecDeque<CompletedBar>,
    ranges: VecDeque<(i64, u64)>,
    lower: BTreeSet<(i64, u64)>,
    upper: BTreeSet<(i64, u64)>,
    next_range_id: u64,
    last_end_ns: Option<u64>,
    failed: bool,
}
impl State {
    pub fn new(config: Config, session_start_ns: u64, session_end_ns: u64) -> Result<Self> {
        let configuration_hash = config.hash()?;
        if session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(SECOND)
            || !session_end_ns.is_multiple_of(SECOND)
        {
            return Err(Error::Invalid("Strategy 350 noise session".into()));
        }
        Ok(Self {
            config,
            configuration_hash,
            session_start_ns,
            session_end_ns,
            bars: VecDeque::with_capacity(5),
            ranges: VecDeque::new(),
            lower: BTreeSet::new(),
            upper: BTreeSet::new(),
            next_range_id: 0,
            last_end_ns: None,
            failed: false,
        })
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    pub fn last_end_ns(&self) -> Option<u64> {
        self.last_end_ns
    }
    fn healthy(&self) -> Result<()> {
        if self.failed {
            Err(Error::Unready(
                "Strategy 350 noise requires recovery".into(),
            ))
        } else {
            Ok(())
        }
    }
    fn rebalance(&mut self) {
        let n = self.lower.len() + self.upper.len();
        let target = (self.config.percentile_bps as usize * n).div_ceil(10_000);
        while self.lower.len() > target {
            let largest = *self.lower.last().unwrap();
            self.lower.remove(&largest);
            self.upper.insert(largest);
        }
        while self.lower.len() < target {
            let smallest = *self.upper.first().unwrap();
            self.upper.remove(&smallest);
            self.lower.insert(smallest);
        }
        while self
            .lower
            .last()
            .zip(self.upper.first())
            .is_some_and(|(a, b)| a > b)
        {
            let largest = *self.lower.last().unwrap();
            let smallest = *self.upper.first().unwrap();
            self.lower.remove(&largest);
            self.upper.remove(&smallest);
            self.lower.insert(smallest);
            self.upper.insert(largest);
        }
    }
    fn insert_range(&mut self, range: i64) -> Result<()> {
        let id = self
            .next_range_id
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("Strategy 350 range sequence".into()))?;
        self.next_range_id = id;
        let item = (range, id);
        if self.lower.last().is_none_or(|last| item <= *last) {
            self.lower.insert(item);
        } else {
            self.upper.insert(item);
        }
        self.ranges.push_back(item);
        if self.ranges.len() > self.config.maximum_session_samples {
            let oldest = self.ranges.pop_front().unwrap();
            if !self.lower.remove(&oldest) {
                self.upper.remove(&oldest);
            }
        }
        self.rebalance();
        Ok(())
    }
    /// A completed bar must be delivered exactly once in increasing market
    /// time. Legitimate no-trade seconds are absent; coverage is a separate
    /// market-data authority and must not be inferred from this state.
    pub fn observe(&mut self, bar: CompletedBar) -> Result<()> {
        self.healthy()?;
        let result = (|| {
            if bar.end_ns <= self.session_start_ns
                || bar.end_ns > self.session_end_ns
                || !bar.end_ns.is_multiple_of(SECOND)
                || self.last_end_ns.is_some_and(|last| bar.end_ns <= last)
                || bar.low_atoms <= 0
                || bar.high_atoms < bar.low_atoms
            {
                return Err(Error::Invalid("Strategy 350 completed bar".into()));
            }
            if self.bars.len() == 5 {
                self.bars.pop_front();
            }
            self.bars.push_back(bar);
            if self.bars.len() == 5 {
                let high = self.bars.iter().map(|b| b.high_atoms).max().unwrap();
                let low = self.bars.iter().map(|b| b.low_atoms).min().unwrap();
                self.insert_range(
                    high.checked_sub(low)
                        .ok_or_else(|| Error::Capacity("Strategy 350 five-bar range".into()))?,
                )?;
            }
            self.last_end_ns = Some(bar.end_ns);
            Ok(())
        })();
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    pub fn distance(&self, entry_atoms: i64) -> Result<Distance> {
        self.healthy()?;
        if entry_atoms <= 0 {
            return Err(Error::Invalid("Strategy 350 entry price".into()));
        }
        let short_range_atoms = if self.bars.len() >= 2 {
            self.bars
                .iter()
                .rev()
                .take(2)
                .map(|b| b.high_atoms)
                .max()
                .unwrap()
                .checked_sub(
                    self.bars
                        .iter()
                        .rev()
                        .take(2)
                        .map(|b| b.low_atoms)
                        .min()
                        .unwrap(),
                )
                .ok_or_else(|| Error::Capacity("Strategy 350 two-bar range".into()))?
        } else {
            0
        };
        let session_p90_atoms = if self.ranges.len() >= self.config.minimum_session_samples {
            self.lower.last().map_or(0, |item| item.0)
        } else {
            0
        };
        let minimum_atoms = 10_i128.pow(u32::from(self.config.price_scale - 1));
        let required_scaled = (minimum_atoms * BPS)
            .max(i128::from(short_range_atoms) * i128::from(self.config.short_multiplier_bps))
            .max(i128::from(session_p90_atoms) * i128::from(self.config.session_multiplier_bps));
        let maximum_scaled = (minimum_atoms * BPS)
            .max(i128::from(entry_atoms) * i128::from(self.config.maximum_entry_fraction_bps));
        Ok(Distance {
            observed_at_ns: self.last_end_ns.unwrap_or(self.session_start_ns),
            short_range_atoms,
            session_p90_atoms,
            session_samples: self.ranges.len(),
            required_scaled,
            maximum_scaled,
            within_cap: required_scaled <= maximum_scaled,
        })
    }
    /// Batch output is contiguous and uses the same state machine as live.
    /// Independent ticker/session batches can be processed concurrently.
    pub fn project(
        config: Config,
        session_start_ns: u64,
        session_end_ns: u64,
        bars: &[CompletedBar],
        entry_atoms: &[i64],
    ) -> Result<Vec<Distance>> {
        if bars.len() != entry_atoms.len() || bars.len() > 1_000_000 {
            return Err(Error::Invalid("Strategy 350 noise batch geometry".into()));
        }
        let mut state = Self::new(config, session_start_ns, session_end_ns)?;
        let mut out = Vec::with_capacity(bars.len());
        for (bar, entry) in bars.iter().zip(entry_atoms) {
            state.observe(*bar)?;
            out.push(state.distance(*entry)?);
        }
        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    const START: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Fixed(SECOND),
            price_scale: 2,
            short_multiplier_bps: 15_000,
            session_multiplier_bps: 12_500,
            maximum_entry_fraction_bps: 500,
            percentile_bps: 9_000,
            minimum_session_samples: 6,
            maximum_session_samples: 60_000,
            source_algorithm_hash: "a".repeat(64),
        }
    }
    #[test]
    fn live_and_batch_share_exact_adaptive_distance() {
        let bars: Vec<_> = (1..=12)
            .map(|i| CompletedBar {
                end_ns: START + i * SECOND,
                high_atoms: 1000 + i as i64 + if i == 8 { 40 } else { 0 },
                low_atoms: 990 + i as i64,
            })
            .collect();
        let entries = vec![1000; bars.len()];
        let batch = State::project(config(), START, START + 20 * SECOND, &bars, &entries).unwrap();
        let mut live = State::new(config(), START, START + 20 * SECOND).unwrap();
        for (i, bar) in bars.iter().enumerate() {
            live.observe(*bar).unwrap();
            assert_eq!(live.distance(1000).unwrap(), batch[i]);
        }
        assert_eq!(batch[0].short_range_atoms, 0);
        assert_eq!(batch[1].short_range_atoms, 11);
        assert_eq!(batch[8].session_samples, 5);
        assert_eq!(batch[9].session_samples, 6);
        assert!(batch[9].session_p90_atoms > 0);
        assert!(live.observe(bars[11]).is_err());
        assert!(live.distance(1000).is_err());
    }
    #[test]
    fn percentile_window_and_exact_cap_are_bounded() {
        let mut c = config();
        c.maximum_session_samples = 6;
        let mut state = State::new(c, START, START + 20 * SECOND).unwrap();
        for i in 1..=12 {
            state
                .observe(CompletedBar {
                    end_ns: START + i * SECOND,
                    high_atoms: 1000 + if i == 8 { 90 } else { 0 },
                    low_atoms: 990,
                })
                .unwrap();
        }
        let distance = state.distance(1000).unwrap();
        assert_eq!(distance.session_samples, 6);
        assert_eq!(distance.session_p90_atoms, 100);
        assert!(!distance.within_cap);
        assert_eq!(distance.maximum_scaled, 50 * BPS);
        let mut changed = config();
        changed.execution_interval = ExecutionInterval::Events;
        assert!(changed.hash().is_err());
    }
}
