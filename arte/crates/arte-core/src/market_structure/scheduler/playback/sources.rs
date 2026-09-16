//! Immutable prepared-input catalog. Identity checks do not certify acquisition
//! coverage or establish that a modeled clock reproduces real execution latency.
use super::Prepared;
use crate::{
    content_hash,
    run_manifest::{Clock, Pinned},
    Error, Result,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Shard {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub prepared_hash: String,
    pub clock_model: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Catalog {
    pub schema_version: u32,
    /// Independently certified source generation / recorded observations.
    pub authority_manifest_hash: String,
    pub clock: Clock,
    /// Sorted, unique (provider, instrument, session) keys.
    pub shards: Vec<Shard>,
}
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn key(shard: &Shard) -> (u16, u64, u32) {
    (shard.provider, shard.instrument, shard.session)
}
impl Catalog {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || !hash_valid(&self.authority_manifest_hash)
            || self.shards.is_empty()
            || self.shards.len() > 100_000
            || matches!(self.clock, Clock::Live)
            || matches!(&self.clock, Clock::FaultSimulation {scenario_hash} if !hash_valid(scenario_hash))
        {
            return Err(Error::Invalid("invalid playback source catalog".into()));
        }
        for shard in &self.shards {
            if shard.provider == 0
                || shard.instrument == 0
                || !(19000101..=29991231).contains(&shard.session)
                || !hash_valid(&shard.prepared_hash)
                || shard.clock_model.is_empty()
                || shard.clock_model.len() > 128
            {
                return Err(Error::Invalid("invalid prepared source shard".into()));
            }
        }
        if self
            .shards
            .windows(2)
            .any(|rows| key(&rows[0]) >= key(&rows[1]))
        {
            return Err(Error::Conflict(
                "source shards must be sorted and unique".into(),
            ));
        }
        content_hash(&("arte.playback-source-catalog.v1", self))
    }
    pub fn require(&self, manifest: &Pinned, prepared: &Prepared) -> Result<()> {
        if self.hash()? != manifest.manifest().source_manifest_hash
            || self.clock != manifest.manifest().clock
        {
            return Err(Error::Conflict(
                "run source catalog or clock differs".into(),
            ));
        }
        let scope = prepared.scope;
        let index = self
            .shards
            .binary_search_by_key(&(scope.provider, scope.instrument, scope.session), key)
            .map_err(|_| Error::Unready("prepared source shard is not declared".into()))?;
        let shard = &self.shards[index];
        if shard.prepared_hash != prepared.hash || shard.clock_model != prepared.clock_model {
            return Err(Error::Conflict(
                "prepared input or clock model differs".into(),
            ));
        }
        Ok(())
    }
}
