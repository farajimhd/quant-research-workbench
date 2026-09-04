use crate::cache::{
    CacheEvidence, CacheMetrics, ChartSnapshot, DerivedSnapshot, DerivedUpdate,
    HistoricalDerivedCache, IndicatorWarmupArtifact, HISTORICAL_CALCULATION_REVISION,
    HISTORICAL_CORPORATE_ACTION_REVISION, HISTORICAL_ENGINE_VERSION,
};
use crate::config::HistoricalGatewayConfig;
use crate::scanner::{
    materialize_watchlist_timeline, materialize_watchlist_timelines, HistoricalScannerDerivedCache,
    HistoricalScannerDerivedSnapshot, HistoricalWatchlistTimelineBatchMaterialization,
    HistoricalWatchlistTimelineMaterialization,
};
use crate::source::{
    split_adjustment_factors, EventCoverage, EventWindow, HistoricalCursor, HistoricalEventSource,
    HistoricalScannerMarketSnapshot, LatestEventCoverage, MarketSourcePlan, SourceRevision,
    StructureEventCountEstimateRequest, StructureEventCountEstimateResponse,
    StructureTradeCountEstimateRequest, StructureTradeCountEstimateResponse,
};
use crate::structure_checkpoint::{
    advance_historical_structure_snapshot, advance_historical_structure_timeline,
    advance_structure_checkpoint, materialize_structure_snapshot,
    materialize_structure_snapshot_from_seed, rebuild_structure_checkpoint,
    HistoricalStructureSessionRegistry, StructureCheckpointAdvanceRequest,
    StructureCheckpointAdvanceResponse, StructureCheckpointRebuildRequest,
    StructureCheckpointRebuildResponse, StructureSnapshotRequest,
    StructureSnapshotSessionAdvanceRequest, StructureSnapshotSessionAdvanceResponse,
    StructureSnapshotSessionBatchRequest, StructureSnapshotSessionBatchResponse,
};
use crate::watchlist_timeline::{
    validate_plan, HistoricalWatchlistPlan, HistoricalWatchlistPlanValidation,
    HistoricalWatchlistTimelineBatchRequest, HistoricalWatchlistTimelineRequest,
};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{DefaultBodyLimit, Path, Query, State};
use axum::http::StatusCode;
use axum::middleware;
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use futures_util::SinkExt;
use qmd_core::bars::is_supported_timeframe;
use qmd_core::capability_catalog::{computation_capability_catalog, ComputationCapability};
use qmd_core::compact_event::LiveCompactEvent;
use qmd_core::event::MarketEvent;
use qmd_core::indicators::INDICATOR_SCHEMA_VERSION;
use qmd_core::market_products::{
    parse_resolution_us, ConditionBarSnapshot, FamilyBarSnapshot, MacroBarSnapshot,
};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::sync::Arc;
use tokio::sync::Semaphore;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub cache: HistoricalDerivedCache,
    pub config: HistoricalGatewayConfig,
    pub scanner: HistoricalScannerDerivedCache,
    pub source: HistoricalEventSource,
    pub structure_checkpoint_advancement_permits: Arc<Semaphore>,
    pub structure_snapshot_sessions: HistoricalStructureSessionRegistry,
    pub watchlist_materialization_permits: Arc<Semaphore>,
}

#[derive(Debug, Deserialize)]
struct HistoryQuery {
    end: String,
    limit: Option<usize>,
    start: String,
    tail: Option<bool>,
}

#[derive(Debug, Deserialize)]
struct LatestCoverageQuery {
    before: Option<String>,
}

#[derive(Debug, Deserialize)]
struct TradableUniverseQuery {
    as_of: String,
}

#[derive(Debug, Deserialize)]
struct IndicatorWarmupRequest {
    required_bars: Option<usize>,
    session_start: DateTime<Utc>,
    ticker: String,
    timeframe: Option<String>,
}

