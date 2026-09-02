use chrono::{DateTime, Duration as ChronoDuration, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::config::{load_env_files, GatewayConfig};
use qmd_core::generic_structure::GENERIC_STRUCTURE_ALGORITHM_VERSION;
use qmd_core::indicators::{DailyStructureCheckpoint, IndicatorClickHouseWriter};
use qmd_core::metrics::SharedMetrics;
use qmd_history_gateway::config::HistoricalGatewayConfig;
use qmd_history_gateway::source::{
    EventWindow, HistoricalEventSource, StructureEventCountEstimateRequest,
};
use qmd_history_gateway::structure_checkpoint::{
    advance_historical_structure_snapshot, rebuild_structure_checkpoint,
    StructureCheckpointAdvanceRequest, StructureCheckpointRebuildRequest,
    STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION, STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::env;
use std::fs;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::sync::{Mutex, Semaphore};
use tokio::task::JoinSet;

const BOOTSTRAP_BUCKETS: [u32; 7] = [90, 56, 28, 14, 7, 3, 1];

#[derive(Clone, Debug)]
struct Args {
    ticker_files: Vec<PathBuf>,
    start_date: NaiveDate,
    end_date: NaiveDate,
    runtime_dir: PathBuf,
    workers: usize,
    lookback_days: i64,
    event_budget: u64,
    event_limit: usize,
    plan_only: bool,
}

#[derive(Clone, Debug, Serialize)]
struct TickerPlan {
    ticker: String,
    rebuild_start: DateTime<Utc>,
    sessions: Vec<NaiveDate>,
}

#[derive(Clone, Debug, Default, Serialize)]
struct Counts {
    active: usize,
    blocked: usize,
    completed: usize,
    failed: usize,
    finished: usize,
    queued: usize,
    skipped: usize,
    unavailable: usize,
}

#[derive(Clone, Debug, Serialize)]
struct Issue {
    ticker: String,
    session_date: Option<NaiveDate>,
    error: String,
}

#[derive(Clone, Debug, Serialize)]
struct Progress {
    schema_version: u16,
    status: String,
    updated_at: DateTime<Utc>,
    algorithm_version: u16,
    start_date: NaiveDate,
    end_date: NaiveDate,
    ticker_count: usize,
    total_units: usize,
    counts: Counts,
    active: BTreeMap<String, NaiveDate>,
    issues: Vec<Issue>,
}

struct ProgressWriter {
    path: PathBuf,
    inner: Mutex<Progress>,
}

impl ProgressWriter {
    fn new(
        path: PathBuf,
        start_date: NaiveDate,
        end_date: NaiveDate,
        plans: &[TickerPlan],
    ) -> Self {
        let total_units = plans.iter().map(|plan| plan.sessions.len()).sum();
        Self {
            path,
            inner: Mutex::new(Progress {
                schema_version: 2,
                status: "running".to_string(),
                updated_at: Utc::now(),
                algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
                start_date,
                end_date,
                ticker_count: plans.len(),
                total_units,
                counts: Counts {
                    queued: total_units,
                    ..Counts::default()
                },
                active: BTreeMap::new(),
                issues: Vec::new(),
            }),
        }
    }

    async fn activate(&self, ticker: &str, session_date: NaiveDate) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.active.insert(ticker.to_string(), session_date);
        progress.counts.active = progress.active.len();
        self.write_locked(&mut progress)
    }

    async fn finish_unit(&self, ticker: &str, outcome: &str) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.active.remove(ticker);
        progress.counts.active = progress.active.len();
        progress.counts.finished = progress.counts.finished.saturating_add(1);
        progress.counts.queued = progress.total_units.saturating_sub(
            progress
                .counts
                .finished
                .saturating_add(progress.counts.active),
        );
        match outcome {
            "completed" => progress.counts.completed += 1,
            "skipped" => progress.counts.skipped += 1,
            "unavailable" => progress.counts.unavailable += 1,
            _ => progress.counts.failed += 1,
        }
        self.write_locked(&mut progress)
    }

    async fn fail_ticker(
        &self,
        ticker: &str,
        session_date: Option<NaiveDate>,
        blocked: usize,
        error: String,
    ) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.active.remove(ticker);
        progress.counts.active = progress.active.len();
        progress.counts.failed += 1;
        progress.counts.blocked = progress.counts.blocked.saturating_add(blocked);
        progress.counts.finished = progress.counts.finished.saturating_add(1 + blocked);
        progress.counts.queued = progress.total_units.saturating_sub(
            progress
                .counts
                .finished
                .saturating_add(progress.counts.active),
        );
        progress.issues.push(Issue {
            ticker: ticker.to_string(),
            session_date,
            error,
        });
        if progress.issues.len() > 100 {
            progress.issues.remove(0);
        }
        self.write_locked(&mut progress)
    }

    async fn complete(&self) -> Result<Progress, String> {
        let mut progress = self.inner.lock().await;
        progress.status = if progress.counts.failed == 0 {
            "completed".to_string()
        } else {
            "failed".to_string()
        };
        self.write_locked(&mut progress)?;
        Ok(progress.clone())
    }

    fn write_locked(&self, progress: &mut Progress) -> Result<(), String> {
        progress.updated_at = Utc::now();
        let bytes = serde_json::to_vec_pretty(progress)
            .map_err(|error| format!("failed to encode campaign progress: {error}"))?;
        let temporary = self.path.with_extension("json.tmp");
        fs::write(&temporary, bytes)
            .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
        fs::rename(&temporary, &self.path)
            .map_err(|error| format!("failed to publish {}: {error}", self.path.display()))
    }
}

