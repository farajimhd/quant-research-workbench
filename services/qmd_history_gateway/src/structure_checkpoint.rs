use crate::config::HistoricalGatewayConfig;
use crate::source::{
    EventWindow, HistoricalEventSource, MarketSourcePlan, MarketSourceTier, SourceRevision,
};
use chrono::{DateTime, Duration, Utc};
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION,
};
use serde::{Deserialize, Serialize};

pub const STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Deserialize)]
pub struct StructureCheckpointAdvanceRequest {
    pub schema_version: u16,
    pub checkpoint: GenericStructureCheckpoint,
    pub as_of: DateTime<Utc>,
    pub expected_source_plan_hash: Option<String>,
    pub event_limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureCheckpointAdvanceResponse {
    pub schema_version: u16,
    pub checkpoint: GenericStructureCheckpoint,
    pub as_of: DateTime<Utc>,
    pub replay_start: DateTime<Utc>,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub source_plan: MarketSourcePlan,
    pub source_revision_before: SourceRevision,
    pub source_revision_after: SourceRevision,
    pub complete: bool,
}

pub async fn advance_structure_checkpoint(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointAdvanceRequest,
) -> Result<StructureCheckpointAdvanceResponse, String> {
    let replay_start = validate_request(config, &request)?;
    let ticker = request.checkpoint.sym.trim().to_ascii_uppercase();
    let replay_end = request
        .as_of
        .checked_add_signed(Duration::microseconds(1))
        .ok_or_else(|| "invalid Generic Structure checkpoint as_of overflow".to_string())?;
    let window = EventWindow {
        start: replay_start,
        end: replay_end,
        tickers: vec![ticker.clone()],
    };
    let source_plan = source.source_plan(&window).await?;
    validate_exact_cursor_plan(&source_plan)?;
    if request
        .expected_source_plan_hash
        .as_deref()
        .is_some_and(|expected| expected != source_plan.plan_hash)
    {
        return Err("Generic Structure checkpoint source plan changed before replay".to_string());
    }
    let source_revision_before = source.source_revision(&window).await?;
    let event_limit = request
        .event_limit
        .unwrap_or(config.structure_checkpoint_max_events)
        .clamp(1, config.structure_checkpoint_max_events);
    let mut engine = GenericStructureEngine::new(&ticker);
    engine.seed_checkpoint(&request.checkpoint);
    let initial_cursor = engine.checkpoint_cursor();
    let rules = source.trade_aggregation_rules();
    let mut batches = source.stream_ordered(
        window.clone(),
        config.batch_size,
        source_revision_before.live_continuation_sequence,
    )?;
    let mut event_count = 0_u64;
    let mut advanced_event_count = 0_u64;
    while let Some(batch) = batches.recv().await {
        for compact in batch? {
            event_count = event_count.saturating_add(1);
            if event_count > event_limit as u64 {
                return Err(format!(
                    "Generic Structure checkpoint replay exceeded event limit {event_limit}"
                ));
            }
            let event = source.market_event(&compact);
            let before = engine.checkpoint_cursor();
            let conditions = match &event {
                MarketEvent::Trade(event) => event.conditions.as_slice(),
                MarketEvent::Quote(event) => event.conditions.as_slice(),
            };
            let trade_rule = rules.resolve(conditions, event.ts());
            engine.apply_event(&event, trade_rule);
            if engine.checkpoint_cursor() != before {
                advanced_event_count = advanced_event_count.saturating_add(1);
            }
        }
    }
    let source_revision_after = source.source_revision(&window).await?;
    if source_revision_after.source_plan_hash != source_plan.plan_hash {
        return Err("Generic Structure checkpoint source plan changed during replay".to_string());
    }
    let checkpoint = engine.checkpoint();
    if checkpoint.checkpoint_cursor() < initial_cursor {
        return Err("Generic Structure checkpoint replay moved its cursor backward".to_string());
    }
    Ok(StructureCheckpointAdvanceResponse {
        schema_version: STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION,
        checkpoint,
        as_of: request.as_of,
        replay_start,
        event_count,
        advanced_event_count,
        source_plan,
        source_revision_before,
        source_revision_after,
        complete: true,
    })
}

fn validate_request(
    config: &HistoricalGatewayConfig,
    request: &StructureCheckpointAdvanceRequest,
) -> Result<DateTime<Utc>, String> {
    if request.schema_version != STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION {
        return Err(format!(
            "invalid Generic Structure checkpoint advancement schema_version {}; expected {}",
            request.schema_version, STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION
        ));
    }
    if request.checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
        return Err(format!(
            "invalid Generic Structure algorithm_version {}; expected {}",
            request.checkpoint.algorithm_version, GENERIC_STRUCTURE_ALGORITHM_VERSION
        ));
    }
    if request.checkpoint.sym.trim().is_empty() {
        return Err("Generic Structure checkpoint sym must not be empty".to_string());
    }
    if request.checkpoint.last_arrival_sequence == 0 {
        return Err(
            "Generic Structure checkpoint must have an exact nonzero arrival cursor".to_string(),
        );
    }
    let start = request
        .checkpoint
        .updated_at
        .ok_or_else(|| "Generic Structure checkpoint must have updated_at".to_string())?;
    if request.as_of < start {
        return Err("Generic Structure checkpoint as_of must not precede updated_at".to_string());
    }
    let max_window = Duration::hours(config.structure_checkpoint_max_window_hours as i64);
    if request.as_of - start > max_window {
        return Err(format!(
            "Generic Structure checkpoint replay window exceeds {} hours",
            config.structure_checkpoint_max_window_hours
        ));
    }
    if request
        .event_limit
        .is_some_and(|limit| limit == 0 || limit > config.structure_checkpoint_max_events)
    {
        return Err(format!(
            "Generic Structure checkpoint event_limit must be between 1 and {}",
            config.structure_checkpoint_max_events
        ));
    }
    Ok(start)
}

