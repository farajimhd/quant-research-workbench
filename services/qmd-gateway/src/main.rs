mod api;
mod clickhouse;
mod config;
mod event;
mod gapfill;
mod massive;
mod session;
mod state;

use crate::api::{app, AppState};
use crate::clickhouse::ClickHouseWriter;
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::gapfill::run_gap_fill_service;
use crate::massive::run_massive_ingest;
use crate::state::SharedMarketState;
use std::net::SocketAddr;
use tokio::sync::{broadcast, mpsc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = GatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let market = SharedMarketState::new();
    let (writer_sender, writer_receiver) = mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);

    let writer = ClickHouseWriter::new(config.clone());
    tokio::spawn(writer.run(writer_receiver));

    tokio::spawn(run_massive_ingest(
        config.clone(),
        market.clone(),
        writer_sender,
        event_sender.clone(),
    ));
    tokio::spawn(run_gap_fill_service(config.clone()));

    let app = app(AppState {
        config,
        events: event_sender,
        market,
    });

    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
