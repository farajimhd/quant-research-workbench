//! Immutable four-timeframe EMA image. The exact-bar source has its own image;
//! a future common-cut root must pin both IDs with the market scheduler cut.
use super::{Config, Frame, State, TIMEFRAMES_NS};
use crate::{event_order::Scope, seed_storage::Object, Error, Result};
use serde::{Deserialize, Serialize};

const MAX_BYTES: usize = 4096;
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    scope: (u16, u64, u32),
    session_start_ns: u64,
    session_end_ns: u64,
    configuration_hash: String,
    price_scale: u8,
    frames: [Frame; 4],
}
impl State {
    fn validate_checkpoint_geometry(&self) -> Result<()> {
        for (index, frame) in self.frames.iter().enumerate() {
            if frame.interval_ns != TIMEFRAMES_NS[index]
                || frame.last_end_ns.is_some_and(|end| {
                    end <= self.session_start_ns
                        || end > self.session_end_ns
                        || !end.is_multiple_of(frame.interval_ns)
                })
            {
                return Err(Error::Conflict("Strategy 350 MACD frame geometry".into()));
            }
            frame
                .macd
                .require_checkpoint(12, 26, 9, frame.last_end_ns.is_some())?;
        }
        Ok(())
    }
    pub fn checkpoint(&self) -> Result<Object> {
        self.validate_checkpoint_geometry()?;
        let payload = serde_json::to_vec(&Saved {
            version: 1,
            scope: (
                self.scope.provider,
                self.scope.instrument,
                self.scope.session,
            ),
            session_start_ns: self.session_start_ns,
            session_end_ns: self.session_end_ns,
            configuration_hash: self.config_hash.clone(),
            price_scale: self.price_scale,
            frames: self.frames.clone(),
        })
        .map_err(|error| Error::Serialization(error.to_string()))?;
        if payload.len() > MAX_BYTES {
            return Err(Error::Capacity("Strategy 350 MACD image bytes".into()));
        }
        Ok(Object::new(payload))
    }
    pub fn restore_checkpoint(
        image: &Object,
        expected_id: &str,
        scope: Scope,
        session_start_ns: u64,
        session_end_ns: u64,
        config: &Config,
    ) -> Result<Self> {
        image.verify()?;
        if image.id != expected_id || image.payload.len() > MAX_BYTES {
            return Err(Error::Conflict("Strategy 350 MACD image identity".into()));
        }
        let saved: Saved = serde_json::from_slice(&image.payload)
            .map_err(|error| Error::Serialization(error.to_string()))?;
        let mut state = Self::new(scope, session_start_ns, session_end_ns, config)?;
        if saved.version != 1
            || saved.scope != (scope.provider, scope.instrument, scope.session)
            || saved.session_start_ns != session_start_ns
            || saved.session_end_ns != session_end_ns
            || saved.configuration_hash != state.config_hash
            || saved.price_scale != state.price_scale
        {
            return Err(Error::Conflict("Strategy 350 MACD image pin".into()));
        }
        state.frames = saved.frames;
        state.validate_checkpoint_geometry()?;
        if state.checkpoint()?.payload != image.payload {
            return Err(Error::Conflict(
                "Strategy 350 MACD image noncanonical".into(),
            ));
        }
        Ok(state)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::{events::Decimal, execution_interval::ExecutionInterval};
    const S: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            execution_interval: ExecutionInterval::Events,
            price_scale: 2,
            source_algorithm_hash: "a".repeat(64),
        }
    }
    fn scope() -> Scope {
        Scope {
            provider: 1,
            instrument: 10,
            session: 20260922,
        }
    }
    #[test]
    fn restored_macd_state_continues_identically_and_rejects_changed_ema() {
        let mut continuous = State::new(scope(), 30 * S, 120 * S, &config()).unwrap();
        for timeframe in TIMEFRAMES_NS {
            continuous
                .observe_completed(
                    timeframe,
                    60 * S,
                    Decimal {
                        atoms: 1000,
                        scale: 2,
                    },
                )
                .unwrap();
        }
        let image = continuous.checkpoint().unwrap();
        let mut restored =
            State::restore_checkpoint(&image, &image.id, scope(), 30 * S, 120 * S, &config())
                .unwrap();
        for (timeframe, end) in [
            (S, 61 * S),
            (5 * S, 65 * S),
            (10 * S, 70 * S),
            (30 * S, 90 * S),
        ] {
            let close = Decimal {
                atoms: 1100,
                scale: 2,
            };
            continuous.observe_completed(timeframe, end, close).unwrap();
            restored.observe_completed(timeframe, end, close).unwrap();
        }
        assert_eq!(
            continuous.checkpoint().unwrap().id,
            restored.checkpoint().unwrap().id
        );
        assert_eq!(
            continuous
                .preview_trade(
                    90 * S + 1,
                    90 * S + 2,
                    Decimal {
                        atoms: 1200,
                        scale: 2,
                    },
                )
                .unwrap(),
            restored
                .preview_trade(
                    90 * S + 1,
                    90 * S + 2,
                    Decimal {
                        atoms: 1200,
                        scale: 2,
                    },
                )
                .unwrap()
        );
        let mut changed: serde_json::Value = serde_json::from_slice(&image.payload).unwrap();
        changed["frames"][0]["macd"]["fast"]["alpha"] = 0.5.into();
        let changed = Object::new(serde_json::to_vec(&changed).unwrap());
        assert!(State::restore_checkpoint(
            &changed,
            &changed.id,
            scope(),
            30 * S,
            120 * S,
            &config(),
        )
        .is_err());
        let mut wrong = config();
        wrong.source_algorithm_hash = "b".repeat(64);
        assert!(
            State::restore_checkpoint(&image, &image.id, scope(), 30 * S, 120 * S, &wrong,)
                .is_err()
        );
    }
}
