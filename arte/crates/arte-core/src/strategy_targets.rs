//! Target and stop geometry ported from the frozen historical-HOD strategy.
//! These are strategy candidates, not broker-authorized prices or orders.
use crate::strategy_encounters::Level;
use crate::v7_encounters::ActiveRole;
use crate::{Error, Result};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TargetLevel {
    pub geometry: Level,
    pub historical: bool,
    pub transition_from: Option<ActiveRole>,
    pub synthetic: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Policy {
    pub distance_fraction: f64,
    pub offset_ticks: f64,
    pub stop_buffer_bps: f64,
    pub all_origins: bool,
    pub encounter_transitions: bool,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Placement {
    AboveUpperBand,
    BelowLowerBand,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Target {
    pub price: f64,
    pub level: TargetLevel,
    pub trigger: TargetLevel,
    pub reference: f64,
    pub placement: Placement,
    pub synthetic: bool,
}
/// Validated causal view. Future-confirmed levels are excluded before selection.
#[derive(Debug, Clone)]
pub struct CausalLevels {
    levels: Vec<TargetLevel>,
    at_ns: u64,
}
pub(crate) fn valid_level(level: &TargetLevel) -> Result<()> {
    let g = &level.geometry;
    if g.id.is_empty()
        || [g.lower, g.price, g.upper]
            .iter()
            .any(|p| !p.is_finite() || *p <= 0.)
        || g.lower > g.price
        || g.price > g.upper
    {
        return Err(Error::Invalid("invalid strategy target level".into()));
    }
    Ok(())
}
fn validate(policy: &Policy, tick: f64, price: f64, minimum: f64) -> Result<()> {
    if !tick.is_finite()
        || tick <= 0.
        || !price.is_finite()
        || price <= 0.
        || !minimum.is_finite()
        || minimum < 0.
        || [
            policy.distance_fraction,
            policy.offset_ticks,
            policy.stop_buffer_bps,
        ]
        .iter()
        .any(|v| !v.is_finite() || *v < 0.)
        || policy.stop_buffer_bps >= 10_000.
    {
        return Err(Error::Invalid("invalid target/stop parameters".into()));
    }
    Ok(())
}
fn snap(price: f64, tick: f64, up: bool) -> Result<f64> {
    let units = price / tick;
    if !units.is_finite() || units.abs() > 1e15 {
        return Err(Error::Invalid(
            "unrepresentable strategy tick geometry".into(),
        ));
    }
    let result = if up {
        (units - 1e-9).ceil() * tick
    } else {
        (units + 1e-9).floor() * tick
    };
    if !result.is_finite() || result <= 0. {
        return Err(Error::Invalid("nonpositive strategy price".into()));
    }
    Ok(result)
}
pub(crate) fn resistance(level: &TargetLevel, policy: &Policy) -> bool {
    level.geometry.role == ActiveRole::Resistance
        || (policy.all_origins
            && level.geometry.role == ActiveRole::Transition
            && (level.transition_from == Some(ActiveRole::Resistance)
                || policy.encounter_transitions))
}
pub(crate) fn eligible(level: &TargetLevel, policy: &Policy) -> bool {
    policy.all_origins || level.historical
}
pub fn stop_below(value: f64, policy: &Policy, tick: f64) -> Result<f64> {
    validate(policy, tick, value, 0.)?;
    snap(
        value - tick.max(value * policy.stop_buffer_bps / 10000.),
        tick,
        false,
    )
}
impl CausalLevels {
    pub fn new(levels: &[TargetLevel], at_ns: u64) -> Result<Self> {
        let mut selected = Vec::new();
        for level in levels {
            valid_level(level)?;
            if level.geometry.confirmed_at_ns <= at_ns {
                selected.push(level.clone());
            }
        }
        Ok(Self {
            levels: selected,
            at_ns,
        })
    }
    fn next_resistance(&self, price: f64, policy: &Policy) -> Option<&TargetLevel> {
        self.levels
            .iter()
            .filter(|r| resistance(r, policy) && eligible(r, policy) && r.geometry.lower > price)
            .min_by(|a, b| {
                a.geometry
                    .lower
                    .total_cmp(&b.geometry.lower)
                    .then(a.geometry.price.total_cmp(&b.geometry.price))
            })
    }
    pub fn select(
        &self,
        broken: &TargetLevel,
        price: f64,
        minimum: f64,
        policy: &Policy,
        tick: f64,
    ) -> Result<Option<Target>> {
        validate(policy, tick, price, minimum)?;
        valid_level(broken)?;
        if broken.geometry.confirmed_at_ns > self.at_ns {
            return Err(Error::Unready(
                "trigger level is not causally available".into(),
            ));
        }
        let reference = broken.geometry.price * (1. + policy.distance_fraction);
        if !reference.is_finite() {
            return Err(Error::Invalid("target reference overflow".into()));
        }
        let mut choices = Vec::new();
        for level in &self.levels {
            if !resistance(level, policy)
                || !eligible(level, policy)
                || level.geometry.lower <= broken.geometry.upper
            {
                continue;
            }
            let above = level.geometry.price < reference;
            let placement = if above {
                level.geometry.upper + policy.offset_ticks * tick
            } else {
                level.geometry.lower - policy.offset_ticks * tick
            };
            let target = snap(placement, tick, above)?;
            if target > price {
                choices.push((level, target, above));
            }
        }
        let best = choices.into_iter().min_by(|(a, _, _), (b, _, _)| {
            (a.geometry.price - reference)
                .abs()
                .total_cmp(&(b.geometry.price - reference).abs())
                .then(a.geometry.price.total_cmp(&b.geometry.price))
        });
        let Some((level, target, above)) = best else {
            return Ok(None);
        };
        if target <= minimum + tick / 2. {
            return Ok(None);
        }
        Ok(Some(Target {
            price: target,
            level: level.clone(),
            trigger: broken.clone(),
            reference,
            placement: if above {
                Placement::AboveUpperBand
            } else {
                Placement::BelowLowerBand
            },
            synthetic: false,
        }))
    }
    pub fn available(
        &self,
        price: f64,
        minimum: f64,
        synthetic_base: Option<f64>,
        reference_price: Option<f64>,
        policy: &Policy,
        tick: f64,
    ) -> Result<Option<Target>> {
        validate(policy, tick, price, minimum)?;
        if synthetic_base
            .into_iter()
            .chain(reference_price)
            .any(|p| !p.is_finite() || p <= 0.)
        {
            return Err(Error::Invalid("invalid target anchor".into()));
        }
        let anchor =
            if let Some(anchor) = self.next_resistance(reference_price.unwrap_or(price), policy) {
                if let Some(selected) = self.select(anchor, price, minimum, policy, tick)? {
                    return Ok(Some(selected));
                }
                // A real target that cannot improve the current target blocks ladder extension.
                if self.select(anchor, price, 0., policy, tick)?.is_some() {
                    return Ok(None);
                }
                anchor.clone()
            } else {
                let mut value = snap(synthetic_base.unwrap_or(price) * 1.10, tick, true)?;
                let mut steps = 0;
                while value <= price {
                    if steps >= 512 {
                        return Err(Error::Capacity(
                            "synthetic target ladder exceeds bounded steps".into(),
                        ));
                    }
                    let next = snap(value * 1.10, tick, true)?;
                    if next <= value {
                        return Err(Error::Invalid("synthetic ladder did not advance".into()));
                    }
                    value = next;
                    steps += 1;
                }
                self.synthetic_level(value)
            };
        let level_price = snap(anchor.geometry.upper * 1.10, tick, true)?;
        let target = snap(level_price - policy.offset_ticks * tick, tick, false)?;
        if target <= price.max(minimum + tick / 2.) {
            return Ok(None);
        }
        Ok(Some(Target {
            price: target,
            level: self.synthetic_level(level_price),
            trigger: anchor,
            reference: level_price,
            placement: Placement::BelowLowerBand,
            synthetic: true,
        }))
    }
    fn synthetic_level(&self, price: f64) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: format!("synthetic:{price:.10}"),
                price,
                lower: price,
                upper: price,
                role: ActiveRole::Resistance,
                confirmed_at_ns: self.at_ns,
            },
            historical: false,
            transition_from: None,
            synthetic: true,
        }
    }
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Swing {
    pub id: String,
    pub lower: f64,
    pub price: f64,
    pub upper: f64,
    pub pivot_at_ns: u64,
    pub confirmed_at_ns: u64,
    pub support: bool,
    pub active: bool,
}
#[derive(Debug, Clone, Default)]
pub struct SwingSelection {
    pub closest: bool,
    pub price_only: bool,
    pub confirmed_after_ns: u64,
    pub pivot_not_before_ns: u64,
    pub maximum_age_ns: Option<u64>,
}
pub fn initial_swing<'a>(
    swings: &'a [Swing],
    boundary_lower: f64,
    now_ns: u64,
    selection: &SwingSelection,
) -> Result<Option<&'a Swing>> {
    if !boundary_lower.is_finite() || boundary_lower <= 0. {
        return Err(Error::Invalid("invalid swing entry boundary".into()));
    }
    Ok(swings
        .iter()
        .filter(|s| {
            s.support
                && s.active
                && [s.lower, s.price, s.upper].iter().all(|v| v.is_finite())
                && 0. < s.lower
                && s.lower <= s.price
                && s.price <= s.upper
                && (if selection.price_only {
                    s.price
                } else {
                    s.upper
                }) < boundary_lower
                && 0 < s.pivot_at_ns
                && s.pivot_at_ns <= s.confirmed_at_ns
                && s.confirmed_at_ns <= now_ns
                && s.confirmed_at_ns > selection.confirmed_after_ns
                && s.pivot_at_ns >= selection.pivot_not_before_ns
                && selection
                    .maximum_age_ns
                    .is_none_or(|age| now_ns - s.confirmed_at_ns <= age)
        })
        .max_by(|a, b| {
            if selection.closest {
                a.lower
                    .total_cmp(&b.lower)
                    .then(a.pivot_at_ns.cmp(&b.pivot_at_ns))
                    .then(a.confirmed_at_ns.cmp(&b.confirmed_at_ns))
            } else {
                a.pivot_at_ns
                    .cmp(&b.pivot_at_ns)
                    .then(a.confirmed_at_ns.cmp(&b.confirmed_at_ns))
                    .then(a.price.total_cmp(&b.price))
            }
        }))
}
#[cfg(test)]
mod tests {
    use super::*;
    fn level(price: f64) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: format!("l-{price}"),
                price,
                lower: price - 0.05,
                upper: price + 0.05,
                role: ActiveRole::Resistance,
                confirmed_at_ns: 1,
            },
            historical: true,
            transition_from: None,
            synthetic: false,
        }
    }
    fn policy() -> Policy {
        Policy {
            distance_fraction: 0.05,
            offset_ticks: 1.,
            stop_buffer_bps: 10.,
            all_origins: true,
            encounter_transitions: true,
        }
    }
    #[test]
    fn real_target_priority_prevents_synthetic_skip() {
        let levels = CausalLevels::new(&[level(10.), level(10.5)], 2).unwrap();
        let target = levels
            .available(9., 0., None, None, &policy(), 0.01)
            .unwrap()
            .unwrap();
        assert!(!target.synthetic);
        assert!((target.price - 10.44).abs() < 1e-9);
        assert!(levels
            .available(9., 11., None, None, &policy(), 0.01)
            .unwrap()
            .is_none());
    }
    #[test]
    fn exhausted_ladder_uses_two_explicit_steps() {
        let levels = CausalLevels::new(&[], 2).unwrap();
        let target = levels
            .available(10., 0., None, None, &policy(), 0.01)
            .unwrap()
            .unwrap();
        assert!(target.synthetic);
        assert!((target.trigger.geometry.price - 11.).abs() < 1e-9);
        assert!((target.price - 12.09).abs() < 1e-9);
    }
    #[test]
    fn future_levels_and_nonresistance_transitions_are_excluded() {
        let mut future = level(10.5);
        future.geometry.confirmed_at_ns = 3;
        let levels = CausalLevels::new(&[level(10.), future], 2).unwrap();
        assert!(levels
            .select(&level(10.), 9., 0., &policy(), 0.01)
            .unwrap()
            .is_none());
        let mut transition = level(10.5);
        transition.geometry.role = ActiveRole::Transition;
        transition.transition_from = Some(ActiveRole::Support);
        let levels = CausalLevels::new(&[transition], 2).unwrap();
        let mut policy = policy();
        policy.encounter_transitions = false;
        assert!(levels
            .select(&level(10.), 9., 0., &policy, 0.01)
            .unwrap()
            .is_none());
    }
    #[test]
    fn stop_is_below_reference_and_tick_aligned() {
        let value = stop_below(10., &policy(), 0.01).unwrap();
        assert!((value - 9.99).abs() < 1e-9);
        assert!(stop_below(0.001, &policy(), 0.01).is_err());
    }
    #[test]
    fn swing_must_be_confirmed_before_now_and_below_boundary() {
        let base = Swing {
            id: "valid".into(),
            lower: 9.,
            price: 9.1,
            upper: 9.2,
            pivot_at_ns: 1,
            confirmed_at_ns: 2,
            support: true,
            active: true,
        };
        let mut future = base.clone();
        future.id = "future".into();
        future.confirmed_at_ns = 5;
        let swings = [base, future];
        assert_eq!(
            initial_swing(&swings, 10., 3, &SwingSelection::default())
                .unwrap()
                .unwrap()
                .id,
            "valid"
        );
        assert!(initial_swing(&swings, 9., 3, &SwingSelection::default())
            .unwrap()
            .is_none());
    }
}
