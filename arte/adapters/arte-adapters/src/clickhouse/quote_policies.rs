//! Immutable provider eligibility policy publication and pinned startup reads.
use super::*;
use arte_core::{
    config::Acceptance,
    quote_state::eligibility::{Pinned, Policy},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
const TABLE: &str = "quote_eligibility_policies_v1";
const MAX_PAYLOAD: usize = 32 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    record_hash: String,
    payload_json: String,
}
fn validate_key(provider: u16, hash: &str) -> Result<()> {
    if provider == 0
        || hash.len() != 64
        || !hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    {
        return Err(Error::Invalid(
            "quote policy provider or content hash".into(),
        ));
    }
    Ok(())
}
fn validate_record(record: Policy, provider: u16, hash: &str, as_of_ns: u64) -> Result<Pinned> {
    validate_key(provider, hash)?;
    if record.provider != provider || record.available_at_ns > as_of_ns {
        return Err(Error::Unready(
            "quote policy provider mismatch or future availability".into(),
        ));
    }
    Pinned::new(record, hash)
}
fn decode(body: &str, provider: u16, hash: &str, as_of_ns: u64) -> Result<Option<Pinned>> {
    validate_key(provider, hash)?;
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("quote policy response limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict("multiple pinned quote policies".into()));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.record_hash != hash || row.payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Conflict(
                "quote policy key or payload size differs".into(),
            ));
        }
        let policy: Policy = serde_json::from_str(&row.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if serde_json::to_string(&policy).map_err(|e| Error::Serialization(e.to_string()))?
            != row.payload_json
        {
            return Err(Error::Conflict(
                "quote policy payload is not canonical".into(),
            ));
        }
        result = Some(validate_record(policy, provider, hash, as_of_ns)?);
    }
    Ok(result)
}
impl ClickHouse {
    async fn find_quote_policy(
        &self,
        provider: u16,
        hash: &str,
        as_of_ns: u64,
    ) -> Result<Option<Pinned>> {
        validate_key(provider, hash)?;
        self.verify_storage(TABLE).await?;
        let query = format!("SELECT DISTINCT record_hash,payload_json FROM {}.{TABLE} WHERE record_hash='{hash}' LIMIT 2 FORMAT JSONEachRow", self.database);
        decode(
            &self.request(&query, String::new()).await?,
            provider,
            hash,
            as_of_ns,
        )
    }
    /// Startup-only read. Never resolve an implicit latest policy during execution.
    pub async fn load_quote_policy(
        &self,
        provider: u16,
        hash: &str,
        as_of_ns: u64,
    ) -> Result<Pinned> {
        self.find_quote_policy(provider, hash, as_of_ns)
            .await?
            .ok_or_else(|| Error::Unready("pinned quote policy is not persisted".into()))
    }
    /// Source certification is a separate obligation; hashes do not approve codes.
    pub async fn publish_quote_policy(
        &self,
        record: &Policy,
        hash: &str,
        as_of_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Pinned> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "quote policy publication acceptance missing: {required:?}"
                )));
            }
        }
        validate_record(record.clone(), record.provider, hash, as_of_ns)?;
        lease.require(hash)?;
        let payload_json =
            serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("quote policy payload limit".into()));
        }
        if let Some(existing) = self
            .find_quote_policy(record.provider, hash, as_of_ns)
            .await?
        {
            lease.require(hash)?;
            return Ok(existing);
        }
        lease.require(hash)?;
        self.insert(
            TABLE,
            &[serde_json::to_value(Row {
                record_hash: hash.into(),
                payload_json,
            })
            .map_err(|e| Error::Serialization(e.to_string()))?],
        )
        .await?;
        let result = self
            .load_quote_policy(record.provider, hash, as_of_ns)
            .await?;
        lease.require(hash)?;
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> Policy {
        Policy {
            provider: 1,
            valid_from_ns: 100,
            valid_to_ns: 200,
            available_at_ns: 10,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: [1, 2].into(),
            allowed_indicators: [3].into(),
            allow_empty_conditions: true,
            allow_empty_indicators: false,
        }
    }
    #[test]
    fn pinned_readback_rejects_conflicts_future_policies_and_noncanonical_payloads() {
        let policy = policy();
        let hash = arte_core::content_hash(&policy).unwrap();
        let mut row = Row {
            record_hash: hash.clone(),
            payload_json: serde_json::to_string(&policy).unwrap(),
        };
        let body = serde_json::to_string(&row).unwrap();
        assert_eq!(decode(&body, 1, &hash, 10).unwrap().unwrap().hash(), hash);
        assert!(decode("", 1, &hash, 10).unwrap().is_none());
        assert!(decode(&body, 1, &hash, 9).is_err());
        assert!(decode(&body, 2, &hash, 10).is_err());
        assert!(decode(&format!("{body}\n{body}"), 1, &hash, 10).is_err());
        row.record_hash = "b".repeat(64);
        assert!(decode(&serde_json::to_string(&row).unwrap(), 1, &hash, 10).is_err());
        row.record_hash = hash.clone();
        row.payload_json.push(' ');
        assert!(decode(&serde_json::to_string(&row).unwrap(), 1, &hash, 10).is_err());
        let mut changed = policy.clone();
        changed.allow_empty_indicators = true;
        row.payload_json = serde_json::to_string(&changed).unwrap();
        assert!(decode(&serde_json::to_string(&row).unwrap(), 1, &hash, 10).is_err());
        assert!(decode("bad json", 1, &hash, 10).is_err());
        assert!(decode(&"x".repeat(4 * MAX_PAYLOAD + 1), 1, &hash, 10).is_err());
        for invalid in ["", "' OR 1=1", &"A".repeat(64)] {
            assert!(validate_key(1, invalid).is_err());
        }
    }
    #[test]
    fn maximum_supported_policy_fits_bounded_payload() {
        let mut p = policy();
        p.allowed_conditions = (64512..=65535).collect();
        p.allowed_indicators = p.allowed_conditions.clone();
        let hash = arte_core::content_hash(&p).unwrap();
        let payload_json = serde_json::to_string(&p).unwrap();
        assert!(payload_json.len() <= MAX_PAYLOAD);
        let body = serde_json::to_string(&Row {
            record_hash: hash.clone(),
            payload_json,
        })
        .unwrap();
        assert!(decode(&body, 1, &hash, 10).unwrap().is_some());
    }
}
