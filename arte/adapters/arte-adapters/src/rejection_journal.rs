//! Shared live/historical pre-submission rejection publication. Cancellation or
//! ambiguous writes retain Pending; only exact readback produces a receipt.
use arte_core::{
    action_rejection::{Committed, Pending, Record},
    strategy_transaction::Committed as DecisionReceipt,
    Result,
};
use std::future::Future;

pub trait Publisher {
    fn append(
        &mut self,
        decision: &DecisionReceipt,
        record: &Record,
    ) -> impl Future<Output = Result<Record>> + Send;
}
/// Recovery readback only. Implementations must not insert missing records.
pub trait Reader {
    fn read(
        &mut self,
        decision: &DecisionReceipt,
        expected: &Record,
    ) -> impl Future<Output = Result<Option<Committed>>> + Send;
}
pub struct ReadbackOutcome {
    pub decision_id: String,
    pub action_index: usize,
    pub result: Result<()>,
}
pub async fn commit(
    pending: &mut Pending,
    decision: &DecisionReceipt,
    publisher: &mut impl Publisher,
) -> Result<Committed> {
    pending.record()?.require(decision)?;
    let readback = publisher.append(decision, pending.record()?).await?;
    pending.acknowledge(&readback)
}

#[cfg(test)]
pub(crate) async fn exercise(
    decision: &DecisionReceipt,
    assessment: arte_core::order_funding::sizing::Assessment,
) {
    use arte_core::{order_funding::sizing::Assessment, Error};
    let now = decision.decision().input.evaluated_at_ns;
    let mut pending = Pending::new(decision, 0, now, "a".repeat(64), assessment.clone()).unwrap();
    let record = pending.record().unwrap().clone();
    assert!(Pending::new(decision, 0, now + 1, "a".repeat(64), assessment.clone()).is_err());
    assert!(Pending::new(decision, 0, now - 1, "a".repeat(64), assessment.clone()).is_err());
    assert!(Pending::new(decision, 8, now, "a".repeat(64), assessment.clone()).is_err());
    assert!(Pending::new(decision, 0, now, "invalid".into(), assessment.clone()).is_err());
    let make_decision = |mode, actions| {
        let source = decision.decision();
        let mut scope = source.scope.clone();
        scope.mode = mode;
        let mut runtime = arte_core::strategy_transaction::Runtime::new(scope, (), 1024).unwrap();
        runtime
            .prepare(
                source.input.clone(),
                &source.safety,
                source.evidence_hash.clone(),
                |_| Ok(actions),
            )
            .unwrap();
        let rows = runtime.pending_batch().unwrap().records().to_vec();
        runtime.acknowledge(&rows).unwrap()
    };
    let wait = make_decision(
        arte_core::strategy_dispatch::Mode::Backtest,
        vec![arte_core::strategy_dispatch::Action::Wait {
            reason: "no entry".into(),
        }],
    );
    assert!(Pending::new(&wait, 0, now, "a".repeat(64), assessment.clone()).is_err());
    for mode in [
        arte_core::strategy_dispatch::Mode::Live,
        arte_core::strategy_dispatch::Mode::Paper,
    ] {
        let live = make_decision(mode, decision.decision().actions.clone());
        Pending::new(&live, 0, now + 1, "a".repeat(64), assessment.clone()).unwrap();
        assert!(Pending::new(&live, 0, now - 1, "a".repeat(64), assessment.clone()).is_err());
    }
    let mut wrong = record.clone();
    wrong.schema_version = 2;
    assert!(wrong.require(decision).is_err());
    wrong = record.clone();
    wrong.decision_id = "f".repeat(64);
    assert!(wrong.require(decision).is_err());
    let mut input = assessment.input().clone();
    input.stop -= 1;
    wrong = record.clone();
    wrong.assessment = Assessment::new(input).unwrap();
    assert!(wrong.require(decision).is_err());
    input = assessment.input().clone();
    input.available_cash_minor = input.policy.maximum_order_cash_minor;
    wrong.assessment = Assessment::new(input).unwrap();
    assert!(wrong.require(decision).is_err());

    struct Fake {
        attempt: usize,
    }
    impl Publisher for Fake {
        async fn append(&mut self, _decision: &DecisionReceipt, record: &Record) -> Result<Record> {
            self.attempt += 1;
            if self.attempt == 1 {
                return Err(Error::Unready("ambiguous journal transport".into()));
            }
            let mut result = record.clone();
            if self.attempt == 2 {
                result.evidence_hash = "b".repeat(64);
            }
            Ok(result)
        }
    }
    let mut publisher = Fake { attempt: 0 };
    struct Never;
    impl Publisher for Never {
        async fn append(&mut self, _: &DecisionReceipt, _: &Record) -> Result<Record> {
            std::future::pending().await
        }
    }
    let waker = std::task::Waker::noop();
    let mut never = Never;
    let mut cancelled = Box::pin(commit(&mut pending, decision, &mut never));
    assert!(cancelled
        .as_mut()
        .poll(&mut std::task::Context::from_waker(waker))
        .is_pending());
    drop(cancelled);
    assert_eq!(
        pending.record().unwrap().hash().unwrap(),
        record.hash().unwrap()
    );
    for _ in 0..2 {
        assert!(commit(&mut pending, decision, &mut publisher)
            .await
            .is_err());
        assert_eq!(
            pending.record().unwrap().hash().unwrap(),
            record.hash().unwrap()
        );
    }
    let committed = commit(&mut pending, decision, &mut publisher)
        .await
        .unwrap();
    assert_eq!(committed.record().hash().unwrap(), record.hash().unwrap());
    assert!(pending.record().is_err());
    assert!(commit(&mut pending, decision, &mut publisher)
        .await
        .is_err());
    assert_eq!(publisher.attempt, 3);
    Committed::from_readback(decision, &record.hash().unwrap(), record.clone()).unwrap();
    assert!(Committed::from_readback(decision, &"0".repeat(64), record.clone()).is_err());
    crate::clickhouse::rejection_roundtrip_test(decision, &record).await;
}
