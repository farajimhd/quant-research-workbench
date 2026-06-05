use crate::config::NewsGatewayConfig;
use crate::metrics::{MetricsSnapshot, SharedMetrics};
use crate::model::{NewsArticleSummary, NewsSnapshot, TickerNewsSnapshot};
use crate::state::SharedNewsState;
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
    pub articles: broadcast::Sender<NewsArticleSummary>,
    pub config: NewsGatewayConfig,
    pub metrics: SharedMetrics,
    pub news: SharedNewsState,
}

#[derive(Debug, Deserialize)]
struct LimitQuery {
    limit: Option<usize>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    config: NewsGatewayConfig,
    metrics: MetricsSnapshot,
    running: bool,
    status: String,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/metrics", get(metrics_snapshot))
        .route("/snapshot/news/recent", get(recent_snapshot))
        .route("/snapshot/news/scanner", get(recent_snapshot))
        .route("/snapshot/news/ticker/{ticker}", get(ticker_snapshot))
        .route("/stream/news", get(news_stream))
        .route("/stream/news/scanner", get(news_stream))
        .route("/stream/news/ticker/{ticker}", get(ticker_stream))
        .layer(CorsLayer::permissive())
        .with_state(Arc::new(state))
}

async fn health(State(state): State<Arc<AppState>>) -> Json<HealthPayload> {
    Json(HealthPayload {
        config: state.config.clone(),
        metrics: state.metrics.snapshot(),
        running: state.config.api_key_present,
        status: if state.config.api_key_present {
            "running".to_string()
        } else {
            "api_only_missing_massive_key".to_string()
        },
    })
}

async fn config(State(state): State<Arc<AppState>>) -> Json<NewsGatewayConfig> {
    Json(state.config.clone())
}

async fn metrics_snapshot(State(state): State<Arc<AppState>>) -> Json<MetricsSnapshot> {
    Json(state.metrics.snapshot())
}

async fn recent_snapshot(
    State(state): State<Arc<AppState>>,
    Query(query): Query<LimitQuery>,
) -> Json<NewsSnapshot> {
    Json(state.news.recent_snapshot(query.limit.unwrap_or(250).min(5_000)).await)
}

async fn ticker_snapshot(
    State(state): State<Arc<AppState>>,
    Path(ticker): Path<String>,
    Query(query): Query<LimitQuery>,
) -> Json<TickerNewsSnapshot> {
    Json(
        state
            .news
            .ticker_snapshot(&ticker, query.limit.unwrap_or(100).min(1_000))
            .await,
    )
}

async fn news_stream(ws: WebSocketUpgrade, State(state): State<Arc<AppState>>) -> impl IntoResponse {
    ws.on_upgrade(move |socket| async move {
        stream_all_news(socket, state).await;
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

async fn stream_all_news(mut socket: WebSocket, state: Arc<AppState>) {
    let mut receiver = state.articles.subscribe();
    loop {
        match receiver.recv().await {
            Ok(article) => match serde_json::to_string(&article) {
                Ok(text) if socket.send(Message::Text(text.into())).await.is_err() => break,
                Ok(_) => {}
                Err(error) => {
                    if socket.send(Message::Text(format!(r#"{{"error":"{error}"}}"#).into())).await.is_err() {
                        break;
                    }
                }
            },
            Err(broadcast::error::RecvError::Lagged(count)) => {
                let warning = format!(r#"{{"warning":"news_stream_lagged","skipped":{count}}}"#);
                if socket.send(Message::Text(warning.into())).await.is_err() {
                    break;
                }
            }
            Err(broadcast::error::RecvError::Closed) => break,
        }
    }
}

async fn stream_ticker(mut socket: WebSocket, state: Arc<AppState>, ticker: String) {
    let mut timer = interval(Duration::from_millis(1_000));
    loop {
        timer.tick().await;
        let snapshot = state.news.ticker_snapshot(&ticker, 100).await;
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
