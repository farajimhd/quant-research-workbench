//! Component recovery only. The coordinator supplies the hash of the pinned
//! run/cut and must restore matching orders, cash, strategy and journal cursors.
use super::*;
use crate::seed_storage::Object;
use std::io::Write;

#[derive(Debug, Clone, Copy)]
pub struct Limits {
    pub positions: usize,
    pub fills: usize,
    pub lots_per_position: usize,
    pub bytes: usize,
}
impl Limits {
    fn empty(self) -> Result<Projection> {
        if self.bytes == 0 || self.bytes > 64 * 1024 * 1024 {
            return Err(Error::Invalid("projection checkpoint byte budget".into()));
        }
        Projection::new(self.positions, self.fills, self.lots_per_position)
    }
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    context_hash: String,
    positions: Vec<(Key, Position)>,
    seen: BTreeMap<String, String>,
}
#[derive(Serialize)]
struct View<'a> {
    version: u32,
    context_hash: &'a str,
    positions: Vec<(&'a Key, &'a Position)>,
    seen: &'a BTreeMap<String, String>,
}
fn hash(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
struct Bounded {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for Bounded {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("projection checkpoint byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
impl Projection {
    pub fn checkpoint(&self, context_hash: &str, limits: Limits) -> Result<Object> {
        limits.empty()?;
        if !hash(context_hash)
            || self.positions.len() > limits.positions
            || self.seen.len() > limits.fills
            || self
                .positions
                .values()
                .any(|p| p.lots.len() > limits.lots_per_position)
        {
            return Err(Error::Invalid(
                "projection checkpoint context or capacity".into(),
            ));
        }
        let view = View {
            version: 1,
            context_hash,
            positions: self.positions.iter().collect(),
            seen: &self.seen,
        };
        let mut writer = Bounded {
            bytes: Vec::new(),
            maximum: limits.bytes,
        };
        serde_json::to_writer(&mut writer, &view)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        Ok(Object::new(writer.bytes))
    }

    /// Expected content and context hashes must come from the committed parent
    /// checkpoint, not from this object's untrusted payload.
    pub fn restore_checkpoint(
        object: &Object,
        expected_hash: &str,
        context_hash: &str,
        limits: Limits,
    ) -> Result<Self> {
        let mut restored = limits.empty()?;
        if !hash(context_hash) || object.id != expected_hash || object.payload.len() > limits.bytes
        {
            return Err(Error::Invalid(
                "projection checkpoint identity or budget".into(),
            ));
        }
        object.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.version != 1
            || snapshot.context_hash != context_hash
            || snapshot.positions.len() > limits.positions
            || snapshot.seen.len() > limits.fills
            || snapshot
                .seen
                .iter()
                .any(|(id, value)| !hash(id) || !hash(value))
            || snapshot.positions.len() > snapshot.seen.len()
        {
            return Err(Error::Invalid("projection checkpoint contract".into()));
        }
        for (key, position) in snapshot.positions {
            if !hash(&key.origin_hash)
                || key.account.is_empty()
                || key.instrument == 0
                || position.price_scale > 9
                || position.last_sequence == 0
                || position.lots.len() > limits.lots_per_position
                || (position.quantity == 0) != position.direction.is_none()
            {
                return Err(Error::Invalid("projection position contract".into()));
            }
            let mut quantity = 0u64;
            let mut cost = 0i128;
            let mut previous_price = None;
            for lot in &position.lots {
                if lot.quantity == 0 || lot.price <= 0 || previous_price == Some(lot.price) {
                    return Err(Error::Invalid("projection FIFO lot contract".into()));
                }
                quantity = quantity
                    .checked_add(lot.quantity)
                    .ok_or_else(|| Error::Invalid("projection quantity overflow".into()))?;
                cost = add(cost, i128::from(lot.quantity) * i128::from(lot.price))?;
                previous_price = Some(lot.price);
            }
            let signed_cost = if position.direction == Some(Direction::Sell) {
                -cost
            } else {
                cost
            };
            if quantity != position.quantity
                || cost != position.open_cost_atoms
                || add(position.trade_cash_atoms, signed_cost)? != position.realized_gross_pnl_atoms
                || restored.positions.insert(key, position).is_some()
            {
                return Err(Error::Invalid(
                    "projection accounting or duplicate key".into(),
                ));
            }
        }
        restored.seen = snapshot.seen;
        if restored.checkpoint(context_hash, limits)?.payload != object.payload {
            return Err(Error::Invalid("noncanonical projection checkpoint".into()));
        }
        Ok(restored)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn limits() -> Limits {
        Limits {
            positions: 4,
            fills: 20,
            lots_per_position: 10,
            bytes: 16384,
        }
    }
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
    fn fifo_and_duplicate_receipts_survive_restore() {
        for entry in [Direction::Buy, Direction::Sell] {
            let exit = if entry == Direction::Buy {
                Direction::Sell
            } else {
                Direction::Buy
            };
            let mut original = limits().empty().unwrap();
            let first = fill(1, Leg::Entry, entry, 10, 100);
            original.apply(&first).unwrap();
            original.apply(&fill(2, Leg::Entry, entry, 5, 120)).unwrap();
            original.apply(&fill(3, Leg::Exit, exit, 12, 130)).unwrap();
            let context = "a".repeat(64);
            let image = original.checkpoint(&context, limits()).unwrap();
            let mut restored =
                Projection::restore_checkpoint(&image, &image.id, &context, limits()).unwrap();
            assert!(!restored.apply(&first).unwrap());
            let last = fill(4, Leg::Exit, exit, 3, 110);
            original.apply(&last).unwrap();
            restored.apply(&last).unwrap();
            assert_eq!(
                original.checkpoint(&context, limits()).unwrap().id,
                restored.checkpoint(&context, limits()).unwrap().id
            );
        }
    }
    #[test]
    fn tampered_accounting_context_and_budgets_fail_closed() {
        let mut projection = limits().empty().unwrap();
        projection
            .apply(&fill(1, Leg::Entry, Direction::Buy, 10, 100))
            .unwrap();
        let context = "a".repeat(64);
        let image = projection.checkpoint(&context, limits()).unwrap();
        assert!(
            Projection::restore_checkpoint(&image, &image.id, &"b".repeat(64), limits()).is_err()
        );
        let mut small = limits();
        small.bytes = 10;
        assert!(projection.checkpoint(&context, small).is_err());
        assert!(Projection::restore_checkpoint(&image, &image.id, &context, small).is_err());
        let mut snapshot: Snapshot = serde_json::from_slice(&image.payload).unwrap();
        snapshot.positions[0].1.open_cost_atoms += 1;
        let changed = Object::new(serde_json::to_vec(&snapshot).unwrap());
        assert!(Projection::restore_checkpoint(&changed, &changed.id, &context, limits()).is_err());
        let mut payload = image.payload.clone();
        payload.push(b' ');
        let changed = Object::new(payload);
        assert!(Projection::restore_checkpoint(&changed, &changed.id, &context, limits()).is_err());
    }
}
