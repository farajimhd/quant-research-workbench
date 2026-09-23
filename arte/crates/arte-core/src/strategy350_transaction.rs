//! Strategy 350's account-owned journal boundary for shared market evidence.
//! This produces domain decisions only; it does not authorize broker orders.
use crate::{
    content_hash,
    event_order::Scope as MarketScope,
    market_structure::scheduler::playback::sources::HistoricalEventProof,
    strategy350_price_gate::PriceEvidence,
    strategy_dispatch::{Action, Decision, InputBoundary, Mode, Safety, StrategyKind},
    strategy_transaction::{Committed, Runtime},
    Error, Result,
};
use serde::Serialize;

/// Prepare one account decision from ticker-shared market evidence. The caller
/// must still supply all other pinned Strategy 350 operands in `other_evidence_hash`.
/// A blocked or stale price gate may record a wait or exit, never an exposure add.
pub struct MarketDecisionInput<'a> {
    pub market_scope: MarketScope,
    pub input: InputBoundary,
    pub safety: &'a Safety,
    pub price: &'a PriceEvidence,
    pub expected_price_gate_hash: &'a str,
    pub maximum_price_age_ns: u64,
    pub other_evidence_hash: &'a str,
}

/// The same account decision/journal envelope, with a pinned modeled replay
/// event instead of a live receive timestamp or REST acquisition clock.
pub struct HistoricalMarketDecisionInput<'a> {
    pub input: InputBoundary,
    pub safety: &'a Safety,
    pub price: &'a PriceEvidence,
    pub source: &'a HistoricalEventProof,
    pub expected_price_gate_hash: &'a str,
    pub other_evidence_hash: &'a str,
}

pub struct CommittedHistoricalDecision<'a> {
    committed: &'a Committed,
}
impl<'a> CommittedHistoricalDecision<'a> {
    pub fn from_readback(
        committed: &'a Committed,
        price: &PriceEvidence,
        source: &HistoricalEventProof,
        expected_price_gate_hash: &str,
        other_evidence_hash: &str,
    ) -> Result<Self> {
        require_other_hash(other_evidence_hash)?;
        let decision = committed.decision();
        let expected = historical_evidence_hash(price, source, other_evidence_hash)?;
        if decision.evidence_hash != expected {
            return Err(Error::Conflict(
                "Strategy 350 historical committed evidence differs".into(),
            ));
        }
        price.require_historical_decision(
            source,
            &decision.scope,
            &decision.input,
            expected_price_gate_hash,
        )?;
        Ok(Self { committed })
    }

    pub(crate) fn require_at(&self, modeled_now_ns: u64) -> Result<&Decision> {
        let decision = self.committed.decision();
        if modeled_now_ns < decision.input.evaluated_at_ns {
            return Err(Error::Unready(
                "Strategy 350 historical order precedes modeled decision".into(),
            ));
        }
        Ok(decision)
    }
}

fn require_other_hash(hash: &str) -> Result<()> {
    if hash.len() != 64
        || !hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(Error::Invalid("Strategy 350 other evidence hash".into()));
    }
    Ok(())
}
fn historical_evidence_hash(
    price: &PriceEvidence,
    source: &HistoricalEventProof,
    other_evidence_hash: &str,
) -> Result<String> {
    content_hash(&(
        "arte.strategy-350-market-decision.v1",
        "historical-modeled",
        price.fingerprint(),
        source.identity_hash()?,
        other_evidence_hash,
    ))
}

