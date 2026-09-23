//! Root-last persistence for isolated multi-ticker backtest recovery.
//! Readback proves the stored graph, not power-loss durability or permission to run.
use super::{
    portfolio_checkpoints::{from_hex, put, to_hex, Store},
    ClickHouse,
};
use crate::playback_runtime::multi::checkpoint::{
    storage::{Header, Stored, CHUNK_BYTES, MAX_ROOT_BYTES},
    Bundle, Limits, Recovered, ShardEvidence,
};
use arte_core::{
    config::Acceptance,
    content_hash,
    execution_events::Fill,
    market_structure::scheduler::playback::sources::Catalog,
    portfolio::checkpoint::Cut,
    run_manifest::Pinned,
    seed_storage::Object,
    simulation_costs::{Model as CostModel, SettlementCurrency},
    Error, Result,
};
use serde::{de::DeserializeOwned, Serialize};
use std::collections::{BTreeMap, BTreeSet};

const CHUNKS: &str = "multi_backtest_checkpoint_chunks_v1";
const ROOTS: &str = "multi_backtest_checkpoints_v1";

pub struct RestoreRequest<'a> {
    pub manifest: &'a Pinned,
    pub startup: &'a str,
    pub cut: &'a Cut,
    pub expected_graph: &'a str,
    pub sources: &'a Catalog,
    pub seeds: &'a crate::playback_runtime::session::market::seed_catalog::RunSeedCatalog,
    pub evidence: BTreeMap<u64, ShardEvidence<'a>>,
    pub cost_model: &'a CostModel,
    pub last_fills: &'a BTreeMap<usize, BTreeMap<String, Fill>>,
    pub currencies: &'a BTreeMap<u64, SettlementCurrency>,
    pub limits: &'a Limits,
}
impl RestoreRequest<'_> {
    pub fn restore<S: Clone + Serialize + DeserializeOwned>(
        &self,
        bundle: &Bundle,
    ) -> Result<Recovered<S>> {
        bundle.restore(
            self.expected_graph,
            self.manifest,
            self.startup,
            self.cut,
            self.sources,
            self.seeds,
            self.evidence.clone(),
            self.cost_model,
            self.last_fills,
            self.currencies,
            self.limits,
        )
    }
}