fn validate_exact_cursor_plan(plan: &MarketSourcePlan) -> Result<(), String> {
    for segment in &plan.segments {
        match segment.tier {
            MarketSourceTier::Recent
            | MarketSourceTier::CurrentLive
            | MarketSourceTier::ClosedMarket => {}
            MarketSourceTier::Archive => return Err(
                "Generic Structure exact live cursor is incompatible with archive ordinal identity"
                    .to_string(),
            ),
            MarketSourceTier::Gap => {
                return Err(
                    "Generic Structure checkpoint replay source plan contains a gap".to_string(),
                )
            }
        }
    }
    Ok(())
}

trait CheckpointCursor {
    fn checkpoint_cursor(&self) -> (i64, u64);
}

impl CheckpointCursor for GenericStructureCheckpoint {
    fn checkpoint_cursor(&self) -> (i64, u64) {
        (
            self.updated_at
                .map(|value| value.timestamp_millis())
                .unwrap_or_default(),
            self.last_arrival_sequence,
        )
    }
}

#[cfg(test)]
mod tests {
    use super::{validate_exact_cursor_plan, MarketSourcePlan};
    use crate::source::{MarketSourceSegment, MarketSourceTier};
    use chrono::{TimeZone, Utc};

    fn plan(tier: MarketSourceTier) -> MarketSourcePlan {
        let start = Utc.with_ymd_and_hms(2026, 8, 11, 13, 30, 0).unwrap();
        MarketSourcePlan {
            archive_watermark: None,
            complete_for_history: true,
            end: start + chrono::Duration::hours(1),
            event_schema_version: 1,
            ordering: "sip_timestamp_us,ticker,arrival_sequence",
            plan_hash: "test".to_string(),
            recent_watermark: None,
            segments: vec![MarketSourceSegment {
                coverage_state: "complete",
                end: start + chrono::Duration::hours(1),
                queryable_by_history: true,
                source: "test".to_string(),
                start,
                tier,
            }],
            start,
            tickers: vec!["AAPL".to_string()],
        }
    }

    #[test]
    fn exact_cursor_advancement_accepts_only_live_identity_tiers() {
        assert!(validate_exact_cursor_plan(&plan(MarketSourceTier::Recent)).is_ok());
        assert!(validate_exact_cursor_plan(&plan(MarketSourceTier::CurrentLive)).is_ok());
        assert!(validate_exact_cursor_plan(&plan(MarketSourceTier::ClosedMarket)).is_ok());
        assert!(validate_exact_cursor_plan(&plan(MarketSourceTier::Archive)).is_err());
        assert!(validate_exact_cursor_plan(&plan(MarketSourceTier::Gap)).is_err());
    }
}
