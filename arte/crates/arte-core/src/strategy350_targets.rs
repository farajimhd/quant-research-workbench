//! Strategy 350 momentum target progression from the frozen v27 source.
//! This is account strategy state, not broker target or order authority.
use crate::{content_hash, execution_interval::ExecutionInterval, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;
pub mod checkpoint;

const FAST_WINDOW_NS: u64 = 3_000_000_000;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub maximum_distinct_levels: usize,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        if self.execution_interval != ExecutionInterval::Events
            || self.maximum_distinct_levels == 0
            || self.maximum_distinct_levels > 100_000
        {
            return Err(Error::Invalid(
                "Strategy 350 target progression configuration".into(),
            ));
        }
        content_hash(&("arte.strategy-350-target-progression.v1", self))
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BreakEvent {
    pub level_id: String,
    pub opened_at_ns: u64,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Upgrade {
    pub previous_multiplier: u32,
    pub multiplier: u32,
    pub confirmed_level_ids: Vec<String>,
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Progress {
    configuration_hash: String,
    session_wide: bool,
    seen: BTreeSet<String>,
    last_three: Vec<String>,
    fast_pending: Vec<BreakEvent>,
    last_opened_at_ns: u64,
    multiplier: u32,
}
impl Progress {
    pub fn new(config: &Config, session_wide: bool) -> Result<Self> {
        Ok(Self {
            configuration_hash: config.hash()?,
            session_wide,
            seen: BTreeSet::new(),
            last_three: Vec::new(),
            fast_pending: Vec::new(),
            last_opened_at_ns: 0,
            multiplier: 5,
        })
    }
    pub fn multiplier(&self) -> u32 {
        self.multiplier
    }
    pub fn distinct_count(&self) -> usize {
        self.seen.len()
    }
    pub fn configuration_hash(&self) -> &str {
        &self.configuration_hash
    }
    /// One validated causal batch. Preflight prevents a rejected batch from
    /// advancing any strategy state, including its duplicate frontier.
    pub fn observe(
        &mut self,
        events: &[BreakEvent],
        known_at_ns: u64,
        config: &Config,
    ) -> Result<Vec<Upgrade>> {
        if self.configuration_hash != config.hash()?
            || known_at_ns == 0
            || known_at_ns < self.last_opened_at_ns
        {
            return Err(Error::Conflict(
                "Strategy 350 target progression identity".into(),
            ));
        }
        let mut added = BTreeSet::new();
        let mut last = self.last_opened_at_ns;
        for event in events {
            if event.level_id.is_empty()
                || event.level_id.len() > 128
                || event.opened_at_ns == 0
                || event.opened_at_ns > known_at_ns
                || event.opened_at_ns < last
            {
                return Err(Error::Invalid("Strategy 350 resistance break order".into()));
            }
            last = event.opened_at_ns;
            if !self.seen.contains(&event.level_id) {
                added.insert(&event.level_id);
            }
        }
        if self.seen.len() + added.len() > config.maximum_distinct_levels {
            return Err(Error::Capacity(
                "Strategy 350 distinct resistance budget".into(),
            ));
        }
        let mut upgrades = Vec::new();
        for event in events {
            self.last_opened_at_ns = event.opened_at_ns;
            if !self.seen.insert(event.level_id.clone()) {
                continue;
            }
            if self.session_wide {
                self.last_three.push(event.level_id.clone());
                if self.last_three.len() > 3 {
                    self.last_three.remove(0);
                }
                if self.seen.len().is_multiple_of(3) {
                    let previous = self.multiplier;
                    self.multiplier = match previous {
                        5 => 8,
                        8 => 10,
                        10 => 12,
                        _ => previous.checked_add(1).ok_or_else(|| {
                            Error::Capacity("Strategy 350 target multiplier".into())
                        })?,
                    };
                    upgrades.push(Upgrade {
                        previous_multiplier: previous,
                        multiplier: self.multiplier,
                        confirmed_level_ids: self.last_three.clone(),
                    });
                }
            } else {
                self.fast_pending
                    .retain(|pending| event.opened_at_ns - pending.opened_at_ns < FAST_WINDOW_NS);
                self.fast_pending.push(event.clone());
                if self.fast_pending.len() >= 3 && self.multiplier < 10 {
                    let previous = self.multiplier;
                    self.multiplier = if previous == 5 { 8 } else { 10 };
                    upgrades.push(Upgrade {
                        previous_multiplier: previous,
                        multiplier: self.multiplier,
                        confirmed_level_ids: self
                            .fast_pending
                            .iter()
                            .map(|item| item.level_id.clone())
                            .collect(),
                    });
                    self.fast_pending.clear();
                }
            }
        }
        Ok(upgrades)
    }
}

/// The v27 source rounds to the nearest tick, ties upward. Frozen inter-level
/// average is required; the entry-to-first-level distance is never substituted.
pub fn target_price(
    entry_basis: f64,
    frozen_average_gap: Option<f64>,
    multiplier: u32,
    tick: f64,
) -> Result<f64> {
    let gap = frozen_average_gap
        .ok_or_else(|| Error::Unready("Strategy 350 frozen average gap missing".into()))?;
    if !entry_basis.is_finite()
        || entry_basis <= 0.
        || !gap.is_finite()
        || gap <= 0.
        || !tick.is_finite()
        || tick <= 0.
        || !matches!(multiplier, 2 | 5 | 8 | 10 | 12..)
    {
        return Err(Error::Invalid(
            "Strategy 350 momentum target operands".into(),
        ));
    }
    let ticks = (entry_basis + f64::from(multiplier) * gap) / tick;
    if !ticks.is_finite() || ticks <= 0. || ticks > 1e15 {
        return Err(Error::Capacity(
            "Strategy 350 momentum target tick range".into(),
        ));
    }
    let target = ((ticks + 0.5 + 1e-9).floor() * tick * 1e10).round() / 1e10;
    if !target.is_finite() || target <= entry_basis {
        return Err(Error::Invalid(
            "Strategy 350 momentum target geometry".into(),
        ));
    }
    Ok(target)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn config(maximum_distinct_levels: usize) -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            maximum_distinct_levels,
        }
    }
    fn event(id: &str, second: u64) -> BreakEvent {
        BreakEvent {
            level_id: id.into(),
            opened_at_ns: second * 1_000_000_000,
        }
    }
    #[test]
    fn fast_triples_are_distinct_disjoint_and_causal() {
        let cfg = config(10);
        let mut state = Progress::new(&cfg, false).unwrap();
        let first = [event("a", 1), event("b", 2), event("c", 3)];
        let upgrades = state.observe(&first, 3_000_000_000, &cfg).unwrap();
        assert_eq!(upgrades[0].multiplier, 8);
        assert_eq!(upgrades[0].confirmed_level_ids, vec!["a", "b", "c"]);
        assert!(state
            .observe(&[event("c", 4)], 4_000_000_000, &cfg)
            .unwrap()
            .is_empty());
        let second = [event("d", 5), event("e", 6), event("f", 7)];
        assert_eq!(
            state.observe(&second, 7_000_000_000, &cfg).unwrap()[0].multiplier,
            10
        );
        assert!(state
            .observe(
                &[event("g", 8), event("h", 9), event("i", 10)],
                10_000_000_000,
                &cfg
            )
            .unwrap()
            .is_empty());
        assert_eq!(state.distinct_count(), 9);
    }
    #[test]
    fn session_triples_continue_while_flat_and_reject_invalid_batch_atomically() {
        let cfg = config(6);
        let mut state = Progress::new(&cfg, true).unwrap();
        assert_eq!(
            state
                .observe(
                    &[event("a", 1), event("b", 2), event("c", 3)],
                    3_000_000_000,
                    &cfg
                )
                .unwrap()[0]
                .multiplier,
            8
        );
        assert!(state
            .observe(
                &[event("d", 4), event("e", 5), event("f", 7)],
                6_000_000_000,
                &cfg
            )
            .is_err());
        assert_eq!(state.distinct_count(), 3);
        assert!(state
            .observe(&[event("d", 4), event("e", 5)], 5_000_000_000, &cfg)
            .unwrap()
            .is_empty());
        let second = state
            .observe(&[event("f", 6)], 6_000_000_000, &cfg)
            .unwrap();
        assert_eq!(second[0].multiplier, 10);
        assert_eq!(second[0].confirmed_level_ids, vec!["d", "e", "f"]);
        assert_eq!(state.distinct_count(), 6);
        assert!(state
            .observe(&[event("g", 7)], 7_000_000_000, &cfg)
            .is_err());
        assert_eq!(state.distinct_count(), 6);
        assert!(state.observe(&[], 5_000_000_000, &cfg).is_err());
    }
    #[test]
    fn target_rounding_requires_a_frozen_interlevel_gap() {
        assert_eq!(target_price(10., Some(0.25), 5, 0.01).unwrap(), 11.25);
        assert_eq!(target_price(10., Some(0.005), 5, 0.01).unwrap(), 10.03);
        assert!(target_price(10., None, 5, 0.01).is_err());
        assert!(target_price(10., Some(0.25), 3, 0.01).is_err());
    }
}
