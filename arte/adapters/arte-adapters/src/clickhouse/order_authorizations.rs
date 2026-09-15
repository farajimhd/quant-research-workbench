//! Immutable order slots. Requires cooperative account ownership before writes.
use super::*;
use arte_core::{config::Acceptance, orders::Authorization};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const TABLE: &str = "order_authorizations_v2";
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

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::orders::*;
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
