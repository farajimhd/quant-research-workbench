//! Owned policy bundle for configuration loading. No implicit strategy defaults.
//! The effective identity remains the shared candidate runtime's existing hash.
use crate::{candidate_features, candidate_runtime, strategy_candidate, Error, Result};
use serde::{Deserialize, Serialize};
pub mod document;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub schema_version: u32,
    pub features: candidate_features::Config,
    pub entry: crate::strategy_entry::Policy,
    pub adds: crate::strategy_adds::Policy,
    pub protection: crate::strategy_protection::Policy,
    pub acquisition: strategy_candidate::AcquisitionPolicy,
    pub recovery: crate::strategy_lifecycle::RecoveryPolicy,
    pub position: Position,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Position {
    pub phase_minimum_progress_r: f64,
    pub failure_window_ns: u64,
    pub failure_buffer_ticks: f64,
    pub failure_exit_enabled: bool,
    pub preserve_peak: bool,
    pub stop_gain_guard: bool,
}
impl Config {
    pub fn policy(&self) -> Result<strategy_candidate::Policy<'_>> {
        if self.schema_version != 1 {
            return Err(Error::Invalid(
                "unsupported candidate configuration schema".into(),
            ));
        }
        if self.features.encounters.tick != self.entry.tick {
            return Err(Error::Conflict(
                "entry and encounter tick sizes differ".into(),
            ));
        }
        Ok(strategy_candidate::Policy {
            maximum_completed_bar_age_ns: self.features.maximum_completed_bar_age_ns,
            entry: &self.entry,
            adds: &self.adds,
            protection: &self.protection,
            phase_minimum_progress_r: self.position.phase_minimum_progress_r,
            failure_window_ns: self.position.failure_window_ns,
            failure_buffer_ticks: self.position.failure_buffer_ticks,
            failure_exit_enabled: self.position.failure_exit_enabled,
            preserve_peak: self.position.preserve_peak,
            stop_gain_guard: self.position.stop_gain_guard,
        })
    }
    /// Requires market and quote policy identity; a file hash is not an effective
    /// strategy hash. Algorithm-specific admissibility remains in the evaluators.
    pub fn effective_hash(
        &self,
        features: &candidate_features::State,
        quote_policy_hash: &str,
    ) -> Result<String> {
        if crate::content_hash(&self.features)? != features.config_identity()? {
            return Err(Error::Conflict(
                "candidate feature configuration differs".into(),
            ));
        }
        candidate_runtime::configuration_hash(
            &self.policy()?,
            &self.acquisition,
            features,
            &self.recovery,
            quote_policy_hash,
        )
    }
}
