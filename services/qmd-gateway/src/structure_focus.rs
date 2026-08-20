use crate::bars::SharedBarStore;
use crate::computation_targets::{
    capability_requires_generic_structure, ComputationTargetLease, SharedComputationTargets,
};
use crate::config::GatewayConfig;
use crate::gapfill::GapFillService;
use crate::generic_structure::{GenericStructureCheckpoint, GENERIC_STRUCTURE_ALGORITHM_VERSION};
use crate::indicators::IndicatorClickHouseWriter;
use chrono::{DateTime, Duration as ChronoDuration, Utc};
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
    focused_repair: GapFillService,
    history_url: String,
    inactive_advance_hours: u64,
    inactive_batch_size: usize,
    inactive_registry: Arc<Mutex<BTreeMap<String, DateTime<Utc>>>>,
    inactive_registry_limit: usize,
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
    source_plan: HistorySourcePlan,
    complete: bool,
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
        Ok(Self {
            bars,
            checkpoint_store,
            client,
            focused_repair,
            history_url: config.qmd_history_gateway_url.clone(),
            inactive_advance_hours: config.structure_focus_inactive_advance_hours,
            inactive_batch_size: config.structure_focus_inactive_batch_size,
            inactive_registry: Arc::new(Mutex::new(BTreeMap::new())),
            inactive_registry_limit: config.structure_focus_inactive_registry_limit,
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
                let checkpoint = self
                    .checkpoint_store
                    .load_structure_checkpoint(ticker)
                    .await?
                    .ok_or_else(|| {
                        format!("no persisted Generic Structure checkpoint exists for {ticker}")
                    })?;
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
                    history_event_count: advanced.event_count,
                    history_advanced_event_count: advanced.advanced_event_count,
                    buffered_event_count,
                    checkpoint_updated_at: advanced.checkpoint.updated_at,
                    checkpoint_arrival_sequence: cursor.1,
                    source_plan_hash: advanced.source_plan.plan_hash,
                });
            }
            Ok::<(), String>(())
        }
        .await;
        if let Err(error) = result {
            for ticker in &staged {
                self.bars.cancel_structure_staging(ticker).await;
            }
            for activation in &activated {
                if !activation.already_active {
                    self.bars.deactivate_structure(&activation.ticker).await;
                }
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
                "{}/materialize/generic-structure-checkpoint",
                self.history_url
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

    async fn advance_checkpoint_through(
        &self,
        mut checkpoint: GenericStructureCheckpoint,
        as_of: DateTime<Utc>,
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
            let mut response = self.advance_checkpoint(checkpoint, slice_end).await?;
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
}

fn non_retryable_history_error(message: &str) -> Option<HistoryErrorResponse> {
    let body = message.get(message.find('{')?..)?;
    let parsed = serde_json::from_str::<HistoryErrorResponse>(body).ok()?;
    (!parsed.retryable && parsed.error_code == "structure_checkpoint_source_incompatible")
        .then_some(parsed)
}

fn next_checkpoint_slice_end(start: DateTime<Utc>, as_of: DateTime<Utc>) -> DateTime<Utc> {
    (start + ChronoDuration::hours(CHECKPOINT_ADVANCE_SLICE_HOURS)).min(as_of)
}

#[cfg(test)]
mod tests {
    use super::{next_checkpoint_slice_end, non_retryable_history_error, SymbolActivationLocks};
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
