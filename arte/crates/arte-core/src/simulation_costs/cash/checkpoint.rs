//! Cash component recovery. A committed parent checkpoint pins this object and
//! the last durable fill. This does not restore orders or settle portfolio cash.
use super::*;
use crate::seed_storage::Object;

const MAXIMUM_BYTES: usize = 16 * 1024;
// Remote derive deliberately does not give public OrderCash a Deserialize impl.
// All decoding must pass the validation below.
#[derive(Deserialize)]
#[serde(remote = "OrderCash", deny_unknown_fields)]
struct Image {
    manifest_hash: String,
    cost_model_hash: String,
    command_id: String,
    account: String,
    instrument: u64,
    price_scale: u8,
    entry_direction: Direction,
    entry_quantity: u64,
    exit_quantity: u64,
    trade_cash_atoms: i128,
    fees_minor: u64,
    last_sequence: u64,
    last_at_ns: u64,
    last_fill_hash: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Snapshot {
    version: u32,
    #[serde(with = "Image")]
    cash: OrderCash,
}
#[derive(Serialize)]
struct View<'a> {
    version: u32,
    cash: &'a OrderCash,
}
impl OrderCash {
    fn require_checkpoint(&self, costs: &Pinned, last_fill: &Fill) -> Result<()> {
        let charge = costs.charge(last_fill)?;
        if self.manifest_hash != costs.manifest_hash()
            || self.cost_model_hash != costs.hash()
            || self.command_id != last_fill.command_id
            || self.account != last_fill.account
            || self.instrument != last_fill.instrument
            || self.price_scale != last_fill.price_scale
            || self.last_sequence != last_fill.sequence
            || self.last_at_ns != last_fill.at_ns
            || self.last_fill_hash != content_hash(last_fill)?
            || self.entry_quantity == 0
            || self.exit_quantity > self.entry_quantity
            || self.fees_minor < charge.fee_minor
            || if last_fill.leg == Leg::Entry {
                self.exit_quantity != 0
                    || self.entry_direction != last_fill.direction
                    || last_fill.quantity > self.entry_quantity
                    || match self.entry_direction {
                        Direction::Buy => self.trade_cash_atoms >= 0,
                        Direction::Sell => self.trade_cash_atoms <= 0,
                    }
            } else {
                self.entry_direction == last_fill.direction
                    || last_fill.quantity > self.exit_quantity
            }
        {
            return Err(Error::Conflict(
                "cash checkpoint differs from durable fill or model".into(),
            ));
        }
        Ok(())
    }
    pub fn checkpoint(&self, costs: &Pinned, last_fill: &Fill) -> Result<Object> {
        self.require_checkpoint(costs, last_fill)?;
        // Identifiers are checked before encoding to bound allocation, including
        // JSON escaping. The scalar fields have fixed maximum encoded lengths.
        if self.command_id.len() > 128 || self.account.len() > 128 {
            return Err(Error::Capacity("cash checkpoint identity budget".into()));
        }
        let bytes = serde_json::to_vec(&View {
            version: 1,
            cash: self,
        })
        .map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > MAXIMUM_BYTES {
            return Err(Error::Capacity("cash checkpoint byte budget".into()));
        }
        Ok(Object::new(bytes))
    }
    /// Both expected_hash and last_fill must be selected by the committed parent
    /// recovery manifest. A caller-provided hash alone is not journal durability.
    pub fn restore_checkpoint(
        object: &Object,
        expected_hash: &str,
        costs: &Pinned,
        last_fill: &Fill,
    ) -> Result<Self> {
        if object.id != expected_hash || object.payload.len() > MAXIMUM_BYTES {
            return Err(Error::Invalid(
                "cash checkpoint identity or byte budget".into(),
            ));
        }
        object.verify()?;
        let snapshot: Snapshot = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.version != 1
            || snapshot.cash.checkpoint(costs, last_fill)?.payload != object.payload
        {
            return Err(Error::Invalid(
                "cash checkpoint version or noncanonical encoding".into(),
            ));
        }
        Ok(snapshot.cash)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::simulation_costs::tests::{fill, model, run};
    #[test]
    fn restored_partial_cash_continues_without_duplicate_fees() {
        let model = model();
        let costs = Pinned::new(model.clone(), &run(model.hash().unwrap())).unwrap();
        for direction in [Direction::Buy, Direction::Sell] {
            let mut entry = fill(3);
            entry.direction = direction;
            let mut original = OrderCash::new(&entry, &costs).unwrap();
            let mut exit = entry.clone();
            exit.sequence = 2;
            exit.leg = Leg::Exit;
            exit.direction = if direction == Direction::Buy {
                Direction::Sell
            } else {
                Direction::Buy
            };
            exit.quantity = 1;
            exit.price = 110;
            original.apply(&exit, &costs).unwrap();
            let image = original.checkpoint(&costs, &exit).unwrap();
            let mut restored =
                OrderCash::restore_checkpoint(&image, &image.id, &costs, &exit).unwrap();
            assert!(!restored.apply(&exit, &costs).unwrap());
            exit.sequence = 3;
            exit.quantity = 2;
            exit.price = 90;
            original.apply(&exit, &costs).unwrap();
            restored.apply(&exit, &costs).unwrap();
            assert_eq!(
                content_hash(&original).unwrap(),
                content_hash(&restored).unwrap()
            );
            assert_eq!(
                original.closed_net_cash_minor(&costs).unwrap(),
                restored.closed_net_cash_minor(&costs).unwrap()
            );
        }
    }
    #[test]
    fn foreign_fill_corruption_and_noncanonical_images_are_rejected() {
        let model = model();
        let costs = Pinned::new(model.clone(), &run(model.hash().unwrap())).unwrap();
        let entry = fill(3);
        let cash = OrderCash::new(&entry, &costs).unwrap();
        let image = cash.checkpoint(&costs, &entry).unwrap();
        let mut other = entry.clone();
        other.price += 1;
        assert!(OrderCash::restore_checkpoint(&image, &image.id, &costs, &other).is_err());
        let mut changed = image.clone();
        changed.payload.push(b' ');
        assert!(OrderCash::restore_checkpoint(&changed, &image.id, &costs, &entry).is_err());
        let changed = Object::new(changed.payload);
        assert!(OrderCash::restore_checkpoint(&changed, &changed.id, &costs, &entry).is_err());
        let mut value: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        value["cash"]["exit_quantity"] = 100.into();
        let changed = Object::new(serde_json::to_vec(&value).unwrap());
        assert!(OrderCash::restore_checkpoint(&changed, &changed.id, &costs, &entry).is_err());
        let mut other_model = model.clone();
        other_model.fixed_per_fill_minor += 1;
        let other_costs =
            Pinned::new(other_model.clone(), &run(other_model.hash().unwrap())).unwrap();
        assert!(OrderCash::restore_checkpoint(&image, &image.id, &other_costs, &entry).is_err());
    }
}
