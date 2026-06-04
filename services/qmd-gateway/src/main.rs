use axum::{extract::State, routing::get, Json, Router};
use serde::Serialize;
use std::{env, net::SocketAddr, sync::Arc};

#[derive(Clone, Debug, Serialize)]
struct GatewayConfig {
    bind: String,
    clickhouse_database: String,
    clickhouse_url: String,
    massive_ws_url: String,
    subscribe_all_symbols: bool,
    subscribe_quotes: bool,
    subscribe_trades: bool,
}

#[derive(Clone, Debug, Serialize)]
struct GatewayState {
    config: GatewayConfig,
    subscriptions: Vec<String>,
}

#[derive(Debug, Serialize)]
struct HealthPayload {
    clickhouse_database: String,
    clickhouse_url: String,
    massive_ws_url: String,
    running: bool,
    status: String,
    subscriptions: Vec<String>,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = GatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let state = Arc::new(GatewayState {
        subscriptions: subscription_channels(&config),
        config,
    });

    let app = Router::new()
        .route("/health", get(health))
        .route("/config", get(config))
        .with_state(state);

    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

async fn health(State(state): State<Arc<GatewayState>>) -> Json<HealthPayload> {
    Json(HealthPayload {
        clickhouse_database: state.config.clickhouse_database.clone(),
        clickhouse_url: state.config.clickhouse_url.clone(),
        massive_ws_url: state.config.massive_ws_url.clone(),
        running: true,
        status: "control_plane_ready".to_string(),
        subscriptions: state.subscriptions.clone(),
    })
}

async fn config(State(state): State<Arc<GatewayState>>) -> Json<GatewayConfig> {
    Json(state.config.clone())
}

impl GatewayConfig {
    fn from_env() -> Self {
        Self {
            bind: env_string("QMD_GATEWAY_BIND", "127.0.0.1:8795"),
            clickhouse_database: env_string("QMD_CLICKHOUSE_DATABASE", "q_live"),
            clickhouse_url: env_string("QMD_CLICKHOUSE_URL", "http://localhost:8123"),
            massive_ws_url: env_string("QMD_MASSIVE_WS_URL", "wss://socket.massive.com/stocks"),
            subscribe_all_symbols: env_bool("QMD_SUBSCRIBE_ALL_SYMBOLS", true),
            subscribe_quotes: env_bool("QMD_SUBSCRIBE_QUOTES", true),
            subscribe_trades: env_bool("QMD_SUBSCRIBE_TRADES", true),
        }
    }
}

fn subscription_channels(config: &GatewayConfig) -> Vec<String> {
    if config.subscribe_all_symbols {
        let mut channels = Vec::new();
        if config.subscribe_trades {
            channels.push("T.*".to_string());
        }
        if config.subscribe_quotes {
            channels.push("Q.*".to_string());
        }
        return channels;
    }
    Vec::new()
}

fn env_string(name: &str, default: &str) -> String {
    env::var(name)
        .ok()
        .map(|value| value.trim().to_string())
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| default.to_string())
}

fn env_bool(name: &str, default: bool) -> bool {
    match env::var(name).ok().map(|value| value.trim().to_ascii_lowercase()) {
        Some(value) if matches!(value.as_str(), "1" | "true" | "yes" | "on") => true,
        Some(value) if matches!(value.as_str(), "0" | "false" | "no" | "off") => false,
        _ => default,
    }
}
