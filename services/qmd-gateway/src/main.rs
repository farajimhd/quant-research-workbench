#![recursion_limit = "512"]

use chrono::Utc;
use qmd_core::api::{app, AppState};
use qmd_core::bars::{spawn_bar_engines, SharedBarStore};
use qmd_core::clickhouse::ClickHouseWriter;
use qmd_core::compact_event::{
    CompactEventClickHouseWriter, CompactEventReferences, LiveCompactEvent, SharedCompactEventStore,
};
use qmd_core::config::{load_env_files, GatewayConfig};
use qmd_core::event::MarketEvent;
use qmd_core::gapfill::{run_gap_fill_service, run_startup_maintenance};
use qmd_core::indicators::{
    load_live_market_structure_references, spawn_indicator_engines, IndicatorClickHouseWriter,
    IndicatorRow, SharedIndicatorStore,
};
use qmd_core::intraday_bars::spawn_intraday_bar_service;
use qmd_core::live_market_state::{
    spawn_live_market_state_service, LiveSymbolMarketStateEvent, SharedLiveMarketStateStore,
};
use qmd_core::maintenance::SharedMaintenanceState;
use qmd_core::market_calendar::{run_market_calendar_refresh, MarketCalendarClient};
use qmd_core::market_products::{
    parse_resolution_us, ConditionClassifier, ProductCacheLimits, SharedMarketProductStore,
};
use qmd_core::massive::{run_massive_ingest, MarketEventFanout};
use qmd_core::metrics::SharedMetrics;
use qmd_core::scanner::{spawn_scanner_primitive_engine, ScannerPrimitive, SharedScannerStore};
use qmd_core::state::SharedMarketState;
use std::net::SocketAddr;
use std::{error::Error, io};
use tokio::sync::{broadcast, mpsc, watch};
use tokio::time::{sleep, timeout, Duration};

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
    metrics.register_lane("massive_feed", "Massive feed", "feed", true, true);
    metrics.register_lane(
        "compact_events",
        "q_live.events persistence",
        "writer",
        config.compact_events_enabled && config.persist_compact_events,
        config.compact_events_enabled && config.persist_compact_events,
    );
    metrics.register_lane(
        "intraday_bars",
        "Canonical intraday bars",
        "writer",
        true,
        true,
    );
    metrics.register_lane(
        "coverage_ledger",
        "Live coverage ledger",
        "coverage",
        true,
        true,
    );
    metrics.register_lane(
        "compact_audit",
        "Compact-event warning audit",
        "audit",
        config.compact_events_enabled && config.persist_compact_events,
        config.compact_events_enabled && config.persist_compact_events,
    );
    metrics.register_lane(
        "raw_events",
        "Raw event persistence",
        "writer",
        config.persist_raw_events,
        false,
    );
    metrics.register_lane(
        "indicators",
        "Indicator persistence",
        "writer",
        config.persist_indicators,
        false,
    );
    metrics.register_lane(
        "live_market_state",
        "Abnormal market-state persistence",
        "writer",
        config.live_market_state_enabled,
        config.live_market_state_enabled,
    );
    let compact_references = CompactEventReferences::load(&config)
        .await
        .map_err(|error| {
            startup_error(format!(
                "qmd-gateway market condition reference load failed: {error}"
            ))
        })?;
    let trade_aggregation_rules = compact_references
        .trade_aggregation_rules()
        .map_err(startup_error)?;
    let compact_event_decoder = compact_references.decoder();
    let market = SharedMarketState::new();
    let bars = SharedBarStore::new(
        config.bar_timeframes.clone(),
        config.bar_history_limit,
        config.bar_shard_count,
        trade_aggregation_rules.clone(),
    );
    let product_resolutions = config
        .intraday_bar_timeframes
        .iter()
        .filter_map(|value| parse_resolution_us(value))
        .collect::<Vec<_>>();
    let products = SharedMarketProductStore::new(
        product_resolutions,
        ProductCacheLimits {
            max_bytes: config.product_cache_max_bytes,
            max_partitions: config.product_cache_max_partitions,
            max_rows: config.product_cache_max_rows,
        },
        config.intraday_bar_shard_count,
        trade_aggregation_rules.clone(),
        ConditionClassifier::training_aligned(),
    );
    let market_structure_references = load_live_market_structure_references(&config, Utc::now())
        .await
        .unwrap_or_else(|error| {
            eprintln!("qmd daily market-structure references unavailable: {error}");
            Default::default()
        });
    let indicators = SharedIndicatorStore::new(
        config.indicator_history_limit,
        config.indicator_history_by_timeframe.clone(),
        config.tick_indicator_window_seconds,
        config.indicator_shard_count,
        trade_aggregation_rules.clone(),
        market_structure_references,
    );
    let reference_refresh_indicators = indicators.clone();
    let scanner = SharedScannerStore::new(config.scanner_primitive_history_limit);
    let live_market_state = SharedLiveMarketStateStore::new(config.live_market_state_history_limit);
    let maintenance = SharedMaintenanceState::new();
    let market_calendar = MarketCalendarClient::new(config.clone());
    market_calendar.refresh(Utc::now()).await;
    let market_calendar_handle = tokio::spawn(run_market_calendar_refresh(market_calendar.clone()));
    let compact_event_store =
        SharedCompactEventStore::new(config.compact_event_live_buffer_events_per_ticker);
    let (writer_sender, writer_receiver) =
        mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (compact_writer_sender, compact_writer_receiver) =
        mpsc::channel::<MarketEvent>(config.compact_event_channel_capacity);
    let (indicator_writer_sender, indicator_writer_receiver) =
        mpsc::channel::<IndicatorRow>(config.indicator_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);
    let (compact_event_sender, _compact_event_receiver) =
        broadcast::channel::<LiveCompactEvent>(10_000);
    let (scanner_sender, _scanner_receiver) = broadcast::channel::<ScannerPrimitive>(10_000);
    let (live_market_state_sender, _live_market_state_receiver) =
        broadcast::channel::<LiveSymbolMarketStateEvent>(10_000);
    let intraday_bar_service = spawn_intraday_bar_service(config.clone(), metrics.clone())
        .await
        .map_err(|error| {
            startup_error(format!(
                "qmd-gateway canonical intraday bar preflight failed: {error}"
            ))
        })?;

    let mut writer_handles = Vec::new();
    if config.persist_raw_events {
        let writer = ClickHouseWriter::new(config.clone(), metrics.clone());
        writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway raw event ClickHouse preflight failed: {error}"
            ))
        })?;
        metrics.set_lane_state(
            "raw_events",
            "healthy",
            "Raw quote/trade writer initialized; awaiting rows.",
        );
        writer_handles.push(tokio::spawn(writer.run(writer_receiver)));
    } else {
        drop(writer_receiver);
        eprintln!(
            "Raw quote/trade ClickHouse persistence is disabled. Set QMD_PERSIST_RAW_EVENTS=true to enable it."
        );
    }
    if config.compact_events_enabled {
        let compact_writer = CompactEventClickHouseWriter::new(
            config.clone(),
            compact_references,
            compact_event_sender.clone(),
            compact_event_store.clone(),
            metrics.clone(),
            intraday_bar_service.router.clone(),
            products.clone(),
        );
        compact_writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway compact event ClickHouse preflight failed: {error}"
            ))
        })?;
        if config.persist_compact_events {
            metrics.set_lane_state(
                "compact_events",
                "healthy",
                "q_live.events writer initialized; awaiting rows.",
            );
            metrics.set_lane_state(
                "compact_audit",
                "healthy",
                "Compact-event warning audit initialized; normal state is sparse.",
            );
        }
        writer_handles.push(tokio::spawn(compact_writer.run(compact_writer_receiver)));
    } else {
        drop(compact_writer_receiver);
        eprintln!(
            "Compact event stream is disabled. Set QMD_COMPACT_EVENTS_ENABLED=true to enable it."
        );
    }
    metrics.set_lane_state(
        "coverage_ledger",
        "healthy",
        "Live event and canonical intraday-bar coverage ledger initialized.",
    );
    let indicator_writer = IndicatorClickHouseWriter::new(config.clone(), metrics.clone());
    if config.persist_indicators {
        indicator_writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway indicator ClickHouse preflight failed: {error}"
            ))
        })?;
        metrics.set_lane_state(
            "indicators",
            "healthy",
            "Indicator writer initialized; awaiting rows.",
        );
    }

    writer_handles.push(tokio::spawn(
        indicator_writer.run(indicator_writer_receiver),
    ));
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
    let (live_market_state_router, live_market_state_task) = spawn_live_market_state_service(
        config.clone(),
        live_market_state.clone(),
        metrics.clone(),
        live_market_state_sender.clone(),
    );
    let bar_router = spawn_bar_engines(
        bars.clone(),
        config.bar_channel_capacity,
        Some(indicator_router.bar_sender()),
        Some(scanner_router.clone()),
        Some(live_market_state_router.clone()),
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
        event_sender: event_sender.clone(),
        metrics: metrics.clone(),
    };

    let (shutdown_sender, mut shutdown_receiver) = watch::channel(false);
    let app = app(AppState {
        bars,
        compact_event_decoder,
        compact_event_store: compact_event_store.clone(),
        compact_events: compact_event_sender,
        config: config.clone(),
        events: event_sender,
        indicators,
        live_market_state,
        live_market_state_events: live_market_state_sender,
        market: market.clone(),
        metrics: metrics.clone(),
        maintenance: maintenance.clone(),
        market_calendar: market_calendar.clone(),
        products,
        intraday_bars: intraday_bar_service.rows.clone(),
        scanner,
        scanner_events: scanner_sender,
        shutdown: shutdown_sender,
        trade_aggregation_rules,
    });

    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!("qmd-gateway API listening on {bind}; startup maintenance may still be running.");
    let server = tokio::spawn(async move {
        axum::serve(listener, app)
            .with_graceful_shutdown(async move {
                tokio::select! {
                    _ = tokio::signal::ctrl_c() => {}
                    _ = shutdown_receiver.changed() => {}
                }
            })
            .await
    });

    let active_collection_window = market_calendar
        .snapshot(Utc::now())
        .active_collection_window;
    let mut producer_handles = Vec::new();
    producer_handles.push(tokio::spawn(run_market_structure_reference_refresh(
        config.clone(),
        reference_refresh_indicators,
    )));
    if active_collection_window {
        producer_handles.push(tokio::spawn(run_massive_ingest(
            config.clone(),
            event_fanout.clone(),
        )));
        producer_handles.push(tokio::spawn(run_startup_maintenance(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
            market_calendar.clone(),
        )));
    } else {
        run_startup_maintenance(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
            market_calendar.clone(),
        )
        .await;
        producer_handles.push(tokio::spawn(run_massive_ingest(
            config.clone(),
            event_fanout.clone(),
        )));
    }
    if config.gap_fill_enabled {
        producer_handles.push(tokio::spawn(run_gap_fill_service(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
            market_calendar.clone(),
        )));
    }
    server.await??;
    eprintln!("QMD shutdown requested; stopping producers and draining writer batches.");
    market_calendar_handle.abort();
    for handle in &producer_handles {
        handle.abort();
    }
    for handle in producer_handles {
        let _ = handle.await;
    }
    drop(event_fanout);
    drop(bar_router);
    drop(indicator_router);
    drop(scanner_router);
    drop(live_market_state_router);
    writer_handles.push(live_market_state_task);
    writer_handles.extend(intraday_bar_service.into_tasks());
    match timeout(Duration::from_secs(15), async {
        let mut failures = Vec::new();
        for handle in writer_handles {
            if let Err(error) = handle.await {
                failures.push(error.to_string());
            }
        }
        if failures.is_empty() {
            Ok(())
        } else {
            Err(failures.join("; "))
        }
    })
    .await
    {
        Ok(Ok(())) => eprintln!("QMD writer queues drained; shutdown complete."),
        Ok(Err(error)) => {
            return Err(startup_error(format!(
                "QMD shutdown encountered writer task failures: {error}"
            )))
        }
        Err(_) => {
            return Err(startup_error(
                "QMD writer drain exceeded 15 seconds; runtime shutdown stopped remaining tasks.",
            ))
        }
    }
    Ok(())
}

async fn run_market_structure_reference_refresh(
    config: GatewayConfig,
    indicators: SharedIndicatorStore,
) {
    loop {
        sleep(Duration::from_secs(60 * 60)).await;
        match load_live_market_structure_references(&config, Utc::now()).await {
            Ok(references) => {
                let count = references.len();
                indicators
                    .replace_market_structure_references(references)
                    .await;
                eprintln!("qmd refreshed daily market-structure references for {count} symbols");
            }
            Err(error) => {
                eprintln!("qmd daily market-structure reference refresh failed: {error}")
            }
        }
    }
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
    if !config.compact_events_enabled || !config.persist_compact_events {
        return Err(
            "canonical intraday bars require QMD_COMPACT_EVENTS_ENABLED=true and QMD_PERSIST_COMPACT_EVENTS=true".to_string(),
        );
    }
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
