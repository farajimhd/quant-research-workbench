//! Completed-second RVOL denominators. Existing scanner ten-second semantics are unchanged.
use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalEventSource, SourceRevision};
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Timelike, Utc, Weekday};
use chrono_tz::America::New_York;
use qmd_core::event::MarketEvent;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};

pub const CONTRACT: &str = "session-relative-volume-baseline-1";
pub const HASH_CONTRACT: &str = "typed-json-sha256-1";
const SECONDS: usize = 57_600;

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BaselineRequest {
    pub session_date: NaiveDate,
    pub tickers: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct BaselineResponse {
    pub contract: &'static str,
    pub session_date: NaiveDate,
    pub session_start: DateTime<Utc>,
    pub sessions: Vec<NaiveDate>,
    pub session_selection: &'static str,
    pub boundary_seconds: u32,
    pub profiles: BTreeMap<String, Vec<Option<f64>>>,
    pub source_revision: SourceRevision,
    pub event_count: u64,
    pub content_hash: String,
    pub content_hash_contract: &'static str,
}

pub fn validate_request(request: &BaselineRequest) -> Result<BTreeSet<String>, String> {
    if request.tickers.is_empty() || request.tickers.len() > 16 {
        return Err("RVOL baseline requires 1..=16 unique tickers".into());
    }
    let tickers = request
        .tickers
        .iter()
        .map(|s| s.trim().to_ascii_uppercase())
        .collect::<BTreeSet<_>>();
    if tickers.len() != request.tickers.len()
        || tickers.iter().any(|s| {
            s.is_empty()
                || s.len() > 32
                || !s
                    .bytes()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || b"._-".contains(&c))
        })
    {
        return Err("RVOL tickers must be unique valid canonical symbols".into());
    }
    boundary(request.session_date, 4)?;
    Ok(tickers)
}

fn boundary(date: NaiveDate, hour: u32) -> Result<DateTime<Utc>, String> {
    New_York
        .with_ymd_and_hms(date.year(), date.month(), date.day(), hour, 0, 0)
        .single()
        .map(|d| d.with_timezone(&Utc))
        .ok_or_else(|| "Invalid RVOL session boundary".into())
}

fn validate_dates(session: NaiveDate, dates: &[NaiveDate]) -> Result<(), String> {
    if dates.len() != 20 || dates.windows(2).any(|w| w[0] >= w[1]) || dates[19] >= session {
        return Err("RVOL requires 20 distinct ordered prior sessions".into());
    }
    let mut day = dates[0];
    while day < session {
        if !matches!(day.weekday(), Weekday::Sat | Weekday::Sun) && !dates.contains(&day) {
            return Err(format!(
                "RVOL canonical coverage missing weekday {day}; closed-session proof required"
            ));
        }
        day = day.succ_opt().ok_or("Invalid RVOL date range")?;
    }
    Ok(())
}

fn increment_index(at: DateTime<Utc>) -> Option<usize> {
    let seconds = at
        .with_timezone(&New_York)
        .time()
        .num_seconds_from_midnight() as usize;
    // Every trade at t belongs to the first integer boundary strictly after t.
    (4 * 3600..20 * 3600)
        .contains(&seconds)
        .then_some(seconds.saturating_sub(4 * 3600) + 1)
}

fn finish_profile(increments: Vec<f64>) -> Vec<Option<f64>> {
    let mut sum = 0.;
    increments
        .into_iter()
        .map(|value| {
            sum += value;
            (sum > 0.).then_some(sum / 20.)
        })
        .collect()
}

pub async fn load(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: BaselineRequest,
) -> Result<BaselineResponse, String> {
    let tickers = validate_request(&request)?;
    let dates = source
        .relative_volume_session_dates_before(request.session_date)
        .await?;
    validate_dates(request.session_date, &dates)?;
    let window = EventWindow {
        start: boundary(dates[0], 4)?,
        end: boundary(dates[19], 20)?,
        tickers: tickers.iter().cloned().collect(),
    };
    let revision = source.source_revision(&window).await?;
    if !revision.complete_for_history || !revision.request_complete {
        return Err("RVOL canonical baseline source window is incomplete".into());
    }
    let mut profiles = tickers
        .iter()
        .map(|t| (t.clone(), vec![0.; SECONDS + 1]))
        .collect::<BTreeMap<_, _>>();
    let rules = source.trade_aggregation_rules();
    let mut batches = source.stream_ordered(
        window.clone(),
        config.batch_size.clamp(1, 100_000),
        revision.live_continuation_sequence,
    )?;
    let mut event_count = 0u64;
    let limit = (config.scanner_max_events_per_snapshot as u64).saturating_mul(4);
    while let Some(batch) = batches.recv().await {
        for compact in batch? {
            event_count += 1;
            if event_count > limit {
                return Err(format!("RVOL baseline exceeded event_limit={limit}"));
            }
            let event = source.market_event(&compact);
            if let MarketEvent::Trade(trade) = event {
                if !dates.contains(&trade.ts.with_timezone(&New_York).date_naive())
                    || !rules.resolve(&trade.conditions, trade.ts).update_volume
                    || !trade.size.is_finite()
                    || trade.size <= 0.
                {
                    continue;
                }
                if let Some(index) = increment_index(trade.ts) {
                    let profile = profiles
                        .get_mut(&trade.ticker.to_ascii_uppercase())
                        .ok_or("Unexpected RVOL source ticker")?;
                    profile[index] += trade.size;
                    if !profile[index].is_finite() {
                        return Err("RVOL volume overflow".into());
                    }
                }
            }
        }
    }
    let after = source.source_revision(&window).await?;
    if !after.complete_for_history
        || !after.request_complete
        || after.token != revision.token
        || after.source_plan_hash != revision.source_plan_hash
        || source
            .relative_volume_session_dates_before(request.session_date)
            .await?
            != dates
    {
        return Err("RVOL source revision or session selection changed during load; retry".into());
    }
    let mut response = BaselineResponse {
        contract: CONTRACT,
        session_date: request.session_date,
        session_start: boundary(request.session_date, 4)?,
        sessions: dates,
        session_selection: "canonical-continuity-prior20-weekday-gaps-fail-closed-v1",
        boundary_seconds: 1,
        profiles: profiles
            .into_iter()
            .map(|(t, p)| (t, finish_profile(p)))
            .collect(),
        source_revision: revision,
        event_count,
        content_hash: String::new(),
        content_hash_contract: HASH_CONTRACT,
    };
    response.content_hash =
        baseline_content_hash(serde_json::to_value(&response).map_err(|e| e.to_string())?)?;
    Ok(response)
}

