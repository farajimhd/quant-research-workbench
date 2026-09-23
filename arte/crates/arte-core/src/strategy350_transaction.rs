//! Strategy 350's account-owned journal boundary for shared market evidence.
//! This produces domain decisions only; it does not authorize broker orders.
use crate::{
    content_hash,
    event_order::Scope as MarketScope,
    market_structure::scheduler::playback::sources::HistoricalEventProof,
    strategy350_effective::Config as EffectiveConfig,
    strategy350_gap::FrozenGap,
    strategy350_macd::historical::Evidence as HistoricalMacdEvidence,
    strategy350_macd::live::Evidence as LiveMacdEvidence,
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
    pub effective: &'a EffectiveConfig,
    pub market_scope: MarketScope,
    pub input: InputBoundary,
    pub safety: &'a Safety,
    pub price: &'a PriceEvidence,
    pub expected_price_gate_hash: &'a str,
    pub maximum_price_age_ns: u64,
    pub refinement: Option<&'a LiveSelectedBucket>,
    pub macd: Option<&'a LiveMacdEvidence>,
    pub gap: Option<&'a FrozenGap>,
    pub other_evidence_hash: &'a str,
}

pub struct LiveReadback<'a> {
    pub effective: &'a EffectiveConfig,
    pub market_scope: MarketScope,
    pub price: &'a PriceEvidence,
    pub expected_price_gate_hash: &'a str,
    pub maximum_price_age_ns: u64,
    pub refinement: Option<&'a LiveSelectedBucket>,
    pub macd: Option<&'a LiveMacdEvidence>,
    pub gap: Option<&'a FrozenGap>,
    pub other_evidence_hash: &'a str,
}

/// The same account decision/journal envelope, with a pinned modeled replay
/// event instead of a live receive timestamp or REST acquisition clock.
pub struct HistoricalMarketDecisionInput<'a> {
    pub effective: &'a EffectiveConfig,
    pub input: InputBoundary,
    pub safety: &'a Safety,
    pub price: &'a PriceEvidence,
    pub source: &'a HistoricalEventProof,
    pub expected_price_gate_hash: &'a str,
    pub refinement: Option<&'a RefinementPlan>,
    pub macd: Option<&'a HistoricalMacdEvidence>,
    pub gap: Option<&'a FrozenGap>,
    pub other_evidence_hash: &'a str,
}

pub struct HistoricalReadback<'a> {
    pub effective: &'a EffectiveConfig,
    pub price: &'a PriceEvidence,
    pub source: &'a HistoricalEventProof,
    pub expected_price_gate_hash: &'a str,
    pub refinement: Option<&'a RefinementPlan>,
    pub macd: Option<&'a HistoricalMacdEvidence>,
    pub gap: Option<&'a FrozenGap>,
    pub other_evidence_hash: &'a str,
}

