//! Cooperative, root-last portfolio checkpoint publication. Never a whole-run fence.
use super::ClickHouse;
use arte_core::{
    config::Acceptance,
    portfolio::{
        checkpoint::{
            storage::{Bundle, Root, CHUNK_BYTES, MAX_ROOT_BYTES},
            Cut, Limits,
        },
        Portfolio,
    },
    run_manifest::Pinned,
    seed_storage::Object,
    Error, Result,
};
use std::{
    collections::{BTreeMap, BTreeSet},
    future::Future,
};
const CHUNKS: &str = "portfolio_checkpoint_chunks_v1";
const ROOTS: &str = "portfolio_checkpoints_v1";
pub fn portfolio_checkpoint_scope(run: &Pinned, cut: &Cut) -> Result<String> {
    if cut.boundary_sequence == 0
        || cut.boundary_hash.len() != 64
        || !cut
            .boundary_hash
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
    {
        return Err(Error::Invalid("invalid portfolio publication cut".into()));
    }
    arte_core::content_hash(&(
        "arte.portfolio-cut-slot.v1",
        run.hash(),
        cut.boundary_sequence,
    ))
}
fn to_hex(bytes: &[u8]) -> String {
    const HEX: &[u8] = b"0123456789abcdef";
    let mut out = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        out.push(HEX[usize::from(byte >> 4)] as char);
        out.push(HEX[usize::from(byte & 15)] as char);
    }
    out
}
fn from_hex(text: &str, maximum: usize) -> Result<Vec<u8>> {
    if text.len() > maximum * 2 || !text.len().is_multiple_of(2) {
        return Err(Error::Capacity("checkpoint hex byte bound".into()));
    }
    let digit = |v| match v {
        b'0'..=b'9' => Ok(v - b'0'),
        b'a'..=b'f' => Ok(v - b'a' + 10),
        _ => Err(Error::Invalid("noncanonical checkpoint hex".into())),
    };
    text.as_bytes()
        .chunks_exact(2)
        .map(|pair| Ok((digit(pair[0])? << 4) | digit(pair[1])?))
        .collect()
}
trait Store {
    fn read(&self, root: bool, key: &str) -> impl Future<Output = Result<Option<Vec<u8>>>>;
    fn write(&self, root: bool, key: &str, bytes: &[u8]) -> impl Future<Output = Result<()>>;
}
impl Store for ClickHouse {
    async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
        let table = if root { ROOTS } else { CHUNKS };
        self.verify_storage(table).await?;
        self.immutable_value(table, "record_hash", key, "payload_hex")
            .await?
            .map(|text| from_hex(&text, if root { MAX_ROOT_BYTES } else { CHUNK_BYTES }))
            .transpose()
    }
    async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
        self.insert(
            if root { ROOTS } else { CHUNKS },
            &[serde_json::json!({"record_hash": key, "payload_hex": to_hex(bytes)})],
        )
        .await
    }
}
async fn put(
    store: &impl Store,
    root: bool,
    key: &str,
    payload: &[u8],
    owned: &impl Fn() -> Result<()>,
) -> Result<()> {
    owned()?;
    match store.read(root, key).await? {
        Some(previous) if previous != payload => {
            return Err(Error::Conflict("portfolio publication slot differs".into()))
        }
        Some(_) => {}
        None => {
            owned()?;
            store.write(root, key, payload).await?;
        }
    }
    owned()?;
    if store.read(root, key).await?.as_deref() != Some(payload) {
        return Err(Error::Unready(
            "portfolio readback missing or different".into(),
        ));
    }
    Ok(())
}
async fn publish(
    store: &impl Store,
    slot: &str,
    bundle: &Bundle,
    owned: &impl Fn() -> Result<()>,
) -> Result<()> {
    owned()?;
    if let Some(previous) = store.read(true, slot).await? {
        if previous != bundle.root.payload {
            return Err(Error::Conflict(
                "portfolio cut already published differently".into(),
            ));
        }
    }
    for chunk in bundle.chunks.values() {
        put(store, false, &chunk.id, &chunk.payload, owned).await?;
    }
    put(store, true, slot, &bundle.root.payload, owned).await
}
impl ClickHouse {
    pub async fn load_portfolio_checkpoint(
        &self,
        run: &Pinned,
        cut: &Cut,
        expected_checkpoint_hash: &str,
        limits: &Limits,
    ) -> Result<Portfolio> {
        let slot = portfolio_checkpoint_scope(run, cut)?;
        let root = Object::new(
            Store::read(self, true, &slot)
                .await?
                .ok_or_else(|| Error::Unready("portfolio root missing".into()))?,
        );
        let header = Root::decode(&root)?;
        if header.manifest_hash != run.hash()
            || &header.cut != cut
            || header.checkpoint_hash != expected_checkpoint_hash
            || header.total_bytes > limits.maximum_bytes
        {
            return Err(Error::Conflict(
                "portfolio stored identity or budget differs".into(),
            ));
        }
        let mut chunks = BTreeMap::new();
        for id in &header.chunks {
            if chunks.contains_key(id) {
                continue;
            }
            let payload = Store::read(self, false, id)
                .await?
                .ok_or_else(|| Error::Unready("portfolio chunk missing".into()))?;
            let chunk = Object {
                id: id.clone(),
                payload,
            };
            chunk.verify()?;
            chunks.insert(id.clone(), chunk);
        }
        Bundle { root, chunks }.hydrate(run, cut, expected_checkpoint_hash, limits)
    }
    pub async fn publish_portfolio_checkpoint(
        &self,
        portfolio: &Portfolio,
        run: &Pinned,
        cut: &Cut,
        limits: &Limits,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "portfolio publication acceptance missing: {required:?}"
                )));
            }
        }
        let slot = portfolio_checkpoint_scope(run, cut)?;
        let bundle = Bundle::from_portfolio(portfolio, run, cut, limits)?;
        let header = Root::decode(&bundle.root)?;
        publish(self, &slot, &bundle, &|| lease.require(&slot)).await?;
        self.load_portfolio_checkpoint(run, cut, &header.checkpoint_hash, limits)
            .await?;
        lease.require(&slot)?;
        Ok(header.checkpoint_hash)
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
    }
    impl Store for Memory {
        async fn read(&self, root: bool, key: &str) -> Result<Option<Vec<u8>>> {
            Ok(self.rows.borrow().get(&(root, key.into())).cloned())
        }
        async fn write(&self, root: bool, key: &str, bytes: &[u8]) -> Result<()> {
            if !root && *self.fail_chunk.borrow() {
                return Err(Error::Unready("injected chunk failure".into()));
            }
            self.rows
                .borrow_mut()
                .insert((root, key.into()), bytes.to_vec());
            if root && *self.ambiguous_root.borrow() {
                return Err(Error::Unready("injected ambiguous root write".into()));
            }
            Ok(())
        }
    }
    #[tokio::test]
    async fn publication_is_root_last_and_ambiguous_retries_are_exact() {
        // Publication protocol fixture; core codec tests validate portfolio semantics.
        let chunk = Object::new(vec![0, 255, 195, 169]);
        let bundle = Bundle {
            root: Object::new(b"root".to_vec()),
            chunks: BTreeMap::from([(chunk.id.clone(), chunk)]),
        };
        let store = Memory::default();
        *store.fail_chunk.borrow_mut() = true;
        assert!(publish(&store, "slot", &bundle, &|| Ok(())).await.is_err());
        assert!(store.read(true, "slot").await.unwrap().is_none());
        *store.fail_chunk.borrow_mut() = false;
        *store.ambiguous_root.borrow_mut() = true;
        assert!(publish(&store, "slot", &bundle, &|| Ok(())).await.is_err());
        publish(&store, "slot", &bundle, &|| Ok(())).await.unwrap();
        assert_eq!(store.rows.borrow().len(), 2);
        let changed = Bundle {
            root: Object::new(b"changed".to_vec()),
            chunks: BTreeMap::new(),
        };
        assert!(publish(&store, "slot", &changed, &|| Ok(())).await.is_err());
        assert!(publish(&store, "new", &bundle, &|| Err(Error::Unready(
            "ownership lost".into()
        )))
        .await
        .is_err());
        assert!(store.read(true, "new").await.unwrap().is_none());
    }
    #[test]
    fn hex_codec_is_lossless_strict_and_bounded() {
        let bytes: Vec<u8> = (0..=255).collect();
        assert_eq!(from_hex(&to_hex(&bytes), 256).unwrap(), bytes);
        assert!(from_hex("FF", 1).is_err());
        assert!(from_hex("0", 1).is_err());
        assert!(from_hex("gg", 1).is_err());
        assert!(from_hex("0000", 1).is_err());
    }
}
