use chrono::{DateTime, Datelike, Duration as ChronoDuration, NaiveDate, NaiveTime, TimeZone, Utc};
use chrono_tz::America::New_York;
use qmd_core::config::{load_env_files, GatewayConfig};
use qmd_core::generic_structure::{GenericStructureEngine, GENERIC_STRUCTURE_ALGORITHM_VERSION};
use qmd_core::indicators::{DailyStructureCheckpoint, IndicatorClickHouseWriter};
use qmd_core::metrics::SharedMetrics;
use qmd_history_gateway::config::HistoricalGatewayConfig;
use qmd_history_gateway::source::{
    HistoricalEventSource, StructureCampaignTicker, StructureEventCountEstimateRequest,
};
use serde::Serialize;
use serde_json::Value;
use std::collections::{BTreeMap, HashSet, VecDeque};
use std::env;
use std::fs;
use std::io::{IsTerminal, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;
use std::time::Duration;
use tokio::sync::{Mutex, Notify};
use tokio::task::JoinSet;

const EVENT_RATE_WINDOW_SECONDS: i64 = 300;
const EVENT_RATE_MIN_SAMPLE_SECONDS: i64 = 15;

#[derive(Clone, Debug)]
struct Args {
    ticker_files: Vec<PathBuf>,
    priority_tickers: Vec<String>,
    start_date: NaiveDate,
    end_date: NaiveDate,
    liquidity_start_date: NaiveDate,
    liquidity_end_date: NaiveDate,
    runtime_dir: PathBuf,
    workers: usize,
    max_retries: usize,
    retry_delay_seconds: u64,
    purge_existing_checkpoints: bool,
    plan_only: bool,
}

#[derive(Clone, Debug, Serialize)]
struct TickerPlan {
    ticker: String,
    rebuild_start: DateTime<Utc>,
    sessions: Vec<NaiveDate>,
    estimated_events: u64,
}

#[derive(Clone, Debug, Default, Serialize)]
struct Counts {
    active: usize,
    blocked: usize,
    completed: usize,
    failed: usize,
    finished: usize,
    queued: usize,
    retried: usize,
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
struct RecentUnit {
    ticker: String,
    session_date: NaiveDate,
    outcome: String,
    event_count: u64,
    cursor: u64,
}

#[derive(Clone, Debug, Serialize)]
struct Progress {
    schema_version: u16,
    status: String,
    started_at: DateTime<Utc>,
    updated_at: DateTime<Utc>,
    algorithm_version: u16,
    start_date: NaiveDate,
    end_date: NaiveDate,
    ticker_count: usize,
    total_units: usize,
    total_estimated_events: u64,
    counts: Counts,
    events_processed: u64,
    events_advanced: u64,
    active: BTreeMap<String, NaiveDate>,
    recent: VecDeque<RecentUnit>,
    issues: Vec<Issue>,
}

struct ProgressWriter {
    path: PathBuf,
    inner: Mutex<Progress>,
    processed_events: AtomicU64,
    status_notify: Notify,
}

impl ProgressWriter {
    fn new(
        path: PathBuf,
        start_date: NaiveDate,
        end_date: NaiveDate,
        plans: &[TickerPlan],
    ) -> Self {
        let total_units = plans.iter().map(|plan| plan.sessions.len()).sum();
        let total_estimated_events = plans.iter().fold(0_u64, |total, plan| {
            total.saturating_add(plan.estimated_events)
        });
        Self {
            path,
            processed_events: AtomicU64::new(0),
            status_notify: Notify::new(),
            inner: Mutex::new(Progress {
                schema_version: 5,
                status: "running".to_string(),
                started_at: Utc::now(),
                updated_at: Utc::now(),
                algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
                start_date,
                end_date,
                ticker_count: plans.len(),
                total_units,
                total_estimated_events,
                counts: Counts {
                    queued: total_units,
                    ..Counts::default()
                },
                events_processed: 0,
                events_advanced: 0,
                active: BTreeMap::new(),
                recent: VecDeque::new(),
                issues: Vec::new(),
            }),
        }
    }

    async fn activate(&self, ticker: &str, session_date: NaiveDate) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.active.insert(ticker.to_string(), session_date);
        progress.counts.active = progress.active.len();
        progress.counts.queued = progress.total_units.saturating_sub(
            progress
                .counts
                .finished
                .saturating_add(progress.counts.active),
        );
        Ok(())
    }

    async fn finish_unit(
        &self,
        ticker: &str,
        session_date: NaiveDate,
        outcome: &str,
        event_count: u64,
        advanced_event_count: u64,
        cursor: u64,
    ) -> Result<(), String> {
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
        progress.events_advanced = progress
            .events_advanced
            .saturating_add(advanced_event_count);
        progress.recent.push_front(RecentUnit {
            ticker: ticker.to_string(),
            session_date,
            outcome: outcome.to_string(),
            event_count,
            cursor,
        });
        progress.recent.truncate(5);
        Ok(())
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

    async fn retry(&self) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        progress.counts.retried = progress.counts.retried.saturating_add(1);
        Ok(())
    }

    async fn complete(&self, force_failed: bool) -> Result<Progress, String> {
        let mut progress = self.inner.lock().await;
        progress.status = if progress.counts.failed == 0 && !force_failed {
            "completed".to_string()
        } else {
            "failed".to_string()
        };
        self.write_locked(&mut progress)?;
        let final_progress = progress.clone();
        drop(progress);
        self.status_notify.notify_waiters();
        Ok(final_progress)
    }

    async fn interrupt(&self) -> Result<Progress, String> {
        let mut progress = self.inner.lock().await;
        progress.status = "interrupted".to_string();
        progress.active.clear();
        progress.counts.active = 0;
        progress.counts.queued = progress
            .total_units
            .saturating_sub(progress.counts.finished);
        self.write_locked(&mut progress)?;
        let final_progress = progress.clone();
        drop(progress);
        self.status_notify.notify_waiters();
        Ok(final_progress)
    }

    async fn snapshot(&self) -> Progress {
        let mut progress = self.inner.lock().await.clone();
        progress.events_processed = self.processed_events.load(Ordering::Relaxed);
        progress
    }

    async fn persist(&self) -> Result<(), String> {
        let mut progress = self.inner.lock().await;
        self.write_locked(&mut progress)
    }

    fn write_locked(&self, progress: &mut Progress) -> Result<(), String> {
        progress.events_processed = self.processed_events.load(Ordering::Relaxed);
        progress.updated_at = Utc::now();
        let bytes = serde_json::to_vec_pretty(progress)
            .map_err(|error| format!("failed to encode campaign progress: {error}"))?;
        let temporary = self.path.with_extension("json.tmp");
        fs::write(&temporary, bytes)
            .map_err(|error| format!("failed to write {}: {error}", temporary.display()))?;
        fs::rename(&temporary, &self.path)
            .map_err(|error| format!("failed to publish {}: {error}", self.path.display()))
    }

    fn record_events(&self, count: u64) {
        self.processed_events.fetch_add(count, Ordering::Relaxed);
    }

    fn rollback_events(&self, count: u64) {
        self.processed_events.fetch_sub(count, Ordering::Relaxed);
    }
}

struct AttemptEventProgress<'a> {
    campaign: &'a ProgressWriter,
    observed: AtomicU64,
    committed: bool,
}

