//! Account/position-owned resistance-add confirmations for the V7 setup candidate.
//! A proposal consumes its tranche index; it is not a fill or broker authorization.
use crate::market::Bar;
use crate::strategy_setup::upper_wick_fraction;
use crate::strategy_targets::{valid_level, TargetLevel};
use crate::v7_encounters::ActiveRole;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;

#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub tick: f64,
    pub buffer_ticks: f64,
    pub buffer_bps: f64,
    pub maximum_chase_bps: f64,
    pub maximum_upper_wick_fraction: f64,
    pub price_only: bool,
    pub tranche_count: usize,
    pub maximum_pending_levels: usize,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct State {
    pub frontier: TargetLevel,
    pub tranches_requested: usize,
    pending: BTreeMap<String, TargetLevel>,
    last_bar_end_ns: u64,
}
#[derive(Debug, Clone, Serialize)]
pub struct Gates {
    pub at_ns: u64,
    pub detector_fresh: bool,
    pub regular_allowed: bool,
    pub no_pending_acquisition: bool,
    pub permission: bool,
    pub tradable: bool,
    pub macd_ready: bool,
    pub no_pending_failed_attempt: bool,
    pub encounter_clear: bool,
    pub range_breakout_allowed: bool,
}
pub struct Frame<'a> {
    pub bar: &'a Bar,
    pub previous: Option<&'a Bar>,
    /// Prior levels with their encounter-owned frozen geometry already resolved.
    pub acquisition_levels: &'a [TargetLevel],
    pub bid: f64,
    pub ask: f64,
    pub vwap: Option<f64>,
    pub stop: f64,
    pub target: f64,
    pub gates: &'a Gates,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Proposal {
    pub confirmed_at_ns: u64,
    pub tranche_index: usize,
    pub tranche_count: usize,
    pub broken: TargetLevel,
    pub threshold: f64,
    pub stop: f64,
    pub target: f64,
    pub maximum_buy_price: f64,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Evaluation {
    pub proposal: Option<Proposal>,
    pub consumed_levels: usize,
    pub pending_levels: usize,
    pub upper_wick_fraction: f64,
    pub blocked_gates: Vec<String>,
}
fn valid_bar(b: &Bar) -> bool {
    b.end_ns.checked_sub(b.start_ns) == Some(1_000_000_000)
        && [b.open, b.close, b.high, b.low]
            .into_iter()
            .all(|v| v.is_finite() && v > 0.)
        && b.high >= b.open.max(b.close)
        && b.low <= b.open.min(b.close)
}
fn threshold(l: &TargetLevel, p: &Policy) -> f64 {
    l.geometry.price + (p.tick * p.buffer_ticks).max(l.geometry.price * p.buffer_bps / 10000.)
}
impl State {
    pub fn new(mut entry_level: TargetLevel, entry_price: f64, entry_at_ns: u64) -> Result<Self> {
        valid_level(&entry_level)?;
        if !entry_price.is_finite()
            || entry_price <= 0.
            || entry_level.geometry.confirmed_at_ns > entry_at_ns
        {
            return Err(Error::Invalid("invalid add entry frontier".into()));
        }
        // Source resets add progression to the actual entry close, not a future overhead level.
        entry_level.geometry.price = entry_price;
        entry_level.geometry.lower = entry_price;
        entry_level.geometry.upper = entry_price;
        Ok(Self {
            frontier: entry_level,
            tranches_requested: 1,
            pending: BTreeMap::new(),
            last_bar_end_ns: entry_at_ns,
        })
    }
    pub fn evaluate(&mut self, f: &Frame<'_>, p: &Policy) -> Result<Evaluation> {
        let b = f.bar;
        if !valid_bar(b)
            || f.previous
                .is_some_and(|v| !valid_bar(v) || v.end_ns > b.start_ns)
            || [p.tick, f.bid, f.ask, f.stop, f.target]
                .into_iter()
                .any(|v| !v.is_finite() || v <= 0.)
            || f.bid > f.ask
            || [
                p.buffer_ticks,
                p.buffer_bps,
                p.maximum_chase_bps,
                p.maximum_upper_wick_fraction,
            ]
            .into_iter()
            .any(|v| !v.is_finite() || v < 0.)
            || p.maximum_upper_wick_fraction > 1.
            || p.tranche_count == 0
            || p.maximum_pending_levels == 0
            || f.gates.at_ns > b.end_ns
            || b.end_ns < self.last_bar_end_ns
        {
            return Err(Error::Invalid("invalid add observation or policy".into()));
        }
        for l in f.acquisition_levels {
            valid_level(l)?;
            if l.geometry.confirmed_at_ns > b.start_ns || !threshold(l, p).is_finite() {
                return Err(Error::Invalid(
                    "future or invalid acquisition threshold".into(),
                ));
            }
        }
        let wick = upper_wick_fraction(b)?;
        let mut result = Evaluation {
            proposal: None,
            consumed_levels: 0,
            pending_levels: self.pending.len(),
            upper_wick_fraction: wick,
            blocked_gates: vec![],
        };
        if b.end_ns == self.last_bar_end_ns {
            result.blocked_gates.push("duplicate_completed_bar".into());
            return Ok(result);
        }
        let mut next = self.clone();
        let contiguous = f.previous.is_some_and(|v| v.end_ns == b.start_ns);
        if !contiguous {
            next.pending.clear();
        }
        if let Some(previous) = f.previous.filter(|_| contiguous) {
            for l in f.acquisition_levels {
                if matches!(
                    l.geometry.role,
                    ActiveRole::Resistance | ActiveRole::Transition
                ) && previous.close <= threshold(l, p)
                    && threshold(l, p) < b.close
                    && l.geometry.price > next.frontier.geometry.price
                {
                    if !next.pending.contains_key(&l.geometry.id)
                        && next.pending.len() >= p.maximum_pending_levels
                    {
                        return Err(Error::Capacity(
                            "add confirmation level limit; no truncation".into(),
                        ));
                    }
                    next.pending.insert(l.geometry.id.clone(), l.clone());
                }
            }
        }
        next.pending.retain(|_, l| {
            b.close > threshold(l, p) && l.geometry.price > next.frontier.geometry.price
        });
        let g = f.gates;
        let gates = [
            ("admission_clock", g.at_ns == b.end_ns),
            ("detector", g.detector_fresh),
            ("contiguous", contiguous),
            ("regular_session", g.regular_allowed),
            ("pending_acquisition", g.no_pending_acquisition),
            ("permission", g.permission),
            ("tradability", g.tradable),
            ("macd", g.macd_ready),
            (
                "vwap",
                f.vwap
                    .is_some_and(|v| v.is_finite() && v > 0. && b.close > v),
            ),
            ("non_red_close", b.close >= b.open),
            ("pending_failed_attempt", g.no_pending_failed_attempt),
            ("encounters", g.encounter_clear),
            ("tranche_limit", next.tranches_requested < p.tranche_count),
            ("range_breakout", g.range_breakout_allowed),
            ("upper_wick", wick <= p.maximum_upper_wick_fraction),
        ];
        result.blocked_gates = gates
            .into_iter()
            .filter(|(_, ready)| !*ready)
            .map(|(name, _)| name.into())
            .collect();
        let bid = if p.price_only { b.close } else { f.bid };
        let ask = if p.price_only { b.close } else { f.ask };
        let ceiling = (f.ask * (1. + p.maximum_chase_bps / 10000.)).min(f.target - p.tick);
        if !(f.stop < bid && bid <= ask && ask <= ceiling) {
            result.blocked_gates.push("execution_envelope".into());
        }
        if result.blocked_gates.is_empty() {
            if let Some(broken) = next.pending.values().min_by(|a, b| {
                a.geometry
                    .price
                    .total_cmp(&b.geometry.price)
                    .then(a.geometry.id.cmp(&b.geometry.id))
            }) {
                result.proposal = Some(Proposal {
                    confirmed_at_ns: b.end_ns,
                    tranche_index: next.tranches_requested,
                    tranche_count: p.tranche_count,
                    broken: broken.clone(),
                    threshold: threshold(broken, p),
                    stop: f.stop,
                    target: f.target,
                    maximum_buy_price: ceiling,
                });
                next.frontier = broken.clone();
                next.tranches_requested += 1;
            }
        }
        if b.close >= b.open {
            result.consumed_levels = next.pending.len();
            next.pending.clear();
        }
        next.last_bar_end_ns = b.end_ns;
        result.pending_levels = next.pending.len();
        *self = next;
        Ok(result)
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_encounters::Level;
    const S: u64 = 1_000_000_000;
    fn bar(t: u64, open: f64, close: f64) -> Bar {
        Bar {
            start_ns: t * S,
            end_ns: (t + 1) * S,
            open,
            close,
            high: open.max(close),
            low: open.min(close),
            volume: 1.,
            notional: close,
            trades: 1,
        }
    }
    fn level(id: &str, p: f64) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: id.into(),
                price: p,
                lower: p - 0.1,
                upper: p + 0.1,
                role: ActiveRole::Transition,
                confirmed_at_ns: S,
            },
            historical: false,
            transition_from: Some(ActiveRole::Support),
            synthetic: false,
        }
    }
    fn policy() -> Policy {
        Policy {
            tick: 0.01,
            buffer_ticks: 1.,
            buffer_bps: 10.,
            maximum_chase_bps: 20.,
            maximum_upper_wick_fraction: 0.5,
            price_only: true,
            tranche_count: 3,
            maximum_pending_levels: 8,
        }
    }
    fn gates(at: u64) -> Gates {
        Gates {
            at_ns: at,
            detector_fresh: true,
            regular_allowed: true,
            no_pending_acquisition: true,
            permission: true,
            tradable: true,
            macd_ready: true,
            no_pending_failed_attempt: true,
            encounter_clear: true,
            range_breakout_allowed: true,
        }
    }
    fn run(
        s: &mut State,
        b: &Bar,
        prev: Option<&Bar>,
        levels: &[TargetLevel],
        g: &Gates,
        p: &Policy,
    ) -> Result<Evaluation> {
        s.evaluate(
            &Frame {
                bar: b,
                previous: prev,
                acquisition_levels: levels,
                bid: b.close - 0.01,
                ask: b.close + 0.01,
                vwap: Some(9.),
                stop: 9.5,
                target: 15.,
                gates: g,
            },
            p,
        )
    }
    #[test]
    fn lowest_crossed_transition_and_one_tranche_per_close() {
        let mut s = State::new(level("entry", 12.), 10., S).unwrap();
        let b = bar(2, 10., 11.);
        let result = run(
            &mut s,
            &b,
            Some(&bar(1, 10., 10.)),
            &[level("higher", 10.8), level("lower", 10.5)],
            &gates(b.end_ns),
            &policy(),
        )
        .unwrap();
        let proposal = result.proposal.unwrap();
        assert_eq!(proposal.broken.geometry.id, "lower");
        assert_eq!(proposal.tranche_index, 1);
        assert_eq!(s.tranches_requested, 2);
        assert_eq!(result.consumed_levels, 2);
        assert!(run(
            &mut s,
            &b,
            Some(&bar(1, 10., 10.)),
            &[],
            &gates(b.end_ns),
            &policy()
        )
        .unwrap()
        .proposal
        .is_none());
    }
    #[test]
    fn failed_green_gate_is_consumed_and_not_replayed() {
        let mut s = State::new(level("entry", 10.), 10., S).unwrap();
        let b = bar(2, 10., 11.);
        let mut g = gates(b.end_ns);
        g.tradable = false;
        assert_eq!(
            run(
                &mut s,
                &b,
                Some(&bar(1, 10., 10.)),
                &[level("r", 10.5)],
                &g,
                &policy()
            )
            .unwrap()
            .consumed_levels,
            1
        );
        let later = bar(3, 11., 11.2);
        assert!(run(
            &mut s,
            &later,
            Some(&b),
            &[level("r", 10.5)],
            &gates(later.end_ns),
            &policy()
        )
        .unwrap()
        .proposal
        .is_none());
    }
    #[test]
    fn red_cross_waits_for_non_red_close_but_gap_discards() {
        for gap in [false, true] {
            let mut s = State::new(level("entry", 10.), 10., S).unwrap();
            let red = bar(2, 11.5, 11.);
            assert_eq!(
                run(
                    &mut s,
                    &red,
                    Some(&bar(1, 10., 10.)),
                    &[level("r", 10.5)],
                    &gates(red.end_ns),
                    &policy()
                )
                .unwrap()
                .pending_levels,
                1
            );
            let green = bar(if gap { 4 } else { 3 }, 11., 11.1);
            assert_eq!(
                run(
                    &mut s,
                    &green,
                    Some(&red),
                    &[],
                    &gates(green.end_ns),
                    &policy()
                )
                .unwrap()
                .proposal
                .is_some(),
                !gap
            );
        }
    }
    #[test]
    fn capacity_failure_preserves_state() {
        let mut s = State::new(level("entry", 10.), 10., S).unwrap();
        let before = crate::content_hash(&s).unwrap();
        let b = bar(2, 10., 11.);
        let mut p = policy();
        p.maximum_pending_levels = 1;
        assert!(run(
            &mut s,
            &b,
            Some(&bar(1, 10., 10.)),
            &[level("r", 10.5), level("r2", 10.6)],
            &gates(b.end_ns),
            &p
        )
        .is_err());
        assert_eq!(before, crate::content_hash(&s).unwrap());
    }
}
