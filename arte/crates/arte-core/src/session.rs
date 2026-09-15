//! Pinned session geometry. Calendar ingestion owns holidays and UTC conversion.
use crate::{content_hash, coverage::Interval, Error, Result};
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Session {
    pub exchange: String,
    pub session: u32,
    pub previous_trading_session: u32,
    pub extended: Interval,
    pub regular: Interval,
    pub available_at_ns: u64,
    pub source_manifest_hash: String,
}
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum Phase {
    Closed,
    Premarket,
    Regular,
    Postmarket,
}
fn hash_valid(hash: &str) -> bool {
    hash.len() == 64
        && hash
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
}
fn date_valid(value: u32) -> bool {
    let year = value / 10000;
    let month = value / 100 % 100;
    let day = value % 100;
    let leap = year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400));
    let days = match month {
        1 | 3 | 5 | 7 | 8 | 10 | 12 => 31,
        4 | 6 | 9 | 11 => 30,
        2 if leap => 29,
        2 => 28,
        _ => 0,
    };
    (1900..=2999).contains(&year) && day > 0 && day <= days
}
impl Session {
    pub fn validate(&self) -> Result<()> {
        self.extended.validate()?;
        self.regular.validate()?;
        if self.exchange.is_empty()
            || self.exchange.len() > 32
            || !self
                .exchange
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'_')
            || !date_valid(self.session)
            || !date_valid(self.previous_trading_session)
            || self.previous_trading_session >= self.session
            || self.regular.start < self.extended.start
            || self.regular.end > self.extended.end
            || self.extended.end - self.extended.start > 24 * 60 * 60 * 1_000_000_000
            || !hash_valid(&self.source_manifest_hash)
        {
            return Err(Error::Invalid("session geometry or provenance".into()));
        }
        Ok(())
    }
    pub fn require(&self, expected_hash: &str, as_of_ns: u64) -> Result<()> {
        self.validate()?;
        if !hash_valid(expected_hash)
            || self.available_at_ns > as_of_ns
            || content_hash(self)? != expected_hash
        {
            return Err(Error::Unready(
                "session unavailable or differs from pinned calendar".into(),
            ));
        }
        Ok(())
    }
    pub fn phase(&self, expected_hash: &str, at_ns: u64) -> Result<Phase> {
        self.require(expected_hash, at_ns)?;
        Ok(
            if at_ns < self.extended.start || at_ns >= self.extended.end {
                Phase::Closed
            } else if at_ns < self.regular.start {
                Phase::Premarket
            } else if at_ns < self.regular.end {
                Phase::Regular
            } else {
                Phase::Postmarket
            },
        )
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    fn session() -> Session {
        Session {
            exchange: "XNYS".into(),
            session: 20261127,
            previous_trading_session: 20261125,
            extended: Interval {
                start: 100,
                end: 500,
            },
            regular: Interval {
                start: 200,
                end: 300,
            },
            available_at_ns: 50,
            source_manifest_hash: "a".repeat(64),
        }
    }
    #[test]
    fn explicit_early_close_and_half_open_boundaries() {
        let s = session();
        let hash = content_hash(&s).unwrap();
        for (at, phase) in [
            (99, Phase::Closed),
            (100, Phase::Premarket),
            (199, Phase::Premarket),
            (200, Phase::Regular),
            (299, Phase::Regular),
            (300, Phase::Postmarket),
            (499, Phase::Postmarket),
            (500, Phase::Closed),
        ] {
            assert_eq!(s.phase(&hash, at).unwrap(), phase);
        }
        assert!(s.phase(&hash, 49).is_err());
        let mut changed = s;
        changed.regular.end = 400;
        assert!(changed.phase(&hash, 350).is_err());
    }
    #[test]
    fn invalid_containment_dates_and_provenance_rejected() {
        for case in 0..6 {
            let mut s = session();
            match case {
                0 => s.regular.end = 501,
                1 => s.regular.start = 99,
                2 => s.previous_trading_session = s.session,
                3 => s.source_manifest_hash.clear(),
                4 => s.exchange = "'".into(),
                _ => s.previous_trading_session = 20260230,
            }
            assert!(s.validate().is_err());
        }
        assert!(date_valid(20000229));
        assert!(!date_valid(19000229));
        assert!(!date_valid(20260001));
    }
}
