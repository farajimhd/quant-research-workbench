//! Explicit hypothetical fill policy. Not a claim about actual broker latency.
use crate::{
    content_hash,
    simulated_execution::{Simulator, MODEL},
    Error, Result,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    pub schema_version: u32,
    pub algorithm: String,
    /// Shared displayed liquidity budget across all orders on an instrument lane.
    pub participation_bps: u32,
    /// Fixed modeled delay from accepted submission to entry-fill eligibility.
    pub submission_latency_ns: u64,
    pub maximum_quote_age_ns: u64,
}
impl Model {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.algorithm != MODEL
            || self.participation_bps == 0
            || self.participation_bps > 10000
            || self.submission_latency_ns > 60_000_000_000
            || self.maximum_quote_age_ns == 0
            || self.maximum_quote_age_ns > 60_000_000_000
        {
            return Err(Error::Invalid(
                "unsupported simulated fill model or limits".into(),
            ));
        }
        content_hash(&("arte.simulated-fill-policy.v1", self))
    }
    pub fn require(&self, expected_hash: &str, simulator: &Simulator) -> Result<()> {
        if self.hash()? != expected_hash || self.participation_bps != simulator.participation_bps()
        {
            return Err(Error::Conflict(
                "simulator differs from pinned fill model".into(),
            ));
        }
        Ok(())
    }
    pub fn require_latency(&self, latency_ns: u64) -> Result<()> {
        self.hash()?;
        if latency_ns != self.submission_latency_ns {
            return Err(Error::Conflict(
                "submission latency differs from fill model".into(),
            ));
        }
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn every_behavioral_setting_is_pinned_and_unknown_algorithms_fail() {
        let model = Model {
            schema_version: 1,
            algorithm: MODEL.into(),
            participation_bps: 5000,
            submission_latency_ns: 25,
            maximum_quote_age_ns: 100,
        };
        let sim = Simulator::new_scoped("r", 1, 2, 4, 5000).unwrap();
        let hash = model.hash().unwrap();
        model.require(&hash, &sim).unwrap();
        model.require_latency(25).unwrap();
        assert!(model.require_latency(0).is_err());
        let other = Simulator::new_scoped("r", 1, 2, 4, 10000).unwrap();
        assert!(model.require(&hash, &other).is_err());
        for field in 0..3 {
            let mut changed = model.clone();
            match field {
                0 => changed.participation_bps += 1,
                1 => changed.submission_latency_ns += 1,
                _ => changed.maximum_quote_age_ns += 1,
            }
            assert!(changed.require(&hash, &sim).is_err());
        }
        let mut changed = model;
        changed.algorithm = "unknown".into();
        assert!(changed.hash().is_err());
    }
}
