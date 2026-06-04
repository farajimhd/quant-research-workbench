use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::state::{ScannerSnapshot, SharedMarketState, StatusMetrics, SymbolSnapshot};
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::extract::{Path, Query, State};
use axum::response::IntoResponse;
use axum::routing::get;
use axum::{Json, Router};
use serde::{Deserialize, Serialize};
use std::sync::Arc;
use tokio::sync::broadcast;
use tokio::time::{interval, Duration};
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub config: GatewayConfig,
    pub events: broadcast::Sender<MarketEvent>,
    pub market: SharedMarketState,
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<usize>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    config: GatewayConfig,
    metrics: StatusMetrics,
    running: bool,
    status: String,
    subscriptions: Vec<String>,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/snapshot/scanner", get(scanner_snapshot))
        .route("/snapshot/ticker/{ticker}", get(ticker_snapshot))
        .route("/stream/events", get(event_stream))
        .route("/stream/scanner", get(scanner_stream))
        .route("/stream/ticker/{ticker}", get(ticker_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthPayload> {
    Json(HealthPayload {
        config: state.config.clone(),
        metrics: state.market.metrics().await,
        running: state.config.api_key_present,
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

async fn scanner_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<ScannerSnapshot> {
    Json(state.market.scanner_snapshot(query.limit.unwrap_or(250).min(5_000)).await)
}

async fn ticker_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
) -> Json<Option<SymbolSnapshot>> {
    Json(state.market.ticker_snapshot(&ticker).await)
}

async fn scanner_stream(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_scanner(socket, state).await;
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

async fn event_stream(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_events(socket, state).await;
    })
}

async fn stream_events(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.events.subscribe();
    loop {
        match receiver.recv().await {
            Ok(event) => match serde_json::to_string(&event) {
                Ok(text) if socket.send(Message::Text(text.into())).await.is_err() => break,
                Ok(_) => {}
                Err(error) => {
                    if socket.send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into())).await.is_err() {
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
            Ok(text) if socket.send(Message::Text(text.into())).await.is_err() => break,
            Ok(_) => {}
            Err(error) => {
                if socket.send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into())).await.is_err() {
                    break;
                }
            }
        }
    }
}

async fn stream_ticker(mut socket: WebSocket, state: Arc<AppState>, ticker: String) {
    let mut timer = interval(Duration::from_millis(state.config.ticker_broadcast_ms));
    loop {
        timer.tick().await;
        let snapshot = state.market.ticker_snapshot(&ticker).await;
        match serde_json::to_string(&snapshot) {
            Ok(text) if socket.send(Message::Text(text.into())).await.is_err() => break,
            Ok(_) => {}
            Err(error) => {
                if socket.send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into())).await.is_err() {
                    break;
                }
            }
        }
    }
}
