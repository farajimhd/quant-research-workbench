//! Shared Strategy 350 purchase price gate. It is one component of the strategy,
//! not an entry authorization or a replacement for structural/quote/risk rules.
use crate::{
    content_hash,
    event_order::Scope,
    events::{Decimal, Observation, Payload},
    execution_interval::ExecutionInterval,
    trade_eligibility::Pinned,
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const VERSION: &str = "arte.strategy-350-price-gate.v1";
fn hash_ok(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub price_scale: u8,
    pub prior_close_max_atoms: i64,
    pub purchase_min_atoms: i64,
    pub late_gain_bps: u32,
    pub hod_floor_bps: u32,
    pub prior_close_source_hash: String,
    pub trade_policy_hash: String,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        self.execution_interval.validate()?;
        if self.execution_interval != ExecutionInterval::Events {
            return Err(Error::Invalid(
                "Strategy 350 price gate requires event cadence".into(),
            ));
        }
        if self.price_scale > 9
            || self.prior_close_max_atoms <= self.purchase_min_atoms
            || self.purchase_min_atoms <= 0
            || self.late_gain_bps == 0
            || self.late_gain_bps > 100_000
            || self.hod_floor_bps == 0
            || self.hod_floor_bps >= 10_000
            || !hash_ok(&self.prior_close_source_hash)
            || !hash_ok(&self.trade_policy_hash)
        {
            return Err(Error::Invalid(
                "Strategy 350 price gate configuration".into(),
            ));
        }
        content_hash(&(VERSION, self))
    }
}
#[derive(Clone)]
pub struct PriceFact {
    pub value: Decimal,
    pub available_at_ns: u64,
    pub source_hash: String,
    pub source_order: Option<(u64, u64)>,
}
pub struct SessionContext {
    pub session: u32,
    pub at_ns: u64,
    pub source_order: (u64, u64),
    pub open: Decimal,
    pub high: Decimal,
    pub prior_high: Option<PriceFact>,
    pub complete: bool,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Block {
    IneligibleTrade,
    PriorCloseUnavailableOrTooHigh,
    CurrentPriceBelowMinimum,
    SessionContextUnavailable,
    LateModeOutsidePriorHodZone,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Outcome {
    pub late_mode: bool,
    pub block: Option<Block>,
}

pub struct State {
    scope: Scope,
    session_start_ns: u64,
    config: Config,
    config_hash: String,
    late_mode: bool,
    // The ordered market lane releases by source time and provider sequence.
    // Receive/availability time is evidence, not the event-order key.
    last_source_order: Option<(u64, u64)>,
    last_context: Option<((u64, u64), i64, i64)>,
    failed: bool,
}
impl State {
    pub fn new(
        scope: Scope,
        session_start_ns: u64,
        config: Config,
        expected_hash: &str,
    ) -> Result<Self> {
        let hash = config.hash()?;
        if hash != expected_hash
            || scope.provider == 0
            || scope.instrument == 0
            || !(19000101..=29991231).contains(&scope.session)
            || session_start_ns == 0
        {
            return Err(Error::Conflict("Strategy 350 price gate identity".into()));
        }
        Ok(Self {
            scope,
            session_start_ns,
            config,
            config_hash: hash,
            late_mode: false,
            last_source_order: None,
            last_context: None,
            failed: false,
        })
    }
    pub fn configuration_hash(&self) -> &str {
        &self.config_hash
    }
    pub fn late_mode(&self) -> Result<bool> {
        if self.failed {
            return Err(Error::Unready(
                "Strategy 350 price gate requires recovery".into(),
            ));
        }
        Ok(self.late_mode)
    }
    /// Session context may advance on a quote, bar, or clock boundary without
    /// an eligible trade. Only the context timestamp grants causal knowledge.
    pub fn observe_context(&mut self, context: &SessionContext, known_at_ns: u64) -> Result<bool> {
        self.late_mode()?;
        let result = self.update_context(context, known_at_ns);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn update_context(&mut self, context: &SessionContext, known_at_ns: u64) -> Result<bool> {
        if !context.complete
            || context.session != self.scope.session
            || context.at_ns < self.session_start_ns
            || context.at_ns > known_at_ns
            || context.source_order.0 < self.session_start_ns
            || context.source_order.0 > context.at_ns
        {
            return Ok(false);
        }
        let open = context.open.atoms_at_scale(self.config.price_scale)?;
        let high = context.high.atoms_at_scale(self.config.price_scale)?;
        if open <= 0 || high < open {
            return Err(Error::Conflict(
                "Strategy 350 session price geometry".into(),
            ));
        }
        if self
            .last_context
            .is_some_and(|(order, old_open, old_high)| {
                context.source_order < order
                    || open != old_open
                    || high < old_high
                    || (context.source_order == order && high != old_high)
            })
        {
            return Err(Error::Conflict(
                "Strategy 350 session context regressed".into(),
            ));
        }
        self.last_context = Some((context.source_order, open, high));
        self.late_mode |= i128::from(high) * 10_000
            >= i128::from(open) * (10_000 + i128::from(self.config.late_gain_bps));
        Ok(true)
    }
    pub fn observe(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        prior_close: Option<&PriceFact>,
        context: &SessionContext,
        evaluated_at_ns: u64,
    ) -> Result<Outcome> {
        self.late_mode()?;
        let result = self.update(event, policy, prior_close, context, evaluated_at_ns);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn update(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        prior_close: Option<&PriceFact>,
        context: &SessionContext,
        evaluated_at_ns: u64,
    ) -> Result<Outcome> {
        event.validate()?;
        if event.key.provider != self.scope.provider
            || event.key.instrument != self.scope.instrument
            || event.key.session != self.scope.session
            || event.available_at_ns < self.session_start_ns
            || event.available_at_ns > evaluated_at_ns
            || self
                .last_source_order
                .is_some_and(|last| (event.sip.ns, event.key.sequence) <= last)
            || event.sip.ns > event.available_at_ns
        {
            return Err(Error::Conflict(
                "Strategy 350 trade scope or causal order".into(),
            ));
        }
        let Payload::Trade { price, .. } = &event.payload else {
            return Err(Error::Invalid("Strategy 350 price gate needs trade".into()));
        };
        if policy.hash() != self.config.trade_policy_hash {
            return Err(Error::Conflict("Strategy 350 trade policy differs".into()));
        }
        let price_atoms = price.atoms_at_scale(self.config.price_scale)?;
        if price_atoms <= 0 {
            return Err(Error::Invalid("Strategy 350 trade price".into()));
        }
        let eligible = policy.evaluate(event, evaluated_at_ns)?;
        self.last_source_order = Some((event.sip.ns, event.key.sequence));
        let context_ready = self.update_context(context, evaluated_at_ns)?;
        if !eligible {
            return Ok(Outcome {
                late_mode: self.late_mode,
                block: Some(Block::IneligibleTrade),
            });
        }
        if !context_ready {
            return Ok(Outcome {
                late_mode: self.late_mode,
                block: Some(Block::SessionContextUnavailable),
            });
        }
        let high = context.high.atoms_at_scale(self.config.price_scale)?;
        if high < price_atoms {
            return Err(Error::Conflict(
                "Strategy 350 session price geometry".into(),
            ));
        }
        let prior = prior_close.and_then(|fact| {
            if fact.available_at_ns == 0
                || fact.available_at_ns > self.session_start_ns
                || fact.source_hash != self.config.prior_close_source_hash
            {
                return None;
            }
            fact.value.atoms_at_scale(self.config.price_scale).ok()
        });
        if prior.is_none_or(|p| p <= 0 || p >= self.config.prior_close_max_atoms) {
            return Ok(Outcome {
                late_mode: self.late_mode,
                block: Some(Block::PriorCloseUnavailableOrTooHigh),
            });
        }
        if price_atoms < self.config.purchase_min_atoms {
            return Ok(Outcome {
                late_mode: self.late_mode,
                block: Some(Block::CurrentPriceBelowMinimum),
            });
        }
        if self.late_mode {
            let prior_high = context.prior_high.as_ref().and_then(|fact| {
                if fact.available_at_ns < self.session_start_ns
                    || fact.available_at_ns > evaluated_at_ns
                    || fact
                        .source_order
                        .is_none_or(|order| order >= (event.sip.ns, event.key.sequence))
                    || fact.source_hash != self.config.trade_policy_hash
                {
                    return None;
                }
                fact.value.atoms_at_scale(self.config.price_scale).ok()
            });
            if prior_high.is_none_or(|hod| {
                hod <= 0
                    || price_atoms >= hod
                    || i128::from(price_atoms) * 10_000
                        < i128::from(hod) * i128::from(self.config.hod_floor_bps)
            }) {
                return Ok(Outcome {
                    late_mode: true,
                    block: Some(Block::LateModeOutsidePriorHodZone),
                });
            }
        }
        Ok(Outcome {
            late_mode: self.late_mode,
            block: None,
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        events::{EventKey, EventKind, SourceTime},
        trade_eligibility,
    };
    use std::collections::BTreeSet;
    const S: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            prior_close_max_atoms: 2000,
            purchase_min_atoms: 100,
            late_gain_bps: 1500,
            hod_floor_bps: 7000,
            prior_close_source_hash: "a".repeat(64),
            trade_policy_hash: policy().hash().into(),
        }
    }
    fn policy() -> Pinned {
        let p = trade_eligibility::Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 0,
            valid_to_ns: u64::MAX,
            available_at_ns: 0,
            source_manifest_hash: "b".repeat(64),
            allowed_conditions: BTreeSet::new(),
            excluded_conditions: BTreeSet::new(),
            allow_empty_conditions: true,
        };
        let hash = p.hash().unwrap();
        Pinned::new(p, &hash).unwrap()
    }
    fn event(at: u64, price: &str) -> Observation {
        Observation {
            key: EventKey {
                provider: 1,
                instrument: 10,
                session: 20260922,
                kind: EventKind::Trade,
                sequence: at,
            },
            payload: Payload::Trade {
                price: Decimal::parse(price).unwrap(),
                size: Decimal::parse("1").unwrap(),
                exchange: 1,
                trade_id: at.to_string(),
                trf: None,
                conditions: vec![],
                correction: None,
            },
            sip: SourceTime {
                ns: at,
                precision_ns: 1,
            },
            participant: None,
            available_at_ns: at,
            receipt: None,
        }
    }
    fn fact(price: &str, at: u64) -> PriceFact {
        PriceFact {
            value: Decimal::parse(price).unwrap(),
            available_at_ns: at,
            source_hash: "a".repeat(64),
            source_order: None,
        }
    }
    fn prior_high(price: &str, at: u64) -> PriceFact {
        PriceFact {
            source_order: Some((at, at)),
            source_hash: policy().hash().into(),
            ..fact(price, at)
        }
    }
    fn context(at: u64, high: &str, prior: Option<PriceFact>) -> SessionContext {
        SessionContext {
            session: 20260922,
            at_ns: at,
            source_order: (at, at),
            open: Decimal::parse("10").unwrap(),
            high: Decimal::parse(high).unwrap(),
            prior_high: prior,
            complete: true,
        }
    }
    fn state() -> State {
        state_with_policy_hash(policy().hash())
    }
    fn state_with_policy_hash(policy_hash: &str) -> State {
        let mut c = config();
        c.trade_policy_hash = policy_hash.into();
        let hash = c.hash().unwrap();
        State::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            S,
            c,
            &hash,
        )
        .unwrap()
    }
    #[test]
    fn strict_prior_close_and_purchase_floor() {
        let p = policy();
        let mut s = state();
        assert_eq!(
            s.observe(
                &event(2 * S, "10"),
                &p,
                None,
                &context(2 * S, "10", None),
                2 * S
            )
            .unwrap()
            .block,
            Some(Block::PriorCloseUnavailableOrTooHigh)
        );
        assert_eq!(
            s.observe(
                &event(3 * S, "0.99"),
                &p,
                Some(&fact("19.99", S)),
                &context(3 * S, "10", None),
                3 * S
            )
            .unwrap()
            .block,
            Some(Block::CurrentPriceBelowMinimum)
        );
        assert_eq!(
            s.observe(
                &event(4 * S, "10"),
                &p,
                Some(&fact("20", S)),
                &context(4 * S, "10", None),
                4 * S
            )
            .unwrap()
            .block,
            Some(Block::PriorCloseUnavailableOrTooHigh)
        );
        assert_eq!(
            s.observe(
                &event(5 * S, "10"),
                &p,
                Some(&fact("19.99", S)),
                &context(5 * S, "10", None),
                5 * S
            )
            .unwrap()
            .block,
            None
        );
    }
    #[test]
    fn late_mode_latches_and_uses_prior_high_only() {
        let p = policy();
        let mut s = state();
        let close = fact("19", S);
        assert_eq!(
            s.observe(
                &event(2 * S, "11.50"),
                &p,
                Some(&close),
                &context(2 * S, "11.50", Some(prior_high("11.4", S))),
                2 * S
            )
            .unwrap()
            .block,
            Some(Block::LateModeOutsidePriorHodZone)
        );
        assert!(s.late_mode().unwrap());
        assert_eq!(
            s.observe(
                &event(3 * S, "10"),
                &p,
                Some(&close),
                &context(3 * S, "11.50", Some(prior_high("11.50", 2 * S))),
                3 * S
            )
            .unwrap()
            .block,
            None
        );
        assert!(s.late_mode().unwrap());
        assert!(s
            .observe(
                &event(3 * S, "10"),
                &p,
                Some(&close),
                &context(3 * S, "11.50", None),
                3 * S
            )
            .is_err());
        assert!(s.late_mode().is_err());
    }
    #[test]
    fn source_order_allows_equal_or_reordered_receipt_times() {
        let p = policy();
        let close = fact("19", S);
        let mut state = state();
        let mut first = event(2 * S, "10");
        first.available_at_ns = 4 * S;
        state
            .observe(&first, &p, Some(&close), &context(2 * S, "10", None), 4 * S)
            .unwrap();
        let mut second = event(3 * S, "10");
        second.sip.ns = first.sip.ns;
        second.available_at_ns = first.available_at_ns;
        state
            .observe(
                &second,
                &p,
                Some(&close),
                &context(2 * S, "10", None),
                4 * S,
            )
            .unwrap();
        let mut third = event(4 * S, "10");
        third.sip.ns = 3 * S;
        third.available_at_ns = 3 * S;
        state
            .observe(&third, &p, Some(&close), &context(3 * S, "10", None), 4 * S)
            .unwrap();
        assert!(state
            .observe(
                &second,
                &p,
                Some(&close),
                &context(2 * S, "10", None),
                4 * S
            )
            .is_err());
        assert!(state.late_mode().is_err());
    }
    #[test]
    fn late_mode_latches_before_purchase_price_gate() {
        let p = policy();
        let mut state = state();
        let outcome = state
            .observe(
                &event(2 * S, "0.99"),
                &p,
                Some(&fact("19", S)),
                &context(2 * S, "11.50", None),
                2 * S,
            )
            .unwrap();
        assert_eq!(outcome.block, Some(Block::CurrentPriceBelowMinimum));
        assert!(outcome.late_mode);
    }
    #[test]
    fn nontrade_context_latches_late_mode_and_rejects_regression() {
        let mut state = state();
        assert!(state
            .observe_context(&context(2 * S, "11.50", None), 2 * S)
            .unwrap());
        assert!(state.late_mode().unwrap());
        assert!(state
            .observe_context(&context(2 * S, "11.50", None), 3 * S)
            .unwrap());
        assert!(state
            .observe_context(&context(3 * S, "11.60", None), 3 * S)
            .unwrap());
        assert!(state
            .observe_context(&context(4 * S, "11.40", None), 4 * S)
            .is_err());
        assert!(state.late_mode().is_err());
    }
    #[test]
    fn context_source_order_can_advance_when_receipt_time_decreases() {
        let mut state = state();
        let mut first = context(4 * S, "10", None);
        first.source_order = (2 * S, 2 * S);
        assert!(state.observe_context(&first, 4 * S).unwrap());
        let mut later_source = context(3 * S, "11.50", None);
        later_source.source_order = (3 * S, 3 * S);
        assert!(state.observe_context(&later_source, 4 * S).unwrap());
        assert!(state.late_mode().unwrap());
    }
    #[test]
    fn ineligible_trade_still_observes_known_session_context() {
        let rejected = trade_eligibility::Policy {
            schema_version: 1,
            provider: 1,
            valid_from_ns: 0,
            valid_to_ns: u64::MAX,
            available_at_ns: 0,
            source_manifest_hash: "b".repeat(64),
            allowed_conditions: BTreeSet::from([7]),
            excluded_conditions: BTreeSet::new(),
            allow_empty_conditions: false,
        };
        let hash = rejected.hash().unwrap();
        let policy = Pinned::new(rejected, &hash).unwrap();
        let mut state = state_with_policy_hash(policy.hash());
        let outcome = state
            .observe(
                &event(2 * S, "11.50"),
                &policy,
                None,
                &context(2 * S, "11.50", None),
                2 * S,
            )
            .unwrap();
        assert_eq!(outcome.block, Some(Block::IneligibleTrade));
        assert!(outcome.late_mode);
    }
}
