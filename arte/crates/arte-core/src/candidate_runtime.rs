//! Shared candidate-to-journal binding. No market transport or broker capability.
use crate::{content_hash, Error, Result};
use crate::{
    strategy_adds as adds, strategy_candidate as candidate, strategy_dispatch as dispatch,
    strategy_entry as entry, strategy_transaction as transaction,
};

pub struct Runtime {
    transaction: transaction::Runtime<candidate::State>,
    config_hash: String,
}
/// Both policies form one pinned effective configuration, regardless of mode.
pub fn configuration_hash(
    completed: &candidate::Policy<'_>,
    intrabar: &candidate::AcquisitionPolicy,
    recovery: &crate::strategy_lifecycle::RecoveryPolicy,
) -> Result<String> {
    content_hash(&("candidate-configuration-v1", completed, intrabar, recovery))
}
impl Runtime {
    pub fn new(
        scope: dispatch::Scope,
        state: candidate::State,
        maximum_state_bytes: usize,
    ) -> Result<Self> {
        let config_hash = scope.config_hash.clone();
        if config_hash.len() != 64
            || !config_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid(
                "candidate requires pinned effective configuration hash".into(),
            ));
        }
        Ok(Self {
            transaction: transaction::Runtime::new(scope, state, maximum_state_bytes)?,
            config_hash,
        })
    }
    pub fn state(&self) -> &candidate::State {
        self.transaction.committed_state()
    }
    pub fn pending_batch(&self) -> Option<&crate::journal::Batch> {
        self.transaction.pending_batch()
    }
    pub fn acknowledge(
        &mut self,
        rows: &[crate::journal::Record],
    ) -> Result<transaction::Committed> {
        self.transaction.acknowledge(rows)
    }
    #[allow(clippy::too_many_arguments)]
    fn validate(
        &self,
        input: &dispatch::InputBoundary,
        broker: &candidate::PositionObservation,
        safety: &dispatch::Safety,
        completed: &candidate::Policy<'_>,
        intrabar: &candidate::AcquisitionPolicy,
        recovery: &crate::strategy_lifecycle::RecoveryPolicy,
    ) -> Result<()> {
        if configuration_hash(completed, intrabar, recovery)? != self.config_hash {
            return Err(Error::Conflict(
                "candidate configuration differs from pinned scope".into(),
            ));
        }
        if broker.quantity != safety.position_quantity
            || broker.pending_entry != safety.pending_entry
            || broker.at_ns > input.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "candidate safety and reconciled position differ".into(),
            ));
        }
        Ok(())
    }
    /// The borrowed frame must contain the causal market arrays for this boundary.
    /// No arrays are copied into account-owned state. Hashes bind their contents.
    #[allow(clippy::too_many_arguments)]
    pub fn completed(
        &mut self,
        mut input: dispatch::InputBoundary,
        safety: &dispatch::Safety,
        frame: &entry::Frame<'_>,
        broker: &candidate::PositionObservation,
        gates: &adds::Gates,
        policy: &candidate::Policy<'_>,
        intrabar: &candidate::AcquisitionPolicy,
    ) -> Result<dispatch::Decision> {
        self.validate(
            &input,
            broker,
            safety,
            policy,
            intrabar,
            frame.recovery_policy,
        )?;
        if frame.bar.end_ns > input.evaluated_at_ns
            || broker.at_ns > frame.bar.end_ns
            || input.event_time_ns != frame.bar.end_ns
            || policy.maximum_completed_bar_age_ns == 0
        {
            return Err(Error::Invalid(
                "candidate completed frame contains future evidence".into(),
            ));
        }
        let stale = input.evaluated_at_ns - frame.bar.end_ns >= policy.maximum_completed_bar_age_ns;
        input.feature_hash = content_hash(&("candidate-completed-v1", frame, broker, gates))?;
        let evidence = input.feature_hash.clone();
        self.transaction.prepare_observed(
            input,
            safety,
            evidence,
            |state| {
                state.observe_reconciled(
                    broker,
                    frame.bar.end_ns,
                    frame.bar.open.max(frame.bar.close),
                    policy.preserve_peak,
                    policy.stop_gain_guard,
                )
            },
            |state| {
                if stale {
                    return Ok(stale_bar_actions(safety.pending_entry));
                }
                Ok(state.completed(frame, broker, gates, policy)?.actions)
            },
        )
    }
    #[allow(clippy::too_many_arguments)]
    pub fn intrabar(
        &mut self,
        mut input: dispatch::InputBoundary,
        safety: &dispatch::Safety,
        observation: &candidate::AcquisitionObservation,
        broker: &candidate::PositionObservation,
        body_high: f64,
        policy: &candidate::Policy<'_>,
        intrabar: &candidate::AcquisitionPolicy,
        recovery: &crate::strategy_lifecycle::RecoveryPolicy,
    ) -> Result<dispatch::Decision> {
        self.validate(&input, broker, safety, policy, intrabar, recovery)?;
        if observation.at_ns != input.evaluated_at_ns || !body_high.is_finite() || body_high <= 0. {
            return Err(Error::Invalid("invalid candidate intrabar boundary".into()));
        }
        input.feature_hash =
            content_hash(&("candidate-intrabar-v1", observation, broker, body_high))?;
        let evidence = input.feature_hash.clone();
        self.transaction.prepare_observed(
            input,
            safety,
            evidence,
            |state| {
                state.observe_reconciled(
                    broker,
                    observation.at_ns,
                    body_high,
                    policy.preserve_peak,
                    policy.stop_gain_guard,
                )
            },
            |state| {
                let result = state.acquisition_update(observation, intrabar)?;
                if result.actions.is_empty() {
                    Ok(vec![dispatch::Action::Hold {
                        reason: "intrabar_acquisition_observed".into(),
                    }])
                } else {
                    Ok(result.actions)
                }
            },
        )
    }
}
fn stale_bar_actions(pending_entry: bool) -> Vec<dispatch::Action> {
    if pending_entry {
        vec![dispatch::Action::CancelEntry {
            reason: "completed_bar_stale_at_evaluation".into(),
        }]
    } else {
        vec![dispatch::Action::Wait {
            reason: "completed_bar_stale_at_evaluation".into(),
        }]
    }
}
