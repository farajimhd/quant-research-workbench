mod api;
mod clickhouse;
mod config;
mod sec;
mod model;
mod state;

use crate::api::{app, AppState};
use crate::clickhouse::SecClickHouse;
use crate::config::SecGatewayConfig;
use crate::model::SecGatewayMessage;
use crate::sec::run_sec_feed_poller;
use crate::state::SharedSecState;
use std::net::SocketAddr;
use std::{error::Error, io};
use tokio::sync::mpsc;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = SecGatewayConfig::from_env();
    preflight_config(&config).map_err(startup_error)?;
    let bind: SocketAddr = config.bind.parse()?;
    let sec = SharedSecState::new(config.recent_history_limit);
    let (writer_sender, writer_receiver) = mpsc::channel::<SecGatewayMessage>(config.writer_channel_capacity);

    let writer = SecClickHouse::new(config.clone());
    writer
        .initialize()
        .await
        .map_err(|error| startup_error(format!("sec-gateway ClickHouse preflight failed: {error}")))?;
    tokio::spawn(writer.run(writer_receiver));
    tokio::spawn(run_sec_feed_poller(config.clone(), sec.clone(), writer_sender));

    let app = app(AppState { config, sec });
    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

fn preflight_config(config: &SecGatewayConfig) -> Result<(), String> {
    if config.user_agent().trim().is_empty() {
        return Err("SEC_USER_AGENT, NEWS_SEC_USER_AGENT, or SEC_EDGAR_USER_AGENT is required before sec-gateway starts".to_string());
    }
    if config.clickhouse_url.trim().is_empty() {
        return Err("SEC_CLICKHOUSE_URL or QMD_CLICKHOUSE_URL is required before sec-gateway starts".to_string());
    }
    if config.clickhouse_user.trim().is_empty() {
        return Err("SEC_CLICKHOUSE_USER or QMD_CLICKHOUSE_USER is required before sec-gateway starts".to_string());
    }
    if config.feed_url.trim().is_empty() {
        return Err("SEC_LATEST_FEED_URL is required before sec-gateway starts".to_string());
    }
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
