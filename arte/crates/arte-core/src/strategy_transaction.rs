//! Prepare/journal/commit boundary for bounded account-owned strategy state.
//! Market arrays stay outside this object and are borrowed by the calculation.
use crate::journal::{Batch, Record};
use crate::strategy_dispatch::{Action, Decision, InputBoundary, Safety, Scope, State as Dispatch};
use crate::{content_hash, Error, Result};
use serde::Serialize;
pub mod checkpoint;

struct Pending<S> {
    request_hash: String,
    next_state: S,
    next_dispatch: Dispatch,
    decision: Decision,
    batch: Batch,
}
pub struct Runtime<S> {
    state: S,
    dispatch: Dispatch,
    pending: Option<Pending<S>>,
    maximum_state_bytes: usize,
    last_committed: Option<(String, Decision)>,
}
/// Journal readback acknowledgment, not a broker authorization or fsync proof.
#[derive(Debug, Clone)]
pub struct Committed {
    decision: Decision,
}
impl Committed {
    pub fn decision(&self) -> &Decision {
        &self.decision
    }
}
fn check_state<S: Serialize>(state: &S, maximum: usize) -> Result<()> {
    let bytes = serde_json::to_vec(state).map_err(|e| Error::Serialization(e.to_string()))?;
    if bytes.len() > maximum {
        return Err(Error::Capacity(
            "position strategy state exceeds configured budget".into(),
        ));
    }
    Ok(())
}
impl<S: Clone + Serialize> Runtime<S> {
    pub fn new(scope: Scope, state: S, maximum_state_bytes: usize) -> Result<Self> {
        if maximum_state_bytes == 0 {
            return Err(Error::Invalid(
                "strategy state budget must be positive".into(),
            ));
        }
        check_state(&state, maximum_state_bytes)?;
        Ok(Self {
            state,
            dispatch: Dispatch::new(scope)?,
            pending: None,
            maximum_state_bytes,
            last_committed: None,
        })
    }
    pub fn committed_state(&self) -> &S {
        &self.state
    }
    pub fn scope(&self) -> &Scope {
        self.dispatch.scope()
    }
    pub fn pending_batch(&self) -> Option<&Batch> {
        self.pending.as_ref().map(|p| &p.batch)
    }
    pub fn pending_decision(&self) -> Option<&Decision> {
        self.pending.as_ref().map(|p| &p.decision)
    }
    /// Retry the exact pending input after any ambiguous journal transport result.
    /// Different inputs cannot overtake the unacknowledged record in this scope.
    pub fn prepare(
        &mut self,
        input: InputBoundary,
        safety: &Safety,
        evidence_hash: String,
        calculate: impl FnOnce(&mut S) -> Result<Vec<Action>>,
    ) -> Result<Decision> {
        self.prepare_observed(input, safety, evidence_hash, |_| Ok(()), calculate)
    }
    /// Apply authoritative observation state before exit arbitration. The input
    /// feature/evidence hashes must bind the complete observation (including fills).
    /// Both callbacks are pure state transformations; neither may perform I/O.
    pub fn prepare_observed(
        &mut self,
        input: InputBoundary,
        safety: &Safety,
        evidence_hash: String,
        observe: impl FnOnce(&mut S) -> Result<()>,
        calculate: impl FnOnce(&mut S) -> Result<Vec<Action>>,
    ) -> Result<Decision> {
        let request_hash = content_hash(&(&input, safety, &evidence_hash))?;
        if let Some(pending) = &self.pending {
            if pending.request_hash != request_hash {
                return Err(Error::Unready("strategy journal acknowledgment pending; retain input in bounded upstream queue".into()));
            }
            return Ok(pending.decision.clone());
        }
        let mut next_state = self.state.clone();
        let mut next_dispatch = self.dispatch.clone();
        let replay = self
            .last_committed
            .as_ref()
            .is_some_and(|(hash, _)| hash == &request_hash);
        if !replay {
            observe(&mut next_state)?;
        }
        let decision =
            next_dispatch.evaluate(input, safety, evidence_hash, || calculate(&mut next_state))?;
        check_state(&next_state, self.maximum_state_bytes)?;
        let batch = Batch::new(std::slice::from_ref(&decision))?;
        self.pending = Some(Pending {
            request_hash,
            next_state,
            next_dispatch,
            decision,
            batch,
        });
        Ok(self.pending.as_ref().unwrap().decision.clone())
    }
    /// Invoke only with rows read back by the journal adapter. Bad/incomplete rows
    /// preserve pending state so the same immutable write can be retried.
    pub fn acknowledge(&mut self, readback: &[Record]) -> Result<Committed> {
        let pending = self
            .pending
            .as_ref()
            .ok_or_else(|| Error::Unready("no prepared strategy decision".into()))?;
        pending.batch.verify_readback(readback)?;
        let pending = self.pending.take().unwrap();
        self.last_committed = Some((pending.request_hash, pending.decision.clone()));
        self.state = pending.next_state;
        self.dispatch = pending.next_dispatch;
        Ok(Committed {
            decision: pending.decision,
        })
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_dispatch::Mode;
    use crate::strategy_lifecycle::Phase;
    fn runtime() -> Runtime<u64> {
        Runtime::new(
            Scope {
                run_id: "run".into(),
                mode: Mode::Backtest,
                account: "a".into(),
                strategy_instance: "s".into(),
                strategy_kind: crate::strategy_dispatch::StrategyKind::GenericCandidate,
                instrument: 1,
                code_hash: "code".into(),
                config_hash: "config".into(),
            },
            0,
            1024,
        )
        .unwrap()
    }
    fn input(i: u64) -> InputBoundary {
        InputBoundary {
            event_id: format!("e{i}"),
            event_time_ns: i,
            available_at_ns: i,
            evaluated_at_ns: i,
            source_sequence: i,
            feature_hash: "features".into(),
        }
    }
    fn safety() -> Safety {
        Safety {
            position_quantity: 0,
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
    fn compute(s: &mut u64) -> Result<Vec<Action>> {
        *s += 1;
        Ok(vec![Action::Wait {
            reason: "gate".into(),
        }])
    }
    #[test]
    fn recovery_preserves_committed_and_prepared_state_and_retry_identity() {
        let mut original = runtime();
        let scope = original.scope().clone();
        let context = "a".repeat(64);
        let restore = |image: &crate::seed_storage::Object, rows: &[Record]| {
            Runtime::<u64>::restore_checkpoint(
                image, &image.id, &context, &scope, 1024, 100_000, rows,
            )
        };
        let genesis = original.checkpoint(&context, 100_000).unwrap();
        assert!(restore(&genesis, &[]).unwrap().1.is_none());
        original
            .prepare(input(1), &safety(), "proof".into(), compute)
            .unwrap();
        let prepared = original.checkpoint(&context, 100_000).unwrap();
        let (mut recovered, receipt) = restore(&prepared, &[]).unwrap();
        assert!(receipt.is_none());
        assert_eq!(*recovered.committed_state(), 0);
        assert!(recovered.acknowledge(&[]).is_err());
        let rows = original.pending_batch().unwrap().records().to_vec();
        recovered
            .prepare(input(1), &safety(), "proof".into(), |_| {
                panic!("must not calculate again")
            })
            .unwrap();
        original.acknowledge(&rows).unwrap();
        recovered.acknowledge(&rows).unwrap();
        assert_eq!(*recovered.committed_state(), 1);
        let committed = original.checkpoint(&context, 100_000).unwrap();
        assert!(restore(&committed, &[]).is_err());
        assert!(restore(&genesis, &rows).is_err());
        let (mut recovered, receipt) = restore(&committed, &rows).unwrap();
        assert_eq!(receipt.unwrap().decision().sequence, 1);
        original
            .prepare(input(2), &safety(), "next".into(), compute)
            .unwrap();
        recovered
            .prepare(input(2), &safety(), "next".into(), compute)
            .unwrap();
        assert_eq!(
            original.checkpoint(&context, 100_000).unwrap().id,
            recovered.checkpoint(&context, 100_000).unwrap().id
        );
        let (mut recovered, receipt) =
            restore(&original.checkpoint(&context, 100_000).unwrap(), &rows).unwrap();
        assert_eq!(receipt.unwrap().decision().sequence, 1);
        assert_eq!(*recovered.committed_state(), 1);
        let next_rows = original.pending_batch().unwrap().records().to_vec();
        original.acknowledge(&next_rows).unwrap();
        recovered.acknowledge(&next_rows).unwrap();
        assert_eq!(*recovered.committed_state(), 2);
        let (mut recovered, receipt) = restore(
            &recovered.checkpoint(&context, 100_000).unwrap(),
            &next_rows,
        )
        .unwrap();
        assert_eq!(receipt.unwrap().decision().sequence, 2);
        recovered
            .prepare(input(2), &safety(), "next".into(), |_| {
                panic!("committed retry")
            })
            .unwrap();
        let (mut recovered, _) = restore(
            &recovered.checkpoint(&context, 100_000).unwrap(),
            &next_rows,
        )
        .unwrap();
        recovered.acknowledge(&next_rows).unwrap();
        assert_eq!(
            original.checkpoint(&context, 100_000).unwrap().id,
            recovered.checkpoint(&context, 100_000).unwrap().id
        );
    }
    #[test]
    fn journal_failure_preserves_state_and_pending_identity() {
        let mut r = runtime();
        let id = r
            .prepare(input(1), &safety(), "proof".into(), compute)
            .unwrap()
            .decision_id
            .clone();
        assert_eq!(*r.committed_state(), 0);
        assert!(r.acknowledge(&[]).is_err());
        assert_eq!(
            r.prepare(input(1), &safety(), "proof".into(), |_| panic!(
                "retry cannot calculate"
            ))
            .unwrap()
            .decision_id,
            id
        );
        assert!(r
            .prepare(input(2), &safety(), "proof".into(), compute)
            .is_err());
        let rows = r.pending_batch().unwrap().records().to_vec();
        let committed = r.acknowledge(&rows).unwrap();
        assert_eq!(committed.decision().decision_id, id);
        assert_eq!(*r.committed_state(), 1);
        assert!(r.acknowledge(&rows).is_err());
    }
    #[test]
    fn failed_calculation_never_advances_committed_state() {
        let mut r = runtime();
        assert!(r
            .prepare(input(1), &safety(), "proof".into(), |s| {
                *s = 999;
                Err(Error::Unready("missing feature".into()))
            })
            .is_err());
        assert_eq!(*r.committed_state(), 0);
        assert!(r.pending_batch().is_none());
        r.prepare(input(1), &safety(), "proof".into(), compute)
            .unwrap();
    }
    #[test]
    fn committed_retry_does_not_run_strategy_twice() {
        let mut r = runtime();
        r.prepare(input(1), &safety(), "proof".into(), compute)
            .unwrap();
        let rows = r.pending_batch().unwrap().records().to_vec();
        r.acknowledge(&rows).unwrap();
        r.prepare(input(1), &safety(), "proof".into(), |_| panic!())
            .unwrap();
        r.acknowledge(&rows).unwrap();
        assert_eq!(*r.committed_state(), 1);
    }
    #[test]
    fn fill_observation_commits_even_when_exit_preempts_calculation() {
        let mut r = runtime();
        let mut safe = safety();
        safe.position_quantity = 10;
        safe.manual_exit = true;
        let decision = r
            .prepare_observed(
                input(1),
                &safe,
                "fill-evidence".into(),
                |s| {
                    *s = 10;
                    Ok(())
                },
                |_| panic!("exit must preempt"),
            )
            .unwrap();
        assert!(matches!(
            decision.actions.last().unwrap(),
            Action::Exit { quantity: 10, .. }
        ));
        assert_eq!(*r.committed_state(), 0);
        let rows = r.pending_batch().unwrap().records().to_vec();
        r.acknowledge(&rows).unwrap();
        assert_eq!(*r.committed_state(), 10);
        r.prepare_observed(
            input(1),
            &safe,
            "fill-evidence".into(),
            |_| panic!("observation cannot repeat"),
            |_| panic!(),
        )
        .unwrap();
        r.acknowledge(&rows).unwrap();
        assert_eq!(*r.committed_state(), 10);
    }
    #[test]
    fn observation_failure_has_no_partial_commit() {
        let mut r = runtime();
        assert!(r
            .prepare_observed(
                input(1),
                &safety(),
                "proof".into(),
                |s| {
                    *s = 10;
                    Err(Error::Conflict("bad revision".into()))
                },
                compute
            )
            .is_err());
        assert_eq!(*r.committed_state(), 0);
        assert!(r.pending_batch().is_none());
    }
}