impl<'a> AttemptEventProgress<'a> {
    fn new(campaign: &'a ProgressWriter) -> Self {
        Self {
            campaign,
            observed: AtomicU64::new(0),
            committed: false,
        }
    }

    fn record(&self, count: u64) {
        self.observed.fetch_add(count, Ordering::Relaxed);
        self.campaign.record_events(count);
    }

    fn observed(&self) -> u64 {
        self.observed.load(Ordering::Relaxed)
    }

    fn commit(&mut self) {
        self.committed = true;
    }
}

impl Drop for AttemptEventProgress<'_> {
    fn drop(&mut self) {
        if !self.committed {
            self.campaign.rollback_events(self.observed());
        }
    }
}

#[derive(Clone, Debug)]
struct DayResult {
    status: &'static str,
    event_count: u64,
    advanced_event_count: u64,
    cursor: u64,
}

#[derive(Default)]
struct EventRateWindow {
    samples: VecDeque<(DateTime<Utc>, u64)>,
}

impl EventRateWindow {
    fn observe(&mut self, observed_at: DateTime<Utc>, processed_events: u64) -> Option<f64> {
        if self.samples.back().is_some_and(|(prior_at, prior_events)| {
            observed_at < *prior_at || processed_events < *prior_events
        }) {
            self.samples.clear();
        }
        self.samples.push_back((observed_at, processed_events));
        let cutoff = observed_at - ChronoDuration::seconds(EVENT_RATE_WINDOW_SECONDS);
        while self
            .samples
            .get(1)
            .is_some_and(|(sample_at, _)| *sample_at <= cutoff)
        {
            self.samples.pop_front();
        }
        let (first_at, first_events) = self.samples.front().copied()?;
        let (last_at, last_events) = self.samples.back().copied()?;
        let elapsed = (last_at - first_at).num_milliseconds() as f64 / 1_000.0;
        if elapsed < EVENT_RATE_MIN_SAMPLE_SECONDS as f64 || last_events < first_events {
            return None;
        }
        Some((last_events - first_events) as f64 / elapsed)
    }
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
    let history_config = HistoricalGatewayConfig::from_env();
    history_config.validate().map_err(io_error)?;
    let source = HistoricalEventSource::initialize(history_config.clone())
        .await
        .map_err(io_error)?;
    let gateway_config = GatewayConfig::from_env();
    let writer = IndicatorClickHouseWriter::new(gateway_config, SharedMetrics::new());
    let automatic_tickers = source
        .structure_campaign_tickers(
            args.start_date,
            args.liquidity_start_date,
            args.liquidity_end_date,
            Utc::now(),
        )
        .await
        .map_err(io_error)?;
    let file_tickers = load_tickers(&args.ticker_files).map_err(io_error)?;
    let tickers = merge_ticker_universe(&args.priority_tickers, &file_tickers, &automatic_tickers)
        .map_err(io_error)?;
    if tickers.is_empty() {
        return Err(io_error("ticker universe is empty"));
    }
    writer.initialize().await.map_err(io_error)?;
    if args.purge_existing_checkpoints {
        let deleted = writer
            .purge_all_daily_structure_checkpoints()
            .await
            .map_err(io_error)?;
        eprintln!(
            "Removed all {deleted} pre-existing daily structure checkpoint row(s); this campaign will rebuild cold."
        );
    }
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
            "Validated Campaign v3 plan: tickers={} units={} plan={}",
            plans.len(),
            plans.iter().map(|plan| plan.sessions.len()).sum::<usize>(),
            plan_path.display()
        );
        return Ok(());
    }
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

    let worker_count = args.workers.min(plans.len()).max(1);
    let reporter = tokio::spawn(report_progress(
        progress.clone(),
        progress_path.clone(),
        worker_count,
    ));
    let queue = Arc::new(Mutex::new(VecDeque::from(plans)));
    let mut tasks = JoinSet::new();
    for _worker_id in 0..worker_count {
        let queue = queue.clone();
        let source = source.clone();
        let writer = writer.clone();
        let config = history_config.clone();
        let progress = progress.clone();
        let max_retries = args.max_retries;
        let retry_delay_seconds = args.retry_delay_seconds;
        tasks.spawn(async move {
            let mut errors = Vec::new();
            loop {
                let Some(plan) = queue.lock().await.pop_front() else {
                    break;
                };
                if let Err(error) = run_ticker(
                    &config,
                    &source,
                    &writer,
                    &progress,
                    plan,
                    max_retries,
                    retry_delay_seconds,
                )
                .await
                {
                    errors.push(error);
                }
            }
            errors
        });
    }

    let mut task_failed = false;
    let mut worker_errors = Vec::new();
    let mut interrupted = false;
    while !tasks.is_empty() {
        tokio::select! {
            signal = tokio::signal::ctrl_c() => {
                if signal.is_ok() {
                    interrupted = true;
                    tasks.abort_all();
                }
            }
            result = tasks.join_next() => {
                match result {
                    Some(Ok(errors)) => {
                        task_failed |= !errors.is_empty();
                        worker_errors.extend(errors);
                    }
                    Some(Err(error)) if interrupted && error.is_cancelled() => {}
                    Some(Err(error)) => {
                        task_failed = true;
                        worker_errors.push(format!("worker panicked or was cancelled: {error}"));
                    }
                    None => break,
                }
            }
        }
        if interrupted {
            while tasks.join_next().await.is_some() {}
            break;
        }
    }
    let final_progress = if interrupted {
        progress.interrupt().await.map_err(io_error)?
    } else {
        progress.complete(task_failed).await.map_err(io_error)?
    };
    reporter
        .await
        .map_err(|error| io_error(format!("progress reporter failed: {error}")))?;
    for error in worker_errors.iter().take(20) {
        eprintln!("worker failure: {error}");
    }
    if worker_errors.len() > 20 {
        eprintln!(
            "{} additional worker failures are recorded in {}",
            worker_errors.len() - 20,
            progress_path.display()
        );
    }
    if interrupted {
        std::process::exit(130);
    }
    if task_failed || final_progress.counts.failed > 0 {
        std::process::exit(1);
    }
    Ok(())
}

