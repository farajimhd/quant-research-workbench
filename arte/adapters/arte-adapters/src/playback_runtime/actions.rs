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
/// Explicit portfolio allocation and verified market/risk inputs. The strategy
/// decision is deliberately absent: the controller owns its committed receipt.
pub struct EntryRequest<'a> {
    pub allocation: &'a Allocation,
    pub portfolio: &'a arte_core::portfolio::Portfolio,
    pub cash_policy: &'a arte_core::order_funding::Policy,
    pub safety: simulation_runtime::AmendmentSafety<'a>,
    pub latency_ns: u64,
}
struct Item {
    receipt: Arc<Committed>,
    completed_request: Option<String>,
    reserved_request: Option<String>,
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
    pub(super) fn restore(
        progress: &[super::checkpoint::ActionProgress],
        receipts: &[&Committed],
    ) -> Result<Self> {
        if progress.len() > 4096 * 16 || receipts.len() > 4096 {
            return Err(Error::Capacity("action recovery population".into()));
        }
        let mut work = Self::default();
        let mut decisions = std::collections::BTreeSet::new();
        for receipt in receipts {
            Self::validate(receipt.decision())?;
            if !decisions.insert(&receipt.decision().decision_id) {
                return Err(Error::Conflict("duplicate action recovery decision".into()));
            }
            work.record(receipt);
        }
        if progress.len() != work.items.len() {
            return Err(Error::Conflict("action recovery population differs".into()));
        }
        for (saved, ((id, index), item)) in progress.iter().zip(work.items.iter_mut()) {
            if &saved.decision_id != id
                || saved.action_index != *index
                || saved.decision_hash != content_hash(item.receipt.decision())?
                || saved.completed_request.as_ref().is_some_and(|h| {
                    h.len() != 64
                        || !h
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
                || saved.reserved_request.as_ref().is_some_and(|h| {
                    h.len() != 64
                        || !h
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
                })
                || (matches!(
                    item.receipt.decision().actions[*index],
                    Action::Enter(_) | Action::Add(_)
                ) && saved.completed_request.is_some()
                    && saved.reserved_request != saved.completed_request)
                || (!matches!(
                    item.receipt.decision().actions[*index],
                    Action::Enter(_) | Action::Add(_)
                ) && saved.reserved_request.is_some())
            {
                return Err(Error::Conflict(
                    "action recovery receipt or identity differs".into(),
                ));
            }
            item.completed_request = saved.completed_request.clone();
            item.reserved_request = saved.reserved_request.clone();
        }
        Ok(work)
    }
    pub(super) fn checkpoint(&self) -> Result<Vec<super::checkpoint::ActionProgress>> {
        if self.items.len() > 4096 * 16 {
            return Err(Error::Capacity(
                "playback action recovery population".into(),
            ));
        }
        self.items
            .iter()
            .map(|((decision_id, action_index), item)| {
                Ok(super::checkpoint::ActionProgress {
                    decision_id: decision_id.clone(),
                    action_index: *action_index,
                    decision_hash: content_hash(item.receipt.decision())?,
                    completed_request: item.completed_request.clone(),
                    reserved_request: item.reserved_request.clone(),
                })
            })
            .collect()
    }
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
                        reserved_request: None,
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
    /// Build, reserve and submit exactly the retained committed entry/add action.
    /// On submission failure the reservation remains for exact retry. This does
    /// not resize orders or silently discard an unfunded strategy decision.
    pub fn enter_action(
        &mut self,
        decision_id: &str,
        action_index: usize,
        request: EntryRequest<'_>,
    ) -> Result<decision_orders::Plan> {
        let at_ns = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("no entry boundary".into()))?
            .evaluated_at_ns;
        let item = self
            .actions
            .items
            .get(&(decision_id.into(), action_index))
            .ok_or_else(|| Error::Unready("no committed entry action".into()))?;
        let regular = request.safety.session.require_phase(at_ns)?;
        let plan = decision_orders::bracket(
            &item.receipt,
            action_index,
            request.allocation,
            at_ns,
            regular,
            request.safety.bands,
            request.safety.risk_policy,
        )?;
        // A completed request must be compared before touching portfolio state.
        // submit_reserved retains the exact completion fingerprint for retries.
        let funding = arte_core::order_funding::requirements(
            &plan,
            request.cash_policy,
            at_ns,
            regular,
            request.safety.bands,
            request.safety.risk_policy,
        )?;
        let submission = simulation_runtime::Submission {
            plan: &plan,
            funding: &funding,
            portfolio: request.portfolio,
            cash_policy: request.cash_policy,
            risk_policy: request.safety.risk_policy,
            bands: request.safety.bands,
            session: request.safety.session,
            now_ns: at_ns,
            latency_ns: request.latency_ns,
        };
        let fingerprint = content_hash(&(
            "playback-submit-v1",
            &plan,
            &funding,
            at_ns,
            request.latency_ns,
        ))?;
        if item
            .reserved_request
            .as_ref()
            .is_some_and(|previous| previous != &fingerprint)
        {
            return Err(Error::Conflict("reserved action request changed".into()));
        }
        if item.completed_request.is_none() {
            self.execution.validate_submission(&submission)?;
            arte_core::order_funding::reserve(
                request.portfolio,
                &plan,
                request.cash_policy,
                at_ns,
                regular,
                request.safety.bands,
                request.safety.risk_policy,
            )?;
            self.actions
                .items
                .get_mut(&(decision_id.into(), action_index))
                .unwrap()
                .reserved_request = Some(fingerprint);
        }
        self.submit_reserved(submission)?;
        Ok(plan)
    }
    pub fn protection_action(
        &mut self,
        decision_id: &str,
        action_index: usize,
        safety: simulation_runtime::AmendmentSafety<'_>,
    ) -> Result<()> {
        let at_ns = self
            .decision_view()?
            .pending()?
            .ok_or_else(|| Error::Unready("no protection boundary".into()))?
            .evaluated_at_ns;
        let item = self
            .actions
            .items
            .get_mut(&(decision_id.into(), action_index))
            .ok_or_else(|| Error::Unready("no committed protection action".into()))?;
        let decision = item.receipt.decision();
        let action = decision
            .actions
            .get(action_index)
            .ok_or_else(|| Error::Invalid("missing protection action".into()))?;
        if !matches!(action, Action::ReplaceStop(_) | Action::ReplaceTarget(_)) {
            return Err(Error::Invalid(
                "action is not a protection replacement".into(),
            ));
        }
        let fingerprint = content_hash(&(
            "playback-protection-v1",
            decision_id,
            action_index,
            at_ns,
            action,
        ))?;
        if let Some(previous) = &item.completed_request {
            return if previous == &fingerprint {
                Ok(())
            } else {
                Err(Error::Conflict("completed protection changed".into()))
            };
        }
        let target_update = if let Action::ReplaceTarget(proposal) = action {
            Some((
                content_hash(&decision.scope)?,
                super::candidate_position::TargetRecord {
                    target: proposal.target.clone(),
                    decision_hash: content_hash(decision)?,
                    at_ns,
                },
            ))
        } else {
            None
        };
        self.execution.replace_for(
            &decision.scope,
            action,
            decision.safety.position_quantity,
            at_ns,
            safety,
        )?;
        if let Some((scope, target)) = target_update {
            self.targets.insert(scope, target);
        }
        item.completed_request = Some(fingerprint);
        Ok(())
    }
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
        if item
            .reserved_request
            .as_ref()
            .is_some_and(|previous| previous != &fingerprint)
        {
            return Err(Error::Conflict("reserved action submission changed".into()));
        }
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
        let decision = item.receipt.decision();
        let scope_hash = content_hash(&decision.scope)?;
        let target = match &decision.actions[request.plan.action_index] {
            Action::Enter(proposal) => proposal
                .target_selection
                .clone()
                .map(|target| {
                    arte_core::strategy_protection::ActiveTarget::Structure(Box::new(target))
                })
                .unwrap_or(arte_core::strategy_protection::ActiveTarget::Official {
                    price: proposal.target,
                }),
            Action::Add(proposal) => {
                let record = self
                    .targets
                    .get(&scope_hash)
                    .ok_or_else(|| Error::Unready("add target provenance missing".into()))?;
                if record.target.price() != proposal.target {
                    return Err(Error::Conflict("add target provenance differs".into()));
                }
                record.target.clone()
            }
            _ => return Err(Error::Invalid("submission action has no target".into())),
        };
        if arte_core::events::Decimal::parse(&target.price().to_string())?
            .atoms_at_scale(bracket.price_scale)?
            != bracket.target.unwrap_or(0)
        {
            return Err(Error::Conflict("submitted target metadata differs".into()));
        }
        let target = super::candidate_position::TargetRecord {
            target,
            decision_hash: content_hash(decision)?,
            at_ns: request.now_ns,
        };
        self.execution.validate_submission(&request)?;
        item.reserved_request = Some(fingerprint.clone());
        self.execution.submit_reserved(request)?;
        self.targets.insert(scope_hash, target);
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
