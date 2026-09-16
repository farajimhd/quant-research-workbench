//! Quiescent execution-lane checkpoint. No pending fill publication is captured.
//! Whole-run recovery must bind this graph to matching portfolio and market cuts.
use super::*;
use arte_core::{
    execution_positions::checkpoint::Limits as ProjectionLimits,
    portfolio::{checkpoint::Cut, Reservation},
    run_manifest::Pinned as Run,
    seed_storage::Object,
    simulation_costs::{cash::OrderCash, Pinned as Costs},
    strategy_dispatch::Scope,
};
use serde::Deserialize;
use std::{
    collections::{BTreeMap, BTreeSet},
    io::Write,
};
#[cfg(test)]
mod tests;

#[derive(Default)]
struct Aggregate {
    quantity: u64,
    cash: i128,
    direction: Option<arte_core::execution_events::Direction>,
    at: u64,
    sequence: u64,
}

#[derive(Clone, Copy)]
pub struct Limits {
    pub maximum_bytes: usize,
    pub maximum_orders: usize,
    pub maximum_pending_fills: usize,
    pub projection: ProjectionLimits,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Cash {
    object: String,
    last_fill: Fill,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Root {
    version: u32,
    manifest_hash: String,
    cost_model_hash: String,
    cut: Cut,
    source: (u16, u64, u32),
    last_source_quote: Option<(arte_core::events::Observation, u64, u64)>,
    simulator: String,
    simulator_hash: String,
    projection: String,
    owners: BTreeMap<String, Scope>,
    reservations: BTreeMap<String, Reservation>,
    released: BTreeSet<String>,
    cash: BTreeMap<String, Cash>,
}
pub struct Bundle {
    pub root: Object,
    pub objects: BTreeMap<String, Object>,
}
fn context(run: &Run, cut: &Cut, limits: Limits) -> Result<String> {
    if run.manifest().mode != arte_core::strategy_dispatch::Mode::Backtest
        || cut.boundary_sequence == 0
        || cut.boundary_hash.len() != 64
        || !cut
            .boundary_hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        || limits.maximum_bytes == 0
        || limits.maximum_bytes > 64 * 1024 * 1024
        || limits.maximum_orders == 0
        || limits.maximum_orders > 100_000
        || limits.maximum_pending_fills == 0
        || limits.maximum_pending_fills > 200_000
    {
        return Err(Error::Invalid(
            "execution checkpoint context or limits".into(),
        ));
    }
    content_hash(&("arte.execution-cut.v1", run.hash(), cut))
}
struct Writer {
    bytes: Vec<u8>,
    maximum: usize,
}
impl Write for Writer {
    fn write(&mut self, bytes: &[u8]) -> std::io::Result<usize> {
        if bytes.len() > self.maximum.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("execution checkpoint byte budget"));
        }
        self.bytes.extend_from_slice(bytes);
        Ok(bytes.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn encode(root: &Root, maximum: usize) -> Result<Object> {
    let mut writer = Writer {
        bytes: Vec::new(),
        maximum,
    };
    serde_json::to_writer(&mut writer, root).map_err(|e| Error::Serialization(e.to_string()))?;
    Ok(Object::new(writer.bytes))
}
impl Runtime {
    /// Call only after all generated fills have durable journal acknowledgments.
    /// Last fills must be supplied from that journal; missing or extra rows fail.
    pub fn checkpoint(
        &self,
        run: &Run,
        cut: &Cut,
        last_fills: &BTreeMap<String, Fill>,
        limits: Limits,
    ) -> Result<Bundle> {
        self.ready()?;
        let context = context(run, cut, limits)?;
        let costs = self
            .costs
            .as_ref()
            .ok_or_else(|| Error::Unready("execution costs unbound".into()))?;
        self.validate_checkpoint(run, cut, costs, last_fills, limits)?;
        let source = self
            .source
            .ok_or_else(|| Error::Unready("execution source missing".into()))?;
        let (simulator_hash, bytes) = self.simulator.checkpoint(limits.maximum_bytes)?;
        let simulator = Object::new(bytes);
        let projection = self.projection.checkpoint(&context, limits.projection)?;
        let mut objects = BTreeMap::new();
        let mut root = Root {
            version: 1,
            manifest_hash: run.hash().into(),
            cost_model_hash: costs.hash().into(),
            cut: cut.clone(),
            source: (source.provider, source.instrument, source.session),
            last_source_quote: self.last_source_quote.clone(),
            simulator: simulator.id.clone(),
            simulator_hash,
            projection: projection.id.clone(),
            owners: self.owners.clone(),
            reservations: self.reservations.clone(),
            released: self.released.clone(),
            cash: BTreeMap::new(),
        };
        let mut used = 0usize;
        for object in [simulator, projection] {
            used = add_object(&mut objects, object, used, limits.maximum_bytes)?;
        }
        for (command, cash) in &self.cash {
            let fill = &last_fills[command];
            let object = cash.checkpoint(costs, fill)?;
            root.cash.insert(
                command.clone(),
                Cash {
                    object: object.id.clone(),
                    last_fill: fill.clone(),
                },
            );
            used = add_object(&mut objects, object, used, limits.maximum_bytes)?;
        }
        Ok(Bundle {
            root: encode(&root, limits.maximum_bytes - used)?,
            objects,
        })
    }
    pub fn restore_checkpoint(
        bundle: &Bundle,
        expected_root: &str,
        run: &Run,
        cut: &Cut,
        costs: Costs,
        limits: Limits,
    ) -> Result<Self> {
        let context = context(run, cut, limits)?;
        if bundle.root.id != expected_root || bundle.objects.len() > limits.maximum_orders + 2 {
            return Err(Error::Conflict(
                "execution root or object count differs".into(),
            ));
        }
        let total = bundle
            .objects
            .values()
            .try_fold(bundle.root.payload.len(), |n, o| {
                n.checked_add(o.payload.len())
            })
            .ok_or_else(|| Error::Capacity("execution checkpoint size overflow".into()))?;
        if total > limits.maximum_bytes {
            return Err(Error::Capacity("execution checkpoint byte budget".into()));
        }
        bundle.root.verify()?;
        let root: Root = serde_json::from_slice(&bundle.root.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if root.version != 1
            || root.manifest_hash != run.hash()
            || root.cut != *cut
            || root.cost_model_hash != costs.hash()
            || costs.manifest_hash() != run.hash()
            || root.owners.len() > limits.maximum_orders
            || root.reservations.len() > limits.maximum_orders
            || root.released.len() > limits.maximum_orders
            || root.cash.len() > limits.maximum_orders
            || encode(&root, limits.maximum_bytes)?.payload != bundle.root.payload
        {
            return Err(Error::Conflict("execution recovery context differs".into()));
        }
        let expected: BTreeSet<_> = [&root.simulator, &root.projection]
            .into_iter()
            .chain(root.cash.values().map(|c| &c.object))
            .cloned()
            .collect();
        if expected != bundle.objects.keys().cloned().collect() {
            return Err(Error::Conflict(
                "execution checkpoint object set differs".into(),
            ));
        }
        for (id, object) in &bundle.objects {
            if id != &object.id {
                return Err(Error::Conflict("execution object key differs".into()));
            }
            object.verify()?;
        }
        let simulator = Simulator::restore(
            &bundle.objects[&root.simulator].payload,
            &root.simulator_hash,
            limits.maximum_bytes,
        )?;
        let projection = Projection::restore_checkpoint(
            &bundle.objects[&root.projection],
            &root.projection,
            &context,
            limits.projection,
        )?;
        let mut restored = Self::new(simulator, projection, limits.maximum_pending_fills)?;
        restored.bind_source(arte_core::event_order::Scope {
            provider: root.source.0,
            instrument: root.source.1,
            session: root.source.2,
        })?;
        let mut fills = BTreeMap::new();
        for (command, cash) in root.cash {
            restored.cash.insert(
                command.clone(),
                OrderCash::restore_checkpoint(
                    &bundle.objects[&cash.object],
                    &cash.object,
                    &costs,
                    &cash.last_fill,
                )?,
            );
            fills.insert(command, cash.last_fill);
        }
        restored.owners = root.owners;
        restored.reservations = root.reservations;
        restored.released = root.released;
        restored.last_source_quote = root.last_source_quote;
        restored.validate_checkpoint(run, cut, &costs, &fills, limits)?;
        restored.costs = Some(costs);
        Ok(restored)
    }
    fn validate_checkpoint(
        &self,
        run: &Run,
        cut: &Cut,
        costs: &Costs,
        fills: &BTreeMap<String, Fill>,
        limits: Limits,
    ) -> Result<()> {
        let orders = self.simulator.positions();
        if self.simulator.run_id() != run.manifest().run_id
            || costs.manifest_hash() != run.hash()
            || self.simulator.clock_ns() != cut.at_ns
            || orders.len() > limits.maximum_orders
            || self.simulator.maximum_quote_fills() / 2 > limits.maximum_orders
            || self.owners.len() != orders.len()
            || self.reservations.len() != orders.len()
            || self.cash.len() != fills.len()
            || !self.cash.keys().eq(fills.keys())
            || self.cash.keys().any(|c| !self.owners.contains_key(c))
            || self.released.iter().any(|c| !self.owners.contains_key(c))
            || self.simulator.maximum_quote_fills() > limits.maximum_pending_fills
        {
            return Err(Error::Conflict(
                "execution recovery population or clock differs".into(),
            ));
        }
        self.validate_source_checkpoint(cut)?;
        let mut aggregates: BTreeMap<Key, Aggregate> = BTreeMap::new();
        for order in orders {
            let b = &order.bracket;
            let owner = self
                .owners
                .get(&b.command_id)
                .ok_or_else(|| Error::Unready("checkpoint order owner missing".into()))?;
            let reservation = self
                .reservations
                .get(&b.command_id)
                .ok_or_else(|| Error::Unready("checkpoint reservation missing".into()))?;
            if *owner != run.scope(&b.account, b.instrument, &owner.strategy_instance)?
                || reservation.command_id != b.command_id
                || reservation.instrument != b.instrument
                || reservation.cash_minor == 0
                || self.released.contains(&b.command_id)
                    && (order.entry_filled != order.exit_filled
                        || (!order.entry_cancelled && order.entry_filled < b.quantity))
                || (order.entry_filled > 0) != self.cash.contains_key(&b.command_id)
            {
                return Err(Error::Conflict(
                    "execution ownership or release state differs".into(),
                ));
            }
            if let Some(cash) = self.cash.get(&b.command_id) {
                let fill = &fills[&b.command_id];
                cash.checkpoint(costs, fill)?;
                if fill.command_id != b.command_id
                    || fill.account != b.account
                    || fill.instrument != b.instrument
                    || fill.price_scale != b.price_scale
                    || cash.entry_quantity() != order.entry_filled
                    || cash.exit_quantity() != order.exit_filled
                    || fill.at_ns > cut.at_ns
                    || fill.sequence > cut.boundary_sequence
                {
                    return Err(Error::Conflict("execution cash and order disagree".into()));
                }
                let entry = match b.side {
                    arte_core::orders::Side::Long => arte_core::execution_events::Direction::Buy,
                    arte_core::orders::Side::Short => arte_core::execution_events::Direction::Sell,
                };
                if cash.entry_direction() != entry {
                    return Err(Error::Conflict(
                        "checkpoint cash direction differs from order".into(),
                    ));
                }
                let held = order.entry_filled - order.exit_filled;
                let a = aggregates.entry(Key::from_fill(fill)?).or_default();
                if held > 0 {
                    if a.direction.is_some_and(|d| d != entry) {
                        return Err(Error::Conflict("checkpoint opposing exposure".into()));
                    }
                    a.direction = Some(entry);
                }
                a.quantity = a
                    .quantity
                    .checked_add(held)
                    .ok_or_else(|| Error::Capacity("checkpoint held quantity".into()))?;
                a.cash = a
                    .cash
                    .checked_add(cash.trade_cash_atoms())
                    .ok_or_else(|| Error::Capacity("checkpoint trade cash".into()))?;
                a.at = a.at.max(fill.at_ns);
                a.sequence = a.sequence.max(fill.sequence);
            }
        }
        if aggregates.len() != self.projection.positions().count() {
            return Err(Error::Conflict("checkpoint projection population".into()));
        }
        for (key, a) in aggregates {
            let p = self
                .projection
                .position(&key)
                .ok_or_else(|| Error::Unready("checkpoint projection missing".into()))?;
            if p.quantity != a.quantity
                || p.trade_cash_atoms != a.cash
                || p.direction != a.direction
                || p.last_at_ns != a.at
                || p.last_sequence != a.sequence
                || p.price_scale != self.simulator.price_scale()
            {
                return Err(Error::Conflict(
                    "checkpoint projection and cash disagree".into(),
                ));
            }
        }
        Ok(())
    }
    fn validate_source_checkpoint(&self, cut: &Cut) -> Result<()> {
        let source = self
            .source
            .ok_or_else(|| Error::Unready("checkpoint source missing".into()))?;
        match (
            &self.last_source_quote,
            self.simulator.last_quote_identity(),
        ) {
            (None, None) => Ok(()),
            (Some((event, sequence, at)), Some((sim_sequence, sim_at, hash))) => {
                event.validate()?;
                let arte_core::events::Payload::Quote {
                    bid,
                    ask,
                    bid_size,
                    ask_size,
                    ..
                } = &event.payload
                else {
                    return Err(Error::Invalid("checkpoint source is not quote".into()));
                };
                let size = |v: arte_core::events::Decimal| -> Result<u64> {
                    u64::try_from(v.atoms_at_scale(0)?)
                        .map_err(|_| Error::Invalid("checkpoint quote size".into()))
                };
                let quote = Quote {
                    sequence: *sequence,
                    at_ns: *at,
                    bid: bid.atoms_at_scale(self.simulator.price_scale())?,
                    ask: ask.atoms_at_scale(self.simulator.price_scale())?,
                    bid_size: size(*bid_size)?,
                    ask_size: size(*ask_size)?,
                };
                if event.key.provider != source.provider
                    || event.key.instrument != source.instrument
                    || event.key.session != source.session
                    || *sequence != sim_sequence
                    || *at != sim_at
                    || *sequence > cut.boundary_sequence
                    || *at > cut.at_ns
                    || event.sip.ns > *at
                    || content_hash(&quote)? != hash
                {
                    return Err(Error::Conflict(
                        "checkpoint source quote differs from simulator".into(),
                    ));
                }
                Ok(())
            }
            _ => Err(Error::Conflict(
                "checkpoint source quote missing or extra".into(),
            )),
        }
    }
}
fn add_object(
    objects: &mut BTreeMap<String, Object>,
    object: Object,
    used: usize,
    maximum: usize,
) -> Result<usize> {
    if objects.contains_key(&object.id) {
        return Ok(used);
    }
    if object.payload.len() > maximum.saturating_sub(used) {
        return Err(Error::Capacity("execution checkpoint object budget".into()));
    }
    let next = used + object.payload.len();
    objects.insert(object.id.clone(), object);
    Ok(next)
}