#[derive(Clone, Debug)]
struct DayResult {
    status: &'static str,
    event_count: u64,
    advanced_event_count: u64,
    cursor: u64,
}

#[tokio::main]
async fn main() -> Result<(), Box<dyn std::error::Error + Send + Sync>> {
    let args = parse_args().map_err(io_error)?;
    let loaded = load_env_files();
    if !loaded.is_empty() {
        eprintln!(
            "Loaded environment files: {}",
            loaded
                .iter()
                .map(|path| path.display().to_string())
                .collect::<Vec<_>>()
                .join("; ")
        );
    }
    fs::create_dir_all(&args.runtime_dir)?;
    let tickers = load_tickers(&args.ticker_files).map_err(io_error)?;
    if tickers.is_empty() {
        return Err(io_error("ticker universe is empty"));
    }

    let history_config = HistoricalGatewayConfig::from_env();
    history_config.validate().map_err(io_error)?;
    let source = HistoricalEventSource::initialize(history_config.clone())
        .await
        .map_err(io_error)?;
    let gateway_config = GatewayConfig::from_env();
    let writer = IndicatorClickHouseWriter::new(gateway_config, SharedMetrics::new());
    let plans = build_plans(&args, &source, &tickers)
        .await
        .map_err(io_error)?;
    let plan_path = args.runtime_dir.join("campaign-plan.json");
    fs::write(
        &plan_path,
        serde_json::to_vec_pretty(&plans)
            .map_err(|error| io_error(format!("failed to encode campaign plan: {error}")))?,
    )?;
    if args.plan_only {
        println!(
            "Validated Campaign v2 plan: tickers={} units={} plan={}",
            plans.len(),
            plans.iter().map(|plan| plan.sessions.len()).sum::<usize>(),
            plan_path.display()
        );
        return Ok(());
    }
    writer.initialize().await.map_err(io_error)?;
    let progress_path = args.runtime_dir.join("campaign-status.json");
    let progress = Arc::new(ProgressWriter::new(
        progress_path.clone(),
        args.start_date,
        args.end_date,
        &plans,
    ));
    {
        let mut initial = progress.inner.lock().await;
        progress.write_locked(&mut initial).map_err(io_error)?;
    }

    println!(
        "Structural Checkpoint Campaign v2: tickers={} units={} workers={} algorithm=v{} status={}",
        plans.len(),
        plans.iter().map(|plan| plan.sessions.len()).sum::<usize>(),
        args.workers.min(plans.len()),
        GENERIC_STRUCTURE_ALGORITHM_VERSION,
        progress_path.display()
    );

    let permits = Arc::new(Semaphore::new(args.workers.min(plans.len()).max(1)));
    let mut tasks = JoinSet::new();
    for plan in plans {
        let permit = permits.clone().acquire_owned().await?;
        let source = source.clone();
        let writer = writer.clone();
        let config = history_config.clone();
        let progress = progress.clone();
        let event_limit = args.event_limit;
        tasks.spawn(async move {
            let _permit = permit;
            run_ticker(&config, &source, &writer, &progress, plan, event_limit).await
        });
    }

    let mut task_failed = false;
    while let Some(result) = tasks.join_next().await {
        match result {
            Ok(Ok(())) => {}
            Ok(Err(error)) => {
                task_failed = true;
                eprintln!("ticker worker failed: {error}");
            }
            Err(error) => {
                task_failed = true;
                eprintln!("ticker worker panicked or was cancelled: {error}");
            }
        }
    }
    let final_progress = progress.complete().await.map_err(io_error)?;
    println!(
        "Campaign {}: completed={} already_current={} unavailable={} failed={} blocked={}",
        final_progress.status,
        final_progress.counts.completed,
        final_progress.counts.skipped,
        final_progress.counts.unavailable,
        final_progress.counts.failed,
        final_progress.counts.blocked,
    );
    if task_failed || final_progress.counts.failed > 0 {
        std::process::exit(1);
    }
    Ok(())
}

