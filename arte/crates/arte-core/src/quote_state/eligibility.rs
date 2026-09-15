//! Explicit provider-scoped quote eligibility. No guessed numeric condition defaults.
use crate::{
    content_hash,
    events::{Observation, Payload},
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub provider: u16,
    pub valid_from_ns: u64,
    pub valid_to_ns: u64,
    pub available_at_ns: u64,
    pub source_manifest_hash: String,
    pub allowed_conditions: BTreeSet<u16>,
    pub allowed_indicators: BTreeSet<u16>,
    pub allow_empty_conditions: bool,
    pub allow_empty_indicators: bool,
}
pub struct Pinned {
    policy: Policy,
    hash: String,
}
impl Pinned {
    pub fn new(policy: Policy, expected_hash: &str) -> Result<Self> {
        if policy.provider == 0
            || policy.valid_from_ns >= policy.valid_to_ns
            || policy.allowed_conditions.len() > 1024
            || policy.allowed_indicators.len() > 1024
            || policy.source_manifest_hash.len() != 64
            || !policy
                .source_manifest_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid(
                "quote eligibility policy scope or provenance".into(),
            ));
        }
        let hash = content_hash(&policy)?;
        if hash != expected_hash {
            return Err(Error::Conflict(
                "quote eligibility policy hash differs".into(),
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
    pub fn require(&self, event: &Observation, now_ns: u64) -> Result<()> {
        let p = &self.policy;
        if event.key.provider != p.provider
            || event.sip.ns < p.valid_from_ns
            || event.sip.ns >= p.valid_to_ns
            || p.available_at_ns > now_ns
        {
            return Err(Error::Unready(
                "quote eligibility policy is not applicable or available".into(),
            ));
        }
        let Payload::Quote {
            conditions,
            indicators,
            ..
        } = &event.payload
        else {
            return Err(Error::Invalid("quote policy received non-quote".into()));
        };
        if (conditions.is_empty() && !p.allow_empty_conditions)
            || (indicators.is_empty() && !p.allow_empty_indicators)
            || conditions
                .iter()
                .any(|code| !p.allowed_conditions.contains(code))
            || indicators
                .iter()
                .any(|code| !p.allowed_indicators.contains(code))
        {
            return Err(Error::Unready(
                "quote conditions or indicators are not approved".into(),
            ));
        }
        Ok(())
    }
}
