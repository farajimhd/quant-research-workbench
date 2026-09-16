//! Startup-only immutable run publication. Not a distributed ownership fence.
use super::ClickHouse;
use arte_core::{
    config::Acceptance,
    run_manifest::{
        storage::{Bundle, Root, CHUNK_BYTES, MAX_ROOT_BYTES},
        Manifest, Pinned,
    },
    seed_storage::Object,
    Error, Result,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
};
const CHUNKS: &str = "run_manifest_chunks_v1";
const ROOTS: &str = "run_manifests_v1";

/// All cooperating writers must lock this stable run slot, not a content hash.
pub fn run_manifest_scope(run_id: &str) -> Result<String> {
    if run_id.is_empty()
        || run_id.len() > 128
        || !run_id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
    {
        return Err(Error::Invalid("invalid run manifest run ID".into()));
    }
    arte_core::content_hash(&("arte.run-manifest-slot.v1", run_id))
}
trait Store {
    fn read(&self, roots: bool, key: &str) -> impl Future<Output = Result<Option<Vec<u8>>>>;
    fn write(&self, roots: bool, key: &str, payload: &[u8]) -> impl Future<Output = Result<()>>;
}
impl Store for ClickHouse {
    async fn read(&self, roots: bool, key: &str) -> Result<Option<Vec<u8>>> {
        let table = if roots { ROOTS } else { CHUNKS };
        self.verify_storage(table).await?;
        let value = self
            .immutable_value(table, "record_hash", key, "payload_json")
            .await?;
        let bound = if roots { MAX_ROOT_BYTES } else { CHUNK_BYTES };
        if value.as_ref().is_some_and(|v| v.len() > bound) {
            return Err(Error::Capacity("run manifest storage payload bound".into()));
        }
        Ok(value.map(String::into_bytes))
    }
    async fn write(&self, roots: bool, key: &str, payload: &[u8]) -> Result<()> {
        let payload = std::str::from_utf8(payload)
            .map_err(|_| Error::Invalid("run manifest chunk is not UTF-8".into()))?;
        self.insert(
            if roots { ROOTS } else { CHUNKS },
            &[serde_json::json!({"record_hash":key,"payload_json":payload})],
        )
        .await
    }
}
async fn load(store: &impl Store, run_id: &str, hash: &str) -> Result<Pinned> {
    let slot = run_manifest_scope(run_id)?;
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Invalid("invalid expected run manifest hash".into()));
    }
    let payload = store
        .read(true, &slot)
        .await?
        .ok_or_else(|| Error::Unready("run manifest is not published".into()))?;
    let root = Object::new(payload);
    let header = Root::decode(&root)?;
    if header.run_id != run_id || header.manifest_hash != hash {
        return Err(Error::Conflict(
            "published run manifest identity differs".into(),
        ));
    }
    let mut chunks = BTreeMap::new();
    for id in &header.chunks {
        if chunks.contains_key(id) {
            continue;
        }
        let payload = store
            .read(false, id)
            .await?
            .ok_or_else(|| Error::Unready("published run manifest chunk is missing".into()))?;
        if payload.len() > CHUNK_BYTES {
            return Err(Error::Capacity("run manifest chunk exceeds bound".into()));
        }
        let object = Object {
            id: id.clone(),
            payload,
        };
        object.verify()?;
        chunks.insert(id.clone(), object);
    }
    Bundle { root, chunks }.hydrate(run_id, hash)
}
async fn put_verified(
    store: &impl Store,
    roots: bool,
    key: &str,
    payload: &[u8],
    owned: &impl Fn() -> Result<()>,
) -> Result<()> {
    owned()?;
    match store.read(roots, key).await? {
        Some(existing) if existing != payload => {
            return Err(Error::Conflict("immutable run storage slot differs".into()))
        }
        Some(_) => {}
        None => {
            owned()?;
            store.write(roots, key, payload).await?;
        }
    }
    owned()?;
    if store.read(roots, key).await?.as_deref() != Some(payload) {
        return Err(Error::Unready(
            "run storage readback missing or different".into(),
        ));
    }
    Ok(())
}
async fn publish(
    store: &impl Store,
    manifest: &Manifest,
    owned: &impl Fn() -> Result<()>,
) -> Result<Pinned> {
    let bundle = Bundle::from_manifest(manifest)?;
    let hash = manifest.hash()?;
    let slot = run_manifest_scope(&manifest.run_id)?;
    owned()?;
    if let Some(existing) = store.read(true, &slot).await? {
        if existing != bundle.root.payload {
            return Err(Error::Conflict(
                "run ID already pins a different manifest".into(),
            ));
        }
    }
    for object in bundle.chunks.values() {
        put_verified(store, false, &object.id, &object.payload, owned).await?;
    }
    // The publication point is deliberately after every child readback.
    put_verified(store, true, &slot, &bundle.root.payload, owned).await?;
    let pinned = load(store, &manifest.run_id, &hash).await?;
    owned()?;
    Ok(pinned)
}
impl ClickHouse {
    pub async fn load_run_manifest(&self, run_id: &str, hash: &str) -> Result<Pinned> {
        load(self, run_id, hash).await
    }
    /// Explicit acceptance and cooperative single-host ownership are required.
    /// Referenced input certification and power-loss durability are separate.
    pub async fn publish_run_manifest(
        &self,
        manifest: &Manifest,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Pinned> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "run publication acceptance missing: {required:?}"
                )));
            }
        }
        let slot = run_manifest_scope(&manifest.run_id)?;
        publish(self, manifest, &|| lease.require(&slot)).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::RefCell;
    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<(bool, String), Vec<u8>>>,
        fail_chunk: RefCell<bool>,
        ambiguous_root: RefCell<bool>,
        writes: RefCell<Vec<bool>>,
    }
    impl Store for Memory {
        async fn read(&self, roots: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(roots, key.into())).cloned())
        }
        async fn write(&self, roots: bool, key: &str, payload: &[u8]) -> Result<()> {
            if !roots && *self.fail_chunk.borrow() {
                return Err(Error::Unready("injected write failure".into()));
            }
            self.writes.borrow_mut().push(roots);
            self.rows
                .borrow_mut()
                .insert((roots, key.into()), payload.to_vec());
            if roots && *self.ambiguous_root.borrow() {
                return Err(Error::Unready("injected lost root acknowledgment".into()));
            }
            Ok(())
        }
    }
    fn manifest() -> Manifest {
        use arte_core::{
            run_manifest::{Clock, Consumer, Execution},
            strategy_dispatch::Mode,
        };
        Manifest {
            schema_version: 1,
            run_id: "run-1".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: "b".repeat(64),
            reference_manifest_hash: "c".repeat(64),
            seed_manifest_hash: "d".repeat(64),
            algorithm_manifest_hash: "e".repeat(64),
            dependency_plan_hash: "f".repeat(64),
            hardware_profile_hash: "1".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "2".repeat(64),
                cost_model_hash: "3".repeat(64),
            },
            consumers: vec![Consumer {
                account: "account".into(),
                instrument: 1,
                strategy_instance: "strategy".into(),
                effective_config_hash: "4".repeat(64),
            }],
        }
    }
    #[tokio::test]
    async fn publication_is_children_first_retryable_and_run_slot_is_immutable() {
        let store = Memory::default();
        let mut m = manifest();
        *store.fail_chunk.borrow_mut() = true;
        assert!(publish(&store, &m, &|| Ok(())).await.is_err());
        assert!(store.rows.borrow().keys().all(|(root, _)| !root));
        *store.fail_chunk.borrow_mut() = false;
        let pinned = publish(&store, &m, &|| Ok(())).await.unwrap();
        assert_eq!(pinned.hash(), m.hash().unwrap());
        assert_eq!(*store.writes.borrow(), vec![false, true]);
        publish(&store, &m, &|| Ok(())).await.unwrap();
        assert_eq!(store.writes.borrow().len(), 2);
        m.seed_manifest_hash = "9".repeat(64);
        assert!(publish(&store, &m, &|| Ok(())).await.is_err());
        assert_eq!(store.writes.borrow().len(), 2);
    }
    #[tokio::test]
    async fn ambiguous_root_acknowledgment_retries_without_rewriting() {
        let store = Memory::default();
        let m = manifest();
        *store.ambiguous_root.borrow_mut() = true;
        assert!(publish(&store, &m, &|| Ok(())).await.is_err());
        assert_eq!(store.writes.borrow().len(), 2);
        assert_eq!(
            load(&store, &m.run_id, &m.hash().unwrap())
                .await
                .unwrap()
                .hash(),
            m.hash().unwrap()
        );
        publish(&store, &m, &|| Ok(())).await.unwrap();
        assert_eq!(store.writes.borrow().len(), 2);
    }
    #[tokio::test]
    async fn missing_corrupt_children_and_lost_ownership_fail_closed() {
        let store = Memory::default();
        let m = manifest();
        assert!(publish(&store, &m, &|| Err(Error::Unready("lost".into())))
            .await
            .is_err());
        assert!(store.writes.borrow().is_empty());
        publish(&store, &m, &|| Ok(())).await.unwrap();
        let key = store
            .rows
            .borrow()
            .keys()
            .find(|(root, _)| !root)
            .unwrap()
            .clone();
        store.rows.borrow_mut().get_mut(&key).unwrap()[0] ^= 1;
        assert!(load(&store, &m.run_id, &m.hash().unwrap()).await.is_err());
        store.rows.borrow_mut().remove(&key);
        assert!(load(&store, &m.run_id, &m.hash().unwrap()).await.is_err());
        assert!(run_manifest_scope("' OR true").is_err());
        assert_ne!(
            run_manifest_scope("run-1").unwrap(),
            run_manifest_scope("run-2").unwrap()
        );
    }
}
