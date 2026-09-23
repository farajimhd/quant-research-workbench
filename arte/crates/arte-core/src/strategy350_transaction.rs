//! Strategy 350's account-owned journal boundary for shared market evidence.
//! This produces domain decisions only; it does not authorize broker orders.
use crate::{
    content_hash,
    event_order::Scope as MarketScope,
    strategy350_price_gate::PriceEvidence,
    strategy_dispatch::{Action, Decision, InputBoundary, Mode, Safety, StrategyKind},
    strategy_transaction::Runtime,
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
