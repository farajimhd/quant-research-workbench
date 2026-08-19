use crate::bars::TradeAggregationRules;
use crate::bars::{BarSnapshot, SharedBarStore};
use crate::capability_catalog::{computation_capability_catalog, ComputationCapability};
use crate::compact_event::{
    CompactEventDecoder, CompactEventMarketPage, CompactEventPage, LiveCompactEvent,
    SharedCompactEventStore,
};
use crate::computation_targets::{
    ComputationTargetLease, ComputationTargetRequest, ComputationTargetSnapshot,
    ComputationTargetSummary, SharedComputationTargets,
};
use crate::config::GatewayConfig;
use crate::definition_catalog::{definition_catalog, QmdDefinitionCatalog};
use crate::event::MarketEvent;
use crate::indicator_catalog::{indicator_taxonomy_catalog, IndicatorTaxonomyEntry};
use crate::indicators::{IndicatorScannerSnapshot, IndicatorSnapshot, SharedIndicatorStore};
use crate::intraday_bars::IntradayBarRow;
use crate::live_market_state::{
    LiveMarketStateSnapshot, LiveSymbolMarketStateEvent, SharedLiveMarketStateStore,
    TickerLiveMarketStateSnapshot,
};
use crate::maintenance::{MaintenanceSnapshot, SharedMaintenanceState};
use crate::market_calendar::{MarketCalendarClient, MarketSnapshot};
use crate::market_products::{
    parse_resolution_us, ConditionBarSnapshot, FamilyBarSnapshot, MacroBarSnapshot,
    ProductCacheMetrics, SharedMarketProductStore,
};
use crate::metrics::{MetricsSnapshot, OperationalSnapshot, SharedMetrics};
use crate::scanner::{
    MarketSignalDelta, MarketSignalSnapshot, ScannerPrimitiveSnapshot, SharedScannerStore,
};
use crate::session::session_phase;
use crate::signal_catalog::{signal_taxonomy_catalog, SignalTaxonomyEntry};
use crate::state::{
    ScannerRowDelta, SharedMarketState, StatusMetrics, SymbolSnapshot, TickerStateSnapshot,
};
use crate::structure_focus::StructureFocusCoordinator;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::middleware;
use axum::response::IntoResponse;
use axum::routing::{delete, get, post};
use axum::{Json, Router};
use chrono::{Datelike, NaiveDate, TimeZone, Timelike, Utc};
use chrono_tz::America::New_York;
use futures_util::SinkExt;
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::sync::Arc;
use tokio::sync::{broadcast, watch};
use tokio::time::{interval, Duration};
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub bars: SharedBarStore,
    pub compact_event_store: SharedCompactEventStore,
    pub compact_event_decoder: CompactEventDecoder,
    pub compact_events: broadcast::Sender<LiveCompactEvent>,
    pub computation_targets: SharedComputationTargets,
    pub config: GatewayConfig,
    pub events: broadcast::Sender<MarketEvent>,
    pub indicators: SharedIndicatorStore,
    pub live_market_state: SharedLiveMarketStateStore,
    pub live_market_state_events: broadcast::Sender<LiveSymbolMarketStateEvent>,
    pub market: SharedMarketState,
    pub maintenance: SharedMaintenanceState,
    pub market_calendar: MarketCalendarClient,
    pub products: SharedMarketProductStore,
    pub metrics: SharedMetrics,
    pub intraday_bars: broadcast::Sender<IntradayBarRow>,
    pub scanner: SharedScannerStore,
    pub scanner_deltas: broadcast::Sender<ScannerRowDelta>,
    pub scanner_events: broadcast::Sender<MarketSignalDelta>,
    pub structure_focus: StructureFocusCoordinator,
    pub shutdown: watch::Sender<bool>,
    pub trade_aggregation_rules: TradeAggregationRules,
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct CompactEventPageQuery {
    after_arrival_sequence: Option<u64>,
    limit: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct CompactEventMarketPageQuery {
    after_arrival_sequence: Option<u64>,
    end_sip_timestamp_us: u64,
    limit: Option<usize>,
    start_sip_timestamp_us: u64,
    tickers: Option<String>,
    through_arrival_sequence: Option<u64>,
}

#[derive(Debug, Deserialize)]
struct BarsQuery {
    limit: Option<usize>,
    timeframe: Option<String>,
    fields: Option<String>,
}

#[derive(Debug, Deserialize)]
struct IntradayBarHistoryQuery {
    before_event_timestamp_us: Option<u64>,
    end_date: Option<String>,
    limit: Option<usize>,
    start_date: Option<String>,
    timeframe: Option<String>,
}

#[derive(Debug, Deserialize)]
struct ProductQuery {
    emit: Option<String>,
    family: Option<String>,
    limit: Option<usize>,
    price_only: Option<bool>,
    resolution: Option<String>,
    timeframe: Option<String>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    config: GatewayConfig,
    metrics: StatusMetrics,
    market_calendar: MarketSnapshot,
    running: bool,
    session_phase: String,
    status: String,
    subscriptions: Vec<String>,
    host_role: String,
    operational: OperationalSnapshot,
}

#[derive(Debug, Serialize)]
struct StandardStatusPayload {
    attention: Vec<Value>,
    live_pipeline: Vec<Value>,
    downstream_products: Vec<Value>,
    header: Value,
    current_operation: Value,
    configuration: Value,
    runtime: MetricsSnapshot,
    tasks: Vec<Value>,
    coverage: Value,
    queues: Value,
    error_state: Value,
    service_specific: Value,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/metrics", get(metrics_snapshot))
        .route("/admin/shutdown", post(request_shutdown))
        .route("/snapshot/status", get(status_snapshot))
        .route("/snapshot/maintenance", get(maintenance_snapshot))
        .route("/snapshot/coverage", get(coverage_snapshot))
        .route("/capability-catalog", get(capability_catalog_snapshot))
        .route("/definition-catalog", get(definition_catalog_snapshot))
        .route(
            "/computation-targets",
            get(computation_target_snapshot).put(replace_computation_target),
        )
        .route(
            "/computation-targets/summary",
            get(computation_target_summary),
        )
        .route(
            "/computation-targets/{target_id}",
            delete(remove_computation_target),
        )
        .route("/indicator-catalog", get(indicator_catalog_snapshot))
        .route("/signal-catalog", get(signal_catalog_snapshot))
        .route("/snapshot/signals", get(market_signal_snapshot))
        .route("/snapshot/signal-events", get(market_signal_event_snapshot))
        .route("/snapshot/scanner", get(scanner_snapshot))
        .route(
            "/snapshot/scanner-indicators",
            get(scanner_indicator_snapshot),
        )
        .route(
            "/snapshot/scanner-primitives",
            get(scanner_primitive_snapshot),
        )
        .route("/snapshot/ticker/{ticker}", get(ticker_snapshot))
        .route(
            "/snapshot/ticker-state/{ticker}",
            get(ticker_state_snapshot),
        )
        .route("/snapshot/bars/{ticker}", get(bar_snapshot))
        .route(
            "/snapshot/intraday-bar-history/{ticker}",
            get(intraday_bar_history_snapshot),
        )
        .route("/snapshot/product-cache", get(product_cache_snapshot))
        .route("/snapshot/family-bars/{ticker}", get(family_bar_snapshot))
        .route(
            "/snapshot/condition-bars/{ticker}",
            get(condition_bar_snapshot),
        )
        .route("/snapshot/macro-bars/{ticker}", get(macro_bar_snapshot))
        .route(
            "/snapshot/scanner-macro-bars",
            get(macro_bar_scanner_snapshot),
        )
        .route(
            "/snapshot/compact-events/{ticker}",
            get(compact_event_snapshot),
        )
        .route(
            "/snapshot/compact-event-page/{ticker}",
            get(compact_event_page_snapshot),
        )
        .route(
            "/snapshot/compact-event-market-page",
            get(compact_event_market_page_snapshot),
        )
        .route("/snapshot/indicators/{ticker}", get(indicator_snapshot))
        .route(
            "/snapshot/live-market-state",
            get(live_market_state_snapshot),
        )
        .route(
            "/snapshot/live-market-state/{ticker}",
            get(ticker_live_market_state_snapshot),
        )
        .route("/stream/compact-events", get(compact_event_stream))
        .route("/stream/intraday-bars", get(intraday_bar_stream))
        .route("/stream/events", get(event_stream))
        .route("/stream/live-market-state", get(live_market_state_stream))
        .route("/stream/scanner", get(scanner_stream))
        .route("/stream/signals", get(market_signal_stream))
        // Deprecated compatibility alias. The wire payload is the canonical
        // MarketSignalEvent contract, not the former unversioned primitive.
        .route("/stream/scanner-primitives", get(market_signal_stream))
        .route("/stream/ticker/{ticker}", get(ticker_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .route("/stream/family-bars/{ticker}", get(family_bar_stream))
        .route("/stream/condition-bars/{ticker}", get(condition_bar_stream))
        .route("/stream/macro-bars/{ticker}", get(macro_bar_stream))
        .route("/stream/indicators/{ticker}", get(indicator_stream))
        .layer(CorsLayer::permissive())
        .layer(middleware::from_fn(
            crate::request_identity::preserve_request_identity,
        ))
        .with_state(Arc::new(state))
}

async fn computation_target_snapshot(
    State(state): State<Arc<AppState>>,
) -> Json<ComputationTargetSnapshot> {
    Json(state.computation_targets.snapshot())
}

async fn computation_target_summary(
    State(state): State<Arc<AppState>>,
) -> Json<ComputationTargetSummary> {
    Json(state.computation_targets.summary())
}

async fn replace_computation_target(
    State(state): State<Arc<AppState>>,
    Json(request): Json<ComputationTargetRequest>,
) -> Result<Json<ComputationTargetLease>, (StatusCode, Json<Value>)> {
    let prepared = state
        .computation_targets
        .prepare(request)
        .map_err(|error| (StatusCode::BAD_REQUEST, Json(json!({ "error": error }))))?;
    if state.computation_targets.matches_active_contract(&prepared) {
        // A lease renewal changes expiry and lineage timestamps, not its
        // computation demand. Reactivate the normalized lease without
        // restaging structure state or replay-warming every ticker/timeframe.
        return Ok(Json(state.computation_targets.activate(prepared)));
    }
    // Focus activation can include a bounded QMD History advancement. Run the
    // mutation in its own task so a caller timeout or browser navigation does
    // not cancel it midway and strand a ticker in transient staging state.
    let activation_state = state.clone();
    let lease = tokio::spawn(async move {
        activate_prepared_computation_target(activation_state, prepared).await
    })
    .await
    .map_err(|error| {
        (
            StatusCode::INTERNAL_SERVER_ERROR,
            Json(json!({
                "error": format!("computation target activation task failed: {error}"),
                "error_code": "computation_target_activation_task_failed",
                "retryable": true,
                "retry_action": "retry_computation_target",
            })),
        )
    })?
    .map_err(|error| {
        (
            StatusCode::SERVICE_UNAVAILABLE,
            Json(json!({
                "error": error,
                "error_code": "structure_focus_activation_failed",
                "retryable": true,
                "retry_action": "retry_computation_target",
            })),
        )
    })?;
    Ok(Json(lease))
}

async fn activate_prepared_computation_target(
    state: Arc<AppState>,
    prepared: ComputationTargetLease,
) -> Result<ComputationTargetLease, String> {
    state.structure_focus.stage_and_activate(&prepared).await?;
    let lease = state.computation_targets.activate(prepared);
    for ticker in &lease.tickers {
        for timeframe in &lease.timeframes {
            if !state.indicators.needs_warm(ticker, timeframe).await {
                continue;
            }
            let bars = state.bars.snapshot(ticker, timeframe, 500).await;
            state
                .indicators
                .warm_from_bars(ticker, timeframe, bars.history)
                .await;
        }
    }
    // Replacing a target can narrow its symbol/timeframe set. Reclaim the old
    // state after warming the current lease; overlapping leases remain intact.
    state
        .indicators
        .reclaim_unused(&state.computation_targets)
        .await;
    if let Err(error) = state
        .structure_focus
        .persist_and_reclaim_unused(&state.computation_targets)
        .await
    {
        eprintln!("QMD structure-state reclaim deferred after target replacement: {error}");
    }
    Ok(lease)
}

async fn remove_computation_target(
    State(state): State<Arc<AppState>>,
    Path(target_id): Path<String>,
) -> Json<Value> {
    let removed = state.computation_targets.remove(&target_id);
    let reclaimed = state
        .indicators
        .reclaim_unused(&state.computation_targets)
        .await;
    let reclaimed_structure_state = match state
        .structure_focus
        .persist_and_reclaim_unused(&state.computation_targets)
        .await
    {
        Ok(symbols) => json!(symbols),
        Err(error) => json!({"deferred": true, "error": error}),
    };
    Json(json!({
        "removed": removed,
        "reclaimed_indicator_state": reclaimed,
        "reclaimed_structure_state": reclaimed_structure_state,
        "target_id": target_id,
    }))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthPayload> {
    let market_calendar = state.market_calendar.snapshot(chrono::Utc::now());
    let maintenance = state.maintenance.snapshot().await;
    let operational = state.metrics.operational_snapshot();
    let status = qmd_status(&market_calendar, &maintenance, &operational);
    Json(HealthPayload {
        config: state.config.clone(),
        metrics: state.market.metrics().await,
        market_calendar,
        running: true,
        session_phase: format!("{:?}", session_phase(chrono::Utc::now())),
        status,
        subscriptions: state.config.subscription_channels(),
        host_role: state.config.resolved_host_role(),
        operational,
    })
}

async fn request_shutdown(State(state): State<Arc<AppState>>, headers: HeaderMap) -> StatusCode {
    let expected = state.config.qmd_shutdown_token.trim();
    let supplied = headers
        .get("x-qmd-shutdown-token")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    if !valid_shutdown_token(expected, supplied) {
        return StatusCode::FORBIDDEN;
    }
    match state.shutdown.send(true) {
        Ok(()) => StatusCode::ACCEPTED,
        Err(_) => StatusCode::SERVICE_UNAVAILABLE,
    }
}

fn valid_shutdown_token(expected: &str, supplied: &str) -> bool {
    !expected.is_empty() && supplied == expected
}

fn scanner_sequence_gap(delivered_sequence: u64, received_sequence: u64) -> Option<u64> {
    if received_sequence > delivered_sequence.saturating_add(1) {
        Some(
            received_sequence
                .saturating_sub(delivered_sequence)
                .saturating_sub(1),
        )
    } else {
        None
    }
}

fn resnapshot_required_frame(
    warning: &str,
    skipped: u64,
    snapshot_endpoint: &str,
    continuation_after_arrival_sequence: Option<u64>,
) -> Value {
    json!({
        "schema_version": 1,
        "type": "stream_gap",
        "warning": warning,
        "skipped": skipped,
        "action": "resnapshot_required",
        "snapshot_endpoint": snapshot_endpoint,
        "continuation_after_arrival_sequence": continuation_after_arrival_sequence,
    })
}

async fn send_resnapshot_required(
    socket: &mut WebSocket,
    warning: &str,
    skipped: u64,
    snapshot_endpoint: &str,
    continuation_after_arrival_sequence: Option<u64>,
) {
    let frame = resnapshot_required_frame(
        warning,
        skipped,
        snapshot_endpoint,
        continuation_after_arrival_sequence,
    );
    let _ = socket.send(Message::Text(frame.to_string().into())).await;
    let _ = socket.close().await;
}

#[cfg(test)]
mod shutdown_tests {
    use super::{
        parse_indicator_projection_fields, resnapshot_required_frame,
        retain_indicator_projection_fields, scanner_sequence_gap, valid_shutdown_token,
    };
    use serde_json::json;

    #[test]
    fn shutdown_requires_the_configured_non_empty_token() {
        assert!(valid_shutdown_token("run-token", "run-token"));
        assert!(!valid_shutdown_token("run-token", "wrong"));
        assert!(!valid_shutdown_token("", ""));
    }

    #[test]
    fn scanner_sequence_gap_requires_resnapshot_only_for_missing_deltas() {
        assert_eq!(scanner_sequence_gap(10, 11), None);
        assert_eq!(scanner_sequence_gap(10, 10), None);
        assert_eq!(scanner_sequence_gap(10, 13), Some(2));
        assert_eq!(scanner_sequence_gap(u64::MAX, u64::MAX), None);
    }

    #[test]
    fn lag_frame_requires_resnapshot_and_preserves_compact_cursor() {
        let frame = resnapshot_required_frame(
            "compact_event_stream_lagged",
            7,
            "/snapshot/compact-event-market-page",
            Some(42),
        );
        assert_eq!(frame["type"], "stream_gap");
        assert_eq!(frame["action"], "resnapshot_required");
        assert_eq!(frame["skipped"], 7);
        assert_eq!(frame["continuation_after_arrival_sequence"], 42);
    }

    #[test]
    fn indicator_projection_retains_identity_and_requested_fields_only() {
        let requested = parse_indicator_projection_fields(Some("close,price_change_pct"))
            .expect("projection fields are valid")
            .expect("projection is requested");
        let mut payload = json!({
            "rows": [{
                "schema_version": "qmd.indicators.v1",
                "session_date": "2026-08-19",
                "timeframe": "5m",
                "sym": "AAPL",
                "bar_start": 1,
                "bar_end": 2,
                "close": 225.5,
                "price_change_pct": 5.1,
                "qmd_structure_score": 0.9
            }]
        });

        retain_indicator_projection_fields(&mut payload, &requested);

        let row = payload["rows"][0]
            .as_object()
            .expect("projected row is an object");
        assert_eq!(row["sym"], "AAPL");
        assert_eq!(row["close"], 225.5);
        assert_eq!(row["price_change_pct"], 5.1);
        assert!(!row.contains_key("qmd_structure_score"));
    }

    #[test]
    fn indicator_projection_rejects_unsafe_or_unbounded_fields() {
        assert!(parse_indicator_projection_fields(Some("close,$secret")).is_err());
        let oversized = (0..65)
            .map(|index| format!("field_{index}"))
            .collect::<Vec<_>>()
            .join(",");
        assert!(parse_indicator_projection_fields(Some(&oversized)).is_err());
    }
}

async fn config(State(state): State<Arc<AppState>>) -> Json<GatewayConfig> {
    Json(state.config.clone())
}

async fn metrics_snapshot(State(state): State<Arc<AppState>>) -> Json<MetricsSnapshot> {
    Json(state.metrics.snapshot())
}

async fn status_snapshot(State(state): State<Arc<AppState>>) -> Json<StandardStatusPayload> {
    let metrics = state.metrics.snapshot();
    let maintenance = state.maintenance.snapshot().await;
    let market_metrics = state.market.metrics().await;
    let market_calendar = state.market_calendar.snapshot(chrono::Utc::now());
    let operational = state.metrics.operational_snapshot();
    let computation_demand = state.computation_targets.snapshot();
    let status = qmd_status(&market_calendar, &maintenance, &operational);
    let queue_drops = metrics.events_broadcast_dropped
        + metrics.bar_events_dropped
        + metrics.indicator_events_dropped
        + metrics.compact_event_queue_dropped
        + metrics.clickhouse_events_dropped;
    Json(StandardStatusPayload {
        attention: build_attention(&operational, &maintenance, queue_drops),
        live_pipeline: build_live_pipeline(&operational, &metrics),
        downstream_products: build_downstream_products(&state.config, &operational, &metrics),
        header: json!({
            "service": "qmd_gateway",
            "status": status.to_ascii_uppercase(),
            "bind": state.config.bind,
            "mode": state.config.gap_fill_mode,
            "execute": true,
            "read_database": state.config.historical_clickhouse_database,
            "write_database": state.config.clickhouse_database,
            "snapshot_utc": chrono::Utc::now().to_rfc3339(),
            "market_status": if market_calendar.active_collection_window { "active" } else { "closed" },
            "market_calendar_source": market_calendar.source,
            "market_calendar_reason": market_calendar.reason,
            "subscriptions": state.config.subscription_channels(),
            "host_role": state.config.resolved_host_role(),
        }),
        current_operation: json!({
            "phase": if maintenance.active { maintenance.phase.clone() } else { "streaming".to_string() },
            "status": if maintenance.active { maintenance.status.clone() } else { "running".to_string() },
            "message": if maintenance.active { maintenance.message.clone() } else { "websocket ingest and writers active".to_string() },
            "started_at": maintenance.started_at_utc,
            "next_action": "",
        }),
        configuration: json!({
            "bind": state.config.bind,
            "clickhouse_database": state.config.clickhouse_database,
            "historical_clickhouse_database": state.config.historical_clickhouse_database,
            "gap_fill_enabled": state.config.gap_fill_enabled,
            "recent_live_prior_market_days": state.config.recent_live_prior_market_days,
            "persist_raw_events": state.config.persist_raw_events,
            "persist_compact_events": state.config.persist_compact_events,
            "persist_indicators": state.config.persist_indicators,
        }),
        runtime: metrics.clone(),
        tasks: vec![
            json!({
                "task": "websocket ingest",
                "status": lane_state(&operational, "massive_feed"),
                "rows": metrics.ingest_events,
                "message": lane_detail(&operational, "massive_feed"),
            }),
            json!({
                "task": "maintenance and gap fill",
                "status": maintenance.status,
                "rows": maintenance.rows_written,
                "message": maintenance.message,
                "done": maintenance.completed_jobs,
                "total": maintenance.total_jobs,
            }),
            json!({
                "task": "bar publication",
                "status": "running",
                "rows": metrics.bar_rows_emitted,
                "message": "Streaming bars are updated from trade and quote events.",
            }),
        ],
        coverage: json!({
            "status": maintenance.status,
            "message": maintenance.message,
            "window_start_utc": maintenance.window_start_utc,
            "window_end_utc": maintenance.window_end_utc,
            "completed_jobs": maintenance.completed_jobs,
            "total_jobs": maintenance.total_jobs,
        }),
        queues: json!({
            "event_broadcast_dropped": metrics.events_broadcast_dropped,
            "bar_events_dropped": metrics.bar_events_dropped,
            "indicator_events_dropped": metrics.indicator_events_dropped,
            "compact_event_queue_dropped": metrics.compact_event_queue_dropped,
            "clickhouse_events_dropped": metrics.clickhouse_events_dropped,
            "queue_drop_total": queue_drops,
        }),
        error_state: json!({
            "status": if queue_drops > 0 || metrics.parse_failures > 0 || metrics.gap_fill_failures > 0 { "degraded" } else { "ok" },
            "active": queue_drops > 0 || metrics.parse_failures > 0 || metrics.gap_fill_failures > 0,
            "severity": if queue_drops > 0 { "warning" } else { "info" },
            "message": if queue_drops > 0 { "One or more downstream queues rejected work; inspect queue counters." } else { "" },
            "retryable": true,
            "last_error": "",
        }),
        service_specific: json!({
            "computation_demand": computation_demand,
            "market": market_metrics,
            "maintenance": maintenance,
            "operational": operational,
            "recent_sessions": state.market_calendar.prior_sessions(
                chrono::Utc::now().with_timezone(&chrono_tz::America::New_York).date_naive(),
                state.config.recent_live_prior_market_days.max(0) as usize + 1,
            ),
            "host_role": state.config.resolved_host_role(),
        }),
    })
}

fn qmd_status(
    market: &MarketSnapshot,
    maintenance: &MaintenanceSnapshot,
    operational: &OperationalSnapshot,
) -> String {
    if operational
        .lanes
        .iter()
        .any(|lane| lane.enabled && lane.required && lane.state == "failed")
    {
        return "degraded".to_string();
    }
    if maintenance.status.contains("manual")
        || maintenance.status.contains("needs_manual")
        || maintenance.status.contains("retention_blocked")
    {
        return "action_required".to_string();
    }
    if maintenance.active {
        return "catching_up".to_string();
    }
    if !market.active_collection_window {
        return "closed".to_string();
    }
    match lane_state(operational, "massive_feed") {
        "healthy" => "running".to_string(),
        "starting" | "connecting" => "starting".to_string(),
        _ => "degraded".to_string(),
    }
}

fn lane<'a>(
    operational: &'a OperationalSnapshot,
    key: &str,
) -> Option<&'a crate::metrics::OperationalLaneSnapshot> {
    operational.lanes.iter().find(|lane| lane.key == key)
}

fn lane_state<'a>(operational: &'a OperationalSnapshot, key: &str) -> &'a str {
    lane(operational, key)
        .map(|value| value.state.as_str())
        .unwrap_or("unknown")
}

