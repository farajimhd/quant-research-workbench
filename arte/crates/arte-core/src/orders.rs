use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Bands {
    pub lower: i64,
    pub upper: i64,
    pub available_at_ns: u64,
    pub official: bool,
}
/// Prices use one explicit integer scale shared with the instrument's tick rule.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Bracket {
    pub command_id: String,
    pub account: String,
    pub instrument: u64,
    pub side: Side,
    pub quantity: u64,
    pub entry: i64,
    pub stop: Option<i64>,
    pub target: Option<i64>,
    pub tick: i64,
    pub deadline_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskPolicy {
    pub band_buffer_ticks: u32,
    pub max_band_age_ns: u64,
}
impl Bracket {
    pub fn validate(
        &self,
        now_ns: u64,
        regular: bool,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        if self.command_id.is_empty()
            || self.account.is_empty()
            || self.instrument == 0
            || self.quantity == 0
            || self.tick <= 0
            || now_ns >= self.deadline_ns
        {
            return Err(Error::Invalid(
                "invalid or expired order identity/quantity".into(),
            ));
        }
        let (stop, target) = self
            .stop
            .zip(self.target)
            .ok_or_else(|| Error::Invalid("complete bracket required".into()))?;
        if [self.entry, stop, target]
            .iter()
            .any(|p| *p <= 0 || p % self.tick != 0)
        {
            return Err(Error::Invalid("invalid price or tick alignment".into()));
        }
        let correct = match self.side {
            Side::Long => stop < self.entry && self.entry < target,
            Side::Short => target < self.entry && self.entry < stop,
        };
        if !correct {
            return Err(Error::Invalid("bracket direction".into()));
        }
        if regular {
            if policy.band_buffer_ticks < 3 || policy.max_band_age_ns == 0 {
                return Err(Error::Invalid(
                    "at least three buffer ticks and a band-age policy required".into(),
                ));
            }
            let bands = bands.ok_or_else(|| Error::Unready("official LULD missing".into()))?;
            if !bands.official
                || bands.available_at_ns > now_ns
                || now_ns - bands.available_at_ns > policy.max_band_age_ns
                || bands.lower <= 0
                || bands.lower >= bands.upper
            {
                return Err(Error::Unready("LULD unavailable, invalid or stale".into()));
            }
            let buffer = self
                .tick
                .checked_mul(policy.band_buffer_ticks as i64)
                .ok_or_else(|| Error::Invalid("buffer overflow".into()))?;
            let lower = bands
                .lower
                .checked_add(buffer)
                .ok_or_else(|| Error::Invalid("band overflow".into()))?;
            let upper = bands
                .upper
                .checked_sub(buffer)
                .ok_or_else(|| Error::Invalid("band overflow".into()))?;
            if [stop, target].iter().any(|p| *p < lower || *p > upper) {
                return Err(Error::Invalid("bracket outside buffered LULD".into()));
            }
        }
        Ok(())
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum OrderState {
    Authorized,
    Durable,
    Submitting,
    Unknown,
    Acknowledged,
    PartiallyFilled,
    Filled,
    Rejected,
    Cancelled,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct OrderRecord {
    pub bracket: Bracket,
    pub state: OrderState,
    pub filled: u64,
    pub broker_id: Option<String>,
    pub durable_receipt: Option<String>,
}
#[derive(Debug, Default, Clone, Serialize, Deserialize)]
pub struct OrderLedger {
    pub records: BTreeMap<String, OrderRecord>,
}
impl OrderLedger {
    pub fn authorize(
        &mut self,
        bracket: Bracket,
        now_ns: u64,
        regular: bool,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<&OrderRecord> {
        bracket.validate(now_ns, regular, bands, policy)?;
        let id = bracket.command_id.clone();
        if let Some(old) = self.records.get(&id) {
            if old.bracket != bracket {
                return Err(Error::Conflict(
                    "command ID reused for another bracket".into(),
                ));
            }
        }
        self.records.entry(id.clone()).or_insert(OrderRecord {
            bracket,
            state: OrderState::Authorized,
            filled: 0,
            broker_id: None,
            durable_receipt: None,
        });
        Ok(&self.records[&id])
    }
    pub fn envelope_hash(&self, id: &str) -> Result<String> {
        content_hash(&self.record(id)?.bracket)
    }
    fn record(&self, id: &str) -> Result<&OrderRecord> {
        self.records
            .get(id)
            .ok_or_else(|| Error::Invalid("unknown command".into()))
    }
    fn record_mut(&mut self, id: &str) -> Result<&mut OrderRecord> {
        self.records
            .get_mut(id)
            .ok_or_else(|| Error::Invalid("unknown command".into()))
    }
    pub fn mark_durable(&mut self, id: &str, ack_hash: &str) -> Result<()> {
        if self.envelope_hash(id)? != ack_hash {
            return Err(Error::Conflict("durable acknowledgment hash".into()));
        }
        let r = self.record_mut(id)?;
        if r.state != OrderState::Authorized {
            return Err(Error::Invalid("invalid durable transition".into()));
        }
        r.durable_receipt = Some(ack_hash.into());
        r.state = OrderState::Durable;
        Ok(())
    }
    pub fn begin_submit(
        &mut self,
        id: &str,
        now_ns: u64,
        regular: bool,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        self.record(id)?
            .bracket
            .validate(now_ns, regular, bands, policy)?;
        let r = self.record_mut(id)?;
        if r.state != OrderState::Durable {
            return Err(Error::Unready(
                "submission requires durable authorization; reconcile unknown outcomes".into(),
            ));
        }
        r.state = OrderState::Submitting;
        Ok(())
    }
    pub fn submission_unknown(&mut self, id: &str) -> Result<()> {
        let r = self.record_mut(id)?;
        if r.state != OrderState::Submitting {
            return Err(Error::Invalid("not submitting".into()));
        }
        r.state = OrderState::Unknown;
        Ok(())
    }
    pub fn reconcile(&mut self, id: &str, broker_id: String, cumulative_fill: u64) -> Result<()> {
        let r = self.record_mut(id)?;
        if broker_id.is_empty()
            || cumulative_fill < r.filled
            || cumulative_fill > r.bracket.quantity
            || !matches!(
                r.state,
                OrderState::Submitting
                    | OrderState::Unknown
                    | OrderState::Acknowledged
                    | OrderState::PartiallyFilled
                    | OrderState::Filled
            )
        {
            return Err(Error::Invalid("invalid broker reconciliation".into()));
        }
        if r.broker_id.as_ref().is_some_and(|old| old != &broker_id) {
            return Err(Error::Conflict("broker ID changed".into()));
        }
        r.broker_id = Some(broker_id);
        r.filled = cumulative_fill;
        r.state = if cumulative_fill == r.bracket.quantity {
            OrderState::Filled
        } else if cumulative_fill > 0 {
            OrderState::PartiallyFilled
        } else {
            OrderState::Acknowledged
        };
        Ok(())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn b() -> Bracket {
        Bracket {
            command_id: "c".into(),
            account: "paper".into(),
            instrument: 1,
            side: Side::Long,
            quantity: 5,
            entry: 100,
            stop: Some(90),
            target: Some(110),
            tick: 1,
            deadline_ns: 1000,
        }
    }
    #[test]
    fn unprotected_rejected() {
        let mut order = b();
        order.target = None;
        assert!(order
            .validate(
                10,
                false,
                None,
                &RiskPolicy {
                    band_buffer_ticks: 3,
                    max_band_age_ns: 100
                }
            )
            .is_err());
    }
    #[test]
    fn bands_direction_and_freshness() {
        let policy = RiskPolicy {
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let bands = Bands {
            lower: 85,
            upper: 115,
            available_at_ns: 1,
            official: true,
        };
        assert!(b().validate(10, true, Some(&bands), &policy).is_ok());
        assert!(b().validate(200, true, Some(&bands), &policy).is_err());
        let mut short = b();
        short.side = Side::Short;
        short.stop = Some(110);
        short.target = Some(90);
        assert!(short.validate(10, true, Some(&bands), &policy).is_ok());
    }
    #[test]
    fn ambiguous_submit_cannot_retry() {
        let policy = RiskPolicy {
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut l = OrderLedger::default();
        l.authorize(b(), 10, false, None, &policy).unwrap();
        assert!(l.begin_submit("c", 10, false, None, &policy).is_err());
        let hash = l.envelope_hash("c").unwrap();
        l.mark_durable("c", &hash).unwrap();
        l.begin_submit("c", 10, false, None, &policy).unwrap();
        l.submission_unknown("c").unwrap();
        assert!(l.begin_submit("c", 10, false, None, &policy).is_err());
        l.reconcile("c", "broker-1".into(), 2).unwrap();
        assert_eq!(l.records["c"].state, OrderState::PartiallyFilled);
    }
    #[test]
    fn expiration_rechecked_after_durability() {
        let policy = RiskPolicy {
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut l = OrderLedger::default();
        l.authorize(b(), 10, false, None, &policy).unwrap();
        let hash = l.envelope_hash("c").unwrap();
        l.mark_durable("c", &hash).unwrap();
        assert!(l.begin_submit("c", 1000, false, None, &policy).is_err());
    }
}
