//! Bounded immutable Strategy 350 adaptive-noise recovery image.
//! This is calculation state, never broker or order permission.
use super::{CompletedBar, Config, State, SECOND};
use crate::{seed_storage::Object, Error, Result};
use serde::{Deserialize, Serialize};

const MAX_BYTES: usize = 8 * 1024 * 1024;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    configuration_hash: String,
    session_start_ns: u64,
    session_end_ns: u64,
    observed_bars: u64,
    last_end_ns: Option<u64>,
    bars: Vec<CompletedBar>,
    ranges: Vec<(i64, u64)>,
}
fn bound(maximum_bytes: usize) -> Result<()> {
    if maximum_bytes == 0 || maximum_bytes > MAX_BYTES {
        return Err(Error::Invalid(
            "Strategy 350 noise checkpoint budget".into(),
        ));
    }
    Ok(())
}
impl State {
    pub fn checkpoint(&self, maximum_bytes: usize) -> Result<Object> {
        bound(maximum_bytes)?;
        self.healthy()?;
        let saved = Saved {
            version: 1,
            configuration_hash: self.configuration_hash.clone(),
            session_start_ns: self.session_start_ns,
            session_end_ns: self.session_end_ns,
            observed_bars: self.observed_bars,
            last_end_ns: self.last_end_ns,
            bars: self.bars.iter().copied().collect(),
            ranges: self.ranges.iter().copied().collect(),
        };
        let payload =
            serde_json::to_vec(&saved).map_err(|e| Error::Serialization(e.to_string()))?;
        if payload.len() > maximum_bytes {
            return Err(Error::Capacity(
                "Strategy 350 noise checkpoint bytes".into(),
            ));
        }
        Ok(Object::new(payload))
    }
    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        config: Config,
        session_start_ns: u64,
        session_end_ns: u64,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bound(maximum_bytes)?;
        image.verify()?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Invalid(
                "Strategy 350 noise image identity or size".into(),
            ));
        }
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        let mut state = Self::new(config, session_start_ns, session_end_ns)?;
        let expected_bars = saved.observed_bars.min(5) as usize;
        let expected_ranges = saved
            .observed_bars
            .saturating_sub(4)
            .min(state.config.maximum_session_samples as u64)
            as usize;
        if saved.version != 1
            || saved.configuration_hash != state.configuration_hash
            || saved.session_start_ns != session_start_ns
            || saved.session_end_ns != session_end_ns
            || saved.observed_bars > (session_end_ns - session_start_ns) / SECOND
            || saved.bars.len() != expected_bars
            || saved.ranges.len() != expected_ranges
            || (saved.observed_bars == 0) != saved.last_end_ns.is_none()
            || saved.last_end_ns != saved.bars.last().map(|b| b.end_ns)
        {
            return Err(Error::Conflict(
                "Strategy 350 noise checkpoint geometry".into(),
            ));
        }
        if saved.bars.iter().any(|bar| {
            bar.end_ns <= session_start_ns
                || bar.end_ns > session_end_ns
                || !bar.end_ns.is_multiple_of(SECOND)
                || bar.low_atoms <= 0
                || bar.high_atoms < bar.low_atoms
        }) || saved
            .bars
            .windows(2)
            .any(|pair| pair[0].end_ns >= pair[1].end_ns)
        {
            return Err(Error::Conflict("Strategy 350 noise saved bars".into()));
        }
        let next_range_id = saved.observed_bars.saturating_sub(4);
        let first_id = next_range_id.saturating_sub(saved.ranges.len() as u64) + 1;
        if saved
            .ranges
            .iter()
            .enumerate()
            .any(|(offset, (range, id))| *range < 0 || *id != first_id + offset as u64)
        {
            return Err(Error::Conflict("Strategy 350 noise saved ranges".into()));
        }
        if saved.bars.len() == 5 {
            let high = saved.bars.iter().map(|b| b.high_atoms).max().unwrap();
            let low = saved.bars.iter().map(|b| b.low_atoms).min().unwrap();
            if high.checked_sub(low) != saved.ranges.last().map(|row| row.0) {
                return Err(Error::Conflict("Strategy 350 latest range differs".into()));
            }
        }
        state.bars = saved.bars.into();
        state.next_range_id = next_range_id;
        state.observed_bars = saved.observed_bars;
        state.last_end_ns = saved.last_end_ns;
        for item in saved.ranges {
            state.ranges.push_back(item);
            state.lower.insert(item);
            state.rebalance();
        }
        if state.checkpoint(maximum_bytes)?.payload != image.payload {
            return Err(Error::Conflict(
                "Strategy 350 noncanonical noise image".into(),
            ));
        }
        Ok(state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::execution_interval::ExecutionInterval;
    const START: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Fixed(SECOND),
            price_scale: 2,
            short_multiplier_bps: 15_000,
            session_multiplier_bps: 12_500,
            maximum_entry_fraction_bps: 500,
            percentile_bps: 9_000,
            minimum_session_samples: 6,
            maximum_session_samples: 6,
            source_algorithm_hash: "a".repeat(64),
        }
    }
    #[test]
    fn restored_state_matches_continuous_adaptive_distance() {
        let mut continuous = State::new(config(), START, START + 20 * SECOND).unwrap();
        for i in 1..=10 {
            continuous
                .observe(CompletedBar {
                    end_ns: START + i * SECOND,
                    high_atoms: 1000 + i as i64 + if i == 6 { 60 } else { 0 },
                    low_atoms: 990 + i as i64,
                })
                .unwrap();
        }
        let image = continuous.checkpoint(MAX_BYTES).unwrap();
        let mut restored = State::restore_checkpoint(
            &image,
            &image.id,
            config(),
            START,
            START + 20 * SECOND,
            MAX_BYTES,
        )
        .unwrap();
        assert_eq!(
            continuous.distance(1000).unwrap(),
            restored.distance(1000).unwrap()
        );
        for i in 11..=15 {
            let bar = CompletedBar {
                end_ns: START + i * SECOND,
                high_atoms: 1000 + i as i64,
                low_atoms: 990 + i as i64,
            };
            continuous.observe(bar).unwrap();
            restored.observe(bar).unwrap();
            assert_eq!(
                continuous.distance(1000).unwrap(),
                restored.distance(1000).unwrap()
            );
        }
        assert_eq!(
            continuous.checkpoint(MAX_BYTES).unwrap().id,
            restored.checkpoint(MAX_BYTES).unwrap().id
        );
        let mut damaged = image.clone();
        damaged.payload[0] ^= 1;
        assert!(State::restore_checkpoint(
            &damaged,
            &image.id,
            config(),
            START,
            START + 20 * SECOND,
            MAX_BYTES
        )
        .is_err());
        let mut forged: Saved = serde_json::from_slice(&image.payload).unwrap();
        forged.ranges.last_mut().unwrap().0 += 1;
        let forged = Object::new(serde_json::to_vec(&forged).unwrap());
        assert!(State::restore_checkpoint(
            &forged,
            &forged.id,
            config(),
            START,
            START + 20 * SECOND,
            MAX_BYTES,
        )
        .is_err());
        let mut changed = config();
        changed.percentile_bps = 8_000;
        assert!(State::restore_checkpoint(
            &image,
            &image.id,
            changed,
            START,
            START + 20 * SECOND,
            MAX_BYTES
        )
        .is_err());
        assert!(State::restore_checkpoint(
            &image,
            &image.id,
            config(),
            START,
            START + 20 * SECOND,
            10
        )
        .is_err());
    }
}
