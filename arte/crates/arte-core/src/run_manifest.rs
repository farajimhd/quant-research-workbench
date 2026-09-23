//! Immutable run identity. Referenced content must be independently persisted and verified.
//! A manifest is not a trading permission or proof of strategy acceptance.
use crate::{
    content_hash,
    execution_interval::ExecutionInterval,
    strategy_dispatch::{Mode, Scope, StrategyKind},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub mod storage;

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Clock {
    Live,
    Historical,
    RecordedLive,
    FaultSimulation { scenario_hash: String },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case", deny_unknown_fields)]
pub enum Execution {
    Broker {
        session_scope_hash: String,
    },
    Simulated {
        fill_model_hash: String,
        cost_model_hash: String,
    },
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Consumer {
    pub account: String,
    pub instrument: u64,
    pub strategy_instance: String,
    pub strategy_kind: StrategyKind,
    pub execution_interval: ExecutionInterval,
    pub effective_config_hash: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Manifest {
    pub schema_version: u32,
    pub run_id: String,
    pub mode: Mode,
    pub code_release_hash: String,
    /// Playback source catalog (which pins its underlying authority), or live
    /// subscription/source configuration. Never a claim to freeze future events.
    pub source_manifest_hash: String,
    pub reference_manifest_hash: String,
    pub seed_manifest_hash: String,
    pub algorithm_manifest_hash: String,
    pub dependency_plan_hash: String,
    pub hardware_profile_hash: String,
    pub clock: Clock,
    pub execution: Execution,
    /// Sorted by (account, instrument, strategy_instance), with unique keys.
    pub consumers: Vec<Consumer>,
}
fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn name_valid(name: &str) -> bool {
    !name.is_empty()
        && name.len() <= 128
        && name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b))
}
fn key(consumer: &Consumer) -> (&str, u64, &str) {
    (
        &consumer.account,
        consumer.instrument,
        &consumer.strategy_instance,
    )
}
/// Validate once at the boundary, then derive scopes by binary search. No mutable
/// manifest access is exposed, and this handle is not a persistence receipt.
pub struct Pinned {
    manifest: Manifest,
    hash: String,
}
impl Pinned {
    pub fn new(manifest: Manifest, expected_hash: &str) -> Result<Self> {
        let hash = manifest.hash()?;
        if hash != expected_hash {
            return Err(Error::Conflict("run manifest hash differs".into()));
        }
        Ok(Self { manifest, hash })
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
    pub fn manifest(&self) -> &Manifest {
        &self.manifest
    }
    pub fn scope(&self, account: &str, instrument: u64, strategy: &str) -> Result<Scope> {
        self.manifest.scope_unchecked(account, instrument, strategy)
    }
}
impl Manifest {
    pub fn validate(&self) -> Result<()> {
        if self.schema_version != 3
            || !name_valid(&self.run_id)
            || self.consumers.is_empty()
            || self.consumers.len() > 100_000
            || [
                &self.code_release_hash,
                &self.source_manifest_hash,
                &self.reference_manifest_hash,
                &self.seed_manifest_hash,
                &self.algorithm_manifest_hash,
                &self.dependency_plan_hash,
                &self.hardware_profile_hash,
            ]
            .iter()
            .any(|hash| !hash_valid(hash))
        {
            return Err(Error::Invalid(
                "run manifest version, identity, pins or capacity".into(),
            ));
        }
        match (&self.mode, &self.clock, &self.execution) {
            (Mode::Live | Mode::Paper, Clock::Live, Execution::Broker { session_scope_hash })
                if hash_valid(session_scope_hash) => {}
            (
                Mode::Backtest,
                Clock::Historical | Clock::RecordedLive | Clock::FaultSimulation { .. },
                Execution::Simulated {
                    fill_model_hash,
                    cost_model_hash,
                },
            ) if hash_valid(fill_model_hash) && hash_valid(cost_model_hash) => {}
            _ => {
                return Err(Error::Conflict(
                    "run clock, mode and execution capability disagree".into(),
                ))
            }
        }
        if let Clock::FaultSimulation { scenario_hash } = &self.clock {
            if !hash_valid(scenario_hash) {
                return Err(Error::Invalid(
                    "fault simulation must pin its scenario".into(),
                ));
            }
        }
        for consumer in &self.consumers {
            consumer.execution_interval.validate()?;
            if !name_valid(&consumer.account)
                || !name_valid(&consumer.strategy_instance)
                || consumer.instrument == 0
                || !hash_valid(&consumer.effective_config_hash)
            {
                return Err(Error::Invalid(
                    "run consumer identity or configuration pin".into(),
                ));
            }
        }
        if self
            .consumers
            .windows(2)
            .any(|pair| key(&pair[0]) >= key(&pair[1]))
        {
            return Err(Error::Conflict(
                "run consumers must be sorted and unique".into(),
            ));
        }
        Ok(())
    }
    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&("arte.run-manifest.v3", self))
    }
    /// Scope fields have one authority; consumers do not repeat run/mode/code pins.
    pub fn scope(&self, account: &str, instrument: u64, strategy: &str) -> Result<Scope> {
        self.validate()?;
        self.scope_unchecked(account, instrument, strategy)
    }
    fn scope_unchecked(&self, account: &str, instrument: u64, strategy: &str) -> Result<Scope> {
        let index = self
            .consumers
            .binary_search_by(|consumer| key(consumer).cmp(&(account, instrument, strategy)))
            .map_err(|_| Error::Unready("consumer is not declared in run manifest".into()))?;
        let consumer = &self.consumers[index];
        Ok(Scope {
            run_id: self.run_id.clone(),
            mode: self.mode,
            account: consumer.account.clone(),
            strategy_instance: consumer.strategy_instance.clone(),
            strategy_kind: consumer.strategy_kind,
            execution_interval: consumer.execution_interval,
            instrument,
            code_hash: self.code_release_hash.clone(),
            config_hash: consumer.effective_config_hash.clone(),
        })
    }
    pub fn require_scope(&self, scope: &Scope) -> Result<()> {
        if self.scope(&scope.account, scope.instrument, &scope.strategy_instance)? != *scope {
            return Err(Error::Conflict(
                "strategy scope differs from run manifest".into(),
            ));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    pub(super) fn manifest() -> Manifest {
        Manifest {
            schema_version: 3,
            run_id: "run-1".into(),
            mode: Mode::Backtest,
            code_release_hash: "a".repeat(64),
            source_manifest_hash: "b".repeat(64),
            reference_manifest_hash: "c".repeat(64),
            seed_manifest_hash: "d".repeat(64),
            algorithm_manifest_hash: "e".repeat(64),
            dependency_plan_hash: "f".repeat(64),
            hardware_profile_hash: "1".repeat(64),
            clock: Clock::Historical,
            execution: Execution::Simulated {
                fill_model_hash: "2".repeat(64),
                cost_model_hash: "3".repeat(64),
            },
            consumers: vec![Consumer {
                account: "account-1".into(),
                instrument: 1,
                strategy_instance: "candidate".into(),
                strategy_kind: StrategyKind::GenericCandidate,
                execution_interval: crate::execution_interval::ExecutionInterval::Fixed(
                    1_000_000_000,
                ),
                effective_config_hash: "4".repeat(64),
            }],
        }
    }
    #[test]
    fn scope_derivation_and_all_input_pins_are_part_of_identity() {
        let m = manifest();
        let scope = m.scope("account-1", 1, "candidate").unwrap();
        m.require_scope(&scope).unwrap();
        let hash = m.hash().unwrap();
        let pinned = Pinned::new(m.clone(), &hash).unwrap();
        assert_eq!(pinned.scope("account-1", 1, "candidate").unwrap(), scope);
        assert!(Pinned::new(m.clone(), &"0".repeat(64)).is_err());
        assert!(crate::candidate_runtime::Runtime::from_manifest(
            &pinned,
            "account-1",
            1,
            "candidate",
            crate::strategy_candidate::State::default(),
            10000
        )
        .is_ok());
        assert!(crate::candidate_runtime::Runtime::from_manifest(
            &pinned,
            "foreign",
            1,
            "candidate",
            crate::strategy_candidate::State::default(),
            10000
        )
        .is_err());
        let roundtrip: Manifest =
            serde_json::from_str(&serde_json::to_string(&m).unwrap()).unwrap();
        assert_eq!(roundtrip.hash().unwrap(), hash);
        let mut changed = m.clone();
        changed.seed_manifest_hash = "9".repeat(64);
        assert_ne!(changed.hash().unwrap(), hash);
        changed = m.clone();
        changed.consumers[0].effective_config_hash = "9".repeat(64);
        assert!(changed.require_scope(&scope).is_err());
        assert_ne!(changed.hash().unwrap(), hash);
        changed = m.clone();
        changed.consumers[0].strategy_kind = StrategyKind::Strategy350;
        assert!(changed.require_scope(&scope).is_err());
        assert_ne!(changed.hash().unwrap(), hash);
        changed = m.clone();
        changed.consumers[0].execution_interval = ExecutionInterval::Fixed(100_000_000);
        assert!(changed.require_scope(&scope).is_err());
        assert_ne!(changed.hash().unwrap(), hash);
        changed.consumers[0].execution_interval = ExecutionInterval::Fixed(150_000_000);
        assert!(changed.validate().is_err());
        let mut missing_interval = serde_json::to_value(&m).unwrap();
        missing_interval["consumers"][0]
            .as_object_mut()
            .unwrap()
            .remove("execution_interval");
        assert!(serde_json::from_value::<Manifest>(missing_interval).is_err());
        changed = m.clone();
        changed.schema_version = 2;
        assert!(changed.validate().is_err());
        let mut missing_kind = serde_json::to_value(&m).unwrap();
        missing_kind["consumers"][0]
            .as_object_mut()
            .unwrap()
            .remove("strategy_kind");
        assert!(serde_json::from_value::<Manifest>(missing_kind).is_err());
        assert!(m.scope("foreign", 1, "candidate").is_err());
        let mut foreign = scope;
        foreign.mode = Mode::Live;
        assert!(m.require_scope(&foreign).is_err());
    }
    #[test]
    fn mode_isolation_missing_pins_duplicates_and_unsorted_consumers_fail() {
        let base = manifest();
        let mut m = base.clone();
        m.execution = Execution::Broker {
            session_scope_hash: "a".repeat(64),
        };
        assert!(m.validate().is_err());
        m.mode = Mode::Live;
        assert!(m.validate().is_err());
        m.clock = Clock::Live;
        m.validate().unwrap();
        m.mode = Mode::Paper;
        m.validate().unwrap();
        m = base.clone();
        m.seed_manifest_hash.clear();
        assert!(m.validate().is_err());
        m = base.clone();
        m.consumers.push(m.consumers[0].clone());
        assert!(m.validate().is_err());
        m.consumers[0].account = "z".into();
        assert!(m.validate().is_err());
        m = base;
        m.clock = Clock::FaultSimulation {
            scenario_hash: String::new(),
        };
        assert!(m.validate().is_err());
    }
}
