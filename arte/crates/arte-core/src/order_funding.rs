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
#[derive(Debug, Clone, Serialize)]
pub struct Funding {
    pub command_id: String,
    pub account: String,
    pub cash_minor: u64,
    pub stop_risk_minor: u64,
    pub plan_hash: String,
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
pub fn reserve(
    portfolio: &Portfolio,
    plan: &Plan,
    policy: &Policy,
    now_ns: u64,
    regular: bool,
    bands: Option<&Bands>,
    risk: &RiskPolicy,
) -> Result<Funding> {
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
        plan.price_scale,
        policy.currency_scale,
    )?
    .checked_add(policy.fee_reserve_minor)
    .ok_or_else(|| Error::Invalid("fee reserve overflow".into()))?;
    let stop_risk = money(
        stop_distance,
        bracket.quantity,
        plan.price_scale,
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
    portfolio.reserve(
        &bracket.account,
        Reservation {
            command_id: bracket.command_id.clone(),
            instrument: bracket.instrument,
            cash_minor: cash,
        },
        now_ns,
    )?;
    Ok(funding)
}
#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;
    fn plan(id: &str) -> Plan {
        Plan {
            decision_id: "decision".into(),
            action_index: 0,
            price_scale: 2,
            bracket: crate::orders::Bracket {
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
    }
}