fn lane_detail<'a>(operational: &'a OperationalSnapshot, key: &str) -> &'a str {
    lane(operational, key)
        .map(|value| value.detail.as_str())
        .unwrap_or("No operational state reported.")
}

fn build_attention(
    operational: &OperationalSnapshot,
    maintenance: &MaintenanceSnapshot,
    queue_drops: u64,
) -> Vec<Value> {
    let mut items = operational
        .lanes
        .iter()
        .filter(|lane| lane.enabled && lane.state == "failed")
        .map(|lane| {
            json!({
                "severity": if lane.required { "critical" } else { "warning" },
                "area": lane.label,
                "since_utc": lane.last_failure_utc,
                "message": lane.detail,
                "impact": if lane.required { "A required live-data path is impaired." } else { "An optional product is impaired." },
                "action": "Inspect the writer error and ClickHouse/network health; the current batch remains pending for retry.",
            })
        })
        .collect::<Vec<_>>();
    if queue_drops > 0 {
        items.push(json!({
            "severity": "critical",
            "area": "Required queue path",
            "message": format!("{queue_drops} receiver-closed event(s) were recorded."),
            "impact": "One or more required consumers stopped accepting work.",
            "action": "Inspect the failed worker and restart only after its cause is understood.",
        }));
    }
    if maintenance.errors > 0 {
        items.push(json!({
            "severity": "warning",
            "area": "Coverage repair",
            "message": maintenance.message,
            "impact": "One or more recent coverage intervals remain incomplete.",
            "action": "Inspect the active interval and page-limit/error counts.",
        }));
    }
    items
}

