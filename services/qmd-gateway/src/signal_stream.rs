use crate::config::GatewayConfig;
use chrono::{DateTime, Utc};
use reqwest::Client;
use ring::digest::{digest, SHA256};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::Arc;
use tokio::sync::Mutex;

pub const SIGNAL_STREAM_SCHEMA_VERSION: u16 = 2;
const MEMORY_OCCURRENCE_LIMIT: usize = 50_000;

#[derive(Clone, Debug, Deserialize)]
pub struct SignalStreamConfigurationRequest {
    pub configuration_revision: String,
    pub session_key: String,
    pub session_start_utc: DateTime<Utc>,
    pub session_end_utc: DateTime<Utc>,
    #[serde(default)]
    pub streams: Vec<Value>,
    #[serde(default)]
    pub rule_sets: Vec<Value>,
    #[serde(default)]
    pub column_catalog: Vec<Value>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SignalStreamEvaluateRequest {
    pub as_of: DateTime<Utc>,
    #[serde(default)]
    pub candidates: Vec<Value>,
    #[serde(default)]
    pub watchlist_members: BTreeMap<String, Vec<String>>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SignalStreamExternalRequest {
    pub signal_stream_id: String,
    #[serde(default)]
    pub rows: Vec<Value>,
}

#[derive(Clone, Debug, Deserialize)]
pub struct SignalStreamSnapshotQuery {
    pub signal_stream_id: Option<String>,
    pub as_of: Option<DateTime<Utc>>,
    pub after_sequence: Option<u64>,
    pub limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
pub struct SignalStreamSnapshot {
    pub schema_version: u16,
    pub authority: &'static str,
    pub configuration_revision: String,
    pub session: Value,
    pub as_of: DateTime<Utc>,
    pub status: String,
    pub last_sequence: u64,
    pub occurrence_count: usize,
    pub occurrences: Vec<Value>,
    pub new_occurrences: Vec<Value>,
    pub signal_streams: Vec<Value>,
    pub admissions_by_watchlist: BTreeMap<String, Vec<Value>>,
}

#[derive(Clone)]
pub struct SharedSignalStreamStore {
    inner: Arc<Mutex<SignalStreamStore>>,
    mutation: Arc<Mutex<()>>,
    writer: SignalStreamClickHouseWriter,
}

#[derive(Clone, Default)]
struct MatchState {
    matching: bool,
    last_emitted_at: Option<DateTime<Utc>>,
    definition_revision: String,
}

#[derive(Default)]
struct SignalStreamStore {
    configuration: Option<SignalStreamConfigurationRequest>,
    states: HashMap<(String, String), MatchState>,
    diagnostics: HashMap<String, Value>,
    occurrences: VecDeque<Value>,
    event_ids: HashSet<String>,
    last_sequence: u64,
    hydrated_session_key: String,
}

impl SharedSignalStreamStore {
    pub async fn new(config: GatewayConfig) -> Result<Self, String> {
        let writer = SignalStreamClickHouseWriter::new(config);
        writer.initialize().await?;
        Ok(Self {
            inner: Arc::new(Mutex::new(SignalStreamStore::default())),
            mutation: Arc::new(Mutex::new(())),
            writer,
        })
    }

    pub async fn configure(
        &self,
        request: SignalStreamConfigurationRequest,
    ) -> Result<SignalStreamSnapshot, String> {
        let _mutation = self.mutation.lock().await;
        validate_configuration(&request)?;
        let needs_hydration = {
            let store = self.inner.lock().await;
            store.hydrated_session_key != request.session_key
        };
        let hydrated = if needs_hydration {
            self.writer
                .load_session(
                    &request.session_key,
                    request.session_start_utc,
                    request.session_end_utc,
                )
                .await?
        } else {
            Vec::new()
        };
        let mut store = self.inner.lock().await;
        if needs_hydration {
            store.states.clear();
            store.diagnostics.clear();
            store.occurrences.clear();
            store.event_ids.clear();
            store.last_sequence = 0;
            for occurrence in hydrated {
                hydrate_occurrence(&mut store, occurrence);
            }
            store.hydrated_session_key = request.session_key.clone();
        }
        let revision_changed = store
            .configuration
            .as_ref()
            .map(|current| current.configuration_revision.as_str())
            != Some(request.configuration_revision.as_str());
        if revision_changed {
            store.states.clear();
            store.diagnostics.clear();
        }
        store.configuration = Some(request);
        Ok(snapshot_locked(&store, None, None, None, 0))
    }

    pub async fn evaluate(
        &self,
        request: SignalStreamEvaluateRequest,
    ) -> Result<SignalStreamSnapshot, String> {
        let _mutation = self.mutation.lock().await;
        let store = self.inner.lock().await;
        let configuration = store
            .configuration
            .clone()
            .ok_or_else(|| "QMD Signal Stream configuration is not materialized".to_string())?;
        if request.as_of < configuration.session_start_utc
            || request.as_of > configuration.session_end_utc
        {
            return Ok(snapshot_locked(&store, None, Some(request.as_of), None, 0));
        }
        let rules = configuration
            .rule_sets
            .iter()
            .filter_map(|rule| Some((rule.get("rule_set_id")?.as_str()?.to_string(), rule.clone())))
            .collect::<HashMap<_, _>>();
        let rows_by_ticker = request
            .candidates
            .iter()
            .filter_map(|row| Some((ticker(row)?, row.clone())))
            .collect::<HashMap<_, _>>();
        let mut next_states = store.states.clone();
        let mut pending = Vec::<Value>::new();
        let mut diagnostics = HashMap::<String, Value>::new();
        for stream in &configuration.streams {
            let Some(stream_id) = string(stream, "signal_stream_id") else {
                continue;
            };
            if stream.get("enabled").and_then(Value::as_bool) == Some(false) {
                diagnostics.insert(
                    stream_id.to_string(),
                    stream_diagnostic(stream, "disabled", 0, 0, 0),
                );
                continue;
            }
            if string(stream, "occurrence_source").unwrap_or("rule_evaluator") != "rule_evaluator"
                || string(stream, "source_type").unwrap_or("core_scan") == "news_events"
            {
                diagnostics.insert(
                    stream_id.to_string(),
                    stream_diagnostic(stream, "ready", 0, 0, 0),
                );
                continue;
            }
            let source_type = string(stream, "source_type").unwrap_or("core_scan");
            let source_id = string(stream, "source_id").unwrap_or("");
            let source_tickers = if source_type == "watchlist" {
                request
                    .watchlist_members
                    .get(source_id)
                    .cloned()
                    .unwrap_or_default()
                    .into_iter()
                    .map(|value| value.to_ascii_uppercase())
                    .collect::<HashSet<_>>()
            } else {
                rows_by_ticker.keys().cloned().collect::<HashSet<_>>()
            };
            let selected_rules = stream
                .get("inclusion_rule_sets")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>();
            let definition_revision = definition_revision(stream, &rules, &selected_rules);
            let mut matching_count = 0usize;
            let mut emitted_count = 0usize;
            for symbol in &source_tickers {
                let Some(row) = rows_by_ticker.get(symbol) else {
                    continue;
                };
                let results = selected_rules
                    .iter()
                    .map(|rule_id| rule_matches(rules.get(*rule_id), row))
                    .collect::<Vec<_>>();
                let matches = !results.is_empty()
                    && if string(stream, "inclusion_operator").unwrap_or("all") == "any" {
                        results.iter().any(|value| *value)
                    } else {
                        results.iter().all(|value| *value)
                    };
                matching_count += usize::from(matches);
                let key = (stream_id.to_string(), symbol.clone());
                let previous = next_states.get(&key).cloned().unwrap_or_default();
                let previous_match =
                    previous.matching && previous.definition_revision == definition_revision;
                let cooldown_ms = stream
                    .get("cooldown_ms")
                    .and_then(Value::as_u64)
                    .unwrap_or(0);
                let cooldown_ready = previous.last_emitted_at.is_none_or(|last| {
                    request.as_of.signed_duration_since(last).num_milliseconds()
                        >= cooldown_ms as i64
                });
                let should_emit = matches
                    && (!previous_match
                        || (string(stream, "rearm_policy").unwrap_or("after_false")
                            == "after_cooldown"
                            && cooldown_ready));
                let mut next = previous;
                next.matching = matches;
                next.definition_revision = definition_revision.clone();
                if should_emit {
                    let occurrence = occurrence(
                        stream,
                        row,
                        request.as_of,
                        &configuration,
                        &definition_revision,
                        "qmd_live_rule_evaluator",
                    );
                    let event_id = string(&occurrence, "event_id").unwrap_or("");
                    if !event_id.is_empty() && !store.event_ids.contains(event_id) {
                        pending.push(occurrence);
                        emitted_count += 1;
                    }
                    next.last_emitted_at = Some(request.as_of);
                }
                next_states.insert(key, next);
            }
            for ((state_stream, symbol), state) in next_states.iter_mut() {
                if state_stream == stream_id && !source_tickers.contains(symbol) {
                    state.matching = false;
                    state.definition_revision = definition_revision.clone();
                }
            }
            diagnostics.insert(
                stream_id.to_string(),
                stream_diagnostic(
                    stream,
                    "ready",
                    source_tickers.len(),
                    matching_count,
                    emitted_count,
                ),
            );
        }
        let pending = assign_sequences(&store, pending);
        drop(store);
        self.writer.insert(&pending).await?;
        let mut store = self.inner.lock().await;
        store.states = next_states;
        store.diagnostics = diagnostics;
        for occurrence in &pending {
            append_occurrence(&mut store, occurrence.clone());
        }
        Ok(snapshot_locked(&store, None, Some(request.as_of), None, 0)
            .with_new_occurrences(pending))
    }

    pub async fn append_external(
        &self,
        request: SignalStreamExternalRequest,
    ) -> Result<SignalStreamSnapshot, String> {
        let _mutation = self.mutation.lock().await;
        let store = self.inner.lock().await;
        let configuration = store
            .configuration
            .clone()
            .ok_or_else(|| "QMD Signal Stream configuration is not materialized".to_string())?;
        let stream = configuration
            .streams
            .iter()
            .find(|row| string(row, "signal_stream_id") == Some(request.signal_stream_id.as_str()))
            .cloned()
            .ok_or_else(|| format!("unknown Signal Stream {}", request.signal_stream_id))?;
        let selected_rules = stream
            .get("inclusion_rule_sets")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        let rules = configuration
            .rule_sets
            .iter()
            .filter_map(|rule| Some((string(rule, "rule_set_id")?.to_string(), rule.clone())))
            .collect::<HashMap<_, _>>();
        let revision = definition_revision(&stream, &rules, &selected_rules);
        let mut pending = Vec::new();
        for row in request.rows {
            let Some(at) =
                datetime_value(&row, "available_at").or_else(|| datetime_value(&row, "event_time"))
            else {
                continue;
            };
            if at < configuration.session_start_utc || at > configuration.session_end_utc {
                continue;
            }
            let source_id = string(&row, "source_event_id").unwrap_or("");
            let source = string(&row, "source_authority").unwrap_or("qmd_external_event");
            let mut value = occurrence(&stream, &row, at, &configuration, &revision, source);
            if !source_id.is_empty() {
                let id = sha256_hex(&format!(
                    "{}|{}|{}|{}",
                    request.signal_stream_id,
                    revision,
                    ticker(&row).unwrap_or_default(),
                    source_id
                ));
                value["event_id"] = Value::String(id.clone());
                value["signal_id"] = Value::String(id);
            }
            let event_id = string(&value, "event_id").unwrap_or("");
            if !event_id.is_empty() && !store.event_ids.contains(event_id) {
                pending.push(value);
            }
        }
        let pending = assign_sequences(&store, pending);
        drop(store);
        self.writer.insert(&pending).await?;
        let mut store = self.inner.lock().await;
        for occurrence in &pending {
            append_occurrence(&mut store, occurrence.clone());
        }
        store.diagnostics.insert(
            request.signal_stream_id,
            stream_diagnostic(
                &stream,
                "ready",
                pending.len(),
                pending.len(),
                pending.len(),
            ),
        );
        Ok(snapshot_locked(&store, None, None, None, 0).with_new_occurrences(pending))
    }

    pub async fn snapshot(&self, query: SignalStreamSnapshotQuery) -> SignalStreamSnapshot {
        let store = self.inner.lock().await;
        snapshot_locked(
            &store,
            query.signal_stream_id.as_deref(),
            query.as_of,
            query.after_sequence,
            query.limit.unwrap_or(5_000).clamp(1, 50_000),
        )
    }
}

trait SnapshotNewOccurrences {
    fn with_new_occurrences(self, rows: Vec<Value>) -> Self;
}

impl SnapshotNewOccurrences for SignalStreamSnapshot {
    fn with_new_occurrences(mut self, rows: Vec<Value>) -> Self {
        self.new_occurrences = rows;
        self
    }
}

fn validate_configuration(request: &SignalStreamConfigurationRequest) -> Result<(), String> {
    if request.configuration_revision.trim().is_empty() {
        return Err("QMD Signal Stream configuration_revision is required".to_string());
    }
    if request.session_start_utc >= request.session_end_utc {
        return Err("QMD Signal Stream session bounds are invalid".to_string());
    }
    let mut ids = HashSet::new();
    for stream in &request.streams {
        let id = string(stream, "signal_stream_id")
            .ok_or_else(|| "QMD Signal Stream id is required".to_string())?;
        if !ids.insert(id.to_string()) {
            return Err(format!("QMD Signal Stream id is duplicated: {id}"));
        }
    }
    Ok(())
}

fn rule_matches(rule: Option<&Value>, values: &Value) -> bool {
    let Some(rule) = rule.and_then(Value::as_object) else {
        return false;
    };
    if rule.get("enabled").and_then(Value::as_bool) == Some(false) {
        return false;
    }
    let results = rule
        .get("conditions")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_object)
        .filter(|condition| condition.get("enabled").and_then(Value::as_bool) != Some(false))
        .map(|condition| condition_matches(condition, values))
        .collect::<Vec<_>>();
    if results.is_empty() {
        return false;
    }
    match rule
        .get("operator")
        .and_then(Value::as_str)
        .unwrap_or("all")
    {
        "any" => results.iter().any(|value| *value),
        "score" => {
            let score =
                results.iter().filter(|value| **value).count() as f64 / results.len().max(1) as f64;
            score
                >= rule
                    .get("required_score")
                    .and_then(Value::as_f64)
                    .unwrap_or(1.0)
        }
        _ => results.iter().all(|value| *value),
    }
}

fn condition_matches(condition: &Map<String, Value>, values: &Value) -> bool {
    let left_instance = operand_instance(condition, "left");
    let Some(left_source) = map_string(condition, "left_instance_id")
        .or(left_instance.as_deref())
        .or_else(|| map_string(condition, "left_source_id"))
    else {
        return false;
    };
    let left = lookup_value(values, left_source);
    let comparator = map_string(condition, "comparator").unwrap_or("");
    if comparator == "is_true" {
        return left.and_then(Value::as_bool) == Some(true);
    }
    if comparator == "is_false" {
        return left.and_then(Value::as_bool) == Some(false);
    }
    let raw_right = map_string(condition, "right_source_id").unwrap_or("");
    let right_instance = operand_instance(condition, "right");
    let right_source = if raw_right.is_empty() {
        ""
    } else {
        map_string(condition, "right_instance_id")
            .or(right_instance.as_deref())
            .unwrap_or(raw_right)
    };
    let right = if right_source.is_empty() {
        condition.get("value")
    } else {
        lookup_value(values, right_source)
    };
    if comparator == "equals" {
        return left.is_some() && values_equal(left, right);
    }
    if comparator == "not_equals" {
        return left.is_some() && !values_equal(left, right);
    }
    let (Some(left), Some(right)) = (numeric_value(left), numeric_value(right)) else {
        return false;
    };
    match comparator {
        "above_by_bps" => {
            let bps = condition
                .get("value")
                .and_then(Value::as_f64)
                .unwrap_or(0.0);
            right > 0.0 && left >= right * (1.0 + bps / 10_000.0)
        }
        "greater_or_equal" => left >= right,
        "greater_than" => left > right,
        "less_or_equal" => left <= right,
        "less_than" => left < right,
        _ => false,
    }
}

fn operand_instance(condition: &Map<String, Value>, side: &str) -> Option<String> {
    let field_ref = map_string(condition, &format!("{side}_field_ref"))?;
    let interval = condition
        .get(&format!("{side}_interval"))
        .and_then(Value::as_object)
        .and_then(|value| {
            let amount = value.get("value")?.as_u64()?;
            let unit = value.get("unit")?.as_str()?;
            let suffix = match unit {
                "milliseconds" => "ms",
                "seconds" => "s",
                "minutes" => "m",
                "hours" => "h",
                "days" => "d",
                "weeks" => "w",
                "months" => "mo",
                _ => return None,
            };
            Some(format!("{amount}{suffix}"))
        });
    let aggregation = map_string(condition, &format!("{side}_aggregation"));
    let mut instance = interval
        .map(|value| format!("{field_ref}@@{value}"))
        .unwrap_or_else(|| field_ref.to_string());
    if let Some(aggregation) = aggregation {
        instance.push_str("##");
        instance.push_str(aggregation);
    }
    Some(instance)
}

fn lookup_value<'a>(values: &'a Value, key: &str) -> Option<&'a Value> {
    let object = values.as_object()?;
    if let Some(value) = object.get(key) {
        return Some(value);
    }
    let stripped = key
        .strip_prefix("data.")
        .unwrap_or(key)
        .split('@')
        .next()
        .unwrap_or(key)
        .trim_end_matches(":value");
    object.get(stripped).or_else(|| {
        object
            .iter()
            .find(|(candidate, _)| candidate.ends_with(stripped))
            .map(|(_, value)| value)
    })
}