#[derive(Debug, Serialize)]
struct TradableUniverseResponse {
    as_of: String,
    count: usize,
    source: &'static str,
    tickers: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct SourcePlanQuery {
    end: String,
    start: String,
    tickers: Option<String>,
}

#[derive(Debug, Deserialize)]
struct BarsQuery {
    end: String,
    event_limit: Option<usize>,
    limit: Option<usize>,
    start: String,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ChartQuery {
    allow_persisted_bars: Option<bool>,
    as_of: Option<String>,
    before: Option<String>,
    end: String,
    indicator_columns: Option<String>,
    include_market_signals: Option<bool>,
    include_structure: Option<bool>,
    limit: Option<usize>,
    mode: Option<String>,
    start: String,
    stage: Option<String>,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ProductQuery {
    as_of: Option<String>,
    end: String,
    limit: Option<usize>,
    resolution: Option<String>,
    start: String,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ScannerDerivedQuery {
    as_of: String,
    end: String,
    start: String,
    tickers: Option<String>,
}

#[derive(Debug, Deserialize)]
struct StreamQuery {
    batch_size: Option<usize>,
    end: String,
    start: String,
    timeframe: Option<String>,
    tickers: Option<String>,
}

#[derive(Debug, Deserialize)]
struct EventPageQuery {
    cursor_ordinal: Option<u64>,
    cursor_sip_timestamp_us: Option<u64>,
    cursor_ticker: Option<String>,
    end: String,
    expected_revision_token: Option<String>,
    expected_source_plan_hash: Option<String>,
    limit: Option<usize>,
    kinds: Option<String>,
    #[serde(default)]
    revision_policy: EventRevisionPolicy,
    start: String,
    tickers: Option<String>,
}

#[derive(Clone, Copy, Debug, Default, Deserialize, PartialEq)]
#[serde(rename_all = "snake_case")]
enum EventRevisionPolicy {
    Advancing,
    #[default]
    Pinned,
}

impl EventRevisionPolicy {
    fn as_str(self) -> &'static str {
        match self {
            Self::Advancing => "advancing",
            Self::Pinned => "pinned",
        }
    }
}

#[derive(Debug, Deserialize)]
struct DerivedStreamQuery {
    after_sequence: Option<u64>,
    as_of: Option<String>,
    end: String,
    emit: Option<String>,
    frame_batch_size: Option<usize>,
    indicator_columns: Option<String>,
    max_updates: Option<u64>,
    retain_cache: Option<bool>,
    start: String,
    timeframe: Option<String>,
    updates_per_second: Option<f64>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    cache: CacheMetrics,
    calculation_revision: &'static str,
    corporate_action_revision: &'static str,
    config: HistoricalGatewayConfig,
    host_role: &'static str,
    running: bool,
    service: &'static str,
    source: &'static str,
    status: &'static str,
    structure_algorithm_version: u16,
}

#[derive(Debug, Serialize)]
struct HistoricalSourceRevisionPayload {
    #[serde(flatten)]
    source_revision: SourceRevision,
    calculation_revision: &'static str,
    corporate_action_revision: &'static str,
}

#[derive(Debug, Serialize)]
struct MarketEventPage {
    complete: bool,
    events: Vec<MarketEvent>,
    next_cursor: Option<HistoricalCursor>,
    revision_policy: &'static str,
    source_revision: SourceRevision,
}

type ApiError = (StatusCode, Json<Value>);

pub fn app(state: AppState) -> Router {
    let watchlist_request_max_bytes = state.config.watchlist_request_max_bytes;
    let structure_checkpoint_request_max_bytes =
        state.config.structure_checkpoint_request_max_bytes;
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/metrics", get(cache_snapshot))
        .route("/snapshot/status", get(status_snapshot))
        .route("/coverage", get(coverage))
        .route("/coverage/latest", get(latest_coverage))
        .route("/universe/tradable", get(tradable_universe))
        .route("/source-plan", get(source_plan))
        .route("/source-revision", get(source_revision_snapshot))
        .route("/capability-catalog", get(capability_catalog_snapshot))
        .route("/snapshot/cache", get(cache_snapshot))
        .route("/snapshot/events", get(event_page_snapshot))
        .route(
            "/snapshot/compact-events/{ticker}",
            get(compact_event_snapshot),
        )
        .route("/snapshot/bars/{ticker}", get(bar_snapshot))
        .route("/snapshot/chart-bars/{ticker}", get(chart_bar_snapshot))
        .route("/snapshot/scanner-market", get(scanner_market_snapshot))
        .route("/snapshot/scanner-derived", get(scanner_derived_snapshot))
        .route(
            "/estimate/generic-structure-event-counts",
            post(generic_structure_event_count_estimates).layer(DefaultBodyLimit::max(
                structure_checkpoint_request_max_bytes,
            )),
        )
        .route(
            "/estimate/generic-structure-trade-counts",
            post(generic_structure_trade_count_estimates).layer(DefaultBodyLimit::max(
                structure_checkpoint_request_max_bytes,
            )),
        )
        .route(
            "/materialize/generic-structure-checkpoint",
            post(materialize_generic_structure_checkpoint).layer(DefaultBodyLimit::max(
                structure_checkpoint_request_max_bytes,
            )),
        )
        .route(
            "/materialize/indicator-warmup",
            post(materialize_indicator_warmup),
        )
        .route(
            "/materialize/generic-structure-snapshot",
            post(materialize_generic_structure_snapshot),
        )
        .route(
            "/materialize/generic-structure-snapshot-advance",
            post(materialize_generic_structure_snapshot_advance).layer(DefaultBodyLimit::max(
                structure_checkpoint_request_max_bytes,
            )),
        )
        .route(
            "/materialize/generic-structure-snapshot-session-advance",
            post(materialize_generic_structure_snapshot_session_advance),
        )
        .route(
            "/materialize/generic-structure-snapshot-session-batch",
            post(materialize_generic_structure_snapshot_session_batch),
        )
        .route(
            "/materialize/generic-structure-rebuild",
            post(materialize_generic_structure_rebuild),
        )
        .route(
            "/plans/watchlist-timeline/validate",
            post(validate_watchlist_timeline_plan),
        )
        .route(
            "/materialize/watchlist-timeline",
            post(materialize_watchlist_timeline_plan)
                .layer(DefaultBodyLimit::max(watchlist_request_max_bytes)),
        )
        .route(
            "/materialize/watchlist-timelines",
            post(materialize_watchlist_timeline_batch)
                .layer(DefaultBodyLimit::max(watchlist_request_max_bytes)),
        )
        .route(
            "/snapshot/chart-macro-bars/{ticker}",
            get(chart_macro_bar_snapshot),
        )
        .route("/snapshot/family-bars/{ticker}", get(family_bar_snapshot))
        .route(
            "/snapshot/condition-bars/{ticker}",
            get(condition_bar_snapshot),
        )
        .route("/snapshot/macro-bars/{ticker}", get(macro_bar_snapshot))
        .route("/stream/compact-events", get(compact_event_stream))
        .route("/stream/events", get(event_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .route("/stream/indicators/{ticker}", get(indicator_stream))
        .route("/stream/derived/{ticker}", get(derived_stream))
        .layer(CorsLayer::permissive())
        .layer(middleware::from_fn(
            qmd_core::request_identity::preserve_request_identity,
        ))
        .with_state(Arc::new(state))
}

async fn capability_catalog_snapshot() -> Json<Vec<ComputationCapability<'static>>> {
    Json(computation_capability_catalog())
}

async fn scanner_derived_snapshot(
    Query(query): Query<ScannerDerivedQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<HistoricalScannerDerivedSnapshot>, ApiError> {
    let as_of = parse_timestamp(&query.as_of)?;
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    let mut replay_window = window(&query.start, &query.end, tickers)?;
    replay_window.end = replay_window.end.min(as_of);
    state
        .scanner
        .snapshot(replay_window, as_of)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn scanner_market_snapshot(
    Query(query): Query<ScannerDerivedQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<HistoricalScannerMarketSnapshot>, ApiError> {
    let as_of = parse_timestamp(&query.as_of)?;
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    let mut replay_window = window(&query.start, &query.end, tickers)?;
    replay_window.end = replay_window.end.min(as_of);
    state
        .source
        .scanner_market_snapshot(replay_window, as_of)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn materialize_generic_structure_checkpoint(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureCheckpointAdvanceRequest>,
) -> Result<Json<StructureCheckpointAdvanceResponse>, ApiError> {
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure checkpoint advancement capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_checkpoint_advancement",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    advance_structure_checkpoint(&state.config, &state.source, request)
        .await
        .map(Json)
        .map_err(structure_checkpoint_advancement_error)
}

async fn materialize_generic_structure_snapshot_advance(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureCheckpointAdvanceRequest>,
) -> Result<Json<StructureCheckpointAdvanceResponse>, ApiError> {
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure historical advancement capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_historical_structure_advancement",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    advance_historical_structure_snapshot(&state.config, &state.source, request)
        .await
        .map(Json)
        .map_err(structure_checkpoint_advancement_error)
}

async fn materialize_generic_structure_snapshot_session_advance(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureSnapshotSessionAdvanceRequest>,
) -> Result<Json<StructureSnapshotSessionAdvanceResponse>, ApiError> {
    if request.schema_version != 1 {
        return Err(service_error(format!(
            "invalid Generic Structure historical session schema_version {}; expected 1",
            request.schema_version
        )));
    }
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure historical advancement capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_historical_structure_advancement",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    let session_id = request.session_id.trim().to_string();
    let checkpoint = state
        .structure_snapshot_sessions
        .checkout(&session_id)
        .await
        .map_err(structure_checkpoint_advancement_error)?;
    let advance_request = StructureCheckpointAdvanceRequest {
        schema_version: request.schema_version,
        checkpoint: checkpoint.clone(),
        as_of: request.as_of,
        expected_source_plan_hash: request.expected_source_plan_hash,
        event_limit: request.event_limit,
    };
    let advanced =
        match advance_historical_structure_snapshot(&state.config, &state.source, advance_request)
            .await
        {
            Ok(advanced) => advanced,
            Err(error) => {
                state
                    .structure_snapshot_sessions
                    .replace(session_id, checkpoint)
                    .await;
                return Err(structure_checkpoint_advancement_error(error));
            }
        };
    state
        .structure_snapshot_sessions
        .replace(session_id.clone(), advanced.checkpoint)
        .await;
    Ok(Json(StructureSnapshotSessionAdvanceResponse {
        schema_version: advanced.schema_version,
        session_id,
        as_of: advanced.as_of,
        replay_start: advanced.replay_start,
        event_count: advanced.event_count,
        advanced_event_count: advanced.advanced_event_count,
        snapshot: advanced.snapshot,
        source_plan: advanced.source_plan,
        source_revision_before: advanced.source_revision_before,
        source_revision_after: advanced.source_revision_after,
        complete: advanced.complete,
    }))
}

async fn materialize_generic_structure_snapshot_session_batch(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureSnapshotSessionBatchRequest>,
) -> Result<Json<StructureSnapshotSessionBatchResponse>, ApiError> {
    if request.schema_version != 1 {
        return Err(service_error(format!(
            "invalid Generic Structure historical batch schema_version {}; expected 1",
            request.schema_version
        )));
    }
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure historical advancement capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_historical_structure_batch",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    let session_id = request.session_id.trim().to_string();
    let checkpoint = state
        .structure_snapshot_sessions
        .checkout(&session_id)
        .await
        .map_err(structure_checkpoint_advancement_error)?;
    let advanced = match advance_historical_structure_timeline(
        &state.config,
        &state.source,
        checkpoint.clone(),
        &request,
    )
    .await
    {
        Ok(advanced) => advanced,
        Err(error) => {
            state
                .structure_snapshot_sessions
                .replace(session_id, checkpoint)
                .await;
            return Err(structure_checkpoint_advancement_error(error));
        }
    };
    state
        .structure_snapshot_sessions
        .replace(session_id, advanced.0)
        .await;
    Ok(Json(advanced.1))
}

async fn materialize_generic_structure_snapshot(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureSnapshotRequest>,
) -> Result<Json<Value>, ApiError> {
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure snapshot capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_structure_snapshot",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    let prepared_seed = state
        .cache
        .structure_session_seed(&request.ticker, request.as_of)
        .await
        .map_err(structure_checkpoint_advancement_error)?;
    let response = if let Some(seed) = prepared_seed {
        materialize_structure_snapshot_from_seed(&state.config, &state.source, request, seed).await
    } else {
        materialize_structure_snapshot(&state.config, &state.source, request).await
    }
    .map_err(structure_checkpoint_advancement_error)?;
    let session_id = state
        .structure_snapshot_sessions
        .register(response.checkpoint.clone())
        .await;
    let mut value = serde_json::to_value(response).map_err(|error| {
        service_error(format!(
            "failed to serialize Generic Structure snapshot: {error}"
        ))
    })?;
    value["session_id"] = json!(session_id);
    Ok(Json(value))
}

async fn materialize_generic_structure_rebuild(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureCheckpointRebuildRequest>,
) -> Result<Json<StructureCheckpointRebuildResponse>, ApiError> {
    if !is_loopback_bind(&state.config.bind) {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "Generic Structure checkpoint rebuild is available only when QMD History is bound to loopback",
                "error_code": "structure_checkpoint_rebuild_not_local",
                "retryable": false,
                "source": "qmd_history_gateway",
            })),
        ));
    }
    let _permit = state
        .structure_checkpoint_advancement_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "Generic Structure checkpoint rebuild capacity is busy",
                    "error_code": "structure_checkpoint_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_checkpoint_rebuild",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    rebuild_structure_checkpoint(&state.config, &state.source, request)
        .await
        .map(Json)
        .map_err(structure_checkpoint_advancement_error)
}

async fn generic_structure_trade_count_estimates(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureTradeCountEstimateRequest>,
) -> Result<Json<StructureTradeCountEstimateResponse>, ApiError> {
    if !is_loopback_bind(&state.config.bind) {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "Generic Structure planning estimates are available only when QMD History is bound to loopback",
                "error_code": "structure_checkpoint_estimate_not_local",
                "retryable": false,
                "source": "qmd_history_gateway",
            })),
        ));
    }
    state
        .source
        .structure_trade_count_estimates(request)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn generic_structure_event_count_estimates(
    State(state): State<Arc<AppState>>,
    Json(request): Json<StructureEventCountEstimateRequest>,
) -> Result<Json<StructureEventCountEstimateResponse>, ApiError> {
    if !is_loopback_bind(&state.config.bind) {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "Generic Structure planning estimates are available only when QMD History is bound to loopback",
                "error_code": "structure_checkpoint_estimate_not_local",
                "retryable": false,
                "source": "qmd_history_gateway",
            })),
        ));
    }
    state
        .source
        .structure_event_count_estimates(request)
        .await
        .map(Json)
        .map_err(service_error)
}

fn is_loopback_bind(bind: &str) -> bool {
    bind.parse::<std::net::SocketAddr>()
        .map(|address| address.ip().is_loopback())
        .unwrap_or(false)
}

async fn validate_watchlist_timeline_plan(
    Json(plan): Json<HistoricalWatchlistPlan>,
) -> Result<Json<HistoricalWatchlistPlanValidation>, ApiError> {
    validate_plan(&plan).map(Json).map_err(bad_request)
}

async fn materialize_watchlist_timeline_plan(
    State(state): State<Arc<AppState>>,
    Json(request): Json<HistoricalWatchlistTimelineRequest>,
) -> Result<Json<HistoricalWatchlistTimelineMaterialization>, ApiError> {
    let _permit = state
        .watchlist_materialization_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "historical Watchlist materialization capacity is busy",
                    "error_code": "watchlist_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_materialization",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    materialize_watchlist_timeline(state.config.clone(), state.source.clone(), request)
        .await
        .map(Json)
        .map_err(watchlist_materialization_error)
}

async fn materialize_watchlist_timeline_batch(
    State(state): State<Arc<AppState>>,
    Json(request): Json<HistoricalWatchlistTimelineBatchRequest>,
) -> Result<Json<HistoricalWatchlistTimelineBatchMaterialization>, ApiError> {
    let _permit = state
        .watchlist_materialization_permits
        .clone()
        .try_acquire_owned()
        .map_err(|_| {
            (
                StatusCode::TOO_MANY_REQUESTS,
                Json(json!({
                    "error": "historical Watchlist materialization capacity is busy",
                    "error_code": "watchlist_capacity_busy",
                    "retryable": true,
                    "retry_action": "retry_materialization",
                    "source": "qmd_history_gateway",
                })),
            )
        })?;
    materialize_watchlist_timelines(state.config.clone(), state.source.clone(), request)
        .await
        .map(Json)
        .map_err(watchlist_materialization_error)
}

async fn health(State(state): State<Arc<AppState>>) -> Result<Json<HealthPayload>, ApiError> {
    state.source.health().await.map_err(service_error)?;
    Ok(Json(HealthPayload {
        cache: state.cache.metrics().await,
        calculation_revision: HISTORICAL_CALCULATION_REVISION,
        corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
        config: state.config.clone(),
        host_role: "historical",
        running: true,
        service: "qmd_history_gateway",
        source: "market_source_plan:archive+recent+live_continuation",
        status: "ready",
        structure_algorithm_version:
            qmd_core::generic_structure::GENERIC_STRUCTURE_ALGORITHM_VERSION,
    }))
}

async fn status_snapshot(State(state): State<Arc<AppState>>) -> Result<Json<Value>, ApiError> {
    state.source.health().await.map_err(service_error)?;
    let cache = state.cache.metrics().await;
    let latest = state
        .source
        .latest_coverage_before(None)
        .await
        .map_err(service_error)?;
    Ok(Json(history_status_payload(&state.config, &cache, &latest)))
}