async fn run_ticker(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    writer: &IndicatorClickHouseWriter,
    progress: &ProgressWriter,
    plan: TickerPlan,
    event_limit: usize,
) -> Result<(), String> {
    for (index, session_date) in plan.sessions.iter().copied().enumerate() {
        progress.activate(&plan.ticker, session_date).await?;
        match build_day(
            config,
            source,
            writer,
            &plan.ticker,
            session_date,
            plan.rebuild_start,
            event_limit,
        )
        .await
        {
            Ok(result) => {
                println!(
                    "{} {} {} events={} advanced={} cursor={}",
                    result.status,
                    plan.ticker,
                    session_date,
                    result.event_count,
                    result.advanced_event_count,
                    result.cursor
                );
                progress.finish_unit(&plan.ticker, result.status).await?;
            }
            Err(error) if no_history_error(&error) => {
                println!("unavailable {} {}: {}", plan.ticker, session_date, error);
                progress.finish_unit(&plan.ticker, "unavailable").await?;
            }
            Err(error) => {
                let blocked = plan.sessions.len().saturating_sub(index + 1);
                progress
                    .fail_ticker(&plan.ticker, Some(session_date), blocked, error.clone())
                    .await?;
                return Err(format!("{} {}: {error}", plan.ticker, session_date));
            }
        }
    }
    Ok(())
}

