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
mod actions;
pub mod candidate_position;
pub mod candidates;
pub mod checkpoint;
pub mod recovery;
pub use actions::{
    ActionInputs, ActionOutcome, AllocatedEntry, EntryAssessment, EntryRequest, PendingAction,
    SizedActionInputs, Sizing, SizingRequest,
};

pub struct Runtime {
    run: Run,
    execution: simulation_runtime::Runtime,
    maximum_quote_age_ns: u64,
    dispatched_boundary: Option<String>,
    actions: actions::Work,
    targets: std::collections::BTreeMap<String, candidate_position::TargetRecord>,
}
impl Runtime {
    #[cfg(test)]
    pub(crate) fn seed_test_order(
        &mut self,
        bracket: arte_core::orders::Bracket,
        at_ns: u64,
    ) -> Result<()> {
        // Plumbing fixture only; production has no raw-order submission path.
        self.execution.submit(bracket, at_ns, 0)
    }
    pub fn new(
        run: Run,
        mut execution: simulation_runtime::Runtime,
        fill_model: arte_core::simulation_model::Model,
        costs: arte_core::simulation_costs::Pinned,
    ) -> Result<Self> {
        let maximum_quote_age_ns = fill_model.maximum_quote_age_ns;
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
        if costs.manifest_hash() != run.manifest_hash() {
            return Err(Error::Conflict(
                "playback cost model belongs to another manifest".into(),
            ));
        }
        execution.bind_costs(costs, fill_model)?;
        Ok(Self {
            run,
            execution,
            maximum_quote_age_ns,
            dispatched_boundary: None,
            actions: actions::Work::default(),
            targets: Default::default(),
        })
    }
    pub fn status(&self) -> Status {
        self.run.status()
    }
    pub fn execution_status(&self) -> simulation_runtime::Status {
        self.execution.status()
    }
    pub fn require_portfolio(
        &self,
        portfolio: &arte_core::portfolio::Portfolio,
        currencies: &std::collections::BTreeMap<
            u64,
            arte_core::simulation_costs::SettlementCurrency,
        >,
    ) -> Result<()> {
        self.execution.require_portfolio(portfolio, currencies)
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
                self.execution
                    .advance_playback_clock(boundary.evaluated_at_ns)?;
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
    pub fn fees_minor(&self, command: &str) -> Result<Option<u64>> {
        self.execution.fees_minor(command)
    }
    /// Journaled net trade cash only; this does not release funding or settle an account.
    pub fn closed_net_cash_minor(&self, command: &str) -> Result<i128> {
        self.execution.closed_net_cash_minor(command)
    }
    pub fn position(
        &self,
        key: &arte_core::execution_positions::Key,
    ) -> Option<&arte_core::execution_positions::Position> {
        self.execution.position(key)
    }
    pub fn strategy_position(
        &self,
        scope: &arte_core::strategy_dispatch::Scope,
    ) -> Result<Option<&arte_core::execution_positions::Position>> {
        if !self.decision_view()?.scopes().contains(scope) {
            return Err(Error::Conflict(
                "strategy absent from playback manifest".into(),
            ));
        }
        self.execution.strategy_position(scope)
    }
    /// One consistent account snapshot after all fills at this boundary commit.
    /// Orders retain strategy ownership and individual protection geometry.
    pub fn account_view<'a>(
        &'a self,
        account: &'a str,
    ) -> Result<simulation_runtime::account_view::AccountView<'a>> {
        let run = self.decision_view()?;
        if !run.scopes().iter().any(|scope| scope.account == account) {
            return Err(Error::Invalid(
                "account absent from playback manifest".into(),
            ));
        }
        self.execution.account_view(account)
    }
    pub fn release_unfilled_reservation(
        &mut self,
        command: &str,
        portfolio: &arte_core::portfolio::Portfolio,
    ) -> Result<bool> {
        self.decision_view()?;
        self.execution
            .release_unfilled_reservation(command, portfolio)
    }
    /// Reconcile only terminal owned orders after durable fill publication.
    /// Open positions and still-fillable entries retain their reservations.
    pub fn reconcile_funding(
        &mut self,
        portfolio: &arte_core::portfolio::Portfolio,
        currencies: &std::collections::BTreeMap<
            u64,
            arte_core::simulation_costs::SettlementCurrency,
        >,
        maximum_orders: usize,
        maximum_receipts: usize,
    ) -> Result<Vec<simulation_runtime::funding::Outcome>> {
        self.decision_view()?;
        self.execution
            .reconcile_funding(portfolio, currencies, maximum_orders, maximum_receipts)
    }
    pub fn settle_closed_order(
        &mut self,
        command: &str,
        portfolio: &arte_core::portfolio::Portfolio,
        currency: &arte_core::simulation_costs::SettlementCurrency,
        maximum_receipts: usize,
    ) -> Result<bool> {
        self.decision_view()?;
        self.execution
            .settle_closed_order(command, portfolio, currency, maximum_receipts)
    }
    pub fn acknowledge(&mut self) -> Result<()> {
        self.decision_view()?;
        self.actions.require_complete()?;
        self.run.acknowledge()?;
        self.dispatched_boundary = None;
        self.actions = actions::Work::default();
        Ok(())
    }
}
impl Boundary for Runtime {
    fn validate_decision(&self, decision: &Decision) -> Result<()> {
        actions::Work::validate(decision)?;
        self.decision_view()?.validate_decision(decision)
    }
    fn record(&mut self, committed: &Committed) -> Result<bool> {
        self.validate_decision(committed.decision())?;
        let added = self.run.record(committed)?;
        if added {
            self.actions.record(committed);
        }
        Ok(added)
    }
}
