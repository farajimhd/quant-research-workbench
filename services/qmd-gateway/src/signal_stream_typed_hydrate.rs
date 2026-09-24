//! Receipt-bounded read-only hydration for the inactive typed Signal Stream.
//! A concrete client must return all physical rows in each range, including
//! conflicts, instead of selecting arbitrary MergeTree rows with LIMIT/FINAL.

use crate::signal_stream_typed::{replay_page, FieldBinding, FieldEvidence, TypedOccurrence};
use crate::signal_stream_typed_publish::{
    CoreRow, FieldRow, ReceiptFence, SqueezeRow, StorageFuture,
};
use ring::digest::{digest, SHA256};
use serde::Serialize;
use serde_json::Value;
use std::collections::{HashMap, HashSet};

#[derive(Clone, Debug)]
pub struct BatchReadback {
    pub catalog: Vec<FieldBinding>,
    pub fields: Vec<FieldRow>,
    pub squeeze: Vec<SqueezeRow>,
    pub core: Vec<CoreRow>,
}

pub trait TypedHydrationStorage {
    /// Return the next committed receipt in sequence order, rejecting
    /// conflicting receipts for the same range or sequence.
    fn next_receipt<'a>(
        &'a self,
        session_key: &'a str,
        after_sequence: u64,
    ) -> StorageFuture<'a, Option<ReceiptFence>>;

    /// Read every physical row addressed by this receipt. A missing family,
    /// duplicate identity, or conflicting row must remain visible to hydration.
    fn read_batch<'a>(&'a self, receipt: &'a ReceiptFence) -> StorageFuture<'a, BatchReadback>;
}

