//! Strategy 350 activation-time resistance gaps. This is frozen geometry,
//! not a live entry signal or authority to submit an order.
//! Port reference: tests/reference/src/trading_runtime/early_squeeze_momentum.py.txt.
use crate::{
    content_hash,
    strategy_targets::{valid_level, TargetLevel},
    v7_encounters::ActiveRole,
    Error, Result,
};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct FrozenGap {
    pub activated_at_ns: u64,
    pub reference_price: f64,
    pub ceiling: f64,
    pub levels: Vec<TargetLevel>,
    pub gaps: Vec<f64>,
    pub average: Option<f64>,
}
impl FrozenGap {
    pub fn hash(&self) -> Result<String> {
        self.validate()?;
        content_hash(&("arte.strategy-350-frozen-gap.v1", self))
    }
    pub fn validate(&self) -> Result<()> {
        if self.activated_at_ns == 0
            || !self.reference_price.is_finite()
            || self.reference_price <= 0.
            || !self.ceiling.is_finite()
            || self.ceiling != self.reference_price * 4.
            || self.levels.len() > 100_000
            || self.gaps.len() != self.levels.len().saturating_sub(1)
        {
            return Err(Error::Invalid("Strategy 350 frozen gap shape".into()));
        }
        let mut previous: Option<(f64, &str)> = None;
        let mut seen = BTreeSet::new();
        for level in &self.levels {
            let middle = midpoint(level)?;
            if !seen.insert(level.geometry.id.as_str())
                || !eligible(level)
                || level.geometry.confirmed_at_ns == 0
                || level.geometry.confirmed_at_ns > self.activated_at_ns
                || middle <= self.reference_price
                || middle > self.ceiling
                || previous.is_some_and(|(prior, id)| {
                    middle < prior || (middle == prior && level.geometry.id.as_str() <= id)
                })
            {
                return Err(Error::Conflict("Strategy 350 frozen gap level".into()));
            }
            previous = Some((middle, &level.geometry.id));
        }
        let mut sum = 0.;
        for (pair, gap) in self.levels.windows(2).zip(&self.gaps) {
            let expected = midpoint(&pair[1])? - midpoint(&pair[0])?;
            if !gap.is_finite() || *gap != expected {
                return Err(Error::Conflict("Strategy 350 frozen gap distance".into()));
            }
            sum += gap;
        }
        if !sum.is_finite()
            || self.average
                != if self.gaps.is_empty() {
                    None
                } else {
                    Some(sum / self.gaps.len() as f64)
                }
        {
            return Err(Error::Conflict("Strategy 350 frozen gap average".into()));
        }
        Ok(())
    }
}

fn midpoint(level: &TargetLevel) -> Result<f64> {
    valid_level(level)?;
    let value = (level.geometry.lower + level.geometry.upper) / 2.;
    if !value.is_finite() || value <= 0. {
        return Err(Error::Invalid("Strategy 350 gap midpoint".into()));
    }
    Ok(value)
}
fn eligible(level: &TargetLevel) -> bool {
    level.geometry.role == ActiveRole::Resistance
        || (level.geometry.role == ActiveRole::Transition
            && level.transition_from == Some(ActiveRole::Resistance))
}

