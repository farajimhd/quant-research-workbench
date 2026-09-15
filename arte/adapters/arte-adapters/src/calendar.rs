//! New York session conversion. Does not infer holidays or previous trading days.
use arte_core::{coverage::Interval, session::Session, Error, Result};
use chrono::{Datelike, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
/// Immutable runtime handle. Construction validates identity and timezone mapping,
/// not the external source's exchange calendar certification.
pub struct PinnedSession {
    session: Session,
    hash: String,
}
impl PinnedSession {
    pub fn new(session: Session, exchange: &str, hash: &str, as_of_ns: u64) -> Result<Self> {
        session.require(hash, as_of_ns)?;
        validate_new_york(&session)?;
        if session.exchange != exchange {
            return Err(Error::Conflict(
                "session exchange differs from instrument reference".into(),
            ));
        }
        Ok(Self {
            session,
            hash: hash.into(),
        })
    }
    pub fn record(&self) -> &Session {
        &self.session
    }
    pub fn hash(&self) -> &str {
        &self.hash
    }
    pub fn order_session(&self, allow_extended: bool) -> Result<arte_core::orders::TradingSession> {
        arte_core::orders::TradingSession::new(
            self.session.clone(),
            self.hash.clone(),
            self.session.available_at_ns,
            allow_extended,
        )
    }
    /// Price/session safety only. Funding, feed health, durable authorization and
    /// broker protection acceptance remain separate required checks.
    pub fn validate_bracket(
        &self,
        order: &arte_core::orders::Bracket,
        now_ns: u64,
        allow_extended: bool,
        bands: Option<&arte_core::orders::Bands>,
        policy: &arte_core::orders::RiskPolicy,
    ) -> Result<()> {
        self.order_session(allow_extended)?
            .validate(order, now_ns, bands, policy)
    }
    pub fn require_regular(&self, scope: arte_core::event_order::Scope, now_ns: u64) -> Result<()> {
        if scope.session != self.session.session || scope.instrument == 0 || scope.provider == 0 {
            return Err(Error::Conflict("runtime session scope mismatch".into()));
        }
        if self.session.phase(&self.hash, now_ns)? != arte_core::session::Phase::Regular {
            return Err(Error::Unready(
                "regular-session admission outside regular hours".into(),
            ));
        }
        Ok(())
    }
}
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
    fn pinned_handle_rechecks_phase_exchange_and_session() {
        let mut local = local(20261127, 20261125);
        local.regular_close = "13:00:00".into();
        let session = local.to_utc().unwrap();
        let hash = arte_core::content_hash(&session).unwrap();
        assert!(PinnedSession::new(session.clone(), "XNAS", &hash, 1).is_err());
        assert!(PinnedSession::new(session.clone(), "XNYS", &hash, 0).is_err());
        let pinned = PinnedSession::new(session.clone(), "XNYS", &hash, 1).unwrap();
        let scope = arte_core::event_order::Scope {
            provider: 1,
            instrument: 1,
            session: session.session,
        };
        assert!(pinned.require_regular(scope, session.regular.start).is_ok());
        assert!(pinned
            .require_regular(scope, session.regular.end - 1)
            .is_ok());
        assert!(pinned
            .require_regular(scope, session.regular.start - 1)
            .is_err());
        assert!(pinned.require_regular(scope, session.regular.end).is_err());
        assert!(pinned
            .require_regular(
                arte_core::event_order::Scope {
                    session: 20261130,
                    ..scope
                },
                session.regular.start
            )
            .is_err());
        assert_eq!(pinned.hash(), hash);
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
    #[test]
    fn bracket_phase_cannot_bypass_regular_bands_or_closed_session() {
        use arte_core::orders::{Bracket, RiskPolicy, Side};
        let session = local(20261127, 20261125).to_utc().unwrap();
        let hash = arte_core::content_hash(&session).unwrap();
        let pinned = PinnedSession::new(session.clone(), "XNYS", &hash, 1).unwrap();
        let mut order = Bracket {
            command_id: "entry".into(),
            account: "paper".into(),
            instrument: 1,
            side: Side::Long,
            quantity: 1,
            entry: 1000,
            price_scale: 2,
            stop: Some(950),
            target: Some(1050),
            tick: 1,
            deadline_ns: session.extended.end + 1,
        };
        let mut policy = RiskPolicy {
            band_provider: 1,
            band_session: session.session,
            band_buffer_ticks: 3,
            max_band_age_ns: 1_000_000_000,
        };
        let bands = arte_core::orders::Bands {
            provider: 1,
            instrument: 1,
            session: session.session,
            lower: 900,
            upper: 1100,
            scale: 2,
            effective_at_ns: session.regular.start,
            available_at_ns: session.regular.start,
            official: true,
        };
        let check = |order: &Bracket, at, extended, bands, policy: &RiskPolicy| {
            pinned.validate_bracket(order, at, extended, bands, policy)
        };
        assert!(check(&order, session.regular.start, true, None, &policy).is_err());
        assert!(check(&order, session.regular.start, false, Some(&bands), &policy).is_ok());
        assert!(check(&order, session.extended.start, false, None, &policy).is_err());
        assert!(check(&order, session.extended.start, true, None, &policy).is_ok());
        assert!(check(&order, session.regular.end, false, None, &policy).is_err());
        assert!(check(&order, session.regular.end, true, None, &policy).is_ok());
        assert!(check(&order, session.extended.end, true, None, &policy).is_err());
        order.stop = None;
        assert!(check(&order, session.extended.start, true, None, &policy).is_err());
        order.stop = Some(950);
        policy.band_session = 20261130;
        assert!(check(&order, session.extended.start, true, None, &policy).is_err());
    }
}
