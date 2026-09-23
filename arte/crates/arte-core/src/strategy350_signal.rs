//! First-session Early Squeeze activation from certified completed 100 ms bars.
//! The first impulse is an occurrence; session-watch membership then latches.
//! This historical clock is the bar close, not a fabricated live receipt time.
use crate::{
    bar_catalogue::{Column, Complete, BASE_INTERVAL_NS},
    boolean_compute::Evaluation,
    content_hash, Error, Result,
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

#[derive(Clone, Copy)]
struct Previous {
    close: i64,
    volume: i64,
    trades: u64,
}
pub struct State {
    config: Config,
    next_bucket_ns: u64,
    session_end_ns: u64,
    previous: Option<Previous>,
    first_occurrence_end_ns: Option<u64>,
}
impl State {
    pub fn new(config: Config, session_start_ns: u64, session_end_ns: u64) -> Result<Self> {
        config.hash()?;
        if session_start_ns == 0
            || session_start_ns >= session_end_ns
            || !session_start_ns.is_multiple_of(BASE_INTERVAL_NS)
            || !session_end_ns.is_multiple_of(BASE_INTERVAL_NS)
        {
            return Err(Error::Invalid("early-squeeze session grid".into()));
        }
        Ok(Self {
            config,
            next_bucket_ns: session_start_ns,
            session_end_ns,
            previous: None,
            first_occurrence_end_ns: None,
        })
    }
    pub fn first_occurrence_end_ns(&self) -> Option<u64> {
        self.first_occurrence_end_ns
    }
    pub fn observe(
        &mut self,
        bucket_start_ns: u64,
        present: bool,
        close: i64,
        volume: i64,
        trades: u64,
    ) -> Result<bool> {
        if bucket_start_ns != self.next_bucket_ns
            || bucket_start_ns >= self.session_end_ns
            || (present && (close <= 0 || volume < 0 || trades == 0))
            || (!present && (close != 0 || volume != 0 || trades != 0))
        {
            return Err(Error::Conflict(
                "early-squeeze bucket order or values".into(),
            ));
        }
        let end_ns = bucket_start_ns + BASE_INTERVAL_NS;
        if present {
            let impulse = self.previous.is_some_and(|prior| {
                (close as i128) * 10_000
                    >= (prior.close as i128) * (10_000 + self.config.minimum_move_bps as i128)
                    && trades > prior.trades
                    && volume > prior.volume
            });
            if impulse && self.first_occurrence_end_ns.is_none() {
                self.first_occurrence_end_ns = Some(end_ns);
            }
            self.previous = Some(Previous {
                close,
                volume,
                trades,
            });
        }
        self.next_bucket_ns = end_ns;
        Ok(self.first_occurrence_end_ns.is_some())
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
    let mut state = State::new(config, session_start_ns, request.interval.end)?;
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
        let mut state = State::new(config(), S, S + 600_000_000).unwrap();
        assert!(!state.observe(S, true, 10_000, 100, 2).unwrap());
        assert!(!state.observe(S + 100_000_000, false, 0, 0, 0).unwrap());
        // Exactly 0.05% rise, with both trade count and volume increasing.
        assert!(state
            .observe(S + 200_000_000, true, 10_005, 101, 3)
            .unwrap());
        assert_eq!(state.first_occurrence_end_ns(), Some(S + 300_000_000));
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
        let mut state = State::new(config(), S, S + 400_000_000).unwrap();
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
}