fn history_status_payload(
    config: &HistoricalGatewayConfig,
    cache: &CacheMetrics,
    latest: &LatestEventCoverage,
) -> Value {
    let requests = cache.hits + cache.misses;
    let hit_rate = if requests == 0 {
        None
    } else {
        Some(cache.hits as f64 / requests as f64)
    };
    let archive_evidence = if latest.session_date.is_some() {
        "ready"
    } else {
        "unknown"
    };
    json!({
        "attention": [],
        "live_pipeline": [],
        "downstream_products": [
            {
                "product": "Historical compact events",
                "enabled": true,
                "state": "ready",
                "detail": "Queries are planned across archive, recent q_live, and current-live continuation tiers."
            },
            {
                "product": "Derived chart and scanner products",
                "enabled": true,
                "state": "ready",
                "detail": "Bars and indicators are derived through the shared QMD computation library."
            }
        ],
        "header": {
            "service": "qmd_history_gateway",
            "status": "READY",
            "bind": config.bind,
            "mode": "read_only",
            "read_database": config.clickhouse_database,
            "recent_database": config.recent_database,
            "snapshot_utc": Utc::now().to_rfc3339(),
            "host_role": "historical"
        },
        "current_operation": {
            "phase": if cache.active_builds > 0 { "building" } else { "serving" },
            "status": "running",
            "message": if cache.active_builds > 0 {
                "Historical derived-cache builds are active."
            } else {
                "Waiting for bounded historical requests."
            },
            "next_action": ""
        },
        "configuration": {
            "batch_size": config.batch_size,
            "cache_max_concurrent_builds": config.cache_max_concurrent_builds,
            "cache_max_entries": config.cache_max_entries,
            "max_events_per_request": config.max_events_per_request,
            "source_tiers": ["archive", "recent", "current_live"]
        },
        "runtime": {
            "active_builds": cache.active_builds,
            "builds": cache.builds,
            "cache_entries": cache.entries,
            "cache_estimated_bytes": cache.estimated_bytes,
            "cache_evictions": cache.evictions,
            "cache_hit_rate": hit_rate,
            "cache_hits": cache.hits,
            "cache_max_bytes": cache.max_bytes,
            "cache_misses": cache.misses
        },
        "tasks": [
            {
                "task": "derived cache builds",
                "status": if cache.active_builds > 0 { "running" } else { "idle" },
                "rows": cache.entries,
                "active": cache.active_builds,
                "message": "Bounded, shared historical computations."
            }
        ],
        "coverage": {
            "status": archive_evidence,
            "message": if let Some(session) = latest.session_date.as_deref() {
                format!("Archive coverage is published through session {session}.")
            } else {
                "No published archive coverage session was reported.".to_string()
            },
            "archive_session_date": latest.session_date,
            "archive_event_count": latest.event_count,
            "archive_ticker_count": latest.ticker_count,
            "coverage_table": latest.coverage_table
        },
        "queues": {
            "active_builds": cache.active_builds,
            "build_capacity": config.cache_max_concurrent_builds
        },
        "error_state": {
            "status": "ok",
            "active": false,
            "severity": "info",
            "message": "",
            "retryable": true,
            "last_error": ""
        },
        "service_specific": {
            "cache": cache,
            "latest_archive_coverage": latest,
            "source": "market_source_plan:archive+recent+live_continuation"
        }
    })
}

async fn cache_snapshot(State(state): State<Arc<AppState>>) -> Json<CacheMetrics> {
    Json(state.cache.metrics().await)
}

async fn config(State(state): State<Arc<AppState>>) -> Json<HistoricalGatewayConfig> {
    Json(state.config.clone())
}

