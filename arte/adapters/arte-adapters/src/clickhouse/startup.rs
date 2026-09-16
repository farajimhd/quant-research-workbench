//! Immutable startup slot per manifest. Cooperative lease, not distributed CAS.
use super::{
    portfolio_checkpoints::{from_hex, put, to_hex, Store},
    ClickHouse,
};
use crate::playback_runtime::session::{
    document::{Document, MAXIMUM_BYTES},
    Session,
};
use arte_core::market_structure::scheduler::playback::accounts::Run;
use arte_core::{
    config::Acceptance, content_hash, run_manifest::Pinned, seed_storage::Object, Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
const CHUNK: usize = 1024 * 1024;
const ROOT_BYTES: usize = 4096;
const ROOTS: &str = "backtest_startups_v1";
const CHUNKS: &str = "backtest_startup_chunks_v1";
pub fn backtest_startup_scope(manifest: &Pinned) -> Result<String> {
    content_hash(&("arte.backtest-startup-slot.v1", manifest.hash()))
}
struct Storage<'a>(&'a ClickHouse);
impl Store for Storage<'_> {
    async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
        let table = if root { ROOTS } else { CHUNKS };
        self.0.verify_storage(table).await?;
        self.0
            .immutable_value(table, "record_hash", key, "payload_hex")
            .await?
            .map(|s| from_hex(&s, if root { ROOT_BYTES } else { CHUNK }))
            .transpose()
    }
    async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
        self.0
            .insert(
                if root { ROOTS } else { CHUNKS },
                &[serde_json::json!({"record_hash":key,"payload_hex":to_hex(bytes)})],
            )
            .await
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest: String,
    document: String,
    bytes: usize,
    chunks: Vec<String>,
}
fn encode<T: Serialize>(value: &T) -> Result<Vec<u8>> {
    serde_json::to_vec(value).map_err(|e| Error::Serialization(e.to_string()))
}
fn require_document(document: &Document, manifest: &Pinned, expected: &str) -> Result<()> {
    if document.manifest_hash != manifest.hash() || document.hash()? != expected {
        return Err(Error::Conflict("startup document identity differs".into()));
    }
    Ok(())
}
async fn load(store: &impl Store, manifest: &Pinned, expected: &str) -> Result<Document> {
    let bytes = store
        .read(true, &backtest_startup_scope(manifest)?)
        .await?
        .ok_or_else(|| Error::Unready("startup root missing".into()))?;
    if bytes.len() > ROOT_BYTES {
        return Err(Error::Capacity("startup root bytes".into()));
    }
    let root: Root =
        serde_json::from_slice(&bytes).map_err(|e| Error::Serialization(e.to_string()))?;
    if root.version != 1
        || root.manifest != manifest.hash()
        || root.document != expected
        || root.bytes == 0
        || root.bytes > MAXIMUM_BYTES
        || root.chunks.len() != root.bytes.div_ceil(CHUNK)
        || encode(&root)? != bytes
    {
        return Err(Error::Conflict("startup root identity or shape".into()));
    }
    let mut payload = Vec::with_capacity(root.bytes);
    for (index, id) in root.chunks.iter().enumerate() {
        if id.len() != 64
            || !id
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("startup chunk identity".into()));
        }
        let bytes = store
            .read(false, id)
            .await?
            .ok_or_else(|| Error::Unready("startup chunk missing".into()))?;
        if bytes.len() != (root.bytes - index * CHUNK).min(CHUNK) {
            return Err(Error::Conflict("startup chunk length differs".into()));
        }
        Object {
            id: id.clone(),
            payload: bytes.clone(),
        }
        .verify()?;
        payload.extend(bytes);
    }
    let document = Document::decode(&payload, expected)?;
    require_document(&document, manifest, expected)?;
    if encode(&document)? != payload {
        return Err(Error::Conflict("noncanonical startup document".into()));
    }
    Ok(document)
}
async fn publish(
    store: &impl Store,
    manifest: &Pinned,
    document: &Document,
    expected: &str,
    owned: &impl Fn() -> Result<()>,
) -> Result<Document> {
    owned()?;
    require_document(document, manifest, expected)?;
    let payload = encode(document)?;
    let chunks: Vec<_> = payload
        .chunks(CHUNK)
        .map(|b| Object::new(b.to_vec()))
        .collect();
    let root = encode(&Root {
        version: 1,
        manifest: manifest.hash().into(),
        document: expected.into(),
        bytes: payload.len(),
        chunks: chunks.iter().map(|c| c.id.clone()).collect(),
    })?;
    if root.len() > ROOT_BYTES {
        return Err(Error::Capacity("startup root bytes".into()));
    }
    let slot = backtest_startup_scope(manifest)?;
    if store.read(true, &slot).await?.is_some_and(|v| v != root) {
        return Err(Error::Conflict(
            "manifest already has different startup inputs".into(),
        ));
    }
    for chunk in chunks {
        put(store, false, &chunk.id, &chunk.payload, owned).await?;
    }
    put(store, true, &slot, &root, owned).await?;
    let verified = load(store, manifest, expected).await?;
    owned()?;
    Ok(verified)
}
impl ClickHouse {
    /// Fresh startup only. A failed/cancelled call returns no usable session.
    /// Rebuild the unused prepared run and retry the same document identity.
    pub async fn create_backtest_session(
        &self,
        run: Run,
        manifest: &Pinned,
        document: &Document,
        expected: &str,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Session> {
        require_acceptance(passed)?;
        let slot = backtest_startup_scope(manifest)?;
        create(&Storage(self), run, manifest, document, expected, &|| {
            lease.require(&slot)
        })
        .await
    }
    pub async fn load_backtest_startup(
        &self,
        manifest: &Pinned,
        expected: &str,
    ) -> Result<Document> {
        load(&Storage(self), manifest, expected).await
    }
    /// Publication proves exact readback, not power-loss durability or strategy acceptance.
    pub async fn publish_backtest_startup(
        &self,
        manifest: &Pinned,
        document: &Document,
        expected: &str,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Document> {
        require_acceptance(passed)?;
        let slot = backtest_startup_scope(manifest)?;
        publish(&Storage(self), manifest, document, expected, &|| {
            lease.require(&slot)
        })
        .await
    }
}
fn require_acceptance(passed: &BTreeSet<Acceptance>) -> Result<()> {
    for gate in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
        if !passed.contains(&gate) {
            return Err(Error::Unready(format!(
                "startup acceptance missing: {gate:?}"
            )));
        }
    }
    Ok(())
}
async fn create(
    store: &impl Store,
    run: Run,
    manifest: &Pinned,
    document: &Document,
    expected: &str,
    owned: &impl Fn() -> Result<()>,
) -> Result<Session> {
    owned()?;
    // Semantic assembly must succeed before the first storage write. The new
    // session stays local and paused until publication/readback is complete.
    let session = Session::from_document(run, manifest, document.clone(), expected)?;
    publish(store, manifest, document, expected, owned).await?;
    owned()?;
    Ok(session)
}

#[cfg(test)]
pub(crate) async fn create_test(
    manifest: &Pinned,
    document: &Document,
    make_run: impl Fn() -> Run,
) -> Session {
    use std::{
        cell::{Cell, RefCell},
        collections::BTreeMap,
    };
    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<(bool, String), Vec<u8>>>,
        ambiguous: Cell<bool>,
        writes: Cell<usize>,
    }
    impl Store for Memory {
        async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(root, key.into())).cloned())
        }
        async fn write(&self, root: bool, key: &str, payload: &[u8]) -> Result<()> {
            self.rows
                .borrow_mut()
                .insert((root, key.into()), payload.into());
            self.writes.set(self.writes.get() + 1);
            if root && self.ambiguous.replace(false) {
                return Err(Error::Unready("ambiguous startup".into()));
            }
            Ok(())
        }
    }
    let store = Memory::default();
    let mut invalid = document.clone();
    invalid.accounts.values_mut().next().unwrap().budget_minor = 0;
    assert!(create(
        &store,
        make_run(),
        manifest,
        &invalid,
        &invalid.hash().unwrap(),
        &|| Ok(())
    )
    .await
    .is_err());
    assert_eq!(store.writes.get(), 0);
    let hash = document.hash().unwrap();
    assert!(
        create(&store, make_run(), manifest, document, &hash, &|| Err(
            Error::Unready("lease lost".into())
        ))
        .await
        .is_err()
    );
    assert_eq!(store.writes.get(), 0);
    store.ambiguous.set(true);
    assert!(
        create(&store, make_run(), manifest, document, &hash, &|| Ok(()))
            .await
            .is_err()
    );
    let writes = store.writes.get();
    let session = create(&store, make_run(), manifest, document, &hash, &|| Ok(()))
        .await
        .unwrap();
    assert_eq!(store.writes.get(), writes);
    assert_eq!(session.startup_hash(), hash);
    session
}