async fn report_progress(progress: Arc<ProgressWriter>, status_path: PathBuf, workers: usize) {
    let interactive = std::io::stdout().is_terminal();
    let color = interactive && env::var_os("NO_COLOR").is_none();
    let _terminal = TerminalSession::enter(interactive);
    let refresh = Duration::from_secs(1);
    let mut last_log_at = Utc::now() - ChronoDuration::seconds(15);
    let mut event_rate_window = EventRateWindow::default();
    loop {
        let status_changed = progress.status_notify.notified();
        if let Err(error) = progress.persist().await {
            eprintln!("failed to persist campaign status: {error}");
        }
        let snapshot = progress.snapshot().await;
        let event_rate = event_rate_window.observe(Utc::now(), snapshot.events_processed);
        if interactive {
            render_dashboard(&snapshot, &status_path, workers, color, event_rate);
        } else if snapshot.status != "running"
            || (Utc::now() - last_log_at) >= ChronoDuration::seconds(15)
        {
            render_log_snapshot(&snapshot, workers, event_rate);
            last_log_at = Utc::now();
        }
        if snapshot.status != "running" {
            break;
        }
        tokio::select! {
            _ = tokio::time::sleep(refresh) => {}
            _ = status_changed => {}
        }
    }
}

fn render_dashboard(
    progress: &Progress,
    status_path: &PathBuf,
    workers: usize,
    color: bool,
    event_rate: Option<f64>,
) {
    let width = terminal_width();
    let lines = dashboard_lines(progress, status_path, workers, width, event_rate);
    let frame = dashboard_frame(&lines, progress, color);
    let mut stdout = std::io::stdout().lock();
    let _ = stdout.write_all(frame.as_bytes());
    let _ = stdout.flush();
}

struct TerminalSession {
    interactive: bool,
}

impl TerminalSession {
    fn enter(interactive: bool) -> Self {
        if interactive {
            let mut stdout = std::io::stdout().lock();
            let _ = write!(stdout, "\x1b[2J\x1b[H\x1b[?25l");
            let _ = stdout.flush();
        }
        Self { interactive }
    }
}

impl Drop for TerminalSession {
    fn drop(&mut self) {
        if self.interactive {
            let mut stdout = std::io::stdout().lock();
            let _ = write!(stdout, "\x1b[?25h");
            let _ = stdout.flush();
        }
    }
}

fn dashboard_frame(lines: &[String], progress: &Progress, color: bool) -> String {
    let status_color = match progress.status.as_str() {
        "completed" => "32",
        "running" if progress.counts.failed == 0 => "36",
        "running" | "interrupted" => "33",
        _ => "31",
    };
    let mut frame = String::from("\x1b[H");
    for (index, line) in lines.iter().enumerate() {
        frame.push_str("\x1b[2K");
        if index == 0 && color {
            frame.push_str(&format!("\x1b[1;{status_color}m{line}\x1b[0m"));
        } else if (index == 2 || index == 3) && color {
            frame.push_str(&format!("\x1b[1m{line}\x1b[0m"));
        } else {
            frame.push_str(line);
        }
        frame.push('\n');
    }
    frame.push_str("\x1b[J");
    frame
}

fn render_log_snapshot(progress: &Progress, workers: usize, event_rate: Option<f64>) {
    println!("{}", log_snapshot(progress, workers, event_rate));
}

fn log_snapshot(progress: &Progress, workers: usize, event_rate: Option<f64>) -> String {
    let resolved = resolved_units(progress);
    let percentage = percentage(resolved, progress.total_units);
    let eta = event_eta_seconds(progress, event_rate)
        .map(format_duration)
        .unwrap_or_else(|| "warming_up".to_string());
    format!(
        "{} status={} progress={}/{} ({:.1}%) active={}/{} queued={} completed={} current={} retried={} unavailable={} failed={} blocked={} events={}/{} event_rate_5m={:.0}/s elapsed={} eta={}",
        Utc::now().format("%Y-%m-%dT%H:%M:%SZ"),
        progress.status,
        resolved,
        progress.total_units,
        percentage,
        progress.counts.active,
        workers,
        progress.counts.queued,
        progress.counts.completed,
        progress.counts.skipped,
        progress.counts.retried,
        progress.counts.unavailable,
        progress.counts.failed,
        progress.counts.blocked,
        progress.events_processed,
        progress.total_estimated_events,
        event_rate.unwrap_or_default(),
        format_duration(elapsed_seconds(progress)),
        eta,
    )
}

