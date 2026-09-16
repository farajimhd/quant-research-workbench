//! Bounded, content-addressed manifest storage. Write and verify all chunks before
//! publishing the root in an immutable run-ID slot. This codec is not a durable
//! receipt; storage adapters must enforce that publication protocol.
use super::{hash_valid, name_valid, Manifest, Pinned};
use crate::{seed_storage::Object, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};

pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_BYTES: usize = 64 * CHUNK_BYTES;
pub const MAX_ROOT_BYTES: usize = 16 * 1024;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Root {
    pub schema_version: u32,
    pub run_id: String,
    pub manifest_hash: String,
    pub total_bytes: usize,
    /// Ordered chunk identities. Repeated content is stored only once.
    pub chunks: Vec<String>,
}
impl Root {
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 1
            || !name_valid(&self.run_id)
            || !hash_valid(&self.manifest_hash)
            || self.total_bytes == 0
            || self.total_bytes > MAX_BYTES
            || self.chunks.len() != self.total_bytes.div_ceil(CHUNK_BYTES)
            || self.chunks.iter().any(|hash| !hash_valid(hash))
        {
            return Err(Error::Invalid("invalid run manifest storage root".into()));
        }
        Ok(())
    }
    pub fn decode(object: &Object) -> Result<Self> {
        if object.payload.len() > MAX_ROOT_BYTES {
            return Err(Error::Invalid("run manifest root exceeds bound".into()));
        }
        object.verify()?;
        let root: Self = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        root.validate()?;
        if encode(&root)? != object.payload {
            return Err(Error::Invalid("noncanonical run manifest root".into()));
        }
        Ok(root)
    }
}

pub struct Bundle {
    pub root: Object,
    pub chunks: BTreeMap<String, Object>,
}
fn encode<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|e| Error::Serialization(e.to_string()))
}
impl Bundle {
    pub fn from_manifest(manifest: &Manifest) -> Result<Self> {
        let hash = manifest.hash()?;
        let payload = encode(manifest)?;
        if payload.len() > MAX_BYTES {
            return Err(Error::Invalid("run manifest exceeds storage bound".into()));
        }
        let mut chunks = BTreeMap::new();
        let mut ids = Vec::new();
        for bytes in payload.chunks(CHUNK_BYTES) {
            let object = Object::new(bytes.to_vec());
            ids.push(object.id.clone());
            chunks.insert(object.id.clone(), object);
        }
        let root = Root {
            schema_version: 1,
            run_id: manifest.run_id.clone(),
            manifest_hash: hash,
            total_bytes: payload.len(),
            chunks: ids,
        };
        root.validate()?;
        Ok(Self {
            root: Object::new(encode(&root)?),
            chunks,
        })
    }

    /// Require the caller's expected identity, not merely the identity claimed
    /// by untrusted storage. Partial or surplus object sets are rejected.
    pub fn hydrate(&self, run_id: &str, manifest_hash: &str) -> Result<Pinned> {
        let root = Root::decode(&self.root)?;
        if root.run_id != run_id || root.manifest_hash != manifest_hash {
            return Err(Error::Conflict(
                "run manifest storage identity differs".into(),
            ));
        }
        if root.chunks.iter().collect::<BTreeSet<_>>()
            != self.chunks.keys().collect::<BTreeSet<_>>()
        {
            return Err(Error::Unready("run manifest chunk set differs".into()));
        }
        let mut payload = Vec::with_capacity(root.total_bytes);
        for (index, id) in root.chunks.iter().enumerate() {
            let object = &self.chunks[id];
            let expected_len = (root.total_bytes - index * CHUNK_BYTES).min(CHUNK_BYTES);
            if object.id != *id || object.payload.len() != expected_len {
                return Err(Error::Invalid(
                    "run manifest chunk identity or size differs".into(),
                ));
            }
            object.verify()?;
            payload.extend_from_slice(&object.payload);
        }
        let manifest: Manifest =
            serde_json::from_slice(&payload).map_err(|e| Error::Serialization(e.to_string()))?;
        if manifest.run_id != root.run_id || encode(&manifest)? != payload {
            return Err(Error::Invalid("noncanonical run manifest payload".into()));
        }
        Pinned::new(manifest, manifest_hash)
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::manifest;
    use super::*;

    #[test]
    fn complete_bundle_required_and_expected_identity_enforced() {
        let m = manifest();
        let hash = m.hash().unwrap();
        let mut bundle = Bundle::from_manifest(&m).unwrap();
        assert_eq!(bundle.hydrate(&m.run_id, &hash).unwrap().hash(), hash);
        assert!(bundle.hydrate("other", &hash).is_err());
        assert!(bundle.hydrate(&m.run_id, &"0".repeat(64)).is_err());
        let id = bundle.chunks.keys().next().unwrap().clone();
        let object = bundle.chunks.remove(&id).unwrap();
        assert!(bundle.hydrate(&m.run_id, &hash).is_err());
        bundle.chunks.insert(id.clone(), object);
        bundle.chunks.get_mut(&id).unwrap().payload[0] ^= 1;
        assert!(bundle.hydrate(&m.run_id, &hash).is_err());
        let mut bundle = Bundle::from_manifest(&m).unwrap();
        let extra = Object::new(vec![1]);
        bundle.chunks.insert(extra.id.clone(), extra);
        assert!(bundle.hydrate(&m.run_id, &hash).is_err());
    }

    #[test]
    fn root_bounds_and_canonical_encoding_enforced() {
        let bundle = Bundle::from_manifest(&manifest()).unwrap();
        let mut root = Root::decode(&bundle.root).unwrap();
        root.total_bytes = MAX_BYTES + 1;
        assert!(Root::decode(&Object::new(encode(&root).unwrap())).is_err());
        let mut spaced = bundle.root.payload.clone();
        spaced.push(b' ');
        assert!(Root::decode(&Object::new(spaced)).is_err());
        assert!(Root::decode(&Object::new(vec![b' '; MAX_ROOT_BYTES + 1])).is_err());
    }

    #[test]
    fn maximum_consumer_count_is_chunked_and_roundtrips() {
        let mut m = manifest();
        let mut consumer = m.consumers[0].clone();
        consumer.account = "a".repeat(128);
        consumer.strategy_instance = "s".repeat(128);
        m.consumers = (1..=100_000)
            .map(|instrument| {
                let mut row = consumer.clone();
                row.instrument = instrument;
                row
            })
            .collect();
        let hash = m.hash().unwrap();
        let bundle = Bundle::from_manifest(&m).unwrap();
        assert!(bundle.chunks.len() > 32);
        assert!(bundle
            .chunks
            .values()
            .all(|o| o.payload.len() <= CHUNK_BYTES));
        let restored = bundle.hydrate(&m.run_id, &hash).unwrap();
        assert_eq!(restored.manifest().consumers.len(), 100_000);
        assert_eq!(
            restored
                .scope(&consumer.account, 100_000, &consumer.strategy_instance)
                .unwrap()
                .config_hash,
            consumer.effective_config_hash
        );
    }
}