fn values_equal(left: Option<&Value>, right: Option<&Value>) -> bool {
    match (left, right) {
        (Some(Value::Number(a)), Some(Value::Number(b))) => a.as_f64() == b.as_f64(),
        (Some(Value::String(a)), Some(Value::String(b))) => a.eq_ignore_ascii_case(b),
        (Some(a), Some(b)) => a == b,
        _ => false,
    }
}

fn numeric_value(value: Option<&Value>) -> Option<f64> {
    match value? {
        Value::Number(number) => number.as_f64().filter(|value| value.is_finite()),
        Value::String(text) => text.parse::<f64>().ok().filter(|value| value.is_finite()),
        _ => None,
    }
}

fn occurrence(
    stream: &Value,
    row: &Value,
    at: DateTime<Utc>,
    configuration: &SignalStreamConfigurationRequest,
    definition_revision: &str,
    source_authority: &str,
) -> Value {
    let stream_id = string(stream, "signal_stream_id").unwrap_or("");
    let symbol = ticker(row).unwrap_or_default();
    let event_id = sha256_hex(&format!(
        "{}|{}|{}|{}",
        stream_id,
        definition_revision,
        symbol,
        at.to_rfc3339()
    ));
    let mut result = Map::new();
    for key in [
        "ticker",
        "symbol",
        "conid",
        "company_name",
        "logo_url",
        "country",
        "news_recency",
        "sec_recency",
    ] {
        if let Some(value) = row.get(key) {
            result.insert(key.to_string(), value.clone());
        }
    }
    for column_id in stream
        .get("columns")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        if let Some(value) = row.get(column_id).or_else(|| lookup_value(row, column_id)) {
            result.insert(column_id.to_string(), value.clone());
        }
    }
    result.insert(
        "schema_version".to_string(),
        json!(SIGNAL_STREAM_SCHEMA_VERSION),
    );
    result.insert("event_id".to_string(), json!(event_id));
    result.insert("signal_id".to_string(), result["event_id"].clone());
    result.insert("signal_stream_id".to_string(), json!(stream_id));
    result.insert(
        "signal_stream_name".to_string(),
        json!(string(stream, "name").unwrap_or(stream_id)),
    );
    result.insert("ticker".to_string(), json!(symbol));
    result.insert("symbol".to_string(), result["ticker"].clone());
    result.insert("event_time".to_string(), json!(at.to_rfc3339()));
    result.insert("effective_at".to_string(), result["event_time"].clone());
    result.insert("available_at".to_string(), result["event_time"].clone());
    result.insert("session_key".to_string(), json!(configuration.session_key));
    result.insert(
        "configuration_revision".to_string(),
        json!(configuration.configuration_revision),
    );
    result.insert(
        "definition_revision".to_string(),
        json!(definition_revision),
    );
    result.insert("source_authority".to_string(), json!(source_authority));
    Value::Object(result)
}

