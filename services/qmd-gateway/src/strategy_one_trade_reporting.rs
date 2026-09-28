//! Strategy 1 live producer parity with ARTE trade_reporting_v1.
//!
//! This is not the legacy QMD bar eligibility rule. The producer-owned 100 ms
//! liquidity product uses this classification before reducing compact events;
//! existing chart/scanner streams retain their prior behavior.

use chrono::{DateTime, Utc};
use chrono_tz::America::New_York;

pub const EVALUATED: u8 = 0x40;
pub const DELAYED: u8 = 0x80;
pub const REVISION: &str = "trade_reporting_v1_conditions_5_13_30_31_32_33_prior_date_lag_gt10s";
const DELAYED_CONDITIONS: [u16; 6] = [5, 13, 30, 31, 32, 33];
const LAG_NS: i64 = 10_000_000_000;

/// Bit 1: explicit reporting condition; bit 2: prior New York date;
/// bit 4: report lag strictly greater than ten seconds; bit 8: unknown clock.
pub fn reporting_reason(
    conditions: &[u16],
    participant: Option<DateTime<Utc>>,
    sip: DateTime<Utc>,
) -> u8 {
    let explicit = u8::from(
        conditions
            .iter()
            .any(|code| DELAYED_CONDITIONS.contains(code)),
    );
    let (Some(participant), Some(sip_ns)) = (participant, sip.timestamp_nanos_opt()) else {
        return explicit | 8;
    };
    let Some(participant_ns) = participant.timestamp_nanos_opt() else {
        return explicit | 8;
    };
    if participant_ns <= 0 || participant_ns > sip_ns {
        return explicit | 8;
    }
    let prior_date = participant.with_timezone(&New_York).date_naive()
        < sip.with_timezone(&New_York).date_naive();
    explicit | (u8::from(prior_date) << 1) | (u8::from(sip_ns - participant_ns > LAG_NS) << 2)
}

/// Preserve the canonical unknown-clock distinction: absent evidence is not
/// proof of a timely report. Explicit delayed conditions still exclude it.
pub fn reporting_flags(reason: u8) -> u8 {
    if reason & 7 != 0 {
        EVALUATED | DELAYED
    } else if reason & 8 != 0 {
        0
    } else {
        EVALUATED
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Timelike;

    fn at(iso: &str) -> DateTime<Utc> {
        DateTime::parse_from_rfc3339(iso)
            .unwrap()
            .with_timezone(&Utc)
    }

    #[test]
    fn timely_opening_trade_is_not_delayed() {
        let sip = at("2026-08-18T08:01:00Z"); // 04:01 ET
        let reason = reporting_reason(&[0], Some(sip), sip);
        assert_eq!(reason, 0);
        assert_eq!(reporting_flags(reason), EVALUATED);
    }

    #[test]
    fn explicit_condition_overrides_unknown_clock() {
        let reason = reporting_reason(&[5], None, at("2026-08-18T08:01:00Z"));
        assert_eq!(reason, 9);
        assert_eq!(reporting_flags(reason), EVALUATED | DELAYED);
        assert_eq!(
            reporting_flags(reporting_reason(&[], None, at("2026-08-18T08:01:00Z"))),
            0
        );
    }

    #[test]
    fn lag_is_strictly_greater_than_ten_seconds() {
        let sip = at("2026-08-18T08:01:10Z");
        assert_eq!(
            reporting_reason(&[], Some(at("2026-08-18T08:01:00Z")), sip),
            0
        );
        let late = sip.with_nanosecond(1).unwrap();
        assert_eq!(
            reporting_reason(&[], Some(at("2026-08-18T08:01:00Z")), late),
            4
        );
        assert_eq!(reporting_flags(4), EVALUATED | DELAYED);
    }

    #[test]
    fn prior_new_york_date_is_delayed_even_with_short_lag() {
        let reason = reporting_reason(
            &[],
            Some(at("2026-08-18T03:59:59Z")),
            at("2026-08-18T04:00:00Z"),
        );
        assert_eq!(reason, 2);
        assert_eq!(reporting_flags(reason), EVALUATED | DELAYED);
    }

    #[test]
    fn future_execution_clock_is_unknown_not_timely() {
        let sip = at("2026-08-18T08:01:00Z");
        assert_eq!(
            reporting_reason(&[], Some(at("2026-08-18T08:01:01Z")), sip),
            8
        );
        assert_eq!(reporting_flags(8), 0);
    }
}
