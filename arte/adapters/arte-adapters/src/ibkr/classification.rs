//! Classification only after exact outcome readback. Never confirms or releases.
use super::{OrderAcknowledgment, Reply};
use arte_core::{
    orders::{
        outcome::{Committed, Observation},
        submission::Marker,
    },
    Error, Result,
};
use std::collections::BTreeSet;
#[derive(Debug, PartialEq, Eq)]
pub enum Disposition {
    ReconcileOrders(Vec<OrderAcknowledgment>),
    ConfirmationRequired { id: String, categories: Vec<String> },
    Blocked,
    Unknown(String),
}
pub struct Classified {
    request_hash: String,
    record_hash: String,
    policy_hash: String,
    disposition: Disposition,
}
impl Classified {
    pub fn request_hash(&self) -> &str {
        &self.request_hash
    }
    pub fn record_hash(&self) -> &str {
        &self.record_hash
    }
    pub fn policy_hash(&self) -> &str {
        &self.policy_hash
    }
    pub fn disposition(&self) -> &Disposition {
        &self.disposition
    }
}
pub fn classify(
    marker: &Marker,
    committed: Committed,
    allowlist: &BTreeSet<String>,
) -> Result<Classified> {
    let record = committed.record();
    record.require(marker)?;
    if allowlist.len() > 256 || allowlist.iter().any(|c| c.is_empty() || c.len() > 128) {
        return Err(Error::Invalid("broker warning policy bounds".into()));
    }
    let disposition = match &record.observation {
        Observation::Unknown { reason } => Disposition::Unknown(reason.clone()),
        Observation::Response { status, .. } if !(200..300).contains(status) => {
            Disposition::Unknown(format!(
                "broker HTTP status {status}; reconcile before retry"
            ))
        }
        Observation::Response { body, .. } => {
            let reply = serde_json::from_slice(body)
                .map_err(|_| Error::Invalid("broker response is not valid JSON".into()))
                .and_then(|value| super::interpret_reply(&value, allowlist));
            match reply {
                Ok(Reply::Acknowledged(orders)) => Disposition::ReconcileOrders(orders),
                Ok(Reply::Confirm { id, categories }) => {
                    Disposition::ConfirmationRequired { id, categories }
                }
                Ok(Reply::Blocked) => Disposition::Blocked,
                Err(error) => Disposition::Unknown(error.to_string()),
            }
        }
    };
    Ok(Classified {
        request_hash: marker.request_hash.clone(),
        record_hash: record.hash()?,
        policy_hash: arte_core::content_hash(&("arte.ibkr-warning-policy.v1", allowlist))?,
        disposition,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::{orders::outcome::Pending, strategy_dispatch::Mode};
    fn marker() -> Marker {
        Marker {
            order_key: "a".repeat(64),
            authorization_hash: "b".repeat(64),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        }
    }
    fn result(
        marker: &Marker,
        status: u16,
        body: &[u8],
        allowlist: &BTreeSet<String>,
    ) -> Classified {
        let mut pending = Pending::new(
            marker,
            11,
            Observation::Response {
                status,
                body: body.to_vec(),
            },
        )
        .unwrap();
        let row = pending.record().unwrap().clone();
        classify(marker, pending.acknowledge(&row).unwrap(), allowlist).unwrap()
    }
    #[test]
    fn receipt_needs_reconciliation_and_cannot_release_session() {
        let marker = marker();
        let scope =
            super::super::submission::Scope::new(Mode::Paper, "s".into(), "DU1".into()).unwrap();
        let mut gate = super::super::session::Gate::new(
            Mode::Paper,
            "s".into(),
            BTreeSet::from(["DU1".into()]),
            100,
        )
        .unwrap();
        gate.observe(
            br#"{"authenticated":true,"established":true,"connected":true,"competing":false}"#,
            10,
        )
        .unwrap();
        gate.begin(&scope, &marker.request_hash, 10).unwrap();
        let classified = result(
            &marker,
            200,
            br#"[{"order_id":"123","order_status":"PreSubmitted"}]"#,
            &BTreeSet::new(),
        );
        assert!(
            matches!(classified.disposition(), Disposition::ReconcileOrders(orders) if orders.len() == 1 && orders[0].status == "PreSubmitted")
        );
        let hash = classified.record_hash().to_owned();
        gate.observe_outcome(classified).unwrap();
        assert_eq!(gate.pending_outcome().unwrap().record_hash(), hash);
        assert!(gate.require(&scope, 11).is_err());
        let duplicate = result(
            &marker,
            200,
            br#"[{"order_id":"123","order_status":"PreSubmitted"}]"#,
            &BTreeSet::new(),
        );
        assert!(gate.observe_outcome(duplicate).is_err());
    }
    #[test]
    fn invalid_http_and_ambiguous_acknowledgments_are_unknown() {
        let marker = marker();
        let policy = BTreeSet::new();
        for body in [
            b"not-json".as_slice(), br#"[]"#, br#"[{"order_id":"1"}]"#,
            br#"[{"order_id":"","order_status":"Submitted"}]"#,
            br#"[{"order_id":"1","order_status":"other"}]"#,
            br#"[{"order_id":"1","order_status":"Submitted"},{"order_id":"1","order_status":"Submitted"}]"#,
            br#"[{"id":"reply","order_id":"1","messageIds":["o163"]}]"#,
        ] { assert!(matches!(result(&marker, 200, body, &policy).disposition(), Disposition::Unknown(_))); }
        assert!(matches!(
            result(
                &marker,
                500,
                br#"[{"order_id":"1","order_status":"Submitted"}]"#,
                &policy
            )
            .disposition(),
            Disposition::Unknown(_)
        ));
        assert!(matches!(
            result(&marker, 200, br#"[{"error":"rejected"}]"#, &policy).disposition(),
            Disposition::Blocked
        ));
    }
    #[test]
    fn approved_notice_still_requires_reply_and_policy_identity_is_pinned() {
        let marker = marker();
        let body = br#"[{"id":"reply-1","messageIds":["o163"]}]"#;
        let blocked = result(&marker, 200, body, &BTreeSet::new());
        let allowed = result(&marker, 200, body, &BTreeSet::from(["o163".into()]));
        assert_eq!(blocked.disposition(), &Disposition::Blocked);
        assert!(
            matches!(allowed.disposition(), Disposition::ConfirmationRequired { id, .. } if id == "reply-1")
        );
        assert_ne!(allowed.policy_hash(), blocked.policy_hash());
        assert_eq!(allowed.record_hash(), blocked.record_hash());
    }
}
