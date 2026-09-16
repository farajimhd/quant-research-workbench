//! Cooperative publication of execution graphs. Not a whole-run recovery fence.
use super::{
    portfolio_checkpoints::{from_hex, put, to_hex, Store},
    ClickHouse,
};
use crate::simulation_runtime::{
    checkpoint::{
        storage::{Header, Stored, CHUNK_BYTES, MAX_ROOT_BYTES},
        Bundle, Limits,
    },
    Runtime,
};
use arte_core::{
    config::Acceptance,
    portfolio::checkpoint::Cut,
    run_manifest::Pinned,
    seed_storage::Object,
    simulation_costs::{Model, Pinned as Costs},
    Error, Result,
};
use std::collections::{BTreeMap, BTreeSet};
const CHUNKS: &str = "execution_checkpoint_chunks_v1";
const ROOTS: &str = "execution_checkpoints_v1";
pub struct ExecutionRecovery<'a> {
    pub run: &'a Pinned,
    pub cut: &'a Cut,
    pub instrument: u64,
    pub expected_root: &'a str,
    pub cost_model: &'a Model,
    pub limits: Limits,
}
pub fn execution_checkpoint_scope(run: &Pinned, cut: &Cut, instrument: u64) -> Result<String> {
    super::portfolio_checkpoints::portfolio_checkpoint_scope(run, cut)?;
    if instrument == 0
        || !run
            .manifest()
            .consumers
            .iter()
            .any(|c| c.instrument == instrument)
    {
        return Err(Error::Invalid(
            "execution publication instrument undeclared".into(),
        ));
    }
    arte_core::content_hash(&(
        "arte.execution-cut-slot.v1",
        run.hash(),
        instrument,
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
fn require_header(header: &Header, request: &ExecutionRecovery<'_>) -> Result<()> {
    if header.manifest_hash != request.run.hash()
        || &header.cut != request.cut
        || header.instrument != request.instrument
        || header.execution_root != request.expected_root
    {
        return Err(Error::Conflict(
            "execution publication context differs".into(),
        ));
    }
    Ok(())
}
async fn read_bundle(store: &impl Store, request: &ExecutionRecovery<'_>) -> Result<Bundle> {
    let slot = execution_checkpoint_scope(request.run, request.cut, request.instrument)?;
    let root = Object::new(
        store
            .read(true, &slot)
            .await?
            .ok_or_else(|| Error::Unready("execution checkpoint root missing".into()))?,
    );
    let header = Header::decode(&root, request.limits)?;
    require_header(&header, request)?;
    let mut chunks = BTreeMap::new();
    for id in &header.chunks {
        if chunks.contains_key(id) {
            continue;
        }
        let payload = store
            .read(false, id)
            .await?
            .ok_or_else(|| Error::Unready("execution checkpoint chunk missing".into()))?;
        let object = Object {
            id: id.clone(),
            payload,
        };
        object.verify()?;
        chunks.insert(id.clone(), object);
    }
    Stored { root, chunks }.hydrate(request.limits)
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
            "execution cut already published differently".into(),
        ));
    }
    for chunk in graph.chunks.values() {
        put(store, false, &chunk.id, &chunk.payload, owned).await?;
    }
    put(store, true, slot, &graph.root.payload, owned).await
}
impl ClickHouse {
    pub async fn load_execution_checkpoint(
        &self,
        request: &ExecutionRecovery<'_>,
    ) -> Result<Runtime> {
        let costs = Costs::new(request.cost_model.clone(), request.run)?;
        let bundle = read_bundle(&Storage(self), request).await?;
        Runtime::restore_checkpoint(
            &bundle,
            request.expected_root,
            request.run,
            request.cut,
            costs,
            request.limits,
        )
    }
    pub async fn publish_execution_checkpoint(
        &self,
        bundle: &Bundle,
        request: &ExecutionRecovery<'_>,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "execution publication acceptance missing: {required:?}"
                )));
            }
        }
        let slot = execution_checkpoint_scope(request.run, request.cut, request.instrument)?;
        lease.require(&slot)?;
        // Full semantic validation precedes the first storage operation.
        Runtime::restore_checkpoint(
            bundle,
            request.expected_root,
            request.run,
            request.cut,
            Costs::new(request.cost_model.clone(), request.run)?,
            request.limits,
        )?;
        let stored = Stored::from_execution(bundle, request.limits)?;
        require_header(&Header::decode(&stored.root, request.limits)?, request)?;
        publish(&Storage(self), &slot, &stored, &|| lease.require(&slot)).await?;
        self.load_execution_checkpoint(request).await?;
        lease.require(&slot)?;
        Ok(bundle.root.id.clone())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};
    #[tokio::test]
    async fn stored_graph_loads_and_restores_only_at_its_exact_cut() {
        let (run, cut, model, limits, bundle) =
            crate::simulation_runtime::checkpoint::tests::publication_fixture();
        let request = ExecutionRecovery {
            run: &run,
            cut: &cut,
            instrument: 1,
            expected_root: &bundle.root.id,
            cost_model: &model,
            limits,
        };
        let slot = execution_checkpoint_scope(&run, &cut, 1).unwrap();
        let graph = Stored::from_execution(&bundle, limits).unwrap();
        let memory = Memory::default();
        publish(&memory, &slot, &graph, &|| Ok(())).await.unwrap();
        let loaded = read_bundle(&memory, &request).await.unwrap();
        Runtime::restore_checkpoint(
            &loaded,
            request.expected_root,
            &run,
            &cut,
            Costs::new(model.clone(), &run).unwrap(),
            limits,
        )
        .unwrap();
        let mut wrong_cut = cut.clone();
        wrong_cut.boundary_hash = "f".repeat(64);
        // The publication slot must not change when the same sequence forks.
        assert_eq!(
            execution_checkpoint_scope(&run, &wrong_cut, 1).unwrap(),
            slot
        );
        let wrong = ExecutionRecovery {
            cut: &wrong_cut,
            ..request
        };
        assert!(read_bundle(&memory, &wrong).await.is_err());
        let id = graph.chunks.keys().next().unwrap();
        memory.rows.borrow_mut().remove(&(false, id.clone()));
        let exact = ExecutionRecovery { cut: &cut, ..wrong };
        assert!(read_bundle(&memory, &exact).await.is_err());
    }
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
    #[tokio::test]
    async fn incomplete_writes_remain_invisible_and_ambiguous_retries_are_exact() {
        // Protocol fixture. The execution codec suite covers semantic validity.
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
        publish(&memory, "slot", &graph, &|| Ok(())).await.unwrap();
        assert_eq!(memory.rows.borrow().len(), 2);
        let changed = Stored {
            root: Object::new(b"changed".to_vec()),
            chunks: BTreeMap::new(),
        };
        assert!(publish(&memory, "slot", &changed, &|| Ok(()))
            .await
            .is_err());
        let checks = Cell::new(0);
        assert!(publish(&memory, "other", &graph, &|| {
            checks.set(checks.get() + 1);
            if checks.get() > 2 {
                Err(Error::Unready("ownership lost".into()))
            } else {
                Ok(())
            }
        })
        .await
        .is_err());
        assert!(memory.read(true, "other").await.unwrap().is_none());
    }
}
