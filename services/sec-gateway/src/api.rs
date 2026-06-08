use crate::config::SecGatewayConfig;
use crate::model::SecFilingSummary;
use crate::state::SharedSecState;
use axum::extract::{Query, State};
use axum::http::StatusCode;
use axum::routing::get;
use axum::{Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};
use tower_http::cors::CorsLayer;

#[derive(Clone)]
pub struct AppState {
    pub config: SecGatewayConfig,
    pub sec: SharedSecState,
}

#[derive(Deserialize)]
pub struct RecentQuery {
    pub limit: Option<usize>,
}

pub fn app(state: AppState) -> Router {
    Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .route("/sec/recent", get(recent))
        .layer(CorsLayer::permissive())
        .with_state(state)
}

async fn health() -> (StatusCode, Json<Value>) {
    (StatusCode::OK, Json(json!({"status": "ok"})))
}

async fn config(State(state): State<AppState>) -> Json<SecGatewayConfig> {
    Json(state.config)
}

async fn recent(State(state): State<AppState>, Query(query): Query<RecentQuery>) -> Json<Vec<SecFilingSummary>> {
    Json(state.sec.recent(query.limit.unwrap_or(100).min(1_000)).await)
}
