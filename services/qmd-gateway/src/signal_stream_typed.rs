//! Versioned, lossless projection boundary for a future typed-only Signal Stream.
//! This module performs no database I/O. Publication must remain disabled until
//! all configured field instances and occurrence kinds pass this projection.

use chrono::{DateTime, NaiveDate, Utc};
use ring::digest::{digest, SHA256};
use serde::Serialize;
use serde_json::{json, Map, Number, Value};
use std::collections::{HashMap, HashSet};

pub const TYPED_OCCURRENCE_VERSION: u16 = 1;

// QMD source occurrences are distinct from the arte live journal authority.
// These append-only tables deliberately do not hide conflicts with FINAL:
// a future publisher/replayer must reject differing rows for one identity.
// These statements are contracts, never executed by the legacy startup path.
pub const CORE_TABLE_SQL: &str = r#"CREATE TABLE q_live.signal_stream_occurrence_typed_v1 (
    schema_version UInt16, session_key String, sequence UInt64, event_id String,
    signal_stream_id String, signal_stream_name String, ticker String,
    event_time DateTime64(6, 'UTC'), effective_at DateTime64(6, 'UTC'),
    available_at DateTime64(6, 'UTC'), configuration_revision String,
    definition_revision String, source_authority String, evidence_count UInt32,
    content_sha256 FixedString(64)
) ENGINE = MergeTree
PARTITION BY session_key
ORDER BY (session_key, sequence, event_id)
SETTINGS storage_policy = 'live_market_ssd'"#;

pub const FIELD_CATALOG_SQL: &str = r#"CREATE TABLE q_live.signal_stream_field_catalog_v1 (
    schema_version UInt16, configuration_revision String, signal_stream_id String,
    field_instance_id FixedString(64), column_id String, source_id String,
    value_type LowCardinality(String), interval_id String, aggregation_id String,
    source_path String, provenance String, query_plan_id String, available_at_contract String
) ENGINE = MergeTree
PARTITION BY tuple()
ORDER BY (configuration_revision, signal_stream_id, field_instance_id)
SETTINGS storage_policy = 'live_market_ssd'"#;

pub const FIELD_EVIDENCE_SQL: &str = r#"CREATE TABLE q_live.signal_stream_field_evidence_v1 (
    schema_version UInt16, session_key String, sequence UInt64, event_id String,
    field_instance_id FixedString(64), value_type LowCardinality(String),
    value_float Nullable(Float64), value_int Nullable(Int64),
    value_uint Nullable(UInt64), value_bool Nullable(UInt8),
    value_text Nullable(String), value_time Nullable(DateTime64(6, 'UTC')),
    content_sha256 FixedString(64)
) ENGINE = MergeTree
PARTITION BY session_key
ORDER BY (session_key, sequence, event_id, field_instance_id)
SETTINGS storage_policy = 'live_market_ssd'"#;

