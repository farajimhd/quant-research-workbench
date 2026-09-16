//! Immutable per-decision/action rejection slot. Ownership is cooperative, not CAS.
use super::ClickHouse;
use arte_core::{
    action_rejection::{Committed, Record},
    config::Acceptance,
    content_hash,
    strategy_transaction::Committed as DecisionReceipt,
    Error, Result,
};
use std::{collections::BTreeSet, future::Future};
const TABLE: &str = "action_rejections_v1";
const MAX_BYTES: usize = 16 * 1024;

trait Store {
    fn read(&self, key: &str) -> impl Future<Output = Result<Option<String>>>;
    fn write(&self, key: &str, payload: &str) -> impl Future<Output = Result<()>>;
}
impl Store for ClickHouse {
    async fn read(&self, key: &str) -> Result<Option<String>> {
        self.verify_storage(TABLE).await?;
        self.immutable_value(TABLE, "record_hash", key, "payload_json")
            .await
    }
    async fn write(&self, key: &str, payload: &str) -> Result<()> {
        self.insert(
            TABLE,
            &[serde_json::json!({"record_hash":key,"payload_json":payload})],
        )
        .await
    }
}
fn payload(record: &Record) -> Result<String> {
    let value = serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
    if value.len() > MAX_BYTES {
        return Err(Error::Capacity("rejection payload bound".into()));
    }
    Ok(value)
}
fn decode(value: &str, expected: &Record, decision: &DecisionReceipt) -> Result<Record> {
    if value.len() > MAX_BYTES {
        return Err(Error::Capacity("rejection readback bound".into()));
    }
    let record: Record =
        serde_json::from_str(value).map_err(|e| Error::Serialization(e.to_string()))?;
    record.require(decision)?;
    if record.hash()? != expected.hash()? || payload(&record)? != value {
        return Err(Error::Conflict(
            "rejection journal slot differs or is noncanonical".into(),
        ));
    }
    Ok(record)
}
async fn publish(
    store: &impl Store,
    decision: &DecisionReceipt,
    record: &Record,
) -> Result<Record> {
    record.require(decision)?;
    let key = record.key()?;
    let value = payload(record)?;
    if let Some(existing) = store.read(&key).await? {
        return decode(&existing, record, decision);
    }
    store.write(&key, &value).await?;
    let readback = store
        .read(&key)
        .await?
        .ok_or_else(|| Error::Unready("rejection readback missing".into()))?;
    decode(&readback, record, decision)
}
impl ClickHouse {
    /// Caller supplies the expected checkpoint record; the returned receipt comes
    /// from a separate database read, never from that supplied copy alone.
    pub async fn read_action_rejection(
        &self,
        decision: &DecisionReceipt,
        expected: &Record,
    ) -> Result<Option<Committed>> {
        expected.require(decision)?;
        let Some(value) = Store::read(self, &expected.key()?).await? else {
            return Ok(None);
        };
        let record = decode(&value, expected, decision)?;
        Ok(Some(Committed::from_readback(
            decision,
            &expected.hash()?,
            record,
        )?))
    }
}
pub struct RejectionPublisher<'a> {
    database: &'a ClickHouse,
    lease: &'a mut crate::ownership::Lease,
    scope_hash: String,
}
impl<'a> RejectionPublisher<'a> {
    pub fn new(
        database: &'a ClickHouse,
        lease: &'a mut crate::ownership::Lease,
        scope: &arte_core::strategy_dispatch::Scope,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<Self> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "rejection publication acceptance missing: {required:?}"
                )));
            }
        }
        let scope_hash = content_hash(scope)?;
        lease.require(&scope_hash)?;
        Ok(Self {
            database,
            lease,
            scope_hash,
        })
    }
}
impl crate::rejection_journal::Publisher for RejectionPublisher<'_> {
    async fn append(&mut self, decision: &DecisionReceipt, record: &Record) -> Result<Record> {
        self.lease.require(&self.scope_hash)?;
        if content_hash(&decision.decision().scope)? != self.scope_hash {
            return Err(Error::Conflict("rejection writer scope differs".into()));
        }
        let readback = publish(self.database, decision, record).await?;
        self.lease.require(&self.scope_hash)?;
        Ok(readback)
    }
}

#[cfg(test)]
pub(crate) async fn exercise(decision: &DecisionReceipt, record: &Record) {
    use std::{
        cell::{Cell, RefCell},
        collections::BTreeMap,
    };
    #[derive(Default)]
    struct Memory {
        rows: RefCell<BTreeMap<String, String>>,
        writes: Cell<usize>,
        ambiguous: Cell<bool>,
    }
    impl Store for Memory {
        async fn read(&self, key: &str) -> Result<Option<String>> {
            Ok(self.rows.borrow().get(key).cloned())
        }
        async fn write(&self, key: &str, value: &str) -> Result<()> {
            self.writes.set(self.writes.get() + 1);
            self.rows.borrow_mut().insert(key.into(), value.into());
            if self.ambiguous.replace(false) {
                return Err(Error::Unready("ambiguous write".into()));
            }
            Ok(())
        }
    }
    let store = Memory::default();
    store.ambiguous.set(true);
    assert!(publish(&store, decision, record).await.is_err());
    let recovered = publish(&store, decision, record).await.unwrap();
    assert_eq!(recovered.hash().unwrap(), record.hash().unwrap());
    assert_eq!(store.writes.get(), 1);
    let mut conflicting = record.clone();
    conflicting.evidence_hash = "e".repeat(64);
    assert!(publish(&store, decision, &conflicting).await.is_err());
    assert_eq!(store.writes.get(), 1);
    assert!(decode(&" ".repeat(MAX_BYTES + 1), record, decision).is_err());
    assert!(decode(&(payload(record).unwrap() + " "), record, decision).is_err());
}
