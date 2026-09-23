//! Shared Live/Paper/Backtest decision envelope and exit-first arbitration.
//! No mode owns broker capability here. All returned actions remain domain intents.
use crate::execution_interval::ExecutionInterval;
use crate::strategy_lifecycle::Phase;
use crate::{content_hash, Error, Result};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Live,
    Paper,
    Backtest,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum StrategyKind {
    GenericCandidate,
    Strategy350,
}
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
pub struct Scope {
    pub run_id: String,
    pub mode: Mode,
    pub account: String,
    pub strategy_instance: String,
    pub strategy_kind: StrategyKind,
    pub execution_interval: ExecutionInterval,
    pub instrument: u64,
    pub code_hash: String,
    pub config_hash: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct InputBoundary {
    pub event_id: String,
    pub event_time_ns: u64,
    pub available_at_ns: u64,
    pub evaluated_at_ns: u64,
    pub source_sequence: u64,
    pub feature_hash: String,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum ExitReason {
    ExitPending,
    SessionFlatten,
    ProtectiveStop,
    ManualExit,
    MacdEpisodeEnded,
    LuldBufferReached,
    EncounterFailure,
    EarlySetupFailed,
    StructuralFailure,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(tag = "action", content = "payload", rename_all = "snake_case")]
pub enum Action {
    Wait {
        reason: String,
    },
    Hold {
        reason: String,
    },
    CancelEntry {
        reason: String,
    },
    Exit {
        reason: ExitReason,
        quantity: u64,
        reduce_only: bool,
    },
    Enter(Box<crate::strategy_entry::Proposal>),
    Add(Box<crate::strategy_adds::Proposal>),
    ReplaceStop(crate::strategy_protection::StopProposal),
    ReplaceTarget(crate::strategy_protection::TargetProposal),
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Decision {
    pub schema_version: u32,
    pub decision_id: String,
    pub sequence: u64,
    pub scope: Scope,
    pub input: InputBoundary,
    pub safety: Safety,
    pub actions: Vec<Action>,
    pub evidence_hash: String,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Safety {
    pub position_quantity: u64,
    pub pending_exit_quantity: u64,
    pub exit_pending: bool,
    pub pending_entry: bool,
    pub last_exit_reason: Option<ExitReason>,
    pub flatten: bool,
    pub protective_stop_crossed: bool,
    pub manual_exit: bool,
    pub completed_macd_reversal: bool,
    pub setup_phase: Phase,
    pub luld_buffer_reached: bool,
    pub encounter_exit: bool,
    pub early_setup_failed: bool,
    pub structural_exit: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    scope: Scope,
    last_sequence: Option<u64>,
    last_input_hash: Option<String>,
    last_decision: Option<Decision>,
    last_evaluated_at_ns: u64,
}
impl State {
    pub(crate) fn from_last_decision(scope: Scope, decision: &Decision) -> Result<Self> {
        crate::journal::Record::from_decision(decision)?;
        if decision.scope != scope {
            return Err(Error::Conflict("recovered decision scope differs".into()));
        }
        let mut state = Self::new(scope)?;
        let mut validated = state.evaluate(
            decision.input.clone(),
            &decision.safety,
            decision.evidence_hash.clone(),
            || Ok(decision.actions.clone()),
        )?;
        validated.sequence = decision.sequence;
        if content_hash(&validated)? != content_hash(decision)? {
            return Err(Error::Conflict(
                "recovered decision arbitration differs".into(),
            ));
        }
        state.last_decision = Some(decision.clone());
        Ok(state)
    }
    pub fn scope(&self) -> &Scope {
        &self.scope
    }
    pub fn new(scope: Scope) -> Result<Self> {
        scope.execution_interval.validate()?;
        if [
            &scope.run_id,
            &scope.account,
            &scope.strategy_instance,
            &scope.code_hash,
            &scope.config_hash,
        ]
        .into_iter()
        .any(|s| s.is_empty())
            || scope.instrument == 0
        {
            return Err(Error::Invalid("incomplete strategy scope".into()));
        }
        Ok(Self {
            scope,
            last_sequence: None,
            last_input_hash: None,
            last_decision: None,
            last_evaluated_at_ns: 0,
        })
    }
    /// Lazy evaluator is never called when safety requires exit/cancellation.
    /// The caller commits its calculation state only after this operation succeeds.
    pub fn evaluate(
        &mut self,
        input: InputBoundary,
        safety: &Safety,
        evidence_hash: String,
        calculate: impl FnOnce() -> Result<Vec<Action>>,
    ) -> Result<Decision> {
        if input.event_id.is_empty()
            || input.feature_hash.is_empty()
            || evidence_hash.is_empty()
            || input.available_at_ns > input.evaluated_at_ns
            || input.event_time_ns > input.evaluated_at_ns
            || input.evaluated_at_ns < self.last_evaluated_at_ns
        {
            return Err(Error::Invalid("invalid decision input boundary".into()));
        }
        let hash = content_hash(&(&input, safety, &evidence_hash))?;
        if let Some(last) = self.last_sequence {
            if input.source_sequence < last {
                return Err(Error::Invalid("decision sequence rewind".into()));
            }
            if input.source_sequence == last {
                if self.last_input_hash.as_ref() != Some(&hash) {
                    return Err(Error::Conflict(
                        "same sequence has different causal evidence".into(),
                    ));
                }
                return self
                    .last_decision
                    .clone()
                    .ok_or_else(|| Error::Invalid("missing last decision".into()));
            }
        }
        let acquired = safety.position_quantity > 0;
        let actions = if safety.exit_pending || safety.pending_exit_quantity > 0 {
            if acquired && safety.position_quantity > safety.pending_exit_quantity {
                vec![Action::Exit {
                    reason: safety.last_exit_reason.unwrap_or(ExitReason::ExitPending),
                    quantity: safety.position_quantity,
                    reduce_only: true,
                }]
            } else if acquired {
                vec![Action::Hold {
                    reason: "exit_fill_pending".into(),
                }]
            } else {
                vec![Action::Wait {
                    reason: "exit_fill_pending".into(),
                }]
            }
        } else {
            let reason = if safety.flatten {
                Some(ExitReason::SessionFlatten)
            } else if safety.protective_stop_crossed {
                Some(ExitReason::ProtectiveStop)
            } else if safety.manual_exit {
                Some(ExitReason::ManualExit)
            } else if safety.completed_macd_reversal && safety.setup_phase == Phase::PostBreakout {
                Some(ExitReason::MacdEpisodeEnded)
            } else if safety.luld_buffer_reached {
                Some(ExitReason::LuldBufferReached)
            } else if safety.encounter_exit {
                Some(ExitReason::EncounterFailure)
            } else if safety.early_setup_failed {
                Some(ExitReason::EarlySetupFailed)
            } else if safety.structural_exit {
                Some(ExitReason::StructuralFailure)
            } else {
                None
            };
            if let Some(reason) = reason.filter(|_| acquired || safety.pending_entry) {
                let mut actions = vec![Action::CancelEntry {
                    reason: "exit_required".into(),
                }];
                if acquired {
                    actions.push(Action::Exit {
                        reason,
                        quantity: safety.position_quantity,
                        reduce_only: true,
                    });
                }
                actions
            } else {
                calculate()?
            }
        };
        if actions.is_empty() || actions.len() > 8 {
            return Err(Error::Invalid(
                "empty or oversized decision action group".into(),
            ));
        }
        let exposure = actions
            .iter()
            .filter(|a| matches!(a, Action::Enter(_) | Action::Add(_)))
            .count();
        let exits = actions
            .iter()
            .filter(|a| matches!(a, Action::Exit { .. }))
            .count();
        if exposure>1||exits>1||(exits>0&&exposure>0)
            || (exits > 0 && actions.iter().any(|a| !matches!(a, Action::Exit{..} | Action::CancelEntry{..})))
            || actions.iter().any(|a|matches!(a,Action::Enter(_))&&acquired||matches!(a,Action::Add(_)|Action::ReplaceStop(_)|Action::ReplaceTarget(_))&&!acquired)
            || actions.iter().any(|a|matches!(a,Action::Exit{quantity,reduce_only,..} if !reduce_only||*quantity==0||*quantity>safety.position_quantity)) {
            return Err(Error::Invalid("incompatible or exposure-increasing exit actions".into()));
        }
        let sequence = self.last_decision.as_ref().map_or(Ok(1), |v| {
            v.sequence
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("decision sequence exhausted".into()))
        })?;
        let decision_id = content_hash(&(&self.scope, &input, safety, &actions, &evidence_hash))?;
        let decision = Decision {
            schema_version: 2,
            decision_id,
            sequence,
            scope: self.scope.clone(),
            input,
            safety: safety.clone(),
            actions,
            evidence_hash,
        };
        self.last_sequence = Some(decision.input.source_sequence);
        self.last_evaluated_at_ns = decision.input.evaluated_at_ns;
        self.last_input_hash = Some(hash);
        self.last_decision = Some(decision.clone());
        Ok(decision)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn state(mode: Mode) -> State {
        State::new(Scope {
            run_id: "run".into(),
            mode,
            account: "a".into(),
            strategy_instance: "s".into(),
            strategy_kind: StrategyKind::GenericCandidate,
            execution_interval: crate::execution_interval::ExecutionInterval::Fixed(1_000_000_000),
            instrument: 1,
            code_hash: "code".into(),
            config_hash: "config".into(),
        })
        .unwrap()
    }
    fn input() -> InputBoundary {
        InputBoundary {
            event_id: "e".into(),
            event_time_ns: 10,
            available_at_ns: 11,
            evaluated_at_ns: 12,
            source_sequence: 1,
            feature_hash: "features".into(),
        }
    }
    fn safety() -> Safety {
        Safety {
            position_quantity: 10,
            pending_exit_quantity: 0,
            exit_pending: false,
            pending_entry: false,
            last_exit_reason: None,
            flatten: false,
            protective_stop_crossed: false,
            manual_exit: false,
            completed_macd_reversal: false,
            setup_phase: Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        }
    }
    #[test]
    fn exit_priority_does_not_call_add_or_entry_evaluator() {
        let mut s = safety();
        s.flatten = true;
        s.manual_exit = true;
        s.protective_stop_crossed = true;
        let d = state(Mode::Live)
            .evaluate(input(), &s, "proof".into(), || panic!("must preempt"))
            .unwrap();
        assert!(matches!(
            d.actions[1],
            Action::Exit {
                reason: ExitReason::SessionFlatten,
                quantity: 10,
                reduce_only: true
            }
        ));
    }
    #[test]
    fn pending_entry_only_cancels_without_zero_quantity_exit() {
        let mut s = safety();
        s.position_quantity = 0;
        s.pending_entry = true;
        s.manual_exit = true;
        let d = state(Mode::Paper)
            .evaluate(input(), &s, "proof".into(), || panic!())
            .unwrap();
        assert_eq!(d.actions.len(), 1);
        assert!(matches!(d.actions[0], Action::CancelEntry { .. }));
    }
    #[test]
    fn mode_shares_actions_but_not_identity_and_retry_is_exact() {
        let s = safety();
        let compute = || {
            Ok(vec![Action::Hold {
                reason: "structure_valid".into(),
            }])
        };
        let mut live = state(Mode::Live);
        let a = live.evaluate(input(), &s, "proof".into(), compute).unwrap();
        let b = state(Mode::Backtest)
            .evaluate(input(), &s, "proof".into(), compute)
            .unwrap();
        assert_eq!(
            content_hash(&a.actions).unwrap(),
            content_hash(&b.actions).unwrap()
        );
        assert_ne!(a.decision_id, b.decision_id);
        let retry = live
            .evaluate(input(), &s, "proof".into(), || panic!())
            .unwrap();
        assert_eq!(retry.decision_id, a.decision_id);
        let mut changed = s.clone();
        changed.position_quantity = 9;
        assert!(live
            .evaluate(input(), &changed, "proof".into(), compute)
            .is_err());
        assert!(live
            .evaluate(input(), &s, "different".into(), compute)
            .is_err());
    }
    #[test]
    fn unconfirmed_macd_phase_does_not_exit_building_setup() {
        let mut s = safety();
        s.completed_macd_reversal = true;
        let d = state(Mode::Backtest)
            .evaluate(input(), &s, "proof".into(), || {
                Ok(vec![Action::Hold {
                    reason: "building".into(),
                }])
            })
            .unwrap();
        assert!(matches!(d.actions[0], Action::Hold { .. }));
    }
    #[test]
    fn future_event_time_cannot_be_evaluated() {
        let mut input = input();
        input.event_time_ns = input.evaluated_at_ns + 1;
        assert!(state(Mode::Live)
            .evaluate(input, &safety(), "proof".into(), || panic!(
                "future input must not calculate"
            ))
            .is_err());
    }
}
