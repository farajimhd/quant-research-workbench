//! Content-addressed reference records. No mutable latest-row selection.
use super::*;
use arte_core::{
    config::Acceptance,
    event_order::Scope,
    reference_data::{PreviousClose, PreviousCloseRequirement},
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
const TABLE: &str = "previous_close_records_v1";
const MAX_PAYLOAD: usize = 4096;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    record_hash: String,
    payload_json: String,
}
fn decode(
    body: &str,
    scope: Scope,
    requirement: &PreviousCloseRequirement,
    now_ns: u64,
) -> Result<Option<PreviousClose>> {
    requirement.validate(scope)?;
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("reference readback byte limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict("multiple pinned reference rows".into()));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.record_hash != requirement.record_hash || row.payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Conflict(
                "reference readback key or size mismatch".into(),
            ));
        }
        let record: PreviousClose = serde_json::from_str(&row.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        record.require(scope, requirement, now_ns)?;
        if serde_json::to_string(&record).map_err(|e| Error::Serialization(e.to_string()))?
            != row.payload_json
        {
            return Err(Error::Conflict("reference payload is not canonical".into()));
        }
        result = Some(record);
    }
    Ok(result)
}
impl ClickHouse {
    async fn find_previous_close(
        &self,
        scope: Scope,
        requirement: &PreviousCloseRequirement,
        now_ns: u64,
    ) -> Result<Option<PreviousClose>> {
        requirement.validate(scope)?;
        self.verify_storage(TABLE).await?;
        let query = format!("SELECT DISTINCT record_hash,payload_json FROM {}.{TABLE} WHERE record_hash='{}' LIMIT 2 FORMAT JSONEachRow", self.database, requirement.record_hash);
        decode(
            &self.request(&query, String::new()).await?,
            scope,
            requirement,
            now_ns,
        )
    }
    pub async fn load_previous_close(
        &self,
        scope: Scope,
        requirement: &PreviousCloseRequirement,
        now_ns: u64,
    ) -> Result<PreviousClose> {
        self.find_previous_close(scope, requirement, now_ns)
            .await?
            .ok_or_else(|| Error::Unready("pinned previous close not persisted".into()))
    }
    /// Immutable insertion plus verified readback. Caller must have separately
    /// certified the source manifest; a stored record alone is not certification.
    #[allow(clippy::too_many_arguments)]
    pub async fn publish_previous_close(
        &self,
        record: &PreviousClose,
        scope: Scope,
        requirement: &PreviousCloseRequirement,
        now_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<PreviousClose> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "reference publication acceptance missing: {required:?}"
                )));
            }
        }
        record.require(scope, requirement, now_ns)?;
        lease.require(&requirement.record_hash)?;
        let payload_json =
            serde_json::to_string(record).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("reference payload byte limit".into()));
        }
        if let Some(existing) = self.find_previous_close(scope, requirement, now_ns).await? {
            return Ok(existing);
        }
        let row = Row {
            record_hash: requirement.record_hash.clone(),
            payload_json,
        };
        self.insert(
            TABLE,
            &[serde_json::to_value(row).map_err(|e| Error::Serialization(e.to_string()))?],
        )
        .await?;
        self.load_previous_close(scope, requirement, now_ns).await
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn reference_readback_rejects_conflicts_future_data_and_corruption() {
        let scope = Scope {
            provider: 1,
            instrument: 1,
            session: 20260915,
        };
        let record = PreviousClose {
            provider: 1,
            instrument: 1,
            session: 20260914,
            price: arte_core::events::Decimal {
                atoms: 10,
                scale: 0,
            },
            available_at_ns: 10,
            source_manifest_hash: "a".repeat(64),
        };
        let requirement = PreviousCloseRequirement {
            session: record.session,
            record_hash: arte_core::content_hash(&record).unwrap(),
        };
        let mut row = Row {
            record_hash: requirement.record_hash.clone(),
            payload_json: serde_json::to_string(&record).unwrap(),
        };
        let body = serde_json::to_string(&row).unwrap();
        assert!(decode("", scope, &requirement, 10).unwrap().is_none());
        assert_eq!(
            decode(&body, scope, &requirement, 10)
                .unwrap()
                .unwrap()
                .price,
            record.price
        );
        assert!(decode(&body, scope, &requirement, 9).is_err());
        assert!(decode(&format!("{body}\n{body}"), scope, &requirement, 10).is_err());
        row.record_hash = "b".repeat(64);
        assert!(decode(
            &serde_json::to_string(&row).unwrap(),
            scope,
            &requirement,
            10
        )
        .is_err());
        row.record_hash = requirement.record_hash.clone();
        row.payload_json.push(' ');
        assert!(decode(
            &serde_json::to_string(&row).unwrap(),
            scope,
            &requirement,
            10
        )
        .is_err());
        assert!(decode("not-json", scope, &requirement, 10).is_err());
        assert!(decode(&"x".repeat(4 * MAX_PAYLOAD + 1), scope, &requirement, 10).is_err());
    }
}
