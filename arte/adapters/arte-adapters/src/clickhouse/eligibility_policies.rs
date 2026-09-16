//! One immutable persistence protocol for provider eligibility policies.
use super::*;
use arte_core::config::Acceptance;
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use std::collections::BTreeSet;
pub(super) const MAX_PAYLOAD: usize = 32 * 1024;

pub(super) trait Contract: Clone + Serialize + DeserializeOwned {
    type Pinned;
    const TABLE: &'static str;
    fn provider(&self) -> u16;
    fn available_at_ns(&self) -> u64;
    fn pin(self, hash: &str) -> Result<Self::Pinned>;
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct Row {
    pub record_hash: String,
    pub payload_json: String,
}
pub(super) fn validate_key(provider: u16, hash: &str) -> Result<()> {
    if provider == 0
        || hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Invalid(
            "eligibility policy provider or content hash".into(),
        ));
    }
    Ok(())
}
fn validate_record<P: Contract>(
    record: P,
    provider: u16,
    hash: &str,
    as_of_ns: u64,
) -> Result<P::Pinned> {
    validate_key(provider, hash)?;
    if record.provider() != provider || record.available_at_ns() > as_of_ns {
        return Err(Error::Unready(
            "eligibility policy provider mismatch or future availability".into(),
        ));
    }
    record.pin(hash)
}
pub(super) fn decode<P: Contract>(
    body: &str,
    provider: u16,
    hash: &str,
    as_of_ns: u64,
) -> Result<Option<P::Pinned>> {
    validate_key(provider, hash)?;
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("eligibility policy response limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict(
                "multiple pinned eligibility policies".into(),
            ));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.record_hash != hash || row.payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Conflict(
                "eligibility policy key or payload size differs".into(),
            ));
        }
        let policy: P = serde_json::from_str(&row.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if serde_json::to_string(&policy).map_err(|e| Error::Serialization(e.to_string()))?
            != row.payload_json
        {
            return Err(Error::Conflict(
                "eligibility policy payload is not canonical".into(),
            ));
        }
        result = Some(validate_record(policy, provider, hash, as_of_ns)?);
    }
    Ok(result)
}
impl ClickHouse {
    async fn find_eligibility_policy<P: Contract>(
        &self,
        provider: u16,
        hash: &str,
        as_of_ns: u64,
    ) -> Result<Option<P::Pinned>> {
        validate_key(provider, hash)?;
        self.verify_storage(P::TABLE).await?;
        let query = format!("SELECT DISTINCT record_hash,payload_json FROM {}.{} WHERE record_hash='{hash}' LIMIT 2 FORMAT JSONEachRow", self.database, P::TABLE);
        decode::<P>(
            &self.request(&query, String::new()).await?,
            provider,
            hash,
            as_of_ns,
        )
    }
    pub(super) async fn load_eligibility_policy<P: Contract>(
        &self,
        provider: u16,
        hash: &str,
        as_of_ns: u64,
    ) -> Result<P::Pinned> {
        self.find_eligibility_policy::<P>(provider, hash, as_of_ns)
            .await?
            .ok_or_else(|| Error::Unready("pinned eligibility policy is not persisted".into()))
    }
    pub(super) async fn publish_eligibility_policy<P: Contract>(
        &self,
        record: &P,
        hash: &str,
        as_of_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<P::Pinned> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "eligibility policy publication acceptance missing: {required:?}"
                )));
            }
        }
        validate_record(record.clone(), record.provider(), hash, as_of_ns)?;
        lease.require(hash)?;
        let payload_json =
            serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("eligibility policy payload limit".into()));
        }
        if let Some(existing) = self
            .find_eligibility_policy::<P>(record.provider(), hash, as_of_ns)
            .await?
        {
            lease.require(hash)?;
            return Ok(existing);
        }
        lease.require(hash)?;
        self.insert(
            P::TABLE,
            &[serde_json::to_value(Row {
                record_hash: hash.into(),
                payload_json,
            })
            .map_err(|e| Error::Serialization(e.to_string()))?],
        )
        .await?;
        let result = self
            .load_eligibility_policy::<P>(record.provider(), hash, as_of_ns)
            .await?;
        lease.require(hash)?;
        Ok(result)
    }
}
