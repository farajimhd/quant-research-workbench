//! Committed long-candidate intents to complete bracket plans. Not authorization.
use crate::{
    content_hash,
    events::Decimal,
    orders::{Bands, Bracket, RiskPolicy, Side},
    strategy350_transaction::CommittedMarketDecision,
    strategy_dispatch::Action,
    strategy_transaction::Committed,
    Error, Result,
};
use serde::{Deserialize, Serialize};

/// Portfolio owns quantity. The execution quote authority supplies the limit price.
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Allocation {
    pub account: String,
    pub instrument: u64,
    pub quantity: u64,
    pub price_scale: u8,
    pub tick: i64,
    pub entry_limit: i64,
    pub deadline_ns: u64,
}
fn exact_price(price: f64, scale: u8) -> Result<i64> {
    if !price.is_finite() || price <= 0. || scale > 9 {
        return Err(Error::Invalid(
            "invalid strategy price or instrument scale".into(),
        ));
    }
    let decimal = Decimal::parse(&price.to_string())?;
    decimal.atoms_at_scale(scale)
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Plan {
    pub scope: crate::strategy_dispatch::Scope,
    pub decision_id: String,
    pub action_index: usize,
    pub bracket: Bracket,
}
impl Plan {
    pub fn validate_scope(&self) -> Result<()> {
        crate::strategy_dispatch::State::new(self.scope.clone())?;
        if self.scope.account != self.bracket.account
            || self.scope.instrument != self.bracket.instrument
        {
            return Err(Error::Conflict(
                "bracket escaped originating strategy scope".into(),
            ));
        }
        Ok(())
    }
}
fn require_strategy_order_authority(scope: &crate::strategy_dispatch::Scope) -> Result<()> {
    if scope.strategy_kind == crate::strategy_dispatch::StrategyKind::Strategy350 {
        return Err(Error::Unready(
            "Strategy 350 order requires causal price-gate decision proof".into(),
        ));
    }
    Ok(())
}
/// The selected candidate is long-only. Do not infer a short/reversal from quantity.
/// Entry and add plans still require cash reservation, market readiness, durable
/// authorization and a final submission-time revalidation in OrderLedger.
pub fn bracket(
    committed: &Committed,
    action_index: usize,
    allocation: &Allocation,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    policy: &RiskPolicy,
) -> Result<Plan> {
    let decision = committed.decision();
    require_strategy_order_authority(&decision.scope)?;
    bracket_from_decision(
        decision,
        action_index,
        allocation,
        now_ns,
        regular,
        bands,
        policy,
    )
}

/// Strategy 350 can plan only from a readback-bound price-gate decision.
/// Cash reservation, market readiness, durable authorization and submission
/// revalidation are separate mandatory gates downstream.
pub fn bracket_350(
    committed: &CommittedMarketDecision<'_>,
    action_index: usize,
    allocation: &Allocation,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    policy: &RiskPolicy,
) -> Result<Plan> {
    let decision = committed.require_at(now_ns)?;
    bracket_from_decision(
        decision,
        action_index,
        allocation,
        now_ns,
        regular,
        bands,
        policy,
    )
}

fn bracket_from_decision(
    decision: &crate::strategy_dispatch::Decision,
    action_index: usize,
    allocation: &Allocation,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    policy: &RiskPolicy,
) -> Result<Plan> {
    if allocation.account != decision.scope.account
        || allocation.instrument != decision.scope.instrument
        || now_ns < decision.input.evaluated_at_ns
        || allocation.price_scale > 9
    {
        return Err(Error::Conflict(
            "order allocation escaped committed decision scope or clock".into(),
        ));
    }
    let (stop, target, maximum) = match decision.actions.get(action_index) {
        Some(Action::Enter(p)) => (p.stop, p.target, p.maximum_buy_price),
        Some(Action::Add(p)) => (p.stop, p.target, p.maximum_buy_price),
        _ => {
            return Err(Error::Invalid(
                "selected action is not an exposure-increasing proposal".into(),
            ))
        }
    };
    if !maximum.is_finite() || maximum <= 0. {
        return Err(Error::Invalid("invalid strategy maximum buy price".into()));
    }
    // A comparison bound need not itself be a tradable price. Compare exact
    // decimals at a common scale; never round the cap into an order price.
    let maximum = Decimal::parse(&maximum.to_string())?;
    let scale = maximum.scale.max(allocation.price_scale);
    let limit =
        i128::from(allocation.entry_limit) * 10_i128.pow(u32::from(scale - allocation.price_scale));
    let maximum = i128::from(maximum.atoms) * 10_i128.pow(u32::from(scale - maximum.scale));
    if limit > maximum {
        return Err(Error::Invalid(
            "entry exceeds strategy maximum buy price".into(),
        ));
    }
    let bracket = Bracket {
        command_id: content_hash(&("decision-bracket-v1", &decision.decision_id, action_index))?,
        account: decision.scope.account.clone(),
        instrument: decision.scope.instrument,
        side: Side::Long,
        quantity: allocation.quantity,
        entry: allocation.entry_limit,
        price_scale: allocation.price_scale,
        stop: Some(exact_price(stop, allocation.price_scale)?),
        target: Some(exact_price(target, allocation.price_scale)?),
        tick: allocation.tick,
        deadline_ns: allocation.deadline_ns,
    };
    bracket.validate(now_ns, regular, bands, policy)?;
    Ok(Plan {
        scope: decision.scope.clone(),
        decision_id: decision.decision_id.clone(),
        action_index,
        bracket,
    })
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_dispatch::{Mode, Scope, StrategyKind};
    #[test]
    fn strategy_350_cannot_use_ungated_generic_order_planner() {
        let mut scope = Scope {
            run_id: "run".into(),
            mode: Mode::Backtest,
            account: "account".into(),
            strategy_instance: crate::strategy350_catalogue::STRATEGY.into(),
            strategy_kind: StrategyKind::Strategy350,
            execution_interval: crate::execution_interval::ExecutionInterval::Fixed(100_000_000),
            instrument: 10,
            code_hash: "code".into(),
            config_hash: "config".into(),
        };
        assert!(require_strategy_order_authority(&scope).is_err());
        scope.mode = Mode::Live;
        assert!(require_strategy_order_authority(&scope).is_err());
        scope.strategy_instance = "independent-candidate".into();
        assert!(require_strategy_order_authority(&scope).is_err());
        scope.strategy_kind = StrategyKind::GenericCandidate;
        assert!(require_strategy_order_authority(&scope).is_ok());
    }
    #[test]
    fn exact_conversion_never_rounds_or_overflows() {
        assert_eq!(exact_price(10.25, 2).unwrap(), 1025);
        assert_eq!(exact_price(10., 4).unwrap(), 100000);
        for (price, scale) in [
            (10.251, 2),
            (f64::NAN, 2),
            (f64::INFINITY, 2),
            (-1., 2),
            (1e20, 9),
            (1., 10),
        ] {
            assert!(exact_price(price, scale).is_err());
        }
    }
}
