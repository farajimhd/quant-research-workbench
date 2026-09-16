//! Bounded dispatch with explicit terminal rejection publication. No error-string
//! classification; uncertain submissions retain their exact funding and allocation.
use super::*;
#[derive(Debug, Clone, serde::Serialize)]
#[serde(tag = "resolution", content = "value", rename_all = "snake_case")]
pub enum Resolution {
    /// Accepted by the owned historical simulator, not a live broker acknowledgment.
    Submitted(Box<decision_orders::Plan>),
    Applied,
    Rejected {
        record_hash: String,
    },
}
pub struct ResolvedAction {
    pub action: PendingAction,
    pub result: Result<Resolution>,
}
impl Runtime {
    /// Select a bounded batch once, before any async publication. A failure blocks
    /// later actions in its decision, but does not stop independent decisions.
    /// Cancellation retains prepared rejection evidence for the next exact retry.
    pub async fn execute_journaled_actions(
        &mut self,
        inputs: SizedActionInputs<'_>,
        publisher: &mut impl crate::rejection_journal::Publisher,
    ) -> Result<Vec<ResolvedAction>> {
        self.require_dispatch_inputs(
            &ActionInputs {
                allocations: &BTreeMap::new(),
                cash_policies: inputs.cash_policies,
                portfolio: inputs.portfolio,
                safety: simulation_runtime::AmendmentSafety {
                    session: inputs.safety.session,
                    risk_policy: inputs.safety.risk_policy,
                    bands: inputs.safety.bands,
                },
                latency_ns: inputs.latency_ns,
                maximum_actions: inputs.maximum_actions,
            },
            Some(inputs.sizing),
        )?;
        let selected = self.pending_actions_bounded(inputs.maximum_actions);
        let mut blocked = std::collections::BTreeSet::new();
        let mut outcomes = Vec::with_capacity(selected.len());
        for action in selected {
            let result = if blocked.contains(&action.decision_id) {
                Err(Error::Unready(
                    "earlier action in this decision failed".into(),
                ))
            } else {
                self.resolve_action(&action, &inputs, publisher).await
            };
            if result.is_err() {
                blocked.insert(action.decision_id.clone());
            }
            outcomes.push(ResolvedAction { action, result });
        }
        Ok(outcomes)
    }
    async fn resolve_action(
        &mut self,
        action: &PendingAction,
        inputs: &SizedActionInputs<'_>,
        publisher: &mut impl crate::rejection_journal::Publisher,
    ) -> Result<Resolution> {
        let id = &action.decision_id;
        let index = action.action_index;
        let safety = || simulation_runtime::AmendmentSafety {
            session: inputs.safety.session,
            risk_policy: inputs.safety.risk_policy,
            bands: inputs.safety.bands,
        };
        match action.kind.as_str() {
            "enter" | "add" => {
                let key = (id.clone(), index);
                if self.actions.items[&key].rejection.is_none() {
                    let cash = inputs
                        .cash_policies
                        .get(&action.account)
                        .ok_or_else(|| Error::Unready("entry cash mandate missing".into()))?;
                    let allocation =
                        if let Some(retained) = self.retained_entry_allocation(id, index)? {
                            Some(retained.clone())
                        } else {
                            let sizing = inputs.sizing.get(&action.account).ok_or_else(|| {
                                Error::Unready("entry sizing policy missing".into())
                            })?;
                            let (assessment, evidence) = self.assess_entry_evidence(
                                id,
                                index,
                                SizingRequest {
                                    sizing,
                                    portfolio: inputs.portfolio,
                                    cash_policy: cash,
                                    safety: safety(),
                                    latency_ns: inputs.latency_ns,
                                },
                            )?;
                            match assessment {
                                EntryAssessment::Sized { allocation, .. } => Some(allocation),
                                EntryAssessment::Rejected(calculation) => {
                                    self.retain_entry_rejection(
                                        &key,
                                        inputs.portfolio,
                                        calculation,
                                        evidence.ok_or_else(|| {
                                            Error::Unready("rejection evidence missing".into())
                                        })?,
                                    )?;
                                    None
                                }
                            }
                        };
                    if let Some(allocation) = allocation {
                        return self
                            .enter_action(
                                id,
                                index,
                                EntryRequest {
                                    allocation: &allocation,
                                    portfolio: inputs.portfolio,
                                    cash_policy: cash,
                                    safety: safety(),
                                    latency_ns: inputs.latency_ns,
                                },
                            )
                            .map(|plan| Resolution::Submitted(Box::new(plan)));
                    }
                }
                self.commit_entry_rejection(id, index, inputs.portfolio, publisher)
                    .await?;
                let record_hash = self.actions.items[&key]
                    .rejection
                    .as_ref()
                    .ok_or_else(|| Error::Unready("resolved rejection missing".into()))?
                    .record
                    .hash()?;
                Ok(Resolution::Rejected { record_hash })
            }
            "replace_stop" | "replace_target" => self
                .protection_action(id, index, safety())
                .map(|()| Resolution::Applied),
            "exit" => self.exit_action(id, index).map(|()| Resolution::Applied),
            "cancel_entry" => self
                .cancel_entry_action(id, index)
                .map(|()| Resolution::Applied),
            _ => Err(Error::Invalid("unknown retained action kind".into())),
        }
    }
}
