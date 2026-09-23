//! Pinned Strategy 350 computation identity shared by live and backtest.
//! A hash in a run manifest is not enough unless its component contract is
//! explicit. This value contains no runtime state or broker authority.
use crate::{
    content_hash, execution_interval::ExecutionInterval, strategy350_gap::Config as GapConfig,
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const VERSION: &str = "arte.strategy-350-effective.v1";

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub gap: GapConfig,
    pub signal_config_hash: String,
    pub screen_config_hash: String,
    pub price_gate_config_hash: String,
    pub macd_config_hash: String,
    pub noise_config_hash: String,
    pub bos_config_hash: String,
    pub level_book_config_hash: String,
    pub rule_set_hash: String,
    pub account_risk_hash: String,
    pub watchlist_config_hash: Option<String>,
}

fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}

impl Config {
    pub fn validate(&self) -> Result<()> {
        self.execution_interval.validate()?;
        self.gap.hash()?;
        if [
            &self.signal_config_hash,
            &self.screen_config_hash,
            &self.price_gate_config_hash,
            &self.macd_config_hash,
            &self.noise_config_hash,
            &self.bos_config_hash,
            &self.level_book_config_hash,
            &self.rule_set_hash,
            &self.account_risk_hash,
        ]
        .iter()
        .any(|hash| !hash_valid(hash))
            || self
                .watchlist_config_hash
                .as_ref()
                .is_some_and(|hash| !hash_valid(hash))
        {
            return Err(Error::Invalid(
                "Strategy 350 effective component hash".into(),
            ));
        }
        Ok(())
    }

    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&(VERSION, self))
    }

    pub fn require_scope(&self, scope: &crate::strategy_dispatch::Scope) -> Result<()> {
        if scope.strategy_kind != crate::strategy_dispatch::StrategyKind::Strategy350
            || scope.execution_interval != self.execution_interval
            || scope.config_hash != self.hash()?
        {
            return Err(Error::Conflict(
                "Strategy 350 effective configuration differs from run".into(),
            ));
        }
        Ok(())
    }

    pub fn require_gap(&self, gap: &crate::strategy350_gap::FrozenGap) -> Result<()> {
        gap.validate()?;
        if gap.configuration_hash != self.gap.hash()? {
            return Err(Error::Conflict(
                "Strategy 350 frozen gap configuration differs".into(),
            ));
        }
        Ok(())
    }

    pub fn require_price_gate(&self, configuration_hash: &str) -> Result<()> {
        if self.price_gate_config_hash != configuration_hash {
            return Err(Error::Conflict(
                "Strategy 350 price gate configuration differs".into(),
            ));
        }
        Ok(())
    }

    pub fn require_macd(&self, configuration_hash: &str) -> Result<()> {
        if self.macd_config_hash != configuration_hash {
            return Err(Error::Conflict(
                "Strategy 350 MACD configuration differs".into(),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
pub(crate) fn test_config(execution_interval: ExecutionInterval) -> Config {
    Config {
        execution_interval,
        gap: GapConfig {
            execution_interval: ExecutionInterval::Fixed(100_000_000),
            maximum_levels: 1_000,
        },
        signal_config_hash: "a".repeat(64),
        screen_config_hash: "b".repeat(64),
        price_gate_config_hash: "c".repeat(64),
        macd_config_hash: "d".repeat(64),
        noise_config_hash: "e".repeat(64),
        bos_config_hash: "f".repeat(64),
        level_book_config_hash: "1".repeat(64),
        rule_set_hash: "2".repeat(64),
        account_risk_hash: "3".repeat(64),
        watchlist_config_hash: None,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn config() -> Config {
        test_config(ExecutionInterval::Events)
    }

    #[test]
    fn every_component_and_cadence_changes_effective_identity() {
        let base = config();
        let hash = base.hash().unwrap();
        let mut changed = base.clone();
        changed.gap.maximum_levels += 1;
        assert_ne!(changed.hash().unwrap(), hash);
        changed = base.clone();
        changed.bos_config_hash = "4".repeat(64);
        assert_ne!(changed.hash().unwrap(), hash);
        changed = base.clone();
        changed.watchlist_config_hash = Some("5".repeat(64));
        assert_ne!(changed.hash().unwrap(), hash);
        changed = base.clone();
        changed.execution_interval = ExecutionInterval::Fixed(100_000_000);
        assert_ne!(changed.hash().unwrap(), hash);
        changed = base.clone();
        changed.account_risk_hash = "invalid".into();
        assert!(changed.hash().is_err());
    }

    #[test]
    fn run_scope_and_frozen_gap_must_match_bundle() {
        let config = config();
        let scope = crate::strategy_dispatch::Scope {
            run_id: "run".into(),
            mode: crate::strategy_dispatch::Mode::Backtest,
            account: "account".into(),
            strategy_instance: "strategy-350".into(),
            strategy_kind: crate::strategy_dispatch::StrategyKind::Strategy350,
            execution_interval: ExecutionInterval::Events,
            instrument: 1,
            code_hash: "a".repeat(64),
            config_hash: config.hash().unwrap(),
        };
        config.require_scope(&scope).unwrap();
        let gap = crate::strategy350_gap::freeze(&[], 10., 100_000_000, &config.gap).unwrap();
        config.require_gap(&gap).unwrap();
        let other = crate::strategy350_gap::freeze(
            &[],
            10.,
            100_000_000,
            &GapConfig {
                maximum_levels: 1_001,
                ..config.gap.clone()
            },
        )
        .unwrap();
        assert!(config.require_gap(&other).is_err());
        let mut foreign = scope;
        foreign.config_hash = "0".repeat(64);
        assert!(config.require_scope(&foreign).is_err());
    }
}
