//! Deterministic quote-touch model. No broker credentials, network or clock access.
//! Displayed size is a modeled budget, not evidence of real queue priority.
use crate::execution_events::{Direction, Origin};
pub use crate::execution_events::{Fill, Leg};
use crate::{
    content_hash,
    orders::{Bracket, Side},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const MODEL: &str = "quote-touch-shared-size-v3";
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Quote {
    pub sequence: u64,
    pub at_ns: u64,
    pub bid: i64,
    pub ask: i64,
    pub bid_size: u64,
    pub ask_size: u64,
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn restored_partial_exit_matches_continuous_run() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        sim.quote(&quote(1, 99, 100, 3)).unwrap();
        sim.acknowledge_amendment("a", 1, 1, &Amendment::ExitPosition)
            .unwrap();
        let first = sim.quote(&quote(2, 101, 102, 1)).unwrap();
        assert_eq!(first[0].leg, Leg::Exit);
        assert_eq!(first[0].quantity, 1);
        let (hash, bytes) = sim.checkpoint(100000).unwrap();
        let mut restored = Simulator::restore(&bytes, &hash, 100000).unwrap();
        let q = quote(3, 102, 103, 10);
        let expected = sim.quote(&q).unwrap();
        let actual = restored.quote(&q).unwrap();
        assert_eq!(
            content_hash(&expected).unwrap(),
            content_hash(&actual).unwrap()
        );
        assert_eq!(actual[0].quantity, 2);
        assert_eq!(
            sim.checkpoint(100000).unwrap(),
            restored.checkpoint(100000).unwrap()
        );
        assert!(restored.quote(&q).unwrap().is_empty());
        assert_eq!(restored.positions()[0].entry_filled, 3);
    }
    #[test]
    fn checkpoint_rejects_corruption_wrong_model_and_invalid_quantities() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        let (hash, bytes) = sim.checkpoint(100000).unwrap();
        assert!(Simulator::restore(&bytes, &"0".repeat(64), 100000).is_err());
        assert!(Simulator::restore(&bytes, &hash, 1).is_err());
        let mut snapshot: Checkpoint = serde_json::from_slice(&bytes).unwrap();
        snapshot.orders[0].exit_filled = 1;
        let encoded = serde_json::to_vec(&snapshot).unwrap();
        assert!(Simulator::restore(&encoded, &content_hash(&snapshot).unwrap(), 100000).is_err());
        snapshot.orders[0].exit_filled = 0;
        snapshot.model = "unknown-model".into();
        let encoded = serde_json::to_vec(&snapshot).unwrap();
        assert!(Simulator::restore(&encoded, &content_hash(&snapshot).unwrap(), 100000).is_err());
    }
    #[test]
    fn cancellation_preserves_position_and_replacement_applies_next_quote() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        sim.quote(&quote(1, 99, 100, 2)).unwrap();
        sim.acknowledge_amendment("a", 1, 1, &Amendment::CancelEntry)
            .unwrap();
        sim.acknowledge_amendment("a", 1, 1, &Amendment::CancelEntry)
            .unwrap();
        let replacement = Amendment::ReplaceProtection {
            stop: 105,
            target: 115,
        };
        assert!(sim.acknowledge_amendment("a", 1, 1, &replacement).is_err());
        sim.acknowledge_amendment("a", 2, 1, &replacement).unwrap();
        assert_eq!(sim.positions()[0].bracket.stop, Some(90));
        assert_eq!(sim.positions()[0].active_stop, 105);
        let fills = sim.quote(&quote(2, 104, 105, 10)).unwrap();
        assert_eq!(fills.len(), 1);
        assert_eq!(fills[0].leg, Leg::Stop);
        assert_eq!(fills[0].quantity, 2);
        assert!(sim.acknowledge_amendment("a", 3, 2, &replacement).is_err());
    }
    #[test]
    fn invalid_amendments_do_not_change_protection_or_revision() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        sim.quote(&quote(1, 99, 100, 2)).unwrap();
        let invalid = Amendment::ReplaceProtection {
            stop: 105,
            target: 115,
        };
        assert!(sim.acknowledge_amendment("a", 1, 1, &invalid).is_err());
        assert!(sim
            .acknowledge_amendment("a", 1, 2, &Amendment::CancelEntry)
            .is_err());
        assert!(sim
            .acknowledge_amendment("a", 2, 1, &Amendment::CancelEntry)
            .is_err());
        assert_eq!(sim.positions()[0].active_stop, 90);
        sim.acknowledge_amendment("a", 1, 1, &Amendment::CancelEntry)
            .unwrap();
    }
    fn bracket(id: &str, side: Side) -> Bracket {
        Bracket {
            command_id: id.into(),
            account: id.into(),
            instrument: 1,
            side,
            quantity: 5,
            entry: 100,
            price_scale: 2,
            stop: Some(if side == Side::Long { 90 } else { 110 }),
            target: Some(if side == Side::Long { 110 } else { 90 }),
            tick: 1,
            deadline_ns: 100,
        }
    }
    fn quote(sequence: u64, bid: i64, ask: i64, size: u64) -> Quote {
        Quote {
            sequence,
            at_ns: sequence,
            bid,
            ask,
            bid_size: size,
            ask_size: size,
        }
    }
    #[test]
    fn accounts_share_liquidity_and_duplicate_quotes_do_not_refill() {
        let mut sim = Simulator::new(1, 2, 2, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        sim.submit(bracket("b", Side::Long), 0, 0).unwrap();
        let q = quote(1, 98, 99, 7);
        let fills = sim.quote(&q).unwrap();
        assert_eq!(
            fills.iter().map(|f| f.quantity).collect::<Vec<_>>(),
            vec![5, 2]
        );
        assert!(sim.quote(&q).unwrap().is_empty());
        let mut changed = q.clone();
        changed.ask_size += 1;
        assert!(sim.quote(&changed).is_err());
        assert_eq!(sim.positions()[1].entry_filled, 2);
    }
    #[test]
    fn partial_stop_latches_and_cancels_unfilled_entry() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        sim.submit(bracket("a", Side::Long), 0, 0).unwrap();
        sim.quote(&quote(1, 99, 100, 3)).unwrap();
        let fill = sim.quote(&quote(2, 85, 86, 1)).unwrap();
        assert_eq!(fill[0].leg, Leg::Stop);
        assert_eq!(fill[0].price, 85);
        let fill = sim.quote(&quote(3, 95, 96, 10)).unwrap();
        assert_eq!(fill.len(), 1);
        assert_eq!(fill[0].leg, Leg::Stop);
        assert_eq!(fill[0].quantity, 2);
        assert_eq!(sim.positions()[0].entry_filled, 3);
        assert_eq!(sim.positions()[0].exit_filled, 3);
    }
    #[test]
    fn latency_participation_and_short_target_are_explicit() {
        let mut sim = Simulator::new(1, 2, 1, 5000).unwrap();
        sim.submit(bracket("short", Side::Short), 0, 2).unwrap();
        assert!(sim.quote(&quote(1, 100, 101, 10)).unwrap().is_empty());
        assert_eq!(sim.quote(&quote(2, 100, 101, 6)).unwrap()[0].quantity, 3);
        let fills = sim.quote(&quote(3, 88, 89, 10)).unwrap();
        assert_eq!(fills[0].leg, Leg::Target);
        assert_eq!(fills[0].quantity, 3);
        assert!(sim.positions()[0].entry_cancelled);
    }
    #[test]
    fn expiry_prevents_entry_and_capacity_never_discards_orders() {
        let mut sim = Simulator::new(1, 2, 1, 10000).unwrap();
        let mut b = bracket("a", Side::Long);
        b.deadline_ns = 2;
        sim.submit(b, 0, 0).unwrap();
        assert!(sim.submit(bracket("b", Side::Long), 0, 0).is_err());
        assert!(sim.quote(&quote(2, 99, 100, 100)).unwrap().is_empty());
        assert!(sim.positions()[0].entry_cancelled);
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub bracket: Bracket,
    pub active_stop: i64,
    pub active_target: i64,
    pub entry_filled: u64,
    pub exit_filled: u64,
    pub entry_cancelled: bool,
    pub stop_triggered: bool,
    pub exit_requested: bool,
    ready_ns: u64,
    submitted_sequence: u64,
    amendment: Option<(u64, String)>,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub enum Amendment {
    CancelEntry,
    ExitPosition,
    ReplaceProtection { stop: i64, target: i64 },
}
pub struct Simulator {
    run_id: String,
    instrument: u64,
    scale: u8,
    capacity: usize,
    participation_bps: u32,
    orders: Vec<Position>,
    last: Option<(u64, u64, String)>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Checkpoint {
    run_id: String,
    model: String,
    instrument: u64,
    scale: u8,
    capacity: usize,
    participation_bps: u32,
    orders: Vec<Position>,
    last: Option<(u64, u64, String)>,
}
impl Simulator {
    #[cfg(test)]
    fn new(instrument: u64, scale: u8, capacity: usize, participation_bps: u32) -> Result<Self> {
        Self::new_scoped("test-run", instrument, scale, capacity, participation_bps)
    }
    pub fn new_scoped(
        run_id: &str,
        instrument: u64,
        scale: u8,
        capacity: usize,
        participation_bps: u32,
    ) -> Result<Self> {
        if run_id.is_empty()
            || run_id.len() > 256
            || instrument == 0
            || scale > 9
            || capacity == 0
            || capacity > 100000
            || participation_bps == 0
            || participation_bps > 10000
        {
            return Err(Error::Invalid("invalid simulation bounds".into()));
        }
        Ok(Self {
            run_id: run_id.into(),
            instrument,
            scale,
            capacity,
            participation_bps,
            orders: vec![],
            last: None,
        })
    }
    pub fn positions(&self) -> &[Position] {
        &self.orders
    }
    pub fn checkpoint(&self, maximum_bytes: usize) -> Result<(String, Vec<u8>)> {
        let snapshot = Checkpoint {
            run_id: self.run_id.clone(),
            model: MODEL.into(),
            instrument: self.instrument,
            scale: self.scale,
            capacity: self.capacity,
            participation_bps: self.participation_bps,
            orders: self.orders.clone(),
            last: self.last.clone(),
        };
        let bytes =
            serde_json::to_vec(&snapshot).map_err(|e| Error::Serialization(e.to_string()))?;
        if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 || bytes.len() > maximum_bytes {
            return Err(Error::Capacity("simulation checkpoint byte budget".into()));
        }
        Ok((content_hash(&snapshot)?, bytes))
    }
    pub fn restore(bytes: &[u8], expected_hash: &str, maximum_bytes: usize) -> Result<Self> {
        if maximum_bytes == 0 || maximum_bytes > 64 * 1024 * 1024 || bytes.len() > maximum_bytes {
            return Err(Error::Capacity("simulation checkpoint byte budget".into()));
        }
        let snapshot: Checkpoint =
            serde_json::from_slice(bytes).map_err(|e| Error::Serialization(e.to_string()))?;
        if snapshot.model != MODEL || content_hash(&snapshot)? != expected_hash {
            return Err(Error::Conflict(
                "simulation checkpoint model or hash mismatch".into(),
            ));
        }
        let mut sim = Self::new_scoped(
            &snapshot.run_id,
            snapshot.instrument,
            snapshot.scale,
            snapshot.capacity,
            snapshot.participation_bps,
        )?;
        if snapshot.orders.len() > snapshot.capacity {
            return Err(Error::Capacity("simulation checkpoint order budget".into()));
        }
        let valid_hash = |s: &str| {
            s.len() == 64
                && s.bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        };
        if snapshot
            .last
            .as_ref()
            .is_some_and(|(seq, _, hash)| *seq == 0 || !valid_hash(hash))
        {
            return Err(Error::Invalid("invalid simulation quote checkpoint".into()));
        }
        let mut commands = std::collections::BTreeSet::new();
        for order in &snapshot.orders {
            let b = &order.bracket;
            b.validate(
                0,
                false,
                None,
                &crate::orders::RiskPolicy {
                    band_buffer_ticks: 3,
                    max_band_age_ns: 1,
                },
            )?;
            if b.instrument != snapshot.instrument
                || b.price_scale != snapshot.scale
                || !commands.insert(&b.command_id)
                || order.exit_filled > order.entry_filled
                || order.entry_filled > b.quantity
                || order.active_stop <= 0
                || order.active_target <= 0
                || order.active_stop % b.tick != 0
                || order.active_target % b.tick != 0
                || match b.side {
                    Side::Long => order.active_stop >= order.active_target,
                    Side::Short => order.active_target >= order.active_stop,
                }
                || order.submitted_sequence > snapshot.last.as_ref().map_or(0, |v| v.0)
                || (order.exit_requested || order.stop_triggered) && !order.entry_cancelled
                || order
                    .amendment
                    .as_ref()
                    .is_some_and(|(rev, hash)| *rev == 0 || !valid_hash(hash))
            {
                return Err(Error::Invalid(
                    "inconsistent simulation position checkpoint".into(),
                ));
            }
        }
        sim.orders = snapshot.orders;
        sim.last = snapshot.last;
        Ok(sim)
    }
    /// Apply a modeled broker acknowledgment after the current quote. The caller
    /// schedules acknowledgment latency and shared OMS authorization. No retroactive
    /// fills occur; old protection governed the quote already consumed.
    pub fn acknowledge_amendment(
        &mut self,
        command: &str,
        revision: u64,
        at_ns: u64,
        amendment: &Amendment,
    ) -> Result<()> {
        if self.last.as_ref().map(|(_, at, _)| *at) != Some(at_ns) || revision == 0 {
            return Err(Error::Invalid(
                "amendment must follow the current simulation clock".into(),
            ));
        }
        let hash = content_hash(&(command, revision, at_ns, amendment))?;
        let order = self
            .orders
            .iter_mut()
            .find(|o| o.bracket.command_id == command)
            .ok_or_else(|| Error::Invalid("unknown simulation command".into()))?;
        if let Some((last, previous)) = &order.amendment {
            if revision == *last {
                return if hash == *previous {
                    Ok(())
                } else {
                    Err(Error::Conflict("simulation amendment changed".into()))
                };
            }
            if last.checked_add(1) != Some(revision) {
                return Err(Error::Invalid(
                    "simulation amendment revision gap or rewind".into(),
                ));
            }
        } else if revision != 1 {
            return Err(Error::Invalid(
                "first amendment revision must be one".into(),
            ));
        }
        match *amendment {
            Amendment::CancelEntry => order.entry_cancelled = true,
            Amendment::ExitPosition => {
                order.entry_cancelled = true;
                order.exit_requested = true;
            }
            Amendment::ReplaceProtection { stop, target } => {
                if order.entry_filled == order.exit_filled || order.stop_triggered {
                    return Err(Error::Unready("no replaceable protected position".into()));
                }
                if stop <= 0
                    || target <= 0
                    || stop % order.bracket.tick != 0
                    || target % order.bracket.tick != 0
                    || match order.bracket.side {
                        Side::Long => stop >= target,
                        Side::Short => target >= stop,
                    }
                {
                    return Err(Error::Invalid(
                        "invalid replacement protection geometry".into(),
                    ));
                }
                if !order.entry_cancelled
                    && order.entry_filled < order.bracket.quantity
                    && match order.bracket.side {
                        Side::Long => stop >= order.bracket.entry || target <= order.bracket.entry,
                        Side::Short => target >= order.bracket.entry || stop <= order.bracket.entry,
                    }
                {
                    return Err(Error::Unready(
                        "replacement incompatible with unfilled entry; cancel entry first".into(),
                    ));
                }
                order.active_stop = stop;
                order.active_target = target;
            }
        }
        order.amendment = Some((revision, hash));
        Ok(())
    }
    /// Upstream uses the shared risk/OMS authorization before modeled submission.
    /// Latency is explicitly simulated, never inferred historical receive latency.
    pub fn submit(&mut self, bracket: Bracket, now_ns: u64, latency_ns: u64) -> Result<()> {
        if bracket.instrument != self.instrument
            || bracket.price_scale != self.scale
            || self.last.as_ref().is_some_and(|(_, at, _)| now_ns < *at)
        {
            return Err(Error::Invalid(
                "simulation submission scope or clock mismatch".into(),
            ));
        }
        // Geometry only here. Session/LULD authorization belongs to the shared OMS.
        bracket.validate(
            now_ns,
            false,
            None,
            &crate::orders::RiskPolicy {
                band_buffer_ticks: 3,
                max_band_age_ns: 1,
            },
        )?;
        if let Some(existing) = self
            .orders
            .iter()
            .find(|o| o.bracket.command_id == bracket.command_id)
        {
            return if existing.bracket == bracket {
                Ok(())
            } else {
                Err(Error::Conflict("simulation command changed".into()))
            };
        }
        if self.orders.len() == self.capacity {
            return Err(Error::Capacity("simulation order capacity".into()));
        }
        let ready_ns = now_ns
            .checked_add(latency_ns)
            .ok_or_else(|| Error::Invalid("simulated latency overflow".into()))?;
        self.orders.push(Position {
            active_stop: bracket.stop.unwrap(),
            active_target: bracket.target.unwrap(),
            bracket,
            entry_filled: 0,
            exit_filled: 0,
            entry_cancelled: false,
            stop_triggered: false,
            exit_requested: false,
            ready_ns,
            submitted_sequence: self.last.as_ref().map_or(0, |v| v.0),
            amendment: None,
        });
        Ok(())
    }
    /// One instrument lane; the displayed liquidity budget is shared across accounts
    /// and orders in submission order. Exact duplicate quotes cannot create fills.
    pub fn quote(&mut self, quote: &Quote) -> Result<Vec<Fill>> {
        if quote.sequence == 0 || quote.bid <= 0 || quote.ask < quote.bid {
            return Err(Error::Invalid("invalid simulation quote".into()));
        }
        let hash = content_hash(quote)?;
        if let Some((sequence, at, previous)) = &self.last {
            if quote.sequence == *sequence {
                return if hash == *previous {
                    Ok(vec![])
                } else {
                    Err(Error::Conflict("simulation quote identity changed".into()))
                };
            }
            if quote.sequence < *sequence || quote.at_ns < *at {
                return Err(Error::Invalid("simulation quote clock rewind".into()));
            }
        }
        let budget = |size| (u128::from(size) * u128::from(self.participation_bps) / 10000) as u64;
        let (mut bid_left, mut ask_left) = (budget(quote.bid_size), budget(quote.ask_size));
        let mut fills = vec![];
        for order in &mut self.orders {
            if quote.sequence <= order.submitted_sequence || quote.at_ns < order.ready_ns {
                continue;
            }
            let b = &order.bracket;
            let long = b.side == Side::Long;
            let exit_price = if long { quote.bid } else { quote.ask };
            let held = order.entry_filled - order.exit_filled;
            if held > 0 {
                let stop = if long {
                    exit_price <= order.active_stop
                } else {
                    exit_price >= order.active_stop
                };
                let target = if long {
                    exit_price >= order.active_target
                } else {
                    exit_price <= order.active_target
                };
                order.stop_triggered |= stop;
                if order.stop_triggered || target || order.exit_requested {
                    order.entry_cancelled = true;
                    let remaining = if long { &mut bid_left } else { &mut ask_left };
                    let quantity = held.min(*remaining);
                    if quantity > 0 {
                        *remaining -= quantity;
                        order.exit_filled += quantity;
                        fills.push(Fill {
                            schema_version: 1,
                            origin: Origin::Simulated {
                                run_id: self.run_id.clone(),
                                model: MODEL.into(),
                            },
                            direction: if long {
                                Direction::Sell
                            } else {
                                Direction::Buy
                            },
                            command_id: b.command_id.clone(),
                            account: b.account.clone(),
                            instrument: b.instrument,
                            price_scale: b.price_scale,
                            sequence: quote.sequence,
                            at_ns: quote.at_ns,
                            executed_at_ns: Some(quote.at_ns),
                            leg: if order.stop_triggered {
                                Leg::Stop
                            } else if order.exit_requested {
                                Leg::Exit
                            } else {
                                Leg::Target
                            },
                            quantity,
                            price: exit_price,
                        });
                    }
                }
            }
            if quote.at_ns >= b.deadline_ns {
                order.entry_cancelled = true;
            }
            let entry_price = if long { quote.ask } else { quote.bid };
            let marketable = if long {
                entry_price <= b.entry
            } else {
                entry_price >= b.entry
            };
            if !order.entry_cancelled && marketable {
                let remaining = if long { &mut ask_left } else { &mut bid_left };
                let quantity = (b.quantity - order.entry_filled).min(*remaining);
                if quantity > 0 {
                    *remaining -= quantity;
                    order.entry_filled += quantity;
                    fills.push(Fill {
                        schema_version: 1,
                        origin: Origin::Simulated {
                            run_id: self.run_id.clone(),
                            model: MODEL.into(),
                        },
                        direction: if long {
                            Direction::Buy
                        } else {
                            Direction::Sell
                        },
                        command_id: b.command_id.clone(),
                        account: b.account.clone(),
                        instrument: b.instrument,
                        price_scale: b.price_scale,
                        sequence: quote.sequence,
                        at_ns: quote.at_ns,
                        executed_at_ns: Some(quote.at_ns),
                        leg: Leg::Entry,
                        quantity,
                        price: entry_price,
                    });
                }
            }
        }
        self.last = Some((quote.sequence, quote.at_ns, hash));
        Ok(fills)
    }
}