async fn coverage(
    Query(query): Query<HistoryQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<EventCoverage>, ApiError> {
    let window = window(&query.start, &query.end, Vec::new())?;
    state
        .source
        .coverage(&window)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn latest_coverage(
    Query(query): Query<LatestCoverageQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<LatestEventCoverage>, ApiError> {
    let before = query
        .before
        .as_deref()
        .map(|value| {
            chrono::NaiveDate::parse_from_str(value, "%Y-%m-%d")
                .map_err(|_| bad_request("before must be an ISO date"))
        })
        .transpose()?;
    state
        .source
        .latest_coverage_before(before)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn tradable_universe(
    Query(query): Query<TradableUniverseQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<TradableUniverseResponse>, ApiError> {
    let as_of = chrono::NaiveDate::parse_from_str(&query.as_of, "%Y-%m-%d")
        .map_err(|_| bad_request("as_of must be an ISO date"))?;
    let tickers = state
        .source
        .tradable_tickers(as_of)
        .await
        .map_err(service_error)?;
    Ok(Json(TradableUniverseResponse {
        as_of: query.as_of,
        count: tickers.len(),
        source: "q_live.feature_tradable_universe_v1:is_tradable",
        tickers,
    }))
}

async fn materialize_indicator_warmup(
    State(state): State<Arc<AppState>>,
    Json(request): Json<IndicatorWarmupRequest>,
) -> Result<Json<IndicatorWarmupArtifact>, ApiError> {
    if !is_loopback_bind(&state.config.bind) {
        return Err((
            StatusCode::FORBIDDEN,
            Json(json!({
                "error": "Indicator warm-up materialization is available only on loopback",
                "error_code": "indicator_warmup_not_local",
                "retryable": false,
                "source": "qmd_history_gateway",
            })),
        ));
    }
    state
        .cache
        .prepare_indicator_warmup(
            &request.ticker,
            request.timeframe.as_deref().unwrap_or("1s"),
            request.session_start,
            request.required_bars.unwrap_or(200),
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn source_plan(
    Query(query): Query<SourcePlanQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<MarketSourcePlan>, ApiError> {
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    let window = window(&query.start, &query.end, tickers)?;
    state
        .source
        .source_plan(&window)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn source_revision_snapshot(
    Query(query): Query<SourcePlanQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<HistoricalSourceRevisionPayload>, ApiError> {
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    let window = window(&query.start, &query.end, tickers)?;
    let source_revision = state
        .source
        .source_revision(&window)
        .await
        .map_err(service_error)?;
    Ok(Json(HistoricalSourceRevisionPayload {
        source_revision,
        calculation_revision: HISTORICAL_CALCULATION_REVISION,
        corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
    }))
}

async fn compact_event_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<HistoryQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<Vec<LiveCompactEvent>>, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker])?;
    let limit = query
        .limit
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    let events = if query.tail.unwrap_or(false) {
        state.source.fetch_latest(&window, limit).await
    } else {
        state
            .source
            .fetch_batch(&window, None, limit)
            .await
            .map(|(events, _)| events)
    }
    .map_err(service_error)?;
    Ok(Json(events))
}

async fn event_page_snapshot(
    Query(query): Query<EventPageQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<MarketEventPage>, ApiError> {
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    let window = window(&query.start, &query.end, tickers)?;
    let expected_revision = expected_event_revision(
        query.revision_policy,
        query.expected_source_plan_hash.as_deref(),
        query.expected_revision_token.as_deref(),
    )?;
    let source_revision = state
        .source
        .source_revision(&window)
        .await
        .map_err(service_error)?;
    if let Some((expected_plan_hash, expected_revision_token)) = expected_revision {
        if event_revision_changed(
            query.revision_policy,
            expected_plan_hash,
            expected_revision_token,
            &source_revision,
        ) {
            return Err(source_revision_conflict(
                expected_plan_hash,
                expected_revision_token.unwrap_or_default(),
                query.revision_policy,
                &source_revision,
            ));
        }
    }
    let limit = query
        .limit
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    let cursor = match (
        query.cursor_sip_timestamp_us,
        query.cursor_ticker,
        query.cursor_ordinal,
    ) {
        (None, None, None) => None,
        (Some(sip_timestamp_us), Some(ticker), Some(ordinal)) => Some(HistoricalCursor {
            ordinal,
            sip_timestamp_us,
            ticker,
        }),
        _ => return Err(bad_request(
            "cursor_sip_timestamp_us, cursor_ticker, and cursor_ordinal must be supplied together",
        )),
    };
    let requested_kinds = query
        .kinds
        .as_deref()
        .unwrap_or("trade,quote")
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(|value| value.trim().to_ascii_lowercase())
        .collect::<std::collections::HashSet<_>>();
    if requested_kinds.is_empty()
        || requested_kinds
            .iter()
            .any(|value| !matches!(value.as_str(), "trade" | "quote"))
    {
        return Err(bad_request("kinds must contain trade and/or quote"));
    }
    let event_type_filter = compact_event_type_filter(&requested_kinds);
    let (events, next_cursor) = state
        .source
        .fetch_batch_at_revision_filtered(
            &window,
            cursor.as_ref(),
            limit,
            source_revision.live_continuation_sequence,
            event_type_filter,
        )
        .await
        .map_err(service_error)?;
    let complete = events.len() < limit || next_cursor.is_none();
    Ok(Json(MarketEventPage {
        complete,
        events: events
            .iter()
            .map(|event| state.source.market_event(event))
            .filter(|event| {
                matches!(event, MarketEvent::Trade(_)) && requested_kinds.contains("trade")
                    || matches!(event, MarketEvent::Quote(_)) && requested_kinds.contains("quote")
            })
            .collect(),
        next_cursor,
        revision_policy: query.revision_policy.as_str(),
        source_revision,
    }))
}

fn compact_event_type_filter(requested_kinds: &std::collections::HashSet<String>) -> Option<u8> {
    if requested_kinds.len() != 1 {
        return None;
    }
    Some(if requested_kinds.contains("quote") {
        0
    } else {
        1
    })
}

async fn bar_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<DerivedSnapshot>, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker.clone()])?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let bar_limit = query.limit.unwrap_or(1_000).clamp(1, 100_000);
    let _legacy_event_limit = query.event_limit;
    state
        .cache
        .snapshot(window, ticker, timeframe, bar_limit)
        .await
        .map(Json)
        .map_err(service_error)
}

async fn chart_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ChartQuery>,
) -> Result<Json<Value>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    if !state
        .config
        .product_timeframes
        .iter()
        .any(|candidate| candidate.eq_ignore_ascii_case(&timeframe))
    {
        return Err(bad_request(format!(
            "unsupported chart timeframe {timeframe}; configured values are {}",
            state.config.product_timeframes.join(", ")
        )));
    }
    let product_query = ProductQuery {
        as_of: query.as_of,
        end: query.end,
        limit: query.limit,
        resolution: Some(timeframe.clone()),
        start: query.start,
        timeframe: None,
    };
    let (window, as_of) = causal_product_window(&product_query, &ticker)?;
    let before = query.before.as_deref().map(parse_timestamp).transpose()?;
    let indicator_columns = parse_indicator_projection(query.indicator_columns.as_deref())?;
    let structure_only = indicator_columns.as_ref().is_some_and(|columns| {
        columns.len() == 2
            && columns.contains("bar_start")
            && columns.contains("qmd_structure_unified_levels")
    });
    let bars_only = parse_chart_stage(query.stage.as_deref())?;
    let mode = parse_chart_mode(query.mode.as_deref())?;
    if bars_only && mode == "live" && query.allow_persisted_bars.unwrap_or(true) {
        if let Some(persisted) = state
            .source
            .persisted_intraday_chart_bars(
                &window,
                &ticker,
                &timeframe,
                product_query.limit.unwrap_or(5_000).clamp(1, 50_000),
                as_of,
                before,
                true,
            )
            .await
            .map_err(service_error)?
            // v3 persisted intraday bars were aggregated on SIP availability
            // time. They remain valid causal-state artifacts, but are not a
            // retrospective execution-time chart authority. A future rebuilt
            // population must advertise the explicit v4 revision before this
            // fast path may serve it.
            .filter(|persisted| {
                persisted.source == "qmd_live_intraday_family_bars_v4_execution_time"
            })
        {
            let event_count = persisted
                .bars
                .iter()
                .map(|bar| bar.event_count)
                .sum::<u64>();
            let persisted_source = persisted.source.clone();
            let bars = persisted
                .bars
                .iter()
                .map(|bar| {
                    json!({
                        "schema_version": 1,
                        "session_date": bar.session_date,
                        "timeframe": timeframe,
                        "sym": ticker,
                        "bar_start": bar.bar_start,
                        "bar_end": bar.bar_end,
                        "is_closed": true,
                        "open": bar.open,
                        "high": bar.high,
                        "low": bar.low,
                        "close": bar.close,
                        "volume": bar.size_sum,
                        "vwap": Value::Null,
                        "execution_vwap": Value::Null,
                        "estimated_luld_active": false,
                        "estimated_luld_reference_price": 0.0,
                        "estimated_luld_lower_price": 0.0,
                        "estimated_luld_upper_price": 0.0,
                        "estimated_luld_distance_to_upper_pct": 0.0,
                        "estimated_luld_distance_to_lower_pct": 0.0,
                        "estimated_luld_state": "unavailable",
                    })
                })
                .collect::<Vec<_>>();
            return Ok(Json(json!({
                "as_of": as_of,
                "bars": bars,
                "cache": {
                    "calculation_revision": HISTORICAL_CALCULATION_REVISION,
                    "corporate_action_revision": HISTORICAL_CORPORATE_ACTION_REVISION,
                    "engine_version": HISTORICAL_ENGINE_VERSION,
                    "event_count": event_count,
                    "hit": true,
                    "source_revision": {
                        "complete_for_history": true,
                        "event_count": event_count,
                        "live_continuation_sequence": Value::Null,
                        "max_build_step": 0,
                        "max_updated_at": "",
                        "request_complete": true,
                        "source_plan_hash": persisted_source,
                        "source_tiers": ["persisted_intraday_base_bars"],
                        "token": persisted_source,
                    }
                },
                "has_more": persisted.has_more,
                "indicators": [],
                "indicators_available": false,
                "market_signal_events": [],
                "next_before": persisted.next_before,
                "structure_events": [],
                "structure_level_history": [],
                "ticker": ticker,
                "timeframe": timeframe,
            })));
        }
    }
    let mut snapshot = state
        .cache
        .chart_snapshot(
            window.clone(),
            ticker.clone(),
            timeframe.clone(),
            product_query.limit.unwrap_or(5_000).clamp(1, 50_000),
            as_of,
            before,
            bars_only,
            structure_only,
        )
        .await
        .map_err(|error| {
            if structure_only {
                structure_checkpoint_advancement_error(error)
            } else {
                service_error(error)
            }
        })?;
    if !bars_only && !structure_only && qmd_core::bars::is_supported_timeframe(&timeframe) {
        // A full chart composes two explicit clocks: retrospective OHLCV uses
        // execution time, while MACD and strategy evidence stay causal in SIP
        // availability order. Requesting indicators must never change candles.
        let chart_bars = state
            .cache
            .chart_snapshot(
                window.clone(),
                ticker.clone(),
                timeframe.clone(),
                product_query.limit.unwrap_or(5_000).clamp(1, 50_000),
                as_of,
                before,
                true,
                false,
            )
            .await
            .map_err(service_error)?;
        snapshot.bars = chart_bars.bars;
        snapshot.has_more = chart_bars.has_more;
        snapshot.next_before = chart_bars.next_before;
    }
    let split_adjustments = state
        .source
        .structure_split_adjustments(
            &ticker,
            window.start - chrono::Duration::milliseconds(1),
            as_of,
        )
        .await
        .map_err(service_error)?;
    adjust_chart_snapshot_for_splits(&mut snapshot, &split_adjustments);
    project_chart_snapshot(
        snapshot,
        indicator_columns.as_ref(),
        query.include_market_signals.unwrap_or(true),
        query.include_structure.unwrap_or(true),
    )
    .map(Json)
}

fn adjust_chart_snapshot_for_splits(
    snapshot: &mut ChartSnapshot,
    adjustments: &[qmd_core::generic_structure::StructureSplitAdjustment],
) {
    for bar in &mut snapshot.bars {
        let (price_factor, share_factor) = split_adjustment_factors(bar.bar_start, adjustments);
        bar.open *= price_factor;
        bar.high *= price_factor;
        bar.low *= price_factor;
        bar.close *= price_factor;
        bar.volume *= share_factor;
        bar.vwap = bar.vwap.map(|value| value * price_factor);
        bar.estimated_luld_reference_price *= price_factor;
        bar.estimated_luld_lower_price *= price_factor;
        bar.estimated_luld_upper_price *= price_factor;
    }
}

fn parse_chart_stage(raw: Option<&str>) -> Result<bool, ApiError> {
    match raw.unwrap_or("full") {
        "bars" => Ok(true),
        "full" => Ok(false),
        value => Err(bad_request(format!(
            "invalid chart stage {value}; expected bars or full"
        ))),
    }
}

fn parse_chart_mode(raw: Option<&str>) -> Result<&str, ApiError> {
    match raw.unwrap_or("live") {
        value @ ("live" | "replay" | "backtest" | "debug") => Ok(value),
        value => Err(bad_request(format!(
            "invalid chart mode {value}; expected live, replay, backtest, or debug"
        ))),
    }
}

fn parse_indicator_projection(raw: Option<&str>) -> Result<Option<BTreeSet<String>>, ApiError> {
    let Some(raw) = raw else {
        return Ok(None);
    };
    let mut columns = BTreeSet::from(["bar_start".to_string()]);
    for column in raw
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
    {
        if column.len() > 64
            || !column
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || byte == b'_')
        {
            return Err(bad_request(format!("invalid indicator column {column}")));
        }
        columns.insert(column.to_string());
        if columns.len() > 128 {
            return Err(bad_request("too many projected indicator columns"));
        }
    }
    Ok(Some(columns))
}

fn project_chart_snapshot(
    mut snapshot: ChartSnapshot,
    columns: Option<&BTreeSet<String>>,
    include_market_signals: bool,
    include_structure: bool,
) -> Result<Value, ApiError> {
    let provenance = chart_indicator_provenance(&snapshot, columns);
    let Some(columns) = columns else {
        let mut value = serde_json::to_value(snapshot).map_err(|error| {
            service_error(format!("failed to serialize chart snapshot: {error}"))
        })?;
        if let Some(object) = value.as_object_mut() {
            object.insert("indicator_provenance".to_string(), provenance);
            if !include_market_signals {
                object.insert("market_signal_events".to_string(), json!([]));
            }
            if !include_structure {
                object.insert("structure_events".to_string(), json!([]));
                object.insert("structure_level_history".to_string(), json!([]));
            }
        }
        return Ok(value);
    };
    let mut indicators = if let Some(projected) = snapshot.indicator_projection.take() {
        projected
    } else {
        let indicator_count = snapshot.indicators.len();
        let mut projected = snapshot
            .indicators
            .into_iter()
            .enumerate()
            .map(|(index, indicator)| {
                let mut value = serde_json::to_value(indicator).map_err(|error| {
                    service_error(format!("failed to serialize chart indicator: {error}"))
                })?;
                if let Some(object) = value.as_object_mut() {
                    object.retain(|key, _| columns.contains(key));
                    if index + 1 < indicator_count {
                        object.remove("qmd_structure_active_levels");
                        object.remove("qmd_structure_timeframe_states");
                    }
                }
                Ok(value)
            })
            .collect::<Result<Vec<_>, ApiError>>()?;
        compact_projected_unified_structure_history(&mut projected);
        projected
    };
    indicators.iter_mut().for_each(|row| {
        if let Some(object) = row.as_object_mut() {
            object.retain(|key, _| {
                columns.contains(key) || key == "qmd_structure_unified_level_delta"
            });
        }
    });
    Ok(json!({
        "as_of": snapshot.as_of,
        "bars": snapshot.bars,
        "cache": snapshot.cache,
        "has_more": snapshot.has_more,
        "indicators": indicators,
        "indicators_available": snapshot.indicators_available,
        "indicator_provenance": provenance,
        "market_signal_events": if include_market_signals { snapshot.market_signal_events } else { Vec::new() },
        "next_before": snapshot.next_before,
        "structure_events": if include_structure { snapshot.structure_events } else { Vec::new() },
        "structure_level_history": if include_structure { snapshot.structure_level_history } else { Vec::new() },
        "ticker": snapshot.ticker,
        "timeframe": snapshot.timeframe,
    }))
}

/// The unified level book is a state snapshot, not an independent observation
/// on every bar. Keep the first state, every presentation-significant
/// transition, and the terminal state. Omitting an unchanged intermediate
/// field means "carry the prior book forward"; an explicit empty array still
/// means that every previously visible level ended at that bar.
fn compact_projected_unified_structure_history(indicators: &mut [Value]) {
    let terminal_index = indicators.len().saturating_sub(1);
    let mut previous = BTreeMap::<String, (Value, Value)>::new();
    for (index, indicator) in indicators.iter_mut().enumerate() {
        let Some(object) = indicator.as_object_mut() else {
            continue;
        };
        let Some(levels) = object.get("qmd_structure_unified_levels").cloned() else {
            continue;
        };
        // A chart row is either a full level-book snapshot or a delta, never
        // both. Clear any projection inherited from a prepared/base row before
        // writing the compact authority for this row.
        object.remove("qmd_structure_unified_level_delta");
        let current = unified_structure_level_map(&levels);
        if index > 0 && index != terminal_index {
            let upserts = current
                .iter()
                .filter_map(|(key, (signature, level))| {
                    (previous.get(key).map(|(value, _)| value) != Some(signature))
                        .then(|| level.clone())
                })
                .collect::<Vec<_>>();
            let removed = previous
                .iter()
                .filter_map(|(key, (_, level))| {
                    if current.contains_key(key) {
                        return None;
                    }
                    Some(json!({
                        "unified_level_id": level.get("unified_level_id").cloned().unwrap_or(Value::Null),
                        "side": level.get("side").cloned().unwrap_or(Value::Null),
                    }))
                })
                .collect::<Vec<_>>();
            object.remove("qmd_structure_unified_levels");
            if !upserts.is_empty() || !removed.is_empty() {
                object.insert(
                    "qmd_structure_unified_level_delta".to_string(),
                    json!({"upserts": upserts, "removed": removed}),
                );
            }
        }
        previous = current;
    }
}

fn unified_structure_level_map(levels: &Value) -> BTreeMap<String, (Value, Value)> {
    levels
        .as_array()
        .into_iter()
        .flatten()
        .filter_map(|level| {
            let identity = format!("{}:{}", level.get("unified_level_id")?, level.get("side")?);
            let mut signature = level.clone();
            if let Some(object) = signature.as_object_mut() {
                // These grow with prints but do not alter a band's bounds,
                // role, line length, opacity, or explanatory label.
                object.remove("total_volume");
                object.remove("trade_count");
                object.remove("sources");
                object.remove("source_count");
                object.remove("touch_count");
                object.remove("hold_count");
                object.remove("last_test_at_ms");
                // The chart communicates the observed hold rate as a whole-
                // percent value across a finite pixel span.
                // Sub-percent churn is not presentation-significant and must
                // not turn every trade-bearing bar into a new state.
                for key in ["hold_probability"] {
                    if let Some(number) = object.get(key).and_then(Value::as_f64) {
                        object.insert(key.to_string(), json!((number * 20.0).round() / 20.0));
                    }
                }
            }
            Some((identity, (signature, level.clone())))
        })
        .collect()
}

fn chart_indicator_provenance(
    snapshot: &ChartSnapshot,
    columns: Option<&BTreeSet<String>>,
) -> Value {
    let returned_bars = snapshot.bars.len();
    let recommended_minimum_bars = 50_usize;
    let requested_columns = columns
        .map(|values| {
            values
                .iter()
                .filter(|value| value.as_str() != "bar_start")
                .cloned()
                .collect::<Vec<_>>()
        })
        .unwrap_or_default();
    json!({
        "schema_version": 1,
        "capability_id": "qmd.family.core_momentum_and_structure",
        "calculation_scope": "request",
        "calculation_revision": snapshot.cache.calculation_revision,
        "corporate_action_revision": snapshot.cache.corporate_action_revision,
        "engine_version": snapshot.cache.engine_version,
        "indicator_schema_version": INDICATOR_SCHEMA_VERSION,
        "effective_parameters": {
            "atr_period": 14,
            "bollinger_period": 20,
            "bollinger_standard_deviations": 2,
            "ema_periods": [9, 20, 50],
            "macd_fast_period": 12,
            "macd_signal_period": 9,
            "macd_slow_period": 26,
            "rsi_period": 14
        },
        "requested_columns": requested_columns,
        "warm_up": {
            "recommended_minimum_bars": recommended_minimum_bars,
            "returned_bars": returned_bars,
            "status": if returned_bars >= recommended_minimum_bars { "satisfied_in_response" } else { "partial_in_response" }
        },
        "source": {
            "complete_for_history": snapshot.cache.source_revision.complete_for_history,
            "event_count": snapshot.cache.event_count,
            "revision_token": snapshot.cache.source_revision.token,
            "source_plan_hash": snapshot.cache.source_revision.source_plan_hash,
            "tiers": snapshot.cache.source_revision.source_tiers
        },
        "as_of": snapshot.as_of,
        "observed_through": snapshot.bars.last().map(|bar| bar.bar_end),
        "complete": snapshot.cache.source_revision.complete_for_history,
        "stale_reason": if snapshot.cache.source_revision.complete_for_history { "" } else { "source_plan_requires_live_continuation_or_contains_a_gap" }
    })
}

async fn family_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<FamilyBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let resolution_us = product_resolution(&query)?;
    state
        .cache
        .family_snapshot(
            product_window,
            ticker,
            resolution_us,
            query
                .limit
                .unwrap_or(10_000)
                .min(state.config.product_cache_max_rows_per_entry),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn condition_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<ConditionBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let resolution_us = product_resolution(&query)?;
    state
        .cache
        .condition_snapshot(
            product_window,
            ticker,
            resolution_us,
            query
                .limit
                .unwrap_or(10_000)
                .min(state.config.product_cache_max_rows_per_entry),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn macro_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<MacroBarSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (product_window, as_of) = causal_product_window(&query, &ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1d".to_string());
    if !matches!(timeframe.as_str(), "1d" | "1w" | "1mo" | "1y") {
        return Err(bad_request("macro timeframe must be 1d, 1w, 1mo, or 1y"));
    }
    state
        .cache
        .macro_snapshot(
            product_window,
            ticker,
            timeframe,
            query.limit.unwrap_or(1_000).min(10_000),
            as_of,
        )
        .await
        .map(Json)
        .map_err(service_error)
}

async fn chart_macro_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Result<Json<crate::source::HistoricalMacroChartSnapshot>, ApiError> {
    let ticker = normalize_ticker(&ticker)?;
    let (window, as_of) = causal_product_window(&query, &ticker)?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1d".to_string());
    if !matches!(timeframe.as_str(), "1d" | "1w" | "1mo" | "1y") {
        return Err(bad_request(
            "chart macro timeframe must be 1d, 1w, 1mo, or 1y",
        ));
    }
    state
        .source
        .chart_macro_bars(&window, &ticker, &timeframe, as_of)
        .await
        .map(Json)
        .map_err(service_error)
}

fn causal_product_window(
    query: &ProductQuery,
    ticker: &str,
) -> Result<(EventWindow, DateTime<Utc>), ApiError> {
    let mut product_window = window(&query.start, &query.end, vec![ticker.to_string()])?;
    let as_of = query
        .as_of
        .as_deref()
        .map(parse_timestamp)
        .transpose()?
        .unwrap_or(product_window.end);
    if as_of <= product_window.start {
        return Err(bad_request("as_of must be after start"));
    }
    product_window.end = product_window.end.min(as_of);
    Ok((product_window, as_of))
}

fn product_resolution(query: &ProductQuery) -> Result<u64, ApiError> {
    match query.resolution.as_deref() {
        Some(value) => parse_resolution_us(value)
            .filter(|resolution| *resolution > 0)
            .ok_or_else(|| {
                bad_request("resolution must be a positive duration such as 100ms, 1s, or 1m")
            }),
        None => Ok(60_000_000),
    }
}

async fn compact_event_stream(
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = stream_window(&query)?;
    let batch_size = query
        .batch_size
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    Ok(websocket
        .on_upgrade(move |socket| stream_compact(socket, state.source.clone(), window, batch_size)))
}

async fn event_stream(
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = stream_window(&query)?;
    let batch_size = query
        .batch_size
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    Ok(websocket.on_upgrade(move |socket| {
        stream_market_events(socket, state.source.clone(), window, batch_size)
    }))
}

async fn bar_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let mut window = stream_window(&query)?;
    window.tickers = vec![ticker];
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let cache = state.cache.clone();
    Ok(websocket.on_upgrade(move |socket| stream_cached_bars(socket, cache, window, timeframe)))
}

async fn derived_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<DerivedStreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker.clone()])?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let emit = query.emit.unwrap_or_else(|| "updates".to_string());
    if !matches!(
        emit.as_str(),
        "frames" | "full" | "updates" | "full_then_updates"
    ) {
        return Err(bad_request(
            "emit must be frames, full, updates, or full_then_updates",
        ));
    }
    let as_of = query
        .as_of
        .as_deref()
        .map(parse_timestamp)
        .transpose()?
        .unwrap_or(window.start);
    if as_of < window.start || as_of > window.end {
        return Err(bad_request("as_of must be inside the requested window"));
    }
    let updates_per_second = query.updates_per_second.unwrap_or(0.0);
    if !updates_per_second.is_finite() || !(0.0..=10_000.0).contains(&updates_per_second) {
        return Err(bad_request(
            "updates_per_second must be between 0 and 10000; zero means unthrottled fast-forward",
        ));
    }
    if query.max_updates.is_some_and(|value| value == 0) {
        return Err(bad_request("max_updates must be greater than zero"));
    }
    let frame_batch_size = query.frame_batch_size.unwrap_or(1);
    if !(1..=5_000).contains(&frame_batch_size) {
        return Err(bad_request("frame_batch_size must be between 1 and 5000"));
    }
    let indicator_columns = parse_indicator_projection(query.indicator_columns.as_deref())?;
    let cache = state.cache.clone();
    Ok(websocket.on_upgrade(move |socket| {
        stream_derived(
            socket,
            cache,
            window,
            ticker,
            timeframe,
            emit,
            frame_batch_size,
            indicator_columns,
            as_of,
            query.after_sequence.unwrap_or(0),
            query.max_updates,
            updates_per_second,
            query.retain_cache.unwrap_or(true),
        )
    }))
}

