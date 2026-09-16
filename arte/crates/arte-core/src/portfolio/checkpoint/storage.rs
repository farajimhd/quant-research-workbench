//! Content-addressed portfolio object graph. Root publication follows every
//! chunk readback; a root alone does not prove a coordinated run checkpoint.
use super::*;
pub const CHUNK_BYTES: usize = 1024 * 1024;
pub const MAX_ROOT_BYTES: usize = 16 * 1024;
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Root {
    pub schema_version: u32,
    pub manifest_hash: String,
    pub cut: Cut,
    pub checkpoint_hash: String,
    pub total_bytes: usize,
    pub chunks: Vec<String>,
}
impl Root {
    pub fn decode(object: &Object) -> Result<Self> {
        if object.payload.len() > MAX_ROOT_BYTES {
            return Err(Error::Capacity("portfolio root byte budget".into()));
        }
        object.verify()?;
        let root: Self = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.schema_version != 1
            || !hash_valid(&root.manifest_hash)
            || !hash_valid(&root.checkpoint_hash)
            || root.cut.boundary_sequence == 0
            || !hash_valid(&root.cut.boundary_hash)
            || root.total_bytes == 0
            || root.total_bytes > 64 * CHUNK_BYTES
            || root.chunks.len() != root.total_bytes.div_ceil(CHUNK_BYTES)
            || root.chunks.iter().any(|id| !hash_valid(id))
            || encode(&root, MAX_ROOT_BYTES)? != object.payload
        {
            return Err(Error::Invalid(
                "invalid or noncanonical portfolio storage root".into(),
            ));
        }
        Ok(root)
    }
}
pub struct Bundle {
    pub root: Object,
    pub chunks: BTreeMap<String, Object>,
}
impl Bundle {
    pub fn from_portfolio(
        portfolio: &Portfolio,
        run: &Pinned,
        cut: &Cut,
        limits: &Limits,
    ) -> Result<Self> {
        let object = portfolio.checkpoint(run, cut, limits)?;
        let mut chunks = BTreeMap::new();
        let mut ids = Vec::new();
        for bytes in object.payload.chunks(CHUNK_BYTES) {
            let chunk = Object::new(bytes.to_vec());
            ids.push(chunk.id.clone());
            chunks.insert(chunk.id.clone(), chunk);
        }
        let root = Root {
            schema_version: 1,
            manifest_hash: run.hash().into(),
            cut: cut.clone(),
            checkpoint_hash: object.id,
            total_bytes: object.payload.len(),
            chunks: ids,
        };
        Ok(Self {
            root: Object::new(encode(&root, MAX_ROOT_BYTES)?),
            chunks,
        })
    }
    pub fn hydrate(
        &self,
        run: &Pinned,
        cut: &Cut,
        expected_checkpoint_hash: &str,
        limits: &Limits,
    ) -> Result<Portfolio> {
        let root = Root::decode(&self.root)?;
        if root.manifest_hash != run.hash()
            || &root.cut != cut
            || root.checkpoint_hash != expected_checkpoint_hash
            || root.total_bytes > limits.maximum_bytes
        {
            return Err(Error::Conflict(
                "portfolio storage identity or byte budget differs".into(),
            ));
        }
        if root.chunks.iter().collect::<BTreeSet<_>>()
            != self.chunks.keys().collect::<BTreeSet<_>>()
        {
            return Err(Error::Unready(
                "portfolio checkpoint chunk set differs".into(),
            ));
        }
        let mut payload = Vec::with_capacity(root.total_bytes);
        for (index, id) in root.chunks.iter().enumerate() {
            let chunk = &self.chunks[id];
            if chunk.id != *id
                || chunk.payload.len() != (root.total_bytes - index * CHUNK_BYTES).min(CHUNK_BYTES)
            {
                return Err(Error::Conflict(
                    "portfolio chunk identity or size differs".into(),
                ));
            }
            chunk.verify()?;
            payload.extend_from_slice(&chunk.payload);
        }
        Portfolio::restore_checkpoint(
            run,
            cut,
            &Object {
                id: root.checkpoint_hash,
                payload,
            },
            expected_checkpoint_hash,
            limits,
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn chunks_preserve_unicode_and_require_the_complete_verified_object_set() {
        let (portfolio, run, cut, mut limits, _) = super::super::tests::fixture();
        limits.maximum_reservations = 20001;
        limits.maximum_bytes = 8 * CHUNK_BYTES;
        {
            let mut state = portfolio.accounts.get("a").unwrap().lock().unwrap();
            for index in 0..20000 {
                let command = format!("{}-{index}", "é".repeat(30));
                state.reservations.insert(
                    command.clone(),
                    Reservation {
                        command_id: command,
                        instrument: 1,
                        cash_minor: 1,
                    },
                );
            }
        }
        let mut bundle = Bundle::from_portfolio(&portfolio, &run, &cut, &limits).unwrap();
        let root = Root::decode(&bundle.root).unwrap();
        assert!(bundle.chunks.len() > 1);
        let restored = bundle
            .hydrate(&run, &cut, &root.checkpoint_hash, &limits)
            .unwrap();
        assert_eq!(restored.snapshot("a").unwrap().reservations.len(), 20001);
        let key = bundle.chunks.keys().next().unwrap().clone();
        let chunk = bundle.chunks.remove(&key).unwrap();
        assert!(bundle
            .hydrate(&run, &cut, &root.checkpoint_hash, &limits)
            .is_err());
        bundle.chunks.insert(key.clone(), chunk);
        bundle.chunks.get_mut(&key).unwrap().payload[0] ^= 1;
        assert!(bundle
            .hydrate(&run, &cut, &root.checkpoint_hash, &limits)
            .is_err());
    }
    #[test]
    fn root_identity_limits_and_canonical_encoding_are_required() {
        let (portfolio, run, cut, limits, _) = super::super::tests::fixture();
        let mut bundle = Bundle::from_portfolio(&portfolio, &run, &cut, &limits).unwrap();
        let root = Root::decode(&bundle.root).unwrap();
        assert!(bundle
            .hydrate(&run, &cut, &"f".repeat(64), &limits)
            .is_err());
        let extra = Object::new(vec![1]);
        bundle.chunks.insert(extra.id.clone(), extra);
        assert!(bundle
            .hydrate(&run, &cut, &root.checkpoint_hash, &limits)
            .is_err());
        let mut payload = bundle.root.payload;
        payload.push(b' ');
        assert!(Root::decode(&Object::new(payload)).is_err());
        assert!(Root::decode(&Object::new(vec![0; MAX_ROOT_BYTES + 1])).is_err());
    }
}