fn assign_sequences(store: &SignalStreamStore, rows: Vec<Value>) -> Vec<Value> {
    rows.into_iter()
        .enumerate()
        .map(|(index, mut row)| {
            row["sequence"] = json!(store.last_sequence + index as u64 + 1);
            row
        })
        .collect()
}

fn append_occurrence(store: &mut SignalStreamStore, occurrence: Value) {
    let event_id = string(&occurrence, "event_id").unwrap_or("").to_string();
    if event_id.is_empty() || !store.event_ids.insert(event_id) {
        return;
    }
    store.last_sequence = store.last_sequence.max(
        occurrence
            .get("sequence")
            .and_then(Value::as_u64)
            .unwrap_or(0),
    );
    store.occurrences.push_back(occurrence);
    while store.occurrences.len() > MEMORY_OCCURRENCE_LIMIT {
        if let Some(removed) = store.occurrences.pop_front() {
            if let Some(id) = string(&removed, "event_id") {
                store.event_ids.remove(id);
            }
        }
    }
}

fn hydrate_occurrence(store: &mut SignalStreamStore, occurrence: Value) {
    let stream_id = string(&occurrence, "signal_stream_id")
        .unwrap_or("")
        .to_string();
    let symbol = ticker(&occurrence).unwrap_or_default();
    let at = datetime_value(&occurrence, "event_time");
    let definition = string(&occurrence, "definition_revision")
        .unwrap_or("")
        .to_string();
    append_occurrence(store, occurrence);
    if !stream_id.is_empty() && !symbol.is_empty() {
        store.states.insert(
            (stream_id, symbol),
            MatchState {
                matching: true,
                last_emitted_at: at,
                definition_revision: definition,
            },
        );
    }
}