pub struct CommittedHistoricalDecision<'a> {
    committed: &'a Committed,
}
impl<'a> CommittedHistoricalDecision<'a> {
    pub fn from_readback(
        committed: &'a Committed,
        request: HistoricalReadback<'_>,
    ) -> Result<Self> {
        let HistoricalReadback {
            effective,
            price,
            source,
            expected_price_gate_hash,
            refinement,
            macd,
            gap,
            other_evidence_hash,
        } = request;
        require_other_hash(other_evidence_hash)?;
        let decision = committed.decision();
        effective.require_scope(&decision.scope)?;
        effective.require_price_gate(expected_price_gate_hash)?;
        if let Some(macd) = macd {
            effective.require_macd(macd.configuration_hash())?;
        }
        if let Some(gap) = gap {
            effective.require_gap(gap)?;
        }
        validate_optional_gap(gap, &decision.input)?;
        let expected =
            historical_evidence_hash(price, source, refinement, macd, gap, other_evidence_hash)?;
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
            require_historical_macd(macd, source, &decision.input)?;
            require_gap(gap, &decision.input)?;
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
fn require_gap(gap: Option<&FrozenGap>, input: &InputBoundary) -> Result<()> {
    let gap = gap.ok_or_else(|| Error::Unready("Strategy 350 activation gap missing".into()))?;
    validate_optional_gap(Some(gap), input)
}
fn validate_optional_gap(gap: Option<&FrozenGap>, input: &InputBoundary) -> Result<()> {
    let Some(gap) = gap else {
        return Ok(());
    };
    gap.validate()?;
    if gap.activated_at_ns > input.event_time_ns {
        return Err(Error::Unready(
            "Strategy 350 activation gap follows decision".into(),
        ));
    }
    Ok(())
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
pub(crate) fn require_historical_macd(
    macd: Option<&HistoricalMacdEvidence>,
    source: &HistoricalEventProof,
    input: &InputBoundary,
) -> Result<()> {
    let macd = macd.ok_or_else(|| Error::Unready("Strategy 350 historical MACD missing".into()))?;
    macd.require_proof(source)?;
    let outcome = macd.outcome();
    if !outcome.bullish
        || outcome.event_time_ns != input.event_time_ns
        || outcome.evaluated_at_ns != input.evaluated_at_ns
        || outcome.event_time_ns != source.source_time_ns()
        || outcome.evaluated_at_ns != source.evaluated_at_ns()
    {
        return Err(Error::Unready(
            "Strategy 350 historical MACD blocks purchase".into(),
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
    gap: Option<&FrozenGap>,
    other_evidence_hash: &str,
) -> Result<String> {
    if let Some(macd) = macd {
        macd.require_proof(source)?;
    }
    if let Some(gap) = gap {
        gap.validate()?;
    }
    content_hash(&(
        "arte.strategy-350-market-decision.v4",
        "historical-modeled",
        price.fingerprint(),
        source.identity_hash()?,
        refinement.map(RefinementPlan::evidence_hash),
        macd.map(HistoricalMacdEvidence::fingerprint),
        gap.map(FrozenGap::hash).transpose()?,
        other_evidence_hash,
    ))
}
fn live_evidence_hash(
    price: &PriceEvidence,
    refinement: Option<&LiveSelectedBucket>,
    macd: Option<&LiveMacdEvidence>,
    gap: Option<&FrozenGap>,
    other_evidence_hash: &str,
) -> Result<String> {
    if let Some(gap) = gap {
        gap.validate()?;
    }
    content_hash(&(
        "arte.strategy-350-market-decision.v4",
        "live-receipt",
        price.fingerprint(),
        refinement
            .map(LiveSelectedBucket::identity_hash)
            .transpose()?,
        macd.map(LiveMacdEvidence::fingerprint),
        gap.map(FrozenGap::hash).transpose()?,
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
        effective,
        input,
        safety,
        price,
        source,
        expected_price_gate_hash,
        refinement,
        macd,
        gap,
        other_evidence_hash,
    } = request;
    require_other_hash(other_evidence_hash)?;
    let scope = runtime.scope().clone();
    effective.require_scope(&scope)?;
    effective.require_price_gate(expected_price_gate_hash)?;
    if let Some(macd) = macd {
        effective.require_macd(macd.configuration_hash())?;
    }
    if let Some(gap) = gap {
        effective.require_gap(gap)?;
    }
    if scope.strategy_kind != StrategyKind::Strategy350
        || scope.mode != Mode::Backtest
        || scope.instrument != source.scope().instrument
    {
        return Err(Error::Invalid(
            "Strategy 350 historical decision scope".into(),
        ));
    }
    price.require_historical_identity(source, &scope, &input, expected_price_gate_hash)?;
    validate_optional_gap(gap, &input)?;
    let evidence_hash =
        historical_evidence_hash(price, source, refinement, macd, gap, other_evidence_hash)?;
    runtime.prepare_observed(input.clone(), safety, evidence_hash, observe, |state| {
        let actions = calculate(state)?;
        if has_exposure(&actions) {
            price.require_historical_decision(source, &scope, &input, expected_price_gate_hash)?;
            require_historical_refinement(refinement, source)?;
            require_historical_macd(macd, source, &input)?;
            require_gap(gap, &input)?;
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
    pub fn from_readback(committed: &'a Committed, request: LiveReadback<'a>) -> Result<Self> {
        let LiveReadback {
            effective,
            market_scope,
            price,
            expected_price_gate_hash,
            maximum_price_age_ns,
            refinement,
            macd,
            gap,
            other_evidence_hash,
        } = request;
        let decision = committed.decision();
        effective.require_scope(&decision.scope)?;
        effective.require_price_gate(expected_price_gate_hash)?;
        if let Some(macd) = macd {
            effective.require_macd(macd.configuration_hash())?;
        }
        if let Some(gap) = gap {
            effective.require_gap(gap)?;
        }
        validate_optional_gap(gap, &decision.input)?;
        if let Some(macd) = macd {
            price.require_live_macd(macd, &decision.scope, &decision.input)?;
        }
        let expected = live_evidence_hash(price, refinement, macd, gap, other_evidence_hash)?;
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
            require_gap(gap, &decision.input)?;
            return Err(Error::Unready(
                "Strategy 350 live MACD evidence is not bound to this decision".into(),
            ));
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
        effective,
        market_scope,
        input,
        safety,
        price,
        expected_price_gate_hash,
        maximum_price_age_ns,
        refinement,
        macd,
        gap,
        other_evidence_hash,
    } = request;
    let scope = runtime.scope().clone();
    effective.require_scope(&scope)?;
    effective.require_price_gate(expected_price_gate_hash)?;
    if let Some(macd) = macd {
        effective.require_macd(macd.configuration_hash())?;
    }
    if let Some(gap) = gap {
        effective.require_gap(gap)?;
    }
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
    validate_optional_gap(gap, &input)?;
    if let Some(macd) = macd {
        price.require_live_macd(macd, &scope, &input)?;
    }
    let evidence_hash = live_evidence_hash(price, refinement, macd, gap, other_evidence_hash)?;
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
            require_gap(gap, &input)?;
            return Err(Error::Unready(
                "Strategy 350 live MACD evidence is not bound to this decision".into(),
            ));
        }
        Ok(actions)
    })
}
