//! Strategy 350 structural and adaptive initial-stop selection.
//! Port reference: tests/reference/src/trading_runtime/early_squeeze_momentum.py.txt.
//! This emits a candidate, not an authorized bracket or broker order.
use crate::{
    content_hash,
    execution_interval::ExecutionInterval,
    strategy350_bos::{Pivot, StructuralSnapshot},
    strategy350_noise::{Distance, State as NoiseState},
    strategy_targets::{valid_level, TargetLevel},
    v7_encounters::ActiveRole,
    Error, Result,
};
use serde::{Deserialize, Serialize};

const BPS: i128 = 10_000;

#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    pub execution_interval: ExecutionInterval,
    pub maximum_snapshot_age_ns: u64,
    pub maximum_noise_age_ns: u64,
    pub maximum_swing_age_ns: u64,
    pub maximum_levels: usize,
    pub fallback_percent: u8,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        if self.execution_interval != ExecutionInterval::Events
            || self.maximum_snapshot_age_ns == 0
            || self.maximum_noise_age_ns == 0
            || self.maximum_swing_age_ns == 0
            || self.maximum_levels == 0
            || self.maximum_levels > 100_000
            || !matches!(self.fallback_percent, 1 | 5)
        {
            return Err(Error::Invalid(
                "Strategy 350 initial-stop configuration".into(),
            ));
        }
        content_hash(&("arte.strategy-350-initial-stop-config.v1", self))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Reason {
    SupportedSwingLow,
    BelowVwapSupport,
    OnePercentFallback,
    FivePercentFallback,
}

#[derive(Clone, Serialize)]
pub struct Selection {
    pub configuration_hash: String,
    pub snapshot_hash: String,
    pub level_set_hash: String,
    pub selected_at_ns: u64,
    pub reason: Reason,
    pub structural_stop: f64,
    pub stop: f64,
    pub pivot: Option<Pivot>,
    pub level: Option<TargetLevel>,
    pub noise: Distance,
}
impl Selection {
    pub fn hash(&self) -> Result<String> {
        if !self.stop.is_finite()
            || self.stop <= 0.
            || !self.structural_stop.is_finite()
            || self.structural_stop <= 0.
            || (!matches!(
                self.reason,
                Reason::OnePercentFallback | Reason::FivePercentFallback
            ) && self.stop > self.structural_stop)
        {
            return Err(Error::Invalid("Strategy 350 stop geometry".into()));
        }
        content_hash(&("arte.strategy-350-initial-stop-selection.v1", self))
    }
}

fn below(value: f64, tick: f64) -> Result<f64> {
    let units = value / tick;
    if !units.is_finite() || units <= 1. || units > 1e15 {
        return Err(Error::Invalid("Strategy 350 structural stop tick".into()));
    }
    let stop = ((((units - 1e-9).ceil() - 1.) * tick) * 1e10).round() / 1e10;
    if !stop.is_finite() || stop <= 0. || stop >= value {
        return Err(Error::Invalid("Strategy 350 below-level stop".into()));
    }
    Ok(stop)
}

fn atoms(value: f64, scale: u8) -> Result<i64> {
    if !value.is_finite() || value <= 0. || scale > 9 {
        return Err(Error::Invalid("Strategy 350 stop price scale".into()));
    }
    let scaled = value * 10_f64.powi(i32::from(scale));
    if !scaled.is_finite() || scaled > i64::MAX as f64 || (scaled - scaled.round()).abs() > 1e-6 {
        return Err(Error::Invalid(
            "Strategy 350 stop price not representable".into(),
        ));
    }
    Ok(scaled.round() as i64)
}