fn snapshot_locked(
    store: &SignalStreamStore,
    stream_id: Option<&str>,
    as_of: Option<DateTime<Utc>>,
    after_sequence: Option<u64>,
    limit: usize,
) -> SignalStreamSnapshot {
    let now = as_of.unwrap_or_else(Utc::now);
    let configuration = store.configuration.as_ref();
    let active = configuration
        .map(|config| now >= config.session_start_utc && now <= config.session_end_utc)
        .unwrap_or(false);
    let bounded_limit = if limit == 0 { 5_000 } else { limit };
    let matching_occurrence_count = store
        .occurrences
        .iter()
        .filter(|row| stream_id.is_none_or(|id| string(row, "signal_stream_id") == Some(id)))
        .filter(|row| {
            as_of.is_none_or(|cutoff| {
                datetime_value(row, "event_time").is_some_and(|at| at <= cutoff)
            })
        })
        .count();
    let incremental_cursor_valid =
        after_sequence.is_some_and(|cursor| cursor <= store.last_sequence);
    let occurrences = if !incremental_cursor_valid {
        store
            .occurrences
            .iter()
            .rev()
            .filter(|row| stream_id.is_none_or(|id| string(row, "signal_stream_id") == Some(id)))
            .filter(|row| {
                as_of.is_none_or(|cutoff| {
                    datetime_value(row, "event_time").is_some_and(|at| at <= cutoff)
                })
            })
            .take(bounded_limit)
            .cloned()
            .collect::<Vec<_>>()
    } else {
        Vec::new()
    };
    let new_occurrences = store
        .occurrences
        .iter()
        .filter(|row| {
            incremental_cursor_valid
                && after_sequence.is_some_and(|sequence| {
                    row.get("sequence").and_then(Value::as_u64).unwrap_or(0) > sequence
                })
        })
        .filter(|row| stream_id.is_none_or(|id| string(row, "signal_stream_id") == Some(id)))
        .cloned()
        .collect::<Vec<_>>();
    let definitions = configuration
        .map(|config| {
            config
                .streams
                .iter()
                .filter(|stream| {
                    stream_id.is_none_or(|id| string(stream, "signal_stream_id") == Some(id))
                })
                .map(|stream| {
                    let id = string(stream, "signal_stream_id").unwrap_or("");
                    let prior = store.diagnostics.get(id).cloned().unwrap_or_else(|| {
                        stream_diagnostic(
                            stream,
                            if active {
                                "awaiting_first_evaluation"
                            } else {
                                "session_closed"
                            },
                            0,
                            0,
                            0,
                        )
                    });
                    let mut value = prior;
                    value["configured"] = Value::Bool(true);
                    value["occurrence_count"] = json!(store
                        .occurrences
                        .iter()
                        .filter(|row| string(row, "signal_stream_id") == Some(id))
                        .count());
                    value
                })
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    SignalStreamSnapshot {
        schema_version: SIGNAL_STREAM_SCHEMA_VERSION,
        authority: "qmd_live_signal_stream_v2",
        configuration_revision: configuration
            .map(|config| config.configuration_revision.clone())
            .unwrap_or_default(),
        session: configuration
            .map(|config| {
                json!({
                    "session_key": config.session_key,
                    "start_at": config.session_start_utc,
                    "end_at": config.session_end_utc,
                    "active": active,
                })
            })
            .unwrap_or_else(|| json!({"active": false})),
        as_of: now,
        status: if configuration.is_some() {
            "ready"
        } else {
            "unconfigured"
        }
        .to_string(),
        last_sequence: store.last_sequence,
        occurrence_count: matching_occurrence_count,
        occurrences,
        new_occurrences,
        signal_streams: definitions,
        admissions_by_watchlist: configuration
            .map(|config| admissions_by_watchlist(config, store, now))
            .unwrap_or_default(),
    }
}

fn admissions_by_watchlist(
    configuration: &SignalStreamConfigurationRequest,
    store: &SignalStreamStore,
    as_of: DateTime<Utc>,
) -> BTreeMap<String, Vec<Value>> {
    let mut result = BTreeMap::<String, Vec<Value>>::new();
    for stream in &configuration.streams {
        let stream_id = string(stream, "signal_stream_id").unwrap_or("");
        let routes = stream
            .get("watchlist_routes")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .collect::<Vec<_>>();
        if routes.is_empty() {
            continue;
        }
        for occurrence in store.occurrences.iter().filter(|row| {
            string(row, "signal_stream_id") == Some(stream_id)
                && datetime_value(row, "event_time").is_some_and(|at| at <= as_of)
        }) {
            for route in &routes {
                result
                    .entry((*route).to_string())
                    .or_default()
                    .push(occurrence.clone());
            }
        }
    }
    result
}

fn stream_diagnostic(
    stream: &Value,
    status: &str,
    candidates: usize,
    matching: usize,
    emitted: usize,
) -> Value {
    json!({
        "signal_stream_id": string(stream, "signal_stream_id").unwrap_or(""),
        "name": string(stream, "name").unwrap_or("Signal Stream"),
        "enabled": stream.get("enabled").and_then(Value::as_bool).unwrap_or(true),
        "status": status,
        "source_type": string(stream, "source_type").unwrap_or("core_scan"),
        "source_id": string(stream, "source_id").unwrap_or(""),
        "occurrence_source": string(stream, "occurrence_source").unwrap_or("rule_evaluator"),
        "candidate_count": candidates,
        "matching_count": matching,
        "emitted_count": emitted,
    })
}

fn definition_revision(
    stream: &Value,
    rules: &HashMap<String, Value>,
    selected: &[&str],
) -> String {
    let payload = json!({
        "stream": stream,
        "rules": selected.iter().filter_map(|id| rules.get(*id)).collect::<Vec<_>>(),
    });
    sha256_hex(&serde_json::to_string(&payload).unwrap_or_default())
}

fn sha256_hex(value: &str) -> String {
    digest(&SHA256, value.as_bytes())
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}

fn ticker(value: &Value) -> Option<String> {
    string(value, "ticker")
        .or_else(|| string(value, "symbol"))
        .map(|value| value.trim().to_ascii_uppercase())
        .filter(|value| !value.is_empty())
}

fn string<'a>(value: &'a Value, key: &str) -> Option<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn map_string<'a>(value: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|value| !value.is_empty())
}

