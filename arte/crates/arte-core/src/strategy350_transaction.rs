//! Strategy 350's account-owned journal boundary for shared market evidence.
//! This produces domain decisions only; it does not authorize broker orders.
use crate::{
    content_hash,
    event_order::Scope as MarketScope,
    market_structure::scheduler::playback::sources::HistoricalEventProof,
    strategy350_macd::historical::Evidence as HistoricalMacdEvidence,
    strategy350_price_gate::PriceEvidence,
    strategy350_screen_join::{LiveSelectedBucket, RefinementPlan},
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
    pub refinement: Option<&'a LiveSelectedBucket>,
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
    pub refinement: Option<&'a RefinementPlan>,
    pub macd: Option<&'a HistoricalMacdEvidence>,
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
        refinement: Option<&RefinementPlan>,
        macd: Option<&HistoricalMacdEvidence>,
        other_evidence_hash: &str,
    ) -> Result<Self> {
        require_other_hash(other_evidence_hash)?;
        let decision = committed.decision();
        let expected =
            historical_evidence_hash(price, source, refinement, macd, other_evidence_hash)?;
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
        if has_exposure(&decision.actions) {
            require_historical_refinement(refinement, source)?;
        }
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
fn has_exposure(actions: &[Action]) -> bool {
    actions
        .iter()
        .any(|action| matches!(action, Action::Enter(_) | Action::Add(_)))
}
fn require_historical_refinement(
    refinement: Option<&RefinementPlan>,
    source: &HistoricalEventProof,
) -> Result<()> {
    if !refinement
        .ok_or_else(|| Error::Unready("Strategy 350 historical refinement missing".into()))?
        .contains_replay_event(source)?
    {
        return Err(Error::Unready(
            "Strategy 350 historical event was not selected for refinement".into(),
        ));
    }
    Ok(())
}
fn require_live_refinement(
    refinement: Option<&LiveSelectedBucket>,
    market_scope: MarketScope,
    input: &InputBoundary,
) -> Result<()> {
    let selected =
        refinement.ok_or_else(|| Error::Unready("Strategy 350 live refinement missing".into()))?;
    let start = selected.start_ns();
    let end = start
        .checked_add(crate::bar_catalogue::BASE_INTERVAL_NS)
        .ok_or_else(|| Error::Capacity("Strategy 350 live selection clock".into()))?;
    if !selected.needs_refinement()
        || selected.scope() != market_scope
        || input.event_time_ns < start
        || input.event_time_ns >= end
        || selected.available_at_ns() < end
        || input.available_at_ns < selected.available_at_ns()
        || input.evaluated_at_ns < selected.available_at_ns()
    {
        return Err(Error::Unready(
            "Strategy 350 live event lacks selected completed bucket".into(),
        ));
    }
    Ok(())
}
fn historical_evidence_hash(
    price: &PriceEvidence,
    source: &HistoricalEventProof,
    refinement: Option<&RefinementPlan>,
    macd: Option<&HistoricalMacdEvidence>,
    other_evidence_hash: &str,
) -> Result<String> {
    if let Some(macd) = macd {
        macd.require_proof(source)?;
    }
    content_hash(&(
        "arte.strategy-350-market-decision.v3",
        "historical-modeled",
        price.fingerprint(),
        source.identity_hash()?,
        refinement.map(RefinementPlan::evidence_hash),
        macd.map(HistoricalMacdEvidence::fingerprint),
        other_evidence_hash,
    ))
}
fn live_evidence_hash(
    price: &PriceEvidence,
    refinement: Option<&LiveSelectedBucket>,
    other_evidence_hash: &str,
) -> Result<String> {
    content_hash(&(
        "arte.strategy-350-market-decision.v2",
        "live-receipt",
        price.fingerprint(),
        refinement
            .map(LiveSelectedBucket::identity_hash)
            .transpose()?,
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
        refinement,
        macd,
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
    let evidence_hash =
        historical_evidence_hash(price, source, refinement, macd, other_evidence_hash)?;
    runtime.prepare_observed(input.clone(), safety, evidence_hash, observe, |state| {
        let actions = calculate(state)?;
        if has_exposure(&actions) {
            price.require_historical_decision(source, &scope, &input, expected_price_gate_hash)?;
            require_historical_refinement(refinement, source)?;
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
        refinement: Option<&LiveSelectedBucket>,
        other_evidence_hash: &str,
    ) -> Result<Self> {
        let decision = committed.decision();
        let expected = live_evidence_hash(price, refinement, other_evidence_hash)?;
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
        if has_exposure(&decision.actions) {
            require_live_refinement(refinement, market_scope, &decision.input)?;
        }
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
        refinement,
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
    let evidence_hash = live_evidence_hash(price, refinement, other_evidence_hash)?;
    runtime.prepare_observed(input.clone(), safety, evidence_hash, observe, |state| {
        let actions = calculate(state)?;
        if has_exposure(&actions) {
            price.require_live_decision(
                market_scope,
                &scope,
                &input,
                expected_price_gate_hash,
                maximum_price_age_ns,
            )?;
            require_live_refinement(refinement, market_scope, &input)?;
        }
        Ok(actions)
    })
}
