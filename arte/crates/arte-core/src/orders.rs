use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
pub mod outcome;
pub mod protection;
mod recovery;
pub mod submission;

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
    pub fn evidence_hash(&self) -> Result<String> {
        content_hash(&(
            "arte.trading-session.v1",
            &self.session,
            &self.hash,
            self.allow_extended,
        ))
    }
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
        if policy.band_session != self.session.session || policy.band_provider == 0 {
            return Err(Error::Conflict(
                "order risk scope differs from pinned session".into(),
            ));
        }
        order.validate(now_ns, self.require_phase(now_ns)?, bands, policy)
    }
    /// Shared live/historical session gate. Returns whether LULD is mandatory.
    /// Does not validate an order or establish the calendar producer's authority.
    pub fn require_phase(&self, now_ns: u64) -> Result<bool> {
        use crate::session::Phase;
        match self.session.phase(&self.hash, now_ns)? {
            Phase::Regular => Ok(true),
            Phase::Premarket | Phase::Postmarket if self.allow_extended => Ok(false),
            _ => Err(Error::Unready(
                "order outside permitted trading hours".into(),
            )),
        }
    }
    /// Protection replacements are not new entries: a trailing stop may exceed
    /// the original entry, and the original entry deadline may have expired.
    pub fn validate_protection(
        &self,
        order: &Bracket,
        prices: (i64, i64),
        now_ns: u64,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        order.validate_geometry(0)?;
        if policy.band_session != self.session.session || policy.band_provider == 0 {
            return Err(Error::Conflict(
                "protection risk scope differs from pinned session".into(),
            ));
        }
        let (stop, target) = prices;
        if stop <= 0
            || target <= 0
            || stop % order.tick != 0
            || target % order.tick != 0
            || match order.side {
                Side::Long => stop >= target,
                Side::Short => target >= stop,
            }
        {
            return Err(Error::Invalid(
                "invalid replacement protection geometry".into(),
            ));
        }
        if self.require_phase(now_ns)? {
            order.validate_band_prices(prices, now_ns, bands, policy)?;
        }
        Ok(())
    }
    fn authorization_context(&self, policy: &RiskPolicy) -> Result<AuthorizationContext> {
        Ok(AuthorizationContext {
            session_hash: self.hash.clone(),
            allow_extended: self.allow_extended,
            risk_policy_hash: content_hash(policy)?,
        })
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
            self.validate_band_prices((stop, target), now_ns, bands, policy)?;
        }
        Ok(())
    }
    fn validate_band_prices(
        &self,
        prices: (i64, i64),
        now_ns: u64,
        bands: Option<&Bands>,
        policy: &RiskPolicy,
    ) -> Result<()> {
        let (stop, target) = prices;
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
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct AuthorizationContext {
    pub session_hash: String,
    pub allow_extended: bool,
    pub risk_policy_hash: String,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Authorization {
    pub bracket: Bracket,
    pub context: AuthorizationContext,
}
impl Authorization {
    pub fn hash(&self) -> Result<String> {
        content_hash(&("arte.order-authorization.v2", &self.bracket, &self.context))
    }
    /// Stable slot: changed prices, quantity or policy cannot select a new slot.
    pub fn key(&self) -> Result<String> {
        content_hash(&(
            "arte.order-slot.v1",
            &self.bracket.account,
            &self.bracket.command_id,
        ))
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct OrderRecord {
    pub bracket: Bracket,
    pub authorization: AuthorizationContext,
    pub state: OrderState,
    pub filled: u64,
    pub broker_id: Option<String>,
    pub durable_receipt: Option<String>,
    pub submission: Option<submission::Marker>,
    pub submission_receipt: Option<String>,
}
#[derive(Debug, Default, Serialize, Deserialize)]
#[serde(try_from = "recovery::StoredLedger")]
pub struct OrderLedger {
    records: BTreeMap<String, OrderRecord>,
}
impl OrderLedger {
    pub fn records(&self) -> impl Iterator<Item = (&str, &OrderRecord)> {
        self.records
            .iter()
            .map(|(id, record)| (id.as_str(), record))
    }
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
        let authorization = session.authorization_context(policy)?;
        let id = bracket.command_id.clone();
        if !self.records.contains_key(&id) && self.records.len() >= recovery::MAX_RECORDS {
            return Err(Error::Capacity("order ledger record limit".into()));
        }
        if let Some(old) = self.records.get(&id) {
            if old.bracket != bracket || old.authorization != authorization {
                return Err(Error::Conflict(
                    "command ID reused for another bracket or authorization context".into(),
                ));
            }
        }
        self.records.entry(id.clone()).or_insert(OrderRecord {
            bracket,
            authorization,
            state: OrderState::Authorized,
            filled: 0,
            broker_id: None,
            durable_receipt: None,
            submission: None,
            submission_receipt: None,
        });
        Ok(&self.records[&id])
    }
    pub fn envelope_hash(&self, id: &str) -> Result<String> {
        self.authorization(id)?.hash()
    }
    pub fn authorization(&self, id: &str) -> Result<Authorization> {
        let record = self.record(id)?;
        Ok(Authorization {
            bracket: record.bracket.clone(),
            context: record.authorization.clone(),
        })
    }
    /// Readback must match the entire prepared authorization, not just a caller's
    /// hash string. The persistence adapter owns external durability evidence.
    pub fn acknowledge_authorization(&mut self, id: &str, readback: &Authorization) -> Result<()> {
        if self.authorization(id)? != *readback {
            return Err(Error::Conflict(
                "order authorization readback mismatch".into(),
            ));
        }
        self.mark_durable(id, &readback.hash()?)
    }
    pub fn record(&self, id: &str) -> Result<&OrderRecord> {
        self.records
            .get(id)
            .ok_or_else(|| Error::Invalid("unknown command".into()))
    }
    fn record_mut(&mut self, id: &str) -> Result<&mut OrderRecord> {
        self.records
            .get_mut(id)
            .ok_or_else(|| Error::Invalid("unknown command".into()))
    }
    fn mark_durable(&mut self, id: &str, ack_hash: &str) -> Result<()> {
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
        if self.record(id)?.authorization != session.authorization_context(policy)? {
            return Err(Error::Conflict(
                "submission authorization context changed".into(),
            ));
        }
        session.validate(&self.record(id)?.bracket, now_ns, bands, policy)?;
        if self.record(id)?.durable_receipt.as_deref() != Some(self.envelope_hash(id)?.as_str()) {
            return Err(Error::Conflict(
                "submission durable envelope mismatch".into(),
            ));
        }
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
    fn durable_identity_pins_session_permissions_and_risk_configuration() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        let hash = ledger.envelope_hash("c").unwrap();
        assert_ne!(hash, content_hash(&b()).unwrap());
        ledger.mark_durable("c", &hash).unwrap();
        // Retry is identity-preserving; observation time does not change the pin.
        ledger
            .authorize(b(), 11, &calendar, None, &policy, gate.at(11))
            .unwrap();
        assert_eq!(hash, ledger.envelope_hash("c").unwrap());
        for case in 0..3 {
            let mut changed_calendar = session(false);
            let mut changed_policy = policy.clone();
            match case {
                0 => {
                    changed_calendar.session.regular.start = 600;
                    changed_calendar.hash = content_hash(&changed_calendar.session).unwrap();
                }
                1 => changed_calendar.allow_extended = false,
                _ => changed_policy.max_band_age_ns = 200,
            }
            assert!(ledger
                .authorize(
                    b(),
                    10,
                    &changed_calendar,
                    None,
                    &changed_policy,
                    gate.at(10)
                )
                .is_err());
            assert!(ledger
                .begin_submit(
                    "c",
                    10,
                    &changed_calendar,
                    None,
                    &changed_policy,
                    gate.at(10)
                )
                .is_err());
            assert_eq!(ledger.record("c").unwrap().state, OrderState::Durable);
            assert_eq!(hash, ledger.envelope_hash("c").unwrap());
        }
        let bytes = serde_json::to_vec(&ledger).unwrap();
        let mut restored: OrderLedger = serde_json::from_slice(&bytes).unwrap();
        restored
            .begin_submit("c", 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        assert_eq!(restored.record("c").unwrap().state, OrderState::Submitting);
        let mut legacy = serde_json::to_value(&ledger).unwrap();
        legacy["records"]["c"]
            .as_object_mut()
            .unwrap()
            .remove("authorization");
        assert!(serde_json::from_value::<OrderLedger>(legacy).is_err());
    }
    #[test]
    fn restored_bracket_or_receipt_corruption_cannot_submit() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        ledger
            .mark_durable("c", &ledger.envelope_hash("c").unwrap())
            .unwrap();
        for case in 0..3 {
            let mut data = serde_json::to_value(&ledger).unwrap();
            match case {
                0 => data["records"]["c"]["bracket"]["quantity"] = 6.into(),
                1 => data["records"]["c"]["durable_receipt"] = serde_json::Value::Null,
                _ => data["records"]["c"]["durable_receipt"] = "b".repeat(64).into(),
            }
            assert!(serde_json::from_value::<OrderLedger>(data).is_err());
        }
    }
    #[test]
    fn recovery_requires_reconciliation_for_interrupted_submission() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        ledger
            .mark_durable("c", &ledger.envelope_hash("c").unwrap())
            .unwrap();
        ledger
            .begin_submit("c", 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        let mut restored: OrderLedger =
            serde_json::from_slice(&serde_json::to_vec(&ledger).unwrap()).unwrap();
        assert_eq!(restored.record("c").unwrap().state, OrderState::Unknown);
        assert!(restored
            .begin_submit("c", 10, &calendar, None, &policy, gate.at(10))
            .is_err());
        restored.reconcile("c", "broker-1".into(), 2).unwrap();
        let restored: OrderLedger =
            serde_json::from_slice(&serde_json::to_vec(&restored).unwrap()).unwrap();
        assert_eq!(
            restored.record("c").unwrap().state,
            OrderState::PartiallyFilled
        );
        assert_eq!(restored.records().count(), 1);
    }
    #[test]
    fn recovery_rejects_duplicate_keys_and_inconsistent_state() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        let record = serde_json::to_string(ledger.record("c").unwrap()).unwrap();
        let duplicate = format!("{{\"records\":{{\"c\":{record},\"c\":{record}}}}}");
        assert!(serde_json::from_str::<OrderLedger>(&duplicate).is_err());
        for case in 0..5 {
            let mut data = serde_json::to_value(&ledger).unwrap();
            match case {
                0 => data["records"]["c"]["filled"] = 1.into(),
                1 => data["records"]["c"]["bracket"]["command_id"] = "other".into(),
                2 => data["records"]["c"]["authorization"]["session_hash"] = "bad".into(),
                3 => data["records"]["c"]["broker_id"] = "".into(),
                _ => data["records"]["c"]["state"] = "Filled".into(),
            }
            assert!(serde_json::from_value::<OrderLedger>(data).is_err());
        }
    }
    #[test]
    fn submission_permission_is_exact_single_use_and_not_recovered() {
        let gate = ready_gate();
        let calendar = session(false);
        let policy = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 100,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        ledger
            .mark_durable("c", &ledger.envelope_hash("c").unwrap())
            .unwrap();
        let request = "a".repeat(64);
        assert!(ledger.prepare_submission_marker("c", &request, 10).is_err());
        ledger
            .begin_submit("c", 10, &calendar, None, &policy, gate.at(10))
            .unwrap();
        let marker = ledger
            .prepare_submission_marker("c", &request, 10)
            .unwrap()
            .clone();
        assert_eq!(
            &marker,
            ledger.prepare_submission_marker("c", &request, 11).unwrap()
        );
        assert!(ledger
            .prepare_submission_marker("c", &"b".repeat(64), 11)
            .is_err());
        let mut wrong = marker.clone();
        wrong.prepared_at_ns += 1;
        assert!(ledger.acknowledge_submission("c", &wrong).is_err());
        let permit = ledger.acknowledge_submission("c", &marker).unwrap();
        assert!(ledger.acknowledge_submission("c", &marker).is_err());
        assert!(ledger.prepare_submission_marker("c", &request, 12).is_err());
        let mut restored: OrderLedger =
            serde_json::from_slice(&serde_json::to_vec(&ledger).unwrap()).unwrap();
        assert_eq!(restored.record("c").unwrap().state, OrderState::Unknown);
        assert!(restored.acknowledge_submission("c", &marker).is_err());
        assert!(restored
            .prepare_submission_marker("c", &request, 12)
            .is_err());
        permit
            .validate_request(&request, 12, &calendar, None, &policy, gate.at(12))
            .unwrap();
    }
    #[test]
    fn send_permission_rechecks_actual_request_clock_and_safety() {
        for case in 0..4 {
            let mut gate = ready_gate();
            let calendar = session(false);
            let mut policy = RiskPolicy {
                band_provider: 1,
                band_session: 20260915,
                band_buffer_ticks: 3,
                max_band_age_ns: 100,
            };
            let mut ledger = OrderLedger::default();
            ledger
                .authorize(b(), 10, &calendar, None, &policy, gate.at(10))
                .unwrap();
            ledger
                .mark_durable("c", &ledger.envelope_hash("c").unwrap())
                .unwrap();
            ledger
                .begin_submit("c", 10, &calendar, None, &policy, gate.at(10))
                .unwrap();
            let request = "a".repeat(64);
            let marker = ledger
                .prepare_submission_marker("c", &request, 10)
                .unwrap()
                .clone();
            let permit = ledger.acknowledge_submission("c", &marker).unwrap();
            let mut request = request;
            let mut now = 12;
            match case {
                0 => request = "b".repeat(64),
                1 => now = 1000,
                2 => gate.transport(false),
                _ => policy.max_band_age_ns += 1,
            }
            assert!(permit
                .validate_request(&request, now, &calendar, None, &policy, gate.at(12))
                .is_err());
        }
    }
    #[test]
    fn published_marker_overrides_missing_snapshot_and_recovery_never_overwrites() {
        let authorization = Authorization {
            bracket: b(),
            context: AuthorizationContext {
                session_hash: "a".repeat(64),
                allow_extended: true,
                risk_policy_hash: "b".repeat(64),
            },
        };
        let marker = submission::Marker {
            order_key: authorization.key().unwrap(),
            authorization_hash: authorization.hash().unwrap(),
            request_hash: "c".repeat(64),
            prepared_at_ns: 10,
        };
        let mut ledger = OrderLedger::default();
        ledger
            .recover_published_order(authorization.clone(), Some(marker.clone()))
            .unwrap();
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Unknown);
        assert!(ledger
            .prepare_submission_marker("c", &marker.request_hash, 11)
            .is_err());
        assert!(ledger.acknowledge_submission("c", &marker).is_err());
        assert!(ledger
            .recover_published_order(authorization.clone(), None)
            .is_err());
        assert_eq!(ledger.record("c").unwrap().state, OrderState::Unknown);
        let mut no_marker = OrderLedger::default();
        no_marker
            .recover_published_order(authorization.clone(), None)
            .unwrap();
        assert_eq!(no_marker.record("c").unwrap().state, OrderState::Durable);
        let mut wrong = marker;
        wrong.authorization_hash = "d".repeat(64);
        let mut failed = OrderLedger::default();
        assert!(failed
            .recover_published_order(authorization, Some(wrong))
            .is_err());
        assert_eq!(failed.records().count(), 0);
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