fn build_live_pipeline(operational: &OperationalSnapshot, metrics: &MetricsSnapshot) -> Vec<Value> {
    let normalize_state = match lane_state(operational, "massive_feed") {
        "healthy" => "healthy",
        "failed" => "blocked",
        "disabled" => "disabled",
        _ => "waiting",
    };
    vec![
        json!({"key": "massive_feed", "label": "Massive feed", "state": lane_state(operational, "massive_feed"), "detail": lane_detail(operational, "massive_feed"), "rows": metrics.ingest_events, "last_event_utc": metrics.last_event_ts, "lag_ms": metrics.last_event_lag_ms}),
        json!({"key": "normalize", "label": "Normalize / encode", "state": normalize_state, "rows": metrics.compact_events_emitted, "rejected": metrics.compact_event_rejected, "detail": "Uses the compact event reference-table encoding contract; consumers should alert if rejects are actively rising."}),
        json!({"key": "compact_events", "label": "q_live.events", "lane": lane(operational, "compact_events"), "rows": metrics.compact_events_persisted, "reorder_pending": metrics.compact_events_reorder_pending}),
        json!({"key": "intraday_bars", "label": "Canonical intraday bars", "lane": lane(operational, "intraday_bars"), "rows": metrics.intraday_bar_rows_persisted, "emitted": metrics.intraday_bar_rows_emitted}),
    ]
}

