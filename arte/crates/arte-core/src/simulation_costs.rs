//! Explicit hypothetical costs, not a broker fee schedule or settlement authority.
use crate::{
    content_hash,
    execution_events::{Fill, Origin},
    run_manifest::{Execution, Pinned as Run},
    strategy_dispatch::Mode,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
pub mod cash;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Model {
    pub schema_version: u32,
    /// Account and instrument settlement currency must be certified separately.
    pub currency: String,
    pub currency_scale: u8,
    pub fixed_per_fill_minor: u64,
    /// Currency units per share, as atoms / 10^per_share_scale.
    pub per_share_atoms: u64,
    pub per_share_scale: u8,
    /// Applied separately to each modeled partial fill, after fixed + variable.
    pub minimum_per_fill_minor: u64,
}
impl Model {
    pub fn hash(&self) -> Result<String> {
        if self.schema_version != 1
            || self.currency.len() != 3
            || !self.currency.bytes().all(|v| v.is_ascii_uppercase())
            || self.currency_scale > 9
            || self.per_share_scale > 9
        {
            return Err(Error::Invalid("invalid simulated cost model".into()));
        }
        content_hash(&("arte.per-fill-costs.v1", self))
    }
    fn fee(&self, quantity: u64) -> Result<u64> {
        let numerator = u128::from(quantity)
            .checked_mul(u128::from(self.per_share_atoms))
            .and_then(|v| v.checked_mul(10_u128.pow(u32::from(self.currency_scale))))
            .ok_or_else(|| Error::Capacity("simulated fee overflow".into()))?;
        let divisor = 10_u128.pow(u32::from(self.per_share_scale));
        let variable = numerator / divisor + u128::from(numerator % divisor != 0);
        let fee = variable
            .checked_add(u128::from(self.fixed_per_fill_minor))
            .ok_or_else(|| Error::Capacity("simulated fee overflow".into()))?
            .max(u128::from(self.minimum_per_fill_minor));
        u64::try_from(fee)
            .map_err(|_| Error::Capacity("simulated fee exceeds currency capacity".into()))
    }
}
/// Validated once, shared read-only across instrument lanes. No defaults are applied.
pub struct Pinned {
    model: Model,
    hash: String,
    fill_model_hash: String,
    run_id: String,
    manifest_hash: String,
    reference_manifest_hash: String,
    consumers: BTreeSet<(String, u64)>,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Charge {
    pub fill_id: String,
    pub cost_model_hash: String,
    pub fee_minor: u64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SettlementCurrency {
    pub instrument: u64,
    pub currency: String,
    pub available_at_ns: u64,
    pub reference_manifest_hash: String,
}
impl Pinned {
    pub fn new(model: Model, run: &Run) -> Result<Self> {
        let hash = model.hash()?;
        let manifest = run.manifest();
        if manifest.mode != Mode::Backtest
            || !matches!(&manifest.execution,
            Execution::Simulated {cost_model_hash, ..} if cost_model_hash == &hash)
        {
            return Err(Error::Conflict(
                "cost model differs from simulated run manifest".into(),
            ));
        }
        Ok(Self {
            model,
            hash,
            fill_model_hash: match &manifest.execution {
                Execution::Simulated {
                    fill_model_hash, ..
                } => fill_model_hash.clone(),
                _ => unreachable!("validated simulated execution"),
            },
            run_id: manifest.run_id.clone(),
            manifest_hash: run.hash().into(),
            reference_manifest_hash: manifest.reference_manifest_hash.clone(),
            consumers: manifest
                .consumers
                .iter()
                .map(|c| (c.account.clone(), c.instrument))
                .collect(),
        })
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
    pub fn manifest_hash(&self) -> &str {
        &self.manifest_hash
    }
    pub fn fill_model_hash(&self) -> &str {
        &self.fill_model_hash
    }
    pub fn run_id(&self) -> &str {
        &self.run_id
    }
    pub fn model(&self) -> &Model {
        &self.model
    }
    pub fn require_currency(
        &self,
        instrument: u64,
        evidence: &SettlementCurrency,
        at_ns: u64,
    ) -> Result<()> {
        if evidence.instrument != instrument
            || evidence.currency != self.model.currency
            || evidence.reference_manifest_hash != self.reference_manifest_hash
            || evidence.available_at_ns > at_ns
        {
            return Err(Error::Conflict(
                "settlement currency evidence differs or is future".into(),
            ));
        }
        Ok(())
    }
    pub fn charge(&self, fill: &Fill) -> Result<Charge> {
        let fill_id = fill.id()?;
        if !matches!(&fill.origin, Origin::Simulated { run_id, model }
            if run_id == &self.run_id && model == crate::simulated_execution::MODEL)
            || !self
                .consumers
                .contains(&(fill.account.clone(), fill.instrument))
        {
            return Err(Error::Conflict(
                "fill escaped cost model run or consumer scope".into(),
            ));
        }
        Ok(Charge {
            fill_id,
            cost_model_hash: self.hash.clone(),
            fee_minor: self.model.fee(fill.quantity)?,
        })
    }
    /// Convert aggregate closed trade cash, then subtract charged fees. Positive
    /// fractions round down and negative fractions round toward minus infinity.
    /// This is modeled net cash, not evidence of broker or exchange settlement.
    pub fn net_cash_minor(
        &self,
        trade_cash_atoms: i128,
        price_scale: u8,
        fees_minor: u64,
    ) -> Result<i128> {
        if price_scale > 9 {
            return Err(Error::Invalid("cash price scale exceeds nine".into()));
        }
        let currency_scale = self.model.currency_scale;
        let gross = if currency_scale >= price_scale {
            trade_cash_atoms
                .checked_mul(10_i128.pow(u32::from(currency_scale - price_scale)))
                .ok_or_else(|| Error::Capacity("cash rescale overflow".into()))?
        } else {
            trade_cash_atoms.div_euclid(10_i128.pow(u32::from(price_scale - currency_scale)))
        };
        gross
            .checked_sub(i128::from(fees_minor))
            .ok_or_else(|| Error::Capacity("net cash overflow".into()))
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        execution_events::{Direction, Leg},
        run_manifest::{Clock, Consumer, Manifest},
    };
    pub(super) fn model() -> Model {
        Model {
            schema_version: 1,
            currency: "USD".into(),
            currency_scale: 2,
            fixed_per_fill_minor: 1,
            per_share_atoms: 5,
            per_share_scale: 3,
            minimum_per_fill_minor: 2,
        }
    }
    pub(super) fn run(cost_model_hash: String) -> Run {
        let manifest = Manifest {
            schema_version: 1,
            run_id: "r".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: "a".repeat(64),
            reference_manifest_hash: "a".repeat(64),
            seed_manifest_hash: "a".repeat(64),
            algorithm_manifest_hash: "a".repeat(64),
            dependency_plan_hash: "a".repeat(64),
            hardware_profile_hash: "a".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "b".repeat(64),
                cost_model_hash,
            },
            consumers: vec![Consumer {
                account: "a".into(),
                instrument: 1,
                strategy_instance: "s".into(),
                effective_config_hash: "c".repeat(64),
            }],
        };
        let hash = manifest.hash().unwrap();
        Run::new(manifest, &hash).unwrap()
    }
    pub(super) fn fill(quantity: u64) -> Fill {
        Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "r".into(),
                model: crate::simulated_execution::MODEL.into(),
            },
            command_id: "c".into(),
            account: "a".into(),
            instrument: 1,
            price_scale: 2,
            sequence: 1,
            at_ns: 1,
            executed_at_ns: Some(1),
            leg: Leg::Entry,
            direction: Direction::Buy,
            quantity,
            price: 100,
        }
    }
    #[test]
    fn fees_are_exact_per_fill_and_partial_fill_costs_are_explicit() {
        let m = model();
        let p = Pinned::new(m.clone(), &run(m.hash().unwrap())).unwrap();
        assert_eq!(p.charge(&fill(1)).unwrap().fee_minor, 2);
        assert_eq!(p.charge(&fill(3)).unwrap().fee_minor, 3);
        assert_eq!(p.charge(&fill(6)).unwrap().fee_minor, 4);
        assert_eq!(p.charge(&fill(3)).unwrap().fee_minor * 2, 6);
        assert_eq!(p.net_cash_minor(199, 3, 2).unwrap(), 17);
        assert_eq!(p.net_cash_minor(-199, 3, 2).unwrap(), -22);
        assert!(p.net_cash_minor(i128::MAX, 0, 0).is_err());
        assert!(p.net_cash_minor(i128::MIN, 2, 1).is_err());
    }
    #[test]
    fn wrong_models_consumers_origins_and_overflow_are_rejected() {
        let m = model();
        assert!(Pinned::new(m.clone(), &run("f".repeat(64))).is_err());
        let p = Pinned::new(m.clone(), &run(m.hash().unwrap())).unwrap();
        let mut f = fill(1);
        f.account = "b".into();
        assert!(p.charge(&f).is_err());
        f = fill(1);
        f.origin = Origin::Broker {
            session_id: "s".into(),
            execution_id: "e".into(),
            paper: true,
        };
        assert!(p.charge(&f).is_err());
        f = fill(1);
        f.quantity = 0;
        assert!(p.charge(&f).is_err());
        let mut huge = model();
        huge.per_share_atoms = u64::MAX;
        huge.per_share_scale = 0;
        let p = Pinned::new(huge.clone(), &run(huge.hash().unwrap())).unwrap();
        assert!(p.charge(&fill(u64::MAX)).is_err());
        let mut invalid = model();
        invalid.currency = "usd".into();
        assert!(invalid.hash().is_err());
    }
}
