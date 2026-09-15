//! Immutable order slots. Requires cooperative account ownership before writes.
use super::*;
use arte_core::{config::Acceptance, orders::Authorization};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const TABLE: &str = "order_authorizations_v2";
const SUBMISSION_TABLE: &str = "order_submissions_v1";
const MAX_PAYLOAD: usize = 16 * 1024;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Row {
    order_key: String,
    envelope_hash: String,
    payload_json: String,
}

fn decode(body: &str, expected: &Authorization) -> Result<Option<Authorization>> {
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("order readback byte limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict(
                "multiple order authorization versions".into(),
            ));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.payload_json.len() > MAX_PAYLOAD
            || row.order_key != expected.key()?
            || row.envelope_hash != expected.hash()?
        {
            return Err(Error::Conflict(
                "order authorization key or hash differs".into(),
            ));
        }
        let authorization: Authorization = serde_json::from_str(&row.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if authorization != *expected
            || serde_json::to_string(&authorization)
                .map_err(|e| Error::Serialization(e.to_string()))?
                != row.payload_json
        {
            return Err(Error::Conflict(
                "order authorization payload differs or is noncanonical".into(),
            ));
        }
        result = Some(authorization);
    }
    Ok(result)
}

impl ClickHouse {
    async fn find_order_authorization(
        &self,
        expected: &Authorization,
    ) -> Result<Option<Authorization>> {
        self.verify_storage(TABLE).await?;
        let query = format!("SELECT DISTINCT order_key,envelope_hash,payload_json FROM {}.{TABLE} WHERE order_key='{}' LIMIT 2 FORMAT JSONEachRow", self.database, expected.key()?);
        decode(&self.request(&query, String::new()).await?, expected)
    }
    async fn append_order_authorization(
        &self,
        authorization: &Authorization,
    ) -> Result<Authorization> {
        let payload_json = serde_json::to_string(authorization)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("order authorization byte limit".into()));
        }
        if let Some(existing) = self.find_order_authorization(authorization).await? {
            return Ok(existing);
        }
        let row = Row {
            order_key: authorization.key()?,
            envelope_hash: authorization.hash()?,
            payload_json,
        };
        self.insert(
            TABLE,
            &[serde_json::to_value(row).map_err(|e| Error::Serialization(e.to_string()))?],
        )
        .await?;
        self.find_order_authorization(authorization)
            .await?
            .ok_or_else(|| Error::Unready("order authorization readback missing".into()))
    }
}

