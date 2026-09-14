//! Complete canonical-data signal preparation, independent of the browser.
use chrono::{NaiveDate, Utc};
use chrono_tz::America::New_York;
use qmd_core::{config::load_env_files, intraday_bars::OrderedIntradayTradeBars,
    signal_stream::{HistoricalSqueezeReplay, SignalStreamConfigurationRequest}};
use qmd_history_gateway::{config::HistoricalGatewayConfig, source::{CanonicalSessionOrdinalRange, EventWindow, HistoricalEventSource}};
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{collections::{BTreeMap, HashSet, VecDeque}, fs, io::{BufWriter, Write}, path::{Path, PathBuf}, time::Instant};

#[derive(Deserialize)]
struct Request {
    configuration: SignalStreamConfigurationRequest,
    tickers: Vec<String>,
    maximum_price_exclusive: f64,
    population_authority: Value,
}

fn write_json(path: &Path, value: &Value) -> Result<(), String> {
    let temporary = path.with_extension("tmp");
    fs::write(&temporary, serde_json::to_vec_pretty(value).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
    fs::rename(temporary, path).map_err(|e| e.to_string())
}

#[tokio::main(flavor = "multi_thread", worker_threads = 2)]
async fn main() {
    if let Err(error) = run().await { eprintln!("Signal preparation failed: {error}"); std::process::exit(1); }
}

async fn run() -> Result<(), String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.len() != 2 { return Err("Usage: historical_squeeze_replay REQUEST.json OUTPUT_DIRECTORY".into()); }
    let root = PathBuf::from(std::env::var("TRADINGML_RUNTIME_ROOT").unwrap_or("D:/TradingML/runtimes".into()))
        .canonicalize().map_err(|e| e.to_string())?;
    let output = PathBuf::from(&args[1]);
    let parent = output.parent().ok_or("Output needs a parent")?.canonicalize().map_err(|e| e.to_string())?;
    if !parent.starts_with(&root) { return Err("Output must be under the operational runtime root".into()); }
    fs::create_dir_all(&output).map_err(|e| e.to_string())?;
    let bytes = fs::read(&args[0]).map_err(|e| e.to_string())?;
    let request_hash = format!("{:x}", Sha256::digest(&bytes));
    let request: Request = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if request.tickers.is_empty() || request.tickers.len() > 25_000 || !request.maximum_price_exclusive.is_finite()
        || request.maximum_price_exclusive <= 0.0 { return Err("Invalid population or price ceiling".into()); }
    if output.join("manifest.json").exists() { return Err("Certified signal output already exists; validate/reuse it or choose a new output".into()); }
    load_env_files();
    let config = HistoricalGatewayConfig::from_env();
    config.validate()?;
    if config.archive_clock_policy != "canonical_sip" { return Err("This producer requires explicit canonical_sip historical timing".into()); }
    let source = HistoricalEventSource::initialize(config).await?;
    let window = EventWindow { start: request.configuration.session_start_utc,
        end: request.configuration.session_end_utc, tickers: request.tickers.clone() };
    let revision = source.source_revision(&window).await?;
    if !revision.complete_for_history || !revision.request_complete { return Err("Canonical source window is incomplete".into()); }
    let stream_definitions = request.configuration.streams.clone();
    let day = window.start.with_timezone(&New_York).date_naive();
    if (window.end - chrono::Duration::microseconds(1)).with_timezone(&New_York).date_naive() != day {
        return Err("Signal preparation requires one New York session".into());
    }
    let ranges = source.canonical_session_ordinal_ranges(day, &window.tickers).await?;
    let present = ranges.iter().map(|r| r.ticker.clone()).collect::<HashSet<_>>();
    let absent = window.tickers.iter().filter(|t| !present.contains(*t)).cloned().collect::<Vec<_>>();
    let mut queue = VecDeque::from(ranges);
    let mut tasks = tokio::task::JoinSet::new();
    let mut active = BTreeMap::new();
    let mut results = BTreeMap::new();
    let clock = Instant::now();
    let mut heartbeat = tokio::time::interval(std::time::Duration::from_secs(10));
    let identity = json!({"request_sha256":request_hash,"source_revision":revision.token,
        "source_sha256":env!("QMD_HISTORY_SOURCE_SHA256")});
    let checkpoint_dir = output.join("ticker-checkpoints");
    fs::create_dir_all(&checkpoint_dir).map_err(|e| e.to_string())?;
    println!("Preparing Early Squeeze: {} stocks, {} with certified daily events, 8 bounded readers", window.tickers.len(), queue.len());
    // Every resumed unit is pinned to identical code, request and canonical authority.
    for entry in fs::read_dir(&checkpoint_dir).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if path.extension().and_then(|s| s.to_str()) != Some("json") { continue; }
        let saved: Value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?).map_err(|e| e.to_string())?;
        if saved["identity"] != identity { return Err("Signal checkpoint authority changed; use a new output directory".into()); }
        let result: TickerResult = serde_json::from_value(saved["result"].clone()).map_err(|e| e.to_string())?;
        if !present.contains(&result.ticker) || results.insert(result.ticker.clone(), result).is_some() {
            return Err("Unexpected or duplicate ticker checkpoint".into());
        }
    }
    queue.retain(|r| !results.contains_key(&r.ticker));
    loop {
        while tasks.len() < 8 {
            let Some(range) = queue.pop_front() else { break; };
            let ticker = range.ticker.clone();
            active.insert(ticker.clone(), Utc::now());
            let worker_source = source.clone();
            let configuration = request.configuration.clone();
            let ceiling = request.maximum_price_exclusive;
            tasks.spawn(async move { scan_ticker(worker_source, day, range, configuration, ceiling).await });
        }
        if tasks.is_empty() { break; }
        tokio::select! {
            result = tasks.join_next() => {
                let result = match result.unwrap() {
                    Ok(Ok(value)) => value,
                    other => { tasks.abort_all(); while tasks.join_next().await.is_some() {}
                        return Err(format!("Ticker reader failed: {other:?}")); }
                };
                active.remove(&result.ticker);
                let key = format!("{:x}", Sha256::digest(result.ticker.as_bytes()));
                write_json(&checkpoint_dir.join(format!("{key}.json")), &json!({"identity":identity,"result":result}))?;
                results.insert(result.ticker.clone(), result);
            }
            _ = heartbeat.tick() => {
                let admitted = results.values().filter(|r| r.occurrence.is_some()).count();
                let status = json!({"stage":"canonical_signal_preparation","completed":results.len(),
                    "total":present.len(),"queued":queue.len(),"active":active,"admitted":admitted,
                    "no_canonical_events":absent.len(),"elapsed_seconds":clock.elapsed().as_secs(),"updated_at":Utc::now()});
                write_json(&output.join("status.json"), &status)?;
                println!("Stocks {}/{} | active {} | queued {} | admitted {} | {}s",
                    results.len(), present.len(), active.len(), queue.len(), admitted, clock.elapsed().as_secs());
            }
            _ = tokio::signal::ctrl_c() => {
                tasks.abort_all(); while tasks.join_next().await.is_some() {}
                write_json(&output.join("status.json"), &json!({"stage":"interrupted","completed":results.len(),"restart":"rerun with identical authority"}))?;
                return Err("Interrupted; completed ticker checkpoints retained, no partial output certified".into());
            }
        }
    }
    let mut writer = BufWriter::new(fs::File::create(output.join("occurrences.jsonl.partial")).map_err(|e| e.to_string())?);
    let mut digest = Sha256::new();
    let mut admitted = HashSet::new();
    let mut duplicates = 0;
    let mut over_price = 0;
    let trades: u64 = results.values().map(|r| r.trades).sum();
    let candle_count: u64 = results.values().map(|r| r.candles).sum();
    let signals: u64 = results.values().map(|r| r.signals).sum();
    for result in results.values() {
        over_price += result.over_price;
        if let Some(event) = &result.occurrence {
            emit(event.clone(), request.maximum_price_exclusive, &mut admitted, &mut duplicates, &mut over_price, &mut writer, &mut digest)?;
        }
    }
    writer.flush().map_err(|e| e.to_string())?;
    writer.get_ref().sync_all().map_err(|e| e.to_string())?;
    drop(writer);
    if source.source_revision(&window).await?.token != revision.token { return Err("Canonical source changed during signal preparation".into()); }
    fs::rename(output.join("occurrences.jsonl.partial"), output.join("occurrences.jsonl")).map_err(|e| e.to_string())?;
    let manifest = json!({"schema_version":1,"authority":"qmd_canonical_sip_squeeze_replay_v1","complete":true,
        "source_sha256":env!("QMD_HISTORY_SOURCE_SHA256"),"timing_policy":"canonical_sip",
        "available_start":window.start,"available_end":window.end,"source_revision":revision,"request_sha256":request_hash,
        "stream_definitions":stream_definitions,
        "occurrences_sha256":format!("{:x}",digest.finalize()),"population_authority":request.population_authority,
        "reader":"certified_daily_ticker_ordinal_ranges","scanning_stops_after_first_admission":true,
        "completed_tickers":results.len(),"no_canonical_events":absent,
        "population_count":window.tickers.len(),"row_count":admitted.len(),"trades":trades,"completed_candles":candle_count,
        "signals":signals,"duplicate_signals_ignored":duplicates,"above_price_excluded":over_price,
        "maximum_price_exclusive":request.maximum_price_exclusive,"activation":"first_qualifying_signal_through_session_end",
        "elapsed_seconds":clock.elapsed().as_secs(),"finished_at":Utc::now()});
    write_json(&output.join("manifest.json"), &manifest)?;
    write_json(&output.join("status.json"), &json!({"stage":"completed","admitted":admitted.len(),"elapsed_seconds":clock.elapsed().as_secs()}))?;
    println!("Complete: {} stocks admitted from {} signals; {} duplicate signals ignored; {}s", admitted.len(), signals, duplicates, clock.elapsed().as_secs());
    Ok(())
}

