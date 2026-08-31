use crate::config::HistoricalGatewayConfig;
use crate::source::{
    EventWindow, HistoricalEventSource, MarketSourcePlan, MarketSourceTier, SourceRevision,
};
use chrono::{DateTime, Datelike, Duration, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use qmd_core::event::MarketEvent;
use qmd_core::generic_structure::{
    GenericStructureCheckpoint, GenericStructureEngine, GenericStructureEvent,
    GenericStructureSnapshot, StructureSplitAdjustment, GENERIC_STRUCTURE_ALGORITHM_VERSION,
};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::sync::{
    atomic::{AtomicU64, Ordering},
    Arc,
};
use tokio::sync::Mutex;

pub const STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION: u16 = 1;
pub const STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION: u16 = 1;
pub const STRUCTURE_SNAPSHOT_SCHEMA_VERSION: u16 = 1;

#[derive(Clone, Debug, Deserialize)]
pub struct StructureSnapshotRequest {
    pub schema_version: u16,
    pub ticker: String,
    pub as_of: DateTime<Utc>,
    pub event_limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureSnapshotResponse {
    pub schema_version: u16,
    pub ticker: String,
    pub as_of: DateTime<Utc>,
    pub seed_authority_start: DateTime<Utc>,
    pub seed_source_plan_hash: String,
    pub seed_source_revision_token: String,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub source_plan: MarketSourcePlan,
    pub source_revision_before: SourceRevision,
    pub source_revision_after: SourceRevision,
    pub checkpoint: GenericStructureCheckpoint,
    pub snapshot: GenericStructureSnapshot,
    pub complete: bool,
}

#[derive(Clone, Debug)]
pub(crate) struct PersistedStructureBookSeed {
    pub authority_start: DateTime<Utc>,
    pub checkpoint: GenericStructureCheckpoint,
    pub revision_token: String,
}

pub(crate) async fn persisted_structure_book_seed(
    source: &HistoricalEventSource,
    ticker: &str,
    before: DateTime<Utc>,
) -> Result<Option<PersistedStructureBookSeed>, String> {
    let events = source
        .persisted_structure_events_before(ticker, before)
        .await?;
    if events.is_empty() {
        return Ok(None);
    }
    let authority_start = events
        .first()
        .map(|event| event.confirmed_at)
        .unwrap_or(before);
    let adjustments = source
        .structure_split_adjustments(ticker, authority_start, before)
        .await?;
    let revision_bytes = serde_json::to_vec(&(&events, &adjustments))
        .map_err(|error| format!("failed to hash persisted structure seed: {error}"))?;
    let revision_token = format!("sha256:{:x}", Sha256::digest(revision_bytes));
    let checkpoint = checkpoint_from_persisted_structure_events(ticker, &events, &adjustments)?;
    Ok(Some(PersistedStructureBookSeed {
        authority_start,
        checkpoint,
        revision_token,
    }))
}

fn checkpoint_from_persisted_structure_events(
    ticker: &str,
    events: &[GenericStructureEvent],
    adjustments: &[StructureSplitAdjustment],
) -> Result<GenericStructureCheckpoint, String> {
    let mut engine = GenericStructureEngine::new(ticker);
    let mut next_split = 0_usize;
    for event in events {
        while next_split < adjustments.len()
            && adjustments[next_split].effective_at <= event.confirmed_at
        {
            engine.apply_split_adjustment(&adjustments[next_split])?;
            next_split += 1;
        }
        engine.seed_events(std::slice::from_ref(event));
    }
    while next_split < adjustments.len() {
        engine.apply_split_adjustment(&adjustments[next_split])?;
        next_split += 1;
    }
    Ok(engine.checkpoint())
}

#[derive(Clone)]
pub struct HistoricalStructureSessionRegistry {
    inner: Arc<Mutex<HistoricalStructureSessionRegistryInner>>,
    next_id: Arc<AtomicU64>,
    max_sessions: usize,
}

#[derive(Default)]
struct HistoricalStructureSessionRegistryInner {
    access_sequence: u64,
    sessions: HashMap<String, HistoricalStructureSession>,
}

struct HistoricalStructureSession {
    access_sequence: u64,
    checkpoint: GenericStructureCheckpoint,
}

impl HistoricalStructureSessionRegistry {
    pub fn new(max_sessions: usize) -> Self {
        Self {
            inner: Arc::new(Mutex::new(
                HistoricalStructureSessionRegistryInner::default(),
            )),
            next_id: Arc::new(AtomicU64::new(1)),
            max_sessions: max_sessions.max(1),
        }
    }

    pub async fn register(&self, checkpoint: GenericStructureCheckpoint) -> String {
        let session_id = format!(
            "gslb-{}-{}",
            Utc::now().timestamp_micros(),
            self.next_id.fetch_add(1, Ordering::Relaxed)
        );
        let mut inner = self.inner.lock().await;
        inner.access_sequence = inner.access_sequence.saturating_add(1);
        if inner.sessions.len() >= self.max_sessions {
            if let Some(oldest) = inner
                .sessions
                .iter()
                .min_by_key(|(_, session)| session.access_sequence)
                .map(|(key, _)| key.clone())
            {
                inner.sessions.remove(&oldest);
            }
        }
        let access_sequence = inner.access_sequence;
        inner.sessions.insert(
            session_id.clone(),
            HistoricalStructureSession {
                access_sequence,
                checkpoint,
            },
        );
        session_id
    }

    pub async fn checkout(&self, session_id: &str) -> Result<GenericStructureCheckpoint, String> {
        let mut inner = self.inner.lock().await;
        inner
            .sessions
            .remove(session_id)
            .map(|session| session.checkpoint)
            .ok_or_else(|| {
                "Generic Structure historical session is missing or expired; reseed the ticker snapshot"
                    .to_string()
            })
    }

    pub async fn replace(&self, session_id: String, checkpoint: GenericStructureCheckpoint) {
        let mut inner = self.inner.lock().await;
        inner.access_sequence = inner.access_sequence.saturating_add(1);
        let access_sequence = inner.access_sequence;
        inner.sessions.insert(
            session_id,
            HistoricalStructureSession {
                access_sequence,
                checkpoint,
            },
        );
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct StructureSnapshotSessionAdvanceRequest {
    pub schema_version: u16,
    pub session_id: String,
    pub as_of: DateTime<Utc>,
    pub expected_source_plan_hash: Option<String>,
    pub event_limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureSnapshotSessionAdvanceResponse {
    pub schema_version: u16,
    pub session_id: String,
    pub as_of: DateTime<Utc>,
    pub replay_start: DateTime<Utc>,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub snapshot: GenericStructureSnapshot,
    pub source_plan: MarketSourcePlan,
    pub source_revision_before: SourceRevision,
    pub source_revision_after: SourceRevision,
    pub complete: bool,
}

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
    pub snapshot: GenericStructureSnapshot,
    pub source_plan: MarketSourcePlan,
    pub source_revision_before: SourceRevision,
    pub source_revision_after: SourceRevision,
    pub complete: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct StructureCheckpointRebuildRequest {
    pub schema_version: u16,
    pub ticker: String,
    pub start: DateTime<Utc>,
    pub as_of: DateTime<Utc>,
    pub expected_source_plan_hash: Option<String>,
    pub event_limit: Option<usize>,
}

#[derive(Clone, Debug, Serialize)]
pub struct StructureCheckpointRebuildResponse {
    pub schema_version: u16,
    pub checkpoint: GenericStructureCheckpoint,
    pub ticker: String,
    pub as_of: DateTime<Utc>,
    pub replay_start: DateTime<Utc>,
    pub event_count: u64,
    pub advanced_event_count: u64,
    pub source_plan: MarketSourcePlan,
    pub source_revision_before: SourceRevision,
    pub source_revision_after: SourceRevision,
    pub complete: bool,
}

pub async fn rebuild_structure_checkpoint(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointRebuildRequest,
) -> Result<StructureCheckpointRebuildResponse, String> {
    rebuild_structure_checkpoint_inner(config, source, request, None).await
}

pub(crate) async fn rebuild_trade_structure_checkpoint(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointRebuildRequest,
) -> Result<StructureCheckpointRebuildResponse, String> {
    rebuild_structure_checkpoint_inner(config, source, request, Some(1)).await
}

async fn rebuild_structure_checkpoint_inner(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointRebuildRequest,
    event_type_filter: Option<u8>,
) -> Result<StructureCheckpointRebuildResponse, String> {
    let ticker = validate_rebuild_request(config, &request)?;
    let replay_end = request
        .as_of
        .checked_add_signed(Duration::microseconds(1))
        .ok_or_else(|| "invalid Generic Structure rebuild as_of overflow".to_string())?;
    let window = EventWindow {
        start: request.start,
        end: replay_end,
        tickers: vec![ticker.clone()],
    };
    let source_plan = source.source_plan(&window).await?;
    validate_rebuild_source_plan(&source_plan)?;
    if request
        .expected_source_plan_hash
        .as_deref()
        .is_some_and(|expected| expected != source_plan.plan_hash)
    {
        return Err("Generic Structure rebuild source plan changed before replay".to_string());
    }
    let source_revision_before = source.source_revision(&window).await?;
    if !source_revision_before.request_complete {
        return Err("Generic Structure rebuild source revision is incomplete".to_string());
    }
    let event_limit = request
        .event_limit
        .unwrap_or(config.structure_checkpoint_rebuild_max_events)
        .clamp(1, config.structure_checkpoint_rebuild_max_events);
    let rules = source.trade_aggregation_rules();
    let mut engine = GenericStructureEngine::new(&ticker);
    let split_adjustments = source
        .structure_split_adjustments(&ticker, request.start, request.as_of)
        .await?;
    let mut next_split = 0_usize;
    let batch_size = if event_type_filter.is_some() {
        config.batch_size.max(100_000)
    } else {
        config.batch_size
    };
    let mut batches = source.stream_structure_ordered_filtered(
        window.clone(),
        batch_size,
        source_revision_before.live_continuation_sequence,
        event_type_filter,
    )?;
    let mut event_count = 0_u64;
    let mut advanced_event_count = 0_u64;
    while let Some(batch) = batches.recv().await {
        for compact in batch? {
            event_count = event_count.saturating_add(1);
            if event_count > event_limit as u64 {
                return Err(format!(
                    "Generic Structure rebuild exceeded event limit {event_limit}"
                ));
            }
            let event = source.market_event(&compact);
            while next_split < split_adjustments.len()
                && split_adjustments[next_split].effective_at <= event.ts()
            {
                engine.apply_split_adjustment(&split_adjustments[next_split])?;
                next_split += 1;
            }
            let before = engine.checkpoint_cursor();
            let conditions = match &event {
                MarketEvent::Trade(event) => event.conditions.as_slice(),
                MarketEvent::Quote(event) => event.conditions.as_slice(),
            };
            engine.apply_event_without_snapshot(&event, rules.resolve(conditions, event.ts()));
            if engine.checkpoint_cursor() != before {
                advanced_event_count = advanced_event_count.saturating_add(1);
            }
        }
    }
    while next_split < split_adjustments.len()
        && split_adjustments[next_split].effective_at <= request.as_of
    {
        engine.apply_split_adjustment(&split_adjustments[next_split])?;
        next_split += 1;
    }
    if event_count == 0 {
        return Err(format!(
            "Generic Structure rebuild found no canonical events for {ticker}"
        ));
    }
    let source_revision_after = source.source_revision(&window).await?;
    if source_revision_after.source_plan_hash != source_plan.plan_hash
        || source_revision_after.token != source_revision_before.token
    {
        return Err("Generic Structure rebuild source revision changed during replay".to_string());
    }
    let mut checkpoint = engine.checkpoint();
    if checkpoint.updated_at.is_none() || checkpoint.last_arrival_sequence == 0 {
        return Err(
            "Generic Structure rebuild did not produce an exact checkpoint cursor".to_string(),
        );
    }
    checkpoint.replayed_through = Some(request.as_of);
    Ok(StructureCheckpointRebuildResponse {
        schema_version: STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
        checkpoint,
        ticker: ticker.clone(),
        as_of: request.as_of,
        replay_start: request.start,
        event_count,
        advanced_event_count,
        source_plan,
        source_revision_before,
        source_revision_after,
        complete: true,
    })
}

pub async fn advance_structure_checkpoint(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointAdvanceRequest,
) -> Result<StructureCheckpointAdvanceResponse, String> {
    advance_structure_checkpoint_inner(config, source, request, true).await
}

pub async fn advance_historical_structure_snapshot(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureCheckpointAdvanceRequest,
) -> Result<StructureCheckpointAdvanceResponse, String> {
    advance_structure_checkpoint_inner(config, source, request, false).await
}

async fn advance_structure_checkpoint_inner(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    mut request: StructureCheckpointAdvanceRequest,
    exact_live_cursor: bool,
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
    if exact_live_cursor {
        validate_exact_cursor_plan(&source_plan)?;
    } else {
        validate_rebuild_source_plan(&source_plan)?;
    }
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
    // Daily historical checkpoints certify the complete book through a UTC
    // boundary, but archive compaction has a different transport ordinal than
    // the live/recent feed. Preserve all structural state while resetting only
    // that source-specific cursor, then consume events strictly after the
    // certified boundary. Live advancement remains exact-cursor strict.
    if !exact_live_cursor {
        request.checkpoint.last_arrival_sequence = 0;
    }
    let mut engine = GenericStructureEngine::new(&ticker);
    engine.seed_checkpoint(&request.checkpoint);
    let initial_cursor = engine.checkpoint_cursor();
    let split_adjustments = source
        .structure_split_adjustments(&ticker, replay_start, request.as_of)
        .await?;
    let mut next_split = 0_usize;
    let rules = source.trade_aggregation_rules();
    let mut batches = source.stream_structure_ordered_filtered(
        window.clone(),
        config.batch_size,
        exact_live_cursor
            .then_some(source_revision_before.live_continuation_sequence)
            .flatten(),
        None,
    )?;
    let mut event_count = 0_u64;
    let mut advanced_event_count = 0_u64;
    while let Some(batch) = batches.recv().await {
        for compact in batch? {
            let event = source.market_event(&compact);
            if !exact_live_cursor && event.ts() <= replay_start {
                continue;
            }
            event_count = event_count.saturating_add(1);
            if event_count > event_limit as u64 {
                return Err(format!(
                    "Generic Structure checkpoint replay exceeded event limit {event_limit}"
                ));
            }
            while next_split < split_adjustments.len()
                && split_adjustments[next_split].effective_at <= event.ts()
            {
                engine.apply_split_adjustment(&split_adjustments[next_split])?;
                next_split += 1;
            }
            let before = engine.checkpoint_cursor();
            let conditions = match &event {
                MarketEvent::Trade(event) => event.conditions.as_slice(),
                MarketEvent::Quote(event) => event.conditions.as_slice(),
            };
            let trade_rule = rules.resolve(conditions, event.ts());
            engine.apply_event_without_snapshot(&event, trade_rule);
            if engine.checkpoint_cursor() != before {
                advanced_event_count = advanced_event_count.saturating_add(1);
            }
        }
    }
    while next_split < split_adjustments.len()
        && split_adjustments[next_split].effective_at <= request.as_of
    {
        engine.apply_split_adjustment(&split_adjustments[next_split])?;
        next_split += 1;
    }
    let source_revision_after = source.source_revision(&window).await?;
    if source_revision_after.source_plan_hash != source_plan.plan_hash {
        return Err("Generic Structure checkpoint source plan changed during replay".to_string());
    }
    let snapshot = engine.snapshot(request.as_of);
    let mut checkpoint = engine.checkpoint();
    checkpoint.replayed_through = Some(request.as_of);
    if exact_live_cursor && checkpoint.checkpoint_cursor() < initial_cursor {
        return Err("Generic Structure checkpoint replay moved its cursor backward".to_string());
    }
    Ok(StructureCheckpointAdvanceResponse {
        schema_version: STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION,
        checkpoint,
        as_of: request.as_of,
        replay_start,
        event_count,
        advanced_event_count,
        snapshot,
        source_plan,
        source_revision_before,
        source_revision_after,
        complete: true,
    })
}

/// Resolve the latest certified end-of-day book before `as_of`, advance it
/// causally through canonical events (including due split adjustments), and
/// expose the resulting point-in-time snapshot. This is the bounded lookup
/// used by strategies when a ticker becomes actionable; it avoids rebuilding
/// the persistent level book on every chart bar.
pub async fn materialize_structure_snapshot(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    request: StructureSnapshotRequest,
) -> Result<StructureSnapshotResponse, String> {
    if request.schema_version != STRUCTURE_SNAPSHOT_SCHEMA_VERSION {
        return Err(format!(
            "invalid Generic Structure snapshot schema_version {}; expected {}",
            request.schema_version, STRUCTURE_SNAPSHOT_SCHEMA_VERSION
        ));
    }
    let ticker = request.ticker.trim().to_ascii_uppercase();
    if ticker.is_empty()
        || ticker.len() > 32
        || !ticker
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    {
        return Err("invalid Generic Structure snapshot ticker".to_string());
    }
    if request.as_of > Utc::now() + Duration::seconds(1) {
        return Err("Generic Structure snapshot as_of cannot be in the future".to_string());
    }
    let Some(seed) = source
        .persisted_structure_checkpoint_before(&ticker, request.as_of)
        .await?
    else {
        if let Some(seed) = persisted_structure_book_seed(source, &ticker, request.as_of).await? {
            let advanced = advance_structure_checkpoint_inner(
                config,
                source,
                StructureCheckpointAdvanceRequest {
                    schema_version: STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION,
                    checkpoint: seed.checkpoint,
                    as_of: request.as_of,
                    expected_source_plan_hash: None,
                    event_limit: request.event_limit,
                },
                false,
            )
            .await?;
            let mut engine = GenericStructureEngine::new(&ticker);
            engine.seed_checkpoint(&advanced.checkpoint);
            let snapshot = engine.snapshot(request.as_of);
            return Ok(StructureSnapshotResponse {
                schema_version: STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
                ticker,
                as_of: request.as_of,
                seed_authority_start: seed.authority_start,
                seed_source_plan_hash: seed.revision_token.clone(),
                seed_source_revision_token: seed.revision_token,
                event_count: advanced.event_count,
                advanced_event_count: advanced.advanced_event_count,
                source_plan: advanced.source_plan,
                source_revision_before: advanced.source_revision_before,
                source_revision_after: advanced.source_revision_after,
                checkpoint: advanced.checkpoint,
                snapshot,
                complete: advanced.complete,
            });
        }
        // A ticker can become actionable before its first daily checkpoint
        // exists (new listing, sparse historical use, or a newly introduced
        // algorithm revision).  Historical modes must cold-build the same
        // event-native book from canonical trades instead of failing the
        // entire market-wide run or silently treating structure as absent.
        let rebuild_start =
            structure_rebuild_start(request.as_of, config.structure_book_lookback_days)?;
        let rebuilt = rebuild_trade_structure_checkpoint(
            config,
            source,
            StructureCheckpointRebuildRequest {
                schema_version: STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
                ticker: ticker.clone(),
                start: rebuild_start,
                as_of: request.as_of,
                expected_source_plan_hash: None,
                event_limit: request.event_limit,
            },
        )
        .await?;
        let mut engine = GenericStructureEngine::new(&ticker);
        engine.seed_checkpoint(&rebuilt.checkpoint);
        let snapshot = engine.snapshot(request.as_of);
        return Ok(StructureSnapshotResponse {
            schema_version: STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
            ticker,
            as_of: request.as_of,
            seed_authority_start: rebuild_start,
            seed_source_plan_hash: rebuilt.source_plan.plan_hash.clone(),
            seed_source_revision_token: rebuilt.source_revision_after.token.clone(),
            event_count: rebuilt.event_count,
            advanced_event_count: rebuilt.advanced_event_count,
            source_plan: rebuilt.source_plan,
            source_revision_before: rebuilt.source_revision_before,
            source_revision_after: rebuilt.source_revision_after,
            checkpoint: rebuilt.checkpoint,
            snapshot,
            complete: rebuilt.complete,
        });
    };
    let advanced = advance_structure_checkpoint_inner(
        config,
        source,
        StructureCheckpointAdvanceRequest {
            schema_version: STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION,
            checkpoint: seed.checkpoint,
            as_of: request.as_of,
            expected_source_plan_hash: None,
            event_limit: request.event_limit,
        },
        false,
    )
    .await?;
    let mut engine = GenericStructureEngine::new(&ticker);
    engine.seed_checkpoint(&advanced.checkpoint);
    let snapshot = engine.snapshot(request.as_of);
    Ok(StructureSnapshotResponse {
        schema_version: STRUCTURE_SNAPSHOT_SCHEMA_VERSION,
        ticker,
        as_of: request.as_of,
        seed_authority_start: seed.authority_start,
        seed_source_plan_hash: seed.source_plan_hash,
        seed_source_revision_token: seed.source_revision_token,
        event_count: advanced.event_count,
        advanced_event_count: advanced.advanced_event_count,
        source_plan: advanced.source_plan,
        source_revision_before: advanced.source_revision_before,
        source_revision_after: advanced.source_revision_after,
        checkpoint: advanced.checkpoint,
        snapshot,
        complete: advanced.complete,
    })
}

fn structure_rebuild_start(
    timestamp: DateTime<Utc>,
    rebuild_days: usize,
) -> Result<DateTime<Utc>, String> {
    let lookback = timestamp
        .checked_sub_signed(Duration::days(rebuild_days as i64))
        .ok_or_else(|| "historical structure rebuild lookback underflow".to_string())?;
    let local = lookback.with_timezone(&New_York);
    let mut date = local.date_naive();
    if local.hour() < 4 {
        date = date
            .pred_opt()
            .ok_or_else(|| "historical structure session anchor underflow".to_string())?;
    }
    New_York
        .with_ymd_and_hms(date.year(), date.month(), date.day(), 4, 0, 0)
        .single()
        .map(|value| value.with_timezone(&Utc))
        .ok_or_else(|| format!("invalid America/New_York structure session anchor for {date}"))
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
    let cursor_at = request
        .checkpoint
        .updated_at
        .ok_or_else(|| "Generic Structure checkpoint must have updated_at".to_string())?;
    let start = request.checkpoint.replayed_through.unwrap_or(cursor_at);
    if start < cursor_at {
        return Err(
            "Generic Structure checkpoint replayed_through must not precede updated_at".to_string(),
        );
    }
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

fn validate_rebuild_request(
    config: &HistoricalGatewayConfig,
    request: &StructureCheckpointRebuildRequest,
) -> Result<String, String> {
    if request.schema_version != STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION {
        return Err(format!(
            "invalid Generic Structure rebuild schema_version {}; expected {}",
            request.schema_version, STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION
        ));
    }
    let ticker = request.ticker.trim().to_ascii_uppercase();
    if ticker.is_empty()
        || ticker.len() > 32
        || !ticker
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    {
        return Err("invalid Generic Structure rebuild ticker".to_string());
    }
    if request.start >= request.as_of {
        return Err("Generic Structure rebuild start must precede as_of".to_string());
    }
    if request.as_of > Utc::now() + Duration::seconds(1) {
        return Err("Generic Structure rebuild as_of cannot be in the future".to_string());
    }
    if request
        .event_limit
        .is_some_and(|limit| limit == 0 || limit > config.structure_checkpoint_rebuild_max_events)
    {
        return Err(format!(
            "Generic Structure rebuild event_limit must be between 1 and {}",
            config.structure_checkpoint_rebuild_max_events
        ));
    }
    Ok(ticker)
}

fn validate_rebuild_source_plan(plan: &MarketSourcePlan) -> Result<(), String> {
    if plan
        .segments
        .iter()
        .any(|segment| matches!(segment.tier, MarketSourceTier::Gap))
    {
        return Err("Generic Structure rebuild source plan contains a gap".to_string());
    }
    if plan.segments.is_empty() {
        return Err("Generic Structure rebuild source plan is empty".to_string());
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
    use super::{
        checkpoint_from_persisted_structure_events, validate_exact_cursor_plan,
        validate_rebuild_source_plan, HistoricalStructureSessionRegistry, MarketSourcePlan,
    };
    use crate::source::{MarketSourceSegment, MarketSourceTier};
    use chrono::{NaiveDate, TimeZone, Utc};
    use qmd_core::generic_structure::{
        GenericStructureEngine, GenericStructureEvent, StructureSplitAdjustment,
        GENERIC_STRUCTURE_ALGORITHM_VERSION,
    };

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

    #[test]
    fn rebuild_accepts_archive_but_rejects_gaps() {
        assert!(validate_rebuild_source_plan(&plan(MarketSourceTier::Archive)).is_ok());
        assert!(validate_rebuild_source_plan(&plan(MarketSourceTier::Recent)).is_ok());
        assert!(validate_rebuild_source_plan(&plan(MarketSourceTier::Gap)).is_err());
    }

    #[test]
    fn persisted_level_book_keeps_prior_month_levels_and_applies_splits() {
        let confirmed_at = Utc.with_ymd_and_hms(2026, 2, 2, 14, 0, 0).unwrap();
        let event = GenericStructureEvent {
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            event_id: 1,
            level_id: 1,
            sym: "SUGP".to_string(),
            timeframe: "100ms".to_string(),
            event_kind: "level_created".to_string(),
            direction: -1,
            price: 8.0,
            lower: 7.9,
            upper: 8.1,
            strength: 0.9,
            confidence: 0.9,
            lifecycle: "active".to_string(),
            total_volume: 10_000.0,
            buy_volume: 4_000.0,
            sell_volume: 6_000.0,
            neutral_volume: 0.0,
            trade_count: 100,
            pivot_at: confirmed_at,
            confirmed_at,
        };
        let adjustment = StructureSplitAdjustment {
            execution_date: NaiveDate::from_ymd_opt(2026, 8, 1).unwrap(),
            effective_at: Utc.with_ymd_and_hms(2026, 8, 1, 8, 0, 0).unwrap(),
            split_from: 1.0,
            split_to: 2.0,
            source_inserted_at: Utc.with_ymd_and_hms(2026, 8, 1, 9, 0, 0).unwrap(),
        };

        let checkpoint =
            checkpoint_from_persisted_structure_events("SUGP", &[event], &[adjustment]).unwrap();
        let mut engine = GenericStructureEngine::new("SUGP");
        engine.seed_checkpoint(&checkpoint);
        let snapshot = engine.snapshot(Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap());

        assert!(snapshot
            .active_levels
            .iter()
            .any(|level| level.side < 0 && (level.price - 4.0).abs() < 0.000_001));
    }

    #[tokio::test]
    async fn historical_sessions_advance_by_bounded_identity() {
        let registry = HistoricalStructureSessionRegistry::new(2);
        let checkpoint = GenericStructureEngine::new("SUGP").checkpoint();
        let session_id = registry.register(checkpoint).await;

        let checked_out = registry.checkout(&session_id).await.unwrap();
        assert_eq!(checked_out.sym, "SUGP");
        assert!(registry.checkout(&session_id).await.is_err());

        registry.replace(session_id.clone(), checked_out).await;
        assert_eq!(registry.checkout(&session_id).await.unwrap().sym, "SUGP");
    }

    #[tokio::test]
    async fn historical_sessions_evict_the_oldest_bounded_entry() {
        let registry = HistoricalStructureSessionRegistry::new(1);
        let first = registry
            .register(GenericStructureEngine::new("SUGP").checkpoint())
            .await;
        let second = registry
            .register(GenericStructureEngine::new("JUNS").checkpoint())
            .await;

        assert!(registry.checkout(&first).await.is_err());
        assert_eq!(registry.checkout(&second).await.unwrap().sym, "JUNS");
    }
}
