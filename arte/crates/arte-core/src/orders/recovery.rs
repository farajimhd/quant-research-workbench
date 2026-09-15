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
fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
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