async fn indicator_stream(
    Path(ticker): Path<String>,
    websocket: WebSocketUpgrade,
    Query(query): Query<StreamQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<impl IntoResponse, ApiError> {
    let mut window = stream_window(&query)?;
    window.tickers = vec![ticker];
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let cache = state.cache.clone();
    Ok(websocket
        .on_upgrade(move |socket| stream_cached_indicators(socket, cache, window, timeframe)))
}

async fn stream_compact(
    mut socket: WebSocket,
    source: HistoricalEventSource,
    window: EventWindow,
    batch_size: usize,
) {
    let mut cursor: Option<HistoricalCursor> = None;
    loop {
        let (events, next) = match source
            .fetch_batch(&window, cursor.as_ref(), batch_size)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                send_stream_error(&mut socket, error).await;
                return;
            }
        };
        for event in &events {
            if send_json(&mut socket, event).await.is_err() {
                return;
            }
        }
        if events.len() < batch_size || next.is_none() {
            let _ = socket.close().await;
            return;
        }
        cursor = next;
    }
}

async fn stream_market_events(
    mut socket: WebSocket,
    source: HistoricalEventSource,
    window: EventWindow,
    batch_size: usize,
) {
    let mut cursor: Option<HistoricalCursor> = None;
    loop {
        let (events, next) = match source
            .fetch_batch(&window, cursor.as_ref(), batch_size)
            .await
        {
            Ok(result) => result,
            Err(error) => {
                send_stream_error(&mut socket, error).await;
                return;
            }
        };
        for event in &events {
            if send_json(&mut socket, &source.market_event(event))
                .await
                .is_err()
            {
                return;
            }
        }
        if events.len() < batch_size || next.is_none() {
            let _ = socket.close().await;
            return;
        }
        cursor = next;
    }
}

