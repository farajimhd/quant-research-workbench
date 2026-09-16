//! Reproducible sizing outcomes. An unfundable proposal is not a transport error.
//! These operands do not prove quote freshness, cash ownership or order approval.
use super::Policy;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Input {
    pub entry: u64,
    pub stop: u64,
    pub price_scale: u8,
    pub policy: Policy,
    pub available_cash_minor: u64,
    pub maximum_quantity: u64,
    pub lot_size: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Rejection {
    CashCannotCoverFees,
    RiskCannotCoverFees,
    NoApprovedLot,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(
    tag = "outcome",
    content = "value",
    rename_all = "snake_case",
    deny_unknown_fields
)]
pub enum Outcome {
    Sized(u64),
    Rejected(Rejection),
}
impl Outcome {
    /// Compatibility boundary for callers that have not adopted typed rejection.
    /// Never classify generic Unready errors by comparing their message text.
    pub fn quantity(self) -> Result<u64> {
        match self {
            Self::Sized(count) if count > 0 => Ok(count),
            Self::Sized(_) => Err(Error::Invalid("zero sized quantity".into())),
            Self::Rejected(reason) => Err(Error::Unready(
                match reason {
                    Rejection::CashCannotCoverFees => "cash cannot cover fees",
                    Rejection::RiskCannotCoverFees => "risk budget cannot cover fees",
                    Rejection::NoApprovedLot => "budget cannot fund one approved lot",
                }
                .into(),
            )),
        }
    }
}
impl Input {
    /// Pure integer calculation shared by historical and live execution.
    /// Invalid configuration remains an error, never a terminal trade rejection.
    /// When both fee limits fail, cash has deterministic precedence.
    pub fn evaluate(&self) -> Result<Outcome> {
        let policy = &self.policy;
        if self.stop == 0
            || self.entry <= self.stop
            || self.price_scale > 9
            || policy.currency_scale > 9
            || self.maximum_quantity == 0
            || self.lot_size == 0
            || policy.maximum_order_cash_minor == 0
            || policy.maximum_order_risk_minor == 0
        {
            return Err(Error::Invalid("invalid long sizing operands".into()));
        }
        let Some(cash) = self
            .available_cash_minor
            .min(policy.maximum_order_cash_minor)
            .checked_sub(policy.fee_reserve_minor)
        else {
            return Ok(Outcome::Rejected(Rejection::CashCannotCoverFees));
        };
        let Some(risk) = policy
            .maximum_order_risk_minor
            .checked_sub(policy.fee_reserve_minor)
        else {
            return Ok(Outcome::Rejected(Rejection::RiskCannotCoverFees));
        };
        let capacity = |budget: u64, price: u64| -> u128 {
            u128::from(budget) * 10_u128.pow(u32::from(self.price_scale))
                / (u128::from(price) * 10_u128.pow(u32::from(policy.currency_scale)))
        };
        let count = capacity(cash, self.entry)
            .min(capacity(risk, self.entry - self.stop))
            .min(u128::from(self.maximum_quantity)) as u64;
        let count = count / self.lot_size * self.lot_size;
        Ok(if count == 0 {
            Outcome::Rejected(Rejection::NoApprovedLot)
        } else {
            Outcome::Sized(count)
        })
    }
}

/// A serializable calculation record, not a journal receipt or authorization.
/// Recovery must validate it and separately bind operands to authoritative data.
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Assessment {
    schema_version: u32,
    input: Input,
    outcome: Outcome,
}
impl Assessment {
    pub fn new(input: Input) -> Result<Self> {
        let outcome = input.evaluate()?;
        Ok(Self {
            schema_version: 1,
            input,
            outcome,
        })
    }
    pub fn input(&self) -> &Input {
        &self.input
    }
    pub fn outcome(&self) -> Result<Outcome> {
        if self.schema_version != 1 || self.input.evaluate()? != self.outcome {
            return Err(Error::Conflict(
                "sizing assessment differs from its operands or version".into(),
            ));
        }
        Ok(self.outcome)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn input() -> Input {
        Input {
            entry: 1000,
            stop: 900,
            price_scale: 2,
            policy: Policy {
                currency_scale: 2,
                maximum_order_cash_minor: 10010,
                maximum_order_risk_minor: 510,
                fee_reserve_minor: 10,
            },
            available_cash_minor: 4010,
            maximum_quantity: 100,
            lot_size: 3,
        }
    }
    #[test]
    fn business_rejections_are_typed_and_configuration_errors_are_not() {
        let mut value = input();
        assert_eq!(value.evaluate().unwrap(), Outcome::Sized(3));
        value.available_cash_minor = 9;
        assert_eq!(
            value.evaluate().unwrap(),
            Outcome::Rejected(Rejection::CashCannotCoverFees)
        );
        value.available_cash_minor = 4010;
        value.policy.maximum_order_risk_minor = 9;
        assert_eq!(
            value.evaluate().unwrap(),
            Outcome::Rejected(Rejection::RiskCannotCoverFees)
        );
        value.policy.maximum_order_risk_minor = 510;
        value.lot_size = 6;
        assert_eq!(
            value.evaluate().unwrap(),
            Outcome::Rejected(Rejection::NoApprovedLot)
        );
        value.lot_size = 0;
        assert!(matches!(value.evaluate(), Err(Error::Invalid(_))));
        value = input();
        value.policy.maximum_order_risk_minor = 0;
        assert!(matches!(value.evaluate(), Err(Error::Invalid(_))));
        value = input();
        value.available_cash_minor = 10;
        assert_eq!(
            value.evaluate().unwrap(),
            Outcome::Rejected(Rejection::NoApprovedLot)
        );
    }
    #[test]
    fn assessment_readback_recomputes_and_rejects_forged_results() {
        let assessment = Assessment::new(input()).unwrap();
        let bytes = serde_json::to_vec(&assessment).unwrap();
        let restored: Assessment = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(restored.outcome().unwrap(), Outcome::Sized(3));
        assert_eq!(
            crate::content_hash(&assessment).unwrap(),
            crate::content_hash(&restored).unwrap()
        );
        let mut changed = restored.clone();
        changed.outcome = Outcome::Rejected(Rejection::NoApprovedLot);
        assert!(changed.outcome().is_err());
        changed = restored.clone();
        changed.input.available_cash_minor = 0;
        assert!(changed.outcome().is_err());
        changed = restored;
        changed.schema_version = 2;
        assert!(changed.outcome().is_err());
    }
    #[test]
    fn extreme_inputs_remain_bounded_and_never_round_quantity_up() {
        let mut value = input();
        value.entry = 2;
        value.stop = 1;
        value.price_scale = 9;
        value.policy.currency_scale = 0;
        value.policy.maximum_order_cash_minor = u64::MAX;
        value.policy.maximum_order_risk_minor = u64::MAX;
        value.available_cash_minor = u64::MAX;
        value.maximum_quantity = u64::MAX;
        value.lot_size = 1;
        assert_eq!(value.evaluate().unwrap(), Outcome::Sized(u64::MAX));
        value.lot_size = u64::MAX;
        assert_eq!(value.evaluate().unwrap(), Outcome::Sized(u64::MAX));
        value.maximum_quantity -= 1;
        assert_eq!(
            value.evaluate().unwrap(),
            Outcome::Rejected(Rejection::NoApprovedLot)
        );
    }
}
