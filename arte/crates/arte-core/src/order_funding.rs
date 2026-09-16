//! Same-currency cash reservation for long bracket plans. No leverage or FX inference.
use crate::{
    decision_orders::Plan,
    orders::{Bands, RiskPolicy, Side},
    portfolio::{Portfolio, Reservation},
    Error, Result,
};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub currency_scale: u8,
    pub maximum_order_cash_minor: u64,
    pub maximum_order_risk_minor: u64,
    pub fee_reserve_minor: u64,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct Funding {
    pub command_id: String,
    pub account: String,
    pub cash_minor: u64,
    pub stop_risk_minor: u64,
    pub plan_hash: String,
}
/// Exact whole-share sizing. Limits include fees; round quantity down to the lot.
/// This proposal does not reserve funds or certify the freshness of available cash.
#[allow(clippy::too_many_arguments)]
pub fn quantity(
    entry: u64,
    stop: u64,
    price_scale: u8,
    policy: &Policy,
    available_cash_minor: u64,
    maximum_quantity: u64,
    lot_size: u64,
) -> Result<u64> {
    if stop == 0
        || entry <= stop
        || price_scale > 9
        || policy.currency_scale > 9
        || maximum_quantity == 0
        || lot_size == 0
        || policy.maximum_order_cash_minor == 0
        || policy.maximum_order_risk_minor == 0
    {
        return Err(Error::Invalid("invalid long sizing operands".into()));
    }
    let cash = available_cash_minor
        .min(policy.maximum_order_cash_minor)
        .checked_sub(policy.fee_reserve_minor)
        .ok_or_else(|| Error::Unready("cash cannot cover fees".into()))?;
    let risk = policy
        .maximum_order_risk_minor
        .checked_sub(policy.fee_reserve_minor)
        .ok_or_else(|| Error::Unready("risk budget cannot cover fees".into()))?;
    let capacity = |budget: u64, price: u64| -> u128 {
        u128::from(budget) * 10_u128.pow(u32::from(price_scale))
            / (u128::from(price) * 10_u128.pow(u32::from(policy.currency_scale)))
    };
    let count = capacity(cash, entry)
        .min(capacity(risk, entry - stop))
        .min(u128::from(maximum_quantity)) as u64;
    let count = count / lot_size * lot_size;
    if count == 0 {
        return Err(Error::Unready("budget cannot fund one approved lot".into()));
    }
    Ok(count)
}
/// Size from an account snapshot and atomically reserve through Portfolio. Another
/// ticker may consume funds between these steps; then reservation fails without
/// changing this plan. Caller retains a successful result for exact retry.
#[allow(clippy::too_many_arguments)]
pub fn size_and_reserve(
    portfolio: &Portfolio,
    maximum_plan: &Plan,
    lot_size: u64,
    policy: &Policy,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    risk: &RiskPolicy,
) -> Result<(Plan, Funding)> {
    maximum_plan
        .bracket
        .validate(now_ns, regular, bands, risk)?;
    if maximum_plan.bracket.side != Side::Long {
        return Err(Error::Invalid(
            "sizing only supports long cash orders".into(),
        ));
    }
    let account = portfolio.snapshot(&maximum_plan.bracket.account)?;
    if account
        .reservations
        .contains_key(&maximum_plan.bracket.command_id)
    {
        return Err(Error::Conflict(
            "already sized command: retry its retained plan, do not resize".into(),
        ));
    }
    let used = account
        .reservations
        .values()
        .try_fold(0_u64, |sum, r| sum.checked_add(r.cash_minor))
        .ok_or_else(|| Error::Invalid("reserved cash overflow".into()))?;
    let available = account
        .budget_minor
        .min(account.broker_available_minor)
        .saturating_sub(used);
    let mut plan = maximum_plan.clone();
    plan.bracket.quantity = quantity(
        plan.bracket.entry as u64,
        plan.bracket.stop.unwrap() as u64,
        plan.bracket.price_scale,
        policy,
        available,
        plan.bracket.quantity,
        lot_size,
    )?;
    let funding = reserve(portfolio, &plan, policy, now_ns, regular, bands, risk)?;
    Ok((plan, funding))
}
/// Round required money upward, never available cash or permissible quantity upward.
fn money(price: u64, quantity: u64, price_scale: u8, currency_scale: u8) -> Result<u64> {
    if price_scale > 9 || currency_scale > 9 || price == 0 || quantity == 0 {
        return Err(Error::Invalid("invalid cash conversion operands".into()));
    }
    let product = u128::from(price)
        .checked_mul(u128::from(quantity))
        .and_then(|v| v.checked_mul(10_u128.pow(u32::from(currency_scale))))
        .ok_or_else(|| Error::Invalid("cash conversion overflow".into()))?;
    let denominator = 10_u128.pow(u32::from(price_scale));
    u64::try_from(product.div_ceil(denominator))
        .map_err(|_| Error::Invalid("cash requirement exceeds supported amount".into()))
}
/// Caller must certify account and instrument settlement currency equality. Risk
/// here is nominal entry-to-stop distance, not a guaranteed maximum realized loss.
pub fn requirements(
    plan: &Plan,
    policy: &Policy,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    risk: &RiskPolicy,
) -> Result<Funding> {
    plan.validate_scope()?;
    let bracket = &plan.bracket;
    bracket.validate(now_ns, regular, bands, risk)?;
    if bracket.side != Side::Long
        || policy.maximum_order_cash_minor == 0
        || policy.maximum_order_risk_minor == 0
    {
        return Err(Error::Invalid(
            "positive long cash and risk mandates required".into(),
        ));
    }
    let stop_distance = bracket
        .entry
        .checked_sub(bracket.stop.unwrap())
        .and_then(|v| u64::try_from(v).ok())
        .ok_or_else(|| Error::Invalid("invalid entry-to-stop distance".into()))?;
    let cash = money(
        bracket.entry as u64,
        bracket.quantity,
        plan.bracket.price_scale,
        policy.currency_scale,
    )?
    .checked_add(policy.fee_reserve_minor)
    .ok_or_else(|| Error::Invalid("fee reserve overflow".into()))?;
    let stop_risk = money(
        stop_distance,
        bracket.quantity,
        plan.bracket.price_scale,
        policy.currency_scale,
    )?
    .checked_add(policy.fee_reserve_minor)
    .ok_or_else(|| Error::Invalid("risk reserve overflow".into()))?;
    if cash > policy.maximum_order_cash_minor || stop_risk > policy.maximum_order_risk_minor {
        return Err(Error::Unready(
            "order exceeds cash or nominal stop-risk mandate".into(),
        ));
    }
    let funding = Funding {
        command_id: bracket.command_id.clone(),
        account: bracket.account.clone(),
        cash_minor: cash,
        stop_risk_minor: stop_risk,
        plan_hash: crate::content_hash(plan)?,
    };
    Ok(funding)
}
pub fn reserve(
    portfolio: &Portfolio,
    plan: &Plan,
    policy: &Policy,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    risk: &RiskPolicy,
) -> Result<Funding> {
    let funding = requirements(plan, policy, now_ns, regular, bands, risk)?;
    let bracket = &plan.bracket;
    portfolio.reserve(
        &bracket.account,
        Reservation {
            command_id: bracket.command_id.clone(),
            instrument: bracket.instrument,
            cash_minor: funding.cash_minor,
        },
        now_ns,
    )?;
    Ok(funding)
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    #[test]
    fn sizing_floors_cash_risk_and_lot_limits() {
        let policy = Policy {
            currency_scale: 2,
            maximum_order_cash_minor: 10010,
            maximum_order_risk_minor: 510,
            fee_reserve_minor: 10,
        };
        assert_eq!(quantity(1000, 900, 2, &policy, 10010, 100, 1).unwrap(), 5);
        assert_eq!(quantity(1000, 900, 2, &policy, 4010, 100, 3).unwrap(), 3);
        assert!(quantity(1000, 900, 2, &policy, 1009, 100, 1).is_err());
        for available in 10..2000 {
            if let Ok(count) = quantity(10001, 9001, 3, &policy, available, 100, 1) {
                assert!(money(10001, count, 3, 2).unwrap() + 10 <= available);
                assert!(money(1000, count, 3, 2).unwrap() + 10 <= policy.maximum_order_risk_minor);
            }
        }
    }
    fn plan(id: &str) -> Plan {
        Plan {
            scope: crate::strategy_dispatch::Scope {
                run_id: "r".into(),
                mode: crate::strategy_dispatch::Mode::Backtest,
                account: "a".into(),
                instrument: 1,
                strategy_instance: "s".into(),
                code_hash: "code".into(),
                config_hash: "config".into(),
            },
            decision_id: "decision".into(),
            action_index: 0,
            bracket: crate::orders::Bracket {
                price_scale: 2,
                command_id: id.into(),
                account: "a".into(),
                instrument: 1,
                side: Side::Long,
                quantity: 6,
                entry: 1000,
                stop: Some(900),
                target: Some(1200),
                tick: 1,
                deadline_ns: 100,
            },
        }
    }
    #[test]
    fn conversion_rounds_required_money_up_and_rejects_overflow() {
        assert_eq!(money(10001, 3, 3, 2).unwrap(), 3001);
        assert_eq!(money(10, 3, 0, 2).unwrap(), 3000);
        assert!(money(u64::MAX, u64::MAX, 0, 9).is_err());
        assert!(money(10, 0, 2, 2).is_err());
    }
    #[test]
    fn plans_share_one_account_budget_and_retry_without_double_reserving() {
        let portfolio = Portfolio::new(BTreeMap::from([(
            "a".into(),
            crate::portfolio::Account {
                currency: "USD".into(),
                currency_scale: 2,
                simulation_run_id: Some("r".into()),
                budget_minor: 10000,
                broker_available_minor: 10000,
                balance_at_ns: 1,
                max_balance_age_ns: 10,
                reservations: BTreeMap::new(),
            },
        )]))
        .unwrap();
        let policy = Policy {
            currency_scale: 2,
            maximum_order_cash_minor: 7000,
            maximum_order_risk_minor: 700,
            fee_reserve_minor: 10,
        };
        let risk = RiskPolicy {
            band_provider: 1,
            band_session: 20260915,
            band_buffer_ticks: 3,
            max_band_age_ns: 10,
        };
        let funding = reserve(&portfolio, &plan("one"), &policy, 2, false, None, &risk).unwrap();
        assert_eq!(funding.cash_minor, 6010);
        assert_eq!(funding.stop_risk_minor, 610);
        reserve(&portfolio, &plan("one"), &policy, 2, false, None, &risk).unwrap();
        assert!(reserve(&portfolio, &plan("two"), &policy, 2, false, None, &risk).is_err());
        assert_eq!(portfolio.snapshot("a").unwrap().reservations.len(), 1);
        let mut strict = policy.clone();
        strict.maximum_order_risk_minor = 609;
        assert!(reserve(&portfolio, &plan("three"), &strict, 2, false, None, &risk).is_err());
        assert_eq!(portfolio.snapshot("a").unwrap().reservations.len(), 1);
        let (sized, funding) =
            size_and_reserve(&portfolio, &plan("two"), 1, &policy, 2, false, None, &risk).unwrap();
        assert_eq!(sized.bracket.quantity, 3);
        assert_eq!(funding.cash_minor, 3010);
        assert!(
            size_and_reserve(&portfolio, &plan("two"), 1, &policy, 2, false, None, &risk).is_err()
        );
        reserve(&portfolio, &sized, &policy, 2, false, None, &risk).unwrap();
        assert_eq!(portfolio.snapshot("a").unwrap().reservations.len(), 2);
    }
}