/// typed-json-sha256-1: n=null, b+ASCII 0/1=bool, i+length+decimal=int,
/// f+IEEE754 big-endian=float, s+length+UTF8=string, a+count+children=array,
/// o+count+sorted string keys/values=object. Lengths/counts are u64 big-endian.
/// Only root content_hash is blanked. Hash the original to_value f64 values,
/// never reparse serialized JSON (serde_json without float_roundtrip can drift).
fn baseline_content_hash(mut value: serde_json::Value) -> Result<String, String> {
    use serde_json::Value;
    fn emit(hash: &mut Sha256, value: &Value) -> Result<(), String> {
        match value {
            Value::Null => hash.update(b"n"),
            Value::Bool(v) => hash.update(if *v { b"b1" } else { b"b0" }),
            Value::Number(v) if v.is_f64() => {
                hash.update(b"f");
                hash.update(
                    v.as_f64()
                        .ok_or("Invalid hash float")?
                        .to_bits()
                        .to_be_bytes(),
                );
            }
            Value::Number(v) => {
                let text = v.to_string();
                hash.update(b"i");
                hash.update((text.len() as u64).to_be_bytes());
                hash.update(text.as_bytes());
            }
            Value::String(v) => {
                hash.update(b"s");
                hash.update((v.len() as u64).to_be_bytes());
                hash.update(v.as_bytes());
            }
            Value::Array(values) => {
                hash.update(b"a");
                hash.update((values.len() as u64).to_be_bytes());
                for v in values {
                    emit(hash, v)?;
                }
            }
            Value::Object(values) => {
                hash.update(b"o");
                hash.update((values.len() as u64).to_be_bytes());
                let mut keys = values.keys().collect::<Vec<_>>();
                keys.sort();
                for key in keys {
                    emit(hash, &Value::String(key.clone()))?;
                    emit(hash, &values[key])?;
                }
            }
        }
        Ok(())
    }
    value
        .as_object_mut()
        .ok_or("RVOL hash requires object")?
        .insert("content_hash".into(), Value::String(String::new()));
    let mut hash = Sha256::new();
    emit(&mut hash, &value)?;
    Ok(format!("sha256:{:x}", hash.finalize()))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn typed_json_hash_matches_python_golden() {
        let value = serde_json::json!({
            "z": [null, true, false, 0, -7, u64::MAX, 1.0, -0.0, 0.1, f64::from_bits(1), f64::MAX],
            "\u{e9}": "\u{1d11e}",
            "a": {"content_hash": "nested", "x": 1.2345678901234567},
            "content_hash": "ignored"
        });
        assert_eq!(
            baseline_content_hash(value).unwrap(),
            "sha256:a5d7ec2b86adbf614c2d62c180e1d856f6c57b2abb001913be7d99a1f7cdfdf2"
        );
    }
    #[test]
    fn exact_boundary_excludes_trade_at_boundary_and_handles_dst() {
        for day in ["2026-01-12", "2026-08-21"] {
            let start = boundary(day.parse().unwrap(), 4).unwrap();
            assert_eq!(increment_index(start), Some(1));
            assert_eq!(
                increment_index(start + chrono::Duration::milliseconds(999)),
                Some(1)
            );
            assert_eq!(
                increment_index(start + chrono::Duration::seconds(1)),
                Some(2)
            );
            assert_eq!(
                increment_index(start + chrono::Duration::seconds(SECONDS as i64)),
                None
            );
        }
        let mut p = vec![0.; SECONDS + 1];
        p[1] = 20.;
        p[2] = 40.;
        let result = finish_profile(p);
        assert_eq!(&result[..3], &[None, Some(1.), Some(3.)]);
        assert_eq!(result.len(), SECONDS + 1);
    }
    #[test]
    fn missing_weekday_never_compresses_twenty_sessions() {
        let session: NaiveDate = "2026-08-21".parse().unwrap();
        let mut day = session;
        let mut dates = Vec::new();
        while dates.len() < 21 {
            day = day.pred_opt().unwrap();
            if !matches!(day.weekday(), Weekday::Sat | Weekday::Sun) {
                dates.push(day);
            }
        }
        dates.reverse();
        assert!(validate_dates(session, &dates[1..]).is_ok());
        dates.remove(19);
        assert!(validate_dates(session, &dates).is_err());
    }
    #[test]
    fn bounded_unique_symbols() {
        let mut r = BaselineRequest {
            session_date: "2026-08-21".parse().unwrap(),
            tickers: vec!["ABC".into()],
        };
        assert!(validate_request(&r).is_ok());
        r.tickers.push("abc".into());
        assert!(validate_request(&r).is_err());
        r.tickers = vec!["A".into(); 17];
        assert!(validate_request(&r).is_err());
    }
}
