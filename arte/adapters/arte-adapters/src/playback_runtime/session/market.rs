//! Portable, pinned assembly of the market owner used by a fresh backtest.
//! No service startup, environment lookup, seed discovery or source fallback.
use arte_core::{
    content_hash,
    coverage::Interval,
    market_structure::{
        self,
        scheduler::{
            playback::{accounts::Run, sources::Catalog, Prepared},
            Scheduler,
        },
        Ordered,
    },
    quote_state::eligibility::{Pinned as QuotePolicy, Policy},
    run_manifest::Pinned,
    seed_storage::Bundle,
    v7_seed::SplitAdjustment,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::{io::Read, sync::Arc};

pub const MAXIMUM_BYTES: usize = 1024 * 1024;
#[cfg(test)]
mod tests;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Document {
    pub schema_version: u32,
    pub manifest_hash: String,
    pub configuration: market_structure::Config,
    pub split: SplitAdjustment,
    pub quote_policy: Policy,
    pub maximum_pending_events: usize,
    pub frames_per_poll: usize,
    pub maximum_consumers: usize,
}
impl Document {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.manifest_hash.len() != 64
            || !self
                .manifest_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            || self.maximum_pending_events == 0
            || self.maximum_pending_events > 10_000_000
            || self.frames_per_poll == 0
            || self.frames_per_poll > 4096
            || self.maximum_consumers == 0
            || self.maximum_consumers > 4096
        {
            return Err(Error::Invalid(
                "market startup document shape or limits".into(),
            ));
        }
        let bytes = serde_json::to_vec(self).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity("market startup document bytes".into()));
        }
        content_hash(&("arte.backtest-market-startup.v1", self))
    }
    pub fn read(reader: impl Read, expected_hash: &str) -> Result<Self> {
        let mut bytes = Vec::new();
        reader
            .take(MAXIMUM_BYTES as u64 + 1)
            .read_to_end(&mut bytes)
            .map_err(|e| Error::Unready(format!("market startup document read: {e}")))?;
        if bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity("market startup document bytes".into()));
        }
        let document: Self =
            serde_json::from_slice(&bytes).map_err(|e| Error::Serialization(e.to_string()))?;
        if document.hash()? != expected_hash {
            return Err(Error::Conflict(
                "market startup document identity differs".into(),
            ));
        }
        Ok(document)
    }
    /// Single-instrument run: seed_manifest_hash is the canonical hash of the
    /// supplied historical seed manifest, not a streaming checkpoint or seed ID.
    /// Persisted acquisition/seed publication proofs must be obtained by loaders.
    pub fn assemble(
        self,
        expected_hash: &str,
        manifest: &Pinned,
        sources: &Catalog,
        prepared: Prepared,
        seed: &Bundle,
    ) -> Result<Run> {
        if self.hash()? != expected_hash
            || self.manifest_hash != manifest.hash()
            || content_hash(&seed.manifest)? != manifest.manifest().seed_manifest_hash
        {
            return Err(Error::Conflict(
                "market startup run or historical seed identity differs".into(),
            ));
        }
        let config = &self.configuration;
        if manifest
            .manifest()
            .consumers
            .iter()
            .any(|c| c.instrument != config.instrument)
        {
            return Err(Error::Conflict(
                "market startup requires single instrument manifest".into(),
            ));
        }
        let nanos = |s: u64| {
            s.checked_mul(1_000_000_000)
                .ok_or_else(|| Error::Invalid("market startup clock overflow".into()))
        };
        let interval = Interval {
            start: nanos(config.start_second)?,
            end: nanos(config.end_second)?,
        };
        sources.require(manifest, &prepared)?;
        prepared.require_interval(interval)?;
        let policy = QuotePolicy::new(
            self.quote_policy.clone(),
            &content_hash(&self.quote_policy)?,
        )?;
        if policy.provider() != config.provider {
            return Err(Error::Conflict(
                "market startup quote policy provider differs".into(),
            ));
        }
        policy.require_interval(interval, interval.start)?;
        let seed = seed.hydrate()?;
        let market = market_structure::Runtime::new(&seed, self.configuration, &self.split)?;
        let mut scheduler = Scheduler::new(
            Ordered::new(market, self.maximum_pending_events)?,
            manifest.manifest().run_id.clone(),
        )?;
        scheduler.bind_quote_policy(Arc::new(policy))?;
        Run::new(
            manifest,
            sources,
            scheduler,
            prepared,
            self.frames_per_poll,
            self.maximum_consumers,
        )
    }
}