/// Construction has no network effects. Storage policy and actual part placement
/// are verified on publication. This lease is not distributed failover fencing.
pub struct OrderPublisher<'a> {
    database: &'a ClickHouse,
    lease: &'a mut crate::ownership::Lease,
    account: String,
    ownership_hash: String,
}
impl<'a> OrderPublisher<'a> {
    pub fn ownership_hash(account: &str) -> Result<String> {
        if account.is_empty() || account.len() > 128 {
            return Err(Error::Invalid("order account identity".into()));
        }
        arte_core::content_hash(&("arte.order-account.v1", account))
    }
    pub fn new(
        database: &'a ClickHouse,
        lease: &'a mut crate::ownership::Lease,
        account: &str,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<Self> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "order publication acceptance missing: {required:?}"
                )));
            }
        }
        let ownership_hash = Self::ownership_hash(account)?;
        lease.require(&ownership_hash)?;
        Ok(Self {
            database,
            lease,
            account: account.into(),
            ownership_hash,
        })
    }
}
impl crate::order_journal::Publisher for OrderPublisher<'_> {
    async fn append(&mut self, authorization: &Authorization) -> Result<Authorization> {
        self.lease.require(&self.ownership_hash)?;
        if authorization.bracket.account != self.account {
            return Err(Error::Conflict("order writer account mismatch".into()));
        }
        self.database
            .append_order_authorization(authorization)
            .await
    }
}
fn decode_submission(
    body: &str,
    expected: &arte_core::orders::submission::Marker,
) -> Result<Option<arte_core::orders::submission::Marker>> {
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("submission readback byte limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict(
                "multiple submission marker versions".into(),
            ));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        let canonical =
            serde_json::to_string(expected).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.order_key != expected.order_key
            || row.envelope_hash != expected.hash()?
            || row.payload_json != canonical
        {
            return Err(Error::Conflict("submission marker readback differs".into()));
        }
        result = Some(expected.clone());
    }
    Ok(result)
}
impl ClickHouse {
    async fn find_submission(
        &self,
        expected: &arte_core::orders::submission::Marker,
    ) -> Result<Option<arte_core::orders::submission::Marker>> {
        self.verify_storage(SUBMISSION_TABLE).await?;
        // The caller binds order_key to the SHA-256 authorization slot before I/O.
        let query = format!("SELECT DISTINCT order_key,envelope_hash,payload_json FROM {}.{SUBMISSION_TABLE} WHERE order_key='{}' LIMIT 2 FORMAT JSONEachRow", self.database, expected.order_key);
        decode_submission(&self.request(&query, String::new()).await?, expected)
    }
    async fn discover_submission(
        &self,
        authorization: &Authorization,
    ) -> Result<Option<arte_core::orders::submission::Marker>> {
        self.verify_storage(SUBMISSION_TABLE).await?;
        let query = format!("SELECT DISTINCT order_key,envelope_hash,payload_json FROM {}.{SUBMISSION_TABLE} WHERE order_key='{}' LIMIT 2 FORMAT JSONEachRow", self.database, authorization.key()?);
        discover_marker(&self.request(&query, String::new()).await?, authorization)
    }
}
fn discover_marker(
    body: &str,
    authorization: &Authorization,
) -> Result<Option<arte_core::orders::submission::Marker>> {
    if body.len() > 4 * MAX_PAYLOAD {
        return Err(Error::Capacity("submission discovery byte limit".into()));
    }
    let mut result = None;
    for line in body.lines().filter(|line| !line.trim().is_empty()) {
        if result.is_some() {
            return Err(Error::Conflict(
                "multiple discovered submission versions".into(),
            ));
        }
        let row: Row =
            serde_json::from_str(line).map_err(|e| Error::Serialization(e.to_string()))?;
        if row.payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("discovered marker byte limit".into()));
        }
        let marker: arte_core::orders::submission::Marker = serde_json::from_str(&row.payload_json)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        marker.require(authorization)?;
        if decode_submission(line, &marker)?.is_none() {
            return Err(Error::Unready("discovered marker missing".into()));
        }
        result = Some(marker);
    }
    Ok(result)
}
impl OrderPublisher<'_> {
    /// Recover a command whose complete authorization is already known from the
    /// pinned decision path. No HTTP request is an order request. A database error
    /// or conflicting marker cannot be treated as absence.
    pub async fn recover_known_order(
        &mut self,
        ledger: &mut arte_core::orders::OrderLedger,
        expected: &Authorization,
    ) -> Result<()> {
        self.lease.require(&self.ownership_hash)?;
        if expected.bracket.account != self.account {
            return Err(Error::Conflict("recovery account mismatch".into()));
        }
        let authorization = self
            .database
            .find_order_authorization(expected)
            .await?
            .ok_or_else(|| Error::Unready("recovery authorization missing".into()))?;
        let marker = self.database.discover_submission(&authorization).await?;
        self.lease.require(&self.ownership_hash)?;
        ledger.recover_published_order(authorization, marker)
    }
}
impl crate::order_journal::SubmissionPublisher for OrderPublisher<'_> {
    async fn append_submission(
        &mut self,
        authorization: &Authorization,
        marker: &arte_core::orders::submission::Marker,
    ) -> Result<arte_core::orders::submission::Marker> {
        self.lease.require(&self.ownership_hash)?;
        if authorization.bracket.account != self.account
            || marker.order_key != authorization.key()?
            || marker.authorization_hash != authorization.hash()?
        {
            return Err(Error::Conflict(
                "submission writer identity mismatch".into(),
            ));
        }
        if self
            .database
            .find_order_authorization(authorization)
            .await?
            .is_none()
        {
            return Err(Error::Unready(
                "authorization must be persisted before submission marker".into(),
            ));
        }
        let payload_json =
            serde_json::to_string(marker).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload_json.len() > MAX_PAYLOAD {
            return Err(Error::Capacity("submission marker byte limit".into()));
        }
        if let Some(existing) = self.database.find_submission(marker).await? {
            return Ok(existing);
        }
        let row = Row {
            order_key: marker.order_key.clone(),
            envelope_hash: marker.hash()?,
            payload_json,
        };
        self.database
            .insert(
                SUBMISSION_TABLE,
                &[serde_json::to_value(row).map_err(|e| Error::Serialization(e.to_string()))?],
            )
            .await?;
        self.database
            .find_submission(marker)
            .await?
            .ok_or_else(|| Error::Unready("submission marker readback missing".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::orders::*;
    #[test]
    fn submission_marker_readback_rejects_changed_request_and_duplicate_versions() {
        let marker = submission::Marker {
            order_key: "a".repeat(64),
            authorization_hash: "b".repeat(64),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        };
        let mut row = Row {
            order_key: marker.order_key.clone(),
            envelope_hash: marker.hash().unwrap(),
            payload_json: serde_json::to_string(&marker).unwrap(),
        };
        let body = serde_json::to_string(&row).unwrap();
        assert!(decode_submission("", &marker).unwrap().is_none());
        assert_eq!(
            decode_submission(&body, &marker).unwrap(),
            Some(marker.clone())
        );
        assert!(decode_submission(&format!("{body}\n{body}"), &marker).is_err());
        row.payload_json.push(' ');
        assert!(decode_submission(&serde_json::to_string(&row).unwrap(), &marker).is_err());
        let mut changed = marker;
        changed.request_hash = "d".repeat(64);
        assert!(decode_submission(&body, &changed).is_err());
    }
    #[test]
    fn readback_requires_exact_canonical_authorization_in_stable_slot() {
        let authorization = Authorization {
            bracket: Bracket {
                command_id: "c".into(),
                account: "a".into(),
                instrument: 1,
                side: Side::Long,
                quantity: 1,
                entry: 100,
                price_scale: 2,
                stop: Some(90),
                target: Some(110),
                tick: 1,
                deadline_ns: 100,
            },
            context: AuthorizationContext {
                session_hash: "a".repeat(64),
                allow_extended: true,
                risk_policy_hash: "b".repeat(64),
            },
        };
        let mut row = Row {
            order_key: authorization.key().unwrap(),
            envelope_hash: authorization.hash().unwrap(),
            payload_json: serde_json::to_string(&authorization).unwrap(),
        };
        let body = serde_json::to_string(&row).unwrap();
        assert!(decode("", &authorization).unwrap().is_none());
        let marker = submission::Marker {
            order_key: authorization.key().unwrap(),
            authorization_hash: authorization.hash().unwrap(),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        };
        let marker_row = Row {
            order_key: marker.order_key.clone(),
            envelope_hash: marker.hash().unwrap(),
            payload_json: serde_json::to_string(&marker).unwrap(),
        };
        let marker_body = serde_json::to_string(&marker_row).unwrap();
        assert!(discover_marker("", &authorization).unwrap().is_none());
        assert_eq!(
            discover_marker(&marker_body, &authorization).unwrap(),
            Some(marker)
        );
        assert!(discover_marker(&format!("{marker_body}\n{marker_body}"), &authorization).is_err());
        let mut foreign = authorization.clone();
        foreign.bracket.account = "other".into();
        assert!(discover_marker(&marker_body, &foreign).is_err());
        assert!(discover_marker("{}", &authorization).is_err());
        assert_eq!(
            decode(&body, &authorization).unwrap(),
            Some(authorization.clone())
        );
        assert!(decode(&format!("{body}\n{body}"), &authorization).is_err());
        let mut changed = authorization.clone();
        changed.bracket.quantity = 2;
        assert_eq!(changed.key().unwrap(), authorization.key().unwrap());
        assert_ne!(changed.hash().unwrap(), authorization.hash().unwrap());
        assert!(decode(&body, &changed).is_err());
        row.payload_json.push(' ');
        assert!(decode(&serde_json::to_string(&row).unwrap(), &authorization).is_err());
        assert!(decode(&" ".repeat(4 * MAX_PAYLOAD + 1), &authorization).is_err());
    }
}
