mod api;
mod cache;
mod config;
mod scanner;
mod source;
mod structure_checkpoint;
mod watchlist_timeline;

use crate::api::{app, AppState};
use crate::cache::HistoricalDerivedCache;
use crate::config::HistoricalGatewayConfig;
use crate::scanner::HistoricalScannerDerivedCache;
use crate::source::HistoricalEventSource;
use crate::structure_checkpoint::HistoricalStructureSessionRegistry;
use qmd_core::config::load_env_files;
use std::io;
use std::net::SocketAddr;
use std::sync::Arc;
use tokio::sync::Semaphore;

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let loaded = load_env_files();
    if !loaded.is_empty() {
        eprintln!(
            "Loaded .env files: {}",
            loaded
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join("; ")
        );
    }
    let config = HistoricalGatewayConfig::from_env();
    config.validate().map_err(startup_error)?;
    let bind: SocketAddr = config.bind.parse()?;
    let source = HistoricalEventSource::initialize(config.clone())
        .await
        .map_err(|error| {
            startup_error(format!("historical ClickHouse preflight failed: {error}"))
        })?;
    let cache = HistoricalDerivedCache::new(config.clone(), source.clone());
    let scanner = HistoricalScannerDerivedCache::new(config.clone(), source.clone());
    let watchlist_materialization_permits = Arc::new(Semaphore::new(
        config.watchlist_max_concurrent_materializations,
    ));
    let structure_checkpoint_advancement_permits = Arc::new(Semaphore::new(
        config.structure_checkpoint_max_concurrent_advancements,
    ));
    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!(
        "qmd-history-gateway listening on {bind}; source={}.{}YYYY",
        config.clickhouse_database, config.table_prefix
    );
    axum::serve(
        listener,
        app(AppState {
            cache,
            config,
            scanner,
            source,
            structure_checkpoint_advancement_permits,
            structure_snapshot_sessions: HistoricalStructureSessionRegistry::new(1024),
            watchlist_materialization_permits,
        }),
    )
    .with_graceful_shutdown(async {
        let _ = tokio::signal::ctrl_c().await;
    })
    .await?;
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
