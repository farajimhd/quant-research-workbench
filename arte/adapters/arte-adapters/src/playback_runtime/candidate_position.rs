//! Long-candidate reconciliation from journaled, strategy-owned simulated state.
use super::*;
use arte_core::{
    strategy_candidate::PositionObservation,
    strategy_dispatch::{Safety, Scope},
    strategy_protection::ActiveTarget,
};

pub struct Reconciled {
    pub position: PositionObservation,
    pub pending_exit_quantity: u64,
}
impl Reconciled {
    /// External risk/admission flags are retained; execution-owned fields cannot
    /// be supplied independently by the coordinator.
    pub fn safety(&self, external: &Safety) -> Safety {
        let mut safety = external.clone();
        safety.position_quantity = self.position.quantity;
        safety.pending_entry = self.position.pending_entry;
        safety.pending_exit_quantity = self.pending_exit_quantity;
        safety.exit_pending = self.pending_exit_quantity > 0;
        safety
    }
}
impl Runtime {
    /// Target provenance remains strategy-owned and must match actual protection.
    /// Heterogeneous working protection cannot be collapsed into the candidate's
    /// scalar stop/target contract. Reconcile it explicitly before evaluation.
    pub fn candidate_position(
        &self,
        scope: &Scope,
        target: Option<&ActiveTarget>,
    ) -> Result<Reconciled> {
        let run = self.decision_view()?;
        let boundary = run
            .pending()?
            .ok_or_else(|| Error::Unready("candidate boundary missing".into()))?;
        let projected = self.strategy_position(scope)?;
        let account = self.account_view(&scope.account)?;
        let mut quantity = 0_u64;
        let mut pending_entry = false;
        let mut pending_exit_quantity = 0_u64;
        let mut protection = None;
        for owned in account.orders.iter().filter(|owned| owned.scope == scope) {
            let order = owned.order;
            pending_entry |= !order.entry_cancelled && order.entry_filled < order.bracket.quantity;
            let held = order.entry_filled - order.exit_filled;
            quantity = quantity
                .checked_add(held)
                .ok_or_else(|| Error::Capacity("candidate quantity overflow".into()))?;
            if held == 0 {
                continue;
            }
            if order.bracket.side != arte_core::orders::Side::Long {
                return Err(Error::Conflict(
                    "selected candidate cannot manage short exposure".into(),
                ));
            }
            if order.exit_requested || order.stop_triggered {
                pending_exit_quantity = pending_exit_quantity
                    .checked_add(held)
                    .ok_or_else(|| Error::Capacity("candidate exit quantity overflow".into()))?;
            }
            let next = (
                order.active_stop,
                order.active_target,
                order.bracket.price_scale,
            );
            if protection.is_some_and(|previous| previous != next) {
                return Err(Error::Unready(
                    "candidate requires reconciled common protection".into(),
                ));
            }
            protection = Some(next);
        }
        if projected.map_or(0, |p| p.quantity) != quantity {
            return Err(Error::Conflict(
                "candidate attribution differs from orders".into(),
            ));
        }
        let (average_price, stop, target) = if let Some((stop, actual_target, scale)) = protection {
            let position = projected
                .ok_or_else(|| Error::Unready("candidate fill projection missing".into()))?;
            let target = target
                .ok_or_else(|| Error::Unready("candidate target provenance missing".into()))?;
            let price = target.price();
            if !price.is_finite()
                || price <= 0.
                || scale > 9
                || arte_core::events::Decimal::parse(&price.to_string())?.atoms_at_scale(scale)?
                    != actual_target
                || position.price_scale != scale
                || position.open_cost_atoms <= 0
                || position.open_cost_atoms > (1_i128 << 53)
                || quantity > (1_u64 << 53)
                || stop <= 0
                || stop > (1_i64 << 53)
            {
                return Err(Error::Conflict(
                    "candidate price evidence or conversion differs".into(),
                ));
            }
            let divisor = 10_f64.powi(i32::from(scale));
            (
                Some(position.open_cost_atoms as f64 / quantity as f64 / divisor),
                Some(stop as f64 / divisor),
                Some(target.clone()),
            )
        } else {
            (None, None, None)
        };
        Ok(Reconciled {
            position: PositionObservation {
                // Modeled boundary revision, not a broker receipt or provider clock.
                revision: boundary.sequence,
                at_ns: boundary.evaluated_at_ns,
                quantity,
                average_price,
                stop,
                target,
                pending_entry,
            },
            pending_exit_quantity,
        })
    }
}
