//! Strategy 350 broken-high base selection from one causal structural snapshot.
//! This does not produce a BOS event or certify the structural detector.
//! Port reference: tests/reference/src/trading_runtime/early_squeeze_momentum.py.txt.
use crate::{
    content_hash,
    execution_interval::ExecutionInterval,
    strategy350_gap::{eligible, midpoint},
    strategy_targets::{valid_level, TargetLevel},
    v7_encounters::ActiveRole,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub maximum_snapshot_age_ns: u64,
    pub maximum_pivots: usize,
    pub maximum_levels: usize,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        self.execution_interval.validate()?;
        if self.execution_interval != ExecutionInterval::Events
            || self.maximum_snapshot_age_ns == 0
            || self.maximum_pivots == 0
            || self.maximum_pivots > 100_000
            || self.maximum_levels == 0
            || self.maximum_levels > 100_000
        {
            return Err(Error::Invalid("Strategy 350 BOS configuration".into()));
        }
        content_hash(&("arte.strategy-350-bos-config.v1", self))
    }
}

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Pivot {
    pub price: f64,
    pub pivot_at_ns: u64,
    pub confirmed_at_ns: u64,
    pub support: bool,
    pub active: bool,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct StructuralSnapshot {
    /// Effective time of the structural-detector row, not receipt time.
    pub effective_at_ns: u64,
    pub pivots: Vec<Pivot>,
    pub broken_high: Option<Pivot>,
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
pub enum Base {
    Support { pivot: Pivot, level: TargetLevel },
    ReclaimedResistance { level: TargetLevel },
}
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Selection {
    pub configuration_hash: String,
    pub structural_snapshot_hash: String,
    pub level_snapshot_hash: String,
    pub base: Base,
}
impl Selection {
    pub fn hash(&self) -> Result<String> {
        for hash in [
            &self.configuration_hash,
            &self.structural_snapshot_hash,
            &self.level_snapshot_hash,
        ] {
            if hash.len() != 64
                || !hash
                    .bytes()
                    .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
            {
                return Err(Error::Invalid("Strategy 350 BOS selection pin".into()));
            }
        }
        match &self.base {
            Base::Support { pivot, level } => {
                valid_level(level)?;
                if !valid_pivot(pivot, u64::MAX)
                    || !pivot.support
                    || !pivot.active
                    || level.geometry.role != ActiveRole::Support
                    || pivot.price < level.geometry.lower
                    || pivot.price > level.geometry.upper
                {
                    return Err(Error::Conflict("Strategy 350 BOS support base".into()));
                }
            }
            Base::ReclaimedResistance { level } => {
                valid_level(level)?;
                if !eligible(level) {
                    return Err(Error::Conflict("Strategy 350 BOS reclaimed base".into()));
                }
            }
        }
        content_hash(&("arte.strategy-350-bos-selection.v1", self))
    }
}
fn valid_pivot(pivot: &Pivot, at_ns: u64) -> bool {
    pivot.price.is_finite()
        && pivot.price > 0.
        && pivot.pivot_at_ns > 0
        && pivot.pivot_at_ns < pivot.confirmed_at_ns
        && pivot.confirmed_at_ns <= at_ns
}

/// A certified producer must supply the broken high and the structural row.
/// Missing broken-high evidence returns None; malformed or future evidence
/// rejects instead of creating a supported base from a later snapshot.
pub fn select(
    snapshot: &StructuralSnapshot,
    levels: &[TargetLevel],
    evaluated_at_ns: u64,
    config: &Config,
) -> Result<Option<Selection>> {
    let configuration_hash = config.hash()?;
    if snapshot.effective_at_ns == 0
        || snapshot.effective_at_ns > evaluated_at_ns
        || evaluated_at_ns - snapshot.effective_at_ns > config.maximum_snapshot_age_ns
        || snapshot.pivots.len() > config.maximum_pivots
        || levels.is_empty()
        || levels.len() > config.maximum_levels
    {
        return Err(Error::Unready(
            "Strategy 350 BOS snapshot unavailable".into(),
        ));
    }
    for pivot in &snapshot.pivots {
        if !valid_pivot(pivot, snapshot.effective_at_ns) {
            return Err(Error::Conflict("Strategy 350 BOS pivot clock".into()));
        }
    }
    let mut ids = BTreeSet::new();
    for level in levels {
        valid_level(level)?;
        midpoint(level)?;
        if !ids.insert(level.geometry.id.as_str())
            || level.geometry.confirmed_at_ns == 0
            || level.geometry.confirmed_at_ns > snapshot.effective_at_ns
        {
            return Err(Error::Conflict("Strategy 350 BOS level clock".into()));
        }
    }
    let Some(broken) = &snapshot.broken_high else {
        return Ok(None);
    };
    if !valid_pivot(broken, snapshot.effective_at_ns) || broken.support || !broken.active {
        return Err(Error::Conflict("Strategy 350 broken high".into()));
    }
    let structural_snapshot_hash =
        content_hash(&("arte.strategy-350-bos-structural-input.v1", snapshot))?;
    let level_snapshot_hash = content_hash(&("arte.strategy-350-bos-level-input.v1", levels))?;
    let wrap = |base: Base| Selection {
        configuration_hash: configuration_hash.clone(),
        structural_snapshot_hash: structural_snapshot_hash.clone(),
        level_snapshot_hash: level_snapshot_hash.clone(),
        base,
    };
    // The source deduplicates the structural detector's local and confirmed
    // pivot arrays by (pivot time, confirmation time, price), then visits lows
    // newest first. `active` and `support` are already typed here.
    let mut seen_pivots = BTreeSet::new();
    let mut lows: Vec<_> = snapshot
        .pivots
        .iter()
        .filter(|pivot| pivot.support && pivot.active && pivot.pivot_at_ns < broken.pivot_at_ns)
        .filter(|pivot| {
            seen_pivots.insert((
                pivot.pivot_at_ns,
                pivot.confirmed_at_ns,
                pivot.price.to_bits(),
            ))
        })
        .collect();
    lows.sort_by_key(|pivot| (pivot.pivot_at_ns, pivot.confirmed_at_ns));
    for pivot in lows.into_iter().rev() {
        let supported = levels
            .iter()
            .filter(|level| {
                level.geometry.role == ActiveRole::Support
                    && level.geometry.lower <= pivot.price
                    && pivot.price <= level.geometry.upper
            })
            .min_by(|a, b| {
                (a.geometry.upper - a.geometry.lower)
                    .total_cmp(&(b.geometry.upper - b.geometry.lower))
                    .then(a.geometry.id.cmp(&b.geometry.id))
            });
        if let Some(level) = supported {
            let selected = wrap(Base::Support {
                pivot: pivot.clone(),
                level: level.clone(),
            });
            selected.hash()?;
            return Ok(Some(selected));
        }
    }
    let reclaimed = levels
        .iter()
        .filter(|level| eligible(level))
        .map(|level| ((level.geometry.lower + level.geometry.upper) / 2., level))
        .filter(|(middle, _)| *middle < broken.price)
        .max_by(|a, b| {
            a.0.total_cmp(&b.0)
                .then(a.1.geometry.id.cmp(&b.1.geometry.id))
        });
    if let Some((_, level)) = reclaimed {
        let selected = wrap(Base::ReclaimedResistance {
            level: level.clone(),
        });
        selected.hash()?;
        return Ok(Some(selected));
    }
    Ok(None)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_encounters::Level;
    const S: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            maximum_snapshot_age_ns: S,
            maximum_pivots: 10,
            maximum_levels: 10,
        }
    }
    fn pivot(price: f64, at: u64, support: bool) -> Pivot {
        Pivot {
            price,
            pivot_at_ns: at,
            confirmed_at_ns: at + S,
            support,
            active: true,
        }
    }
    fn level(id: &str, price: f64, role: ActiveRole, width: f64) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: id.into(),
                price,
                lower: price - width,
                upper: price + width,
                role,
                confirmed_at_ns: S,
            },
            historical: false,
            transition_from: None,
            synthetic: false,
        }
    }
    fn snapshot() -> StructuralSnapshot {
        StructuralSnapshot {
            effective_at_ns: 5 * S,
            pivots: vec![
                pivot(9., S, true),
                pivot(10., 2 * S, true),
                pivot(10., 2 * S, true),
            ],
            broken_high: Some(pivot(14., 3 * S, false)),
        }
    }
    fn select_test(
        snapshot: &StructuralSnapshot,
        levels: &[TargetLevel],
    ) -> Result<Option<Selection>> {
        select(snapshot, levels, 5 * S, &config())
    }
    #[test]
    fn newest_supported_low_uses_narrowest_support_band() {
        let levels = [
            level("wide", 10., ActiveRole::Support, 1.),
            level("narrow", 10., ActiveRole::Support, 0.2),
            level("older", 9., ActiveRole::Support, 0.1),
            level("reclaimed", 12., ActiveRole::Resistance, 0.1),
        ];
        let result = select_test(&snapshot(), &levels).unwrap().unwrap();
        let mut changed = result.clone();
        changed.configuration_hash = "z".repeat(64);
        assert!(changed.hash().is_err());
        let Base::Support { pivot, level } = result.base else {
            panic!("support expected")
        };
        assert_eq!(pivot.pivot_at_ns, 2 * S);
        assert_eq!(level.geometry.id, "narrow");
    }
    #[test]
    fn reclaimed_resistance_is_fallback_only_and_clocks_fail_closed() {
        let levels = [
            level("lower", 11., ActiveRole::Resistance, 0.1),
            level("higher", 13., ActiveRole::Resistance, 0.1),
        ];
        let result = select_test(&snapshot(), &levels).unwrap().unwrap();
        let Base::ReclaimedResistance { level } = result.base else {
            panic!("reclaimed expected")
        };
        assert_eq!(level.geometry.id, "higher");
        let mut future = snapshot();
        future.effective_at_ns = 6 * S;
        assert!(select_test(&future, &levels).is_err());
        let mut missing = snapshot();
        missing.broken_high = None;
        assert!(select_test(&missing, &levels).unwrap().is_none());
        let mut wrong = config();
        wrong.execution_interval = ExecutionInterval::Fixed(100_000_000);
        assert!(select(&snapshot(), &levels, 5 * S, &wrong).is_err());
    }
}
