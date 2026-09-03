use crate::bars::SharedBarStore;
use crate::computation_targets::{
    capability_requires_generic_structure, ComputationTargetLease, SharedComputationTargets,
};
use crate::config::GatewayConfig;
use crate::gapfill::GapFillService;
use crate::generic_structure::{GenericStructureCheckpoint, GENERIC_STRUCTURE_ALGORITHM_VERSION};
use crate::indicators::{DailyStructureCheckpoint, IndicatorClickHouseWriter};
use crate::structure_certification::{
    build_checkpoint_certification, checkpoint_sha256, validate_checkpoint_certification,
    StructureReplayEvidence,
};
use chrono::{DateTime, Duration as ChronoDuration, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::json;
use std::collections::BTreeMap;
use std::sync::{Arc, Weak};
use std::time::Duration;
use tokio::sync::{Mutex, OwnedMutexGuard};

#[derive(Clone, Default)]
struct SymbolActivationLocks {
    inner: Arc<Mutex<BTreeMap<String, Weak<Mutex<()>>>>>,
}

impl SymbolActivationLocks {
    async fn acquire(&self, tickers: &[String]) -> Vec<OwnedMutexGuard<()>> {
        // Computation target tickers are normalized, but sorting here keeps
        // multi-symbol acquisitions deadlock-free for every caller.
        let mut symbols = tickers.to_vec();
        symbols.sort();
        symbols.dedup();
        let locks = {
            let mut registry = self.inner.lock().await;
            registry.retain(|_, lock| lock.strong_count() > 0);
            symbols
                .into_iter()
                .map(|symbol| {
                    if let Some(lock) = registry.get(&symbol).and_then(Weak::upgrade) {
                        return lock;
                    }
                    let lock = Arc::new(Mutex::new(()));
                    registry.insert(symbol, Arc::downgrade(&lock));
                    lock
                })
                .collect::<Vec<_>>()
        };
        let mut guards = Vec::with_capacity(locks.len());
        for lock in locks {
            guards.push(lock.lock_owned().await);
        }
        guards
    }
}

#[derive(Clone)]
pub struct StructureFocusCoordinator {
    bars: SharedBarStore,
    checkpoint_store: IndicatorClickHouseWriter,
    client: Client,
    rebuild_client: Client,
    focused_repair: GapFillService,
    history_url: String,
    inactive_advance_hours: u64,
    inactive_batch_size: usize,
    inactive_registry: Arc<Mutex<BTreeMap<String, DateTime<Utc>>>>,
    inactive_registry_limit: usize,
    cold_rebuild_days: u64,
    staging_max_events: usize,
    activation_locks: SymbolActivationLocks,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureFocusActivation {
    pub ticker: String,
    pub already_active: bool,
    pub history_event_count: u64,
    pub history_advanced_event_count: u64,
    pub buffered_event_count: usize,
    pub checkpoint_updated_at: Option<DateTime<Utc>>,
    pub checkpoint_arrival_sequence: u64,
    pub source_plan_hash: String,
}

#[derive(Clone, Debug, Default)]
pub struct StructureFocusRestore {
    pub active: usize,
    pub blocked: usize,
}

#[derive(Clone, Debug, Default)]
pub struct StructureFocusAdvance {
    pub advanced: Vec<String>,
    pub blocked: Vec<String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureFocusRebuild {
    pub ticker: String,
    pub replay_start: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub checkpoint_updated_at: DateTime<Utc>,
    pub checkpoint_arrival_sequence: u64,
    pub source_plan_hash: String,
    pub source_revision_token: String,
    pub previous_error_code: String,
    pub previous_retry_action: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct DailyStructureCheckpointBuild {
    pub ticker: String,
    pub session_date: NaiveDate,
    pub seeded_from_session_date: Option<NaiveDate>,
    pub replay_start: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub checkpoint_updated_at: DateTime<Utc>,
    pub checkpoint_arrival_sequence: u64,
    pub source_plan_hash: String,
    pub source_revision_token: String,
    pub checkpoint_sha256: String,
    pub chain_sha256: String,
    pub status: String,
}

#[derive(Debug, Deserialize)]
struct HistoryErrorResponse {
    #[serde(default)]
    error: String,
    #[serde(default)]
    error_code: String,
    #[serde(default)]
    retry_action: String,
    #[serde(default = "default_true")]
    retryable: bool,
}

fn default_true() -> bool {
    true
}

#[derive(Debug, Deserialize)]
struct HistoryAdvanceResponse {
    checkpoint: GenericStructureCheckpoint,
    event_count: u64,
    advanced_event_count: u64,
    event_evidence: StructureReplayEvidence,
    source_plan: HistorySourcePlan,
    source_revision_before: HistorySourceRevision,
    source_revision_after: HistorySourceRevision,
    complete: bool,
}

#[derive(Debug, Deserialize)]
struct HistoryRebuildResponse {
    checkpoint: GenericStructureCheckpoint,
    ticker: String,
    as_of: DateTime<Utc>,
    replay_start: DateTime<Utc>,
    event_count: u64,
    advanced_event_count: u64,
    event_evidence: StructureReplayEvidence,
    source_plan: HistorySourcePlan,
    source_revision_before: HistorySourceRevision,
    source_revision_after: HistorySourceRevision,
    complete: bool,
}

#[derive(Debug, Deserialize)]
struct HistorySourceRevision {
    token: String,
    #[serde(default)]
    source_plan_hash: String,
    #[serde(default)]
    complete_for_history: bool,
    #[serde(default)]
    request_complete: bool,
}

#[derive(Debug, Deserialize)]
struct HistorySourcePlan {
    plan_hash: String,
    #[serde(default)]
    segments: Vec<HistorySourceSegment>,
}

#[derive(Debug, Deserialize)]
struct HistorySourceSegment {
    start: DateTime<Utc>,
    end: DateTime<Utc>,
    tier: String,
}

const CHECKPOINT_ADVANCE_SLICE_HOURS: i64 = 48;

impl StructureFocusCoordinator {
    pub fn new(
        config: &GatewayConfig,
        bars: SharedBarStore,
        checkpoint_store: IndicatorClickHouseWriter,
        focused_repair: GapFillService,
    ) -> Result<Self, String> {
        let client = Client::builder()
            .timeout(Duration::from_secs(
                config.structure_focus_history_timeout_seconds,
            ))
            .build()
            .map_err(|error| format!("failed to build QMD History client: {error}"))?;
        let rebuild_client = Client::builder()
            .timeout(Duration::from_secs(
                config.structure_focus_rebuild_timeout_seconds,
            ))
            .build()
            .map_err(|error| format!("failed to build QMD History rebuild client: {error}"))?;
        Ok(Self {
            bars,
            checkpoint_store,
            client,
            rebuild_client,
            focused_repair,
            history_url: config.qmd_history_gateway_url.clone(),
            inactive_advance_hours: config.structure_focus_inactive_advance_hours,
            inactive_batch_size: config.structure_focus_inactive_batch_size,
            inactive_registry: Arc::new(Mutex::new(BTreeMap::new())),
            inactive_registry_limit: config.structure_focus_inactive_registry_limit,
            cold_rebuild_days: config.structure_focus_cold_rebuild_days,
            staging_max_events: config.structure_focus_staging_max_events,
            activation_locks: SymbolActivationLocks::default(),
        })
    }

    pub async fn stage_and_activate(
        &self,
        lease: &ComputationTargetLease,
    ) -> Result<Vec<StructureFocusActivation>, String> {
        if !lease
            .capabilities
            .iter()
            .any(|capability| capability_requires_generic_structure(capability))
        {
            return Ok(Vec::new());
        }
        // Target upserts can arrive concurrently from chart history and live
        // refreshes. Serialize only overlapping symbols so the later request
        // observes and reuses the first request's activated structure instead
        // of colliding with its transient staging marker.
        let _activation_guards = self.activation_locks.acquire(&lease.tickers).await;
        let mut staged = Vec::new();
        let mut activated = Vec::new();
        let mut current_ticker = None::<String>;
        let result = async {
            for ticker in &lease.tickers {
                if self
                    .bars
                    .begin_structure_staging(ticker, self.staging_max_events)
                    .await?
                {
                    staged.push(ticker.clone());
                } else {
                    let checkpoint = self.bars.structure_checkpoint(ticker).await;
                    activated.push(StructureFocusActivation {
                        ticker: ticker.clone(),
                        already_active: true,
                        history_event_count: 0,
                        history_advanced_event_count: 0,
                        buffered_event_count: 0,
                        checkpoint_updated_at: checkpoint
                            .as_ref()
                            .and_then(|checkpoint| checkpoint.updated_at),
                        checkpoint_arrival_sequence: checkpoint
                            .map(|checkpoint| checkpoint.last_arrival_sequence)
                            .unwrap_or_default(),
                        source_plan_hash: "already_active".to_string(),
                    });
                }
            }
            for ticker in &staged {
                current_ticker = Some(ticker.clone());
                let mut seed_event_count = 0_u64;
                let mut seed_advanced_event_count = 0_u64;
                let mut seed_source_plan_hash = String::new();
                let checkpoint = if let Some(checkpoint) = self
                    .checkpoint_store
                    .load_structure_checkpoint(ticker)
                    .await?
                {
                    checkpoint
                } else if let Some(checkpoint) = self
                    .latest_compatible_daily_seed(ticker)
                    .await?
                {
                    checkpoint
                } else {
                    let now = Utc::now();
                    let rebuilt = self
                        .request_structure_rebuild(
                            ticker,
                            now - ChronoDuration::days(self.cold_rebuild_days as i64),
                            now - ChronoDuration::seconds(1),
                            None,
                        )
                        .await?;
                    seed_event_count = rebuilt.event_count;
                    seed_advanced_event_count = rebuilt.advanced_event_count;
                    seed_source_plan_hash = rebuilt.source_plan.plan_hash.clone();
                    rebuilt.checkpoint
                };
                if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
                    || checkpoint.last_arrival_sequence == 0
                    || checkpoint.updated_at.is_none()
                {
                    return Err(format!(
                        "persisted Generic Structure checkpoint for {ticker} lacks an exact current cursor"
                    ));
                }
                let gaps = self
                    .history_gap_segments(
                        ticker,
                        checkpoint.updated_at.expect("validated checkpoint time"),
                        Utc::now(),
                    )
                    .await?;
                if !gaps.is_empty() {
                    self.focused_repair
                        .repair_focused_ticker_intervals(ticker, &gaps)
                        .await?;
                }
                let advanced = self.advance_checkpoint_through(checkpoint, Utc::now()).await?;
                if !advanced.complete
                    || advanced.checkpoint.sym.to_ascii_uppercase() != *ticker
                    || advanced.checkpoint.algorithm_version
                        != GENERIC_STRUCTURE_ALGORITHM_VERSION
                {
                    return Err(format!(
                        "QMD History returned an incomplete or mismatched checkpoint for {ticker}"
                    ));
                }
                let (buffered_event_count, cursor) = self
                    .bars
                    .activate_structure_checkpoint(ticker, &advanced.checkpoint)
                    .await?;
                activated.push(StructureFocusActivation {
                    ticker: ticker.clone(),
                    already_active: false,
                    history_event_count: seed_event_count.saturating_add(advanced.event_count),
                    history_advanced_event_count: seed_advanced_event_count
                        .saturating_add(advanced.advanced_event_count),
                    buffered_event_count,
                    checkpoint_updated_at: advanced.checkpoint.updated_at,
                    checkpoint_arrival_sequence: cursor.1,
                    source_plan_hash: if seed_source_plan_hash.is_empty() {
                        advanced.source_plan.plan_hash
                    } else {
                        format!("{}+{}", seed_source_plan_hash, advanced.source_plan.plan_hash)
                    },
                });
            }
            Ok::<(), String>(())
        }
        .await;
        if let Err(error) = result {
            let quarantine = non_retryable_history_error(&error).and_then(|history_error| {
                current_ticker
                    .as_ref()
                    .map(|ticker| (ticker.clone(), history_error))
            });
            for ticker in &staged {
                self.bars.cancel_structure_staging(ticker).await;
            }
            for activation in &activated {
                if !activation.already_active {
                    self.bars.deactivate_structure(&activation.ticker).await;
                }
            }
            if let Some((ticker, history_error)) = quarantine {
                self.checkpoint_store
                    .persist_structure_focus_blocked(
                        &ticker,
                        &history_error.error_code,
                        &history_error.retry_action,
                        &history_error.error,
                    )
                    .await
                    .map_err(|persist_error| {
                        format!(
                            "{error}; additionally failed to persist blocked Generic Structure status for {ticker}: {persist_error}"
                        )
                    })?;
                self.inactive_registry.lock().await.remove(&ticker);
            }
            return Err(error);
        }
        let mut inactive = self.inactive_registry.lock().await;
        for activation in &activated {
            inactive.remove(&activation.ticker);
        }
        Ok(activated)
    }

    pub async fn restore_inactive_registry(&self) -> Result<StructureFocusRestore, String> {
        let (entries, blocked) = self
            .checkpoint_store
            .load_structure_focus_registry(self.inactive_registry_limit)
            .await?;
        let active = entries.len();
        self.inactive_registry.lock().await.extend(entries);
        Ok(StructureFocusRestore { active, blocked })
    }

    pub async fn rebuild_blocked_checkpoint(
        &self,
        ticker: &str,
        replay_start: DateTime<Utc>,
        as_of: DateTime<Utc>,
        event_limit: Option<usize>,
    ) -> Result<StructureFocusRebuild, String> {
        let ticker = ticker.trim().to_ascii_uppercase();
        if ticker.is_empty()
            || ticker.len() > 32
            || !ticker
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        {
            return Err("invalid Generic Structure rebuild ticker".to_string());
        }
        if replay_start >= as_of {
            return Err("Generic Structure rebuild start must precede as_of".to_string());
        }
        let _activation_guards = self.activation_locks.acquire(&[ticker.clone()]).await;
        if self.bars.active_structure_symbols().await.contains(&ticker) {
            return Err(format!(
                "Generic Structure rebuild refuses to replace active state for {ticker}"
            ));
        }
        let Some((state, error_code, retry_action)) = self
            .checkpoint_store
            .load_structure_focus_status(&ticker)
            .await?
        else {
            return Err(format!(
                "no Generic Structure focus registry record exists for {ticker}"
            ));
        };
        if state != "blocked"
            || error_code != "structure_checkpoint_source_incompatible"
            || retry_action != "rebuild_checkpoint_from_canonical_history"
        {
            return Err(format!(
                "Generic Structure checkpoint for {ticker} is not blocked for canonical-history rebuild"
            ));
        }
        let response = self
            .rebuild_client
            .post(format!(
                "{}/materialize/generic-structure-rebuild",
                self.history_url
            ))
            .json(&json!({
                "schema_version": 1,
                "ticker": ticker,
                "start": replay_start,
                "as_of": as_of,
                "event_limit": event_limit,
            }))
            .send()
            .await
            .map_err(|error| format!("QMD History checkpoint rebuild request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD History checkpoint rebuild returned HTTP {status}: {body}"
            ));
        }
        let rebuilt = response
            .json::<HistoryRebuildResponse>()
            .await
            .map_err(|error| format!("invalid QMD History rebuild response: {error}"))?;
        let checkpoint_updated_at = rebuilt
            .checkpoint
            .updated_at
            .ok_or_else(|| "rebuilt Generic Structure checkpoint lacks updated_at".to_string())?;
        if !rebuilt.complete
            || rebuilt.ticker != ticker
            || rebuilt.checkpoint.sym.to_ascii_uppercase() != ticker
            || rebuilt.checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
            || rebuilt.checkpoint.last_arrival_sequence == 0
            || rebuilt.source_revision_before.token != rebuilt.source_revision_after.token
            || rebuilt
                .source_plan
                .segments
                .iter()
                .any(|segment| segment.tier == "gap")
        {
            return Err(format!(
                "QMD History returned an incomplete or mismatched rebuild for {ticker}"
            ));
        }
        self.checkpoint_store
            .persist_structure_checkpoint(&rebuilt.checkpoint)
            .await?;
        let next_due = Utc::now() + ChronoDuration::hours(self.inactive_advance_hours as i64);
        self.checkpoint_store
            .persist_structure_focus_registry(&ticker, next_due)
            .await?;
        self.inactive_registry
            .lock()
            .await
            .insert(ticker.clone(), next_due);
        Ok(StructureFocusRebuild {
            ticker,
            replay_start: rebuilt.replay_start,
            as_of: rebuilt.as_of,
            event_count: rebuilt.event_count,
            advanced_event_count: rebuilt.advanced_event_count,
            checkpoint_updated_at,
            checkpoint_arrival_sequence: rebuilt.checkpoint.last_arrival_sequence,
            source_plan_hash: rebuilt.source_plan.plan_hash,
            source_revision_token: rebuilt.source_revision_after.token,
            previous_error_code: error_code,
            previous_retry_action: retry_action,
        })
    }

    pub async fn build_daily_checkpoint(
        &self,
        ticker: &str,
        session_date: NaiveDate,
        rebuild_start: DateTime<Utc>,
        event_limit: Option<usize>,
    ) -> Result<DailyStructureCheckpointBuild, String> {
        let ticker = ticker.trim().to_ascii_uppercase();
        if ticker.is_empty()
            || ticker.len() > 32
            || !ticker
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
        {
            return Err("invalid daily Generic Structure checkpoint ticker".to_string());
        }
        let local_end = New_York
            .from_local_datetime(&session_date.and_time(NaiveTime::from_hms_opt(20, 0, 0).unwrap()))
            .single()
            .ok_or_else(|| "invalid New York structure checkpoint session boundary".to_string())?;
        let as_of = local_end
            .with_timezone(&Utc)
            .checked_sub_signed(ChronoDuration::microseconds(1))
            .ok_or_else(|| "daily structure checkpoint session boundary underflow".to_string())?;
        if as_of >= Utc::now() {
            return Err("daily structure checkpoint session is not complete".to_string());
        }
        if rebuild_start >= as_of {
            return Err(
                "daily structure checkpoint rebuild_start must precede session end".to_string(),
            );
        }
        let _activation_guards = self.activation_locks.acquire(&[ticker.clone()]).await;
        let authority_end = as_of
            .checked_add_signed(ChronoDuration::microseconds(1))
            .ok_or_else(|| "daily structure checkpoint authority end overflow".to_string())?;
        let next_session_date = session_date
            .succ_opt()
            .ok_or_else(|| "daily structure checkpoint session date overflow".to_string())?;
        if let Some(existing) = self
            .checkpoint_store
            .load_daily_structure_checkpoint_before(&ticker, next_session_date)
            .await?
            .filter(|row| row.session_date == session_date)
            .filter(|row| daily_seed_covers_rebuild_start(row.authority_start, rebuild_start))
        {
            let current = self
                .history_source_revision(&ticker, existing.authority_start, authority_end)
                .await?;
            if current.complete_for_history
                && current.request_complete
                && current.source_plan_hash == existing.source_plan_hash
                && current.token == existing.source_revision_token
                && existing
                    .certification
                    .as_ref()
                    .is_some_and(|certification| {
                        validate_checkpoint_certification(
                            certification,
                            &existing.checkpoint,
                            existing.session_date,
                            existing.authority_start,
                            &existing.source_plan_hash,
                            &existing.source_revision_token,
                        )
                        .is_ok()
                    })
            {
                let checkpoint_updated_at = existing
                    .checkpoint
                    .updated_at
                    .ok_or_else(|| "daily structure checkpoint lacks updated_at".to_string())?;
                let replay_start = existing
                    .checkpoint
                    .replayed_through
                    .or(existing.checkpoint.updated_at)
                    .ok_or_else(|| "daily structure checkpoint lacks replay cursor".to_string())?;
                return Ok(DailyStructureCheckpointBuild {
                    ticker,
                    session_date,
                    seeded_from_session_date: Some(existing.session_date),
                    replay_start,
                    as_of,
                    event_count: 0,
                    advanced_event_count: 0,
                    checkpoint_updated_at,
                    checkpoint_arrival_sequence: existing.checkpoint.last_arrival_sequence,
                    source_plan_hash: existing.source_plan_hash,
                    source_revision_token: existing.source_revision_token,
                    checkpoint_sha256: existing
                        .certification
                        .as_ref()
                        .map(|value| value.checkpoint_sha256.clone())
                        .unwrap_or_default(),
                    chain_sha256: existing
                        .certification
                        .as_ref()
                        .map(|value| value.chain_sha256.clone())
                        .unwrap_or_default(),
                    status: "already_current".to_string(),
                });
            }
        }
        let seed = self
            .checkpoint_store
            .load_daily_structure_checkpoint_before(&ticker, session_date)
            .await?;
        // A daily seed is usable only when it covers at least the caller's
        // requested historical authority. This lets an operator replace a
        // shallow checkpoint with a months-long book instead of silently
        // advancing the incomplete seed forever.
        let seed = seed
            .filter(|seed| daily_seed_covers_rebuild_start(seed.authority_start, rebuild_start))
            .filter(|seed| {
                seed.certification.as_ref().is_some_and(|certification| {
                    validate_checkpoint_certification(
                        certification,
                        &seed.checkpoint,
                        seed.session_date,
                        seed.authority_start,
                        &seed.source_plan_hash,
                        &seed.source_revision_token,
                    )
                    .is_ok()
                })
            });
        // Resume is valid only when the complete authority that produced the
        // prior checkpoint is still identical. This revision includes both
        // compact-event continuity and the point-in-time split authority. A
        // corrected archive day or split therefore forces a canonical rebuild
        // instead of silently carrying stale geometry into later sessions.
        let seed = if let Some(seed) = seed {
            let seed_authority_end = New_York
                .from_local_datetime(
                    &seed
                        .session_date
                        .and_time(NaiveTime::from_hms_opt(20, 0, 0).unwrap()),
                )
                .single()
                .ok_or_else(|| "invalid prior daily checkpoint session boundary".to_string())?
                .with_timezone(&Utc);
            let current = self
                .history_source_revision(&ticker, seed.authority_start, seed_authority_end)
                .await?;
            if daily_seed_revision_is_compatible(
                &seed.source_plan_hash,
                &seed.source_revision_token,
                &current,
            ) {
                Some(seed)
            } else {
                eprintln!(
                    "Generic Structure daily checkpoint seed for {ticker} session {} is stale; rebuilding from canonical authority",
                    seed.session_date
                );
                None
            }
        } else {
            None
        };
        let (
            checkpoint,
            replay_start,
            event_count,
            advanced_event_count,
            _source_plan,
            _slice_revision,
            seeded_from,
            authority_start,
            event_evidence,
            predecessor_checkpoint_hash,
            predecessor_chain_hash,
        ) = if let Some(seed) = seed {
            let seed_checkpoint_hash = checkpoint_sha256(&seed.checkpoint)?;
            let seed_chain_hash = seed
                .certification
                .as_ref()
                .map(|value| value.chain_sha256.clone())
                .unwrap_or_default();
            let replay_start = seed
                .checkpoint
                .replayed_through
                .or(seed.checkpoint.updated_at)
                .ok_or_else(|| "daily structure seed lacks replay cursor".to_string())?;
            match self
                .advance_historical_checkpoint_through(seed.checkpoint.clone(), as_of)
                .await
            {
                Ok(advanced) => {
                    if !advanced.complete
                        || advanced.source_revision_before.token
                            != advanced.source_revision_after.token
                        || advanced
                            .source_plan
                            .segments
                            .iter()
                            .any(|segment| segment.tier == "gap")
                    {
                        return Err(
                            "daily structure checkpoint advancement was incomplete or source-inconsistent"
                                .to_string(),
                        );
                    }
                    (
                        advanced.checkpoint,
                        replay_start,
                        advanced.event_count,
                        advanced.advanced_event_count,
                        advanced.source_plan,
                        advanced.source_revision_after.token,
                        Some(seed.session_date),
                        seed.authority_start,
                        advanced.event_evidence,
                        seed_checkpoint_hash,
                        seed_chain_hash,
                    )
                }
                Err(error) if non_retryable_history_error(&error).is_some() => {
                    let rebuilt = self
                        .request_structure_rebuild(
                            &ticker,
                            seed.authority_start,
                            as_of,
                            event_limit,
                        )
                        .await?;
                    if !rebuilt.complete
                        || rebuilt.ticker != ticker
                        || rebuilt.checkpoint.algorithm_version
                            != GENERIC_STRUCTURE_ALGORITHM_VERSION
                        || rebuilt.source_revision_before.token
                            != rebuilt.source_revision_after.token
                        || rebuilt
                            .source_plan
                            .segments
                            .iter()
                            .any(|segment| segment.tier == "gap")
                    {
                        return Err(
                            "daily structure checkpoint archive fallback was incomplete or source-inconsistent"
                                .to_string(),
                        );
                    }
                    (
                        rebuilt.checkpoint,
                        rebuilt.replay_start,
                        rebuilt.event_count,
                        rebuilt.advanced_event_count,
                        rebuilt.source_plan,
                        rebuilt.source_revision_after.token,
                        None,
                        seed.authority_start,
                        rebuilt.event_evidence,
                        String::new(),
                        String::new(),
                    )
                }
                Err(error) => return Err(error),
            }
        } else {
            let response = self
                .rebuild_client
                .post(format!(
                    "{}/materialize/generic-structure-rebuild",
                    self.history_url
                ))
                .json(&json!({
                    "schema_version": 1,
                    "ticker": ticker,
                    "start": rebuild_start,
                    "as_of": as_of,
                    "event_limit": event_limit,
                }))
                .send()
                .await
                .map_err(|error| {
                    format!("QMD History daily checkpoint rebuild request failed: {error}")
                })?;
            let status = response.status();
            if !status.is_success() {
                let body = response.text().await.unwrap_or_default();
                return Err(format!(
                    "QMD History daily checkpoint rebuild returned HTTP {status}: {body}"
                ));
            }
            let rebuilt = response
                .json::<HistoryRebuildResponse>()
                .await
                .map_err(|error| format!("invalid QMD History daily rebuild response: {error}"))?;
            if !rebuilt.complete
                || rebuilt.ticker != ticker
                || rebuilt.checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
                || rebuilt.source_revision_before.token != rebuilt.source_revision_after.token
                || rebuilt
                    .source_plan
                    .segments
                    .iter()
                    .any(|segment| segment.tier == "gap")
            {
                return Err(
                    "daily structure checkpoint rebuild was incomplete or source-inconsistent"
                        .to_string(),
                );
            }
            (
                rebuilt.checkpoint,
                rebuilt.replay_start,
                rebuilt.event_count,
                rebuilt.advanced_event_count,
                rebuilt.source_plan,
                rebuilt.source_revision_after.token,
                None,
                rebuild_start,
                rebuilt.event_evidence,
                String::new(),
                String::new(),
            )
        };
        let checkpoint_updated_at = checkpoint
            .updated_at
            .ok_or_else(|| "daily structure checkpoint lacks updated_at".to_string())?;
        if checkpoint.sym.to_ascii_uppercase() != ticker
            || checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
            || checkpoint.last_arrival_sequence == 0
        {
            return Err("daily structure checkpoint payload identity is invalid".to_string());
        }
        let authority_revision = self
            .history_source_revision(&ticker, authority_start, authority_end)
            .await?;
        if !authority_revision.complete_for_history
            || !authority_revision.request_complete
            || authority_revision.source_plan_hash.trim().is_empty()
            || authority_revision.token.trim().is_empty()
        {
            return Err("daily structure checkpoint full authority is incomplete".to_string());
        }
        let certification = build_checkpoint_certification(
            &checkpoint,
            event_evidence,
            session_date,
            authority_start,
            &authority_revision.source_plan_hash,
            &authority_revision.token,
            predecessor_checkpoint_hash,
            predecessor_chain_hash,
        )?;
        let checkpoint_hash = certification.checkpoint_sha256.clone();
        let chain_hash = certification.chain_sha256.clone();
        let record = DailyStructureCheckpoint {
            checkpoint_set_id: self
                .checkpoint_store
                .structure_checkpoint_set_id()
                .to_string(),
            session_date,
            algorithm_version: checkpoint.algorithm_version,
            sym: ticker.clone(),
            authority_start,
            checkpoint_at: checkpoint_updated_at,
            last_arrival_sequence: checkpoint.last_arrival_sequence,
            source_plan_hash: authority_revision.source_plan_hash.clone(),
            source_revision_token: authority_revision.token.clone(),
            source_complete: true,
            built_at: Utc::now(),
            checkpoint: checkpoint.clone(),
            certification: Some(certification),
        };
        self.checkpoint_store
            .persist_daily_structure_checkpoint(&record)
            .await?;
        Ok(DailyStructureCheckpointBuild {
            ticker,
            session_date,
            seeded_from_session_date: seeded_from,
            replay_start,
            as_of,
            event_count,
            advanced_event_count,
            checkpoint_updated_at,
            checkpoint_arrival_sequence: checkpoint.last_arrival_sequence,
            source_plan_hash: authority_revision.source_plan_hash,
            source_revision_token: authority_revision.token,
            checkpoint_sha256: checkpoint_hash,
            chain_sha256: chain_hash,
            status: "completed".to_string(),
        })
    }

    pub async fn persist_and_reclaim_unused(
        &self,
        targets: &SharedComputationTargets,
    ) -> Result<Vec<String>, String> {
        let mut reclaimed = Vec::new();
        for ticker in self.bars.active_structure_symbols().await {
            if targets.requires_generic_structure(&ticker) {
                continue;
            }
            let Some(checkpoint) = self.bars.structure_checkpoint(&ticker).await else {
                continue;
            };
            self.checkpoint_store
                .persist_structure_checkpoint(&checkpoint)
                .await?;
            {
                let inactive = self.inactive_registry.lock().await;
                if !inactive.contains_key(&ticker) && inactive.len() >= self.inactive_registry_limit
                {
                    return Err(format!(
                        "inactive Generic Structure registry is full at {}; keeping {ticker} active",
                        self.inactive_registry_limit
                    ));
                }
            }
            let next_due = Utc::now() + ChronoDuration::hours(self.inactive_advance_hours as i64);
            self.checkpoint_store
                .persist_structure_focus_registry(&ticker, next_due)
                .await?;
            self.inactive_registry
                .lock()
                .await
                .insert(ticker.clone(), next_due);
            self.bars.deactivate_structure(&ticker).await;
            reclaimed.push(ticker);
        }
        Ok(reclaimed)
    }

    pub async fn apply_due_split_adjustments(
        &self,
        now: DateTime<Utc>,
    ) -> Result<Vec<String>, String> {
        let mut adjusted = Vec::new();
        for ticker in self.bars.active_structure_symbols().await {
            let Some(checkpoint) = self.bars.structure_checkpoint(&ticker).await else {
                continue;
            };
            let start_date = checkpoint
                .updated_at
                .unwrap_or(now)
                .with_timezone(&New_York)
                .date_naive();
            let end_date = now.with_timezone(&New_York).date_naive();
            for adjustment in self
                .checkpoint_store
                .structure_split_adjustments(&ticker, start_date, end_date)
                .await?
                .into_iter()
                .filter(|adjustment| adjustment.effective_at <= now)
            {
                if self
                    .bars
                    .apply_structure_split_adjustment(&ticker, &adjustment)
                    .await?
                {
                    let successor =
                        self.bars
                            .structure_checkpoint(&ticker)
                            .await
                            .ok_or_else(|| {
                                format!("split-adjusted checkpoint disappeared for {ticker}")
                            })?;
                    self.checkpoint_store
                        .persist_structure_checkpoint(&successor)
                        .await?;
                    adjusted.push(format!(
                        "{}:{}:{}-for-{}",
                        ticker,
                        adjustment.execution_date,
                        adjustment.split_from,
                        adjustment.split_to
                    ));
                }
            }
        }
        Ok(adjusted)
    }

    pub async fn advance_inactive_due(&self) -> Result<StructureFocusAdvance, String> {
        let now = Utc::now();
        let due = {
            let mut inactive = self.inactive_registry.lock().await;
            let due = inactive
                .iter()
                .filter(|(_, next_due)| **next_due <= now)
                .take(self.inactive_batch_size)
                .map(|(ticker, _)| ticker.clone())
                .collect::<Vec<_>>();
            for ticker in &due {
                inactive.insert(ticker.clone(), now + ChronoDuration::minutes(5));
            }
            due
        };
        let mut advanced = Vec::new();
        let mut blocked = Vec::new();
        for ticker in due {
            let checkpoint = self
                .checkpoint_store
                .load_structure_checkpoint(&ticker)
                .await?
                .ok_or_else(|| format!("inactive checkpoint disappeared for {ticker}"))?;
            let gaps = self
                .history_gap_segments(
                    &ticker,
                    checkpoint
                        .updated_at
                        .ok_or_else(|| format!("inactive checkpoint lacks time for {ticker}"))?,
                    Utc::now(),
                )
                .await?;
            if !gaps.is_empty() {
                self.focused_repair
                    .repair_focused_ticker_intervals(&ticker, &gaps)
                    .await?;
            }
            let response = match self
                .advance_checkpoint_through(checkpoint, Utc::now())
                .await
            {
                Ok(response) => response,
                Err(error) => {
                    if let Some(history_error) = non_retryable_history_error(&error) {
                        self.checkpoint_store
                            .persist_structure_focus_blocked(
                                &ticker,
                                &history_error.error_code,
                                &history_error.retry_action,
                                &history_error.error,
                            )
                            .await?;
                        self.inactive_registry.lock().await.remove(&ticker);
                        blocked.push(ticker);
                        continue;
                    }
                    return Err(format!("{ticker}: {error}"));
                }
            };
            self.checkpoint_store
                .persist_structure_checkpoint(&response.checkpoint)
                .await?;
            let next_due = Utc::now() + ChronoDuration::hours(self.inactive_advance_hours as i64);
            self.checkpoint_store
                .persist_structure_focus_registry(&ticker, next_due)
                .await?;
            self.inactive_registry
                .lock()
                .await
                .insert(ticker.clone(), next_due);
            advanced.push(ticker);
        }
        Ok(StructureFocusAdvance { advanced, blocked })
    }

    async fn advance_checkpoint(
        &self,
        checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
    ) -> Result<HistoryAdvanceResponse, String> {
        let response = self
            .client
            .post(format!(
                "{}{}",
                self.history_url,
                checkpoint_advance_endpoint(false)
            ))
            .json(&json!({
                "schema_version": 1,
                "checkpoint": checkpoint,
                "as_of": as_of,
            }))
            .send()
            .await
            .map_err(|error| format!("QMD History checkpoint request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD History checkpoint advancement returned HTTP {status}: {body}"
            ));
        }
        response
            .json::<HistoryAdvanceResponse>()
            .await
            .map_err(|error| format!("invalid QMD History checkpoint response: {error}"))
    }

    async fn advance_historical_checkpoint(
        &self,
        checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
    ) -> Result<HistoryAdvanceResponse, String> {
        let response = self
            // Archive checkpoint advancement is an operator/rebuild-class
            // workload.  Large but bounded slices can legitimately exceed
            // the interactive history timeout while QMD History continues
            // computing, so use the independently bounded rebuild client.
            // Live checkpoint and chart paths remain on the low-latency
            // history client.
            .rebuild_client
            .post(format!(
                "{}{}",
                self.history_url,
                checkpoint_advance_endpoint(true)
            ))
            .json(&json!({
                "schema_version": 1,
                "checkpoint": checkpoint,
                "as_of": as_of,
            }))
            .send()
            .await
            .map_err(|error| {
                format!("QMD History historical checkpoint request failed: {error}")
            })?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD History historical checkpoint advancement returned HTTP {status}: {body}"
            ));
        }
        response
            .json::<HistoryAdvanceResponse>()
            .await
            .map_err(|error| format!("invalid QMD History historical checkpoint response: {error}"))
    }

    async fn latest_compatible_daily_seed(
        &self,
        ticker: &str,
    ) -> Result<Option<GenericStructureCheckpoint>, String> {
        let tomorrow = Utc::now()
            .with_timezone(&New_York)
            .date_naive()
            .succ_opt()
            .ok_or_else(|| "daily structure seed date overflow".to_string())?;
        let Some(seed) = self
            .checkpoint_store
            .load_daily_structure_checkpoint_before(ticker, tomorrow)
            .await?
        else {
            return Ok(None);
        };
        let authority_end = seed
            .checkpoint
            .replayed_through
            .or(seed.checkpoint.updated_at)
            .ok_or_else(|| "daily structure seed lacks replay cursor".to_string())?
            .checked_add_signed(ChronoDuration::microseconds(1))
            .ok_or_else(|| "daily structure seed cursor overflow".to_string())?;
        let current = self
            .history_source_revision(ticker, seed.authority_start, authority_end)
            .await?;
        if current.complete_for_history
            && current.request_complete
            && current.source_plan_hash == seed.source_plan_hash
            && current.token == seed.source_revision_token
        {
            Ok(Some(seed.checkpoint))
        } else {
            Ok(None)
        }
    }

    async fn request_structure_rebuild(
        &self,
        ticker: &str,
        replay_start: DateTime<Utc>,
        as_of: DateTime<Utc>,
        event_limit: Option<usize>,
    ) -> Result<HistoryRebuildResponse, String> {
        let response = self
            .rebuild_client
            .post(format!(
                "{}/materialize/generic-structure-rebuild",
                self.history_url
            ))
            .json(&json!({
                "schema_version": 1,
                "ticker": ticker,
                "start": replay_start,
                "as_of": as_of,
                "event_limit": event_limit,
            }))
            .send()
            .await
            .map_err(|error| format!("QMD History checkpoint rebuild request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD History checkpoint rebuild returned HTTP {status}: {body}"
            ));
        }
        let rebuilt = response
            .json::<HistoryRebuildResponse>()
            .await
            .map_err(|error| format!("invalid QMD History rebuild response: {error}"))?;
        if !rebuilt.complete
            || rebuilt.ticker != ticker
            || rebuilt.checkpoint.sym.to_ascii_uppercase() != ticker
            || rebuilt.checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
            || rebuilt.checkpoint.last_arrival_sequence == 0
            || rebuilt.source_revision_before.token != rebuilt.source_revision_after.token
            || rebuilt
                .source_plan
                .segments
                .iter()
                .any(|segment| segment.tier == "gap")
        {
            return Err(format!(
                "QMD History returned an incomplete or mismatched rebuild for {ticker}"
            ));
        }
        Ok(rebuilt)
    }

    async fn advance_checkpoint_through(
        &self,
        checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
    ) -> Result<HistoryAdvanceResponse, String> {
        self.advance_checkpoint_through_mode(checkpoint, as_of, false)
            .await
    }

    async fn advance_historical_checkpoint_through(
        &self,
        checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
    ) -> Result<HistoryAdvanceResponse, String> {
        self.advance_checkpoint_through_mode(checkpoint, as_of, true)
            .await
    }

    async fn advance_checkpoint_through_mode(
        &self,
        mut checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
        historical_archive: bool,
    ) -> Result<HistoryAdvanceResponse, String> {
        let mut total_event_count = 0_u64;
        let mut total_advanced_event_count = 0_u64;
        loop {
            let cursor_at = checkpoint
                .updated_at
                .ok_or_else(|| "Generic Structure checkpoint lacks updated_at".to_string())?;
            let start = checkpoint.replayed_through.unwrap_or(cursor_at);
            let slice_end = next_checkpoint_slice_end(start, as_of);
            let previous_cursor = (start, checkpoint.last_arrival_sequence);
            let mut response = if historical_archive {
                self.advance_historical_checkpoint(checkpoint, slice_end)
                    .await?
            } else {
                self.advance_checkpoint(checkpoint, slice_end).await?
            };
            total_event_count = total_event_count.saturating_add(response.event_count);
            total_advanced_event_count =
                total_advanced_event_count.saturating_add(response.advanced_event_count);
            let next_cursor = (
                response.checkpoint.replayed_through.ok_or_else(|| {
                    "QMD History returned a Generic Structure checkpoint without replayed_through"
                        .to_string()
                })?,
                response.checkpoint.last_arrival_sequence,
            );
            if slice_end < as_of && next_cursor <= previous_cursor {
                return Err(format!(
                    "Generic Structure checkpoint made no cursor progress through {slice_end}; cannot safely bridge the remaining replay window"
                ));
            }
            response.event_count = total_event_count;
            response.advanced_event_count = total_advanced_event_count;
            if slice_end >= as_of {
                return Ok(response);
            }
            checkpoint = response.checkpoint.clone();
        }
    }

    async fn history_gap_segments(
        &self,
        ticker: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<Vec<(DateTime<Utc>, DateTime<Utc>)>, String> {
        let response = self
            .client
            .get(format!(
                "{}/source-plan?start={}&end={}&tickers={}",
                self.history_url,
                urlencoding::encode(&start.to_rfc3339()),
                urlencoding::encode(&end.to_rfc3339()),
                urlencoding::encode(ticker),
            ))
            .send()
            .await
            .map_err(|error| format!("QMD History source-plan request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            return Err(format!(
                "QMD History source-plan request returned HTTP {status}"
            ));
        }
        let plan = response
            .json::<HistorySourcePlan>()
            .await
            .map_err(|error| format!("invalid QMD History source plan: {error}"))?;
        Ok(plan
            .segments
            .into_iter()
            .filter(|segment| segment.tier == "gap")
            .map(|segment| (segment.start, segment.end))
            .collect())
    }

    async fn history_source_revision(
        &self,
        ticker: &str,
        start: DateTime<Utc>,
        end: DateTime<Utc>,
    ) -> Result<HistorySourceRevision, String> {
        let response = self
            .client
            .get(format!(
                "{}/source-revision?start={}&end={}&tickers={}",
                self.history_url,
                urlencoding::encode(&start.to_rfc3339()),
                urlencoding::encode(&end.to_rfc3339()),
                urlencoding::encode(ticker),
            ))
            .send()
            .await
            .map_err(|error| format!("QMD History source-revision request failed: {error}"))?;
        let status = response.status();
        if !status.is_success() {
            let body = response.text().await.unwrap_or_default();
            return Err(format!(
                "QMD History source-revision request returned HTTP {status}: {body}"
            ));
        }
        response
            .json::<HistorySourceRevision>()
            .await
            .map_err(|error| format!("invalid QMD History source revision: {error}"))
    }
}

