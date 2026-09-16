//! Bounded terminal-order funding reconciliation. Fill journals remain the
//! authority for realized cash; this module does not infer or synthesize fills.
use super::*;
use arte_core::{portfolio::Portfolio, simulation_costs::SettlementCurrency};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    ReleaseUnfilled,
    SettleClosed,
}
pub struct Outcome {
    pub command_id: String,
    pub account: String,
    pub instrument: u64,
    pub kind: Kind,
    pub result: Result<bool>,
}
impl Runtime {
    /// Stable submission order. Errors retain funding and are reported per order;
    /// other orders in the bounded selection can still reconcile successfully.
    pub(crate) fn reconcile_funding(
        &mut self,
        portfolio: &Portfolio,
        currencies: &BTreeMap<u64, SettlementCurrency>,
        maximum_orders: usize,
        maximum_receipts: usize,
    ) -> Result<Vec<Outcome>> {
        self.ready()?;
        if maximum_orders == 0
            || maximum_orders > 4096
            || maximum_receipts == 0
            || maximum_receipts > 1_000_000
        {
            return Err(Error::Capacity("funding reconciliation limits".into()));
        }
        let selected: Vec<_> = self
            .simulator
            .positions()
            .iter()
            .filter_map(|order| {
                if self.released.contains(&order.bracket.command_id) {
                    return None;
                }
                let kind =
                    if order.entry_cancelled && order.entry_filled == 0 && order.exit_filled == 0 {
                        Kind::ReleaseUnfilled
                    } else if order.entry_filled > 0
                        && order.entry_filled == order.exit_filled
                        && (order.entry_cancelled || order.entry_filled == order.bracket.quantity)
                    {
                        Kind::SettleClosed
                    } else {
                        return None;
                    };
                Some((
                    order.bracket.command_id.clone(),
                    order.bracket.account.clone(),
                    order.bracket.instrument,
                    kind,
                ))
            })
            .take(maximum_orders)
            .collect();
        Ok(selected
            .into_iter()
            .map(|(command_id, account, instrument, kind)| {
                let result = match kind {
                    Kind::ReleaseUnfilled => {
                        self.release_unfilled_reservation(&command_id, portfolio)
                    }
                    Kind::SettleClosed => match currencies.get(&instrument) {
                        Some(currency) => self.settle_closed_order(
                            &command_id,
                            portfolio,
                            currency,
                            maximum_receipts,
                        ),
                        None => Err(Error::Unready(
                            "settlement currency evidence missing".into(),
                        )),
                    },
                };
                Outcome {
                    command_id,
                    account,
                    instrument,
                    kind,
                    result,
                }
            })
            .collect())
    }
}