/// Only levels confirmed at activation can enter the frozen set. It is the
/// caller's responsibility to supply one certified, point-in-time V7 view.
/// The entry-to-first-level distance is deliberately not an inter-level gap.
pub fn freeze(
    levels: &[TargetLevel],
    reference_price: f64,
    activated_at_ns: u64,
    maximum_levels: usize,
) -> Result<FrozenGap> {
    if activated_at_ns == 0
        || !reference_price.is_finite()
        || reference_price <= 0.
        || maximum_levels == 0
        || maximum_levels > 100_000
        || levels.len() > 100_000
    {
        return Err(Error::Invalid("Strategy 350 gap activation".into()));
    }
    let ceiling = reference_price * 4.;
    if !ceiling.is_finite() {
        return Err(Error::Invalid("Strategy 350 gap ceiling".into()));
    }
    let mut seen = BTreeSet::new();
    let mut selected = Vec::new();
    for level in levels {
        let middle = midpoint(level)?;
        if !seen.insert(level.geometry.id.as_str())
            || level.geometry.confirmed_at_ns == 0
            || level.geometry.confirmed_at_ns > activated_at_ns
        {
            return Err(Error::Conflict(
                "Strategy 350 gap level identity or clock".into(),
            ));
        }
        if eligible(level) && reference_price < middle && middle <= ceiling {
            if selected.len() == maximum_levels {
                return Err(Error::Capacity("Strategy 350 gap level budget".into()));
            }
            selected.push((middle, level.clone()));
        }
    }
    selected.sort_unstable_by(|a, b| {
        a.0.total_cmp(&b.0)
            .then(a.1.geometry.id.cmp(&b.1.geometry.id))
    });
    let mut gaps = Vec::with_capacity(selected.len().saturating_sub(1));
    for pair in selected.windows(2) {
        gaps.push(pair[1].0 - pair[0].0);
    }
    let average = if gaps.is_empty() {
        None
    } else {
        let sum: f64 = gaps.iter().sum();
        if !sum.is_finite() {
            return Err(Error::Capacity("Strategy 350 gap sum".into()));
        }
        Some(sum / gaps.len() as f64)
    };
    let result = FrozenGap {
        activated_at_ns,
        reference_price,
        ceiling,
        levels: selected.into_iter().map(|(_, level)| level).collect(),
        gaps,
        average,
    };
    result.hash()?;
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::strategy_encounters::Level;
    const S: u64 = 1_000_000_000;
    fn level(
        id: &str,
        middle: f64,
        role: ActiveRole,
        transition_from: Option<ActiveRole>,
    ) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: id.into(),
                price: middle,
                lower: middle - 0.1,
                upper: middle + 0.1,
                role,
                confirmed_at_ns: S,
            },
            historical: false,
            transition_from,
            synthetic: false,
        }
    }
    #[test]
    fn freezes_only_inter_resistance_gaps_at_activation() {
        let levels = vec![
            level("far", 40., ActiveRole::Resistance, None),
            level("support", 20., ActiveRole::Support, None),
            level(
                "second",
                25.,
                ActiveRole::Transition,
                Some(ActiveRole::Resistance),
            ),
            level("first", 15., ActiveRole::Resistance, None),
            level("above", 41., ActiveRole::Resistance, None),
        ];
        let frozen = freeze(&levels, 10., 2 * S, 5).unwrap();
        assert_eq!(
            frozen
                .levels
                .iter()
                .map(|v| v.geometry.id.as_str())
                .collect::<Vec<_>>(),
            vec!["first", "second", "far"]
        );
        assert_eq!(frozen.gaps, vec![10., 15.]);
        assert_eq!(frozen.average, Some(12.5));
        let mut changed = frozen.clone();
        changed.gaps[0] = 9.;
        assert!(changed.hash().is_err());
        assert_eq!(
            freeze(&levels, 10., 2 * S, 5).unwrap().hash().unwrap(),
            frozen.hash().unwrap()
        );
        assert!(freeze(&levels, 10., 2 * S, 2).is_err());
    }
    #[test]
    fn rejects_future_or_duplicate_geometry_and_missing_gap() {
        let one = level("one", 15., ActiveRole::Resistance, None);
        assert_eq!(
            freeze(std::slice::from_ref(&one), 10., 2 * S, 1)
                .unwrap()
                .average,
            None
        );
        let mut future = one.clone();
        future.geometry.confirmed_at_ns = 3 * S;
        assert!(freeze(&[future], 10., 2 * S, 1).is_err());
        assert!(freeze(&[one.clone(), one], 10., 2 * S, 2).is_err());
        assert!(freeze(&[], f64::INFINITY, 2 * S, 1).is_err());
    }
}