fn non_retryable_history_error(message: &str) -> Option<HistoryErrorResponse> {
    let body = message.get(message.find('{')?..)?;
    let parsed = serde_json::from_str::<HistoryErrorResponse>(body).ok()?;
    (!parsed.retryable && parsed.error_code == "structure_checkpoint_source_incompatible")
        .then_some(parsed)
}

fn checkpoint_advance_endpoint(historical_archive: bool) -> &'static str {
    if historical_archive {
        "/materialize/generic-structure-snapshot-advance"
    } else {
        "/materialize/generic-structure-checkpoint"
    }
}

fn next_checkpoint_slice_end(start: DateTime<Utc>, as_of: DateTime<Utc>) -> DateTime<Utc> {
    (start + ChronoDuration::hours(CHECKPOINT_ADVANCE_SLICE_HOURS)).min(as_of)
}

fn daily_seed_covers_rebuild_start(
    seed_authority_start: DateTime<Utc>,
    requested_rebuild_start: DateTime<Utc>,
) -> bool {
    seed_authority_start <= requested_rebuild_start
}

fn daily_seed_revision_is_compatible(
    stored_plan_hash: &str,
    stored_revision_token: &str,
    current: &HistorySourceRevision,
) -> bool {
    current.complete_for_history
        && current.request_complete
        && !stored_plan_hash.trim().is_empty()
        && !stored_revision_token.trim().is_empty()
        && current.source_plan_hash == stored_plan_hash
        && current.token == stored_revision_token
}