fn build_downstream_products(
    config: &GatewayConfig,
    operational: &OperationalSnapshot,
    metrics: &MetricsSnapshot,
) -> Vec<Value> {
    let scanner_state = if metrics.bar_events_dropped > 0 || metrics.bar_rows_scanner_dropped > 0 {
        "degraded"
    } else {
        match lane_state(operational, "massive_feed") {
            "healthy" => "healthy",
            "failed" => "degraded",
            "disabled" => "disabled",
            _ => "waiting",
        }
    };
    vec![
        json!({"product": "Intraday bars", "enabled": true, "state": lane_state(operational, "intraday_bars"), "rows": metrics.intraday_bar_rows_persisted, "detail": lane_detail(operational, "intraday_bars")}),
        json!({"product": "Indicators", "enabled": config.persist_indicators, "state": lane_state(operational, "indicators"), "detail": lane_detail(operational, "indicators")}),
        json!({"product": "Scanner primitives", "enabled": true, "state": scanner_state, "rows": metrics.scanner_candidates_emitted, "detail": "Zero candidates is normal when no primitive threshold is met."}),
        json!({"product": "Abnormal market state", "enabled": config.live_market_state_enabled, "state": lane_state(operational, "live_market_state"), "rows": metrics.live_market_state_events_persisted, "detail": lane_detail(operational, "live_market_state")}),
    ]
}

async fn maintenance_snapshot(State(state): State<Arc<AppState>>) -> Json<MaintenanceSnapshot> {
    Json(state.maintenance.snapshot().await)
}

async fn live_market_state_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<LiveMarketStateSnapshot> {
    Json(
        state
            .live_market_state
            .snapshot(query.limit.unwrap_or(250).min(5_000))
            .await,
    )
}

async fn ticker_live_market_state_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<LimitQuery>,
) -> Json<TickerLiveMarketStateSnapshot> {
    Json(
        state
            .live_market_state
            .ticker_snapshot(&ticker, query.limit.unwrap_or(250).min(5_000))
            .await,
    )
}

async fn coverage_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<Value> {
    let limit = query.limit.unwrap_or(12).clamp(1, 50);
    let mut rows = Vec::new();
    let mut errors = Vec::new();

    let legacy_sql = format!(
        r#"
        SELECT
            started_at,
            finished_at,
            coverage_kind,
            status,
            start_ts_utc,
            end_ts_utc,
            action,
            rows_written,
            host_role,
            command,
            summary_json
        FROM {table}
        ORDER BY started_at DESC
        LIMIT {limit}
        FORMAT JSONEachRow
        "#,
        table = state.config.qmd_coverage_table,
        limit = limit,
    );
    match coverage_query_rows(&state.config, &legacy_sql).await {
        Ok(mut values) => rows.append(&mut values),
        Err(error) => errors.push(format!("legacy: {error}")),
    }

    let live_sql = event_coverage_snapshot_sql(
        &state.config.qmd_live_event_coverage_table,
        "live_coverage",
        limit,
    );
    match coverage_query_rows(&state.config, &live_sql).await {
        Ok(mut values) => rows.append(&mut values),
        Err(error) => errors.push(format!("live: {error}")),
    }

    let flatfile_sql = format!(
        r#"
        SELECT updated_at_utc AS started_at, updated_at_utc AS finished_at,
            'flatfile_events' AS coverage_kind,
            concat(remote_status, '/', historical_status) AS status,
            toDateTime64(session_date, 3, 'UTC') AS start_ts_utc,
            toDateTime64(session_date + 1, 3, 'UTC') AS end_ts_utc,
            source_kind AS action, historical_rows AS rows_written,
            host_role, command,
            toJSONString(map('remote_key', remote_key, 'remote_etag', remote_etag, 'error', error)) AS summary_json,
            'flatfile_coverage' AS table_group
        FROM {} FINAL ORDER BY session_date DESC, source_kind LIMIT {} FORMAT JSONEachRow
    "#,
        state.config.qmd_flatfile_coverage_table, limit
    );
    match coverage_query_rows(&state.config, &flatfile_sql).await {
        Ok(mut values) => rows.append(&mut values),
        Err(error) => errors.push(format!("flatfile: {error}")),
    }

    rows.sort_by(|left, right| {
        let left_key = left
            .get("finished_at")
            .and_then(Value::as_str)
            .or_else(|| left.get("started_at").and_then(Value::as_str))
            .unwrap_or_default();
        let right_key = right
            .get("finished_at")
            .and_then(Value::as_str)
            .or_else(|| right.get("started_at").and_then(Value::as_str))
            .unwrap_or_default();
        right_key.cmp(left_key)
    });
    if errors.is_empty() {
        Json(json!({ "rows": rows, "per_group_limit": limit }))
    } else {
        Json(json!({ "rows": rows, "per_group_limit": limit, "error": errors.join("; ") }))
    }
}

fn event_coverage_snapshot_sql(table: &str, action: &str, limit: usize) -> String {
    format!(
        r#"
        SELECT
            started_at_utc AS started_at,
            updated_at_utc AS finished_at,
            coverage_kind,
            status,
            coverage_start_utc AS start_ts_utc,
            coverage_end_utc AS end_ts_utc,
            source AS action,
            rows_written,
            '' AS host_role,
            '' AS command,
            metadata_json AS summary_json,
            '{action}' AS table_group
        FROM {table} FINAL
        ORDER BY updated_at_utc DESC
        LIMIT {limit}
        FORMAT JSONEachRow
        "#,
        table = table,
        action = action,
        limit = limit,
    )
}

