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
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::Mutex;

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
                let response = self
                    .client
                    .post(format!(
                        "{}/materialize/generic-structure-checkpoint",
                        self.history_url
                    ))
                    .json(&json!({
                        "schema_version": 1,
                        "checkpoint": checkpoint,
                        "as_of": Utc::now(),
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
                let advanced = response
                    .json::<HistoryAdvanceResponse>()
                    .await
                    .map_err(|error| format!("invalid QMD History checkpoint response: {error}"))?;
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

    pub async fn restore_inactive_registry(&self) -> Result<usize, String> {
        let entries = self
            .checkpoint_store
            .load_structure_focus_registry(self.inactive_registry_limit)
            .await?;
        let count = entries.len();
        self.inactive_registry.lock().await.extend(entries);
        Ok(count)
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

    pub async fn advance_inactive_due(&self) -> Result<Vec<String>, String> {
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
            let response = self.advance_checkpoint(checkpoint).await?;
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
        Ok(advanced)
    }

    async fn advance_checkpoint(
        &self,
        checkpoint: GenericStructureCheckpoint,
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
                "as_of": Utc::now(),
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
