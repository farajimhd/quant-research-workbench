//! New York session conversion. Does not infer holidays or previous trading days.
use arte_core::{coverage::Interval, session::Session, Error, Result};
use chrono::{Datelike, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
#[derive(Debug, Clone, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct LocalSession {
    pub exchange: String,
    pub session: u32,
    pub previous_trading_session: u32,
    pub extended_open: String,
    pub regular_open: String,
    pub regular_close: String,
    pub extended_close: String,
    pub available_at_ns: u64,
    pub source_manifest_hash: String,
}
impl LocalSession {
    /// Ambiguous/nonexistent local times fail; no DST guess is made.
    pub fn to_utc(&self) -> Result<Session> {
        let date = NaiveDate::from_ymd_opt(
            (self.session / 10000) as i32,
            self.session / 100 % 100,
            self.session % 100,
        )
        .ok_or_else(|| Error::Invalid("calendar session date".into()))?;
        let convert = |clock: &str| -> Result<u64> {
            let time = NaiveTime::parse_from_str(clock, "%H:%M:%S")
                .map_err(|_| Error::Invalid("calendar local clock".into()))?;
            let at = New_York
                .from_local_datetime(&date.and_time(time))
                .single()
                .ok_or_else(|| Error::Invalid("ambiguous or nonexistent New York time".into()))?;
            let ns = at
                .timestamp_nanos_opt()
                .ok_or_else(|| Error::Invalid("calendar timestamp overflow".into()))?;
            u64::try_from(ns).map_err(|_| Error::Invalid("calendar timestamp before epoch".into()))
        };
        let session = Session {
            exchange: self.exchange.clone(),
            session: self.session,
            previous_trading_session: self.previous_trading_session,
            extended: Interval {
                start: convert(&self.extended_open)?,
                end: convert(&self.extended_close)?,
            },
            regular: Interval {
                start: convert(&self.regular_open)?,
                end: convert(&self.regular_close)?,
            },
            available_at_ns: self.available_at_ns,
            source_manifest_hash: self.source_manifest_hash.clone(),
        };
        validate_new_york(&session)?;
        Ok(session)
    }
}
/// The half-open interval's final included instant must share the declared date.
/// This policy covers same-day US equity sessions, not overnight exchange sessions.
pub fn validate_new_york(session: &Session) -> Result<()> {
    session.validate()?;
    for ns in [
        session.extended.start,
        session.extended.end - 1,
        session.regular.start,
        session.regular.end - 1,
    ] {
        let date = Utc
            .timestamp_opt((ns / 1_000_000_000) as i64, (ns % 1_000_000_000) as u32)
            .single()
            .ok_or_else(|| Error::Invalid("calendar UTC timestamp".into()))?
            .with_timezone(&New_York);
        let day = date.year() as u32 * 10000 + date.month() * 100 + date.day();
        if day != session.session {
            return Err(Error::Conflict(
                "UTC interval differs from New York session date".into(),
            ));
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;
    fn local(date: u32, previous: u32) -> LocalSession {
        LocalSession {
            exchange: "XNYS".into(),
            session: date,
            previous_trading_session: previous,
            extended_open: "04:00:00".into(),
            regular_open: "09:30:00".into(),
            regular_close: "16:00:00".into(),
            extended_close: "20:00:00".into(),
            available_at_ns: 1,
            source_manifest_hash: "a".repeat(64),
        }
    }
    #[test]
    fn dst_and_supplied_early_close_convert_without_fixed_utc_offset() {
        for (date, previous, utc_hour) in [(20260105, 20260102, 14), (20260706, 20260702, 13)] {
            let s = local(date, previous).to_utc().unwrap();
            assert_eq!(
                Utc.timestamp_opt((s.regular.start / 1_000_000_000) as i64, 0)
                    .unwrap()
                    .hour(),
                utc_hour
            );
        }
        let mut early = local(20261127, 20261125);
        early.regular_close = "13:00:00".into();
        let s = early.to_utc().unwrap();
        assert_eq!(s.regular.end - s.regular.start, 12_600 * 1_000_000_000);
        let mut wrong = s;
        wrong.session = 20261128;
        assert!(validate_new_york(&wrong).is_err());
    }
    #[test]
    fn dst_ambiguity_gap_and_invalid_dates_fail() {
        for (date, previous, clock) in [
            (20260308, 20260306, "02:30:00"),
            (20261101, 20261030, "01:30:00"),
        ] {
            let mut s = local(date, previous);
            s.extended_open = clock.into();
            assert!(s.to_utc().is_err());
        }
        assert!(local(20260230, 20260227).to_utc().is_err());
    }
}
