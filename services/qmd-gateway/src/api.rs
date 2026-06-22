use crate::bars::{BarSnapshot, SharedBarStore};
use crate::compact_event::{LiveCompactEvent, SharedCompactEventStore};
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::indicator_catalog::{indicator_catalog, IndicatorCatalogEntry};
use crate::indicators::{IndicatorSnapshot, SharedIndicatorStore};
use crate::metrics::{MetricsSnapshot, SharedMetrics};
use crate::scanner::{ScannerPrimitive, ScannerPrimitiveSnapshot, SharedScannerStore};
use crate::session::session_phase;
use crate::signal_catalog::{signal_catalog, SignalMethodEntry};
use crate::state::{ScannerSnapshot, SharedMarketState, StatusMetrics, SymbolSnapshot};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use reqwest::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tokio::sync::broadcast;
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
    pub market: SharedMarketState,
    pub metrics: SharedMetrics,
    pub scanner: SharedScannerStore,
    pub scanner_events: broadcast::Sender<ScannerPrimitive>,
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
    running: bool,
    session_phase: String,
    status: String,
    subscriptions: Vec<String>,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/metrics", get(metrics_snapshot))
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
        .route("/stream/compact-events", get(compact_event_stream))
        .route("/stream/events", get(event_stream))
        .route("/stream/scanner", get(scanner_stream))
        .route("/stream/scanner-primitives", get(scanner_primitive_stream))
        .route("/stream/ticker/{ticker}", get(ticker_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .route("/stream/indicators/{ticker}", get(indicator_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthPayload> {
    Json(HealthPayload {
        config: state.config.clone(),
        metrics: state.market.metrics().await,
        running: state.config.api_key_present,
        session_phase: format!("{:?}", session_phase(chrono::Utc::now())),
        status: if state.config.api_key_present {
            "running".to_string()
        } else {
            "api_only_missing_massive_key".to_string()
        },
        subscriptions: state.config.subscription_channels(),
    })
}

async fn config(State(state): State<Arc<AppState>>) -> Json<GatewayConfig> {
    Json(state.config.clone())
}

async fn metrics_snapshot(State(state): State<Arc<AppState>>) -> Json<MetricsSnapshot> {
    Json(state.metrics.snapshot())
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

    let flatfile_sql = event_coverage_snapshot_sql(
        &state.config.qmd_flatfile_event_coverage_table,
        "flatfile_coverage",
        limit,
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
    rows.truncate(limit);

    if errors.is_empty() {
        Json(json!({ "rows": rows }))
    } else {
        Json(json!({ "rows": rows, "error": errors.join("; ") }))
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
