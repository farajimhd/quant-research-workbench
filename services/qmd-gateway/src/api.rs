use crate::bars::{BarSnapshot, SharedBarStore};
use crate::compact_event::{LiveCompactEvent, SharedCompactEventStore};
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::indicator_catalog::{indicator_catalog, IndicatorCatalogEntry};
use crate::indicators::{IndicatorSnapshot, SharedIndicatorStore};
use crate::intraday_bars::IntradayBarRow;
use crate::live_market_state::{
    LiveMarketStateSnapshot, LiveSymbolMarketStateEvent, SharedLiveMarketStateStore,
    TickerLiveMarketStateSnapshot,
};
use crate::maintenance::{MaintenanceSnapshot, SharedMaintenanceState};
use crate::market_calendar::{MarketCalendarClient, MarketSnapshot};
use crate::metrics::{MetricsSnapshot, OperationalSnapshot, SharedMetrics};
use crate::scanner::{ScannerPrimitive, ScannerPrimitiveSnapshot, SharedScannerStore};
use crate::session::session_phase;
use crate::signal_catalog::{signal_catalog, SignalMethodEntry};
use crate::state::{ScannerSnapshot, SharedMarketState, StatusMetrics, SymbolSnapshot};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::{HeaderMap, StatusCode};
use axum::response::IntoResponse;
use axum::routing::{get, post};
use axum::{Json, Router};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::{broadcast, watch};
use tokio::time::{interval, Duration};
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub bars: SharedBarStore,
    pub compact_event_store: SharedCompactEventStore,
    pub compact_events: broadcast::Sender<LiveCompactEvent>,
    pub config: GatewayConfig,
    pub events: broadcast::Sender<MarketEvent>,
    pub indicators: SharedIndicatorStore,
    pub live_market_state: SharedLiveMarketStateStore,
    pub live_market_state_events: broadcast::Sender<LiveSymbolMarketStateEvent>,
    pub market: SharedMarketState,
    pub maintenance: SharedMaintenanceState,
    pub market_calendar: MarketCalendarClient,
    pub metrics: SharedMetrics,
    pub intraday_bars: broadcast::Sender<IntradayBarRow>,
    pub scanner: SharedScannerStore,
    pub scanner_events: broadcast::Sender<ScannerPrimitive>,
    pub shutdown: watch::Sender<bool>,
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<usize>,
}

#[derive(Debug, Deserialize)]
struct BarsQuery {
    limit: Option<usize>,
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
        .route("/indicator-catalog", get(indicator_catalog_snapshot))
        .route("/signal-catalog", get(signal_catalog_snapshot))
        .route("/snapshot/scanner", get(scanner_snapshot))
        .route(
            "/snapshot/scanner-primitives",
            get(scanner_primitive_snapshot),
        )
        .route("/snapshot/ticker/{ticker}", get(ticker_snapshot))
        .route("/snapshot/bars/{ticker}", get(bar_snapshot))
        .route(
            "/snapshot/compact-events/{ticker}",
            get(compact_event_snapshot),
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
        .route("/stream/scanner-primitives", get(scanner_primitive_stream))
        .route("/stream/ticker/{ticker}", get(ticker_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .route("/stream/indicators/{ticker}", get(indicator_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
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

#[cfg(test)]
mod shutdown_tests {
    use super::valid_shutdown_token;

    #[test]
    fn shutdown_requires_the_configured_non_empty_token() {
        assert!(valid_shutdown_token("run-token", "run-token"));
        assert!(!valid_shutdown_token("run-token", "wrong"));
        assert!(!valid_shutdown_token("", ""));
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

async fn indicator_catalog_snapshot() -> Json<&'static [IndicatorCatalogEntry]> {
    Json(indicator_catalog())
}

async fn signal_catalog_snapshot() -> Json<&'static [SignalMethodEntry]> {
    Json(signal_catalog())
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
) -> Json<ScannerSnapshot> {
    Json(
        state
            .market
            .scanner_snapshot(query.limit.unwrap_or(250).min(5_000))
            .await,
    )
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

async fn ticker_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
) -> Json<Option<SymbolSnapshot>> {
    Json(state.market.ticker_snapshot(&ticker).await)
}

async fn bar_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
) -> Json<BarSnapshot> {
    Json(
        state
            .bars
            .snapshot(
                &ticker,
                query.timeframe.as_deref().unwrap_or("1m"),
                query
                    .limit
                    .unwrap_or(500)
                    .min(state.config.bar_history_limit),
            )
            .await
            .price_bars(),
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
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_scanner(socket, state).await;
    })
}

async fn scanner_primitive_stream(
    ws: WebSocketUpgrade,
    State(state): State<Arc<AppState>>,
) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_scanner_primitives(socket, state).await;
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
                let warning =
                    format!(r#"{{"warning":"intraday_bar_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
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
                let warning =
                    format!(r#"{{"warning":"live_market_state_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_compact_events(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.compact_events.subscribe();
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
                let warning =
                    format!(r#"{{"warning":"compact_event_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
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
                let warning = format!(r#"{{"warning":"event_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_scanner(mut socket: WebSocket, state: Arc<AppState>) {
    let mut timer = interval(Duration::from_millis(state.config.scanner_broadcast_ms));
    loop {
        timer.tick().await;
        let snapshot = state.market.scanner_snapshot(250).await;
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

async fn stream_scanner_primitives(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.scanner_events.subscribe();
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
                let warning =
                    format!(r#"{{"warning":"scanner_primitive_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
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
        let snapshot = state
            .bars
            .snapshot(
                &ticker,
                &timeframe,
                limit.min(state.config.bar_history_limit),
            )
            .await
            .price_bars();
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