#[cfg(test)]
mod tests {
    use super::{
        checkpoint_advance_endpoint, daily_seed_covers_rebuild_start,
        daily_seed_revision_is_compatible, next_checkpoint_slice_end, non_retryable_history_error,
        HistorySourceRevision, SymbolActivationLocks,
    };
    use chrono::{Duration as ChronoDuration, TimeZone, Utc};
    use std::time::Duration;

    #[tokio::test]
    async fn overlapping_symbol_activation_waits_for_the_active_request() {
        let locks = SymbolActivationLocks::default();
        let first = locks.acquire(&["AAPL".to_string()]).await;
        let waiting_locks = locks.clone();
        let waiter =
            tokio::spawn(async move { waiting_locks.acquire(&["AAPL".to_string()]).await });

        tokio::time::sleep(Duration::from_millis(10)).await;
        assert!(!waiter.is_finished());
        drop(first);
        let second = tokio::time::timeout(Duration::from_secs(1), waiter)
            .await
            .expect("same-symbol request should resume after release")
            .expect("same-symbol request task should succeed");
        assert_eq!(second.len(), 1);
    }

    #[tokio::test]
    async fn unrelated_symbol_activation_remains_parallel() {
        let locks = SymbolActivationLocks::default();
        let _first = locks.acquire(&["AAPL".to_string()]).await;
        let second = tokio::time::timeout(
            Duration::from_millis(100),
            locks.acquire(&["MSFT".to_string()]),
        )
        .await
        .expect("different-symbol request should not wait");
        assert_eq!(second.len(), 1);
    }