#[cfg(test)]
pub(crate) async fn exercise(manifest: &Pinned, document: &Document) {
    use std::{
        cell::{Cell, RefCell},
        collections::BTreeMap,
    };
    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<(bool, String), Vec<u8>>>,
        fail_chunk: Cell<bool>,
        ambiguous_root: Cell<bool>,
        writes: Cell<usize>,
    }
    impl Store for Memory {
        async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(root, key.into())).cloned())
        }
        async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
            if !root && self.fail_chunk.get() {
                return Err(Error::Unready("chunk failure".into()));
            }
            self.writes.set(self.writes.get() + 1);
            self.rows
                .borrow_mut()
                .insert((root, key.into()), bytes.to_vec());
            if root && self.ambiguous_root.replace(false) {
                return Err(Error::Unready("ambiguous root write".into()));
            }
            Ok(())
        }
    }
    let memory = Memory::default();
    let hash = document.hash().unwrap();
    let slot = backtest_startup_scope(manifest).unwrap();
    memory.fail_chunk.set(true);
    assert!(publish(&memory, manifest, document, &hash, &|| Ok(()))
        .await
        .is_err());
    assert!(memory.read(true, &slot).await.unwrap().is_none());
    assert!(load(&memory, manifest, &hash).await.is_err());
    memory.fail_chunk.set(false);
    memory.ambiguous_root.set(true);
    assert!(publish(&memory, manifest, document, &hash, &|| Ok(()))
        .await
        .is_err());
    let writes = memory.writes.get();
    let loaded = publish(&memory, manifest, document, &hash, &|| Ok(()))
        .await
        .unwrap();
    assert_eq!(loaded.hash().unwrap(), hash);
    assert_eq!(memory.writes.get(), writes);
    let mut changed = document.clone();
    changed.accounts.values_mut().next().unwrap().budget_minor += 1;
    assert!(publish(
        &memory,
        manifest,
        &changed,
        &changed.hash().unwrap(),
        &|| Ok(())
    )
    .await
    .is_err());
    assert_eq!(memory.writes.get(), writes);
    assert!(
        publish(&memory, manifest, document, &hash, &|| Err(Error::Unready(
            "lease lost".into()
        )))
        .await
        .is_err()
    );
    assert!(load(&memory, manifest, &"f".repeat(64)).await.is_err());
    let root = memory.read(true, &slot).await.unwrap().unwrap();
    let header: Root = serde_json::from_slice(&root).unwrap();
    let key = (false, header.chunks[0].clone());
    let original = memory.rows.borrow_mut().remove(&key).unwrap();
    assert!(load(&memory, manifest, &hash).await.is_err());
    let mut corrupt = original.clone();
    corrupt[0] ^= 1;
    memory.rows.borrow_mut().insert(key.clone(), corrupt);
    assert!(load(&memory, manifest, &hash).await.is_err());
    memory.rows.borrow_mut().insert(key, original);
    let mut noncanonical = root.clone();
    noncanonical.push(b' ');
    memory
        .rows
        .borrow_mut()
        .insert((true, slot.clone()), noncanonical);
    assert!(load(&memory, manifest, &hash).await.is_err());
    memory.rows.borrow_mut().insert((true, slot), root);
    assert_eq!(
        load(&memory, manifest, &hash)
            .await
            .unwrap()
            .hash()
            .unwrap(),
        hash
    );
}
