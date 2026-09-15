//! Validate recovered ledger state before exposing any execution operation.
use super::*;
use serde::de::{MapAccess, Visitor};
use std::fmt;

pub(super) const MAX_RECORDS: usize = 100_000;
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
pub(super) struct StoredLedger {
    #[serde(deserialize_with = "unique_records")]
    records: BTreeMap<String, OrderRecord>,
}
fn unique_records<'de, D: serde::Deserializer<'de>>(
    deserializer: D,
) -> std::result::Result<BTreeMap<String, OrderRecord>, D::Error> {
    struct Records;
    impl<'de> Visitor<'de> for Records {
        type Value = BTreeMap<String, OrderRecord>;
        fn expecting(&self, f: &mut fmt::Formatter) -> fmt::Result {
            f.write_str("bounded unique order records")
        }
        fn visit_map<M: MapAccess<'de>>(
            self,
            mut map: M,
        ) -> std::result::Result<Self::Value, M::Error> {
            let mut records = BTreeMap::new();
            while let Some(key) = map.next_key::<String>()? {
                if records.len() >= MAX_RECORDS {
                    return Err(serde::de::Error::custom("order recovery record limit"));
                }
                if records.contains_key(&key) {
                    return Err(serde::de::Error::custom("duplicate order recovery key"));
                }
                records.insert(key, map.next_value()?);
            }
            Ok(records)
        }
    }
    deserializer.deserialize_map(Records)
}
pub(super) fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl OrderLedger {
    /// Bootstrap a known command from verified publication readback. The caller
    /// must hold account ownership and query both authorization and submission
    /// storage. This does not reconstruct fills or broker-protection state.
    pub fn recover_published_order(
        &mut self,
        authorization: Authorization,
        submission: Option<submission::Marker>,
    ) -> Result<()> {
        let id = authorization.bracket.command_id.clone();
        if self.records.contains_key(&id) {
            return Err(Error::Conflict(
                "recovery cannot overwrite an existing order".into(),
            ));
        }
        if self.records.len() >= MAX_RECORDS {
            return Err(Error::Capacity("order ledger record limit".into()));
        }
        let durable_receipt = Some(authorization.hash()?);
        let submission_receipt = submission
            .as_ref()
            .map(submission::Marker::hash)
            .transpose()?;
        let state = if submission.is_some() {
            OrderState::Unknown
        } else {
            OrderState::Durable
        };
        let record = OrderRecord {
            bracket: authorization.bracket,
            authorization: authorization.context,
            state,
            filled: 0,
            broker_id: None,
            durable_receipt,
            submission,
            submission_receipt,
        };
        // Reuse the snapshot invariant validator before changing the destination.
        let mut checked = Self::try_from(StoredLedger {
            records: BTreeMap::from([(id.clone(), record)]),
        })?;
        self.records
            .insert(id.clone(), checked.records.remove(&id).unwrap());
        Ok(())
    }
}
impl TryFrom<StoredLedger> for OrderLedger {
    type Error = Error;
    fn try_from(mut stored: StoredLedger) -> Result<Self> {
        for (id, record) in &mut stored.records {
            if id != &record.bracket.command_id {
                return Err(Error::Conflict(
                    "recovered order key differs from command".into(),
                ));
            }
            // Historical geometry validation only. Expiry, feed and session are
            // rechecked against current clocks before a future submission.
            record.bracket.validate_geometry(0)?;
            if !hash_valid(&record.authorization.session_hash)
                || !hash_valid(&record.authorization.risk_policy_hash)
            {
                return Err(Error::Invalid("recovered order context hash".into()));
            }
            let authorization = Authorization {
                bracket: record.bracket.clone(),
                context: record.authorization.clone(),
            };
            if let Some(marker) = &record.submission {
                marker.require(&authorization)?;
                if matches!(record.state, OrderState::Authorized | OrderState::Durable) {
                    return Err(Error::Conflict(
                        "submission marker before submitting state".into(),
                    ));
                }
                if let Some(receipt) = &record.submission_receipt {
                    if receipt != &marker.hash()? {
                        return Err(Error::Conflict(
                            "recovered submission receipt mismatch".into(),
                        ));
                    }
                }
            } else if record.submission_receipt.is_some() {
                return Err(Error::Conflict("submission receipt without marker".into()));
            }
            let broker = record.broker_id.as_ref().is_some_and(|id| !id.is_empty());
            if record.broker_id.is_some() && !broker {
                return Err(Error::Invalid("empty recovered broker identity".into()));
            }
            let consistent = match record.state {
                OrderState::Authorized => {
                    record.durable_receipt.is_none() && record.filled == 0 && !broker
                }
                OrderState::Durable | OrderState::Submitting | OrderState::Unknown => {
                    record.filled == 0 && !broker
                }
                OrderState::Acknowledged => broker && record.filled == 0,
                OrderState::PartiallyFilled => {
                    broker && record.filled > 0 && record.filled < record.bracket.quantity
                }
                OrderState::Filled => broker && record.filled == record.bracket.quantity,
                OrderState::Rejected => record.filled == 0,
                OrderState::Cancelled => {
                    record.filled < record.bracket.quantity && (record.filled == 0 || broker)
                }
            };
            if !consistent {
                return Err(Error::Conflict("inconsistent recovered order state".into()));
            }
            if record.state != OrderState::Authorized
                && record.durable_receipt.as_deref() != Some(authorization.hash()?.as_str())
            {
                return Err(Error::Conflict(
                    "recovered order durability receipt mismatch".into(),
                ));
            }
            if record.state == OrderState::Submitting {
                record.state = OrderState::Unknown;
            }
        }
        Ok(Self {
            records: stored.records,
        })
    }
}