/// Select from one causal structural row and one current V7 level view. The
/// noise state must contain only completed bars known by `now_ns`.
#[allow(clippy::too_many_arguments)]
pub fn select(
    snapshot: &StructuralSnapshot,
    levels: &[TargetLevel],
    noise: &NoiseState,
    now_ns: u64,
    entry_ask: f64,
    vwap: f64,
    tick: f64,
    config: &Config,
) -> Result<Selection> {
    let configuration_hash = config.hash()?;
    if now_ns == 0
        || snapshot.effective_at_ns == 0
        || snapshot.effective_at_ns > now_ns
        || now_ns - snapshot.effective_at_ns > config.maximum_snapshot_age_ns
        || levels.len() > config.maximum_levels
        || !entry_ask.is_finite()
        || entry_ask <= 0.
        || !vwap.is_finite()
        || vwap <= 0.
        || !tick.is_finite()
        || tick <= 0.
    {
        return Err(Error::Unready(
            "Strategy 350 initial-stop input stale or missing".into(),
        ));
    }
    let noise_at = noise
        .last_end_ns()
        .ok_or_else(|| Error::Unready("Strategy 350 completed noise bars missing".into()))?;
    if noise_at > now_ns || now_ns - noise_at > config.maximum_noise_age_ns {
        return Err(Error::Unready("Strategy 350 adaptive noise stale".into()));
    }
    for level in levels {
        valid_level(level)?;
        if level.geometry.confirmed_at_ns == 0
            || level.geometry.confirmed_at_ns > snapshot.effective_at_ns
        {
            return Err(Error::Conflict(
                "Strategy 350 support level is not causal".into(),
            ));
        }
    }
    for pivot in &snapshot.pivots {
        if pivot.active
            && pivot.support
            && (pivot.pivot_at_ns == 0
                || pivot.pivot_at_ns >= pivot.confirmed_at_ns
                || pivot.confirmed_at_ns > snapshot.effective_at_ns
                || !pivot.price.is_finite()
                || pivot.price <= 0.)
        {
            return Err(Error::Conflict(
                "Strategy 350 support pivot is not causal".into(),
            ));
        }
    }
    let supports: Vec<_> = levels
        .iter()
        .filter(|level| level.geometry.role == ActiveRole::Support)
        .collect();
    let supported = snapshot
        .pivots
        .iter()
        .filter(|pivot| {
            pivot.active
                && pivot.support
                && pivot.pivot_at_ns <= now_ns
                && now_ns - pivot.pivot_at_ns <= config.maximum_swing_age_ns
        })
        .flat_map(|pivot| {
            let level = supports
                .iter()
                .copied()
                .filter(|level| {
                    level.geometry.lower <= pivot.price && pivot.price <= level.geometry.upper
                })
                .min_by(|a, b| {
                    (a.geometry.upper - a.geometry.lower)
                        .total_cmp(&(b.geometry.upper - b.geometry.lower))
                        .then(a.geometry.id.cmp(&b.geometry.id))
                });
            level.map(|level| (pivot, level))
        })
        .max_by(|(a, al), (b, bl)| {
            a.pivot_at_ns
                .cmp(&b.pivot_at_ns)
                .then(a.confirmed_at_ns.cmp(&b.confirmed_at_ns))
                .then(a.price.total_cmp(&b.price))
                .then(al.geometry.id.cmp(&bl.geometry.id))
        });
    let (reason, structural_stop, pivot, level) = if let Some((pivot, level)) = supported {
        (
            Reason::SupportedSwingLow,
            below(pivot.price, tick)?,
            Some(pivot.clone()),
            Some(level.clone()),
        )
    } else {
        let support = supports
            .iter()
            .copied()
            .filter(|level| level.geometry.upper < vwap)
            .max_by(|a, b| {
                a.geometry
                    .upper
                    .total_cmp(&b.geometry.upper)
                    .then(a.geometry.id.cmp(&b.geometry.id))
            });
        if let Some(level) = support.filter(|level| {
            let distance = entry_ask - level.geometry.lower;
            distance >= 0. && distance <= entry_ask * 0.01
        }) {
            (
                Reason::BelowVwapSupport,
                below(level.geometry.lower, tick)?,
                None,
                Some(level.clone()),
            )
        } else {
            let raw = entry_ask * (1. - f64::from(config.fallback_percent) / 100.);
            let stop = (((raw / tick + 1e-9).floor() * tick) * 1e10).round() / 1e10;
            let reason = if config.fallback_percent == 1 {
                Reason::OnePercentFallback
            } else {
                Reason::FivePercentFallback
            };
            (reason, stop, None, None)
        }
    };
    if !structural_stop.is_finite() || structural_stop <= 0. || structural_stop >= entry_ask {
        return Err(Error::Unready(
            "Strategy 350 structural stop outside entry".into(),
        ));
    }
    let scale = noise.price_scale();
    let entry_atoms = atoms(entry_ask, scale)?;
    let tick_atoms = atoms(tick, scale)?;
    let distance = noise.distance(entry_atoms)?;
    let structural_scaled = if matches!(
        reason,
        Reason::OnePercentFallback | Reason::FivePercentFallback
    ) {
        0
    } else {
        i128::from(entry_atoms - atoms(structural_stop, scale)?) * BPS
    };
    let required = structural_scaled.max(distance.required_scaled);
    if required > distance.maximum_scaled {
        return Err(Error::Unready(
            "Strategy 350 adaptive stop exceeds entry cap".into(),
        ));
    }
    let tick_scaled = i128::from(tick_atoms) * BPS;
    let stop_atoms = (i128::from(entry_atoms) * BPS - required + tick_scaled - 1) / tick_scaled
        * i128::from(tick_atoms);
    if stop_atoms <= 0 || stop_atoms >= i128::from(entry_atoms) || stop_atoms > i128::from(i64::MAX)
    {
        return Err(Error::Unready(
            "Strategy 350 adaptive stop unrepresentable".into(),
        ));
    }
    let stop = stop_atoms as f64 / 10_f64.powi(i32::from(scale));
    let selection = Selection {
        configuration_hash,
        snapshot_hash: content_hash(snapshot)?,
        level_set_hash: content_hash(&levels)?,
        selected_at_ns: now_ns,
        reason,
        structural_stop,
        stop,
        pivot,
        level,
        noise: distance,
    };
    selection.hash()?;
    Ok(selection)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{
        strategy350_noise::{CompletedBar, Config as NoiseConfig},
        strategy_encounters::Level,
    };
    const S: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            maximum_snapshot_age_ns: S,
            maximum_noise_age_ns: 2 * S,
            maximum_swing_age_ns: 30 * S,
            maximum_levels: 100,
            fallback_percent: 1,
        }
    }
    fn noise(high: i64, low: i64) -> NoiseState {
        let mut state = NoiseState::new(
            NoiseConfig {
                execution_interval: ExecutionInterval::Fixed(S),
                price_scale: 2,
                short_multiplier_bps: 15_000,
                session_multiplier_bps: 12_500,
                maximum_entry_fraction_bps: 500,
                percentile_bps: 9_000,
                minimum_session_samples: 6,
                maximum_session_samples: 60_000,
                source_algorithm_hash: "a".repeat(64),
            },
            S,
            20 * S,
        )
        .unwrap();
        state
            .observe(CompletedBar {
                end_ns: 2 * S,
                high_atoms: high,
                low_atoms: low,
            })
            .unwrap();
        state
            .observe(CompletedBar {
                end_ns: 3 * S,
                high_atoms: high,
                low_atoms: low,
            })
            .unwrap();
        state
    }
    fn snapshot(pivots: Vec<Pivot>) -> StructuralSnapshot {
        StructuralSnapshot {
            effective_at_ns: 4 * S,
            pivots,
            broken_high: None,
        }
    }
    fn support(id: &str, lower: f64, upper: f64) -> TargetLevel {
        TargetLevel {
            geometry: Level {
                id: id.into(),
                price: (lower + upper) / 2.,
                lower,
                upper,
                role: ActiveRole::Support,
                confirmed_at_ns: 3 * S,
            },
            historical: false,
            transition_from: None,
            synthetic: false,
        }
    }
    #[test]
    fn supported_low_and_fallback_both_receive_adaptive_protection() {
        let pivot = Pivot {
            price: 9.95,
            pivot_at_ns: 2 * S,
            confirmed_at_ns: 3 * S,
            support: true,
            active: true,
        };
        let support = support("band", 9.9, 10.);
        let market_noise = noise(1010, 1000);
        let structural = select(
            &snapshot(vec![pivot]),
            &[support],
            &market_noise,
            4 * S,
            10.4,
            10.2,
            0.01,
            &config(),
        )
        .unwrap();
        assert_eq!(structural.reason, Reason::SupportedSwingLow);
        assert_eq!(structural.structural_stop, 9.94);
        assert_eq!(structural.stop, 9.94);
        assert_eq!(structural.hash().unwrap().len(), 64);
        let mut effective = crate::strategy350_effective::test_config(ExecutionInterval::Events);
        effective.noise_config_hash = market_noise.configuration_hash().into();
        let pinned = effective
            .select_initial_stop(&snapshot(vec![]), &[], &market_noise, 4 * S, 10., 10., 0.01)
            .unwrap();
        assert_eq!(pinned.reason, Reason::OnePercentFallback);
        effective.noise_config_hash = "0".repeat(64);
        assert!(effective
            .select_initial_stop(&snapshot(vec![]), &[], &market_noise, 4 * S, 10., 10., 0.01,)
            .is_err());
        let fallback = select(
            &snapshot(vec![]),
            &[],
            &market_noise,
            4 * S,
            10.,
            10.,
            0.01,
            &config(),
        )
        .unwrap();
        assert_eq!(fallback.reason, Reason::OnePercentFallback);
        assert_eq!(fallback.stop, 9.85);
        let mut five_percent = config();
        five_percent.fallback_percent = 5;
        let widened = select(
            &snapshot(vec![]),
            &[],
            &market_noise,
            4 * S,
            10.,
            10.,
            0.01,
            &five_percent,
        )
        .unwrap();
        assert_eq!(widened.structural_stop, 9.5);
        assert_eq!(widened.stop, 9.85);
    }
    #[test]
    fn stale_future_or_over_cap_evidence_cannot_create_a_stop() {
        let market_noise = noise(1010, 1000);
        assert!(select(
            &snapshot(vec![]),
            &[],
            &market_noise,
            7 * S,
            10.,
            10.,
            0.01,
            &config()
        )
        .is_err());
        let mut future = support("future", 9.9, 10.);
        future.geometry.confirmed_at_ns = 5 * S;
        assert!(select(
            &snapshot(vec![]),
            &[future],
            &market_noise,
            4 * S,
            10.,
            10.,
            0.01,
            &config()
        )
        .is_err());
        let loud_noise = noise(1050, 1000);
        assert!(select(
            &snapshot(vec![]),
            &[],
            &loud_noise,
            4 * S,
            10.,
            10.,
            0.01,
            &config()
        )
        .is_err());
    }
}
