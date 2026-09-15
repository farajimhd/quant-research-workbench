use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Side {
    Long,
    Short,
}
pub use crate::luld::Evidence as Bands;
/// Prices use one explicit integer scale shared with the instrument's tick rule.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Bracket {
    pub command_id: String,
    pub account: String,
    pub instrument: u64,
    pub side: Side,
    pub quantity: u64,
    pub entry: i64,
    pub price_scale: u8,
    pub stop: Option<i64>,
    pub target: Option<i64>,
    pub tick: i64,
    pub deadline_ns: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RiskPolicy {
    pub band_provider: u16,
    pub band_session: u32,
    pub band_buffer_ticks: u32,
    pub max_band_age_ns: u64,
}
/// Pinned calendar policy shared by authorization and submission. The source
/// adapter owns exchange certification and timezone validation.
pub struct TradingSession {
    session: crate::session::Session,
    hash: String,
    allow_extended: bool,
}
impl TradingSession {
    pub fn new(
        session: crate::session::Session,
        hash: String,
        as_of_ns: u64,
        allow_extended: bool,
    ) -> Result<Self> {
        session.require(&hash, as_of_ns)?;
        Ok(Self {
            session,
            hash,
            allow_extended,
        })
    }
    pub fn validate(
        &self,
        order: &Bracket,
        now_ns: u64,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        use crate::session::Phase;
        if policy.band_session != self.session.session || policy.band_provider == 0 {
            return Err(Error::Conflict(
                "order risk scope differs from pinned session".into(),
            ));
        }
        let regular = match self.session.phase(&self.hash, now_ns)? {
            Phase::Regular => true,
            Phase::Premarket | Phase::Postmarket if self.allow_extended => false,
            _ => {
                return Err(Error::Unready(
                    "order outside permitted trading hours".into(),
                ))
            }
        };
        order.validate(now_ns, regular, bands, policy)
    }
}
impl Bracket {
    /// Structural check only; never sufficient to authorize an order.
    pub fn validate_geometry(&self, now_ns: u64) -> Result<()> {
        if self.command_id.is_empty()
            || self.account.is_empty()
            || self.instrument == 0
            || self.quantity == 0
            || self.tick <= 0
            || self.price_scale > 9
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
        Ok(())
    }
    pub fn validate(
        &self,
        now_ns: u64,
        regular: bool,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        self.validate_geometry(now_ns)?;
        let (stop, target) = self.stop.zip(self.target).unwrap();
        if regular {
            if policy.band_buffer_ticks < 3 || policy.max_band_age_ns == 0 {
                return Err(Error::Invalid(
                    "at least three buffer ticks and a band-age policy required".into(),
                ));
            }
            let bands = bands.ok_or_else(|| Error::Unready("official LULD missing".into()))?;
            bands.require(
                crate::event_order::Scope {
                    provider: policy.band_provider,
                    instrument: self.instrument,
                    session: policy.band_session,
                },
                self.price_scale,
                now_ns,
                policy.max_band_age_ns,
            )?;
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
        session: &TradingSession,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
        market: crate::exposure::Check<'_>,
    ) -> Result<&OrderRecord> {
        market.require(bracket.instrument)?;
        session.validate(&bracket, now_ns, bands, policy)?;
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
        session: &TradingSession,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
        market: crate::exposure::Check<'_>,
    ) -> Result<()> {
        market.require(self.record(id)?.bracket.instrument)?;
        session.validate(&self.record(id)?.bracket, now_ns, bands, policy)?;
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
    fn session(regular: bool) -> TradingSession {
        // Synthetic nanosecond geometry tests the core without timezone I/O.
        let session = crate::session::Session {
            exchange: "XNYS".into(),
            session: 20260915,
            previous_trading_session: 20260914,
            extended: crate::coverage::Interval {
                start: 1,
                end: 2000,
            },
            regular: crate::coverage::Interval {
                start: if regular { 1 } else { 500 },
                end: 1500,
            },
            available_at_ns: 1,
            source_manifest_hash: "a".repeat(64),
        };
        let hash = content_hash(&session).unwrap();
        TradingSession::new(session, hash, 1, true).unwrap()
    }
    fn b() -> Bracket {
        Bracket {
            price_scale: 2,
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
                    band_provider: 1,
                    band_session: 20260915,
                    band_buffer_ticks: 3,
                    max_band_age_ns: 100
                }
            )
            .is_err());
    }
    #[test]
    fn durable_bracket_identity_includes_price_scale() {
        let original = b();
        let mut changed = original.clone();
        changed.price_scale = 3;
        assert_ne!(
            content_hash(&original).unwrap(),
            content_hash(&changed).unwrap()
        );
        let mut value = serde_json::to_value(original).unwrap();
        value.as_object_mut().unwrap().remove("price_scale");
        assert!(serde_json::from_value::<Bracket>(value).is_err());
    }
    #[test]
    fn bands_direction_and_freshness() {
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let bands = Bands {
            provider: 1,
            instrument: 1,
            session: 20260915,
            scale: 2,
            effective_at_ns: 1,
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
        for case in 0..6 {
            let mut invalid = bands.clone();
            match case {
                0 => invalid.instrument = 2,
                1 => invalid.provider = 2,
                2 => invalid.session -= 1,
                3 => invalid.scale = 3,
                4 => invalid.effective_at_ns = 2,
                _ => invalid.official = false,
            }
            assert!(b().validate(10, true, Some(&invalid), &policy).is_err());
            assert!(short.validate(10, true, Some(&invalid), &policy).is_err());
        }
        let gate = ready_gate();
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &session(true), Some(&bands), &policy, gate.at(10))
            .unwrap();
        ledger
            .mark_durable("c", &ledger.envelope_hash("c").unwrap())
            .unwrap();
        let mut restamped = bands.clone();
        restamped.available_at_ns = 102;
        assert!(ledger
            .begin_submit(
                "c",
                102,
                &session(true),
                Some(&restamped),
                &policy,
                gate.at(10)
            )
            .is_err());
        assert_eq!(ledger.records["c"].state, OrderState::Durable);
        let mut wrong_session = bands.clone();
        wrong_session.session -= 1;
        assert!(ledger
            .begin_submit(
                "c",
                10,
                &session(true),
                Some(&wrong_session),
                &policy,
                gate.at(10)
            )
            .is_err());
        assert_eq!(ledger.records["c"].state, OrderState::Durable);
        ledger
            .begin_submit("c", 10, &session(true), Some(&bands), &policy, gate.at(10))
            .unwrap();
        assert_eq!(ledger.records["c"].state, OrderState::Submitting);
        for field in [
            "effective_at_ns",
            "provider",
            "instrument",
            "session",
            "scale",
        ] {
            let mut serialized = serde_json::to_value(&bands).unwrap();
            serialized.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<Bands>(serialized).is_err());
        }
        for field in ["band_provider", "band_session"] {
            let mut serialized = serde_json::to_value(&policy).unwrap();
            serialized.as_object_mut().unwrap().remove(field);
            assert!(serde_json::from_value::<RiskPolicy>(serialized).is_err());
        }
    }
    #[test]
    fn ambiguous_submit_cannot_retry() {
        let gate = ready_gate();
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut l = OrderLedger::default();
        l.authorize(b(), 10, &session(false), None, &policy, gate.at(10))
            .unwrap();
        assert!(l
            .begin_submit("c", 10, &session(false), None, &policy, gate.at(10))
            .is_err());
        let hash = l.envelope_hash("c").unwrap();
        l.mark_durable("c", &hash).unwrap();
        l.begin_submit("c", 10, &session(false), None, &policy, gate.at(10))
            .unwrap();
        l.submission_unknown("c").unwrap();
        assert!(l
            .begin_submit("c", 10, &session(false), None, &policy, gate.at(10))
            .is_err());
        l.reconcile("c", "broker-1".into(), 2).unwrap();
        assert_eq!(l.records["c"].state, OrderState::PartiallyFilled);
    }
    #[test]
    fn expiration_rechecked_after_durability() {
        let gate = ready_gate();
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut l = OrderLedger::default();
        l.authorize(b(), 10, &session(false), None, &policy, gate.at(10))
            .unwrap();
        let hash = l.envelope_hash("c").unwrap();
        l.mark_durable("c", &hash).unwrap();
        assert!(l
            .begin_submit("c", 1000, &session(false), None, &policy, gate.at(10))
            .is_err());
    }
    #[test]
    fn phase_is_rechecked_after_durability_without_changing_order_state() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut order = b();
        order.deadline_ns = 3000;
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(order, 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        ledger
            .mark_durable("c", &ledger.envelope_hash("c").unwrap())
            .unwrap();
        // UTC and local monotonic clocks are independent. Keep healthy monotonic
        // evidence so failure specifically exercises the calendar/LULD gate.
        for utc in [500, 2000] {
            assert!(ledger
                .begin_submit("c", utc, &calendar, None, &policy, gate.at(10))
                .is_err());
            assert_eq!(ledger.records["c"].state, OrderState::Durable);
        }
        let bands = Bands {
            provider: 1,
            instrument: 1,
            session: 20260915,
            scale: 2,
            lower: 85,
            upper: 115,
            effective_at_ns: 500,
            available_at_ns: 500,
            official: true,
        };
        ledger
            .begin_submit("c", 500, &calendar, Some(&bands), &policy, gate.at(10))
            .unwrap();
        assert_eq!(ledger.records["c"].state, OrderState::Submitting);
    }
    fn ready_gate() -> crate::exposure::Gate {
        let mut g = crate::exposure::Gate::new(100, 4).unwrap();
        g.transport(true);
        for k in [
            crate::events::EventKind::Trade,
            crate::events::EventKind::Quote,
        ] {
            g.update(1, k, 1, true).unwrap();
        }
        g
    }
    #[test]
    fn market_health_rechecked_between_authorization_and_submission() {
        let mut gate = ready_gate();
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &session(false), None, &policy, gate.at(10))
            .unwrap();
        let hash = ledger.envelope_hash("c").unwrap();
        ledger.mark_durable("c", &hash).unwrap();
        gate.update(1, crate::events::EventKind::Quote, 11, false)
            .unwrap();
        assert!(ledger
            .begin_submit("c", 12, &session(false), None, &policy, gate.at(12))
            .is_err());
        assert_eq!(ledger.records["c"].state, OrderState::Durable);
    }
}