pub const SQUEEZE_EVIDENCE_SQL: &str = r#"CREATE TABLE q_live.signal_stream_squeeze_evidence_v1 (
    schema_version UInt16, session_key String, sequence UInt64, event_id String,
    episode_id String, role LowCardinality(String),
    started_at DateTime64(6, 'UTC'), expires_at DateTime64(6, 'UTC'),
    anchor_price Float64, move_pct Float64, high_water_pct Float64,
    content_sha256 FixedString(64)
) ENGINE = MergeTree
PARTITION BY session_key
ORDER BY (session_key, sequence, event_id)
SETTINGS storage_policy = 'live_market_ssd'"#;

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct FieldBinding {
    pub stream_id: String,
    pub column_id: String,
    pub source_id: String,
    pub value_type: String,
    pub interval_id: String,
    pub aggregation_id: String,
    pub source_path: String,
    pub provenance: String,
    pub query_plan_id: String,
    pub available_at_contract: String,
    pub field_instance_id: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub enum ScalarValue {
    Float(f64),
    Int(i64),
    Uint(u64),
    Bool(bool),
    Text(String),
    Time(DateTime<Utc>),
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct FieldEvidence {
    pub field_instance_id: String,
    pub value: ScalarValue,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct SqueezeEvidence {
    pub episode_id: String,
    pub role: String,
    pub started_at: DateTime<Utc>,
    pub expires_at: DateTime<Utc>,
    pub anchor_price: f64,
    pub move_pct: f64,
    pub high_water_pct: f64,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct TypedOccurrence {
    pub session_key: String,
    pub sequence: u64,
    pub event_id: String,
    pub stream_id: String,
    pub stream_name: String,
    pub ticker: String,
    pub event_time: DateTime<Utc>,
    pub effective_at: DateTime<Utc>,
    pub available_at: DateTime<Utc>,
    pub configuration_revision: String,
    pub definition_revision: String,
    pub source_authority: String,
    pub fields: Vec<FieldEvidence>,
    pub squeeze: Option<SqueezeEvidence>,
    pub content_sha256: String,
}

fn hash(value: &Value) -> Result<String, String> {
    let bytes = serde_json::to_vec(value).map_err(|error| error.to_string())?;
    Ok(digest(&SHA256, &bytes)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect())
}

fn required<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|v| !v.is_empty())
        .ok_or_else(|| format!("typed Signal occurrence requires {key}"))
}

fn timestamp(value: &Value, key: &str) -> Result<DateTime<Utc>, String> {
    required(value, key)?
        .parse::<DateTime<Utc>>()
        .map_err(|_| format!("typed Signal occurrence has invalid {key}"))
}

fn finite(value: &Value, key: &str) -> Result<f64, String> {
    value
        .get(key)
        .and_then(Value::as_f64)
        .filter(|v| v.is_finite())
        .ok_or_else(|| format!("typed Signal occurrence requires finite {key}"))
}

pub fn bindings(stream: &Value, catalog: &[Value]) -> Result<Vec<FieldBinding>, String> {
    let stream_id = required(stream, "signal_stream_id")?;
    let mut output = Vec::new();
    let mut seen = HashSet::new();
    for column_id in stream
        .get("columns")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
    {
        let column_id = column_id
            .as_str()
            .filter(|v| !v.is_empty())
            .ok_or_else(|| "Signal Stream column ID is invalid".to_string())?;
        if !seen.insert(column_id) {
            return Err(format!("duplicate Signal Stream column {column_id}"));
        }
        let entry = catalog
            .iter()
            .find(|entry| entry.get("column_id").and_then(Value::as_str) == Some(column_id))
            .ok_or_else(|| format!("unbound Signal Stream column {column_id}"))?;
        let source_id = required(entry, "source_id")?;
        let value_type = required(entry, "value_type")?;
        let source_path = required(entry, "source_path")?;
        let provenance = required(entry, "provenance")?;
        let query_plan_id = required(entry, "query_plan_id")?;
        let available_at_contract = required(entry, "available_at")?;
        if !matches!(
            value_type,
            "number" | "integer" | "boolean" | "string" | "text" | "datetime"
        ) {
            return Err(format!(
                "unsupported Signal Stream value_type {value_type} for {column_id}"
            ));
        }
        let interval_id = stream
            .get("column_intervals")
            .and_then(|v| v.get(column_id))
            .map(|v| serde_json::to_string(v).unwrap_or_default())
            .unwrap_or_default();
        let aggregation_id = stream
            .get("column_aggregations")
            .and_then(|v| v.get(column_id))
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let identity = json!([
            stream_id,
            column_id,
            source_id,
            value_type,
            interval_id,
            aggregation_id,
            source_path,
            provenance,
            query_plan_id,
            available_at_contract
        ]);
        output.push(FieldBinding {
            stream_id: stream_id.to_string(),
            column_id: column_id.to_string(),
            source_id: source_id.to_string(),
            value_type: value_type.to_string(),
            interval_id,
            aggregation_id,
            source_path: source_path.to_string(),
            provenance: provenance.to_string(),
            query_plan_id: query_plan_id.to_string(),
            available_at_contract: available_at_contract.to_string(),
            field_instance_id: hash(&identity)?,
        });
    }
    Ok(output)
}

fn scalar(value: &Value, binding: &FieldBinding) -> Result<ScalarValue, String> {
    match binding.value_type.as_str() {
        "number" => value
            .as_i64()
            .map(ScalarValue::Int)
            .or_else(|| value.as_u64().map(ScalarValue::Uint))
            .or_else(|| {
                value
                    .as_f64()
                    .filter(|v| v.is_finite())
                    .map(ScalarValue::Float)
            }),
        "integer" => value
            .as_i64()
            .map(ScalarValue::Int)
            .or_else(|| value.as_u64().map(ScalarValue::Uint)),
        "boolean" => value.as_bool().map(ScalarValue::Bool),
        "string" | "text" => value.as_str().map(|v| ScalarValue::Text(v.to_string())),
        "datetime" => value
            .as_str()
            .and_then(|v| v.parse::<DateTime<Utc>>().ok())
            .map(ScalarValue::Time),
        _ => None,
    }
    .ok_or_else(|| {
        format!(
            "Signal Stream field {} violates declared {} type",
            binding.column_id, binding.value_type
        )
    })
}

pub fn project(occurrence: &Value, bindings: &[FieldBinding]) -> Result<TypedOccurrence, String> {
    let object = occurrence
        .as_object()
        .ok_or_else(|| "Signal occurrence is not an object".to_string())?;
    let stream_id = required(occurrence, "signal_stream_id")?;
    if bindings
        .iter()
        .any(|binding| binding.stream_id != stream_id)
    {
        return Err("Signal occurrence field binding belongs to another stream".to_string());
    }
    let schema_version = occurrence
        .get("schema_version")
        .and_then(Value::as_u64)
        .ok_or_else(|| "Signal occurrence has no schema_version".to_string())?;
    if schema_version != 2 {
        return Err(format!(
            "unsupported source Signal schema_version {schema_version}"
        ));
    }
    let event_id = required(occurrence, "event_id")?;
    if required(occurrence, "signal_id")? != event_id {
        return Err("Signal occurrence signal_id differs from event_id".to_string());
    }
    let ticker = required(occurrence, "ticker")?;
    if required(occurrence, "symbol")? != ticker {
        return Err("Signal occurrence symbol differs from ticker".to_string());
    }
    let sequence = occurrence
        .get("sequence")
        .and_then(Value::as_u64)
        .filter(|v| *v > 0)
        .ok_or_else(|| "Signal occurrence needs positive sequence".to_string())?;
    let session_key = required(occurrence, "session_key")?;
    if NaiveDate::parse_from_str(session_key, "%Y-%m-%d")
        .map(|date| date.format("%Y-%m-%d").to_string() != session_key)
        .unwrap_or(true)
    {
        return Err("Signal occurrence session_key must be YYYY-MM-DD".to_string());
    }
    let mut allowed: HashSet<&str> = [
        "schema_version",
        "event_id",
        "signal_id",
        "signal_stream_id",
        "signal_stream_name",
        "ticker",
        "symbol",
        "event_time",
        "effective_at",
        "available_at",
        "session_key",
        "configuration_revision",
        "definition_revision",
        "source_authority",
        "sequence",
    ]
    .into_iter()
    .collect();
    let mut fields = Vec::new();
    for binding in bindings {
        allowed.insert(&binding.column_id);
        if let Some(value) = object.get(&binding.column_id) {
            fields.push(FieldEvidence {
                field_instance_id: binding.field_instance_id.clone(),
                value: scalar(value, binding)?,
            });
        }
    }
    let squeeze = if object.contains_key("squeeze_episode_role") {
        for key in [
            "squeeze_episode_id",
            "squeeze_episode_role",
            "squeeze_episode_started_at",
            "squeeze_episode_expires_at",
            "squeeze_anchor_price",
            "squeeze_move_pct",
            "squeeze_high_water_pct",
        ] {
            allowed.insert(key);
        }
        let role = required(occurrence, "squeeze_episode_role")?;
        if !matches!(role, "start" | "milestone") {
            return Err("invalid squeeze episode role".to_string());
        }
        Some(SqueezeEvidence {
            episode_id: required(occurrence, "squeeze_episode_id")?.to_string(),
            role: role.to_string(),
            started_at: timestamp(occurrence, "squeeze_episode_started_at")?,
            expires_at: timestamp(occurrence, "squeeze_episode_expires_at")?,
            anchor_price: finite(occurrence, "squeeze_anchor_price")?,
            move_pct: finite(occurrence, "squeeze_move_pct")?,
            high_water_pct: finite(occurrence, "squeeze_high_water_pct")?,
        })
    } else {
        None
    };
    if let Some(key) = object.keys().find(|key| !allowed.contains(key.as_str())) {
        return Err(format!("unmodeled Signal occurrence field {key}"));
    }
    let projected = TypedOccurrence {
        session_key: session_key.to_string(),
        sequence,
        event_id: event_id.to_string(),
        stream_id: stream_id.to_string(),
        stream_name: required(occurrence, "signal_stream_name")?.to_string(),
        ticker: ticker.to_string(),
        event_time: timestamp(occurrence, "event_time")?,
        effective_at: timestamp(occurrence, "effective_at")?,
        available_at: timestamp(occurrence, "available_at")?,
        configuration_revision: required(occurrence, "configuration_revision")?.to_string(),
        definition_revision: required(occurrence, "definition_revision")?.to_string(),
        source_authority: required(occurrence, "source_authority")?.to_string(),
        fields,
        squeeze,
        content_sha256: hash(occurrence)?,
    };
    // This is a strict boundary: even subtle number/timestamp normalization
    // must fail before a typed writer can acknowledge a lossy occurrence.
    restore(&projected, bindings)?;
    Ok(projected)
}

pub fn restore(row: &TypedOccurrence, bindings: &[FieldBinding]) -> Result<Value, String> {
    let mut value = json!({
        "schema_version": 2, "event_id": row.event_id, "signal_id": row.event_id,
        "signal_stream_id": row.stream_id, "signal_stream_name": row.stream_name,
        "ticker": row.ticker, "symbol": row.ticker, "sequence": row.sequence,
        "event_time": row.event_time.to_rfc3339(), "effective_at": row.effective_at.to_rfc3339(),
        "available_at": row.available_at.to_rfc3339(), "session_key": row.session_key,
        "configuration_revision": row.configuration_revision,
        "definition_revision": row.definition_revision, "source_authority": row.source_authority,
    });
    let object: &mut Map<String, Value> = value.as_object_mut().unwrap();
    let catalog: HashMap<&str, &FieldBinding> = bindings
        .iter()
        .map(|binding| (binding.field_instance_id.as_str(), binding))
        .collect();
    if catalog.len() != bindings.len() {
        return Err("duplicate field-instance binding".to_string());
    }
    let mut seen = HashSet::new();
    for evidence in &row.fields {
        let binding = catalog
            .get(evidence.field_instance_id.as_str())
            .ok_or_else(|| format!("unknown field instance {}", evidence.field_instance_id))?;
        if !seen.insert(&binding.column_id) {
            return Err("duplicate field evidence".to_string());
        }
        let scalar = match &evidence.value {
            ScalarValue::Float(v) => {
                Value::Number(Number::from_f64(*v).ok_or("nonfinite evidence")?)
            }
            ScalarValue::Int(v) => json!(v),
            ScalarValue::Uint(v) => json!(v),
            ScalarValue::Bool(v) => json!(v),
            ScalarValue::Text(v) => json!(v),
            ScalarValue::Time(v) => json!(v.to_rfc3339()),
        };
        object.insert(binding.column_id.clone(), scalar);
    }
    if let Some(squeeze) = &row.squeeze {
        object.insert("squeeze_episode_id".to_string(), json!(squeeze.episode_id));
        object.insert("squeeze_episode_role".to_string(), json!(squeeze.role));
        object.insert(
            "squeeze_episode_started_at".to_string(),
            json!(squeeze.started_at.to_rfc3339()),
        );
        object.insert(
            "squeeze_episode_expires_at".to_string(),
            json!(squeeze.expires_at.to_rfc3339()),
        );
        object.insert(
            "squeeze_anchor_price".to_string(),
            json!(squeeze.anchor_price),
        );
        object.insert("squeeze_move_pct".to_string(), json!(squeeze.move_pct));
        object.insert(
            "squeeze_high_water_pct".to_string(),
            json!(squeeze.high_water_pct),
        );
    }
    if hash(&value)? != row.content_sha256 {
        return Err("typed Signal occurrence content hash mismatch".to_string());
    }
    Ok(value)
}

/// Validate one read-only page from a global, unfiltered session sequence.
/// A missing or conflicting position must stop startup hydration, not advance
/// the cursor past an occurrence that may belong to another source kind.
pub fn replay_page(
    session_key: &str,
    after_sequence: u64,
    limit: usize,
    rows: &[TypedOccurrence],
    bindings_by_stream: &HashMap<String, Vec<FieldBinding>>,
) -> Result<(Vec<Value>, u64), String> {
    if !(1..=5_000).contains(&limit) || rows.len() > limit {
        return Err("typed Signal replay page exceeds its bounded limit".to_string());
    }
    let mut cursor = after_sequence;
    let mut restored = Vec::with_capacity(rows.len());
    for row in rows {
        if row.session_key != session_key {
            return Err("typed Signal replay page crosses sessions".to_string());
        }
        let expected = cursor
            .checked_add(1)
            .ok_or_else(|| "typed Signal replay cursor overflow".to_string())?;
        if row.sequence != expected {
            return Err(format!(
                "typed Signal replay expected sequence {expected}, found {}",
                row.sequence
            ));
        }
        let bindings = bindings_by_stream
            .get(&row.stream_id)
            .ok_or_else(|| format!("typed Signal replay has no catalog for {}", row.stream_id))?;
        restored.push(restore(row, bindings)?);
        cursor = row.sequence;
    }
    Ok((restored, cursor))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn typed_squeeze_round_trip_and_unknown_field_rejection() {
        let stream = json!({"signal_stream_id":"price-squeeze-early", "columns":["last-price"]});
        let catalog = vec![
            json!({"column_id":"last-price", "source_id":"market.last_price", "value_type":"number",
            "source_path":"qmd://market", "provenance":"qmd", "query_plan_id":"market-v1",
            "available_at":"QMD publication clock"}),
        ];
        let binding = bindings(&stream, &catalog).unwrap();
        let source = json!({
            "schema_version":2, "event_id":"early-1", "signal_id":"early-1",
            "signal_stream_id":"price-squeeze-early", "signal_stream_name":"Early Squeeze",
            "ticker":"ABC", "symbol":"ABC", "sequence":1,
            "event_time":"2026-08-17T15:00:00+00:00", "effective_at":"2026-08-17T15:00:00+00:00",
            "available_at":"2026-08-17T15:00:00+00:00", "session_key":"2026-08-17",
            "configuration_revision":"config-1", "definition_revision":"definition-1",
            "source_authority":"qmd_event_time_squeeze_episode", "last-price":10.25,
            "squeeze_episode_id":"episode-1", "squeeze_episode_role":"start",
            "squeeze_episode_started_at":"2026-08-17T15:00:00+00:00",
            "squeeze_episode_expires_at":"2026-08-17T15:05:00+00:00",
            "squeeze_anchor_price":10.0, "squeeze_move_pct":2.5, "squeeze_high_water_pct":2.5
        });
        let projected = project(&source, &binding).unwrap();
        assert_eq!(restore(&projected, &binding).unwrap(), source);
        let mut unmodeled = source.clone();
        unmodeled["other"] = json!(42);
        assert!(project(&unmodeled, &binding).is_err());
        assert!(CORE_TABLE_SQL.contains("storage_policy = 'live_market_ssd'"));
        assert!(FIELD_EVIDENCE_SQL.contains("value_float Nullable(Float64)"));
    }

    #[test]
    fn global_sequence_core_round_trips_external_and_recovery_sources() {
        let stream = json!({"signal_stream_id":"external-1", "columns":[]});
        let bindings = bindings(&stream, &[]).unwrap();
        for (sequence, authority) in [
            (2, "qmd_external_event"),
            (3, "qmd_history_signal_recovery"),
        ] {
            let source = json!({
                "schema_version":2, "event_id":format!("event-{sequence}"),
                "signal_id":format!("event-{sequence}"), "signal_stream_id":"external-1",
                "signal_stream_name":"External", "ticker":"ABC", "symbol":"ABC",
                "sequence":sequence, "event_time":"2026-08-17T15:00:00+00:00",
                "effective_at":"2026-08-17T15:00:00+00:00",
                "available_at":"2026-08-17T15:00:00+00:00",
                "session_key":"2026-08-17", "configuration_revision":"config-1",
                "definition_revision":"definition-1", "source_authority":authority
            });
            let projected = project(&source, &bindings).unwrap();
            assert_eq!(projected.sequence, sequence);
            assert_eq!(restore(&projected, &bindings).unwrap(), source);
        }
    }

    #[test]
    fn catalog_binding_rejects_unknown_or_wrongly_typed_evidence() {
        let stream = json!({"signal_stream_id":"external-1", "columns":["price"]});
        assert!(bindings(&stream, &[]).is_err());
        let catalog = vec![
            json!({"column_id":"price", "source_id":"market.last_price", "value_type":"number",
            "source_path":"qmd://market", "provenance":"qmd", "query_plan_id":"market-v1",
            "available_at":"QMD publication clock"}),
        ];
        let bindings = bindings(&stream, &catalog).unwrap();
        let mut source = json!({
            "schema_version":2, "event_id":"event-1", "signal_id":"event-1",
            "signal_stream_id":"external-1", "signal_stream_name":"External",
            "ticker":"ABC", "symbol":"ABC", "sequence":1,
            "event_time":"2026-08-17T15:00:00+00:00",
            "effective_at":"2026-08-17T15:00:00+00:00",
            "available_at":"2026-08-17T15:00:00+00:00",
            "session_key":"2026-08-17", "configuration_revision":"config-1",
            "definition_revision":"definition-1", "source_authority":"qmd_external_event",
            "price":"not a number"
        });
        assert!(project(&source, &bindings).is_err());
        source["price"] = json!(10);
        assert_eq!(
            restore(&project(&source, &bindings).unwrap(), &bindings).unwrap(),
            source
        );
        source["price"] = Value::Null;
        assert!(project(&source, &bindings).is_err());
    }

    #[test]
    fn replay_page_checks_global_cursor_and_recovered_content() {
        let stream = json!({"signal_stream_id":"external-1", "columns":[]});
        let bindings = bindings(&stream, &[]).unwrap();
        let source = json!({
            "schema_version":2, "event_id":"event-1", "signal_id":"event-1",
            "signal_stream_id":"external-1", "signal_stream_name":"External",
            "ticker":"ABC", "symbol":"ABC", "sequence":1,
            "event_time":"2026-08-17T15:00:00+00:00",
            "effective_at":"2026-08-17T15:00:00+00:00",
            "available_at":"2026-08-17T15:00:00+00:00",
            "session_key":"2026-08-17", "configuration_revision":"config-1",
            "definition_revision":"definition-1", "source_authority":"qmd_external_event"
        });
        let row = project(&source, &bindings).unwrap();
        let catalogs = HashMap::from([("external-1".to_string(), bindings)]);
        let (restored, cursor) =
            replay_page("2026-08-17", 0, 10, &[row.clone()], &catalogs).unwrap();
        assert_eq!(restored, vec![source]);
        assert_eq!(cursor, 1);
        assert!(replay_page("2026-08-17", 1, 10, &[row.clone()], &catalogs).is_err());
        assert!(replay_page("2026-08-18", 0, 10, &[row.clone()], &catalogs).is_err());
        let mut corrupt = row;
        corrupt.content_sha256 = "0".repeat(64);
        assert!(replay_page("2026-08-17", 0, 10, &[corrupt], &catalogs).is_err());
    }
}