fn dashboard_lines(
    progress: &Progress,
    status_path: &PathBuf,
    workers: usize,
    width: usize,
    event_rate: Option<f64>,
) -> Vec<String> {
    let resolved = resolved_units(progress);
    let percentage = percentage(resolved, progress.total_units);
    let elapsed = elapsed_seconds(progress);
    let unit_rate = rate(resolved as u64, elapsed);
    let eta = event_eta_seconds(progress, event_rate).map(format_duration);
    let event_percentage =
        percentage_u64(progress.events_processed, progress.total_estimated_events);
    let compact = width < 80;
    let bar_width = if compact {
        width.saturating_sub(38).clamp(10, 22)
    } else {
        width.saturating_sub(34).clamp(10, 42)
    };
    let filled = if progress.total_units == 0 {
        bar_width
    } else {
        ((resolved as f64 / progress.total_units as f64) * bar_width as f64).round() as usize
    }
    .min(bar_width);
    let bar = format!("{}{}", "#".repeat(filled), "-".repeat(bar_width - filled));
    let active = if progress.active.is_empty() {
        "waiting for workers".to_string()
    } else {
        progress
            .active
            .iter()
            .take(if width >= 110 { 8 } else { 4 })
            .map(|(ticker, date)| format!("{ticker}@{date}"))
            .collect::<Vec<_>>()
            .join("  ")
    };
    let recent = progress
        .recent
        .front()
        .map(|unit| {
            format!(
                "{}@{} {}  events={} cursor={}",
                unit.ticker,
                unit.session_date,
                unit.outcome,
                format_count(unit.event_count),
                unit.cursor
            )
        })
        .unwrap_or_else(|| "none yet".to_string());
    let failure = progress
        .issues
        .last()
        .map(|issue| {
            format!(
                "{}{}: {}",
                issue.ticker,
                issue
                    .session_date
                    .map(|date| format!("@{date}"))
                    .unwrap_or_default(),
                issue.error
            )
        })
        .unwrap_or_else(|| "none".to_string());
    let state = if progress.status == "running" && progress.counts.failed > 0 {
        "DEGRADED"
    } else {
        progress.status.as_str()
    };
    let eta = eta.unwrap_or_else(|| "warming up".to_string());
    let mut lines = if compact {
        vec![
            format!(
                "Checkpoint Campaign v3 | {} | v{} | {} workers",
                state.to_ascii_uppercase(),
                progress.algorithm_version,
                workers
            ),
            format!(
                "{} to {} | {} tickers | {} UTC",
                progress.start_date,
                progress.end_date,
                format_count(progress.ticker_count as u64),
                progress.updated_at.format("%H:%M:%S")
            ),
            format!(
                "Resolved {}/{} ({:.1}%) [{bar}]",
                format_count(resolved as u64),
                format_count(progress.total_units as u64),
                percentage,
            ),
            format!(
                "Durable {} | current {} | retries {} | failed {}",
                format_count(progress.counts.completed as u64),
                format_count(progress.counts.skipped as u64),
                format_count(progress.counts.retried as u64),
                format_count(progress.counts.failed as u64),
            ),
            format!(
                "Unavailable {} | blocked {} | queued {}",
                format_count(progress.counts.unavailable as u64),
                format_count(progress.counts.blocked as u64),
                format_count(progress.counts.queued as u64),
            ),
            format!(
                "Events {}/{} ({:.1}%) | 5m rate {}/s",
                format_count(progress.events_processed),
                format_count(progress.total_estimated_events),
                event_percentage,
                format_count(event_rate.unwrap_or_default().round() as u64),
            ),
            format!(
                "Active {}/{} | {:.2} checkpoints/s",
                progress.counts.active, workers, unit_rate,
            ),
            format!("Elapsed {} | ETA {eta}", format_duration(elapsed)),
            format!("Active: {active}"),
            format!("Latest: {recent}"),
            format!("Latest failure: {failure}"),
            format!("Status: {}", status_path.display()),
            "Ctrl+C stops safely; rerun the same command to resume.".to_string(),
        ]
    } else {
        vec![
            format!(
                "Structural Checkpoint Campaign v3 | {} | algorithm v{} | workers {}",
                state.to_ascii_uppercase(),
                progress.algorithm_version,
                workers
            ),
            format!(
                "Range {} to {} | {:>6} tickers | updated {} UTC",
                progress.start_date,
                progress.end_date,
                format_count(progress.ticker_count as u64),
                progress.updated_at.format("%H:%M:%S")
            ),
            format!(
                "Resolved [{bar}] {:>5.1}%  {} / {}",
                percentage,
                format_count(resolved as u64),
                format_count(progress.total_units as u64)
            ),
            format!(
                "Durable {:>8} | current {:>8} | retries {:>6} | unavailable {:>6} | failed {:>4} | blocked {:>6}",
                format_count(progress.counts.completed as u64),
                format_count(progress.counts.skipped as u64),
                format_count(progress.counts.retried as u64),
                format_count(progress.counts.unavailable as u64),
                format_count(progress.counts.failed as u64),
                format_count(progress.counts.blocked as u64),
            ),
            format!(
                "Events {} / {} ({:.1}%) | 5m rate {}/s | elapsed {} | ETA {}",
                format_count(progress.events_processed),
                format_count(progress.total_estimated_events),
                event_percentage,
                format_count(event_rate.unwrap_or_default().round() as u64),
                format_duration(elapsed),
                eta,
            ),
            format!(
                "Active {}/{} | queued {} | {:.2} checkpoints/s",
                progress.counts.active,
                workers,
                format_count(progress.counts.queued as u64),
                unit_rate,
            ),
            format!("Active: {active}"),
            format!("Latest: {recent}"),
            format!("Latest failure: {failure}"),
            format!("Durable status: {}", status_path.display()),
            "Ctrl+C safely stops workers; rerun the same command to resume.".to_string(),
        ]
    };
    for line in &mut lines {
        *line = truncate_line(line, width);
    }
    lines
}

fn resolved_units(progress: &Progress) -> usize {
    progress
        .counts
        .completed
        .saturating_add(progress.counts.skipped)
        .saturating_add(progress.counts.unavailable)
        .saturating_add(progress.counts.failed)
        .saturating_add(progress.counts.blocked)
}

fn percentage(value: usize, total: usize) -> f64 {
    if total == 0 {
        100.0
    } else {
        value as f64 * 100.0 / total as f64
    }
}

fn percentage_u64(value: u64, total: u64) -> f64 {
    if total == 0 {
        100.0
    } else {
        value.min(total) as f64 * 100.0 / total as f64
    }
}

fn event_eta_seconds(progress: &Progress, event_rate: Option<f64>) -> Option<i64> {
    let event_rate = event_rate.filter(|value| value.is_finite() && *value > 0.0)?;
    let remaining = progress
        .total_estimated_events
        .saturating_sub(progress.events_processed);
    if remaining == 0 {
        return Some(0);
    }
    Some((remaining as f64 / event_rate).ceil() as i64)
}

fn elapsed_seconds(progress: &Progress) -> i64 {
    (Utc::now() - progress.started_at).num_seconds().max(0)
}

fn rate(value: u64, elapsed_seconds: i64) -> f64 {
    if elapsed_seconds <= 0 {
        0.0
    } else {
        value as f64 / elapsed_seconds as f64
    }
}

fn format_duration(seconds: i64) -> String {
    let seconds = seconds.max(0);
    let hours = seconds / 3_600;
    let minutes = seconds % 3_600 / 60;
    let seconds = seconds % 60;
    format!("{hours:02}:{minutes:02}:{seconds:02}")
}

