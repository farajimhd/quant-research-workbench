//! Immutable event staging publication. Not the final range-query event codec.
use super::*;
use arte_core::config::Acceptance;
use arte_core::event_storage::{Batch, Manifest, PayloadObject, MAX_OBSERVATIONS};
use arte_core::events::ObservationRef;
use std::collections::BTreeSet;
const PAYLOADS: &str = "event_payload_staging_v1";
const OBSERVATIONS: &str = "event_observation_staging_v1";
const MANIFESTS: &str = "event_batch_staging_v1";
const PAGE: usize = 256;
fn writer_gate(passed: &BTreeSet<Acceptance>) -> Result<()> {
    for required in [
        Acceptance::RepositoryExtracted,
        Acceptance::SourceIdentity,
        Acceptance::EventStorage,
        Acceptance::Durability,
    ] {
        if !passed.contains(&required) {
            return Err(Error::Unready(format!(
                "event publication acceptance missing: {required:?}"
            )));
        }
    }
    Ok(())
}
fn valid_hash(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn decode_rows(body: &str, requested: &[String]) -> Result<BTreeMap<String, String>> {
    let allowed: BTreeSet<_> = requested.iter().collect();
    let mut values = BTreeMap::new();
    for (count, line) in body.lines().filter(|s| !s.trim().is_empty()).enumerate() {
        if count >= requested.len() * 2 {
            return Err(Error::Capacity("immutable event readback row limit".into()));
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("invalid event readback".into()))?;
        let key = row
            .get("object_hash")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("event hash missing".into()))?
            .to_string();
        let value = row
            .get("value_json")
            .and_then(Value::as_str)
            .ok_or_else(|| Error::Invalid("event value missing".into()))?
            .to_string();
        if !allowed.contains(&key) {
            return Err(Error::Conflict("foreign event readback identity".into()));
        }
        if values
            .insert(key, value.clone())
            .is_some_and(|old| old != value)
        {
            return Err(Error::Conflict("immutable event object conflicts".into()));
        }
    }
    Ok(values)
}
impl ClickHouse {
    async fn event_values(&self, table: &str, ids: &[String]) -> Result<BTreeMap<String, String>> {
        if ids.is_empty() || ids.len() > MAX_OBSERVATIONS || ids.iter().any(|id| !valid_hash(id)) {
            return Err(Error::Invalid("invalid bounded event object query".into()));
        }
        identifier(table)?;
        let mut values = BTreeMap::new();
        let mut bytes = 0usize;
        for page in ids.chunks(PAGE) {
            let keys = page
                .iter()
                .map(|id| format!("'{id}'"))
                .collect::<Vec<_>>()
                .join(",");
            let sql=format!("SELECT DISTINCT object_hash,value_json FROM {}.{table} WHERE object_hash IN ({keys}) LIMIT {} FORMAT JSONEachRow", self.database,2*page.len()+1);
            let body = self.request(&sql, String::new()).await?;
            bytes = bytes
                .checked_add(body.len())
                .ok_or_else(|| Error::Capacity("event readback byte overflow".into()))?;
            if bytes > arte_core::event_storage::MAX_BYTES * 3 {
                return Err(Error::Capacity("event readback byte limit".into()));
            }
            values.extend(decode_rows(&body, page)?);
        }
        Ok(values)
    }
    async fn stage_event_values(
        &self,
        table: &str,
        values: &BTreeMap<String, String>,
    ) -> Result<()> {
        let ids: Vec<_> = values.keys().cloned().collect();
        let existing = self.event_values(table, &ids).await?;
        for (id, value) in &existing {
            if values.get(id) != Some(value) {
                return Err(Error::Conflict("stored event object differs".into()));
            }
        }
        let missing: Vec<_> = values
            .iter()
            .filter(|(id, _)| !existing.contains_key(*id))
            .collect();
        for page in missing.chunks(PAGE) {
            let rows: Vec<Value> = page
                .iter()
                .map(|(id, value)| serde_json::json!({"object_hash":id,"value_json":value}))
                .collect();
            self.insert(table, &rows).await?;
        }
        if self.event_values(table, &ids).await? != *values {
            return Err(Error::Unready(
                "event staging readback incomplete or different".into(),
            ));
        }
        Ok(())
    }
    /// Supervisor must validate actual evidence behind each acceptance and own the
    /// publication lane. A set of enum values is not itself an evidence certificate.
    /// No table creation, connection loop, or automatic retry is performed here.
    pub async fn publish_event_batch(
        &self,
        batch: &Batch,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<String> {
        writer_gate(passed)?;
        batch.verify_readback(batch.payloads(), batch.observations())?;
        for table in [PAYLOADS, OBSERVATIONS, MANIFESTS] {
            self.verify_storage(table).await?;
        }
        let payloads: BTreeMap<_, _> = batch
            .payloads()
            .iter()
            .map(|object| {
                Ok((
                    object.hash.clone(),
                    serde_json::to_string(&object.payload)
                        .map_err(|e| Error::Serialization(e.to_string()))?,
                ))
            })
            .collect::<Result<_>>()?;
        let observations: BTreeMap<_, _> = batch
            .observations()
            .iter()
            .map(|r| {
                Ok((
                    arte_core::content_hash(r)?,
                    serde_json::to_string(r).map_err(|e| Error::Serialization(e.to_string()))?,
                ))
            })
            .collect::<Result<_>>()?;
        self.stage_event_values(PAYLOADS, &payloads).await?;
        self.stage_event_values(OBSERVATIONS, &observations).await?;
        let id = batch.id()?;
        // Publication marker is last; interrupted staging remains invisible to readers.
        self.stage_event_values(
            MANIFESTS,
            &BTreeMap::from([(
                id.clone(),
                serde_json::to_string(batch.manifest())
                    .map_err(|e| Error::Serialization(e.to_string()))?,
            )]),
        )
        .await?;
        self.load_event_batch(&id).await?;
        Ok(id)
    }
    pub async fn load_event_batch(&self, id: &str) -> Result<Batch> {
        let ids = vec![id.to_owned()];
        let manifests = self.event_values(MANIFESTS, &ids).await?;
        let serialized = manifests
            .get(id)
            .ok_or_else(|| Error::Unready("event batch not published".into()))?;
        let manifest: Manifest =
            serde_json::from_str(serialized).map_err(|e| Error::Serialization(e.to_string()))?;
        if arte_core::content_hash(&manifest)? != id {
            return Err(Error::Conflict("event batch manifest hash mismatch".into()));
        }
        let payloads: Vec<PayloadObject> = self
            .event_values(PAYLOADS, &manifest.payload_hashes)
            .await?
            .into_iter()
            .map(|(hash, json)| {
                Ok(PayloadObject {
                    hash,
                    payload: serde_json::from_str(&json)
                        .map_err(|e| Error::Serialization(e.to_string()))?,
                })
            })
            .collect::<Result<_>>()?;
        let references = self
            .event_values(OBSERVATIONS, &manifest.observation_hashes)
            .await?;
        let observations: Vec<ObservationRef> = references
            .into_iter()
            .map(|(hash, json)| {
                let r: ObservationRef =
                    serde_json::from_str(&json).map_err(|e| Error::Serialization(e.to_string()))?;
                if arte_core::content_hash(&r)? != hash {
                    return Err(Error::Conflict("stored observation hash mismatch".into()));
                }
                Ok(r)
            })
            .collect::<Result<_>>()?;
        Batch::restore(id, manifest, &payloads, &observations)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn publisher_requires_every_explicit_acceptance() {
        let mut gates = BTreeSet::new();
        for gate in [
            Acceptance::RepositoryExtracted,
            Acceptance::SourceIdentity,
            Acceptance::EventStorage,
            Acceptance::Durability,
        ] {
            assert!(writer_gate(&gates).is_err());
            gates.insert(gate);
        }
        writer_gate(&gates).unwrap();
    }
    #[test]
    fn bulk_readback_rejects_conflicts_and_unrequested_keys() {
        let ids = vec!["a".repeat(64)];
        let row = serde_json::json!({"object_hash":ids[0],"value_json":"same"}).to_string();
        assert_eq!(
            decode_rows(&format!("{row}\n{row}"), &ids).unwrap().len(),
            1
        );
        let other = serde_json::json!({"object_hash":ids[0],"value_json":"different"}).to_string();
        assert!(decode_rows(&format!("{row}\n{other}"), &ids).is_err());
        assert!(decode_rows(&row, &["b".repeat(64)]).is_err());
        assert!(!valid_hash("'; DROP TABLE x"));
    }
}
