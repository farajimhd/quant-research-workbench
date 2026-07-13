mod api;
mod config;
mod source;

use crate::api::{app, AppState};
use crate::config::HistoricalGatewayConfig;
use crate::source::HistoricalEventSource;
use qmd_core::config::load_env_files;
use std::io;
use std::net::SocketAddr;

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
    let listener = tokio::net::TcpListener::bind(bind).await?;
    eprintln!(
        "qmd-history-gateway listening on {bind}; source={}.{}YYYY",
        config.clickhouse_database, config.table_prefix
    );
    axum::serve(listener, app(AppState { config, source }))
        .with_graceful_shutdown(async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await?;
    Ok(())
}

fn startup_error(message: impl Into<String>) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(io::Error::new(io::ErrorKind::Other, message.into()))
}
