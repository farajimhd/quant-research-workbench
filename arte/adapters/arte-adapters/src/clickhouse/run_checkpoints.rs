//! Root-last publication of common-cut backtest recovery graphs.
use super::{
    portfolio_checkpoints::{from_hex, put, to_hex, Store},
    ClickHouse,
};
use crate::playback_runtime::recovery::{
    storage::{Header, Stored, CHUNK_BYTES, MAX_ROOT_BYTES},
    Bundle, Recovered, RestoreRequest,
};
use arte_core::{
    config::Acceptance, content_hash, portfolio::checkpoint::Cut, run_manifest::Pinned,
    seed_storage::Object, Error, Result,
};
use std::collections::{BTreeMap, BTreeSet};
const CHUNKS: &str = "backtest_checkpoint_chunks_v1";
const ROOTS: &str = "backtest_checkpoints_v1";
pub fn backtest_checkpoint_scope(run: &Pinned, cut: &Cut) -> Result<String> {
    super::portfolio_checkpoints::portfolio_checkpoint_scope(run, cut)?;
    content_hash(&(
        "arte.backtest-run-cut-slot.v1",
        run.hash(),
        cut.boundary_sequence,
    ))
}
struct Storage<'a>(&'a ClickHouse);
impl Store for Storage<'_> {
    async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
        let table = if root { ROOTS } else { CHUNKS };
        self.0.verify_storage(table).await?;
        self.0
            .immutable_value(table, "record_hash", key, "payload_hex")
            .await?
            .map(|text| from_hex(&text, if root { MAX_ROOT_BYTES } else { CHUNK_BYTES }))
            .transpose()
    }
    async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
        self.0
            .insert(
                if root { ROOTS } else { CHUNKS },
                &[serde_json::json!({"record_hash": key, "payload_hex": to_hex(bytes)})],
            )
            .await
    }
}
fn require_header(header: &Header, request: &RestoreRequest<'_>) -> Result<()> {
    if header.manifest != request.manifest.hash()
        || &header.cut != request.cut
        || header.checkpoint != request.expected_root
    {
        return Err(Error::Conflict(
            "backtest publication context differs".into(),
        ));
    }
    Ok(())
}
async fn read_bundle(store: &impl Store, request: &RestoreRequest<'_>) -> Result<Bundle> {
    let slot = backtest_checkpoint_scope(request.manifest, request.cut)?;
    let root = Object::new(
        store
            .read(true, &slot)
            .await?
            .ok_or_else(|| Error::Unready("backtest checkpoint root missing".into()))?,
    );
    let header = Header::decode(&root, request.limits.maximum_bytes)?;
    require_header(&header, request)?;
    let mut chunks = BTreeMap::new();
    for id in header.chunks {
        if chunks.contains_key(&id) {
            continue;
        }
        let payload = store
            .read(false, &id)
            .await?
            .ok_or_else(|| Error::Unready("backtest checkpoint chunk missing".into()))?;
        let object = Object {
            id: id.clone(),
            payload,
        };
        object.verify()?;
        chunks.insert(id, object);
    }
    Stored { root, chunks }.hydrate(request.limits.maximum_bytes)
}
async fn publish(
    store: &impl Store,
    slot: &str,
    graph: &Stored,
    owned: &impl Fn() -> Result<()>,
) -> Result<()> {
    owned()?;
    if store
        .read(true, slot)
        .await?
        .is_some_and(|bytes| bytes != graph.root.payload)
    {
        return Err(Error::Conflict(
            "backtest cut already published differently".into(),
        ));
    }
    for chunk in graph.chunks.values() {
        put(store, false, &chunk.id, &chunk.payload, owned).await?;
    }
    put(store, true, slot, &graph.root.payload, owned).await
}
impl ClickHouse {
    pub async fn load_backtest_checkpoint(
        &self,
        request: &RestoreRequest<'_>,
    ) -> Result<Recovered> {
        request.restore(&read_bundle(&Storage(self), request).await?)
    }
    pub async fn publish_backtest_checkpoint(
        &self,
        bundle: &Bundle,
        request: &RestoreRequest<'_>,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "backtest publication acceptance missing: {required:?}"
                )));
            }
        }
        let slot = backtest_checkpoint_scope(request.manifest, request.cut)?;
        lease.require(&slot)?;
        // Full graph, journal and funding validation before the first write.
        request.restore(bundle)?;
        let graph = Stored::from_bundle(bundle, request.limits.maximum_bytes)?;
        require_header(
            &Header::decode(&graph.root, request.limits.maximum_bytes)?,
            request,
        )?;
        publish(&Storage(self), &slot, &graph, &|| lease.require(&slot)).await?;
        self.load_backtest_checkpoint(request).await?;
        lease.require(&slot)?;
        Ok(bundle.root.id.clone())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<(bool, String), Vec<u8>>>,
        fail_chunk: Cell<bool>,
        ambiguous_root: Cell<bool>,
    }
    impl Store for Memory {
        async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(root, key.into())).cloned())
        }
        async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
            if !root && self.fail_chunk.get() {
                return Err(Error::Unready("injected chunk failure".into()));
            }
            self.rows
                .borrow_mut()
                .insert((root, key.into()), bytes.to_vec());
            if root && self.ambiguous_root.get() {
                return Err(Error::Unready("ambiguous root".into()));
            }
            Ok(())
        }
    }
    pub(crate) async fn roundtrip(bundle: &Bundle, request: &RestoreRequest<'_>) -> Recovered {
        request.restore(bundle).unwrap();
        let graph = Stored::from_bundle(bundle, request.limits.maximum_bytes).unwrap();
        let slot = backtest_checkpoint_scope(request.manifest, request.cut).unwrap();
        let mut forked_cut = request.cut.clone();
        forked_cut.boundary_hash = "f".repeat(64);
        assert_eq!(
            slot,
            backtest_checkpoint_scope(request.manifest, &forked_cut).unwrap()
        );
        let memory = Memory::default();
        assert!(read_bundle(&memory, request).await.is_err());
        publish(&memory, &slot, &graph, &|| Ok(())).await.unwrap();
        let loaded = read_bundle(&memory, request).await.unwrap();
        let recovered = request.restore(&loaded).unwrap();
        publish(&memory, &slot, &graph, &|| Ok(())).await.unwrap();
        assert_eq!(memory.rows.borrow().len(), graph.chunks.len() + 1);
        let id = graph.chunks.keys().next().unwrap();
        memory.rows.borrow_mut().remove(&(false, id.clone()));
        assert!(read_bundle(&memory, request).await.is_err());
        recovered
    }
    #[tokio::test]
    async fn partial_graph_is_invisible_and_root_retry_is_idempotent() {
        // Storage protocol only; actual graph roundtrip is tested in playback.
        let chunk = Object::new(vec![0, 255]);
        let graph = Stored {
            root: Object::new(b"header".to_vec()),
            chunks: BTreeMap::from([(chunk.id.clone(), chunk)]),
        };
        let memory = Memory::default();
        memory.fail_chunk.set(true);
        assert!(publish(&memory, "slot", &graph, &|| Ok(())).await.is_err());
        assert!(memory.read(true, "slot").await.unwrap().is_none());
        memory.fail_chunk.set(false);
        memory.ambiguous_root.set(true);
        assert!(publish(&memory, "slot", &graph, &|| Ok(())).await.is_err());
        memory.ambiguous_root.set(false);
        publish(&memory, "slot", &graph, &|| Ok(())).await.unwrap();
        assert_eq!(memory.rows.borrow().len(), 2);
        let wrong = Stored {
            root: Object::new(b"fork".to_vec()),
            chunks: BTreeMap::new(),
        };
        assert!(publish(&memory, "slot", &wrong, &|| Ok(())).await.is_err());
        assert!(publish(&memory, "new", &graph, &|| Err(Error::Unready(
            "lease lost".into()
        )))
        .await
        .is_err());
        assert!(memory.read(true, "new").await.unwrap().is_none());
    }
}
