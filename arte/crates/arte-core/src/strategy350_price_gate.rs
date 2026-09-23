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
#[derive(Clone, Serialize, Deserialize)]
pub struct PriceFact {
    pub value: Decimal,
    pub available_at_ns: u64,
    pub source_hash: String,
    pub source_order: Option<(u64, u64)>,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ContextSource {
    Live,
    HistoricalRest,
}
#[derive(Serialize)]
pub struct SessionContext {
    pub(crate) source: ContextSource,
    pub(crate) session: u32,
    pub(crate) at_ns: u64,
    pub(crate) source_order: (u64, u64),
    pub(crate) open: Decimal,
    pub(crate) high: Decimal,
    pub(crate) prior_high: Option<PriceFact>,
    pub(crate) complete: bool,
}
impl SessionContext {
    pub fn source(&self) -> ContextSource {
        self.source
    }
    pub fn session(&self) -> u32 {
        self.session
    }
    pub fn available_at_ns(&self) -> u64 {
        self.at_ns
    }
    pub fn source_order(&self) -> (u64, u64) {
        self.source_order
    }
    pub fn open(&self) -> Decimal {
        self.open
    }
    pub fn high(&self) -> Decimal {
        self.high
    }
    pub fn prior_high(&self) -> Option<&PriceFact> {
        self.prior_high.as_ref()
    }
    pub fn complete(&self) -> bool {
        self.complete
    }
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum Block {
    IneligibleTrade,
    PriorCloseUnavailableOrTooHigh,
    CurrentPriceBelowMinimum,
    SessionContextUnavailable,
    LateModeOutsidePriorHodZone,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub struct Outcome {
    pub late_mode: bool,
    pub block: Option<Block>,
}

/// One ticker-shared market observation can be projected to several accounts.
/// Historical REST acquisition time is deliberately not live availability.
#[derive(Debug, Clone)]
pub struct PriceEvidence {
    scope: Scope,
    source: ContextSource,
    config_hash: String,
    event_time_ns: u64,
    live_available_at_ns: Option<u64>,
    live_run_id: Option<String>,
    outcome: Outcome,
    fingerprint: String,
}
impl PriceEvidence {
    pub fn outcome(&self) -> Outcome {
        self.outcome
    }
    pub fn fingerprint(&self) -> &str {
        &self.fingerprint
    }
    pub fn source(&self) -> ContextSource {
        self.source
    }
    pub fn live_available_at_ns(&self) -> Option<u64> {
        self.live_available_at_ns
    }
    /// Bind even blocked evidence to the exact live run before journaling it.
    pub fn require_live_identity(
        &self,
        market_scope: Scope,
        scope: &crate::strategy_dispatch::Scope,
        input: &crate::strategy_dispatch::InputBoundary,
        expected_gate_hash: &str,
    ) -> Result<()> {
        use crate::strategy_dispatch::{Mode, StrategyKind};
        let available = self
            .live_available_at_ns
            .ok_or_else(|| Error::Unready("Strategy 350 live price evidence missing".into()))?;
        if self.source != ContextSource::Live
            || self.scope != market_scope
            || self.config_hash != expected_gate_hash
            || scope.strategy_kind != StrategyKind::Strategy350
            || !matches!(scope.mode, Mode::Live | Mode::Paper)
            || scope.instrument != self.scope.instrument
            || self.live_run_id.as_deref() != Some(scope.run_id.as_str())
            || input.event_time_ns < self.event_time_ns
            || input.available_at_ns < available
            || input.evaluated_at_ns < available
        {
            return Err(Error::Unready(
                "Strategy 350 price evidence does not belong to this live decision".into(),
            ));
        }
        Ok(())
    }
    /// Final exposure gate. This is not an order or broker authorization.
    pub fn require_live_decision(
        &self,
        market_scope: Scope,
        scope: &crate::strategy_dispatch::Scope,
        input: &crate::strategy_dispatch::InputBoundary,
        expected_gate_hash: &str,
        maximum_age_ns: u64,
    ) -> Result<()> {
        self.require_live_identity(market_scope, scope, input, expected_gate_hash)?;
        let available = self.live_available_at_ns.unwrap();
        if self.outcome.block.is_some()
            || maximum_age_ns == 0
            || input.evaluated_at_ns - available >= maximum_age_ns
        {
            return Err(Error::Unready(
                "Strategy 350 price evidence is not exposure authority".into(),
            ));
        }
        Ok(())
    }
}

pub struct State {
    scope: Scope,
    source: ContextSource,
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
        source: ContextSource,
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
            source,
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
        if context.source != self.source
            || !context.complete
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
    /// Capture the complete causal price-gate input in one immutable identity.
    /// The gate still advances on blocked observations; only an allowed live
    /// outcome can later satisfy `require_live_decision`.
    pub fn observe_evidence(
        &mut self,
        event: &Observation,
        policy: &Pinned,
        prior_close: Option<&PriceFact>,
        context: &SessionContext,
        evaluated_at_ns: u64,
    ) -> Result<PriceEvidence> {
        if (self.source == ContextSource::Live) != event.receipt.is_some() {
            return Err(Error::Conflict(
                "Strategy 350 evidence source and receive clock differ".into(),
            ));
        }
        let outcome = self.observe(event, policy, prior_close, context, evaluated_at_ns)?;
        let fingerprint = content_hash(&(
            "arte.strategy-350-price-evidence.v1",
            self.scope.provider,
            self.scope.instrument,
            self.scope.session,
            self.source,
            &self.config_hash,
            event,
            policy.hash(),
            prior_close,
            context,
            evaluated_at_ns,
            outcome,
        ))?;
        Ok(PriceEvidence {
            scope: self.scope,
            source: self.source,
            config_hash: self.config_hash.clone(),
            event_time_ns: event.sip.ns,
            live_available_at_ns: (self.source == ContextSource::Live)
                .then_some(event.available_at_ns),
            live_run_id: event.receipt.as_ref().map(|receipt| receipt.run_id.clone()),
            outcome,
            fingerprint,
        })
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
        events::{EventKey, EventKind, Receipt, SourceTime},
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
            source: ContextSource::Live,
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
            ContextSource::Live,
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
    fn historical_rest_context_cannot_certify_live_gate() {
        let mut rest = context(2 * S, "10", None);
        rest.source = ContextSource::HistoricalRest;
        assert_eq!(rest.source(), ContextSource::HistoricalRest);
        let mut live = state();
        assert!(!live.observe_context(&rest, 2 * S).unwrap());
        assert_eq!(
            live.observe(
                &event(2 * S, "10"),
                &policy(),
                Some(&fact("19", S)),
                &rest,
                2 * S,
            )
            .unwrap()
            .block,
            Some(Block::SessionContextUnavailable)
        );
        let config = config();
        let hash = config.hash().unwrap();
        let mut historical = State::new(
            Scope {
                provider: 1,
                instrument: 10,
                session: 20260922,
            },
            ContextSource::HistoricalRest,
            S,
            config,
            &hash,
        )
        .unwrap();
        assert!(historical.observe_context(&rest, 2 * S).unwrap());
    }
    #[test]
    fn shared_price_evidence_binds_live_scope_config_and_age_per_account() {
        use crate::strategy_dispatch::{InputBoundary, Mode, Scope as DecisionScope, StrategyKind};
        let mut gate = state();
        let mut live_event = event(2 * S, "10");
        assert!(gate
            .observe_evidence(
                &live_event,
                &policy(),
                Some(&fact("19", S)),
                &context(2 * S, "10", None),
                2 * S,
            )
            .is_err());
        live_event.receipt = Some(Receipt {
            run_id: "run".into(),
            lane: 1,
            sequence: 1,
            utc_ns: 2 * S,
            monotonic_ns: 100,
        });
        let evidence = gate
            .observe_evidence(
                &live_event,
                &policy(),
                Some(&fact("19", S)),
                &context(2 * S, "10", None),
                2 * S,
            )
            .unwrap();
        assert_eq!(evidence.outcome().block, None);
        assert_eq!(evidence.live_available_at_ns(), Some(2 * S));
        assert_eq!(evidence.fingerprint().len(), 64);
        let market_scope = Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        };
        let mut decision_scope = DecisionScope {
            run_id: "run".into(),
            mode: Mode::Live,
            account: "first".into(),
            strategy_instance: "renamed-350".into(),
            strategy_kind: StrategyKind::Strategy350,
            execution_interval: crate::execution_interval::ExecutionInterval::Fixed(100_000_000),
            instrument: 10,
            code_hash: "a".repeat(64),
            config_hash: "b".repeat(64),
        };
        let mut input = InputBoundary {
            event_id: "completed-bar".into(),
            event_time_ns: 2 * S,
            available_at_ns: 2 * S,
            evaluated_at_ns: 2 * S + 100_000_000,
            source_sequence: 1,
            feature_hash: "feature".into(),
        };
        let gate_hash = gate.configuration_hash();
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_ok());
        let mut account =
            crate::strategy_transaction::Runtime::new(decision_scope.clone(), 0_u64, 1024).unwrap();
        let safety = crate::strategy_dispatch::Safety {
            position_quantity: 0,
            pending_exit_quantity: 0,
            exit_pending: false,
            pending_entry: false,
            last_exit_reason: None,
            flatten: false,
            protective_stop_crossed: false,
            manual_exit: false,
            completed_macd_reversal: false,
            setup_phase: crate::strategy_lifecycle::Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        };
        assert!(crate::strategy350_transaction::prepare_market_decision(
            &mut account,
            crate::strategy350_transaction::MarketDecisionInput {
                market_scope: Scope {
                    session: 20260923,
                    ..market_scope
                },
                input: input.clone(),
                safety: &safety,
                price: &evidence,
                expected_price_gate_hash: gate_hash,
                maximum_price_age_ns: 200_000_000,
                other_evidence_hash: &"c".repeat(64),
            },
            |_| panic!("wrong scope cannot observe account state"),
            |_| panic!("wrong scope cannot calculate"),
        )
        .is_err());
        let decision = crate::strategy350_transaction::prepare_market_decision(
            &mut account,
            crate::strategy350_transaction::MarketDecisionInput {
                market_scope,
                input: input.clone(),
                safety: &safety,
                price: &evidence,
                expected_price_gate_hash: gate_hash,
                maximum_price_age_ns: 200_000_000,
                other_evidence_hash: &"c".repeat(64),
            },
            |state| {
                *state += 1;
                Ok(())
            },
            |_| {
                Ok(vec![crate::strategy_dispatch::Action::Wait {
                    reason: "other_gates_pending".into(),
                }])
            },
        )
        .unwrap();
        assert_eq!(
            decision.evidence_hash,
            content_hash(&(
                "arte.strategy-350-market-decision.v1",
                evidence.fingerprint(),
                "c".repeat(64)
            ))
            .unwrap()
        );
        assert_eq!(*account.committed_state(), 0);
        let rows = account.pending_batch().unwrap().records().to_vec();
        let committed = account.acknowledge(&rows).unwrap();
        assert_eq!(*account.committed_state(), 1);
        assert!(
            crate::strategy350_transaction::CommittedMarketDecision::from_readback(
                &committed,
                market_scope,
                &evidence,
                gate_hash,
                200_000_000,
                &"d".repeat(64),
            )
            .is_err()
        );
        let authorized = crate::strategy350_transaction::CommittedMarketDecision::from_readback(
            &committed,
            market_scope,
            &evidence,
            gate_hash,
            200_000_000,
            &"c".repeat(64),
        )
        .unwrap();
        assert_eq!(
            authorized
                .require_at(input.evaluated_at_ns)
                .unwrap()
                .decision_id,
            decision.decision_id
        );
        assert!(authorized.require_at(2 * S + 200_000_000).is_err());
        let mut add_input = input.clone();
        add_input.event_id = "add-bar".into();
        add_input.source_sequence = 2;
        add_input.evaluated_at_ns = 2 * S + 150_000_000;
        let mut add_safety = safety.clone();
        add_safety.position_quantity = 1;
        let level = crate::strategy_targets::TargetLevel {
            geometry: crate::strategy_encounters::Level {
                id: "resistance".into(),
                price: 10.,
                lower: 10.,
                upper: 10.,
                role: crate::v7_encounters::ActiveRole::Resistance,
                confirmed_at_ns: S,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        };
        let add = crate::strategy_adds::Proposal {
            confirmed_at_ns: add_input.event_time_ns,
            tranche_index: 2,
            tranche_count: 3,
            broken: level,
            threshold: 10.,
            stop: 9.,
            target: 11.,
            maximum_buy_price: 10.5,
        };
        crate::strategy350_transaction::prepare_market_decision(
            &mut account,
            crate::strategy350_transaction::MarketDecisionInput {
                market_scope,
                input: add_input,
                safety: &add_safety,
                price: &evidence,
                expected_price_gate_hash: gate_hash,
                maximum_price_age_ns: 200_000_000,
                other_evidence_hash: &"e".repeat(64),
            },
            |_| Ok(()),
            |_| Ok(vec![crate::strategy_dispatch::Action::Add(Box::new(add))]),
        )
        .unwrap();
        let add_rows = account.pending_batch().unwrap().records().to_vec();
        let add_committed = account.acknowledge(&add_rows).unwrap();
        let add_proof = crate::strategy350_transaction::CommittedMarketDecision::from_readback(
            &add_committed,
            market_scope,
            &evidence,
            gate_hash,
            200_000_000,
            &"e".repeat(64),
        )
        .unwrap();
        let allocation = crate::decision_orders::Allocation {
            account: "first".into(),
            instrument: 10,
            quantity: 1,
            price_scale: 2,
            tick: 1,
            entry_limit: 1000,
            deadline_ns: 3 * S,
        };
        let band = crate::luld::Evidence {
            provider: 1,
            instrument: 10,
            session: 20260922,
            lower: 800,
            upper: 1200,
            scale: 2,
            effective_at_ns: 2 * S,
            available_at_ns: 2 * S,
            official: true,
        };
        let risk = crate::orders::RiskPolicy {
            band_provider: 1,
            band_session: 20260922,
            band_buffer_ticks: 3,
            max_band_age_ns: S,
        };
        let plan = crate::decision_orders::bracket_350(
            &add_proof,
            0,
            &allocation,
            2 * S + 175_000_000,
            true,
            Some(&band),
            &risk,
        )
        .unwrap();
        assert_eq!(plan.bracket.stop, Some(900));
        assert_eq!(plan.bracket.target, Some(1100));
        assert!(crate::decision_orders::bracket_350(
            &add_proof,
            0,
            &allocation,
            2 * S + 200_000_000,
            true,
            Some(&band),
            &risk,
        )
        .is_err());
        decision_scope.account = "second".into();
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_ok());
        decision_scope.run_id = "other-run".into();
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_err());
        decision_scope.run_id = "run".into();
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                &"0".repeat(64),
                200_000_000
            )
            .is_err());
        input.evaluated_at_ns += 100_000_000;
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_err());
        input.evaluated_at_ns -= 100_000_000;
        decision_scope.mode = Mode::Backtest;
        assert!(evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_err());
        decision_scope.mode = Mode::Live;
        assert!(evidence
            .require_live_decision(
                Scope {
                    session: 20260923,
                    ..market_scope
                },
                &decision_scope,
                &input,
                gate_hash,
                200_000_000,
            )
            .is_err());
        let mut historical_context = context(2 * S, "10", None);
        historical_context.source = ContextSource::HistoricalRest;
        let config = config();
        let hash = config.hash().unwrap();
        let mut historical = State::new(
            market_scope,
            ContextSource::HistoricalRest,
            S,
            config,
            &hash,
        )
        .unwrap();
        let history_evidence = historical
            .observe_evidence(
                &event(2 * S, "10"),
                &policy(),
                Some(&fact("19", S)),
                &historical_context,
                2 * S,
            )
            .unwrap();
        assert_eq!(history_evidence.live_available_at_ns(), None);
        assert!(history_evidence
            .require_live_decision(
                market_scope,
                &decision_scope,
                &input,
                gate_hash,
                200_000_000
            )
            .is_err());
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