async fn build_day(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    writer: &IndicatorClickHouseWriter,
    ticker: &str,
    session_date: NaiveDate,
    rebuild_start: DateTime<Utc>,
    event_limit: usize,
) -> Result<DayResult, String> {
    let authority_end = session_end(session_date)?;
    let as_of = authority_end - ChronoDuration::microseconds(1);
    let next_date = session_date
        .succ_opt()
        .ok_or_else(|| "checkpoint session date overflow".to_string())?;
    if let Some(existing) = writer
        .load_daily_structure_checkpoint_before(ticker, next_date)
        .await?
        .filter(|row| row.session_date == session_date)
        .filter(|row| row.authority_start <= rebuild_start)
    {
        if checkpoint_revision_matches(source, &existing, authority_end).await? {
            return Ok(DayResult {
                status: "skipped",
                event_count: 0,
                advanced_event_count: 0,
                cursor: existing.last_arrival_sequence,
            });
        }
    }

    let seed = writer
        .load_daily_structure_checkpoint_before(ticker, session_date)
        .await?
        .filter(|row| row.authority_start <= rebuild_start);
    let seed = if let Some(seed) = seed {
        let seed_end = session_end(seed.session_date)?;
        checkpoint_revision_matches(source, &seed, seed_end)
            .await?
            .then_some(seed)
    } else {
        None
    };

    let (checkpoint, event_count, advanced_event_count, authority_start) = if let Some(seed) = seed
    {
        let advanced = advance_historical_structure_snapshot(
            config,
            source,
            StructureCheckpointAdvanceRequest {
                schema_version: STRUCTURE_CHECKPOINT_ADVANCEMENT_SCHEMA_VERSION,
                checkpoint: seed.checkpoint,
                as_of,
                expected_source_plan_hash: None,
                event_limit: Some(event_limit),
            },
        )
        .await?;
        if !advanced.complete
            || advanced.source_revision_before.token != advanced.source_revision_after.token
        {
            return Err("historical checkpoint advancement was source-inconsistent".to_string());
        }
        (
            advanced.checkpoint,
            advanced.event_count,
            advanced.advanced_event_count,
            seed.authority_start,
        )
    } else {
        let rebuilt = rebuild_structure_checkpoint(
            config,
            source,
            StructureCheckpointRebuildRequest {
                schema_version: STRUCTURE_CHECKPOINT_REBUILD_SCHEMA_VERSION,
                ticker: ticker.to_string(),
                start: rebuild_start,
                as_of,
                expected_source_plan_hash: None,
                event_limit: Some(event_limit),
            },
        )
        .await?;
        if !rebuilt.complete
            || rebuilt.source_revision_before.token != rebuilt.source_revision_after.token
        {
            return Err("historical checkpoint rebuild was source-inconsistent".to_string());
        }
        (
            rebuilt.checkpoint,
            rebuilt.event_count,
            rebuilt.advanced_event_count,
            rebuild_start,
        )
    };

    let checkpoint_at = checkpoint
        .updated_at
        .ok_or_else(|| "calculated checkpoint has no exact event time".to_string())?;
    if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION
        || checkpoint.sym.to_ascii_uppercase() != ticker
        || checkpoint.last_arrival_sequence == 0
    {
        return Err("calculated checkpoint identity is invalid".to_string());
    }
    let revision = source
        .source_revision(&EventWindow {
            start: authority_start,
            end: authority_end,
            tickers: vec![ticker.to_string()],
        })
        .await?;
    if !revision.complete_for_history
        || !revision.request_complete
        || revision.source_plan_hash.trim().is_empty()
        || revision.token.trim().is_empty()
    {
        return Err("daily checkpoint authority is incomplete".to_string());
    }
    writer
        .persist_daily_structure_checkpoint(&DailyStructureCheckpoint {
            session_date,
            algorithm_version: checkpoint.algorithm_version,
            sym: ticker.to_string(),
            authority_start,
            checkpoint_at,
            last_arrival_sequence: checkpoint.last_arrival_sequence,
            source_plan_hash: revision.source_plan_hash,
            source_revision_token: revision.token,
            source_complete: true,
            built_at: Utc::now(),
            checkpoint: checkpoint.clone(),
        })
        .await?;
    Ok(DayResult {
        status: "completed",
        event_count,
        advanced_event_count,
        cursor: checkpoint.last_arrival_sequence,
    })
}

async fn checkpoint_revision_matches(
    source: &HistoricalEventSource,
    checkpoint: &DailyStructureCheckpoint,
    authority_end: DateTime<Utc>,
) -> Result<bool, String> {
    let current = source
        .source_revision(&EventWindow {
            start: checkpoint.authority_start,
            end: authority_end,
            tickers: vec![checkpoint.sym.clone()],
        })
        .await?;
    Ok(current.complete_for_history
        && current.request_complete
        && current.source_plan_hash == checkpoint.source_plan_hash
        && current.token == checkpoint.source_revision_token)
}

async fn build_plans(
    args: &Args,
    source: &HistoricalEventSource,
    tickers: &[String],
) -> Result<Vec<TickerPlan>, String> {
    let planning_start = args.start_date - ChronoDuration::days(args.lookback_days);
    let planning_end = args
        .end_date
        .succ_opt()
        .ok_or_else(|| "campaign end date overflow".to_string())?;
    let mut estimates = BTreeMap::new();
    for batch in tickers.chunks(25_000) {
        let response = source
            .structure_event_count_estimates(StructureEventCountEstimateRequest {
                as_of: Utc::now(),
                start_date: planning_start,
                end_date: planning_end,
                tickers: batch.to_vec(),
            })
            .await?;
        for row in response.estimates {
            estimates.insert(row.ticker, (row.total_events, row.max_session_events));
        }
    }
    let completed_sessions = source
        .completed_session_dates_between(planning_start, args.end_date, Utc::now())
        .await?
        .into_iter()
        .collect::<BTreeSet<_>>();
    let target_sessions = completed_sessions
        .range(args.start_date..=args.end_date)
        .copied()
        .collect::<Vec<_>>();
    let rebuild_start = New_York
        .from_local_datetime(
            &planning_start.and_time(
                NaiveTime::from_hms_opt(4, 0, 0)
                    .ok_or_else(|| "invalid campaign start time".to_string())?,
            ),
        )
        .single()
        .ok_or_else(|| "invalid New York campaign start".to_string())?
        .with_timezone(&Utc);
    Ok(tickers
        .iter()
        .map(|ticker| {
            let (total, maximum) = estimates.get(ticker).copied().unwrap_or_default();
            let bootstrap_days = bootstrap_days(total, maximum, args.event_budget);
            let mut sessions = target_sessions.iter().copied().collect::<BTreeSet<_>>();
            if bootstrap_days > 0 && planning_start < args.start_date {
                let mut cursor = planning_start + ChronoDuration::days(bootstrap_days as i64);
                while cursor < args.start_date {
                    if let Some(session) =
                        previous_session(&completed_sessions, cursor, planning_start)
                    {
                        sessions.insert(session);
                    }
                    cursor += ChronoDuration::days(bootstrap_days as i64);
                }
                if let Some(session) = previous_session(
                    &completed_sessions,
                    args.start_date - ChronoDuration::days(1),
                    planning_start,
                ) {
                    sessions.insert(session);
                }
            }
            TickerPlan {
                ticker: ticker.clone(),
                rebuild_start,
                sessions: sessions.into_iter().collect(),
            }
        })
        .collect())
}