async fn stream_cached_bars(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    timeframe: String,
) {
    let ticker = window.tickers[0].clone();
    let lease = match cache
        .acquire_derived(window, ticker, timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };
    let mut receiver = lease.entry.subscribe_bars();
    let mut last_sequence = 0;
    loop {
        let (frames, complete, error, _) = lease.entry.current_bars().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                && send_json(&mut socket, &frame.bar).await.is_err()
            {
                return;
            }
            last_sequence = frame.sequence;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                    if send_json(&mut socket, &frame.bar).await.is_err() {
                        return;
                    }
                }
                last_sequence = frame.sequence;
            }
            Ok(_) => {}
            Err(tokio::sync::broadcast::error::RecvError::Lagged(count)) => {
                send_stream_gap(&mut socket, "historical_bar_stream_lagged", count).await;
                return;
            }
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

async fn stream_cached_indicators(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    timeframe: String,
) {
    let ticker = window.tickers[0].clone();
    let lease = match cache
        .acquire_derived(window, ticker, timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };
    let mut receiver = lease.entry.subscribe();
    let mut last_sequence = 0;
    loop {
        let (frames, complete, error, _) = lease.entry.current().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                && send_json(&mut socket, &frame.indicator).await.is_err()
            {
                return;
            }
            last_sequence = frame.sequence;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
                    && send_json(&mut socket, &frame.indicator).await.is_err()
                {
                    return;
                }
                last_sequence = frame.sequence;
            }
            Ok(_) => {}
            Err(tokio::sync::broadcast::error::RecvError::Lagged(count)) => {
                send_stream_gap(&mut socket, "historical_indicator_stream_lagged", count).await;
                return;
            }
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

#[allow(clippy::too_many_arguments)]
async fn stream_derived(
    mut socket: WebSocket,
    cache: HistoricalDerivedCache,
    window: EventWindow,
    ticker: String,
    timeframe: String,
    emit: String,
    frame_batch_size: usize,
    indicator_columns: Option<BTreeSet<String>>,
    as_of: DateTime<Utc>,
    after_sequence: u64,
    max_updates: Option<u64>,
    updates_per_second: f64,
    retain_cache: bool,
) {
    let lease = match cache
        .acquire_derived(window, ticker.clone(), timeframe.clone())
        .await
    {
        Ok(lease) => lease,
        Err(error) => {
            send_stream_error(&mut socket, error).await;
            return;
        }
    };

    if emit == "frames" {
        let (frames, _, error, events_processed) = loop {
            let state = lease.entry.current().await;
            if state.1 {
                break state;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        };
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            if !retain_cache {
                cache.evict(&lease.key).await;
            }
            return;
        }
        let visible = frames
            .iter()
            .filter(|frame| {
                frame.as_of <= as_of
                    && frame.bar.is_closed
                    && frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
            })
            .collect::<Vec<_>>();
        let metadata = DerivedFramesMetadata {
            as_of,
            cache: CacheEvidence {
                calculation_revision: HISTORICAL_CALCULATION_REVISION,
                corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
                engine_version: HISTORICAL_ENGINE_VERSION,
                event_count: events_processed,
                hit: lease.hit,
                source_revision: lease.source_revision.clone(),
            },
            frame_count: visible.len(),
            indicator_columns: indicator_columns
                .as_ref()
                .map(|columns| columns.iter().cloned().collect())
                .unwrap_or_default(),
            ticker: ticker.clone(),
            timeframe: timeframe.clone(),
            update_type: "metadata",
        };
        if send_json(&mut socket, &metadata).await.is_err() {
            if !retain_cache {
                cache.evict(&lease.key).await;
            }
            return;
        }
        for frames in visible.chunks(frame_batch_size) {
            let result = if let Some(columns) = indicator_columns.as_ref() {
                let projected = match frames
                    .iter()
                    .map(|frame| project_derived_update(frame, columns))
                    .collect::<Result<Vec<_>, _>>()
                {
                    Ok(projected) => projected,
                    Err(error) => {
                        send_stream_error(&mut socket, error).await;
                        if !retain_cache {
                            cache.evict(&lease.key).await;
                        }
                        return;
                    }
                };
                if frame_batch_size == 1 {
                    send_json(&mut socket, &projected[0]).await
                } else {
                    send_json(
                        &mut socket,
                        &json!({"type": "frames_batch", "frames": projected}),
                    )
                    .await
                }
            } else if frame_batch_size == 1 {
                send_json(&mut socket, frames[0]).await
            } else {
                send_json(
                    &mut socket,
                    &DerivedFramesBatch {
                        frames,
                        update_type: "frames_batch",
                    },
                )
                .await
            };
            if result.is_err() {
                if !retain_cache {
                    cache.evict(&lease.key).await;
                }
                return;
            }
        }
        let _ = socket.close().await;
        if !retain_cache {
            cache.evict(&lease.key).await;
        }
        return;
    }

    if emit == "full" || emit == "full_then_updates" {
        let (frames, _, error, events_processed) = loop {
            let state = lease.entry.current().await;
            if state.1 {
                break state;
            }
            tokio::time::sleep(std::time::Duration::from_millis(10)).await;
        };
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        let visible = frames
            .iter()
            .filter(|frame| {
                frame.as_of <= as_of && frame.bar.timeframe.eq_ignore_ascii_case(&timeframe)
            })
            .cloned()
            .collect::<Vec<_>>();
        let full = FullDerivedEnvelope {
            as_of,
            bars: visible.iter().map(|frame| frame.bar.clone()).collect(),
            cache: CacheEvidence {
                calculation_revision: HISTORICAL_CALCULATION_REVISION,
                corporate_action_revision: HISTORICAL_CORPORATE_ACTION_REVISION,
                engine_version: HISTORICAL_ENGINE_VERSION,
                event_count: events_processed,
                hit: lease.hit,
                source_revision: lease.source_revision.clone(),
            },
            indicators: visible
                .iter()
                .map(|frame| frame.indicator.clone())
                .collect(),
            next_sequence: visible.last().map_or(0, |frame| frame.sequence),
            ticker: ticker.clone(),
            timeframe: timeframe.clone(),
            update_type: "full",
        };
        if send_json(&mut socket, &full).await.is_err() {
            return;
        }
        if emit == "full" {
            let _ = socket.close().await;
            return;
        }
    }

    let mut receiver = lease.entry.subscribe();
    let mut last_sequence = after_sequence;
    let mut updates_sent = 0_u64;
    if emit == "full_then_updates" {
        let (frames, _, _, _) = lease.entry.current().await;
        last_sequence = last_sequence.max(
            frames
                .iter()
                .filter(|frame| frame.as_of <= as_of)
                .map(|frame| frame.sequence)
                .max()
                .unwrap_or(0),
        );
    }
    loop {
        let (frames, complete, error, _) = lease.entry.current().await;
        if let Some(error) = error {
            send_stream_error(&mut socket, error).await;
            return;
        }
        for frame in &frames {
            if frame.sequence <= last_sequence {
                continue;
            }
            if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                if send_json(&mut socket, frame).await.is_err() {
                    return;
                }
                updates_sent += 1;
            }
            last_sequence = frame.sequence;
            if max_updates.is_some_and(|limit| updates_sent >= limit) {
                let _ = socket.close().await;
                return;
            }
            throttle(updates_per_second).await;
        }
        if complete {
            let _ = socket.close().await;
            return;
        }
        match receiver.recv().await {
            Ok(frame) if frame.sequence > last_sequence => {
                if frame.bar.timeframe.eq_ignore_ascii_case(&timeframe) {
                    if send_json(&mut socket, &frame).await.is_err() {
                        return;
                    }
                    updates_sent += 1;
                }
                last_sequence = frame.sequence;
                if max_updates.is_some_and(|limit| updates_sent >= limit) {
                    let _ = socket.close().await;
                    return;
                }
                throttle(updates_per_second).await;
            }
            Ok(_) => {}
            Err(tokio::sync::broadcast::error::RecvError::Lagged(count)) => {
                send_stream_gap(&mut socket, "historical_derived_stream_lagged", count).await;
                return;
            }
            Err(tokio::sync::broadcast::error::RecvError::Closed) => return,
        }
    }
}

#[derive(Serialize)]
struct FullDerivedEnvelope {
    as_of: DateTime<Utc>,
    bars: Vec<qmd_core::bars::BarRow>,
    cache: CacheEvidence,
    indicators: Vec<qmd_core::indicators::IndicatorRow>,
    next_sequence: u64,
    ticker: String,
    timeframe: String,
    #[serde(rename = "type")]
    update_type: &'static str,
}

#[derive(Serialize)]
struct DerivedFramesMetadata {
    as_of: DateTime<Utc>,
    cache: CacheEvidence,
    frame_count: usize,
    indicator_columns: Vec<String>,
    ticker: String,
    timeframe: String,
    #[serde(rename = "type")]
    update_type: &'static str,
}

fn project_derived_update(
    frame: &DerivedUpdate,
    columns: &BTreeSet<String>,
) -> Result<Value, String> {
    let row = &frame.indicator;
    let mut indicator = serde_json::Map::new();
    macro_rules! include {
        ($key:literal, $value:expr) => {
            if columns.contains($key) {
                indicator.insert($key.to_string(), json!($value));
            }
        };
    }
    include!("atr_14", row.atr_14);
    include!("bar_end", row.bar_end);
    include!("bar_start", row.bar_start);
    include!("close", row.close);
    include!(
        "flow_structure_composite_bias",
        &row.flow_structure_composite_bias
    );
    include!(
        "flow_structure_composite_confidence",
        row.flow_structure_composite_confidence
    );
    include!(
        "flow_structure_composite_score",
        row.flow_structure_composite_score
    );
    include!("macd_histogram", row.macd_histogram);
    include!("macd_line", row.macd_line);
    include!("macd_signal", row.macd_signal);
    include!(
        "price_change_1_bar_pct",
        row.bar_fields.price_change_1_bar_pct
    );
    include!("structure_bos_direction", row.structure_bos_direction);
    include!("structure_choch_direction", row.structure_choch_direction);
    include!("structure_luld_upper", row.structure_luld_upper);
    include!("structure_swing_high", row.structure_swing_high);
    include!("structure_swing_low", row.structure_swing_low);
    include!("sym", &row.sym);
    include!("timeframe", &row.timeframe);
    include!("vwap", row.vwap);
    include!("execution_vwap", row.execution_vwap);
    include!(
        "price_vs_execution_vwap_pct",
        row.price_vs_execution_vwap_pct
    );
    Ok(json!({
        "as_of": frame.as_of,
        "bar": {
            "bar_end": frame.bar.bar_end,
            "close": frame.bar.close,
            "dollar_volume": frame.bar.dollar_volume,
            "high": frame.bar.high,
            "low": frame.bar.low,
            "open": frame.bar.open,
            "spread_bps_close": frame.bar.spread_bps_close,
            "spread_bps_mean": frame.bar.spread_bps_mean,
            "sym": frame.bar.sym,
            "timeframe": frame.bar.timeframe,
            "trade_count": frame.bar.trade_count,
            "volume": frame.bar.volume,
        },
        "indicator": Value::Object(indicator),
        "sequence": frame.sequence,
        "type": frame.update_type,
    }))
}