fn sha256<T: Serialize>(value: &T) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(digest(&SHA256, &bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn count(actual: usize, expected: u32, family: &str) -> Result<(), String> {
    if actual != expected as usize {
        Err(format!(
            "typed Signal {family} count mismatch: {actual} != {expected}"
        ))
    } else {
        Ok(())
    }
}

pub async fn hydrate_next<S: TypedHydrationStorage>(
    storage: &S,
    session_key: &str,
    after_sequence: u64,
    limit: usize,
) -> Result<(Vec<Value>, u64), String> {
    if !(1..=5_000).contains(&limit) {
        return Err("typed Signal hydration limit must be 1..=5000".to_string());
    }
    let Some(receipt) = storage.next_receipt(session_key, after_sequence).await? else {
        return Ok((Vec::new(), after_sequence));
    };
    let next = after_sequence
        .checked_add(1)
        .ok_or_else(|| "typed Signal hydration cursor overflow".to_string())?;
    if receipt.schema_version != 1
        || receipt.session_key != session_key
        || receipt.first_sequence != next
        || receipt.last_sequence < next
        || receipt.core_count == 0
        || receipt.core_count as usize > limit
        || receipt.last_sequence - receipt.first_sequence + 1 != receipt.core_count as u64
        || receipt.owner_id.is_empty()
        || receipt.lease_epoch == 0
    {
        return Err("typed Signal hydration receipt is invalid or has a cursor gap".to_string());
    }
    let mut batch = storage.read_batch(&receipt).await?;
    count(batch.catalog.len(), receipt.catalog_count, "catalog")?;
    count(batch.fields.len(), receipt.field_count, "field")?;
    count(batch.squeeze.len(), receipt.squeeze_count, "squeeze")?;
    count(batch.core.len(), receipt.core_count, "core")?;
    batch
        .catalog
        .sort_by(|a, b| a.field_instance_id.cmp(&b.field_instance_id));
    batch.fields.sort_by(|a, b| {
        (a.sequence, &a.event_id, &a.field_instance_id).cmp(&(
            b.sequence,
            &b.event_id,
            &b.field_instance_id,
        ))
    });
    batch
        .squeeze
        .sort_by(|a, b| (a.sequence, &a.event_id).cmp(&(b.sequence, &b.event_id)));
    batch
        .core
        .sort_by(|a, b| (a.sequence, &a.event_id).cmp(&(b.sequence, &b.event_id)));
    let hash = sha256(&(
        crate::signal_stream_typed_publish::FamilyRows::Catalog(batch.catalog.clone()),
        crate::signal_stream_typed_publish::FamilyRows::Fields(batch.fields.clone()),
        crate::signal_stream_typed_publish::FamilyRows::Squeeze(batch.squeeze.clone()),
        crate::signal_stream_typed_publish::FamilyRows::Core(batch.core.clone()),
    ))?;
    if hash != receipt.batch_sha256 {
        return Err("typed Signal hydration batch hash mismatch".to_string());
    }
    let mut catalogs: HashMap<String, Vec<FieldBinding>> = HashMap::new();
    for core in &batch.core {
        catalogs.entry(core.signal_stream_id.clone()).or_default();
    }
    let mut catalog_ids = HashSet::new();
    for binding in batch.catalog {
        if !catalog_ids.insert(binding.field_instance_id.clone()) {
            return Err("typed Signal hydration duplicate catalog binding".to_string());
        }
        catalogs
            .entry(binding.stream_id.clone())
            .or_default()
            .push(binding);
    }
    let mut evidence: HashMap<(u64, String), Vec<FieldEvidence>> = HashMap::new();
    for field in batch.fields {
        if field.schema_version != 1
            || field.session_key != session_key
            || sha256(&field.value)? != field.content_sha256
        {
            return Err("typed Signal hydration field evidence is invalid".to_string());
        }
        evidence
            .entry((field.sequence, field.event_id))
            .or_default()
            .push(FieldEvidence {
                field_instance_id: field.field_instance_id,
                value: field.value,
            });
    }
    let mut squeeze = HashMap::new();
    for row in batch.squeeze {
        if row.schema_version != 1
            || row.session_key != session_key
            || sha256(&row.evidence)? != row.content_sha256
            || squeeze
                .insert((row.sequence, row.event_id), row.evidence)
                .is_some()
        {
            return Err("typed Signal hydration squeeze evidence is invalid".to_string());
        }
    }
    let mut rows = Vec::new();
    for core in batch.core {
        if core.schema_version != 1 || core.session_key != session_key {
            return Err("typed Signal hydration core identity is invalid".to_string());
        }
        let key = (core.sequence, core.event_id.clone());
        let fields = evidence.remove(&key).unwrap_or_default();
        if fields.len() != core.evidence_count as usize {
            return Err("typed Signal hydration core evidence count mismatch".to_string());
        }
        rows.push(TypedOccurrence {
            session_key: core.session_key,
            sequence: core.sequence,
            event_id: core.event_id,
            stream_id: core.signal_stream_id,
            stream_name: core.signal_stream_name,
            ticker: core.ticker,
            event_time: core.event_time,
            effective_at: core.effective_at,
            available_at: core.available_at,
            configuration_revision: core.configuration_revision,
            definition_revision: core.definition_revision,
            source_authority: core.source_authority,
            fields,
            squeeze: squeeze.remove(&key),
            content_sha256: core.content_sha256,
        });
    }
    if !evidence.is_empty() || !squeeze.is_empty() {
        return Err("typed Signal hydration found orphan child evidence".to_string());
    }
    replay_page(session_key, after_sequence, limit, &rows, &catalogs)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::signal_stream_typed::{bindings, project};
    use crate::signal_stream_typed_publish::{prepare_batch, FamilyRows, PreparedBatch};
    use serde_json::json;

    struct FakeReader {
        receipt: Option<ReceiptFence>,
        batch: BatchReadback,
    }

    impl TypedHydrationStorage for FakeReader {
        fn next_receipt<'a>(
            &'a self,
            _session: &'a str,
            _after: u64,
        ) -> StorageFuture<'a, Option<ReceiptFence>> {
            Box::pin(async { Ok(self.receipt.clone()) })
        }

        fn read_batch<'a>(
            &'a self,
            _receipt: &'a ReceiptFence,
        ) -> StorageFuture<'a, BatchReadback> {
            Box::pin(async { Ok(self.batch.clone()) })
        }
    }

    fn fixture() -> (FakeReader, Value) {
        let stream = json!({"signal_stream_id":"price-squeeze-early", "columns":["price"]});
        let catalog = vec![json!({
            "column_id":"price", "source_id":"market.last_price", "value_type":"number",
            "source_path":"qmd://market", "provenance":"qmd", "query_plan_id":"market-v1",
            "available_at":"QMD publication clock"
        })];
        let bindings = bindings(&stream, &catalog).unwrap();
        let source = json!({
            "schema_version":2, "event_id":"early-1", "signal_id":"early-1",
            "signal_stream_id":"price-squeeze-early", "signal_stream_name":"Early Squeeze",
            "ticker":"ABC", "symbol":"ABC", "sequence":1,
            "event_time":"2026-08-17T15:00:00+00:00",
            "effective_at":"2026-08-17T15:00:00+00:00",
            "available_at":"2026-08-17T15:00:00+00:00",
            "session_key":"2026-08-17", "configuration_revision":"config-1",
            "definition_revision":"definition-1", "source_authority":"qmd_event_time_squeeze_episode",
            "price":10.25, "squeeze_episode_id":"episode-1", "squeeze_episode_role":"start",
            "squeeze_episode_started_at":"2026-08-17T15:00:00+00:00",
            "squeeze_episode_expires_at":"2026-08-17T15:05:00+00:00",
            "squeeze_anchor_price":10.0, "squeeze_move_pct":2.5,
            "squeeze_high_water_pct":2.5
        });
        let projected = project(&source, &bindings).unwrap();
        let prepared = prepare_batch(
            &[projected],
            &HashMap::from([("price-squeeze-early".to_string(), bindings)]),
            "owner-1",
            1,
        )
        .unwrap();
        (reader(prepared), source)
    }

    fn reader(prepared: PreparedBatch) -> FakeReader {
        let FamilyRows::Catalog(catalog) = prepared.catalog else {
            unreachable!()
        };
        let FamilyRows::Fields(fields) = prepared.fields else {
            unreachable!()
        };
        let FamilyRows::Squeeze(squeeze) = prepared.squeeze else {
            unreachable!()
        };
        let FamilyRows::Core(core) = prepared.core else {
            unreachable!()
        };
        FakeReader {
            receipt: Some(prepared.receipt),
            batch: BatchReadback {
                catalog,
                fields,
                squeeze,
                core,
            },
        }
    }

    #[tokio::test]
    async fn receipt_hydration_restores_complete_typed_occurrence() {
        let (reader, source) = fixture();
        let (rows, cursor) = hydrate_next(&reader, "2026-08-17", 0, 10).await.unwrap();
        assert_eq!(rows, vec![source]);
        assert_eq!(cursor, 1);
    }

    #[tokio::test]
    async fn receipt_hydration_rejects_missing_conflicting_or_gapped_rows() {
        let (mut reader, _) = fixture();
        reader.batch.fields.clear();
        assert!(hydrate_next(&reader, "2026-08-17", 0, 10).await.is_err());
        let (mut reader, _) = fixture();
        reader.batch.core[0].ticker = "XYZ".to_string();
        assert!(hydrate_next(&reader, "2026-08-17", 0, 10).await.is_err());
        let (reader, _) = fixture();
        assert!(hydrate_next(&reader, "2026-08-17", 1, 10).await.is_err());
    }

    #[tokio::test]
    async fn missing_receipt_does_not_expose_partial_rows() {
        let (mut reader, _) = fixture();
        reader.receipt = None;
        let (rows, cursor) = hydrate_next(&reader, "2026-08-17", 0, 10).await.unwrap();
        assert!(rows.is_empty());
        assert_eq!(cursor, 0);
    }
}
