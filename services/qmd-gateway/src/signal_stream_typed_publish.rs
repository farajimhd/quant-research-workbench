//! Typed-only Signal Stream publication protocol. The legacy producer does not
//! call this module. A storage adapter must provide exact, conflict-detecting
//! reads before this can be enabled against ClickHouse/Keeper.

use crate::signal_stream_typed::{
    restore, FieldBinding, ScalarValue, SqueezeEvidence, TypedOccurrence,
};
use ring::digest::{digest, SHA256};
use serde::Serialize;
use std::collections::{HashMap, HashSet};
use std::future::Future;
use std::pin::Pin;

pub const RECEIPT_TABLE_SQL: &str = r#"CREATE TABLE q_live.signal_stream_receipt_v1 (
    schema_version UInt16, session_key String, first_sequence UInt64,
    last_sequence UInt64, owner_id String, lease_epoch UInt64,
    core_count UInt32, field_count UInt32, squeeze_count UInt32,
    catalog_count UInt32, batch_sha256 FixedString(64)
) ENGINE = MergeTree
PARTITION BY session_key
ORDER BY (session_key, first_sequence, last_sequence, batch_sha256)
SETTINGS storage_policy = 'live_market_ssd'"#;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct CoreRow {
    pub schema_version: u16,
    pub session_key: String,
    pub sequence: u64,
    pub event_id: String,
    pub signal_stream_id: String,
    pub signal_stream_name: String,
    pub ticker: String,
    pub event_time: chrono::DateTime<chrono::Utc>,
    pub effective_at: chrono::DateTime<chrono::Utc>,
    pub available_at: chrono::DateTime<chrono::Utc>,
    pub configuration_revision: String,
    pub definition_revision: String,
    pub source_authority: String,
    pub evidence_count: u32,
    pub content_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct FieldRow {
    pub schema_version: u16,
    pub session_key: String,
    pub sequence: u64,
    pub event_id: String,
    pub field_instance_id: String,
    pub value: ScalarValue,
    pub content_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SqueezeRow {
    pub schema_version: u16,
    pub session_key: String,
    pub sequence: u64,
    pub event_id: String,
    pub evidence: SqueezeEvidence,
    pub content_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct ReceiptFence {
    pub schema_version: u16,
    pub session_key: String,
    pub first_sequence: u64,
    pub last_sequence: u64,
    pub owner_id: String,
    pub lease_epoch: u64,
    pub core_count: u32,
    pub field_count: u32,
    pub squeeze_count: u32,
    pub catalog_count: u32,
    pub batch_sha256: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub enum FamilyRows {
    Catalog(Vec<FieldBinding>),
    Fields(Vec<FieldRow>),
    Squeeze(Vec<SqueezeRow>),
    Core(Vec<CoreRow>),
    Receipt(Vec<ReceiptFence>),
}

impl FamilyRows {
    fn is_empty(&self) -> bool {
        match self {
            Self::Catalog(v) => v.is_empty(),
            Self::Fields(v) => v.is_empty(),
            Self::Squeeze(v) => v.is_empty(),
            Self::Core(v) => v.is_empty(),
            Self::Receipt(v) => v.is_empty(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct PreparedBatch {
    pub catalog: FamilyRows,
    pub fields: FamilyRows,
    pub squeeze: FamilyRows,
    pub core: FamilyRows,
    pub receipt: ReceiptFence,
}

fn sha256<T: Serialize>(value: &T) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(digest(&SHA256, &bytes)
        .as_ref()
        .iter()
        .map(|v| format!("{v:02x}"))
        .collect())
}

pub fn prepare_batch(
    rows: &[TypedOccurrence],
    bindings_by_stream: &HashMap<String, Vec<FieldBinding>>,
    owner_id: &str,
    lease_epoch: u64,
) -> Result<PreparedBatch, String> {
    if rows.is_empty() || rows.len() > 5_000 || owner_id.is_empty() || lease_epoch == 0 {
        return Err("typed Signal batch needs bounded rows and a fenced owner".to_string());
    }
    let first = &rows[0];
    let mut catalog = Vec::new();
    let mut fields = Vec::new();
    let mut squeeze = Vec::new();
    let mut core = Vec::new();
    let mut seen_events = HashSet::new();
    let mut seen_catalog = HashSet::new();
    for (index, row) in rows.iter().enumerate() {
        let expected = first
            .sequence
            .checked_add(index as u64)
            .ok_or_else(|| "typed Signal sequence overflow".to_string())?;
        if row.session_key != first.session_key
            || row.sequence != expected
            || !seen_events.insert(&row.event_id)
        {
            return Err(
                "typed Signal batch has mixed session, gap, or duplicate event".to_string(),
            );
        }
        let bindings = bindings_by_stream
            .get(&row.stream_id)
            .ok_or_else(|| format!("typed Signal catalog missing stream {}", row.stream_id))?;
        restore(row, bindings)?;
        for binding in bindings {
            if binding.stream_id != row.stream_id {
                return Err("typed Signal catalog stream mismatch".to_string());
            }
            if seen_catalog.insert(&binding.field_instance_id) {
                catalog.push(binding.clone());
            }
        }
        let evidence_ids: HashSet<&str> = row
            .fields
            .iter()
            .map(|field| field.field_instance_id.as_str())
            .collect();
        if evidence_ids.len() != row.fields.len() {
            return Err("typed Signal occurrence repeats field evidence".to_string());
        }
        for field in &row.fields {
            fields.push(FieldRow {
                schema_version: 1,
                session_key: row.session_key.clone(),
                sequence: row.sequence,
                event_id: row.event_id.clone(),
                field_instance_id: field.field_instance_id.clone(),
                value: field.value.clone(),
                content_sha256: sha256(&field.value)?,
            });
        }
        if let Some(evidence) = &row.squeeze {
            squeeze.push(SqueezeRow {
                schema_version: 1,
                session_key: row.session_key.clone(),
                sequence: row.sequence,
                event_id: row.event_id.clone(),
                evidence: evidence.clone(),
                content_sha256: sha256(evidence)?,
            });
        }
        core.push(CoreRow {
            schema_version: 1,
            session_key: row.session_key.clone(),
            sequence: row.sequence,
            event_id: row.event_id.clone(),
            signal_stream_id: row.stream_id.clone(),
            signal_stream_name: row.stream_name.clone(),
            ticker: row.ticker.clone(),
            event_time: row.event_time,
            effective_at: row.effective_at,
            available_at: row.available_at,
            configuration_revision: row.configuration_revision.clone(),
            definition_revision: row.definition_revision.clone(),
            source_authority: row.source_authority.clone(),
            evidence_count: u32::try_from(row.fields.len()).map_err(|_| "too much evidence")?,
            content_sha256: row.content_sha256.clone(),
        });
    }
    catalog.sort_by(|a, b| a.field_instance_id.cmp(&b.field_instance_id));
    fields.sort_by(|a, b| {
        (a.sequence, &a.event_id, &a.field_instance_id).cmp(&(
            b.sequence,
            &b.event_id,
            &b.field_instance_id,
        ))
    });
    squeeze.sort_by(|a, b| (a.sequence, &a.event_id).cmp(&(b.sequence, &b.event_id)));
    let catalog = FamilyRows::Catalog(catalog);
    let fields = FamilyRows::Fields(fields);
    let squeeze = FamilyRows::Squeeze(squeeze);
    let core = FamilyRows::Core(core);
    let batch_hash = sha256(&(&catalog, &fields, &squeeze, &core))?;
    let receipt = ReceiptFence {
        schema_version: 1,
        session_key: first.session_key.clone(),
        first_sequence: first.sequence,
        last_sequence: rows.last().unwrap().sequence,
        owner_id: owner_id.to_string(),
        lease_epoch,
        core_count: rows.len() as u32,
        field_count: match &fields {
            FamilyRows::Fields(v) => v.len() as u32,
            _ => unreachable!(),
        },
        squeeze_count: match &squeeze {
            FamilyRows::Squeeze(v) => v.len() as u32,
            _ => unreachable!(),
        },
        catalog_count: match &catalog {
            FamilyRows::Catalog(v) => v.len() as u32,
            _ => unreachable!(),
        },
        batch_sha256: batch_hash,
    };
    Ok(PreparedBatch {
        catalog,
        fields,
        squeeze,
        core,
        receipt,
    })
}

pub type StorageFuture<'a, T> = Pin<Box<dyn Future<Output = Result<T, String>> + Send + 'a>>;

/// The storage adapter must hold an exclusive, monotonically fenced session
/// claim. `read_family` must return every row matching the proposed identities;
/// it may not silently collapse conflicting or duplicate physical rows.
/// `last_receipt` must likewise reject conflicting terminal fences rather
/// than choosing one with `FINAL` or an unordered limit.
pub trait TypedPublicationStorage {
    fn verify_lease<'a>(&'a self, receipt: &'a ReceiptFence) -> StorageFuture<'a, ()>;
    fn last_receipt<'a>(&'a self, session_key: &'a str) -> StorageFuture<'a, Option<ReceiptFence>>;
    fn append_family<'a>(&'a self, family: &'a FamilyRows) -> StorageFuture<'a, ()>;
    fn read_family<'a>(&'a self, family: &'a FamilyRows) -> StorageFuture<'a, FamilyRows>;
}

async fn publish_one<S: TypedPublicationStorage>(
    storage: &S,
    expected: &FamilyRows,
) -> Result<(), String> {
    if expected.is_empty() {
        return Ok(());
    }
    let append_result = storage.append_family(expected).await;
    let actual = storage.read_family(expected).await.map_err(|error| {
        format!(
            "typed Signal exact readback failed after append {:?}: {error}",
            append_result.err()
        )
    })?;
    if actual != *expected {
        return Err("typed Signal publication readback is missing or conflicting".to_string());
    }
    Ok(())
}

pub async fn publish_batch<S: TypedPublicationStorage>(
    storage: &S,
    batch: &PreparedBatch,
) -> Result<ReceiptFence, String> {
    storage.verify_lease(&batch.receipt).await?;
    let last = storage.last_receipt(&batch.receipt.session_key).await?;
    if let Some(existing) = &last {
        if existing.first_sequence == batch.receipt.first_sequence
            && existing.last_sequence == batch.receipt.last_sequence
        {
            if existing != &batch.receipt {
                return Err("typed Signal receipt conflicts with the proposed batch".to_string());
            }
            for family in [&batch.catalog, &batch.fields, &batch.squeeze, &batch.core] {
                if !family.is_empty() && storage.read_family(family).await? != *family {
                    return Err(
                        "typed Signal committed receipt has incomplete evidence".to_string()
                    );
                }
            }
            let receipt_rows = FamilyRows::Receipt(vec![batch.receipt.clone()]);
            if storage.read_family(&receipt_rows).await? != receipt_rows {
                return Err("typed Signal committed receipt readback conflicts".to_string());
            }
            return Ok(batch.receipt.clone());
        }
    }
    let next = last
        .as_ref()
        .map(|receipt| receipt.last_sequence)
        .unwrap_or(0)
        .checked_add(1)
        .ok_or_else(|| "typed Signal receipt cursor overflow".to_string())?;
    if batch.receipt.first_sequence != next {
        return Err(format!(
            "typed Signal batch starts at {}, expected {next}",
            batch.receipt.first_sequence
        ));
    }
    for family in [&batch.catalog, &batch.fields, &batch.squeeze, &batch.core] {
        publish_one(storage, family).await?;
    }
    storage.verify_lease(&batch.receipt).await?;
    publish_one(storage, &FamilyRows::Receipt(vec![batch.receipt.clone()])).await?;
    Ok(batch.receipt.clone())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::signal_stream_typed::{bindings, project};
    use serde_json::json;
    use std::sync::Mutex;

    #[derive(Default)]
    struct FakeState {
        catalog: Option<FamilyRows>,
        fields: Option<FamilyRows>,
        squeeze: Option<FamilyRows>,
        core: Option<FamilyRows>,
        receipt: Option<FamilyRows>,
        fail_at: Option<&'static str>,
        commit_before_error: bool,
        conflict_at: Option<&'static str>,
        lease_valid: bool,
    }

    struct FakeStorage(Mutex<FakeState>);

    fn kind(family: &FamilyRows) -> &'static str {
        match family {
            FamilyRows::Catalog(_) => "catalog",
            FamilyRows::Fields(_) => "fields",
            FamilyRows::Squeeze(_) => "squeeze",
            FamilyRows::Core(_) => "core",
            FamilyRows::Receipt(_) => "receipt",
        }
    }

    fn slot<'a>(state: &'a mut FakeState, family: &FamilyRows) -> &'a mut Option<FamilyRows> {
        match family {
            FamilyRows::Catalog(_) => &mut state.catalog,
            FamilyRows::Fields(_) => &mut state.fields,
            FamilyRows::Squeeze(_) => &mut state.squeeze,
            FamilyRows::Core(_) => &mut state.core,
            FamilyRows::Receipt(_) => &mut state.receipt,
        }
    }

    fn empty(family: &FamilyRows) -> FamilyRows {
        match family {
            FamilyRows::Catalog(_) => FamilyRows::Catalog(Vec::new()),
            FamilyRows::Fields(_) => FamilyRows::Fields(Vec::new()),
            FamilyRows::Squeeze(_) => FamilyRows::Squeeze(Vec::new()),
            FamilyRows::Core(_) => FamilyRows::Core(Vec::new()),
            FamilyRows::Receipt(_) => FamilyRows::Receipt(Vec::new()),
        }
    }

    impl TypedPublicationStorage for FakeStorage {
        fn verify_lease<'a>(&'a self, _receipt: &'a ReceiptFence) -> StorageFuture<'a, ()> {
            Box::pin(async {
                if self.0.lock().unwrap().lease_valid {
                    Ok(())
                } else {
                    Err("lease expired".to_string())
                }
            })
        }

        fn last_receipt<'a>(
            &'a self,
            _session: &'a str,
        ) -> StorageFuture<'a, Option<ReceiptFence>> {
            Box::pin(async {
                Ok(match &self.0.lock().unwrap().receipt {
                    Some(FamilyRows::Receipt(rows)) => rows.last().cloned(),
                    _ => None,
                })
            })
        }

        fn append_family<'a>(&'a self, family: &'a FamilyRows) -> StorageFuture<'a, ()> {
            Box::pin(async {
                let mut state = self.0.lock().unwrap();
                let fails = state.fail_at == Some(kind(family));
                if !fails || state.commit_before_error {
                    *slot(&mut state, family) = Some(family.clone());
                }
                if fails {
                    Err("response lost or append failed".to_string())
                } else {
                    Ok(())
                }
            })
        }

        fn read_family<'a>(&'a self, family: &'a FamilyRows) -> StorageFuture<'a, FamilyRows> {
            Box::pin(async {
                let mut state = self.0.lock().unwrap();
                let mut actual = slot(&mut state, family)
                    .clone()
                    .unwrap_or_else(|| empty(family));
                if state.conflict_at == Some(kind(family)) {
                    if let FamilyRows::Core(rows) = &mut actual {
                        if let Some(row) = rows.first_mut() {
                            row.content_sha256 = "0".repeat(64);
                        }
                    }
                }
                Ok(actual)
            })
        }
    }

    fn batch() -> PreparedBatch {
        let stream = json!({"signal_stream_id":"price-squeeze-early", "columns":["price"]});
        let catalog = vec![json!({
            "column_id":"price", "source_id":"market.last_price", "value_type":"number",
            "source_path":"qmd://market", "provenance":"qmd", "query_plan_id":"market-v1",
            "available_at":"QMD publication clock"
        })];
        let binding = bindings(&stream, &catalog).unwrap();
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
        let row = project(&source, &binding).unwrap();
        prepare_batch(
            &[row],
            &HashMap::from([("price-squeeze-early".to_string(), binding)]),
            "owner-1",
            1,
        )
        .unwrap()
    }

    fn storage() -> FakeStorage {
        FakeStorage(Mutex::new(FakeState {
            lease_valid: true,
            ..FakeState::default()
        }))
    }

    #[tokio::test]
    async fn typed_publication_receipt_follows_exact_family_readbacks() {
        let store = storage();
        let prepared = batch();
        let receipt = publish_batch(&store, &prepared).await.unwrap();
        assert_eq!(receipt.last_sequence, 1);
        assert!(matches!(
            store.0.lock().unwrap().receipt,
            Some(FamilyRows::Receipt(_))
        ));
        assert!(RECEIPT_TABLE_SQL.contains("storage_policy = 'live_market_ssd'"));
    }

    #[tokio::test]
    async fn ambiguous_receipt_is_accepted_only_after_exact_readback() {
        for family in ["catalog", "fields", "squeeze", "core", "receipt"] {
            let store = storage();
            store.0.lock().unwrap().fail_at = Some(family);
            store.0.lock().unwrap().commit_before_error = true;
            assert!(publish_batch(&store, &batch()).await.is_ok(), "{family}");
        }
    }

    #[tokio::test]
    async fn partial_or_conflicting_family_never_publishes_receipt() {
        for (fail_at, commit_before_error, conflict_at) in [
            (Some("fields"), false, None),
            (None, false, Some("core")),
            (Some("receipt"), false, None),
        ] {
            let store = storage();
            {
                let mut state = store.0.lock().unwrap();
                state.fail_at = fail_at;
                state.commit_before_error = commit_before_error;
                state.conflict_at = conflict_at;
            }
            assert!(publish_batch(&store, &batch()).await.is_err());
            if fail_at != Some("receipt") {
                assert!(store.0.lock().unwrap().receipt.is_none());
            }
        }
    }

    #[tokio::test]
    async fn publication_rejects_unfenced_or_reused_global_cursor() {
        let store = storage();
        store.0.lock().unwrap().lease_valid = false;
        assert!(publish_batch(&store, &batch()).await.is_err());
        store.0.lock().unwrap().lease_valid = true;
        assert!(publish_batch(&store, &batch()).await.is_ok());
        assert!(publish_batch(&store, &batch()).await.is_ok());
        let mut conflicting = batch();
        conflicting.receipt.batch_sha256 = "0".repeat(64);
        assert!(publish_batch(&store, &conflicting).await.is_err());
    }
}
