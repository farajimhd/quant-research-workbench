//! Immutable trade policy publication and exact-hash startup reads.
use super::eligibility_policies::Contract;
use super::*;
use arte_core::{
    config::Acceptance,
    trade_eligibility::{Pinned, Policy},
};
use std::collections::BTreeSet;
impl Contract for Policy {
    type Pinned = Pinned;
    const TABLE: &'static str = "trade_eligibility_policies_v1";
    fn provider(&self) -> u16 {
        self.provider
    }
    fn available_at_ns(&self) -> u64 {
        self.available_at_ns
    }
    fn pin(self, hash: &str) -> Result<Pinned> {
        Pinned::new(self, hash)
    }
}
impl ClickHouse {
    /// Startup-only read. Never select an implicit latest policy.
    pub async fn load_trade_policy(
        &self,
        provider: u16,
        hash: &str,
        as_of_ns: u64,
    ) -> Result<Pinned> {
        self.load_eligibility_policy::<Policy>(provider, hash, as_of_ns)
            .await
    }
    /// Source certification remains separate; hashes do not approve condition codes.
    pub async fn publish_trade_policy(
        &self,
        record: &Policy,
        hash: &str,
        as_of_ns: u64,
        passed: &BTreeSet<Acceptance>,
        lease: &mut crate::ownership::Lease,
    ) -> Result<Pinned> {
        self.publish_eligibility_policy(record, hash, as_of_ns, passed, lease)
            .await
    }
}
#[cfg(test)]
use super::eligibility_policies::{validate_key, Row, MAX_PAYLOAD};
#[cfg(test)]
fn decode(body: &str, provider: u16, hash: &str, as_of_ns: u64) -> Result<Option<Pinned>> {
    super::eligibility_policies::decode::<Policy>(body, provider, hash, as_of_ns)
}
#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> Policy {
        Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 100,
            valid_to_ns: 200,
            available_at_ns: 10,
            source_manifest_hash: "a".repeat(64),
            allowed_conditions: [1, 2].into(),
            excluded_conditions: [3].into(),
            allow_empty_conditions: true,
        }
    }
    #[test]
    fn pinned_readback_rejects_conflicts_future_policies_and_noncanonical_payloads() {
        let policy = policy();
        let hash = policy.hash().unwrap();
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
        changed.allow_empty_conditions = false;
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
        p.excluded_conditions.clear();
        let hash = p.hash().unwrap();
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
