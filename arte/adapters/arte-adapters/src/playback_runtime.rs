//! One in-process owner gates playback advancement on decisions AND fill journals.
//! No services, credentials, or implicit source acquisition are created here.
use crate::{fill_journal::Publisher, simulation_runtime, strategy_journal::accounts::Boundary};
use arte_core::{
    market_structure::scheduler::{
        playback::{accounts::Run, Poll, Status},
        Kind,
    },
    strategy_dispatch::Decision,
    strategy_transaction::Committed,
    Error, Result,
};

pub struct Runtime {
    run: Run,
    execution: simulation_runtime::Runtime,
    maximum_quote_age_ns: u64,
    dispatched_boundary: Option<String>,
}
impl Runtime {
    pub fn new(
        run: Run,
        execution: simulation_runtime::Runtime,
        maximum_quote_age_ns: u64,
    ) -> Result<Self> {
        if maximum_quote_age_ns == 0 || maximum_quote_age_ns > 60_000_000_000 {
            return Err(Error::Invalid("playback quote age bound".into()));
        }
        let status = run.status();
        if status.pending_boundary
            || status.acknowledged_boundaries != 0
            || status.admitted_events != 0
        {
            return Err(Error::Conflict(
                "playback controller requires an unused run".into(),
            ));
        }
        execution.require_new_playback(&run)?;
        Ok(Self {
            run,
            execution,
            maximum_quote_age_ns,
            dispatched_boundary: None,
        })
    }
    pub fn status(&self) -> Status {
        self.run.status()
    }
    pub fn execution_status(&self) -> simulation_runtime::Status {
        self.execution.status()
    }
    pub fn pause(&mut self) -> Result<()> {
        self.run.pause()
    }
    pub fn resume(&mut self) -> Result<()> {
        self.run.resume()
    }
    pub fn step(&mut self) -> Result<()> {
        self.run.step()
    }
    pub fn poll(&mut self) -> Result<Poll> {
        let outcome = self.run.poll()?;
        if outcome == Poll::Boundary {
            let boundary = self
                .run
                .pending()?
                .ok_or_else(|| Error::Unready("playback boundary missing".into()))?;
            if self.dispatched_boundary.as_deref() != Some(boundary.id) {
                self.execution.require_committed_fills()?;
                if matches!(boundary.kind, Kind::Quote { .. }) {
                    self.execution
                        .quote_playback(&self.run, self.maximum_quote_age_ns)?;
                }
                self.dispatched_boundary = Some(boundary.id.into());
            }
        }
        Ok(outcome)
    }
    /// Position-sensitive evaluation is unavailable until quote fills are journaled.
    /// This immutable view cannot advance the playback cursor.
    pub fn decision_view(&self) -> Result<&Run> {
        self.execution.require_committed_fills()?;
        let boundary = self
            .run
            .pending()?
            .ok_or_else(|| Error::Unready("no decision boundary".into()))?;
        if self.dispatched_boundary.as_deref() != Some(boundary.id) {
            return Err(Error::Unready("boundary execution dispatch pending".into()));
        }
        Ok(&self.run)
    }
    pub async fn commit_fills(&mut self, publisher: &mut impl Publisher) -> Result<bool> {
        self.execution.commit_next(publisher).await
    }
    pub fn next_fill_scope_hash(&self) -> Result<Option<String>> {
        self.execution.next_scope_hash()
    }
    pub fn position(
        &self,
        key: &arte_core::execution_positions::Key,
    ) -> Option<&arte_core::execution_positions::Position> {
        self.execution.position(key)
    }
    pub fn acknowledge(&mut self) -> Result<()> {
        self.decision_view()?;
        self.run.acknowledge()?;
        self.dispatched_boundary = None;
        Ok(())
    }
}
impl Boundary for Runtime {
    fn validate_decision(&self, decision: &Decision) -> Result<()> {
        self.decision_view()?.validate_decision(decision)
    }
    fn record(&mut self, committed: &Committed) -> Result<bool> {
        self.decision_view()?;
        self.run.record(committed)
    }
}
