//! Exact live bars and signal state share one scheduler-boundary recovery root.
//! A complete scheduler and lane root must also refer to this root before
//! restarting a live actor; publication here alone never authorizes trading.
use super::{
    portfolio_checkpoints::{from_hex, to_hex},
    ClickHouse,
};
use crate::live_exact_signal::{Bundle, Owner, Recovery as OwnerRecovery};
use arte_core::{
    config::Acceptance,
    exact_bars::{Builder, Mode as BarMode},
    seed_storage::Object,
    Error, Result,
};
use serde_json::Value;
use std::collections::BTreeSet;

const OBJECTS: &str = "exact_signal_objects_v1";
const ROOTS: &str = "exact_signal_roots_v1";
const LIMIT: usize = 4096;

fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn exact_signal_checkpoint_scope(configuration_hash: &str) -> Result<String> {
    if !hash_valid(configuration_hash) {
        return Err(Error::Invalid("exact signal configuration hash".into()));
    }
    arte_core::content_hash(&("arte.exact-signal-owner.v1", configuration_hash))
}
fn slot(configuration_hash: &str, sequence: u64) -> Result<String> {
    exact_signal_checkpoint_scope(configuration_hash)?;
    arte_core::content_hash(&("arte.exact-signal-slot.v1", configuration_hash, sequence))
}
pub struct Recovery<'a> {
    pub owner: OwnerRecovery<'a>,
    pub configuration_hash: &'a str,
    pub expected_root: &'a str,
    pub as_of_ns: u64,
}
impl Recovery<'_> {
    fn validate(&self) -> Result<String> {
        if !hash_valid(self.expected_root) || self.as_of_ns < self.owner.session_start_ns {
            return Err(Error::Invalid(
                "exact signal recovery root or cutoff".into(),
            ));
        }
        let bars = Builder::new(
            self.owner.scope,
            BarMode::Live,
            self.owner.session_start_ns,
            self.owner.session_end_ns,
            self.owner.price_scale,
            self.owner.size_scale,
            self.owner.source_generation_hash.into(),
        )?;
        let expected = arte_core::content_hash(&(
            "arte.exact-signal-configuration.v1",
            bars.configuration_hash(),
            self.owner.signal_config.hash()?,
        ))?;
        if expected != self.configuration_hash {
            return Err(Error::Conflict(
                "exact signal checkpoint configuration".into(),
            ));
        }
        slot(self.configuration_hash, self.owner.expected_sequence)
    }
}
#[derive(Clone, PartialEq, Eq)]
struct RootRow {
    hash: String,
    published_at_ns: u64,
}
fn decode_root(body: &str) -> Result<Option<RootRow>> {
    let mut found = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        let value: Value =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        let hash = value
            .get("root_hash")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("exact signal root hash".into()))?;
        let published_at_ns = value
            .get("published_at_ns")
            .and_then(|v| {
                v.as_u64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .ok_or_else(|| Error::Invalid("exact signal root publication".into()))?;
        if !hash_valid(hash) {
            return Err(Error::Invalid("exact signal root hash".into()));
        }
        let row = RootRow {
            hash: hash.into(),
            published_at_ns,
        };
        if found.as_ref().is_some_and(|old| old != &row) {
            return Err(Error::Conflict("exact signal root slot fork".into()));
        }
        found = Some(row);
    }
    Ok(found)
}
impl ClickHouse {
    async fn exact_signal_root(&self, slot_hash: &str) -> Result<Option<RootRow>> {
        self.verify_storage(ROOTS).await?;
        let sql = format!("SELECT DISTINCT root_hash,published_at_ns FROM {}.{ROOTS} WHERE slot_hash='{slot_hash}' LIMIT 3 FORMAT JSONEachRow", self.database);
        decode_root(&self.request(&sql, String::new()).await?)
    }
    async fn exact_signal_object(&self, hash: &str) -> Result<Object> {
        self.verify_storage(OBJECTS).await?;
        let hex = self
            .immutable_value(OBJECTS, "object_hash", hash, "payload_hex")
            .await?
            .ok_or_else(|| Error::Unready("exact signal object missing".into()))?;
        let object = Object {
            id: hash.into(),
            payload: from_hex(&hex, LIMIT)?,
        };
        object.verify()?;
        Ok(object)
    }
    async fn put_exact_signal_object(&self, object: &Object) -> Result<()> {
        object.verify()?;
        if object.payload.len() > LIMIT {
            return Err(Error::Capacity("exact signal object bytes".into()));
        }
        self.verify_storage(OBJECTS).await?;
        let hex = to_hex(&object.payload);
        match self
            .immutable_value(OBJECTS, "object_hash", &object.id, "payload_hex")
            .await?
        {
            Some(existing) if existing == hex => return Ok(()),
            Some(_) => return Err(Error::Conflict("exact signal object fork".into())),
            None => {}
        }
        self.insert(
            OBJECTS,
            &[serde_json::json!({"object_hash":object.id,"payload_hex":hex})],
        )
        .await?;
        if self.exact_signal_object(&object.id).await?.payload != object.payload {
            return Err(Error::Unready("exact signal object readback".into()));
        }
        Ok(())
    }
    pub async fn load_exact_signal_checkpoint(&self, request: Recovery<'_>) -> Result<Owner> {
        let key = request.validate()?;
        let row = self
            .exact_signal_root(&key)
            .await?
            .ok_or_else(|| Error::Unready("exact signal root missing".into()))?;
        if row.hash != request.expected_root || row.published_at_ns > request.as_of_ns {
            return Err(Error::Unready(
                "exact signal root not available at cut".into(),
            ));
        }
        let root = self.exact_signal_object(&row.hash).await?;
        let (bars_id, signal_id) = Bundle::references(&root)?;
        let bundle = Bundle {
            root,
            bars: self.exact_signal_object(&bars_id).await?,
            signal: self.exact_signal_object(&signal_id).await?,
        };
        let owner = Owner::restore(&bundle, request.expected_root, request.owner)?;
        if owner.last_evaluated_at_ns() > row.published_at_ns {
            return Err(Error::Conflict(
                "exact signal publication predates evidence".into(),
            ));
        }
        Ok(owner)
    }
    pub async fn publish_exact_signal_checkpoint(
        &self,
        owner: &Owner,
        request: Recovery<'_>,
        published_at_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "exact signal publication acceptance missing: {required:?}"
                )));
            }
        }
        let key = request.validate()?;
        let scope = exact_signal_checkpoint_scope(request.configuration_hash)?;
        lease.require(&scope)?;
        if published_at_ns > request.as_of_ns
            || published_at_ns < owner.last_evaluated_at_ns()
            || published_at_ns < request.owner.session_start_ns
            || owner.last_boundary()
                != (
                    request.owner.expected_sequence,
                    request.owner.expected_boundary_id,
                )
        {
            return Err(Error::Conflict(
                "exact signal publication boundary or clock".into(),
            ));
        }
        let bundle = owner.checkpoint()?;
        if owner.configuration_hash()? != request.configuration_hash {
            return Err(Error::Conflict("exact signal owner configuration".into()));
        }
        if bundle.root.id != request.expected_root {
            return Err(Error::Conflict("exact signal expected root differs".into()));
        }
        Owner::restore(
            &bundle,
            request.expected_root,
            OwnerRecovery {
                scope: request.owner.scope,
                session_start_ns: request.owner.session_start_ns,
                session_end_ns: request.owner.session_end_ns,
                price_scale: request.owner.price_scale,
                size_scale: request.owner.size_scale,
                source_generation_hash: request.owner.source_generation_hash,
                signal_config: request.owner.signal_config.clone(),
                expected_sequence: request.owner.expected_sequence,
                expected_boundary_id: request.owner.expected_boundary_id,
            },
        )?;
        self.verify_storage(ROOTS).await?;
        let row = RootRow {
            hash: bundle.root.id.clone(),
            published_at_ns,
        };
        if let Some(existing) = self.exact_signal_root(&key).await? {
            if existing != row {
                return Err(Error::Conflict("exact signal root slot fork".into()));
            }
        } else {
            for object in [&bundle.bars, &bundle.signal, &bundle.root] {
                lease.require(&scope)?;
                self.put_exact_signal_object(object).await?;
            }
            lease.require(&scope)?;
            self.insert(ROOTS, &[serde_json::json!({"slot_hash":key,"root_hash":row.hash,"published_at_ns":published_at_ns})]).await?;
        }
        lease.require(&scope)?;
        if self.exact_signal_root(&key).await?.as_ref() != Some(&row) {
            return Err(Error::Unready("exact signal root readback".into()));
        }
        self.load_exact_signal_checkpoint(request).await?;
        lease.require(&scope)?;
        Ok(bundle.root.id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn root_rows_are_immutable_and_scoped() {
        let h = "a".repeat(64);
        assert_ne!(slot(&h, 1).unwrap(), slot(&h, 2).unwrap());
        assert!(slot("bad", 1).is_err());
        let one = serde_json::json!({"root_hash":h,"published_at_ns":1});
        assert!(decode_root(&format!("{one}\n{one}")).unwrap().is_some());
        let two = serde_json::json!({"root_hash":"b".repeat(64),"published_at_ns":1});
        assert!(decode_root(&format!("{one}\n{two}")).is_err());
    }
}
