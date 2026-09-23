//! First-session Early Squeeze activation from certified completed 100 ms bars.
//! The first impulse is an occurrence; session-watch membership then latches.
//! This historical clock is the bar close, not a fabricated live receipt time.
use crate::{
    bar_catalogue::{Column, Complete, BASE_INTERVAL_NS},
    boolean_compute::Evaluation,
    content_hash,
    seed_storage::Object,
    Error, Result,
};
use serde::{Deserialize, Serialize};

pub const VERSION: &str = "arte.strategy-350-early-squeeze.v1";
#[derive(Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Config {
    /// Five basis points is the inspected 0.05% source rule.
    pub minimum_move_bps: u32,
    pub source_algorithm_hash: String,
}
impl Config {
    pub fn hash(&self) -> Result<String> {
        if self.minimum_move_bps == 0
            || self.minimum_move_bps > 10_000
            || self.source_algorithm_hash.len() != 64
            || !self
                .source_algorithm_hash
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        {
            return Err(Error::Invalid("early-squeeze signal configuration".into()));
        }
        content_hash(&(VERSION, self))
    }
}

#[derive(Clone, Copy, Serialize, Deserialize)]
struct Previous {
    close: i64,
    volume: i64,
    trades: u64,
}
pub struct State {
    config: Config,
    scope_hash: String,
    session_start_ns: u64,
    next_bucket_ns: u64,
    session_end_ns: u64,
    mode: Mode,
    previous: Option<Previous>,
    first_occurrence_end_ns: Option<u64>,
    first_occurrence_available_at_ns: Option<u64>,
    last_available_at_ns: Option<u64>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Mode {
    Historical,
    Live,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Occurrence {
    pub event_time_ns: u64,
    /// Never inferred for historical calculation.
    pub available_at_ns: Option<u64>,
}
#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Saved {
    version: u32,
    scope_hash: String,
    config_hash: String,
    session_start_ns: u64,
    session_end_ns: u64,
    next_bucket_ns: u64,
    mode: Mode,
    previous: Option<Previous>,
    first_occurrence_end_ns: Option<u64>,
    first_occurrence_available_at_ns: Option<u64>,
    last_available_at_ns: Option<u64>,
}
fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
impl State {
    fn new(
        config: Config,
        scope_hash: String,
        session_start_ns: u64,
        session_end_ns: u64,
        mode: Mode,
    ) -> Result<Self> {
        config.hash()?;
        if !hash_valid(&scope_hash)
            || session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(BASE_INTERVAL_NS)
            || !session_end_ns.is_multiple_of(BASE_INTERVAL_NS)
        {
            return Err(Error::Invalid("early-squeeze session grid".into()));
        }
        Ok(Self {
            config,
            scope_hash,
            session_start_ns,
            next_bucket_ns: session_start_ns,
            session_end_ns,
            mode,
            previous: None,
            first_occurrence_end_ns: None,
            first_occurrence_available_at_ns: None,
            last_available_at_ns: None,
        })
    }
    pub fn new_historical(
        config: Config,
        scope_hash: String,
        session_start_ns: u64,
        session_end_ns: u64,
    ) -> Result<Self> {
        Self::new(
            config,
            scope_hash,
            session_start_ns,
            session_end_ns,
            Mode::Historical,
        )
    }
    pub fn new_live(
        config: Config,
        scope_hash: String,
        session_start_ns: u64,
        session_end_ns: u64,
    ) -> Result<Self> {
        Self::new(
            config,
            scope_hash,
            session_start_ns,
            session_end_ns,
            Mode::Live,
        )
    }
    pub fn first_occurrence_end_ns(&self) -> Option<u64> {
        self.first_occurrence_end_ns
    }
    pub fn first_occurrence(&self) -> Option<Occurrence> {
        self.first_occurrence_end_ns
            .map(|event_time_ns| Occurrence {
                event_time_ns,
                available_at_ns: self.first_occurrence_available_at_ns,
            })
    }
    pub fn observe(
        &mut self,
        bucket_start_ns: u64,
        present: bool,
        close: i64,
        volume: i64,
        trades: u64,
    ) -> Result<bool> {
        if self.mode != Mode::Historical {
            return Err(Error::Invalid(
                "live signal requires availability clock".into(),
            ));
        }
        self.observe_inner(bucket_start_ns, present, close, volume, trades, None)
    }
    pub fn observe_live(
        &mut self,
        bucket_start_ns: u64,
        present: bool,
        close: i64,
        volume: i64,
        trades: u64,
        available_at_ns: u64,
    ) -> Result<bool> {
        if self.mode != Mode::Live {
            return Err(Error::Invalid(
                "historical signal cannot acquire live clock".into(),
            ));
        }
        self.observe_inner(
            bucket_start_ns,
            present,
            close,
            volume,
            trades,
            Some(available_at_ns),
        )
    }
    fn observe_inner(
        &mut self,
        bucket_start_ns: u64,
        present: bool,
        close: i64,
        volume: i64,
        trades: u64,
        available_at_ns: Option<u64>,
    ) -> Result<bool> {
        let end_ns = bucket_start_ns
            .checked_add(BASE_INTERVAL_NS)
            .ok_or_else(|| Error::Capacity("early-squeeze bucket clock".into()))?;
        if bucket_start_ns != self.next_bucket_ns
            || bucket_start_ns >= self.session_end_ns
            || end_ns > self.session_end_ns
            || available_at_ns.is_some_and(|at| {
                at < end_ns || self.last_available_at_ns.is_some_and(|last| at < last)
            })
            || (present && (close <= 0 || volume < 0 || trades == 0))
            || (!present && (close != 0 || volume != 0 || trades != 0))
        {
            return Err(Error::Conflict(
                "early-squeeze bucket order or values".into(),
            ));
        }
        if present {
            let impulse = self.previous.is_some_and(|prior| {
                (close as i128) * 10_000
                    >= (prior.close as i128) * (10_000 + self.config.minimum_move_bps as i128)
                    && trades > prior.trades
                    && volume > prior.volume
            });
            if impulse && self.first_occurrence_end_ns.is_none() {
                self.first_occurrence_end_ns = Some(end_ns);
                self.first_occurrence_available_at_ns = available_at_ns;
            }
            self.previous = Some(Previous {
                close,
                volume,
                trades,
            });
        }
        self.next_bucket_ns = end_ns;
        if available_at_ns.is_some() {
            self.last_available_at_ns = available_at_ns;
        }
        Ok(self.first_occurrence_end_ns.is_some())
    }
    pub fn checkpoint(&self) -> Result<Object> {
        let saved = Saved {
            version: 1,
            scope_hash: self.scope_hash.clone(),
            config_hash: self.config.hash()?,
            session_start_ns: self.session_start_ns,
            session_end_ns: self.session_end_ns,
            next_bucket_ns: self.next_bucket_ns,
            mode: self.mode,
            previous: self.previous,
            first_occurrence_end_ns: self.first_occurrence_end_ns,
            first_occurrence_available_at_ns: self.first_occurrence_available_at_ns,
            last_available_at_ns: self.last_available_at_ns,
        };
        let bytes = serde_json::to_vec(&saved).map_err(|e| Error::Serialization(e.to_string()))?;
        if bytes.len() > 4096 {
            return Err(Error::Capacity("early-squeeze recovery bytes".into()));
        }
        Ok(Object::new(bytes))
    }
    pub fn restore(
        object: &Object,
        scope_hash: &str,
        config: Config,
        session_start_ns: u64,
        session_end_ns: u64,
        mode: Mode,
    ) -> Result<Self> {
        object.verify()?;
        if !hash_valid(scope_hash) || object.payload.len() > 4096 {
            return Err(Error::Invalid("early-squeeze recovery bounds".into()));
        }
        let saved: Saved = serde_json::from_slice(&object.payload)
            .map_err(|e| Error::Serialization(e.to_string()))?;
        if saved.version != 1
            || saved.scope_hash != scope_hash
            || saved.config_hash != config.hash()?
            || saved.session_start_ns != session_start_ns
            || saved.session_end_ns != session_end_ns
            || saved.mode != mode
            || saved.next_bucket_ns < session_start_ns
            || saved.next_bucket_ns > session_end_ns
            || !saved.next_bucket_ns.is_multiple_of(BASE_INTERVAL_NS)
            || saved
                .previous
                .is_some_and(|p| p.close <= 0 || p.volume < 0 || p.trades == 0)
            || saved.first_occurrence_end_ns.is_some_and(|at| {
                at <= session_start_ns
                    || at > saved.next_bucket_ns
                    || !at.is_multiple_of(BASE_INTERVAL_NS)
            })
            || (saved.first_occurrence_end_ns.is_some() && saved.previous.is_none())
            || (saved.first_occurrence_end_ns.is_none()
                != saved.first_occurrence_available_at_ns.is_none()
                && mode == Mode::Live)
            || (mode == Mode::Historical
                && (saved.first_occurrence_available_at_ns.is_some()
                    || saved.last_available_at_ns.is_some()))
            || (mode == Mode::Live
                && saved.next_bucket_ns > session_start_ns
                && saved.last_available_at_ns.is_none())
            || (mode == Mode::Live
                && saved.next_bucket_ns == session_start_ns
                && saved.last_available_at_ns.is_some())
            || saved
                .last_available_at_ns
                .is_some_and(|at| at < saved.next_bucket_ns)
            || saved.first_occurrence_available_at_ns.is_some_and(|at| {
                at < saved.first_occurrence_end_ns.unwrap_or(u64::MAX)
                    || saved.last_available_at_ns.is_some_and(|last| at > last)
            })
        {
            return Err(Error::Conflict(
                "early-squeeze recovery state differs".into(),
            ));
        }
        let mut state = Self::new(
            config,
            scope_hash.into(),
            session_start_ns,
            session_end_ns,
            mode,
        )?;
        state.next_bucket_ns = saved.next_bucket_ns;
        state.previous = saved.previous;
        state.first_occurrence_end_ns = saved.first_occurrence_end_ns;
        state.first_occurrence_available_at_ns = saved.first_occurrence_available_at_ns;
        state.last_available_at_ns = saved.last_available_at_ns;
        Ok(state)
    }
}

pub struct Projection {
    pub first_occurrence_end_ns: Option<u64>,
    pub evaluations: Vec<Evaluation>,
}
/// Requires the full certified session prefix. A partial slice may omit the
/// first occurrence and cannot establish the session-watch activation state.
pub fn project(bars: &Complete, config: Config, session_start_ns: u64) -> Result<Projection> {
    let request = bars.request();
    if request.instruments.len() != 1
        || request.interval.start != session_start_ns
        || request.timeframe_ns != BASE_INTERVAL_NS
        || ![Column::Close, Column::Volume, Column::Trades]
            .into_iter()
            .all(|c| request.columns.contains(&c))
    {
        return Err(Error::Unready(
            "early-squeeze full-session bar source".into(),
        ));
    }
    let mut state = State::new_historical(
        config,
        request.hash()?,
        session_start_ns,
        request.interval.end,
    )?;
    let mut evaluations = Vec::with_capacity(
        ((request.interval.end - request.interval.start) / BASE_INTERVAL_NS) as usize,
    );
    for batch in bars.batches() {
        let close = batch
            .close
            .as_ref()
            .ok_or_else(|| Error::Unready("early-squeeze close".into()))?;
        let volume = batch
            .volume
            .as_ref()
            .ok_or_else(|| Error::Unready("early-squeeze volume".into()))?;
        let trades = batch
            .trades
            .as_ref()
            .ok_or_else(|| Error::Unready("early-squeeze trades".into()))?;
        for i in 0..batch.count as usize {
            let bucket_start_ns = batch.first_start_ns + i as u64 * BASE_INTERVAL_NS;
            let active = state.observe(
                bucket_start_ns,
                batch.present[i],
                close[i],
                volume[i],
                trades[i],
            )?;
            evaluations.push(Evaluation {
                bucket_start_ns,
                value: Some(active),
            });
        }
    }
    Ok(Projection {
        first_occurrence_end_ns: state.first_occurrence_end_ns(),
        evaluations,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    const S: u64 = 1_000_000_000;
    fn config() -> Config {
        Config {
            minimum_move_bps: 5,
            source_algorithm_hash: "a".repeat(64),
        }
    }
    #[test]
    fn first_impulse_latches_across_empty_and_nonimpulse_buckets() {
        let mut state =
            State::new_historical(config(), "a".repeat(64), S, S + 600_000_000).unwrap();
        assert!(!state.observe(S, true, 10_000, 100, 2).unwrap());
        assert!(!state.observe(S + 100_000_000, false, 0, 0, 0).unwrap());
        // Exactly 0.05% rise, with both trade count and volume increasing.
        assert!(state
            .observe(S + 200_000_000, true, 10_005, 101, 3)
            .unwrap());
        assert_eq!(state.first_occurrence_end_ns(), Some(S + 300_000_000));
        assert_eq!(state.first_occurrence().unwrap().available_at_ns, None);
        assert!(state.observe(S + 300_000_000, true, 9_000, 1, 1).unwrap());
        assert!(state.observe(S + 400_000_000, false, 0, 0, 0).unwrap());
        assert!(state
            .observe(S + 500_000_000, true, 11_000, 10, 10)
            .unwrap());
        assert_eq!(state.first_occurrence_end_ns(), Some(S + 300_000_000));
        assert!(state
            .observe(S + 500_000_000, true, 11_000, 10, 10)
            .is_err());
    }
    #[test]
    fn price_only_or_nonincreasing_activity_does_not_open_occurrence() {
        let mut state =
            State::new_historical(config(), "a".repeat(64), S, S + 400_000_000).unwrap();
        assert!(!state.observe(S, true, 10_000, 100, 2).unwrap());
        assert!(!state
            .observe(S + 100_000_000, true, 10_004, 200, 4)
            .unwrap());
        assert!(!state
            .observe(S + 200_000_000, true, 10_100, 200, 5)
            .unwrap());
        assert!(!state
            .observe(S + 300_000_000, true, 10_200, 300, 5)
            .unwrap());
        assert_eq!(state.first_occurrence_end_ns(), None);
        assert!(Config {
            minimum_move_bps: 0,
            ..config()
        }
        .hash()
        .is_err());
    }
    #[test]
    fn live_availability_is_required_and_recovery_keeps_both_clocks() {
        let scope = "b".repeat(64);
        let mut live = State::new_live(config(), scope.clone(), S, S + 400_000_000).unwrap();
        assert!(live.observe(S, true, 10_000, 100, 2).is_err());
        assert!(live
            .observe_live(S, true, 10_000, 100, 2, S + 50_000_000)
            .is_err());
        assert!(!live
            .observe_live(S, true, 10_000, 100, 2, S + 200_000_000)
            .unwrap());
        let checkpoint = live.checkpoint().unwrap();
        let mut restored = State::restore(
            &checkpoint,
            &scope,
            config(),
            S,
            S + 400_000_000,
            Mode::Live,
        )
        .unwrap();
        assert!(State::restore(
            &checkpoint,
            &"c".repeat(64),
            config(),
            S,
            S + 400_000_000,
            Mode::Live
        )
        .is_err());
        assert!(State::restore(
            &checkpoint,
            &scope,
            config(),
            S,
            S + 400_000_000,
            Mode::Historical
        )
        .is_err());
        assert!(restored
            .observe_live(S + 100_000_000, true, 10_005, 101, 3, S + 199_000_000)
            .is_err());
        assert!(restored
            .observe_live(S + 100_000_000, true, 10_005, 101, 3, S + 300_000_000)
            .unwrap());
        assert_eq!(
            restored.first_occurrence(),
            Some(Occurrence {
                event_time_ns: S + 200_000_000,
                available_at_ns: Some(S + 300_000_000),
            })
        );
        assert!(live
            .observe_live(S + 100_000_000, true, 10_005, 101, 3, S + 300_000_000)
            .unwrap());
        assert_eq!(
            live.checkpoint().unwrap().id,
            restored.checkpoint().unwrap().id
        );
        let mut corrupt = restored.checkpoint().unwrap();
        corrupt.payload[0] ^= 1;
        assert!(
            State::restore(&corrupt, &scope, config(), S, S + 400_000_000, Mode::Live).is_err()
        );
        let mut invalid: serde_json::Value =
            serde_json::from_slice(&restored.checkpoint().unwrap().payload).unwrap();
        invalid["next_bucket_ns"] = serde_json::json!(S + 100_000_000);
        let rehashed = Object::new(serde_json::to_vec(&invalid).unwrap());
        assert!(
            State::restore(&rehashed, &scope, config(), S, S + 400_000_000, Mode::Live).is_err()
        );
    }
}
