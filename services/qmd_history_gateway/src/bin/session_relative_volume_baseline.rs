//! Bounded research export using the same loader as the QMD RVOL endpoint.
use qmd_history_gateway::{config::HistoricalGatewayConfig, relative_volume, source::HistoricalEventSource};

#[tokio::main]
async fn main() -> Result<(), String> {
    let args: Vec<String> = std::env::args().skip(1).collect();
    if args.len() != 2 {
        return Err("Usage: session_relative_volume_baseline YYYY-MM-DD TICKER[,TICKER...] (JSON on stdout)".into());
    }
    let request = relative_volume::BaselineRequest {
        session_date: args[0].parse().map_err(|_| "Invalid session date")?,
        tickers: args[1].split(',').map(str::to_string).collect(),
    };
    relative_volume::validate_request(&request)?;
    qmd_core::config::load_env_files();
    let config = HistoricalGatewayConfig::from_env();
    config.validate()?;
    eprintln!("RVOL baseline: validating canonical source; active=1 failed=0");
    let source = HistoricalEventSource::initialize(config.clone()).await?;
    let response = relative_volume::load(&config, &source, request).await?;
    println!("{}", serde_json::to_string(&response).map_err(|e| e.to_string())?);
    eprintln!("RVOL baseline: completed=1 active=0 failed=0 events={}", response.event_count);
    Ok(())
}
