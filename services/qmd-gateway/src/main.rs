#![recursion_limit = "512"]

use chrono::Utc;
use qmd_core::api::{app, AppState};
use qmd_core::bars::{spawn_bar_engines, SharedBarStore};
use qmd_core::clickhouse::ClickHouseWriter;
use qmd_core::compact_event::{
    CompactEventClickHouseWriter, CompactEventReferences, LiveCompactEvent, SharedCompactEventStore,
};
use qmd_core::computation_targets::SharedComputationTargets;
use qmd_core::config::{is_valid_qmd_host_role, load_env_files, GatewayConfig};
use qmd_core::event::MarketEvent;
use qmd_core::gapfill::{run_gap_fill_service, run_startup_maintenance, GapFillService};
use qmd_core::indicator_reconciliation::IndicatorReconciler;
use qmd_core::indicators::{
    load_live_market_structure_references, spawn_indicator_engines, IndicatorClickHouseWriter,
    IndicatorRow, SharedIndicatorStore,
};
use qmd_core::intraday_bars::{
    run_intraday_bar_reconciliation_service, spawn_intraday_bar_service,
};
use qmd_core::live_market_state::{
    spawn_live_market_state_service, LiveSymbolMarketStateEvent, SharedLiveMarketStateStore,
};
use qmd_core::maintenance::SharedMaintenanceState;
use qmd_core::market_calendar::{run_market_calendar_refresh, MarketCalendarClient};
use qmd_core::market_products::{
    parse_resolution_us, spawn_market_product_engines, ConditionClassifier, ProductCacheLimits,
    SharedMarketProductStore,
};
use qmd_core::massive::{run_canonical_event_fanout, run_massive_ingest, MarketEventFanout};
use qmd_core::metrics::SharedMetrics;
use qmd_core::scanner::{spawn_scanner_primitive_engine, MarketSignalDelta, SharedScannerStore};
use qmd_core::signal_stream::{spawn_all_market_squeeze_engine, SharedSignalStreamStore};
use qmd_core::state::{ScannerRowDelta, SharedMarketState};
use qmd_core::structure_focus::StructureFocusCoordinator;
use std::collections::HashMap;
use std::net::SocketAddr;
use std::path::Path;
use std::{error::Error, io};
use tokio::sync::{broadcast, mpsc, watch};
use tokio::time::{sleep, timeout, Duration};

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let loaded_env_files = load_env_files();
    if !loaded_env_files.is_empty() {
        println!(
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
        "canonical_events",
        "Normalized event computation fanout",
        "computation",
        config.compact_events_enabled,
        config.compact_events_enabled,
    );
    metrics.register_lane(
        "intraday_bars",
        "Canonical intraday bars",
        "writer",
        true,
        true,
    );
    metrics.register_lane(
        "intraday_repairs",
        "Deferred intraday repair execution",
        "repair",
        true,
        false,
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
    let computation_targets = SharedComputationTargets::default();
    let market = SharedMarketState::new(trade_aggregation_rules.clone());
    let bars = SharedBarStore::new_for_computational_funnel(
        config.bar_timeframes.clone(),
        config.bar_history_limit,
        config.bar_shard_count,
        trade_aggregation_rules.clone(),
        computation_targets.clone(),
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
    let product_router = spawn_market_product_engines(
        products.clone(),
        computation_targets.clone(),
        config.compact_event_channel_capacity,
        config.intraday_bar_shard_count,
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
    let signal_streams = SharedSignalStreamStore::new(config.clone())
        .await
        .map_err(|error| {
            startup_error(format!("qmd-gateway Signal Stream store failed: {error}"))
        })?;
    let live_market_state = SharedLiveMarketStateStore::new(config.live_market_state_history_limit);
    let maintenance = SharedMaintenanceState::new();
    let market_calendar = MarketCalendarClient::new(config.clone());
    market_calendar.refresh(Utc::now()).await;
    let market_calendar_handle = tokio::spawn(run_market_calendar_refresh(market_calendar.clone()));
    let compact_event_store = SharedCompactEventStore::new(
        config.compact_event_live_buffer_events_per_ticker,
        config.compact_event_live_buffer_events_total,
    );
    let (writer_sender, writer_receiver) =
        mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    let (compact_writer_sender, compact_writer_receiver) =
        mpsc::channel::<MarketEvent>(config.compact_event_channel_capacity);
    let compact_repair_capacity = (config.compact_event_channel_capacity / 10)
        .max(1_024)
        .min(25_000)
        .min(config.compact_event_channel_capacity);
    let (compact_repair_writer_sender, compact_repair_writer_receiver) =
        mpsc::channel::<MarketEvent>(compact_repair_capacity);
    let (indicator_writer_sender, indicator_writer_receiver) =
        mpsc::channel::<IndicatorRow>(config.indicator_channel_capacity);
    let (event_sender, _event_receiver) = broadcast::channel::<MarketEvent>(10_000);
    let (compact_event_sender, _compact_event_receiver) =
        broadcast::channel::<LiveCompactEvent>(10_000);
    let (canonical_event_sender, canonical_event_receiver) =
        mpsc::channel::<MarketEvent>(config.event_channel_capacity);
    metrics.set_lane_capacity("canonical_events", config.event_channel_capacity as u64);
    let (scanner_sender, _scanner_receiver) = broadcast::channel::<MarketSignalDelta>(10_000);
    let (scanner_delta_sender, _scanner_delta_receiver) =
        broadcast::channel::<ScannerRowDelta>(10_000);
    let (live_market_state_sender, _live_market_state_receiver) =
        broadcast::channel::<LiveSymbolMarketStateEvent>(10_000);
    let intraday_bar_service = spawn_intraday_bar_service(
        config.clone(),
        metrics.clone(),
        compact_event_decoder.clone(),
        trade_aggregation_rules.clone(),
    )
    .await
    .map_err(|error| {
        startup_error(format!(
            "qmd-gateway canonical intraday bar preflight failed: {error}"
        ))
    })?;
    let intraday_bar_reconciler = intraday_bar_service.reconciler.clone();

    let mut writer_handles = Vec::new();
    writer_handles.push(spawn_all_market_squeeze_engine(
        intraday_bar_service.rows.subscribe(),
        signal_streams.clone(),
        market.clone(),
    ));
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
        println!(
            "Raw quote/trade ClickHouse persistence is disabled. Set QMD_PERSIST_RAW_EVENTS=true to enable it."
        );
    }
    if config.compact_events_enabled {
        let compact_writer = CompactEventClickHouseWriter::new(
            config.clone(),
            compact_references.clone(),
            compact_event_sender.clone(),
            compact_event_store.clone(),
            metrics.clone(),
            intraday_bar_service.router.clone(),
            intraday_bar_service.durability.clone(),
            product_router,
            canonical_event_sender.clone(),
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
        writer_handles.push(tokio::spawn(
            compact_writer.run(compact_writer_receiver, compact_repair_writer_receiver),
        ));
    } else {
        drop(compact_writer_receiver);
        drop(compact_repair_writer_receiver);
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
    let structure_checkpoint_store = indicator_writer.clone();
    let structure_watermarks = HashMap::new();
    if config.persist_indicators || config.persist_structure_events {
        indicator_writer.initialize().await.map_err(|error| {
            startup_error(format!(
                "qmd-gateway indicator ClickHouse preflight failed: {error}"
            ))
        })?;
    }
    if config.persist_indicators {
        metrics.set_lane_state(
            "indicators",
            "healthy",
            "Indicator writer initialized; awaiting rows.",
        );
    }
    if config.persist_structure_events {
        metrics.set_lane_state(
            "structure_events",
            "healthy",
            "Canonical QMD structure-event writer initialized; awaiting confirmed events.",
        );
    }
    let indicator_reconciler = IndicatorReconciler::new(
        config.clone(),
        market_calendar.clone(),
        compact_event_decoder.clone(),
        indicators.clone(),
        trade_aggregation_rules.clone(),
        indicator_writer.clone(),
    );

    writer_handles.push(tokio::spawn(indicator_writer.run(
        indicator_writer_receiver,
        bars.clone(),
        structure_watermarks,
    )));
    let scanner_router = spawn_scanner_primitive_engine(
        scanner.clone(),
        config.scanner_primitive_channel_capacity,
        metrics.clone(),
        scanner_sender.clone(),
        signal_streams.clone(),
        market.clone(),
    );
    let indicator_router = spawn_indicator_engines(
        indicators.clone(),
        computation_targets.clone(),
        config.indicator_channel_capacity,
        config.indicator_bar_channel_capacity,
        indicator_writer_sender,
        scanner_router.clone(),
        metrics.clone(),
    );
    let (live_market_state_router, live_market_state_task) = spawn_live_market_state_service(
        config.clone(),
        live_market_state.clone(),
        metrics.clone(),
        live_market_state_sender.clone(),
    );
    let bar_router = spawn_bar_engines(
        bars.clone(),
        computation_targets.clone(),
        config.bar_channel_capacity,
        Some(indicator_router.bar_sender()),
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
        compact_repair_writer_sender: if config.compact_events_enabled {
            Some(compact_repair_writer_sender)
        } else {
            None
        },
        canonical_event_capacity: if config.compact_events_enabled {
            Some(canonical_event_sender.clone())
        } else {
            None
        },
        bar_router: bar_router.clone(),
        indicator_router: indicator_router.clone(),
        live_market_state_router: live_market_state_router.clone(),
        event_sender: event_sender.clone(),
        scanner_delta_sender: scanner_delta_sender.clone(),
        metrics: metrics.clone(),
    };
    let focused_gap_repair = GapFillService::new(
        config.clone(),
        event_fanout.clone(),
        maintenance.clone(),
        compact_event_store.clone(),
        market_calendar.clone(),
        compact_references.clone(),
    );
    let structure_focus = StructureFocusCoordinator::new(
        &config,
        bars.clone(),
        structure_checkpoint_store,
        focused_gap_repair,
    )
    .map_err(startup_error)?;
    let restored_inactive_focus =
        structure_focus
            .restore_inactive_registry()
            .await
            .map_err(|error| {
                startup_error(format!(
                    "qmd-gateway structure focus registry restore failed: {error}"
                ))
            })?;
    eprintln!(
        "Restored {} inactive Generic Structure focus checkpoints; {} remain blocked pending canonical-history rebuild.",
        restored_inactive_focus.active, restored_inactive_focus.blocked,
    );
    drop(canonical_event_sender);
    let mut canonical_fanout = event_fanout.clone();
    canonical_fanout.canonical_event_capacity = None;
    writer_handles.push(tokio::spawn(run_canonical_event_fanout(
        canonical_event_receiver,
        canonical_fanout,
    )));

    let (shutdown_sender, mut shutdown_receiver) = watch::channel(false);
    let structure_focus_reclaimer = structure_focus.clone();
    let structure_focus_advancer = structure_focus.clone();
    let structure_split_adjuster = structure_focus.clone();
    let structure_focus_targets = computation_targets.clone();
    let app = app(AppState {
        bars,
        compact_event_decoder,
        compact_event_store: compact_event_store.clone(),
        compact_events: compact_event_sender,
        computation_targets,
        config: config.clone(),
        events: event_sender,
        indicators,
        indicator_reconciler,
        live_market_state,
        live_market_state_events: live_market_state_sender,
        market: market.clone(),
        metrics: metrics.clone(),
        maintenance: maintenance.clone(),
        market_calendar: market_calendar.clone(),
        products,
        intraday_bars: intraday_bar_service.rows.clone(),
        scanner,
        scanner_deltas: scanner_delta_sender,
        scanner_events: scanner_sender,
        signal_streams,
        structure_focus,
        shutdown: shutdown_sender,
        trade_aggregation_rules,
    });

    let listener = tokio::net::TcpListener::bind(bind).await?;
    println!("qmd-gateway API listening on {bind}; startup maintenance may still be running.");
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
    producer_handles.push(tokio::spawn(async move {
        loop {
            sleep(Duration::from_secs(5)).await;
            if let Err(error) = structure_focus_reclaimer
                .persist_and_reclaim_unused(&structure_focus_targets)
                .await
            {
                eprintln!("QMD structure-state expiry reclaim deferred: {error}");
            }
        }
    }));
    producer_handles.push(tokio::spawn(async move {
        loop {
            sleep(Duration::from_secs(60)).await;
            match structure_focus_advancer.advance_inactive_due().await {
                Ok(result) => {
                    if !result.blocked.is_empty() {
                        eprintln!(
                            "QMD blocked {} non-retryable inactive Generic Structure checkpoints pending canonical-history rebuild: {}",
                            result.blocked.len(),
                            result.blocked.join(","),
                        );
                    }
                    if !result.advanced.is_empty() {
                        eprintln!(
                            "QMD advanced {} inactive Generic Structure checkpoints before retention.",
                            result.advanced.len()
                        );
                    }
                }
                Err(error) => {
                    eprintln!("QMD inactive structure checkpoint advancement deferred: {error}")
                }
            }
        }
    }));
    producer_handles.push(tokio::spawn(async move {
        loop {
            sleep(Duration::from_secs(30)).await;
            match structure_split_adjuster
                .apply_due_split_adjustments(Utc::now())
                .await
            {
                Ok(adjusted) if !adjusted.is_empty() => eprintln!(
                    "QMD applied and persisted {} due Generic Structure split adjustments: {}",
                    adjusted.len(),
                    adjusted.join(","),
                ),
                Ok(_) => {}
                Err(error) => {
                    eprintln!("QMD Generic Structure split adjustment deferred: {error}")
                }
            }
        }
    }));
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
            compact_references.clone(),
        )));
    } else {
        run_startup_maintenance(
            config.clone(),
            event_fanout.clone(),
            maintenance.clone(),
            compact_event_store.clone(),
            market_calendar.clone(),
            compact_references.clone(),
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
            compact_references,
        )));
    }
    if config.derived_reconciliation_enabled {
        producer_handles.push(tokio::spawn(run_intraday_bar_reconciliation_service(
            config.clone(),
            intraday_bar_reconciler,
            maintenance.clone(),
            market_calendar.clone(),
            metrics.clone(),
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
    if !is_valid_qmd_host_role(&config.qmd_host_role) {
        return Err(format!(
            "QMD_HOST_ROLE must be 'laptop' or 'workstation'; received '{}'",
            config.qmd_host_role
        ));
    }
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
    if !(config.qmd_history_gateway_url.starts_with("http://")
        || config.qmd_history_gateway_url.starts_with("https://"))
    {
        return Err("QMD_HISTORY_GATEWAY_URL must be an HTTP(S) URL".to_string());
    }
    if !config.compact_events_enabled || !config.persist_compact_events {
        return Err(
            "canonical intraday bars require QMD_COMPACT_EVENTS_ENABLED=true and QMD_PERSIST_COMPACT_EVENTS=true".to_string(),
        );
    }
    if !config.persist_structure_events {
        return Err(
            "focused Generic Structure continuity requires QMD_PERSIST_STRUCTURE_EVENTS=true"
                .to_string(),
        );
    }
    if config.permits_historical_flatfile_autorun() {
        let python = Path::new(&config.historical_pipeline_python);
        if !python.is_file() {
            return Err(format!(
                "workstation historical flatfile autorun requires QMD_HISTORICAL_PIPELINE_PYTHON to name an existing executable; received '{}'",
                config.historical_pipeline_python
            ));
        }
        let updater = config.historical_update_script_path();
        if !updater.is_file() {
            return Err(format!(
                "workstation historical flatfile autorun requires the updater script at {}",
                updater.display()
            ));
        }
        let updater_runtime = Path::new(&config.historical_update_runtime_root);
        if config.historical_update_runtime_root.trim().is_empty() || !updater_runtime.is_absolute()
        {
            return Err(
                "QMD_HISTORICAL_UPDATE_RUNTIME_ROOT must be an absolute external path for workstation historical flatfile autorun"
                    .to_string(),
            );
        }
        let repository_root = Path::new(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(Path::parent)
            .expect("qmd-gateway manifest has a repository parent");
        if updater_runtime.starts_with(repository_root) {
            return Err(format!(
                "QMD_HISTORICAL_UPDATE_RUNTIME_ROOT must remain outside the repository: {}",
                updater_runtime.display()
            ));
        }
    }
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