fn datetime_value(value: &Value, key: &str) -> Option<DateTime<Utc>> {
    string(value, key)?.parse::<DateTime<Utc>>().ok()
}

#[derive(Clone)]
struct SignalStreamClickHouseWriter {
    client: Client,
    config: GatewayConfig,
}

impl SignalStreamClickHouseWriter {
    fn new(config: GatewayConfig) -> Self {
        Self {
            client: Client::new(),
            config,
        }
    }

    async fn initialize(&self) -> Result<(), String> {
        self.query(
            &format!(
                "CREATE DATABASE IF NOT EXISTS `{}`",
                self.config.clickhouse_database
            ),
            false,
        )
        .await?;
        self.query(&self.create_table_sql(), true).await.map(|_| ())
    }

    async fn insert(&self, rows: &[Value]) -> Result<(), String> {
        if rows.is_empty() {
            return Ok(());
        }
        let body = rows
            .iter()
            .map(|row| {
                let event_time = datetime_value(row, "event_time")
                    .map(|value| value.format("%Y-%m-%d %H:%M:%S%.6f").to_string())
                    .unwrap_or_default();
                json!({
                    "schema_version": SIGNAL_STREAM_SCHEMA_VERSION,
                    "session_key": string(row, "session_key").unwrap_or(""),
                    "configuration_revision": string(row, "configuration_revision").unwrap_or(""),
                    "definition_revision": string(row, "definition_revision").unwrap_or(""),
                    "sequence": row.get("sequence").and_then(Value::as_u64).unwrap_or(0),
                    "event_id": string(row, "event_id").unwrap_or(""),
                    "signal_stream_id": string(row, "signal_stream_id").unwrap_or(""),
                    "ticker": ticker(row).unwrap_or_default(),
                    "event_time": event_time,
                    "payload_json": serde_json::to_string(row).unwrap_or_else(|_| "{}".to_string()),
                    "source_run_id": self.config.qmd_run_id,
                    "inserted_at_utc": Utc::now().format("%Y-%m-%d %H:%M:%S%.6f").to_string(),
                })
                .to_string()
            })
            .collect::<Vec<_>>()
            .join("\n");
        self.query(
            &format!(
                "INSERT INTO {} FORMAT JSONEachRow\n{}",
                self.config.signal_stream_table, body
            ),
            true,
        )
        .await
        .map(|_| ())
    }

