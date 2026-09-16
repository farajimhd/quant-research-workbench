//! Borrowed, journal-gated account evidence. Protection stays per order.
use super::*;
use arte_core::strategy_dispatch::Scope;

pub struct OwnedOrder<'a> {
    pub scope: &'a Scope,
    pub order: &'a arte_core::simulated_execution::Position,
}
pub struct AccountView<'a> {
    /// Playback clock of this read, not a fabricated broker/provider receipt.
    pub evaluated_at_ns: u64,
    pub account: &'a str,
    pub instrument: u64,
    pub position: Option<&'a Position>,
    pub orders: Vec<OwnedOrder<'a>>,
}
impl Runtime {
    pub(crate) fn account_view<'a>(&'a self, account: &'a str) -> Result<AccountView<'a>> {
        self.ready()?;
        let key = Key {
            origin_hash: content_hash(&(
                "simulated-position-v1",
                self.simulator.run_id(),
                arte_core::simulated_execution::MODEL,
            ))?,
            account: account.into(),
            instrument: self.simulator.instrument(),
        };
        let position = self.projection.position(&key);
        let mut orders = Vec::new();
        let mut held = 0_u64;
        let mut direction = None;
        for order in self
            .simulator
            .positions()
            .iter()
            .filter(|p| p.bracket.account == account)
        {
            let scope = self
                .owners
                .get(&order.bracket.command_id)
                .ok_or_else(|| Error::Unready("account order ownership missing".into()))?;
            if scope.account != account
                || scope.instrument != key.instrument
                || scope.run_id != self.simulator.run_id()
                || scope.mode != arte_core::strategy_dispatch::Mode::Backtest
            {
                return Err(Error::Conflict("account order ownership differs".into()));
            }
            let open = order
                .entry_filled
                .checked_sub(order.exit_filled)
                .ok_or_else(|| Error::Conflict("account order fill quantities".into()))?;
            held = held
                .checked_add(open)
                .ok_or_else(|| Error::Capacity("account quantity overflow".into()))?;
            if open > 0 {
                let next = match order.bracket.side {
                    arte_core::orders::Side::Long => arte_core::execution_events::Direction::Buy,
                    arte_core::orders::Side::Short => arte_core::execution_events::Direction::Sell,
                };
                if direction.is_some_and(|previous| previous != next) {
                    return Err(Error::Conflict("mixed account position directions".into()));
                }
                direction = Some(next);
            }
            orders.push(OwnedOrder { scope, order });
        }
        if position.map_or(0, |p| p.quantity) != held
            || (held > 0 && position.and_then(|p| p.direction) != direction)
            || position.is_some_and(|p| p.last_at_ns > self.simulator.clock_ns())
        {
            return Err(Error::Conflict(
                "account projection differs from journaled execution".into(),
            ));
        }
        Ok(AccountView {
            evaluated_at_ns: self.simulator.clock_ns(),
            account,
            instrument: key.instrument,
            position,
            orders,
        })
    }
}