#[derive(Serialize)]
struct DerivedFramesBatch<'a> {
    frames: &'a [&'a DerivedUpdate],
    #[serde(rename = "type")]
    update_type: &'static str,
}

async fn throttle(updates_per_second: f64) {
    if updates_per_second > 0.0 {
        tokio::time::sleep(std::time::Duration::from_secs_f64(1.0 / updates_per_second)).await;
    }
}

fn window(start: &str, end: &str, tickers: Vec<String>) -> Result<EventWindow, ApiError> {
    let start = parse_timestamp(start)?;
    let end = parse_timestamp(end)?;
    if end <= start {
        return Err(bad_request("end must be later than start"));
    }
    Ok(EventWindow {
        end,
        start,
        tickers,
    })
}

fn stream_window(query: &StreamQuery) -> Result<EventWindow, ApiError> {
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .filter(|value| !value.trim().is_empty())
        .map(str::to_string)
        .collect();
    window(&query.start, &query.end, tickers)
}

fn parse_timestamp(value: &str) -> Result<DateTime<Utc>, ApiError> {
    DateTime::parse_from_rfc3339(value)
        .map(|value| value.with_timezone(&Utc))
        .map_err(|_| bad_request(format!("timestamp must be RFC3339 with timezone: {value}")))
}

fn normalize_ticker(value: &str) -> Result<String, ApiError> {
    let ticker = value.trim().to_ascii_uppercase();
    if ticker.is_empty()
        || ticker.len() > 32
        || !ticker
            .chars()
            .all(|ch| ch.is_ascii_alphanumeric() || matches!(ch, '.' | '-' | '/'))
    {
        return Err(bad_request("ticker is invalid"));
    }
    Ok(ticker)
}

fn validate_timeframe(value: &str) -> Result<(), ApiError> {
    if is_supported_timeframe(value) {
        Ok(())
    } else {
        Err(bad_request(format!(
            "unsupported timeframe {value}; expected one of 1s, 10s, 30s, 1m, 5m, 1h"
        )))
    }
}

async fn send_json(socket: &mut WebSocket, value: &impl Serialize) -> Result<(), ()> {
    let text = serde_json::to_string(value).map_err(|_| ())?;
    socket
        .send(Message::Text(text.into()))
        .await
        .map_err(|_| ())
}

async fn send_stream_error(socket: &mut WebSocket, message: String) {
    let payload = json!({
        "error": message,
        "source": "historical_clickhouse",
        "terminal": true,
    });
    let _ = send_json(socket, &payload).await;
    let _ = socket.close().await;
}

fn stream_gap_frame(warning: &str, skipped: u64) -> Value {
    json!({
        "schema_version": 1,
        "type": "stream_gap",
        "warning": warning,
        "skipped": skipped,
        "action": "resnapshot_required",
        "retry_action": "reconnect_with_original_window",
        "terminal": true,
    })
}

async fn send_stream_gap(socket: &mut WebSocket, warning: &str, skipped: u64) {
    let _ = send_json(socket, &stream_gap_frame(warning, skipped)).await;
    let _ = socket.close().await;
}

fn bad_request(message: impl Into<String>) -> ApiError {
    (
        StatusCode::BAD_REQUEST,
        Json(json!({"error": message.into()})),
    )
}

fn service_error(message: String) -> ApiError {
    (
        StatusCode::BAD_GATEWAY,
        Json(json!({"error": message, "source": "historical_clickhouse"})),
    )
}

fn structure_checkpoint_advancement_error(message: String) -> ApiError {
    let normalized = message.to_ascii_lowercase();
    let (status, error_code, retryable, retry_action) = if normalized.contains("archive")
        || normalized.contains("gap")
        || normalized.contains("source plan changed")
        || normalized.contains("predates the required execution-clock-v1")
    {
        (
            StatusCode::CONFLICT,
            "structure_checkpoint_source_incompatible",
            false,
            "rebuild_checkpoint_from_canonical_history",
        )
    } else if normalized.contains("event limit") || normalized.contains("window exceeds") {
        (
            StatusCode::PAYLOAD_TOO_LARGE,
            "structure_checkpoint_resource_limit",
            false,
            "advance_checkpoint_more_frequently",
        )
    } else if normalized.contains("invalid")
        || normalized.contains("must")
        || normalized.contains("does not match")
    {
        (
            StatusCode::BAD_REQUEST,
            "invalid_structure_checkpoint_request",
            false,
            "correct_request",
        )
    } else {
        (
            StatusCode::BAD_GATEWAY,
            "structure_checkpoint_source_unavailable",
            true,
            "retry_checkpoint_advancement",
        )
    };
    (
        status,
        Json(json!({
            "error": message,
            "error_code": error_code,
            "retryable": retryable,
            "retry_action": retry_action,
            "source": "qmd_history_gateway",
        })),
    )
}

fn watchlist_materialization_error(message: String) -> ApiError {
    let normalized = message.to_ascii_lowercase();
    let (status, error_code, retryable, retry_action, source) = if [
        "source revision changed",
        "complete pinned market-event window",
        "source authority changed",
    ]
    .iter()
    .any(|needle| normalized.contains(needle))
    {
        (
            StatusCode::CONFLICT,
            "watchlist_source_revision_conflict",
            true,
            "restart_materialization",
            "qmd_source_revision",
        )
    } else if normalized.contains("event_limit=")
        || (normalized.contains("exceeds") && normalized.contains("limit="))
    {
        (
            StatusCode::PAYLOAD_TOO_LARGE,
            "watchlist_resource_limit",
            false,
            "reduce_request",
            "qmd_history_gateway",
        )
    } else if [
        "clickhouse http",
        "daily references unavailable",
        "error sending request for url",
        "response body stream failed",
        "connection refused",
        "invalid historical stream",
        "historical source",
        "live gateway",
        "timed out",
    ]
    .iter()
    .any(|needle| normalized.contains(needle))
    {
        (
            StatusCode::BAD_GATEWAY,
            "watchlist_source_unavailable",
            true,
            "retry_materialization",
            "historical_clickhouse",
        )
    } else if [
        "encoding failed",
        "serialization failed",
        "worker panicked",
        "worker stopped early",
        "reducer is unavailable",
    ]
    .iter()
    .any(|needle| normalized.contains(needle))
    {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            "watchlist_internal_failure",
            false,
            "inspect_service",
            "qmd_history_gateway",
        )
    } else {
        (
            StatusCode::BAD_REQUEST,
            "invalid_watchlist_materialization",
            false,
            "correct_request",
            "request",
        )
    };
    (
        status,
        Json(json!({
            "error": message,
            "error_code": error_code,
            "retryable": retryable,
            "retry_action": retry_action,
            "source": source,
        })),
    )
}

fn source_revision_conflict(
    expected_plan_hash: &str,
    expected_revision_token: &str,
    policy: EventRevisionPolicy,
    actual: &SourceRevision,
) -> ApiError {
    (
        StatusCode::CONFLICT,
        Json(json!({
            "error": "historical source authority changed during paged read; restart from the first page",
            "error_code": "source_revision_conflict",
            "revision_policy": policy.as_str(),
            "expected": {
                "source_plan_hash": expected_plan_hash,
                "revision_token": expected_revision_token,
            },
            "actual": {
                "source_plan_hash": actual.source_plan_hash,
                "revision_token": actual.token,
            },
            "retry_action": "restart_snapshot",
        })),
    )
}

fn expected_event_revision<'a>(
    policy: EventRevisionPolicy,
    expected_plan_hash: Option<&'a str>,
    expected_revision_token: Option<&'a str>,
) -> Result<Option<(&'a str, Option<&'a str>)>, ApiError> {
    match (policy, expected_plan_hash, expected_revision_token) {
        (_, None, None) => Ok(None),
        (EventRevisionPolicy::Pinned, Some(plan_hash), Some(token)) => {
            Ok(Some((plan_hash, Some(token))))
        }
        (EventRevisionPolicy::Advancing, Some(plan_hash), token) => {
            Ok(Some((plan_hash, token)))
        }
        (EventRevisionPolicy::Pinned, _, _) => Err(bad_request(
            "pinned reads require expected_source_plan_hash and expected_revision_token together",
        )),
        (EventRevisionPolicy::Advancing, None, Some(_)) => Err(bad_request(
            "advancing reads cannot supply expected_revision_token without expected_source_plan_hash",
        )),
    }
}

fn event_revision_changed(
    policy: EventRevisionPolicy,
    expected_plan_hash: &str,
    expected_revision_token: Option<&str>,
    actual: &SourceRevision,
) -> bool {
    actual.source_plan_hash != expected_plan_hash
        || matches!(policy, EventRevisionPolicy::Pinned)
            && expected_revision_token.is_some_and(|token| actual.token != token)
}

#[cfg(test)]
mod tests {
    use super::{
        causal_product_window, compact_event_type_filter,
        compact_projected_unified_structure_history, event_revision_changed,
        expected_event_revision, is_loopback_bind, parse_chart_mode, parse_chart_stage,
        parse_indicator_projection, parse_timestamp, product_resolution, stream_gap_frame,
        structure_checkpoint_advancement_error, validate_timeframe,
        watchlist_materialization_error, EventRevisionPolicy, ProductQuery,
    };
    use crate::source::SourceRevision;
    use axum::http::StatusCode;
    use serde_json::json;

    #[test]
    fn timestamps_require_explicit_timezone() {
        assert!(parse_timestamp("2026-07-13T04:00:00-04:00").is_ok());
        assert!(parse_timestamp("2026-07-13 04:00:00").is_err());
    }

    #[test]
    fn compact_event_kind_maps_quote_and_trade_to_wire_tokens() {
        assert_eq!(
            compact_event_type_filter(&["quote".to_string()].into_iter().collect()),
            Some(0)
        );
        assert_eq!(
            compact_event_type_filter(&["trade".to_string()].into_iter().collect()),
            Some(1)
        );
        assert_eq!(
            compact_event_type_filter(
                &["quote".to_string(), "trade".to_string()]
                    .into_iter()
                    .collect()
            ),
            None
        );
    }