async fn coverage_query_rows(config: &GatewayConfig, sql: &str) -> Result<Vec<Value>, String> {
    let text = clickhouse_query(config, sql, true).await?;
    Ok(text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .collect::<Vec<_>>())
}

async fn indicator_catalog_snapshot() -> Json<Vec<IndicatorTaxonomyEntry<'static>>> {
    Json(indicator_taxonomy_catalog())
}

async fn capability_catalog_snapshot() -> Json<Vec<ComputationCapability<'static>>> {
    Json(computation_capability_catalog())
}

async fn definition_catalog_snapshot() -> Json<QmdDefinitionCatalog> {
    Json(definition_catalog())
}

async fn signal_catalog_snapshot() -> Json<Vec<SignalTaxonomyEntry<'static>>> {
    Json(signal_taxonomy_catalog())
}

async fn clickhouse_query(
    config: &GatewayConfig,
    body: &str,
    use_database: bool,
) -> Result<String, String> {
    let url = if use_database {
        format!(
            "{}/?database={}",
            config.clickhouse_url,
            urlencoding::encode(&config.clickhouse_database)
        )
    } else {
        format!("{}/", config.clickhouse_url)
    };
    let mut request = Client::new()
        .post(url)
        .header("Content-Type", "text/plain; charset=utf-8")
        .header("X-ClickHouse-User", &config.clickhouse_user)
        .body(body.to_string());
    let password = config.clickhouse_password();
    if !password.is_empty() {
        request = request.header("X-ClickHouse-Key", password);
    }
    let response = request.send().await.map_err(|error| error.to_string())?;
    let status = response.status();
    let text = response.text().await.map_err(|error| error.to_string())?;
    if !status.is_success() {
        return Err(format!("ClickHouse HTTP {status}: {text}"));
    }
    Ok(text)
}

async fn scanner_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<Value> {
    let now = Utc::now();
    let snapshot = state
        .market
        .scanner_snapshot_at(now, query.limit.unwrap_or(250).min(25_000))
        .await;
    let calendar = state.market_calendar.snapshot(now);
    let local = now.with_timezone(&New_York);
    let local_minutes = local.hour() * 60 + local.minute();
    let regular_open = 9 * 60 + 30;
    let regular_close_at = New_York
        .from_local_datetime(
            &local
                .date_naive()
                .and_hms_opt(16, 0, 0)
                .expect("valid market close"),
        )
        .single()
        .map(|value| value.with_timezone(&Utc));
    let session_close_at = calendar.session_close_at.or(regular_close_at);
    let trading_day = !matches!(local.weekday(), chrono::Weekday::Sat | chrono::Weekday::Sun)
        && !calendar.reason.starts_with("holiday_closed:");
    let mut payload = serde_json::to_value(snapshot).unwrap_or_else(|_| json!({}));
    payload["market_clock"] = json!({
        "observed_at": now.to_rfc3339(),
        "utc_date": now.format("%Y-%m-%d").to_string(),
        "utc_time": now.format("%H:%M:%S%.3f").to_string(),
        "exchange_date": local.format("%Y-%m-%d").to_string(),
        "exchange_time": local.format("%H:%M:%S").to_string(),
        "trading_date": local.format("%Y-%m-%d").to_string(),
        "timezone": "America/New_York",
        "weekday": local.format("%A").to_string(),
        "session_id": local.format("%Y-%m-%d").to_string(),
        "session_phase": format!("{:?}", session_phase(now)).to_ascii_lowercase(),
        "session_open_at": format!("{}T09:30:00", local.format("%Y-%m-%d")),
        "session_close_at": session_close_at.map(|value| value.to_rfc3339()),
        "minutes_since_open": if trading_day && local_minutes >= regular_open { Some(local_minutes - regular_open) } else { None },
        "minutes_until_close": if trading_day { session_close_at.and_then(|close| (close > now).then_some((close - now).num_minutes())) } else { None },
        "is_trading_day": trading_day,
        "is_early_close": calendar.is_early_close,
        "market_status": if calendar.active_collection_window { "active" } else { "closed" },
        "market_is_open": calendar.active_collection_window,
        "market_feed_status": if calendar.stale { "stale" } else { "ready" },
        "market_calendar_source": calendar.source,
        "market_calendar_reason": calendar.reason,
    });
    Json(payload)
}

async fn scanner_primitive_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<ScannerPrimitiveSnapshot> {
    Json(
        state
            .scanner
            .snapshot(query.limit.unwrap_or(250).min(5_000))
            .await,
    )
}

async fn scanner_indicator_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<BarsQuery>,
) -> Result<Json<Value>, (StatusCode, String)> {
    let fields = parse_indicator_projection_fields(query.fields.as_deref())
        .map_err(|error| (StatusCode::BAD_REQUEST, error))?;
    let snapshot = state
        .indicators
        .scanner_snapshot(
            query.timeframe.as_deref().unwrap_or("10s"),
            query.limit.unwrap_or(25_000).min(25_000),
        )
        .await;
    Ok(Json(project_indicator_scanner_snapshot(
        snapshot,
        fields.as_ref(),
    )))
}

fn parse_indicator_projection_fields(
    raw: Option<&str>,
) -> Result<Option<BTreeSet<String>>, String> {
    let Some(raw) = raw.map(str::trim).filter(|value| !value.is_empty()) else {
        return Ok(None);
    };
    let fields = raw
        .split(',')
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_string)
        .collect::<BTreeSet<_>>();
    if fields.len() > 64 {
        return Err("fields accepts at most 64 indicator field names".to_string());
    }
    if fields.iter().any(|field| {
        field.len() > 64
            || !field
                .chars()
                .all(|character| character.is_ascii_alphanumeric() || character == '_')
    }) {
        return Err("fields contains an invalid indicator field name".to_string());
    }
    Ok(Some(fields))
}

fn project_indicator_scanner_snapshot(
    snapshot: IndicatorScannerSnapshot,
    requested: Option<&BTreeSet<String>>,
) -> Value {
    let mut value = serde_json::to_value(snapshot).expect("indicator snapshot is serializable");
    let Some(requested) = requested else {
        return value;
    };
    retain_indicator_projection_fields(&mut value, requested);
    value
}

fn retain_indicator_projection_fields(value: &mut Value, requested: &BTreeSet<String>) {
    let mandatory = [
        "schema_version",
        "session_date",
        "timeframe",
        "sym",
        "bar_start",
        "bar_end",
    ];
    if let Some(rows) = value.get_mut("rows").and_then(Value::as_array_mut) {
        for row in rows {
            if let Some(object) = row.as_object_mut() {
                object
                    .retain(|key, _| mandatory.contains(&key.as_str()) || requested.contains(key));
            }
        }
    }
}

async fn market_signal_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<MarketSignalSnapshot> {
    Json(
        state
            .scanner
            .signal_snapshot(query.limit.unwrap_or(250).min(5_000))
            .await,
    )
}

async fn market_signal_event_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<MarketSignalSnapshot> {
    Json(
        state
            .scanner
            .signal_event_snapshot(query.limit.unwrap_or(1_000).min(10_000))
            .await,
    )
}

async fn ticker_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
) -> Json<Option<SymbolSnapshot>> {
    Json(state.market.ticker_snapshot(&ticker).await)
}

async fn ticker_state_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
) -> Json<TickerStateSnapshot> {
    Json(state.market.ticker_state_snapshot(&ticker).await)
}

async fn bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
) -> Json<BarSnapshot> {
    let timeframe = query.timeframe.as_deref().unwrap_or("1m");
    let mut snapshot = state
        .bars
        .snapshot(
            &ticker,
            timeframe,
            query
                .limit
                .unwrap_or(500)
                .min(state.config.bar_history_limit),
        )
        .await
        .price_bars();
    if let Some(resolution_us) = parse_resolution_us(timeframe) {
        let family = state
            .products
            .family_snapshot(
                &ticker,
                resolution_us,
                state.config.bar_history_limit.saturating_mul(3),
                chrono::Utc::now(),
            )
            .await;
        snapshot.reconcile_family_authority(&family.rows);
    }
    Json(snapshot)
}