pub fn scope(manifest: &Pinned, startup: &str, cut: &Cut) -> Result<String> {
    super::portfolio_checkpoints::portfolio_checkpoint_scope(manifest, cut)?;
    if startup.len() != 64
        || !startup
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(Error::Invalid("multi-backtest startup identity".into()));
    }
    content_hash(&(
        "arte.multi-backtest-cut-slot.v1",
        manifest.hash(),
        startup,
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
            .map(|value| from_hex(&value, if root { MAX_ROOT_BYTES } else { CHUNK_BYTES }))
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

fn require_header(header: &Header, request: &RestoreRequest<'_>) -> Result<()> {
    if header.manifest != request.manifest.hash()
        || header.startup != request.startup
        || &header.cut != request.cut
        || header.graph != request.expected_graph
    {
        return Err(Error::Conflict(
            "multi-backtest publication context differs".into(),
        ));
    }
    Ok(())
}

async fn read_bundle(store: &impl Store, request: &RestoreRequest<'_>) -> Result<Bundle> {
    let slot = scope(request.manifest, request.startup, request.cut)?;
    let root = Object::new(
        store
            .read(true, &slot)
            .await?
            .ok_or_else(|| Error::Unready("multi-backtest root missing".into()))?,
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
            .ok_or_else(|| Error::Unready("multi-backtest chunk missing".into()))?;
        let object = Object {
            id: id.clone(),
            payload,
        };
        object.verify()?;
        chunks.insert(id, object);
    }
    Stored { root, chunks }.hydrate(
        request.manifest,
        request.startup,
        request.cut,
        request.expected_graph,
        request.limits.maximum_bytes,
    )
}

async fn publish(
    store: &impl Store,
    slot: &str,
    archive: &Stored,
    owned: &impl Fn() -> Result<()>,
) -> Result<()> {
    owned()?;
    if store
        .read(true, slot)
        .await?
        .is_some_and(|prior| prior != archive.root.payload)
    {
        return Err(Error::Conflict(
            "multi-backtest cut already published differently".into(),
        ));
    }
    for chunk in archive.chunks.values() {
        put(store, false, &chunk.id, &chunk.payload, owned).await?;
    }
    put(store, true, slot, &archive.root.payload, owned).await
}

fn require_acceptance(passed: &BTreeSet<Acceptance>) -> Result<()> {
    for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
        if !passed.contains(&required) {
            return Err(Error::Unready(format!(
                "multi-backtest publication acceptance missing: {required:?}"
            )));
        }
    }
    Ok(())
}

async fn publish_verified<S: Clone + Serialize + DeserializeOwned>(
    store: &impl Store,
    bundle: &Bundle,
    request: &RestoreRequest<'_>,
    owned: &impl Fn() -> Result<()>,
) -> Result<String> {
    owned()?;
    request.restore::<S>(bundle)?;
    let archive = Stored::from_bundle(
        bundle,
        request.manifest,
        request.startup,
        request.cut,
        request.limits.maximum_bytes,
    )?;
    require_header(
        &Header::decode(&archive.root, request.limits.maximum_bytes)?,
        request,
    )?;
    let slot = scope(request.manifest, request.startup, request.cut)?;
    publish(store, &slot, &archive, owned).await?;
    request.restore::<S>(&read_bundle(store, request).await?)?;
    owned()?;
    Ok(bundle.root.id.clone())
}

impl ClickHouse {
    /// Returns an isolated backtest state only. Caller must not infer resume
    /// approval, broker authority or power-loss durability from this result.
    pub async fn load_multi_backtest_checkpoint<S: Clone + Serialize + DeserializeOwned>(
        &self,
        request: &RestoreRequest<'_>,
    ) -> Result<Recovered<S>> {
        request.restore(&read_bundle(&Storage(self), request).await?)
    }

    pub async fn publish_multi_backtest_checkpoint<S: Clone + Serialize + DeserializeOwned>(
        &self,
        bundle: &Bundle,
        request: &RestoreRequest<'_>,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        require_acceptance(passed)?;
        let slot = scope(request.manifest, request.startup, request.cut)?;
        lease.require(&slot)?;
        publish_verified::<S>(&Storage(self), bundle, request, &|| lease.require(&slot)).await
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::cell::{Cell, RefCell};

    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<(bool, String), Vec<u8>>>,
        fail_chunk: Cell<bool>,
    }
    impl Store for Memory {
        async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(root, key.into())).cloned())
        }
        async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
            if !root && self.fail_chunk.get() {
                return Err(Error::Unready("injected chunk write failure".into()));
            }
            self.rows
                .borrow_mut()
                .insert((root, key.into()), bytes.to_vec());
            Ok(())
        }
    }

    #[tokio::test]
    async fn chunks_precede_root_and_conflicts_never_replace_it() {
        let store = Memory::default();
        let slot = "a".repeat(64);
        let chunk = Object::new(vec![7]);
        let archive = Stored {
            root: Object::new(vec![8]),
            chunks: BTreeMap::from([(chunk.id.clone(), chunk)]),
        };
        store.fail_chunk.set(true);
        assert!(publish(&store, &slot, &archive, &|| Ok(())).await.is_err());
        assert!(store.read(true, &slot).await.unwrap().is_none());
        store.fail_chunk.set(false);
        publish(&store, &slot, &archive, &|| Ok(())).await.unwrap();
        assert_eq!(store.read(true, &slot).await.unwrap(), Some(vec![8]));
        let changed = Stored {
            root: Object::new(vec![9]),
            chunks: BTreeMap::new(),
        };
        assert!(publish(&store, &slot, &changed, &|| Ok(())).await.is_err());
        assert_eq!(store.read(true, &slot).await.unwrap(), Some(vec![8]));
        assert!(require_acceptance(&BTreeSet::new()).is_err());
    }
}
