//! Provider-scoped calculation eligibility shared by Live and Backtest.
//! No numerical provider condition code or correction meaning is guessed.
use crate::{
    content_hash,
    coverage::Interval,
    events::{Observation, Payload},
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
#[cfg(test)]
mod tests;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub schema_version: u32,
    pub provider: u16,
    pub valid_from_ns: u64,
    pub valid_to_ns: u64,
    pub available_at_ns: u64,
    pub source_manifest_hash: String,
    pub allowed_conditions: BTreeSet<u16>,
    pub excluded_conditions: BTreeSet<u16>,
    pub allow_empty_conditions: bool,
}
pub struct Pinned {
    policy: Policy,
    hash: String,
}
impl Policy {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.provider == 0
            || self.valid_from_ns >= self.valid_to_ns
            || self.allowed_conditions.len() + self.excluded_conditions.len() > 1024
            || !self
                .allowed_conditions
                .is_disjoint(&self.excluded_conditions)
            || self.source_manifest_hash.len() != 64
            || !self
                .source_manifest_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid(
                "trade eligibility policy scope or provenance".into(),
            ));
        }
        content_hash(&("arte.trade-eligibility.v1", self))
    }
}
impl Pinned {
    pub fn new(policy: Policy, expected_hash: &str) -> Result<Self> {
        let hash = policy.hash()?;
        if hash != expected_hash {
            return Err(Error::Conflict(
                "trade eligibility policy hash differs".into(),
            ));
        }
        Ok(Self { policy, hash })
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
    pub fn provider(&self) -> u16 {
        self.policy.provider
    }
    pub fn require_interval(&self, interval: Interval, as_of_ns: u64) -> Result<()> {
        interval.validate()?;
        if interval.start < self.policy.valid_from_ns
            || interval.end > self.policy.valid_to_ns
            || self.policy.available_at_ns > as_of_ns
        {
            return Err(Error::Unready(
                "trade policy does not cover causal interval".into(),
            ));
        }
        Ok(())
    }
    /// False means a known exclusion, never missing metadata or an unknown code.
    /// This controls the shared all-or-none bar calculation contract. It is not
    /// a provider's full independent OHLC/volume/consolidation rule interpreter.
    pub fn evaluate(&self, event: &Observation, evaluated_at_ns: u64) -> Result<bool> {
        event.validate()?;
        let p = &self.policy;
        if event.key.provider != p.provider
            || event.sip.ns < p.valid_from_ns
            || event.sip.ns >= p.valid_to_ns
            || p.available_at_ns > evaluated_at_ns
            || event.sip.ns > evaluated_at_ns
        {
            return Err(Error::Unready(
                "trade policy not applicable or causally available".into(),
            ));
        }
        let Payload::Trade {
            conditions,
            correction,
            ..
        } = &event.payload
        else {
            return Err(Error::Invalid(
                "trade eligibility received non-trade".into(),
            ));
        };
        if correction.is_some() {
            return Err(Error::Unready(
                "marked trade requires causal correction contract".into(),
            ));
        }
        if conditions.is_empty() {
            return Ok(p.allow_empty_conditions);
        }
        let mut eligible = true;
        for code in conditions {
            if p.excluded_conditions.contains(code) {
                eligible = false;
            } else if !p.allowed_conditions.contains(code) {
                return Err(Error::Unready(format!("unknown trade condition {code}")));
            }
        }
        Ok(eligible)
    }
}
