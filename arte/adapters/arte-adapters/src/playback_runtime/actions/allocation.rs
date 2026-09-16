//! Read-only cash/risk sizing from the owned executable quote. Reservation and
//! final validation remain in enter_action; another lane may consume cash first.
use super::*;
use arte_core::{events::Payload, order_funding};

#[derive(Debug, Clone, serde::Serialize, serde::Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Sizing {
    pub price_scale: u8,
    pub tick: i64,
    pub maximum_quantity: u64,
    pub lot_size: u64,
    pub order_lifetime_ns: u64,
}
pub struct SizingRequest<'a> {
    pub sizing: &'a Sizing,
    pub portfolio: &'a arte_core::portfolio::Portfolio,
    pub cash_policy: &'a order_funding::Policy,
    pub safety: simulation_runtime::AmendmentSafety<'a>,
    pub latency_ns: u64,
}
pub struct AllocatedEntry {
    /// Retain this for enter_action retry when result is an error. Never rerun
    /// sizing after the order's own reservation has reduced available cash.
    pub allocation: Allocation,
    pub result: Result<decision_orders::Plan>,
}
/// Read-only result, not an action-completion receipt. A rejected assessment must
/// still be journaled and reconciled before a runner may advance past the action.
pub enum EntryAssessment {
    Sized {
        allocation: Allocation,
        calculation: order_funding::sizing::Assessment,
    },
    Rejected(order_funding::sizing::Assessment),
}
impl Runtime {
    /// Size immediately before funding, rather than preparing all account orders
    /// from one cash snapshot. Portfolio still arbitrates concurrent lane races.
    /// Outer errors occur before funding; inner errors retain the exact allocation.
    pub fn allocate_and_enter_action(
        &mut self,
        decision_id: &str,
        action_index: usize,
        request: &SizingRequest<'_>,
    ) -> Result<AllocatedEntry> {
        let allocation = self.allocate_entry_action(
            decision_id,
            action_index,
            SizingRequest {
                sizing: request.sizing,
                portfolio: request.portfolio,
                cash_policy: request.cash_policy,
                safety: simulation_runtime::AmendmentSafety {
                    session: request.safety.session,
                    risk_policy: request.safety.risk_policy,
                    bands: request.safety.bands,
                },
                latency_ns: request.latency_ns,
            },
        )?;
        let result = self.enter_action(
            decision_id,
            action_index,
            EntryRequest {
                allocation: &allocation,
                portfolio: request.portfolio,
                cash_policy: request.cash_policy,
                safety: simulation_runtime::AmendmentSafety {
                    session: request.safety.session,
                    risk_policy: request.safety.risk_policy,
                    bands: request.safety.bands,
                },
                latency_ns: request.latency_ns,
            },
        );
        Ok(AllocatedEntry { allocation, result })
    }
    /// Initial proposal only. Retain a funded allocation for retry; never resize
    /// it using the remaining balance after its own reservation has been deducted.
    pub fn allocate_entry_action(
        &self,
        decision_id: &str,
        action_index: usize,
        request: SizingRequest<'_>,
    ) -> Result<Allocation> {
        match self.assess_entry_action(decision_id, action_index, request)? {
            EntryAssessment::Sized { allocation, .. } => Ok(allocation),
            EntryAssessment::Rejected(calculation) => {
                calculation.outcome()?.quantity()?;
                Err(Error::Conflict("rejected sizing produced quantity".into()))
            }
        }
    }
    /// Cash/risk insufficiency is a typed outcome. Missing/stale inputs, invalid
    /// configuration and uncertain submission state remain errors, not rejection.
    /// This method never reserves cash or completes a pending action.
    pub fn assess_entry_action(
        &self,
        decision_id: &str,
        action_index: usize,
        request: SizingRequest<'_>,
    ) -> Result<EntryAssessment> {
        let run = self.decision_view()?;
        let now = run
            .pending()?
            .ok_or_else(|| Error::Unready("allocation boundary missing".into()))?
            .evaluated_at_ns;
        let item = self
            .actions
            .items
            .get(&(decision_id.into(), action_index))
            .ok_or_else(|| Error::Unready("allocation committed action missing".into()))?;
        if item.reserved_request.is_some() || item.completed_request.is_some() {
            return Err(Error::Conflict(
                "funded action must retry its retained allocation".into(),
            ));
        }
        let sizing = request.sizing;
        if sizing.price_scale > 9
            || sizing.tick <= 0
            || sizing.maximum_quantity == 0
            || sizing.lot_size == 0
            || sizing.order_lifetime_ns == 0
        {
            return Err(Error::Invalid("allocation sizing policy".into()));
        }
        let scope = &item.receipt.decision().scope;
        self.execution
            .require_instrument_scale(scope.instrument, sizing.price_scale)?;
        let quote = run
            .quotes()?
            .require_executable(now, self.maximum_quote_age_ns)?;
        if quote.key.instrument != scope.instrument {
            return Err(Error::Conflict("allocation quote instrument".into()));
        }
        let Payload::Quote { ask, .. } = &quote.payload else {
            return Err(Error::Invalid("allocation requires quote".into()));
        };
        let mut allocation = Allocation {
            account: scope.account.clone(),
            instrument: scope.instrument,
            quantity: sizing.maximum_quantity,
            price_scale: sizing.price_scale,
            tick: sizing.tick,
            entry_limit: ask.atoms_at_scale(sizing.price_scale)?,
            deadline_ns: now
                .checked_add(sizing.order_lifetime_ns)
                .ok_or_else(|| Error::Capacity("allocation deadline overflow".into()))?,
        };
        let regular = request.safety.session.require_phase(now)?;
        let mut plan = decision_orders::bracket(
            &item.receipt,
            action_index,
            &allocation,
            now,
            regular,
            request.safety.bands,
            request.safety.risk_policy,
        )?;
        let account = request.portfolio.snapshot(&scope.account)?;
        if account.balance_at_ns > now || now - account.balance_at_ns > account.max_balance_age_ns {
            return Err(Error::Unready(
                "allocation account balance stale or future".into(),
            ));
        }
        if account.reservations.contains_key(&plan.bracket.command_id) {
            return Err(Error::Conflict("reserved command cannot be resized".into()));
        }
        let used = account
            .reservations
            .values()
            .try_fold(0u64, |n, r| n.checked_add(r.cash_minor))
            .ok_or_else(|| Error::Capacity("allocation reserved cash overflow".into()))?;
        let available = account
            .budget_minor
            .min(account.broker_available_minor)
            .saturating_sub(used);
        let calculation = order_funding::sizing::Assessment::new(order_funding::sizing::Input {
            entry: plan.bracket.entry as u64,
            stop: plan
                .bracket
                .stop
                .ok_or_else(|| Error::Unready("allocation stop missing".into()))?
                as u64,
            price_scale: sizing.price_scale,
            policy: request.cash_policy.clone(),
            available_cash_minor: available,
            maximum_quantity: sizing.maximum_quantity,
            lot_size: sizing.lot_size,
        })?;
        allocation.quantity = match calculation.outcome()? {
            order_funding::sizing::Outcome::Sized(quantity) => quantity,
            order_funding::sizing::Outcome::Rejected(_) => {
                return Ok(EntryAssessment::Rejected(calculation));
            }
        };
        plan.bracket.quantity = allocation.quantity;
        let funding = order_funding::requirements(
            &plan,
            request.cash_policy,
            now,
            regular,
            request.safety.bands,
            request.safety.risk_policy,
        )?;
        self.execution
            .validate_submission(&simulation_runtime::Submission {
                plan: &plan,
                funding: &funding,
                portfolio: request.portfolio,
                cash_policy: request.cash_policy,
                risk_policy: request.safety.risk_policy,
                bands: request.safety.bands,
                session: request.safety.session,
                now_ns: now,
                latency_ns: request.latency_ns,
            })?;
        Ok(EntryAssessment::Sized {
            allocation,
            calculation,
        })
    }
}
