//! Bounded servicing of an already-dispatched market boundary. This coordinator
//! owns no duplicate phase state and never fabricates candidate admission evidence.
use super::*;
use arte_core::{portfolio::checkpoint::Cut, simulation_costs::SettlementCurrency};
use std::collections::BTreeMap;

pub struct Inputs<'a> {
    pub actions: SizedActionInputs<'a>,
    pub currencies: &'a BTreeMap<u64, SettlementCurrency>,
    pub maximum_funding_orders: usize,
    pub maximum_settlement_receipts: usize,
    pub decision_concurrency: usize,
}
pub struct Journals<'a, F, D, R> {
    /// Execution-position scope hash, not strategy scope hash.
    pub fills: &'a mut BTreeMap<String, F>,
    pub decisions: &'a mut BTreeMap<String, D>,
    pub rejections: &'a mut R,
}
pub enum Step {
    FillsPublished {
        scope_hash: String,
    },
    Funding(Vec<simulation_runtime::funding::Outcome>),
    NeedsEvaluation {
        scope_hashes: Vec<String>,
    },
    Decisions(Vec<candidates::Outcome>),
    Strategy350Decisions(Vec<super::strategy350_accounts::Outcome>),
    Actions(Vec<ResolvedAction>),
    /// No acknowledgment is performed. The caller must publish the common-cut
    /// checkpoint and meet its durability gate before advancing the market.
    CheckpointRequired(Cut),
}
impl Runtime {
    /// One bounded unit of work per call. Poll first to dispatch the boundary.
    /// Inspect all per-unit results; errors retain their owning component's state.
    /// After NeedsEvaluation, assemble certified evidence and call prepare_owned
    /// for those scopes, then service again. Cancellation is retryable in place.
    pub async fn service_boundary<F, D, R>(
        &mut self,
        candidates: &mut candidates::Candidates,
        inputs: Inputs<'_>,
        journals: Journals<'_, F, D, R>,
    ) -> Result<Step>
    where
        F: crate::fill_journal::Publisher,
        D: crate::strategy_journal::Publisher,
        R: crate::rejection_journal::Publisher,
    {
        if inputs.maximum_funding_orders == 0
            || inputs.maximum_funding_orders > 4096
            || inputs.maximum_settlement_receipts == 0
            || inputs.maximum_settlement_receipts > 1_000_000
            || inputs.decision_concurrency == 0
            || inputs.decision_concurrency > 64
            || inputs.actions.maximum_actions == 0
            || inputs.actions.maximum_actions > 4096
            || journals.fills.len() > 4096
            || journals.decisions.len() > 4096
        {
            return Err(Error::Capacity("boundary service limits".into()));
        }
        candidates.require(self)?;
        if !candidates
            .scope_hashes()
            .eq(journals.decisions.keys().map(String::as_str))
        {
            return Err(Error::Conflict(
                "boundary decision publisher scopes differ".into(),
            ));
        }
        // Fill publication must precede any position-sensitive calculation.
        if let Some(scope_hash) = self.next_fill_scope_hash()? {
            let publisher = journals
                .fills
                .get_mut(&scope_hash)
                .ok_or_else(|| Error::Unready("fill scope publisher missing".into()))?;
            self.commit_fills(publisher).await?;
            return Ok(Step::FillsPublished { scope_hash });
        }
        self.decision_view()?;
        // Releasing closed-order funding precedes new sizing. A nonempty batch
        // returns immediately, including errors, so no outcome is silently lost.
        let funding = self.reconcile_funding(
            inputs.actions.portfolio,
            inputs.currencies,
            inputs.maximum_funding_orders,
            inputs.maximum_settlement_receipts,
        )?;
        if !funding.is_empty() {
            return Ok(Step::Funding(funding));
        }
        candidates.observe(self)?;
        let missing = candidates.needed_evaluations(self)?;
        if !missing.is_empty() {
            return Ok(Step::NeedsEvaluation {
                scope_hashes: missing,
            });
        }
        let decisions = candidates
            .commit_accounts(self, journals.decisions, inputs.decision_concurrency)
            .await?;
        if !decisions.is_empty() {
            return Ok(Step::Decisions(decisions));
        }
        let actions = self
            .execute_journaled_actions(inputs.actions, journals.rejections)
            .await?;
        if !actions.is_empty() {
            return Ok(Step::Actions(actions));
        }
        self.actions.require_complete()?;
        let boundary = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("boundary missing before checkpoint".into()))?;
        Ok(Step::CheckpointRequired(Cut {
            boundary_sequence: self
                .status()
                .acknowledged_boundaries
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("boundary sequence exhausted".into()))?,
            boundary_hash: boundary.id.to_owned(),
            at_ns: boundary.evaluated_at_ns,
        }))
    }

    /// Strategy 350 uses the same fill, funding, action and checkpoint owners
    /// as generic playback, but a separate account evaluator and journal set.
    pub async fn service_strategy350_boundary<S, F, D, R>(
        &mut self,
        accounts: &mut super::strategy350_accounts::Accounts<S>,
        inputs: Inputs<'_>,
        journals: Journals<'_, F, D, R>,
    ) -> Result<Step>
    where
        S: Clone + serde::Serialize,
        F: crate::fill_journal::Publisher,
        D: crate::strategy_journal::Publisher,
        R: crate::rejection_journal::Publisher,
    {
        if inputs.maximum_funding_orders == 0
            || inputs.maximum_funding_orders > 4096
            || inputs.maximum_settlement_receipts == 0
            || inputs.maximum_settlement_receipts > 1_000_000
            || inputs.decision_concurrency == 0
            || inputs.decision_concurrency > 64
            || inputs.actions.maximum_actions == 0
            || inputs.actions.maximum_actions > 4096
            || journals.fills.len() > 4096
            || journals.decisions.len() > 4096
        {
            return Err(Error::Capacity(
                "Strategy 350 boundary service limits".into(),
            ));
        }
        accounts.require_controller(self)?;
        if !accounts
            .scope_hashes()
            .eq(journals.decisions.keys().map(String::as_str))
        {
            return Err(Error::Conflict(
                "Strategy 350 decision publisher scopes differ".into(),
            ));
        }
        if let Some(scope_hash) = self.next_fill_scope_hash()? {
            let publisher = journals
                .fills
                .get_mut(&scope_hash)
                .ok_or_else(|| Error::Unready("fill scope publisher missing".into()))?;
            self.commit_fills(publisher).await?;
            return Ok(Step::FillsPublished { scope_hash });
        }
        self.decision_view()?;
        let funding = self.reconcile_funding(
            inputs.actions.portfolio,
            inputs.currencies,
            inputs.maximum_funding_orders,
            inputs.maximum_settlement_receipts,
        )?;
        if !funding.is_empty() {
            return Ok(Step::Funding(funding));
        }
        let missing = accounts.needed_evaluations(self)?;
        if !missing.is_empty() {
            return Ok(Step::NeedsEvaluation {
                scope_hashes: missing,
            });
        }
        let decisions = accounts
            .commit_accounts(self, journals.decisions, inputs.decision_concurrency)
            .await?;
        if !decisions.is_empty() {
            return Ok(Step::Strategy350Decisions(decisions));
        }
        let actions = self
            .execute_journaled_actions(inputs.actions, journals.rejections)
            .await?;
        if !actions.is_empty() {
            return Ok(Step::Actions(actions));
        }
        self.actions.require_complete()?;
        accounts.registered_receipts(self)?;
        let boundary = self.decision_view()?.pending()?.ok_or_else(|| {
            Error::Unready("Strategy 350 boundary missing before checkpoint".into())
        })?;
        Ok(Step::CheckpointRequired(Cut {
            boundary_sequence: self
                .status()
                .acknowledged_boundaries
                .checked_add(1)
                .ok_or_else(|| Error::Capacity("boundary sequence exhausted".into()))?,
            boundary_hash: boundary.id.to_owned(),
            at_ns: boundary.evaluated_at_ns,
        }))
    }
}
