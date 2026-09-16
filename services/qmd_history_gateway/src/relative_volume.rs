//! Completed-second RVOL denominators. Existing scanner ten-second semantics are unchanged.
use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalEventSource, SourceRevision};
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Timelike, Utc, Weekday};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::path::PathBuf;

pub const CONTRACT: &str = "session-relative-volume-baseline-1";
pub const HASH_CONTRACT: &str = "typed-json-sha256-1";
const SECONDS: usize = 57_600;

// Cache exact f64 bits rather than round-tripping volume through JSON numbers.
// Each daily artifact retains the complete original source-revision scope.
#[derive(Deserialize, Serialize)]
struct DailyVolume {
    contract: String,
    date: NaiveDate,
    ticker: String,
    source_start: DateTime<Utc>,
    source_end: DateTime<Utc>,
    source_tickers: Vec<String>,
    source_revision: SourceRevision,
    buckets: Vec<(usize, u64, u64)>,
}

struct PendingDaily(Vec<(PathBuf, PathBuf)>);
impl Drop for PendingDaily {
    fn drop(&mut self) {
        for (pending, _) in &self.0 {
            let _ = std::fs::remove_file(pending);
        }
    }
}

fn same_revision(a: &SourceRevision, b: &SourceRevision) -> bool {
    a.complete_for_history
        && a.request_complete
        && b.complete_for_history
        && b.request_complete
        && a.token == b.token
        && a.source_plan_hash == b.source_plan_hash
}

fn daily_directory(config: &HistoricalGatewayConfig, date: NaiveDate, ticker: &str) -> PathBuf {
    config
        .prepared_bar_cache_root
        .join("session-volume-v1")
        .join(date.to_string())
        .join(ticker)
}

async fn cached_daily(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    date: NaiveDate,
    ticker: &str,
    checked: &mut BTreeMap<String, bool>,
) -> Result<Option<Vec<(usize, f64, u64)>>, String> {
    let directory = daily_directory(config, date, ticker);
    let entries = match std::fs::read_dir(directory) {
        Ok(entries) => entries,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(format!("RVOL daily cache read failed: {e}")),
    };
    for entry in entries {
        let entry = entry.map_err(|e| e.to_string())?;
        let path = entry.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") {
            continue;
        }
        if entry.metadata().map_err(|e| e.to_string())?.len() > 4 * 1024 * 1024 {
            continue;
        }
        let bytes = std::fs::read(&path).map_err(|e| e.to_string())?;
        let hash = format!("{:x}", Sha256::digest(&bytes));
        if path.file_stem().and_then(|s| s.to_str()) != Some(hash.as_str()) {
            continue;
        }
        let Ok(cache) = serde_json::from_slice::<DailyVolume>(&bytes) else {
            continue;
        };
        if cache.contract != "session-volume-daily-1"
            || cache.date != date
            || cache.ticker != ticker
            || !cache.source_tickers.iter().any(|t| t == ticker)
            || cache.source_start > boundary(date, 4)?
            || cache.source_end < boundary(date, 20)?
            || cache.buckets.len() > SECONDS
            || cache.buckets.windows(2).any(|v| v[0].0 >= v[1].0)
            || cache.buckets.iter().any(|&(i, bits, _)| {
                i == 0
                    || i > SECONDS
                    || !f64::from_bits(bits).is_finite()
                    || f64::from_bits(bits) < 0.
            })
        {
            continue;
        }
        let key = serde_json::to_string(&(
            &cache.source_start,
            &cache.source_end,
            &cache.source_tickers,
            &cache.source_revision.token,
            &cache.source_revision.source_plan_hash,
        ))
        .map_err(|e| e.to_string())?;
        let valid = if let Some(valid) = checked.get(&key) {
            *valid
        } else {
            let current = source
                .source_revision(&EventWindow {
                    start: cache.source_start,
                    end: cache.source_end,
                    tickers: cache.source_tickers.clone(),
                })
                .await?;
            let valid = same_revision(&cache.source_revision, &current);
            checked.insert(key, valid);
            valid
        };
        if valid {
            return Ok(Some(
                cache
                    .buckets
                    .into_iter()
                    .map(|(i, b, n)| (i, f64::from_bits(b), n))
                    .collect(),
            ));
        }
    }
    Ok(None)
}

fn stage_daily(
    config: &HistoricalGatewayConfig,
    cache: &DailyVolume,
    pending: &mut PendingDaily,
) -> Result<(), String> {
    let bytes = serde_json::to_vec(cache).map_err(|e| e.to_string())?;
    let directory = daily_directory(config, cache.date, &cache.ticker);
    std::fs::create_dir_all(&directory).map_err(|e| e.to_string())?;
    let target = directory.join(format!("{:x}.json", Sha256::digest(&bytes)));
    if target.exists() {
        return Ok(());
    }
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let temporary = target.with_extension(format!("{}-{nonce}.pending", std::process::id()));
    pending.0.push((temporary.clone(), target));
    std::fs::write(&temporary, bytes).map_err(|e| e.to_string())?;
    Ok(())
}