fn bootstrap_days(total: u64, maximum_session: u64, event_budget: u64) -> u32 {
    if total <= event_budget || maximum_session == 0 {
        return 0;
    }
    let safe_sessions = (event_budget / maximum_session).max(1);
    let safe_calendar_days = (safe_sessions * 7 / 5).max(1);
    BOOTSTRAP_BUCKETS
        .into_iter()
        .find(|days| u64::from(*days) <= safe_calendar_days)
        .unwrap_or(1)
}

fn previous_session(
    sessions: &BTreeSet<NaiveDate>,
    mut date: NaiveDate,
    lower_bound: NaiveDate,
) -> Option<NaiveDate> {
    while date >= lower_bound {
        if sessions.contains(&date) {
            return Some(date);
        }
        date = date.pred_opt()?;
    }
    None
}

fn session_end(session_date: NaiveDate) -> Result<DateTime<Utc>, String> {
    New_York
        .from_local_datetime(
            &session_date.and_time(
                NaiveTime::from_hms_opt(20, 0, 0)
                    .ok_or_else(|| "invalid checkpoint session time".to_string())?,
            ),
        )
        .single()
        .map(|value| value.with_timezone(&Utc))
        .ok_or_else(|| "invalid New York checkpoint session boundary".to_string())
}

fn no_history_error(error: &str) -> bool {
    error.contains("found no canonical events")
}

fn load_tickers(paths: &[PathBuf]) -> Result<Vec<String>, String> {
    let mut tickers = BTreeSet::new();
    for path in paths {
        let text = fs::read_to_string(path)
            .map_err(|error| format!("failed to read {}: {error}", path.display()))?;
        match serde_json::from_str::<Value>(&text) {
            Ok(value) => collect_json_tickers(&value, &mut tickers)?,
            Err(_) => {
                for line in text.lines() {
                    insert_ticker(line, &mut tickers)?;
                }
            }
        }
    }
    Ok(tickers.into_iter().collect())
}

fn collect_json_tickers(value: &Value, tickers: &mut BTreeSet<String>) -> Result<(), String> {
    match value {
        Value::Array(rows) => {
            for row in rows {
                collect_json_tickers(row, tickers)?;
            }
        }
        Value::Object(row) => {
            if let Some(value) = row
                .get("symbol")
                .or_else(|| row.get("ticker"))
                .or_else(|| row.get("sym"))
                .and_then(Value::as_str)
            {
                insert_ticker(value, tickers)?;
            } else if let Some(rows) = row.get("rows").or_else(|| row.get("tickers")) {
                collect_json_tickers(rows, tickers)?;
            }
        }
        Value::String(value) => {
            insert_ticker(value, tickers)?;
        }
        _ => {}
    }
    Ok(())
}

fn insert_ticker(value: &str, tickers: &mut BTreeSet<String>) -> Result<(), String> {
    let ticker = value.trim().to_ascii_uppercase();
    if ticker.is_empty() {
        return Ok(());
    }
    if ticker.len() > 32
        || !ticker
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
    {
        return Err(format!("invalid ticker in universe: {value:?}"));
    }
    tickers.insert(ticker);
    Ok(())
}

