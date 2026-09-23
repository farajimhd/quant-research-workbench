//! Bounded, content-addressed recovery for account-owned target progress.
use super::{Config, Progress};
use crate::{seed_storage::Object, Error, Result};
use serde::{Deserialize, Serialize};
use std::collections::BTreeSet;

const MAX_BYTES: usize = 8 * 1024 * 1024;

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    state: Progress,
}

fn bound(maximum_bytes: usize) -> Result<()> {
    if maximum_bytes == 0 || maximum_bytes > MAX_BYTES {
        return Err(Error::Invalid(
            "Strategy 350 target checkpoint budget".into(),
        ));
    }
    Ok(())
}

impl Progress {
    fn validate_recovery(&self, config: &Config, session_wide: bool) -> Result<()> {
        if self.configuration_hash != config.hash()?
            || self.session_wide != session_wide
            || self.seen.len() > config.maximum_distinct_levels
            || (self.seen.is_empty() != (self.last_opened_at_ns == 0))
            || self.seen.iter().any(|id| id.is_empty() || id.len() > 128)
        {
            return Err(Error::Conflict(
                "Strategy 350 target checkpoint identity".into(),
            ));
        }
        if session_wide {
            let steps = self.seen.len() / 3;
            let expected = match steps {
                0 => 5,
                1 => 8,
                2 => 10,
                _ => 12 + (steps - 3) as u32,
            };
            let recent: BTreeSet<_> = self.last_three.iter().collect();
            if self.multiplier != expected
                || !self.fast_pending.is_empty()
                || self.last_three.len() != self.seen.len().min(3)
                || recent.len() != self.last_three.len()
                || self.last_three.iter().any(|id| !self.seen.contains(id))
            {
                return Err(Error::Conflict(
                    "Strategy 350 session target checkpoint".into(),
                ));
            }
        } else {
            if !self.last_three.is_empty()
                || !matches!(self.multiplier, 5 | 8 | 10)
                || (self.multiplier == 8 && self.seen.len() < 3)
                || (self.multiplier == 10 && self.seen.len() < 6)
                || (self.multiplier < 10 && self.fast_pending.len() >= 3)
                || self.fast_pending.len() > self.seen.len()
            {
                return Err(Error::Conflict(
                    "Strategy 350 position target checkpoint".into(),
                ));
            }
            let mut pending_ids = BTreeSet::new();
            let mut last = 0;
            for event in &self.fast_pending {
                if event.opened_at_ns == 0
                    || event.opened_at_ns < last
                    || event.opened_at_ns > self.last_opened_at_ns
                    || !self.seen.contains(&event.level_id)
                    || !pending_ids.insert(&event.level_id)
                {
                    return Err(Error::Conflict("Strategy 350 pending target breaks".into()));
                }
                last = event.opened_at_ns;
            }
        }
        Ok(())
    }

    pub fn checkpoint(&self, config: &Config, maximum_bytes: usize) -> Result<Object> {
        bound(maximum_bytes)?;
        self.validate_recovery(config, self.session_wide)?;
        let payload = serde_json::to_vec(&Saved {
            version: 1,
            state: self.clone(),
        })
        .map_err(|error| Error::Serialization(error.to_string()))?;
        if payload.len() > maximum_bytes {
            return Err(Error::Capacity(
                "Strategy 350 target checkpoint bytes".into(),
            ));
        }
        Ok(Object::new(payload))
    }

    pub fn restore_checkpoint(
        image: &Object,
        expected_hash: &str,
        config: &Config,
        session_wide: bool,
        maximum_bytes: usize,
    ) -> Result<Self> {
        bound(maximum_bytes)?;
        image.verify()?;
        if image.id != expected_hash || image.payload.len() > maximum_bytes {
            return Err(Error::Conflict("Strategy 350 target image identity".into()));
        }
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        if saved.version != 1 {
            return Err(Error::Conflict("Strategy 350 target image version".into()));
        }
        saved.state.validate_recovery(config, session_wide)?;
        if saved.state.checkpoint(config, maximum_bytes)?.payload != image.payload {
            return Err(Error::Conflict(
                "Strategy 350 target image canonical bytes".into(),
            ));
        }
        Ok(saved.state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{execution_interval::ExecutionInterval, strategy350_targets::BreakEvent};
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            maximum_distinct_levels: 10,
        }
    }
    fn event(id: &str, second: u64) -> BreakEvent {
        BreakEvent {
            level_id: id.into(),
            opened_at_ns: second * 1_000_000_000,
        }
    }
    #[test]
    fn session_and_position_images_restore_only_with_matching_pins() {
        let config = config();
        for session_wide in [false, true] {
            let mut progress = Progress::new(&config, session_wide).unwrap();
            progress
                .observe(
                    &[event("a", 1), event("b", 2), event("c", 3)],
                    3_000_000_000,
                    &config,
                )
                .unwrap();
            let image = progress.checkpoint(&config, 4096).unwrap();
            let restored =
                Progress::restore_checkpoint(&image, &image.id, &config, session_wide, 4096)
                    .unwrap();
            assert_eq!(restored.multiplier(), 8);
            assert_eq!(restored.checkpoint(&config, 4096).unwrap().id, image.id);
            assert!(
                Progress::restore_checkpoint(&image, &image.id, &config, !session_wide, 4096)
                    .is_err()
            );
            let mut changed = config.clone();
            changed.maximum_distinct_levels += 1;
            assert!(
                Progress::restore_checkpoint(&image, &image.id, &changed, session_wide, 4096)
                    .is_err()
            );
            assert!(Progress::restore_checkpoint(
                &image,
                &"0".repeat(64),
                &config,
                session_wide,
                4096
            )
            .is_err());
        }
    }
    #[test]
    fn tampered_but_rehashed_target_state_still_fails_semantic_restore() {
        let config = config();
        let mut progress = Progress::new(&config, true).unwrap();
        progress
            .observe(
                &[event("a", 1), event("b", 2), event("c", 3)],
                3_000_000_000,
                &config,
            )
            .unwrap();
        let mut saved: Saved =
            serde_json::from_slice(&progress.checkpoint(&config, 4096).unwrap().payload).unwrap();
        saved.state.multiplier = 10;
        let image = Object::new(serde_json::to_vec(&saved).unwrap());
        assert!(Progress::restore_checkpoint(&image, &image.id, &config, true, 4096).is_err());
    }
}
