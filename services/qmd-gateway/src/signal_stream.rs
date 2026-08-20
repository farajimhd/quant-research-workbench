use crate::config::GatewayConfig;
use chrono::{DateTime, Utc};
use reqwest::Client;
use ring::digest::{digest, SHA256};
use serde::{Deserialize, Serialize};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet, VecDeque};
use std::sync::Arc;
use std::time::Duration;
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
    #[serde(default)]
    pub recovery_templates: Vec<Value>,
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
    pub recovery: Value,
}

#[derive(Clone)]
pub struct SharedSignalStreamStore {
    inner: Arc<Mutex<SignalStreamStore>>,
    mutation: Arc<Mutex<()>>,
    writer: SignalStreamClickHouseWriter,
    recovery: Arc<Mutex<SignalRecoveryState>>,
    history_url: String,
    history_client: Client,
}

#[derive(Clone, Default)]
struct MatchState {
    matching: bool,
    last_emitted_at: Option<DateTime<Utc>>,
    observed_at: Option<DateTime<Utc>>,
    definition_revision: String,
}

#[derive(Clone, Default)]
struct PendingBaseline {
    at: Option<DateTime<Utc>>,
    definition_revision: String,
    row: Value,
}

#[derive(Default)]
struct SignalRecoveryState {
    key: String,
    status: String,
    attempts: u64,
    started_at: Option<DateTime<Utc>>,
    completed_at: Option<DateTime<Utc>>,
    recovery_through: Option<DateTime<Utc>>,
    recovered_count: usize,
    source_revision: Value,
    last_error: String,
    active: bool,
    pending_baselines: HashMap<(String, String), PendingBaseline>,
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
        let history_url = config
            .qmd_history_gateway_url
            .trim_end_matches('/')
            .to_string();
        let writer = SignalStreamClickHouseWriter::new(config);
        writer.initialize().await?;
        Ok(Self {
            inner: Arc::new(Mutex::new(SignalStreamStore::default())),
            mutation: Arc::new(Mutex::new(())),
            writer,
            recovery: Arc::new(Mutex::new(SignalRecoveryState::default())),
            history_url,
            history_client: Client::new(),
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
        drop(store);
        self.schedule_recovery().await;
        let recovery = self.recovery_snapshot().await;
        let store = self.inner.lock().await;
        Ok(snapshot_locked(&store, None, None, None, 0, recovery))
    }

    pub async fn evaluate(
        &self,
        request: SignalStreamEvaluateRequest,
    ) -> Result<SignalStreamSnapshot, String> {
        let _mutation = self.mutation.lock().await;
        let mut recovery = self.recovery.lock().await;
        let recovering = recovery.active && recovery.status == "recovering";
        let recovery_snapshot = recovery_value(&recovery);
        let store = self.inner.lock().await;
        let configuration = store
            .configuration
            .clone()
            .ok_or_else(|| "QMD Signal Stream configuration is not materialized".to_string())?;
        if request.as_of < configuration.session_start_utc
            || request.as_of > configuration.session_end_utc
        {
            return Ok(snapshot_locked(
                &store,
                None,
                Some(request.as_of),
                None,
                0,
                recovery_snapshot,
            ));
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
                let state_was_known = next_states.contains_key(&key);
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
                next.observed_at = Some(request.as_of);
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
                    if recovering && !state_was_known {
                        recovery.pending_baselines.insert(
                            key.clone(),
                            PendingBaseline {
                                at: Some(request.as_of),
                                definition_revision: definition_revision.clone(),
                                row: row.clone(),
                            },
                        );
                    } else {
                        let event_id = string(&occurrence, "event_id").unwrap_or("");
                        if !event_id.is_empty() && !store.event_ids.contains(event_id) {
                            pending.push(occurrence);
                            emitted_count += 1;
                        }
                        next.last_emitted_at = Some(request.as_of);
                    }
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
        drop(recovery);
        self.writer.insert(&pending).await?;
        let recovery_snapshot = self.recovery_snapshot().await;
        let mut store = self.inner.lock().await;
        store.states = next_states;
        store.diagnostics = diagnostics;
        for occurrence in &pending {
            append_occurrence(&mut store, occurrence.clone());
        }
        Ok(snapshot_locked(
            &store,
            None,
            Some(request.as_of),
            None,
            0,
            recovery_snapshot,
        )
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
        let recovery_snapshot = self.recovery_snapshot().await;
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
        Ok(
            snapshot_locked(&store, None, None, None, 0, recovery_snapshot)
                .with_new_occurrences(pending),
        )
    }

    pub async fn snapshot(&self, query: SignalStreamSnapshotQuery) -> SignalStreamSnapshot {
        let recovery = self.recovery_snapshot().await;
        let store = self.inner.lock().await;
        snapshot_locked(
            &store,
            query.signal_stream_id.as_deref(),
            query.as_of,
            query.after_sequence,
            query.limit.unwrap_or(5_000).clamp(1, 50_000),
            recovery,
        )
    }

    async fn recovery_snapshot(&self) -> Value {
        let recovery = self.recovery.lock().await;
        recovery_value(&recovery)
    }

    async fn schedule_recovery(&self) {
        let configuration = self.inner.lock().await.configuration.clone();
        let Some(configuration) = configuration else {
            return;
        };
        let recoverable = configuration
            .recovery_templates
            .iter()
            .any(|row| string(row, "recovery_kind") == Some("qmd_history_timeline"));
        let unavailable = configuration
            .recovery_templates
            .iter()
            .filter(|row| string(row, "recovery_kind") == Some("coverage_unavailable"))
            .count();
        let key = format!(
            "{}|{}",
            configuration.session_key, configuration.configuration_revision
        );
        if !recoverable {
            let mut recovery = self.recovery.lock().await;
            recovery.key = key;
            recovery.status = if unavailable > 0 {
                "coverage_incomplete"
            } else {
                "source_native"
            }
            .to_string();
            recovery.active = false;
            return;
        }
        let now = Utc::now();
        let cutoff = (now - chrono::Duration::seconds(5)).min(configuration.session_end_utc);
        if cutoff <= configuration.session_start_utc {
            return;
        }
        {
            let mut recovery = self.recovery.lock().await;
            if recovery.key == key && (recovery.active || recovery.status == "complete") {
                return;
            }
            recovery.key = key.clone();
            recovery.status = "recovering".to_string();
            recovery.attempts = recovery.attempts.saturating_add(1);
            recovery.started_at = Some(now);
            recovery.completed_at = None;
            recovery.recovery_through = Some(cutoff);
            recovery.last_error.clear();
            recovery.active = true;
            recovery.pending_baselines.clear();
        }
        let runtime = self.clone();
        tokio::spawn(async move {
            loop {
                match runtime
                    .recover_session(key.clone(), configuration.clone(), cutoff)
                    .await
                {
                    Ok(()) => return,
                    Err(error) => {
                        let mut recovery = runtime.recovery.lock().await;
                        if recovery.key != key {
                            return;
                        }
                        recovery.status = if error.contains("complete pinned market-event window")
                            || error.contains("not complete_for_history")
                            || error.contains("coverage")
                        {
                            "coverage_incomplete"
                        } else {
                            "retryable_error"
                        }
                        .to_string();
                        recovery.last_error = error;
                        // Keep ownership of the recovery loop inside QMD. An
                        // idempotent configuration PUT or a Canvas open must
                        // never become the mechanism that starts another scan.
                        recovery.active = true;
                        recovery.completed_at = Some(Utc::now());
                    }
                }
                tokio::time::sleep(Duration::from_secs(30)).await;
                let mut recovery = runtime.recovery.lock().await;
                if recovery.key != key || recovery.status == "complete" {
                    return;
                }
                recovery.status = "recovering".to_string();
                recovery.attempts = recovery.attempts.saturating_add(1);
                recovery.started_at = Some(Utc::now());
                recovery.completed_at = None;
            }
        });
    }

    async fn recover_session(
        &self,
        key: String,
        configuration: SignalStreamConfigurationRequest,
        cutoff: DateTime<Utc>,
    ) -> Result<(), String> {
        let mut requests = Vec::new();
        for template in &configuration.recovery_templates {
            if string(template, "recovery_kind") != Some("qmd_history_timeline") {
                continue;
            }
            let plan = bounded_recovery_plan(
                template
                    .get("plan")
                    .cloned()
                    .ok_or_else(|| "Signal recovery template has no plan".to_string())?,
                configuration.session_start_utc,
                cutoff,
            )?;
            requests.push(json!({
                "plan": plan,
                "external_feature_revisions": template
                    .get("external_feature_revisions")
                    .cloned()
                    .unwrap_or_else(|| json!([])),
                "external_feature_intervals": template
                    .get("external_feature_intervals")
                    .cloned()
                    .unwrap_or_else(|| json!([])),
            }));
        }
        if requests.is_empty() {
            return Ok(());
        }
        let response = self
            .history_client
            .post(format!(
                "{}/materialize/watchlist-timelines",
                self.history_url
            ))
            .timeout(Duration::from_secs(600))
            .json(&json!({"requests": requests}))
            .send()
            .await
            .map_err(|error| format!("QMD History signal recovery request failed: {error}"))?;
        let status = response.status();
        let payload = response.json::<Value>().await.map_err(|error| {
            format!("QMD History signal recovery response was invalid: {error}")
        })?;
        if !status.is_success() {
            return Err(format!(
                "QMD History signal recovery failed status={status}: {}",
                payload
                    .get("error")
                    .and_then(Value::as_str)
                    .unwrap_or("unknown error")
            ));
        }
        let source_revision = payload
            .get("source_revision")
            .ok_or_else(|| "QMD History signal recovery has no source revision".to_string())?;
        if source_revision
            .get("request_complete")
            .and_then(Value::as_bool)
            != Some(true)
            || source_revision
                .get("complete_for_history")
                .and_then(Value::as_bool)
                != Some(true)
        {
            return Err(
                "QMD History signal recovery source window is not complete_for_history".to_string(),
            );
        }
        let recovered_count = self
            .merge_recovery(&configuration, cutoff, &payload)
            .await?;
        let mut recovery = self.recovery.lock().await;
        if recovery.key == key {
            recovery.status = "complete".to_string();
            recovery.active = false;
            recovery.completed_at = Some(Utc::now());
            recovery.recovery_through = Some(cutoff);
            recovery.recovered_count = recovered_count;
            recovery.source_revision = payload
                .get("source_revision")
                .cloned()
                .unwrap_or(Value::Null);
            recovery.last_error.clear();
            recovery.pending_baselines.clear();
        }
        Ok(())
    }

    async fn merge_recovery(
        &self,
        configuration: &SignalStreamConfigurationRequest,
        cutoff: DateTime<Utc>,
        payload: &Value,
    ) -> Result<usize, String> {
        let _mutation = self.mutation.lock().await;
        let mut recovery = self.recovery.lock().await;
        let mut store = self.inner.lock().await;
        let rules = configuration
            .rule_sets
            .iter()
            .filter_map(|rule| Some((string(rule, "rule_set_id")?.to_string(), rule.clone())))
            .collect::<HashMap<_, _>>();
        let stream_by_id = configuration
            .streams
            .iter()
            .filter_map(|stream| {
                Some((
                    string(stream, "signal_stream_id")?.to_string(),
                    stream.clone(),
                ))
            })
            .collect::<HashMap<_, _>>();
        let mut recovered_states = HashMap::<(String, String), bool>::new();
        let mut pending = Vec::<Value>::new();
        for materialization in payload
            .get("materializations")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
        {
            let stream_id = string(materialization, "watchlist_id")
                .unwrap_or("")
                .strip_prefix("signal-recovery:")
                .unwrap_or("")
                .to_string();
            let Some(stream) = stream_by_id.get(&stream_id) else {
                continue;
            };
            let selected_rules = stream
                .get("inclusion_rule_sets")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
                .filter_map(Value::as_str)
                .collect::<Vec<_>>();
            let revision = definition_revision(stream, &rules, &selected_rules);
            for chunk in materialization
                .get("chunks")
                .and_then(Value::as_array)
                .into_iter()
                .flatten()
            {
                for transition in chunk
                    .get("transitions")
                    .and_then(Value::as_array)
                    .into_iter()
                    .flatten()
                {
                    let symbol = string(transition, "ticker")
                        .unwrap_or("")
                        .to_ascii_uppercase();
                    if symbol.is_empty() {
                        continue;
                    }
                    let key = (stream_id.clone(), symbol.clone());
                    match string(transition, "event").unwrap_or("") {
                        "added" => {
                            recovered_states.insert(key, true);
                            let at =
                                datetime_value(transition, "effective_at").ok_or_else(|| {
                                    "Recovered Signal transition has no time".to_string()
                                })?;
                            let row = recovery_row(stream, transition, configuration);
                            pending.push(occurrence(
                                stream,
                                &row,
                                at,
                                configuration,
                                &revision,
                                "qmd_history_signal_recovery",
                            ));
                        }
                        "removed" => {
                            recovered_states.insert(key, false);
                        }
                        _ => {}
                    }
                }
            }
        }
        for (key, baseline) in recovery.pending_baselines.drain() {
            if recovered_states.get(&key).copied().unwrap_or(false) {
                continue;
            }
            let Some(at) = baseline.at else {
                continue;
            };
            let Some(stream) = stream_by_id.get(&key.0) else {
                continue;
            };
            pending.push(occurrence(
                stream,
                &baseline.row,
                at,
                configuration,
                &baseline.definition_revision,
                "qmd_live_recovery_handoff",
            ));
        }
        for (key, matching) in recovered_states {
            let state = store.states.entry(key).or_default();
            if state.observed_at.is_none_or(|observed| observed <= cutoff) {
                state.matching = matching;
                state.observed_at = Some(cutoff);
            }
        }
        pending.sort_by_key(|row| datetime_value(row, "event_time"));
        pending.retain(|row| {
            string(row, "event_id").is_some_and(|event_id| !store.event_ids.contains(event_id))
        });
        let pending = assign_sequences(&store, pending);
        drop(store);
        drop(recovery);
        self.writer.insert(&pending).await?;
        let mut store = self.inner.lock().await;
        let inserted_count = pending.len();
        for occurrence in pending {
            append_occurrence(&mut store, occurrence);
        }
        Ok(inserted_count)
    }
}

fn recovery_value(state: &SignalRecoveryState) -> Value {
    json!({
        "status": if state.status.is_empty() { "not_started" } else { state.status.as_str() },
        "active": state.active,
        "attempts": state.attempts,
        "started_at": state.started_at,
        "completed_at": state.completed_at,
        "recovery_through": state.recovery_through,
        "recovered_count": state.recovered_count,
        "source_revision": state.source_revision,
        "last_error": state.last_error,
    })
}

fn bounded_recovery_plan(
    mut plan: Value,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> Result<Value, String> {
    let object = plan
        .as_object_mut()
        .ok_or_else(|| "Signal recovery plan must be an object".to_string())?;
    object.insert("start".to_string(), json!(start.to_rfc3339()));
    object.insert("end".to_string(), json!(end.to_rfc3339()));
    object.insert(
        "evaluation_windows".to_string(),
        json!([{"start": start.to_rfc3339(), "end": end.to_rfc3339()}]),
    );
    object.remove("plan_hash");
    let encoded = serde_json::to_vec(&plan)
        .map_err(|error| format!("Signal recovery plan encoding failed: {error}"))?;
    plan["plan_hash"] = json!(format!("sha256:{}", sha256_bytes(&encoded)));
    Ok(plan)
}

fn recovery_row(
    stream: &Value,
    transition: &Value,
    configuration: &SignalStreamConfigurationRequest,
) -> Value {
    let mut row = transition
        .get("evidence")
        .and_then(Value::as_object)
        .cloned()
        .unwrap_or_default();
    let symbol = string(transition, "ticker").unwrap_or("");
    row.insert("ticker".to_string(), json!(symbol));
    row.insert("symbol".to_string(), json!(symbol));
    let intervals = stream.get("column_intervals").and_then(Value::as_object);
    let aggregations = stream.get("column_aggregations").and_then(Value::as_object);
    for column_id in stream
        .get("columns")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
    {
        let Some(column) = configuration
            .column_catalog
            .iter()
            .find(|column| string(column, "column_id") == Some(column_id))
        else {
            continue;
        };
        let source_id = string(column, "source_id").unwrap_or("");
        let interval = intervals
            .and_then(|values| values.get(column_id))
            .and_then(interval_value);
        let mut instance = interval
            .map(|value| format!("{source_id}@@{value}"))
            .unwrap_or_else(|| source_id.to_string());
        if let Some(aggregation) = aggregations
            .and_then(|values| values.get(column_id))
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
        {
            instance.push_str("##");
            instance.push_str(aggregation);
        }
        if let Some(value) = row
            .get(&instance)
            .cloned()
            .or_else(|| row.get(source_id).cloned())
        {
            row.insert(column_id.to_string(), value);
        }
    }
    Value::Object(row)
}

fn interval_value(value: &Value) -> Option<String> {
    let object = value.as_object()?;
    let amount = object.get("value")?.as_u64()?;
    let suffix = match object.get("unit")?.as_str()? {
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
}

fn sha256_bytes(value: &[u8]) -> String {
    digest(&SHA256, value)
        .as_ref()
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
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
                observed_at: at,
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
    recovery: Value,
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
    let overall_recovery_status = recovery
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("not_started");
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
                    if let Some(template) = config
                        .recovery_templates
                        .iter()
                        .find(|template| string(template, "signal_stream_id") == Some(id))
                    {
                        let kind =
                            string(template, "recovery_kind").unwrap_or("coverage_unavailable");
                        value["recovery_kind"] = json!(kind);
                        value["recovery_status"] = json!(match kind {
                            "qmd_history_timeline" => overall_recovery_status,
                            "source_native" => "source_native",
                            _ => "coverage_incomplete",
                        });
                        if let Some(reason) = template.get("reason") {
                            value["recovery_reason"] = reason.clone();
                        }
                    }
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
        recovery,
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

        let snapshot = snapshot_locked(&store, Some("squeeze"), None, Some(1), 5000, Value::Null);

        assert!(snapshot.occurrences.is_empty());
        assert_eq!(snapshot.occurrence_count, 2);
        assert_eq!(snapshot.new_occurrences.len(), 1);
        assert_eq!(snapshot.new_occurrences[0]["event_id"], "b");
    }

    #[test]
    fn recovery_plan_rebinds_bounds_and_hashes_exact_content() {
        let start = "2026-08-17T08:00:00Z".parse().unwrap();
        let end = "2026-08-17T15:00:00Z".parse().unwrap();
        let plan = bounded_recovery_plan(
            json!({"watchlist_id":"signal-recovery:squeeze","plan_hash":"stale"}),
            start,
            end,
        )
        .unwrap();
        assert_eq!(plan["start"], "2026-08-17T08:00:00+00:00");
        assert_eq!(plan["end"], "2026-08-17T15:00:00+00:00");
        assert!(plan["plan_hash"].as_str().unwrap().starts_with("sha256:"));
    }

    #[test]
    fn recovered_evidence_maps_exact_interval_and_aggregation_to_canvas_column() {
        let configuration = SignalStreamConfigurationRequest {
            configuration_revision: "rev".to_string(),
            session_key: "2026-08-17".to_string(),
            session_start_utc: "2026-08-17T08:00:00Z".parse().unwrap(),
            session_end_utc: "2026-08-18T00:00:00Z".parse().unwrap(),
            streams: Vec::new(),
            rule_sets: Vec::new(),
            column_catalog: vec![json!({
                "column_id":"bid-price",
                "source_id":"quote.bid_price"
            })],
            recovery_templates: Vec::new(),
        };
        let stream = json!({
            "columns":["bid-price"],
            "column_intervals":{"bid-price":{"value":100,"unit":"milliseconds"}},
            "column_aggregations":{"bid-price":"max"}
        });
        let transition = json!({
            "ticker":"ABC",
            "evidence":{"quote.bid_price@@100ms##max":12.34}
        });
        let row = recovery_row(&stream, &transition, &configuration);
        assert_eq!(row["bid-price"], 12.34);
        assert_eq!(row["ticker"], "ABC");
    }
}