    async fn load_session(
        &self,
        session_key: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<Vec<Value>, String> {
        let sql = format!(
            "SELECT payload_json FROM {table} FINAL WHERE session_key='{session}' AND event_time>=parseDateTime64BestEffort('{start}',6,'UTC') AND event_time<=parseDateTime64BestEffort('{end}',6,'UTC') ORDER BY event_time,sequence,event_id FORMAT JSONEachRow",
            table = self.config.signal_stream_table,
            session = escape_sql(session_key),
            start = start.to_rfc3339(),
            end = end.to_rfc3339(),
        );
        let text = self.query(&sql, true).await?;
        text.lines()
            .filter(|line| !line.trim().is_empty())
            .map(|line| {
                let envelope: Value =
                    serde_json::from_str(line).map_err(|error| error.to_string())?;
                let payload = envelope
                    .get("payload_json")
                    .and_then(Value::as_str)
                    .ok_or_else(|| {
                        "QMD Signal Stream history row has no payload_json".to_string()
                    })?;
                serde_json::from_str(payload).map_err(|error| error.to_string())
            })
            .collect()
    }

    fn create_table_sql(&self) -> String {
        format!(
            r#"CREATE TABLE IF NOT EXISTS {table} (
                schema_version UInt16,
                session_key String,
                configuration_revision String,
                definition_revision String,
                sequence UInt64,
                event_id String,
                signal_stream_id LowCardinality(String),
                ticker LowCardinality(String),
                event_time DateTime64(6, 'UTC'),
                payload_json String,
                source_run_id String,
                inserted_at_utc DateTime64(6, 'UTC')
            ) ENGINE = ReplacingMergeTree(inserted_at_utc)
            PARTITION BY session_key
            ORDER BY (session_key, signal_stream_id, event_time, ticker, event_id)"#,
            table = self.config.signal_stream_table,
        )
    }