    #[test]
    fn stale_checkpoints_are_planned_inside_history_resource_limits() {
        let start = Utc.with_ymd_and_hms(2026, 8, 1, 12, 0, 0).unwrap();
        let distant_end = start + ChronoDuration::hours(240);
        assert_eq!(
            next_checkpoint_slice_end(start, distant_end),
            start + ChronoDuration::hours(48)
        );
        let near_end = start + ChronoDuration::hours(12);
        assert_eq!(next_checkpoint_slice_end(start, near_end), near_end);
    }

    #[test]
    fn shallow_daily_seed_cannot_override_a_longer_requested_book() {
        let requested = Utc.with_ymd_and_hms(2026, 2, 21, 9, 0, 0).unwrap();
        let shallow = Utc.with_ymd_and_hms(2026, 8, 14, 8, 0, 0).unwrap();
        let complete = Utc.with_ymd_and_hms(2026, 1, 1, 9, 0, 0).unwrap();

        assert!(!daily_seed_covers_rebuild_start(shallow, requested));
        assert!(daily_seed_covers_rebuild_start(complete, requested));
    }

    #[test]
    fn daily_resume_requires_exact_complete_event_and_split_revision() {
        let current = HistorySourceRevision {
            token: "events+split-v2".to_string(),
            source_plan_hash: "plan-v2".to_string(),
            complete_for_history: true,
            request_complete: true,
        };
        assert!(daily_seed_revision_is_compatible(
            "plan-v2",
            "events+split-v2",
            &current
        ));
        assert!(!daily_seed_revision_is_compatible(
            "plan-v2",
            "events+old-split",
            &current
        ));
        assert!(!daily_seed_revision_is_compatible(
            "old-plan",
            "events+split-v2",
            &current
        ));
    }

    #[test]
    fn daily_archive_advancement_does_not_use_the_live_exact_cursor_endpoint() {
        assert_eq!(
            checkpoint_advance_endpoint(true),
            "/materialize/generic-structure-snapshot-advance"
        );
        assert_eq!(
            checkpoint_advance_endpoint(false),
            "/materialize/generic-structure-checkpoint"
        );
    }

    #[test]
    fn non_retryable_history_conflicts_are_typed_for_quarantine() {
        let error = r#"QMD History checkpoint advancement returned HTTP 409 Conflict: {"error":"cursor identity mismatch","error_code":"structure_checkpoint_source_incompatible","retry_action":"rebuild_checkpoint_from_canonical_history","retryable":false}"#;
        let parsed = non_retryable_history_error(error).expect("non-retryable conflict");
        assert_eq!(
            parsed.error_code,
            "structure_checkpoint_source_incompatible"
        );
        assert_eq!(
            parsed.retry_action,
            "rebuild_checkpoint_from_canonical_history"
        );
        assert!(
            non_retryable_history_error(r#"{"error_code":"temporary","retryable":true}"#).is_none()
        );
    }
}
