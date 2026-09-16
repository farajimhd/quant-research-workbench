//! Per-order modeled trade cash. Updating this object does not settle a portfolio.
use super::*;
use crate::execution_events::{Direction, Leg};
pub mod checkpoint;

#[derive(Debug, Clone, Serialize)]
pub struct OrderCash {
    manifest_hash: String,
    cost_model_hash: String,
    command_id: String,
    account: String,
    instrument: u64,
    price_scale: u8,
    entry_direction: Direction,
    entry_quantity: u64,
    exit_quantity: u64,
    entry_notional_atoms: i128,
    exit_notional_atoms: i128,
    trade_cash_atoms: i128,
    fees_minor: u64,
    last_sequence: u64,
    last_at_ns: u64,
    last_fill_hash: String,
}
impl OrderCash {
    pub fn new(fill: &Fill, costs: &Pinned) -> Result<Self> {
        if fill.leg != Leg::Entry {
            return Err(Error::Unready(
                "cash projection requires entry prefix".into(),
            ));
        }
        let mut state = Self {
            manifest_hash: costs.manifest_hash().into(),
            cost_model_hash: costs.hash().into(),
            command_id: fill.command_id.clone(),
            account: fill.account.clone(),
            instrument: fill.instrument,
            price_scale: fill.price_scale,
            entry_direction: fill.direction,
            entry_quantity: 0,
            exit_quantity: 0,
            entry_notional_atoms: 0,
            exit_notional_atoms: 0,
            trade_cash_atoms: 0,
            fees_minor: 0,
            last_sequence: 0,
            last_at_ns: 0,
            last_fill_hash: String::new(),
        };
        state.apply(fill, costs)?;
        Ok(state)
    }
    /// Exact latest-fill retries are idempotent; older sequences are never replayed
    /// against advanced cash. Recovery must replay a verified ordered prefix.
    pub fn apply(&mut self, fill: &Fill, costs: &Pinned) -> Result<bool> {
        let charge = costs.charge(fill)?;
        if self.manifest_hash != costs.manifest_hash()
            || self.cost_model_hash != costs.hash()
            || self.command_id != fill.command_id
            || self.account != fill.account
            || self.instrument != fill.instrument
            || self.price_scale != fill.price_scale
        {
            return Err(Error::Conflict("cash fill scope differs".into()));
        }
        let hash = content_hash(fill)?;
        if fill.sequence <= self.last_sequence {
            return if fill.sequence == self.last_sequence && hash == self.last_fill_hash {
                Ok(false)
            } else {
                Err(Error::Conflict(
                    "cash fill sequence changed or rewound".into(),
                ))
            };
        }
        let entry = fill.leg == Leg::Entry;
        if fill.at_ns < self.last_at_ns {
            return Err(Error::Conflict("cash fill clock rewound".into()));
        }
        if (entry && (fill.direction != self.entry_direction || self.exit_quantity > 0))
            || (!entry && fill.direction == self.entry_direction)
        {
            return Err(Error::Invalid(
                "cash fill direction or lifecycle invalid".into(),
            ));
        }
        let mut next = self.clone();
        let quantity = if entry {
            &mut next.entry_quantity
        } else {
            &mut next.exit_quantity
        };
        *quantity = quantity
            .checked_add(fill.quantity)
            .ok_or_else(|| Error::Capacity("cash quantity overflow".into()))?;
        if next.exit_quantity > next.entry_quantity {
            return Err(Error::Invalid("cash exit exceeds held quantity".into()));
        }
        let notional = i128::from(fill.price)
            .checked_mul(i128::from(fill.quantity))
            .ok_or_else(|| Error::Capacity("fill notional overflow".into()))?;
        let change = if fill.direction == Direction::Buy {
            -notional
        } else {
            notional
        };
        let total = if entry {
            &mut next.entry_notional_atoms
        } else {
            &mut next.exit_notional_atoms
        };
        *total = total
            .checked_add(notional)
            .ok_or_else(|| Error::Capacity("order notional overflow".into()))?;
        next.trade_cash_atoms = next
            .trade_cash_atoms
            .checked_add(change)
            .ok_or_else(|| Error::Capacity("trade cash overflow".into()))?;
        next.fees_minor = next
            .fees_minor
            .checked_add(charge.fee_minor)
            .ok_or_else(|| Error::Capacity("cash fee total overflow".into()))?;
        next.last_sequence = fill.sequence;
        next.last_at_ns = fill.at_ns;
        next.last_fill_hash = hash;
        *self = next;
        Ok(true)
    }
    pub fn fees_minor(&self) -> u64 {
        self.fees_minor
    }
    /// Cumulative entry cost, not the cost basis of remaining FIFO lots.
    /// Divide by entry_quantity and 10^price_scale for average entry fill price.
    pub fn entry_notional_atoms(&self) -> i128 {
        self.entry_notional_atoms
    }
    pub fn exit_notional_atoms(&self) -> i128 {
        self.exit_notional_atoms
    }
    pub fn price_scale(&self) -> u8 {
        self.price_scale
    }
    pub fn last_at_ns(&self) -> u64 {
        self.last_at_ns
    }
    pub fn trade_cash_atoms(&self) -> i128 {
        self.trade_cash_atoms
    }
    pub fn entry_quantity(&self) -> u64 {
        self.entry_quantity
    }
    pub fn entry_direction(&self) -> Direction {
        self.entry_direction
    }
    pub fn exit_quantity(&self) -> u64 {
        self.exit_quantity
    }
    pub fn closed_net_cash_minor(&self, costs: &Pinned) -> Result<i128> {
        if self.manifest_hash != costs.manifest_hash() || self.cost_model_hash != costs.hash() {
            return Err(Error::Conflict("cash cost binding changed".into()));
        }
        if self.entry_quantity == 0 || self.entry_quantity != self.exit_quantity {
            return Err(Error::Unready("cash projection has open exposure".into()));
        }
        costs.net_cash_minor(self.trade_cash_atoms, self.price_scale, self.fees_minor)
    }
}