pub fn prepare_historical_market_decision<S: Clone + Serialize>(
    runtime: &mut Runtime<S>,
    request: HistoricalMarketDecisionInput<'_>,
    observe: impl FnOnce(&mut S) -> Result<()>,
    calculate: impl FnOnce(&mut S) -> Result<Vec<Action>>,
) -> Result<Decision> {
    let HistoricalMarketDecisionInput {
        input,
        safety,
        price,
        source,
        expected_price_gate_hash,
        other_evidence_hash,
    } = request;
    require_other_hash(other_evidence_hash)?;
    let scope = runtime.scope().clone();
    if scope.strategy_kind != StrategyKind::Strategy350
        || scope.mode != Mode::Backtest
        || scope.instrument != source.scope().instrument
    {
        return Err(Error::Invalid(
            "Strategy 350 historical decision scope".into(),
        ));
    }
    price.require_historical_identity(source, &scope, &input, expected_price_gate_hash)?;
    let evidence_hash = historical_evidence_hash(price, source, other_evidence_hash)?;
    runtime.prepare_observed(input.clone(), safety, evidence_hash, observe, |state| {
        let actions = calculate(state)?;
        if actions
            .iter()
            .any(|action| matches!(action, Action::Enter(_) | Action::Add(_)))
        {
            price.require_historical_decision(source, &scope, &input, expected_price_gate_hash)?;
        }
        Ok(actions)
    })
}

/// Sealed evidence for planning an exposure increase after journal readback.
/// It is not broker submission authority and must be rechecked at order time.
pub struct CommittedMarketDecision<'a> {
    committed: &'a Committed,
    price: &'a PriceEvidence,
    maximum_price_age_ns: u64,
}

impl<'a> CommittedMarketDecision<'a> {
    pub fn from_readback(
        committed: &'a Committed,
        market_scope: MarketScope,
        price: &'a PriceEvidence,
        expected_price_gate_hash: &str,
        maximum_price_age_ns: u64,
        other_evidence_hash: &str,
    ) -> Result<Self> {
        let decision = committed.decision();
        let expected = content_hash(&(
            "arte.strategy-350-market-decision.v1",
            price.fingerprint(),
            other_evidence_hash,
        ))?;
        if decision.evidence_hash != expected {
            return Err(Error::Conflict(
                "Strategy 350 committed market evidence differs".into(),
            ));
        }
        price.require_live_decision(
            market_scope,
            &decision.scope,
            &decision.input,
            expected_price_gate_hash,
            maximum_price_age_ns,
        )?;
        Ok(Self {
            committed,
            price,
            maximum_price_age_ns,
        })
    }

    pub(crate) fn require_at(&self, now_ns: u64) -> Result<&Decision> {
        let decision = self.committed.decision();
        let available = self
            .price
            .live_available_at_ns()
            .ok_or_else(|| Error::Unready("Strategy 350 live price availability missing".into()))?;
        if now_ns < decision.input.evaluated_at_ns
            || now_ns < available
            || now_ns - available >= self.maximum_price_age_ns
        {
            return Err(Error::Unready(
                "Strategy 350 price evidence expired before order planning".into(),
            ));
        }
        Ok(decision)
    }
}

pub fn prepare_market_decision<S: Clone + Serialize>(
    runtime: &mut Runtime<S>,
    request: MarketDecisionInput<'_>,
    observe: impl FnOnce(&mut S) -> Result<()>,
    calculate: impl FnOnce(&mut S) -> Result<Vec<Action>>,
) -> Result<Decision> {
    let MarketDecisionInput {
        market_scope,
        input,
        safety,
        price,
        expected_price_gate_hash,
        maximum_price_age_ns,
        other_evidence_hash,
    } = request;
    let scope = runtime.scope().clone();
    if scope.strategy_kind != StrategyKind::Strategy350
        || !matches!(scope.mode, Mode::Live | Mode::Paper)
        || scope.instrument != market_scope.instrument
        || other_evidence_hash.len() != 64
        || !other_evidence_hash
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(Error::Invalid(
            "Strategy 350 market decision identity or evidence".into(),
        ));
    }
    price.require_live_identity(market_scope, &scope, &input, expected_price_gate_hash)?;
    let evidence_hash = content_hash(&(
        "arte.strategy-350-market-decision.v1",
        price.fingerprint(),
        other_evidence_hash,
    ))?;
    runtime.prepare_observed(input.clone(), safety, evidence_hash, observe, |state| {
        let actions = calculate(state)?;
        if actions
            .iter()
            .any(|action| matches!(action, Action::Enter(_) | Action::Add(_)))
        {
            price.require_live_decision(
                market_scope,
                &scope,
                &input,
                expected_price_gate_hash,
                maximum_price_age_ns,
            )?;
        }
        Ok(actions)
    })
}