async fn intraday_bar_history_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<IntradayBarHistoryQuery>,
) -> Result<Json<Value>, (StatusCode, Json<Value>)> {
    let ticker = ticker.trim().to_ascii_uppercase();
    if !valid_ticker(&ticker) {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "ticker is invalid"})),
        ));
    }
    let timeframe = query
        .timeframe
        .as_deref()
        .unwrap_or("1m")
        .to_ascii_lowercase();
    let Some(resolution_us) = parse_resolution_us(&timeframe) else {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "timeframe is invalid"})),
        ));
    };
    if !state
        .config
        .intraday_bar_timeframes
        .iter()
        .any(|value| value.eq_ignore_ascii_case(&timeframe))
    {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "timeframe is not persisted by QMD Live"})),
        ));
    }
    let today = Utc::now().with_timezone(&New_York).date_naive();
    let start_date = parse_iso_date(query.start_date.as_deref()).unwrap_or(today);
    let end_date = parse_iso_date(query.end_date.as_deref()).unwrap_or(today);
    if start_date > end_date {
        return Err((
            StatusCode::BAD_REQUEST,
            Json(json!({"error": "start_date must not follow end_date"})),
        ));
    }
    let limit = query.limit.unwrap_or(20_000).clamp(1, 50_000);
    let sql = intraday_bar_history_sql(
        &state.config.intraday_bar_table,
        &ticker,
        resolution_us,
        start_date,
        end_date,
        query.before_event_timestamp_us,
        limit.saturating_add(1),
    );
    let text = clickhouse_query(&state.config, &sql, true)
        .await
        .map_err(|error| {
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": error, "source": "qmd_live_clickhouse"})),
            )
        })?;
    let mut rows = text
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str::<Value>(line).map_err(|error| error.to_string()))
        .collect::<Result<Vec<_>, _>>()
        .map_err(|error| {
            (
                StatusCode::BAD_GATEWAY,
                Json(json!({"error": error, "source": "qmd_live_clickhouse"})),
            )
        })?;
    let has_more = rows.len() > limit;
    rows.truncate(limit);
    rows.reverse();
    let bars = rows
        .into_iter()
        .filter_map(|row| intraday_row_to_chart_bar(row, &ticker, &timeframe, resolution_us))
        .collect::<Vec<_>>();
    let next_before_event_timestamp_us = bars
        .first()
        .and_then(|row| row.get("first_event_timestamp_us"))
        .and_then(Value::as_u64);
    Ok(Json(json!({
        "schema_version": 1,
        "ticker": ticker,
        "timeframe": timeframe,
        "bars": bars,
        "has_more": has_more,
        "next_before_event_timestamp_us": next_before_event_timestamp_us,
        "source": "qmd_live_intraday_family_bars_v2",
    })))
}

fn parse_iso_date(value: Option<&str>) -> Option<NaiveDate> {
    value.and_then(|raw| NaiveDate::parse_from_str(raw, "%Y-%m-%d").ok())
}

fn valid_ticker(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 10
        && value.bytes().enumerate().all(|(index, byte)| {
            byte.is_ascii_uppercase()
                || byte.is_ascii_digit()
                || (index > 0 && matches!(byte, b'.' | b'-'))
        })
}

fn intraday_bar_history_sql(
    table: &str,
    ticker: &str,
    resolution_us: u64,
    start_date: NaiveDate,
    end_date: NaiveDate,
    before_event_timestamp_us: Option<u64>,
    limit: usize,
) -> String {
    let before_filter = before_event_timestamp_us
        .map(|value| format!(" AND first_event_timestamp_us < {value}"))
        .unwrap_or_default();
    format!(
        r#"SELECT local_date, open, high, low, close, size_sum, event_count,
            first_event_timestamp_us, last_event_timestamp_us,
            bar_start_session_us, bar_end_session_us
        FROM (
            SELECT local_date, bucket_index,
                argMax(open, updated_at_utc) AS open,
                argMax(high, updated_at_utc) AS high,
                argMax(low, updated_at_utc) AS low,
                argMax(close, updated_at_utc) AS close,
                argMax(size_sum, updated_at_utc) AS size_sum,
                argMax(event_count, updated_at_utc) AS event_count,
                argMax(first_event_timestamp_us, updated_at_utc) AS first_event_timestamp_us,
                argMax(last_event_timestamp_us, updated_at_utc) AS last_event_timestamp_us,
                argMax(bar_start_session_us, updated_at_utc) AS bar_start_session_us,
                argMax(bar_end_session_us, updated_at_utc) AS bar_end_session_us
            FROM {table}
            WHERE ticker = '{ticker}' AND label_resolution_us = {resolution_us}
              AND bar_family = 'trade' AND local_date >= toDate('{start_date}')
              AND local_date <= toDate('{end_date}')
            GROUP BY local_date, bucket_index
        )
        WHERE 1 = 1{before_filter}
        ORDER BY local_date DESC, bucket_index DESC
        LIMIT {limit}
        FORMAT JSONEachRow"#
    )
}

fn intraday_row_to_chart_bar(
    row: Value,
    ticker: &str,
    timeframe: &str,
    resolution_us: u64,
) -> Option<Value> {
    let object = row.as_object()?;
    let local_date =
        NaiveDate::parse_from_str(object.get("local_date")?.as_str()?, "%Y-%m-%d").ok()?;
    let session_start_us = object.get("bar_start_session_us")?.as_i64()?;
    let midnight = New_York
        .from_local_datetime(&local_date.and_hms_opt(0, 0, 0)?)
        .single()?;
    let bar_start = midnight + chrono::Duration::microseconds(session_start_us);
    let bar_end = bar_start + chrono::Duration::microseconds(i64::try_from(resolution_us).ok()?);
    let open = object.get("open")?.as_f64()?;
    let high = object.get("high")?.as_f64()?;
    let low = object.get("low")?.as_f64()?;
    let close = object.get("close")?.as_f64()?;
    let volume = object
        .get("size_sum")
        .and_then(Value::as_f64)
        .unwrap_or(0.0);
    let trade_count = object
        .get("event_count")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    Some(json!({
        "schema_version": 2,
        "session_date": local_date.to_string(),
        "timeframe": timeframe,
        "sym": ticker,
        "bar_start": bar_start.with_timezone(&Utc).to_rfc3339(),
        "bar_end": bar_end.with_timezone(&Utc).to_rfc3339(),
        "is_closed": bar_end.with_timezone(&Utc) <= Utc::now(),
        "first_event_ts": object.get("first_event_timestamp_us"),
        "last_event_ts": object.get("last_event_timestamp_us"),
        "first_event_timestamp_us": object.get("first_event_timestamp_us"),
        "last_event_timestamp_us": object.get("last_event_timestamp_us"),
        "open": open,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "trade_count": trade_count,
        "avg_trade_size": if trade_count > 0 { volume / trade_count as f64 } else { 0.0 },
        "price_change": close - open,
        "price_change_pct": if open > 0.0 { (close / open - 1.0) * 100.0 } else { 0.0 },
        "high_low_range": high - low,
        "high_low_range_pct": if open > 0.0 { (high - low) / open * 100.0 } else { 0.0 },
        "source": "qmd_live_intraday_family_bars_v2",
    }))
}

async fn product_cache_snapshot(State(state): State<Arc<AppState>>) -> Json<ProductCacheMetrics> {
    Json(state.products.metrics().await)
}

async fn family_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Json<FamilyBarSnapshot> {
    let resolution_us = query
        .resolution
        .as_deref()
        .and_then(parse_resolution_us)
        .unwrap_or(60_000_000);
    let limit = query
        .limit
        .unwrap_or(1_500)
        .min(state.config.product_cache_max_rows);
    let snapshot = match (query.family.as_deref(), query.price_only.unwrap_or(false)) {
        (Some("trade"), true) => {
            state
                .products
                .trade_price_snapshot(&ticker, resolution_us, limit, chrono::Utc::now())
                .await
        }
        (Some(family), _) => {
            state
                .products
                .family_snapshot_for(&ticker, resolution_us, family, limit, chrono::Utc::now())
                .await
        }
        (None, _) => {
            state
                .products
                .family_snapshot(&ticker, resolution_us, limit, chrono::Utc::now())
                .await
        }
    };
    Json(snapshot)
}

async fn condition_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Json<ConditionBarSnapshot> {
    let resolution_us = query
        .resolution
        .as_deref()
        .and_then(parse_resolution_us)
        .unwrap_or(60_000_000);
    Json(
        state
            .products
            .condition_snapshot(
                &ticker,
                resolution_us,
                query
                    .limit
                    .unwrap_or(1_500)
                    .min(state.config.product_cache_max_rows),
                chrono::Utc::now(),
            )
            .await,
    )
}

