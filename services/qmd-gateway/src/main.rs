mod api;
mod bars;
mod clickhouse;
mod config;
mod event;
mod gapfill;
mod indicator_catalog;
mod indicators;
mod massive;
mod metrics;
mod replay;
mod scanner;
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
use crate::metrics::SharedMetrics;
use crate::replay::run_replay_service;
use crate::scanner::{spawn_scanner_primitive_engine, ScannerPrimitive, SharedScannerStore};
use crate::state::SharedMarketState;
use std::net::SocketAddr;
use tokio::sync::{broadcast, mpsc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let config = GatewayConfig::from_env();
    let bind: SocketAddr = config.bind.parse()?;
    let metrics = SharedMetrics::new();
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
    let scanner = SharedScannerStore::new(config.scanner_primitive_history_limit);
    let (writer_sender, writer_receiver) = mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (bar_writer_sender, bar_writer_receiver) = mpsc::channel::<BarRow>(config.bar_channel_capacity);
    let (indicator_writer_sender, indicator_writer_receiver) =
        mpsc::channel::<IndicatorRow>(config.indicator_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);
    let (scanner_sender, _scanner_receiver) = broadcast::channel::<ScannerPrimitive>(10_000);

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
    let scanner_router = spawn_scanner_primitive_engine(
        scanner.clone(),
        config.scanner_primitive_channel_capacity,
        metrics.clone(),
        scanner_sender.clone(),
    );
    let bar_router = spawn_bar_engines(
        bars.clone(),
        config.bar_channel_capacity,
        Some(indicator_router.bar_sender()),
        Some(scanner_router.clone()),
        bar_writer_sender,
        metrics.clone(),
    );

    tokio::spawn(run_massive_ingest(
        config.clone(),
        market.clone(),
        writer_sender,
        bar_router.clone(),
        indicator_router.clone(),
        event_sender.clone(),
        metrics.clone(),
    ));
    tokio::spawn(run_gap_fill_service(config.clone(), metrics.clone()));
    tokio::spawn(run_replay_service(
        config.clone(),
        metrics.clone(),
        market.clone(),
        bar_router.clone(),
        indicator_router.clone(),
    ));

    let app = app(AppState {
        bars,
        config,
        events: event_sender,
        indicators,
        market,
        metrics,
        scanner,
        scanner_events: scanner_sender,
    });

    let listener = tokio::net::TcpListener::bind(bind).await?;
    axum::serve(listener, app)
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}
