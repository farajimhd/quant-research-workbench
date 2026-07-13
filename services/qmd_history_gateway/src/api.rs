use crate::config::HistoricalGatewayConfig;
use crate::source::{EventWindow, HistoricalCursor, HistoricalEventSource};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::http::StatusCode;
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use chrono::{DateTime, Utc};
use futures_util::SinkExt;
use qmd_core::bars::{is_supported_timeframe, BarSnapshot, SharedBarStore};
use qmd_core::compact_event::LiveCompactEvent;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::sync::Arc;
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub config: HistoricalGatewayConfig,
    pub source: HistoricalEventSource,
}

#[derive(Debug, Deserialize)]
struct HistoryQuery {
    end: String,
    limit: Option<usize>,
    start: String,
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
struct StreamQuery {
    batch_size: Option<usize>,
    end: String,
    start: String,
    timeframe: Option<String>,
    tickers: Option<String>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    config: HistoricalGatewayConfig,
    host_role: &'static str,
    running: bool,
    service: &'static str,
    source: &'static str,
    status: &'static str,
}

type ApiError = (StatusCode, Json<Value>);

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route(
            "/snapshot/compact-events/{ticker}",
            get(compact_event_snapshot),
        )
        .route("/snapshot/bars/{ticker}", get(bar_snapshot))
        .route("/stream/compact-events", get(compact_event_stream))
        .route("/stream/events", get(event_stream))
        .route("/stream/bars/{ticker}", get(bar_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
}

async fn health(State(state): State<Arc<AppState>>) -> Result<Json<HealthPayload>, ApiError> {
    state.source.health().await.map_err(service_error)?;
    Ok(Json(HealthPayload {
        config: state.config.clone(),
        host_role: "historical",
        running: true,
        service: "qmd_history_gateway",
        source: "market_sip_compact.events_YYYY",
        status: "ready",
    }))
}

async fn config(State(state): State<Arc<AppState>>) -> Json<HistoricalGatewayConfig> {
    Json(state.config.clone())
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
    let (events, _) = state
        .source
        .fetch_batch(&window, None, limit)
        .await
        .map_err(service_error)?;
    Ok(Json(events))
}

async fn bar_snapshot(
    Path(ticker): Path<String>,
    Query(query): Query<BarsQuery>,
    State(state): State<Arc<AppState>>,
) -> Result<Json<BarSnapshot>, ApiError> {
    let window = window(&query.start, &query.end, vec![ticker.clone()])?;
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    let bar_limit = query.limit.unwrap_or(1_000).clamp(1, 100_000);
    let event_limit = query
        .event_limit
        .unwrap_or(state.config.max_events_per_request)
        .clamp(1, state.config.max_events_per_request);
    let events =
        collect_events(&state.source, &window, state.config.batch_size, event_limit).await?;
    let bars = SharedBarStore::new(vec![timeframe.clone()], bar_limit, 1);
    let shard = bars.shard(0);
    for event in &events {
        shard.apply_event(&state.source.market_event(event)).await;
    }
    shard.finalize_due(window.end).await;
    Ok(Json(bars.snapshot(&ticker, &timeframe, bar_limit).await))
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
    let batch_size = query
        .batch_size
        .unwrap_or(state.config.batch_size)
        .clamp(1, 100_000);
    let timeframe = query.timeframe.unwrap_or_else(|| "1m".to_string());
    validate_timeframe(&timeframe)?;
    Ok(websocket.on_upgrade(move |socket| {
        stream_bars(socket, state.source.clone(), window, timeframe, batch_size)
    }))
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

async fn stream_bars(
    mut socket: WebSocket,
    source: HistoricalEventSource,
    window: EventWindow,
    timeframe: String,
    batch_size: usize,
) {
    let bars = SharedBarStore::new(vec![timeframe], 1, 1);
    let shard = bars.shard(0);
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
            for bar in shard.apply_event(&source.market_event(event)).await {
                if send_json(&mut socket, &bar).await.is_err() {
                    return;
                }
            }
        }
        if events.len() < batch_size || next.is_none() {
            for bar in shard.finalize_due(window.end).await {
                if send_json(&mut socket, &bar).await.is_err() {
                    return;
                }
            }
            let _ = socket.close().await;
            return;
        }
        cursor = next;
    }
}

async fn collect_events(
    source: &HistoricalEventSource,
    window: &EventWindow,
    batch_size: usize,
    max_events: usize,
) -> Result<Vec<LiveCompactEvent>, ApiError> {
    let mut events = Vec::new();
    let mut cursor: Option<HistoricalCursor> = None;
    loop {
        // Fetch one row beyond the allowed total so a window containing exactly
        // `max_events` is accepted while an actual overflow still fails loudly.
        let remaining_with_probe = max_events.saturating_sub(events.len()).saturating_add(1);
        let request_size = batch_size.min(remaining_with_probe).max(1);
        let (batch, next) = source
            .fetch_batch(window, cursor.as_ref(), request_size)
            .await
            .map_err(service_error)?;
        let count = batch.len();
        events.extend(batch);
        if events.len() > max_events {
            return Err(bad_request(format!(
                "historical bar request exceeded event_limit={max_events}; narrow the window"
            )));
        }
        if count < request_size || next.is_none() {
            return Ok(events);
        }
        cursor = next;
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

#[cfg(test)]
mod tests {
    use super::{parse_timestamp, validate_timeframe};

    #[test]
    fn timestamps_require_explicit_timezone() {
        assert!(parse_timestamp("2026-07-13T04:00:00-04:00").is_ok());
        assert!(parse_timestamp("2026-07-13 04:00:00").is_err());
    }

    #[test]
    fn timeframes_are_validated_by_the_shared_qmd_bar_contract() {
        assert!(validate_timeframe("1m").is_ok());
        assert!(validate_timeframe("2m").is_err());
    }
}