#[cfg(test)]
mod tests {
    use super::super::tests::{fill, model, run};
    use super::*;
    #[test]
    fn closed_cash_uses_actual_partial_fills_and_does_not_double_charge() {
        let m = model();
        let costs = Pinned::new(m.clone(), &run(m.hash().unwrap())).unwrap();
        let entry = fill(3);
        let mut state = OrderCash::new(&entry, &costs).unwrap();
        assert!(!state.apply(&entry, &costs).unwrap());
        assert!(state.closed_net_cash_minor(&costs).is_err());
        let mut exit = fill(1);
        exit.sequence = 2;
        exit.leg = Leg::Exit;
        exit.direction = Direction::Sell;
        exit.price = 110;
        state.apply(&exit, &costs).unwrap();
        assert!(state.closed_net_cash_minor(&costs).is_err());
        exit.sequence = 3;
        exit.quantity = 2;
        exit.price = 90;
        state.apply(&exit, &costs).unwrap();
        assert_eq!(state.trade_cash_atoms(), -10);
        assert_eq!(state.fees_minor(), 7);
        assert_eq!(state.closed_net_cash_minor(&costs).unwrap(), -17);
        let before = content_hash(&state).unwrap();
        assert!(!state.apply(&exit, &costs).unwrap());
        exit.price += 1;
        assert!(state.apply(&exit, &costs).is_err());
        exit.sequence += 1;
        assert!(state.apply(&exit, &costs).is_err());
        assert_eq!(content_hash(&state).unwrap(), before);
    }
    #[test]
    fn short_cash_and_scope_guards_are_explicit() {
        let m = model();
        let costs = Pinned::new(m.clone(), &run(m.hash().unwrap())).unwrap();
        let mut entry = fill(2);
        entry.direction = Direction::Sell;
        let mut state = OrderCash::new(&entry, &costs).unwrap();
        let mut exit = entry.clone();
        exit.sequence = 2;
        exit.leg = Leg::Target;
        exit.price = 90;
        let before = content_hash(&state).unwrap();
        let mut foreign = exit.clone();
        foreign.command_id = "another-order".into();
        assert!(state.apply(&foreign, &costs).is_err());
        assert!(state.apply(&exit, &costs).is_err());
        assert_eq!(content_hash(&state).unwrap(), before);
        exit.direction = Direction::Buy;
        state.apply(&exit, &costs).unwrap();
        assert_eq!(state.closed_net_cash_minor(&costs).unwrap(), 16);
        assert!(OrderCash::new(&exit, &costs).is_err());
    }
}
