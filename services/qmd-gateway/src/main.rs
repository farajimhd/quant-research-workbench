#![recursion_limit = "512"]

mod api;
mod bars;
mod clickhouse;
mod compact_event;
mod config;
mod event;
mod gapfill;
mod indicator_catalog;
mod indicators;
mod live_market_state;
mod maintenance;
mod massive;
mod metrics;
mod reference_tradability;
mod replay;
mod scanner;
mod session;
mod signal_catalog;
mod state;
mod timefmt;

use crate::api::{app, AppState};
use crate::bars::{spawn_bar_engines, BarClickHouseWriter, BarRow, SharedBarStore};
use crate::clickhouse::ClickHouseWriter;
use crate::compact_event::{
    CompactEventClickHouseWriter, CompactEventReferences, LiveCompactEvent, SharedCompactEventStore,
};
use crate::config::{load_env_files, GatewayConfig};
use crate::event::MarketEvent;
use crate::gapfill::{run_gap_fill_service, run_startup_maintenance};
use crate::indicators::{
    spawn_indicator_engines, IndicatorClickHouseWriter, IndicatorRow, SharedIndicatorStore,
};
use crate::live_market_state::{
    spawn_live_market_state_service, LiveSymbolMarketStateEvent, SharedLiveMarketStateStore,
};
use crate::maintenance::SharedMaintenanceState;
use crate::massive::{run_massive_ingest, MarketEventFanout};
use crate::metrics::SharedMetrics;
use crate::reference_tradability::{
    refresh_reference_tradability_once, spawn_reference_tradability_refresh,
    SharedReferenceTradabilityStore,
};
use crate::replay::run_replay_service;
use crate::scanner::{spawn_scanner_primitive_engine, ScannerPrimitive, SharedScannerStore};
use crate::session::is_streaming_phase;
use crate::state::SharedMarketState;
use chrono::Utc;
use std::net::SocketAddr;
use std::{error::Error, io};
use tokio::sync::{broadcast, mpsc};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let loaded_env_files = load_env_files();
    if !loaded_env_files.is_empty() {
        eprintln!(
            "Loaded .env files: {}",
            loaded_env_files
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join("; ")
        );
    }
    let config = GatewayConfig::from_env();
    preflight_config(&config).map_err(startup_error)?;
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
    let live_market_state = SharedLiveMarketStateStore::new(config.live_market_state_history_limit);
    let reference_tradability = SharedReferenceTradabilityStore::new(
        config.reference_tradability_enabled,
        config.reference_tradability_fail_closed,
    );
    let maintenance = SharedMaintenanceState::new();
    let compact_event_store =
        SharedCompactEventStore::new(config.compact_event_live_buffer_events_per_ticker);
    let (writer_sender, writer_receiver) =
        mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (compact_writer_sender, compact_writer_receiver) =
        mpsc::channel::<MarketEvent>(config.compact_event_channel_capacity);
    let (bar_writer_sender, bar_writer_receiver) =
        mpsc::channel::<BarRow>(config.bar_channel_capacity);
    let (indicator_writer_sender, indicator_writer_receiver) =
        mpsc::channel::<IndicatorRow>(config.indicator_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);
    let (compact_event_sender, _compact_event_receiver) =
        broadcast::channel::<LiveCompactEvent>(10_000);
    let (scanner_sender, _scanner_receiver) = broadcast::channel::<ScannerPrimitive>(10_000);
    let (live_market_state_sender, _live_market_state_receiver) =
        broadcast::channel::<LiveSymbolMarketStateEvent>(10_000);

    match refresh_reference_tradability_once(&config, &reference_tradability, &metrics).await {
        Ok(summary) => eprintln!(
            "QMD reference tradability loaded: enabled={} loaded={} symbols={} blocked={} universe_date={}",
            summary.enabled,
            summary.loaded,
            summary.symbols,
            summary.blocked,
            summary.latest_universe_date.as_deref().unwrap_or("-")
        ),
        Err(error) => {
            metrics.inc_reference_tradability_refresh_failure();
            eprintln!("QMD reference tradability initial load failed; emissions will follow fail_closed policy: {error}");
        }
    }
    spawn_reference_tradability_refresh(
        config.clone(),
        reference_tradability.clone(),
        metrics.clone(),
    );

    if config.persist_raw_events {
        let writer = ClickHouseWriter::new(config.clone());
        writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway raw event ClickHouse preflight failed: {error}"
            ))
        })?;
        tokio::spawn(writer.run(writer_receiver));
    } else {
        drop(writer_receiver);
        eprintln!(
            "Raw quote/trade ClickHouse persistence is disabled. Set QMD_PERSIST_RAW_EVENTS=true to enable it."
        );
    }
    if config.compact_events_enabled {
        let references = CompactEventReferences::load(&config.reference_dir).map_err(|error| {
            startup_error(format!(
                "qmd-gateway compact reference load failed: {error}"
            ))
        })?;
        let compact_writer = CompactEventClickHouseWriter::new(
            config.clone(),
            references,
            compact_event_sender.clone(),
            compact_event_store.clone(),
            reference_tradability.clone(),
            metrics.clone(),
        );
        compact_writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway compact event ClickHouse preflight failed: {error}"
            ))
        })?;
        tokio::spawn(compact_writer.run(compact_writer_receiver));
    } else {
        drop(compact_writer_receiver);
        eprintln!(
            "Compact event stream is disabled. Set QMD_COMPACT_EVENTS_ENABLED=true to enable it."
        );
    }
    let bar_writer = BarClickHouseWriter::new(config.clone());
    bar_writer.initialize().await.map_err(|error| {
        startup_error(format!(
            "qmd-gateway bar ClickHouse preflight failed: {error}"
        ))
    })?;
    let indicator_writer = IndicatorClickHouseWriter::new(config.clone());
    if config.persist_indicators {
        indicator_writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway indicator ClickHouse preflight failed: {error}"
            ))
        })?;
    }

    tokio::spawn(bar_writer.run(bar_writer_receiver));
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
        reference_tradability.clone(),
    );
    let live_market_state_router = spawn_live_market_state_service(
        config.clone(),
        live_market_state.clone(),
        metrics.clone(),
        live_market_state_sender.clone(),
        reference_tradability.clone(),
    );
    let bar_router = spawn_bar_engines(
        bars.clone(),
        config.bar_channel_capacity,
        Some(indicator_router.bar_sender()),
        Some(scanner_router.clone()),
        Some(live_market_state_router.clone()),
        bar_writer_sender,
        metrics.clone(),
    );

    let event_fanout = MarketEventFanout {
        state: market.clone(),
        writer_sender: if config.persist_raw_events {
            Some(writer_sender)
        } else {
            None
        },
        compact_writer_sender: if config.compact_events_enabled {
            Some(compact_writer_sender)
        } else {
            None
        },
        bar_router: bar_router.clone(),
        indicator_router: indicator_router.clone(),
        live_market_state_router: live_market_state_router.clone(),
        reference_tradability: reference_tradability.clone(),
        event_sender: event_sender.clone(),
        metrics: metrics.clone(),
    };

    let app = app(AppState {
        bars,
        compact_event_store: compact_event_store.clone(),
        compact_events: compact_event_sender,
        config: config.clone(),
        events: event_sender,
        indicators,
        live_market_state,
        live_market_state_events: live_market_state_sender,
        market: market.clone(),
        metrics: metrics.clone(),
        reference_tradability,
        maintenance: maintenance.clone(),
        scanner,
        scanner_events: scanner_sender,
    });

    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!("qmd-gateway API listening on {bind}; startup maintenance may still be running.");
    let server = tokio::spawn(async move {
        axum::serve(listener, app)
            .with_graceful_shutdown(async {
                let _ = tokio::signal::ctrl_c().await;
            })
            .await
    });

    if is_streaming_phase(Utc::now()) {
        tokio::spawn(run_massive_ingest(config.clone(), event_fanout.clone()));
        tokio::spawn(run_startup_maintenance(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
        ));
    } else {
        run_startup_maintenance(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
        )
        .await;
        tokio::spawn(run_massive_ingest(config.clone(), event_fanout.clone()));
    }
    if config.gap_fill_enabled {
        tokio::spawn(run_gap_fill_service(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
        ));
    }
    tokio::spawn(run_replay_service(
        config.clone(),
        metrics.clone(),
        market.clone(),
        bar_router.clone(),
        indicator_router.clone(),
    ));

    server.await??;
    Ok(())
}

fn preflight_config(config: &GatewayConfig) -> Result<(), String> {
    if config.massive_api_key.trim().is_empty() {
        return Err("MASSIVE_API_KEY is required before qmd-gateway starts".to_string());
    }
    if config.subscription_channels().is_empty() {
        return Err(
            "at least one Massive subscription channel is required before qmd-gateway starts"
                .to_string(),
        );
    }
    if config.clickhouse_url.trim().is_empty() {
        return Err("QMD_CLICKHOUSE_URL is required before qmd-gateway starts".to_string());
    }
    if config.clickhouse_user.trim().is_empty() {
        return Err("QMD_CLICKHOUSE_USER is required before qmd-gateway starts".to_string());
    }
    if config.compact_events_enabled && config.reference_dir.trim().is_empty() {
        return Err("QMD_REFERENCE_DIR is required when compact events are enabled".to_string());
    }
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