async fn macro_bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> Json<MacroBarSnapshot> {
    Json(
        state
            .products
            .macro_snapshot(
                &ticker,
                query.timeframe.as_deref().unwrap_or("1d"),
                query.limit.unwrap_or(500).min(10_000),
                chrono::Utc::now(),
            )
            .await,
    )
}

async fn macro_bar_scanner_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<ProductQuery>,
) -> Json<crate::market_products::MacroBarScannerSnapshot> {
    Json(
        state
            .products
            .macro_scanner_snapshot(
                query.timeframe.as_deref().unwrap_or("1d"),
                query.limit.unwrap_or(25_000).min(25_000),
                chrono::Utc::now(),
            )
            .await,
    )
}

async fn compact_event_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<LimitQuery>,
) -> Json<Vec<LiveCompactEvent>> {
    Json(
        state
            .compact_event_store
            .latest_sorted(
                &ticker,
                query
                    .limit
                    .unwrap_or(128)
                    .min(state.config.compact_event_live_buffer_events_per_ticker),
            )
            .await,
    )
}

async fn compact_event_page_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<CompactEventPageQuery>,
) -> Json<CompactEventPage> {
    let limit = query
        .limit
        .unwrap_or(128)
        .min(state.config.compact_event_live_buffer_events_per_ticker);
    Json(match query.after_arrival_sequence {
        Some(sequence) => {
            state
                .compact_event_store
                .page_after(&ticker, sequence, limit)
                .await
        }
        None => state.compact_event_store.latest_page(&ticker, limit).await,
    })
}

async fn compact_event_market_page_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<CompactEventMarketPageQuery>,
) -> Result<Json<CompactEventMarketPage>, (StatusCode, String)> {
    if query.start_sip_timestamp_us >= query.end_sip_timestamp_us {
        return Err((
            StatusCode::BAD_REQUEST,
            "start_sip_timestamp_us must precede end_sip_timestamp_us".to_string(),
        ));
    }
    let tickers = query
        .tickers
        .as_deref()
        .unwrap_or_default()
        .split(',')
        .map(str::trim)
        .filter(|ticker| !ticker.is_empty())
        .map(str::to_string)
        .collect::<Vec<_>>();
    Ok(Json(
        state
            .compact_event_store
            .market_page_after(
                query.after_arrival_sequence.unwrap_or(0),
                query.start_sip_timestamp_us,
                query.end_sip_timestamp_us,
                &tickers,
                query.limit.unwrap_or(10_000),
                query.through_arrival_sequence,
            )
            .await,
    ))
}

async fn indicator_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
) -> Json<IndicatorSnapshot> {
    Json(
        state
            .indicators
            .snapshot(
                &ticker,
                query.timeframe.as_deref().unwrap_or("1m"),
                query
                    .limit
                    .unwrap_or(500)
                    .min(state.config.indicator_history_limit),
            )
            .await,
    )
}

async fn scanner_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_scanner(socket, state, query.limit.unwrap_or(250).min(25_000)).await;
    })
}

async fn market_signal_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_market_signals(socket, state).await;
    })
}

async fn ticker_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_ticker(socket, state, ticker.to_ascii_uppercase()).await;
    })
}

async fn bar_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_bars(
            socket,
            state,
            ticker.to_ascii_uppercase(),
            query.timeframe.unwrap_or_else(|| "1m".to_string()),
            query.limit.unwrap_or(500),
        )
        .await;
    })
}

async fn family_bar_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        let resolution_us = query
            .resolution
            .as_deref()
            .and_then(parse_resolution_us)
            .unwrap_or(60_000_000);
        stream_product_snapshots(socket, state, ticker, resolution_us, query, "family").await;
    })
}

async fn condition_bar_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        let resolution_us = query
            .resolution
            .as_deref()
            .and_then(parse_resolution_us)
            .unwrap_or(60_000_000);
        stream_product_snapshots(socket, state, ticker, resolution_us, query, "condition").await;
    })
}

async fn macro_bar_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<ProductQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_product_snapshots(socket, state, ticker, 0, query, "macro").await;
    })
}

async fn stream_product_snapshots(
    mut socket: WebSocket,
    state: Arc<AppState>,
    ticker: String,
    resolution_us: u64,
    query: ProductQuery,
    kind: &'static str,
) {
    let mut timer = interval(Duration::from_millis(state.config.ticker_broadcast_ms));
    let limit = query
        .limit
        .unwrap_or(1_500)
        .min(state.config.product_cache_max_rows);
    let emit = query.emit.as_deref().unwrap_or("full_then_updates");
    if !matches!(emit, "full" | "updates" | "full_then_updates") {
        let _ = socket
            .send(Message::Text(
                r#"{"error":"emit must be full, updates, or full_then_updates"}"#.into(),
            ))
            .await;
        return;
    }
    let mut first = true;
    let mut seen = std::collections::HashMap::<String, u64>::new();
    loop {
        timer.tick().await;
        let now = chrono::Utc::now();
        let payload = match kind {
            "condition" => {
                let mut snapshot = state
                    .products
                    .condition_snapshot(&ticker, resolution_us, limit, now)
                    .await;
                if emit == "updates" || !first {
                    snapshot.rows.retain(|row| {
                        let key = format!("{}:{}", row.label_resolution_us, row.bucket_index);
                        row.revision > seen.get(&key).copied().unwrap_or(0)
                    });
                }
                for row in &snapshot.rows {
                    seen.insert(
                        format!("{}:{}", row.label_resolution_us, row.bucket_index),
                        row.revision,
                    );
                }
                serde_json::to_string(&snapshot)
            }
            "macro" => {
                let mut snapshot = state
                    .products
                    .macro_snapshot(
                        &ticker,
                        query.timeframe.as_deref().unwrap_or("1d"),
                        limit,
                        now,
                    )
                    .await;
                if emit == "updates" || !first {
                    snapshot.rows.retain(|row| {
                        let key =
                            format!("{}:{}:{}", row.timeframe, row.session_date, row.bar_family);
                        row.revision > seen.get(&key).copied().unwrap_or(0)
                    });
                }
                for row in &snapshot.rows {
                    seen.insert(
                        format!("{}:{}:{}", row.timeframe, row.session_date, row.bar_family),
                        row.revision,
                    );
                }
                serde_json::to_string(&snapshot)
            }
            _ => {
                let mut snapshot =
                    match (query.family.as_deref(), query.price_only.unwrap_or(false)) {
                        (Some("trade"), true) => {
                            state
                                .products
                                .trade_price_snapshot(&ticker, resolution_us, limit, now)
                                .await
                        }
                        (Some(family), _) => {
                            state
                                .products
                                .family_snapshot_for(&ticker, resolution_us, family, limit, now)
                                .await
                        }
                        (None, _) => {
                            state
                                .products
                                .family_snapshot(&ticker, resolution_us, limit, now)
                                .await
                        }
                    };
                if emit == "updates" || !first {
                    snapshot.rows.retain(|row| {
                        let key = format!(
                            "{}:{}:{}:{}",
                            row.local_date,
                            row.label_resolution_us,
                            row.bucket_index,
                            row.bar_family
                        );
                        row.revision > seen.get(&key).copied().unwrap_or(0)
                    });
                }
                for row in &snapshot.rows {
                    seen.insert(
                        format!(
                            "{}:{}:{}:{}",
                            row.local_date,
                            row.label_resolution_us,
                            row.bucket_index,
                            row.bar_family
                        ),
                        row.revision,
                    );
                }
                serde_json::to_string(&snapshot)
            }
        };
        match payload {
            Ok(text) => {
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            Err(error) => {
                if socket
                    .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                    .await
                    .is_err()
                {
                    break;
                }
            }
        }
        if emit == "full" {
            return;
        }
        first = false;
    }
}

async fn indicator_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_indicators(
            socket,
            state,
            ticker.to_ascii_uppercase(),
            query.timeframe.unwrap_or_else(|| "1m".to_string()),
            query.limit.unwrap_or(500),
        )
        .await;
    })
}

async fn event_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_events(socket, state).await;
    })
}

async fn compact_event_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_compact_events(socket, state).await;
    })
}

async fn intraday_bar_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_intraday_bars(socket, state).await;
    })
}

