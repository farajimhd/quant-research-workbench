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
use tokio::sync::mpsc;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = SecGatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let sec = SharedSecState::new(config.recent_history_limit);
    let (writer_sender, writer_receiver) = mpsc::channel::<SecGatewayMessage>(config.writer_channel_capacity);

    let writer = SecClickHouse::new(config.clone());
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
