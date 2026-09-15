//! Deterministic quote-touch model. No broker credentials, network or clock access.
//! Displayed size is a modeled budget, not evidence of real queue priority.
use crate::{
    content_hash,
    orders::{Bracket, Side},
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const MODEL: &str = "quote-touch-shared-size-v1";
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
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Leg {
    Entry,
    Stop,
    Target,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Fill {
    pub model: String,
    pub command_id: String,
    pub account: String,
    pub instrument: u64,
    pub price_scale: u8,
    pub sequence: u64,
    pub at_ns: u64,
    pub leg: Leg,
    pub quantity: u64,
    pub price: i64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Position {
    pub bracket: Bracket,
    pub entry_filled: u64,
    pub exit_filled: u64,
    pub entry_cancelled: bool,
    pub stop_triggered: bool,
    ready_ns: u64,
    submitted_sequence: u64,
}
pub struct Simulator {
    instrument: u64,
    scale: u8,
    capacity: usize,
    participation_bps: u32,
    orders: Vec<Position>,
    last: Option<(u64, u64, String)>,
}
impl Simulator {
    pub fn new(
        instrument: u64,
        scale: u8,
        capacity: usize,
        participation_bps: u32,
    ) -> Result<Self> {
        if instrument == 0
            || scale > 9
            || capacity == 0
            || capacity > 100000
            || participation_bps == 0
            || participation_bps > 10000
        {
            return Err(Error::Invalid("invalid simulation bounds".into()));
        }
        Ok(Self {
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
            bracket,
            entry_filled: 0,
            exit_filled: 0,
            entry_cancelled: false,
            stop_triggered: false,
            ready_ns,
            submitted_sequence: self.last.as_ref().map_or(0, |v| v.0),
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
                    exit_price <= b.stop.unwrap()
                } else {
                    exit_price >= b.stop.unwrap()
                };
                let target = if long {
                    exit_price >= b.target.unwrap()
                } else {
                    exit_price <= b.target.unwrap()
                };
                order.stop_triggered |= stop;
                if order.stop_triggered || target {
                    order.entry_cancelled = true;
                    let remaining = if long { &mut bid_left } else { &mut ask_left };
                    let quantity = held.min(*remaining);
                    if quantity > 0 {
                        *remaining -= quantity;
                        order.exit_filled += quantity;
                        fills.push(Fill {
                            model: MODEL.into(),
                            command_id: b.command_id.clone(),
                            account: b.account.clone(),
                            instrument: b.instrument,
                            price_scale: b.price_scale,
                            sequence: quote.sequence,
                            at_ns: quote.at_ns,
                            leg: if order.stop_triggered {
                                Leg::Stop
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
                        model: MODEL.into(),
                        command_id: b.command_id.clone(),
                        account: b.account.clone(),
                        instrument: b.instrument,
                        price_scale: b.price_scale,
                        sequence: quote.sequence,
                        at_ns: quote.at_ns,
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
