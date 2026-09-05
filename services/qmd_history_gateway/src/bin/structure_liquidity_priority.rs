//! Read-only priority report. Reported dollar turnover is a scheduling proxy,
//! not a spread/depth measurement or a strategy signal.
use chrono::NaiveDate;
use qmd_core::config::load_env_files;
use qmd_history_gateway::{config::HistoricalGatewayConfig, source::HistoricalEventSource};
use serde_json::json;

#[tokio::main]
async fn main() -> Result<(), String> {
    let args: Vec<_> = std::env::args().skip(1).collect();
    if args.len() < 2 || args.len() > 3 {
        return Err("usage: structure_liquidity_priority YYYY-MM-DD OUTPUT_JSON [MAX_MARKET_CAP_USD]".into());
    }
    let day = NaiveDate::parse_from_str(&args[0], "%Y-%m-%d").map_err(|e| e.to_string())?;
    let output = std::path::PathBuf::from(&args[1]);
    let root = std::path::Path::new(r"D:\TradingML\runtimes").canonicalize().map_err(|e| e.to_string())?;
    let parent = output.parent().ok_or("output parent missing")?.canonicalize().map_err(|e| e.to_string())?;
    if !parent.starts_with(root) { return Err("Output must be under D:\\TradingML\\runtimes".into()); }
    load_env_files();
    let source = HistoricalEventSource::initialize(HistoricalGatewayConfig::from_env()).await?;
    let maximum = args.get(2).map(|v| v.parse::<f64>().map_err(|e| e.to_string())).transpose()?;
    let evidence = source.structure_session_liquidity(day, maximum).await?;
    let rows = evidence["rows"].as_array().ok_or("ranking rows missing")?;
    if rows.len() < 10 {
        return Err("Fewer than ten tradable tickers with positive session turnover".into());
    }
    let result = json!({"schema_version":1,"session_date":day,"timezone":"America/New_York",
        "session_start":"04:00","session_end_exclusive":"20:00",
        "metric":"canonical_reported_trade_dollar_volume", "source":"market_sip_compact.events_YYYY",
        "scope":"point-in-time tradable universe; positive reported trades; includes extended-hours conditions",
        "priority_tickers":rows.iter().take(10).map(|r| r["ticker"].clone()).collect::<Vec<_>>(),
        "source_revision":evidence["source_revision"],
        "max_market_cap":evidence["max_market_cap"],"market_cap_as_of":evidence["market_cap_as_of"],
        "reference_sha256":evidence["reference_sha256"],"excluded_missing_cap":evidence["excluded_missing_cap"],"excluded_above_cap":evidence["excluded_above_cap"],
        "ranked_ticker_count":rows.len(),"top_ten":&rows[..10]});
    std::fs::write(&args[1], serde_json::to_vec_pretty(&result).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    println!("Priority session {day}, 04:00-20:00 ET; {} eligible tickers", rows.len());
    for (index, row) in rows.iter().take(10).enumerate() {
        println!("{:>2}. {:<8} ${:.2} million reported turnover", index + 1, row["ticker"].as_str().unwrap_or("?"), row["dollar_volume"].as_f64().unwrap_or(0.0) / 1_000_000.0);
    }
    println!("Report: {}", output.display());
    Ok(())
}