fn parse_args() -> Result<Args, String> {
    let mut values = env::args().skip(1);
    let mut ticker_files = Vec::new();
    let mut start_date = None;
    let mut end_date = None;
    let mut runtime_dir = None;
    let mut workers = 4_usize;
    let mut lookback_days = 180_i64;
    let mut event_budget = 3_500_000_u64;
    let mut event_limit = 50_000_000_usize;
    let mut plan_only = false;
    while let Some(argument) = values.next() {
        let value = |name: &str, values: &mut std::iter::Skip<std::env::Args>| {
            values
                .next()
                .ok_or_else(|| format!("{name} requires a value"))
        };
        match argument.as_str() {
            "--ticker-file" => ticker_files.push(PathBuf::from(value(&argument, &mut values)?)),
            "--start-date" => start_date = Some(parse_date(&value(&argument, &mut values)?)?),
            "--end-date" => end_date = Some(parse_date(&value(&argument, &mut values)?)?),
            "--runtime-dir" => runtime_dir = Some(PathBuf::from(value(&argument, &mut values)?)),
            "--workers" => workers = parse_number(&argument, &value(&argument, &mut values)?)?,
            "--lookback-days" => {
                lookback_days = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--event-budget" => {
                event_budget = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--event-limit" => {
                event_limit = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--plan-only" => plan_only = true,
            "--help" | "-h" => {
                println!("structure-checkpoint-campaign");
                println!("  --ticker-file PATH [--ticker-file PATH]");
                println!("  --start-date YYYY-MM-DD --end-date YYYY-MM-DD");
                println!("  --runtime-dir PATH [--workers 4] [--lookback-days 180]");
                println!("  [--event-budget 3500000] [--event-limit 50000000]");
                println!("  [--plan-only]");
                std::process::exit(0);
            }
            _ => return Err(format!("unknown argument {argument:?}; use --help")),
        }
    }
    let start_date = start_date.ok_or_else(|| "--start-date is required".to_string())?;
    let end_date = end_date.ok_or_else(|| "--end-date is required".to_string())?;
    if start_date > end_date {
        return Err("--start-date must be on or before --end-date".to_string());
    }
    if ticker_files.is_empty() {
        return Err("at least one --ticker-file is required".to_string());
    }
    if !(1..=32).contains(&workers) {
        return Err("--workers must be between 1 and 32".to_string());
    }
    if !(2..=3650).contains(&lookback_days) {
        return Err("--lookback-days must be between 2 and 3650".to_string());
    }
    if event_budget == 0 || event_limit == 0 {
        return Err("--event-budget and --event-limit must be positive".to_string());
    }
    Ok(Args {
        ticker_files,
        start_date,
        end_date,
        runtime_dir: runtime_dir.ok_or_else(|| "--runtime-dir is required".to_string())?,
        workers,
        lookback_days,
        event_budget,
        event_limit,
        plan_only,
    })
}

fn parse_date(value: &str) -> Result<NaiveDate, String> {
    NaiveDate::parse_from_str(value, "%Y-%m-%d")
        .map_err(|error| format!("invalid date {value:?}: {error}"))
}

fn parse_number<T>(name: &str, value: &str) -> Result<T, String>
where
    T: std::str::FromStr,
    T::Err: std::fmt::Display,
{
    value
        .parse()
        .map_err(|error| format!("invalid {name} value {value:?}: {error}"))
}

fn io_error(message: impl Into<String>) -> Box<dyn std::error::Error + Send + Sync> {
    Box::new(std::io::Error::new(
        std::io::ErrorKind::Other,
        message.into(),
    ))
}

#[cfg(test)]
mod tests {
    use super::{bootstrap_days, insert_ticker};
    use std::collections::BTreeSet;

    #[test]
    fn planner_uses_bounded_bootstrap_intervals() {
        assert_eq!(bootstrap_days(3_000_000, 200_000, 3_500_000), 0);
        assert_eq!(bootstrap_days(10_000_000, 100_000, 3_500_000), 28);
        assert_eq!(bootstrap_days(10_000_000, 1_000_000, 3_500_000), 3);
    }

    #[test]
    fn ticker_universe_is_normalized_and_validated() {
        let mut tickers = BTreeSet::new();
        insert_ticker(" sugp ", &mut tickers).unwrap();
        insert_ticker("SUGP", &mut tickers).unwrap();
        assert_eq!(tickers.into_iter().collect::<Vec<_>>(), vec!["SUGP"]);
    }
}