async fn stream_intraday_bars(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.intraday_bars.subscribe();
    loop {
        match receiver.recv().await {
            Ok(row) => {
                let payload = serde_json::to_string(&row).unwrap_or_else(|_| "{}".to_string());
                if socket.send(Message::Text(payload.into())).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Lagged(count)) => {
                send_resnapshot_required(
                    &mut socket,
                    "intraday_bar_stream_lagged",
                    count,
                    "/snapshot/family-bars/{ticker}",
                    None,
                )
                .await;
                break;
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn live_market_state_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_live_market_state(socket, state).await;
    })
}

async fn stream_live_market_state(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.live_market_state_events.subscribe();
    loop {
        match receiver.recv().await {
            Ok(event) => {
                if socket
                    .send(Message::Text(
                        serde_json::to_string(&event)
                            .unwrap_or_else(|_| "{}".to_string())
                            .into(),
                    ))
                    .await
                    .is_err()
                {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Lagged(count)) => {
                send_resnapshot_required(
                    &mut socket,
                    "live_market_state_stream_lagged",
                    count,
                    "/snapshot/live-market-state",
                    None,
                )
                .await;
                break;
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_compact_events(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.compact_events.subscribe();
    let mut delivered_arrival_sequence = 0_u64;
    loop {
        match receiver.recv().await {
            Ok(event) => match serde_json::to_string(&event) {
                Ok(text) => {
                    if socket.send(Message::Text(text.into())).await.is_err() {
                        break;
                    }
                    delivered_arrival_sequence = event.arrival_sequence;
                }
                Err(error) => {
                    if socket
                        .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            },
            Err(broadcast::error::RecvError::Lagged(count)) => {
                send_resnapshot_required(
                    &mut socket,
                    "compact_event_stream_lagged",
                    count,
                    "/snapshot/compact-event-market-page",
                    Some(delivered_arrival_sequence),
                )
                .await;
                break;
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_events(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.events.subscribe();
    loop {
        match receiver.recv().await {
            Ok(event) => match serde_json::to_string(&event) {
                Ok(text) => {
                    if socket.send(Message::Text(text.into())).await.is_err() {
                        break;
                    }
                }
                Err(error) => {
                    if socket
                        .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            },
            Err(broadcast::error::RecvError::Lagged(count)) => {
                send_resnapshot_required(
                    &mut socket,
                    "event_stream_lagged",
                    count,
                    "/snapshot/compact-event-market-page",
                    None,
                )
                .await;
                break;
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn send_scanner_snapshot(
    socket: &mut WebSocket,
    state: &AppState,
    limit: usize,
    reason: Option<&'static str>,
    skipped: Option<u64>,
) -> Result<u64, ()> {
    let snapshot = state.market.scanner_snapshot(limit).await;
    let sequence = snapshot.sequence;
    let payload = json!({
        "kind": "snapshot",
        "reason": reason,
        "skipped": skipped,
        "snapshot": snapshot,
    });
    let text = serde_json::to_string(&payload).map_err(|_| ())?;
    socket
        .send(Message::Text(text.into()))
        .await
        .map_err(|_| ())?;
    Ok(sequence)
}

async fn stream_scanner(mut socket: WebSocket, state: Arc<AppState>, limit: usize) {
    let mut receiver = state.scanner_deltas.subscribe();
    let Ok(mut delivered_sequence) =
        send_scanner_snapshot(&mut socket, &state, limit, None, None).await
    else {
        return;
    };
    loop {
        match receiver.recv().await {
            Ok(delta) if delta.sequence <= delivered_sequence => continue,
            Ok(delta) if scanner_sequence_gap(delivered_sequence, delta.sequence).is_some() => {
                let skipped = scanner_sequence_gap(delivered_sequence, delta.sequence)
                    .expect("guard requires a Scanner sequence gap");
                let warning = json!({
                    "warning": "scanner_delta_sequence_gap",
                    "expected_sequence": delivered_sequence.saturating_add(1),
                    "received_sequence": delta.sequence,
                    "skipped": skipped,
                    "action": "resnapshot",
                });
                if socket
                    .send(Message::Text(warning.to_string().into()))
                    .await
                    .is_err()
                {
                    break;
                }
                match send_scanner_snapshot(
                    &mut socket,
                    &state,
                    limit,
                    Some("sequence_gap"),
                    Some(skipped),
                )
                .await
                {
                    Ok(sequence) => delivered_sequence = sequence,
                    Err(()) => break,
                }
            }
            Ok(delta) => match serde_json::to_string(&json!({"kind": "row_delta", "delta": delta}))
            {
                Ok(text) => {
                    if socket.send(Message::Text(text.into())).await.is_err() {
                        break;
                    }
                    delivered_sequence = delta.sequence;
                }
                Err(error) => {
                    if socket
                        .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            },
            Err(broadcast::error::RecvError::Lagged(count)) => {
                let warning = format!(
                    r#"{{"warning":"scanner_delta_stream_lagged","skipped":{count},"action":"resnapshot"}}"#
                );
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
                match send_scanner_snapshot(
                    &mut socket,
                    &state,
                    limit,
                    Some("receiver_lag"),
                    Some(count),
                )
                .await
                {
                    Ok(sequence) => delivered_sequence = sequence,
                    Err(()) => break,
                }
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_market_signals(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.scanner_events.subscribe();
    let mut delivered_sequence = state.scanner.signal_snapshot(0).await.last_sequence;
    loop {
        match receiver.recv().await {
            Ok(delta) => match serde_json::to_string(&delta) {
                Ok(text) => {
                    if socket.send(Message::Text(text.into())).await.is_err() {
                        break;
                    }
                    delivered_sequence = delta.sequence;
                }
                Err(error) => {
                    if socket
                        .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                        .await
                        .is_err()
                    {
                        break;
                    }
                }
            },
            Err(broadcast::error::RecvError::Lagged(count)) => {
                send_resnapshot_required(
                    &mut socket,
                    "market_signal_stream_lagged",
                    count,
                    "/snapshot/signal-events",
                    Some(delivered_sequence),
                )
                .await;
                break;
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_ticker(mut socket: WebSocket, state: Arc<AppState>, ticker: String) {
    let mut timer = interval(Duration::from_millis(state.config.ticker_broadcast_ms));
    loop {
        timer.tick().await;
        let snapshot = state.market.ticker_snapshot(&ticker).await;
        match serde_json::to_string(&snapshot) {
            Ok(text) => {
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            Err(error) => {
                if socket
                    .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                    .await
                    .is_err()
                {
                    break;
                }
            }
        }
    }
}

async fn stream_bars(
    mut socket: WebSocket,
    state: Arc<AppState>,
    ticker: String,
    timeframe: String,
    limit: usize,
) {
    let mut timer = interval(Duration::from_millis(state.config.ticker_broadcast_ms));
    loop {
        timer.tick().await;
        let mut snapshot = state
            .bars
            .snapshot(
                &ticker,
                &timeframe,
                limit.min(state.config.bar_history_limit),
            )
            .await
            .price_bars();
        if let Some(resolution_us) = parse_resolution_us(&timeframe) {
            let family = state
                .products
                .family_snapshot(
                    &ticker,
                    resolution_us,
                    limit.min(state.config.product_cache_max_rows),
                    chrono::Utc::now(),
                )
                .await;
            snapshot.reconcile_family_authority(&family.rows);
        }
        match serde_json::to_string(&snapshot) {
            Ok(text) => {
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            Err(error) => {
                if socket
                    .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                    .await
                    .is_err()
                {
                    break;
                }
            }
        }
    }
}

async fn stream_indicators(
    mut socket: WebSocket,
    state: Arc<AppState>,
    ticker: String,
    timeframe: String,
    limit: usize,
) {
    let mut timer = interval(Duration::from_millis(state.config.ticker_broadcast_ms));
    loop {
        timer.tick().await;
        let snapshot = state
            .indicators
            .snapshot(
                &ticker,
                &timeframe,
                limit.min(state.config.indicator_history_limit),
            )
            .await;
        match serde_json::to_string(&snapshot) {
            Ok(text) => {
                if socket.send(Message::Text(text.into())).await.is_err() {
                    break;
                }
            }
            Err(error) => {
                if socket
                    .send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into()))
                    .await
                    .is_err()
                {
                    break;
                }
            }
        }
    }
}
