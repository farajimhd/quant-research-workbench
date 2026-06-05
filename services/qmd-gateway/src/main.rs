mod api;
mod bars;
mod clickhouse;
mod config;
mod event;
mod gapfill;
mod indicator_catalog;
mod indicators;
mod massive;
mod signal_catalog;
mod session;
mod state;

use crate::api::{app, AppState};
use crate::bars::{spawn_bar_engines, BarClickHouseWriter, BarRow, SharedBarStore};
use crate::clickhouse::ClickHouseWriter;
use crate::config::GatewayConfig;
use crate::event::MarketEvent;
use crate::gapfill::run_gap_fill_service;
use crate::indicators::{spawn_indicator_engines, IndicatorClickHouseWriter, IndicatorRow, SharedIndicatorStore};
use crate::massive::run_massive_ingest;
use crate::state::SharedMarketState;
use std::net::SocketAddr;
use tokio::sync::{broadcast, mpsc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = GatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let market = SharedMarketState::new();
    let bars = SharedBarStore::new(
        config.bar_timeframes.clone(),
        config.bar_history_limit,
        config.bar_shard_count,
    );
    let indicators = SharedIndicatorStore::new(
        config.indicator_history_limit,
        config.indicator_history_by_timeframe.clone(),
        config.tick_indicator_window_seconds,
        config.indicator_shard_count,
    );
    let (writer_sender, writer_receiver) = mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (bar_writer_sender, bar_writer_receiver) = mpsc::channel::<BarRow>(config.bar_channel_capacity);
    let (indicator_writer_sender, indicator_writer_receiver) =
        mpsc::channel::<IndicatorRow>(config.indicator_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);

    let writer = ClickHouseWriter::new(config.clone());
    tokio::spawn(writer.run(writer_receiver));
    let bar_writer = BarClickHouseWriter::new(config.clone());
    tokio::spawn(bar_writer.run(bar_writer_receiver));
    let indicator_writer = IndicatorClickHouseWriter::new(config.clone());
    tokio::spawn(indicator_writer.run(indicator_writer_receiver));
    let indicator_router = spawn_indicator_engines(
        indicators.clone(),
        config.indicator_channel_capacity,
        config.indicator_bar_channel_capacity,
        indicator_writer_sender,
    );
    let bar_router = spawn_bar_engines(
        bars.clone(),
        config.bar_channel_capacity,
        Some(indicator_router.bar_sender()),
        bar_writer_sender,
    );

    tokio::spawn(run_massive_ingest(
        config.clone(),
        market.clone(),
        writer_sender,
        bar_router,
        indicator_router,
        event_sender.clone(),
    ));
    tokio::spawn(run_gap_fill_service(config.clone()));

    let app = app(AppState {
        bars,
        config,
        events: event_sender,
        indicators,
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
