//! One immutable historical V7 seed selection for every run source shard.
//! The seed object itself is still verified and hydrated by its own owner.
use arte_core::{
    content_hash, event_order::Scope, market_structure::scheduler::playback::sources::Catalog,
    publication::SeedProducer, run_manifest::Pinned, seed_storage::Bundle, Error, Result,
};
use serde::{Deserialize, Serialize};

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Entry {
    pub provider: u16,
    pub instrument: u64,
    pub session: u32,
    pub seed_manifest_hash: String,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RunSeedCatalog {
    pub schema_version: u32,
    pub entries: Vec<Entry>,
}
fn key(provider: u16, instrument: u64, session: u32) -> (u16, u64, u32) {
    (provider, instrument, session)
}
fn valid_hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
}
impl RunSeedCatalog {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.entries.is_empty()
            || self.entries.len() > 100_000
            || self.entries.iter().any(|entry| {
                entry.provider == 0
                    || entry.instrument == 0
                    || !(19000101..=29991231).contains(&entry.session)
                    || !valid_hash(&entry.seed_manifest_hash)
            })
            || self.entries.windows(2).any(|pair| {
                key(pair[0].provider, pair[0].instrument, pair[0].session)
                    >= key(pair[1].provider, pair[1].instrument, pair[1].session)
            })
        {
            return Err(Error::Conflict("run seed catalog shape or ordering".into()));
        }
        content_hash(&("arte.backtest-run-seed-catalog.v1", self))
    }
    pub fn require_run(&self, manifest: &Pinned, sources: &Catalog) -> Result<()> {
        if self.hash()? != manifest.manifest().seed_manifest_hash
            || sources.hash()? != manifest.manifest().source_manifest_hash
            || self.entries.len() != sources.shards.len()
            || self
                .entries
                .iter()
                .zip(&sources.shards)
                .any(|(entry, shard)| {
                    key(entry.provider, entry.instrument, entry.session)
                        != key(shard.provider, shard.instrument, shard.session)
                })
        {
            return Err(Error::Conflict(
                "run seed and source shard sets differ".into(),
            ));
        }
        Ok(())
    }
    pub fn require_shard(
        &self,
        manifest: &Pinned,
        sources: &Catalog,
        scope: Scope,
        start_ns: u64,
        seed: &Bundle,
    ) -> Result<()> {
        self.require_run(manifest, sources)?;
        let identity = key(scope.provider, scope.instrument, scope.session);
        let index = self
            .entries
            .binary_search_by_key(&identity, |entry| {
                key(entry.provider, entry.instrument, entry.session)
            })
            .map_err(|_| Error::Unready("run seed shard missing".into()))?;
        let entry = &self.entries[index];
        if entry.seed_manifest_hash != content_hash(&seed.manifest)?
            || seed.manifest.instrument != scope.instrument
            || seed.manifest.session >= scope.session
            || seed.manifest.available_at_ns > start_ns
            || seed.manifest.producer != SeedProducer::HistoricalV7
        {
            return Err(Error::Conflict("run historical seed shard differs".into()));
        }
        Ok(())
    }
}
