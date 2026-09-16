use super::*;
use arte_core::{
    content_hash,
    decision_orders::{self, Allocation},
    strategy_dispatch::Action,
};
use std::{collections::BTreeMap, sync::Arc};

#[derive(Debug, Clone, serde::Serialize)]
pub struct PendingAction {
    pub decision_id: String,
    pub action_index: usize,
    pub account: String,
    pub kind: String,
}
struct Item {
    receipt: Arc<Committed>,
    completed_request: Option<String>,
}
#[derive(Default)]
pub(super) struct Work {
    items: BTreeMap<(String, usize), Item>,
}
fn kind(action: &Action) -> Option<&'static str> {
    match action {
        Action::Wait { .. } | Action::Hold { .. } => None,
        Action::Enter(_) => Some("enter"),
        Action::Add(_) => Some("add"),
        Action::Exit { .. } => Some("exit"),
        Action::CancelEntry { .. } => Some("cancel_entry"),
        Action::ReplaceStop(_) => Some("replace_stop"),
        Action::ReplaceTarget(_) => Some("replace_target"),
    }
}
impl Work {
    pub fn validate(decision: &Decision) -> Result<()> {
        // At most 4096 declared consumers, each with one bounded decision.
        if decision.actions.len() > 16 {
            return Err(Error::Capacity(
                "playback decision exceeds 16-action budget".into(),
            ));
        }
        Ok(())
    }
    pub fn record(&mut self, committed: &Committed) {
        let receipt = Arc::new(committed.clone());
        for (index, action) in committed.decision().actions.iter().enumerate() {
            if kind(action).is_some() {
                self.items.insert(
                    (committed.decision().decision_id.clone(), index),
                    Item {
                        receipt: Arc::clone(&receipt),
                        completed_request: None,
                    },
                );
            }
        }
    }
    pub fn require_complete(&self) -> Result<()> {
        if self
            .items
            .values()
            .any(|item| item.completed_request.is_none())
        {
            return Err(Error::Unready(
                "committed decision has unresolved execution actions".into(),
            ));
        }
        Ok(())
    }
}
impl Runtime {
    pub fn exit_action(&mut self, decision_id: &str, action_index: usize) -> Result<()> {
        let at_ns = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("no exit boundary".into()))?
            .evaluated_at_ns;
        let item = self
            .actions
            .items
            .get_mut(&(decision_id.into(), action_index))
            .ok_or_else(|| Error::Unready("no committed exit action".into()))?;
        let Some(Action::Exit {
            quantity,
            reduce_only: true,
            ..
        }) = item.receipt.decision().actions.get(action_index)
        else {
            return Err(Error::Invalid("action is not a reduce-only exit".into()));
        };
        let fingerprint = content_hash(&(
            "playback-exit-v1",
            decision_id,
            action_index,
            at_ns,
            quantity,
        ))?;
        if let Some(previous) = &item.completed_request {
            return if previous == &fingerprint {
                Ok(())
            } else {
                Err(Error::Conflict("completed exit changed".into()))
            };
        }
        self.execution
            .exit_for(&item.receipt.decision().scope, *quantity, at_ns)?;
        item.completed_request = Some(fingerprint);
        Ok(())
    }
    pub fn cancel_entry_action(&mut self, decision_id: &str, action_index: usize) -> Result<()> {
        let at_ns = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("no cancellation boundary".into()))?
            .evaluated_at_ns;
        let item = self
            .actions
            .items
            .get_mut(&(decision_id.into(), action_index))
            .ok_or_else(|| Error::Unready("no committed cancellation action".into()))?;
        if !matches!(
            item.receipt.decision().actions.get(action_index),
            Some(Action::CancelEntry { .. })
        ) {
            return Err(Error::Invalid("action is not an entry cancellation".into()));
        }
        let fingerprint = content_hash(&("playback-cancel-v1", decision_id, action_index, at_ns))?;
        if let Some(previous) = &item.completed_request {
            return if previous == &fingerprint {
                Ok(())
            } else {
                Err(Error::Conflict("completed cancellation changed".into()))
            };
        }
        self.execution
            .cancel_entries_for(&item.receipt.decision().scope, at_ns)?;
        item.completed_request = Some(fingerprint);
        Ok(())
    }
    pub fn pending_actions(&self) -> Vec<PendingAction> {
        self.actions
            .items
            .iter()
            .filter(|(_, item)| item.completed_request.is_none())
            .map(|((id, index), item)| PendingAction {
                decision_id: id.clone(),
                action_index: *index,
                account: item.receipt.decision().scope.account.clone(),
                kind: kind(&item.receipt.decision().actions[*index])
                    .unwrap()
                    .into(),
            })
            .collect()
    }
    /// The supplied plan is not trusted as the strategy authority: re-derive it
    /// from the retained committed action and compare before simulated submission.
    pub fn submit_reserved(&mut self, request: simulation_runtime::Submission<'_>) -> Result<()> {
        let boundary = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("no execution boundary".into()))?;
        if request.now_ns != boundary.evaluated_at_ns {
            return Err(Error::Conflict(
                "submission clock differs from playback boundary".into(),
            ));
        }
        let key = (request.plan.decision_id.clone(), request.plan.action_index);
        let item = self
            .actions
            .items
            .get_mut(&key)
            .ok_or_else(|| Error::Unready("submission has no committed action".into()))?;
        let fingerprint = content_hash(&(
            "playback-submit-v1",
            request.plan,
            request.funding,
            request.now_ns,
            request.latency_ns,
        ))?;
        if let Some(previous) = &item.completed_request {
            return if previous == &fingerprint {
                Ok(())
            } else {
                Err(Error::Conflict(
                    "completed action submission changed".into(),
                ))
            };
        }
        let bracket = &request.plan.bracket;
        let expected = decision_orders::bracket(
            &item.receipt,
            request.plan.action_index,
            &Allocation {
                account: bracket.account.clone(),
                instrument: bracket.instrument,
                quantity: bracket.quantity,
                price_scale: bracket.price_scale,
                tick: bracket.tick,
                entry_limit: bracket.entry,
                deadline_ns: bracket.deadline_ns,
            },
            request.now_ns,
            request.session.require_phase(request.now_ns)?,
            request.bands,
            request.risk_policy,
        )?;
        if content_hash(&expected)? != content_hash(request.plan)? {
            return Err(Error::Conflict(
                "submitted bracket differs from committed strategy action".into(),
            ));
        }
        self.execution.submit_reserved(request)?;
        item.completed_request = Some(fingerprint);
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn executable_actions_are_never_classified_as_no_work() {
        assert_eq!(kind(&Action::Wait { reason: "x".into() }), None);
        assert_eq!(kind(&Action::Hold { reason: "x".into() }), None);
        assert_eq!(
            kind(&Action::CancelEntry { reason: "x".into() }),
            Some("cancel_entry")
        );
        assert_eq!(
            kind(&Action::Exit {
                reason: arte_core::strategy_dispatch::ExitReason::ManualExit,
                quantity: 1,
                reduce_only: true
            }),
            Some("exit")
        );
    }
}