    #[test]
    fn timeframes_are_validated_by_the_shared_qmd_bar_contract() {
        assert!(validate_timeframe("100ms").is_ok());
        assert!(validate_timeframe("5s").is_ok());
        assert!(validate_timeframe("1m").is_ok());
        assert!(validate_timeframe("2m").is_ok());
        assert!(validate_timeframe("0m").is_err());
    }

    #[test]
    fn historical_lag_is_terminal_and_requires_original_window_resnapshot() {
        let frame = stream_gap_frame("historical_derived_stream_lagged", 9);
        assert_eq!(frame["type"], "stream_gap");
        assert_eq!(frame["action"], "resnapshot_required");
        assert_eq!(frame["retry_action"], "reconnect_with_original_window");
        assert_eq!(frame["terminal"], true);
        assert_eq!(frame["skipped"], 9);
    }

    #[test]
    fn legacy_structure_checkpoint_is_a_non_retryable_source_conflict() {
        let error = structure_checkpoint_advancement_error(
            "persisted structure checkpoint for SUGP predates the required execution-clock-v1 source contract"
                .to_string(),
        );
        assert_eq!(error.0, StatusCode::CONFLICT);
        assert_eq!(
            error.1 .0["error_code"],
            "structure_checkpoint_source_incompatible"
        );
        assert_eq!(error.1 .0["retryable"], false);
        assert_eq!(
            error.1 .0["retry_action"],
            "rebuild_checkpoint_from_canonical_history"
        );
    }

    #[test]
    fn watchlist_failures_preserve_retry_and_restart_semantics() {
        let conflict = watchlist_materialization_error(
            "QMD History source revision changed while replaying".to_string(),
        );
        assert_eq!(conflict.0, StatusCode::CONFLICT);
        assert_eq!(
            conflict.1 .0["error_code"],
            "watchlist_source_revision_conflict"
        );
        assert_eq!(conflict.1 .0["retryable"], true);
        assert_eq!(conflict.1 .0["retry_action"], "restart_materialization");

        let upstream = watchlist_materialization_error(
            "historical Watchlist daily references unavailable: ClickHouse HTTP 503".to_string(),
        );
        assert_eq!(upstream.0, StatusCode::BAD_GATEWAY);
        assert_eq!(upstream.1 .0["error_code"], "watchlist_source_unavailable");
        assert_eq!(upstream.1 .0["retryable"], true);

        let body_stream = watchlist_materialization_error(
            "ClickHouse response body stream failed: error decoding response body".to_string(),
        );
        assert_eq!(body_stream.0, StatusCode::BAD_GATEWAY);
        assert_eq!(
            body_stream.1 .0["error_code"],
            "watchlist_source_unavailable"
        );
        assert_eq!(body_stream.1 .0["retryable"], true);

        let bounded = watchlist_materialization_error(
            "historical Watchlist replay exceeded event_limit=100".to_string(),
        );
        assert_eq!(bounded.0, StatusCode::PAYLOAD_TOO_LARGE);
        assert_eq!(bounded.1 .0["error_code"], "watchlist_resource_limit");
        assert_eq!(bounded.1 .0["retryable"], false);

        let invalid = watchlist_materialization_error(
            "historical Watchlist batch watchlist_id values must be unique".to_string(),
        );
        assert_eq!(invalid.0, StatusCode::BAD_REQUEST);
        assert_eq!(
            invalid.1 .0["error_code"],
            "invalid_watchlist_materialization"
        );
    }

    fn revision(plan: &str, token: &str) -> SourceRevision {
        SourceRevision {
            complete_for_history: false,
            event_count: 1,
            live_continuation_sequence: Some(7),
            max_build_step: 7,
            max_updated_at: "2026-08-11T14:00:00Z".to_string(),
            request_complete: true,
            source_plan_hash: plan.to_string(),
            source_tiers: vec!["currentlive".to_string()],
            token: token.to_string(),
        }
    }

    #[test]
    fn pinned_event_pages_reject_token_drift() {
        let actual = revision("plan-a", "token-2");
        assert!(event_revision_changed(
            EventRevisionPolicy::Pinned,
            "plan-a",
            Some("token-1"),
            &actual,
        ));
        assert!(
            expected_event_revision(EventRevisionPolicy::Pinned, Some("plan-a"), None,).is_err()
        );
    }

    #[test]
    fn advancing_event_pages_accept_tail_progress_but_not_plan_drift() {
        let actual = revision("plan-a", "token-2");
        assert!(!event_revision_changed(
            EventRevisionPolicy::Advancing,
            "plan-a",
            Some("token-1"),
            &actual,
        ));
        assert!(event_revision_changed(
            EventRevisionPolicy::Advancing,
            "plan-b",
            None,
            &actual,
        ));
    }

    #[test]
    fn product_windows_never_build_past_as_of() {
        let query = ProductQuery {
            as_of: Some("2026-07-10T13:44:15Z".to_string()),
            end: "2026-07-10T13:44:30Z".to_string(),
            limit: None,
            resolution: Some("1s".to_string()),
            start: "2026-07-10T13:44:00Z".to_string(),
            timeframe: None,
        };
        let (window, as_of) = causal_product_window(&query, "AAPL").unwrap();
        assert_eq!(window.end, as_of);
        assert_eq!(product_resolution(&query).unwrap(), 1_000_000);
    }

    #[test]
    fn invalid_product_resolution_is_rejected() {
        let query = ProductQuery {
            as_of: None,
            end: "2026-07-10T13:44:30Z".to_string(),
            limit: None,
            resolution: Some("nonsense".to_string()),
            start: "2026-07-10T13:44:00Z".to_string(),
            timeframe: None,
        };
        assert!(product_resolution(&query).is_err());
    }

    #[test]
    fn chart_indicator_projection_is_bounded_and_keeps_the_time_key() {
        let columns = parse_indicator_projection(Some("ema_20,rsi_14,ema_20"))
            .unwrap()
            .unwrap();
        assert_eq!(columns.len(), 3);
        assert!(columns.contains("bar_start"));
        assert!(columns.contains("ema_20"));
        assert!(parse_indicator_projection(Some("ema-20")).is_err());
    }

    #[test]
    fn unified_structure_projection_keeps_only_material_transitions_and_terminal_state() {
        let level = |hold: f64, volume: f64| {
            json!({
                "unified_level_id": 17,
                "side": 1,
                "lower": 9.98,
                "upper": 10.02,
                "price": 10.0,
                "hold_probability": hold,
                "touch_count": 3,
                "hold_count": 2,
                "break_count": 0,
                "role_flip_count": 1,
                "total_volume": volume,
                "trade_count": volume as u64,
                "last_test_at_ms": volume as i64,
                "sources": [{"total_volume": volume}],
            })
        };
        let mut indicators = vec![
            json!({"bar_start": "a", "qmd_structure_unified_levels": [level(0.70, 10.0)]}),
            json!({"bar_start": "b", "qmd_structure_unified_levels": [level(0.70, 20.0)]}),
            json!({"bar_start": "c", "qmd_structure_unified_levels": [level(0.75, 30.0)]}),
            json!({"bar_start": "d", "qmd_structure_unified_levels": [level(0.75, 40.0)]}),
        ];

        compact_projected_unified_structure_history(&mut indicators);

        assert!(indicators[0].get("qmd_structure_unified_levels").is_some());
        assert!(indicators[1].get("qmd_structure_unified_levels").is_none());
        assert!(indicators[1]
            .get("qmd_structure_unified_level_delta")
            .is_none());
        assert!(indicators[2].get("qmd_structure_unified_levels").is_none());
        assert_eq!(
            indicators[2]["qmd_structure_unified_level_delta"]["upserts"]
                .as_array()
                .map(Vec::len),
            Some(1)
        );
        assert!(indicators[3].get("qmd_structure_unified_levels").is_some());
        assert!(indicators.iter().all(|row| {
            !(row.get("qmd_structure_unified_levels").is_some()
                && row.get("qmd_structure_unified_level_delta").is_some())
        }));

        let mut contaminated = vec![
            json!({
                "bar_start": "a",
                "qmd_structure_unified_levels": [level(0.70, 10.0)],
                "qmd_structure_unified_level_delta": {
                    "upserts": [{"unified_level_id": 999, "side": -1}],
                    "removed": [],
                },
            }),
            json!({
                "bar_start": "b",
                "qmd_structure_unified_levels": [level(0.75, 20.0)],
                "qmd_structure_unified_level_delta": {
                    "upserts": [{"unified_level_id": 998, "side": -1}],
                    "removed": [],
                },
            }),
        ];
        compact_projected_unified_structure_history(&mut contaminated);
        assert!(contaminated.iter().all(|row| {
            !(row.get("qmd_structure_unified_levels").is_some()
                && row.get("qmd_structure_unified_level_delta").is_some())
        }));
        assert!(contaminated[0]
            .get("qmd_structure_unified_level_delta")
            .is_none());
        assert!(contaminated[1]
            .get("qmd_structure_unified_level_delta")
            .is_none());

        let mut closes = vec![
            json!({"bar_start": "a", "qmd_structure_unified_levels": [level(0.70, 10.0)]}),
            json!({"bar_start": "b", "qmd_structure_unified_levels": []}),
            json!({"bar_start": "c", "qmd_structure_unified_levels": []}),
        ];
        compact_projected_unified_structure_history(&mut closes);
        assert_eq!(
            closes[1]["qmd_structure_unified_level_delta"]["removed"]
                .as_array()
                .map(Vec::len),
            Some(1)
        );
        assert_eq!(closes[2]["qmd_structure_unified_levels"], json!([]));
    }

    #[test]
    fn chart_stage_defaults_to_full_and_rejects_unknown_values() {
        assert!(!parse_chart_stage(None).unwrap());
        assert!(parse_chart_stage(Some("bars")).unwrap());
        assert!(!parse_chart_stage(Some("full")).unwrap());
        assert!(parse_chart_stage(Some("indicators")).is_err());
    }

    #[test]
    fn chart_mode_defaults_to_live_and_rejects_unknown_values() {
        assert_eq!(parse_chart_mode(None).unwrap(), "live");
        for mode in ["live", "replay", "backtest", "debug"] {
            assert_eq!(parse_chart_mode(Some(mode)).unwrap(), mode);
        }
        assert!(parse_chart_mode(Some("historical")).is_err());
    }

    #[test]
    fn checkpoint_rebuild_is_restricted_to_loopback_bindings() {
        assert!(is_loopback_bind("127.0.0.1:8801"));
        assert!(is_loopback_bind("[::1]:8801"));
        assert!(!is_loopback_bind("0.0.0.0:8801"));
        assert!(!is_loopback_bind("192.168.1.4:8801"));
        assert!(!is_loopback_bind("localhost:8801"));
    }
}
