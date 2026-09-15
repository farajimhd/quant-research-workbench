//! Immutable event staging publication. Not the final range-query event codec.
use super::*;
use crate::ownership::job_hash as job_key;
use arte_core::config::Acceptance;
use arte_core::event_storage::{Batch, Manifest, PayloadObject, MAX_OBSERVATIONS};
use arte_core::events::ObservationRef;
use std::collections::BTreeSet;
const PAYLOADS: &str = "event_payload_staging_v1";
const OBSERVATIONS: &str = "event_observation_staging_v1";
const MANIFESTS: &str = "event_batch_staging_v1";
const PAGE: usize = 256;
#[derive(Debug, Clone, PartialEq, Eq)]
struct JobHead {
    revision: u64,
    head_hash: String,
}
fn decode_head(body: &str) -> Result<Option<JobHead>> {
    let mut latest: Option<JobHead> = None;
    for (i, line) in body.lines().filter(|s| !s.trim().is_empty()).enumerate() {
        if i >= 2 {
            return Err(Error::Capacity("job head readback exceeds bound".into()));
        }
        let row: Value = serde_json::from_str(line)
            .map_err(|_| Error::Invalid("invalid job head row".into()))?;
        let revision = row
            .get("revision")
            .and_then(|v| {
                v.as_u64()
                    .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
            })
            .filter(|n| *n > 0)
            .ok_or_else(|| Error::Invalid("invalid job revision".into()))?;
        let hash = row
            .get("head_hash")
            .and_then(Value::as_str)
            .filter(|h| valid_hash(h))
            .ok_or_else(|| Error::Invalid("invalid job head hash".into()))?;
        if let Some(first) = &latest {
            if revision > first.revision || (revision == first.revision && hash != first.head_hash)
            {
                return Err(Error::Conflict(
                    "job head order or revision conflict".into(),
                ));
            }
        } else {
            latest = Some(JobHead {
                revision,
                head_hash: hash.into(),
            });
        }
    }
    Ok(latest)
}
fn advance_head(
    current: Option<&JobHead>,
    id: &str,
    previous: Option<&str>,
    page: usize,
) -> Result<(JobHead, bool)> {
    let revision =
        u64::try_from(page).map_err(|_| Error::Invalid("job revision overflow".into()))?;
    let desired = JobHead {
        revision,
        head_hash: id.into(),
    };
    if revision == 0 || !valid_hash(id) {
        return Err(Error::Invalid("invalid next job head".into()));
    }
    if current == Some(&desired) {
        return Ok((desired, false));
    }
    let expected_revision = current.map_or(Some(1), |head| head.revision.checked_add(1));
    if Some(revision) != expected_revision || current.map(|h| h.head_hash.as_str()) != previous {
        return Err(Error::Conflict(
            "acquisition job head has moved or predecessor is missing".into(),
        ));
    }
    Ok((desired, true))
}
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
    pub async fn checkpoint_acquisition(
        &self,
        acquisition: &mut crate::rest_acquisition::Acquisition,
        job_name: &str,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<String> {
        writer_gate(passed)?;
        let record = acquisition
            .progress_record()
            .ok_or_else(|| Error::Unready("no acquisition progress pending".into()))?;
        let id = record.id()?;
        let job = job_key(job_name, &record.plan_hash)?;
        self.verify_storage("event_acquisition_jobs_v1").await?;
        let current = self.acquisition_head(&job).await?;
        let (desired, append) = advance_head(
            current.as_ref(),
            &id,
            record.previous.as_deref(),
            record.page_number,
        )?;
        let json =
            serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
        self.verify_storage("event_acquisition_progress_v1").await?;
        self.stage_event_values(
            "event_acquisition_progress_v1",
            &BTreeMap::from([(id.clone(), json)]),
        )
        .await?;
        if append {
            self.insert(
                "event_acquisition_jobs_v1",
                &[serde_json::json!({"job_hash":job,"revision":desired.revision,"head_hash":id})],
            )
            .await?;
        }
        if self.acquisition_head(&job).await?.as_ref() != Some(&desired) {
            return Err(Error::Unready(
                "acquisition job head readback differs".into(),
            ));
        }
        acquisition.acknowledge_progress(&id)?;
        Ok(id)
    }
    /// One fenced owner per job is mandatory. This is not a ClickHouse lease/CAS.
    async fn acquisition_head(&self, job_hash: &str) -> Result<Option<JobHead>> {
        if !valid_hash(job_hash) {
            return Err(Error::Invalid("invalid acquisition job hash".into()));
        }
        let sql=format!("SELECT DISTINCT revision,head_hash FROM {}.event_acquisition_jobs_v1 WHERE job_hash='{job_hash}' ORDER BY revision DESC LIMIT 2 FORMAT JSONEachRow",self.database);
        decode_head(&self.request(&sql, String::new()).await?)
    }
    /// Resume only the durable head indexed by this named plan. No matching head
    /// returns the untouched plan; an existing malformed chain is an error.
    pub async fn recover_acquisition_job(
        &self,
        acquisition: crate::rest_acquisition::Acquisition,
        job_name: &str,
        maximum_records: usize,
        maximum_bytes: usize,
    ) -> Result<crate::rest_acquisition::Acquisition> {
        let job = job_key(job_name, &acquisition.plan_hash()?)?;
        match self.acquisition_head(&job).await? {
            Some(head) => {
                let recovered = self
                    .recover_acquisition(
                        acquisition,
                        &head.head_hash,
                        maximum_records,
                        maximum_bytes,
                    )
                    .await?;
                if recovered.completed_pages() as u64 != head.revision {
                    return Err(Error::Conflict(
                        "job revision and recovered page count differ".into(),
                    ));
                }
                Ok(recovered)
            }
            None => Ok(acquisition),
        }
    }
    /// The caller pins the last acknowledged head outside volatile worker state.
    /// Recovered progress does not certify coverage or activate a writer.
    pub async fn recover_acquisition(
        &self,
        acquisition: crate::rest_acquisition::Acquisition,
        head: &str,
        maximum_records: usize,
        maximum_bytes: usize,
    ) -> Result<crate::rest_acquisition::Acquisition> {
        if maximum_records == 0
            || maximum_records > arte_core::acquisition::MAX_PAGES
            || maximum_bytes == 0
            || maximum_bytes > 1024 * 1024 * 1024
        {
            return Err(Error::Invalid("invalid acquisition recovery budget".into()));
        }
        let mut next = Some(head.to_owned());
        let mut records = Vec::new();
        let mut seen = BTreeSet::new();
        let mut bytes = 0usize;
        while let Some(id) = next {
            if records.len() >= maximum_records || !seen.insert(id.clone()) {
                return Err(Error::Capacity(
                    "acquisition recovery bound or cycle".into(),
                ));
            }
            let values = self
                .event_values("event_acquisition_progress_v1", std::slice::from_ref(&id))
                .await?;
            let json = values
                .get(&id)
                .ok_or_else(|| Error::Unready("acquisition progress record missing".into()))?;
            bytes = bytes
                .checked_add(json.len())
                .ok_or_else(|| Error::Capacity("acquisition recovery byte overflow".into()))?;
            if bytes > maximum_bytes {
                return Err(Error::Capacity("acquisition recovery byte budget".into()));
            }
            let record: crate::rest_acquisition::ProgressRecord =
                serde_json::from_str(json).map_err(|e| Error::Serialization(e.to_string()))?;
            if record.id()? != id {
                return Err(Error::Conflict("acquisition progress hash mismatch".into()));
            }
            next = record.previous.clone();
            records.push(record);
        }
        records.reverse();
        acquisition.restore(head, &records)
    }
    /// Revalidates one batch at a time; does not retain the session in memory.
    pub async fn verify_acquisition(
        &self,
        certificate: arte_core::acquisition::Certificate,
    ) -> Result<arte_core::acquisition::VerifiedCertificate> {
        let mut verifier = arte_core::acquisition::Verifier::new(certificate)?;
        while let Some(id) = verifier.next_batch() {
            let batch = self.load_event_batch(id).await?;
            verifier.observe(&batch)?;
        }
        verifier.finish()
    }
    pub async fn publish_acquisition(
        &self,
        certificate: arte_core::acquisition::Certificate,
        passed: &BTreeSet<Acceptance>,
        now_ns: u64,
    ) -> Result<String> {
        writer_gate(passed)?;
        if certificate.published_at_ns > now_ns {
            return Err(Error::Invalid(
                "coverage publication is in the future".into(),
            ));
        }
        let verified = self.verify_acquisition(certificate).await?;
        let id = verified.certificate().id()?;
        self.verify_storage("event_coverage_staging_v1").await?;
        let json = serde_json::to_string(verified.certificate())
            .map_err(|e| Error::Serialization(e.to_string()))?;
        self.stage_event_values(
            "event_coverage_staging_v1",
            &BTreeMap::from([(id.clone(), json)]),
        )
        .await?;
        Ok(id)
    }
    pub async fn load_acquisition(
        &self,
        id: &str,
    ) -> Result<arte_core::acquisition::VerifiedCertificate> {
        let values = self
            .event_values("event_coverage_staging_v1", &[id.to_owned()])
            .await?;
        let json = values
            .get(id)
            .ok_or_else(|| Error::Unready("coverage certificate not published".into()))?;
        let certificate: arte_core::acquisition::Certificate =
            serde_json::from_str(json).map_err(|e| Error::Serialization(e.to_string()))?;
        if certificate.id()? != id {
            return Err(Error::Conflict("coverage certificate hash mismatch".into()));
        }
        self.verify_acquisition(certificate).await
    }
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
    fn job_head_retry_is_idempotent_but_fork_or_skipped_page_fails() {
        let id = "a".repeat(64);
        let next = "b".repeat(64);
        let (head, append) = advance_head(None, &id, None, 1).unwrap();
        assert!(append);
        assert!(!advance_head(Some(&head), &id, None, 1).unwrap().1);
        assert!(advance_head(Some(&head), &next, Some(&id), 2).unwrap().1);
        assert!(advance_head(Some(&head), &next, None, 2).is_err());
        assert!(advance_head(Some(&head), &next, Some(&id), 3).is_err());
    }
    #[test]
    fn latest_job_head_rejects_conflicts_before_background_merges() {
        let a = "a".repeat(64);
        let b = "b".repeat(64);
        let row = serde_json::json!({"revision":"2","head_hash":a}).to_string();
        assert_eq!(
            decode_head(&format!("{row}\n{row}"))
                .unwrap()
                .unwrap()
                .revision,
            2
        );
        let conflicting = serde_json::json!({"revision":2,"head_hash":b}).to_string();
        assert!(decode_head(&format!("{row}\n{conflicting}")).is_err());
        assert_ne!(job_key("job", &a).unwrap(), job_key("job", &b).unwrap());
        assert!(job_key("job'; DROP TABLE x", &a).is_err());
    }
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
