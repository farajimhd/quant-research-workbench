//! Immutable historical seed object graph. Storage acknowledgments and manifest
//! visibility are separate steps; a partially written graph cannot be loaded.
use crate::publication::{SeedCatalog, SeedManifest, SeedProducer};
use crate::v7_seed::{HistoricalSeed, SeedLevel, SourceCertificate};
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
const ENVELOPE_VERSION: &str = "arte-seed-objects-1";
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Header {
    envelope_version: String,
    algorithm: String,
    source: SourceCertificate,
    previous_seed: Option<String>,
    available_at_second: u64,
    configuration_hash: String,
    split_evidence: Vec<String>,
    split_factor: f64,
    seed_hash: String,
    level_objects: Vec<String>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Object {
    pub id: String,
    pub payload: Vec<u8>,
}
impl Object {
    pub fn new(payload: Vec<u8>) -> Self {
        Self {
            id: format!("{:x}", Sha256::digest(&payload)),
            payload,
        }
    }
    pub fn verify(&self) -> Result<()> {
        if self.id != format!("{:x}", Sha256::digest(&self.payload)) {
            return Err(Error::Invalid("immutable object hash mismatch".into()));
        }
        Ok(())
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Bundle {
    pub manifest: SeedManifest,
    pub objects: BTreeMap<String, Object>,
}
fn encode<T: Serialize>(value: &T) -> Result<Object> {
    Ok(Object::new(
        serde_json::to_vec(value).map_err(|e| Error::Serialization(e.to_string()))?,
    ))
}
fn nanos(seconds: u64) -> Result<u64> {
    seconds
        .checked_mul(1_000_000_000)
        .ok_or_else(|| Error::Invalid("seed timestamp overflow".into()))
}
impl Bundle {
    pub fn from_seed(seed: &HistoricalSeed) -> Result<Self> {
        seed.verify()?;
        let mut objects = BTreeMap::new();
        let mut level_objects = Vec::new();
        for level in &seed.levels {
            let object = encode(level)?;
            level_objects.push(object.id.clone());
            objects.insert(object.id.clone(), object);
        }
        let header = Header {
            envelope_version: ENVELOPE_VERSION.into(),
            algorithm: seed.version.clone(),
            source: seed.source.clone(),
            previous_seed: seed.previous_seed.clone(),
            available_at_second: seed.available_at_second,
            configuration_hash: seed.configuration_hash.clone(),
            split_evidence: seed.split_evidence.clone(),
            split_factor: seed.split_factor,
            seed_hash: seed.hash.clone(),
            level_objects,
        };
        let root = encode(&header)?;
        let root_object = root.id.clone();
        objects.insert(root.id.clone(), root);
        let manifest = SeedManifest {
            id: seed.hash.clone(),
            instrument: seed.source.instrument,
            session: seed.source.session,
            session_end_ns: nanos(seed.source.end_second)?,
            available_at_ns: nanos(seed.available_at_second)?,
            producer: SeedProducer::HistoricalV7,
            algorithm: seed.version.clone(),
            source_generation: seed.source.source_generation.clone(),
            previous_seed: seed.previous_seed.clone(),
            objects: objects.keys().cloned().collect(),
            root_object,
        };
        Ok(Self { manifest, objects })
    }
    pub fn hydrate(&self) -> Result<HistoricalSeed> {
        if self.objects.keys().cloned().collect::<BTreeSet<_>>() != self.manifest.objects {
            return Err(Error::Unready(
                "seed object set incomplete or unexpected".into(),
            ));
        }
        for (key, object) in &self.objects {
            object.verify()?;
            if key != &object.id {
                return Err(Error::Invalid(
                    "object key differs from payload identity".into(),
                ));
            }
        }
        let root = self
            .objects
            .get(&self.manifest.root_object)
            .ok_or_else(|| Error::Unready("seed root missing".into()))?;
        let header: Header = serde_json::from_slice(&root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if header.envelope_version != ENVELOPE_VERSION {
            return Err(Error::Invalid("seed envelope version mismatch".into()));
        }
        let mut expected = BTreeSet::from([self.manifest.root_object.clone()]);
        let mut levels = Vec::new();
        for id in &header.level_objects {
            if !expected.insert(id.clone()) {
                return Err(Error::Invalid("duplicate level object reference".into()));
            }
            let object = self
                .objects
                .get(id)
                .ok_or_else(|| Error::Unready("level object missing".into()))?;
            let level: SeedLevel = serde_json::from_slice(&object.payload)
                .map_err(|e| Error::Serialization(e.to_string()))?;
            levels.push(level);
        }
        if expected != self.manifest.objects {
            return Err(Error::Invalid(
                "manifest contains unreferenced objects".into(),
            ));
        }
        let seed = HistoricalSeed {
            version: header.algorithm,
            source: header.source,
            previous_seed: header.previous_seed,
            available_at_second: header.available_at_second,
            configuration_hash: header.configuration_hash,
            levels,
            split_evidence: header.split_evidence,
            split_factor: header.split_factor,
            hash: header.seed_hash,
        };
        seed.verify()?;
        let canonical = Self::from_seed(&seed)?;
        if content_hash(&canonical.manifest)? != content_hash(&self.manifest)? {
            return Err(Error::Invalid(
                "manifest does not describe its historical seed".into(),
            ));
        }
        Ok(seed)
    }
    /// In-memory reference implementation of the database write/readback/commit protocol.
    pub fn publish(&self, catalog: &mut SeedCatalog, now_ns: u64) -> Result<()> {
        self.hydrate()?;
        for object in self.objects.values() {
            let id = catalog.add_object(object.payload.clone())?;
            if id != object.id || catalog.object(&id)? != object.payload {
                return Err(Error::Invalid("object readback mismatch".into()));
            }
        }
        catalog.publish(self.manifest.clone(), now_ns)
    }
    pub fn load(
        catalog: &SeedCatalog,
        id: &str,
        instrument: u64,
        session: u32,
        start_ns: u64,
    ) -> Result<HistoricalSeed> {
        let manifest = catalog.load(id, instrument, session, start_ns)?.clone();
        let mut objects = BTreeMap::new();
        for id in &manifest.objects {
            objects.insert(
                id.clone(),
                Object {
                    id: id.clone(),
                    payload: catalog.object(id)?.to_vec(),
                },
            );
        }
        Self { manifest, objects }.hydrate()
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::v7_extraction::Candle;
    use crate::v7_seed::{build, input_hash, SeedPolicy, SplitAdjustment};
    fn seed() -> HistoricalSeed {
        let bars: Vec<_> = (0..18)
            .map(|i| {
                let p = 10. + (i % 2) as f64;
                Candle {
                    t: 100 + i,
                    open: p,
                    close: p,
                    high: p + 0.01,
                    low: p - 0.01,
                    volume: 100.,
                }
            })
            .collect();
        let source = SourceCertificate {
            instrument: 1,
            ticker: "TEST".into(),
            session: 20260914,
            start_second: 100,
            end_second: 120,
            source_generation: "certified".into(),
            input_hash: input_hash(&bars, &[]).unwrap(),
            certified_at_second: 121,
        };
        build(
            &bars,
            &[],
            source,
            None,
            &SeedPolicy::default(),
            &SplitAdjustment::default(),
            121,
        )
        .unwrap()
    }
    #[test]
    fn immutable_roundtrip_and_publication_retry() {
        let original = seed();
        let bundle = Bundle::from_seed(&original).unwrap();
        let mut catalog = SeedCatalog::default();
        bundle.publish(&mut catalog, 121_000_000_000).unwrap();
        bundle.publish(&mut catalog, 121_000_000_000).unwrap();
        let loaded = Bundle::load(&catalog, &original.hash, 1, 20260915, 200_000_000_000).unwrap();
        assert_eq!(
            content_hash(&original).unwrap(),
            content_hash(&loaded).unwrap()
        );
    }
    #[test]
    fn partial_objects_never_become_visible() {
        let bundle = Bundle::from_seed(&seed()).unwrap();
        let mut catalog = SeedCatalog::default();
        let first = bundle.objects.values().next().unwrap();
        catalog.add_object(first.payload.clone()).unwrap();
        assert!(catalog
            .publish(bundle.manifest.clone(), 121_000_000_000)
            .is_err());
        assert!(Bundle::load(&catalog, &bundle.manifest.id, 1, 20260915, 200_000_000_000).is_err());
        bundle.publish(&mut catalog, 121_000_000_000).unwrap();
    }
    #[test]
    fn corrupt_payload_or_metadata_is_rejected() {
        let bundle = Bundle::from_seed(&seed()).unwrap();
        let mut corrupt = bundle.clone();
        corrupt.objects.values_mut().next().unwrap().payload.push(0);
        assert!(corrupt.hydrate().is_err());
        let mut corrupt = bundle;
        corrupt.manifest.instrument = 2;
        assert!(corrupt.hydrate().is_err());
    }
}
