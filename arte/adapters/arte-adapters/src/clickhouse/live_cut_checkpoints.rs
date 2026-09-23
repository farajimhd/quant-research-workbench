//! Immutable ClickHouse graph for a single pending live-lane recovery cut.
//! Child objects are written and read back before the root slot becomes visible.
//! Neither a stored cut nor its readback authorizes orders or proves feed health.
use super::{
    portfolio_checkpoints::{from_hex, to_hex},
    ClickHouse,
};
use crate::{
    live_exact_signal,
    live_market::{self, recovery::Bundle, Lane},
};
use arte_core::{
    config::Acceptance, market_structure::scheduler, seed_storage::Object, Error, Result,
};
use serde_json::Value;
use std::collections::BTreeSet;

const OBJECTS: &str = "live_cut_objects_v1";
const ROOTS: &str = "live_cut_roots_v1";
const MAX_BYTES: usize = 64 * 1024 * 1024;

fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
pub fn live_cut_checkpoint_scope(context_hash: &str) -> Result<String> {
    if !hash_valid(context_hash) {
        return Err(Error::Invalid("live cut context hash".into()));
    }
    arte_core::content_hash(&("arte.live-cut-owner.v1", context_hash))
}
fn slot(context_hash: &str, sequence: u64) -> Result<String> {
    live_cut_checkpoint_scope(context_hash)?;
    if sequence == 0 {
        return Err(Error::Invalid("live cut boundary sequence".into()));
    }
    arte_core::content_hash(&("arte.live-cut-slot.v1", context_hash, sequence))
}
pub struct Recovery<'a> {
    pub lane: &'a live_market::recovery::Request<'a>,
    pub as_of_ns: u64,
}
impl Recovery<'_> {
    fn validate(&self) -> Result<String> {
        if !hash_valid(self.lane.expected_root)
            || self.lane.context_hash != self.lane.scheduler.context_hash
            || self.lane.maximum_bytes == 0
            || self.lane.maximum_bytes > MAX_BYTES
            || self.as_of_ns < self.lane.signal.session_start_ns
        {
            return Err(Error::Invalid(
                "live cut recovery identity or budget".into(),
            ));
        }
        slot(self.lane.context_hash, self.lane.signal.expected_sequence)
    }
    fn lane_request(&self) -> live_market::recovery::Request<'_> {
        let source = self.lane;
        live_market::recovery::Request {
            context_hash: source.context_hash,
            expected_root: source.expected_root,
            scheduler: scheduler::checkpoint::Request {
                context_hash: source.scheduler.context_hash,
                run_id: source.scheduler.run_id,
                seed_hash: source.scheduler.seed_hash,
                configuration_hash: source.scheduler.configuration_hash,
                quote_policy: source.scheduler.quote_policy.clone(),
                maximum_pending: source.scheduler.maximum_pending,
                maximum_bytes: source.scheduler.maximum_bytes,
            },
            signal: live_exact_signal::Recovery {
                scope: source.signal.scope,
                session_start_ns: source.signal.session_start_ns,
                session_end_ns: source.signal.session_end_ns,
                price_scale: source.signal.price_scale,
                size_scale: source.signal.size_scale,
                source_generation_hash: source.signal.source_generation_hash,
                signal_config: source.signal.signal_config.clone(),
                noise_config: source.signal.noise_config.clone(),
                macd_config: source.signal.macd_config.clone(),
                expected_sequence: source.signal.expected_sequence,
                expected_boundary_id: source.signal.expected_boundary_id,
            },
            feature_config: source.feature_config.clone(),
            allowed_lateness_ns: source.allowed_lateness_ns,
            maximum_quote_age_ns: source.maximum_quote_age_ns,
            maximum_bytes: source.maximum_bytes,
        }
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
            .ok_or_else(|| Error::Invalid("live cut root hash".into()))?;
        let published_at_ns = value
            .get("published_at_ns")
            .and_then(|v| {
                v.as_u64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .ok_or_else(|| Error::Invalid("live cut publication time".into()))?;
        if !hash_valid(hash) {
            return Err(Error::Invalid("live cut root hash".into()));
        }
        let row = RootRow {
            hash: hash.into(),
            published_at_ns,
        };
        if found.as_ref().is_some_and(|old| old != &row) {
            return Err(Error::Conflict("live cut root slot fork".into()));
        }
        found = Some(row);
    }
    Ok(found)
}
impl ClickHouse {
    async fn live_cut_root(&self, key: &str) -> Result<Option<RootRow>> {
        self.verify_storage(ROOTS).await?;
        let sql = format!("SELECT DISTINCT root_hash,published_at_ns FROM {}.{ROOTS} WHERE slot_hash='{key}' LIMIT 3 FORMAT JSONEachRow", self.database);
        decode_root(&self.request(&sql, String::new()).await?)
    }
    async fn live_cut_object(&self, hash: &str, maximum_bytes: usize) -> Result<Object> {
        if !hash_valid(hash) || maximum_bytes == 0 || maximum_bytes > MAX_BYTES {
            return Err(Error::Invalid("live cut object request".into()));
        }
        self.verify_storage(OBJECTS).await?;
        let hex = self
            .immutable_value(OBJECTS, "object_hash", hash, "payload_hex")
            .await?
            .ok_or_else(|| Error::Unready("live cut object missing".into()))?;
        let object = Object {
            id: hash.into(),
            payload: from_hex(&hex, maximum_bytes)?,
        };
        object.verify()?;
        Ok(object)
    }
    async fn put_live_cut_object(&self, object: &Object, maximum_bytes: usize) -> Result<()> {
        object.verify()?;
        if object.payload.len() > maximum_bytes || maximum_bytes > MAX_BYTES {
            return Err(Error::Capacity("live cut object bytes".into()));
        }
        self.verify_storage(OBJECTS).await?;
        let hex = to_hex(&object.payload);
        match self
            .immutable_value(OBJECTS, "object_hash", &object.id, "payload_hex")
            .await?
        {
            Some(existing) if existing == hex => return Ok(()),
            Some(_) => return Err(Error::Conflict("live cut object fork".into())),
            None => {}
        }
        self.insert(
            OBJECTS,
            &[serde_json::json!({"object_hash":object.id,"payload_hex":hex})],
        )
        .await?;
        if self
            .live_cut_object(&object.id, maximum_bytes)
            .await?
            .payload
            != object.payload
        {
            return Err(Error::Unready("live cut object readback".into()));
        }
        Ok(())
    }
    pub async fn load_live_cut_checkpoint(&self, request: &Recovery<'_>) -> Result<Lane> {
        let key = request.validate()?;
        let row = self
            .live_cut_root(&key)
            .await?
            .ok_or_else(|| Error::Unready("live cut root missing".into()))?;
        if row.hash != request.lane.expected_root || row.published_at_ns > request.as_of_ns {
            return Err(Error::Unready(
                "live cut root not available at cutoff".into(),
            ));
        }
        let limit = request.lane.maximum_bytes;
        let root = self.live_cut_object(&row.hash, limit).await?;
        let refs = Bundle::references(&root)?;
        if refs.sequence != request.lane.signal.expected_sequence
            || refs.evaluated_at_ns > row.published_at_ns
        {
            return Err(Error::Conflict("live cut root publication clock".into()));
        }
        let scheduler_root = self.live_cut_object(&refs.scheduler, limit).await?;
        let [market_id, trades_id, quotes_id, book_id] =
            scheduler::checkpoint::Bundle::references(&scheduler_root)?;
        let signal_root = self.live_cut_object(&refs.signal, limit).await?;
        let (bars_id, signal_id, noise_id, macd_id, macd_source_id) =
            live_exact_signal::Bundle::references(&signal_root)?;
        let bundle = Bundle {
            root,
            scheduler: scheduler::checkpoint::Bundle {
                root: scheduler_root,
                market: self.live_cut_object(&market_id, limit).await?,
                trades: self.live_cut_object(&trades_id, limit).await?,
                quotes: self.live_cut_object(&quotes_id, limit).await?,
                book: self.live_cut_object(&book_id, limit).await?,
            },
            features: self.live_cut_object(&refs.features, limit).await?,
            signal: live_exact_signal::Bundle {
                root: signal_root,
                bars: self.live_cut_object(&bars_id, limit).await?,
                signal: self.live_cut_object(&signal_id, limit).await?,
                noise: self.live_cut_object(&noise_id, limit).await?,
                macd: self.live_cut_object(&macd_id, limit).await?,
                macd_source: self.live_cut_object(&macd_source_id, limit).await?,
            },
        };
        Lane::restore_pending(&bundle, request.lane_request())
    }
    pub async fn publish_live_cut_checkpoint(
        &self,
        lane: &Lane,
        request: &Recovery<'_>,
        published_at_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<String> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "live cut publication acceptance missing: {required:?}"
                )));
            }
        }
        let key = request.validate()?;
        let scope = live_cut_checkpoint_scope(request.lane.context_hash)?;
        lease.require(&scope)?;
        let bundle =
            lane.checkpoint_pending(request.lane.context_hash, request.lane.maximum_bytes)?;
        let refs = Bundle::references(&bundle.root)?;
        if bundle.root.id != request.lane.expected_root
            || refs.sequence != request.lane.signal.expected_sequence
            || published_at_ns < refs.evaluated_at_ns
            || published_at_ns > request.as_of_ns
        {
            return Err(Error::Conflict(
                "live cut publication context or clock".into(),
            ));
        }
        Lane::restore_pending(&bundle, request.lane_request())?;
        self.verify_storage(ROOTS).await?;
        let row = RootRow {
            hash: bundle.root.id.clone(),
            published_at_ns,
        };
        if let Some(existing) = self.live_cut_root(&key).await? {
            if existing != row {
                return Err(Error::Conflict("live cut root slot fork".into()));
            }
        } else {
            for object in bundle.objects() {
                lease.require(&scope)?;
                self.put_live_cut_object(object, request.lane.maximum_bytes)
                    .await?;
            }
            lease.require(&scope)?;
            self.insert(ROOTS, &[serde_json::json!({"slot_hash":key,"root_hash":row.hash,"published_at_ns":published_at_ns})]).await?;
        }
        lease.require(&scope)?;
        if self.live_cut_root(&key).await?.as_ref() != Some(&row) {
            return Err(Error::Unready("live cut root readback".into()));
        }
        self.load_live_cut_checkpoint(request).await?;
        lease.require(&scope)?;
        Ok(bundle.root.id)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn root_slot_is_immutable_and_sequence_scoped() {
        let context = "a".repeat(64);
        assert_ne!(slot(&context, 1).unwrap(), slot(&context, 2).unwrap());
        assert!(slot("bad", 1).is_err());
        assert!(slot(&context, 0).is_err());
        let one = serde_json::json!({"root_hash":context,"published_at_ns":1});
        assert!(decode_root(&format!("{one}\n{one}")).unwrap().is_some());
        let two = serde_json::json!({"root_hash":"b".repeat(64),"published_at_ns":1});
        assert!(decode_root(&format!("{one}\n{two}")).is_err());
    }
}
