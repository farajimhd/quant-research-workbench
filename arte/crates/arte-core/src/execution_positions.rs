//! Fill-derived FIFO projection. Not the broker balance or reservation authority.
use crate::{
    content_hash,
    execution_events::{Direction, Fill, Leg, Origin},
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};
#[derive(Debug, Clone, PartialEq, Eq, PartialOrd, Ord, Serialize, Deserialize)]
pub struct Key {
    pub origin_hash: String,
    pub account: String,
    pub instrument: u64,
}
#[cfg(test)]
mod tests {
    use super::*;
    fn fill(sequence: u64, leg: Leg, direction: Direction, quantity: u64, price: i64) -> Fill {
        Fill {
            schema_version: 1,
            origin: Origin::Simulated {
                run_id: "r".into(),
                model: "m".into(),
            },
            command_id: "c".into(),
            account: "a".into(),
            instrument: 1,
            price_scale: 2,
            sequence,
            at_ns: sequence,
            executed_at_ns: Some(sequence),
            leg,
            direction,
            quantity,
            price,
        }
    }
    #[test]
    fn fifo_partial_exit_exact_cost_and_duplicates() {
        let mut p = Projection::new(2, 10, 4).unwrap();
        let first = fill(1, Leg::Entry, Direction::Buy, 10, 100);
        let key = Key::from_fill(&first).unwrap();
        p.apply(&first).unwrap();
        assert!(!p.apply(&first).unwrap());
        p.apply(&fill(2, Leg::Entry, Direction::Buy, 5, 120))
            .unwrap();
        p.apply(&fill(3, Leg::Target, Direction::Sell, 12, 130))
            .unwrap();
        let state = p.position(&key).unwrap();
        assert_eq!(
            (
                state.quantity,
                state.open_cost_atoms,
                state.realized_gross_pnl_atoms,
                state.trade_cash_atoms
            ),
            (3, 360, 320, -40)
        );
        let before = content_hash(state).unwrap();
        assert!(p
            .apply(&fill(4, Leg::Exit, Direction::Sell, 4, 130))
            .is_err());
        assert_eq!(content_hash(p.position(&key).unwrap()).unwrap(), before);
        p.apply(&fill(4, Leg::Exit, Direction::Sell, 3, 110))
            .unwrap();
        assert_eq!(p.position(&key).unwrap().quantity, 0);
        assert_eq!(p.position(&key).unwrap().realized_gross_pnl_atoms, 290);
    }
    #[test]
    fn short_direction_scope_and_capacity_are_enforced() {
        let mut p = Projection::new(1, 3, 1).unwrap();
        let first = fill(1, Leg::Entry, Direction::Sell, 5, 100);
        let key = Key::from_fill(&first).unwrap();
        p.apply(&first).unwrap();
        assert!(p
            .apply(&fill(2, Leg::Entry, Direction::Sell, 1, 101))
            .is_err());
        assert!(p
            .apply(&fill(2, Leg::Entry, Direction::Buy, 1, 100))
            .is_err());
        let mut other = first.clone();
        other.account = "other".into();
        assert!(p.apply(&other).is_err());
        p.apply(&fill(2, Leg::Exit, Direction::Buy, 2, 80)).unwrap();
        let state = p.position(&key).unwrap();
        assert_eq!(state.realized_gross_pnl_atoms, 40);
        assert_eq!(state.quantity, 3);
        assert_eq!(state.open_cost_atoms, 300);
    }
}
impl Key {
    pub fn from_fill(fill: &Fill) -> Result<Self> {
        fill.id()?;
        let origin_hash = match &fill.origin {
            Origin::Broker {
                session_id, paper, ..
            } => content_hash(&("broker-position-v1", session_id, paper))?,
            Origin::Simulated { run_id, model } => {
                content_hash(&("simulated-position-v1", run_id, model))?
            }
        };
        Ok(Self {
            origin_hash,
            account: fill.account.clone(),
            instrument: fill.instrument,
        })
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
struct Lot {
    quantity: u64,
    price: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub price_scale: u8,
    pub direction: Option<Direction>,
    pub quantity: u64,
    /// Average entry is this exact numerator divided by quantity (in price atoms).
    pub open_cost_atoms: i128,
    pub realized_gross_pnl_atoms: i128,
    /// Signed trade cash only. Excludes fees, FX, settlement and margin treatment.
    pub trade_cash_atoms: i128,
    pub last_at_ns: u64,
    pub last_sequence: u64,
    lots: VecDeque<Lot>,
}
pub struct Projection {
    maximum_positions: usize,
    maximum_fills: usize,
    maximum_lots_per_position: usize,
    positions: BTreeMap<Key, Position>,
    seen: BTreeMap<String, String>,
}
fn add(a: i128, b: i128) -> Result<i128> {
    a.checked_add(b)
        .ok_or_else(|| Error::Invalid("position accounting overflow".into()))
}
impl Projection {
    pub fn new(
        maximum_positions: usize,
        maximum_fills: usize,
        maximum_lots_per_position: usize,
    ) -> Result<Self> {
        if maximum_positions == 0
            || maximum_positions > 100000
            || maximum_fills == 0
            || maximum_fills > 10000000
            || maximum_lots_per_position == 0
            || maximum_lots_per_position > 100000
        {
            return Err(Error::Invalid("invalid position projection bounds".into()));
        }
        Ok(Self {
            maximum_positions,
            maximum_fills,
            maximum_lots_per_position,
            positions: BTreeMap::new(),
            seen: BTreeMap::new(),
        })
    }
    pub fn position(&self, key: &Key) -> Option<&Position> {
        self.positions.get(key)
    }
    /// Call after durable fill acknowledgment. Validation failure changes no state.
    /// Returns false for an exact duplicate. Opposing entries and excess exits fail
    /// closed; they require an explicit reconciliation/correction workflow.
    pub fn apply(&mut self, fill: &Fill) -> Result<bool> {
        let id = fill.id()?;
        let hash = content_hash(fill)?;
        if let Some(previous) = self.seen.get(&id) {
            return if previous == &hash {
                Ok(false)
            } else {
                Err(Error::Conflict("accounted fill changed".into()))
            };
        }
        if self.seen.len() == self.maximum_fills {
            return Err(Error::Capacity("position fill identity budget".into()));
        }
        let key = Key::from_fill(fill)?;
        if !self.positions.contains_key(&key) && self.positions.len() == self.maximum_positions {
            return Err(Error::Capacity("position projection capacity".into()));
        }
        let mut next = self.positions.get(&key).cloned().unwrap_or(Position {
            price_scale: fill.price_scale,
            direction: None,
            quantity: 0,
            open_cost_atoms: 0,
            realized_gross_pnl_atoms: 0,
            trade_cash_atoms: 0,
            last_at_ns: 0,
            last_sequence: 0,
            lots: VecDeque::new(),
        });
        if next.price_scale != fill.price_scale
            || fill.at_ns < next.last_at_ns
            || fill.sequence < next.last_sequence
        {
            return Err(Error::Invalid(
                "fill projection scale or causal clock changed".into(),
            ));
        }
        let notional = i128::from(fill.price) * i128::from(fill.quantity);
        next.trade_cash_atoms = add(
            next.trade_cash_atoms,
            if fill.direction == Direction::Buy {
                -notional
            } else {
                notional
            },
        )?;
        if fill.leg == Leg::Entry {
            if next.direction.is_some_and(|d| d != fill.direction) {
                return Err(Error::Unready(
                    "opposing entry requires explicit netting reconciliation".into(),
                ));
            }
            next.quantity = next
                .quantity
                .checked_add(fill.quantity)
                .ok_or_else(|| Error::Invalid("position quantity overflow".into()))?;
            next.open_cost_atoms = add(next.open_cost_atoms, notional)?;
            next.direction = Some(fill.direction);
            if let Some(last) = next.lots.back_mut().filter(|lot| lot.price == fill.price) {
                last.quantity = last
                    .quantity
                    .checked_add(fill.quantity)
                    .ok_or_else(|| Error::Invalid("lot quantity overflow".into()))?;
            } else {
                if next.lots.len() == self.maximum_lots_per_position {
                    return Err(Error::Capacity("position lot budget".into()));
                }
                next.lots.push_back(Lot {
                    quantity: fill.quantity,
                    price: fill.price,
                });
            }
        } else {
            if next.quantity < fill.quantity
                || next.direction.is_none()
                || next.direction == Some(fill.direction)
            {
                return Err(Error::Conflict(
                    "exit exceeds or increases projected position".into(),
                ));
            }
            let mut remaining = fill.quantity;
            while remaining > 0 {
                let lot = next
                    .lots
                    .front_mut()
                    .ok_or_else(|| Error::Invalid("missing position lot".into()))?;
                let count = remaining.min(lot.quantity);
                let delta = i128::from(fill.price) - i128::from(lot.price);
                let profit = delta
                    * i128::from(count)
                    * if next.direction == Some(Direction::Buy) {
                        1
                    } else {
                        -1
                    };
                next.realized_gross_pnl_atoms = add(next.realized_gross_pnl_atoms, profit)?;
                next.open_cost_atoms -= i128::from(lot.price) * i128::from(count);
                remaining -= count;
                lot.quantity -= count;
                if lot.quantity == 0 {
                    next.lots.pop_front();
                }
            }
            next.quantity -= fill.quantity;
            if next.quantity == 0 {
                next.direction = None;
            }
        }
        next.last_at_ns = fill.at_ns;
        next.last_sequence = fill.sequence;
        self.positions.insert(key, next);
        self.seen.insert(id, hash);
        Ok(true)
    }
}