fn format_count(value: u64) -> String {
    let digits = value.to_string();
    let mut result = String::with_capacity(digits.len() + digits.len() / 3);
    for (index, character) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            result.push(',');
        }
        result.push(character);
    }
    result
}

fn terminal_width() -> usize {
    env::var("COLUMNS")
        .ok()
        .and_then(|value| value.parse::<usize>().ok())
        .unwrap_or(120)
        .clamp(60, 240)
}

fn truncate_line(value: &str, width: usize) -> String {
    let count = value.chars().count();
    if count <= width {
        return value.to_string();
    }
    let keep = width.saturating_sub(3);
    format!("{}...", value.chars().take(keep).collect::<String>())
}

async fn run_ticker(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    writer: &IndicatorClickHouseWriter,
    progress: &ProgressWriter,
    plan: TickerPlan,
    max_retries: usize,
    retry_delay_seconds: u64,
) -> Result<(), String> {
    let manifest = source
        .structure_campaign_manifest(&plan.ticker, plan.rebuild_start, &plan.sessions)
        .await?;
    let after_last_session = plan
        .sessions
        .last()
        .copied()
        .and_then(|date| date.succ_opt())
        .ok_or_else(|| format!("{} has no valid campaign session boundary", plan.ticker))?;
    let seed = writer
        .load_daily_structure_checkpoint_before(&plan.ticker, after_last_session)
        .await?
        .filter(|row| row.authority_start == plan.rebuild_start)
        .filter(|row| {
            manifest
                .sessions
                .iter()
                .find(|session| session.session_date == row.session_date)
                .is_some_and(|session| {
                    row.source_complete
                        && row.source_plan_hash == session.source_revision.source_plan_hash
                        && row.source_revision_token == session.source_revision.token
                })
        });
    let seed_session = seed.as_ref().map(|row| row.session_date);
    let seed_cursor = seed
        .as_ref()
        .map(|row| row.last_arrival_sequence)
        .unwrap_or_default();
    let mut engine = GenericStructureEngine::new(&plan.ticker);
    if let Some(seed) = seed {
        engine.seed_checkpoint(&seed.checkpoint);
    }
    let rules = source.trade_aggregation_rules();

    for session in &manifest.sessions {
        let session_date = session.session_date;
        if session_is_covered_by_seed(session_date, seed_session) {
            progress
                .finish_unit(&plan.ticker, session_date, "skipped", 0, 0, seed_cursor)
                .await?;
            continue;
        }
        progress.activate(&plan.ticker, session_date).await?;
        let mut attempt = 0_usize;
        let result = loop {
            let mut event_progress = AttemptEventProgress::new(progress);
            let checkpoint_before = engine.checkpoint();
            let build_result = process_ordinal_session(
                config,
                source,
                writer,
                &manifest,
                session,
                &rules,
                &mut engine,
                &mut event_progress,
            )
            .await;
            match build_result {
                Ok(result)
                    if result.event_count == event_progress.observed()
                        && result.event_count == session.event_count =>
                {
                    event_progress.commit();
                    break Ok(result);
                }
                Ok(result) => {
                    break Err(format!(
                        "event progress mismatch: streamed {} but completed {}",
                        event_progress.observed(),
                        result.event_count
                    ))
                }
                Err(error) if retryable_error(&error) && attempt < max_retries => {
                    engine.seed_checkpoint(&checkpoint_before);
                    attempt += 1;
                    progress.retry().await?;
                    tokio::time::sleep(Duration::from_secs(
                        retry_delay_seconds.saturating_mul(1_u64 << (attempt - 1).min(6)),
                    ))
                    .await;
                }
                Err(error) => {
                    engine.seed_checkpoint(&checkpoint_before);
                    break Err(error);
                }
            }
        };
        match result {
            Ok(result) => {
                progress
                    .finish_unit(
                        &plan.ticker,
                        session_date,
                        result.status,
                        result.event_count,
                        result.advanced_event_count,
                        result.cursor,
                    )
                    .await?;
            }
            Err(error) => {
                let index = manifest
                    .sessions
                    .iter()
                    .position(|value| value.session_date == session_date)
                    .unwrap_or_default();
                let blocked = manifest.sessions.len().saturating_sub(index + 1);
                progress
                    .fail_ticker(&plan.ticker, Some(session_date), blocked, error.clone())
                    .await?;
                return Err(format!("{} {}: {error}", plan.ticker, session_date));
            }
        }
    }
    drop(engine);
    Ok(())
}

fn session_is_covered_by_seed(session_date: NaiveDate, seed_session: Option<NaiveDate>) -> bool {
    seed_session.is_some_and(|date| session_date <= date)
}