fn emit(event: Value, ceiling: f64, admitted: &mut HashSet<String>, duplicates: &mut u64,
    over_price: &mut u64, writer: &mut BufWriter<fs::File>, digest: &mut Sha256) -> Result<(), String> {
    let ticker = event["ticker"].as_str().ok_or("Signal lacks ticker")?;
    if admitted.contains(ticker) { *duplicates += 1; return Ok(()); }
    let price = event["last_price"].as_f64().ok_or("Signal lacks price")?;
    if !price.is_finite() || price <= 0.0 { return Err("Signal has invalid price".into()); }
    if price >= ceiling { *over_price += 1; return Ok(()); }
    admitted.insert(ticker.to_string());
    let mut bytes = serde_json::to_vec(&event).map_err(|e| e.to_string())?;
    bytes.push(b'\n');
    writer.write_all(&bytes).map_err(|e| e.to_string())?;
    digest.update(bytes);
    Ok(())
}


#[derive(Debug, Serialize, Deserialize)]
struct TickerResult {
    ticker: String,
    occurrence: Option<Value>,
    trades: u64,
    candles: u64,
    signals: u64,
    over_price: u64,
}

async fn scan_ticker(source: HistoricalEventSource, day: NaiveDate, range: CanonicalSessionOrdinalRange,
    configuration: SignalStreamConfigurationRequest, ceiling: f64) -> Result<TickerResult, String> {
    let start = configuration.session_start_utc.timestamp_micros() as u64;
    let end = configuration.session_end_utc.timestamp_micros() as u64;
    let mut engine = HistoricalSqueezeReplay::new(configuration)?;
    let mut bars = OrderedIntradayTradeBars::new(source.trade_aggregation_rules());
    let mut stream = source.stream_indicator_ordinal_range(day, &range.ticker, range.first_ordinal, range.next_ordinal, 4096)?;
    let mut result = TickerResult {ticker:range.ticker.clone(), occurrence:None, trades:0, candles:0, signals:0, over_price:0};
    while let Some(batch) = stream.recv().await {
        for event in batch.map_err(|e| format!("{}: {e}", range.ticker))? {
            if event.sip_timestamp_us < start { continue; }
            if event.sip_timestamp_us >= end { break; }
            result.trades += 1;
            if let Some(bar) = bars.push(&event, source.market_event(&event))? {
                result.candles += 1;
                for occurrence in engine.observe(&bar)? {
                    if accept(&mut result, occurrence, ceiling)? { return Ok(result); }
                }
            }
        }
    }
    for bar in bars.finish() {
        result.candles += 1;
        for occurrence in engine.observe(&bar)? {
            if accept(&mut result, occurrence, ceiling)? { return Ok(result); }
        }
    }
    Ok(result)
}

fn accept(result: &mut TickerResult, event: Value, ceiling: f64) -> Result<bool, String> {
    result.signals += 1;
    let price = event["last_price"].as_f64().filter(|v| v.is_finite() && *v > 0.0).ok_or("Invalid signal price")?;
    if price >= ceiling { result.over_price += 1; return Ok(false); }
    result.occurrence = Some(event);
    Ok(true)
}
