use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub enum SeedProducer {
    HistoricalV7,
    StreamingRecovery,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SeedManifest {
    pub id: String,
    pub instrument: u64,
    pub session: u32,
    pub session_end_ns: u64,
    pub available_at_ns: u64,
    pub producer: SeedProducer,
    pub algorithm: String,
    pub source_generation: String,
    pub previous_seed: Option<String>,
    pub objects: BTreeSet<String>,
    pub root_object: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct LevelVersion {
    pub level_id: String,
    pub valid_from_ns: u64,
    pub valid_to_ns: Option<u64>,
    pub available_at_ns: u64,
    pub generation: String,
    pub payload_hash: String,
}
impl LevelVersion {
    pub fn visible(&self, at: u64, generation: &str) -> bool {
        self.generation == generation
            && self.available_at_ns <= at
            && self.valid_from_ns <= at
            && self.valid_to_ns.is_none_or(|end| at < end)
    }
}
#[derive(Debug, Default)]
pub struct SeedCatalog {
    objects: BTreeMap<String, Vec<u8>>,
    seeds: BTreeMap<String, SeedManifest>,
}
impl SeedCatalog {
    pub fn add_object(&mut self, value: Vec<u8>) -> Result<String> {
        let id = format!("{:x}", Sha256::digest(&value));
        self.objects.entry(id.clone()).or_insert(value);
        Ok(id)
    }
    pub fn object(&self, id: &str) -> Result<&[u8]> {
        let bytes = self
            .objects
            .get(id)
            .ok_or_else(|| Error::Unready("seed object missing".into()))?;
        if format!("{:x}", Sha256::digest(bytes)) != id {
            return Err(Error::Invalid("seed object hash mismatch".into()));
        }
        Ok(bytes)
    }
    pub fn publish(&mut self, seed: SeedManifest, now: u64) -> Result<()> {
        if seed.producer != SeedProducer::HistoricalV7 {
            return Err(Error::Invalid(
                "streaming output is not daily seed authority".into(),
            ));
        }
        if seed.id.is_empty()
            || seed.instrument == 0
            || seed.algorithm.is_empty()
            || seed.source_generation.is_empty()
            || seed.available_at_ns < seed.session_end_ns
            || now < seed.available_at_ns
        {
            return Err(Error::Invalid("invalid seed publication boundary".into()));
        }
        if !seed.objects.contains(&seed.root_object)
            || seed.objects.iter().any(|id| !self.objects.contains_key(id))
        {
            return Err(Error::Unready("seed objects incomplete".into()));
        }
        if let Some(prior) = &seed.previous_seed {
            let p = self
                .seeds
                .get(prior)
                .ok_or_else(|| Error::Unready("predecessor seed missing".into()))?;
            if p.instrument != seed.instrument || p.session >= seed.session {
                return Err(Error::Invalid("seed lineage mismatch".into()));
            }
        }
        if let Some(old) = self.seeds.get(&seed.id) {
            if content_hash(old)? != content_hash(&seed)? {
                return Err(Error::Conflict("seed identity".into()));
            }
        }
        self.seeds.insert(seed.id.clone(), seed);
        Ok(())
    }
    pub fn load(
        &self,
        id: &str,
        instrument: u64,
        session: u32,
        start: u64,
    ) -> Result<&SeedManifest> {
        let s = self
            .seeds
            .get(id)
            .ok_or_else(|| Error::Unready("seed not published".into()))?;
        if s.instrument != instrument || s.session >= session || s.available_at_ns > start {
            return Err(Error::Unready("seed not causally available".into()));
        }
        Ok(s)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn future_and_streaming_seeds_rejected() {
        let mut c = SeedCatalog::default();
        let root = c.add_object(b"test seed root".to_vec()).unwrap();
        let mut s = SeedManifest {
            id: "seed".into(),
            instrument: 1,
            session: 20260914,
            session_end_ns: 100,
            available_at_ns: 110,
            producer: SeedProducer::StreamingRecovery,
            algorithm: "v7".into(),
            source_generation: "g".into(),
            previous_seed: None,
            objects: BTreeSet::from([root.clone()]),
            root_object: root,
        };
        assert!(c.publish(s.clone(), 110).is_err());
        s.producer = SeedProducer::HistoricalV7;
        assert!(c.publish(s.clone(), 100).is_err());
        c.publish(s, 110).unwrap();
        assert!(c.load("seed", 1, 20260915, 109).is_err());
        assert!(c.load("seed", 1, 20260915, 110).is_ok());
    }
}
