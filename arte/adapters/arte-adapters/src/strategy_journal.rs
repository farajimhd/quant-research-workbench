//! Shared asynchronous journal commit. Failed writes retain prepared strategy state.
use crate::{clickhouse::ClickHouse, ownership::Lease};
use arte_core::{
    config::Acceptance,
    journal::{Batch, Record},
    strategy_transaction::Committed,
    Error, Result,
};
use std::{collections::BTreeSet, future::Future};

pub trait Prepared {
    fn pending(&self) -> Option<&Batch>;
    fn acknowledge(&mut self, rows: &[Record]) -> Result<Committed>;
}
impl<S: Clone + serde::Serialize> Prepared for arte_core::strategy_transaction::Runtime<S> {
    fn pending(&self) -> Option<&Batch> {
        self.pending_batch()
    }
    fn acknowledge(&mut self, rows: &[Record]) -> Result<Committed> {
        self.acknowledge(rows)
    }
}
impl Prepared for arte_core::candidate_runtime::Runtime {
    fn pending(&self) -> Option<&Batch> {
        self.pending_batch()
    }
    fn acknowledge(&mut self, rows: &[Record]) -> Result<Committed> {
        self.acknowledge(rows)
    }
}
pub trait Publisher {
    fn append(&mut self, batch: &Batch) -> impl Future<Output = Result<Vec<Record>>> + Send;
}
/// The mutable lease borrow prevents simultaneous writers using the same handle.
/// This is cooperative single-host ownership, not distributed failover fencing.
pub struct DatabasePublisher<'a> {
    database: &'a ClickHouse,
    lease: &'a mut Lease,
    scope_hash: String,
}
impl<'a> DatabasePublisher<'a> {
    pub fn new(
        database: &'a ClickHouse,
        lease: &'a mut Lease,
        scope: &arte_core::strategy_dispatch::Scope,
        passed: &BTreeSet<Acceptance>,
    ) -> Result<Self> {
        for required in [Acceptance::RepositoryExtracted, Acceptance::Durability] {
            if !passed.contains(&required) {
                return Err(Error::Unready(format!(
                    "strategy journal acceptance missing: {required:?}"
                )));
            }
        }
        let scope_hash = arte_core::content_hash(scope)?;
        lease.require(&scope_hash)?;
        Ok(Self {
            database,
            lease,
            scope_hash,
        })
    }
}
impl Publisher for DatabasePublisher<'_> {
    async fn append(&mut self, batch: &Batch) -> Result<Vec<Record>> {
        self.lease.require(&self.scope_hash)?;
        if batch
            .records()
            .iter()
            .any(|r| r.scope_hash != self.scope_hash)
        {
            return Err(Error::Conflict(
                "strategy journal writer scope mismatch".into(),
            ));
        }
        self.database.append_decisions(batch).await
    }
}
/// Do not dequeue a new input until this succeeds. Cancellation leaves the pending
/// transaction intact; retry uses the same journal slots and decision identities.
/// The returned value proves readback only, not broker authorization.
pub async fn commit(
    runtime: &mut impl Prepared,
    publisher: &mut impl Publisher,
) -> Result<Committed> {
    let batch = runtime
        .pending()
        .ok_or_else(|| Error::Unready("no prepared candidate decision".into()))?;
    let rows = publisher.append(batch).await?;
    batch.verify_readback(&rows)?;
    runtime.acknowledge(&rows)
}

#[cfg(test)]
mod tests {
    use super::*;
    use arte_core::strategy_dispatch::*;
    fn prepared() -> arte_core::strategy_transaction::Runtime<u64> {
        let mut runtime = arte_core::strategy_transaction::Runtime::new(
            Scope {
                run_id: "run".into(),
                mode: Mode::Backtest,
                account: "a".into(),
                strategy_instance: "s".into(),
                instrument: 1,
                code_hash: "code".into(),
                config_hash: "config".into(),
            },
            0,
            1024,
        )
        .unwrap();
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
            setup_phase: arte_core::strategy_lifecycle::Phase::Building,
            luld_buffer_reached: false,
            encounter_exit: false,
            early_setup_failed: false,
            structural_exit: false,
        };
        runtime
            .prepare(
                InputBoundary {
                    event_id: "e".into(),
                    event_time_ns: 1,
                    available_at_ns: 1,
                    evaluated_at_ns: 1,
                    source_sequence: 1,
                    feature_hash: "features".into(),
                },
                &safety,
                "evidence".into(),
                |state| {
                    *state += 1;
                    Ok(vec![Action::Wait {
                        reason: "gate".into(),
                    }])
                },
            )
            .unwrap();
        runtime
    }
    struct Store {
        fail: bool,
        rows: Vec<Record>,
        incomplete: bool,
    }
    impl Publisher for Store {
        async fn append(&mut self, batch: &Batch) -> Result<Vec<Record>> {
            self.rows.extend_from_slice(batch.records());
            if self.fail {
                self.fail = false;
                return Err(Error::Unready("ambiguous write".into()));
            }
            if self.incomplete {
                return Ok(vec![]);
            }
            Ok(self.rows.clone())
        }
    }
    #[tokio::test]
    async fn ambiguous_write_and_incomplete_readback_preserve_transaction() {
        let mut runtime = prepared();
        let id = runtime.pending_decision().unwrap().decision_id.clone();
        let mut store = Store {
            fail: true,
            rows: vec![],
            incomplete: false,
        };
        assert!(commit(&mut runtime, &mut store).await.is_err());
        assert_eq!(*runtime.committed_state(), 0);
        store.incomplete = true;
        assert!(commit(&mut runtime, &mut store).await.is_err());
        assert_eq!(runtime.pending_decision().unwrap().decision_id, id);
        store.incomplete = false;
        assert_eq!(
            commit(&mut runtime, &mut store)
                .await
                .unwrap()
                .decision()
                .decision_id,
            id
        );
        assert_eq!(*runtime.committed_state(), 1);
        assert!(runtime.pending_batch().is_none());
        assert!(commit(&mut runtime, &mut store).await.is_err());
    }
    struct Pending;
    impl Publisher for Pending {
        async fn append(&mut self, _: &Batch) -> Result<Vec<Record>> {
            std::future::pending().await
        }
    }
    #[tokio::test(start_paused = true)]
    async fn cancelled_append_can_retry_without_recalculation() {
        let mut runtime = prepared();
        assert!(tokio::time::timeout(
            std::time::Duration::from_secs(1),
            commit(&mut runtime, &mut Pending)
        )
        .await
        .is_err());
        assert_eq!(*runtime.committed_state(), 0);
        let mut store = Store {
            fail: false,
            rows: vec![],
            incomplete: false,
        };
        commit(&mut runtime, &mut store).await.unwrap();
        assert_eq!(*runtime.committed_state(), 1);
    }
}
