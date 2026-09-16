//! Strategy-local projection of the frozen directional-change swing algorithm.
//! Major visual swings are independent and are not evaluated by this component.
use crate::{content_hash, market::Bar, strategy_targets::Swing, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, VecDeque};
pub const VERSION: &str = "arte-local-directional-swings-v1";
#[cfg(test)]
mod tests;
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub reversal_bps: f64,
    pub volatility_multiple: f64,
    pub volatility_cap_multiple: f64,
    /// The source detector passes candle sequence, not elapsed seconds.
    pub lifetime_bars: u64,
    pub maximum_levels: usize,
}
impl Config {
    fn validate(&self) -> Result<()> {
        if [
            self.reversal_bps,
            self.volatility_multiple,
            self.volatility_cap_multiple,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v <= 0.)
            || self.lifetime_bars == 0
            || self.lifetime_bars > 1_000_000
            || self.maximum_levels == 0
            || self.maximum_levels > 100_000
        {
            return Err(Error::Invalid("local swing configuration".into()));
        }
        Ok(())
    }
}
#[derive(Clone, Serialize, Deserialize)]
struct Extreme {
    price: f64,
    at_ns: u64,
    distance: f64,
}
#[derive(Clone, Copy, PartialEq, Serialize, Deserialize)]
enum Phase {
    Active,
    AwaitingRetest,
    RetestContact,
}
#[derive(Clone, Serialize, Deserialize)]
struct Level {
    swing: Swing,
    phase: Phase,
    beyond: u8,
    last_test: u64,
    break_at: u64,
    contact_at: u64,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct Snapshot {
    pub at_ns: u64,
    /// Pre-candle active levels plus confirmations on this candle, matching the
    /// source strategy's local_swings + confirmed_swings input order.
    pub swings: Vec<Swing>,
    pub gap_reset: bool,
}
#[derive(Clone, Serialize, Deserialize)]
pub struct State {
    config: Config,
    instrument: u64,
    session: u32,
    generation: u64,
    sequence: u64,
    next_id: u64,
    previous: Option<Bar>,
    ranges: VecDeque<f64>,
    direction: i8,
    high: Option<Extreme>,
    low: Option<Extreme>,
    levels: BTreeMap<u64, Level>,
    snapshot: Option<Snapshot>,
    failed: bool,
}
impl State {
    pub fn new(instrument: u64, session: u32, config: Config) -> Result<Self> {
        config.validate()?;
        if instrument == 0 || session == 0 {
            return Err(Error::Invalid("local swing scope".into()));
        }
        Ok(Self {
            config,
            instrument,
            session,
            generation: 0,
            sequence: 0,
            next_id: 0,
            previous: None,
            ranges: VecDeque::new(),
            direction: 0,
            high: None,
            low: None,
            levels: BTreeMap::new(),
            snapshot: None,
            failed: false,
        })
    }
    pub fn configuration_hash(&self) -> Result<String> {
        content_hash(&(VERSION, self.instrument, self.session, &self.config))
    }
    pub fn snapshot(&self) -> Result<Option<&Snapshot>> {
        if self.failed {
            return Err(Error::Unready("local swings require recovery".into()));
        }
        Ok(self.snapshot.as_ref())
    }
    /// Only completed one-second candles. Gaps reset state; no empty-interval
    /// certification is inferred. Scope is immutable for this session owner.
    pub fn observe(&mut self, bar: &Bar) -> Result<()> {
        self.snapshot()?;
        let result = self.update(bar);
        if result.is_err() {
            self.failed = true;
        }
        result
    }
    fn update(&mut self, bar: &Bar) -> Result<()> {
        if bar.start_ns.checked_add(1_000_000_000) != Some(bar.end_ns)
            || [bar.open, bar.high, bar.low, bar.close]
                .iter()
                .any(|v| !v.is_finite() || *v <= 0.)
            || bar.low > bar.open.min(bar.close)
            || bar.high < bar.open.max(bar.close)
            || self
                .previous
                .as_ref()
                .is_some_and(|p| bar.start_ns < p.end_ns)
        {
            return Err(Error::Invalid("local swing candle or order".into()));
        }
        let gap = self
            .previous
            .as_ref()
            .is_some_and(|p| bar.start_ns > p.end_ns);
        if gap {
            *self = Self::new(self.instrument, self.session, self.config.clone())?;
        }
        if self.generation == 0 {
            self.generation = bar.end_ns;
        }
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("local swing sequence".into()))?;
        let mut swings: Vec<_> = self
            .levels
            .values()
            .filter(|l| l.phase == Phase::Active)
            .map(|l| l.swing.clone())
            .collect();
        let quote_tick = tick(bar.close);
        let t = self.sequence;
        self.levels
            .retain(|_, l| t - l.last_test < self.config.lifetime_bars);
        for l in self.levels.values_mut() {
            let contact = bar.low <= l.swing.upper && bar.high >= l.swing.lower;
            let beyond = if l.swing.support {
                bar.close < l.swing.lower - quote_tick
            } else {
                bar.close > l.swing.upper + quote_tick
            };
            let rejected = if l.swing.support {
                bar.close > l.swing.upper + quote_tick
            } else {
                bar.close < l.swing.lower - quote_tick
            };
            if l.phase == Phase::Active {
                l.beyond = if beyond {
                    l.beyond.saturating_add(1)
                } else {
                    0
                };
                if l.beyond >= 2 {
                    l.phase = Phase::AwaitingRetest;
                    l.break_at = t;
                    l.last_test = t;
                } else if contact {
                    l.last_test = t;
                }
            } else if l.phase == Phase::AwaitingRetest && contact && t > l.break_at {
                l.phase = Phase::RetestContact;
                l.contact_at = t;
                l.last_test = t;
            } else if l.phase == Phase::RetestContact && beyond && t > l.contact_at {
                l.swing.support = !l.swing.support;
                l.phase = Phase::Active;
                l.beyond = 0;
                l.last_test = t;
                l.swing.confirmed_at_ns = bar.end_ns;
            } else if rejected {
                l.phase = Phase::Active;
                l.beyond = 0;
                l.last_test = t;
            }
            l.swing.active = l.phase == Phase::Active;
        }
        let mut ranges: Vec<_> = self.ranges.iter().copied().collect();
        ranges.sort_by(f64::total_cmp);
        let volatility = if ranges.is_empty() {
            0.
        } else if ranges.len() % 2 == 1 {
            ranges[ranges.len() / 2]
        } else {
            (ranges[ranges.len() / 2 - 1] + ranges[ranges.len() / 2]) / 2.
        };
        let extreme = |price: f64| -> Result<Extreme> {
            let floor = (2. * tick(price)).max(price * self.config.reversal_bps / 10000.);
            let distance = floor.max(
                (volatility * self.config.volatility_multiple)
                    .min(floor * self.config.volatility_cap_multiple),
            );
            if !distance.is_finite() {
                return Err(Error::Invalid("local swing threshold overflow".into()));
            }
            Ok(Extreme {
                price,
                at_ns: bar.end_ns,
                distance,
            })
        };
        if self.high.as_ref().is_none_or(|h| bar.high > h.price) {
            self.high = Some(extreme(bar.high)?);
        }
        if self.low.as_ref().is_none_or(|l| bar.low < l.price) {
            self.low = Some(extreme(bar.low)?);
        }
        let h = self.high.as_ref().unwrap().clone();
        let l = self.low.as_ref().unwrap().clone();
        let high = self.direction >= 0
            && h.at_ns < bar.end_ns
            && self.previous.as_ref().is_some_and(|p| bar.close < p.close)
            && bar.close <= h.price - h.distance;
        let low = self.direction <= 0
            && l.at_ns < bar.end_ns
            && self.previous.as_ref().is_some_and(|p| bar.close > p.close)
            && bar.close >= l.price + l.distance;
        if high || low {
            let next_high = extreme(bar.high)?;
            let next_low = extreme(bar.low)?;
            if high != low {
                self.found(if high { h } else { l }, !high, bar.end_ns)?;
                self.direction = if high { -1 } else { 1 };
            }
            self.high = Some(next_high);
            self.low = Some(next_low);
        }
        swings.extend(
            self.levels
                .values()
                .filter(|l| l.swing.confirmed_at_ns == bar.end_ns)
                .map(|l| l.swing.clone()),
        );
        let range = self.previous.as_ref().map_or(bar.high - bar.low, |p| {
            (bar.high - bar.low)
                .max((bar.high - p.close).abs())
                .max((bar.low - p.close).abs())
        });
        if self.ranges.len() == 30 {
            self.ranges.pop_front();
        }
        self.ranges.push_back(range);
        self.previous = Some(bar.clone());
        self.snapshot = Some(Snapshot {
            at_ns: bar.end_ns,
            swings,
            gap_reset: gap,
        });
        Ok(())
    }
    fn found(&mut self, extreme: Extreme, support: bool, at_ns: u64) -> Result<()> {
        let width = tick(extreme.price).max(extreme.price * 0.0002);
        let best = self
            .levels
            .iter()
            .filter(|(_, l)| {
                l.phase == Phase::Active
                    && l.swing.support == support
                    && (l.swing.price - extreme.price).abs()
                        <= width.min((l.swing.upper - l.swing.lower) / 2.)
            })
            .min_by(|(ak, a), (bk, b)| {
                (a.swing.price - extreme.price)
                    .abs()
                    .total_cmp(&(b.swing.price - extreme.price).abs())
                    .then(ak.cmp(bk))
            })
            .map(|(k, _)| *k);
        if let Some(key) = best {
            self.levels.get_mut(&key).unwrap().last_test = self.sequence;
            return Ok(());
        }
        if self.levels.len() >= self.config.maximum_levels {
            return Err(Error::Capacity("local swing levels; no truncation".into()));
        }
        self.next_id = self
            .next_id
            .checked_add(1)
            .ok_or_else(|| Error::Capacity("local swing identity".into()))?;
        self.levels.insert(
            self.next_id,
            Level {
                swing: Swing {
                    id: content_hash(&(
                        VERSION,
                        self.instrument,
                        self.session,
                        self.generation,
                        self.next_id,
                    ))?,
                    lower: extreme.price - width,
                    price: extreme.price,
                    upper: extreme.price + width,
                    pivot_at_ns: extreme.at_ns,
                    confirmed_at_ns: at_ns,
                    support,
                    active: true,
                },
                phase: Phase::Active,
                beyond: 0,
                last_test: self.sequence,
                break_at: 0,
                contact_at: 0,
            },
        );
        Ok(())
    }
}
fn tick(price: f64) -> f64 {
    if price < 1. {
        0.0001
    } else {
        0.01
    }
}
