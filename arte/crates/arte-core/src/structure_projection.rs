//! Current V7 state to strategy geometry. No historical look-back substitution.
use crate::{
    strategy_encounters::Level, strategy_targets::TargetLevel, v7_encounters::ActiveRole,
    v7_stream::Stream, Error, Result,
};
use std::collections::BTreeSet;
const SECOND: u64 = 1_000_000_000;
pub fn current(stream: &Stream, at_ns: u64, maximum: usize) -> Result<Vec<TargetLevel>> {
    if maximum == 0 || maximum > 100_000 {
        return Err(Error::Capacity("strategy level projection budget".into()));
    }
    if stream.as_of.checked_mul(SECOND).is_none_or(|at| at > at_ns) {
        return Err(Error::Unready(
            "current V7 state is newer than requested strategy boundary".into(),
        ));
    }
    let mut projected = vec![];
    let mut ids = BTreeSet::new();
    for source in stream.qualified_levels()? {
        if projected.len() == maximum {
            return Err(Error::Capacity(
                "strategy level projection full; no truncation".into(),
            ));
        }
        if !ids.insert(source.id.clone()) {
            return Err(Error::Conflict("duplicate V7 level identity".into()));
        }
        let segment = source
            .segments
            .last()
            .ok_or_else(|| Error::Unready("qualified V7 level has no causal segment".into()))?;
        let confirmed_at_ns = segment
            .at
            .checked_mul(SECOND)
            .ok_or_else(|| Error::Invalid("V7 segment clock overflow".into()))?;
        if confirmed_at_ns > at_ns || segment.at > stream.as_of || segment.role != source.role {
            return Err(Error::Conflict(
                "V7 level role or segment clock differs".into(),
            ));
        }
        if !segment.band.estimated()
            || segment.band.fit.center != source.band.fit.center
            || segment.band.lower != source.band.lower
            || segment.band.upper != source.band.upper
        {
            return Err(Error::Conflict(
                "V7 fitted geometry differs from its causal segment".into(),
            ));
        }
        let price = source
            .band
            .fit
            .center
            .ok_or_else(|| Error::Unready("V7 fitted center missing".into()))?;
        let lower = source
            .band
            .lower
            .ok_or_else(|| Error::Unready("V7 lower band missing".into()))?;
        let upper = source
            .band
            .upper
            .ok_or_else(|| Error::Unready("V7 upper band missing".into()))?;
        let transition_from = if source.role == ActiveRole::Transition {
            source
                .segments
                .iter()
                .rev()
                .skip(1)
                .find(|s| s.role != ActiveRole::Transition)
                .map(|s| s.role)
        } else {
            None
        };
        let level = TargetLevel {
            geometry: Level {
                id: source.id.clone(),
                price,
                lower,
                upper,
                role: source.role,
                confirmed_at_ns,
            },
            historical: source.historical,
            transition_from,
            synthetic: false,
        };
        crate::strategy_targets::valid_level(&level)?;
        projected.push(level);
    }
    projected.sort_by(|a, b| a.geometry.id.cmp(&b.geometry.id));
    Ok(projected)
}