fn publish_daily(pending: &PendingDaily) -> Result<(), String> {
    for (temporary, target) in &pending.0 {
        if target.exists() {
            continue;
        }
        std::fs::rename(temporary, target)
            .map_err(|e| format!("RVOL daily cache publish failed: {e}"))?;
    }
    Ok(())
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct BaselineRequest {
    pub session_date: NaiveDate,
    pub tickers: Vec<String>,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct SessionVolumeRequest {
    pub session_date: NaiveDate,
    pub ticker: String,
    pub as_of: DateTime<Utc>,
}

#[derive(Debug, Serialize)]
pub struct SessionVolumeResponse {
    pub contract: &'static str,
    pub session_date: NaiveDate,
    pub ticker: String,
    pub session_start: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub boundary_seconds: u32,
    pub profile: Vec<f64>,
    pub source_revision: SourceRevision,
}

pub async fn session_volume(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: SessionVolumeRequest,
) -> Result<SessionVolumeResponse, String> {
    let tickers = validate_request(&BaselineRequest {
        session_date: request.session_date,
        tickers: vec![request.ticker],
    })?;
    let ticker = tickers.into_iter().next().ok_or("Missing ticker")?;
    let start = boundary(request.session_date, 4)?;
    let end = request
        .as_of
        .with_nanosecond(0)
        .ok_or("Invalid RVOL clock")?;
    if end <= start || end > boundary(request.session_date, 20)? {
        return Err(
            "Session volume requires a completed second after 04:00 and no later than 20:00 ET"
                .into(),
        );
    }
    let window = EventWindow {
        start,
        end,
        tickers: vec![ticker.clone()],
    };
    let revision = source.source_revision(&window).await?;
    if !revision.complete_for_history || !revision.request_complete {
        return Err("RVOL current-session source is incomplete".into());
    }
    let mut checked = BTreeMap::new();
    let buckets =
        match cached_daily(config, source, request.session_date, &ticker, &mut checked).await? {
            Some(buckets) => buckets,
            None => source
                .daily_session_volume(&window)
                .await?
                .into_iter()
                .map(|(_, i, v, n)| (i, v, n))
                .collect(),
        };
    let length = (end - start).num_seconds() as usize + 1;
    let profile = cumulative_profile(buckets, length)?;
    let after = source.source_revision(&window).await?;
    if !same_revision(&revision, &after) {
        return Err("RVOL current-session source changed during load; retry".into());
    }
    Ok(SessionVolumeResponse {
        contract: "session-volume-profile-1",
        session_date: request.session_date,
        ticker,
        session_start: start,
        as_of: end,
        boundary_seconds: 1,
        profile,
        source_revision: revision,
    })
}

fn cumulative_profile(buckets: Vec<(usize, f64, u64)>, length: usize) -> Result<Vec<f64>, String> {
    if length == 0 || length > SECONDS + 1 {
        return Err("Invalid session-volume prefix length".into());
    }
    let mut profile = vec![0.; length];
    for (index, volume, _) in buckets {
        if index == 0 || index > SECONDS || !volume.is_finite() || volume < 0. {
            return Err("Invalid session-volume aggregate".into());
        }
        if index < length {
            profile[index] += volume;
        }
    }
    let mut total = 0.;
    for volume in &mut profile {
        total += *volume;
        if !total.is_finite() {
            return Err("Session volume overflow".into());
        }
        *volume = total;
    }
    Ok(profile)
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
    pub event_count_basis: &'static str,
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
                || s == "."
                || s == ".."
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

#[cfg(test)]
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
    let mut event_count = 0u64;
    let limit = (config.scanner_max_events_per_snapshot as u64).saturating_mul(4);
    let mut checked = BTreeMap::new();
    let mut pending = PendingDaily(Vec::new());
    for &date in &dates {
        let mut daily = BTreeMap::new();
        let mut missing = Vec::new();
        for ticker in &tickers {
            match cached_daily(config, source, date, ticker, &mut checked).await? {
                Some(buckets) => {
                    daily.insert(ticker.clone(), buckets);
                }
                None => missing.push(ticker.clone()),
            }
        }
        if !missing.is_empty() {
            let day_window = EventWindow {
                start: boundary(date, 4)?,
                end: boundary(date, 20)?,
                tickers: missing.clone(),
            };
            for ticker in &missing {
                daily.insert(ticker.clone(), Vec::new());
            }
            for (ticker, index, volume, count) in source.daily_session_volume(&day_window).await? {
                daily
                    .get_mut(&ticker)
                    .ok_or("Unexpected RVOL aggregate ticker")?
                    .push((index, volume, count));
            }
            for ticker in missing {
                let cache = DailyVolume {
                    contract: "session-volume-daily-1".into(),
                    date,
                    ticker: ticker.clone(),
                    source_start: window.start,
                    source_end: window.end,
                    source_tickers: window.tickers.clone(),
                    source_revision: revision.clone(),
                    buckets: daily[&ticker]
                        .iter()
                        .map(|&(i, v, n)| (i, v.to_bits(), n))
                        .collect(),
                };
                stage_daily(config, &cache, &mut pending)?;
            }
        }
        for (ticker, buckets) in daily {
            let profile = profiles
                .get_mut(&ticker)
                .ok_or("Unexpected RVOL aggregate ticker")?;
            for (index, volume, count) in buckets {
                event_count = event_count
                    .checked_add(count)
                    .ok_or("RVOL event count overflow")?;
                if event_count > limit {
                    return Err(format!("RVOL baseline exceeded event_limit={limit}"));
                }
                if index == 0 || index > SECONDS || !volume.is_finite() || volume < 0. {
                    return Err("Invalid RVOL aggregate bucket".into());
                }
                profile[index] += volume;
                if !profile[index].is_finite() {
                    return Err("RVOL volume overflow".into());
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
    publish_daily(&pending)?;
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
        event_count_basis: "session_trade_events",
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
    #[tokio::test]
    #[ignore = "Requires canonical database and explicit RVOL_TEST_DATE/TICKER/AS_OF"]
    async fn canonical_current_session_matches_event_replay() {
        qmd_core::config::load_env_files();
        let config = HistoricalGatewayConfig::from_env();
        let source = HistoricalEventSource::initialize(config.clone())
            .await
            .unwrap();
        let request = SessionVolumeRequest {
            session_date: std::env::var("RVOL_TEST_DATE").unwrap().parse().unwrap(),
            ticker: std::env::var("RVOL_TEST_TICKER").unwrap(),
            as_of: std::env::var("RVOL_TEST_AS_OF").unwrap().parse().unwrap(),
        };
        let response = session_volume(&config, &source, request).await.unwrap();
        let window = EventWindow {
            start: response.session_start,
            end: response.as_of,
            tickers: vec![response.ticker.clone()],
        };
        let mut batches = source
            .stream_ordered_filtered(
                window.clone(),
                25_000,
                response.source_revision.live_continuation_sequence,
                Some(qmd_core::compact_event::TRADE_EVENT_TYPE),
            )
            .unwrap();
        let mut increments = vec![0.; response.profile.len()];
        let rules = source.trade_aggregation_rules();
        let mut count = 0;
        while let Some(batch) = batches.recv().await {
            for compact in batch.unwrap() {
                count += 1;
                assert!(count <= config.scanner_max_events_per_snapshot);
                if let qmd_core::event::MarketEvent::Trade(trade) = source.market_event(&compact) {
                    if rules.resolve(&trade.conditions, trade.ts).update_volume
                        && trade.size.is_finite()
                        && trade.size > 0.
                    {
                        increments[increment_index(trade.ts).unwrap()] += trade.size;
                    }
                }
            }
        }
        let expected = cumulative_profile(
            increments
                .into_iter()
                .enumerate()
                .skip(1)
                .map(|(i, v)| (i, v, 0))
                .collect(),
            response.profile.len(),
        )
        .unwrap();
        assert!(same_revision(
            &response.source_revision,
            &source.source_revision(&window).await.unwrap()
        ));
        assert_eq!(response.profile, expected);
        println!(
            "Canonical replay parity: {count} trades, {} completed-second values",
            response.profile.len()
        );
    }
    #[test]
    fn chart_prefix_does_not_expose_cached_future_volume() {
        let buckets = vec![(1, 2.5, 1), (3, 10., 1), (10, 1000., 1)];
        assert_eq!(
            cumulative_profile(buckets, 4).unwrap(),
            vec![0., 2.5, 2.5, 12.5]
        );
        assert!(cumulative_profile(vec![(1, f64::NAN, 1)], 4).is_err());
        assert!(cumulative_profile(vec![(0, 1., 1)], 4).is_err());
    }

    #[test]
    fn daily_cache_preserves_exact_volume_bits_and_revision_scope() {
        let date = "2026-08-21".parse().unwrap();
        let revision = SourceRevision {
            complete_for_history: true,
            request_complete: true,
            event_count: 1,
            live_continuation_sequence: None,
            max_build_step: 1,
            max_updated_at: "revision-time".into(),
            source_plan_hash: "plan".into(),
            source_tiers: vec!["archive".into()],
            token: "token".into(),
        };
        let cache = DailyVolume {
            contract: "session-volume-daily-1".into(),
            date,
            ticker: "TEST".into(),
            source_start: boundary(date, 4).unwrap(),
            source_end: boundary(date, 20).unwrap(),
            source_tickers: vec!["TEST".into(), "OTHER".into()],
            source_revision: revision.clone(),
            buckets: vec![(1, 1.2345678901234567f64.to_bits(), 1)],
        };
        let loaded: DailyVolume =
            serde_json::from_slice(&serde_json::to_vec(&cache).unwrap()).unwrap();
        assert_eq!(loaded.buckets, cache.buckets);
        assert_eq!(loaded.source_tickers, cache.source_tickers);
        assert!(same_revision(&revision, &loaded.source_revision));
        let mut changed = revision.clone();
        changed.source_plan_hash = "different".into();
        assert!(!same_revision(&revision, &changed));
        changed = revision.clone();
        changed.request_complete = false;
        assert!(!same_revision(&revision, &changed));
    }
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
