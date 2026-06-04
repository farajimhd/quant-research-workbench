use chrono::{DateTime, Datelike, Timelike, Utc, Weekday};
use chrono_tz::America::New_York;
use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum SessionPhase {
    Premarket,
    Regular,
    Aftermarket,
    Maintenance,
}

pub fn session_phase(now_utc: DateTime<Utc>) -> SessionPhase {
    let local = now_utc.with_timezone(&New_York);
    if matches!(local.weekday(), Weekday::Sat | Weekday::Sun) {
        return SessionPhase::Maintenance;
    }
    let minute = local.hour() * 60 + local.minute();
    match minute {
        240..=569 => SessionPhase::Premarket,
        570..=959 => SessionPhase::Regular,
        960..=1199 => SessionPhase::Aftermarket,
        _ => SessionPhase::Maintenance,
    }
}

pub fn is_streaming_phase(now_utc: DateTime<Utc>) -> bool {
    matches!(
        session_phase(now_utc),
        SessionPhase::Premarket | SessionPhase::Regular | SessionPhase::Aftermarket
    )
}
