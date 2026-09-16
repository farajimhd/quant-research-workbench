//! Owned pre-submission rejection lifecycle. Does not release funds or erase orders.
use super::*;
use arte_core::{action_rejection, portfolio::Portfolio};
pub(super) struct State {
    pub record: action_rejection::Record,
    pub journaled: bool,
    /// Deliberately not serialized. Recovery must obtain independent readback.
    pub verified: bool,
}
impl Runtime {
    /// Verify a bounded stable batch without republishing or recalculating.
    /// Successful prefixes survive cancellation/failure. Failed items stay pending.
    pub async fn verify_entry_rejections(
        &mut self,
        portfolio: &Portfolio,
        reader: &mut impl crate::rejection_journal::Reader,
        maximum_records: usize,
    ) -> Result<Vec<crate::rejection_journal::ReadbackOutcome>> {
        self.decision_view()?;
        if maximum_records == 0 || maximum_records > 4096 {
            return Err(Error::Capacity("rejection readback batch size".into()));
        }
        let selected: Vec<_> = self
            .actions
            .items
            .iter()
            .filter_map(|(key, item)| {
                item.rejection
                    .as_ref()
                    .filter(|r| !r.verified)
                    .map(|r| (key.clone(), item.receipt.clone(), r.record.clone()))
            })
            .take(maximum_records)
            .collect();
        let mut outcomes = Vec::with_capacity(selected.len());
        for ((decision_id, action_index), decision, record) in selected {
            let result = match reader.read(&decision, &record).await {
                Ok(Some(receipt)) => {
                    if receipt.record().hash()? != record.hash()? {
                        Err(Error::Conflict(
                            "rejection reader returned another record".into(),
                        ))
                    } else {
                        self.confirm_entry_rejection(&receipt, portfolio)
                    }
                }
                Ok(None) => Err(Error::Unready("rejection journal readback missing".into())),
                Err(error) => Err(error),
            };
            outcomes.push(crate::rejection_journal::ReadbackOutcome {
                decision_id,
                action_index,
                result,
            });
        }
        Ok(outcomes)
    }
    fn require_unfunded_rejection(
        &self,
        key: &(String, usize),
        portfolio: &Portfolio,
    ) -> Result<()> {
        let item = self
            .actions
            .items
            .get(key)
            .ok_or_else(|| Error::Unready("rejection action missing".into()))?;
        if item.reserved_request.is_some()
            || item.completed_request.is_some()
            || item.allocation.is_some()
        {
            return Err(Error::Conflict(
                "funded or completed action cannot be rejected".into(),
            ));
        }
        let command = content_hash(&("decision-bracket-v1", &key.0, key.1))?;
        self.execution.require_absent_command(&command)?;
        if let Some(rejection) = &item.rejection {
            let scope = &item.receipt.decision().scope;
            portfolio.require_simulation(
                &scope.account,
                &scope.run_id,
                rejection.record.assessment.input().policy.currency_scale,
                None,
            )?;
        }
        if portfolio
            .snapshot(&item.receipt.decision().scope.account)?
            .reservations
            .contains_key(&command)
        {
            return Err(Error::Conflict(
                "rejection command has portfolio funding".into(),
            ));
        }
        Ok(())
    }
    /// Capture once before any asynchronous publication. Exact retries retain the
    /// original quote/account/policy evidence even if available cash changes.
    pub fn prepare_entry_rejection(
        &mut self,
        decision_id: &str,
        action_index: usize,
        request: SizingRequest<'_>,
    ) -> Result<&action_rejection::Record> {
        self.decision_view()?;
        let key = (decision_id.into(), action_index);
        self.require_unfunded_rejection(&key, request.portfolio)?;
        if self.actions.items[&key].rejection.is_none() {
            let portfolio = request.portfolio;
            let (assessment, evidence) =
                self.assess_entry_evidence(decision_id, action_index, request)?;
            let EntryAssessment::Rejected(calculation) = assessment else {
                return Err(Error::Invalid("fundable entry cannot be rejected".into()));
            };
            self.retain_entry_rejection(
                &key,
                portfolio,
                calculation,
                evidence.ok_or_else(|| Error::Unready("rejection evidence missing".into()))?,
            )?;
        }
        Ok(&self.actions.items[&key].rejection.as_ref().unwrap().record)
    }
    pub(super) fn retain_entry_rejection(
        &mut self,
        key: &(String, usize),
        portfolio: &Portfolio,
        calculation: arte_core::order_funding::sizing::Assessment,
        evidence: String,
    ) -> Result<()> {
        self.require_unfunded_rejection(key, portfolio)?;
        let item = self.actions.items.get_mut(key).unwrap();
        if item.rejection.is_some() {
            return Err(Error::Conflict(
                "rejection evidence already retained".into(),
            ));
        }
        let pending = action_rejection::Pending::new(
            &item.receipt,
            key.1,
            item.receipt.decision().input.evaluated_at_ns,
            evidence,
            calculation,
        )?;
        item.rejection = Some(State {
            record: pending.record()?.clone(),
            journaled: false,
            verified: false,
        });
        Ok(())
    }
    /// Includes journaled records: callers must independently load each after
    /// recovery. The checkpoint itself is not a journal readback receipt.
    pub fn rejection_records(&self) -> Vec<&action_rejection::Record> {
        self.actions
            .items
            .values()
            .filter_map(|item| item.rejection.as_ref().map(|r| &r.record))
            .collect()
    }
    pub fn confirm_entry_rejection(
        &mut self,
        receipt: &action_rejection::Committed,
        portfolio: &Portfolio,
    ) -> Result<()> {
        self.decision_view()?;
        let record = receipt.record();
        let key = (record.decision_id.clone(), record.action_index);
        self.require_unfunded_rejection(&key, portfolio)?;
        let item = self.actions.items.get_mut(&key).unwrap();
        record.require(&item.receipt)?;
        let retained = item
            .rejection
            .as_mut()
            .ok_or_else(|| Error::Unready("no prepared rejection".into()))?;
        if retained.record.hash()? != record.hash()? {
            return Err(Error::Conflict(
                "rejection readback differs from owned evidence".into(),
            ));
        }
        retained.journaled = true;
        retained.verified = true;
        Ok(())
    }
    /// Cancellation retains the original record. Unknown outcomes retry the same
    /// immutable slot. Does not treat a submission failure as a sizing rejection.
    pub async fn commit_entry_rejection(
        &mut self,
        decision_id: &str,
        action_index: usize,
        portfolio: &Portfolio,
        publisher: &mut impl crate::rejection_journal::Publisher,
    ) -> Result<bool> {
        self.decision_view()?;
        let key = (decision_id.into(), action_index);
        self.require_unfunded_rejection(&key, portfolio)?;
        let item = &self.actions.items[&key];
        let state = item
            .rejection
            .as_ref()
            .ok_or_else(|| Error::Unready("no prepared rejection".into()))?;
        if state.journaled && state.verified {
            return Ok(false);
        }
        let record = &state.record;
        let mut pending = action_rejection::Pending::new(
            &item.receipt,
            record.action_index,
            record.evaluated_at_ns,
            record.evidence_hash.clone(),
            record.assessment.clone(),
        )?;
        let receipt =
            crate::rejection_journal::commit(&mut pending, &item.receipt, publisher).await?;
        self.confirm_entry_rejection(&receipt, portfolio)?;
        Ok(true)
    }
    pub(in crate::playback_runtime) fn require_rejection_funding_absent(
        &self,
        portfolio: &Portfolio,
    ) -> Result<()> {
        for (key, item) in &self.actions.items {
            if item.rejection.is_some() {
                self.require_unfunded_rejection(key, portfolio)?;
            }
        }
        Ok(())
    }
}
