mod api;
mod bars;
mod clickhouse;
mod config;
mod event;
mod gapfill;
mod massive;
mod session;
mod state;

use crate::api::{app, AppState};
use crate::bars::{run_bar_engine, BarClickHouseWriter, BarRow, SharedBarStore};
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
    let bars = SharedBarStore::new(config.bar_timeframes.clone(), config.bar_history_limit);
    let (writer_sender, writer_receiver) = mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (bar_event_sender, bar_event_receiver) = mpsc::channel::<MarketEvent>(config.bar_channel_capacity);
    let (bar_writer_sender, bar_writer_receiver) = mpsc::channel::<BarRow>(config.bar_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);

    let writer = ClickHouseWriter::new(config.clone());
    tokio::spawn(writer.run(writer_receiver));
    let bar_writer = BarClickHouseWriter::new(config.clone());
    tokio::spawn(bar_writer.run(bar_writer_receiver));
    tokio::spawn(run_bar_engine(
        bars.clone(),
        bar_event_receiver,
        bar_writer_sender,
    ));

    tokio::spawn(run_massive_ingest(
        config.clone(),
        market.clone(),
        writer_sender,
        bar_event_sender,
        event_sender.clone(),
    ));
    tokio::spawn(run_gap_fill_service(config.clone()));

    let app = app(AppState {
        bars,
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