async fn process_ordinal_session(
    config: &HistoricalGatewayConfig,
    source: &HistoricalEventSource,
    writer: &IndicatorClickHouseWriter,
    manifest: &qmd_history_gateway::source::StructureCampaignManifest,
    session: &qmd_history_gateway::source::StructureCampaignSession,
    rules: &qmd_core::bars::TradeAggregationRules,
    engine: &mut GenericStructureEngine,
    event_progress: &mut AttemptEventProgress<'_>,
) -> Result<DayResult, String> {
    let session_date = session.session_date;
    let authority_end = session_end(session_date)?;
    let as_of = authority_end - ChronoDuration::microseconds(1);
    for adjustment in manifest
        .split_adjustments
        .iter()
        .filter(|adjustment| adjustment.effective_at <= as_of)
    {
        engine.apply_split_adjustment(adjustment)?;
    }
    let mut batches =
        source.stream_structure_ordinal_session(session, &manifest.ticker, config.batch_size)?;
    let mut event_count = 0_u64;
    let mut advanced_event_count = 0_u64;
    let mut prior_sip = 0_u64;
    let mut first_sip = 0_u64;
    let mut last_sip = 0_u64;
    while let Some(batch) = batches.recv().await {
        let batch = batch?;
        for compact in &batch {
            if compact.ticker.to_ascii_uppercase() != manifest.ticker
                || compact.arrival_sequence < session.first_ordinal
                || compact.arrival_sequence >= session.next_ordinal
            {
                return Err(format!(
                    "ordinal stream escaped its pinned range for {} {}",
                    manifest.ticker, session_date
                ));
            }
            if prior_sip > compact.sip_timestamp_us {
                return Err(format!(
                    "ordinal stream is not SIP-time monotonic for {} {}",
                    manifest.ticker, session_date
                ));
            }
            let event_at = DateTime::<Utc>::from_timestamp_micros(compact.sip_timestamp_us as i64)
                .ok_or_else(|| "ordinal stream contains an invalid SIP timestamp".to_string())?;
            if event_at.with_timezone(&New_York).date_naive() != session_date {
                return Err(format!(
                    "ordinal stream event date does not match {} {}",
                    manifest.ticker, session_date
                ));
            }
            if first_sip == 0 {
                first_sip = compact.sip_timestamp_us;
            }
            prior_sip = compact.sip_timestamp_us;
            last_sip = compact.sip_timestamp_us;
            let event = source.market_event(compact);
            let before = engine.checkpoint_cursor();
            let conditions = match &event {
                qmd_core::event::MarketEvent::Trade(event) => event.conditions.as_slice(),
                qmd_core::event::MarketEvent::Quote(event) => event.conditions.as_slice(),
            };
            engine.apply_event_without_snapshot(&event, rules.resolve(conditions, event.ts()));
            if engine.checkpoint_cursor() != before {
                advanced_event_count = advanced_event_count.saturating_add(1);
            }
            event_count = event_count.saturating_add(1);
        }
        event_progress.record(batch.len() as u64);
    }
    if event_count != session.event_count
        || (event_count > 0
            && (first_sip != session.first_sip_timestamp_us
                || last_sip != session.last_sip_timestamp_us))
    {
        return Err(format!(
            "ordinal stream authority mismatch for {} {}: expected {} events {}..{}, received {} events {}..{}",
            manifest.ticker,
            session_date,
            session.event_count,
            session.first_sip_timestamp_us,
            session.last_sip_timestamp_us,
            event_count,
            first_sip,
            last_sip,
        ));
    }
    let mut checkpoint = engine.checkpoint();
    checkpoint.replayed_through = Some(as_of);
    engine.seed_checkpoint(&checkpoint);
    let checkpoint_at = checkpoint.updated_at.unwrap_or(as_of);
    if checkpoint.algorithm_version != GENERIC_STRUCTURE_ALGORITHM_VERSION {
        return Err(format!(
            "calculated checkpoint algorithm v{} does not match v{}",
            checkpoint.algorithm_version, GENERIC_STRUCTURE_ALGORITHM_VERSION
        ));
    }
    if checkpoint.sym.to_ascii_uppercase() != manifest.ticker {
        return Err(format!(
            "calculated checkpoint ticker {} does not match {}",
            checkpoint.sym, manifest.ticker,
        ));
    }
    let revision = &session.source_revision;
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
            sym: manifest.ticker.clone(),
            authority_start: manifest.authority_start,
            checkpoint_at,
            last_arrival_sequence: checkpoint.last_arrival_sequence,
            source_plan_hash: revision.source_plan_hash.clone(),
            source_revision_token: revision.token.clone(),
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

async fn build_plans(
    args: &Args,
    source: &HistoricalEventSource,
    tickers: &[String],
) -> Result<Vec<TickerPlan>, String> {
    let planning_start = args.start_date;
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
        .await?;
    let sessions = completed_sessions
        .into_iter()
        .filter(|session| *session >= args.start_date && *session <= args.end_date)
        .collect::<Vec<_>>();
    if sessions.is_empty() {
        return Err(format!(
            "no completed market sessions exist between {} and {}",
            args.start_date, args.end_date
        ));
    }
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
            let (estimated_events, _) = estimates.get(ticker).copied().unwrap_or_default();
            TickerPlan {
                ticker: ticker.clone(),
                rebuild_start,
                sessions: sessions.clone(),
                estimated_events,
            }
        })
        .collect())
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

fn retryable_error(error: &str) -> bool {
    let error = error.to_ascii_lowercase();
    [
        "429",
        "502",
        "503",
        "504",
        "timed out",
        "timeout",
        "connection reset",
        "connection aborted",
        "unexpected eof",
        "error decoding response body",
        "temporarily unavailable",
        "memory limit",
        "too many simultaneous queries",
    ]
    .iter()
    .any(|marker| error.contains(marker))
}

fn load_tickers(paths: &[PathBuf]) -> Result<Vec<String>, String> {
    let mut tickers = Vec::new();
    let mut seen = HashSet::new();
    for path in paths {
        let text = fs::read_to_string(path)
            .map_err(|error| format!("failed to read {}: {error}", path.display()))?;
        match serde_json::from_str::<Value>(&text) {
            Ok(value) => collect_json_tickers(&value, &mut tickers, &mut seen)?,
            Err(_) => {
                for line in text.lines() {
                    insert_ticker(line, &mut tickers, &mut seen)?;
                }
            }
        }
    }
    Ok(tickers)
}

fn collect_json_tickers(
    value: &Value,
    tickers: &mut Vec<String>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
    match value {
        Value::Array(rows) => {
            for row in rows {
                collect_json_tickers(row, tickers, seen)?;
            }
        }
        Value::Object(row) => {
            if let Some(value) = row
                .get("symbol")
                .or_else(|| row.get("ticker"))
                .or_else(|| row.get("sym"))
                .and_then(Value::as_str)
            {
                insert_ticker(value, tickers, seen)?;
            } else if let Some(rows) = row.get("rows").or_else(|| row.get("tickers")) {
                collect_json_tickers(rows, tickers, seen)?;
            }
        }
        Value::String(value) => {
            insert_ticker(value, tickers, seen)?;
        }
        _ => {}
    }
    Ok(())
}

fn insert_ticker(
    value: &str,
    tickers: &mut Vec<String>,
    seen: &mut HashSet<String>,
) -> Result<(), String> {
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
    if seen.insert(ticker.clone()) {
        tickers.push(ticker);
    }
    Ok(())
}

fn merge_ticker_universe(
    priority_tickers: &[String],
    file_tickers: &[String],
    automatic_tickers: &[StructureCampaignTicker],
) -> Result<Vec<String>, String> {
    let mut tickers = Vec::new();
    let mut seen = HashSet::new();
    for ticker in priority_tickers {
        insert_ticker(ticker, &mut tickers, &mut seen)?;
    }
    for row in automatic_tickers {
        insert_ticker(&row.ticker, &mut tickers, &mut seen)?;
    }
    for ticker in file_tickers {
        insert_ticker(ticker, &mut tickers, &mut seen)?;
    }
    Ok(tickers)
}

