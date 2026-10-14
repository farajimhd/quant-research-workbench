//! Read-only windows over centrally sealed, producer-owned elapsed history.
//! The app's cold preflight verifies the complete product and source lineage.
//! This service preserves that pin, checks actual SSD placement, validates the
//! requested window, and never calculates missing features or publishes data.
use crate::source::HistoricalEventSource;
use chrono::{DateTime, Datelike, NaiveDate, TimeZone, Utc};
use chrono_tz::America::New_York;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::BTreeMap;
use std::future::Future;

pub const CONTRACT: &str = "qmd-causal-history-window-v1";
// Wire unit classes must match the shared producer catalog, including V18.
const MARKET_UNITS: [&str; 10] = [
    "price", "ratio", "dollars", "bool", "duration", "log_count",
    "log_dollars", "log_duration", "log_price", "log_shares",
];
const PREFIX: [&str; 8] = [
    "source_build_id",
    "session_date",
    "listing_id",
    "ticker",
    "derivation_attempt_id",
    "product_revision",
    "first_bucket",
    "stop_bucket",
];
const TABLES: [&str; 4] = [
    "qmd_causal_history_field_v1",
    "qmd_causal_history_clock_v1",
    "qmd_causal_history_value_v1",
    "qmd_causal_history_coverage_v1",
];

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryScope {
    pub source_build_id: String,
    pub session_date: String,
    pub listing_id: String,
    pub ticker: String,
    pub derivation_attempt_id: String,
    pub product_revision: String,
    pub first_bucket: u32,
    pub stop_bucket: u32,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryWindowRequest {
    pub scope: HistoryScope,
    pub expected_content_hash: String,
    pub field_ids: Vec<u32>,
    pub first_ordinal: u32,
    pub stop_ordinal: u32,
    pub as_of: DateTime<Utc>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct HistoryCoverage {
    pub source_build_id: String,
    pub session_date: String,
    pub listing_id: String,
    pub ticker: String,
    pub derivation_attempt_id: String,
    pub product_revision: String,
    pub first_bucket: u32,
    pub stop_bucket: u32,
    pub source_bar_attempt_id: String,
    pub source_bar_unit_hash: String,
    pub source_liquidity_attempt_id: String,
    pub source_liquidity_hash: String,
    pub opening_reference_hash: String,
    pub market_plan_hash: String,
    pub producer_code_hash: String,
    pub volume_window_seconds: u32,
    pub elapsed_clock_count: u32,
    pub field_count: u32,
    pub clock_row_count: u32,
    pub value_row_count: u64,
    pub field_content_hash: String,
    pub clock_content_hash: String,
    pub value_content_hash: String,
    pub content_hash: String,
    pub certified_at: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct FieldRow {
    field_id: u32,
    field_name: String,
    unit: String,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ClockRow {
    bucket_index: u32,
    source_known: u8,
    observed: u8,
    extremes_valid: u8,
    reference_close: Option<f64>,
    share_volume: f64,
    execution_notional: f64,
    rolling_volume: f64,
}

#[derive(Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct ValueRow {
    field_id: u32,
    first_ordinal: u32,
    stop_ordinal: u32,
    value_bits: Option<u32>,
    valid: u8,
    bits_consistent: u8,
}

#[derive(Debug, Serialize)]
pub struct FieldIntervals {
    pub field_id: u32,
    pub field_name: String,
    pub unit: String,
    pub first_ordinal: Vec<u32>,
    pub stop_ordinal: Vec<u32>,
    pub value_bits: Vec<Option<u32>>,
    pub valid: Vec<bool>,
}

#[derive(Debug, Serialize)]
pub struct HistoryWindowResponse {
    pub contract: &'static str,
    pub coverage: HistoryCoverage,
    pub first_ordinal: u32,
    pub stop_ordinal: u32,
    pub observed_ordinal: Vec<u32>,
    pub bucket_index: Vec<u32>,
    pub source_known: Vec<bool>,
    pub observed: Vec<bool>,
    pub extremes_valid: Vec<bool>,
    pub reference_close: Vec<Option<f64>>,
    pub share_volume: Vec<f64>,
    pub execution_notional: Vec<f64>,
    pub rolling_volume: Vec<f64>,
    pub fields: Vec<FieldIntervals>,
}

fn hash_valid(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|v| v.is_ascii_digit() || (b'a'..=b'f').contains(&v))
}

fn uuid_valid(value: &str) -> bool {
    value.len() == 36
        && value.bytes().enumerate().all(|(i, v)| {
            if [8, 13, 18, 23].contains(&i) {
                v == b'-'
            } else {
                v.is_ascii_digit() || (b'a'..=b'f').contains(&v)
            }
        })
}

fn text_valid(value: &str, maximum: usize) -> bool {
    !value.is_empty() && value.chars().count() <= maximum && !value.chars().any(char::is_control)
}

pub fn completed_clock(scope: &HistoryScope, bucket: u32) -> Result<DateTime<Utc>, String> {
    let day = NaiveDate::parse_from_str(&scope.session_date, "%Y-%m-%d")
        .map_err(|_| "Invalid history session date")?;
    let four = New_York
        .with_ymd_and_hms(day.year(), day.month(), day.day(), 4, 0, 0)
        .single()
        .ok_or("Ambiguous history session origin")?;
    Ok((four + chrono::Duration::seconds(i64::from(bucket) + 1 - 14400)).with_timezone(&Utc))
}

pub fn validate_request(request: &HistoryWindowRequest) -> Result<(), String> {
    let s = &request.scope;
    let day = NaiveDate::parse_from_str(&s.session_date, "%Y-%m-%d")
        .map_err(|_| "Invalid history session date")?;
    if day.to_string() != s.session_date
        || !text_valid(&s.source_build_id, 128)
        || !text_valid(&s.listing_id, 128)
        || !text_valid(&s.ticker, 32)
        || !s.ticker.bytes().enumerate().all(|(i, v)| {
            v.is_ascii_uppercase()
                || v.is_ascii_digit()
                || (i > 0 && [b'.', b'_', b'-'].contains(&v))
        })
        || !uuid_valid(&s.derivation_attempt_id)
        || !hash_valid(&s.product_revision)
        || !hash_valid(&request.expected_content_hash)
        || s.first_bucket < 14400
        || s.first_bucket >= s.stop_bucket
        || s.stop_bucket > 72000
        || request.first_ordinal >= request.stop_ordinal
        || request.stop_ordinal > s.stop_bucket - s.first_bucket
        || request.stop_ordinal - request.first_ordinal > 4096
        || request.field_ids.len() > 957
        || request.field_ids.iter().any(|v| *v >= 957)
        || request.field_ids.windows(2).any(|v| v[0] >= v[1])
        || request.field_ids.len() * (request.stop_ordinal - request.first_ordinal) as usize > 65536
    {
        return Err("History request exceeds its explicit typed window contract".into());
    }
    if completed_clock(s, s.first_bucket + request.stop_ordinal - 1)? > request.as_of {
        return Err("History window extends beyond its completed as-of clock".into());
    }
    Ok(())
}

fn literal(value: &str) -> String {
    format!("'{}'", value.replace('\\', "\\\\").replace('\'', "\\'"))
}

fn scope_sql(scope: &HistoryScope, attempt: bool) -> String {
    let mut result = format!(
        "source_build_id={} AND session_date=toDate({}) AND listing_id={} AND ticker={} \
        AND product_revision={} AND first_bucket={} AND stop_bucket={}",
        literal(&scope.source_build_id),
        literal(&scope.session_date),
        literal(&scope.listing_id),
        literal(&scope.ticker),
        literal(&scope.product_revision),
        scope.first_bucket,
        scope.stop_bucket
    );
    if attempt {
        result.push_str(&format!(
            " AND derivation_attempt_id=toUUID({})",
            literal(&scope.derivation_attempt_id)
        ));
    }
    result
}

fn rows<T: for<'de> Deserialize<'de>>(text: &str, maximum: usize) -> Result<Vec<T>, String> {
    if text.len() > 8 * 1024 * 1024 {
        return Err("History query exceeds its byte bound".into());
    }
    let mut result = Vec::new();
    for line in text.lines().filter(|v| !v.trim().is_empty()) {
        if result.len() >= maximum {
            return Err("History query exceeds its row bound".into());
        }
        result.push(
            serde_json::from_str(line)
                .map_err(|_| "History query returned an invalid typed row")?,
        );
    }
    Ok(result)
}

fn header_hash(header: &HistoryCoverage) -> Result<String, String> {
    let mut value = serde_json::to_value(header).map_err(|e| e.to_string())?;
    let object = value
        .as_object_mut()
        .ok_or("Invalid history coverage object")?;
    object.remove("content_hash");
    object.remove("certified_at");
    let text = serde_json::to_string(&value).map_err(|e| e.to_string())?;
    // Match the producer's deterministic Python ensure_ascii serialization.
    let mut ascii = String::new();
    for c in text.chars() {
        if c.is_ascii() {
            ascii.push(c);
        } else {
            let mut buffer = [0; 2];
            for unit in c.encode_utf16(&mut buffer).iter() {
                ascii.push_str(&format!("\\u{unit:04x}"));
            }
        }
    }
    Ok(format!("{:x}", Sha256::digest(ascii.as_bytes())))
}

fn validate_header(header: &HistoryCoverage, request: &HistoryWindowRequest) -> Result<(), String> {
    let actual = serde_json::to_value(header).map_err(|e| e.to_string())?;
    let expected = serde_json::to_value(&request.scope).map_err(|e| e.to_string())?;
    if PREFIX.iter().any(|key| actual[key] != expected[key])
        || header.content_hash != request.expected_content_hash
        || header_hash(header)? != header.content_hash
        || !uuid_valid(&header.source_bar_attempt_id)
        || !uuid_valid(&header.source_liquidity_attempt_id)
        || [
            &header.source_bar_unit_hash,
            &header.source_liquidity_hash,
            &header.opening_reference_hash,
            &header.market_plan_hash,
            &header.producer_code_hash,
            &header.field_content_hash,
            &header.clock_content_hash,
            &header.value_content_hash,
        ]
        .iter()
        .any(|v| !hash_valid(v))
        || header.elapsed_clock_count != request.scope.stop_bucket - request.scope.first_bucket
        || header.clock_row_count != header.elapsed_clock_count
        || header.field_count > 957
        || (header.field_count == 0
            && (header.field_content_hash != format!("{:x}", Sha256::digest([]))
                || header.value_content_hash != format!("{:x}", Sha256::digest([]))))
        || header.field_count < request.field_ids.len() as u32
        || !(1..=3600).contains(&header.volume_window_seconds)
        || header.value_row_count < u64::from(header.field_count)
        || header.value_row_count
            > u64::from(header.field_count) * u64::from(header.elapsed_clock_count)
    {
        return Err("History coverage differs from the pinned product".into());
    }
    Ok(())
}

pub async fn load(
    source: &HistoricalEventSource,
    request: HistoryWindowRequest,
) -> Result<HistoryWindowResponse, String> {
    load_with(request, |sql| {
        let source = source.clone();
        async move { source.query_columnar_history(&sql).await }
    })
    .await
}

async fn load_with<F, Fut>(
    request: HistoryWindowRequest,
    query: F,
) -> Result<HistoryWindowResponse, String>
where
    F: Fn(String) -> Fut,
    Fut: Future<Output = Result<String, String>>,
{
    validate_request(&request)?;
    let tables = TABLES
        .iter()
        .map(|v| literal(v))
        .collect::<Vec<_>>()
        .join(",");
    let preflight = query(format!("SELECT \
      (SELECT count() FROM system.storage_policies WHERE policy_name='live_market_ssd' AND disks=['live_market_ssd']) AS policy_count,\
      (SELECT count() FROM system.tables WHERE database='q_live' AND name IN ({tables}) AND engine='MergeTree' AND storage_policy='live_market_ssd') AS table_count,\
      (SELECT count() FROM system.parts WHERE database='q_live' AND table IN ({tables}) AND active AND disk_name!='live_market_ssd') AS bad_parts FORMAT JSONEachRow")).await?;
    #[derive(Deserialize)]
    struct Placement {
        policy_count: u64,
        table_count: u64,
        bad_parts: u64,
    }
    let placement = rows::<Placement>(&preflight, 1)?;
    if placement.len() != 1
        || placement[0].policy_count != 1
        || placement[0].table_count != 4
        || placement[0].bad_parts != 0
    {
        return Err(
            "History product requires certified SSD policy and actual part placement".into(),
        );
    }
    let predicate = scope_sql(&request.scope, true);
    // The coverage query omits the attempt so a conflicting successor seal
    // cannot hide behind the request's pinned attempt ID.
    let seal_predicate = scope_sql(&request.scope, false);
    let seal_sql = format!("SELECT * FROM q_live.qmd_causal_history_coverage_v1 WHERE {seal_predicate} LIMIT 2 FORMAT JSONEachRow");
    let before = query(seal_sql.clone()).await?;
    let mut headers = rows::<HistoryCoverage>(&before, 2)?;
    if headers.len() != 1 {
        return Err("History coverage is missing or ambiguous".into());
    }
    let header = headers.remove(0);
    validate_header(&header, &request)?;
    let ids = request
        .field_ids
        .iter()
        .map(u32::to_string)
        .collect::<Vec<_>>()
        .join(",");
    let n = (request.stop_ordinal - request.first_ordinal) as usize;
    let first_bucket = request.scope.first_bucket + request.first_ordinal;
    let stop_bucket = request.scope.first_bucket + request.stop_ordinal;
    let field_sql = format!("SELECT field_id,field_name,unit FROM q_live.qmd_causal_history_field_v1 WHERE {predicate} AND field_id IN ({ids}) ORDER BY field_id LIMIT {} FORMAT JSONEachRow", request.field_ids.len() + 1);
    let clock_sql = format!("SELECT bucket_index,source_known,observed,extremes_valid,reference_close,share_volume,execution_notional,rolling_volume FROM q_live.qmd_causal_history_clock_v1 WHERE {predicate} AND bucket_index>={first_bucket} AND bucket_index<{stop_bucket} ORDER BY bucket_index LIMIT {} FORMAT JSONEachRow", n + 1);
    let value_sql = format!("SELECT field_id,first_ordinal,stop_ordinal,value_bits,valid,\
      toUInt8(if(valid=0,isNull(value) AND isNull(value_bits),\
      isNotNull(value) AND isNotNull(value_bits) AND reinterpretAsUInt32(ifNull(value,toFloat32(0)))=ifNull(value_bits,toUInt32(0)))) AS bits_consistent \
      FROM q_live.qmd_causal_history_value_v1 WHERE {predicate} AND field_id IN ({ids}) \
      AND first_ordinal<{} AND stop_ordinal>{} ORDER BY field_id,first_ordinal LIMIT {} FORMAT JSONEachRow", request.stop_ordinal, request.first_ordinal, request.field_ids.len() * n + 1);
    // A certified activity-only product carries dense clocks and zero fields.
    // Do not issue feature reads or derive private features for that population.
    let (fields, clocks, values) = if request.field_ids.is_empty() {
        (String::new(), query(clock_sql).await?, String::new())
    } else {
        tokio::try_join!(query(field_sql), query(clock_sql), query(value_sql))?
    };
    let fields = rows::<FieldRow>(&fields, request.field_ids.len() + 1)?;
    let clocks = rows::<ClockRow>(&clocks, n + 1)?;
    let values = rows::<ValueRow>(&values, request.field_ids.len() * n + 1)?;
    let response = assemble(request, header, fields, clocks, values)?;
    if query(seal_sql).await? != before {
        return Err("History coverage changed during window read".into());
    }
    Ok(response)
}

fn assemble(
    request: HistoryWindowRequest,
    header: HistoryCoverage,
    fields: Vec<FieldRow>,
    clocks: Vec<ClockRow>,
    values: Vec<ValueRow>,
) -> Result<HistoryWindowResponse, String> {
    let n = (request.stop_ordinal - request.first_ordinal) as usize;
    if fields.iter().map(|v| v.field_id).collect::<Vec<_>>() != request.field_ids
        || clocks.len() != n
    {
        return Err("History window has missing or duplicate fields or clocks".into());
    }
    let mut result = HistoryWindowResponse {
        contract: CONTRACT,
        coverage: header,
        first_ordinal: request.first_ordinal,
        stop_ordinal: request.stop_ordinal,
        observed_ordinal: (request.first_ordinal..request.stop_ordinal).collect(),
        bucket_index: Vec::with_capacity(n),
        source_known: Vec::with_capacity(n),
        observed: Vec::with_capacity(n),
        extremes_valid: Vec::with_capacity(n),
        reference_close: Vec::with_capacity(n),
        share_volume: Vec::with_capacity(n),
        execution_notional: Vec::with_capacity(n),
        rolling_volume: Vec::with_capacity(n),
        fields: Vec::with_capacity(fields.len()),
    };
    for (i, clock) in clocks.into_iter().enumerate() {
        if clock.bucket_index != request.scope.first_bucket + request.first_ordinal + i as u32
            || clock.source_known > 1
            || clock.observed > clock.source_known
            || clock.extremes_valid > clock.observed
            || clock
                .reference_close
                .is_some_and(|v| !v.is_finite() || v <= 0.)
            || (clock.observed == 1 && clock.reference_close.is_none())
            || [
                clock.share_volume,
                clock.execution_notional,
                clock.rolling_volume,
            ]
            .iter()
            .any(|v| !v.is_finite() || *v < 0.)
            || clock.share_volume > 2f64.powi(53)
            || clock.rolling_volume > 2f64.powi(53)
        {
            return Err("History window clock or market validity differs".into());
        }
        result.bucket_index.push(clock.bucket_index);
        result.source_known.push(clock.source_known == 1);
        result.observed.push(clock.observed == 1);
        result.extremes_valid.push(clock.extremes_valid == 1);
        result.reference_close.push(clock.reference_close);
        result.share_volume.push(clock.share_volume);
        result.execution_notional.push(clock.execution_notional);
        result.rolling_volume.push(clock.rolling_volume);
    }
    let mut grouped: BTreeMap<u32, Vec<ValueRow>> = BTreeMap::new();
    for value in values {
        grouped.entry(value.field_id).or_default().push(value);
    }
    if grouped.keys().copied().collect::<Vec<_>>() != request.field_ids {
        return Err("History interval field population differs".into());
    }
    for field in fields {
        if !text_valid(&field.field_name, 256)
            || !MARKET_UNITS.contains(&field.unit.as_str())
        {
            return Err("History field contract differs".into());
        }
        let mut output = FieldIntervals {
            field_id: field.field_id,
            field_name: field.field_name,
            unit: field.unit,
            first_ordinal: Vec::new(),
            stop_ordinal: Vec::new(),
            value_bits: Vec::new(),
            valid: Vec::new(),
        };
        let mut end = request.first_ordinal;
        for (i, value) in grouped
            .remove(&field.field_id)
            .ok_or("Missing history field intervals")?
            .into_iter()
            .enumerate()
        {
            if value.first_ordinal >= value.stop_ordinal
                || value.stop_ordinal > result.coverage.elapsed_clock_count
                || (i == 0 && (value.first_ordinal > end || value.stop_ordinal <= end))
                || (i > 0 && value.first_ordinal != end)
                || value.valid > 1
                || value.bits_consistent != 1
                || ((value.valid == 1) != value.value_bits.is_some())
                || value
                    .value_bits
                    .is_some_and(|bits| !f32::from_bits(bits).is_finite())
            {
                return Err("History interval coverage, bits or validity differs".into());
            }
            end = value.stop_ordinal;
            output.first_ordinal.push(value.first_ordinal);
            output.stop_ordinal.push(value.stop_ordinal);
            output.value_bits.push(value.value_bits);
            output.valid.push(value.valid == 1);
        }
        if end < request.stop_ordinal {
            return Err("History interval window is incomplete".into());
        }
        result.fields.push(output);
    }
    Ok(result)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::sync::{
        atomic::{AtomicUsize, Ordering},
        Arc,
    };

    fn request() -> HistoryWindowRequest {
        HistoryWindowRequest {
            scope: HistoryScope {
                source_build_id: "build-μ🚀".into(),
                session_date: "2026-08-03".into(),
                listing_id: "listing".into(),
                ticker: "ABC".into(),
                derivation_attempt_id: "00000000-0000-0000-0000-000000000001".into(),
                product_revision: "a".repeat(64),
                first_bucket: 34200,
                stop_bucket: 34300,
            },
            expected_content_hash: "a".repeat(64),
            field_ids: vec![326],
            first_ordinal: 0,
            stop_ordinal: 2,
            as_of: "2026-08-03T13:30:02Z".parse().unwrap(),
        }
    }

    fn header(request: &HistoryWindowRequest) -> HistoryCoverage {
        let s = &request.scope;
        let mut result = HistoryCoverage {
            source_build_id: s.source_build_id.clone(),
            session_date: s.session_date.clone(),
            listing_id: s.listing_id.clone(),
            ticker: s.ticker.clone(),
            derivation_attempt_id: s.derivation_attempt_id.clone(),
            product_revision: s.product_revision.clone(),
            first_bucket: s.first_bucket,
            stop_bucket: s.stop_bucket,
            source_bar_attempt_id: s.derivation_attempt_id.clone(),
            source_bar_unit_hash: "a".repeat(64),
            source_liquidity_attempt_id: s.derivation_attempt_id.clone(),
            source_liquidity_hash: "a".repeat(64),
            opening_reference_hash: "a".repeat(64),
            market_plan_hash: "a".repeat(64),
            producer_code_hash: "a".repeat(64),
            volume_window_seconds: 30,
            elapsed_clock_count: 100,
            field_count: 1,
            clock_row_count: 100,
            value_row_count: 100,
            field_content_hash: "a".repeat(64),
            clock_content_hash: "a".repeat(64),
            value_content_hash: "a".repeat(64),
            content_hash: String::new(),
            certified_at: "2026-10-11 12:00:00.000000".into(),
        };
        result.content_hash = header_hash(&result).unwrap();
        result
    }

    #[test]
    fn producer_ascii_hash_matches_python_golden_including_surrogate_pairs() {
        assert_eq!(
            header(&request()).content_hash,
            "19d6de821134667a89e846e8bb7ee1930bd12b3cdaff4f753571d36ab22793b0"
        );
    }

    #[test]
    fn future_unsorted_and_unbounded_requests_are_rejected() {
        let mut r = request();
        assert!(validate_request(&r).is_ok());
        r.as_of = "2026-08-03T13:30:01Z".parse().unwrap();
        assert!(validate_request(&r).is_err());
        r = request();
        r.field_ids = vec![326, 326];
        assert!(validate_request(&r).is_err());
        r = request();
        r.scope.stop_bucket = 72001;
        assert!(validate_request(&r).is_err());
        r = request();
        r.scope.derivation_attempt_id = "not-an-attempt".into();
        assert!(validate_request(&r).is_err());
    }

    #[test]
    fn complete_shared_market_catalog_preserves_transport_bounds() {
        let mut r = request();
        r.field_ids = vec![0, 148, 753, 901, 902, 956];
        assert!(validate_request(&r).is_ok());
        r.field_ids = vec![957];
        assert!(validate_request(&r).is_err());
        r.field_ids = (0..957).collect();
        r.stop_ordinal = 100;
        r.as_of = completed_clock(&r.scope, r.scope.stop_bucket - 1).unwrap();
        assert!(validate_request(&r).is_err()); // finite field x clock budget
        r.stop_ordinal = r.first_ordinal + 1;
        assert!(validate_request(&r).is_ok());
    }

    #[test]
    fn completed_clock_uses_the_declared_post_dst_interval() {
        let mut s = request().scope;
        s.session_date = "2026-03-08".into();
        assert_eq!(
            completed_clock(&s, 34200).unwrap().to_rfc3339(),
            "2026-03-08T13:30:01+00:00"
        );
        s.session_date = "2026-11-01".into();
        assert_eq!(
            completed_clock(&s, 34200).unwrap().to_rfc3339(),
            "2026-11-01T14:30:01+00:00"
        );
    }

    async fn fixture(case: &'static str) -> Result<HistoryWindowResponse, String> {
        let mut r = request();
        let mut h = header(&r);
        if case == "activity-only" {
            r.field_ids.clear();
            h.field_count = 0;
            h.value_row_count = 0;
            h.field_content_hash = format!("{:x}", Sha256::digest([]));
            h.value_content_hash = h.field_content_hash.clone();
            h.content_hash = header_hash(&h).unwrap();
        }
        r.expected_content_hash = h.content_hash.clone();
        let seals = Arc::new(AtomicUsize::new(0));
        let queries = Arc::new(std::sync::Mutex::new(Vec::<String>::new()));
        let collected = queries.clone();
        let result = load_with(r, move |sql| {
            collected.lock().unwrap().push(sql.clone());
            let body = if sql.contains("FROM system.storage_policies") {
                json!({"policy_count":1,"table_count":4,"bad_parts":if case == "disk" {1} else {0}}).to_string()
            } else if sql.contains("FROM q_live.qmd_causal_history_coverage_v1") {
                let count = seals.fetch_add(1, Ordering::SeqCst);
                let body = serde_json::to_string(&h).unwrap();
                if case == "missing-seal" { String::new() }
                else if case == "ambiguous-seal" { format!("{body}\n{body}") }
                else if case == "changing-seal" && count > 0 { format!("{body}\n") }
                else { body }
            } else if sql.contains("FROM q_live.qmd_causal_history_field_v1") {
                json!({"field_id":326,"field_name":"history.attention_mean.58",
                    "unit":case.strip_prefix("unit:").unwrap_or("dollars")}).to_string()
            } else if sql.contains("FROM q_live.qmd_causal_history_clock_v1") {
                (0..2).map(|i| json!({"bucket_index":34200 + if case == "duplicate-clock" {0} else {i},
                    "source_known":1,"observed":1,"extremes_valid":1,"reference_close":10.,
                    "share_volume":if case == "fractional-volume" {1.25} else if case == "negative-volume" {-0.25} else if case == "oversized-volume" {2f64.powi(54)} else {1.},
                    "execution_notional":3.,"rolling_volume":if case == "fractional-volume" {(i+1) as f64 * 1.25} else {(i+1) as f64}}).to_string()).collect::<Vec<_>>().join("\n")
            } else if sql.contains("FROM q_live.qmd_causal_history_value_v1") {
                json!({"field_id":326,"first_ordinal":if case == "interval-gap" {1} else {0},"stop_ordinal":100,
                    "value_bits":if case == "infinite-bits" {0x7f800000u32} else {(-0f32).to_bits()},
                    "valid":1,"bits_consistent":if case == "value-drift" {0} else {1}}).to_string()
            } else { panic!("Unexpected synthetic query {sql}"); };
            async move { Ok(body) }
        }).await;
        if case == "activity-only" {
            assert!(!queries
                .lock()
                .unwrap()
                .iter()
                .any(|sql| sql.contains("FROM q_live.qmd_causal_history_field_v1")
                    || sql.contains("FROM q_live.qmd_causal_history_value_v1")));
        }
        if case == "valid" && std::env::var("QUALIFY_HISTORY_SQL_PROBE").as_deref() == Ok("1") {
            let root = std::path::Path::new("D:/TradingML/runtimes/task-0223-app");
            if !root.is_dir() {
                return Err("History SQL probe runtime root is unavailable".into());
            }
            let data = json!({"source_sha256":format!("{:x}",Sha256::digest(include_bytes!("columnar_history.rs"))),
                "queries":*queries.lock().unwrap()});
            std::fs::write(
                root.join("qmd-columnar-history-select-probe.json"),
                serde_json::to_vec(&data).unwrap(),
            )
            .map_err(|_| "Cannot preserve bounded history SQL probe")?;
        }
        result
    }

    #[tokio::test]
    async fn sealed_inputs_are_returned_as_columns_and_exact_bits() {
        let response = fixture("valid").await.unwrap();
        assert_eq!(response.contract, CONTRACT);
        assert_eq!(response.observed_ordinal, vec![0, 1]);
        assert_eq!(response.bucket_index, vec![34200, 34201]);
        assert_eq!(response.rolling_volume, vec![1., 2.]);
        assert_eq!(response.fields[0].value_bits, vec![Some((-0f32).to_bits())]);
    }

    #[tokio::test]
    async fn activity_only_population_reads_clocks_without_feature_work() {
        let response = fixture("activity-only").await.unwrap();
        assert!(response.fields.is_empty());
        assert_eq!(response.coverage.field_count, 0);
        assert_eq!(response.rolling_volume, vec![1., 2.]);
    }

    #[tokio::test]
    async fn shared_v18_catalog_units_pass_and_unknown_units_fail_closed() {
        for case in ["unit:price", "unit:ratio", "unit:dollars", "unit:bool",
                     "unit:duration", "unit:log_count", "unit:log_dollars",
                     "unit:log_duration", "unit:log_price", "unit:log_shares"] {
            let response = fixture(case).await.unwrap();
            assert_eq!(response.fields[0].unit, case.strip_prefix("unit:").unwrap());
        }
        assert!(fixture("unit:unrecognized").await.is_err());
    }

    #[tokio::test]
    async fn canonical_fractional_volume_is_preserved_exactly() {
        let response = fixture("fractional-volume").await.unwrap();
        assert_eq!(response.share_volume, vec![1.25, 1.25]);
        assert_eq!(response.rolling_volume, vec![1.25, 2.5]);
    }

    #[tokio::test]
    async fn source_placement_seals_clocks_and_intervals_fail_closed() {
        for case in [
            "disk",
            "missing-seal",
            "ambiguous-seal",
            "changing-seal",
            "duplicate-clock",
            "interval-gap",
            "infinite-bits",
            "value-drift",
            "negative-volume",
            "oversized-volume",
        ] {
            assert!(fixture(case).await.is_err(), "Corruption must fail: {case}");
        }
    }

    #[tokio::test]
    async fn invalid_asof_does_not_open_a_database_read() {
        let mut r = request();
        r.stop_ordinal = 3;
        assert!(load_with(r, |_| async {
            panic!("Invalid request must not issue SELECTs");
            #[allow(unreachable_code)]
            Ok(String::new())
        })
        .await
        .is_err());
    }
}
