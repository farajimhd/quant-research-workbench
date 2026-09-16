//! Root-last publication of common-cut backtest recovery graphs.
use super::{
    portfolio_checkpoints::{from_hex, put, to_hex, Store},
    ClickHouse,
};
use crate::playback_runtime::recovery::{
    publication::{Finalized, Owners, Published},
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
    /// Holds exclusive runtime owners across publication. Cancellation never
    /// acknowledges the boundary; retain `finalized` and retry it unchanged.
    pub async fn commit_backtest_boundary(
        &self,
        finalized: &Finalized,
        request: &RestoreRequest<'_>,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
        owners: Owners<'_>,
    ) -> Result<()> {
        require_acceptance(passed)?;
        let slot = backtest_checkpoint_scope(request.manifest, request.cut)?;
        commit_verified(
            &Storage(self),
            finalized,
            request,
            &|| lease.require(&slot),
            owners,
        )
        .await
    }
    pub async fn load_backtest_checkpoint(
        &self,
        request: &RestoreRequest<'_>,
    ) -> Result<Recovered> {
        request.restore(&read_bundle(&Storage(self), request).await?)
    }
    pub async fn publish_backtest_checkpoint(
        &self,
        finalized: &Finalized,
        request: &RestoreRequest<'_>,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Published> {
        require_acceptance(passed)?;
        let slot = backtest_checkpoint_scope(request.manifest, request.cut)?;
        lease.require(&slot)?;
        publish_verified(&Storage(self), finalized, request, &|| lease.require(&slot)).await
    }
}
fn require_acceptance(passed: &BTreeSet<Acceptance>) -> Result<()> {
    for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
        if !passed.contains(&required) {
            return Err(Error::Unready(format!(
                "backtest publication acceptance missing: {required:?}"
            )));
        }
    }
    Ok(())
}
async fn commit_verified(
    store: &impl Store,
    finalized: &Finalized,
    request: &RestoreRequest<'_>,
    owned: &impl Fn() -> Result<()>,
    owners: Owners<'_>,
) -> Result<()> {
    owned()?;
    let current = Finalized::capture(
        Owners {
            controller: owners.controller,
            candidates: owners.candidates,
            portfolio: owners.portfolio,
            manifest: owners.manifest,
            last_fills: owners.last_fills,
            currencies: owners.currencies,
            limits: owners.limits,
        },
        request.cut,
    )?;
    if current.bundle().root.id != finalized.bundle().root.id {
        return Err(Error::Conflict(
            "boundary changed before publication".into(),
        ));
    }
    drop(current);
    let receipt = publish_verified(store, finalized, request, owned).await?;
    owned()?;
    // No await after the last ownership check or within acknowledgment.
    receipt.acknowledge(owners)
}
async fn publish_verified(
    store: &impl Store,
    finalized: &Finalized,
    request: &RestoreRequest<'_>,
    owned: &impl Fn() -> Result<()>,
) -> Result<Published> {
    let bundle = finalized.bundle();
    owned()?;
    // Full graph, journal and funding validation before the first write.
    request.restore(bundle)?;
    let slot = backtest_checkpoint_scope(request.manifest, request.cut)?;
    let graph = Stored::from_bundle(bundle, request.limits.maximum_bytes)?;
    require_header(
        &Header::decode(&graph.root, request.limits.maximum_bytes)?,
        request,
    )?;
    publish(store, &slot, &graph, owned).await?;
    request.restore(&read_bundle(store, request).await?)?;
    owned()?;
    Published::verified(&bundle.root.id, request.manifest, request.cut)
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
        pause_root: Cell<bool>,
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
            if root && self.pause_root.get() {
                std::future::pending::<()>().await;
            }
            if root && self.ambiguous_root.get() {
                return Err(Error::Unready("ambiguous root".into()));
            }
            Ok(())
        }
    }
    pub(crate) async fn commit(
        bundle: &Finalized,
        request: &RestoreRequest<'_>,
        mut owners: Owners<'_>,
    ) {
        let memory = Memory::default();
        let before = owners.controller.status().acknowledged_boundaries;
        let mut accounts: BTreeMap<_, _> = owners
            .manifest
            .manifest()
            .consumers
            .iter()
            .map(|scope| {
                (
                    scope.account.clone(),
                    owners.portfolio.snapshot(&scope.account).unwrap(),
                )
            })
            .collect();
        accounts.values_mut().next().unwrap().budget_minor -= 1;
        let mut changed = arte_core::portfolio::Portfolio::new(accounts).unwrap();
        let mut drifted = owners.reborrow();
        drifted.portfolio = &mut changed;
        assert!(matches!(
            commit_verified(&memory, bundle, request, &|| Ok(()), drifted).await,
            Err(Error::Conflict(reason)) if reason == "boundary changed before publication"
        ));
        assert!(memory.rows.borrow().is_empty());
        memory.fail_chunk.set(true);
        assert!(
            commit_verified(&memory, bundle, request, &|| Ok(()), owners.reborrow())
                .await
                .is_err()
        );
        assert_eq!(owners.controller.status().acknowledged_boundaries, before);
        memory.fail_chunk.set(false);
        memory.pause_root.set(true);
        {
            use std::{
                future::Future,
                task::{Context, Poll, Waker},
            };
            let mut attempt = Box::pin(commit_verified(
                &memory,
                bundle,
                request,
                &|| Ok(()),
                owners.reborrow(),
            ));
            assert!(matches!(
                attempt
                    .as_mut()
                    .poll(&mut Context::from_waker(Waker::noop())),
                Poll::Pending
            ));
        }
        assert_eq!(owners.controller.status().acknowledged_boundaries, before);
        let slot = backtest_checkpoint_scope(request.manifest, request.cut).unwrap();
        assert!(memory.read(true, &slot).await.unwrap().is_some());
        memory.pause_root.set(false);
        let checks = Cell::new(0);
        assert!(commit_verified(
            &memory,
            bundle,
            request,
            &|| {
                checks.set(checks.get() + 1);
                if checks.get() >= 3 {
                    Err(Error::Unready("ownership lost during retry".into()))
                } else {
                    Ok(())
                }
            },
            owners.reborrow()
        )
        .await
        .is_err());
        assert!(checks.get() >= 3);
        assert_eq!(owners.controller.status().acknowledged_boundaries, before);
        commit_verified(&memory, bundle, request, &|| Ok(()), owners)
            .await
            .unwrap();
    }
    pub(crate) async fn publication(bundle: &Finalized, request: &RestoreRequest<'_>) -> Published {
        let memory = Memory::default();
        memory.fail_chunk.set(true);
        assert!(publish_verified(&memory, bundle, request, &|| Ok(()))
            .await
            .is_err());
        memory.fail_chunk.set(false);
        memory.ambiguous_root.set(true);
        assert!(publish_verified(&memory, bundle, request, &|| Ok(()))
            .await
            .is_err());
        memory.ambiguous_root.set(false);
        publish_verified(&memory, bundle, request, &|| Ok(()))
            .await
            .unwrap()
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
