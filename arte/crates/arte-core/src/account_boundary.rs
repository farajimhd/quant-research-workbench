//! Bounded multi-account journal barrier for one shared market boundary.
//! Does not authorize orders or roll back accounts. Recovery requires receipts.
use crate::{
    content_hash,
    strategy_dispatch::{InputBoundary, Scope, State},
    strategy_transaction::Committed,
    Error, Result,
};
use std::collections::{BTreeMap, BTreeSet};
pub mod checkpoint;

pub struct Barrier {
    input: InputBoundary,
    // Exact scope hash -> accepted decision hash. No market arrays are copied.
    accounts: BTreeMap<String, Option<String>>,
    finished: bool,
}
impl Barrier {
    pub fn new(input: InputBoundary, scopes: &[Scope], maximum_accounts: usize) -> Result<Self> {
        if maximum_accounts == 0 || maximum_accounts > 4096 || scopes.len() > maximum_accounts {
            return Err(Error::Capacity("account boundary budget".into()));
        }
        let first = scopes
            .first()
            .ok_or_else(|| Error::Invalid("account boundary requires consumers".into()))?;
        if input.event_id.is_empty()
            || input.available_at_ns > input.evaluated_at_ns
            || input.event_time_ns > input.evaluated_at_ns
        {
            return Err(Error::Invalid("account boundary clocks or identity".into()));
        }
        let mut identities = BTreeSet::new();
        let mut accounts = BTreeMap::new();
        for scope in scopes {
            State::new(scope.clone())?;
            if scope.run_id != first.run_id
                || scope.mode != first.mode
                || scope.instrument != first.instrument
                || !identities.insert((&scope.account, &scope.strategy_instance))
            {
                return Err(Error::Conflict("account boundary consumer scope".into()));
            }
            accounts.insert(content_hash(scope)?, None);
        }
        Ok(Self {
            input,
            accounts,
            finished: false,
        })
    }
    pub fn remaining(&self) -> usize {
        self.accounts
            .values()
            .filter(|value| value.is_none())
            .count()
    }
    pub fn finished(&self) -> bool {
        self.finished
    }
    /// Account workers can skip already committed consumers on a retry.
    pub fn needs_decision(&self, scope: &Scope) -> Result<bool> {
        self.accounts
            .get(&content_hash(scope)?)
            .map(|value| value.is_none())
            .ok_or_else(|| Error::Conflict("unregistered boundary consumer".into()))
    }
    fn same_market_input(&self, input: &InputBoundary) -> bool {
        input.event_id == self.input.event_id
            && input.event_time_ns == self.input.event_time_ns
            && input.available_at_ns == self.input.available_at_ns
            && input.source_sequence == self.input.source_sequence
    }
    /// Only a verified transaction acknowledgment can satisfy a consumer. Repeated
    /// identical receipts are idempotent; changed decisions cannot replace them.
    pub fn record(&mut self, committed: &Committed) -> Result<bool> {
        let decision = committed.decision();
        self.validate_decision(decision)?;
        let slot = self
            .accounts
            .get_mut(&content_hash(&decision.scope)?)
            .unwrap();
        let hash = content_hash(decision)?;
        if slot.is_some() {
            return Ok(false);
        }
        *slot = Some(hash);
        Ok(true)
    }
    /// Preflight only. A prepared decision is never a journal receipt.
    pub fn validate_decision(&self, decision: &crate::strategy_dispatch::Decision) -> Result<()> {
        if self.finished
            || !self.same_market_input(&decision.input)
            || decision.input.evaluated_at_ns < self.input.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "receipt belongs to another boundary or clock".into(),
            ));
        }
        let slot = self
            .accounts
            .get(&content_hash(&decision.scope)?)
            .ok_or_else(|| Error::Conflict("receipt consumer scope mismatch".into()))?;
        let hash = content_hash(decision)?;
        match slot {
            Some(previous) if previous == &hash => Ok(()),
            Some(_) => Err(Error::Conflict(
                "consumer decision changed after commit".into(),
            )),
            None => Ok(()),
        }
    }
    /// Failed market acknowledgment retains every receipt. This is an in-process
    /// barrier, not a distributed transaction or a durable checkpoint.
    pub fn acknowledge_market(
        &mut self,
        current: &InputBoundary,
        acknowledge: impl FnOnce(&str) -> Result<()>,
    ) -> Result<()> {
        if self.finished
            || !self.same_market_input(current)
            || current.evaluated_at_ns != self.input.evaluated_at_ns
        {
            return Err(Error::Conflict(
                "market acknowledgment boundary mismatch".into(),
            ));
        }
        if self.remaining() != 0 {
            return Err(Error::Unready("account journal receipts pending".into()));
        }
        acknowledge(&self.input.event_id)?;
        self.finished = true;
        Ok(())
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use crate::strategy_dispatch::{Action, Mode, Safety};
    use crate::strategy_lifecycle::Phase;
    use crate::strategy_transaction::Runtime;
    fn scope(account: &str) -> Scope {
        Scope {
            run_id: "r".into(),
            mode: Mode::Backtest,
            account: account.into(),
            strategy_instance: "s".into(),
            strategy_kind: crate::strategy_dispatch::StrategyKind::GenericCandidate,
            execution_interval: crate::execution_interval::ExecutionInterval::Fixed(1_000_000_000),
            instrument: 1,
            code_hash: "c".into(),
            config_hash: "f".into(),
        }
    }
    fn input() -> InputBoundary {
        InputBoundary {
            event_id: "e".into(),
            event_time_ns: 1,
            available_at_ns: 2,
            evaluated_at_ns: 3,
            source_sequence: 1,
            feature_hash: "shared".into(),
        }
    }
    pub(crate) fn receipt(scope: Scope, input: InputBoundary) -> Committed {
        let mut runtime = Runtime::new(scope, 0_u64, 1024).unwrap();
        let safety = Safety {
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
        };
        runtime
            .prepare(input, &safety, "evidence".into(), |_| {
                Ok(vec![Action::Wait {
                    reason: "gate".into(),
                }])
            })
            .unwrap();
        let rows = runtime.pending_batch().unwrap().records().to_vec();
        runtime.acknowledge(&rows).unwrap()
    }
    #[test]
    fn recovery_requires_exact_independent_receipts() {
        let scopes = [scope("a"), scope("b")];
        let a = receipt(scopes[0].clone(), input());
        let b = receipt(scopes[1].clone(), input());
        let context = "a".repeat(64);
        let mut barrier = Barrier::new(input(), &scopes, 2).unwrap();
        barrier.record(&a).unwrap();
        let image = barrier.checkpoint(&context, 4096).unwrap();
        let restore = |receipts: &[&Committed]| {
            Barrier::restore_checkpoint(
                &image,
                &image.id,
                &context,
                input(),
                &scopes,
                2,
                4096,
                receipts,
            )
        };
        assert!(restore(&[]).is_err());
        assert!(restore(&[&b]).is_err());
        assert!(restore(&[&a, &a]).is_err());
        assert!(restore(&[&a, &b]).is_err());
        let mut recovered = restore(&[&a]).unwrap();
        assert!(!recovered.needs_decision(&scopes[0]).unwrap());
        assert!(recovered.needs_decision(&scopes[1]).unwrap());
        assert!(recovered
            .acknowledge_market(&input(), |_| panic!("pending consumer"))
            .is_err());
        recovered.record(&b).unwrap();
        barrier.record(&b).unwrap();
        let complete = barrier.checkpoint(&context, 4096).unwrap();
        let all = Barrier::restore_checkpoint(
            &complete,
            &complete.id,
            &context,
            input(),
            &scopes,
            2,
            4096,
            &[&b, &a],
        )
        .unwrap();
        assert_eq!(all.remaining(), 0);
        assert!(!all.finished());
        assert_eq!(
            recovered.checkpoint(&context, 4096).unwrap().id,
            barrier.checkpoint(&context, 4096).unwrap().id
        );
        recovered.acknowledge_market(&input(), |_| Ok(())).unwrap();
        assert!(recovered.checkpoint(&context, 4096).is_err());
    }
    #[test]
    fn recovery_rejects_changed_scope_context_clock_and_payload() {
        let scopes = [scope("a")];
        let context = "a".repeat(64);
        let barrier = Barrier::new(input(), &scopes, 1).unwrap();
        let image = barrier.checkpoint(&context, 4096).unwrap();
        assert!(barrier.checkpoint(&context, 1).is_err());
        let restore =
            |image: &crate::seed_storage::Object, context: &str, input, scopes: &[Scope]| {
                Barrier::restore_checkpoint(image, &image.id, context, input, scopes, 1, 4096, &[])
            };
        assert!(restore(&image, &"b".repeat(64), input(), &scopes).is_err());
        assert!(restore(&image, &context, input(), &[scope("b")]).is_err());
        let mut changed = input();
        changed.evaluated_at_ns += 1;
        assert!(restore(&image, &context, changed, &scopes).is_err());
        let mut changed = input();
        changed.feature_hash = "different".into();
        assert!(restore(&image, &context, changed, &scopes).is_err());
        let mut corrupt = crate::seed_storage::Object::new(image.payload.clone());
        corrupt.payload.push(b' ');
        assert!(restore(&corrupt, &context, input(), &scopes).is_err());
        let noncanonical = crate::seed_storage::Object::new(corrupt.payload);
        assert!(restore(&noncanonical, &context, input(), &scopes).is_err());
        assert_eq!(
            restore(&image, &context, input(), &scopes)
                .unwrap()
                .remaining(),
            1
        );
    }
    #[test]
    fn partial_commit_retry_and_market_acknowledgment() {
        let scopes = [scope("a"), scope("b")];
        let mut barrier = Barrier::new(input(), &scopes, 2).unwrap();
        let a = receipt(scopes[0].clone(), input());
        assert!(barrier.record(&a).unwrap());
        assert!(!barrier.record(&a).unwrap());
        assert!(!barrier.needs_decision(&scopes[0]).unwrap());
        assert!(barrier.needs_decision(&scopes[1]).unwrap());
        assert_eq!(barrier.remaining(), 1);
        assert!(barrier
            .acknowledge_market(&input(), |_| panic!("cannot advance"))
            .is_err());
        let mut later = input();
        later.evaluated_at_ns = 4;
        later.feature_hash = "account-b".into();
        barrier.record(&receipt(scopes[1].clone(), later)).unwrap();
        assert_eq!(barrier.remaining(), 0);
        assert!(barrier
            .acknowledge_market(&input(), |_| Err(Error::Unready("retry".into())))
            .is_err());
        assert!(!barrier.finished());
        barrier
            .acknowledge_market(&input(), |id| {
                assert_eq!(id, "e");
                Ok(())
            })
            .unwrap();
        assert!(barrier.finished());
        assert!(barrier.record(&a).is_err());
        assert!(barrier
            .acknowledge_market(&input(), |_| panic!("duplicate ack"))
            .is_err());
    }
    #[test]
    fn rejects_mismatched_receipts_without_losing_progress() {
        let scopes = [scope("a"), scope("b")];
        let mut barrier = Barrier::new(input(), &scopes, 2).unwrap();
        barrier
            .record(&receipt(scopes[0].clone(), input()))
            .unwrap();
        for kind in 0..8 {
            let mut bad = input();
            let mut owner = scopes[1].clone();
            match kind {
                0 => bad.event_id = "other".into(),
                1 => bad.event_time_ns = 2,
                2 => bad.available_at_ns = 1,
                3 => bad.source_sequence = 2,
                4 => bad.evaluated_at_ns = 2,
                5 => owner.config_hash = "different".into(),
                6 => owner.mode = Mode::Live,
                _ => owner.account = "c".into(),
            }
            assert!(barrier.record(&receipt(owner, bad)).is_err());
            assert_eq!(barrier.remaining(), 1);
        }
        let mut changed = input();
        changed.feature_hash = "changed".into();
        assert!(barrier
            .record(&receipt(scopes[0].clone(), changed))
            .is_err());
        barrier
            .record(&receipt(scopes[1].clone(), input()))
            .unwrap();
        let mut wrong = input();
        wrong.source_sequence = 9;
        assert!(barrier
            .acknowledge_market(&wrong, |_| panic!("wrong market"))
            .is_err());
        assert!(!barrier.finished());
    }
    #[test]
    fn bounds_and_freezes_consumer_set() {
        assert!(Barrier::new(input(), &[], 2).is_err());
        assert!(Barrier::new(input(), &[scope("a")], 0).is_err());
        assert!(Barrier::new(input(), &[scope("a")], 4097).is_err());
        assert!(Barrier::new(input(), &[scope("a"), scope("b")], 1).is_err());
        let mut other = scope("a");
        other.config_hash = "other".into();
        assert!(Barrier::new(input(), &[scope("a"), other], 2).is_err());
        let mut other = scope("b");
        other.instrument = 2;
        assert!(Barrier::new(input(), &[scope("a"), other], 2).is_err());
        let barrier = Barrier::new(input(), &[scope("a")], 1).unwrap();
        assert!(barrier.needs_decision(&scope("b")).is_err());
    }
}