    async fn query(&self, body: &str, use_database: bool) -> Result<String, String> {
        let url = if use_database {
            format!(
                "{}/?database={}",
                self.config.clickhouse_url,
                urlencoding::encode(&self.config.clickhouse_database)
            )
        } else {
            self.config.clickhouse_url.clone()
        };
        let response = self
            .client
            .post(url)
            .basic_auth(
                self.config.clickhouse_user.clone(),
                Some(self.config.clickhouse_password()),
            )
            .header("Content-Type", "text/plain; charset=utf-8")
            .body(body.to_string())
            .send()
            .await
            .map_err(|error| error.to_string())?;
        let status = response.status();
        let text = response.text().await.map_err(|error| error.to_string())?;
        if !status.is_success() {
            return Err(format!("ClickHouse HTTP {status}: {text}"));
        }
        Ok(text)
    }
}

fn escape_sql(value: &str) -> String {
    value.replace('\\', "\\\\").replace('\'', "\\'")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn evaluates_typed_rule_conditions_from_data_field_rows() {
        let row = json!({"ticker":"ABC","price_change_1_bar_pct":5.4,"session_phase":"regular"});
        let rule = json!({"operator":"all","conditions":[
            {"left_source_id":"price_change_1_bar_pct","comparator":"greater_or_equal","value":5.0},
            {"left_source_id":"session_phase","comparator":"equals","value":"REGULAR"}
        ]});
        assert!(rule_matches(Some(&rule), &row));
    }

    #[test]
    fn field_instance_lookup_falls_back_to_stable_data_field_identity() {
        let row = json!({"price_change_1_bar_pct":5.4});
        assert_eq!(
            lookup_value(&row, "data.price_change_1_bar_pct@1:value"),
            Some(&json!(5.4))
        );
    }

    #[test]
    fn incremental_snapshot_returns_only_sequences_after_cursor() {
        let mut store = SignalStreamStore::default();
        append_occurrence(
            &mut store,
            json!({"event_id":"a","signal_stream_id":"squeeze","ticker":"AAA","event_time":"2026-08-17T15:00:00Z","sequence":1}),
        );
        append_occurrence(
            &mut store,
            json!({"event_id":"b","signal_stream_id":"squeeze","ticker":"BBB","event_time":"2026-08-17T15:01:00Z","sequence":2}),
        );

        let snapshot = snapshot_locked(&store, Some("squeeze"), None, Some(1), 5000);

        assert!(snapshot.occurrences.is_empty());
        assert_eq!(snapshot.occurrence_count, 2);
        assert_eq!(snapshot.new_occurrences.len(), 1);
        assert_eq!(snapshot.new_occurrences[0]["event_id"], "b");
    }
}