fn parse_args() -> Result<Args, String> {
    let mut values = env::args().skip(1);
    let mut ticker_files = Vec::new();
    let mut priority_tickers = Vec::new();
    let mut start_date = None;
    let mut end_date = None;
    let mut liquidity_start_date = None;
    let mut liquidity_end_date = None;
    let mut runtime_dir = None;
    let mut workers = 4_usize;
    let mut max_retries = 5_usize;
    let mut retry_delay_seconds = 2_u64;
    let mut purge_existing_checkpoints = false;
    let mut plan_only = false;
    while let Some(argument) = values.next() {
        let value = |name: &str, values: &mut std::iter::Skip<std::env::Args>| {
            values
                .next()
                .ok_or_else(|| format!("{name} requires a value"))
        };
        match argument.as_str() {
            "--ticker-file" => ticker_files.push(PathBuf::from(value(&argument, &mut values)?)),
            "--priority-ticker" => priority_tickers.push(value(&argument, &mut values)?),
            "--start-date" => start_date = Some(parse_date(&value(&argument, &mut values)?)?),
            "--end-date" => end_date = Some(parse_date(&value(&argument, &mut values)?)?),
            "--liquidity-start-date" => {
                liquidity_start_date = Some(parse_date(&value(&argument, &mut values)?)?)
            }
            "--liquidity-end-date" => {
                liquidity_end_date = Some(parse_date(&value(&argument, &mut values)?)?)
            }
            "--runtime-dir" => runtime_dir = Some(PathBuf::from(value(&argument, &mut values)?)),
            "--workers" => workers = parse_number(&argument, &value(&argument, &mut values)?)?,
            "--max-retries" => {
                max_retries = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--retry-delay-seconds" => {
                retry_delay_seconds = parse_number(&argument, &value(&argument, &mut values)?)?
            }
            "--purge-existing-checkpoints" => purge_existing_checkpoints = true,
            "--plan-only" => plan_only = true,
            "--help" | "-h" => {
                println!("structure-checkpoint-campaign v3");
                println!("  --start-date YYYY-MM-DD --end-date YYYY-MM-DD");
                println!("  --runtime-dir PATH [--workers 4]  # allowed: 1-64");
                println!("  [--priority-ticker SUGP] [--ticker-file PATH]");
                println!("  [--liquidity-start-date YYYY-MM-DD --liquidity-end-date YYYY-MM-DD]");
                println!("  [--max-retries 5] [--retry-delay-seconds 2]");
                println!("  [--purge-existing-checkpoints] [--plan-only]");
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
    validate_worker_count(workers)?;
    if max_retries > 10 {
        return Err("--max-retries must be between 0 and 10".to_string());
    }
    if !(1..=60).contains(&retry_delay_seconds) {
        return Err("--retry-delay-seconds must be between 1 and 60".to_string());
    }
    let default_liquidity_start = NaiveDate::from_ymd_opt(end_date.year(), end_date.month(), 1)
        .ok_or_else(|| "invalid default liquidity month".to_string())?;
    let liquidity_start_date = liquidity_start_date.unwrap_or(default_liquidity_start);
    let liquidity_end_date = liquidity_end_date.unwrap_or(end_date);
    if liquidity_start_date > liquidity_end_date {
        return Err("--liquidity-start-date must not be after --liquidity-end-date".to_string());
    }
    Ok(Args {
        ticker_files,
        priority_tickers,
        start_date,
        end_date,
        liquidity_start_date,
        liquidity_end_date,
        runtime_dir: runtime_dir.ok_or_else(|| "--runtime-dir is required".to_string())?,
        workers,
        max_retries,
        retry_delay_seconds,
        purge_existing_checkpoints,
        plan_only,
    })
}

fn validate_worker_count(workers: usize) -> Result<(), String> {
    if !(1..=64).contains(&workers) {
        return Err("--workers must be between 1 and 64".to_string());
    }
    Ok(())
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
    use super::{
        dashboard_frame, dashboard_lines, insert_ticker, log_snapshot, merge_ticker_universe,
        retryable_error, session_is_covered_by_seed, validate_worker_count, AttemptEventProgress,
        Counts, EventRateWindow, Progress, ProgressWriter, RecentUnit, TickerPlan,
        GENERIC_STRUCTURE_ALGORITHM_VERSION,
    };
    use chrono::{NaiveDate, TimeZone, Utc};
    use qmd_history_gateway::source::StructureCampaignTicker;
    use std::collections::{BTreeMap, HashSet, VecDeque};
    use std::path::PathBuf;

    #[test]
    fn ticker_universe_is_normalized_and_validated() {
        let mut tickers = Vec::new();
        let mut seen = HashSet::new();
        insert_ticker(" sugp ", &mut tickers, &mut seen).unwrap();
        insert_ticker("SUGP", &mut tickers, &mut seen).unwrap();
        assert_eq!(tickers, vec!["SUGP"]);
    }

    #[test]
    fn explicit_priorities_precede_automatic_liquidity_order_and_file_extras() {
        let automatic = vec![
            StructureCampaignTicker {
                currently_active: true,
                priority_dollar_volume: 20_000_000.0,
                ticker: "AAPL".to_string(),
            },
            StructureCampaignTicker {
                currently_active: false,
                priority_dollar_volume: 1_000_000.0,
                ticker: "OLD".to_string(),
            },
        ];
        let merged = merge_ticker_universe(
            &["SUGP".to_string(), "JUNS".to_string()],
            &["AAPL".to_string(), "EXTRA".to_string()],
            &automatic,
        )
        .unwrap();
        assert_eq!(merged, vec!["SUGP", "JUNS", "AAPL", "OLD", "EXTRA"]);
    }

    #[test]
    fn completed_seed_skips_only_covered_sessions() {
        let seed = NaiveDate::from_ymd_opt(2026, 8, 20).unwrap();
        assert!(session_is_covered_by_seed(
            NaiveDate::from_ymd_opt(2026, 8, 20).unwrap(),
            Some(seed)
        ));
        assert!(!session_is_covered_by_seed(
            NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
            Some(seed)
        ));
        assert!(!session_is_covered_by_seed(
            NaiveDate::from_ymd_opt(2026, 8, 20).unwrap(),
            None
        ));
    }

    #[test]
    fn only_transient_source_failures_are_retried() {
        assert!(retryable_error(
            "ClickHouse 502 error decoding response body"
        ));
        assert!(retryable_error("memory limit exceeded"));
        assert!(!retryable_error("checkpoint algorithm version mismatch"));
    }

    #[test]
    fn campaign_accepts_up_to_sixty_four_workers() {
        assert!(validate_worker_count(1).is_ok());
        assert!(validate_worker_count(32).is_ok());
        assert!(validate_worker_count(64).is_ok());
        assert_eq!(
            validate_worker_count(65).unwrap_err(),
            "--workers must be between 1 and 64"
        );
    }

    #[test]
    fn event_rate_uses_only_the_fixed_five_minute_window() {
        let start = Utc.with_ymd_and_hms(2026, 9, 3, 14, 0, 0).unwrap();
        let mut window = EventRateWindow::default();
        assert_eq!(window.observe(start, 100), None);
        assert_eq!(
            window.observe(start + chrono::Duration::seconds(60), 700),
            Some(10.0)
        );
        assert_eq!(
            window.observe(start + chrono::Duration::seconds(360), 3_700),
            Some(10.0)
        );
        assert_eq!(
            window.observe(start + chrono::Duration::seconds(370), 3_600),
            None
        );
    }

    #[tokio::test]
    async fn active_worker_events_are_aggregated_and_aborted_attempts_roll_back() {
        let date = NaiveDate::from_ymd_opt(2026, 8, 21).unwrap();
        let path = std::env::temp_dir().join(format!(
            "structure-campaign-worker-progress-{}.json",
            std::process::id()
        ));
        let writer = ProgressWriter::new(
            path.clone(),
            date,
            date,
            &[TickerPlan {
                ticker: "SUGP".to_string(),
                rebuild_start: Utc.with_ymd_and_hms(2026, 8, 21, 8, 0, 0).unwrap(),
                sessions: vec![date],
                estimated_events: 1_000,
            }],
        );
        let mut committed = AttemptEventProgress::new(&writer);
        let aborted = AttemptEventProgress::new(&writer);
        committed.record(100);
        aborted.record(200);
        assert_eq!(writer.snapshot().await.events_processed, 300);

        committed.commit();
        drop(committed);
        drop(aborted);

        assert_eq!(writer.snapshot().await.events_processed, 100);
        let _ = std::fs::remove_file(path);
    }

    #[test]
    fn dashboard_preserves_critical_state_at_compact_width() {
        let now = Utc.with_ymd_and_hms(2026, 9, 2, 20, 0, 0).unwrap();
        let progress = Progress {
            schema_version: 5,
            status: "running".to_string(),
            started_at: now - chrono::Duration::minutes(5),
            updated_at: now,
            algorithm_version: GENERIC_STRUCTURE_ALGORITHM_VERSION,
            start_date: NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
            end_date: NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
            ticker_count: 13_888,
            total_units: 201_694,
            total_estimated_events: 50_000_000,
            counts: Counts {
                active: 2,
                completed: 10_000,
                queued: 190_691,
                retried: 7,
                skipped: 1_000,
                failed: 1,
                blocked: 2,
                finished: 11_003,
                unavailable: 0,
            },
            events_processed: 12_345_678,
            events_advanced: 11_000_000,
            active: BTreeMap::from([
                (
                    "SUGP".to_string(),
                    NaiveDate::from_ymd_opt(2026, 8, 21).unwrap(),
                ),
                (
                    "AAPL".to_string(),
                    NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
                ),
            ]),
            recent: VecDeque::from([RecentUnit {
                ticker: "MSFT".to_string(),
                session_date: NaiveDate::from_ymd_opt(2026, 8, 31).unwrap(),
                outcome: "completed".to_string(),
                event_count: 456_789,
                cursor: 987_654,
            }]),
            issues: Vec::new(),
        };

        let lines = dashboard_lines(
            &progress,
            &PathBuf::from("D:/runtime/status.json"),
            4,
            60,
            Some(2_000.0),
        );
        let wide_lines = dashboard_lines(
            &progress,
            &PathBuf::from("D:/runtime/status.json"),
            4,
            120,
            Some(2_000.0),
        );

        println!(
            "COMPACT\n{}\nWIDE\n{}",
            lines.join("\n"),
            wide_lines.join("\n")
        );

        assert!(lines.iter().all(|line| line.chars().count() <= 60));
        assert!(wide_lines.iter().all(|line| line.chars().count() <= 120));
        assert!(lines[0].contains("DEGRADED"));
        assert!(lines.iter().any(|line| line.contains("Resolved")));
        assert!(lines.iter().any(|line| line.contains("5m rate")));
        assert!(lines.iter().any(|line| line.contains("Ctrl+C")));
        assert!(!lines.iter().any(|line| line.contains('\u{1b}')));
        let frame = dashboard_frame(&wide_lines, &progress, true);
        assert!(frame.starts_with("\u{1b}[H"));
        assert!(!frame.contains("\u{1b}[2J"));
        assert!(frame.ends_with("\u{1b}[J"));
        let plain = log_snapshot(&progress, 4, Some(2_000.0));
        assert!(plain.contains("progress=11003/201694 (5.5%)"));
        assert!(plain.contains("events=12345678/50000000"));
        assert!(plain.contains("event_rate_5m=2000/s"));
        assert!(!plain.contains('\u{1b}'));
    }

    #[tokio::test]
    async fn interruption_returns_active_work_to_the_resumable_queue() {
        let date = NaiveDate::from_ymd_opt(2026, 8, 21).unwrap();
        let path = std::env::temp_dir().join(format!(
            "structure-campaign-interrupt-{}.json",
            std::process::id()
        ));
        let writer = ProgressWriter::new(
            path.clone(),
            date,
            date,
            &[TickerPlan {
                ticker: "SUGP".to_string(),
                rebuild_start: Utc.with_ymd_and_hms(2026, 2, 22, 9, 0, 0).unwrap(),
                sessions: vec![date],
                estimated_events: 123,
            }],
        );
        writer.activate("SUGP", date).await.unwrap();

        let interrupted = writer.interrupt().await.unwrap();

        assert_eq!(interrupted.status, "interrupted");
        assert_eq!(interrupted.counts.active, 0);
        assert_eq!(interrupted.counts.queued, 1);
        assert!(interrupted.active.is_empty());
        let persisted: serde_json::Value =
            serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
        assert_eq!(persisted["status"], "interrupted");
        assert_eq!(persisted["total_estimated_events"], 123);
        let _ = std::fs::remove_file(path);
    }
}
